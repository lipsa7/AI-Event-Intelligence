"""
Deterministic execution-blueprint compiler.

Recommended location:
    services/blueprint_compiler.py

Role
----
Validated Intent
      +
Versioned Policy Snapshot
      +
Allowed User Preferences
      ↓
BlueprintCompiler
      ↓
Execution Blueprint
      ↓
PolicyEnforcementService / ModelRouterService / Pipeline

Core principle
--------------
LLMs may interpret user language into a candidate intent, but they do NOT
freely invent policies or execution blueprints.

The compiler deterministically combines:
    defaults
    < user preferences
    < analysis-mode policy
    < product governance
    < hard restrictions

Higher layers override lower layers.

This file does NOT:
- call an LLM;
- decide whether a report should be released;
- execute the research pipeline;
- measure grounding/reasoning quality;
- silently truncate structured evidence.

Expected policy snapshot
------------------------
{
    "policy_bundle_id": "POLICY-BUNDLE-001",
    "versions": {
        "product_governance": "3.0",
        "research_mode": "2.1",
        "hard_restrictions": "1.4"
    },

    "defaults": {
        "data_policy": {...},
        "execution_policy": {...},
        "reasoning_policy": {...},
        "quality_policy": {...},
        "selection_policy": {...},
        "runtime_policy": {...},
        "output_contract": {...},
        "escalation": {...}
    },

    "analysis_modes": {
        "research": {
            "reasoning_policy": {...},
            "output_contract": {...}
        }
    },

    "product_governance": {
        "quality_policy": {...}
    },

    "hard_restrictions": {
        "runtime_policy": {
            "allow_silent_truncation": false
        }
    }
}

Expected validated intent
-------------------------
{
    "intent_id": "INTENT-001",
    "intent_type": "research",
    "research_question": "What is changing underneath ...?",
    "analysis_mode": "research",
    "requested_output": "research_report",
    "constraints": {...},
    "source_preferences": {...}
}

Blueprint output
----------------
{
    "schema_version": "1.0",
    "compiler_version": "1.0",
    "blueprint_id": "BLUEPRINT-...",
    "compiled_at": "...",

    "intent": {...},

    "policy_bundle": {
        "policy_bundle_id": "...",
        "versions": {...}
    },

    "data_policy": {...},
    "execution_policy": {...},
    "reasoning_policy": {...},
    "quality_policy": {...},
    "selection_policy": {...},
    "runtime_policy": {...},
    "output_contract": {...},
    "escalation": {...},

    "provenance": {
        "precedence": [...],
        "analysis_mode": "...",
        "user_preference_keys_applied": [...]
    }
}

Important architecture distinction
----------------------------------
BlueprintCompiler:
    compiles what SHOULD happen.

PolicyEnforcementService:
    deterministically checks whether runtime behavior/output CONFORMS.

Evaluation:
    measures AI quality.

AuditReceiptService:
    records what happened.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Mapping, Optional, Sequence, Set


BLUEPRINT_SCHEMA_VERSION = "1.0"
COMPILER_VERSION = "1.0"

BLUEPRINT_SECTIONS = (
    "data_policy",
    "execution_policy",
    "reasoning_policy",
    "quality_policy",
    "selection_policy",
    "runtime_policy",
    "output_contract",
    "escalation",
)

PRECEDENCE_LOW_TO_HIGH = (
    "defaults",
    "user_preferences",
    "analysis_mode",
    "product_governance",
    "hard_restrictions",
)

ALLOWED_GATE_OPERATORS = {
    ">",
    ">=",
    "<",
    "<=",
    "==",
    "!=",
}

ALLOWED_GATE_TYPES = {
    "hard",
    "soft",
    "monitor",
}

ALLOWED_GATE_ACTIONS = {
    "allow",
    "block",
    "review",
    "retry",
    "fallback",
    "remove_section",
    "request_more_evidence",
}

# User preferences may tune these blueprint sections, but only where a higher
# policy layer does not override them.
USER_PREFERENCE_ALLOWED_SECTIONS = {
    "data_policy",
    "execution_policy",
    "reasoning_policy",
    "selection_policy",
    "runtime_policy",
    "output_contract",
}

# These are compiler-level structural invariants, not product-policy defaults.
REQUIRED_OUTPUT_CONTRACT_FIELDS = {
    "required_sections",
}

REQUIRED_INTENT_FIELDS = {
    "intent_id",
    "intent_type",
    "research_question",
    "analysis_mode",
}


# Canonical report sections used when adapting the newer PolicyRegistry snapshot
# to the legacy BlueprintCompiler structural contract.
DEFAULT_RESEARCH_REPORT_SECTIONS = (
    "event_landscape",
    "connections",
    "what_is_happening_underneath",
    "hidden_insights",
    "second_order_effects",
    "counterevidence_and_alternative_explanations",
    "what_to_watch",
    "evidence_trail",
    "critical_unknowns",
)

CANONICAL_POLICY_MODULE_KEYS = (
    "cost_policy",
    "execution_profile",
    "evidence_discovery_policy",
    "model_routing_policy",
    "reasoning_context_policy",
    "evidence_policy",
    "prospective_reasoning_policy",
    "release_policy",
)


class BlueprintCompilerError(RuntimeError):
    """Base error raised by BlueprintCompiler."""


class BlueprintValidationError(BlueprintCompilerError):
    """Raised when intent, policy, or compiled blueprint is invalid."""


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


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


def canonical_json_bytes(
    payload: Mapping[str, Any],
) -> bytes:
    return json.dumps(
        _json_safe(dict(payload)),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(
    payload: bytes | str,
) -> str:
    if isinstance(payload, str):
        payload = payload.encode("utf-8")

    return hashlib.sha256(
        payload
    ).hexdigest()


def _deep_merge(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Deterministic recursive merge.

    Mapping values merge recursively.
    Scalars/lists replace the lower-precedence value entirely.

    Lists are intentionally NOT concatenated because policy list-merging can
    create ambiguous execution semantics.
    """
    result = deepcopy(
        dict(base)
    )

    for key, override_value in override.items():
        if (
            key in result
            and isinstance(result[key], Mapping)
            and isinstance(override_value, Mapping)
        ):
            result[key] = _deep_merge(
                result[key],
                override_value,
            )
        else:
            result[key] = deepcopy(
                override_value
            )

    return result


