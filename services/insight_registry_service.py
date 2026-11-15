"""
Local insight lifecycle registry.

Recommended location:
    services/insight_registry_service.py

Purpose
-------
StrategicInferenceAgent
        ↓
candidate insight
        ↓
InsightQuality / Grounding / Red Team
        ↓
InsightRegistryService
        ↓
stable logical insight identity
        +
immutable revision history
        +
lifecycle state
        +
trace/report/evidence lineage

The registry prevents a critical failure mode in research systems:

    insight text changes
        ↓
    old conclusion silently disappears
        ↓
    report/history no longer explains what changed

Instead:

    INSIGHT-123                 # stable logical identity
        ↓
    INSIGHTREV-001              # immutable revision
        ↓
    INSIGHTREV-002              # later revision
        ↓
    INSIGHTREV-003

Old revisions remain queryable and are explicitly marked superseded.

Lifecycle model
---------------
Logical insight status:
    ACTIVE
    WEAKENED
    RETRACTED
    SUPERSEDED

Revision status:
    ACTIVE
    WEAKENED
    SUPERSEDED
    RETRACTED

Revision is an EVENT, not a destructive overwrite.

Typical flow:

    ACTIVE revision
        ↓
    WEAKENED
        ↓
    revise()
        ↓
    old revision -> SUPERSEDED
    new revision -> ACTIVE

or:

    ACTIVE
        ↓
    RETRACTED

A logical insight may also be superseded by another logical insight when the
underlying thesis itself is replaced rather than merely revised.

Critical epistemic boundary
---------------------------
Registry state != truth.

This service does NOT:
- create evidence;
- ground claims;
- decide source independence;
- generate strategic insights;
- change quality scores;
- authorize release;
- change Blueprint/policy;
- change Fast/Deep runtime;
- change model routing.

It records lifecycle decisions already made by the research pipeline.

Stable identity
---------------
Two identifiers are deliberately separate:

    insight_id
        stable logical thesis identity

    insight_revision_id
        immutable text/version identity

This allows:
- report citations to a precise revision;
- future evidence to weaken/revise/retract an existing thesis;
- audit receipts to fingerprint exact versions;
- user feedback to refer to a stable insight;
- preference data to retain exact revision lineage.

Trace integration
-----------------
Each revision can carry:
- insight_trace_id
- quality_assessment_id
- grounding_assessment_id
- red_team_assessment_id
- supporting_evidence_ids
- counterevidence_ids
- report_ids

The registry stores IDs only. The authoritative structured synthesis trace stays
in InsightTraceService.

Free/local constraint
---------------------
Pure Python, local/in-memory, no API key, no paid service, no LLM required.

Persistence
-----------
Supports:
- export_snapshot()
- restore_snapshot()

A later SQLite/repository adapter can persist snapshots without changing the
lifecycle contract.

No private chain-of-thought is stored.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Mapping, Optional, Sequence, Set


SERVICE_VERSION = "insight-registry-service-v1.0"

ALLOWED_INSIGHT_TYPES = {
    "descriptive",
    "prospective",
}

LOGICAL_STATUSES = {
    "active",
    "weakened",
    "retracted",
    "superseded",
}

REVISION_STATUSES = {
    "active",
    "weakened",
    "superseded",
    "retracted",
}

TERMINAL_LOGICAL_STATUSES = {
    "retracted",
    "superseded",
}

TERMINAL_REVISION_STATUSES = {
    "superseded",
    "retracted",
}

ALLOWED_EPISTEMIC_STATUSES = {
    "supported",
    "partially_supported",
    "inference_supported",
    "unsupported",
    "contradicted",
    "unresolved",
}

ALLOWED_QUALITY_STATUSES = {
    "strong",
    "usable_with_limits",
    "needs_refinement",
    "insufficient_evidence",
    "unassessed",
}

ALLOWED_PROSPECTIVE_STATUSES = {
    "",
    "plausible_unprecedented",
    "plausible_with_precedent",
    "weakly_supported",
    "unsupported",
    "contradicted",
    "unresolved",
}

ALLOWED_EVENT_TYPES = {
    "created",
    "weakened",
    "strengthened",
    "revised",
    "retracted",
    "superseded",
    "report_linked",
    "trace_linked",
    "assessment_updated",
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


class InsightRegistryError(RuntimeError):
    """Base registry exception."""


class InsightRegistryValidationError(InsightRegistryError):
    """Raised when caller input violates the registry contract."""


class InsightRegistryIntegrityError(InsightRegistryError):
    """Raised when stable identity/history invariants are violated."""


class InsightRegistryStateError(InsightRegistryError):
    """Raised when a lifecycle transition is not allowed."""


# ---------------------------------------------------------------------------
# Helpers
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
        return {
            str(key): _json_safe(item)
            for key, item in value.items()
        }

    if isinstance(value, (list, tuple, set)):
        return [
            _json_safe(item)
            for item in value
        ]

    return str(value)


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _fingerprint(value: Any) -> str:
    return _sha256(
        _canonical_json_bytes(value)
    )


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return (
        f"{prefix}-"
        + _fingerprint(payload)[:length].upper()
    )


def _utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
    )


def _stable_unique_strings(values: Any) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set)):
        raise InsightRegistryValidationError(
            "Expected a string or sequence of strings."
        )

    result = []
    seen = set()

    for value in values:
        text = _clean(value)

        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return result


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = _normalize_enum(key)
            current_path = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            if normalized_key in PROHIBITED_PRIVATE_REASONING_FIELDS:
                return current_path

            nested = _contains_prohibited_field(
                item,
                current_path,
            )

            if nested:
                return nested

    elif isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            nested = _contains_prohibited_field(
                item,
                f"{path}[{index}]",
            )
            if nested:
                return nested

    return None


def _require_no_private_reasoning(
    value: Any,
    *,
    field_name: str,
) -> None:
    path = _contains_prohibited_field(value)

    if path:
        raise InsightRegistryValidationError(
            f"{field_name} contains prohibited private/raw reasoning field "
            f"at {path!r}."
        )


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class InsightRegistryService:
    """
    Thread-safe local lifecycle registry for strategic insights.
    """

    def __init__(
        self,
        *,
        clock: Any = _utc_now_iso,
    ) -> None:
        if not callable(clock):
            raise InsightRegistryValidationError(
                "clock must be callable."
            )

        self.clock = clock
        self._lock = threading.RLock()

        # Stable logical insight ID -> logical record.
        self._insights: Dict[
            str,
            Dict[str, Any],
        ] = {}

        # Immutable revision ID -> revision record.
        self._revisions: Dict[
            str,
            Dict[str, Any],
        ] = {}

        # Revision ID -> owning logical insight ID.
        self._revision_owner: Dict[
            str,
            str,
        ] = {}

        # Event ID -> immutable lifecycle event.
        self._events: Dict[
            str,
            Dict[str, Any],
        ] = {}

        # Logical insight ID -> ordered event IDs.
        self._event_index: Dict[
            str,
            list[str],
        ] = {}

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create_insight(
        self,
        *,
        insight_id: str = "",
        insight_revision_id: str = "",
        insight_type: str,
        text: str,
        research_question: str = "",
        run_id: str = "",
        report_ids: Sequence[str] = (),
        supporting_evidence_ids: Sequence[str] = (),
        counterevidence_ids: Sequence[str] = (),
        insight_trace_id: str = "",
        quality_assessment_id: str = "",
        grounding_assessment_id: str = "",
        red_team_assessment_id: str = "",
        quality_status: str = "unassessed",
        epistemic_status: str = "unresolved",
        prospective_status: str = "",
        mechanism: str = "",
        second_order_effect: str = "",
        critical_unknowns: Sequence[str] = (),
        metadata: Optional[Mapping[str, Any]] = None,
        event_nonce: str = "",
    ) -> Dict[str, Any]:
        """
        Create a new logical insight and its first immutable revision.
        """
        normalized = self._normalize_revision_input(
            insight_revision_id=insight_revision_id,
            insight_type=insight_type,
            text=text,
            supporting_evidence_ids=supporting_evidence_ids,
            counterevidence_ids=counterevidence_ids,
            insight_trace_id=insight_trace_id,
            quality_assessment_id=quality_assessment_id,
            grounding_assessment_id=grounding_assessment_id,
            red_team_assessment_id=red_team_assessment_id,
            quality_status=quality_status,
            epistemic_status=epistemic_status,
            prospective_status=prospective_status,
            mechanism=mechanism,
            second_order_effect=second_order_effect,
            critical_unknowns=critical_unknowns,
            report_ids=report_ids,
            metadata=metadata,
        )

        research_question = _clean(research_question)
        run_id = _clean(run_id)

        logical_id = _clean(insight_id)

        if not logical_id:
            logical_id = _stable_id(
                "INSIGHT",
                {
                    "insight_type": normalized["insight_type"],
                    "initial_text": normalized["text"],
                    "research_question": research_question,
                    "run_id": run_id,
                    "event_nonce": _clean(event_nonce),
                },
            )

        now = self.clock()

        revision_id = (
            normalized["insight_revision_id"]
            or _stable_id(
                "INSIGHTREV",
                {
                    "insight_id": logical_id,
                    "text": normalized["text"],
                    "created_at": now,
                    "event_nonce": _clean(event_nonce),
                },
            )
        )

        with self._lock:
            if logical_id in self._insights:
                existing = self._insights[logical_id]

                if (
                    existing["current_revision_id"] == revision_id
                    and revision_id in self._revisions
                ):
                    return self.get_insight(
                        logical_id,
                        include_history=True,
                    )

                raise InsightRegistryIntegrityError(
                    f"insight_id={logical_id!r} already exists."
                )

            if revision_id in self._revisions:
                raise InsightRegistryIntegrityError(
                    f"insight_revision_id={revision_id!r} already exists."
                )

            revision = self._build_revision_record(
                insight_id=logical_id,
                revision_id=revision_id,
                revision_number=1,
                parent_revision_id="",
                revision_status="active",
                created_at=now,
                superseded_at="",
                superseded_by_revision_id="",
                weakened_at="",
                retracted_at="",
                retraction_reason="",
                **{
                    key: value
                    for key, value in normalized.items()
                    if key != "insight_revision_id"
                },
            )

            logical = {
                "insight_id": logical_id,
                "insight_type": normalized["insight_type"],
                "lifecycle_status": "active",
                "research_question": research_question,
                "origin_run_id": run_id,
                "created_at": now,
                "updated_at": now,
                "current_revision_id": revision_id,
                "revision_ids": [revision_id],
                "superseded_by_insight_id": "",
                "retracted_at": "",
                "retraction_reason": "",
                "weakening_reason": "",
                "metadata": {
                    "service_version": SERVICE_VERSION,
                    "stable_logical_identity": True,
                    "revision_text_is_immutable": True,
                    "registry_state_is_not_truth": True,
                    "authorizes_release": False,
                    "modifies_policy": False,
                    "modifies_runtime": False,
                    "private_chain_of_thought_stored": False,
                },
            }

            self._insights[logical_id] = logical
            self._revisions[revision_id] = revision
            self._revision_owner[revision_id] = logical_id

            self._append_event(
                insight_id=logical_id,
                event_type="created",
                revision_id=revision_id,
                reason="initial_revision",
                details={
                    "run_id": run_id,
                    "research_question": research_question,
                },
            )

            return self.get_insight(
                logical_id,
                include_history=True,
            )

    # ------------------------------------------------------------------
    # Revision
    # ------------------------------------------------------------------

    def revise_insight(
        self,
        insight_id: str,
        *,
        text: str,
        reason: str,
        insight_revision_id: str = "",
        supporting_evidence_ids: Sequence[str] = (),
        counterevidence_ids: Sequence[str] = (),
        insight_trace_id: str = "",
        quality_assessment_id: str = "",
        grounding_assessment_id: str = "",
        red_team_assessment_id: str = "",
        quality_status: str = "unassessed",
        epistemic_status: str = "unresolved",
        prospective_status: str = "",
        mechanism: str = "",
        second_order_effect: str = "",
        critical_unknowns: Sequence[str] = (),
        report_ids: Sequence[str] = (),
        metadata: Optional[Mapping[str, Any]] = None,
        event_nonce: str = "",
    ) -> Dict[str, Any]:
        """
        Add a new immutable revision to an existing logical insight.

        The prior current revision becomes SUPERSEDED.

        A RETRACTED or logically SUPERSEDED insight cannot be silently revived by
        revise_insight(). Callers must create a new logical insight if the thesis
        is being resurrected/replaced.
        """
        insight_id = _clean(insight_id)
        reason = _clean(reason)

        if not reason:
            raise InsightRegistryValidationError(
                "Revision reason cannot be empty."
            )

        with self._lock:
            logical = self._require_insight(
                insight_id
            )

            if (
                logical["lifecycle_status"]
                in TERMINAL_LOGICAL_STATUSES
            ):
                raise InsightRegistryStateError(
                    "Cannot revise a retracted/superseded logical insight."
                )

            current_revision = self._revisions[
                logical["current_revision_id"]
            ]

            normalized = self._normalize_revision_input(
                insight_revision_id=insight_revision_id,
                insight_type=logical["insight_type"],
                text=text,
                supporting_evidence_ids=supporting_evidence_ids,
                counterevidence_ids=counterevidence_ids,
                insight_trace_id=insight_trace_id,
                quality_assessment_id=quality_assessment_id,
                grounding_assessment_id=grounding_assessment_id,
                red_team_assessment_id=red_team_assessment_id,
                quality_status=quality_status,
                epistemic_status=epistemic_status,
                prospective_status=prospective_status,
                mechanism=mechanism,
                second_order_effect=second_order_effect,
                critical_unknowns=critical_unknowns,
                report_ids=report_ids,
                metadata=metadata,
            )

            # Prevent meaningless revisions that silently clone the same content
            # and assessment lineage.
            comparable_current = self._revision_comparable_payload(
                current_revision
            )
            comparable_new = self._revision_comparable_payload(
                {
                    **normalized,
                    "insight_id": insight_id,
                }
            )

            if comparable_current == comparable_new:
                raise InsightRegistryValidationError(
                    "New revision is materially identical to the current revision."
                )

            now = self.clock()

            new_revision_id = (
                normalized["insight_revision_id"]
                or _stable_id(
                    "INSIGHTREV",
                    {
                        "insight_id": insight_id,
                        "parent_revision_id": current_revision[
                            "insight_revision_id"
                        ],
                        "text": normalized["text"],
                        "created_at": now,
                        "event_nonce": _clean(event_nonce),
                    },
                )
            )

            if new_revision_id in self._revisions:
                raise InsightRegistryIntegrityError(
                    f"insight_revision_id={new_revision_id!r} already exists."
                )

            # Supersede previous revision.
            current_revision["revision_status"] = "superseded"
            current_revision["superseded_at"] = now
            current_revision["superseded_by_revision_id"] = new_revision_id

            revision_number = len(
                logical["revision_ids"]
            ) + 1

            new_revision = self._build_revision_record(
                insight_id=insight_id,
                revision_id=new_revision_id,
                revision_number=revision_number,
                parent_revision_id=current_revision[
                    "insight_revision_id"
                ],
                revision_status="active",
                created_at=now,
                superseded_at="",
                superseded_by_revision_id="",
                weakened_at="",
                retracted_at="",
                retraction_reason="",
                **{
                    key: value
                    for key, value in normalized.items()
                    if key != "insight_revision_id"
                },
            )

            self._revisions[
                new_revision_id
            ] = new_revision
            self._revision_owner[
                new_revision_id
            ] = insight_id

            logical[
                "current_revision_id"
            ] = new_revision_id

            logical[
                "revision_ids"
            ].append(
                new_revision_id
            )

            # Revision resolves the previous WEAKENED logical status into ACTIVE
            # unless the new revision is itself explicitly weakened later.
            logical[
                "lifecycle_status"
            ] = "active"
            logical[
                "weakening_reason"
            ] = ""
            logical[
                "updated_at"
            ] = now

            self._append_event(
                insight_id=insight_id,
                event_type="revised",
                revision_id=new_revision_id,
                reason=reason,
                details={
                    "parent_revision_id": current_revision[
                        "insight_revision_id"
                    ],
                    "new_revision_id": new_revision_id,
                },
            )

            return self.get_insight(
                insight_id,
                include_history=True,
            )

    # ------------------------------------------------------------------
    # Weakening / strengthening
    # ------------------------------------------------------------------

    def weaken_insight(
        self,
        insight_id: str,
        *,
        reason: str,
        epistemic_status: str = "partially_supported",
        counterevidence_ids: Sequence[str] = (),
        grounding_assessment_id: str = "",
        quality_assessment_id: str = "",
    ) -> Dict[str, Any]:
        """
        Mark the current thesis/revision as weakened without rewriting its text.
        """
        insight_id = _clean(insight_id)
        reason = _clean(reason)
        epistemic_status = _normalize_enum(
            epistemic_status
        )

        if not reason:
            raise InsightRegistryValidationError(
                "Weakening reason cannot be empty."
            )

        if epistemic_status not in ALLOWED_EPISTEMIC_STATUSES:
            raise InsightRegistryValidationError(
                f"Unsupported epistemic_status={epistemic_status!r}."
            )

        if epistemic_status in {
            "supported",
            "inference_supported",
        }:
            raise InsightRegistryValidationError(
                "weaken_insight() requires a weakened/unresolved epistemic status."
            )

        with self._lock:
            logical = self._require_mutable_logical_insight(
                insight_id
            )

            revision = self._current_revision(
                logical
            )

            now = self.clock()

            logical["lifecycle_status"] = "weakened"
            logical["weakening_reason"] = reason
            logical["updated_at"] = now

            revision["revision_status"] = "weakened"
            revision["weakened_at"] = now
            revision["epistemic_status"] = epistemic_status
            revision["counterevidence_ids"] = _stable_unique_strings(
                list(
                    revision["counterevidence_ids"]
                )
                + list(counterevidence_ids)
            )

            if grounding_assessment_id:
                revision["grounding_assessment_id"] = _clean(
                    grounding_assessment_id
                )

            if quality_assessment_id:
                revision["quality_assessment_id"] = _clean(
                    quality_assessment_id
                )

            self._append_event(
                insight_id=insight_id,
                event_type="weakened",
                revision_id=revision[
                    "insight_revision_id"
                ],
                reason=reason,
                details={
                    "epistemic_status": epistemic_status,
                    "counterevidence_ids": list(
                        revision[
                            "counterevidence_ids"
                        ]
                    ),
                },
            )

            return self.get_insight(
                insight_id,
                include_history=True,
            )

    def strengthen_insight(
        self,
        insight_id: str,
        *,
        reason: str,
        epistemic_status: str,
        supporting_evidence_ids: Sequence[str] = (),
        grounding_assessment_id: str = "",
        quality_assessment_id: str = "",
    ) -> Dict[str, Any]:
        """
        Restore a WEAKENED current revision to ACTIVE after an external
        Grounding/Quality process has materially strengthened it.

        This updates assessment lineage/status, not revision text.
        """
        insight_id = _clean(insight_id)
        reason = _clean(reason)
        epistemic_status = _normalize_enum(
            epistemic_status
        )

        if not reason:
            raise InsightRegistryValidationError(
                "Strengthening reason cannot be empty."
            )

        if epistemic_status not in {
            "supported",
            "partially_supported",
            "inference_supported",
        }:
            raise InsightRegistryValidationError(
                "strengthen_insight() requires a supported epistemic status."
            )

        with self._lock:
            logical = self._require_mutable_logical_insight(
                insight_id
            )

            if logical[
                "lifecycle_status"
            ] != "weakened":
                raise InsightRegistryStateError(
                    "Only a weakened insight can be strengthened in place."
                )

            revision = self._current_revision(
                logical
            )

            now = self.clock()

            logical["lifecycle_status"] = "active"
            logical["weakening_reason"] = ""
            logical["updated_at"] = now

            revision["revision_status"] = "active"
            revision["epistemic_status"] = epistemic_status
            revision["supporting_evidence_ids"] = _stable_unique_strings(
                list(
                    revision["supporting_evidence_ids"]
                )
                + list(supporting_evidence_ids)
            )

            if grounding_assessment_id:
                revision["grounding_assessment_id"] = _clean(
                    grounding_assessment_id
                )

            if quality_assessment_id:
                revision["quality_assessment_id"] = _clean(
                    quality_assessment_id
                )

            self._append_event(
                insight_id=insight_id,
                event_type="strengthened",
                revision_id=revision[
                    "insight_revision_id"
                ],
                reason=reason,
                details={
                    "epistemic_status": epistemic_status,
                    "supporting_evidence_ids": list(
                        revision[
                            "supporting_evidence_ids"
                        ]
                    ),
                },
            )

            return self.get_insight(
                insight_id,
                include_history=True,
            )

    # ------------------------------------------------------------------
    # Retraction / logical supersession
    # ------------------------------------------------------------------

    def retract_insight(
        self,
        insight_id: str,
        *,
        reason: str,
        epistemic_status: str = "unsupported",
        counterevidence_ids: Sequence[str] = (),
        grounding_assessment_id: str = "",
    ) -> Dict[str, Any]:
        insight_id = _clean(insight_id)
        reason = _clean(reason)
        epistemic_status = _normalize_enum(
            epistemic_status
        )

        if not reason:
            raise InsightRegistryValidationError(
                "Retraction reason cannot be empty."
            )

        if epistemic_status not in {
            "unsupported",
            "contradicted",
            "unresolved",
        }:
            raise InsightRegistryValidationError(
                "Retraction must use unsupported, contradicted, or unresolved "
                "epistemic status."
            )

        with self._lock:
            logical = self._require_insight(
                insight_id
            )

            if logical[
                "lifecycle_status"
            ] == "retracted":
                # Idempotent only if reason/status lineage is consistent enough.
                return self.get_insight(
                    insight_id,
                    include_history=True,
                )

            if logical[
                "lifecycle_status"
            ] == "superseded":
                raise InsightRegistryStateError(
                    "A logically superseded insight cannot be retracted."
                )

            revision = self._current_revision(
                logical
            )

            now = self.clock()

            logical["lifecycle_status"] = "retracted"
            logical["retracted_at"] = now
            logical["retraction_reason"] = reason
            logical["updated_at"] = now

            revision["revision_status"] = "retracted"
            revision["retracted_at"] = now
            revision["retraction_reason"] = reason
            revision["epistemic_status"] = epistemic_status
            revision["counterevidence_ids"] = _stable_unique_strings(
                list(
                    revision["counterevidence_ids"]
                )
                + list(counterevidence_ids)
            )

            if grounding_assessment_id:
                revision["grounding_assessment_id"] = _clean(
                    grounding_assessment_id
                )

            self._append_event(
                insight_id=insight_id,
                event_type="retracted",
                revision_id=revision[
                    "insight_revision_id"
                ],
                reason=reason,
                details={
                    "epistemic_status": epistemic_status,
                    "counterevidence_ids": list(
                        revision[
                            "counterevidence_ids"
                        ]
                    ),
                },
            )

            return self.get_insight(
                insight_id,
                include_history=True,
            )

    def supersede_insight(
        self,
        insight_id: str,
        *,
        superseded_by_insight_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Supersede one logical thesis with another logical thesis.

        Use revise_insight() when the same logical thesis is merely updated.
        Use supersede_insight() when a different thesis replaces it.
        """
        insight_id = _clean(insight_id)
        superseded_by_insight_id = _clean(
            superseded_by_insight_id
        )
        reason = _clean(reason)

        if not reason:
            raise InsightRegistryValidationError(
                "Supersession reason cannot be empty."
            )

        if insight_id == superseded_by_insight_id:
            raise InsightRegistryValidationError(
                "An insight cannot supersede itself."
            )

        with self._lock:
            logical = self._require_insight(
                insight_id
            )
            replacement = self._require_insight(
                superseded_by_insight_id
            )

            if (
                logical["lifecycle_status"]
                in TERMINAL_LOGICAL_STATUSES
            ):
                raise InsightRegistryStateError(
                    "Insight is already terminal."
                )

            if (
                replacement["lifecycle_status"]
                in TERMINAL_LOGICAL_STATUSES
            ):
                raise InsightRegistryStateError(
                    "Replacement insight must be active or weakened."
                )

            now = self.clock()

            logical["lifecycle_status"] = "superseded"
            logical["superseded_by_insight_id"] = (
                superseded_by_insight_id
            )
            logical["updated_at"] = now

            current = self._current_revision(
                logical
            )
            current["revision_status"] = "superseded"
            current["superseded_at"] = now

            self._append_event(
                insight_id=insight_id,
                event_type="superseded",
                revision_id=current[
                    "insight_revision_id"
                ],
                reason=reason,
                details={
                    "superseded_by_insight_id": superseded_by_insight_id,
                },
            )

            return self.get_insight(
                insight_id,
                include_history=True,
            )

    # ------------------------------------------------------------------
    # Lineage updates
    # ------------------------------------------------------------------

    def link_trace(
        self,
        insight_id: str,
        *,
        insight_trace_id: str,
        insight_revision_id: str = "",
    ) -> Dict[str, Any]:
        insight_trace_id = _clean(
            insight_trace_id
        )

        if not insight_trace_id:
            raise InsightRegistryValidationError(
                "insight_trace_id cannot be empty."
            )

        with self._lock:
            logical = self._require_insight(
                _clean(insight_id)
            )

            revision = self._resolve_revision(
                logical,
                insight_revision_id,
            )

            existing = revision[
                "insight_trace_id"
            ]

            if (
                existing
                and existing != insight_trace_id
            ):
                raise InsightRegistryIntegrityError(
                    "Revision already points to a different insight trace."
                )

            revision[
                "insight_trace_id"
            ] = insight_trace_id

            logical[
                "updated_at"
            ] = self.clock()

            self._append_event(
                insight_id=logical[
                    "insight_id"
                ],
                event_type="trace_linked",
                revision_id=revision[
                    "insight_revision_id"
                ],
                reason="trace_linked",
                details={
                    "insight_trace_id": insight_trace_id,
                },
            )

            return self.get_insight(
                logical["insight_id"],
                include_history=True,
            )

    def link_report(
        self,
        insight_id: str,
        *,
        report_id: str,
        insight_revision_id: str = "",
    ) -> Dict[str, Any]:
        report_id = _clean(report_id)

        if not report_id:
            raise InsightRegistryValidationError(
                "report_id cannot be empty."
            )

        with self._lock:
            logical = self._require_insight(
                _clean(insight_id)
            )

            revision = self._resolve_revision(
                logical,
                insight_revision_id,
            )

            if report_id not in revision[
                "report_ids"
            ]:
                revision[
                    "report_ids"
                ].append(
                    report_id
                )

                logical[
                    "updated_at"
                ] = self.clock()

                self._append_event(
                    insight_id=logical[
                        "insight_id"
                    ],
                    event_type="report_linked",
                    revision_id=revision[
                        "insight_revision_id"
                    ],
                    reason="report_linked",
                    details={
                        "report_id": report_id,
                    },
                )

            return self.get_insight(
                logical["insight_id"],
                include_history=True,
            )

    def update_assessment_lineage(
        self,
        insight_id: str,
        *,
        insight_revision_id: str = "",
        quality_assessment_id: str = "",
        grounding_assessment_id: str = "",
        red_team_assessment_id: str = "",
        quality_status: str = "",
        epistemic_status: str = "",
        prospective_status: str = "",
        supporting_evidence_ids: Sequence[str] = (),
        counterevidence_ids: Sequence[str] = (),
        reason: str,
    ) -> Dict[str, Any]:
        """
        Update assessment/evidence lineage without changing immutable insight text.

        If the text itself changes, revise_insight() must be used.
        """
        reason = _clean(reason)

        if not reason:
            raise InsightRegistryValidationError(
                "Assessment update reason cannot be empty."
            )

        with self._lock:
            logical = self._require_mutable_logical_insight(
                _clean(insight_id)
            )

            revision = self._resolve_revision(
                logical,
                insight_revision_id,
            )

            if (
                revision["revision_status"]
                in TERMINAL_REVISION_STATUSES
            ):
                raise InsightRegistryStateError(
                    "Cannot update assessment lineage on a terminal revision."
                )

            if quality_status:
                quality_status = _normalize_enum(
                    quality_status
                )

                if quality_status not in ALLOWED_QUALITY_STATUSES:
                    raise InsightRegistryValidationError(
                        f"Unsupported quality_status={quality_status!r}."
                    )

                revision["quality_status"] = quality_status

            if epistemic_status:
                epistemic_status = _normalize_enum(
                    epistemic_status
                )

                if epistemic_status not in ALLOWED_EPISTEMIC_STATUSES:
                    raise InsightRegistryValidationError(
                        f"Unsupported epistemic_status={epistemic_status!r}."
                    )

                revision["epistemic_status"] = epistemic_status

            if prospective_status:
                prospective_status = _normalize_enum(
                    prospective_status
                )

                if prospective_status not in ALLOWED_PROSPECTIVE_STATUSES:
                    raise InsightRegistryValidationError(
                        f"Unsupported prospective_status={prospective_status!r}."
                    )

                revision["prospective_status"] = prospective_status

            for field, value in (
                (
                    "quality_assessment_id",
                    quality_assessment_id,
                ),
                (
                    "grounding_assessment_id",
                    grounding_assessment_id,
                ),
                (
                    "red_team_assessment_id",
                    red_team_assessment_id,
                ),
            ):
                if _clean(value):
                    revision[field] = _clean(value)

            revision["supporting_evidence_ids"] = _stable_unique_strings(
                list(
                    revision[
                        "supporting_evidence_ids"
                    ]
                )
                + list(supporting_evidence_ids)
            )

            revision["counterevidence_ids"] = _stable_unique_strings(
                list(
                    revision[
                        "counterevidence_ids"
                    ]
                )
                + list(counterevidence_ids)
            )

            logical["updated_at"] = self.clock()

            self._append_event(
                insight_id=logical["insight_id"],
                event_type="assessment_updated",
                revision_id=revision[
                    "insight_revision_id"
                ],
                reason=reason,
                details={
                    "quality_assessment_id": revision[
                        "quality_assessment_id"
                    ],
                    "grounding_assessment_id": revision[
                        "grounding_assessment_id"
                    ],
                    "red_team_assessment_id": revision[
                        "red_team_assessment_id"
                    ],
                    "quality_status": revision[
                        "quality_status"
                    ],
                    "epistemic_status": revision[
                        "epistemic_status"
                    ],
                    "prospective_status": revision[
                        "prospective_status"
                    ],
                },
            )

            return self.get_insight(
                logical["insight_id"],
                include_history=True,
            )

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_insight(
        self,
        insight_id: str,
        *,
        include_history: bool = False,
    ) -> Dict[str, Any]:
        insight_id = _clean(insight_id)

        with self._lock:
            logical = self._require_insight(
                insight_id
            )

            output = deepcopy(
                logical
            )

            output[
                "current_revision"
            ] = deepcopy(
                self._revisions[
                    logical[
                        "current_revision_id"
                    ]
                ]
            )

            if include_history:
                output[
                    "revisions"
                ] = [
                    deepcopy(
                        self._revisions[
                            revision_id
                        ]
                    )
                    for revision_id
                    in logical[
                        "revision_ids"
                    ]
                ]

                output[
                    "lifecycle_events"
                ] = [
                    deepcopy(
                        self._events[
                            event_id
                        ]
                    )
                    for event_id
                    in self._event_index.get(
                        insight_id,
                        [],
                    )
                ]

            return output

    def get_revision(
        self,
        insight_revision_id: str,
    ) -> Dict[str, Any]:
        insight_revision_id = _clean(
            insight_revision_id
        )

        with self._lock:
            revision = self._revisions.get(
                insight_revision_id
            )

            if revision is None:
                raise InsightRegistryValidationError(
                    f"Unknown insight_revision_id={insight_revision_id!r}."
                )

            return deepcopy(
                revision
            )

    def list_insights(
        self,
        *,
        lifecycle_status: str = "",
        insight_type: str = "",
        include_terminal: bool = True,
    ) -> list[Dict[str, Any]]:
        lifecycle_status = _normalize_enum(
            lifecycle_status
        )
        insight_type = _normalize_enum(
            insight_type
        )

        if (
            lifecycle_status
            and lifecycle_status
            not in LOGICAL_STATUSES
        ):
            raise InsightRegistryValidationError(
                f"Unsupported lifecycle_status={lifecycle_status!r}."
            )

        if (
            insight_type
            and insight_type
            not in ALLOWED_INSIGHT_TYPES
        ):
            raise InsightRegistryValidationError(
                f"Unsupported insight_type={insight_type!r}."
            )

        with self._lock:
            result = []

            for logical in self._insights.values():
                status = logical[
                    "lifecycle_status"
                ]

                if (
                    not include_terminal
                    and status
                    in TERMINAL_LOGICAL_STATUSES
                ):
                    continue

                if (
                    lifecycle_status
                    and status != lifecycle_status
                ):
                    continue

                if (
                    insight_type
                    and logical[
                        "insight_type"
                    ]
                    != insight_type
                ):
                    continue

                result.append(
                    self.get_insight(
                        logical["insight_id"],
                        include_history=False,
                    )
                )

            result.sort(
                key=lambda item: (
                    item["created_at"],
                    item["insight_id"],
                )
            )

            return result

    def lifecycle_history(
        self,
        insight_id: str,
    ) -> list[Dict[str, Any]]:
        insight_id = _clean(insight_id)

        with self._lock:
            self._require_insight(
                insight_id
            )

            return [
                deepcopy(
                    self._events[
                        event_id
                    ]
                )
                for event_id
                in self._event_index.get(
                    insight_id,
                    [],
                )
            ]

    # ------------------------------------------------------------------
    # Audit fingerprints
    # ------------------------------------------------------------------

    def revision_fingerprint(
        self,
        insight_revision_id: str,
    ) -> Dict[str, str]:
        revision = self.get_revision(
            insight_revision_id
        )

        return {
            "insight_id": revision[
                "insight_id"
            ],
            "insight_revision_id": revision[
                "insight_revision_id"
            ],
            "revision_fingerprint": revision[
                "revision_fingerprint"
            ],
        }

    def registry_fingerprint(
        self,
    ) -> str:
        with self._lock:
            return _fingerprint(
                {
                    "insights": self._insights,
                    "revisions": self._revisions,
                    "events": self._events,
                }
            )

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def export_snapshot(
        self,
    ) -> Dict[str, Any]:
        with self._lock:
            payload = {
                "service_version": SERVICE_VERSION,
                "insights": deepcopy(
                    self._insights
                ),
                "revisions": deepcopy(
                    self._revisions
                ),
                "revision_owner": deepcopy(
                    self._revision_owner
                ),
                "events": deepcopy(
                    self._events
                ),
                "event_index": deepcopy(
                    self._event_index
                ),
                "metadata": {
                    "stable_logical_identity": True,
                    "revision_text_is_immutable": True,
                    "registry_state_is_not_truth": True,
                    "private_chain_of_thought_stored": False,
                    "authorizes_release": False,
                    "modifies_policy": False,
                    "modifies_runtime": False,
                },
            }

            payload[
                "snapshot_sha256"
            ] = _fingerprint(
                {
                    key: value
                    for key, value
                    in payload.items()
                    if key != "snapshot_sha256"
                }
            )

            return payload

    def restore_snapshot(
        self,
        snapshot: Mapping[str, Any],
    ) -> None:
        if not isinstance(snapshot, Mapping):
            raise InsightRegistryValidationError(
                "snapshot must be an object."
            )

        _require_no_private_reasoning(
            snapshot,
            field_name="snapshot",
        )

        supplied_hash = _clean(
            snapshot.get(
                "snapshot_sha256"
            )
        )

        if supplied_hash:
            computed = _fingerprint(
                {
                    key: value
                    for key, value
                    in snapshot.items()
                    if key != "snapshot_sha256"
                }
            )

            if supplied_hash != computed:
                raise InsightRegistryIntegrityError(
                    "Registry snapshot fingerprint mismatch."
                )

        raw_insights = snapshot.get(
            "insights"
        ) or {}
        raw_revisions = snapshot.get(
            "revisions"
        ) or {}
        raw_revision_owner = snapshot.get(
            "revision_owner"
        ) or {}
        raw_events = snapshot.get(
            "events"
        ) or {}
        raw_event_index = snapshot.get(
            "event_index"
        ) or {}

        for name, value in (
            ("insights", raw_insights),
            ("revisions", raw_revisions),
            ("revision_owner", raw_revision_owner),
            ("events", raw_events),
            ("event_index", raw_event_index),
        ):
            if not isinstance(value, Mapping):
                raise InsightRegistryValidationError(
                    f"snapshot.{name} must be an object."
                )

        insights = deepcopy(
            dict(raw_insights)
        )
        revisions = deepcopy(
            dict(raw_revisions)
        )
        revision_owner = deepcopy(
            dict(raw_revision_owner)
        )
        events = deepcopy(
            dict(raw_events)
        )
        event_index = deepcopy(
            dict(raw_event_index)
        )

        self._validate_snapshot_state(
            insights=insights,
            revisions=revisions,
            revision_owner=revision_owner,
            events=events,
            event_index=event_index,
        )

        with self._lock:
            self._insights = insights
            self._revisions = revisions
            self._revision_owner = revision_owner
            self._events = events
            self._event_index = event_index

    # ------------------------------------------------------------------
    # Internal builders / normalization
    # ------------------------------------------------------------------

    def _normalize_revision_input(
        self,
        *,
        insight_revision_id: str,
        insight_type: str,
        text: str,
        supporting_evidence_ids: Sequence[str],
        counterevidence_ids: Sequence[str],
        insight_trace_id: str,
        quality_assessment_id: str,
        grounding_assessment_id: str,
        red_team_assessment_id: str,
        quality_status: str,
        epistemic_status: str,
        prospective_status: str,
        mechanism: str,
        second_order_effect: str,
        critical_unknowns: Sequence[str],
        report_ids: Sequence[str],
        metadata: Optional[Mapping[str, Any]],
    ) -> Dict[str, Any]:
        insight_type = _normalize_enum(
            insight_type
        )
        text = _clean(text)

        if insight_type not in ALLOWED_INSIGHT_TYPES:
            raise InsightRegistryValidationError(
                f"Unsupported insight_type={insight_type!r}."
            )

        if not text:
            raise InsightRegistryValidationError(
                "Insight text cannot be empty."
            )

        quality_status = _normalize_enum(
            quality_status
            or "unassessed"
        )

        if quality_status not in ALLOWED_QUALITY_STATUSES:
            raise InsightRegistryValidationError(
                f"Unsupported quality_status={quality_status!r}."
            )

        epistemic_status = _normalize_enum(
            epistemic_status
            or "unresolved"
        )

        if epistemic_status not in ALLOWED_EPISTEMIC_STATUSES:
            raise InsightRegistryValidationError(
                f"Unsupported epistemic_status={epistemic_status!r}."
            )

        prospective_status = _normalize_enum(
            prospective_status
        )

        if prospective_status not in ALLOWED_PROSPECTIVE_STATUSES:
            raise InsightRegistryValidationError(
                f"Unsupported prospective_status={prospective_status!r}."
            )

        if (
            insight_type == "descriptive"
            and prospective_status
        ):
            raise InsightRegistryValidationError(
                "Descriptive insights cannot carry prospective_status."
            )

        metadata = (
            {}
            if metadata is None
            else deepcopy(
                dict(metadata)
            )
        )

        _require_no_private_reasoning(
            metadata,
            field_name="revision metadata",
        )

        return {
            "insight_revision_id": _clean(
                insight_revision_id
            ),
            "insight_type": insight_type,
            "text": text,
            "supporting_evidence_ids": _stable_unique_strings(
                supporting_evidence_ids
            ),
            "counterevidence_ids": _stable_unique_strings(
                counterevidence_ids
            ),
            "insight_trace_id": _clean(
                insight_trace_id
            ),
            "quality_assessment_id": _clean(
                quality_assessment_id
            ),
            "grounding_assessment_id": _clean(
                grounding_assessment_id
            ),
            "red_team_assessment_id": _clean(
                red_team_assessment_id
            ),
            "quality_status": quality_status,
            "epistemic_status": epistemic_status,
            "prospective_status": prospective_status,
            "mechanism": _clean(
                mechanism
            ),
            "second_order_effect": _clean(
                second_order_effect
            ),
            "critical_unknowns": _stable_unique_strings(
                critical_unknowns
            ),
            "report_ids": _stable_unique_strings(
                report_ids
            ),
            "metadata": metadata,
        }

    def _build_revision_record(
        self,
        *,
        insight_id: str,
        revision_id: str,
        revision_number: int,
        parent_revision_id: str,
        revision_status: str,
        created_at: str,
        superseded_at: str,
        superseded_by_revision_id: str,
        weakened_at: str,
        retracted_at: str,
        retraction_reason: str,
        insight_type: str,
        text: str,
        supporting_evidence_ids: Sequence[str],
        counterevidence_ids: Sequence[str],
        insight_trace_id: str,
        quality_assessment_id: str,
        grounding_assessment_id: str,
        red_team_assessment_id: str,
        quality_status: str,
        epistemic_status: str,
        prospective_status: str,
        mechanism: str,
        second_order_effect: str,
        critical_unknowns: Sequence[str],
        report_ids: Sequence[str],
        metadata: Mapping[str, Any],
    ) -> Dict[str, Any]:
        record = {
            "insight_id": insight_id,
            "insight_revision_id": revision_id,
            "revision_number": int(
                revision_number
            ),
            "parent_revision_id": _clean(
                parent_revision_id
            ),
            "revision_status": _normalize_enum(
                revision_status
            ),
            "insight_type": insight_type,
            "text": text,
            "supporting_evidence_ids": list(
                supporting_evidence_ids
            ),
            "counterevidence_ids": list(
                counterevidence_ids
            ),
            "insight_trace_id": insight_trace_id,
            "quality_assessment_id": quality_assessment_id,
            "grounding_assessment_id": grounding_assessment_id,
            "red_team_assessment_id": red_team_assessment_id,
            "quality_status": quality_status,
            "epistemic_status": epistemic_status,
            "prospective_status": prospective_status,
            "mechanism": mechanism,
            "second_order_effect": second_order_effect,
            "critical_unknowns": list(
                critical_unknowns
            ),
            "report_ids": list(
                report_ids
            ),
            "created_at": created_at,
            "weakened_at": weakened_at,
            "superseded_at": superseded_at,
            "superseded_by_revision_id": superseded_by_revision_id,
            "retracted_at": retracted_at,
            "retraction_reason": retraction_reason,
            "metadata": deepcopy(
                dict(metadata)
            ),
        }

        record[
            "revision_fingerprint"
        ] = self._immutable_revision_fingerprint(
            record
        )

        return record

    def _immutable_revision_fingerprint(
        self,
        revision: Mapping[str, Any],
    ) -> str:
        """
        Fingerprint the immutable semantic/version payload.

        Lifecycle markers and later assessment lineage updates are deliberately
        excluded so the exact text/version identity remains stable.
        """
        return _fingerprint(
            {
                "insight_id": revision[
                    "insight_id"
                ],
                "insight_revision_id": revision[
                    "insight_revision_id"
                ],
                "revision_number": revision[
                    "revision_number"
                ],
                "parent_revision_id": revision[
                    "parent_revision_id"
                ],
                "insight_type": revision[
                    "insight_type"
                ],
                "text": revision[
                    "text"
                ],
                "mechanism": revision[
                    "mechanism"
                ],
                "second_order_effect": revision[
                    "second_order_effect"
                ],
                "critical_unknowns": revision[
                    "critical_unknowns"
                ],
            }
        )

    def _revision_comparable_payload(
        self,
        revision: Mapping[str, Any],
    ) -> Dict[str, Any]:
        return {
            "insight_type": _normalize_enum(
                revision.get(
                    "insight_type"
                )
            ),
            "text": _clean(
                revision.get(
                    "text"
                )
            ),
            "supporting_evidence_ids": _stable_unique_strings(
                revision.get(
                    "supporting_evidence_ids"
                )
                or []
            ),
            "counterevidence_ids": _stable_unique_strings(
                revision.get(
                    "counterevidence_ids"
                )
                or []
            ),
            "insight_trace_id": _clean(
                revision.get(
                    "insight_trace_id"
                )
            ),
            "quality_assessment_id": _clean(
                revision.get(
                    "quality_assessment_id"
                )
            ),
            "grounding_assessment_id": _clean(
                revision.get(
                    "grounding_assessment_id"
                )
            ),
            "red_team_assessment_id": _clean(
                revision.get(
                    "red_team_assessment_id"
                )
            ),
            "quality_status": _normalize_enum(
                revision.get(
                    "quality_status"
                )
            ),
            "epistemic_status": _normalize_enum(
                revision.get(
                    "epistemic_status"
                )
            ),
            "prospective_status": _normalize_enum(
                revision.get(
                    "prospective_status"
                )
            ),
            "mechanism": _clean(
                revision.get(
                    "mechanism"
                )
            ),
            "second_order_effect": _clean(
                revision.get(
                    "second_order_effect"
                )
            ),
            "critical_unknowns": _stable_unique_strings(
                revision.get(
                    "critical_unknowns"
                )
                or []
            ),
            "report_ids": _stable_unique_strings(
                revision.get(
                    "report_ids"
                )
                or []
            ),
            "metadata": deepcopy(
                dict(
                    revision.get(
                        "metadata"
                    )
                    or {}
                )
            ),
        }

    # ------------------------------------------------------------------
    # Events
    # ------------------------------------------------------------------

    def _append_event(
        self,
        *,
        insight_id: str,
        event_type: str,
        revision_id: str,
        reason: str,
        details: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> Dict[str, Any]:
        event_type = _normalize_enum(
            event_type
        )

        if event_type not in ALLOWED_EVENT_TYPES:
            raise InsightRegistryValidationError(
                f"Unsupported event_type={event_type!r}."
            )

        details = (
            {}
            if details is None
            else deepcopy(
                dict(details)
            )
        )

        _require_no_private_reasoning(
            details,
            field_name="lifecycle event details",
        )

        recorded_at = self.clock()

        event_id = _stable_id(
            "INSIGHTEVENT",
            {
                "insight_id": insight_id,
                "event_type": event_type,
                "revision_id": revision_id,
                "reason": _clean(reason),
                "details": details,
                "recorded_at": recorded_at,
                "ordinal": len(
                    self._event_index.get(
                        insight_id,
                        [],
                    )
                ),
            },
        )

        if event_id in self._events:
            raise InsightRegistryIntegrityError(
                "Lifecycle event ID collision."
            )

        event = {
            "event_id": event_id,
            "insight_id": insight_id,
            "insight_revision_id": _clean(
                revision_id
            ),
            "event_type": event_type,
            "reason": _clean(
                reason
            ),
            "details": details,
            "recorded_at": recorded_at,
        }

        self._events[
            event_id
        ] = event

        self._event_index.setdefault(
            insight_id,
            [],
        ).append(
            event_id
        )

        return event

    # ------------------------------------------------------------------
    # Internal access
    # ------------------------------------------------------------------

    def _require_insight(
        self,
        insight_id: str,
    ) -> Dict[str, Any]:
        insight = self._insights.get(
            insight_id
        )

        if insight is None:
            raise InsightRegistryValidationError(
                f"Unknown insight_id={insight_id!r}."
            )

        return insight

    def _require_mutable_logical_insight(
        self,
        insight_id: str,
    ) -> Dict[str, Any]:
        insight = self._require_insight(
            insight_id
        )

        if (
            insight["lifecycle_status"]
            in TERMINAL_LOGICAL_STATUSES
        ):
            raise InsightRegistryStateError(
                "Logical insight is terminal and cannot be modified."
            )

        return insight

    def _current_revision(
        self,
        logical: Mapping[str, Any],
    ) -> Dict[str, Any]:
        revision_id = logical[
            "current_revision_id"
        ]

        revision = self._revisions.get(
            revision_id
        )

        if revision is None:
            raise InsightRegistryIntegrityError(
                "Logical insight points to a missing current revision."
            )

        return revision

    def _resolve_revision(
        self,
        logical: Mapping[str, Any],
        insight_revision_id: str,
    ) -> Dict[str, Any]:
        revision_id = (
            _clean(
                insight_revision_id
            )
            or logical[
                "current_revision_id"
            ]
        )

        if revision_id not in logical[
            "revision_ids"
        ]:
            raise InsightRegistryIntegrityError(
                "Requested revision does not belong to the insight."
            )

        revision = self._revisions.get(
            revision_id
        )

        if revision is None:
            raise InsightRegistryIntegrityError(
                "Revision index is inconsistent."
            )

        return revision

    # ------------------------------------------------------------------
    # Snapshot validation
    # ------------------------------------------------------------------

    def _validate_snapshot_state(
        self,
        *,
        insights: Mapping[str, Any],
        revisions: Mapping[str, Any],
        revision_owner: Mapping[str, Any],
        events: Mapping[str, Any],
        event_index: Mapping[str, Any],
    ) -> None:
        for insight_id, logical in insights.items():
            if not isinstance(logical, Mapping):
                raise InsightRegistryValidationError(
                    "Snapshot logical insight must be an object."
                )

            if (
                _clean(
                    logical.get(
                        "insight_id"
                    )
                )
                != insight_id
            ):
                raise InsightRegistryIntegrityError(
                    "Snapshot insight key/id mismatch."
                )

            status = _normalize_enum(
                logical.get(
                    "lifecycle_status"
                )
            )

            if status not in LOGICAL_STATUSES:
                raise InsightRegistryValidationError(
                    f"Invalid logical lifecycle status={status!r}."
                )

            revision_ids = _stable_unique_strings(
                logical.get(
                    "revision_ids"
                )
                or []
            )

            if not revision_ids:
                raise InsightRegistryIntegrityError(
                    "Logical insight has no revisions."
                )

            current_revision_id = _clean(
                logical.get(
                    "current_revision_id"
                )
            )

            if current_revision_id not in revision_ids:
                raise InsightRegistryIntegrityError(
                    "Current revision is not in logical revision_ids."
                )

            for revision_id in revision_ids:
                if revision_id not in revisions:
                    raise InsightRegistryIntegrityError(
                        "Logical insight references missing revision."
                    )

                if (
                    _clean(
                        revision_owner.get(
                            revision_id
                        )
                    )
                    != insight_id
                ):
                    raise InsightRegistryIntegrityError(
                        "revision_owner is inconsistent."
                    )

        for revision_id, revision in revisions.items():
            if not isinstance(revision, Mapping):
                raise InsightRegistryValidationError(
                    "Snapshot revision must be an object."
                )

            if (
                _clean(
                    revision.get(
                        "insight_revision_id"
                    )
                )
                != revision_id
            ):
                raise InsightRegistryIntegrityError(
                    "Snapshot revision key/id mismatch."
                )

            owner = _clean(
                revision.get(
                    "insight_id"
                )
            )

            if owner not in insights:
                raise InsightRegistryIntegrityError(
                    "Revision belongs to unknown insight."
                )

            status = _normalize_enum(
                revision.get(
                    "revision_status"
                )
            )

            if status not in REVISION_STATUSES:
                raise InsightRegistryValidationError(
                    f"Invalid revision status={status!r}."
                )

            expected_fingerprint = self._immutable_revision_fingerprint(
                revision
            )

            if (
                _clean(
                    revision.get(
                        "revision_fingerprint"
                    )
                )
                != expected_fingerprint
            ):
                raise InsightRegistryIntegrityError(
                    "Revision fingerprint mismatch."
                )

        for event_id, event in events.items():
            if not isinstance(event, Mapping):
                raise InsightRegistryValidationError(
                    "Snapshot lifecycle event must be an object."
                )

            if (
                _clean(
                    event.get(
                        "event_id"
                    )
                )
                != event_id
            ):
                raise InsightRegistryIntegrityError(
                    "Snapshot lifecycle event key/id mismatch."
                )

            event_type = _normalize_enum(
                event.get(
                    "event_type"
                )
            )

            if event_type not in ALLOWED_EVENT_TYPES:
                raise InsightRegistryValidationError(
                    f"Invalid event_type={event_type!r}."
                )

            owner = _clean(
                event.get(
                    "insight_id"
                )
            )

            if owner not in insights:
                raise InsightRegistryIntegrityError(
                    "Lifecycle event belongs to unknown insight."
                )

        for insight_id, event_ids in event_index.items():
            if insight_id not in insights:
                raise InsightRegistryIntegrityError(
                    "event_index contains unknown insight."
                )

            for event_id in _stable_unique_strings(
                event_ids
            ):
                if event_id not in events:
                    raise InsightRegistryIntegrityError(
                        "event_index references missing lifecycle event."
                    )

                if (
                    _clean(
                        events[
                            event_id
                        ].get(
                            "insight_id"
                        )
                    )
                    != insight_id
                ):
                    raise InsightRegistryIntegrityError(
                        "event_index owner mismatch."
                    )