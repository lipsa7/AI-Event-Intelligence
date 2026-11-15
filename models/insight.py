"""
Canonical non-ORM insight domain models.

Insight is a first-class research object, but persistence and revision lineage
are owned by InsightRegistryService. This module defines the immutable contract
shared by strategic inference, refinement, grounding, red-team review, ranking,
and report generation.

Critical boundaries
-------------------
Insight != Evidence
Insight != Grounding result
Insight != Release decision
User feedback != Grounding evidence

Evidence-role contract
----------------------
Evidence is attached to explicit roles rather than a generic "supporting
evidence" bucket:

    direct_premise_evidence
    analogical_mechanism_evidence
    feasibility_evidence
    counterevidence
    counter_incentive_evidence
    alternative_explanation_evidence

Evidence roles are many-to-many. One evidence record may legitimately appear
in more than one role.

Prospective insight contract
----------------------------
A prospective insight does not require an exact historical precedent. Its
factual premises must still be grounded, its mechanism may use analogical
evidence, feasibility/magnitude must be considered separately, and material
counter-incentives or alternative explanations must remain visible.
"""

from __future__ import annotations

import hashlib
import json
import re

from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Mapping


MODEL_VERSION = "insight-model-v3.0"


ALLOWED_INSIGHT_TYPES = {
    "descriptive",
    "prospective",
}


ALLOWED_SUPPORT_STATUSES = {
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
    "superseded",
    "retracted",
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
    """Raised when an insight payload violates the canonical contract."""


# ---------------------------------------------------------------------------
# Normalization / deterministic serialization
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    if value is None:
        return ""

    return " ".join(
        str(value).split()
    ).strip()


def _enum(value: Any) -> str:
    text = (
        _clean(value)
        .casefold()
        .replace(
            "-",
            "_",
        )
        .replace(
            " ",
            "_",
        )
    )

    text = re.sub(
        r"[^a-z0-9_]+",
        "",
        text,
    )

    while "__" in text:
        text = text.replace(
            "__",
            "_",
        )

    return text.strip(
        "_"
    )


def _canonical_sort_key(
    value: Any,
) -> str:
    """
    Stable ordering key used whenever an unordered
    container must be serialized.
    """

    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        allow_nan=False,
    )


def _unique_strings(
    values: Any,
) -> tuple[str, ...]:
    """
    Normalize and de-duplicate strings.

    Sequence order is preserved.

    Sets are sorted before processing so deterministic IDs and
    fingerprints never depend on Python set iteration order.
    """

    if values is None:
        return ()

    if isinstance(
        values,
        str,
    ):
        values = [
            values
        ]

    elif isinstance(
        values,
        set,
    ):
        values = sorted(
            values,
            key=lambda item: _clean(item),
        )

    if not isinstance(
        values,
        (
            list,
            tuple,
            set,
        ),
    ):
        raise InsightModelError(
            "Expected a string or sequence of strings."
        )

    output: list[str] = []
    seen: set[str] = set()

    for item in values:
        text = _clean(
            item
        )

        if (
            text
            and text not in seen
        ):
            seen.add(
                text
            )

            output.append(
                text
            )

    return tuple(
        output
    )


def _json_safe(
    value: Any,
) -> Any:
    if (
        value is None
        or isinstance(
            value,
            (
                str,
                int,
                float,
                bool,
            ),
        )
    ):
        return value

    if isinstance(
        value,
        Mapping,
    ):
        return {
            str(key): _json_safe(item)
            for key, item
            in value.items()
        }

    if isinstance(
        value,
        set,
    ):
        return [
            _json_safe(item)
            for item in sorted(
                value,
                key=_canonical_sort_key,
            )
        ]

    if isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        return [
            _json_safe(item)
            for item
            in value
        ]

    if (
        hasattr(
            value,
            "to_dict",
        )
        and callable(
            value.to_dict
        )
    ):
        return _json_safe(
            value.to_dict()
        )

    return str(
        value
    )


def _deep_freeze(
    value: Any,
) -> Any:
    if isinstance(
        value,
        Mapping,
    ):
        return MappingProxyType(
            {
                str(key): _deep_freeze(item)
                for key, item
                in value.items()
            }
        )

    if isinstance(
        value,
        set,
    ):
        return tuple(
            _deep_freeze(item)
            for item in sorted(
                value,
                key=_canonical_sort_key,
            )
        )

    if isinstance(
        value,
        (
            list,
            tuple,
        ),
    ):
        return tuple(
            _deep_freeze(item)
            for item
            in value
        )

    return value


def _sha256_payload(
    value: Any,
) -> str:
    raw = json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(
            ",",
            ":",
        ),
        allow_nan=False,
    ).encode(
        "utf-8"
    )

    return hashlib.sha256(
        raw
    ).hexdigest()


def stable_id(
    prefix: str,
    payload: Any,
    *,
    length: int = 24,
) -> str:
    prefix = _clean(
        prefix
    ).upper()

    if not prefix:
        raise InsightModelError(
            "ID prefix cannot be empty."
        )

    if length <= 0:
        raise InsightModelError(
            "ID length must be positive."
        )

    return (
        f"{prefix}-"
        f"{_sha256_payload(payload)[:length].upper()}"
    )


# ---------------------------------------------------------------------------
# Safety / validation helpers
# ---------------------------------------------------------------------------


