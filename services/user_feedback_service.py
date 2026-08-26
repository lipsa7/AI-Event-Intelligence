"""
Structured user-feedback interpretation for strategic research insights.

Recommended location:
    services/user_feedback_service.py

Purpose
-------
Released/draft research report
        ↓
user feedback
    "too surface-level"
    "more implications"
    "this is too speculative"
    "more like INSIGHT-3"
    "I liked this because it connects regulation to incentives"
        ↓
UserFeedbackService
        ↓
structured feedback event
        ↓
UserPreferenceService
        + PreferenceDataService
        + BlueprintCompiler
        ↓
future/refinement execution policy

This service converts user feedback into a constrained, auditable QUALITY
signal. It does not directly rewrite policy, alter grounding standards, or
change evidence.

Critical epistemic boundary
---------------------------
USER FEEDBACK != FACTUAL EVIDENCE

Positive/negative insight examples are quality references only. They must never
be admitted into FACT / METRIC / TRIPLE evidence stores or cited as factual
support.

Control-plane boundary
----------------------
This service does NOT authorize:
- model routing;
- search budgets;
- quality thresholds;
- release gates;
- grounding exceptions;
- policy changes;
- safety changes.

It only emits structured preference/refinement suggestions. PolicyRegistry and
BlueprintCompiler remain authoritative.

Fast / Deep
-----------
There are no separate fast/deep branches here.

A user may say "deeper" or "too surface-level". This service converts that into
a preference signal such as desired_depth_delta="increase". The final amount of
additional work remains bounded by the compiled research profile and global
ExecutionBudgetService.

Optional LLM interpretation
---------------------------
An optional narrow interpreter can improve understanding of free-form feedback.
Its output is strictly validated:
- it cannot invent insight IDs;
- it cannot emit evidence IDs;
- it cannot emit policy/control-plane fields;
- it cannot authorize execution;
- explicit UI/user selections override model interpretation.

The deterministic parser still handles common feedback patterns without any LLM,
keeping the project fully local/free.

Output
------
A typical structured event:

{
  "feedback_id": "FEEDBACK-...",
  "feedback_scope": "insight",
  "target_report_id": "REPORT-...",
  "target_insight_ids": ["INSIGHT-3"],
  "sentiment": "negative",
  "feedback_types": ["too_surface_level"],
  "structured_preferences": {
      "desired_depth_delta": "increase",
      "preferred_patterns": [...],
      "avoid_patterns": [...],
      "requested_lenses": [...],
      "preferred_insight_ids": [...],
      "rejected_insight_ids": [...],
      "positive_examples": [...],
      "negative_examples": [...]
  },
  "refinement_request": {
      "requested": true,
      "target_weaknesses": [...],
      "recommended_actions": [...]
  },
  "metadata": {
      "examples_are_quality_guidance_not_evidence": true,
      "authorizes_execution": false
  }
}

Non-responsibilities
--------------------
- Does not persist long-term preferences (UserPreferenceService does that).
- Does not create training pairs (PreferenceDataService does that).
- Does not rank insights (InsightRankerService does that).
- Does not generate final insights.
- Does not verify truth.
- Does not decide report release.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Set


SERVICE_VERSION = "user-feedback-service-v2.0"

ALLOWED_FEEDBACK_SCOPES = {
    "insight",
    "report",
    "run",
}

ALLOWED_SENTIMENTS = {
    "positive",
    "negative",
    "mixed",
    "neutral",
}

ALLOWED_FEEDBACK_TYPES = {
    "too_surface_level",
    "too_obvious",
    "want_more_implications",
    "want_more_mechanism",
    "want_more_cross_event_synthesis",
    "too_speculative",
    "jumping_to_conclusions",
    "insufficient_evidence",
    "missed_counterevidence",
    "missed_lens",
    "too_detailed",
    "too_brief",
    "too_repetitive",
    "good_insight",
    "bad_insight",
    "prefer_insight",
    "reject_insight",
    "want_more_uncertainty",
    "want_less_uncertainty",
    "custom",
}

ALLOWED_DEPTH_DELTAS = {
    "increase",
    "decrease",
    "unchanged",
}

ALLOWED_VERBOSITY_DELTAS = {
    "increase",
    "decrease",
    "unchanged",
}

ALLOWED_REFINEMENT_ACTIONS = {
    "retrieve_support",
    "retrieve_counterevidence",
    "expand_graph_context",
    "compare_baseline",
    "refine_reasoning",
    "broaden_evidence_search",
    "model_escalation",
}

ALLOWED_TARGET_WEAKNESSES = {
    "surface_level",
    "obvious",
    "missing_implications",
    "missing_mechanism",
    "weak_cross_event_synthesis",
    "too_speculative",
    "unsupported_leap",
    "insufficient_evidence",
    "missing_counterevidence",
    "missing_lens",
    "too_detailed",
    "too_brief",
    "repetitive",
    "missing_uncertainty",
    "excessive_uncertainty",
}

ALLOWED_QUALITY_PATTERNS = {
    "cross_event_synthesis",
    "mechanism",
    "second_order_effect",
    "structural_change",
    "non_obvious_constraint",
    "counterevidence",
    "alternative_explanation",
    "explicit_uncertainty",
    "concise_synthesis",
    "evidence_density",
    "prospective_mechanism",
    "feasibility_check",
}

ALLOWED_AVOID_PATTERNS = {
    "fact_restatement",
    "surface_level_summary",
    "generic_recommendation",
    "obvious_implication",
    "unsupported_leap",
    "excessive_speculation",
    "repetition",
    "excessive_detail",
    "premature_certainty",
}

# Fields an LLM interpreter must never return anywhere in its object tree.
PROHIBITED_CONTROL_FIELDS = {
    "blueprint",
    "blueprint_id",
    "policy",
    "policy_registry",
    "quality_policy",
    "execution_policy",
    "runtime_policy",
    "data_policy",
    "release_policy",
    "release_gate",
    "release_authorized",
    "authorize_release",
    "model_route",
    "model_router",
    "model_name",
    "model_tier",
    "threshold",
    "thresholds",
    "grounding_threshold",
    "confidence_threshold",
    "search_budget",
    "token_budget",
    "cost_budget",
    "max_run_seconds",
    "max_search_rounds",
    "max_search_queries",
    "skip_grounding",
    "skip_red_team",
    "disable_grounding",
    "disable_red_team",
    "evidence_ids",
    "evidence_id",
    "fact_ids",
    "metric_ids",
    "triple_ids",
}

# Common deterministic mappings. They are product-quality mappings, not policy.
FEEDBACK_RULES = (
    {
        "code": "too_surface_level",
        "patterns": (
            r"\btoo surface[- ]?level\b",
            r"\bsurface[- ]?level\b",
            r"\bnot deep enough\b",
            r"\bdeeper insight",
            r"\bmore depth\b",
            r"\btries? harder\b",
        ),
        "sentiment": "negative",
        "depth": "increase",
        "preferred": (
            "cross_event_synthesis",
            "mechanism",
            "second_order_effect",
            "structural_change",
            "non_obvious_constraint",
        ),
        "avoid": (
            "surface_level_summary",
            "fact_restatement",
        ),
        "weaknesses": (
            "surface_level",
        ),
        "actions": (
            "expand_graph_context",
            "broaden_evidence_search",
            "refine_reasoning",
        ),
    },
    {
        "code": "too_obvious",
        "patterns": (
            r"\btoo obvious\b",
            r"\bobvious insight",
            r"\bnot non[- ]?obvious\b",
            r"\bnothing new\b",
            r"\balready obvious\b",
        ),
        "sentiment": "negative",
        "depth": "increase",
        "preferred": (
            "structural_change",
            "non_obvious_constraint",
            "mechanism",
            "second_order_effect",
        ),
        "avoid": (
            "obvious_implication",
            "fact_restatement",
        ),
        "weaknesses": (
            "obvious",
        ),
        "actions": (
            "compare_baseline",
            "expand_graph_context",
            "refine_reasoning",
        ),
    },
    {
        "code": "want_more_implications",
        "patterns": (
            r"\bmore implications\b",
            r"\bmore second[- ]order\b",
            r"\bsecond[- ]order effects?\b",
            r"\bwhat happens next\b",
            r"\bfurther consequences\b",
        ),
        "sentiment": "neutral",
        "depth": "increase",
        "preferred": (
            "second_order_effect",
            "structural_change",
        ),
        "avoid": (),
        "weaknesses": (
            "missing_implications",
        ),
        "actions": (
            "expand_graph_context",
            "refine_reasoning",
        ),
    },
    {
        "code": "want_more_mechanism",
        "patterns": (
            r"\bmore mechanism\b",
            r"\bexplain why\b",
            r"\bhow does this happen\b",
            r"\bwhy would this happen\b",
            r"\bcausal chain\b",
        ),
        "sentiment": "neutral",
        "depth": "increase",
        "preferred": (
            "mechanism",
            "prospective_mechanism",
        ),
        "avoid": (),
        "weaknesses": (
            "missing_mechanism",
        ),
        "actions": (
            "expand_graph_context",
            "refine_reasoning",
        ),
    },
    {
        "code": "want_more_cross_event_synthesis",
        "patterns": (
            r"\bconnect more events\b",
            r"\bconnect the events\b",
            r"\bcross[- ]event\b",
            r"\bmore synthesis\b",
            r"\bconnect the dots\b",
        ),
        "sentiment": "neutral",
        "depth": "increase",
        "preferred": (
            "cross_event_synthesis",
            "structural_change",
        ),
        "avoid": (
            "fact_restatement",
        ),
        "weaknesses": (
            "weak_cross_event_synthesis",
        ),
        "actions": (
            "expand_graph_context",
            "broaden_evidence_search",
            "refine_reasoning",
        ),
    },
    {
        "code": "too_speculative",
        "patterns": (
            r"\btoo speculative\b",
            r"\bover[- ]?speculat",
            r"\btoo much speculation\b",
            r"\bnot grounded enough\b",
            r"\btoo hypothetical\b",
        ),
        "sentiment": "negative",
        "depth": "unchanged",
        "preferred": (
            "evidence_density",
            "explicit_uncertainty",
            "feasibility_check",
        ),
        "avoid": (
            "excessive_speculation",
            "unsupported_leap",
            "premature_certainty",
        ),
        "weaknesses": (
            "too_speculative",
        ),
        "actions": (
            "retrieve_support",
            "retrieve_counterevidence",
            "refine_reasoning",
        ),
    },
    {
        "code": "jumping_to_conclusions",
        "patterns": (
            r"\bjumping to conclusions\b",
            r"\btoo strong a conclusion\b",
            r"\boverclaim",
            r"\bunsupported leap\b",
            r"\bleap in reasoning\b",
        ),
        "sentiment": "negative",
        "depth": "unchanged",
        "preferred": (
            "explicit_uncertainty",
            "counterevidence",
            "evidence_density",
        ),
        "avoid": (
            "unsupported_leap",
            "premature_certainty",
        ),
        "weaknesses": (
            "unsupported_leap",
        ),
        "actions": (
            "retrieve_support",
            "retrieve_counterevidence",
            "refine_reasoning",
        ),
    },
    {
        "code": "insufficient_evidence",
        "patterns": (
            r"\bnot enough evidence\b",
            r"\binsufficient evidence\b",
            r"\bmore evidence\b",
            r"\bweak evidence\b",
        ),
        "sentiment": "negative",
        "depth": "unchanged",
        "preferred": (
            "evidence_density",
        ),
        "avoid": (
            "unsupported_leap",
        ),
        "weaknesses": (
            "insufficient_evidence",
        ),
        "actions": (
            "retrieve_support",
            "retrieve_counterevidence",
        ),
    },
    {
        "code": "missed_counterevidence",
        "patterns": (
            r"\bmissed counterevidence\b",
            r"\bwhat contradicts\b",
            r"\bopposing evidence\b",
            r"\bother side\b",
            r"\bcounter[- ]?evidence\b",
        ),
        "sentiment": "negative",
        "depth": "unchanged",
        "preferred": (
            "counterevidence",
            "alternative_explanation",
        ),
        "avoid": (
            "premature_certainty",
        ),
        "weaknesses": (
            "missing_counterevidence",
        ),
        "actions": (
            "retrieve_counterevidence",
            "broaden_evidence_search",
        ),
    },
    {
        "code": "too_detailed",
        "patterns": (
            r"\btoo detailed\b",
            r"\btoo much detail\b",
            r"\btoo long\b",
            r"\btoo verbose\b",
            r"\bmore concise\b",
            r"\bshorter\b",
        ),
        "sentiment": "negative",
        "depth": "unchanged",
        "verbosity": "decrease",
        "preferred": (
            "concise_synthesis",
        ),
        "avoid": (
            "excessive_detail",
            "repetition",
        ),
        "weaknesses": (
            "too_detailed",
        ),
        "actions": (
            "refine_reasoning",
        ),
    },
    {
        "code": "too_brief",
        "patterns": (
            r"\btoo brief\b",
            r"\btoo short\b",
            r"\bmore explanation\b",
            r"\bmore detail\b",
        ),
        "sentiment": "negative",
        "depth": "unchanged",
        "verbosity": "increase",
        "preferred": (),
        "avoid": (),
        "weaknesses": (
            "too_brief",
        ),
        "actions": (
            "refine_reasoning",
        ),
    },
    {
        "code": "too_repetitive",
        "patterns": (
            r"\btoo repetitive\b",
            r"\brepetitive\b",
            r"\brepeats? itself\b",
        ),
        "sentiment": "negative",
        "depth": "unchanged",
        "preferred": (
            "concise_synthesis",
        ),
        "avoid": (
            "repetition",
        ),
        "weaknesses": (
            "repetitive",
        ),
        "actions": (
            "refine_reasoning",
        ),
    },
    {
        "code": "good_insight",
        "patterns": (
            r"\bgood insight\b",
            r"\bthis insight is good\b",
            r"\bthis is useful\b",
            r"\bthis is insightful\b",
            r"\bi like this insight\b",
            r"\bmore like this\b",
        ),
        "sentiment": "positive",
        "depth": "unchanged",
        "preferred": (),
        "avoid": (),
        "weaknesses": (),
        "actions": (),
    },
)


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class UserFeedbackError(RuntimeError):
    """Base exception."""


class UserFeedbackValidationError(UserFeedbackError):
    """Raised when feedback violates the contract."""


class UserFeedbackIntegrityError(UserFeedbackError):
    """Raised when model/caller references unknown objects."""


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


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return (
        f"{prefix}-"
        + _sha256(
            _canonical_json_bytes(payload)
        )[:length].upper()
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
        raise UserFeedbackValidationError(
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


def _contains_prohibited_field(value: Any, path: str = "") -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = _normalize_enum(key)
            current_path = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            if normalized_key in PROHIBITED_CONTROL_FIELDS:
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


def _normalize_example(value: Any) -> Dict[str, str]:
    if isinstance(value, str):
        text = _clean(value)

        if not text:
            raise UserFeedbackValidationError(
                "Example text cannot be empty."
            )

        return {
            "text": text,
            "reason": "",
        }

    if not isinstance(value, Mapping):
        raise UserFeedbackValidationError(
            "Feedback example must be text or an object."
        )

    text = _clean(
        value.get("text")
        or value.get("insight")
        or value.get("example")
    )

    if not text:
        raise UserFeedbackValidationError(
            "Feedback example is missing text."
        )

    return {
        "text": text,
        "reason": _clean(
            value.get("reason")
            or value.get("why")
        ),
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class UserFeedbackService:
    """
    Convert raw/explicit user feedback into a structured quality signal.

    Optional `llm_interpreter`
    --------------------------
    Must be callable or expose interpret/generate/ask.

    It receives a strict prompt and may only return the documented schema.
    Model output is validated and cannot invent insight IDs or control policy.
    """

    def __init__(
        self,
        *,
        llm_interpreter: Optional[Any] = None,
        clock: Callable[[], str] = _utc_now_iso,
        model_name: str = "",
    ) -> None:
        if not callable(clock):
            raise UserFeedbackValidationError(
                "clock must be callable."
            )

        self.llm_interpreter = llm_interpreter
        self.clock = clock
        self.model_name = _clean(model_name)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def interpret(
        self,
        feedback_text: str,
        *,
        feedback_scope: str = "report",
        report_id: str = "",
        run_id: str = "",
        target_insight_ids: Sequence[str] = (),
        available_insights: Sequence[Mapping[str, Any]] = (),
        rating: Optional[int] = None,
        requested_lenses: Sequence[str] = (),
        positive_examples: Sequence[Any] = (),
        negative_examples: Sequence[Any] = (),
        preferred_insight_ids: Sequence[str] = (),
        rejected_insight_ids: Sequence[str] = (),
        event_nonce: str = "",
    ) -> Dict[str, Any]:
        """
        Interpret one user feedback event.

        Explicit fields supplied by the UI/caller override free-text/LLM
        interpretation. This prevents a model from changing an explicit user
        selection.
        """
        feedback_text = _clean(feedback_text)
        feedback_scope = _normalize_enum(
            feedback_scope
        )

        if feedback_scope not in ALLOWED_FEEDBACK_SCOPES:
            raise UserFeedbackValidationError(
                f"Unsupported feedback_scope={feedback_scope!r}."
            )

        report_id = _clean(report_id)
        run_id = _clean(run_id)

        available = self._normalize_available_insights(
            available_insights
        )
        allowed_insight_ids = set(
            available
        )

        explicit_targets = self._validate_insight_ids(
            target_insight_ids,
            allowed_insight_ids=allowed_insight_ids,
            field_name="target_insight_ids",
            allow_unknown_when_inventory_empty=True,
        )

        explicit_preferred_ids = self._validate_insight_ids(
            preferred_insight_ids,
            allowed_insight_ids=allowed_insight_ids,
            field_name="preferred_insight_ids",
            allow_unknown_when_inventory_empty=True,
        )

        explicit_rejected_ids = self._validate_insight_ids(
            rejected_insight_ids,
            allowed_insight_ids=allowed_insight_ids,
            field_name="rejected_insight_ids",
            allow_unknown_when_inventory_empty=True,
        )

        explicit_positive_examples = [
            _normalize_example(item)
            for item in positive_examples
        ]

        explicit_negative_examples = [
            _normalize_example(item)
            for item in negative_examples
        ]

        explicit_lenses = _stable_unique_strings(
            requested_lenses
        )

        normalized_rating = self._normalize_rating(
            rating
        )

        if (
            not feedback_text
            and normalized_rating is None
            and not explicit_targets
            and not explicit_preferred_ids
            and not explicit_rejected_ids
            and not explicit_positive_examples
            and not explicit_negative_examples
            and not explicit_lenses
        ):
            raise UserFeedbackValidationError(
                "Feedback event is empty."
            )

        deterministic = self._deterministic_interpretation(
            feedback_text=feedback_text,
            target_insight_ids=explicit_targets,
            available_insights=available,
            rating=normalized_rating,
        )

        model_result = {}

        if (
            self.llm_interpreter is not None
            and feedback_text
        ):
            model_result = self._run_llm_interpreter(
                feedback_text=feedback_text,
                feedback_scope=feedback_scope,
                report_id=report_id,
                run_id=run_id,
                target_insight_ids=explicit_targets,
                available_insights=available,
                rating=normalized_rating,
            )

        merged = self._merge_interpretations(
            deterministic=deterministic,
            model=model_result,
        )

        # Explicit caller/UI values always win.
        if explicit_targets:
            merged[
                "target_insight_ids"
            ] = explicit_targets

        merged_preferences = merged[
            "structured_preferences"
        ]

        merged_preferences[
            "requested_lenses"
        ] = _stable_unique_strings(
            list(
                merged_preferences.get(
                    "requested_lenses"
                )
                or []
            )
            + explicit_lenses
        )

        merged_preferences[
            "preferred_insight_ids"
        ] = _stable_unique_strings(
            list(
                merged_preferences.get(
                    "preferred_insight_ids"
                )
                or []
            )
            + explicit_preferred_ids
        )

        merged_preferences[
            "rejected_insight_ids"
        ] = _stable_unique_strings(
            list(
                merged_preferences.get(
                    "rejected_insight_ids"
                )
                or []
            )
            + explicit_rejected_ids
        )

        merged_preferences[
            "positive_examples"
        ] = self._merge_examples(
            merged_preferences.get(
                "positive_examples"
            )
            or [],
            explicit_positive_examples,
        )

        merged_preferences[
            "negative_examples"
        ] = self._merge_examples(
            merged_preferences.get(
                "negative_examples"
            )
            or [],
            explicit_negative_examples,
        )

        self._validate_interpretation_ids(
            merged,
            allowed_insight_ids=allowed_insight_ids,
            allow_unknown_when_inventory_empty=True,
        )

        if normalized_rating is not None:
            merged["rating"] = normalized_rating

        recorded_at = self.clock()

        feedback_id = _stable_id(
            "FEEDBACK",
            {
                "recorded_at": recorded_at,
                "event_nonce": _clean(
                    event_nonce
                ),
                "feedback_scope": feedback_scope,
                "report_id": report_id,
                "run_id": run_id,
                "feedback_text": feedback_text,
                "target_insight_ids": merged[
                    "target_insight_ids"
                ],
                "rating": normalized_rating,
                "structured_preferences": merged[
                    "structured_preferences"
                ],
            },
        )

        result = {
            "feedback_id": feedback_id,
            "recorded_at": recorded_at,
            "feedback_scope": feedback_scope,
            "target_report_id": report_id,
            "target_run_id": run_id,
            "target_insight_ids": merged[
                "target_insight_ids"
            ],
            "sentiment": merged[
                "sentiment"
            ],
            "feedback_types": merged[
                "feedback_types"
            ],
            "rating": normalized_rating,
            "raw_feedback": feedback_text,
            "structured_preferences": merged[
                "structured_preferences"
            ],
            "refinement_request": merged[
                "refinement_request"
            ],
            "metadata": {
                "service_version": SERVICE_VERSION,
                "model": self.model_name,
                "llm_interpreter_used": (
                    self.llm_interpreter is not None
                    and bool(feedback_text)
                ),
                "available_insight_ids": sorted(
                    allowed_insight_ids
                ),
                "examples_are_quality_guidance_not_evidence": True,
                "user_feedback_is_grounding_evidence": False,
                "authorizes_execution": False,
                "authorizes_release": False,
                "modifies_policy": False,
                "profile_agnostic": True,
                "private_chain_of_thought_stored": False,
            },
        }

        return result

    # ------------------------------------------------------------------
    # Deterministic interpretation
    # ------------------------------------------------------------------

    def _deterministic_interpretation(
        self,
        *,
        feedback_text: str,
        target_insight_ids: Sequence[str],
        available_insights: Mapping[str, Mapping[str, Any]],
        rating: Optional[int],
    ) -> Dict[str, Any]:
        lowered = feedback_text.casefold()

        feedback_types = []
        sentiments = []
        preferred_patterns = []
        avoid_patterns = []
        weaknesses = []
        actions = []
        depth_signals = []
        verbosity_signals = []
        requested_lenses = []
        preferred_ids = []
        rejected_ids = []
        positive_examples = []
        negative_examples = []

        for rule in FEEDBACK_RULES:
            matched = any(
                re.search(
                    pattern,
                    lowered,
                    flags=re.IGNORECASE,
                )
                for pattern in rule[
                    "patterns"
                ]
            )

            if not matched:
                continue

            feedback_types.append(
                rule["code"]
            )
            sentiments.append(
                rule["sentiment"]
            )
            depth_signals.append(
                rule.get(
                    "depth",
                    "unchanged",
                )
            )
            verbosity_signals.append(
                rule.get(
                    "verbosity",
                    "unchanged",
                )
            )
            preferred_patterns.extend(
                rule.get(
                    "preferred",
                    ()
                )
            )
            avoid_patterns.extend(
                rule.get(
                    "avoid",
                    ()
                )
            )
            weaknesses.extend(
                rule.get(
                    "weaknesses",
                    ()
                )
            )
            actions.extend(
                rule.get(
                    "actions",
                    ()
                )
            )

        # Lightweight lens extraction from "missed X angle/lens" and
        # "consider X angle/lens".
        lens_patterns = (
            r"\bmissed (?:the )?([a-z][a-z0-9 &/\-]{1,50}) (?:angle|lens)\b",
            r"\bconsider (?:the )?([a-z][a-z0-9 &/\-]{1,50}) (?:angle|lens)\b",
            r"\badd (?:the )?([a-z][a-z0-9 &/\-]{1,50}) (?:angle|lens)\b",
        )

        for pattern in lens_patterns:
            for match in re.finditer(
                pattern,
                feedback_text,
                flags=re.IGNORECASE,
            ):
                lens = _clean(
                    match.group(1)
                )

                if lens:
                    requested_lenses.append(
                        lens
                    )
                    feedback_types.append(
                        "missed_lens"
                    )
                    weaknesses.append(
                        "missing_lens"
                    )
                    actions.extend(
                        [
                            "expand_graph_context",
                            "broaden_evidence_search",
                            "refine_reasoning",
                        ]
                    )

        # Explicit textual references to known stable insight IDs.
        mentioned_ids = self._find_known_insight_ids(
            feedback_text,
            available_insights=available_insights,
        )

        if (
            "more like this" in lowered
            or "i like this insight" in lowered
            or "this insight is good" in lowered
        ):
            positive_target_ids = (
                list(target_insight_ids)
                or mentioned_ids
            )

            preferred_ids.extend(
                positive_target_ids
            )

            for insight_id in positive_target_ids:
                insight_text = _clean(
                    available_insights.get(
                        insight_id,
                        {},
                    ).get(
                        "text"
                    )
                )

                if insight_text:
                    positive_examples.append(
                        {
                            "text": insight_text,
                            "reason": feedback_text,
                        }
                    )

        if re.search(
            r"\b(?:don'?t like|dislike|bad insight|reject this|not useful)\b",
            lowered,
            flags=re.IGNORECASE,
        ):
            negative_target_ids = (
                list(target_insight_ids)
                or mentioned_ids
            )

            rejected_ids.extend(
                negative_target_ids
            )
            feedback_types.append(
                "bad_insight"
            )
            sentiments.append(
                "negative"
            )

            for insight_id in negative_target_ids:
                insight_text = _clean(
                    available_insights.get(
                        insight_id,
                        {},
                    ).get(
                        "text"
                    )
                )

                if insight_text:
                    negative_examples.append(
                        {
                            "text": insight_text,
                            "reason": feedback_text,
                        }
                    )

        # Pairwise preference:
        # "more like INSIGHT-B than INSIGHT-A"
        known_pattern = re.compile(
            r"\bmore like\s+([A-Za-z0-9_\-]+)\s+than\s+([A-Za-z0-9_\-]+)\b",
            flags=re.IGNORECASE,
        )

        for match in known_pattern.finditer(
            feedback_text
        ):
            preferred_candidate = self._resolve_insight_reference(
                match.group(1),
                available_insights,
            )
            rejected_candidate = self._resolve_insight_reference(
                match.group(2),
                available_insights,
            )

            if preferred_candidate:
                preferred_ids.append(
                    preferred_candidate
                )

            if rejected_candidate:
                rejected_ids.append(
                    rejected_candidate
                )

            if (
                preferred_candidate
                and rejected_candidate
            ):
                feedback_types.append(
                    "prefer_insight"
                )
                sentiments.append(
                    "positive"
                )

        # Rating is an explicit useful signal.
        if rating is not None:
            if rating >= 4:
                sentiments.append(
                    "positive"
                )
            elif rating <= 2:
                sentiments.append(
                    "negative"
                )
            else:
                sentiments.append(
                    "neutral"
                )

        feedback_types = _stable_unique_strings(
            feedback_types
        )

        if not feedback_types and feedback_text:
            feedback_types = [
                "custom"
            ]

        sentiment = self._combine_sentiments(
            sentiments
        )

        desired_depth_delta = self._combine_delta(
            depth_signals,
            allowed=ALLOWED_DEPTH_DELTAS,
        )

        verbosity_delta = self._combine_delta(
            verbosity_signals,
            allowed=ALLOWED_VERBOSITY_DELTAS,
        )

        actions = [
            action
            for action in _stable_unique_strings(
                actions
            )
            if action in ALLOWED_REFINEMENT_ACTIONS
        ]

        weaknesses = [
            weakness
            for weakness in _stable_unique_strings(
                weaknesses
            )
            if weakness in ALLOWED_TARGET_WEAKNESSES
        ]

        refinement_requested = bool(
            weaknesses
            or actions
        )

        return {
            "target_insight_ids": _stable_unique_strings(
                list(target_insight_ids)
                + mentioned_ids
            ),
            "sentiment": sentiment,
            "feedback_types": [
                code
                for code in feedback_types
                if code in ALLOWED_FEEDBACK_TYPES
            ],
            "structured_preferences": {
                "desired_depth_delta": desired_depth_delta,
                "verbosity_delta": verbosity_delta,
                "preferred_patterns": [
                    item
                    for item in _stable_unique_strings(
                        preferred_patterns
                    )
                    if item in ALLOWED_QUALITY_PATTERNS
                ],
                "avoid_patterns": [
                    item
                    for item in _stable_unique_strings(
                        avoid_patterns
                    )
                    if item in ALLOWED_AVOID_PATTERNS
                ],
                "requested_lenses": _stable_unique_strings(
                    requested_lenses
                ),
                "preferred_insight_ids": _stable_unique_strings(
                    preferred_ids
                ),
                "rejected_insight_ids": _stable_unique_strings(
                    rejected_ids
                ),
                "positive_examples": positive_examples,
                "negative_examples": negative_examples,
            },
            "refinement_request": {
                "requested": refinement_requested,
                "target_weaknesses": weaknesses,
                "recommended_actions": actions,
                "execution_authorized_here": False,
            },
        }

    # ------------------------------------------------------------------
    # Optional LLM interpreter
    # ------------------------------------------------------------------

    def _run_llm_interpreter(
        self,
        *,
        feedback_text: str,
        feedback_scope: str,
        report_id: str,
        run_id: str,
        target_insight_ids: Sequence[str],
        available_insights: Mapping[str, Mapping[str, Any]],
        rating: Optional[int],
    ) -> Dict[str, Any]:
        prompt = self._build_interpreter_prompt(
            feedback_text=feedback_text,
            feedback_scope=feedback_scope,
            report_id=report_id,
            run_id=run_id,
            target_insight_ids=target_insight_ids,
            available_insights=available_insights,
            rating=rating,
        )

        interpreter = self.llm_interpreter

        if callable(interpreter):
            raw = interpreter(prompt)

        else:
            method = None

            for method_name in (
                "interpret",
                "generate",
                "ask",
            ):
                candidate = getattr(
                    interpreter,
                    method_name,
                    None,
                )

                if callable(candidate):
                    method = candidate
                    break

            if method is None:
                raise UserFeedbackValidationError(
                    "llm_interpreter must be callable or expose "
                    "interpret/generate/ask."
                )

            raw = method(prompt)

        payload = self._parse_json_object(
            raw
        )

        prohibited_path = _contains_prohibited_field(
            payload
        )

        if prohibited_path:
            raise UserFeedbackIntegrityError(
                "llm_interpreter attempted to emit prohibited control/evidence "
                f"field at {prohibited_path!r}."
            )

        normalized = self._normalize_model_interpretation(
            payload
        )

        self._validate_interpretation_ids(
            normalized,
            allowed_insight_ids=set(
                available_insights
            ),
            allow_unknown_when_inventory_empty=False,
        )

        return normalized

    def _build_interpreter_prompt(
        self,
        *,
        feedback_text: str,
        feedback_scope: str,
        report_id: str,
        run_id: str,
        target_insight_ids: Sequence[str],
        available_insights: Mapping[str, Mapping[str, Any]],
        rating: Optional[int],
    ) -> str:
        insight_inventory = [
            {
                "insight_id": insight_id,
                "text": _clean(
                    item.get("text")
                ),
            }
            for insight_id, item in available_insights.items()
        ]

        return f"""
