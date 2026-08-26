"""
Natural-language intent interpretation and deterministic validation service.

Recommended location:
    services/intent_service.py

Role
----
User request
    +
optional explicit user insight preferences
        ↓
IntentService
    ├─ optional LLM interpretation into a CANDIDATE intent
    └─ deterministic validation / normalization
        ↓
VALIDATED INTENT
        ↓
BlueprintCompiler
    +
PolicyRegistry
    +
(optional persisted UserPreferenceService profile)
        ↓
Execution Blueprint

Core control-plane rule
-----------------------
The LLM may interpret what the user is asking for and faithfully capture
explicit preferences about what kinds of insights the user finds useful.

The LLM may NOT:
- invent policies;
- create quality gates;
- lower grounding or safety thresholds;
- choose models;
- authorize execution;
- choose refinement limits;
- generate the final Blueprint.

Those decisions belong to deterministic control-plane services.

Validated intent contract
-------------------------
{
    "intent_id": "INTENT-...",
    "intent_type": "research",
    "research_question": "...",
    "analysis_mode": "research",
    "requested_output": "research_report",
    "constraints": {...},
    "source_preferences": {...},
    "insight_preferences": {
        "desired_depth": "deep",
        "preferred_patterns": [
            "cross_event_synthesis",
            "mechanism",
            "second_order_effect"
        ],
        "avoid_patterns": [
            "fact_restatement"
        ],
        "analytical_lenses": [],
        "positive_examples": [],
        "negative_examples": []
    }
}

`insight_preferences` are preferences about usefulness and reasoning style.
They are NOT evidence and must never weaken grounding requirements.

Important feedback architecture
-------------------------------
Initial request:
    IntentService captures explicit insight preferences.

After a report:
    UserFeedbackService
        ↓
    UserPreferenceService / PreferenceDataService
        ↓
    BlueprintCompiler

Feedback-generated preferences do NOT need to be reparsed through
IntentService. IntentService only owns user-request interpretation.

Non-responsibilities
--------------------
- No policy compilation.
- No policy enforcement.
- No model routing.
- No report generation.
- No evidence retrieval.
- No release decision.
- No feedback persistence.
- No reinforcement-learning/model training.
- No silent request truncation.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Callable, Dict, Iterable, Mapping, Optional, Set


INTENT_SCHEMA_VERSION = "1.1"
INTENT_PROMPT_VERSION = "intent-v2"

DEFAULT_ALLOWED_INTENT_TYPES = {
    "research",
}

DEFAULT_ALLOWED_ANALYSIS_MODES = {
    "research",
}

DEFAULT_ALLOWED_OUTPUTS = {
    "research_report",
}

ALLOWED_INSIGHT_DEPTHS = {
    "",
    "standard",
    "deep",
    "very_deep",
}

ALLOWED_INSIGHT_PATTERNS = {
    "cross_event_synthesis",
    "mechanism",
    "structural_shift",
    "changing_incentives",
    "constraint_analysis",
    "second_order_effect",
    "counterevidence",
    "uncertainty",
    "conditional_watch_signal",
    "comparative_analysis",
    "causal_chain",
    "non_obvious_connection",
    "material_update",
}

ALLOWED_AVOID_PATTERNS = {
    "fact_restatement",
    "headline_restatement",
    "generic_recommendation",
    "unsupported_speculation",
    "repetition",
    "surface_level_summary",
    "overconfident_prediction",
}

CANDIDATE_FIELDS = {
    "intent_type",
    "research_question",
    "analysis_mode",
    "requested_output",
    "constraints",
    "source_preferences",
    "insight_preferences",
}

INSIGHT_PREFERENCE_FIELDS = {
    "desired_depth",
    "preferred_patterns",
    "avoid_patterns",
    "analytical_lenses",
    "positive_examples",
    "negative_examples",
}

EXAMPLE_FIELDS = {
    "text",
    "reason",
}

# These fields represent control-plane authority. They are prohibited anywhere
# inside a model-generated candidate intent, including nested objects.
PROHIBITED_CONTROL_FIELDS = {
    "intent_id",
    "blueprint",
    "blueprint_id",
    "policy",
    "policy_bundle",
    "policy_bundle_id",
    "quality_policy",
    "quality_gate",
    "quality_gates",
    "grounding_threshold",
    "release_threshold",
    "confidence_threshold",
    "execution_policy",
    "reasoning_policy",
    "runtime_policy",
    "hard_restrictions",
    "product_governance",
    "gates",
    "release_decision",
    "release_status",
    "model",
    "model_name",
    "model_route",
    "model_router",
    "max_refinement_rounds",
    "allow_ungrounded_release",
    "skip_grounding",
    "skip_red_team",
    "disable_red_team",
    "disable_grounding",
}


class IntentServiceError(RuntimeError):
    """Base exception for intent-service failures."""


class IntentValidationError(IntentServiceError):
    """Raised when candidate intent violates the deterministic contract."""


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
    if value is None or isinstance(
        value,
        (str, int, float, bool),
    ):
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


def _canonical_json_bytes(
    value: Any,
) -> bytes:
    return json.dumps(
        _json_safe(value),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _sha256_hex(
    value: bytes | str,
) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")

    return hashlib.sha256(
        value
    ).hexdigest()


def _stable_intent_id(
    semantic_intent: Mapping[str, Any],
) -> str:
    digest = _sha256_hex(
        _canonical_json_bytes(
            semantic_intent
        )
    )[:24].upper()

    return f"INTENT-{digest}"


def _normalize_mapping(
    value: Any,
    *,
    field_name: str,
) -> Dict[str, Any]:
    if value is None:
        return {}

    if not isinstance(
        value,
        Mapping,
    ):
        raise IntentValidationError(
            f"{field_name} must be an object."
        )

    return {
        str(key): _json_safe(item)
        for key, item in value.items()
    }


def _normalize_string_list(
    value: Any,
    *,
    field_name: str,
) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        value = [value]

    if not isinstance(
        value,
        (list, tuple, set),
    ):
        raise IntentValidationError(
            f"{field_name} must be a string or list of strings."
        )

    result: list[str] = []
    seen: Set[str] = set()

    for item in value:
        text = _clean(item)

        if text and text not in seen:
            seen.add(text)
            result.append(text)

    return result


def _parse_json_object(
    raw: str | Mapping[str, Any],
) -> Dict[str, Any]:
    if isinstance(
        raw,
        Mapping,
    ):
        return dict(raw)

    text = str(
        raw
    ).strip()

    if not text:
        raise IntentValidationError(
            "Intent parser returned an empty response."
        )

    try:
        parsed = json.loads(
            text
        )

        if isinstance(
            parsed,
            dict,
        ):
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
            raise IntentValidationError(
                "Intent parser returned malformed fenced JSON."
            ) from exc

        if isinstance(
            parsed,
            dict,
        ):
            return parsed

    first = text.find(
        "{"
    )
    last = text.rfind(
        "}"
    )

    if (
        first >= 0
        and last > first
    ):
        try:
            parsed = json.loads(
                text[
                    first:last + 1
                ]
            )
        except json.JSONDecodeError as exc:
            raise IntentValidationError(
                "Intent parser response did not contain valid JSON."
            ) from exc

        if isinstance(
            parsed,
            dict,
        ):
            return parsed

    raise IntentValidationError(
        "Intent parser response did not contain a JSON object."
    )


def _find_prohibited_keys(
    value: Any,
    *,
    path: str = "",
) -> list[str]:
    """
    Recursively find control-plane fields.

    This prevents a model from hiding a policy override inside:
        constraints
        source_preferences
        insight_preferences
        examples
    """
    found: list[str] = []

    if isinstance(
        value,
        Mapping,
    ):
        for key, item in value.items():
            key_text = str(
                key
            )
            normalized = _normalize_enum(
                key_text
            )
            current_path = (
                f"{path}.{key_text}"
                if path
                else key_text
            )

            if normalized in PROHIBITED_CONTROL_FIELDS:
                found.append(
                    current_path
                )

            found.extend(
                _find_prohibited_keys(
                    item,
                    path=current_path,
                )
            )

    elif isinstance(
        value,
        (list, tuple),
    ):
        for index, item in enumerate(
            value
        ):
            found.extend(
                _find_prohibited_keys(
                    item,
                    path=(
                        f"{path}[{index}]"
                    ),
                )
            )

    return found


class IntentService:
    """
    Interpret and validate user research intent.

    `insight_preferences` may describe what kind of insight the user explicitly
    wants, for example:
        - deeper cross-event synthesis;
        - mechanisms;
        - structural shifts;
        - second-order consequences;
        - less surface-level fact restatement.

    These preferences affect the Blueprint only after BlueprintCompiler applies
    policy precedence. They never override grounding or release safeguards.
    """

    def __init__(
        self,
        *,
        intent_parser: Optional[
            Callable[
                [str],
                str | Mapping[str, Any],
            ]
        ] = None,
        allowed_intent_types: Optional[
            Iterable[str]
        ] = None,
        allowed_analysis_modes: Optional[
            Iterable[str]
        ] = None,
        allowed_outputs: Optional[
            Iterable[str]
        ] = None,
        default_intent_type: str = "research",
        default_analysis_mode: str = "research",
        default_output: str = "research_report",
        model_name: str = "",
        prompt_version: str = INTENT_PROMPT_VERSION,
    ) -> None:
        if (
            intent_parser is not None
            and not callable(
                intent_parser
            )
        ):
            raise IntentValidationError(
                "intent_parser must be callable when supplied."
            )

        self.intent_parser = (
            intent_parser
        )

        self.allowed_intent_types = (
            self._normalize_allowed_values(
                allowed_intent_types
                or DEFAULT_ALLOWED_INTENT_TYPES,
                field_name=(
                    "allowed_intent_types"
                ),
            )
        )

        self.allowed_analysis_modes = (
            self._normalize_allowed_values(
                allowed_analysis_modes
                or DEFAULT_ALLOWED_ANALYSIS_MODES,
                field_name=(
                    "allowed_analysis_modes"
                ),
            )
        )

        self.allowed_outputs = (
            self._normalize_allowed_values(
                allowed_outputs
                or DEFAULT_ALLOWED_OUTPUTS,
                field_name=(
                    "allowed_outputs"
                ),
            )
        )

        self.default_intent_type = (
            _normalize_enum(
                default_intent_type
            )
        )
        self.default_analysis_mode = (
            _normalize_enum(
                default_analysis_mode
            )
        )
        self.default_output = (
            _normalize_enum(
                default_output
            )
        )

        if (
            self.default_intent_type
            not in self.allowed_intent_types
        ):
            raise IntentValidationError(
                "default_intent_type is not in allowed_intent_types."
            )

        if (
            self.default_analysis_mode
            not in self.allowed_analysis_modes
        ):
            raise IntentValidationError(
                "default_analysis_mode is not in allowed_analysis_modes."
            )

        if (
            self.default_output
            not in self.allowed_outputs
        ):
            raise IntentValidationError(
                "default_output is not in allowed_outputs."
            )

        self.model_name = _clean(
            model_name
        )
        self.prompt_version = _clean(
            prompt_version
        )

        if not self.prompt_version:
            raise IntentValidationError(
                "prompt_version cannot be empty."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def interpret(
        self,
        *,
        request_text: str,
        context: Optional[
            Mapping[str, Any]
        ] = None,
        explicit_insight_preferences: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> Dict[str, Any]:
        """
        Interpret one user request and return the validated semantic intent.

        `explicit_insight_preferences` is useful for direct UI controls such as:
            depth = deep
            prefer mechanism
            avoid fact restatement

        Explicit UI/user values deterministically override the parser's
        interpretation of the SAME preference field. They still remain subject
        to validation and later Blueprint/policy precedence.
        """
        return self.interpret_with_metadata(
            request_text=request_text,
            context=context,
            explicit_insight_preferences=(
                explicit_insight_preferences
            ),
        )[
            "validated_intent"
        ]

    def interpret_with_metadata(
        self,
        *,
        request_text: str,
        context: Optional[
            Mapping[str, Any]
        ] = None,
        explicit_insight_preferences: Optional[
            Mapping[str, Any]
        ] = None,
    ) -> Dict[str, Any]:
        request_text = _clean(
            request_text
        )

        if not request_text:
            raise IntentValidationError(
                "request_text cannot be empty."
            )

        safe_context = (
            _normalize_mapping(
                context,
                field_name="context",
            )
            if context is not None
            else {}
        )

        explicit_preferences = (
            self._normalize_insight_preferences(
                explicit_insight_preferences
                or {}
            )
        )

        if self.intent_parser is None:
            candidate = {
                "intent_type": (
                    self.default_intent_type
                ),
                "research_question": (
                    request_text
                ),
                "analysis_mode": (
                    self.default_analysis_mode
                ),
                "requested_output": (
                    self.default_output
                ),
                "constraints": {},
                "source_preferences": {},
                "insight_preferences": {},
            }

            parser_mode = (
                "deterministic_fallback"
            )
            raw_response_hash = ""

        else:
            prompt = self._build_prompt(
                request_text=request_text,
                context=safe_context,
            )

            raw = self.intent_parser(
                prompt
            )

            candidate = (
                _parse_json_object(
                    raw
                )
            )

            raw_response_hash = (
                _sha256_hex(
                    _canonical_json_bytes(
                        raw
                        if isinstance(
                            raw,
                            Mapping,
                        )
                        else str(
                            raw
                        )
                    )
                )
            )

            parser_mode = (
                "interpreted"
            )

        if explicit_insight_preferences:
            candidate_preferences = (
                self._normalize_insight_preferences(
                    candidate.get(
                        "insight_preferences"
                    )
                    or {}
                )
            )

            candidate[
                "insight_preferences"
            ] = self._merge_insight_preferences(
                candidate_preferences,
                explicit_preferences,
            )

        validated = self.validate_candidate(
            candidate
        )

        return {
            "validated_intent": (
                validated
            ),
            "metadata": {
                "schema_version": (
                    INTENT_SCHEMA_VERSION
                ),
                "prompt_version": (
                    self.prompt_version
                ),
                "model": (
                    self.model_name
                    if self.intent_parser
                    is not None
                    else ""
                ),
                "parser_mode": (
                    parser_mode
                ),
                "request_fingerprint": (
                    _sha256_hex(
                        _canonical_json_bytes(
                            {
                                "request_text": (
                                    request_text
                                ),
                                "context": (
                                    safe_context
                                ),
                                "explicit_insight_preferences": (
                                    explicit_preferences
                                ),
                            }
                        )
                    )
                ),
                "raw_response_hash": (
                    raw_response_hash
                ),
            },
        }

    def validate_candidate(
        self,
        candidate: Mapping[
            str,
            Any,
        ],
    ) -> Dict[str, Any]:
        """
        Deterministically validate/canonicalize a candidate intent.

        Unknown top-level fields and nested control-plane fields are rejected.
        """
        if not isinstance(
            candidate,
            Mapping,
        ):
            raise IntentValidationError(
                "candidate intent must be an object."
            )

        prohibited_paths = (
            _find_prohibited_keys(
                candidate
            )
        )

        if prohibited_paths:
            raise IntentValidationError(
                "Candidate intent attempted to set prohibited control-plane "
                "field(s): "
                + ", ".join(
                    sorted(
                        prohibited_paths
                    )
                )
            )

        keys = {
            str(key)
            for key in candidate
        }

        unknown = sorted(
            keys
            - CANDIDATE_FIELDS
        )

        if unknown:
            raise IntentValidationError(
                "Candidate intent contains unknown top-level field(s): "
                + ", ".join(
                    unknown
                )
            )

        intent_type = _normalize_enum(
            candidate.get(
                "intent_type"
            )
        )
        research_question = _clean(
            candidate.get(
                "research_question"
            )
        )
        analysis_mode = _normalize_enum(
            candidate.get(
                "analysis_mode"
            )
        )
        requested_output = _normalize_enum(
            candidate.get(
                "requested_output"
            )
        )

        if not intent_type:
            raise IntentValidationError(
                "intent_type is required."
            )

        if (
            intent_type
            not in self.allowed_intent_types
        ):
            raise IntentValidationError(
                f"Unsupported intent_type={intent_type!r}."
            )

        if not research_question:
            raise IntentValidationError(
                "research_question is required."
            )

        if not analysis_mode:
            raise IntentValidationError(
                "analysis_mode is required."
            )

        if (
            analysis_mode
            not in self.allowed_analysis_modes
        ):
            raise IntentValidationError(
                f"Unsupported analysis_mode={analysis_mode!r}."
            )

        if not requested_output:
            raise IntentValidationError(
                "requested_output is required."
            )

        if (
            requested_output
            not in self.allowed_outputs
        ):
            raise IntentValidationError(
                f"Unsupported requested_output={requested_output!r}."
            )

        constraints = _normalize_mapping(
            candidate.get(
                "constraints"
            ),
            field_name="constraints",
        )

        source_preferences = (
            _normalize_mapping(
                candidate.get(
                    "source_preferences"
                ),
                field_name="source_preferences",
            )
        )

        insight_preferences = (
            self._normalize_insight_preferences(
                candidate.get(
                    "insight_preferences"
                )
                or {}
            )
        )

        semantic_intent = {
            "intent_type": (
                intent_type
            ),
            "research_question": (
                research_question
            ),
            "analysis_mode": (
                analysis_mode
            ),
            "requested_output": (
                requested_output
            ),
            "constraints": (
                constraints
            ),
            "source_preferences": (
                source_preferences
            ),
            "insight_preferences": (
                insight_preferences
            ),
        }

        return {
            "intent_id": (
                _stable_intent_id(
                    semantic_intent
                )
            ),
            **semantic_intent,
        }

    def validate_existing_intent(
        self,
        intent: Mapping[
            str,
            Any,
        ],
    ) -> Dict[str, Any]:
        """
        Revalidate a saved intent without trusting its existing intent_id.
        """
        if not isinstance(
            intent,
            Mapping,
        ):
            raise IntentValidationError(
                "intent must be an object."
            )

        candidate = {
            key: deepcopy(
                intent.get(
                    key
                )
            )
            for key
            in CANDIDATE_FIELDS
            if key in intent
        }

        return self.validate_candidate(
            candidate
        )

    # ------------------------------------------------------------------
    # Insight preference contract
    # ------------------------------------------------------------------

    def _normalize_insight_preferences(
        self,
        value: Mapping[
            str,
            Any,
        ],
    ) -> Dict[str, Any]:
        if value is None:
            value = {}

        if not isinstance(
            value,
            Mapping,
        ):
            raise IntentValidationError(
                "insight_preferences must be an object."
            )

        prohibited_paths = (
            _find_prohibited_keys(
                value,
                path="insight_preferences",
            )
        )

        if prohibited_paths:
            raise IntentValidationError(
                "insight_preferences contains prohibited control-plane "
                "field(s): "
                + ", ".join(
                    sorted(
                        prohibited_paths
                    )
                )
            )

        unknown = sorted(
            set(
                str(key)
                for key in value
            )
            - INSIGHT_PREFERENCE_FIELDS
        )

        if unknown:
            raise IntentValidationError(
                "insight_preferences contains unknown field(s): "
                + ", ".join(
                    unknown
                )
            )

        desired_depth = _normalize_enum(
            value.get(
                "desired_depth"
            )
        )

        if (
            desired_depth
            not in ALLOWED_INSIGHT_DEPTHS
        ):
            raise IntentValidationError(
                f"Unsupported desired_depth={desired_depth!r}."
            )

        preferred_patterns = [
            _normalize_enum(
                item
            )
            for item
            in _normalize_string_list(
                value.get(
                    "preferred_patterns"
                ),
                field_name=(
                    "insight_preferences.preferred_patterns"
                ),
            )
        ]

        preferred_patterns = list(
            dict.fromkeys(
                item
                for item
                in preferred_patterns
                if item
            )
        )

        invalid_preferred = sorted(
            set(
                preferred_patterns
            )
            - ALLOWED_INSIGHT_PATTERNS
        )

        if invalid_preferred:
            raise IntentValidationError(
                "Unsupported preferred insight pattern(s): "
                + ", ".join(
                    invalid_preferred
                )
            )

        avoid_patterns = [
            _normalize_enum(
                item
            )
            for item
            in _normalize_string_list(
                value.get(
                    "avoid_patterns"
                ),
                field_name=(
                    "insight_preferences.avoid_patterns"
                ),
            )
        ]

        avoid_patterns = list(
            dict.fromkeys(
                item
                for item
                in avoid_patterns
                if item
            )
        )

        invalid_avoid = sorted(
            set(
                avoid_patterns
            )
            - ALLOWED_AVOID_PATTERNS
        )

        if invalid_avoid:
            raise IntentValidationError(
                "Unsupported avoid insight pattern(s): "
                + ", ".join(
                    invalid_avoid
                )
            )

        analytical_lenses = (
            _normalize_string_list(
                value.get(
                    "analytical_lenses"
                ),
                field_name=(
                    "insight_preferences.analytical_lenses"
                ),
            )
        )

        positive_examples = (
            self._normalize_examples(
                value.get(
                    "positive_examples"
                ),
                field_name=(
                    "insight_preferences.positive_examples"
                ),
            )
        )

        negative_examples = (
            self._normalize_examples(
                value.get(
                    "negative_examples"
                ),
                field_name=(
                    "insight_preferences.negative_examples"
                ),
            )
        )

        return {
            "desired_depth": (
                desired_depth
            ),
            "preferred_patterns": (
                preferred_patterns
            ),
            "avoid_patterns": (
                avoid_patterns
            ),
            "analytical_lenses": (
                analytical_lenses
            ),
            "positive_examples": (
                positive_examples
            ),
            "negative_examples": (
                negative_examples
            ),
        }

    def _normalize_examples(
        self,
        value: Any,
        *,
        field_name: str,
    ) -> list[Dict[str, str]]:
        if value is None:
            return []

        if not isinstance(
            value,
            (list, tuple),
        ):
            raise IntentValidationError(
                f"{field_name} must be a list."
            )

        result: list[
            Dict[str, str]
        ] = []

        seen: Set[
            tuple[str, str]
        ] = set()

        for index, item in enumerate(
            value
        ):
            if isinstance(
                item,
                str,
            ):
                item = {
                    "text": item,
                    "reason": "",
                }

            if not isinstance(
                item,
                Mapping,
            ):
                raise IntentValidationError(
                    f"{field_name}[{index}] must be a string or object."
                )

            unknown = sorted(
                set(
                    str(key)
                    for key in item
                )
                - EXAMPLE_FIELDS
            )

            if unknown:
                raise IntentValidationError(
                    f"{field_name}[{index}] contains unknown field(s): "
                    + ", ".join(
                        unknown
                    )
                )

            text = _clean(
                item.get(
                    "text"
                )
            )
            reason = _clean(
                item.get(
                    "reason"
                )
            )

            if not text:
                raise IntentValidationError(
                    f"{field_name}[{index}].text cannot be empty."
                )

            key = (
                text,
                reason,
            )

            if key not in seen:
                seen.add(
                    key
                )
                result.append(
                    {
                        "text": text,
                        "reason": reason,
                    }
                )

        return result

    def _merge_insight_preferences(
        self,
        interpreted: Mapping[
            str,
            Any,
        ],
        explicit: Mapping[
            str,
            Any,
        ],
    ) -> Dict[str, Any]:
        """
        Merge parser-inferred preferences with explicitly supplied UI/user
        preferences.

        Explicit values have precedence because they are direct user input.
        Lists are unioned in stable order. Examples are preserved.
        """
        interpreted = (
            self._normalize_insight_preferences(
                interpreted
            )
        )
        explicit = (
            self._normalize_insight_preferences(
                explicit
            )
        )

        desired_depth = (
            explicit[
                "desired_depth"
            ]
            or interpreted[
                "desired_depth"
            ]
        )

        def merge_list(
            left: list[Any],
            right: list[Any],
        ) -> list[Any]:
            result = []

            for item in (
                left
                + right
            ):
                if item not in result:
                    result.append(
                        item
                    )

            return result

        return {
            "desired_depth": (
                desired_depth
            ),
            "preferred_patterns": (
                merge_list(
                    interpreted[
                        "preferred_patterns"
                    ],
                    explicit[
                        "preferred_patterns"
                    ],
                )
            ),
            "avoid_patterns": (
                merge_list(
                    interpreted[
                        "avoid_patterns"
                    ],
                    explicit[
                        "avoid_patterns"
                    ],
                )
            ),
            "analytical_lenses": (
                merge_list(
                    interpreted[
                        "analytical_lenses"
                    ],
                    explicit[
                        "analytical_lenses"
                    ],
                )
            ),
            "positive_examples": (
                merge_list(
                    interpreted[
                        "positive_examples"
                    ],
                    explicit[
                        "positive_examples"
                    ],
                )
            ),
            "negative_examples": (
                merge_list(
                    interpreted[
                        "negative_examples"
                    ],
                    explicit[
                        "negative_examples"
                    ],
                )
            ),
        }

    # ------------------------------------------------------------------
    # Prompt
    # ------------------------------------------------------------------

    def _build_prompt(
        self,
        *,
        request_text: str,
        context: Mapping[
            str,
            Any,
        ],
    ) -> str:
        return f"""
