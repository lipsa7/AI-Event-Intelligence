"""
Persistent-style user preference projection for insight-quality personalization.

Recommended location:
    services/user_preference_service.py

Purpose
-------
UserFeedbackService
        ↓
structured feedback event
        ↓
UserPreferenceService
        ↓
versioned preference profile
        ↓
BlueprintCompiler / StrategicInference guidance / InsightRanker

This service converts many feedback events into a stable, auditable preference
profile such as:

{
    "desired_depth": "very_high",
    "verbosity": "balanced",
    "preferred_patterns": [
        "cross_event_synthesis",
        "mechanism",
        "second_order_effect"
    ],
    "avoid_patterns": [
        "fact_restatement",
        "surface_level_summary"
    ],
    "analytical_lenses": [
        "competitive advantage"
    ],
    "positive_examples": [...],
    "negative_examples": [...]
}

Critical boundaries
-------------------
USER PREFERENCE != FACTUAL EVIDENCE

This service never:
- inserts feedback/examples into evidence stores;
- changes grounding standards;
- changes release gates;
- changes research runtime profiles;
- changes model routing;
- changes budgets;
- authorizes refinement execution.

In particular:

    semantic desired_depth
    !=
    research_depth execution profile

A user saying "deeper insights" may increase the preferred reasoning depth of the
output. It must NOT silently change a 5-minute Fast run into a 20-minute Deep
run. `research_depth` remains an explicit Intent / Blueprint execution choice.

Architecture
------------
This service uses an event-sourced projection:

feedback events
        ↓
immutable event log
        ↓
deterministic replay
        ↓
current preference profile

Benefits:
- idempotent feedback ingestion;
- auditable preference provenance;
- feedback retraction;
- deterministic rebuild;
- future training data can be derived separately by PreferenceDataService;
- no hidden model state.

Free-project constraint
-----------------------
The implementation is local and deterministic. No paid API or LLM is required.

Persistence
-----------
The core service is storage-agnostic and keeps state in memory. It supports:
- export_snapshot()
- restore_snapshot()

A later repository/SQLite adapter can persist the returned snapshot without
changing the preference semantics.

Preference scoring
------------------
Preferred/avoid patterns and analytical lenses use bounded signed evidence
weights. These are preference-strength heuristics, NOT confidence or truth
probabilities.

The event log remains authoritative. The materialized profile is a projection.

Example retention
-----------------
All feedback events remain in the event log. The materialized profile keeps a
bounded number of representative positive/negative examples for prompt/context
safety. Retention limits are configuration, not epistemic thresholds.

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


SERVICE_VERSION = "user-preference-service-v2.0"

DEPTH_LEVELS = (
    "low",
    "medium",
    "high",
    "very_high",
)

VERBOSITY_LEVELS = (
    "concise",
    "balanced",
    "detailed",
)

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

ALLOWED_SENTIMENTS = {
    "positive",
    "negative",
    "mixed",
    "neutral",
}

# This service only accepts preference-level information. These fields are
# prohibited recursively in incoming preference override payloads.
PROHIBITED_PREFERENCE_OVERRIDE_FIELDS = {
    "research_depth",
    "analysis_mode",
    "blueprint",
    "blueprint_id",
    "policy",
    "runtime_policy",
    "execution_policy",
    "quality_policy",
    "data_policy",
    "release_policy",
    "release_gate",
    "model_route",
    "model_name",
    "threshold",
    "thresholds",
    "search_budget",
    "token_budget",
    "cost_budget",
    "max_run_seconds",
    "max_search_rounds",
    "max_search_queries",
    "skip_grounding",
    "skip_red_team",
    "evidence_id",
    "evidence_ids",
    "fact_ids",
    "metric_ids",
    "triple_ids",
}

DEFAULT_CONFIG = {
    "default_desired_depth": "high",
    "default_verbosity": "balanced",
    "pattern_weight_step": 1.0,
    "lens_weight_step": 1.0,
    "max_abs_weight": 6.0,
    "active_pattern_threshold": 0.5,
    "active_lens_threshold": 0.5,
    "max_positive_examples": 12,
    "max_negative_examples": 12,
    "max_active_lenses": 12,
    "max_active_patterns": 12,
}


class UserPreferenceError(RuntimeError):
    """Base exception."""


class UserPreferenceValidationError(UserPreferenceError):
    """Raised when preference input violates the contract."""


class UserPreferenceIntegrityError(UserPreferenceError):
    """Raised when event identity/history integrity is inconsistent."""


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

    return hashlib.sha256(
        value
    ).hexdigest()


def _stable_id(prefix: str, payload: Any, length: int = 24) -> str:
    return (
        f"{prefix}-"
        + _sha256(
            _canonical_json_bytes(
                payload
            )
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

    if not isinstance(
        values,
        (list, tuple, set),
    ):
        raise UserPreferenceValidationError(
            "Expected a string or sequence of strings."
        )

    result = []
    seen = set()

    for value in values:
        text = _clean(
            value
        )

        if text and text not in seen:
            seen.add(
                text
            )
            result.append(
                text
            )

    return result


def _normalize_example(value: Any) -> Dict[str, str]:
    if isinstance(
        value,
        str,
    ):
        text = _clean(
            value
        )

        if not text:
            raise UserPreferenceValidationError(
                "Example text cannot be empty."
            )

        return {
            "text": text,
            "reason": "",
        }

    if not isinstance(
        value,
        Mapping,
    ):
        raise UserPreferenceValidationError(
            "Example must be text or an object."
        )

    text = _clean(
        value.get("text")
        or value.get("insight")
        or value.get("example")
    )

    if not text:
        raise UserPreferenceValidationError(
            "Example is missing text."
        )

    return {
        "text": text,
        "reason": _clean(
            value.get("reason")
            or value.get("why")
        ),
    }


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[str]:
    if isinstance(
        value,
        Mapping,
    ):
        for key, item in value.items():
            normalized_key = _normalize_enum(
                key
            )

            current_path = (
                f"{path}.{key}"
                if path
                else str(key)
            )

            if (
                normalized_key
                in PROHIBITED_PREFERENCE_OVERRIDE_FIELDS
            ):
                return current_path

            nested = _contains_prohibited_field(
                item,
                current_path,
            )

            if nested:
                return nested

    elif isinstance(
        value,
        (list, tuple),
    ):
        for index, item in enumerate(
            value
        ):
            nested = _contains_prohibited_field(
                item,
                f"{path}[{index}]",
            )

            if nested:
                return nested

    return None


def _fingerprint(value: Any) -> str:
    return _sha256(
        _canonical_json_bytes(
            value
        )
    )


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class UserPreferenceService:
    """
    Event-sourced preference profile service.

    `profile_key`
    -------------
    Application-level namespace for one user's preference projection.

    It need not contain personally identifying data. A local project can use
    "default".
    """

    def __init__(
        self,
        *,
        profile_key: str = "default",
        config: Optional[
            Mapping[str, Any]
        ] = None,
        clock: Any = _utc_now_iso,
    ) -> None:
        self.profile_key = (
            _clean(
                profile_key
            )
            or "default"
        )

        if not callable(
            clock
        ):
            raise UserPreferenceValidationError(
                "clock must be callable."
            )

        self.clock = clock
        self.config = self._normalize_config(
            config
        )

        self._lock = threading.RLock()

        # feedback_id -> normalized event
        self._events: Dict[
            str,
            Dict[str, Any],
        ] = {}

        # retracted feedback IDs -> audit record
        self._retractions: Dict[
            str,
            Dict[str, Any],
        ] = {}

        # Explicit user preference overrides are events too, but kept separate
        # from report feedback so their provenance is easy to inspect.
        self._explicit_events: Dict[
            str,
            Dict[str, Any],
        ] = {}

        self._profile = self._empty_profile()

    # ------------------------------------------------------------------
    # Public API: feedback
    # ------------------------------------------------------------------

    def apply_feedback(
        self,
        feedback_event: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """
        Idempotently add one UserFeedbackService event and rebuild projection.
        """
        event = self._normalize_feedback_event(
            feedback_event
        )

        feedback_id = event[
            "feedback_id"
        ]

        with self._lock:
            existing = self._events.get(
                feedback_id
            )

            if existing is not None:
                if (
                    _fingerprint(existing)
                    != _fingerprint(event)
                ):
                    raise UserPreferenceIntegrityError(
                        "feedback_id already exists with different content: "
                        f"{feedback_id!r}."
                    )

                return self.get_profile()

            self._events[
                feedback_id
            ] = event

            # Re-applying an explicitly retracted event does not silently
            # reactivate it. Reactivation requires restore_feedback().
            self._rebuild_profile()

            return self.get_profile()

    def apply_feedback_batch(
        self,
        feedback_events: Sequence[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        if (
            not isinstance(
                feedback_events,
                Sequence,
            )
            or isinstance(
                feedback_events,
                (str, bytes),
            )
        ):
            raise UserPreferenceValidationError(
                "feedback_events must be a sequence."
            )

        normalized = [
            self._normalize_feedback_event(
                event
            )
            for event in feedback_events
        ]

        with self._lock:
            # Validate the whole batch before mutating.
            staged = {}

            for event in normalized:
                feedback_id = event[
                    "feedback_id"
                ]

                if feedback_id in staged:
                    if (
                        _fingerprint(
                            staged[
                                feedback_id
                            ]
                        )
                        != _fingerprint(
                            event
                        )
                    ):
                        raise UserPreferenceIntegrityError(
                            "Batch contains duplicate feedback_id with "
                            "different content."
                        )

                    continue

                existing = self._events.get(
                    feedback_id
                )

                if (
                    existing is not None
                    and _fingerprint(
                        existing
                    )
                    != _fingerprint(
                        event
                    )
                ):
                    raise UserPreferenceIntegrityError(
                        "feedback_id already exists with different content: "
                        f"{feedback_id!r}."
                    )

                staged[
                    feedback_id
                ] = event

            for feedback_id, event in staged.items():
                if feedback_id not in self._events:
                    self._events[
                        feedback_id
                    ] = event

            self._rebuild_profile()

            return self.get_profile()

    def retract_feedback(
        self,
        feedback_id: str,
        *,
        reason: str,
    ) -> Dict[str, Any]:
        """
        Retract one feedback event and rebuild from remaining active events.
        """
        feedback_id = _clean(
            feedback_id
        )

        reason = _clean(
            reason
        )

        if not feedback_id:
            raise UserPreferenceValidationError(
                "feedback_id cannot be empty."
            )

        if not reason:
            raise UserPreferenceValidationError(
                "retraction reason cannot be empty."
            )

        with self._lock:
            if feedback_id not in self._events:
                raise UserPreferenceValidationError(
                    f"Unknown feedback_id={feedback_id!r}."
                )

            self._retractions[
                feedback_id
            ] = {
                "feedback_id": feedback_id,
                "reason": reason,
                "retracted_at": self.clock(),
            }

            self._rebuild_profile()

            return self.get_profile()

    def restore_feedback(
        self,
        feedback_id: str,
    ) -> Dict[str, Any]:
        feedback_id = _clean(
            feedback_id
        )

        with self._lock:
            if feedback_id not in self._events:
                raise UserPreferenceValidationError(
                    f"Unknown feedback_id={feedback_id!r}."
                )

            self._retractions.pop(
                feedback_id,
                None,
            )

            self._rebuild_profile()

            return self.get_profile()

    # ------------------------------------------------------------------
    # Public API: explicit semantic preferences
    # ------------------------------------------------------------------

    def set_explicit_preferences(
        self,
        preferences: Mapping[str, Any],
        *,
        reason: str = "explicit_user_preference",
        event_nonce: str = "",
    ) -> Dict[str, Any]:
        """
        Record explicit semantic preferences.

        Explicit preferences override inferred/event-weighted preferences in the
        materialized profile.

        They still cannot set:
        - research_depth;
        - runtime budgets;
        - policies;
        - model routes;
        - release controls.
        """
        normalized = self._normalize_explicit_preferences(
            preferences
        )

        reason = _clean(
            reason
        ) or "explicit_user_preference"

        recorded_at = self.clock()

        event_id = _stable_id(
            "PREFEVENT",
            {
                "profile_key": self.profile_key,
                "recorded_at": recorded_at,
                "event_nonce": _clean(
                    event_nonce
                ),
                "preferences": normalized,
                "reason": reason,
            },
        )

        event = {
            "preference_event_id": event_id,
            "recorded_at": recorded_at,
            "reason": reason,
            "preferences": normalized,
        }

        with self._lock:
            existing = self._explicit_events.get(
                event_id
            )

            if existing is not None:
                if (
                    _fingerprint(
                        existing
                    )
                    != _fingerprint(
                        event
                    )
                ):
                    raise UserPreferenceIntegrityError(
                        "preference_event_id collision with different content."
                    )

                return self.get_profile()

            self._explicit_events[
                event_id
            ] = event

            self._rebuild_profile()

            return self.get_profile()

    # ------------------------------------------------------------------
    # Public API: profile / compiler payload
    # ------------------------------------------------------------------

    def get_profile(
        self,
    ) -> Dict[str, Any]:
        with self._lock:
            return deepcopy(
                self._profile
            )

    def compiler_preferences(
        self,
    ) -> Dict[str, Any]:
        """
        Return the user-mutable semantic preference subset safe for
        BlueprintCompiler.

        Note the deliberate absence of `research_depth`.
        """
        with self._lock:
            profile = self._profile

            return {
                "desired_depth": profile[
                    "desired_depth"
                ],
                "verbosity": profile[
                    "verbosity"
                ],
                "preferred_patterns": list(
                    profile[
                        "preferred_patterns"
                    ]
                ),
                "avoid_patterns": list(
                    profile[
                        "avoid_patterns"
                    ]
                ),
                "analytical_lenses": list(
                    profile[
                        "analytical_lenses"
                    ]
                ),
                "positive_examples": deepcopy(
                    profile[
                        "positive_examples"
                    ]
                ),
                "negative_examples": deepcopy(
                    profile[
                        "negative_examples"
                    ]
                ),
            }

    def preference_trace(
        self,
    ) -> Dict[str, Any]:
        with self._lock:
            return {
                "profile_id": self._profile[
                    "profile_id"
                ],
                "profile_version": self._profile[
                    "profile_version"
                ],
                "active_feedback_ids": list(
                    self._profile[
                        "active_feedback_ids"
                    ]
                ),
                "retracted_feedback_ids": sorted(
                    self._retractions
                ),
                "explicit_preference_event_ids": list(
                    self._profile[
                        "explicit_preference_event_ids"
                    ]
                ),
                "pattern_weights": deepcopy(
                    self._profile[
                        "diagnostics"
                    ][
                        "pattern_weights"
                    ]
                ),
                "avoid_pattern_weights": deepcopy(
                    self._profile[
                        "diagnostics"
                    ][
                        "avoid_pattern_weights"
                    ]
                ),
                "lens_weights": deepcopy(
                    self._profile[
                        "diagnostics"
                    ][
                        "lens_weights"
                    ]
                ),
                "depth_signal": deepcopy(
                    self._profile[
                        "diagnostics"
                    ][
                        "depth_signal"
                    ]
                ),
                "verbosity_signal": deepcopy(
                    self._profile[
                        "diagnostics"
                    ][
                        "verbosity_signal"
                    ]
                ),
                "private_chain_of_thought_stored": False,
            }

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def export_snapshot(
        self,
    ) -> Dict[str, Any]:
        with self._lock:
            snapshot = {
                "service_version": SERVICE_VERSION,
                "profile_key": self.profile_key,
                "config": deepcopy(
                    self.config
                ),
                "feedback_events": deepcopy(
                    self._events
                ),
                "retractions": deepcopy(
                    self._retractions
                ),
                "explicit_preference_events": deepcopy(
                    self._explicit_events
                ),
                "profile": deepcopy(
                    self._profile
                ),
                "metadata": {
                    "user_preferences_are_grounding_evidence": False,
                    "examples_are_quality_guidance_not_evidence": True,
                    "research_depth_modified_here": False,
                    "policy_modified_here": False,
                    "private_chain_of_thought_stored": False,
                },
            }

            snapshot[
                "snapshot_sha256"
            ] = _fingerprint(
                {
                    key: value
                    for key, value
                    in snapshot.items()
                    if key != "snapshot_sha256"
                }
            )

            return snapshot

    def restore_snapshot(
        self,
        snapshot: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(
            snapshot,
            Mapping,
        ):
            raise UserPreferenceValidationError(
                "snapshot must be an object."
            )

        supplied_hash = _clean(
            snapshot.get(
                "snapshot_sha256"
            )
        )

        if supplied_hash:
            computed_hash = _fingerprint(
                {
                    key: value
                    for key, value
                    in snapshot.items()
                    if key != "snapshot_sha256"
                }
            )

            if supplied_hash != computed_hash:
                raise UserPreferenceIntegrityError(
                    "Preference snapshot fingerprint mismatch."
                )

        if (
            _clean(
                snapshot.get(
                    "profile_key"
                )
            )
            != self.profile_key
        ):
            raise UserPreferenceValidationError(
                "Snapshot profile_key does not match this service."
            )

        raw_events = (
            snapshot.get(
                "feedback_events"
            )
            or {}
        )
        raw_retractions = (
            snapshot.get(
                "retractions"
            )
            or {}
        )
        raw_explicit = (
            snapshot.get(
                "explicit_preference_events"
            )
            or {}
        )

        if not isinstance(
            raw_events,
            Mapping,
        ):
            raise UserPreferenceValidationError(
                "snapshot.feedback_events must be an object."
            )

        if not isinstance(
            raw_retractions,
            Mapping,
        ):
            raise UserPreferenceValidationError(
                "snapshot.retractions must be an object."
            )

        if not isinstance(
            raw_explicit,
            Mapping,
        ):
            raise UserPreferenceValidationError(
                "snapshot.explicit_preference_events must be an object."
            )

        normalized_events = {}

        for feedback_id, event in raw_events.items():
            normalized = self._normalize_feedback_event(
                event
            )

            if normalized[
                "feedback_id"
            ] != feedback_id:
                raise UserPreferenceIntegrityError(
                    "Snapshot feedback event key/id mismatch."
                )

            normalized_events[
                feedback_id
            ] = normalized

        normalized_retractions = {}

        for feedback_id, retraction in raw_retractions.items():
            if feedback_id not in normalized_events:
                raise UserPreferenceIntegrityError(
                    "Snapshot retracts an unknown feedback event."
                )

            if not isinstance(
                retraction,
                Mapping,
            ):
                raise UserPreferenceValidationError(
                    "Retraction records must be objects."
                )

            reason = _clean(
                retraction.get(
                    "reason"
                )
            )

            if not reason:
                raise UserPreferenceValidationError(
                    "Retraction record is missing reason."
                )

            normalized_retractions[
                feedback_id
            ] = {
                "feedback_id": feedback_id,
                "reason": reason,
                "retracted_at": _clean(
                    retraction.get(
                        "retracted_at"
                    )
                ),
            }

        normalized_explicit = {}

        for event_id, event in raw_explicit.items():
            if not isinstance(
                event,
                Mapping,
            ):
                raise UserPreferenceValidationError(
                    "Explicit preference events must be objects."
                )

            if (
                _clean(
                    event.get(
                        "preference_event_id"
                    )
                )
                != event_id
            ):
                raise UserPreferenceIntegrityError(
                    "Explicit preference event key/id mismatch."
                )

            normalized_explicit[
                event_id
            ] = {
                "preference_event_id": event_id,
                "recorded_at": _clean(
                    event.get(
                        "recorded_at"
                    )
                ),
                "reason": _clean(
                    event.get(
                        "reason"
                    )
                ),
                "preferences": self._normalize_explicit_preferences(
                    event.get(
                        "preferences"
                    )
                    or {}
                ),
            }

        with self._lock:
            self._events = normalized_events
            self._retractions = normalized_retractions
            self._explicit_events = normalized_explicit

            self._rebuild_profile()

            return self.get_profile()

    # ------------------------------------------------------------------
    # Projection
    # ------------------------------------------------------------------

    def _rebuild_profile(
        self,
    ) -> None:
        pattern_weights: Dict[
            str,
            float,
        ] = {}
        avoid_weights: Dict[
            str,
            float,
        ] = {}
        lens_weights: Dict[
            str,
            float,
        ] = {}

        depth_score = 0
        verbosity_score = 0

        depth_feedback_ids = []
        verbosity_feedback_ids = []

        positive_examples: list[
            Dict[str, Any]
        ] = []
        negative_examples: list[
            Dict[str, Any]
        ] = []

        active_feedback_ids = []

        preferred_insight_ids: list[str] = []
        rejected_insight_ids: list[str] = []

        active_events = [
            event
            for feedback_id, event
            in self._events.items()
            if feedback_id
            not in self._retractions
        ]

        active_events.sort(
            key=lambda event: (
                _clean(
                    event.get(
                        "recorded_at"
                    )
                ),
                event[
                    "feedback_id"
                ],
            )
        )

        for event in active_events:
            feedback_id = event[
                "feedback_id"
            ]

            active_feedback_ids.append(
                feedback_id
            )

            preferences = event[
                "structured_preferences"
            ]

            depth_delta = preferences[
                "desired_depth_delta"
            ]

            if depth_delta == "increase":
                depth_score += 1
                depth_feedback_ids.append(
                    feedback_id
                )
            elif depth_delta == "decrease":
                depth_score -= 1
                depth_feedback_ids.append(
                    feedback_id
                )

            verbosity_delta = preferences[
                "verbosity_delta"
            ]

            if verbosity_delta == "increase":
                verbosity_score += 1
                verbosity_feedback_ids.append(
                    feedback_id
                )
            elif verbosity_delta == "decrease":
                verbosity_score -= 1
                verbosity_feedback_ids.append(
                    feedback_id
                )

            sentiment_multiplier = self._sentiment_multiplier(
                event[
                    "sentiment"
                ]
            )

            for pattern in preferences[
                "preferred_patterns"
            ]:
                self._add_weight(
                    pattern_weights,
                    pattern,
                    self.config[
                        "pattern_weight_step"
                    ]
                    * sentiment_multiplier,
                )

            for pattern in preferences[
                "avoid_patterns"
            ]:
                self._add_weight(
                    avoid_weights,
                    pattern,
                    self.config[
                        "pattern_weight_step"
                    ],
                )

            for lens in preferences[
                "requested_lenses"
            ]:
                self._add_weight(
                    lens_weights,
                    lens,
                    self.config[
                        "lens_weight_step"
                    ],
                )

            preferred_insight_ids.extend(
                preferences[
                    "preferred_insight_ids"
                ]
            )

            rejected_insight_ids.extend(
                preferences[
                    "rejected_insight_ids"
                ]
            )

            for example in preferences[
                "positive_examples"
            ]:
                positive_examples.append(
                    {
                        **deepcopy(
                            example
                        ),
                        "feedback_id": feedback_id,
                    }
                )

            for example in preferences[
                "negative_examples"
            ]:
                negative_examples.append(
                    {
                        **deepcopy(
                            example
                        ),
                        "feedback_id": feedback_id,
                    }
                )

        inferred_depth = self._apply_ordinal_delta(
            self.config[
                "default_desired_depth"
            ],
            DEPTH_LEVELS,
            depth_score,
        )

        inferred_verbosity = self._apply_ordinal_delta(
            self.config[
                "default_verbosity"
            ],
            VERBOSITY_LEVELS,
            verbosity_score,
        )

        active_patterns = self._top_weighted_keys(
            pattern_weights,
            threshold=self.config[
                "active_pattern_threshold"
            ],
            limit=self.config[
                "max_active_patterns"
            ],
        )

        active_avoid = self._top_weighted_keys(
            avoid_weights,
            threshold=self.config[
                "active_pattern_threshold"
            ],
            limit=self.config[
                "max_active_patterns"
            ],
        )

        active_lenses = self._top_weighted_keys(
            lens_weights,
            threshold=self.config[
                "active_lens_threshold"
            ],
            limit=self.config[
                "max_active_lenses"
            ],
        )

        # Explicit preference events are applied in chronological order; later
        # explicit values override earlier explicit values for scalar fields,
        # while lists are merged.
        explicit_depth = ""
        explicit_verbosity = ""
        explicit_preferred = []
        explicit_avoid = []
        explicit_lenses = []
        explicit_positive = []
        explicit_negative = []

        explicit_event_ids = []

        explicit_events = sorted(
            self._explicit_events.values(),
            key=lambda event: (
                _clean(
                    event.get(
                        "recorded_at"
                    )
                ),
                event[
                    "preference_event_id"
                ],
            ),
        )

        for event in explicit_events:
            explicit_event_ids.append(
                event[
                    "preference_event_id"
                ]
            )

            prefs = event[
                "preferences"
            ]

            if prefs.get(
                "desired_depth"
            ):
                explicit_depth = prefs[
                    "desired_depth"
                ]

            if prefs.get(
                "verbosity"
            ):
                explicit_verbosity = prefs[
                    "verbosity"
                ]

            explicit_preferred.extend(
                prefs.get(
                    "preferred_patterns"
                )
                or []
            )
            explicit_avoid.extend(
                prefs.get(
                    "avoid_patterns"
                )
                or []
            )
            explicit_lenses.extend(
                prefs.get(
                    "analytical_lenses"
                )
                or []
            )
            explicit_positive.extend(
                prefs.get(
                    "positive_examples"
                )
                or []
            )
            explicit_negative.extend(
                prefs.get(
                    "negative_examples"
                )
                or []
            )

        final_depth = (
            explicit_depth
            or inferred_depth
        )

        final_verbosity = (
            explicit_verbosity
            or inferred_verbosity
        )

        final_preferred = self._stable_merge_with_explicit_first(
            explicit_preferred,
            active_patterns,
        )[
            : self.config[
                "max_active_patterns"
            ]
        ]

        final_avoid = self._stable_merge_with_explicit_first(
            explicit_avoid,
            active_avoid,
        )[
            : self.config[
                "max_active_patterns"
            ]
        ]

        final_lenses = self._stable_merge_with_explicit_first(
            explicit_lenses,
            active_lenses,
        )[
            : self.config[
                "max_active_lenses"
            ]
        ]

        final_positive_examples = self._merge_examples(
            explicit_positive,
            positive_examples,
            limit=self.config[
                "max_positive_examples"
            ],
        )

        final_negative_examples = self._merge_examples(
            explicit_negative,
            negative_examples,
            limit=self.config[
                "max_negative_examples"
            ],
        )

        profile_version = (
            self._profile.get(
                "profile_version",
                0,
            )
            + 1
        )

        profile_id = _stable_id(
            "PREFPROFILE",
            {
                "profile_key": self.profile_key,
            },
        )

        projection_fingerprint = _fingerprint(
            {
                "desired_depth": final_depth,
                "verbosity": final_verbosity,
                "preferred_patterns": final_preferred,
                "avoid_patterns": final_avoid,
                "analytical_lenses": final_lenses,
                "positive_examples": final_positive_examples,
                "negative_examples": final_negative_examples,
                "active_feedback_ids": active_feedback_ids,
                "explicit_event_ids": explicit_event_ids,
            }
        )

        self._profile = {
            "profile_id": profile_id,
            "profile_key": self.profile_key,
            "profile_version": profile_version,
            "updated_at": self.clock(),
            "desired_depth": final_depth,
            "verbosity": final_verbosity,
            "preferred_patterns": final_preferred,
            "avoid_patterns": final_avoid,
            "analytical_lenses": final_lenses,
            "positive_examples": final_positive_examples,
            "negative_examples": final_negative_examples,
            "preferred_insight_ids": _stable_unique_strings(
                preferred_insight_ids
            ),
            "rejected_insight_ids": _stable_unique_strings(
                rejected_insight_ids
            ),
            "active_feedback_ids": active_feedback_ids,
            "explicit_preference_event_ids": explicit_event_ids,
            "diagnostics": {
                "pattern_weights": self._sorted_weight_map(
                    pattern_weights
                ),
                "avoid_pattern_weights": self._sorted_weight_map(
                    avoid_weights
                ),
                "lens_weights": self._sorted_weight_map(
                    lens_weights
                ),
                "depth_signal": {
                    "raw_delta": depth_score,
                    "feedback_ids": depth_feedback_ids,
                    "inferred_value": inferred_depth,
                    "explicit_override": explicit_depth,
                },
                "verbosity_signal": {
                    "raw_delta": verbosity_score,
                    "feedback_ids": verbosity_feedback_ids,
                    "inferred_value": inferred_verbosity,
                    "explicit_override": explicit_verbosity,
                },
            },
            "metadata": {
                "service_version": SERVICE_VERSION,
                "projection_fingerprint": projection_fingerprint,
                "feedback_event_count": len(
                    self._events
                ),
                "active_feedback_event_count": len(
                    active_feedback_ids
                ),
                "retracted_feedback_event_count": len(
                    self._retractions
                ),
                "explicit_preference_event_count": len(
                    explicit_event_ids
                ),
                "user_preferences_are_grounding_evidence": False,
                "examples_are_quality_guidance_not_evidence": True,
                "research_depth_modified_here": False,
                "policy_modified_here": False,
                "authorizes_execution": False,
                "private_chain_of_thought_stored": False,
            },
        }

    def _empty_profile(
        self,
    ) -> Dict[str, Any]:
        profile_id = _stable_id(
            "PREFPROFILE",
            {
                "profile_key": self.profile_key,
            },
        )

        return {
            "profile_id": profile_id,
            "profile_key": self.profile_key,
            "profile_version": 0,
            "updated_at": self.clock(),
            "desired_depth": self.config[
                "default_desired_depth"
            ],
            "verbosity": self.config[
                "default_verbosity"
            ],
            "preferred_patterns": [],
            "avoid_patterns": [],
            "analytical_lenses": [],
            "positive_examples": [],
            "negative_examples": [],
            "preferred_insight_ids": [],
            "rejected_insight_ids": [],
            "active_feedback_ids": [],
            "explicit_preference_event_ids": [],
            "diagnostics": {
                "pattern_weights": {},
                "avoid_pattern_weights": {},
                "lens_weights": {},
                "depth_signal": {
                    "raw_delta": 0,
                    "feedback_ids": [],
                    "inferred_value": self.config[
                        "default_desired_depth"
                    ],
                    "explicit_override": "",
                },
                "verbosity_signal": {
                    "raw_delta": 0,
                    "feedback_ids": [],
                    "inferred_value": self.config[
                        "default_verbosity"
                    ],
                    "explicit_override": "",
                },
            },
            "metadata": {
                "service_version": SERVICE_VERSION,
                "projection_fingerprint": "",
                "feedback_event_count": 0,
                "active_feedback_event_count": 0,
                "retracted_feedback_event_count": 0,
                "explicit_preference_event_count": 0,
                "user_preferences_are_grounding_evidence": False,
                "examples_are_quality_guidance_not_evidence": True,
                "research_depth_modified_here": False,
                "policy_modified_here": False,
                "authorizes_execution": False,
                "private_chain_of_thought_stored": False,
            },
        }

    # ------------------------------------------------------------------
    # Normalization
    # ------------------------------------------------------------------

    def _normalize_feedback_event(
        self,
        event: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(
            event,
            Mapping,
        ):
            raise UserPreferenceValidationError(
                "feedback_event must be an object."
            )

        feedback_id = _clean(
            event.get(
                "feedback_id"
            )
        )

        if not feedback_id:
            raise UserPreferenceValidationError(
                "feedback_event is missing feedback_id."
            )

        sentiment = _normalize_enum(
            event.get(
                "sentiment"
            )
            or "neutral"
        )

        if sentiment not in ALLOWED_SENTIMENTS:
            raise UserPreferenceValidationError(
                f"Unsupported feedback sentiment={sentiment!r}."
            )

        preferences = event.get(
            "structured_preferences"
        ) or {}

        if not isinstance(
            preferences,
            Mapping,
        ):
            raise UserPreferenceValidationError(
                "structured_preferences must be an object."
            )

        prohibited = _contains_prohibited_field(
            preferences
        )

        if prohibited:
            raise UserPreferenceValidationError(
                "structured_preferences contains a prohibited control/evidence "
                f"field at {prohibited!r}."
            )

        depth_delta = _normalize_enum(
            preferences.get(
                "desired_depth_delta"
            )
            or "unchanged"
        )

        if depth_delta not in ALLOWED_DEPTH_DELTAS:
            raise UserPreferenceValidationError(
                "Invalid desired_depth_delta."
            )

        verbosity_delta = _normalize_enum(
            preferences.get(
                "verbosity_delta"
            )
            or "unchanged"
        )

        if (
            verbosity_delta
            not in ALLOWED_VERBOSITY_DELTAS
        ):
            raise UserPreferenceValidationError(
                "Invalid verbosity_delta."
            )

        preferred_patterns = [
            item
            for item in _stable_unique_strings(
                preferences.get(
                    "preferred_patterns"
                )
                or []
            )
            if item in ALLOWED_QUALITY_PATTERNS
        ]

        avoid_patterns = [
            item
            for item in _stable_unique_strings(
                preferences.get(
                    "avoid_patterns"
                )
                or []
            )
            if item in ALLOWED_AVOID_PATTERNS
        ]

        positive_examples = [
            _normalize_example(
                item
            )
            for item in (
                preferences.get(
                    "positive_examples"
                )
                or []
            )
        ]

        negative_examples = [
            _normalize_example(
                item
            )
            for item in (
                preferences.get(
                    "negative_examples"
                )
                or []
            )
        ]

        metadata = event.get(
            "metadata"
        ) or {}

        if (
            metadata
            and not isinstance(
                metadata,
                Mapping,
            )
        ):
            raise UserPreferenceValidationError(
                "feedback_event.metadata must be an object."
            )

        if bool(
            metadata.get(
                "user_feedback_is_grounding_evidence"
            )
        ):
            raise UserPreferenceIntegrityError(
                "Feedback events cannot be marked as grounding evidence."
            )

        if bool(
            metadata.get(
                "authorizes_execution"
            )
        ):
            raise UserPreferenceIntegrityError(
                "Feedback events cannot authorize execution."
            )

        normalized = {
            "feedback_id": feedback_id,
            "recorded_at": _clean(
                event.get(
                    "recorded_at"
                )
            ),
            "feedback_scope": _normalize_enum(
                event.get(
                    "feedback_scope"
                )
            ),
            "target_report_id": _clean(
                event.get(
                    "target_report_id"
                )
            ),
            "target_run_id": _clean(
                event.get(
                    "target_run_id"
                )
            ),
            "target_insight_ids": _stable_unique_strings(
                event.get(
                    "target_insight_ids"
                )
                or []
            ),
            "sentiment": sentiment,
            "feedback_types": _stable_unique_strings(
                event.get(
                    "feedback_types"
                )
                or []
            ),
            "rating": self._normalize_rating(
                event.get(
                    "rating"
                )
            ),
            "structured_preferences": {
                "desired_depth_delta": depth_delta,
                "verbosity_delta": verbosity_delta,
                "preferred_patterns": preferred_patterns,
                "avoid_patterns": avoid_patterns,
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
                "positive_examples": positive_examples,
                "negative_examples": negative_examples,
            },
        }

        return normalized

    def _normalize_explicit_preferences(
        self,
        preferences: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(
            preferences,
            Mapping,
        ):
            raise UserPreferenceValidationError(
                "preferences must be an object."
            )

        prohibited = _contains_prohibited_field(
            preferences
        )

        if prohibited:
            raise UserPreferenceValidationError(
                "Explicit preferences contain a prohibited control/evidence "
                f"field at {prohibited!r}."
            )

        allowed_top_level = {
            "desired_depth",
            "verbosity",
            "preferred_patterns",
            "avoid_patterns",
            "analytical_lenses",
            "positive_examples",
            "negative_examples",
        }

        unknown_fields = (
            set(
                preferences
            )
            - allowed_top_level
        )

        if unknown_fields:
            raise UserPreferenceValidationError(
                "Unsupported explicit preference fields: "
                + ", ".join(
                    sorted(
                        unknown_fields
                    )
                )
            )

        desired_depth = _normalize_enum(
            preferences.get(
                "desired_depth"
            )
        )

        if (
            desired_depth
            and desired_depth
            not in DEPTH_LEVELS
        ):
            raise UserPreferenceValidationError(
                "desired_depth must be one of "
                + ", ".join(
                    DEPTH_LEVELS
                )
            )

        verbosity = _normalize_enum(
            preferences.get(
                "verbosity"
            )
        )

        if (
            verbosity
            and verbosity
            not in VERBOSITY_LEVELS
        ):
            raise UserPreferenceValidationError(
                "verbosity must be one of "
                + ", ".join(
                    VERBOSITY_LEVELS
                )
            )

        return {
            "desired_depth": desired_depth,
            "verbosity": verbosity,
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
            "analytical_lenses": _stable_unique_strings(
                preferences.get(
                    "analytical_lenses"
                )
                or []
            ),
            "positive_examples": [
                _normalize_example(
                    item
                )
                for item in (
                    preferences.get(
                        "positive_examples"
                    )
                    or []
                )
            ],
            "negative_examples": [
                _normalize_example(
                    item
                )
                for item in (
                    preferences.get(
                        "negative_examples"
                    )
                    or []
                )
            ],
        }

    def _normalize_rating(
        self,
        value: Any,
    ) -> Optional[int]:
        if value is None or value == "":
            return None

        try:
            rating = int(
                value
            )
        except (
            TypeError,
            ValueError,
        ) as exc:
            raise UserPreferenceValidationError(
                "rating must be an integer from 1 to 5."
            ) from exc

        if not 1 <= rating <= 5:
            raise UserPreferenceValidationError(
                "rating must be between 1 and 5."
            )

        return rating

    def _normalize_config(
        self,
        config: Optional[
            Mapping[str, Any]
        ],
    ) -> Dict[str, Any]:
        result = deepcopy(
            DEFAULT_CONFIG
        )

        if config is not None:
            if not isinstance(
                config,
                Mapping,
            ):
                raise UserPreferenceValidationError(
                    "config must be an object."
                )

            unknown = (
                set(
                    config
                )
                - set(
                    DEFAULT_CONFIG
                )
            )

            if unknown:
                raise UserPreferenceValidationError(
                    "Unknown config fields: "
                    + ", ".join(
                        sorted(
                            unknown
                        )
                    )
                )

            result.update(
                dict(
                    config
                )
            )

        depth = _normalize_enum(
            result[
                "default_desired_depth"
            ]
        )

        if depth not in DEPTH_LEVELS:
            raise UserPreferenceValidationError(
                "default_desired_depth is invalid."
            )

        result[
            "default_desired_depth"
        ] = depth

        verbosity = _normalize_enum(
            result[
                "default_verbosity"
            ]
        )

        if verbosity not in VERBOSITY_LEVELS:
            raise UserPreferenceValidationError(
                "default_verbosity is invalid."
            )

        result[
            "default_verbosity"
        ] = verbosity

        for field in (
            "pattern_weight_step",
            "lens_weight_step",
            "max_abs_weight",
            "active_pattern_threshold",
            "active_lens_threshold",
        ):
            try:
                result[
                    field
                ] = float(
                    result[
                        field
                    ]
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise UserPreferenceValidationError(
                    f"{field} must be numeric."
                ) from exc

            if result[field] < 0:
                raise UserPreferenceValidationError(
                    f"{field} cannot be negative."
                )

        for field in (
            "max_positive_examples",
            "max_negative_examples",
            "max_active_lenses",
            "max_active_patterns",
        ):
            try:
                result[
                    field
                ] = int(
                    result[
                        field
                    ]
                )
            except (
                TypeError,
                ValueError,
            ) as exc:
                raise UserPreferenceValidationError(
                    f"{field} must be an integer."
                ) from exc

            if result[field] < 0:
                raise UserPreferenceValidationError(
                    f"{field} cannot be negative."
                )

        return result

    # ------------------------------------------------------------------
    # Projection helpers
    # ------------------------------------------------------------------

    def _sentiment_multiplier(
        self,
        sentiment: str,
    ) -> float:
        # A preference explicitly tagged preferred by UserFeedbackService should
        # remain a positive quality signal even if the overall feedback sentence
        # was negative ("too surface-level; prefer mechanisms").
        #
        # Sentiment only slightly dampens neutral/mixed events rather than
        # reversing the semantic preference direction.
        return {
            "positive": 1.0,
            "negative": 1.0,
            "mixed": 0.75,
            "neutral": 0.75,
        }[
            sentiment
        ]

    def _add_weight(
        self,
        weights: Dict[
            str,
            float,
        ],
        key: str,
        delta: float,
    ) -> None:
        current = float(
            weights.get(
                key,
                0.0,
            )
        )

        updated = current + float(
            delta
        )

        limit = float(
            self.config[
                "max_abs_weight"
            ]
        )

        updated = max(
            -limit,
            min(
                limit,
                updated,
            ),
        )

        weights[
            key
        ] = round(
            updated,
            4,
        )

    def _top_weighted_keys(
        self,
        weights: Mapping[
            str,
            float,
        ],
        *,
        threshold: float,
        limit: int,
    ) -> list[str]:
        eligible = [
            (
                key,
                float(
                    value
                ),
            )
            for key, value
            in weights.items()
            if float(
                value
            ) >= threshold
        ]

        eligible.sort(
            key=lambda item: (
                -item[1],
                item[0],
            )
        )

        return [
            key
            for key, _
            in eligible[
                :limit
            ]
        ]

    def _sorted_weight_map(
        self,
        weights: Mapping[
            str,
            float,
        ],
    ) -> Dict[str, float]:
        return {
            key: float(
                value
            )
            for key, value
            in sorted(
                weights.items(),
                key=lambda item: (
                    -float(
                        item[1]
                    ),
                    item[0],
                ),
            )
        }

    def _apply_ordinal_delta(
        self,
        default: str,
        levels: Sequence[str],
        raw_delta: int,
    ) -> str:
        try:
            index = list(
                levels
            ).index(
                default
            )
        except ValueError as exc:
            raise UserPreferenceIntegrityError(
                "Configured default is not present in ordinal levels."
            ) from exc

        # Repeated feedback can move more than one level, but clamping keeps the
        # output inside the stable preference vocabulary.
        target = max(
            0,
            min(
                len(levels) - 1,
                index + int(
                    raw_delta
                ),
            ),
        )

        return levels[
            target
        ]

    def _stable_merge_with_explicit_first(
        self,
        explicit: Sequence[str],
        inferred: Sequence[str],
    ) -> list[str]:
        return _stable_unique_strings(
            list(
                explicit
            )
            + list(
                inferred
            )
        )

    def _merge_examples(
        self,
        explicit: Sequence[Any],
        inferred: Sequence[Any],
        *,
        limit: int,
    ) -> list[Dict[str, Any]]:
        result = []
        seen = set()

        # Explicit examples first.
        for item in list(
            explicit
        ) + list(
            reversed(
                list(
                    inferred
                )
            )
        ):
            normalized = _normalize_example(
                item
            )

            feedback_id = ""

            if isinstance(
                item,
                Mapping,
            ):
                feedback_id = _clean(
                    item.get(
                        "feedback_id"
                    )
                )

            key = (
                normalized[
                    "text"
                ].casefold(),
                normalized[
                    "reason"
                ].casefold(),
            )

            if key in seen:
                continue

            seen.add(
                key
            )

            output = {
                **normalized,
            }

            if feedback_id:
                output[
                    "feedback_id"
                ] = feedback_id

            result.append(
                output
            )

            if len(result) >= limit:
                break

        return result