def _private_path(
    value: Any,
    path: str = "",
) -> str | None:
    if isinstance(
        value,
        Mapping,
    ):
        for key, item in value.items():
            current = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            if (
                _enum(key)
                in PROHIBITED_PRIVATE_REASONING_FIELDS
            ):
                return current

            nested = _private_path(
                item,
                current,
            )

            if nested:
                return nested

    elif isinstance(
        value,
        (
            list,
            tuple,
            set,
        ),
    ):
        iterable = (
            sorted(
                value,
                key=_canonical_sort_key,
            )
            if isinstance(
                value,
                set,
            )
            else value
        )

        for index, item in enumerate(
            iterable
        ):
            nested = _private_path(
                item,
                f"{path}[{index}]",
            )

            if nested:
                return nested

    return None


def _metadata(
    value: Any,
) -> Mapping[str, Any]:
    if value is None:
        value = {}

    if not isinstance(
        value,
        Mapping,
    ):
        raise InsightModelError(
            "metadata must be an object."
        )

    private = _private_path(
        value
    )

    if private:
        raise InsightModelError(
            "metadata contains prohibited private/raw "
            f"reasoning field at {private!r}."
        )

    return _deep_freeze(
        _json_safe(
            dict(value)
        )
    )


def _optional_bool(
    value: Any,
    *,
    field_name: str,
) -> bool | None:
    if (
        value is None
        or value == ""
    ):
        return None

    if isinstance(
        value,
        bool,
    ):
        return value

    if (
        isinstance(
            value,
            int,
        )
        and value in {
            0,
            1,
        }
    ):
        return bool(
            value
        )

    if isinstance(
        value,
        str,
    ):
        normalized = (
            value
            .strip()
            .casefold()
        )

        if normalized in {
            "true",
            "yes",
            "1",
        }:
            return True

        if normalized in {
            "false",
            "no",
            "0",
        }:
            return False

    raise InsightModelError(
        f"{field_name} must be boolean/null, "
        f"not {value!r}."
    )


def _validate_enum(
    value: str,
    *,
    allowed: set[str],
    field_name: str,
) -> None:
    if value not in allowed:
        raise InsightModelError(
            f"Unsupported {field_name}={value!r}."
        )


def _ensure_unique_ids(
    values: tuple[Any, ...],
    *,
    attribute: str,
    field_name: str,
) -> None:
    ids = [
        getattr(
            item,
            attribute,
        )
        for item
        in values
    ]

    if (
        len(ids)
        != len(
            set(ids)
        )
    ):
        raise InsightModelError(
            f"{field_name} contains duplicate "
            f"{attribute} values."
        )


# ---------------------------------------------------------------------------
# Premise
# ---------------------------------------------------------------------------


@dataclass(
    frozen=True
)
class Premise:
    """
    Atomic factual premise that an insight depends on.

    Premise evidence is the canonical location for
    direct_premise_evidence.
    """

    premise_id: str
    text: str

    evidence_ids: tuple[
        str,
        ...
    ] = ()

    status: str = (
        "unresolved"
    )

    notes: str = ""

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "premise_id",
            _clean(
                self.premise_id
            ),
        )

        object.__setattr__(
            self,
            "text",
            _clean(
                self.text
            ),
        )

        object.__setattr__(
            self,
            "evidence_ids",
            _unique_strings(
                self.evidence_ids
            ),
        )

        object.__setattr__(
            self,
            "status",
            _enum(
                self.status
                or "unresolved"
            ),
        )

        object.__setattr__(
            self,
            "notes",
            _clean(
                self.notes
            ),
        )

        if not self.premise_id:
            raise InsightModelError(
                "Premise.premise_id cannot be empty."
            )

        if not self.text:
            raise InsightModelError(
                "Premise.text cannot be empty."
            )

        _validate_enum(
            self.status,
            allowed=(
                ALLOWED_SUPPORT_STATUSES
            ),
            field_name=(
                "premise status"
            ),
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[
            str,
            Any,
        ],
    ) -> "Premise":
        if not isinstance(
            value,
            Mapping,
        ):
            raise InsightModelError(
                "Premise must be an object."
            )

        text = _clean(
            value.get(
                "text"
            )
            or value.get(
                "premise"
            )
            or value.get(
                "statement"
            )
        )

        premise_id = _clean(
            value.get(
                "premise_id"
            )
            or value.get(
                "id"
            )
        )

        if (
            not premise_id
            and text
        ):
            premise_id = stable_id(
                "PREMISE",
                {
                    "text": text
                },
            )

        return cls(
            premise_id=(
                premise_id
            ),
            text=text,
            evidence_ids=(
                _unique_strings(
                    value.get(
                        "evidence_ids"
                    )
                    or []
                )
            ),
            status=_enum(
                value.get(
                    "status"
                )
                or "unresolved"
            ),
            notes=_clean(
                value.get(
                    "notes"
                )
            ),
        )

    def semantic_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "premise_id": (
                self.premise_id
            ),
            "text": self.text,
        }

    def revision_content_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            **self.semantic_dict(),
            "evidence_ids": list(
                self.evidence_ids
            ),
        }

    def to_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            **self.revision_content_dict(),
            "status": self.status,
            "notes": self.notes,
        }


# ---------------------------------------------------------------------------
# Mechanism
# ---------------------------------------------------------------------------


