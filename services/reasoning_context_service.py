"""
Deterministic reasoning-context compiler for the Event Intelligence pipeline.

Recommended location:
    services/reasoning_context_service.py

Role
----
Validated Intent / Blueprint
          +
accepted evidence
          +
current / historical event context
          +
constraints
          +
graph context
          +
prior-report context
          +
semantic user preferences
          ↓
ReasoningContextService
          ↓
auditable, typed, batched reasoning context
          ↓
ConstraintAgent / StrategicInferenceAgent / RedTeam / report composition

Critical boundaries
-------------------
USER PREFERENCE != EVIDENCE
EVENT CONTEXT != EVIDENCE
GRAPH CONTEXT != EVIDENCE
CONSTRAINT RECORD != EVIDENCE
PRIOR REPORT != EVIDENCE

unless an upstream evidence-admission process separately created an accepted
evidence record.

No accepted evidence record is dropped because of serialization order or
context-window size. Records are batched whole. Oversized records are emitted
alone, untruncated.

Finalization enforces:

    all_accepted - examined == empty
    relevant <= examined
    referenced <= final_reasoning_context <= examined

Fast/Deep use the same code. This service does not choose or modify
research_depth. desired_depth remains semantic preference guidance.

No LLM, web search, paid API, grounding, source-independence inference, model
routing or release authorization is performed here.
No private chain-of-thought is requested, persisted or returned.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Dict, Iterator, Mapping, Optional, Sequence, Set


SERVICE_VERSION = "reasoning-context-service-v2.0"
OUTPUT_SCHEMA_VERSION = "reasoning-context-schema-v2.0"

COLLECTION_ACCEPTED_EVIDENCE = "accepted_evidence"
COLLECTION_CURRENT_EVENTS = "current_events"
COLLECTION_HISTORICAL_EVENTS = "historical_events"
COLLECTION_CONSTRAINTS = "constraints"
COLLECTION_GRAPH_RECORDS = "graph_records"
COLLECTION_PRIOR_REPORTS = "prior_reports"
COLLECTION_POSITIVE_EXAMPLES = "positive_examples"
COLLECTION_NEGATIVE_EXAMPLES = "negative_examples"
COLLECTION_FINAL_EVIDENCE = "final_reasoning_evidence"

ALL_COLLECTIONS = {
    COLLECTION_ACCEPTED_EVIDENCE,
    COLLECTION_CURRENT_EVENTS,
    COLLECTION_HISTORICAL_EVENTS,
    COLLECTION_CONSTRAINTS,
    COLLECTION_GRAPH_RECORDS,
    COLLECTION_PRIOR_REPORTS,
    COLLECTION_POSITIVE_EXAMPLES,
    COLLECTION_NEGATIVE_EXAMPLES,
    COLLECTION_FINAL_EVIDENCE,
}

PREFERENCE_ALLOWED_FIELDS = {
    "desired_depth",
    "verbosity",
    "preferred_patterns",
    "avoid_patterns",
    "analytical_lenses",
    "positive_examples",
    "negative_examples",
    "preferred_insight_ids",
    "rejected_insight_ids",
}

PROHIBITED_PREFERENCE_FIELDS = {
    "research_depth",
    "analysis_mode",
    "execution_profile",
    "runtime_policy",
    "execution_policy",
    "model_route",
    "model_routing",
    "search_budget",
    "token_budget",
    "cost_budget",
    "max_run_seconds",
    "max_search_rounds",
    "max_search_queries",
    "release_authorized",
    "authorizes_release",
    "skip_grounding",
    "skip_red_team",
    "evidence_ids",
    "source_ids",
}

PROHIBITED_PRIVATE_REASONING_FIELDS = {
    "chain_of_thought",
    "chainofthought",
    "cot",
    "scratchpad",
    "hidden_reasoning",
    "hiddenreasoning",
    "internal_monologue",
    "internalmonologue",
    "raw_reasoning",
    "rawreasoning",
    "private_reasoning",
    "privatereasoning",
    "reasoning_tokens",
    "reasoningtokens",
    "deliberation",
    "deliberation_trace",
    "deliberationtrace",
    "raw_prompt",
    "rawprompt",
    "raw_completion",
    "rawcompletion",
    "raw_response",
    "rawresponse",
}


class ReasoningContextError(RuntimeError):
    pass


class ReasoningContextValidationError(ReasoningContextError):
    pass


class ReasoningContextCompletenessError(ReasoningContextError):
    pass


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _enum(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    while "__" in text:
        text = text.replace("__", "_")
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


def _sha256(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = _canonical_json_bytes(value)
    return hashlib.sha256(payload).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return f"{_clean(prefix).upper()}-{_sha256(payload)[:length].upper()}"


def _unique_strings(values: Any, *, uppercase: bool = False) -> list[str]:
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    if not isinstance(values, (list, tuple, set)):
        return []

    out = []
    seen = set()

    for item in values:
        if isinstance(item, Mapping):
            item = (
                item.get("evidence_id")
                or item.get("event_id")
                or item.get("constraint_id")
                or item.get("report_id")
                or item.get("insight_id")
                or item.get("id")
                or ""
            )

        text = _clean(item)
        if uppercase:
            text = text.upper()

        if text and text not in seen:
            seen.add(text)
            out.append(text)

    return out


def _contains_prohibited_field(
    value: Any,
    prohibited: Set[str],
    path: str = "",
) -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _enum(key)
            current = f"{path}.{key}" if path else str(key)

            if normalized in prohibited:
                return current

            nested = _contains_prohibited_field(item, prohibited, current)
            if nested:
                return nested

    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            nested = _contains_prohibited_field(
                item,
                prohibited,
                f"{path}[{index}]",
            )
            if nested:
                return nested

    return None


def _record_size(record: Mapping[str, Any]) -> int:
    return len(
        json.dumps(
            _json_safe(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


class ReasoningContextService:
    """
    Deterministically separates, normalizes and batches reasoning context.
    """

    def __init__(
        self,
        *,
        default_evidence_batch_max_characters: int = 16_000,
        default_structured_batch_max_characters: int = 14_000,
        default_preference_batch_max_characters: int = 8_000,
    ) -> None:
        self.default_evidence_batch_max_characters = max(
            1_000, int(default_evidence_batch_max_characters)
        )
        self.default_structured_batch_max_characters = max(
            1_000, int(default_structured_batch_max_characters)
        )
        self.default_preference_batch_max_characters = max(
            1_000, int(default_preference_batch_max_characters)
        )

    def build_context(
        self,
        *,
        research_question: str,
        accepted_evidence: Any,
        current_events: Any = None,
        historical_events: Any = None,
        constraints: Any = None,
        graph_context: Any = None,
        prior_reports: Any = None,
        blueprint: Optional[Mapping[str, Any]] = None,
        user_preferences: Optional[Mapping[str, Any]] = None,
        run_id: str = "",
    ) -> Dict[str, Any]:
        research_question = _clean(research_question)
        if not research_question:
            raise ReasoningContextValidationError(
                "research_question cannot be empty."
            )

        blueprint = blueprint if isinstance(blueprint, Mapping) else {}
        user_preferences = (
            user_preferences if isinstance(user_preferences, Mapping) else {}
        )

        for name, value in (
            ("blueprint", blueprint),
            ("user_preferences", user_preferences),
            ("accepted_evidence", accepted_evidence or {}),
            ("current_events", current_events or {}),
            ("historical_events", historical_events or {}),
            ("constraints", constraints or {}),
            ("graph_context", graph_context or {}),
            ("prior_reports", prior_reports or {}),
        ):
            prohibited = _contains_prohibited_field(
                value,
                PROHIBITED_PRIVATE_REASONING_FIELDS,
            )
            if prohibited:
                raise ReasoningContextValidationError(
                    f"{name} contains prohibited private/raw reasoning field "
                    f"at {prohibited!r}."
                )

        preference_control_path = _contains_prohibited_field(
            user_preferences,
            PROHIBITED_PREFERENCE_FIELDS,
        )
        if preference_control_path:
            raise ReasoningContextValidationError(
                "user_preferences contains execution/control-plane field "
                f"at {preference_control_path!r}. research_depth belongs to "
                "execution policy; desired_depth is semantic preference."
            )

        policy = self._context_policy(blueprint)

        evidence_records = self._extract_evidence_records(accepted_evidence)
        current_event_records = self._extract_event_records(
            current_events, current=True
        )
        historical_event_records = self._extract_event_records(
            historical_events, current=False
        )
        constraint_records = self._extract_constraint_records(constraints)
        graph_records = self._extract_graph_records(graph_context)
        prior_report_records = self._extract_prior_report_records(prior_reports)

        preference_projection = self._preference_projection(user_preferences)
        positive_examples = preference_projection.pop("positive_examples", [])
        negative_examples = preference_projection.pop("negative_examples", [])
        reasoning_policy = self._reasoning_policy_projection(blueprint)

        collections = {
            COLLECTION_ACCEPTED_EVIDENCE: evidence_records,
            COLLECTION_CURRENT_EVENTS: current_event_records,
            COLLECTION_HISTORICAL_EVENTS: historical_event_records,
            COLLECTION_CONSTRAINTS: constraint_records,
            COLLECTION_GRAPH_RECORDS: graph_records,
            COLLECTION_PRIOR_REPORTS: prior_report_records,
            COLLECTION_POSITIVE_EXAMPLES: positive_examples,
            COLLECTION_NEGATIVE_EXAMPLES: negative_examples,
            COLLECTION_FINAL_EVIDENCE: [],
        }

        batches = {
            COLLECTION_ACCEPTED_EVIDENCE: self._batch_records(
                evidence_records,
                collection=COLLECTION_ACCEPTED_EVIDENCE,
                max_characters=policy["evidence_batch_max_characters"],
            ),
            COLLECTION_CURRENT_EVENTS: self._batch_records(
                current_event_records,
                collection=COLLECTION_CURRENT_EVENTS,
                max_characters=policy["structured_batch_max_characters"],
            ),
            COLLECTION_HISTORICAL_EVENTS: self._batch_records(
                historical_event_records,
                collection=COLLECTION_HISTORICAL_EVENTS,
                max_characters=policy["structured_batch_max_characters"],
            ),
            COLLECTION_CONSTRAINTS: self._batch_records(
                constraint_records,
                collection=COLLECTION_CONSTRAINTS,
                max_characters=policy["structured_batch_max_characters"],
            ),
            COLLECTION_GRAPH_RECORDS: self._batch_records(
                graph_records,
                collection=COLLECTION_GRAPH_RECORDS,
                max_characters=policy["structured_batch_max_characters"],
            ),
            COLLECTION_PRIOR_REPORTS: self._batch_records(
                prior_report_records,
                collection=COLLECTION_PRIOR_REPORTS,
                max_characters=policy["structured_batch_max_characters"],
            ),
            COLLECTION_POSITIVE_EXAMPLES: self._batch_records(
                positive_examples,
                collection=COLLECTION_POSITIVE_EXAMPLES,
                max_characters=policy["preference_batch_max_characters"],
            ),
            COLLECTION_NEGATIVE_EXAMPLES: self._batch_records(
                negative_examples,
                collection=COLLECTION_NEGATIVE_EXAMPLES,
                max_characters=policy["preference_batch_max_characters"],
            ),
            COLLECTION_FINAL_EVIDENCE: [],
        }

        evidence_ids = [r["evidence_id"] for r in evidence_records]
        current_event_ids = [r["event_id"] for r in current_event_records]
        historical_event_ids = [r["event_id"] for r in historical_event_records]
        constraint_ids = [r["constraint_id"] for r in constraint_records]
        graph_ids = [r["graph_record_id"] for r in graph_records]
        report_ids = [r["report_context_id"] for r in prior_report_records]

        run_id = _clean(run_id)

        context_id = _stable_id(
            "REASONCTX",
            {
                "run_id": run_id,
                "research_question": research_question,
                "accepted_evidence_ids": evidence_ids,
                "current_event_ids": current_event_ids,
                "historical_event_ids": historical_event_ids,
                "constraint_ids": constraint_ids,
                "graph_record_ids": graph_ids,
                "prior_report_ids": report_ids,
                "reasoning_policy": reasoning_policy,
                "preferences": preference_projection,
                "positive_examples": positive_examples,
                "negative_examples": negative_examples,
            },
        )

        output = {
            "reasoning_context_id": context_id,
            "run_id": run_id,
            "research_question": research_question,
            "control_context": {
                "reasoning_policy": reasoning_policy,
                "semantic_user_preferences": preference_projection,
                "notes": {
                    "user_preferences_are_evidence": False,
                    "reasoning_policy_is_evidence": False,
                    "research_depth_modified_here": False,
                },
            },
            "collections": collections,
            "batches": batches,
            "evidence_manifest": {
                "all_accepted_evidence_ids": evidence_ids,
                "examined_evidence_ids": [],
                "relevant_evidence_ids": [],
                "final_reasoning_context_ids": [],
                "referenced_evidence_ids": [],
                "unexamined_evidence_ids": list(evidence_ids),
            },
            "structured_record_manifest": {
                "current_event_ids": current_event_ids,
                "historical_event_ids": historical_event_ids,
                "constraint_ids": constraint_ids,
                "graph_record_ids": graph_ids,
                "prior_report_context_ids": report_ids,
                "positive_preference_example_count": len(positive_examples),
                "negative_preference_example_count": len(negative_examples),
            },
            "context_policy": policy,
            "metadata": {
                "service_version": SERVICE_VERSION,
                "output_schema_version": OUTPUT_SCHEMA_VERSION,
                "context_finalized": False,
                "all_accepted_evidence_retained": True,
                "accepted_evidence_record_count": len(evidence_records),
                "accepted_evidence_batch_count": len(
                    batches[COLLECTION_ACCEPTED_EVIDENCE]
                ),
                "current_event_record_count": len(current_event_records),
                "historical_event_record_count": len(historical_event_records),
                "constraint_record_count": len(constraint_records),
                "graph_record_count": len(graph_records),
                "prior_report_context_count": len(prior_report_records),
                "user_preferences_are_grounding_evidence": False,
                "constraints_are_grounding_evidence": False,
                "event_context_is_grounding_evidence": False,
                "graph_context_is_grounding_evidence": False,
                "prior_reports_are_grounding_evidence": False,
                "source_independence_inferred_here": False,
                "grounding_performed_here": False,
                "search_performed_here": False,
                "authorizes_release": False,
                "modifies_policy": False,
                "modifies_research_depth": False,
                "chooses_model_route": False,
                "private_chain_of_thought_stored": False,
            },
        }

        output["metadata"]["context_fingerprint"] = self.context_fingerprint(output)
        self._assert_initial_completeness(output)
        return output

    def finalize_evidence_selection(
        self,
        *,
        context: Mapping[str, Any],
        examined_evidence_ids: Sequence[str],
        relevant_evidence_ids: Sequence[str],
        referenced_evidence_ids: Sequence[str] = (),
        final_reasoning_context_ids: Optional[Sequence[str]] = None,
    ) -> Dict[str, Any]:
        normalized = self._validate_context(context)

        accepted = set(
            normalized["evidence_manifest"]["all_accepted_evidence_ids"]
        )
        examined = set(_unique_strings(examined_evidence_ids, uppercase=True))
        relevant = set(_unique_strings(relevant_evidence_ids, uppercase=True))
        referenced = set(_unique_strings(referenced_evidence_ids, uppercase=True))

        for label, values in (
            ("examined_evidence_ids", examined),
            ("relevant_evidence_ids", relevant),
            ("referenced_evidence_ids", referenced),
        ):
            unknown = values - accepted
            if unknown:
                raise ReasoningContextCompletenessError(
                    f"{label} contains unknown IDs: "
                    + ", ".join(sorted(unknown))
                )

        unexamined = accepted - examined
        if unexamined:
            raise ReasoningContextCompletenessError(
                "Final reasoning context cannot be created while accepted "
                "evidence remains unexamined: "
                + ", ".join(sorted(unexamined))
            )

        if not relevant <= examined:
            raise ReasoningContextCompletenessError(
                "relevant_evidence_ids must be a subset of examined_evidence_ids."
            )

        if not referenced <= examined:
            raise ReasoningContextCompletenessError(
                "referenced_evidence_ids must be a subset of examined_evidence_ids."
            )

        if final_reasoning_context_ids is None:
            final_ids = relevant | referenced
        else:
            final_ids = set(
                _unique_strings(final_reasoning_context_ids, uppercase=True)
            )

        unknown_final = final_ids - accepted
        if unknown_final:
            raise ReasoningContextCompletenessError(
                "final_reasoning_context_ids contains unknown IDs: "
                + ", ".join(sorted(unknown_final))
            )

        if not referenced <= final_ids:
            raise ReasoningContextCompletenessError(
                "referenced_evidence_ids must be a subset of "
                "final_reasoning_context_ids."
            )

        if not final_ids <= examined:
            raise ReasoningContextCompletenessError(
                "final_reasoning_context_ids must be a subset of examined_evidence_ids."
            )

        output = deepcopy(normalized)

        accepted_records = {
            record["evidence_id"]: record
            for record in output["collections"][COLLECTION_ACCEPTED_EVIDENCE]
        }

        final_records = [
            deepcopy(accepted_records[evidence_id])
            for evidence_id in sorted(final_ids)
        ]

        output["collections"][COLLECTION_FINAL_EVIDENCE] = final_records
        output["batches"][COLLECTION_FINAL_EVIDENCE] = self._batch_records(
            final_records,
            collection=COLLECTION_FINAL_EVIDENCE,
            max_characters=output["context_policy"][
                "evidence_batch_max_characters"
            ],
        )

        output["evidence_manifest"] = {
            "all_accepted_evidence_ids": sorted(accepted),
            "examined_evidence_ids": sorted(examined),
            "relevant_evidence_ids": sorted(relevant),
            "final_reasoning_context_ids": sorted(final_ids),
            "referenced_evidence_ids": sorted(referenced),
            "unexamined_evidence_ids": [],
        }

        output["metadata"]["context_finalized"] = True
        output["metadata"]["all_accepted_evidence_examined"] = True
        output["metadata"]["final_reasoning_evidence_record_count"] = len(
            final_records
        )
        output["metadata"]["final_reasoning_evidence_batch_count"] = len(
            output["batches"][COLLECTION_FINAL_EVIDENCE]
        )
        output["metadata"]["context_fingerprint"] = self.context_fingerprint(output)

        self.assert_finalized_completeness(output)
        return output

    def iter_batches(
        self,
        context: Mapping[str, Any],
        *,
        collection: str,
    ) -> Iterator[Dict[str, Any]]:
        context = self._validate_context(context)
        collection = _enum(collection)

        if collection not in ALL_COLLECTIONS:
            raise ReasoningContextValidationError(
                f"Unknown collection={collection!r}."
            )

        for batch in context["batches"].get(collection, []):
            yield deepcopy(batch)

    def get_batch(
        self,
        context: Mapping[str, Any],
        *,
        collection: str,
        batch_index: int,
    ) -> Dict[str, Any]:
        context = self._validate_context(context)
        collection = _enum(collection)

        if collection not in ALL_COLLECTIONS:
            raise ReasoningContextValidationError(
                f"Unknown collection={collection!r}."
            )

        try:
            batch_index = int(batch_index)
        except (TypeError, ValueError) as exc:
            raise ReasoningContextValidationError(
                "batch_index must be an integer."
            ) from exc

        batches = context["batches"].get(collection, [])

        if not 0 <= batch_index < len(batches):
            raise ReasoningContextValidationError(
                f"batch_index out of range for {collection}."
            )

        return deepcopy(batches[batch_index])

    def render_control_context(
        self,
        context: Mapping[str, Any],
    ) -> str:
        context = self._validate_context(context)

        return json.dumps(
            {
                "reasoning_context_id": context["reasoning_context_id"],
                "research_question": context["research_question"],
                "control_context": context["control_context"],
                "epistemic_boundary": {
                    "control_context_is_evidence": False,
                    "user_preferences_are_evidence": False,
                },
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    def render_batch(
        self,
        context: Mapping[str, Any],
        *,
        collection: str,
        batch_index: int,
    ) -> str:
        return json.dumps(
            self.get_batch(
                context,
                collection=collection,
                batch_index=batch_index,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )

    def assert_finalized_completeness(
        self,
        context: Mapping[str, Any],
    ) -> None:
        context = self._validate_context(context)
        manifest = context["evidence_manifest"]

        accepted = set(manifest["all_accepted_evidence_ids"])
        examined = set(manifest["examined_evidence_ids"])
        relevant = set(manifest["relevant_evidence_ids"])
        final_ids = set(manifest["final_reasoning_context_ids"])
        referenced = set(manifest["referenced_evidence_ids"])
        unexamined = set(manifest["unexamined_evidence_ids"])

        if accepted - examined:
            raise ReasoningContextCompletenessError(
                "Not all accepted evidence has been examined."
            )

        if unexamined:
            raise ReasoningContextCompletenessError(
                "unexamined_evidence_ids must be empty in finalized context."
            )

        if not relevant <= examined:
            raise ReasoningContextCompletenessError(
                "relevant evidence must be a subset of examined evidence."
            )

        if not referenced <= final_ids:
            raise ReasoningContextCompletenessError(
                "referenced evidence must be a subset of final reasoning context."
            )

        if not final_ids <= examined:
            raise ReasoningContextCompletenessError(
                "final reasoning context must be a subset of examined evidence."
            )

        final_record_ids = {
            record["evidence_id"]
            for record in context["collections"][COLLECTION_FINAL_EVIDENCE]
        }

        if final_record_ids != final_ids:
            raise ReasoningContextCompletenessError(
                "Final-evidence collection does not match final_reasoning_context_ids."
            )

        final_batch_ids = {
            record["evidence_id"]
            for batch in context["batches"][COLLECTION_FINAL_EVIDENCE]
            for record in batch["records"]
        }

        if final_batch_ids != final_ids:
            raise ReasoningContextCompletenessError(
                "Final-evidence batches do not match final_reasoning_context_ids."
            )

    def context_fingerprint(
        self,
        context: Mapping[str, Any],
    ) -> str:
        payload = deepcopy(dict(context))
        metadata = payload.get("metadata")

        if isinstance(metadata, Mapping):
            metadata = dict(metadata)
            metadata.pop("context_fingerprint", None)
            payload["metadata"] = metadata

        return _sha256(payload)

    # ------------------------------------------------------------------
    # Policy projections
    # ------------------------------------------------------------------

    def _context_policy(
        self,
        blueprint: Mapping[str, Any],
    ) -> Dict[str, Any]:
        raw = blueprint.get("reasoning_context_policy") or {}

        if not isinstance(raw, Mapping):
            raise ReasoningContextValidationError(
                "blueprint.reasoning_context_policy must be an object."
            )

        def parse(key: str, default: int) -> int:
            value = raw.get(key, default)

            if isinstance(value, bool):
                raise ReasoningContextValidationError(
                    f"{key} must be an integer, not bool."
                )

            try:
                numeric = int(value)
            except (TypeError, ValueError) as exc:
                raise ReasoningContextValidationError(
                    f"{key} must be an integer."
                ) from exc

            if numeric < 1000:
                raise ReasoningContextValidationError(
                    f"{key} must be >= 1000."
                )

            return numeric

        return {
            "evidence_batch_max_characters": parse(
                "evidence_batch_max_characters",
                self.default_evidence_batch_max_characters,
            ),
            "structured_batch_max_characters": parse(
                "structured_batch_max_characters",
                self.default_structured_batch_max_characters,
            ),
            "preference_batch_max_characters": parse(
                "preference_batch_max_characters",
                self.default_preference_batch_max_characters,
            ),
            "batching_strategy": "complete_records_no_silent_truncation",
            "oversized_single_record_policy": "emit_one_record_batch_untruncated",
        }

    @staticmethod
    def _reasoning_policy_projection(
        blueprint: Mapping[str, Any],
    ) -> Dict[str, Any]:
        reasoning = blueprint.get("reasoning_policy") or {}

        if not isinstance(reasoning, Mapping):
            raise ReasoningContextValidationError(
                "blueprint.reasoning_policy must be an object."
            )

        allowed = {
            "desired_depth",
            "preferred_patterns",
            "avoid_patterns",
            "analytical_lenses",
            "require_cross_event_synthesis",
            "require_mechanism",
            "require_second_order_effects",
            "require_counterevidence",
            "require_explicit_uncertainty",
            "reject_fact_restatement_as_hidden_insight",
        }

        return {
            key: _json_safe(reasoning[key])
            for key in sorted(allowed)
            if key in reasoning
        }

    # ------------------------------------------------------------------
    # Preference projection
    # ------------------------------------------------------------------

    def _preference_projection(
        self,
        user_preferences: Mapping[str, Any],
    ) -> Dict[str, Any]:
        output = {}

        for key in sorted(PREFERENCE_ALLOWED_FIELDS):
            if key not in user_preferences:
                continue

            if key in {"positive_examples", "negative_examples"}:
                output[key] = self._normalize_examples(
                    user_preferences[key],
                    polarity=(
                        "positive"
                        if key == "positive_examples"
                        else "negative"
                    ),
                )

            elif key in {
                "preferred_patterns",
                "avoid_patterns",
                "analytical_lenses",
                "preferred_insight_ids",
                "rejected_insight_ids",
            }:
                output[key] = _unique_strings(user_preferences[key])

            else:
                output[key] = _clean(user_preferences[key])

        ignored = sorted(
            {
                _enum(key)
                for key in user_preferences
                if _enum(key) not in PREFERENCE_ALLOWED_FIELDS
            }
        )

        if ignored:
            output["_ignored_unknown_preference_fields"] = ignored

        return output

    @staticmethod
    def _normalize_examples(
        value: Any,
        *,
        polarity: str,
    ) -> list[Dict[str, Any]]:
        if value is None:
            return []

        if isinstance(value, (str, Mapping)):
            value = [value]

        if not isinstance(value, (list, tuple, set)):
            return []

        output = []

        for index, item in enumerate(value, start=1):
            if isinstance(item, Mapping):
                text = _clean(
                    item.get("text")
                    or item.get("insight")
                    or item.get("example")
                )
                reason = _clean(item.get("reason"))
                insight_id = _clean(item.get("insight_id")).upper()
            else:
                text = _clean(item)
                reason = ""
                insight_id = ""

            if not text:
                continue

            output.append(
                {
                    "preference_example_id": _stable_id(
                        "PREFCTX",
                        {
                            "polarity": polarity,
                            "ordinal": index,
                            "insight_id": insight_id,
                            "text": text,
                            "reason": reason,
                        },
                    ),
                    "polarity": polarity,
                    "insight_id": insight_id,
                    "text": text,
                    "reason": reason,
                    "is_grounding_evidence": False,
                }
            )

        return output

    # ------------------------------------------------------------------
    # Evidence / structured record normalization
    # ------------------------------------------------------------------

    def _extract_evidence_records(
        self,
        value: Any,
    ) -> list[Dict[str, Any]]:
        candidates = []

        if isinstance(value, Mapping):
            recognized = False

            for key in (
                "facts",
                "metrics",
                "entity_triples",
                "evidence",
                "accepted_evidence",
                "records",
            ):
                raw = value.get(key)
                if isinstance(raw, list):
                    recognized = True
                    candidates.extend(
                        item
                        for item in raw
                        if isinstance(item, Mapping)
                    )

            if not recognized and self._evidence_id(value):
                candidates.append(value)

        elif isinstance(value, list):
            candidates.extend(
                item
                for item in value
                if isinstance(item, Mapping)
            )

        output = []
        seen = {}

        for index, raw in enumerate(candidates):
            evidence_id = self._evidence_id(raw)

            if not evidence_id:
                raise ReasoningContextValidationError(
                    f"Accepted evidence record {index} has no stable evidence ID."
                )

            record = deepcopy(dict(raw))
            record["evidence_id"] = evidence_id
            fingerprint = _sha256(record)

            if evidence_id in seen:
                if seen[evidence_id] != fingerprint:
                    raise ReasoningContextValidationError(
                        "Duplicate evidence ID with different content: "
                        + evidence_id
                    )
                continue

            seen[evidence_id] = fingerprint
            output.append(record)

        output.sort(key=lambda record: record["evidence_id"])
        return output

    @staticmethod
    def _evidence_id(record: Mapping[str, Any]) -> str:
        return _clean(
            record.get("evidence_id")
            or record.get("fact_id")
            or record.get("metric_id")
            or record.get("triple_id")
            or record.get("id")
        ).upper()

    def _extract_event_records(
        self,
        value: Any,
        *,
        current: bool,
    ) -> list[Dict[str, Any]]:
        candidates = []

        if isinstance(value, Mapping):
            for key in (
                "events",
                "current_events",
                "historical_events",
                "records",
            ):
                raw = value.get(key)
                if isinstance(raw, list):
                    candidates.extend(
                        item
                        for item in raw
                        if isinstance(item, Mapping)
                    )

            if (
                not candidates
                and (value.get("event_id") or value.get("id"))
            ):
                candidates.append(value)

        elif isinstance(value, list):
            candidates.extend(
                item
                for item in value
                if isinstance(item, Mapping)
            )

        output = []
        seen = {}

        for index, raw in enumerate(candidates):
            event_id = _clean(
                raw.get("event_id")
                or raw.get("id")
            ).upper()

            if not event_id:
                raise ReasoningContextValidationError(
                    f"Event record {index} has no stable event_id."
                )

            record = deepcopy(dict(raw))
            record["event_id"] = event_id
            record["context_time_scope"] = (
                "current" if current else "historical"
            )
            record["is_grounding_evidence"] = False

            fingerprint = _sha256(record)

            if event_id in seen:
                if seen[event_id] != fingerprint:
                    raise ReasoningContextValidationError(
                        "Duplicate event ID with different content: "
                        + event_id
                    )
                continue

            seen[event_id] = fingerprint
            output.append(record)

        output.sort(key=lambda record: record["event_id"])
        return output

    def _extract_constraint_records(
        self,
        value: Any,
    ) -> list[Dict[str, Any]]:
        candidates = []

        if isinstance(value, Mapping):
            raw = value.get("constraints")
            if isinstance(raw, list):
                candidates.extend(
                    item
                    for item in raw
                    if isinstance(item, Mapping)
                )
            elif value.get("constraint_id"):
                candidates.append(value)

        elif isinstance(value, list):
            candidates.extend(
                item
                for item in value
                if isinstance(item, Mapping)
            )

        output = []
        seen = {}

        for index, raw in enumerate(candidates):
            constraint_id = _clean(
                raw.get("constraint_id")
                or raw.get("id")
            ).upper()

            if not constraint_id:
                raise ReasoningContextValidationError(
                    f"Constraint record {index} has no stable constraint_id."
                )

            record = deepcopy(dict(raw))
            record["constraint_id"] = constraint_id
            record["is_grounding_evidence"] = False

            fingerprint = _sha256(record)

            if constraint_id in seen:
                if seen[constraint_id] != fingerprint:
                    raise ReasoningContextValidationError(
                        "Duplicate constraint ID with different content: "
                        + constraint_id
                    )
                continue

            seen[constraint_id] = fingerprint
            output.append(record)

        output.sort(key=lambda record: record["constraint_id"])
        return output

    def _extract_graph_records(
        self,
        value: Any,
    ) -> list[Dict[str, Any]]:
        if value is None or value == "":
            return []

        output = []

        if isinstance(value, Mapping):
            nodes = value.get("nodes")
            edges = value.get("edges")
            relationships = value.get("relationships")

            if isinstance(nodes, list):
                for node in nodes:
                    if isinstance(node, Mapping):
                        output.append(self._graph_record("node", node))

            edge_items = (
                edges
                if isinstance(edges, list)
                else relationships
                if isinstance(relationships, list)
                else []
            )

            for edge in edge_items:
                if isinstance(edge, Mapping):
                    output.append(self._graph_record("edge", edge))

            if not output:
                output.append(
                    self._graph_record("graph_context", value)
                )

        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Mapping):
                    record_type = (
                        "edge"
                        if "source" in item and "target" in item
                        else "graph_record"
                    )
                    output.append(
                        self._graph_record(record_type, item)
                    )

        else:
            output.append(
                self._graph_record(
                    "graph_text",
                    {"text": _clean(value)},
                )
            )

        unique = {}

        for record in output:
            record_id = record["graph_record_id"]
            fingerprint = _sha256(record)

            if (
                record_id in unique
                and unique[record_id]["fingerprint"] != fingerprint
            ):
                raise ReasoningContextValidationError(
                    "Graph record ID collision."
                )

            unique[record_id] = {
                "fingerprint": fingerprint,
                "record": record,
            }

        return [
            unique[key]["record"]
            for key in sorted(unique)
        ]

    @staticmethod
    def _graph_record(
        record_type: str,
        value: Mapping[str, Any],
    ) -> Dict[str, Any]:
        explicit_id = _clean(
            value.get("relationship_id")
            or value.get("edge_id")
            or value.get("node_id")
            or value.get("id")
        ).upper()

        record_id = (
            explicit_id
            if explicit_id
            else _stable_id(
                "GRAPHCTX",
                {
                    "record_type": record_type,
                    "value": value,
                },
            )
        )

        return {
            "graph_record_id": record_id,
            "graph_record_type": record_type,
            "record": deepcopy(dict(value)),
            "is_grounding_evidence": False,
        }

    def _extract_prior_report_records(
        self,
        value: Any,
    ) -> list[Dict[str, Any]]:
        if value is None or value == "":
            return []

        candidates = []

        if isinstance(value, Mapping):
            raw = value.get("reports")
            if isinstance(raw, list):
                candidates.extend(
                    item
                    for item in raw
                    if isinstance(item, Mapping)
                )
            else:
                candidates.append(value)

        elif isinstance(value, list):
            candidates.extend(
                item
                for item in value
                if isinstance(item, Mapping)
            )

        output = []
        seen = {}

        for index, raw in enumerate(candidates):
            report_id = _clean(
                raw.get("report_id")
                or raw.get("id")
            ).upper()

            context_id = (
                report_id
                if report_id
                else _stable_id(
                    "REPORTCTX",
                    {
                        "ordinal": index,
                        "report": raw,
                    },
                )
            )

            record = {
                "report_context_id": context_id,
                "report_id": report_id,
                "record": deepcopy(dict(raw)),
                "is_grounding_evidence": False,
            }

            fingerprint = _sha256(record)

            if context_id in seen:
                if seen[context_id] != fingerprint:
                    raise ReasoningContextValidationError(
                        "Duplicate prior-report context ID with different content: "
                        + context_id
                    )
                continue

            seen[context_id] = fingerprint
            output.append(record)

        output.sort(key=lambda record: record["report_context_id"])
        return output

    # ------------------------------------------------------------------
    # Batching / completeness
    # ------------------------------------------------------------------

    def _batch_records(
        self,
        records: Sequence[Mapping[str, Any]],
        *,
        collection: str,
        max_characters: int,
    ) -> list[Dict[str, Any]]:
        if collection not in ALL_COLLECTIONS:
            raise ReasoningContextValidationError(
                f"Unknown collection={collection!r}."
            )

        batches = []
        current = []
        current_size = 0

        def emit(
            batch_records: list[Dict[str, Any]],
            *,
            oversized: bool,
        ) -> None:
            if not batch_records:
                return

            batch_index = len(batches)

            batches.append(
                {
                    "batch_id": _stable_id(
                        "CTXBATCH",
                        {
                            "collection": collection,
                            "batch_index": batch_index,
                            "records": batch_records,
                        },
                    ),
                    "collection": collection,
                    "batch_index": batch_index,
                    "record_count": len(batch_records),
                    "serialized_character_count": sum(
                        _record_size(record)
                        for record in batch_records
                    ),
                    "target_max_characters": max_characters,
                    "oversized_single_record": oversized,
                    "records": deepcopy(batch_records),
                }
            )

        for raw in records:
            record = deepcopy(dict(raw))
            size = _record_size(record)

            if size > max_characters:
                if current:
                    emit(current, oversized=False)
                    current = []
                    current_size = 0

                emit([record], oversized=True)
                continue

            if (
                current
                and current_size + size > max_characters
            ):
                emit(current, oversized=False)
                current = []
                current_size = 0

            current.append(record)
            current_size += size

        if current:
            emit(current, oversized=False)

        original_fingerprints = [
            _sha256(record)
            for record in records
        ]
        batched_fingerprints = [
            _sha256(record)
            for batch in batches
            for record in batch["records"]
        ]

        if original_fingerprints != batched_fingerprints:
            raise ReasoningContextCompletenessError(
                f"Batch completeness invariant failed for {collection}."
            )

        return batches

    def _assert_initial_completeness(
        self,
        context: Mapping[str, Any],
    ) -> None:
        accepted_records = context["collections"][COLLECTION_ACCEPTED_EVIDENCE]
        accepted_ids = [
            record["evidence_id"]
            for record in accepted_records
        ]
        manifest_ids = context["evidence_manifest"][
            "all_accepted_evidence_ids"
        ]

        if accepted_ids != manifest_ids:
            raise ReasoningContextCompletenessError(
                "Accepted evidence collection does not match manifest."
            )

        batched_ids = [
            record["evidence_id"]
            for batch in context["batches"][COLLECTION_ACCEPTED_EVIDENCE]
            for record in batch["records"]
        ]

        if accepted_ids != batched_ids:
            raise ReasoningContextCompletenessError(
                "Accepted evidence batches do not preserve every record exactly once."
            )

    @staticmethod
    def _validate_context(
        context: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(context, Mapping):
            raise ReasoningContextValidationError(
                "context must be an object."
            )

        required = {
            "reasoning_context_id",
            "research_question",
            "control_context",
            "collections",
            "batches",
            "evidence_manifest",
            "context_policy",
            "metadata",
        }

        missing = sorted(required - set(context))

        if missing:
            raise ReasoningContextValidationError(
                "Context missing required fields: "
                + ", ".join(missing)
            )

        return deepcopy(dict(context))


ReasoningContextCompiler = ReasoningContextService