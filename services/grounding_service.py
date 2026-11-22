
"""
Evidence-grounding service for structured research claims.

Recommended location:
    services/grounding_service.py

Role
----
StrategicInferenceAgent / structured report claims
        +
validated evidence records
        ↓
GroundingService
        ↓
claim-level support assessment
        ↓
RedTeamAgent / PolicyEnforcementService / final report release

Core principle
--------------
Semantic similarity is NOT treated as factual support.

Grounding evaluates an atomic claim against the exact evidence records that
are supplied to it, then applies deterministic validation around:
- evidence-ID validity;
- support/counterevidence ID allowlisting;
- attribution;
- temporal consistency;
- numeric consistency;
- multi-evidence requirements.

Allowed semantic statuses
-------------------------
supported
partially_supported
inference_supported
unsupported
contradicted

Responsibilities
----------------
- Ground atomic claims against supplied evidence.
- Preserve multi-evidence support.
- Validate cited evidence IDs.
- Reject model-invented evidence IDs.
- Distinguish direct support from inference-supported claims.
- Surface unsupported parts, contradictions, and validation failures.
- Ground all supplied claims without silent truncation.

Non-responsibilities
--------------------
- No evidence retrieval/index search.
- No report generation.
- No release decision.
- No policy thresholding.
- No source-independence judgement.
- No strategic inference.
- No numeric confidence score.
- No arbitrary top-k or character truncation.

Recommended pipeline
--------------------
Report statement
    ↓
atomic structured claim
    ↓
candidate/cited evidence records
    ↓
support classifier / entailment model
    ↓
deterministic ID + attribution + temporal + numeric checks
    ↓
GroundingResult
    ↓
RedTeamAgent / PolicyEnforcementService

The model may classify support, but deterministic code controls what evidence
IDs are legal and whether required validation checks passed.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass, asdict
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Set

logger = logging.getLogger("event_intelligence.services.grounding_service")


SERVICE_VERSION = "grounding-service-v1.2-insight-aggregate-contract"
GROUNDING_PROMPT_VERSION = "grounding-v1.4-forecasts-judged-on-premises"

ALLOWED_GROUNDING_STATUSES = {
    "supported",
    "partially_supported",
    "inference_supported",
    "unsupported",
    "contradicted",
}

ALLOWED_EPISTEMIC_TYPES = {
    "fact",
    "connection",
    "mechanism",
    "hidden_insight",
    "second_order_effect",
    "counterevidence",
    "alternative_explanation",
    "watch_signal",
    "unknown",
}


class GroundingServiceError(RuntimeError):
    """Base exception for grounding failures."""


class GroundingValidationError(GroundingServiceError):
    """Raised when grounding input/output violates the service contract."""


@dataclass(frozen=True)
class GroundedClaim:
    claim_id: str
    claim_text: str
    epistemic_type: str
    grounding_status: str
    cited_evidence_ids: List[str]
    valid_evidence_ids: List[str]
    invalid_evidence_ids: List[str]
    supporting_evidence_ids: List[str]
    contradicted_evidence_ids: List[str]
    citation_valid: bool
    attribution_valid: bool
    temporal_valid: bool
    numeric_valid: bool
    multi_evidence_required: bool
    multi_evidence_satisfied: bool
    unsupported_parts: List[str]
    rationale: str
    metadata: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_key(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return text.strip("_")


def _unique_strings(values: Any) -> List[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set)):
        values = [values]

    result: List[str] = []
    seen: Set[str] = set()

    for value in values:
        if isinstance(value, Mapping):
            value = (
                value.get("evidence_id")
                or value.get("id")
                or value.get("fact_id")
                or value.get("metric_id")
                or value.get("triple_id")
                or ""
            )

        text = _clean(value)

        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return result


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value

    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]

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


def _evidence_id(record: Mapping[str, Any]) -> str:
    return _clean(
        record.get("evidence_id")
        or record.get("id")
        or record.get("fact_id")
        or record.get("metric_id")
        or record.get("triple_id")
    )


def _evidence_text(record: Mapping[str, Any]) -> str:
    """
    Extract the human-readable evidence body without discarding the original
    structured record. The full record is still supplied to the classifier.
    """
    for field in (
        "text",
        "statement",
        "fact",
        "claim",
        "summary",
        "excerpt",
        "exact_excerpt",
        "description",
    ):
        value = _clean(
            record.get(field)
        )
        if value:
            return value

    # Metrics and triples may not have one prose field.
    if (
        record.get("value") is not None
        or record.get("unit") is not None
    ):
        pieces = [
            _clean(record.get("metric_name") or record.get("name")),
            _clean(record.get("value")),
            _clean(record.get("unit")),
            _clean(record.get("period")),
        ]
        text = " ".join(
            piece
            for piece in pieces
            if piece
        )
        if text:
            return text

    if (
        record.get("source") is not None
        and record.get("target") is not None
    ):
        pieces = [
            _clean(record.get("source")),
            _clean(record.get("relation") or record.get("relation_type")),
            _clean(record.get("target")),
        ]
        text = " ".join(
            piece
            for piece in pieces
            if piece
        )
        if text:
            return text

    return ""


def _normalize_status(value: Any) -> str:
    status = _normalize_key(value)

    aliases = {
        "partial": "partially_supported",
        "partially": "partially_supported",
        "inference": "inference_supported",
        "supported_inference": "inference_supported",
        "not_supported": "unsupported",
        "contradiction": "contradicted",
    }

    status = aliases.get(
        status,
        status,
    )

    return (
        status
        if status in ALLOWED_GROUNDING_STATUSES
        else ""
    )


def _normalize_epistemic_type(value: Any) -> str:
    normalized = _normalize_key(value)

    aliases = {
        "hidden_insights": "hidden_insight",
        "second_order": "second_order_effect",
        "second_order_effects": "second_order_effect",
        "watch": "watch_signal",
        "counter_signal": "counterevidence",
        "counter_signal_evidence": "counterevidence",
        "alternative": "alternative_explanation",
    }

    normalized = aliases.get(
        normalized,
        normalized,
    )

    return (
        normalized
        if normalized in ALLOWED_EPISTEMIC_TYPES
        else "unknown"
    )


def _parse_json_object(
    raw: str | Mapping[str, Any],
) -> Dict[str, Any]:
    if isinstance(raw, Mapping):
        return dict(raw)

    text = str(raw).strip()

    if not text:
        raise GroundingValidationError(
            "Support classifier returned an empty response."
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
        try:
            parsed = json.loads(
                fenced.group(1)
            )
        except json.JSONDecodeError as exc:
            raise GroundingValidationError(
                "Support classifier returned malformed fenced JSON."
            ) from exc

        if isinstance(parsed, dict):
            return parsed

    first = text.find("{")
    last = text.rfind("}")

    if first >= 0 and last > first:
        try:
            parsed = json.loads(
                text[first:last + 1]
            )
        except json.JSONDecodeError as exc:
            raise GroundingValidationError(
                "Support classifier response did not contain valid JSON."
            ) from exc

        if isinstance(parsed, dict):
            return parsed

    raise GroundingValidationError(
        "Support classifier response did not contain a JSON object."
    )


class GroundingService:
    """
    Provider-agnostic grounding service.

    Parameters
    ----------
    support_classifier:
        Callable receiving one prompt and returning either a JSON string or
        Mapping with:
            status
            supporting_evidence_ids
            contradicted_evidence_ids
            attribution_valid
            temporal_valid
            numeric_valid
            unsupported_parts
            rationale

        The classifier can be an LLM/NLI wrapper, but it is strictly bounded to
        the supplied evidence records.

    model_name:
        Metadata only; model selection belongs to ModelRouterService.

    prompt_version:
        Used for reproducibility and telemetry.
    """

    def __init__(
        self,
        *,
        support_classifier: Optional[Callable[
            [str],
            str | Mapping[str, Any],
        ]] = None,
        model_name: str = "",
        prompt_version: str = GROUNDING_PROMPT_VERSION,
    ) -> None:
        self._classifier_service: Any = None
        self.classifier_provider = "external"

        if support_classifier is None:
            (
                support_classifier,
                resolved_model_name,
                classifier_provider,
                classifier_service,
            ) = self._build_default_support_classifier()

            if not _clean(model_name):
                model_name = resolved_model_name

            self.classifier_provider = classifier_provider
            self._classifier_service = classifier_service

        if not callable(
            support_classifier
        ):
            raise GroundingValidationError(
                "support_classifier must be callable."
            )

        self.support_classifier = (
            support_classifier
        )
        self.model_name = _clean(
            model_name
        )
        self.prompt_version = _clean(
            prompt_version
        )

        if not self.prompt_version:
            raise GroundingValidationError(
                "prompt_version cannot be empty."
            )

    @staticmethod
    def _build_default_support_classifier() -> tuple[
        Callable[[str], str | Mapping[str, Any]],
        str,
        str,
        Any,
    ]:
        """
        Construct the semantic support classifier used by grounding.

        Routing policy:
            GROUNDING_LLM_PROVIDER=auto|gemini|local|ollama

        auto prefers Gemini when GEMINI_API_KEY is present. This means the
        orchestrator can simply call ``GroundingService()`` and will no longer
        fall into its legacy local-LLM constructor fallback.
        """
        provider = _clean(
            os.getenv(
                "GROUNDING_LLM_PROVIDER",
                "auto",
            )
        ).casefold() or "auto"

        if provider not in {
            "auto",
            "gemini",
            "local",
            "ollama",
        }:
            raise GroundingValidationError(
                "GROUNDING_LLM_PROVIDER must be "
                "auto, gemini, local or ollama."
            )

        use_gemini = (
            provider == "gemini"
            or (
                provider == "auto"
                and bool(
                    _clean(
                        os.getenv(
                            "GEMINI_API_KEY"
                        )
                    )
                )
            )
        )

        if use_gemini:
            try:
                from services.gemini_llm_service import (
                    GeminiLLMService,
                )
            except Exception as exc:
                raise GroundingValidationError(
                    "Gemini grounding was selected but "
                    "services.gemini_llm_service could not be imported."
                ) from exc

            service = GeminiLLMService.grounding()

            def classify(prompt: str) -> str:
                return service.ask(
                    prompt,
                    temperature=0.0,
                    response_format="json",
                    metadata={
                        "stage": "grounding",
                        "purpose": "claim_support_classification",
                    },
                )

            return (
                classify,
                service.model_name,
                "gemini",
                service,
            )

        # Explicit/local fallback. The hosted route is preferred for this app
        # because claim-level semantic support classification repeatedly timed
        # out on the local Ollama path.
        try:
            from services.llm_service import (
                LLMService,
            )
        except Exception as exc:
            raise GroundingValidationError(
                "Local grounding was selected but services.llm_service "
                "could not be imported."
            ) from exc

        local_model = (
            _clean(
                os.getenv(
                    "OLLAMA_GROUNDING_MODEL"
                )
            )
            or _clean(
                os.getenv(
                    "OLLAMA_MODEL"
                )
            )
            or "qwen2.5:3b"
        )

        try:
            timeout_seconds = float(
                os.getenv(
                    "OLLAMA_GROUNDING_TIMEOUT_SECONDS",
                    "90",
                )
            )
        except (TypeError, ValueError):
            timeout_seconds = 90.0

        service = LLMService(
            model_name=local_model,
            timeout_seconds=max(
                20.0,
                min(
                    timeout_seconds,
                    180.0,
                ),
            ),
            max_retries=0,
            keep_alive="10m",
        )

        return (
            service.ask,
            _clean(
                getattr(
                    service,
                    "model_name",
                    local_model,
                )
            ),
            "ollama",
            service,
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ground_claim(
        self,
        *,
        claim_id: str,
        claim_text: str,
        evidence_ids: Sequence[str],
        evidence_records: Sequence[
            Mapping[str, Any]
        ],
        epistemic_type: str = "unknown",
        multi_evidence_required: bool = False,
    ) -> GroundedClaim:
        """
        Ground one atomic claim against exactly the cited evidence IDs.

        No search or evidence expansion occurs here.
        """
        claim_id = _clean(
            claim_id
        )
        claim_text = _clean(
            claim_text
        )
        epistemic_type = (
            _normalize_epistemic_type(
                epistemic_type
            )
        )

        if not claim_id:
            raise GroundingValidationError(
                "claim_id cannot be empty."
            )

        if not claim_text:
            raise GroundingValidationError(
                f"Claim {claim_id!r} cannot have empty claim_text."
            )

        normalized_evidence = (
            self._normalize_evidence_records(
                evidence_records
            )
        )

        evidence_by_id = {
            record["evidence_id"]: record
            for record in normalized_evidence
        }

        cited_ids = _unique_strings(
            evidence_ids
        )

        valid_ids = [
            evidence_id
            for evidence_id in cited_ids
            if evidence_id in evidence_by_id
        ]

        invalid_ids = [
            evidence_id
            for evidence_id in cited_ids
            if evidence_id not in evidence_by_id
        ]

        citation_valid = (
            bool(cited_ids)
            and not invalid_ids
        )

        # A claim with no valid evidence cannot be classified as grounded.
        if not valid_ids:
            return GroundedClaim(
                claim_id=claim_id,
                claim_text=claim_text,
                epistemic_type=epistemic_type,
                grounding_status="unsupported",
                cited_evidence_ids=cited_ids,
                valid_evidence_ids=[],
                invalid_evidence_ids=invalid_ids,
                supporting_evidence_ids=[],
                contradicted_evidence_ids=[],
                citation_valid=False,
                attribution_valid=False,
                temporal_valid=False,
                numeric_valid=False,
                multi_evidence_required=bool(
                    multi_evidence_required
                ),
                multi_evidence_satisfied=False,
                unsupported_parts=[
                    "No valid cited evidence was available for grounding."
                ],
                rationale=(
                    "The claim could not be grounded because none of its "
                    "cited evidence IDs resolved to supplied evidence records."
                ),
                metadata=self._claim_metadata(
                    claim_id=claim_id,
                    claim_text=claim_text,
                    epistemic_type=epistemic_type,
                    valid_records=[],
                ),
            )

        candidate_records = [
            evidence_by_id[
                evidence_id
            ]
            for evidence_id in valid_ids
        ]

        prompt = self._build_prompt(
            claim_id=claim_id,
            claim_text=claim_text,
            epistemic_type=epistemic_type,
            evidence_records=candidate_records,
            multi_evidence_required=bool(
                multi_evidence_required
            ),
        )

        # v13.18 (22 Nov 2026): the verdict may already be here from a batched call (ground_claims
        # sends several claims in one request). A batched verdict that does not validate is
        # re-checked on its own, so batching never changes what is accepted.
        prefetched = self._take_prefetched(prompt)
        if prefetched is not None:
            try:
                result = self._validate_classifier_output(
                    parsed=prefetched,
                    allowed_evidence_ids=set(valid_ids),
                )
            except GroundingValidationError:
                prefetched = None
        if prefetched is None:
            parsed = _parse_json_object(
                self._classify(
                    prompt
                )
            )

            result = self._validate_classifier_output(
                parsed=parsed,
                allowed_evidence_ids=set(
                    valid_ids
                ),
            )

        status = result[
            "status"
        ]

        supporting_ids = result[
            "supporting_evidence_ids"
        ]
        contradicted_ids = result[
            "contradicted_evidence_ids"
        ]

        multi_evidence_satisfied = (
            len(
                supporting_ids
            )
            >= 2
            if multi_evidence_required
            else bool(
                supporting_ids
            )
        )

        # Deterministic fail-closed adjustments.
        #
        # 1. Invalid citation IDs mean a claim cannot remain fully "supported".
        # 2. Required multi-evidence support must actually contain >=2 legal
        #    support IDs.
        # 3. A "supported" status requires at least one supporting evidence ID.
        # 4. "contradicted" requires at least one contradiction ID.
        if (
            invalid_ids
            and status
            in {
                "supported",
                "inference_supported",
            }
        ):
            status = (
                "partially_supported"
            )

        # v10.2 (15 Nov 2026): only a FACT is downgraded for resting on one record. A judgment
        # (conclusion, mechanism, connection, forecast) that follows from one record keeps its
        # verdict and is labelled single-source by the caller, as the claim gate (v4.0) intends;
        # the downgrade made a lab thesis "unverified" and deleted shown sentences.
        if (
            multi_evidence_required
            and not multi_evidence_satisfied
            and status
            in {
                "supported",
                "inference_supported",
            }
            and epistemic_type == "fact"
        ):
            status = (
                "partially_supported"
            )

        if (
            status
            in {
                "supported",
                "inference_supported",
                "partially_supported",
            }
            and not supporting_ids
        ):
            status = "unsupported"

        if (
            status == "contradicted"
            and not contradicted_ids
        ):
            raise GroundingValidationError(
                "Classifier returned contradicted without a legal "
                "contradicted_evidence_id."
            )

        # Validation flags also constrain the semantic status.
        if (
            status
            in {
                "supported",
                "inference_supported",
            }
            and not (
                result[
                    "attribution_valid"
                ]
                and result[
                    "temporal_valid"
                ]
                and result[
                    "numeric_valid"
                ]
            )
        ):
            status = (
                "partially_supported"
            )

        unsupported_parts = list(
            result[
                "unsupported_parts"
            ]
        )

        if invalid_ids:
            unsupported_parts.append(
                "One or more cited evidence IDs were invalid: "
                + ", ".join(
                    invalid_ids
                )
            )

        if (
            multi_evidence_required
            and not multi_evidence_satisfied
        ):
            unsupported_parts.append(
                "The claim requires multiple supporting evidence records, "
                "but fewer than two valid supporting records were identified."
            )

        unsupported_parts = (
            _unique_strings(
                unsupported_parts
            )
        )

        return GroundedClaim(
            claim_id=claim_id,
            claim_text=claim_text,
            epistemic_type=epistemic_type,
            grounding_status=status,
            cited_evidence_ids=cited_ids,
            valid_evidence_ids=valid_ids,
            invalid_evidence_ids=invalid_ids,
            supporting_evidence_ids=supporting_ids,
            contradicted_evidence_ids=contradicted_ids,
            citation_valid=citation_valid,
            attribution_valid=result[
                "attribution_valid"
            ],
            temporal_valid=result[
                "temporal_valid"
            ],
            numeric_valid=result[
                "numeric_valid"
            ],
            multi_evidence_required=bool(
                multi_evidence_required
            ),
            multi_evidence_satisfied=(
                multi_evidence_satisfied
            ),
            unsupported_parts=unsupported_parts,
            rationale=result[
                "rationale"
            ],
            metadata=self._claim_metadata(
                claim_id=claim_id,
                claim_text=claim_text,
                epistemic_type=epistemic_type,
                valid_records=candidate_records,
            ),
        )

    def _ground_claim_cached(self, *, claim_id: str, claim_text: Any, evidence_ids: Any,
                             evidence_records: Sequence[Mapping[str, Any]], epistemic_type: Any,
                             multi_evidence_required: bool) -> GroundedClaim:
        """12 Nov 2026 (cost): a claim already checked against the same records in this run is not
        checked again. Repair rounds re-send a candidate whose premises are mostly unchanged; each
        re-check was a paid call that returned the same verdict. Key: prompt version, claim text,
        type, and the ids and text of the cited records - so a changed claim or record is re-checked.
        Set EVENT_INTELLIGENCE_GROUNDING_CACHE=0 to turn off."""
        import dataclasses
        import hashlib
        import os
        if os.getenv("EVENT_INTELLIGENCE_GROUNDING_CACHE", "1").strip() in {"0", "false", "no"}:
            return self.ground_claim(claim_id=claim_id, claim_text=claim_text, evidence_ids=evidence_ids,
                                     evidence_records=evidence_records, epistemic_type=epistemic_type,
                                     multi_evidence_required=multi_evidence_required)
        cache = self.__dict__.setdefault("_verdict_cache", {})
        stats = self.__dict__.setdefault("verdict_cache_stats", {"hits": 0, "misses": 0})
        wanted = sorted({_clean(e).upper() for e in (evidence_ids or [])})
        by_id = {_clean(r.get("evidence_id")).upper(): r for r in evidence_records if isinstance(r, Mapping)}
        cited = [[eid, _clean((by_id.get(eid) or {}).get("text") or (by_id.get(eid) or {}).get("source_excerpt")
                               or (by_id.get(eid) or {}).get("claim"))] for eid in wanted]
        key = hashlib.sha256(json.dumps(
            [getattr(self, "prompt_version", ""), _clean(claim_text), _clean(epistemic_type),
             bool(multi_evidence_required), cited], ensure_ascii=False).encode("utf-8")).hexdigest()
        hit = cache.get(key)
        if hit is not None:
            stats["hits"] += 1
            return dataclasses.replace(hit, claim_id=claim_id)
        stats["misses"] += 1
        result = self.ground_claim(claim_id=claim_id, claim_text=claim_text, evidence_ids=evidence_ids,
                                   evidence_records=evidence_records, epistemic_type=epistemic_type,
                                   multi_evidence_required=multi_evidence_required)
        cache[key] = result
        return result

    # ------------------------------------------------------------------
    # v13.18 (22 Nov 2026): batched claim checks
    # ------------------------------------------------------------------
    # RUN-ACC0E1ED (22 Nov): 285 of the run's 428 Gemini calls were one-claim support checks, each
    # resending the same ~1,500-token instructions. With the 480-call cap that left the stages after
    # the insights short (actor moves: 19 of 24 claim checks refused; judge and red team skipped).
    # ground_claims now sends up to BATCH_SIZE claims per request: same instructions, same evidence
    # per claim, same validation. Any claim the batch does not answer validly is checked alone.
    # EVENT_INTELLIGENCE_GROUNDING_BATCH=1 restores one call per claim.
    BATCH_ENV = "EVENT_INTELLIGENCE_GROUNDING_BATCH"
    # v13.19: 6 -> 4. Batch prompting is mostly stable up to about 4 items with "notable drops often
    # occurring at b=6" (Cheng et al. 2023, Batch Prompting); small models degrade first.
    BATCH_DEFAULT = 4
    _PROMPT_SPLIT = "\nEvidence records:\n"

    class _PromptCaptured(Exception):
        pass

    def _batch_size(self) -> int:
        raw = os.getenv(self.BATCH_ENV)
        if raw is None and getattr(self, "classifier_provider", "") != "gemini":
            return 1      # a supplied classifier (tests, local model) keeps one call per claim
        try:
            value = int(raw if raw is not None else str(self.BATCH_DEFAULT))
        except (TypeError, ValueError):
            value = self.BATCH_DEFAULT
        return max(1, min(value, 12))

    def _local(self):
        import threading
        local = self.__dict__.get("_thread_local")
        if local is None:
            local = self.__dict__.setdefault("_thread_local", threading.local())
        return local

    def _classify(self, prompt: str):
        captured = getattr(self._local(), "capture", None)
        if captured is not None:
            captured.append(prompt)
            raise GroundingService._PromptCaptured()
        return self.support_classifier(prompt)

    def _take_prefetched(self, prompt: str) -> Optional[Dict[str, Any]]:
        store = getattr(self._local(), "prefetched", None)
        if not store:
            return None
        item = store.pop(prompt, None)
        return dict(item) if isinstance(item, Mapping) else None

    def _batch_prompt(self, prompts: Sequence[str]) -> str:
        header = prompts[0].split(self._PROMPT_SPLIT, 1)[0]
        header = header.replace(
            "Your task is to assess whether ONE atomic claim is supported by the supplied\nevidence records.",
            "Your task is to assess, for EACH of the numbered items below, whether its ONE atomic claim\n"
            "is supported by THAT item's own evidence records. Judge every item independently: never use\n"
            "one item's evidence for another item's claim.")
        header = header.replace(
            "Return exactly one JSON object:",
            "Return exactly one JSON object {\"results\": [...]} with one entry per item, in item order,\n"
            "each entry having \"item\" (the item number), \"claim_id\" and these fields:")
        parts = [header, ""]
        for number, prompt in enumerate(prompts, start=1):
            body = prompt.split(self._PROMPT_SPLIT, 1)[1]
            parts.append(f"===== ITEM {number} =====\nEvidence records:\n{body}\n")
        parts.append(f"Return {{\"results\": [...]}} with exactly {len(prompts)} entries.")
        return "\n".join(parts)

    def _prefetch_batched(self, run_claims: Callable[[], Any]) -> None:
        """Collect the prompts the claims would send (no call made), ask them in batches, and keep
        each valid verdict for its claim. Failures leave the claim to its own call."""
        size = self._batch_size()
        if size <= 1:
            return
        local = self._local()
        if getattr(local, "capture", None) is not None or getattr(local, "prefetched", None):
            return
        captured: List[str] = []
        stats = self.__dict__.setdefault("verdict_cache_stats", {"hits": 0, "misses": 0})
        saved_stats = dict(stats) if isinstance(stats, dict) else None
        local.capture = captured
        try:
            run_claims()
        finally:
            local.capture = None
            if saved_stats is not None:
                stats.clear()
                stats.update(saved_stats)
        prompts = list(dict.fromkeys(p for p in captured if self._PROMPT_SPLIT in p))
        if len(prompts) < 2:
            return
        store: Dict[str, Dict[str, Any]] = {}
        for start in range(0, len(prompts), size):
            chunk = prompts[start:start + size]
            if len(chunk) == 1:
                continue
            try:
                raw = self.support_classifier(self._batch_prompt(chunk))
                parsed = _parse_json_object(raw)
            except Exception as error:
                if type(error).__name__ == "GeminiCallBudgetExceeded":
                    raise
                logger.info("grounding_batch_failed size=%s error=%s", len(chunk), type(error).__name__)
                continue
            entries = parsed.get("results") if isinstance(parsed, Mapping) else None
            if not isinstance(entries, list):
                continue
            for position, entry in enumerate(entries):
                if not isinstance(entry, Mapping):
                    continue
                try:
                    number = int(entry.get("item") or position + 1)
                except (TypeError, ValueError):
                    number = position + 1
                if 1 <= number <= len(chunk) and chunk[number - 1] not in store:
                    store[chunk[number - 1]] = {k: v for k, v in entry.items() if k not in {"item", "claim_id"}}
        local.prefetched = store
        logger.info("grounding_batched claims=%s batches=%s answered=%s", len(prompts),
                    -(-len(prompts) // size), len(store))

    def batched_checks(self, claims: Sequence[Mapping[str, Any]], evidence_records: Sequence[Mapping[str, Any]]):
        """v13.18: context manager for callers that check claims one at a time on purpose (so one bad
        check cannot void the rest): the claims are asked in batches up front, and each later
        one-claim ground_claims call uses its batched verdict when there is a valid one."""
        import contextlib

        @contextlib.contextmanager
        def manager():
            local = self._local()
            local.batch_depth = int(getattr(local, "batch_depth", 0) or 0) + 1
            try:
                if local.batch_depth == 1 and len(claims) > 1:
                    try:
                        normalized = self._normalize_evidence_records(evidence_records)
                        self._prefetch_batched(lambda: self._ground_claim_list(claims, normalized))
                    except Exception as error:   # each claim is then checked alone, as before
                        logger.info("grounding_batch_skipped error=%s", type(error).__name__)
                yield
            finally:
                local.batch_depth -= 1
                if local.batch_depth <= 0:
                    local.batch_depth = 0
                    local.prefetched = None
        return manager()

    def ground_claims(
        self,
        *,
        claims: Sequence[
            Mapping[str, Any]
        ],
        evidence_records: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        """
        Ground every supplied claim in order.

        A classifier failure raises rather than silently skipping a claim.
        """
        if not isinstance(
            claims,
            Sequence,
        ) or isinstance(
            claims,
            (str, bytes),
        ):
            raise GroundingValidationError(
                "claims must be a sequence of objects."
            )

        normalized_evidence = (
            self._normalize_evidence_records(
                evidence_records
            )
        )

        def run_claims() -> List[GroundedClaim]:
            return self._ground_claim_list(claims, normalized_evidence)

        local = self._local()
        inside_batch = int(getattr(local, "batch_depth", 0) or 0) > 0
        if len(claims) > 1 and not inside_batch:
            try:
                self._prefetch_batched(run_claims)
            except GroundingValidationError:
                pass
        try:
            results = run_claims()
        finally:
            if not inside_batch:
                local.prefetched = None

        if len(results) != len(claims):
            raise GroundingServiceError(
                "Grounding completeness invariant failed."
            )
        return self._aggregate_claim_results(results, normalized_evidence)

    def _ground_claim_list(self, claims, normalized_evidence) -> List[GroundedClaim]:
        capturing = getattr(self._local(), "capture", None) is not None
        results: List[
            GroundedClaim
        ] = []

        seen_claim_ids: Set[
            str
        ] = set()

        for index, claim in enumerate(
            claims
        ):
            if not isinstance(
                claim,
                Mapping,
            ):
                raise GroundingValidationError(
                    f"claims[{index}] must be an object."
                )

            claim_id = _clean(
                claim.get(
                    "claim_id"
                )
                or claim.get(
                    "id"
                )
            )

            if not claim_id:
                raise GroundingValidationError(
                    f"claims[{index}] is missing claim_id/id."
                )

            if claim_id in seen_claim_ids:
                raise GroundingValidationError(
                    f"Duplicate claim_id={claim_id!r}."
                )

            seen_claim_ids.add(
                claim_id
            )

            try:
                grounded = (
                self._ground_claim_cached(
                    claim_id=claim_id,
                    claim_text=(
                        claim.get(
                            "claim_text"
                        )
                        or claim.get(
                            "text"
                        )
                        or claim.get(
                            "claim"
                        )
                        or claim.get(
                            "statement"
                        )
                        or ""
                    ),
                    evidence_ids=(
                        claim.get(
                            "evidence_ids"
                        )
                        or claim.get(
                            "supporting_evidence_ids"
                        )
                        or []
                    ),
                    evidence_records=normalized_evidence,
                    epistemic_type=(
                        claim.get(
                            "epistemic_type"
                        )
                        or claim.get(
                            "type"
                        )
                        or "unknown"
                    ),
                    multi_evidence_required=bool(
                        claim.get(
                            "multi_evidence_required",
                            False,
                        )
                    ),
                )
                )
            except GroundingService._PromptCaptured:
                continue
            except GroundingValidationError:
                if capturing:
                    continue
                raise
            results.append(grounded)

        return results

    def _aggregate_claim_results(self, results, normalized_evidence) -> Dict[str, Any]:

        status_counts = {
            status: 0
            for status
            in sorted(
                ALLOWED_GROUNDING_STATUSES
            )
        }

        for result in results:
            status_counts[
                result.grounding_status
            ] += 1

        # --------------------------------------------------------------
        # Insight-level aggregate contract
        # --------------------------------------------------------------
        #
        # InsightQualityService consumes one grounding object per insight and
        # expects top-level fields such as ``status`` and
        # ``support_evidence_ids``.  The original implementation exposed only
        # claim-level detail (review_status/status_counts/claims), which meant
        # final quality assessment could not see the successful grounding
        # decisions it had just produced.
        #
        # Preserve the complete claim trace while also exposing a conservative
        # aggregate view for downstream quality/release logic.
        statuses = [
            result.grounding_status
            for result in results
        ]

        if not statuses:
            aggregate_status = "unsupported"
        elif "contradicted" in statuses:
            aggregate_status = "contradicted"
        elif all(
            status == "unsupported"
            for status in statuses
        ):
            aggregate_status = "unsupported"
        elif (
            "unsupported" in statuses
            or "partially_supported" in statuses
        ):
            aggregate_status = "partially_supported"
        elif "inference_supported" in statuses:
            aggregate_status = "inference_supported"
        else:
            aggregate_status = "supported"

        supporting_evidence_ids = _unique_strings(
            [
                evidence_id
                for result in results
                for evidence_id in result.supporting_evidence_ids
            ]
        )

        contradicted_evidence_ids = _unique_strings(
            [
                evidence_id
                for result in results
                for evidence_id in result.contradicted_evidence_ids
            ]
        )

        unsupported_parts = _unique_strings(
            [
                part
                for result in results
                for part in result.unsupported_parts
            ]
        )

        return {
            "review_status": "complete",
            # Canonical insight-level grounding fields consumed by
            # InsightQualityService.
            "status": aggregate_status,
            "grounding_status": aggregate_status,
            "support_evidence_ids": supporting_evidence_ids,
            # Compatibility alias used by other callers.
            "supporting_evidence_ids": supporting_evidence_ids,
            "counterevidence_ids": contradicted_evidence_ids,
            "contradicted_evidence_ids": contradicted_evidence_ids,
            "critical_unknowns": unsupported_parts,
            "unsupported_parts": unsupported_parts,
            # Full claim-level audit detail remains authoritative.
            "claim_count": len(
                results
            ),
            "status_counts": (
                status_counts
            ),
            "claims": [
                result.to_dict()
                for result in results
            ],
            "metadata": {
                "prompt_version": (
                    self.prompt_version
                ),
                "model": (
                    self.model_name
                ),
                "aggregate_status": aggregate_status,
                "aggregate_contract": (
                    "insight_quality_service_v1"
                ),
                "evidence_record_count": len(
                    normalized_evidence
                ),
                "evidence_fingerprint": (
                    _sha256_hex(
                        _canonical_json_bytes(
                            normalized_evidence
                        )
                    )
                ),
            },
        }

    def ground_report(
        self,
        *,
        report: Mapping[str, Any],
        evidence_records: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        """
        Deterministically extract groundable claims from the final research
        report schema and ground them.

        This is schema extraction, not semantic claim decomposition. If a report
        writer emits one item containing several independent claims, the writer
        should split that item upstream.
        """
        claims = self.extract_report_claims(
            report
        )

        result = self.ground_claims(
            claims=claims,
            evidence_records=evidence_records,
        )

        result[
            "metadata"
        ][
            "report_fingerprint"
        ] = _sha256_hex(
            _canonical_json_bytes(
                report
            )
        )

        return result

    # ------------------------------------------------------------------
    # Report claim extraction
    # ------------------------------------------------------------------

    def extract_report_claims(
        self,
        report: Mapping[str, Any],
    ) -> List[Dict[str, Any]]:
        if not isinstance(
            report,
            Mapping,
        ):
            raise GroundingValidationError(
                "report must be an object."
            )

        claims: List[
            Dict[str, Any]
        ] = []

        counter = 0

        def add_claim(
            *,
            section: str,
            value: Any,
            epistemic_type: str,
            default_multi: bool = False,
            explicit_id: str = "",
        ) -> None:
            nonlocal counter

            if value is None:
                return

            counter += 1

            if isinstance(
                value,
                str,
            ):
                text = _clean(
                    value
                )
                evidence_ids: List[
                    str
                ] = []
                multi = (
                    default_multi
                )

            elif isinstance(
                value,
                Mapping,
            ):
                text = self._claim_text_from_item(
                    value
                )

                evidence_ids = _unique_strings(
                    value.get(
                        "evidence_ids"
                    )
                    or value.get(
                        "supporting_evidence_ids"
                    )
                    or value.get(
                        "support_ids"
                    )
                    or []
                )

                multi = bool(
                    value.get(
                        "multi_evidence_required",
                        default_multi,
                    )
                )

                item_id = _clean(
                    value.get(
                        "claim_id"
                    )
                    or value.get(
                        "id"
                    )
                )

                explicit_id = (
                    explicit_id
                    or item_id
                )

            else:
                return

            if not text:
                return

            claim_id = (
                explicit_id
                or (
                    "CLAIM-"
                    + _sha256_hex(
                        _canonical_json_bytes(
                            {
                                "section": section,
                                "text": text,
                                "ordinal": counter,
                            }
                        )
                    )[:20].upper()
                )
            )

            claims.append(
                {
                    "claim_id": (
                        claim_id
                    ),
                    "claim_text": (
                        text
                    ),
                    "epistemic_type": (
                        epistemic_type
                    ),
                    "evidence_ids": (
                        evidence_ids
                    ),
                    "multi_evidence_required": (
                        multi
                    ),
                    "section": (
                        section
                    ),
                }
            )

        executive = report.get(
            "executive_synthesis"
        )

        if isinstance(
            executive,
            Mapping,
        ):
            executive_evidence = _unique_strings(
                executive.get(
                    "evidence_ids"
                )
            )

            for key, epistemic in (
                (
                    "what_is_happening",
                    "fact",
                ),
                (
                    "what_it_may_mean",
                    "mechanism",
                ),
            ):
                value = _clean(
                    executive.get(
                        key
                    )
                )

                if value:
                    counter += 1

                    claims.append(
                        {
                            "claim_id": (
                                "CLAIM-"
                                + _sha256_hex(
                                    _canonical_json_bytes(
                                        {
                                            "section": (
                                                "executive_synthesis"
                                            ),
                                            "field": key,
                                            "text": value,
                                        }
                                    )
                                )[:20].upper()
                            ),
                            "claim_text": value,
                            "epistemic_type": (
                                epistemic
                            ),
                            "evidence_ids": (
                                executive_evidence
                            ),
                            "multi_evidence_required": (
                                key
                                == "what_it_may_mean"
                            ),
                            "section": (
                                "executive_synthesis"
                            ),
                        }
                    )

        section_specs = [
            (
                "event_landscape",
                "fact",
                False,
            ),
            (
                "connections",
                "connection",
                True,
            ),
            (
                "hidden_insights",
                "hidden_insight",
                True,
            ),
            (
                "second_order_effects",
                "second_order_effect",
                True,
            ),
            (
                "counterevidence_and_alternatives",
                "counterevidence",
                False,
            ),
            (
                "what_to_watch",
                "watch_signal",
                False,
            ),
        ]

        for section, epistemic_type, default_multi in section_specs:
            values = report.get(
                section
            )

            if not isinstance(
                values,
                list,
            ):
                continue

            for value in values:
                add_claim(
                    section=section,
                    value=value,
                    epistemic_type=epistemic_type,
                    default_multi=default_multi,
                )

        dynamics = report.get(
            "underlying_dynamics"
        )

        if isinstance(
            dynamics,
            Mapping,
        ):
            for subsection, epistemic in (
                (
                    "mechanisms",
                    "mechanism",
                ),
                (
                    "structural_shifts",
                    "mechanism",
                ),
                (
                    "changing_incentives",
                    "mechanism",
                ),
                (
                    "constraint_links",
                    "connection",
                ),
            ):
                values = dynamics.get(
                    subsection
                )

                if not isinstance(
                    values,
                    list,
                ):
                    continue

                for value in values:
                    add_claim(
                        section=(
                            "underlying_dynamics."
                            + subsection
                        ),
                        value=value,
                        epistemic_type=epistemic,
                        default_multi=(
                            epistemic
                            in {
                                "mechanism",
                                "connection",
                            }
                        ),
                    )

        return claims

    # ------------------------------------------------------------------
    # Evidence normalization
    # ------------------------------------------------------------------

    def _normalize_evidence_records(
        self,
        evidence_records: Sequence[
            Mapping[str, Any]
        ],
    ) -> List[Dict[str, Any]]:
        if not isinstance(
            evidence_records,
            Sequence,
        ) or isinstance(
            evidence_records,
            (str, bytes),
        ):
            raise GroundingValidationError(
                "evidence_records must be a sequence of objects."
            )

        result: List[
            Dict[str, Any]
        ] = []

        seen_ids: Set[
            str
        ] = set()

        for index, record in enumerate(
            evidence_records
        ):
            if not isinstance(
                record,
                Mapping,
            ):
                raise GroundingValidationError(
                    f"evidence_records[{index}] must be an object."
                )

            evidence_id = _evidence_id(
                record
            )

            if not evidence_id:
                raise GroundingValidationError(
                    f"evidence_records[{index}] is missing an evidence ID."
                )

            if evidence_id in seen_ids:
                raise GroundingValidationError(
                    f"Duplicate evidence_id={evidence_id!r}."
                )

            seen_ids.add(
                evidence_id
            )

            normalized = {
                str(key): _json_safe(
                    value
                )
                for key, value
                in record.items()
            }

            normalized[
                "evidence_id"
            ] = evidence_id

            normalized[
                "evidence_text"
            ] = _evidence_text(
                record
            )

            result.append(
                normalized
            )

        return result

    # ------------------------------------------------------------------
    # Prompt / output validation
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        *,
        claim_id: str,
        claim_text: str,
        epistemic_type: str,
        evidence_records: Sequence[
            Mapping[str, Any]
        ],
        multi_evidence_required: bool,
    ) -> str:
        return f"""