def _filter_blueprint_sections(
    value: Any,
) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}

    return {
        key: deepcopy(section)
        for key, section in value.items()
        if key in BLUEPRINT_SECTIONS
    }


def _filter_user_preferences(
    preferences: Any,
) -> Dict[str, Any]:
    if not isinstance(preferences, Mapping):
        return {}

    return {
        key: deepcopy(value)
        for key, value in preferences.items()
        if key in USER_PREFERENCE_ALLOWED_SECTIONS
    }


def _flatten_leaf_paths(
    payload: Mapping[str, Any],
    *,
    prefix: str = "",
) -> Set[str]:
    result: Set[str] = set()

    for key, value in payload.items():
        path = (
            f"{prefix}.{key}"
            if prefix
            else str(key)
        )

        if isinstance(value, Mapping):
            nested = _flatten_leaf_paths(
                value,
                prefix=path,
            )

            if nested:
                result.update(nested)
            else:
                result.add(path)
        else:
            result.add(path)

    return result


def _normalize_quality_gate_aliases(
    layer: Any,
) -> Dict[str, Any]:
    """Normalize PolicyRegistry gate field aliases for compiler v1."""
    if not isinstance(layer, Mapping):
        return {}

    normalized = deepcopy(dict(layer))
    quality = normalized.get("quality_policy")

    if isinstance(quality, Mapping):
        quality = deepcopy(dict(quality))
        gates = quality.get("gates")

        if isinstance(gates, list):
            normalized_gates = []

            for gate in gates:
                if isinstance(gate, Mapping):
                    gate_payload = deepcopy(dict(gate))
                    if (
                        "on_failure" not in gate_payload
                        and "on_fail" in gate_payload
                    ):
                        gate_payload["on_failure"] = gate_payload["on_fail"]
                    normalized_gates.append(gate_payload)
                else:
                    normalized_gates.append(deepcopy(gate))

            quality["gates"] = normalized_gates

        normalized["quality_policy"] = quality

    return normalized