You are a narrow user-feedback interpreter for a strategic research product.

Your job is to convert the user's QUALITY preference into structured feedback.

Critical rules:
1. User feedback is NOT factual evidence.
2. Insight examples are QUALITY references only.
3. Do not create evidence IDs.
4. Do not change grounding standards.
5. Do not create policy, thresholds, model routes, budgets or release decisions.
6. Do not authorize execution.
7. You may reference only insight IDs from the supplied inventory.
8. If the user asks for "deeper" analysis, express that as a preference signal,
   not as a runtime/search budget.
9. Return no chain-of-thought.

Feedback scope:
{feedback_scope}

Report ID:
{report_id}

Run ID:
{run_id}

Explicit target insight IDs:
{json.dumps(list(target_insight_ids), ensure_ascii=False)}

Rating:
{json.dumps(rating)}

Available insights:
{json.dumps(insight_inventory, ensure_ascii=False, indent=2)}

User feedback:
{feedback_text}

Allowed feedback_types:
{json.dumps(sorted(ALLOWED_FEEDBACK_TYPES))}

Allowed preferred_patterns:
{json.dumps(sorted(ALLOWED_QUALITY_PATTERNS))}

Allowed avoid_patterns:
{json.dumps(sorted(ALLOWED_AVOID_PATTERNS))}

