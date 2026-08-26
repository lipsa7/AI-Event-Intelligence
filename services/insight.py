"""
Canonical insight-domain models.

Recommended location:
    models/insight.py

Purpose
-------
This module defines the stable data contract shared by:

    StrategicInferenceAgent
            ↓
        Insight
            ↓
    InsightQualityService
    GroundingService
    RedTeamAgent
    InsightTraceService
    InsightRegistryService
    ReportService

The model deliberately separates:

    logical insight identity
        INSIGHT-123

from:

    immutable revision identity
        INSIGHTREV-001

and separates:

    factual premises
    mechanism
    feasibility
    counterevidence
    alternative explanations
    final inference

Critical epistemic boundary
---------------------------
An Insight object is a structured CLAIM object.

It is NOT:
- factual evidence;
- proof;
- a grounding result;
- a release decision;
- a source-provenance assertion;
- private chain-of-thought.

Evidence is referenced by stable IDs only.

Prospective insights
--------------------
Prospective insights support the architecture:

    factual premises
        +
    analogical mechanism evidence
        +
    feasibility / magnitude / blockers
        +
    counter-incentives
        ↓
    prospective inference

Direct precedent is optional. Lack of direct precedent is not automatically a
quality failure.

The model therefore supports:

    direct_precedent_found: bool | None
    prospective_status:
        ""
        "plausible_unprecedented"
        "plausible_with_precedent"
        "weakly_supported"
        "unsupported"
        "contradicted"
        "unresolved"

Important:
    analogical evidence may support a mechanism,
    but it does not prove the predicted action itself.

Immutability
------------
The canonical InsightRevision dataclass is frozen.

When insight text/mechanism/semantic content changes, create a new revision.
InsightRegistryService owns lifecycle transitions such as:

    ACTIVE → WEAKENED → REVISED / RETRACTED / SUPERSEDED

This model module does not perform lifecycle mutation.

No paid dependency is required.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, Mapping, Optional, Sequence, Tuple


MODEL_VERSION = "insight-model-v1.0"

ALLOWED_INSIGHT_TYPES = {
    "descriptive",
    "prospective",
}

ALLOWED_PREMISE_STATUSES = {
    "supported",
    "partially_supported",
    "unsupported",
    "contradicted",
    "unresolved",
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

ALLOWED_REVISION_STATUSES = {
    "active",
    "weakened",
    "superseded",
    "retracted",
}

ALLOWED_LOGICAL_STATUSES = {
    "active",
    "weakened",
    "retracted",
    "superseded",
}

ALLOWED_EVIDENCE_ROLES = {
    "direct_premise_evidence",
    "analogical_mechanism_evidence",
    "feasibility_evidence",
    "counterevidence",
    "counter_incentive_evidence",
    "alternative_explanation_evidence",
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


class InsightModelError(ValueError):
    """Base insight-model validation error."""


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _normalize_enum(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    return text.strip("_")


def _stable_unique_strings(values: Any) -> Tuple[str, ...]:
    if values is None:
        return ()

    if isinstance(values, str):
        values = [values]

    if not isinstance(values, (list, tuple, set)):
        raise InsightModelError(
            "Expected a string or sequence of strings."
        )

    result = []
    seen = set()

    for value in values:
        text = _clean(value)

        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return tuple(result)


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

    if hasattr(value, "__dataclass_fields__"):
        return _json_safe(
            asdict(value)
        )

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


def stable_id(
    prefix: str,
    payload: Any,
    *,
    length: int = 24,
) -> str:
    prefix = _clean(prefix).upper()

    if not prefix:
        raise InsightModelError(
            "ID prefix cannot be empty."
        )

    return (
        f"{prefix}-"
        + _sha256(
            _canonical_json_bytes(payload)
        )[:length].upper()
    )


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


def _validate_metadata(
    metadata: Mapping[str, Any],
) -> Dict[str, Any]:
    if not isinstance(metadata, Mapping):
        raise InsightModelError(
            "metadata must be an object."
        )

    prohibited = _contains_prohibited_field(
        metadata
    )

    if prohibited:
        raise InsightModelError(
            "metadata contains prohibited private/raw reasoning field "
            f"at {prohibited!r}."
        )

    return {
        str(key): _json_safe(value)
        for key, value in metadata.items()
    }


# ---------------------------------------------------------------------------
# Submodels
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Premise:
    premise_id: str
    text: str
    evidence_ids: Tuple[str, ...] = ()
    status: str = "unresolved"
    notes: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "premise_id",
            _clean(self.premise_id),
        )
        object.__setattr__(
            self,
            "text",
            _clean(self.text),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            _stable_unique_strings(
                self.evidence_ids
            ),
        )
        object.__setattr__(
            self,
            "status",
            _normalize_enum(
                self.status
            ),
        )
        object.__setattr__(
            self,
            "notes",
            _clean(self.notes),
        )

        if not self.premise_id:
            raise InsightModelError(
                "Premise.premise_id cannot be empty."
            )

        if not self.text:
            raise InsightModelError(
                "Premise.text cannot be empty."
            )

        if (
            self.status
            not in ALLOWED_PREMISE_STATUSES
        ):
            raise InsightModelError(
                f"Unsupported premise status={self.status!r}."
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "Premise":
        if not isinstance(value, Mapping):
            raise InsightModelError(
                "Premise must be an object."
            )

        premise_id = _clean(
            value.get("premise_id")
            or value.get("id")
        )

        text = _clean(
            value.get("text")
            or value.get("premise")
            or value.get("statement")
        )

        if not premise_id and text:
            premise_id = stable_id(
                "PREMISE",
                {
                    "text": text,
                },
            )

        return cls(
            premise_id=premise_id,
            text=text,
            evidence_ids=_stable_unique_strings(
                value.get("evidence_ids")
                or []
            ),
            status=_normalize_enum(
                value.get("status")
                or "unresolved"
            ),
            notes=_clean(
                value.get("notes")
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(
            asdict(self)
        )


@dataclass(frozen=True)
class Mechanism:
    description: str = ""
    analogical_evidence_ids: Tuple[str, ...] = ()
    status: str = "unresolved"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "description",
            _clean(self.description),
        )
        object.__setattr__(
            self,
            "analogical_evidence_ids",
            _stable_unique_strings(
                self.analogical_evidence_ids
            ),
        )
        object.__setattr__(
            self,
            "status",
            _normalize_enum(
                self.status
            ),
        )

        if (
            self.status
            not in ALLOWED_PREMISE_STATUSES
        ):
            raise InsightModelError(
                f"Unsupported mechanism status={self.status!r}."
            )

    @classmethod
    def from_value(
        cls,
        value: Any,
    ) -> "Mechanism":
        if value is None:
            return cls()

        if isinstance(value, str):
            return cls(
                description=value,
            )

        if not isinstance(value, Mapping):
            raise InsightModelError(
                "mechanism must be text or an object."
            )

        return cls(
            description=_clean(
                value.get("description")
                or value.get("mechanism")
            ),
            analogical_evidence_ids=_stable_unique_strings(
                value.get(
                    "analogical_evidence_ids"
                )
                or value.get(
                    "evidence_ids"
                )
                or []
            ),
            status=_normalize_enum(
                value.get("status")
                or "unresolved"
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(
            asdict(self)
        )


@dataclass(frozen=True)
class FeasibilityAssessment:
    constraint_ids: Tuple[str, ...] = ()
    feasibility_evidence_ids: Tuple[str, ...] = ()
    magnitude_check: str = ""
    blocking_factors: Tuple[str, ...] = ()
    status: str = "unresolved"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "constraint_ids",
            _stable_unique_strings(
                self.constraint_ids
            ),
        )
        object.__setattr__(
            self,
            "feasibility_evidence_ids",
            _stable_unique_strings(
                self.feasibility_evidence_ids
            ),
        )
        object.__setattr__(
            self,
            "magnitude_check",
            _clean(self.magnitude_check),
        )
        object.__setattr__(
            self,
            "blocking_factors",
            _stable_unique_strings(
                self.blocking_factors
            ),
        )
        object.__setattr__(
            self,
            "status",
            _normalize_enum(
                self.status
            ),
        )

        if (
            self.status
            not in ALLOWED_PREMISE_STATUSES
        ):
            raise InsightModelError(
                f"Unsupported feasibility status={self.status!r}."
            )

    @classmethod
    def from_mapping(
        cls,
        value: Optional[Mapping[str, Any]],
    ) -> "FeasibilityAssessment":
        if value is None:
            return cls()

        if not isinstance(value, Mapping):
            raise InsightModelError(
                "feasibility must be an object."
            )

        return cls(
            constraint_ids=_stable_unique_strings(
                value.get("constraint_ids")
                or []
            ),
            feasibility_evidence_ids=_stable_unique_strings(
                value.get(
                    "feasibility_evidence_ids"
                )
                or value.get(
                    "evidence_ids"
                )
                or []
            ),
            magnitude_check=_clean(
                value.get("magnitude_check")
            ),
            blocking_factors=_stable_unique_strings(
                value.get("blocking_factors")
                or []
            ),
            status=_normalize_enum(
                value.get("status")
                or "unresolved"
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(
            asdict(self)
        )


@dataclass(frozen=True)
class AlternativeExplanation:
    alternative_id: str
    text: str
    evidence_ids: Tuple[str, ...] = ()
    status: str = "unresolved"

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "alternative_id",
            _clean(self.alternative_id),
        )
        object.__setattr__(
            self,
            "text",
            _clean(self.text),
        )
        object.__setattr__(
            self,
            "evidence_ids",
            _stable_unique_strings(
                self.evidence_ids
            ),
        )
        object.__setattr__(
            self,
            "status",
            _normalize_enum(
                self.status
            ),
        )

        if not self.alternative_id:
            raise InsightModelError(
                "AlternativeExplanation.alternative_id cannot be empty."
            )

        if not self.text:
            raise InsightModelError(
                "AlternativeExplanation.text cannot be empty."
            )

        if (
            self.status
            not in ALLOWED_PREMISE_STATUSES
        ):
            raise InsightModelError(
                f"Unsupported alternative status={self.status!r}."
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "AlternativeExplanation":
        if not isinstance(value, Mapping):
            raise InsightModelError(
                "Alternative explanation must be an object."
            )

        text = _clean(
            value.get("text")
            or value.get("explanation")
        )

        alternative_id = _clean(
            value.get("alternative_id")
            or value.get("id")
        )

        if not alternative_id and text:
            alternative_id = stable_id(
                "ALT",
                {
                    "text": text,
                },
            )

        return cls(
            alternative_id=alternative_id,
            text=text,
            evidence_ids=_stable_unique_strings(
                value.get("evidence_ids")
                or []
            ),
            status=_normalize_enum(
                value.get("status")
                or "unresolved"
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(
            asdict(self)
        )


# ---------------------------------------------------------------------------
# Canonical immutable revision
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InsightRevision:
    insight_id: str
    insight_revision_id: str
    insight_type: str
    text: str

    premises: Tuple[Premise, ...] = ()
    mechanism: Mechanism = field(
        default_factory=Mechanism
    )
    feasibility: FeasibilityAssessment = field(
        default_factory=FeasibilityAssessment
    )

    supporting_evidence_ids: Tuple[str, ...] = ()
    counterevidence_ids: Tuple[str, ...] = ()
    counter_incentive_evidence_ids: Tuple[str, ...] = ()
    alternative_explanations: Tuple[
        AlternativeExplanation,
        ...
    ] = ()

    event_ids: Tuple[str, ...] = ()
    constraint_ids: Tuple[str, ...] = ()
    analytical_lenses: Tuple[str, ...] = ()
    reasoning_patterns: Tuple[str, ...] = ()

    structural_change: str = ""
    second_order_effect: str = ""
    critical_unknowns: Tuple[str, ...] = ()

    direct_precedent_found: Optional[bool] = None
    prospective_status: str = ""

    quality_status: str = "unassessed"
    epistemic_status: str = "unresolved"
    revision_status: str = "active"

    insight_trace_id: str = ""
    quality_assessment_id: str = ""
    grounding_assessment_id: str = ""
    red_team_assessment_id: str = ""
    report_ids: Tuple[str, ...] = ()

    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "insight_id",
            _clean(self.insight_id),
        )
        object.__setattr__(
            self,
            "insight_revision_id",
            _clean(self.insight_revision_id),
        )
        object.__setattr__(
            self,
            "insight_type",
            _normalize_enum(
                self.insight_type
            ),
        )
        object.__setattr__(
            self,
            "text",
            _clean(self.text),
        )

        if not self.insight_id:
            raise InsightModelError(
                "insight_id cannot be empty."
            )

        if not self.insight_revision_id:
            raise InsightModelError(
                "insight_revision_id cannot be empty."
            )

        if (
            self.insight_type
            not in ALLOWED_INSIGHT_TYPES
        ):
            raise InsightModelError(
                f"Unsupported insight_type={self.insight_type!r}."
            )

        if not self.text:
            raise InsightModelError(
                "Insight text cannot be empty."
            )

        object.__setattr__(
            self,
            "premises",
            tuple(
                item
                if isinstance(item, Premise)
                else Premise.from_mapping(item)
                for item in self.premises
            ),
        )

        object.__setattr__(
            self,
            "mechanism",
            (
                self.mechanism
                if isinstance(
                    self.mechanism,
                    Mechanism,
                )
                else Mechanism.from_value(
                    self.mechanism
                )
            ),
        )

        object.__setattr__(
            self,
            "feasibility",
            (
                self.feasibility
                if isinstance(
                    self.feasibility,
                    FeasibilityAssessment,
                )
                else FeasibilityAssessment.from_mapping(
                    self.feasibility
                )
            ),
        )

        object.__setattr__(
            self,
            "alternative_explanations",
            tuple(
                item
                if isinstance(
                    item,
                    AlternativeExplanation,
                )
                else AlternativeExplanation.from_mapping(
                    item
                )
                for item in self.alternative_explanations
            ),
        )

        for field_name in (
            "supporting_evidence_ids",
            "counterevidence_ids",
            "counter_incentive_evidence_ids",
            "event_ids",
            "constraint_ids",
            "analytical_lenses",
            "reasoning_patterns",
            "critical_unknowns",
            "report_ids",
        ):
            object.__setattr__(
                self,
                field_name,
                _stable_unique_strings(
                    getattr(
                        self,
                        field_name,
                    )
                ),
            )

        for field_name in (
            "structural_change",
            "second_order_effect",
            "insight_trace_id",
            "quality_assessment_id",
            "grounding_assessment_id",
            "red_team_assessment_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _clean(
                    getattr(
                        self,
                        field_name,
                    )
                ),
            )

        prospective_status = _normalize_enum(
            self.prospective_status
        )

        quality_status = _normalize_enum(
            self.quality_status
            or "unassessed"
        )

        epistemic_status = _normalize_enum(
            self.epistemic_status
            or "unresolved"
        )

        revision_status = _normalize_enum(
            self.revision_status
            or "active"
        )

        object.__setattr__(
            self,
            "prospective_status",
            prospective_status,
        )
        object.__setattr__(
            self,
            "quality_status",
            quality_status,
        )
        object.__setattr__(
            self,
            "epistemic_status",
            epistemic_status,
        )
        object.__setattr__(
            self,
            "revision_status",
            revision_status,
        )

        if (
            prospective_status
            not in ALLOWED_PROSPECTIVE_STATUSES
        ):
            raise InsightModelError(
                f"Unsupported prospective_status={prospective_status!r}."
            )

        if (
            self.insight_type == "descriptive"
            and prospective_status
        ):
            raise InsightModelError(
                "Descriptive insight cannot carry prospective_status."
            )

        if (
            quality_status
            not in ALLOWED_QUALITY_STATUSES
        ):
            raise InsightModelError(
                f"Unsupported quality_status={quality_status!r}."
            )

        if (
            epistemic_status
            not in ALLOWED_EPISTEMIC_STATUSES
        ):
            raise InsightModelError(
                f"Unsupported epistemic_status={epistemic_status!r}."
            )

        if (
            revision_status
            not in ALLOWED_REVISION_STATUSES
        ):
            raise InsightModelError(
                f"Unsupported revision_status={revision_status!r}."
            )

        metadata = _validate_metadata(
            self.metadata
        )
        object.__setattr__(
            self,
            "metadata",
            metadata,
        )

        self._validate_evidence_roles_and_references()

    def _validate_evidence_roles_and_references(
        self,
    ) -> None:
        premise_evidence = set()

        for premise in self.premises:
            premise_evidence.update(
                premise.evidence_ids
            )

        # Supporting evidence may be declared at revision level or premise level.
        all_support = set(
            self.supporting_evidence_ids
        ) | premise_evidence

        analogical = set(
            self.mechanism.analogical_evidence_ids
        )

        feasibility = set(
            self.feasibility.feasibility_evidence_ids
        )

        counter = set(
            self.counterevidence_ids
        )

        counter_incentive = set(
            self.counter_incentive_evidence_ids
        )

        alternative = set()

        for item in self.alternative_explanations:
            alternative.update(
                item.evidence_ids
            )

        role_map = {
            "direct_premise_evidence": sorted(
                all_support
                - analogical
                - feasibility
                - counter
                - counter_incentive
                - alternative
            ),
            "analogical_mechanism_evidence": sorted(
                analogical
            ),
            "feasibility_evidence": sorted(
                feasibility
            ),
            "counterevidence": sorted(
                counter
            ),
            "counter_incentive_evidence": sorted(
                counter_incentive
            ),
            "alternative_explanation_evidence": sorted(
                alternative
            ),
        }

        object.__setattr__(
            self,
            "_derived_evidence_roles",
            role_map,
        )

    @property
    def all_referenced_evidence_ids(
        self,
    ) -> Tuple[str, ...]:
        values = []

        values.extend(
            self.supporting_evidence_ids
        )
        values.extend(
            self.counterevidence_ids
        )
        values.extend(
            self.counter_incentive_evidence_ids
        )
        values.extend(
            self.mechanism.analogical_evidence_ids
        )
        values.extend(
            self.feasibility.feasibility_evidence_ids
        )

        for premise in self.premises:
            values.extend(
                premise.evidence_ids
            )

        for alternative in self.alternative_explanations:
            values.extend(
                alternative.evidence_ids
            )

        return _stable_unique_strings(
            values
        )

    @property
    def evidence_roles(
        self,
    ) -> Dict[str, Tuple[str, ...]]:
        role_map = getattr(
            self,
            "_derived_evidence_roles",
            {},
        )

        return {
            key: tuple(value)
            for key, value in role_map.items()
        }

    @property
    def is_prospective(
        self,
    ) -> bool:
        return (
            self.insight_type
            == "prospective"
        )

    @property
    def is_release_safe_by_status_only(
        self,
    ) -> bool:
        """
        Convenience indicator only.

        This does NOT authorize release.
        Real release still requires Grounding/RedTeam/policy gates.
        """
        return bool(
            self.quality_status
            in {
                "strong",
                "usable_with_limits",
            }
            and self.epistemic_status
            in {
                "supported",
                "partially_supported",
                "inference_supported",
            }
            and self.revision_status
            in {
                "active",
                "weakened",
            }
        )

    @property
    def revision_fingerprint(
        self,
    ) -> str:
        """
        Fingerprint immutable semantic revision identity.

        Assessment IDs/statuses/report links are intentionally excluded.
        """
        return _sha256(
            _canonical_json_bytes(
                {
                    "insight_id": self.insight_id,
                    "insight_revision_id": self.insight_revision_id,
                    "insight_type": self.insight_type,
                    "text": self.text,
                    "premises": [
                        item.to_dict()
                        for item in self.premises
                    ],
                    "mechanism": self.mechanism.to_dict(),
                    "feasibility": self.feasibility.to_dict(),
                    "structural_change": self.structural_change,
                    "second_order_effect": self.second_order_effect,
                    "critical_unknowns": list(
                        self.critical_unknowns
                    ),
                    "direct_precedent_found": (
                        self.direct_precedent_found
                    ),
                }
            )
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        *,
        generate_missing_revision_id: bool = True,
    ) -> "InsightRevision":
        if not isinstance(value, Mapping):
            raise InsightModelError(
                "InsightRevision must be an object."
            )

        _validate_metadata(
            value.get("metadata")
            or {}
        )

        insight_id = _clean(
            value.get("insight_id")
            or value.get("id")
        )

        text = _clean(
            value.get("insight")
            or value.get("text")
            or value.get("statement")
        )

        insight_type = _normalize_enum(
            value.get("insight_type")
            or "descriptive"
        )

        revision_id = _clean(
            value.get(
                "insight_revision_id"
            )
        )

        if (
            generate_missing_revision_id
            and not revision_id
            and insight_id
            and text
        ):
            revision_id = stable_id(
                "INSIGHTREV",
                {
                    "insight_id": insight_id,
                    "text": text,
                    "insight_type": insight_type,
                },
            )

        mechanism_value = (
            value.get("mechanism_details")
            if isinstance(
                value.get("mechanism_details"),
                Mapping,
            )
            else value.get("mechanism")
        )

        feasibility_value = (
            value.get("feasibility")
            or {}
        )

        # Preserve top-level constraint_ids for compatibility with earlier
        # StrategicInferenceAgent outputs.
        if isinstance(
            feasibility_value,
            Mapping,
        ):
            feasibility_value = dict(
                feasibility_value
            )

            if (
                "constraint_ids"
                not in feasibility_value
                and value.get(
                    "constraint_ids"
                )
            ):
                feasibility_value[
                    "constraint_ids"
                ] = value.get(
                    "constraint_ids"
                )

        premises = tuple(
            Premise.from_mapping(item)
            for item in (
                value.get("premises")
                or []
            )
        )

        alternatives_raw = (
            value.get("alternative_explanations")
            or []
        )

        alternatives = []

        for item in alternatives_raw:
            if isinstance(item, str):
                alternatives.append(
                    AlternativeExplanation(
                        alternative_id=stable_id(
                            "ALT",
                            {
                                "text": item,
                            },
                        ),
                        text=item,
                    )
                )
            else:
                alternatives.append(
                    AlternativeExplanation.from_mapping(
                        item
                    )
                )

        return cls(
            insight_id=insight_id,
            insight_revision_id=revision_id,
            insight_type=insight_type,
            text=text,
            premises=premises,
            mechanism=Mechanism.from_value(
                mechanism_value
            ),
            feasibility=FeasibilityAssessment.from_mapping(
                feasibility_value
            ),
            supporting_evidence_ids=_stable_unique_strings(
                value.get(
                    "supporting_evidence_ids"
                )
                or value.get(
                    "evidence_ids"
                )
                or []
            ),
            counterevidence_ids=_stable_unique_strings(
                value.get(
                    "counterevidence_ids"
                )
                or []
            ),
            counter_incentive_evidence_ids=_stable_unique_strings(
                value.get(
                    "counter_incentive_evidence_ids"
                )
                or []
            ),
            alternative_explanations=tuple(
                alternatives
            ),
            event_ids=_stable_unique_strings(
                value.get(
                    "event_ids"
                )
                or []
            ),
            constraint_ids=_stable_unique_strings(
                value.get(
                    "constraint_ids"
                )
                or []
            ),
            analytical_lenses=_stable_unique_strings(
                value.get(
                    "analytical_lenses"
                )
                or value.get(
                    "lenses"
                )
                or []
            ),
            reasoning_patterns=_stable_unique_strings(
                value.get(
                    "reasoning_patterns"
                )
                or []
            ),
            structural_change=_clean(
                value.get(
                    "structural_change"
                )
                or value.get(
                    "structural_implication"
                )
            ),
            second_order_effect=_clean(
                value.get(
                    "second_order_effect"
                )
                or value.get(
                    "second_order_implication"
                )
                or value.get(
                    "implication"
                )
            ),
            critical_unknowns=_stable_unique_strings(
                value.get(
                    "critical_unknowns"
                )
                or []
            ),
            direct_precedent_found=(
                None
                if value.get(
                    "direct_precedent_found"
                )
                is None
                else bool(
                    value.get(
                        "direct_precedent_found"
                    )
                )
            ),
            prospective_status=_normalize_enum(
                value.get(
                    "prospective_status"
                )
            ),
            quality_status=_normalize_enum(
                value.get(
                    "quality_status"
                )
                or "unassessed"
            ),
            epistemic_status=_normalize_enum(
                value.get(
                    "epistemic_status"
                )
                or value.get(
                    "grounding_status"
                )
                or "unresolved"
            ),
            revision_status=_normalize_enum(
                value.get(
                    "revision_status"
                )
                or "active"
            ),
            insight_trace_id=_clean(
                value.get(
                    "insight_trace_id"
                )
            ),
            quality_assessment_id=_clean(
                value.get(
                    "quality_assessment_id"
                )
            ),
            grounding_assessment_id=_clean(
                value.get(
                    "grounding_assessment_id"
                )
            ),
            red_team_assessment_id=_clean(
                value.get(
                    "red_team_assessment_id"
                )
            ),
            report_ids=_stable_unique_strings(
                value.get(
                    "report_ids"
                )
                or []
            ),
            metadata=_validate_metadata(
                value.get("metadata")
                or {}
            ),
        )

    def to_dict(
        self,
        *,
        include_derived: bool = True,
    ) -> Dict[str, Any]:
        payload = {
            "insight_id": self.insight_id,
            "insight_revision_id": self.insight_revision_id,
            "insight_type": self.insight_type,
            "insight": self.text,
            "premises": [
                item.to_dict()
                for item in self.premises
            ],
            "mechanism": self.mechanism.to_dict(),
            "feasibility": self.feasibility.to_dict(),
            "supporting_evidence_ids": list(
                self.supporting_evidence_ids
            ),
            "counterevidence_ids": list(
                self.counterevidence_ids
            ),
            "counter_incentive_evidence_ids": list(
                self.counter_incentive_evidence_ids
            ),
            "alternative_explanations": [
                item.to_dict()
                for item in self.alternative_explanations
            ],
            "event_ids": list(
                self.event_ids
            ),
            "constraint_ids": list(
                self.constraint_ids
            ),
            "analytical_lenses": list(
                self.analytical_lenses
            ),
            "reasoning_patterns": list(
                self.reasoning_patterns
            ),
            "structural_change": self.structural_change,
            "second_order_effect": self.second_order_effect,
            "critical_unknowns": list(
                self.critical_unknowns
            ),
            "direct_precedent_found": self.direct_precedent_found,
            "prospective_status": self.prospective_status,
            "quality_status": self.quality_status,
            "epistemic_status": self.epistemic_status,
            "revision_status": self.revision_status,
            "insight_trace_id": self.insight_trace_id,
            "quality_assessment_id": self.quality_assessment_id,
            "grounding_assessment_id": self.grounding_assessment_id,
            "red_team_assessment_id": self.red_team_assessment_id,
            "report_ids": list(
                self.report_ids
            ),
            "metadata": _json_safe(
                self.metadata
            ),
        }

        if include_derived:
            payload[
                "all_referenced_evidence_ids"
            ] = list(
                self.all_referenced_evidence_ids
            )
            payload[
                "evidence_roles"
            ] = {
                key: list(value)
                for key, value in self.evidence_roles.items()
            }
            payload[
                "revision_fingerprint"
            ] = self.revision_fingerprint
            payload[
                "is_release_safe_by_status_only"
            ] = self.is_release_safe_by_status_only
            payload[
                "model_version"
            ] = MODEL_VERSION

        return payload


# ---------------------------------------------------------------------------
# Stable logical insight handle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Insight:
    """
    Stable logical insight handle.

    This is intentionally lightweight. The detailed thesis content lives in
    InsightRevision.
    """

    insight_id: str
    insight_type: str
    lifecycle_status: str
    current_revision_id: str
    revision_ids: Tuple[str, ...]
    research_question: str = ""
    origin_run_id: str = ""
    superseded_by_insight_id: str = ""
    metadata: Mapping[str, Any] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "insight_id",
            _clean(self.insight_id),
        )
        object.__setattr__(
            self,
            "insight_type",
            _normalize_enum(
                self.insight_type
            ),
        )
        object.__setattr__(
            self,
            "lifecycle_status",
            _normalize_enum(
                self.lifecycle_status
            ),
        )
        object.__setattr__(
            self,
            "current_revision_id",
            _clean(
                self.current_revision_id
            ),
        )
        object.__setattr__(
            self,
            "revision_ids",
            _stable_unique_strings(
                self.revision_ids
            ),
        )
        object.__setattr__(
            self,
            "research_question",
            _clean(self.research_question),
        )
        object.__setattr__(
            self,
            "origin_run_id",
            _clean(self.origin_run_id),
        )
        object.__setattr__(
            self,
            "superseded_by_insight_id",
            _clean(
                self.superseded_by_insight_id
            ),
        )
        object.__setattr__(
            self,
            "metadata",
            _validate_metadata(
                self.metadata
            ),
        )

        if not self.insight_id:
            raise InsightModelError(
                "Insight.insight_id cannot be empty."
            )

        if (
            self.insight_type
            not in ALLOWED_INSIGHT_TYPES
        ):
            raise InsightModelError(
                f"Unsupported insight_type={self.insight_type!r}."
            )

        if (
            self.lifecycle_status
            not in ALLOWED_LOGICAL_STATUSES
        ):
            raise InsightModelError(
                "Unsupported logical lifecycle_status="
                f"{self.lifecycle_status!r}."
            )

        if not self.revision_ids:
            raise InsightModelError(
                "Insight must contain at least one revision ID."
            )

        if (
            self.current_revision_id
            not in set(
                self.revision_ids
            )
        ):
            raise InsightModelError(
                "current_revision_id must belong to revision_ids."
            )

        if (
            self.lifecycle_status
            == "superseded"
            and not self.superseded_by_insight_id
        ):
            raise InsightModelError(
                "Superseded insight requires superseded_by_insight_id."
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
    ) -> "Insight":
        if not isinstance(value, Mapping):
            raise InsightModelError(
                "Insight must be an object."
            )

        return cls(
            insight_id=_clean(
                value.get(
                    "insight_id"
                )
            ),
            insight_type=_normalize_enum(
                value.get(
                    "insight_type"
                )
                or "descriptive"
            ),
            lifecycle_status=_normalize_enum(
                value.get(
                    "lifecycle_status"
                )
                or "active"
            ),
            current_revision_id=_clean(
                value.get(
                    "current_revision_id"
                )
            ),
            revision_ids=_stable_unique_strings(
                value.get(
                    "revision_ids"
                )
                or []
            ),
            research_question=_clean(
                value.get(
                    "research_question"
                )
            ),
            origin_run_id=_clean(
                value.get(
                    "origin_run_id"
                )
                or value.get(
                    "run_id"
                )
            ),
            superseded_by_insight_id=_clean(
                value.get(
                    "superseded_by_insight_id"
                )
            ),
            metadata=_validate_metadata(
                value.get(
                    "metadata"
                )
                or {}
            ),
        )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "insight_id": self.insight_id,
            "insight_type": self.insight_type,
            "lifecycle_status": self.lifecycle_status,
            "current_revision_id": self.current_revision_id,
            "revision_ids": list(
                self.revision_ids
            ),
            "research_question": self.research_question,
            "origin_run_id": self.origin_run_id,
            "superseded_by_insight_id": self.superseded_by_insight_id,
            "metadata": _json_safe(
                self.metadata
            ),
            "model_version": MODEL_VERSION,
        }


# ---------------------------------------------------------------------------
# Convenience builders
# ---------------------------------------------------------------------------


def build_insight_revision(
    value: Mapping[str, Any],
    *,
    generate_missing_revision_id: bool = True,
) -> InsightRevision:
    """
    Convenience adapter for agent/service outputs.
    """
    return InsightRevision.from_mapping(
        value,
        generate_missing_revision_id=generate_missing_revision_id,
    )


def validate_insight_payload(
    value: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Parse and return canonical JSON-safe revision payload.

    Useful at service boundaries.
    """
    return build_insight_revision(
        value
    ).to_dict()