@dataclass(
    frozen=True
)
class Mechanism:
    """
    Mechanism explaining why the premises could
    produce the insight.

    Analogical evidence supports the general mechanism.
    It does not prove that the predicted action/outcome
    will occur.
    """

    description: str = ""

    analogical_evidence_ids: tuple[
        str,
        ...
    ] = ()

    status: str = (
        "unresolved"
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "description",
            _clean(
                self.description
            ),
        )

        object.__setattr__(
            self,
            "analogical_evidence_ids",
            _unique_strings(
                self.analogical_evidence_ids
            ),
        )

        object.__setattr__(
            self,
            "status",
            _enum(
                self.status
                or "unresolved"
            ),
        )

        _validate_enum(
            self.status,
            allowed=(
                ALLOWED_SUPPORT_STATUSES
            ),
            field_name=(
                "mechanism status"
            ),
        )

    @classmethod
    def from_value(
        cls,
        value: Any,
    ) -> "Mechanism":
        if value is None:
            return cls()

        if isinstance(
            value,
            str,
        ):
            return cls(
                description=value
            )

        if not isinstance(
            value,
            Mapping,
        ):
            raise InsightModelError(
                "mechanism must be text or an object."
            )

        return cls(
            description=_clean(
                value.get(
                    "description"
                )
                or value.get(
                    "mechanism"
                )
            ),
            analogical_evidence_ids=(
                _unique_strings(
                    value.get(
                        "analogical_evidence_ids"
                    )
                    or []
                )
            ),
            status=_enum(
                value.get(
                    "status"
                )
                or "unresolved"
            ),
        )

    def semantic_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "description": (
                self.description
            )
        }

    def revision_content_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            **self.semantic_dict(),
            "analogical_evidence_ids": list(
                self.analogical_evidence_ids
            ),
        }

    def to_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            **self.revision_content_dict(),
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Feasibility / magnitude
# ---------------------------------------------------------------------------


@dataclass(
    frozen=True
)
class FeasibilityAssessment:
    """
    Feasibility and magnitude context for a mechanism.

    `magnitude_check` is a concise research conclusion,
    not an arbitrary numeric confidence score.

    `feasibility_evidence_ids` may support:
    - technical feasibility;
    - economic feasibility;
    - legal/regulatory feasibility;
    - infrastructure constraints;
    - actor capability;
    - timing;
    - magnitude.
    """

    constraint_ids: tuple[
        str,
        ...
    ] = ()

    feasibility_evidence_ids: tuple[
        str,
        ...
    ] = ()

    magnitude_check: str = ""

    blocking_factors: tuple[
        str,
        ...
    ] = ()

    status: str = (
        "unresolved"
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "constraint_ids",
            _unique_strings(
                self.constraint_ids
            ),
        )

        object.__setattr__(
            self,
            "feasibility_evidence_ids",
            _unique_strings(
                self.feasibility_evidence_ids
            ),
        )

        object.__setattr__(
            self,
            "magnitude_check",
            _clean(
                self.magnitude_check
            ),
        )

        object.__setattr__(
            self,
            "blocking_factors",
            _unique_strings(
                self.blocking_factors
            ),
        )

        object.__setattr__(
            self,
            "status",
            _enum(
                self.status
                or "unresolved"
            ),
        )

        _validate_enum(
            self.status,
            allowed=(
                ALLOWED_SUPPORT_STATUSES
            ),
            field_name=(
                "feasibility status"
            ),
        )

    @classmethod
    def from_mapping(
        cls,
        value: (
            Mapping[
                str,
                Any,
            ]
            | None
        ),
    ) -> "FeasibilityAssessment":
        if value is None:
            return cls()

        if not isinstance(
            value,
            Mapping,
        ):
            raise InsightModelError(
                "feasibility must be an object."
            )

        return cls(
            constraint_ids=(
                _unique_strings(
                    value.get(
                        "constraint_ids"
                    )
                    or []
                )
            ),
            feasibility_evidence_ids=(
                _unique_strings(
                    value.get(
                        "feasibility_evidence_ids"
                    )
                    or []
                )
            ),
            magnitude_check=_clean(
                value.get(
                    "magnitude_check"
                )
            ),
            blocking_factors=(
                _unique_strings(
                    value.get(
                        "blocking_factors"
                    )
                    or []
                )
            ),
            status=_enum(
                value.get(
                    "status"
                )
                or "unresolved"
            ),
        )

    def semantic_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "constraint_ids": list(
                self.constraint_ids
            ),
            "magnitude_check": (
                self.magnitude_check
            ),
            "blocking_factors": list(
                self.blocking_factors
            ),
        }

    def revision_content_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            **self.semantic_dict(),
            "feasibility_evidence_ids": list(
                self.feasibility_evidence_ids
            ),
        }

    def to_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            **self.revision_content_dict(),
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Alternative explanations
# ---------------------------------------------------------------------------