Allowed refinement actions:
{json.dumps(sorted(ALLOWED_REFINEMENT_ACTIONS))}

Allowed target weaknesses:
{json.dumps(sorted(ALLOWED_TARGET_WEAKNESSES))}

Return exactly one JSON object:

{{
  "target_insight_ids": [],
  "sentiment": "positive | negative | mixed | neutral",
  "feedback_types": [],
  "structured_preferences": {{
    "desired_depth_delta": "increase | decrease | unchanged",
    "verbosity_delta": "increase | decrease | unchanged",
    "preferred_patterns": [],
    "avoid_patterns": [],
    "requested_lenses": [],
    "preferred_insight_ids": [],
    "rejected_insight_ids": [],
    "positive_examples": [
      {{
        "text": "quality-reference insight text",
        "reason": "why the user liked it"
      }}
    ],
    "negative_examples": [
      {{
        "text": "quality-reference insight text",
        "reason": "why the user disliked it"
      }}
    ]
  }},
  "refinement_request": {{
    "requested": false,
    "target_weaknesses": [],
    "recommended_actions": [],
    "execution_authorized_here": false
  }}
}}
""".strip()

    def _normalize_model_interpretation(
        self,
        payload: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(payload, Mapping):
            raise UserFeedbackValidationError(
                "Model interpretation must be an object."
            )

        sentiment = _normalize_enum(
            payload.get("sentiment")
            or "neutral"
        )

        if sentiment not in ALLOWED_SENTIMENTS:
            raise UserFeedbackValidationError(
                f"Invalid model sentiment={sentiment!r}."
            )

        feedback_types = [
            item
            for item in _stable_unique_strings(
                payload.get(
                    "feedback_types"
                )
                or []
            )
            if item in ALLOWED_FEEDBACK_TYPES
        ]

        preferences = payload.get(
            "structured_preferences"
        ) or {}

        if not isinstance(preferences, Mapping):
            raise UserFeedbackValidationError(
                "structured_preferences must be an object."
            )

        depth = _normalize_enum(
            preferences.get(
                "desired_depth_delta"
            )
            or "unchanged"
        )

        if depth not in ALLOWED_DEPTH_DELTAS:
            raise UserFeedbackValidationError(
                "Invalid desired_depth_delta."
            )

        verbosity = _normalize_enum(
            preferences.get(
                "verbosity_delta"
            )
            or "unchanged"
        )

        if verbosity not in ALLOWED_VERBOSITY_DELTAS:
            raise UserFeedbackValidationError(
                "Invalid verbosity_delta."
            )

        refinement = payload.get(
            "refinement_request"
        ) or {}

        if not isinstance(refinement, Mapping):
            raise UserFeedbackValidationError(
                "refinement_request must be an object."
            )

        # This field must always remain false regardless of model output.
        if bool(
            refinement.get(
                "execution_authorized_here"
            )
        ):
            raise UserFeedbackIntegrityError(
                "llm_interpreter cannot authorize execution."
            )

        return {
            "target_insight_ids": _stable_unique_strings(
                payload.get(
                    "target_insight_ids"
                )
                or []
            ),
            "sentiment": sentiment,
            "feedback_types": feedback_types,
            "structured_preferences": {
                "desired_depth_delta": depth,
                "verbosity_delta": verbosity,
                "preferred_patterns": [
                    item
                    for item in _stable_unique_strings(
                        preferences.get(
                            "preferred_patterns"
                        )
                        or []
                    )
                    if item in ALLOWED_QUALITY_PATTERNS
                ],
                "avoid_patterns": [
                    item
                    for item in _stable_unique_strings(
                        preferences.get(
                            "avoid_patterns"
                        )
                        or []
                    )
                    if item in ALLOWED_AVOID_PATTERNS
                ],
                "requested_lenses": _stable_unique_strings(
                    preferences.get(
                        "requested_lenses"
                    )
                    or []
                ),
                "preferred_insight_ids": _stable_unique_strings(
                    preferences.get(
                        "preferred_insight_ids"
                    )
                    or []
                ),
                "rejected_insight_ids": _stable_unique_strings(
                    preferences.get(
                        "rejected_insight_ids"
                    )
                    or []
                ),
                "positive_examples": [
                    _normalize_example(item)
                    for item in (
                        preferences.get(
                            "positive_examples"
                        )
                        or []
                    )
                ],
                "negative_examples": [
                    _normalize_example(item)
                    for item in (
                        preferences.get(
                            "negative_examples"
                        )
                        or []
                    )
                ],
            },
            "refinement_request": {
                "requested": bool(
                    refinement.get(
                        "requested"
                    )
                ),
                "target_weaknesses": [
                    item
                    for item in _stable_unique_strings(
                        refinement.get(
                            "target_weaknesses"
                        )
                        or []
                    )
                    if item in ALLOWED_TARGET_WEAKNESSES
                ],
                "recommended_actions": [
                    item
                    for item in _stable_unique_strings(
                        refinement.get(
                            "recommended_actions"
                        )
                        or []
                    )
                    if item in ALLOWED_REFINEMENT_ACTIONS
                ],
                "execution_authorized_here": False,
            },
        }

    # ------------------------------------------------------------------
    # Merge
    # ------------------------------------------------------------------

    def _merge_interpretations(
        self,
        *,
        deterministic: Mapping[str, Any],
        model: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not model:
            return deepcopy(
                dict(
                    deterministic
                )
            )

        det_prefs = deterministic[
            "structured_preferences"
        ]
        model_prefs = model[
            "structured_preferences"
        ]

        merged_depth = self._combine_delta(
            [
                det_prefs.get(
                    "desired_depth_delta",
                    "unchanged",
                ),
                model_prefs.get(
                    "desired_depth_delta",
                    "unchanged",
                ),
            ],
            allowed=ALLOWED_DEPTH_DELTAS,
        )

        merged_verbosity = self._combine_delta(
            [
                det_prefs.get(
                    "verbosity_delta",
                    "unchanged",
                ),
                model_prefs.get(
                    "verbosity_delta",
                    "unchanged",
                ),
            ],
            allowed=ALLOWED_VERBOSITY_DELTAS,
        )

        refinement = {
            "requested": bool(
                deterministic[
                    "refinement_request"
                ].get(
                    "requested"
                )
                or model[
                    "refinement_request"
                ].get(
                    "requested"
                )
            ),
            "target_weaknesses": _stable_unique_strings(
                list(
                    deterministic[
                        "refinement_request"
                    ].get(
                        "target_weaknesses"
                    )
                    or []
                )
                + list(
                    model[
                        "refinement_request"
                    ].get(
                        "target_weaknesses"
                    )
                    or []
                )
            ),
            "recommended_actions": _stable_unique_strings(
                list(
                    deterministic[
                        "refinement_request"
                    ].get(
                        "recommended_actions"
                    )
                    or []
                )
                + list(
                    model[
                        "refinement_request"
                    ].get(
                        "recommended_actions"
                    )
                    or []
                )
            ),
            "execution_authorized_here": False,
        }

        refinement[
            "target_weaknesses"
        ] = [
            item
            for item in refinement[
                "target_weaknesses"
            ]
            if item in ALLOWED_TARGET_WEAKNESSES
        ]

        refinement[
            "recommended_actions"
        ] = [
            item
            for item in refinement[
                "recommended_actions"
            ]
            if item in ALLOWED_REFINEMENT_ACTIONS
        ]

        return {
            "target_insight_ids": _stable_unique_strings(
                list(
                    deterministic.get(
                        "target_insight_ids"
                    )
                    or []
                )
                + list(
                    model.get(
                        "target_insight_ids"
                    )
                    or []
                )
            ),
            "sentiment": self._combine_sentiments(
                [
                    deterministic.get(
                        "sentiment",
                        "neutral",
                    ),
                    model.get(
                        "sentiment",
                        "neutral",
                    ),
                ]
            ),
            "feedback_types": _stable_unique_strings(
                list(
                    deterministic.get(
                        "feedback_types"
                    )
                    or []
                )
                + list(
                    model.get(
                        "feedback_types"
                    )
                    or []
                )
            ),
            "structured_preferences": {
                "desired_depth_delta": merged_depth,
                "verbosity_delta": merged_verbosity,
                "preferred_patterns": _stable_unique_strings(
                    list(
                        det_prefs.get(
                            "preferred_patterns"
                        )
                        or []
                    )
                    + list(
                        model_prefs.get(
                            "preferred_patterns"
                        )
                        or []
                    )
                ),
                "avoid_patterns": _stable_unique_strings(
                    list(
                        det_prefs.get(
                            "avoid_patterns"
                        )
                        or []
                    )
                    + list(
                        model_prefs.get(
                            "avoid_patterns"
                        )
                        or []
                    )
                ),
                "requested_lenses": _stable_unique_strings(
                    list(
                        det_prefs.get(
                            "requested_lenses"
                        )
                        or []
                    )
                    + list(
                        model_prefs.get(
                            "requested_lenses"
                        )
                        or []
                    )
                ),
                "preferred_insight_ids": _stable_unique_strings(
                    list(
                        det_prefs.get(
                            "preferred_insight_ids"
                        )
                        or []
                    )
                    + list(
                        model_prefs.get(
                            "preferred_insight_ids"
                        )
                        or []
                    )
                ),
                "rejected_insight_ids": _stable_unique_strings(
                    list(
                        det_prefs.get(
                            "rejected_insight_ids"
                        )
                        or []
                    )
                    + list(
                        model_prefs.get(
                            "rejected_insight_ids"
                        )
                        or []
                    )
                ),
                "positive_examples": self._merge_examples(
                    det_prefs.get(
                        "positive_examples"
                    )
                    or [],
                    model_prefs.get(
                        "positive_examples"
                    )
                    or [],
                ),
                "negative_examples": self._merge_examples(
                    det_prefs.get(
                        "negative_examples"
                    )
                    or [],
                    model_prefs.get(
                        "negative_examples"
                    )
                    or [],
                ),
            },
            "refinement_request": refinement,
        }

    # ------------------------------------------------------------------
    # Validation / utilities
    # ------------------------------------------------------------------

    def _normalize_available_insights(
        self,
        insights: Sequence[Mapping[str, Any]],
    ) -> Dict[str, Dict[str, Any]]:
        if insights is None:
            return {}

        if (
            not isinstance(insights, Sequence)
            or isinstance(insights, (str, bytes))
        ):
            raise UserFeedbackValidationError(
                "available_insights must be a sequence."
            )

        result = {}

        for index, item in enumerate(insights):
            if not isinstance(item, Mapping):
                raise UserFeedbackValidationError(
                    f"available_insights[{index}] must be an object."
                )

            insight_id = _clean(
                item.get("insight_id")
                or item.get("id")
            )

            if not insight_id:
                raise UserFeedbackValidationError(
                    f"available_insights[{index}] is missing insight_id."
                )

            text = _clean(
                item.get("insight")
                or item.get("text")
                or item.get("statement")
            )

            if insight_id in result:
                raise UserFeedbackValidationError(
                    f"Duplicate insight_id={insight_id!r}."
                )

            result[
                insight_id
            ] = {
                "insight_id": insight_id,
                "text": text,
            }

        return result

    def _validate_insight_ids(
        self,
        values: Sequence[str],
        *,
        allowed_insight_ids: Set[str],
        field_name: str,
        allow_unknown_when_inventory_empty: bool,
    ) -> list[str]:
        normalized = _stable_unique_strings(
            values
        )

        if (
            not allowed_insight_ids
            and allow_unknown_when_inventory_empty
        ):
            return normalized

        unknown = [
            item
            for item in normalized
            if item not in allowed_insight_ids
        ]

        if unknown:
            raise UserFeedbackIntegrityError(
                f"{field_name} references unknown insight IDs: "
                + ", ".join(unknown)
            )

        return normalized

    def _validate_interpretation_ids(
        self,
        interpretation: Mapping[str, Any],
        *,
        allowed_insight_ids: Set[str],
        allow_unknown_when_inventory_empty: bool,
    ) -> None:
        preferences = interpretation.get(
            "structured_preferences"
        ) or {}

        for field_name, values in (
            (
                "target_insight_ids",
                interpretation.get(
                    "target_insight_ids"
                )
                or [],
            ),
            (
                "preferred_insight_ids",
                preferences.get(
                    "preferred_insight_ids"
                )
                or [],
            ),
            (
                "rejected_insight_ids",
                preferences.get(
                    "rejected_insight_ids"
                )
                or [],
            ),
        ):
            self._validate_insight_ids(
                values,
                allowed_insight_ids=allowed_insight_ids,
                field_name=field_name,
                allow_unknown_when_inventory_empty=(
                    allow_unknown_when_inventory_empty
                ),
            )

    def _find_known_insight_ids(
        self,
        text: str,
        *,
        available_insights: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        if not text or not available_insights:
            return []

        lowered = text.casefold()
        result = []

        for insight_id in available_insights:
            if insight_id.casefold() in lowered:
                result.append(
                    insight_id
                )

        return result

    def _resolve_insight_reference(
        self,
        reference: str,
        available_insights: Mapping[str, Mapping[str, Any]],
    ) -> str:
        reference = _clean(
            reference
        )

        if not reference:
            return ""

        if reference in available_insights:
            return reference

        lowered = reference.casefold()

        for insight_id in available_insights:
            if insight_id.casefold() == lowered:
                return insight_id

        return ""

    def _normalize_rating(
        self,
        rating: Optional[int],
    ) -> Optional[int]:
        if rating is None:
            return None

        try:
            rating = int(rating)
        except (TypeError, ValueError) as exc:
            raise UserFeedbackValidationError(
                "rating must be an integer from 1 to 5."
            ) from exc

        if not 1 <= rating <= 5:
            raise UserFeedbackValidationError(
                "rating must be between 1 and 5."
            )

        return rating

    def _combine_sentiments(
        self,
        sentiments: Sequence[str],
    ) -> str:
        normalized = [
            _normalize_enum(
                item
            )
            for item in sentiments
            if _normalize_enum(
                item
            ) in ALLOWED_SENTIMENTS
        ]

        substantive = {
            item
            for item in normalized
            if item != "neutral"
        }

        if not substantive:
            return "neutral"

        if substantive == {"positive"}:
            return "positive"

        if substantive == {"negative"}:
            return "negative"

        return "mixed"

    def _combine_delta(
        self,
        values: Sequence[str],
        *,
        allowed: Set[str],
    ) -> str:
        normalized = [
            _normalize_enum(
                item
            )
            for item in values
            if _normalize_enum(
                item
            ) in allowed
        ]

        active = {
            item
            for item in normalized
            if item != "unchanged"
        }

        if not active:
            return "unchanged"

        if len(active) == 1:
            return next(
                iter(
                    active
                )
            )

        # Conflicting feedback should not be guessed into one direction.
        return "unchanged"

    def _merge_examples(
        self,
        first: Sequence[Any],
        second: Sequence[Any],
    ) -> list[Dict[str, str]]:
        result = []
        seen = set()

        for item in list(first) + list(second):
            normalized = _normalize_example(
                item
            )

            key = (
                normalized["text"].casefold(),
                normalized["reason"].casefold(),
            )

            if key in seen:
                continue

            seen.add(key)
            result.append(
                normalized
            )

        return result

    def _parse_json_object(
        self,
        raw: Any,
    ) -> Dict[str, Any]:
        if isinstance(raw, Mapping):
            return deepcopy(
                dict(
                    raw
                )
            )

        text = _clean(
            raw
        )

        if not text:
            raise UserFeedbackValidationError(
                "llm_interpreter returned an empty response."
            )

        try:
            parsed = json.loads(
                text
            )

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
                raise UserFeedbackValidationError(
                    "llm_interpreter returned malformed JSON."
                ) from exc

            if isinstance(parsed, dict):
                return parsed

        first = text.find("{")
        last = text.rfind("}")

        if first >= 0 and last > first:
            try:
                parsed = json.loads(
                    text[
                        first:last + 1
                    ]
                )
            except json.JSONDecodeError as exc:
                raise UserFeedbackValidationError(
                    "llm_interpreter response did not contain valid JSON."
                ) from exc

            if isinstance(parsed, dict):
                return parsed

        raise UserFeedbackValidationError(
            "llm_interpreter response did not contain a JSON object."
        )