You are a narrow evidence-grounding component.

Your task is to assess whether ONE atomic claim is supported by the supplied
evidence records.

Do not retrieve outside information.
Do not rely on general world knowledge.
Do not treat semantic similarity as proof.
Do not create new evidence IDs.
Do not return a numeric confidence score.

Return exactly one JSON object:

{{
  "status": "supported | partially_supported | inference_supported | unsupported | contradicted",
  "supporting_evidence_ids": ["only IDs from supplied evidence"],
  "contradicted_evidence_ids": ["only IDs from supplied evidence"],
  "attribution_valid": true,
  "temporal_valid": true,
  "numeric_valid": true,
  "unsupported_parts": ["specific unsupported component, if any"],
  "rationale": "brief evidence-bounded explanation"
}}

Definitions:

supported:
The evidence directly supports the material factual content of the claim.

partially_supported:
Some material parts are supported, but one or more material parts are absent,
too strong, mismatched, or insufficiently established.

inference_supported:
The evidence supports the stated premises and the claim is a reasonable
explicit inference, but the exact conclusion is not directly stated as fact.

unsupported:
The supplied evidence does not establish the material claim.

contradicted:
At least one supplied evidence record materially conflicts with the claim.

Validation rules:
1. supporting_evidence_ids and contradicted_evidence_ids must contain only
   supplied evidence IDs.