You are an intent-interpretation component for an evidence-grounded research
product.

Your task is ONLY to convert the user's request into a candidate research
intent and faithfully capture EXPLICIT preferences about what kind of insight
the user finds useful.

You do not authorize execution and you do not create governance policy.

User request:
{request_text}

Optional user/context information:
{json.dumps(dict(context), ensure_ascii=False, indent=2, default=str)}

Return exactly one JSON object with ONLY these fields:

{{
  "intent_type": "research",
  "research_question": "a faithful, specific research question",
  "analysis_mode": "research",
  "requested_output": "research_report",
  "constraints": {{}},
  "source_preferences": {{}},
  "insight_preferences": {{
    "desired_depth": "",
    "preferred_patterns": [],
    "avoid_patterns": [],
    "analytical_lenses": [],
    "positive_examples": [],
    "negative_examples": []
  }}
}}

Allowed desired_depth values:
standard, deep, very_deep, or empty when not explicitly stated.

Allowed preferred_patterns:
{json.dumps(sorted(ALLOWED_INSIGHT_PATTERNS), ensure_ascii=False)}

Allowed avoid_patterns:
{json.dumps(sorted(ALLOWED_AVOID_PATTERNS), ensure_ascii=False)}

Rules:
1. Preserve the user's actual research question and scope.
2. Do not invent entities, dates, geographies, or constraints.
3. Capture an insight preference only when the user explicitly states or
   demonstrates it.
4. A user-provided example of a good/bad insight belongs in positive_examples
   or negative_examples. It is NOT evidence about the research topic.
5. Do not infer a preference simply because an example happens to contain a
   certain domain.
6. Do not emit an intent_id; the service generates it deterministically.
7. Do not create policies, thresholds, gates, release decisions, model choices,
   refinement limits, Blueprints, or routing instructions.
8. Do not weaken grounding, red-team, or evidence requirements.
9. Do not add recommendations or strategic conclusions to the intent.
10. Do not add commentary outside the JSON object.
""".strip()

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _normalize_allowed_values(
        self,
        values: Iterable[str],
        *,
        field_name: str,
    ) -> Set[str]:
        normalized = {
            _normalize_enum(
                value
            )
            for value in values
            if _normalize_enum(
                value
            )
        }

        if not normalized:
            raise IntentValidationError(
                f"{field_name} cannot be empty."
            )

        return normalized