@dataclass(
    frozen=True
)
class AlternativeExplanation:
    """
    Competing explanation that could weaken the
    main interpretation.
    """

    alternative_id: str
    text: str

    evidence_ids: tuple[
        str,
        ...
    ] = ()

    status: str = (
        "unresolved"
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "alternative_id",
            _clean(
                self.alternative_id
            ),
        )

        object.__setattr__(
            self,
            "text",
            _clean(
                self.text
            ),
        )

        object.__setattr__(
            self,
            "evidence_ids",
            _unique_strings(
                self.evidence_ids
            ),
        )

        object.__setattr__(
            self,
            "status",
            _enum(
                self.status
                or "unresolved"
            ),
        )

        if not self.alternative_id:
            raise InsightModelError(
                "AlternativeExplanation.alternative_id "
                "cannot be empty."
            )

        if not self.text:
            raise InsightModelError(
                "AlternativeExplanation.text "
                "cannot be empty."
            )

        _validate_enum(
            self.status,
            allowed=(
                ALLOWED_SUPPORT_STATUSES
            ),
            field_name=(
                "alternative explanation status"
            ),
        )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[
            str,
            Any,
        ],
    ) -> "AlternativeExplanation":
        if not isinstance(
            value,
            Mapping,
        ):
            raise InsightModelError(
                "Alternative explanation "
                "must be an object."
            )

        text = _clean(
            value.get(
                "text"
            )
            or value.get(
                "explanation"
            )
        )

        alternative_id = _clean(
            value.get(
                "alternative_id"
            )
            or value.get(
                "id"
            )
        )

        if (
            not alternative_id
            and text
        ):
            alternative_id = stable_id(
                "ALT",
                {
                    "text": text
                },
            )

        return cls(
            alternative_id=(
                alternative_id
            ),
            text=text,
            evidence_ids=(
                _unique_strings(
                    value.get(
                        "evidence_ids"
                    )
                    or []
                )
            ),
            status=_enum(
                value.get(
                    "status"
                )
                or "unresolved"
            ),
        )

    def semantic_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "alternative_id": (
                self.alternative_id
            ),
            "text": self.text,
        }

    def revision_content_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            **self.semantic_dict(),
            "evidence_ids": list(
                self.evidence_ids
            ),
        }

    def to_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            **self.revision_content_dict(),
            "status": self.status,
        }


# ---------------------------------------------------------------------------
# Insight revision
# ---------------------------------------------------------------------------