def _normalize_policy_snapshot_contract(
    policy_snapshot: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Accept both BlueprintCompiler v1 snapshots and the newer
    PolicyRegistryService snapshot shape.

    The registry's newer shape exposes `analysis_mode_policy` and canonical
    policy modules directly. The older compiler expects `defaults` and
    `analysis_modes`. This adapter preserves policy precedence without
    weakening governance.
    """
    snapshot = deepcopy(dict(policy_snapshot))

    if not _clean(snapshot.get("policy_bundle_id")):
        snapshot["policy_bundle_id"] = (
            _clean(snapshot.get("version"))
            or _clean(snapshot.get("policy_snapshot_id"))
        )

    defaults = snapshot.get("defaults")
    if not isinstance(defaults, Mapping):
        defaults = {section: {} for section in BLUEPRINT_SECTIONS}
    else:
        defaults = deepcopy(dict(defaults))
        for section in BLUEPRINT_SECTIONS:
            defaults.setdefault(section, {})

    output_contract = defaults.get("output_contract")
    if not isinstance(output_contract, Mapping):
        output_contract = {}
    else:
        output_contract = deepcopy(dict(output_contract))

    output_contract.setdefault(
        "required_sections",
        list(DEFAULT_RESEARCH_REPORT_SECTIONS),
    )
    defaults["output_contract"] = output_contract
    snapshot["defaults"] = _normalize_quality_gate_aliases(defaults)

    analysis_modes = snapshot.get("analysis_modes")
    if not isinstance(analysis_modes, Mapping):
        analysis_mode = _clean(snapshot.get("analysis_mode")) or "research"
        mode_policy = snapshot.get("analysis_mode_policy")
        analysis_modes = {
            analysis_mode: (
                deepcopy(dict(mode_policy))
                if isinstance(mode_policy, Mapping)
                else {}
            )
        }
    else:
        analysis_modes = deepcopy(dict(analysis_modes))

    snapshot["analysis_modes"] = {
        str(mode): _normalize_quality_gate_aliases(layer)
        for mode, layer in analysis_modes.items()
    }

    snapshot["product_governance"] = _normalize_quality_gate_aliases(
        snapshot.get("product_governance") or {}
    )
    snapshot["hard_restrictions"] = _normalize_quality_gate_aliases(
        snapshot.get("hard_restrictions") or {}
    )

    return snapshot


def _validated_intent_preferences(
    validated_intent: Mapping[str, Any],
) -> Dict[str, Any]:
    """Translate validated intent insight preferences into blueprint sections."""
    insight_preferences = validated_intent.get("insight_preferences")
    if not isinstance(insight_preferences, Mapping):
        return {}

    if "research_depth" in insight_preferences:
        raise BlueprintValidationError(
            "research_depth is execution policy and cannot be changed "
            "through insight_preferences."
        )

    return {
        "reasoning_policy": deepcopy(dict(insight_preferences))
    }


def _explicit_user_preferences(
    explicit_preferences: Optional[Mapping[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(explicit_preferences, Mapping):
        return {}

    explicit = deepcopy(dict(explicit_preferences))
    execution_policy = explicit.get("execution_policy")
    if (
        isinstance(execution_policy, Mapping)
        and "research_depth" in execution_policy
    ):
        raise BlueprintValidationError(
            "research_depth cannot be changed through user_preferences."
        )

    return _filter_user_preferences(explicit)


def _semantic_blueprint_payload(
    blueprint: Mapping[str, Any],
) -> Dict[str, Any]:
    """
    Fields used to compute blueprint identity.

    `compiled_at` and `blueprint_id` are excluded so the same semantic blueprint
    compiles to the same stable ID across runs.
    """
    return {
        key: value
        for key, value in blueprint.items()
        if key not in {
            "compiled_at",
            "blueprint_id",
        }
    }


class BlueprintCompiler:
    """
    Deterministically compile a validated intent and policy snapshot.

    The compiler is intentionally strict: missing required policy sections or
    malformed quality gates fail closed instead of producing a partial,
    under-governed blueprint.
    """

    def __init__(
        self,
        *,
        compiler_version: str = COMPILER_VERSION,
    ) -> None:
        self.compiler_version = _clean(
            compiler_version
        )

        if not self.compiler_version:
            raise BlueprintValidationError(
                "compiler_version cannot be empty."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(
        self,
        *,
        validated_intent: Mapping[str, Any],
        policy_snapshot: Mapping[str, Any],
        user_preferences: Optional[
            Mapping[str, Any]
        ] = None,
        compiled_at: str = "",
    ) -> Dict[str, Any]:
        """
        Compile one deterministic Execution Blueprint.

        Accepts both the legacy compiler snapshot contract and the newer
        PolicyRegistryService snapshot contract. Policy precedence remains:
            defaults
            < user preferences
            < analysis-mode policy
            < product governance
            < hard restrictions
        """
        self._validate_intent(validated_intent)

        normalized_snapshot = _normalize_policy_snapshot_contract(
            policy_snapshot
        )
        self._validate_policy_snapshot(normalized_snapshot)

        intent = _json_safe(dict(validated_intent))
        analysis_mode = _clean(validated_intent.get("analysis_mode"))

        defaults = _filter_blueprint_sections(
            normalized_snapshot.get("defaults", {})
        )
        preferences = _explicit_user_preferences(
            user_preferences
        )
        intent_preferences = _validated_intent_preferences(
            validated_intent
        )

        analysis_modes = normalized_snapshot.get("analysis_modes", {})
        analysis_mode_policy: Dict[str, Any] = {}
        if isinstance(analysis_modes, Mapping):
            candidate = analysis_modes.get(analysis_mode, {})
            if isinstance(candidate, Mapping):
                analysis_mode_policy = _filter_blueprint_sections(candidate)

        product_governance = _filter_blueprint_sections(
            normalized_snapshot.get("product_governance", {})
        )
        hard_restrictions = _filter_blueprint_sections(
            normalized_snapshot.get("hard_restrictions", {})
        )

        merged: Dict[str, Any] = {}
        for layer in (
            defaults,
            preferences,
            analysis_mode_policy,
            intent_preferences,
            product_governance,
            hard_restrictions,
        ):
            merged = _deep_merge(merged, layer)

        canonical_modules = {
            key: _json_safe(normalized_snapshot[key])
            for key in CANONICAL_POLICY_MODULE_KEYS
            if key in normalized_snapshot
        }

        blueprint: Dict[str, Any] = {
            "schema_version": BLUEPRINT_SCHEMA_VERSION,
            "compiler_version": self.compiler_version,
            "compiled_at": _clean(compiled_at) or _utc_now_iso(),
            "intent": intent,
            "policy_bundle": {
                "policy_bundle_id": _clean(
                    normalized_snapshot.get("policy_bundle_id")
                ),
                "versions": _json_safe(
                    dict(normalized_snapshot.get("versions", {}))
                ),
            },
            **merged,
            **canonical_modules,
            "policy_snapshot_id": _clean(
                normalized_snapshot.get("policy_snapshot_id")
            ),
            "policy_snapshot_fingerprint": _clean(
                normalized_snapshot.get("policy_snapshot_fingerprint")
            ),
            "provenance": {
                "precedence_low_to_high": list(PRECEDENCE_LOW_TO_HIGH),
                "analysis_mode": analysis_mode,
                "user_preference_keys_applied": sorted(
                    _flatten_leaf_paths(
                        _deep_merge(preferences, intent_preferences)
                    )
                ),
            },
        }

        self._validate_compiled_blueprint(blueprint)

        blueprint["blueprint_id"] = (
            "BLUEPRINT-"
            + sha256_hex(
                canonical_json_bytes(
                    _semantic_blueprint_payload(blueprint)
                )
            )[:24].upper()
        )
        return blueprint

    def fingerprint(
        self,
        blueprint: Mapping[str, Any],
    ) -> str:
        """
        Semantic fingerprint of the blueprint, excluding compile timestamp/ID.
        """
        return sha256_hex(
            canonical_json_bytes(
                _semantic_blueprint_payload(
                    blueprint
                )
            )
        )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _validate_intent(
        self,
        intent: Mapping[str, Any],
    ) -> None:
        if not isinstance(
            intent,
            Mapping,
        ):
            raise BlueprintValidationError(
                "validated_intent must be an object."
            )

        missing = sorted(
            field
            for field in REQUIRED_INTENT_FIELDS
            if not _clean(
                intent.get(field)
            )
        )

        if missing:
            raise BlueprintValidationError(
                "Validated intent is missing required field(s): "
                + ", ".join(
                    missing
                )
            )

    def _validate_policy_snapshot(
        self,
        policy_snapshot: Mapping[str, Any],
    ) -> None:
        if not isinstance(
            policy_snapshot,
            Mapping,
        ):
            raise BlueprintValidationError(
                "policy_snapshot must be an object."
            )

        if not _clean(
            policy_snapshot.get(
                "policy_bundle_id"
            )
        ):
            raise BlueprintValidationError(
                "policy_snapshot.policy_bundle_id is required."
            )

        versions = policy_snapshot.get(
            "versions"
        )

        if not isinstance(
            versions,
            Mapping,
        ) or not versions:
            raise BlueprintValidationError(
                "policy_snapshot.versions must be a non-empty object."
            )

        defaults = _filter_blueprint_sections(
            policy_snapshot.get(
                "defaults",
                {},
            )
        )

        missing_defaults = [
            section
            for section in BLUEPRINT_SECTIONS
            if section not in defaults
        ]

        if missing_defaults:
            raise BlueprintValidationError(
                "Policy defaults are missing required blueprint section(s): "
                + ", ".join(
                    missing_defaults
                )
            )

    def _validate_compiled_blueprint(
        self,
        blueprint: Mapping[str, Any],
    ) -> None:
        missing_sections = [
            section
            for section in BLUEPRINT_SECTIONS
            if (
                section not in blueprint
                or not isinstance(
                    blueprint.get(
                        section
                    ),
                    Mapping,
                )
            )
        ]

        if missing_sections:
            raise BlueprintValidationError(
                "Compiled blueprint is missing structured section(s): "
                + ", ".join(
                    missing_sections
                )
            )

        self._validate_runtime_policy(
            blueprint[
                "runtime_policy"
            ]
        )

        self._validate_quality_policy(
            blueprint[
                "quality_policy"
            ]
        )

        self._validate_output_contract(
            blueprint[
                "output_contract"
            ]
        )

    def _validate_runtime_policy(
        self,
        runtime_policy: Mapping[str, Any],
    ) -> None:
        # Architectural invariant: validated structured records must never be
        # silently lost because of serialization/context limits.
        if runtime_policy.get(
            "allow_silent_truncation"
        ) is True:
            raise BlueprintValidationError(
                "runtime_policy.allow_silent_truncation=true violates the "
                "record-preserving execution contract."
            )

        strategy = _clean(
            runtime_policy.get(
                "context_overflow_strategy"
            )
        ).casefold()

        if strategy and strategy not in {
            "batch",
            "split_retry",
            "fail",
            "batch_then_consolidate",
        }:
            raise BlueprintValidationError(
                "Unsupported runtime_policy.context_overflow_strategy="
                f"{strategy!r}."
            )

    def _validate_quality_policy(
        self,
        quality_policy: Mapping[str, Any],
    ) -> None:
        gates = quality_policy.get(
            "gates",
            []
        )

        if gates is None:
            gates = []

        if not isinstance(
            gates,
            list,
        ):
            raise BlueprintValidationError(
                "quality_policy.gates must be a list."
            )

        seen_gate_ids: Set[str] = set()

        for index, gate in enumerate(
            gates
        ):
            if not isinstance(
                gate,
                Mapping,
            ):
                raise BlueprintValidationError(
                    f"quality_policy.gates[{index}] must be an object."
                )

            gate_id = _clean(
                gate.get(
                    "gate_id"
                )
            )

            metric = _clean(
                gate.get(
                    "metric"
                )
            )

            operator = _clean(
                gate.get(
                    "operator"
                )
            )

            gate_type = _clean(
                gate.get(
                    "gate_type"
                )
            ).casefold()

            action = _clean(
                gate.get(
                    "on_failure"
                )
            ).casefold()

            if not gate_id:
                raise BlueprintValidationError(
                    f"quality_policy.gates[{index}].gate_id is required."
                )

            if gate_id in seen_gate_ids:
                raise BlueprintValidationError(
                    f"Duplicate quality gate_id={gate_id!r}."
                )

            seen_gate_ids.add(
                gate_id
            )

            if not metric:
                raise BlueprintValidationError(
                    f"quality_policy.gates[{index}].metric is required."
                )

            if operator not in ALLOWED_GATE_OPERATORS:
                raise BlueprintValidationError(
                    f"Unsupported gate operator={operator!r}."
                )

            if "threshold" not in gate:
                raise BlueprintValidationError(
                    f"quality_policy.gates[{index}].threshold is required."
                )

            if gate_type not in ALLOWED_GATE_TYPES:
                raise BlueprintValidationError(
                    f"Unsupported gate_type={gate_type!r}."
                )

            if action not in ALLOWED_GATE_ACTIONS:
                raise BlueprintValidationError(
                    f"Unsupported on_failure action={action!r}."
                )

    def _validate_output_contract(
        self,
        output_contract: Mapping[str, Any],
    ) -> None:
        missing = sorted(
            field
            for field in REQUIRED_OUTPUT_CONTRACT_FIELDS
            if field not in output_contract
        )

        if missing:
            raise BlueprintValidationError(
                "output_contract is missing required field(s): "
                + ", ".join(
                    missing
                )
            )

        required_sections = output_contract.get(
            "required_sections"
        )

        if (
            not isinstance(
                required_sections,
                list,
            )
            or not all(
                _clean(section)
                for section in required_sections
            )
        ):
            raise BlueprintValidationError(
                "output_contract.required_sections must be a non-empty-string list."
            )