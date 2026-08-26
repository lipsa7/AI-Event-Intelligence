"""
Evidence discovery orchestration for evidence-grounded strategic research.

Recommended location:
    agents/evidence_discovery_agent.py

Core pipeline
-------------
Candidate insight / grounding gaps
        ↓
EvidenceDiscoveryAgent
        ↓
query plan
        ↓
parallel web search
        ↓
candidate URLs
        ↓
parallel source fetch
        ↓
existing source-processing pipeline
    ArticleService
        → ExtractionAgent
        → EvidenceBuilderAgent
        → source/provenance verification
        ↓
accepted evidence records
        ↓
GroundingService reassessment
        ↓
continue, refine, or stop

Epistemic rule
--------------
SEARCH RESULT != SOURCE != EVIDENCE != SUPPORTED PREMISE != SUPPORTED INFERENCE

Search-result snippets are discovery metadata only. They never become grounding
evidence directly.

Fast vs Deep
------------
This agent deliberately does NOT contain separate "fast" and "deep" code paths.

The same code consumes a compiled Blueprint:

    FAST profile  -> small budgets / fewer rounds / fewer sources
    DEEP profile  -> larger budgets / more rounds / more sources

So a product can move from ~5-minute research to ~20-minute research without
rewriting this agent. PolicyRegistry + BlueprintCompiler decide the actual
budgets.

Prospective insights
--------------------
A prospective insight is not rejected merely because exact precedent is absent.
Discovery instead separates:

1. factual premise support;
2. analogical support for the proposed mechanism;
3. magnitude / feasibility / blocker checks;
4. counterevidence / counter-incentives;
5. alternative explanations.

Analogical evidence can support the mechanism. It does NOT prove that the
specific predicted future action will occur.

Global budget
-------------
For a strict end-to-end SLA, inject the shared ExecutionBudgetService used by the
whole run. The expected adapter contract is:

    remaining_discretionary_seconds() -> float

and optionally:

    authorize(
        action: str,
        expected_seconds: float = 0.0,
        metadata: dict | None = None,
    ) -> bool | {"allowed": bool, "reason": str}

    record(
        action: str,
        elapsed_seconds: float,
        metadata: dict | None = None,
    ) -> None

The "discretionary" remaining time should already exclude time reserved for
mandatory final stages such as final grounding, Red Team, report compilation and
audit.

The agent also enforces its local per-insight/search limits from the Blueprint.

Synthesis transparency
----------------------
The returned search_manifest records:
- every planned query;
- which queries executed;
- search results seen;
- sources fetched;
- sources rejected and why;
- accepted evidence IDs;
- material-information classification;
- unresolved grounding gaps;
- why discovery stopped.

This is an auditable research trace suitable for InsightTraceService. It is not
a raw chain-of-thought dump.

Non-responsibilities
--------------------
- Does not write the final strategic insight.
- Does not authorize release.
- Does not lower grounding standards.
- Does not treat search snippets as evidence.
- Does not assume source independence when provenance is unknown.
- Does not claim the open web was searched exhaustively.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import math
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Sequence, Set
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


PROMPT_VERSION = "evidence-discovery-v2.0"

ALLOWED_INSIGHT_TYPES = {
    "descriptive",
    "prospective",
}

ALLOWED_SEARCH_MODES = {
    "premise_support",
    "direct_precedent",
    "analogical_mechanism",
    "feasibility",
    "counterevidence",
    "alternative_explanation",
}

SEARCH_MODE_TO_EVIDENCE_ROLE = {
    "premise_support": "direct_premise_evidence",
    "direct_precedent": "direct_premise_evidence",
    "analogical_mechanism": "analogical_mechanism_evidence",
    "feasibility": "feasibility_evidence",
    "counterevidence": "counterevidence",
    "alternative_explanation": "alternative_explanation_evidence",
}

ALLOWED_EVIDENCE_ROLES = {
    "direct_premise_evidence",
    "analogical_mechanism_evidence",
    "feasibility_evidence",
    "counterevidence",
    "counter_incentive_evidence",
    "alternative_explanation_evidence",
}

# Conservative defaults. In the final product these should normally be
# overridden by a versioned Blueprint generated from PolicyRegistry.
DEFAULT_POLICY = {
    "external_search_enabled": True,
    "max_search_rounds_per_insight": 2,
    "max_search_queries_per_insight": 6,
    "max_results_per_query": 5,
    "max_sources_fetched_per_insight": 8,
    "max_elapsed_seconds_per_insight": 45,
    "max_parallel_searches": 4,
    "max_parallel_fetches": 4,
    "search_timeout_seconds": 12,
    "fetch_timeout_seconds": 12,
    "saturation_patience_rounds": 1,
    # Tune on evidence_discovery_gold.jsonl. This is not a universal truth.
    "material_similarity_threshold": 0.90,
}


class EvidenceDiscoveryError(RuntimeError):
    """Base exception for discovery failures."""


class EvidenceDiscoveryValidationError(EvidenceDiscoveryError):
    """Raised when inputs or dependency outputs violate the contract."""


class EvidenceIntegrityError(EvidenceDiscoveryError):
    """Raised when evidence identity/provenance integrity is inconsistent."""


@dataclass(frozen=True)
class OperationBudget:
    allowed: bool
    timeout_seconds: float
    reason: str = ""


# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_enum(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return text.strip("_")


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, Mapping):
        return {str(k): _json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(v) for v in value]
    return str(value)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_hex(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return f"{prefix}-" + _sha256_hex(
        _canonical_json_bytes(payload)
    )[:length].upper()


def _stable_unique_strings(values: Any) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        raise EvidenceDiscoveryValidationError(
            "Expected a string or sequence of strings."
        )

    result: list[str] = []
    seen: Set[str] = set()
    for value in values:
        text = _clean(value)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


def _canonicalize_url(url: str) -> str:
    """
    Conservative URL normalization for result deduplication.

    SourceProvenanceService remains the authority for true source identity.
    """
    url = _clean(url)
    if not url:
        return ""

    try:
        parts = urlsplit(url)
    except ValueError:
        return url

    if not parts.scheme or not parts.netloc:
        return url

    query_items = []
    tracking = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid")

    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        lowered = key.casefold()
        if any(lowered == t or lowered.startswith(t) for t in tracking):
            continue
        query_items.append((key, value))

    return urlunsplit(
        (
            parts.scheme.casefold(),
            parts.netloc.casefold(),
            parts.path or "/",
            urlencode(sorted(query_items), doseq=True),
            "",
        )
    )


def _evidence_id(record: Mapping[str, Any]) -> str:
    return _clean(
        record.get("evidence_id")
        or record.get("id")
        or record.get("fact_id")
        or record.get("metric_id")
        or record.get("triple_id")
    )


def _evidence_text(record: Mapping[str, Any]) -> str:
    for field in (
        "text",
        "statement",
        "claim",
        "fact",
        "summary",
        "exact_excerpt",
        "excerpt",
        "description",
    ):
        value = _clean(record.get(field))
        if value:
            return value

    if record.get("value") is not None or record.get("unit") is not None:
        return " ".join(
            x
            for x in (
                _clean(record.get("metric_name") or record.get("name")),
                _clean(record.get("value")),
                _clean(record.get("unit")),
                _clean(record.get("period")),
                _clean(record.get("entity_name") or record.get("entity_id")),
            )
            if x
        )

    if record.get("source") is not None and record.get("target") is not None:
        return " ".join(
            x
            for x in (
                _clean(record.get("source")),
                _clean(record.get("relation") or record.get("relation_type")),
                _clean(record.get("target")),
            )
            if x
        )

    return ""


def _lexical_similarity(left: str, right: str) -> float:
    """Fallback only; production should normally inject embedding similarity."""
    token_pattern = re.compile(r"[a-z0-9]+", re.IGNORECASE)
    left_tokens = {x.casefold() for x in token_pattern.findall(left)}
    right_tokens = {x.casefold() for x in token_pattern.findall(right)}

    if not left_tokens and not right_tokens:
        return 1.0
    if not left_tokens or not right_tokens:
        return 0.0

    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _call_with_supported_kwargs(func: Callable[..., Any], **kwargs: Any) -> Any:
    """
    Pass timeout/context kwargs only when a dependency accepts them.

    This keeps the agent compatible with simple local adapters while allowing
    production search/fetch services to enforce true network timeouts.
    """
    try:
        sig = inspect.signature(func)
    except (TypeError, ValueError):
        return func(**kwargs)

    accepts_var_kw = any(
        p.kind == inspect.Parameter.VAR_KEYWORD
        for p in sig.parameters.values()
    )

    if accepts_var_kw:
        return func(**kwargs)

    supported = {
        key: value
        for key, value in kwargs.items()
        if key in sig.parameters
    }
    return func(**supported)


# ---------------------------------------------------------------------------
# Evidence discovery agent
# ---------------------------------------------------------------------------


class EvidenceDiscoveryAgent:
    """
    Investigate grounding gaps using bounded external evidence discovery.

    Dependencies
    ------------
    query_planner:
        Optional callable/object. Receives a prompt and returns:
            {"queries": [...]}
        or a bare list.

        Without it, a conservative deterministic fallback query plan is used.

    web_search_service:
        Callable or object with search(...). Search results are candidate
        references only.

    source_fetch_service:
        Callable or object with fetch(...). Must retrieve the underlying source.

    evidence_processor:
        Callable or object with process_source(...). This is the admission path
        that converts a fetched source into accepted evidence records.

    grounding_reassessor:
        Optional callable/object. Reassesses remaining grounding gaps after each
        discovery round.

    budget_service:
        Shared end-to-end ExecutionBudgetService. The agent is profile-agnostic:
        FAST and DEEP are just different Blueprint budgets.

    similarity_fn:
        Optional semantic similarity function, ideally backed by
        EmbeddingService.
    """

    def __init__(
        self,
        *,
        web_search_service: Any,
        source_fetch_service: Any,
        evidence_processor: Any,
        query_planner: Optional[Any] = None,
        grounding_reassessor: Optional[Any] = None,
        budget_service: Optional[Any] = None,
        similarity_fn: Optional[Callable[[str, str], float]] = None,
        model_name: str = "",
        prompt_version: str = PROMPT_VERSION,
    ) -> None:
        if web_search_service is None:
            raise EvidenceDiscoveryValidationError(
                "web_search_service is required."
            )
        if source_fetch_service is None:
            raise EvidenceDiscoveryValidationError(
                "source_fetch_service is required."
            )
        if evidence_processor is None:
            raise EvidenceDiscoveryValidationError(
                "evidence_processor is required."
            )
        if similarity_fn is not None and not callable(similarity_fn):
            raise EvidenceDiscoveryValidationError(
                "similarity_fn must be callable when supplied."
            )

        self.web_search_service = web_search_service
        self.source_fetch_service = source_fetch_service
        self.evidence_processor = evidence_processor
        self.query_planner = query_planner
        self.grounding_reassessor = grounding_reassessor
        self.budget_service = budget_service
        self.similarity_fn = similarity_fn or _lexical_similarity
        self.model_name = _clean(model_name)
        self.prompt_version = _clean(prompt_version)

        if not self.prompt_version:
            raise EvidenceDiscoveryValidationError(
                "prompt_version cannot be empty."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def discover(
        self,
        *,
        insight_id: str,
        insight_text: str,
        insight_type: str,
        premises: Sequence[Mapping[str, Any]],
        grounding_gaps: Sequence[Mapping[str, Any]] = (),
        mechanism: Optional[Mapping[str, Any]] = None,
        existing_evidence: Sequence[Mapping[str, Any]] = (),
        blueprint: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        started_at = time.monotonic()

        insight_id = _clean(insight_id)
        insight_text = _clean(insight_text)
        insight_type = _normalize_enum(insight_type)

        if not insight_id:
            raise EvidenceDiscoveryValidationError(
                "insight_id cannot be empty."
            )
        if not insight_text:
            raise EvidenceDiscoveryValidationError(
                "insight_text cannot be empty."
            )
        if insight_type not in ALLOWED_INSIGHT_TYPES:
            raise EvidenceDiscoveryValidationError(
                f"Unsupported insight_type={insight_type!r}."
            )

        normalized_premises = self._normalize_premises(premises)
        normalized_gaps = self._normalize_grounding_gaps(grounding_gaps)
        normalized_mechanism = self._normalize_mechanism(mechanism)
        policy = self._extract_policy(blueprint)
        existing = self._normalize_existing_evidence(existing_evidence)

        evidence_by_id: Dict[str, Dict[str, Any]] = {
            _evidence_id(record): deepcopy(record)
            for record in existing
        }

        discovery_id = _stable_id(
            "DISCOVERY",
            {
                "insight_id": insight_id,
                "insight_text": insight_text,
                "insight_type": insight_type,
                "premises": normalized_premises,
                "grounding_gaps": normalized_gaps,
                "mechanism": normalized_mechanism,
                "existing_evidence_ids": sorted(evidence_by_id),
                "policy": policy,
            },
        )

        usage: Dict[str, float] = {
            "search_rounds": 0,
            "queries_planned": 0,
            "queries_executed": 0,
            "search_results_seen": 0,
            "sources_fetched": 0,
            "accepted_evidence_records": 0,
            "materially_new_evidence_records": 0,
            "independent_corroborations": 0,
            "duplicate_or_redundant_records": 0,
            "planner_calls": 0,
            "grounding_reassessments": 0,
            "reported_input_tokens": 0,
            "reported_output_tokens": 0,
            "reported_total_tokens": 0,
            "reported_estimated_cost": 0.0,
        }

        search_manifest: list[Dict[str, Any]] = []
        planned_queries_all: list[Dict[str, Any]] = []
        unexecuted_queries: list[Dict[str, Any]] = []
        unprocessed_search_results: list[Dict[str, Any]] = []
        rejected_candidates: list[Dict[str, Any]] = []
        discovered_evidence: list[Dict[str, Any]] = []
        evidence_materiality: Dict[str, Dict[str, Any]] = {}

        current_gaps = deepcopy(normalized_gaps)
        resolved_premise_ids: Set[str] = set()
        executed_query_keys: Set[tuple[str, str]] = set()
        fetched_urls: Set[str] = set()
        saturation_streak = 0
        stop_reason = ""
        stop_detail = ""

        if not policy["external_search_enabled"]:
            stop_reason = "external_search_disabled"
            return self._build_result(
                discovery_id=discovery_id,
                insight_id=insight_id,
                insight_text=insight_text,
                insight_type=insight_type,
                premises=normalized_premises,
                mechanism=normalized_mechanism,
                policy=policy,
                existing_evidence=existing,
                discovered_evidence=discovered_evidence,
                evidence_materiality=evidence_materiality,
                current_gaps=current_gaps,
                resolved_premise_ids=resolved_premise_ids,
                search_manifest=search_manifest,
                planned_queries_all=planned_queries_all,
                unexecuted_queries=unexecuted_queries,
                unprocessed_search_results=unprocessed_search_results,
                rejected_candidates=rejected_candidates,
                usage=usage,
                stop_reason=stop_reason,
                stop_detail=stop_detail,
                started_at=started_at,
                blueprint=blueprint,
            )

        for round_number in range(
            1,
            policy["max_search_rounds_per_insight"] + 1,
        ):
            round_budget = self._operation_budget(
                action="evidence_discovery_round",
                local_timeout=policy["max_elapsed_seconds_per_insight"],
                started_at=started_at,
                metadata={
                    "discovery_id": discovery_id,
                    "insight_id": insight_id,
                    "round": round_number,
                },
            )
            if not round_budget.allowed:
                stop_reason = "budget_exhausted"
                stop_detail = round_budget.reason
                break

            usage["search_rounds"] += 1

            plan = self._plan_queries(
                insight_id=insight_id,
                insight_text=insight_text,
                insight_type=insight_type,
                premises=normalized_premises,
                grounding_gaps=current_gaps,
                mechanism=normalized_mechanism,
                round_number=round_number,
                existing_evidence_ids=sorted(evidence_by_id),
            )

            if self.query_planner is not None:
                usage["planner_calls"] += 1

            self._accumulate_usage(
                usage,
                plan.get("usage"),
            )

            new_queries: list[Dict[str, Any]] = []

            for query in plan["queries"]:
                usage["queries_planned"] += 1
                planned_queries_all.append(deepcopy(query))

                key = (
                    query["mode"],
                    query["query"].casefold(),
                )

                if key in executed_query_keys:
                    unexecuted_queries.append(
                        {
                            **deepcopy(query),
                            "round": round_number,
                            "reason": "duplicate_query",
                        }
                    )
                    continue

                if (
                    usage["queries_executed"]
                    + len(new_queries)
                    >= policy["max_search_queries_per_insight"]
                ):
                    unexecuted_queries.append(
                        {
                            **deepcopy(query),
                            "round": round_number,
                            "reason": "query_budget_exhausted",
                        }
                    )
                    continue

                new_queries.append(query)

            if not new_queries:
                stop_reason = "no_new_queries"
                break

            # ----------------------------------------------------------
            # Parallel search batch
            # ----------------------------------------------------------
            search_batch, search_rejections = self._execute_search_batch(
                queries=new_queries,
                policy=policy,
                started_at=started_at,
                discovery_id=discovery_id,
                insight_id=insight_id,
                round_number=round_number,
            )

            for rejection in search_rejections:
                unexecuted_queries.append(rejection)

            for item in search_batch:
                query = item["query"]
                executed_query_keys.add(
                    (
                        query["mode"],
                        query["query"].casefold(),
                    )
                )
                usage["queries_executed"] += 1

                trace = {
                    "round": round_number,
                    **deepcopy(query),
                    "search_status": item["status"],
                    "search_elapsed_seconds": item["elapsed_seconds"],
                    "results_seen": deepcopy(item["results"]),
                    "source_fetches": [],
                    "accepted_evidence_ids": [],
                    "material_evidence_ids": [],
                    "rejected_candidates": [],
                }

                if item.get("error"):
                    trace["error"] = item["error"]
                    trace["error_type"] = item.get("error_type", "")

                usage["search_results_seen"] += len(item["results"])
                self._accumulate_usage(usage, item.get("usage"))
                search_manifest.append(trace)

            if not search_batch:
                stop_reason = "budget_exhausted"
                stop_detail = (
                    search_rejections[0]["reason"]
                    if search_rejections
                    else "search_batch_not_started"
                )
                break

            # ----------------------------------------------------------
            # Candidate URL selection + parallel fetch batch
            # ----------------------------------------------------------
            candidate_sources = self._collect_fetch_candidates(
                search_batch=search_batch,
                fetched_urls=fetched_urls,
                max_sources=max(
                    0,
                    policy["max_sources_fetched_per_insight"]
                    - int(usage["sources_fetched"]),
                ),
                unprocessed_search_results=unprocessed_search_results,
            )

            fetch_batch, fetch_rejections = self._execute_fetch_batch(
                candidates=candidate_sources,
                policy=policy,
                started_at=started_at,
                discovery_id=discovery_id,
                insight_id=insight_id,
                round_number=round_number,
            )

            for rejection in fetch_rejections:
                unprocessed_search_results.append(rejection)

            # Map query_id -> trace object for attaching source/evidence lineage.
            trace_by_query_id = {
                trace["query_id"]: trace
                for trace in search_manifest
                if trace["round"] == round_number
            }

            round_material_count = 0

            for fetched in fetch_batch:
                query_id = fetched["query"]["query_id"]
                trace = trace_by_query_id.get(query_id)
                if trace is None:
                    continue

                fetch_trace = {
                    "result_id": fetched["result"]["result_id"],
                    "url": fetched["result"]["url"],
                    "canonical_url": fetched["canonical_url"],
                    "fetch_status": fetched["status"],
                    "fetch_elapsed_seconds": fetched["elapsed_seconds"],
                    "accepted_evidence_ids": [],
                    "rejected_candidates": [],
                }

                if fetched.get("error"):
                    fetch_trace["error"] = fetched["error"]
                    fetch_trace["error_type"] = fetched.get(
                        "error_type",
                        "",
                    )
                    trace["source_fetches"].append(fetch_trace)
                    continue

                fetched_urls.add(fetched["canonical_url"])
                usage["sources_fetched"] += 1
                self._accumulate_usage(usage, fetched.get("usage"))

                discovery_context = {
                    "discovery_id": discovery_id,
                    "insight_id": insight_id,
                    "insight_type": insight_type,
                    "search_round": round_number,
                    "query_id": query_id,
                    "query": fetched["query"]["query"],
                    "search_mode": fetched["query"]["mode"],
                    "evidence_role": fetched["query"]["evidence_role"],
                    "target_premise_ids": list(
                        fetched["query"]["target_premise_ids"]
                    ),
                    "result_rank": fetched["result"]["rank"],
                    "search_result": deepcopy(fetched["result"]),
                }

                process_budget = self._operation_budget(
                    action="evidence_processing",
                    local_timeout=policy[
                        "max_elapsed_seconds_per_insight"
                    ],
                    started_at=started_at,
                    metadata={
                        "discovery_id": discovery_id,
                        "query_id": query_id,
                        "url": fetched["result"]["url"],
                    },
                )

                if not process_budget.allowed:
                    unprocessed_search_results.append(
                        {
                            "round": round_number,
                            "query_id": query_id,
                            "reason": process_budget.reason,
                            "result": deepcopy(fetched["result"]),
                        }
                    )
                    stop_reason = "budget_exhausted"
                    stop_detail = process_budget.reason
                    trace["source_fetches"].append(fetch_trace)
                    break

                processing_started = time.monotonic()

                try:
                    processed = self._invoke_evidence_processor(
                        source=fetched["source"],
                        discovery_context=discovery_context,
                        timeout_seconds=process_budget.timeout_seconds,
                    )
                except Exception as exc:
                    rejection = {
                        "round": round_number,
                        "query_id": query_id,
                        "result_id": fetched["result"]["result_id"],
                        "url": fetched["result"]["url"],
                        "reason": "evidence_processing_error",
                        "error_type": type(exc).__name__,
                        "error": _clean(exc),
                    }
                    rejected_candidates.append(rejection)
                    fetch_trace["rejected_candidates"].append(rejection)
                    trace["rejected_candidates"].append(rejection)
                    trace["source_fetches"].append(fetch_trace)
                    self._record_budget_usage(
                        action="evidence_processing",
                        elapsed_seconds=(
                            time.monotonic() - processing_started
                        ),
                        metadata={
                            "discovery_id": discovery_id,
                            "query_id": query_id,
                        },
                    )
                    continue

                self._record_budget_usage(
                    action="evidence_processing",
                    elapsed_seconds=time.monotonic() - processing_started,
                    metadata={
                        "discovery_id": discovery_id,
                        "query_id": query_id,
                    },
                )

                self._accumulate_usage(
                    usage,
                    (processed.get("metadata") or {}).get("usage"),
                )

                for candidate_rejection in (
                    processed.get("rejected_candidates") or []
                ):
                    rejection = {
                        "round": round_number,
                        "query_id": query_id,
                        "result_id": fetched["result"]["result_id"],
                        "url": fetched["result"]["url"],
                        **(
                            dict(candidate_rejection)
                            if isinstance(
                                candidate_rejection,
                                Mapping,
                            )
                            else {"reason": _clean(candidate_rejection)}
                        ),
                    }
                    rejected_candidates.append(rejection)
                    fetch_trace["rejected_candidates"].append(rejection)
                    trace["rejected_candidates"].append(rejection)

                accepted_records = (
                    processed.get("accepted_evidence") or []
                )

                if not isinstance(accepted_records, list):
                    raise EvidenceDiscoveryValidationError(
                        "evidence_processor.accepted_evidence must be a list."
                    )

                for raw_evidence in accepted_records:
                    evidence_record = self._normalize_admitted_evidence(
                        raw_evidence,
                        discovery_context=discovery_context,
                    )
                    evidence_id = _evidence_id(evidence_record)

                    if evidence_id in evidence_by_id:
                        existing_record = evidence_by_id[evidence_id]

                        if (
                            self._full_evidence_fingerprint(existing_record)
                            != self._full_evidence_fingerprint(evidence_record)
                        ):
                            raise EvidenceIntegrityError(
                                "Evidence ID collision with different content: "
                                f"{evidence_id!r}."
                            )

                        materiality = {
                            "classification": "same_evidence_id",
                            "materially_new": False,
                            "nearest_evidence_id": evidence_id,
                            "similarity": 1.0,
                            "source_independence": self._source_independence_status(
                                evidence_record
                            ),
                            "reason": (
                                "Evidence ID already existed; record preserved "
                                "but not counted as new information."
                            ),
                        }

                    else:
                        materiality = self._classify_materiality(
                            evidence_record,
                            comparison_records=list(evidence_by_id.values()),
                            threshold=policy[
                                "material_similarity_threshold"
                            ],
                        )

                        evidence_by_id[evidence_id] = deepcopy(
                            evidence_record
                        )
                        discovered_evidence.append(
                            deepcopy(evidence_record)
                        )
                        usage["accepted_evidence_records"] += 1

                        if materiality["materially_new"]:
                            usage[
                                "materially_new_evidence_records"
                            ] += 1
                            round_material_count += 1

                        if (
                            materiality["classification"]
                            == "independent_corroboration"
                        ):
                            usage["independent_corroborations"] += 1

                        if materiality["classification"] in {
                            "exact_duplicate_content",
                            "duplicate_underlying_source",
                            "corroboration_independence_unknown",
                        }:
                            usage[
                                "duplicate_or_redundant_records"
                            ] += 1

                    evidence_materiality[evidence_id] = deepcopy(
                        materiality
                    )
                    fetch_trace["accepted_evidence_ids"].append(evidence_id)
                    trace["accepted_evidence_ids"].append(evidence_id)

                    if materiality["materially_new"]:
                        trace["material_evidence_ids"].append(evidence_id)

                trace["source_fetches"].append(fetch_trace)

            if stop_reason == "budget_exhausted":
                break

            # ----------------------------------------------------------
            # Re-ground after the complete round
            # ----------------------------------------------------------
            if self.grounding_reassessor is not None:
                reassess_budget = self._operation_budget(
                    action="grounding_reassessment",
                    local_timeout=policy[
                        "max_elapsed_seconds_per_insight"
                    ],
                    started_at=started_at,
                    metadata={
                        "discovery_id": discovery_id,
                        "insight_id": insight_id,
                        "round": round_number,
                    },
                )

                if not reassess_budget.allowed:
                    stop_reason = "budget_exhausted"
                    stop_detail = reassess_budget.reason
                    break

                reassess_started = time.monotonic()

                reassessment = self._invoke_grounding_reassessor(
                    timeout_seconds=reassess_budget.timeout_seconds,
                    insight_id=insight_id,
                    insight_text=insight_text,
                    insight_type=insight_type,
                    premises=normalized_premises,
                    mechanism=normalized_mechanism,
                    evidence_records=list(evidence_by_id.values()),
                    previous_gaps=current_gaps,
                )

                self._record_budget_usage(
                    action="grounding_reassessment",
                    elapsed_seconds=time.monotonic() - reassess_started,
                    metadata={
                        "discovery_id": discovery_id,
                        "round": round_number,
                    },
                )

                usage["grounding_reassessments"] += 1

                self._accumulate_usage(
                    usage,
                    (reassessment.get("metadata") or {}).get("usage"),
                )

                current_gaps = self._normalize_grounding_gaps(
                    reassessment.get("remaining_gaps") or []
                )
                resolved_premise_ids.update(
                    _stable_unique_strings(
                        reassessment.get("resolved_premise_ids") or []
                    )
                )

                if not current_gaps:
                    stop_reason = "all_requested_gaps_resolved"
                    break

            if round_material_count == 0:
                saturation_streak += 1
            else:
                saturation_streak = 0

            if (
                saturation_streak
                >= policy["saturation_patience_rounds"]
            ):
                stop_reason = (
                    "search_saturation_no_material_information_gain"
                )
                break

        if not stop_reason:
            stop_reason = "max_search_rounds_reached"

        return self._build_result(
            discovery_id=discovery_id,
            insight_id=insight_id,
            insight_text=insight_text,
            insight_type=insight_type,
            premises=normalized_premises,
            mechanism=normalized_mechanism,
            policy=policy,
            existing_evidence=existing,
            discovered_evidence=discovered_evidence,
            evidence_materiality=evidence_materiality,
            current_gaps=current_gaps,
            resolved_premise_ids=resolved_premise_ids,
            search_manifest=search_manifest,
            planned_queries_all=planned_queries_all,
            unexecuted_queries=unexecuted_queries,
            unprocessed_search_results=unprocessed_search_results,
            rejected_candidates=rejected_candidates,
            usage=usage,
            stop_reason=stop_reason,
            stop_detail=stop_detail,
            started_at=started_at,
            blueprint=blueprint,
        )

    # ------------------------------------------------------------------
    # Query planning
    # ------------------------------------------------------------------

    def _plan_queries(
        self,
        *,
        insight_id: str,
        insight_text: str,
        insight_type: str,
        premises: Sequence[Mapping[str, Any]],
        grounding_gaps: Sequence[Mapping[str, Any]],
        mechanism: Mapping[str, Any],
        round_number: int,
        existing_evidence_ids: Sequence[str],
    ) -> Dict[str, Any]:
        if self.query_planner is None:
            return {
                "queries": self._deterministic_query_plan(
                    insight_text=insight_text,
                    insight_type=insight_type,
                    premises=premises,
                    grounding_gaps=grounding_gaps,
                    mechanism=mechanism,
                ),
                "usage": {},
                "planner_mode": "deterministic_fallback",
            }

        prompt = self._build_query_planner_prompt(
            insight_id=insight_id,
            insight_text=insight_text,
            insight_type=insight_type,
            premises=premises,
            grounding_gaps=grounding_gaps,
            mechanism=mechanism,
            round_number=round_number,
            existing_evidence_ids=existing_evidence_ids,
        )

        raw = self._invoke_query_planner(prompt)
        payload = self._parse_query_plan(raw)

        return {
            "queries": self._normalize_queries(
                payload.get("queries") or []
            ),
            "usage": payload.get("usage") or {},
            "planner_mode": "model_or_external_planner",
        }

    def _build_query_planner_prompt(
        self,
        *,
        insight_id: str,
        insight_text: str,
        insight_type: str,
        premises: Sequence[Mapping[str, Any]],
        grounding_gaps: Sequence[Mapping[str, Any]],
        mechanism: Mapping[str, Any],
        round_number: int,
        existing_evidence_ids: Sequence[str],
    ) -> str:
        premise_ids = [p["premise_id"] for p in premises]

        prospective = ""
        if insight_type == "prospective":
            prospective = """