@dataclass(
    frozen=True
)
class InsightRevision:
    """
    Immutable semantic/evidence revision of one logical INSIGHT-*.

    StrategicInferenceAgent may create a candidate revision.

    InsightQualityService, GroundingService and RedTeamAgent may assess the
    same revision without changing its semantic fingerprint.

    If the thesis, premises, mechanism, feasibility, alternatives, event
    linkage, or attached evidence materially change, a new revision should
    be created.

    Assessment status is deliberately separate from revision identity.
    """

    insight_id: str
    insight_revision_id: str
    insight_type: str
    text: str

    premises: tuple[
        Premise,
        ...
    ] = ()

    mechanism: Mechanism = field(
        default_factory=Mechanism
    )

    feasibility: FeasibilityAssessment = field(
        default_factory=FeasibilityAssessment
    )

    # Evidence weakening the interpretation itself.
    counterevidence_ids: tuple[
        str,
        ...
    ] = ()

    # Human-readable reasons actors might not behave
    # as the proposed mechanism predicts.
    counter_incentives: tuple[
        str,
        ...
    ] = ()

    # Evidence supporting those counter-incentives.
    counter_incentive_evidence_ids: tuple[
        str,
        ...
    ] = ()

    alternative_explanations: tuple[
        AlternativeExplanation,
        ...
    ] = ()

    # Events synthesized into this insight.
    event_ids: tuple[
        str,
        ...
    ] = ()

    # Useful for ranking/preference learning.
    analytical_lenses: tuple[
        str,
        ...
    ] = ()

    reasoning_patterns: tuple[
        str,
        ...
    ] = ()

    structural_change: str = ""

    second_order_effect: str = ""

    critical_unknowns: tuple[
        str,
        ...
    ] = ()

    # Relevant only for prospective insights.
    direct_precedent_found: bool | None = None

    prospective_status: str = ""

    # Downstream assessment state.
    quality_status: str = (
        "unassessed"
    )

    epistemic_status: str = (
        "unresolved"
    )

    revision_status: str = (
        "active"
    )

    # Audit/assessment references.
    insight_trace_id: str = ""

    quality_assessment_id: str = ""

    grounding_assessment_id: str = ""

    red_team_assessment_id: str = ""

    metadata: Mapping[
        str,
        Any,
    ] = field(
        default_factory=dict
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "insight_id",
            _clean(
                self.insight_id
            ),
        )

        object.__setattr__(
            self,
            "insight_revision_id",
            _clean(
                self.insight_revision_id
            ),
        )

        object.__setattr__(
            self,
            "insight_type",
            _enum(
                self.insight_type
            ),
        )

        object.__setattr__(
            self,
            "text",
            _clean(
                self.text
            ),
        )

        if not self.insight_id:
            raise InsightModelError(
                "insight_id cannot be empty."
            )

        if not self.insight_revision_id:
            raise InsightModelError(
                "insight_revision_id cannot be empty."
            )

        if not self.text:
            raise InsightModelError(
                "Insight text cannot be empty."
            )

        _validate_enum(
            self.insight_type,
            allowed=(
                ALLOWED_INSIGHT_TYPES
            ),
            field_name=(
                "insight_type"
            ),
        )

        premises = tuple(
            item
            if isinstance(
                item,
                Premise,
            )
            else Premise.from_mapping(
                item
            )
            for item
            in self.premises
        )

        alternatives = tuple(
            item
            if isinstance(
                item,
                AlternativeExplanation,
            )
            else AlternativeExplanation.from_mapping(
                item
            )
            for item
            in self.alternative_explanations
        )

        object.__setattr__(
            self,
            "premises",
            premises,
        )

        object.__setattr__(
            self,
            "alternative_explanations",
            alternatives,
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

        _ensure_unique_ids(
            premises,
            attribute=(
                "premise_id"
            ),
            field_name=(
                "premises"
            ),
        )

        _ensure_unique_ids(
            alternatives,
            attribute=(
                "alternative_id"
            ),
            field_name=(
                "alternative_explanations"
            ),
        )

        for name in (
            "counterevidence_ids",
            "counter_incentives",
            "counter_incentive_evidence_ids",
            "event_ids",
            "analytical_lenses",
            "reasoning_patterns",
            "critical_unknowns",
        ):
            object.__setattr__(
                self,
                name,
                _unique_strings(
                    getattr(
                        self,
                        name,
                    )
                ),
            )

        for name in (
            "structural_change",
            "second_order_effect",
            "insight_trace_id",
            "quality_assessment_id",
            "grounding_assessment_id",
            "red_team_assessment_id",
        ):
            object.__setattr__(
                self,
                name,
                _clean(
                    getattr(
                        self,
                        name,
                    )
                ),
            )

        object.__setattr__(
            self,
            "direct_precedent_found",
            _optional_bool(
                self.direct_precedent_found,
                field_name=(
                    "direct_precedent_found"
                ),
            ),
        )

        prospective_status = _enum(
            self.prospective_status
        )

        if (
            self.insight_type
            == "prospective"
            and not prospective_status
        ):
            prospective_status = (
                "unresolved"
            )

        object.__setattr__(
            self,
            "prospective_status",
            prospective_status,
        )

        object.__setattr__(
            self,
            "quality_status",
            _enum(
                self.quality_status
                or "unassessed"
            ),
        )

        object.__setattr__(
            self,
            "epistemic_status",
            _enum(
                self.epistemic_status
                or "unresolved"
            ),
        )

        object.__setattr__(
            self,
            "revision_status",
            _enum(
                self.revision_status
                or "active"
            ),
        )

        object.__setattr__(
            self,
            "metadata",
            _metadata(
                self.metadata
            ),
        )

        _validate_enum(
            self.prospective_status,
            allowed=(
                ALLOWED_PROSPECTIVE_STATUSES
            ),
            field_name=(
                "prospective_status"
            ),
        )

        _validate_enum(
            self.quality_status,
            allowed=(
                ALLOWED_QUALITY_STATUSES
            ),
            field_name=(
                "quality_status"
            ),
        )

        _validate_enum(
            self.epistemic_status,
            allowed=(
                ALLOWED_EPISTEMIC_STATUSES
            ),
            field_name=(
                "epistemic_status"
            ),
        )

        _validate_enum(
            self.revision_status,
            allowed=(
                ALLOWED_REVISION_STATUSES
            ),
            field_name=(
                "revision_status"
            ),
        )

        # Prospective-specific fields must not leak
        # into descriptive insight state.
        if (
            self.insight_type
            == "descriptive"
        ):
            if self.prospective_status:
                raise InsightModelError(
                    "Descriptive insight cannot "
                    "carry prospective_status."
                )

            if (
                self.direct_precedent_found
                is not None
            ):
                raise InsightModelError(
                    "Descriptive insight cannot carry "
                    "direct_precedent_found."
                )

    # ------------------------------------------------------------------
    # Evidence-role derivation
    # ------------------------------------------------------------------

    @property
    def direct_premise_evidence_ids(
        self,
    ) -> tuple[
        str,
        ...
    ]:
        """
        Direct premise evidence is derived exclusively
        from explicit premise → evidence mappings.

        There is deliberately no generic
        supporting_evidence_ids bucket.
        """

        values: list[str] = []

        for premise in self.premises:
            values.extend(
                premise.evidence_ids
            )

        return _unique_strings(
            values
        )

    @property
    def evidence_roles(
        self,
    ) -> dict[
        str,
        tuple[
            str,
            ...
        ],
    ]:
        """
        Explicit many-to-many evidence-role projection.
        """

        alternative: list[str] = []

        for item in (
            self.alternative_explanations
        ):
            alternative.extend(
                item.evidence_ids
            )

        return {
            "direct_premise_evidence": (
                self.direct_premise_evidence_ids
            ),
            "analogical_mechanism_evidence": (
                self.mechanism
                .analogical_evidence_ids
            ),
            "feasibility_evidence": (
                self.feasibility
                .feasibility_evidence_ids
            ),
            "counterevidence": (
                self.counterevidence_ids
            ),
            "counter_incentive_evidence": (
                self.counter_incentive_evidence_ids
            ),
            "alternative_explanation_evidence": (
                _unique_strings(
                    alternative
                )
            ),
        }

    @property
    def all_referenced_evidence_ids(
        self,
    ) -> tuple[
        str,
        ...
    ]:
        values: list[str] = []

        for evidence_ids in (
            self.evidence_roles.values()
        ):
            values.extend(
                evidence_ids
            )

        return _unique_strings(
            values
        )

    @property
    def is_prospective(
        self,
    ) -> bool:
        return (
            self.insight_type
            == "prospective"
        )

    # ------------------------------------------------------------------
    # Fingerprints
    # ------------------------------------------------------------------

    @property
    def semantic_payload(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Thesis-level content only.

        Evidence IDs, assessment states, revision IDs and lifecycle state
        are intentionally excluded.

        Equal semantic fingerprints therefore mean equivalent thesis
        structure, not equivalent evidence state.
        """

        return {
            "insight_id": (
                self.insight_id
            ),
            "insight_type": (
                self.insight_type
            ),
            "text": self.text,
            "premises": [
                item.semantic_dict()
                for item
                in self.premises
            ],
            "mechanism": (
                self.mechanism
                .semantic_dict()
            ),
            "feasibility": (
                self.feasibility
                .semantic_dict()
            ),
            "counter_incentives": list(
                self.counter_incentives
            ),
            "alternative_explanations": [
                item.semantic_dict()
                for item
                in self.alternative_explanations
            ],
            "analytical_lenses": list(
                self.analytical_lenses
            ),
            "reasoning_patterns": list(
                self.reasoning_patterns
            ),
            "structural_change": (
                self.structural_change
            ),
            "second_order_effect": (
                self.second_order_effect
            ),
            "critical_unknowns": list(
                self.critical_unknowns
            ),
        }

    @property
    def revision_content_payload(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        """
        Immutable research-content payload used for revision identity.

        Includes evidence and event linkage.

        Excludes downstream:
        - quality status;
        - grounding status;
        - red-team state;
        - assessment IDs;
        - revision lifecycle status.

        Re-grounding the same research content therefore does not
        manufacture a new semantic revision.
        """

        return {
            **self.semantic_payload,
            "premises": [
                item.revision_content_dict()
                for item
                in self.premises
            ],
            "mechanism": (
                self.mechanism
                .revision_content_dict()
            ),
            "feasibility": (
                self.feasibility
                .revision_content_dict()
            ),
            "counterevidence_ids": list(
                self.counterevidence_ids
            ),
            "counter_incentive_evidence_ids": list(
                self.counter_incentive_evidence_ids
            ),
            "alternative_explanations": [
                item.revision_content_dict()
                for item
                in self.alternative_explanations
            ],
            "event_ids": list(
                self.event_ids
            ),
            "direct_precedent_found": (
                self.direct_precedent_found
            ),
        }

    @property
    def semantic_fingerprint(
        self,
    ) -> str:
        return _sha256_payload(
            self.semantic_payload
        )

    @property
    def revision_fingerprint(
        self,
    ) -> str:
        return _sha256_payload(
            self.revision_content_payload
        )

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[
            str,
            Any,
        ],
        *,
        generate_missing_revision_id: bool = True,
    ) -> "InsightRevision":
        if not isinstance(
            value,
            Mapping,
        ):
            raise InsightModelError(
                "InsightRevision must be an object."
            )

        private = _private_path(
            value
        )

        if private:
            raise InsightModelError(
                "InsightRevision contains prohibited "
                "private/raw reasoning at "
                f"{private!r}."
            )

        insight_id = _clean(
            value.get(
                "insight_id"
            )
            or value.get(
                "id"
            )
        )

        insight_type = _enum(
            value.get(
                "insight_type"
            )
        )

        text = _clean(
            value.get(
                "insight"
            )
            or value.get(
                "text"
            )
            or value.get(
                "statement"
            )
        )

        if not insight_type:
            raise InsightModelError(
                "insight_type must be explicit."
            )

        if not insight_id:
            raise InsightModelError(
                "insight_id must be explicit."
            )

        if not text:
            raise InsightModelError(
                "Insight text cannot be empty."
            )

        premises = tuple(
            Premise.from_mapping(
                item
            )
            for item
            in (
                value.get(
                    "premises"
                )
                or []
            )
        )

        mechanism = (
            Mechanism.from_value(
                value.get(
                    "mechanism"
                )
            )
        )

        feasibility = (
            FeasibilityAssessment.from_mapping(
                value.get(
                    "feasibility"
                )
                or {}
            )
        )

        alternatives: list[
            AlternativeExplanation
        ] = []

        for item in (
            value.get(
                "alternative_explanations"
            )
            or []
        ):
            if isinstance(
                item,
                str,
            ):
                alternatives.append(
                    AlternativeExplanation(
                        alternative_id=stable_id(
                            "ALT",
                            {
                                "text": item
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

        counterevidence_ids = (
            _unique_strings(
                value.get(
                    "counterevidence_ids"
                )
                or []
            )
        )

        counter_incentives = (
            _unique_strings(
                value.get(
                    "counter_incentives"
                )
                or []
            )
        )

        counter_incentive_evidence_ids = (
            _unique_strings(
                value.get(
                    "counter_incentive_evidence_ids"
                )
                or []
            )
        )

        event_ids = _unique_strings(
            value.get(
                "event_ids"
            )
            or []
        )

        analytical_lenses = (
            _unique_strings(
                value.get(
                    "analytical_lenses"
                )
                or []
            )
        )

        reasoning_patterns = (
            _unique_strings(
                value.get(
                    "reasoning_patterns"
                )
                or []
            )
        )

        structural_change = _clean(
            value.get(
                "structural_change"
            )
        )

        second_order_effect = _clean(
            value.get(
                "second_order_effect"
            )
        )

        critical_unknowns = (
            _unique_strings(
                value.get(
                    "critical_unknowns"
                )
                or []
            )
        )

        direct_precedent_found = (
            _optional_bool(
                value.get(
                    "direct_precedent_found"
                ),
                field_name=(
                    "direct_precedent_found"
                ),
            )
        )

        revision_id = _clean(
            value.get(
                "insight_revision_id"
            )
        )

        # Revision identity is based on the complete
        # semantic + evidence research content, not
        # merely the insight sentence.
        if (
            generate_missing_revision_id
            and not revision_id
        ):
            revision_content = {
                "insight_id": (
                    insight_id
                ),
                "insight_type": (
                    insight_type
                ),
                "text": text,
                "premises": [
                    item.revision_content_dict()
                    for item
                    in premises
                ],
                "mechanism": (
                    mechanism
                    .revision_content_dict()
                ),
                "feasibility": (
                    feasibility
                    .revision_content_dict()
                ),
                "counterevidence_ids": list(
                    counterevidence_ids
                ),
                "counter_incentives": list(
                    counter_incentives
                ),
                "counter_incentive_evidence_ids": list(
                    counter_incentive_evidence_ids
                ),
                "alternative_explanations": [
                    item.revision_content_dict()
                    for item
                    in alternatives
                ],
                "event_ids": list(
                    event_ids
                ),
                "analytical_lenses": list(
                    analytical_lenses
                ),
                "reasoning_patterns": list(
                    reasoning_patterns
                ),
                "structural_change": (
                    structural_change
                ),
                "second_order_effect": (
                    second_order_effect
                ),
                "critical_unknowns": list(
                    critical_unknowns
                ),
                "direct_precedent_found": (
                    direct_precedent_found
                ),
            }

            revision_id = stable_id(
                "INSIGHTREV",
                revision_content,
            )

        return cls(
            insight_id=(
                insight_id
            ),
            insight_revision_id=(
                revision_id
            ),
            insight_type=(
                insight_type
            ),
            text=text,
            premises=premises,
            mechanism=mechanism,
            feasibility=feasibility,
            counterevidence_ids=(
                counterevidence_ids
            ),
            counter_incentives=(
                counter_incentives
            ),
            counter_incentive_evidence_ids=(
                counter_incentive_evidence_ids
            ),
            alternative_explanations=tuple(
                alternatives
            ),
            event_ids=(
                event_ids
            ),
            analytical_lenses=(
                analytical_lenses
            ),
            reasoning_patterns=(
                reasoning_patterns
            ),
            structural_change=(
                structural_change
            ),
            second_order_effect=(
                second_order_effect
            ),
            critical_unknowns=(
                critical_unknowns
            ),
            direct_precedent_found=(
                direct_precedent_found
            ),
            prospective_status=_enum(
                value.get(
                    "prospective_status"
                )
            ),
            quality_status=_enum(
                value.get(
                    "quality_status"
                )
                or "unassessed"
            ),
            epistemic_status=_enum(
                value.get(
                    "epistemic_status"
                )
                or "unresolved"
            ),
            revision_status=_enum(
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
            metadata=_metadata(
                value.get(
                    "metadata"
                )
                or {}
            ),
        )

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(
        self,
        *,
        include_derived: bool = True,
    ) -> dict[
        str,
        Any,
    ]:
        payload: dict[
            str,
            Any,
        ] = {
            "insight_id": (
                self.insight_id
            ),
            "insight_revision_id": (
                self.insight_revision_id
            ),
            "insight_type": (
                self.insight_type
            ),
            "insight": (
                self.text
            ),
            "premises": [
                item.to_dict()
                for item
                in self.premises
            ],
            "mechanism": (
                self.mechanism
                .to_dict()
            ),
            "feasibility": (
                self.feasibility
                .to_dict()
            ),
            "counterevidence_ids": list(
                self.counterevidence_ids
            ),
            "counter_incentives": list(
                self.counter_incentives
            ),
            "counter_incentive_evidence_ids": list(
                self.counter_incentive_evidence_ids
            ),
            "alternative_explanations": [
                item.to_dict()
                for item
                in self.alternative_explanations
            ],
            "event_ids": list(
                self.event_ids
            ),
            "analytical_lenses": list(
                self.analytical_lenses
            ),
            "reasoning_patterns": list(
                self.reasoning_patterns
            ),
            "structural_change": (
                self.structural_change
            ),
            "second_order_effect": (
                self.second_order_effect
            ),
            "critical_unknowns": list(
                self.critical_unknowns
            ),
            "direct_precedent_found": (
                self.direct_precedent_found
            ),
            "prospective_status": (
                self.prospective_status
            ),
            "quality_status": (
                self.quality_status
            ),
            "epistemic_status": (
                self.epistemic_status
            ),
            "revision_status": (
                self.revision_status
            ),
            "insight_trace_id": (
                self.insight_trace_id
            ),
            "quality_assessment_id": (
                self.quality_assessment_id
            ),
            "grounding_assessment_id": (
                self.grounding_assessment_id
            ),
            "red_team_assessment_id": (
                self.red_team_assessment_id
            ),
            "metadata": (
                _json_safe(
                    self.metadata
                )
            ),
        }

        if include_derived:
            payload[
                "direct_premise_evidence_ids"
            ] = list(
                self.direct_premise_evidence_ids
            )

            payload[
                "evidence_roles"
            ] = {
                key: list(
                    ids
                )
                for key, ids
                in self.evidence_roles.items()
            }

            payload[
                "all_referenced_evidence_ids"
            ] = list(
                self.all_referenced_evidence_ids
            )

            payload[
                "semantic_fingerprint"
            ] = (
                self.semantic_fingerprint
            )

            payload[
                "revision_fingerprint"
            ] = (
                self.revision_fingerprint
            )

            payload[
                "model_version"
            ] = MODEL_VERSION

        return payload


# ---------------------------------------------------------------------------
# Logical insight handle
# ---------------------------------------------------------------------------


@dataclass(
    frozen=True
)
class Insight:
    """
    Stable logical INSIGHT-* handle.

    Semantic/evidence content lives in immutable
    InsightRevision objects.

    Lifecycle persistence is owned by
    InsightRegistryService.
    """

    insight_id: str
    insight_type: str
    lifecycle_status: str
    current_revision_id: str

    revision_ids: tuple[
        str,
        ...
    ]

    research_question: str = ""
    origin_run_id: str = ""
    superseded_by_insight_id: str = ""

    metadata: Mapping[
        str,
        Any,
    ] = field(
        default_factory=dict
    )

    def __post_init__(
        self,
    ) -> None:
        object.__setattr__(
            self,
            "insight_id",
            _clean(
                self.insight_id
            ),
        )

        object.__setattr__(
            self,
            "insight_type",
            _enum(
                self.insight_type
            ),
        )

        object.__setattr__(
            self,
            "lifecycle_status",
            _enum(
                self.lifecycle_status
                or "active"
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
            _unique_strings(
                self.revision_ids
            ),
        )

        object.__setattr__(
            self,
            "research_question",
            _clean(
                self.research_question
            ),
        )

        object.__setattr__(
            self,
            "origin_run_id",
            _clean(
                self.origin_run_id
            ),
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
            _metadata(
                self.metadata
            ),
        )

        if not self.insight_id:
            raise InsightModelError(
                "Insight.insight_id cannot be empty."
            )

        _validate_enum(
            self.insight_type,
            allowed=(
                ALLOWED_INSIGHT_TYPES
            ),
            field_name=(
                "insight_type"
            ),
        )

        _validate_enum(
            self.lifecycle_status,
            allowed=(
                ALLOWED_LOGICAL_STATUSES
            ),
            field_name=(
                "lifecycle_status"
            ),
        )

        if not self.revision_ids:
            raise InsightModelError(
                "Insight must contain at least "
                "one revision ID."
            )

        if (
            self.current_revision_id
            not in set(
                self.revision_ids
            )
        ):
            raise InsightModelError(
                "current_revision_id must belong "
                "to revision_ids."
            )

        if (
            self.lifecycle_status
            == "superseded"
            and not self.superseded_by_insight_id
        ):
            raise InsightModelError(
                "Superseded insight requires "
                "superseded_by_insight_id."
            )

        if (
            self.lifecycle_status
            != "superseded"
            and self.superseded_by_insight_id
        ):
            raise InsightModelError(
                "superseded_by_insight_id is only valid "
                "when lifecycle_status is 'superseded'."
            )

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[
            str,
            Any,
        ],
    ) -> "Insight":
        if not isinstance(
            value,
            Mapping,
        ):
            raise InsightModelError(
                "Insight must be an object."
            )

        private = _private_path(
            value
        )

        if private:
            raise InsightModelError(
                "Insight contains prohibited private/raw "
                f"reasoning at {private!r}."
            )

        insight_type = _enum(
            value.get(
                "insight_type"
            )
        )

        if not insight_type:
            raise InsightModelError(
                "insight_type must be explicit."
            )

        return cls(
            insight_id=_clean(
                value.get(
                    "insight_id"
                )
            ),
            insight_type=(
                insight_type
            ),
            lifecycle_status=_enum(
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
            revision_ids=_unique_strings(
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
            metadata=_metadata(
                value.get(
                    "metadata"
                )
                or {}
            ),
        )

    def to_dict(
        self,
    ) -> dict[
        str,
        Any,
    ]:
        return {
            "insight_id": (
                self.insight_id
            ),
            "insight_type": (
                self.insight_type
            ),
            "lifecycle_status": (
                self.lifecycle_status
            ),
            "current_revision_id": (
                self.current_revision_id
            ),
            "revision_ids": list(
                self.revision_ids
            ),
            "research_question": (
                self.research_question
            ),
            "origin_run_id": (
                self.origin_run_id
            ),
            "superseded_by_insight_id": (
                self.superseded_by_insight_id
            ),
            "metadata": (
                _json_safe(
                    self.metadata
                )
            ),
            "model_version": (
                MODEL_VERSION
            ),
        }


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def build_insight_revision(
    value: Mapping[
        str,
        Any,
    ],
    *,
    generate_missing_revision_id: bool = True,
) -> InsightRevision:
    return InsightRevision.from_mapping(
        value,
        generate_missing_revision_id=(
            generate_missing_revision_id
        ),
    )


def validate_insight_payload(
    value: Mapping[
        str,
        Any,
    ],
) -> dict[
    str,
    Any,
]:
    return build_insight_revision(
        value
    ).to_dict()


__all__ = [
    "MODEL_VERSION",
    "InsightModelError",
    "Premise",
    "Mechanism",
    "FeasibilityAssessment",
    "AlternativeExplanation",
    "InsightRevision",
    "Insight",
    "stable_id",
    "build_insight_revision",
    "validate_insight_payload",
]