2. A numeric match requires the correct quantity, unit/dimension, entity, and
   period/context. Similar numbers are not enough.
3. Historical evidence must not be silently converted into a current-state
   claim.
4. A plan, target, proposal, or future commitment is not evidence that the
   action was completed.
5. Attribution must match the correct actor/entity.
6. Multiple evidence records may jointly support one claim.
7. Do not infer source independence merely because multiple IDs are present.
8. For inference-supported claims, the evidence must support the premises and
   the inferential step must be reasonable from those premises.
9. If the evidence contains a material conflict, use contradicted where that
   conflict defeats the claim; otherwise identify it in unsupported_parts.
10. Chronology: if the claim says or implies that one development caused,
   enabled, drove or explains another, compare their dates in the evidence.
   A development that the evidence dates AFTER the one it supposedly caused
   cannot be its cause: set temporal_valid=false and use unsupported (or
   contradicted when the dates are explicit), and name the impossible order
   in unsupported_parts. If the dates needed to check the order are missing,
   say so in unsupported_parts.
11. Company claims: when the only support for a statement that a need,
   demand, problem or market exists is a company describing its own product,
   funding or market (or a report of that announcement), the evidence
   supports only that the company says so. If the claim states it as fact,
   use partially_supported and name it in unsupported_parts as the company's
   own claim. If the claim attributes it ("Ascerta says ..."), it can be
   supported.