This is a PROSPECTIVE insight.

Do not interpret lack of exact precedent as evidence that the insight is false.
Investigate separately:
- factual premise support;
- analogical support for the GENERAL mechanism;
- magnitude / feasibility / blockers;
- counter-incentives;
- alternative explanations.

Analogical evidence supports the proposed mechanism only. It does not prove the
specific future action will occur.
""".strip()

        return f"""
You are a narrow evidence-discovery query planner.

Generate web-search queries that INVESTIGATE the candidate insight rather than
merely confirm it.

Insight ID:
{insight_id}

Insight type:
{insight_type}

Candidate insight:
{insight_text}

Premises:
{json.dumps(list(premises), ensure_ascii=False, indent=2)}

Current grounding gaps:
{json.dumps(list(grounding_gaps), ensure_ascii=False, indent=2)}

Proposed mechanism:
{json.dumps(dict(mechanism), ensure_ascii=False, indent=2)}

Round:
{round_number}

Already accepted evidence IDs:
{json.dumps(list(existing_evidence_ids), ensure_ascii=False)}

{prospective}

Return exactly one JSON object:

{{
  "queries": [
    {{
      "mode": "premise_support | direct_precedent | analogical_mechanism | feasibility | counterevidence | alternative_explanation",
      "query": "specific search query",
      "target_premise_ids": ["only IDs supplied above"],
      "rationale": "which evidence gap this query tests"
    }}
  ]
}}

Rules:
1. Search snippets are never evidence.
2. Include counterevidence or alternative-explanation investigation where
   materially relevant.
3. Do not invent entities, dates, policies, numbers or events.
4. For prospective insights, use analogical-mechanism search when exact
   precedent is absent or unnecessary.
5. Feasibility search should test blockers, costs, capacity, timing, law,
   capabilities or competing incentives where relevant.
6. Do not force every search mode when a gap is already resolved.
7. Avoid near-duplicate query variants.
8. target_premise_ids may contain only:
   {json.dumps(premise_ids)}
9. Return no commentary outside JSON.
""".strip()

    def _deterministic_query_plan(
        self,
        *,
        insight_text: str,
        insight_type: str,
        premises: Sequence[Mapping[str, Any]],
        grounding_gaps: Sequence[Mapping[str, Any]],
        mechanism: Mapping[str, Any],
    ) -> list[Dict[str, Any]]:
        raw: list[Dict[str, Any]] = []

        for gap in grounding_gaps:
            question = _clean(
                gap.get("search_question")
                or gap.get("question")
            )
            if not question:
                continue

            premise_id = _clean(gap.get("premise_id"))
            mode = self._mode_from_gap_type(
                _normalize_enum(gap.get("gap_type"))
            )

            raw.append(
                {
                    "mode": mode,
                    "query": question,
                    "target_premise_ids": (
                        [premise_id] if premise_id else []
                    ),
                    "rationale": (
                        "Investigate an explicit grounding gap."
                    ),
                }
            )

        if not raw:
            for premise in premises:
                raw.append(
                    {
                        "mode": "premise_support",
                        "query": premise["text"],
                        "target_premise_ids": [premise["premise_id"]],
                        "rationale": (
                            "Search for direct factual premise support."
                        ),
                    }
                )

        raw.append(
            {
                "mode": "counterevidence",
                "query": f"{insight_text} contrary evidence barriers",
                "target_premise_ids": [],
                "rationale": (
                    "Look for material evidence that weakens the insight."
                ),
            }
        )

        raw.append(
            {
                "mode": "alternative_explanation",
                "query": f"{insight_text} alternative explanation other factors",
                "target_premise_ids": [],
                "rationale": (
                    "Investigate competing explanations."
                ),
            }
        )

        if insight_type == "prospective":
            mechanism_text = _clean(
                mechanism.get("description")
                or mechanism.get("mechanism")
            )

            if mechanism_text:
                raw.append(
                    {
                        "mode": "analogical_mechanism",
                        "query": (
                            f"{mechanism_text} analogous historical examples "
                            "incentive actor response"
                        ),
                        "target_premise_ids": [],
                        "rationale": (
                            "Test the general mechanism in analogous contexts."
                        ),
                    }
                )

            raw.append(
                {
                    "mode": "feasibility",
                    "query": (
                        f"{insight_text} feasibility costs blockers constraints timing"
                    ),
                    "target_premise_ids": [],
                    "rationale": (
                        "Test whether the predicted response is practically feasible."
                    ),
                }
            )

        return self._normalize_queries(raw)

    # ------------------------------------------------------------------
    # Parallel search / fetch
    # ------------------------------------------------------------------

    def _execute_search_batch(
        self,
        *,
        queries: Sequence[Mapping[str, Any]],
        policy: Mapping[str, Any],
        started_at: float,
        discovery_id: str,
        insight_id: str,
        round_number: int,
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        runnable: list[tuple[Dict[str, Any], float]] = []
        rejected: list[Dict[str, Any]] = []

        for query in queries:
            budget = self._operation_budget(
                action="web_search",
                local_timeout=min(
                    policy["search_timeout_seconds"],
                    policy["max_elapsed_seconds_per_insight"],
                ),
                started_at=started_at,
                metadata={
                    "discovery_id": discovery_id,
                    "insight_id": insight_id,
                    "round": round_number,
                    "query_id": query["query_id"],
                },
            )

            if not budget.allowed:
                rejected.append(
                    {
                        **deepcopy(query),
                        "round": round_number,
                        "reason": budget.reason,
                    }
                )
                continue

            runnable.append(
                (
                    deepcopy(dict(query)),
                    budget.timeout_seconds,
                )
            )

        if not runnable:
            return [], rejected

        workers = min(
            policy["max_parallel_searches"],
            len(runnable),
        )

        results: list[Dict[str, Any]] = []

        def run_one(
            query: Mapping[str, Any],
            timeout_seconds: float,
        ) -> Dict[str, Any]:
            started = time.monotonic()
            try:
                payload = self._invoke_search(
                    query=query["query"],
                    max_results=policy["max_results_per_query"],
                    timeout_seconds=timeout_seconds,
                )

                raw_results = payload["results"]
                normalized = [
                    self._normalize_search_result(item, rank=index)
                    for index, item in enumerate(
                        raw_results[: policy["max_results_per_query"]],
                        start=1,
                    )
                ]

                return {
                    "query": deepcopy(dict(query)),
                    "status": "complete",
                    "results": normalized,
                    "usage": payload.get("usage") or {},
                    "elapsed_seconds": time.monotonic() - started,
                }

            except Exception as exc:
                return {
                    "query": deepcopy(dict(query)),
                    "status": "error",
                    "results": [],
                    "usage": {},
                    "elapsed_seconds": time.monotonic() - started,
                    "error_type": type(exc).__name__,
                    "error": _clean(exc),
                }

        executor = ThreadPoolExecutor(max_workers=workers)
        futures = {
            executor.submit(run_one, query, timeout): query
            for query, timeout in runnable
        }

        try:
            for future in as_completed(futures):
                item = future.result()
                results.append(item)
                self._record_budget_usage(
                    action="web_search",
                    elapsed_seconds=item["elapsed_seconds"],
                    metadata={
                        "discovery_id": discovery_id,
                        "query_id": item["query"]["query_id"],
                    },
                )
        finally:
            executor.shutdown(
                wait=False,
                cancel_futures=True,
            )

        results.sort(
            key=lambda x: x["query"]["query_id"]
        )
        return results, rejected

    def _collect_fetch_candidates(
        self,
        *,
        search_batch: Sequence[Mapping[str, Any]],
        fetched_urls: Set[str],
        max_sources: int,
        unprocessed_search_results: list[Dict[str, Any]],
    ) -> list[Dict[str, Any]]:
        if max_sources <= 0:
            for item in search_batch:
                for result in item["results"]:
                    unprocessed_search_results.append(
                        {
                            "query_id": item["query"]["query_id"],
                            "reason": "source_fetch_budget_exhausted",
                            "result": deepcopy(result),
                        }
                    )
            return []

        candidates: list[Dict[str, Any]] = []
        seen_urls: Set[str] = set()

        # Preserve search rank; lower result rank is considered first.
        flattened = []
        for item in search_batch:
            for result in item["results"]:
                flattened.append(
                    (
                        int(result["rank"] or 10**9),
                        item["query"],
                        result,
                    )
                )

        flattened.sort(
            key=lambda x: (
                x[0],
                x[1]["query_id"],
                x[2]["result_id"],
            )
        )

        for _, query, result in flattened:
            canonical = _canonicalize_url(result["url"])

            if not canonical:
                unprocessed_search_results.append(
                    {
                        "query_id": query["query_id"],
                        "reason": "missing_result_url",
                        "result": deepcopy(result),
                    }
                )
                continue

            if canonical in fetched_urls or canonical in seen_urls:
                unprocessed_search_results.append(
                    {
                        "query_id": query["query_id"],
                        "reason": "duplicate_url",
                        "result": deepcopy(result),
                    }
                )
                continue

            if len(candidates) >= max_sources:
                unprocessed_search_results.append(
                    {
                        "query_id": query["query_id"],
                        "reason": "source_fetch_budget_exhausted",
                        "result": deepcopy(result),
                    }
                )
                continue

            seen_urls.add(canonical)
            candidates.append(
                {
                    "query": deepcopy(dict(query)),
                    "result": deepcopy(dict(result)),
                    "canonical_url": canonical,
                }
            )

        return candidates

    def _execute_fetch_batch(
        self,
        *,
        candidates: Sequence[Mapping[str, Any]],
        policy: Mapping[str, Any],
        started_at: float,
        discovery_id: str,
        insight_id: str,
        round_number: int,
    ) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
        runnable: list[tuple[Dict[str, Any], float]] = []
        rejected: list[Dict[str, Any]] = []

        for candidate in candidates:
            budget = self._operation_budget(
                action="source_fetch",
                local_timeout=min(
                    policy["fetch_timeout_seconds"],
                    policy["max_elapsed_seconds_per_insight"],
                ),
                started_at=started_at,
                metadata={
                    "discovery_id": discovery_id,
                    "insight_id": insight_id,
                    "round": round_number,
                    "query_id": candidate["query"]["query_id"],
                    "url": candidate["result"]["url"],
                },
            )

            if not budget.allowed:
                rejected.append(
                    {
                        "round": round_number,
                        "query_id": candidate["query"]["query_id"],
                        "reason": budget.reason,
                        "result": deepcopy(candidate["result"]),
                    }
                )
                continue

            runnable.append(
                (
                    deepcopy(dict(candidate)),
                    budget.timeout_seconds,
                )
            )

        if not runnable:
            return [], rejected

        workers = min(
            policy["max_parallel_fetches"],
            len(runnable),
        )

        results: list[Dict[str, Any]] = []

        def run_one(
            candidate: Mapping[str, Any],
            timeout_seconds: float,
        ) -> Dict[str, Any]:
            started = time.monotonic()

            try:
                source = self._invoke_fetch(
                    candidate["result"]["url"],
                    timeout_seconds=timeout_seconds,
                )

                return {
                    **deepcopy(dict(candidate)),
                    "status": "complete",
                    "source": source,
                    "usage": {},
                    "elapsed_seconds": time.monotonic() - started,
                }

            except Exception as exc:
                return {
                    **deepcopy(dict(candidate)),
                    "status": "error",
                    "source": {},
                    "usage": {},
                    "elapsed_seconds": time.monotonic() - started,
                    "error_type": type(exc).__name__,
                    "error": _clean(exc),
                }

        executor = ThreadPoolExecutor(max_workers=workers)
        futures = {
            executor.submit(run_one, candidate, timeout): candidate
            for candidate, timeout in runnable
        }

        try:
            for future in as_completed(futures):
                item = future.result()
                results.append(item)
                self._record_budget_usage(
                    action="source_fetch",
                    elapsed_seconds=item["elapsed_seconds"],
                    metadata={
                        "discovery_id": discovery_id,
                        "query_id": item["query"]["query_id"],
                    },
                )
        finally:
            executor.shutdown(
                wait=False,
                cancel_futures=True,
            )

        results.sort(
            key=lambda x: (
                x["query"]["query_id"],
                int(x["result"]["rank"] or 10**9),
            )
        )

        return results, rejected

    # ------------------------------------------------------------------
    # Material information gain / source independence
    # ------------------------------------------------------------------

    def _classify_materiality(
        self,
        record: Mapping[str, Any],
        *,
        comparison_records: Sequence[Mapping[str, Any]],
        threshold: float,
    ) -> Dict[str, Any]:
        if not comparison_records:
            return {
                "classification": "materially_new",
                "materially_new": True,
                "nearest_evidence_id": "",
                "similarity": 0.0,
                "source_independence": self._source_independence_status(
                    record
                ),
                "reason": "No prior evidence was available for comparison.",
            }

        content_fp = self._semantic_evidence_fingerprint(record)

        for existing in comparison_records:
            if (
                content_fp
                == self._semantic_evidence_fingerprint(existing)
            ):
                independence = self._compare_source_independence(
                    record,
                    existing,
                )

                if independence == "independent":
                    return {
                        "classification": "independent_corroboration",
                        "materially_new": True,
                        "nearest_evidence_id": _evidence_id(existing),
                        "similarity": 1.0,
                        "source_independence": independence,
                        "reason": (
                            "Same proposition, but provenance indicates a "
                            "different independent source group."
                        ),
                    }

                if independence == "same_underlying_origin":
                    return {
                        "classification": "duplicate_underlying_source",
                        "materially_new": False,
                        "nearest_evidence_id": _evidence_id(existing),
                        "similarity": 1.0,
                        "source_independence": independence,
                        "reason": (
                            "Same proposition appears to derive from the same "
                            "underlying source/origin."
                        ),
                    }

                return {
                    "classification": "exact_duplicate_content",
                    "materially_new": False,
                    "nearest_evidence_id": _evidence_id(existing),
                    "similarity": 1.0,
                    "source_independence": "unknown",
                    "reason": (
                        "Same proposition with source independence unproven."
                    ),
                }

        record_text = _evidence_text(record)
        nearest: Optional[Mapping[str, Any]] = None
        nearest_similarity = -1.0

        for existing in comparison_records:
            existing_text = _evidence_text(existing)

            if not record_text or not existing_text:
                continue

            similarity = float(
                self.similarity_fn(
                    record_text,
                    existing_text,
                )
            )

            if not 0.0 <= similarity <= 1.0:
                raise EvidenceDiscoveryValidationError(
                    "similarity_fn must return a value in [0, 1]."
                )

            if similarity > nearest_similarity:
                nearest_similarity = similarity
                nearest = existing

        role = _normalize_enum(record.get("evidence_role"))
        relation = _normalize_enum(
            record.get("grounding_relation")
            or record.get("relation_to_claim")
            or record.get("stance")
        )

        # Opposing evidence can be materially valuable even when semantically
        # similar to the claim it opposes.
        if role in {
            "counterevidence",
            "counter_incentive_evidence",
            "alternative_explanation_evidence",
        } or relation in {
            "contradicts",
            "counter",
            "counterevidence",
            "weakens",
        }:
            return {
                "classification": "material_counterevidence",
                "materially_new": True,
                "nearest_evidence_id": (
                    _evidence_id(nearest) if nearest else ""
                ),
                "similarity": max(nearest_similarity, 0.0),
                "source_independence": self._source_independence_status(
                    record
                ),
                "reason": (
                    "Opposing/alternative evidence changes the available "
                    "information state."
                ),
            }

        if nearest is None or nearest_similarity < threshold:
            return {
                "classification": "materially_new",
                "materially_new": True,
                "nearest_evidence_id": (
                    _evidence_id(nearest) if nearest else ""
                ),
                "similarity": max(nearest_similarity, 0.0),
                "source_independence": self._source_independence_status(
                    record
                ),
                "reason": (
                    "No sufficiently similar prior evidence record was found."
                ),
            }

        independence = self._compare_source_independence(
            record,
            nearest,
        )

        if independence == "same_underlying_origin":
            return {
                "classification": "duplicate_underlying_source",
                "materially_new": False,
                "nearest_evidence_id": _evidence_id(nearest),
                "similarity": nearest_similarity,
                "source_independence": independence,
                "reason": (
                    "Highly similar evidence appears to derive from the same "
                    "underlying origin."
                ),
            }

        if independence == "independent":
            return {
                "classification": "independent_corroboration",
                "materially_new": True,
                "nearest_evidence_id": _evidence_id(nearest),
                "similarity": nearest_similarity,
                "source_independence": independence,
                "reason": (
                    "Similar proposition from an independently identified "
                    "source group adds corroborative value."
                ),
            }

        return {
            "classification": "corroboration_independence_unknown",
            "materially_new": False,
            "nearest_evidence_id": _evidence_id(nearest),
            "similarity": nearest_similarity,
            "source_independence": "unknown",
            "reason": (
                "Highly similar evidence with unproven source independence is "
                "preserved but does not count as material information gain."
            ),
        }

    # ------------------------------------------------------------------
    # Budget integration
    # ------------------------------------------------------------------

    def _operation_budget(
        self,
        *,
        action: str,
        local_timeout: float,
        started_at: float,
        metadata: Mapping[str, Any],
    ) -> OperationBudget:
        local_remaining = max(
            0.0,
            float(local_timeout) - (time.monotonic() - started_at),
        )

        if local_remaining <= 0.0:
            return OperationBudget(
                allowed=False,
                timeout_seconds=0.0,
                reason="per_insight_time_budget_exhausted",
            )

        global_remaining = math.inf

        if self.budget_service is not None:
            remaining_method = getattr(
                self.budget_service,
                "remaining_discretionary_seconds",
                None,
            )

            if callable(remaining_method):
                global_remaining = max(
                    0.0,
                    float(remaining_method()),
                )

            if global_remaining <= 0.0:
                return OperationBudget(
                    allowed=False,
                    timeout_seconds=0.0,
                    reason="global_discretionary_budget_exhausted",
                )

            authorize = getattr(
                self.budget_service,
                "authorize",
                None,
            )

            if callable(authorize):
                decision = _call_with_supported_kwargs(
                    authorize,
                    action=action,
                    expected_seconds=min(
                        local_remaining,
                        global_remaining,
                    ),
                    metadata=dict(metadata),
                )

                if isinstance(decision, Mapping):
                    allowed = bool(decision.get("allowed"))
                    reason = _clean(decision.get("reason"))
                else:
                    allowed = bool(decision)
                    reason = ""

                if not allowed:
                    return OperationBudget(
                        allowed=False,
                        timeout_seconds=0.0,
                        reason=(
                            reason
                            or "global_budget_service_rejected_operation"
                        ),
                    )

        timeout = min(
            local_remaining,
            global_remaining,
        )

        # Leave a tiny local margin instead of starting work at effectively zero.
        if timeout < 0.25:
            return OperationBudget(
                allowed=False,
                timeout_seconds=0.0,
                reason="insufficient_remaining_time",
            )

        return OperationBudget(
            allowed=True,
            timeout_seconds=timeout,
            reason="",
        )

    def _record_budget_usage(
        self,
        *,
        action: str,
        elapsed_seconds: float,
        metadata: Mapping[str, Any],
    ) -> None:
        if self.budget_service is None:
            return

        record = getattr(
            self.budget_service,
            "record",
            None,
        )

        if callable(record):
            _call_with_supported_kwargs(
                record,
                action=action,
                elapsed_seconds=float(elapsed_seconds),
                metadata=dict(metadata),
            )

    # ------------------------------------------------------------------
    # Dependency invocation
    # ------------------------------------------------------------------

    def _invoke_query_planner(self, prompt: str) -> Any:
        planner = self.query_planner

        if callable(planner):
            return planner(prompt)

        for method_name in ("plan", "generate", "ask"):
            method = getattr(planner, method_name, None)
            if callable(method):
                return method(prompt)

        raise EvidenceDiscoveryValidationError(
            "query_planner must be callable or expose plan/generate/ask."
        )

    def _invoke_search(
        self,
        *,
        query: str,
        max_results: int,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        service = self.web_search_service

        if callable(service):
            raw = _call_with_supported_kwargs(
                service,
                query=query,
                max_results=max_results,
                timeout_seconds=timeout_seconds,
            )
        else:
            method = getattr(service, "search", None)

            if not callable(method):
                raise EvidenceDiscoveryValidationError(
                    "web_search_service must be callable or expose search()."
                )

            raw = _call_with_supported_kwargs(
                method,
                query=query,
                max_results=max_results,
                timeout_seconds=timeout_seconds,
            )

        if isinstance(raw, Mapping):
            results = raw.get("results") or []
            usage = raw.get("usage") or {}
        else:
            results = raw
            usage = {}

        if (
            not isinstance(results, Sequence)
            or isinstance(results, (str, bytes))
        ):
            raise EvidenceDiscoveryValidationError(
                "web_search_service must return a sequence of results."
            )

        return {
            "results": list(results),
            "usage": usage,
        }

    def _invoke_fetch(
        self,
        url: str,
        *,
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        service = self.source_fetch_service

        if callable(service):
            raw = _call_with_supported_kwargs(
                service,
                url=url,
                timeout_seconds=timeout_seconds,
            )
        else:
            method = getattr(service, "fetch", None)

            if not callable(method):
                raise EvidenceDiscoveryValidationError(
                    "source_fetch_service must be callable or expose fetch()."
                )

            raw = _call_with_supported_kwargs(
                method,
                url=url,
                timeout_seconds=timeout_seconds,
            )

        if isinstance(raw, Mapping):
            result = deepcopy(dict(raw))
        elif isinstance(raw, str):
            result = {
                "url": url,
                "content": raw,
            }
        else:
            raise EvidenceDiscoveryValidationError(
                "source_fetch_service must return text or an object."
            )

        result.setdefault("url", url)
        return result

    def _invoke_evidence_processor(
        self,
        *,
        source: Mapping[str, Any],
        discovery_context: Mapping[str, Any],
        timeout_seconds: float,
    ) -> Dict[str, Any]:
        processor = self.evidence_processor

        if callable(processor):
            raw = _call_with_supported_kwargs(
                processor,
                source=source,
                discovery_context=dict(discovery_context),
                timeout_seconds=timeout_seconds,
            )
        else:
            method = None

            for method_name in ("process_source", "process"):
                candidate = getattr(
                    processor,
                    method_name,
                    None,
                )
                if callable(candidate):
                    method = candidate
                    break

            if method is None:
                raise EvidenceDiscoveryValidationError(
                    "evidence_processor must be callable or expose "
                    "process_source/process."
                )

            raw = _call_with_supported_kwargs(
                method,
                source=source,
                discovery_context=dict(discovery_context),
                timeout_seconds=timeout_seconds,
            )

        if not isinstance(raw, Mapping):
            raise EvidenceDiscoveryValidationError(
                "evidence_processor must return an object."
            )

        return deepcopy(dict(raw))

    def _invoke_grounding_reassessor(
        self,
        *,
        timeout_seconds: float,
        **payload: Any,
    ) -> Dict[str, Any]:
        reassessor = self.grounding_reassessor

        if callable(reassessor):
            raw = _call_with_supported_kwargs(
                reassessor,
                timeout_seconds=timeout_seconds,
                **payload,
            )
        else:
            method = None

            for method_name in (
                "reassess",
                "assess_gaps",
                "evaluate_gaps",
            ):
                candidate = getattr(
                    reassessor,
                    method_name,
                    None,
                )
                if callable(candidate):
                    method = candidate
                    break

            if method is None:
                raise EvidenceDiscoveryValidationError(
                    "grounding_reassessor must be callable or expose "
                    "reassess/assess_gaps/evaluate_gaps."
                )

            raw = _call_with_supported_kwargs(
                method,
                timeout_seconds=timeout_seconds,
                **payload,
            )

        if not isinstance(raw, Mapping):
            raise EvidenceDiscoveryValidationError(
                "grounding_reassessor must return an object."
            )

        return deepcopy(dict(raw))

    # ------------------------------------------------------------------
    # Parsing / normalization
    # ------------------------------------------------------------------

    def _parse_query_plan(self, raw: Any) -> Dict[str, Any]:
        if isinstance(raw, Mapping):
            return deepcopy(dict(raw))

        if isinstance(raw, list):
            return {"queries": deepcopy(raw)}

        text = _clean(raw)

        if not text:
            raise EvidenceDiscoveryValidationError(
                "query_planner returned an empty response."
            )

        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass

        fenced = re.search(
            r"```(?:json)?\s*(\{.*?\})\s*```",
            text,
            flags=re.IGNORECASE | re.DOTALL,
        )

        if fenced:
            parsed = json.loads(fenced.group(1))
            if isinstance(parsed, dict):
                return parsed

        first = text.find("{")
        last = text.rfind("}")

        if first >= 0 and last > first:
            parsed = json.loads(text[first : last + 1])
            if isinstance(parsed, dict):
                return parsed

        raise EvidenceDiscoveryValidationError(
            "query_planner response did not contain a JSON object."
        )

    def _normalize_queries(
        self,
        queries: Sequence[Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        if (
            not isinstance(queries, Sequence)
            or isinstance(queries, (str, bytes))
        ):
            raise EvidenceDiscoveryValidationError(
                "query plan must be a sequence of objects."
            )

        result: list[Dict[str, Any]] = []
        seen: Set[tuple[str, str]] = set()

        for index, query in enumerate(queries):
            if not isinstance(query, Mapping):
                raise EvidenceDiscoveryValidationError(
                    f"queries[{index}] must be an object."
                )

            mode = _normalize_enum(query.get("mode"))
            query_text = _clean(query.get("query"))

            if mode not in ALLOWED_SEARCH_MODES:
                raise EvidenceDiscoveryValidationError(
                    f"Unsupported search mode={mode!r}."
                )
            if not query_text:
                raise EvidenceDiscoveryValidationError(
                    f"queries[{index}] has an empty query."
                )

            targets = _stable_unique_strings(
                query.get("target_premise_ids") or []
            )
            key = (mode, query_text.casefold())

            if key in seen:
                continue

            seen.add(key)

            result.append(
                {
                    "query_id": (
                        _clean(
                            query.get("query_id")
                            or query.get("id")
                        )
                        or _stable_id(
                            "QUERY",
                            {
                                "mode": mode,
                                "query": query_text,
                                "targets": targets,
                            },
                        )
                    ),
                    "mode": mode,
                    "evidence_role": SEARCH_MODE_TO_EVIDENCE_ROLE[mode],
                    "query": query_text,
                    "target_premise_ids": targets,
                    "rationale": _clean(query.get("rationale")),
                }
            )

        return result

    def _normalize_search_result(
        self,
        raw: Any,
        *,
        rank: Optional[int],
    ) -> Dict[str, Any]:
        if isinstance(raw, str):
            raw = {"url": raw}

        if not isinstance(raw, Mapping):
            raise EvidenceDiscoveryValidationError(
                "Every search result must be a URL string or object."
            )

        url = _clean(raw.get("url") or raw.get("link"))
        title = _clean(raw.get("title"))
        snippet = _clean(
            raw.get("snippet")
            or raw.get("description")
        )

        return {
            "result_id": (
                _clean(
                    raw.get("result_id")
                    or raw.get("id")
                )
                or _stable_id(
                    "RESULT",
                    {
                        "url": url,
                        "title": title,
                        "snippet": snippet,
                    },
                )
            ),
            "rank": rank,
            "title": title,
            "url": url,
            "snippet": snippet,
            "provider": _clean(
                raw.get("provider")
                or raw.get("source")
            ),
            "is_grounding_evidence": False,
        }

    def _normalize_premises(
        self,
        premises: Sequence[Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        if (
            not isinstance(premises, Sequence)
            or isinstance(premises, (str, bytes))
        ):
            raise EvidenceDiscoveryValidationError(
                "premises must be a sequence of objects."
            )

        result: list[Dict[str, Any]] = []
        seen: Set[str] = set()

        for index, premise in enumerate(premises):
            if not isinstance(premise, Mapping):
                raise EvidenceDiscoveryValidationError(
                    f"premises[{index}] must be an object."
                )

            premise_id = _clean(
                premise.get("premise_id")
                or premise.get("id")
            )
            text = _clean(
                premise.get("text")
                or premise.get("premise")
                or premise.get("claim")
            )

            if not premise_id:
                raise EvidenceDiscoveryValidationError(
                    f"premises[{index}] is missing premise_id."
                )
            if not text:
                raise EvidenceDiscoveryValidationError(
                    f"premises[{index}] is missing text."
                )
            if premise_id in seen:
                raise EvidenceDiscoveryValidationError(
                    f"Duplicate premise_id={premise_id!r}."
                )

            seen.add(premise_id)
            result.append(
                {
                    "premise_id": premise_id,
                    "text": text,
                    "premise_type": (
                        _normalize_enum(
                            premise.get("premise_type")
                        )
                        or "factual"
                    ),
                }
            )

        if not result:
            raise EvidenceDiscoveryValidationError(
                "At least one premise is required."
            )

        return result

    def _normalize_grounding_gaps(
        self,
        gaps: Sequence[Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        if gaps is None:
            return []

        if (
            not isinstance(gaps, Sequence)
            or isinstance(gaps, (str, bytes))
        ):
            raise EvidenceDiscoveryValidationError(
                "grounding_gaps must be a sequence of objects."
            )

        result = []

        for index, gap in enumerate(gaps):
            if not isinstance(gap, Mapping):
                raise EvidenceDiscoveryValidationError(
                    f"grounding_gaps[{index}] must be an object."
                )

            result.append(
                {
                    "gap_id": (
                        _clean(gap.get("gap_id"))
                        or _stable_id("GAP", gap)
                    ),
                    "premise_id": _clean(
                        gap.get("premise_id")
                    ),
                    "gap_type": (
                        _normalize_enum(gap.get("gap_type"))
                        or "premise_support"
                    ),
                    "search_question": _clean(
                        gap.get("search_question")
                        or gap.get("question")
                    ),
                    "details": _clean(
                        gap.get("details")
                        or gap.get("reason")
                    ),
                }
            )

        return result

    def _normalize_mechanism(
        self,
        mechanism: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        if mechanism is None:
            return {}

        if not isinstance(mechanism, Mapping):
            raise EvidenceDiscoveryValidationError(
                "mechanism must be an object."
            )

        return {
            "description": _clean(
                mechanism.get("description")
                or mechanism.get("mechanism")
            ),
            "mechanism_type": _normalize_enum(
                mechanism.get("mechanism_type")
                or mechanism.get("type")
            ),
            "actor": _clean(mechanism.get("actor")),
            "incentive": _clean(mechanism.get("incentive")),
            "predicted_response": _clean(
                mechanism.get("predicted_response")
                or mechanism.get("response")
            ),
        }

    def _normalize_existing_evidence(
        self,
        evidence: Sequence[Mapping[str, Any]],
    ) -> list[Dict[str, Any]]:
        if (
            not isinstance(evidence, Sequence)
            or isinstance(evidence, (str, bytes))
        ):
            raise EvidenceDiscoveryValidationError(
                "existing_evidence must be a sequence of objects."
            )

        result: list[Dict[str, Any]] = []
        seen: Dict[str, str] = {}

        for index, record in enumerate(evidence):
            if not isinstance(record, Mapping):
                raise EvidenceDiscoveryValidationError(
                    f"existing_evidence[{index}] must be an object."
                )

            normalized = deepcopy(dict(record))
            evidence_id = _evidence_id(normalized)

            if not evidence_id:
                raise EvidenceDiscoveryValidationError(
                    f"existing_evidence[{index}] is missing evidence_id."
                )

            normalized["evidence_id"] = evidence_id
            fp = self._full_evidence_fingerprint(normalized)

            if evidence_id in seen and seen[evidence_id] != fp:
                raise EvidenceIntegrityError(
                    "Duplicate evidence ID with different content: "
                    f"{evidence_id!r}."
                )

            if evidence_id not in seen:
                seen[evidence_id] = fp
                result.append(normalized)

        return result

    def _normalize_admitted_evidence(
        self,
        record: Any,
        *,
        discovery_context: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(record, Mapping):
            raise EvidenceDiscoveryValidationError(
                "Every accepted evidence record must be an object."
            )

        normalized = deepcopy(dict(record))
        evidence_id = _evidence_id(normalized)

        if not evidence_id:
            raise EvidenceDiscoveryValidationError(
                "Accepted evidence is missing a stable evidence_id."
            )

        normalized["evidence_id"] = evidence_id

        role = _normalize_enum(
            normalized.get("evidence_role")
            or discovery_context.get("evidence_role")
        )

        if role not in ALLOWED_EVIDENCE_ROLES:
            raise EvidenceDiscoveryValidationError(
                f"Unsupported evidence_role={role!r}."
            )

        normalized["evidence_role"] = role

        normalized.setdefault(
            "discovery_metadata",
            {
                "discovery_id": discovery_context["discovery_id"],
                "search_round": discovery_context["search_round"],
                "query_id": discovery_context["query_id"],
                "search_mode": discovery_context["search_mode"],
                "result_rank": discovery_context["result_rank"],
            },
        )

        return normalized

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def _extract_policy(
        self,
        blueprint: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        policy = deepcopy(DEFAULT_POLICY)

        if blueprint is None:
            return policy

        if not isinstance(blueprint, Mapping):
            raise EvidenceDiscoveryValidationError(
                "blueprint must be an object."
            )

        data_policy = blueprint.get("data_policy") or {}
        discovery = blueprint.get("evidence_discovery_policy") or {}
        runtime = blueprint.get("runtime_policy") or {}

        for name, value in (
            ("data_policy", data_policy),
            ("evidence_discovery_policy", discovery),
            ("runtime_policy", runtime),
        ):
            if not isinstance(value, Mapping):
                raise EvidenceDiscoveryValidationError(
                    f"blueprint.{name} must be an object."
                )

        if "allow_external_search" in data_policy:
            policy["external_search_enabled"] = bool(
                data_policy["allow_external_search"]
            )

        if "external_search_enabled" in discovery:
            policy["external_search_enabled"] = bool(
                discovery["external_search_enabled"]
            )

        integer_fields = {
            "max_search_rounds_per_insight",
            "max_search_queries_per_insight",
            "max_results_per_query",
            "max_sources_fetched_per_insight",
            "max_elapsed_seconds_per_insight",
            "max_parallel_searches",
            "max_parallel_fetches",
            "search_timeout_seconds",
            "fetch_timeout_seconds",
            "saturation_patience_rounds",
        }

        for field in integer_fields:
            if field in discovery:
                policy[field] = discovery[field]
            elif field in runtime:
                policy[field] = runtime[field]

            try:
                policy[field] = int(policy[field])
            except (TypeError, ValueError) as exc:
                raise EvidenceDiscoveryValidationError(
                    f"{field} must be an integer."
                ) from exc

            if policy[field] <= 0:
                raise EvidenceDiscoveryValidationError(
                    f"{field} must be > 0."
                )

        if "material_similarity_threshold" in discovery:
            policy["material_similarity_threshold"] = discovery[
                "material_similarity_threshold"
            ]

        try:
            policy["material_similarity_threshold"] = float(
                policy["material_similarity_threshold"]
            )
        except (TypeError, ValueError) as exc:
            raise EvidenceDiscoveryValidationError(
                "material_similarity_threshold must be numeric."
            ) from exc

        if not (
            0.0
            <= policy["material_similarity_threshold"]
            <= 1.0
        ):
            raise EvidenceDiscoveryValidationError(
                "material_similarity_threshold must be in [0, 1]."
            )

        return policy

    # ------------------------------------------------------------------
    # Evidence identity / provenance
    # ------------------------------------------------------------------

    def _semantic_evidence_fingerprint(
        self,
        record: Mapping[str, Any],
    ) -> str:
        """
        Proposition-level fingerprint.

        Provenance is deliberately excluded so independent corroboration of the
        same proposition can be recognized.
        """
        semantic = {
            "record_type": _normalize_enum(
                record.get("record_type")
                or record.get("evidence_type")
                or record.get("type")
            ),
            "text": _evidence_text(record),
            "metric_name": _clean(record.get("metric_name")),
            "value": _json_safe(record.get("value")),
            "unit": _clean(record.get("unit")),
            "period": _clean(record.get("period")),
            "entity_id": _clean(record.get("entity_id")),
            "source_entity_id": _clean(
                record.get("source_entity_id")
            ),
            "relation_type": _normalize_enum(
                record.get("relation_type")
                or record.get("relation")
            ),
            "target_entity_id": _clean(
                record.get("target_entity_id")
            ),
            "temporal_status": _normalize_enum(
                record.get("temporal_status")
            ),
            "claim_status": _normalize_enum(
                record.get("claim_status")
            ),
        }

        return _sha256_hex(_canonical_json_bytes(semantic))

    def _full_evidence_fingerprint(
        self,
        record: Mapping[str, Any],
    ) -> str:
        return _sha256_hex(_canonical_json_bytes(record))

    def _source_group(
        self,
        record: Mapping[str, Any],
    ) -> str:
        provenance = record.get("source_provenance")
        provenance = (
            provenance
            if isinstance(provenance, Mapping)
            else {}
        )

        return _clean(
            record.get("independence_group_id")
            or provenance.get("independence_group_id")
            or record.get("canonical_origin_id")
            or provenance.get("canonical_origin_id")
        )

    def _source_independence_status(
        self,
        record: Mapping[str, Any],
    ) -> str:
        return (
            "group_known"
            if self._source_group(record)
            else "unknown"
        )

    def _compare_source_independence(
        self,
        left: Mapping[str, Any],
        right: Mapping[str, Any],
    ) -> str:
        left_group = self._source_group(left)
        right_group = self._source_group(right)

        if not left_group or not right_group:
            return "unknown"

        if left_group == right_group:
            return "same_underlying_origin"

        return "independent"

    # ------------------------------------------------------------------
    # Final trace
    # ------------------------------------------------------------------

    def _premise_evidence_summary(
        self,
        premises: Sequence[Mapping[str, Any]],
        evidence_records: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        summary = {
            premise["premise_id"]: {
                "premise_text": premise["text"],
                "direct_support_ids": [],
                "contradicting_ids": [],
                "analogical_mechanism_ids": [],
                "feasibility_ids": [],
                "alternative_explanation_ids": [],
            }
            for premise in premises
        }

        for record in evidence_records:
            evidence_id = _evidence_id(record)
            role = _normalize_enum(record.get("evidence_role"))

            support_ids = _stable_unique_strings(
                record.get("supports_premise_ids") or []
            )
            contradict_ids = _stable_unique_strings(
                record.get("contradicts_premise_ids") or []
            )

            for premise_id in support_ids:
                if premise_id not in summary:
                    continue

                if role == "analogical_mechanism_evidence":
                    field = "analogical_mechanism_ids"
                elif role == "feasibility_evidence":
                    field = "feasibility_ids"
                elif role == "alternative_explanation_evidence":
                    field = "alternative_explanation_ids"
                else:
                    field = "direct_support_ids"

                if evidence_id not in summary[premise_id][field]:
                    summary[premise_id][field].append(evidence_id)

            for premise_id in contradict_ids:
                if premise_id in summary:
                    if (
                        evidence_id
                        not in summary[premise_id]["contradicting_ids"]
                    ):
                        summary[premise_id]["contradicting_ids"].append(
                            evidence_id
                        )

        return summary

    def _build_result(
        self,
        *,
        discovery_id: str,
        insight_id: str,
        insight_text: str,
        insight_type: str,
        premises: Sequence[Mapping[str, Any]],
        mechanism: Mapping[str, Any],
        policy: Mapping[str, Any],
        existing_evidence: Sequence[Mapping[str, Any]],
        discovered_evidence: Sequence[Mapping[str, Any]],
        evidence_materiality: Mapping[str, Mapping[str, Any]],
        current_gaps: Sequence[Mapping[str, Any]],
        resolved_premise_ids: Set[str],
        search_manifest: Sequence[Mapping[str, Any]],
        planned_queries_all: Sequence[Mapping[str, Any]],
        unexecuted_queries: Sequence[Mapping[str, Any]],
        unprocessed_search_results: Sequence[Mapping[str, Any]],
        rejected_candidates: Sequence[Mapping[str, Any]],
        usage: Mapping[str, Any],
        stop_reason: str,
        stop_detail: str,
        started_at: float,
        blueprint: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        all_evidence = [
            deepcopy(record)
            for record in existing_evidence
        ] + [
            deepcopy(record)
            for record in discovered_evidence
        ]

        material_ids = [
            evidence_id
            for evidence_id, info in evidence_materiality.items()
            if bool(info.get("materially_new"))
        ]

        independent_ids = [
            evidence_id
            for evidence_id, info in evidence_materiality.items()
            if (
                info.get("classification")
                == "independent_corroboration"
            )
        ]

        status = "complete"

        if stop_reason == "budget_exhausted":
            status = "budget_exhausted"
        elif (
            stop_reason
            == "search_saturation_no_material_information_gain"
        ):
            status = "saturated"
        elif stop_reason == "external_search_disabled":
            status = "not_run"

        blueprint_id = (
            _clean(blueprint.get("blueprint_id"))
            if isinstance(blueprint, Mapping)
            else ""
        )

        elapsed = time.monotonic() - started_at

        return {
            "discovery_id": discovery_id,
            "insight_id": insight_id,
            "insight_text": insight_text,
            "insight_type": insight_type,
            "status": status,
            "stop_reason": stop_reason,
            "stop_detail": stop_detail,
            "premises": deepcopy(list(premises)),
            "mechanism": deepcopy(dict(mechanism)),
            "remaining_grounding_gaps": deepcopy(
                list(current_gaps)
            ),
            "resolved_premise_ids": sorted(
                resolved_premise_ids
            ),
            "existing_evidence_ids": [
                _evidence_id(record)
                for record in existing_evidence
            ],
            "discovered_evidence": deepcopy(
                list(discovered_evidence)
            ),
            "discovered_evidence_ids": [
                _evidence_id(record)
                for record in discovered_evidence
            ],
            "materially_new_evidence_ids": material_ids,
            "independent_corroboration_ids": independent_ids,
            "evidence_materiality": deepcopy(
                dict(evidence_materiality)
            ),
            "premise_evidence_summary": self._premise_evidence_summary(
                premises,
                all_evidence,
            ),
            "search_manifest": deepcopy(
                list(search_manifest)
            ),
            "planned_queries": deepcopy(
                list(planned_queries_all)
            ),
            "unexecuted_queries": deepcopy(
                list(unexecuted_queries)
            ),
            "unprocessed_search_results": deepcopy(
                list(unprocessed_search_results)
            ),
            "rejected_candidates": deepcopy(
                list(rejected_candidates)
            ),
            "usage": {
                **dict(usage),
                "elapsed_seconds": elapsed,
            },
            "policy_snapshot": deepcopy(dict(policy)),
            "metadata": {
                "prompt_version": self.prompt_version,
                "model": self.model_name,
                "blueprint_id": blueprint_id,
                "profile_agnostic": True,
                "search_results_are_evidence": False,
                "source_independence_assumed_when_unknown": False,
                "open_web_completeness_claim": "not_guaranteed",
                "search_completeness_claim": (
                    "systematic_search_within_compiled_budget"
                ),
                "trace_contains_private_chain_of_thought": False,
                "input_fingerprint": _sha256_hex(
                    _canonical_json_bytes(
                        {
                            "insight_id": insight_id,
                            "insight_text": insight_text,
                            "insight_type": insight_type,
                            "premises": premises,
                            "mechanism": mechanism,
                            "existing_evidence_ids": [
                                _evidence_id(record)
                                for record in existing_evidence
                            ],
                            "policy": policy,
                        }
                    )
                ),
            },
        }

    # ------------------------------------------------------------------
    # Misc helpers
    # ------------------------------------------------------------------

    def _mode_from_gap_type(self, gap_type: str) -> str:
        return {
            "premise": "premise_support",
            "premise_support": "premise_support",
            "missing_evidence": "premise_support",
            "direct_precedent": "direct_precedent",
            "mechanism": "analogical_mechanism",
            "analogical_mechanism": "analogical_mechanism",
            "feasibility": "feasibility",
            "magnitude": "feasibility",
            "actor_feasibility": "feasibility",
            "counterevidence": "counterevidence",
            "counter_incentive": "counterevidence",
            "alternative_explanation": "alternative_explanation",
        }.get(
            gap_type,
            "premise_support",
        )

    def _accumulate_usage(
        self,
        aggregate: Dict[str, float],
        usage: Any,
    ) -> None:
        if not isinstance(usage, Mapping):
            return

        aliases = {
            "input_tokens": "reported_input_tokens",
            "prompt_tokens": "reported_input_tokens",
            "output_tokens": "reported_output_tokens",
            "completion_tokens": "reported_output_tokens",
            "total_tokens": "reported_total_tokens",
            "estimated_cost": "reported_estimated_cost",
            "estimated_cost_usd": "reported_estimated_cost",
        }

        for source_key, target_key in aliases.items():
            value = usage.get(source_key)
            if value is None:
                continue

            try:
                aggregate[target_key] += float(value)
            except (TypeError, ValueError):
                continue