12. Forecasts and predicted moves (v10.2, 15 Nov 2026): a claim about what will, is likely to,
   or is expected to happen, or what an actor will do next, cannot be stated by evidence about
   the past, so "the evidence does not state this will happen" is never by itself a reason to
   reject it. Judge it on its premises and its reasoning:
   - inference_supported: the evidence supports the premises it rests on and the expectation
     follows reasonably from them;
   - partially_supported: some premises are backed but one is missing, or the expectation is
     stated more strongly or more certainly than the premises allow;
   - unsupported: its premises are not in the evidence, or the expectation does not follow
     from them;
   - contradicted: the evidence shows it will not happen, or has already gone the other way.
   A forecast is never "supported"; that word is for facts the evidence states.
13. Do not add commentary outside the JSON object.

Evidence records:
{json.dumps(list(evidence_records), ensure_ascii=False, indent=2, default=str)}

Based only on the evidence records above, assess this claim.

Claim ID:
{claim_id}

Epistemic type:
{epistemic_type}

Claim:
{claim_text}

Multi-evidence required:
{str(multi_evidence_required).lower()}
""".strip()

    def _validate_classifier_output(
        self,
        *,
        parsed: Mapping[str, Any],
        allowed_evidence_ids: Set[str],
    ) -> Dict[str, Any]:
        if not isinstance(
            parsed,
            Mapping,
        ):
            raise GroundingValidationError(
                "Support classifier output must be an object."
            )

        prohibited = {
            "confidence",
            "confidence_score",
            "probability",
            "score",
        }

        present = sorted(
            key
            for key in prohibited
            if key in parsed
        )

        if present:
            raise GroundingValidationError(
                "Support classifier returned prohibited numeric-confidence "
                "field(s): "
                + ", ".join(
                    present
                )
            )

        status = _normalize_status(
            parsed.get(
                "status"
            )
        )

        if not status:
            raise GroundingValidationError(
                "Classifier status must be one of: "
                + ", ".join(
                    sorted(
                        ALLOWED_GROUNDING_STATUSES
                    )
                )
            )

        supporting = _unique_strings(
            parsed.get(
                "supporting_evidence_ids"
            )
        )

        contradicted = _unique_strings(
            parsed.get(
                "contradicted_evidence_ids"
            )
        )

        illegal_ids = sorted(
            (
                set(
                    supporting
                )
                | set(
                    contradicted
                )
            )
            - allowed_evidence_ids
        )

        if illegal_ids:
            raise GroundingValidationError(
                "Support classifier referenced evidence IDs outside the "
                "supplied candidate set: "
                + ", ".join(
                    illegal_ids
                )
            )

        overlap = sorted(
            set(
                supporting
            )
            & set(
                contradicted
            )
        )

        if overlap:
            raise GroundingValidationError(
                "The same evidence ID cannot be both supporting and "
                "contradicting in one grounding result: "
                + ", ".join(
                    overlap
                )
            )

        def require_bool(
            field: str,
        ) -> bool:
            value = parsed.get(
                field
            )

            if not isinstance(
                value,
                bool,
            ):
                raise GroundingValidationError(
                    f"{field} must be boolean."
                )

            return value

        rationale = _clean(
            parsed.get(
                "rationale"
            )
        )

        if not rationale:
            raise GroundingValidationError(
                "rationale cannot be empty."
            )

        return {
            "status": status,
            "supporting_evidence_ids": (
                supporting
            ),
            "contradicted_evidence_ids": (
                contradicted
            ),
            "attribution_valid": require_bool(
                "attribution_valid"
            ),
            "temporal_valid": require_bool(
                "temporal_valid"
            ),
            "numeric_valid": require_bool(
                "numeric_valid"
            ),
            "unsupported_parts": (
                _unique_strings(
                    parsed.get(
                        "unsupported_parts"
                    )
                )
            ),
            "rationale": rationale,
        }

    def _claim_metadata(
        self,
        *,
        claim_id: str,
        claim_text: str,
        epistemic_type: str,
        valid_records: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        return {
            "prompt_version": (
                self.prompt_version
            ),
            "model": (
                self.model_name
            ),
            "candidate_evidence_count": len(
                valid_records
            ),
            "claim_fingerprint": (
                _sha256_hex(
                    _canonical_json_bytes(
                        {
                            "claim_id": (
                                claim_id
                            ),
                            "claim_text": (
                                claim_text
                            ),
                            "epistemic_type": (
                                epistemic_type
                            ),
                        }
                    )
                )
            ),
            "evidence_fingerprint": (
                _sha256_hex(
                    _canonical_json_bytes(
                        list(
                            valid_records
                        )
                    )
                )
            ),
        }

    def _claim_text_from_item(
        self,
        item: Mapping[str, Any],
    ) -> str:
        for field in (
            "claim_text",
            "claim",
            "statement",
            "text",
            "development",
            "description",
            "mechanism",
            "insight",
            "effect",
            "signal",
            "interpretation",
            "alternative",
            "what_is_happening",
            "what_it_may_mean",
            "summary",
            "title",
        ):
            value = _clean(
                item.get(
                    field
                )
            )

            if value:
                return value

        return ""
