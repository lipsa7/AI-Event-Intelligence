"""
Deterministic local-first model router for the Event Intelligence pipeline.

Recommended location:
    services/model_router_service.py

Role
----
Validated Intent
      +
Compiled Blueprint / PolicyRegistry
      +
task + execution context
      +
local model catalog / availability
      ↓
ModelRouterService
      ↓
deterministic ModelRouteDecision
      ↓
LLMService(model_name=decision["model_name"])
      ↓
agent/service execution

Core principle
--------------
MODEL SELECTION IS POLICY, NOT AGENT AUTONOMY.

Agents may request a task such as:
    strategic_inference
    prospective_reasoning
    grounding
    red_team
    feedback_interpretation

but they do not choose the model.

This service:
- chooses only among explicitly configured models;
- respects Blueprint/cost/locality policy;
- uses the same code for Fast and Deep;
- supports bounded model escalation;
- never performs an LLM call itself;
- never searches the web;
- never modifies research_depth;
- never treats user preference or model quality as evidence;
- never authorizes report release.

Free/local-first contract
-------------------------
The project has zero mandatory external cost.

By default:
- only provider="ollama" models are eligible;
- paid models are ineligible;
- remote/hosted model classes are ineligible;
- no API key is required;
- no model is auto-downloaded.

A caller may configure multiple locally installed Ollama models. If only one
model is configured, every eligible task routes to that model and escalation
fails closed when no stronger model exists.

Typical setup
-------------
router = ModelRouterService(
    model_catalog={
        "local_fast": {
            "model_name": "qwen3:8b",
            "provider": "ollama",
            "tier": "small",
            "capabilities": ["json", "reasoning", "evidence_classification"],
            "enabled": True,
            "paid": False,
            "local": True
        },
        "local_reasoning": {
            "model_name": "qwen3:14b",
            "provider": "ollama",
            "tier": "medium",
            "capabilities": [
                "json",
                "reasoning",
                "strong_reasoning",
                "evidence_classification",
                "long_context"
            ],
            "enabled": True,
            "paid": False,
            "local": True
        }
    }
)

decision = router.route(
    task="strategic_inference",
    blueprint=blueprint,
    routing_context={"candidate_count": 6}
)

llm = router.create_llm_service(
    decision,
    base_url="http://127.0.0.1:11434"
)

Task coverage
-------------
Core task families include:

    extraction
    cluster_labeling
    evidence_review
    evidence_query_generation
    candidate_source_triage
    constraint_analysis
    strategic_inference
    prospective_reasoning
    mechanism_analogy_analysis
    insight_quality
    grounding
    red_team
    feedback_interpretation
    report_synthesis

Model escalation
----------------
A downstream component may recommend `model_escalation`, but this service
authorizes the actual route.

Escalation is bounded by compiled policy and may occur only when:
- escalation is explicitly requested;
- policy allows escalation;
- the current route is known;
- a strictly stronger eligible model exists;
- configured escalation limits are not exceeded.

Escalation never lowers evidence/grounding standards.

Research depth
--------------
research_depth is an execution-profile input (fast/deep), not something the
router writes.

Fast:
    prefer the least expensive/local model that satisfies task capability and
    policy requirements, unless the task requires a stronger class.

Deep:
    may prefer a stronger eligible model for high-reasoning tasks.

Both use the same routing algorithm.

No private chain-of-thought is requested, persisted or returned.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from copy import deepcopy
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Mapping, Optional, Sequence, Set


SERVICE_VERSION = "model-router-service-v2.0"
OUTPUT_SCHEMA_VERSION = "model-route-schema-v2.0"

ALLOWED_PROVIDERS = {
    "ollama",
    "local",
}

ALLOWED_TIERS = (
    "tiny",
    "small",
    "medium",
    "large",
    "xlarge",
)

TIER_RANK = {
    tier: index
    for index, tier in enumerate(ALLOWED_TIERS)
}

ALLOWED_RESEARCH_DEPTHS = {
    "fast",
    "deep",
    "balanced",
    "unknown",
}

ALLOWED_TASKS = {
    "extraction",
    "cluster_labeling",
    "evidence_review",
    "evidence_query_generation",
    "candidate_source_triage",
    "constraint_analysis",
    "strategic_inference",
    "prospective_reasoning",
    "mechanism_analogy_analysis",
    "insight_quality",
    "grounding",
    "red_team",
    "feedback_interpretation",
    "report_synthesis",
}

# Minimum capabilities are intentionally generic and domain-neutral.
TASK_REQUIREMENTS = {
    "extraction": {
        "required_capabilities": {"json"},
        "minimum_tier": "tiny",
        "reasoning_intensity": 1,
        "prefer_speed": True,
    },
    "cluster_labeling": {
        "required_capabilities": {"json"},
        "minimum_tier": "tiny",
        "reasoning_intensity": 1,
        "prefer_speed": True,
    },
    "evidence_review": {
        "required_capabilities": {"json", "evidence_classification"},
        "minimum_tier": "small",
        "reasoning_intensity": 2,
        "prefer_speed": True,
    },
    "evidence_query_generation": {
        "required_capabilities": {"json", "reasoning"},
        "minimum_tier": "small",
        "reasoning_intensity": 2,
        "prefer_speed": True,
    },
    "candidate_source_triage": {
        "required_capabilities": {"json", "evidence_classification"},
        "minimum_tier": "small",
        "reasoning_intensity": 2,
        "prefer_speed": True,
    },
    "constraint_analysis": {
        "required_capabilities": {"json", "reasoning"},
        "minimum_tier": "small",
        "reasoning_intensity": 3,
        "prefer_speed": False,
    },
    "strategic_inference": {
        "required_capabilities": {"json", "reasoning"},
        "minimum_tier": "small",
        "reasoning_intensity": 4,
        "prefer_speed": False,
    },
    "prospective_reasoning": {
        "required_capabilities": {"json", "reasoning", "strong_reasoning"},
        "minimum_tier": "medium",
        "reasoning_intensity": 5,
        "prefer_speed": False,
    },
    "mechanism_analogy_analysis": {
        "required_capabilities": {"json", "reasoning", "strong_reasoning"},
        "minimum_tier": "medium",
        "reasoning_intensity": 5,
        "prefer_speed": False,
    },
    "insight_quality": {
        "required_capabilities": {"json", "reasoning"},
        "minimum_tier": "small",
        "reasoning_intensity": 3,
        "prefer_speed": False,
    },
    "grounding": {
        "required_capabilities": {"json", "evidence_classification"},
        "minimum_tier": "small",
        "reasoning_intensity": 4,
        "prefer_speed": False,
    },
    "red_team": {
        "required_capabilities": {"json", "reasoning", "strong_reasoning"},
        "minimum_tier": "medium",
        "reasoning_intensity": 5,
        "prefer_speed": False,
    },
    "feedback_interpretation": {
        "required_capabilities": {"json"},
        "minimum_tier": "tiny",
        "reasoning_intensity": 1,
        "prefer_speed": True,
    },
    "report_synthesis": {
        "required_capabilities": {"json", "reasoning"},
        "minimum_tier": "small",
        "reasoning_intensity": 3,
        "prefer_speed": False,
    },
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


class ModelRouterError(RuntimeError):
    """Base model-router error."""


class ModelRouterValidationError(ModelRouterError):
    """Invalid configuration or routing request."""


class NoEligibleModelError(ModelRouterError):
    """No configured model satisfies the route policy."""


class ModelEscalationDeniedError(ModelRouterError):
    """Explicit escalation could not be legally satisfied."""


@dataclass(frozen=True)
class ModelSpec:
    model_id: str
    model_name: str
    provider: str
    tier: str

    capabilities: tuple[str, ...] = field(default_factory=tuple)

    enabled: bool = True
    local: bool = True
    paid: bool = False

    max_context_tokens: Optional[int] = None
    speed_rank: int = 100
    quality_rank: int = 100

    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


@dataclass(frozen=True)
class ModelRouteDecision:
    route_id: str
    task: str
    model_id: str
    model_name: str
    provider: str
    tier: str
    capabilities: tuple[str, ...]

    research_depth_observed: str
    escalation_requested: bool
    escalation_applied: bool
    previous_model_id: str

    reason_codes: tuple[str, ...]
    eligible_model_ids: tuple[str, ...]
    rejected_models: tuple[Mapping[str, Any], ...]

    policy_snapshot: Mapping[str, Any]
    routing_context_fingerprint: str

    authorizes_release: bool = False
    modifies_research_depth: bool = False
    model_call_performed: bool = False
    paid_model_selected: bool = False
    private_chain_of_thought_stored: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(asdict(self))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return " ".join(str(value).split()).strip()


def _enum(value: Any) -> str:
    text = _clean(value).casefold()
    text = text.replace("-", "_").replace(" ", "_")
    import re

    text = re.sub(r"[^a-z0-9_]+", "", text)

    while "__" in text:
        text = text.replace("__", "_")

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


def _sha256(value: Any) -> str:
    if isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = json.dumps(
            _json_safe(value),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")

    return hashlib.sha256(payload).hexdigest()


def _stable_id(
    prefix: str,
    payload: Any,
    *,
    length: int = 24,
) -> str:
    return (
        f"{_clean(prefix).upper()}-"
        + _sha256(payload)[:length].upper()
    )


def _string_set(value: Any) -> set[str]:
    if value is None:
        return set()

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, (list, tuple, set)):
        return set()

    return {
        _enum(item)
        for item in value
        if _enum(item)
    }


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _enum(key)
            current = f"{path}.{key}" if path else str(key)

            if normalized in PROHIBITED_PRIVATE_REASONING_FIELDS:
                return current

            nested = _contains_prohibited_field(
                item,
                current,
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


def _strict_bool(
    value: Any,
    *,
    name: str,
) -> bool:
    if isinstance(value, bool):
        return value

    raise ModelRouterValidationError(
        f"{name} must be boolean."
    )


def _optional_positive_int(
    value: Any,
    *,
    name: str,
) -> Optional[int]:
    if value is None or value == "":
        return None

    if isinstance(value, bool):
        raise ModelRouterValidationError(
            f"{name} must be an integer, not bool."
        )

    if isinstance(value, float) and not value.is_integer():
        raise ModelRouterValidationError(
            f"{name} must be an integer."
        )

    try:
        numeric = int(value)
    except (TypeError, ValueError) as exc:
        raise ModelRouterValidationError(
            f"{name} must be an integer."
        ) from exc

    if numeric <= 0:
        raise ModelRouterValidationError(
            f"{name} must be > 0."
        )

    return numeric


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------


class ModelRouterService:
    """
    Deterministic policy-bound local model router.
    """

    def __init__(
        self,
        *,
        model_catalog: Optional[Mapping[str, Mapping[str, Any]]] = None,
        availability_checker: Optional[Callable[[str], bool]] = None,
        default_model_name: str = "",
    ) -> None:
        self.availability_checker = availability_checker

        if availability_checker is not None and not callable(
            availability_checker
        ):
            raise ModelRouterValidationError(
                "availability_checker must be callable."
            )

        if model_catalog is None:
            model_catalog = self._default_catalog(
                default_model_name=default_model_name
            )

        self._catalog = self._normalize_catalog(
            model_catalog
        )

        if not self._catalog:
            raise ModelRouterValidationError(
                "model_catalog cannot be empty."
            )

        self._last_decision: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def catalog_snapshot(self) -> Dict[str, Dict[str, Any]]:
        return {
            model_id: spec.to_dict()
            for model_id, spec in sorted(
                self._catalog.items()
            )
        }

    def get_last_decision(self) -> Dict[str, Any]:
        return deepcopy(
            self._last_decision
        )

    def route(
        self,
        *,
        task: str,
        blueprint: Optional[Mapping[str, Any]] = None,
        routing_context: Optional[Mapping[str, Any]] = None,
        available_model_names: Optional[Sequence[str]] = None,
        current_model_id: str = "",
        escalation_request: bool = False,
        escalation_reason: str = "",
        escalation_count: int = 0,
    ) -> Dict[str, Any]:
        """
        Select one eligible model deterministically.

        Parameters
        ----------
        task:
            One ALLOWED_TASKS value.

        blueprint:
            Compiled Blueprint. Reads model_routing_policy, cost_policy and
            execution policy. Does not mutate any of them.

        routing_context:
            Non-secret operational context such as estimated token count,
            candidate count, required capabilities or JSON requirement.

        available_model_names:
            Optional explicit local inventory from LLMService.list_models().
            When provided, models absent from the inventory are ineligible.

        current_model_id / escalation_request:
            Used for bounded, strictly stronger escalation.
        """
        task = _enum(task)

        if task not in ALLOWED_TASKS:
            raise ModelRouterValidationError(
                f"Unsupported routing task={task!r}."
            )

        blueprint = (
            blueprint
            if isinstance(blueprint, Mapping)
            else {}
        )

        routing_context = (
            routing_context
            if isinstance(routing_context, Mapping)
            else {}
        )

        for name, value in (
            ("blueprint", blueprint),
            ("routing_context", routing_context),
        ):
            prohibited = _contains_prohibited_field(
                value
            )

            if prohibited:
                raise ModelRouterValidationError(
                    f"{name} contains prohibited private/raw reasoning field "
                    f"at {prohibited!r}."
                )

        if not isinstance(escalation_request, bool):
            raise ModelRouterValidationError(
                "escalation_request must be boolean."
            )

        if isinstance(escalation_count, bool):
            raise ModelRouterValidationError(
                "escalation_count must be integer, not bool."
            )

        try:
            escalation_count = int(
                escalation_count
            )
        except (TypeError, ValueError) as exc:
            raise ModelRouterValidationError(
                "escalation_count must be integer."
            ) from exc

        if escalation_count < 0:
            raise ModelRouterValidationError(
                "escalation_count must be >= 0."
            )

        policy = self._routing_policy(
            blueprint
        )

        research_depth = self._research_depth(
            blueprint
        )

        task_requirement = self._task_requirement(
            task=task,
            policy=policy,
            routing_context=routing_context,
        )

        explicit_available_names = (
            {
                _clean(name)
                for name in available_model_names
                if _clean(name)
            }
            if available_model_names is not None
            else None
        )

        eligible = []
        rejected = []

        for model_id, spec in sorted(
            self._catalog.items()
        ):
            rejection_reasons = self._model_rejection_reasons(
                spec=spec,
                task=task,
                requirement=task_requirement,
                policy=policy,
                explicit_available_names=explicit_available_names,
            )

            if rejection_reasons:
                rejected.append(
                    {
                        "model_id": model_id,
                        "model_name": spec.model_name,
                        "reasons": rejection_reasons,
                    }
                )
            else:
                eligible.append(
                    spec
                )

        if not eligible:
            raise NoEligibleModelError(
                self._no_eligible_message(
                    task=task,
                    rejected=rejected,
                )
            )

        current_model_id = _clean(
            current_model_id
        )

        selected: Optional[
            ModelSpec
        ] = None

        reason_codes = []

        if escalation_request:
            selected = self._select_escalation(
                eligible=eligible,
                current_model_id=current_model_id,
                policy=policy,
                escalation_count=escalation_count,
            )
            reason_codes.extend(
                [
                    "explicit_escalation_requested",
                    "strictly_stronger_local_model_selected",
                ]
            )

        else:
            selected = self._select_base_route(
                eligible=eligible,
                task=task,
                requirement=task_requirement,
                research_depth=research_depth,
                policy=policy,
            )

            if research_depth == "fast":
                reason_codes.append(
                    "fast_profile_prefers_smallest_sufficient_model"
                )
            elif research_depth == "deep":
                reason_codes.append(
                    "deep_profile_may_prefer_stronger_reasoning_model"
                )
            else:
                reason_codes.append(
                    "balanced_or_unknown_profile_uses_task_sufficiency"
                )

            if task_requirement[
                "required_capabilities"
            ]:
                reason_codes.append(
                    "task_capability_requirements_satisfied"
                )

        if selected is None:
            raise NoEligibleModelError(
                "No model selected despite eligible catalog."
            )

        route_payload = {
            "task": task,
            "model_id": selected.model_id,
            "model_name": selected.model_name,
            "provider": selected.provider,
            "tier": selected.tier,
            "research_depth": research_depth,
            "escalation_request": escalation_request,
            "current_model_id": current_model_id,
            "policy": policy,
            "routing_context_fingerprint": _sha256(
                routing_context
            ),
        }

        decision = ModelRouteDecision(
            route_id=_stable_id(
                "MODELROUTE",
                route_payload,
            ),
            task=task,
            model_id=selected.model_id,
            model_name=selected.model_name,
            provider=selected.provider,
            tier=selected.tier,
            capabilities=selected.capabilities,
            research_depth_observed=research_depth,
            escalation_requested=escalation_request,
            escalation_applied=escalation_request,
            previous_model_id=current_model_id,
            reason_codes=tuple(
                reason_codes
            ),
            eligible_model_ids=tuple(
                spec.model_id
                for spec in eligible
            ),
            rejected_models=tuple(
                deepcopy(
                    rejected
                )
            ),
            policy_snapshot=deepcopy(
                policy
            ),
            routing_context_fingerprint=_sha256(
                routing_context
            ),
            authorizes_release=False,
            modifies_research_depth=False,
            model_call_performed=False,
            paid_model_selected=bool(
                selected.paid
            ),
            private_chain_of_thought_stored=False,
        )

        result = decision.to_dict()

        result["metadata"] = {
            "service_version": SERVICE_VERSION,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "task_requirement": _json_safe(
                task_requirement
            ),
            "escalation_reason": _clean(
                escalation_reason
            )[:800],
            "escalation_count_before_route": escalation_count,
            "catalog_fingerprint": _sha256(
                self.catalog_snapshot()
            ),
            "model_availability_checked": bool(
                explicit_available_names is not None
                or self.availability_checker is not None
            ),
            "cost_policy_enforced": True,
            "locality_policy_enforced": True,
            "agent_selected_model": False,
            "user_preference_is_evidence": False,
            "quality_score_is_truth_probability": False,
            "search_performed": False,
            "llm_call_performed": False,
            "authorizes_release": False,
            "modifies_policy": False,
            "modifies_research_depth": False,
            "private_chain_of_thought_stored": False,
        }

        self._last_decision = deepcopy(
            result
        )

        return result

    def create_llm_service(
        self,
        route_decision: Mapping[str, Any],
        *,
        llm_service_factory: Any = None,
        **llm_kwargs: Any,
    ) -> Any:
        """
        Instantiate the existing LLMService using a validated route decision.

        This helper does not perform inference.
        """
        if not isinstance(
            route_decision,
            Mapping,
        ):
            raise ModelRouterValidationError(
                "route_decision must be an object."
            )

        model_id = _clean(
            route_decision.get(
                "model_id"
            )
        )
        model_name = _clean(
            route_decision.get(
                "model_name"
            )
        )
        provider = _enum(
            route_decision.get(
                "provider"
            )
        )

        if (
            not model_id
            or model_id not in self._catalog
        ):
            raise ModelRouterValidationError(
                "route_decision references unknown model_id."
            )

        spec = self._catalog[
            model_id
        ]

        if (
            model_name != spec.model_name
            or provider != spec.provider
        ):
            raise ModelRouterValidationError(
                "route_decision model identity does not match router catalog."
            )

        if spec.paid or not spec.local:
            raise ModelRouterValidationError(
                "create_llm_service refuses paid/non-local route."
            )

        if llm_service_factory is None:
            try:
                from services.llm_service import LLMService  # type: ignore
            except Exception:
                try:
                    from llm_service import LLMService  # type: ignore
                except Exception as exc:
                    raise ModelRouterValidationError(
                        "LLMService could not be imported."
                    ) from exc

            llm_service_factory = LLMService

        if "model_name" in llm_kwargs:
            supplied = _clean(
                llm_kwargs[
                    "model_name"
                ]
            )

            if supplied != spec.model_name:
                raise ModelRouterValidationError(
                    "Caller cannot override routed model_name."
                )

        llm_kwargs[
            "model_name"
        ] = spec.model_name

        return llm_service_factory(
            **llm_kwargs
        )

    def validate_route_decision(
        self,
        route_decision: Mapping[str, Any],
    ) -> None:
        """
        Validate that a persisted route still matches the configured catalog.
        """
        if not isinstance(
            route_decision,
            Mapping,
        ):
            raise ModelRouterValidationError(
                "route_decision must be an object."
            )

        model_id = _clean(
            route_decision.get(
                "model_id"
            )
        )

        if model_id not in self._catalog:
            raise ModelRouterValidationError(
                "route_decision references unknown model_id."
            )

        spec = self._catalog[
            model_id
        ]

        if _clean(
            route_decision.get(
                "model_name"
            )
        ) != spec.model_name:
            raise ModelRouterValidationError(
                "route_decision model_name mismatch."
            )

        if _enum(
            route_decision.get(
                "provider"
            )
        ) != spec.provider:
            raise ModelRouterValidationError(
                "route_decision provider mismatch."
            )

        if bool(
            route_decision.get(
                "paid_model_selected",
                False,
            )
        ) != bool(
            spec.paid
        ):
            raise ModelRouterValidationError(
                "route_decision paid-model flag mismatch."
            )

    # ------------------------------------------------------------------
    # Policy
    # ------------------------------------------------------------------

    def _routing_policy(
        self,
        blueprint: Mapping[str, Any],
    ) -> Dict[str, Any]:
        raw = (
            blueprint.get(
                "model_routing_policy"
            )
            or {}
        )

        if not isinstance(
            raw,
            Mapping,
        ):
            raise ModelRouterValidationError(
                "blueprint.model_routing_policy must be an object."
            )

        cost_policy = (
            blueprint.get(
                "cost_policy"
            )
            or {}
        )

        if not isinstance(
            cost_policy,
            Mapping,
        ):
            cost_policy = {}

        paid_llm_calls_allowed = cost_policy.get(
            "paid_llm_calls_allowed",
            False,
        )

        if not isinstance(
            paid_llm_calls_allowed,
            bool,
        ):
            raise ModelRouterValidationError(
                "cost_policy.paid_llm_calls_allowed must be boolean."
            )

        allowed_model_ids = _string_set(
            raw.get(
                "allowed_model_ids"
            )
        )

        blocked_model_ids = _string_set(
            raw.get(
                "blocked_model_ids"
            )
        )

        max_tier = _enum(
            raw.get(
                "max_tier"
            )
            or "xlarge"
        )

        if max_tier not in TIER_RANK:
            raise ModelRouterValidationError(
                "model_routing_policy.max_tier is invalid."
            )

        allow_escalation = raw.get(
            "allow_escalation",
            True,
        )

        if not isinstance(
            allow_escalation,
            bool,
        ):
            raise ModelRouterValidationError(
                "model_routing_policy.allow_escalation must be boolean."
            )

        max_escalations = raw.get(
            "max_escalations_per_stage",
            1,
        )

        if isinstance(
            max_escalations,
            bool,
        ):
            raise ModelRouterValidationError(
                "max_escalations_per_stage must be integer, not bool."
            )

        try:
            max_escalations = int(
                max_escalations
            )
        except (TypeError, ValueError) as exc:
            raise ModelRouterValidationError(
                "max_escalations_per_stage must be integer."
            ) from exc

        if max_escalations < 0:
            raise ModelRouterValidationError(
                "max_escalations_per_stage must be >= 0."
            )

        task_overrides = raw.get(
            "task_overrides"
        ) or {}

        if not isinstance(
            task_overrides,
            Mapping,
        ):
            raise ModelRouterValidationError(
                "model_routing_policy.task_overrides must be an object."
            )

        normalized_overrides = {}

        for raw_task, value in task_overrides.items():
            task = _enum(
                raw_task
            )

            if task not in ALLOWED_TASKS:
                raise ModelRouterValidationError(
                    f"Unknown task override={raw_task!r}."
                )

            if not isinstance(
                value,
                Mapping,
            ):
                raise ModelRouterValidationError(
                    f"task_overrides.{task} must be an object."
                )

            override = {}

            if "minimum_tier" in value:
                minimum_tier = _enum(
                    value[
                        "minimum_tier"
                    ]
                )

                if minimum_tier not in TIER_RANK:
                    raise ModelRouterValidationError(
                        f"task_overrides.{task}.minimum_tier is invalid."
                    )

                override[
                    "minimum_tier"
                ] = minimum_tier

            if "required_capabilities" in value:
                override[
                    "required_capabilities"
                ] = sorted(
                    _string_set(
                        value[
                            "required_capabilities"
                        ]
                    )
                )

            if "preferred_model_ids" in value:
                override[
                    "preferred_model_ids"
                ] = sorted(
                    _string_set(
                        value[
                            "preferred_model_ids"
                        ]
                    )
                )

            normalized_overrides[
                task
            ] = override

        return {
            "require_local": True,
            "paid_llm_calls_allowed": paid_llm_calls_allowed,
            "allowed_model_ids": sorted(
                allowed_model_ids
            ),
            "blocked_model_ids": sorted(
                blocked_model_ids
            ),
            "max_tier": max_tier,
            "allow_escalation": allow_escalation,
            "max_escalations_per_stage": max_escalations,
            "task_overrides": normalized_overrides,
            "require_confirmed_availability": bool(
                raw.get(
                    "require_confirmed_availability",
                    False,
                )
            ),
        }

    @staticmethod
    def _research_depth(
        blueprint: Mapping[str, Any],
    ) -> str:
        candidates = []

        for section_name in (
            "execution_policy",
            "runtime_policy",
        ):
            section = blueprint.get(
                section_name
            )

            if isinstance(
                section,
                Mapping,
            ):
                candidates.append(
                    section.get(
                        "research_depth"
                    )
                )

        candidates.append(
            blueprint.get(
                "research_depth"
            )
        )

        for value in candidates:
            normalized = _enum(
                value
            )

            if normalized in {
                "fast",
                "deep",
                "balanced",
            }:
                return normalized

        return "unknown"

    def _task_requirement(
        self,
        *,
        task: str,
        policy: Mapping[str, Any],
        routing_context: Mapping[str, Any],
    ) -> Dict[str, Any]:
        base = deepcopy(
            TASK_REQUIREMENTS[
                task
            ]
        )

        override = policy[
            "task_overrides"
        ].get(
            task,
            {},
        )

        required_capabilities = set(
            base[
                "required_capabilities"
            ]
        )

        required_capabilities.update(
            override.get(
                "required_capabilities",
                [],
            )
        )

        context_capabilities = _string_set(
            routing_context.get(
                "required_capabilities"
            )
        )

        required_capabilities.update(
            context_capabilities
        )

        if bool(
            routing_context.get(
                "requires_json",
                False,
            )
        ):
            required_capabilities.add(
                "json"
            )

        if bool(
            routing_context.get(
                "requires_long_context",
                False,
            )
        ):
            required_capabilities.add(
                "long_context"
            )

        minimum_tier = override.get(
            "minimum_tier",
            base[
                "minimum_tier"
            ],
        )

        estimated_input_tokens = _optional_positive_int(
            routing_context.get(
                "estimated_input_tokens"
            ),
            name="routing_context.estimated_input_tokens",
        )

        return {
            "required_capabilities": sorted(
                required_capabilities
            ),
            "minimum_tier": minimum_tier,
            "reasoning_intensity": base[
                "reasoning_intensity"
            ],
            "prefer_speed": bool(
                base[
                    "prefer_speed"
                ]
            ),
            "preferred_model_ids": list(
                override.get(
                    "preferred_model_ids",
                    [],
                )
            ),
            "estimated_input_tokens": estimated_input_tokens,
        }

    # ------------------------------------------------------------------
    # Eligibility
    # ------------------------------------------------------------------

    def _model_rejection_reasons(
        self,
        *,
        spec: ModelSpec,
        task: str,
        requirement: Mapping[str, Any],
        policy: Mapping[str, Any],
        explicit_available_names: Optional[set[str]],
    ) -> list[str]:
        reasons = []

        if not spec.enabled:
            reasons.append(
                "model_disabled"
            )

        if policy[
            "require_local"
        ] and not spec.local:
            reasons.append(
                "non_local_model"
            )

        if spec.provider not in ALLOWED_PROVIDERS:
            reasons.append(
                "provider_not_allowed_by_local_first_policy"
            )

        if (
            spec.paid
            and not policy[
                "paid_llm_calls_allowed"
            ]
        ):
            reasons.append(
                "paid_llm_disallowed"
            )

        allowed_ids = set(
            policy[
                "allowed_model_ids"
            ]
        )

        if (
            allowed_ids
            and _enum(
                spec.model_id
            )
            not in allowed_ids
        ):
            reasons.append(
                "model_not_in_policy_allowlist"
            )

        blocked_ids = set(
            policy[
                "blocked_model_ids"
            ]
        )

        if _enum(
            spec.model_id
        ) in blocked_ids:
            reasons.append(
                "model_blocked_by_policy"
            )

        if TIER_RANK[
            spec.tier
        ] > TIER_RANK[
            policy[
                "max_tier"
            ]
        ]:
            reasons.append(
                "model_exceeds_max_tier"
            )

        if TIER_RANK[
            spec.tier
        ] < TIER_RANK[
            requirement[
                "minimum_tier"
            ]
        ]:
            reasons.append(
                "model_below_task_minimum_tier"
            )

        missing_capabilities = (
            set(
                requirement[
                    "required_capabilities"
                ]
            )
            - set(
                spec.capabilities
            )
        )

        if missing_capabilities:
            reasons.append(
                "missing_capabilities:"
                + ",".join(
                    sorted(
                        missing_capabilities
                    )
                )
            )

        estimated_tokens = requirement.get(
            "estimated_input_tokens"
        )

        if (
            estimated_tokens is not None
            and spec.max_context_tokens is not None
            and estimated_tokens
            > spec.max_context_tokens
        ):
            reasons.append(
                "context_window_too_small"
            )

        if explicit_available_names is not None:
            if not self._name_in_inventory(
                spec.model_name,
                explicit_available_names,
            ):
                reasons.append(
                    "model_not_in_explicit_local_inventory"
                )

        elif self.availability_checker is not None:
            try:
                available = bool(
                    self.availability_checker(
                        spec.model_name
                    )
                )
            except Exception:
                available = False

            if not available:
                reasons.append(
                    "availability_checker_rejected_model"
                )

        elif policy[
            "require_confirmed_availability"
        ]:
            reasons.append(
                "availability_not_confirmed"
            )

        return reasons

    @staticmethod
    def _name_in_inventory(
        model_name: str,
        inventory: set[str],
    ) -> bool:
        if model_name in inventory:
            return True

        if ":" not in model_name:
            return (
                model_name
                + ":latest"
            ) in inventory

        return False

    # ------------------------------------------------------------------
    # Selection
    # ------------------------------------------------------------------

    def _select_base_route(
        self,
        *,
        eligible: Sequence[ModelSpec],
        task: str,
        requirement: Mapping[str, Any],
        research_depth: str,
        policy: Mapping[str, Any],
    ) -> ModelSpec:
        preferred_ids = {
            _enum(
                item
            )
            for item in requirement.get(
                "preferred_model_ids",
                []
            )
        }

        def score(
            spec: ModelSpec,
        ) -> tuple:
            preferred_penalty = (
                0
                if (
                    not preferred_ids
                    or _enum(
                        spec.model_id
                    )
                    in preferred_ids
                )
                else 1
            )

            tier_rank = TIER_RANK[
                spec.tier
            ]

            # Fast: smallest sufficient model, then speed.
            if research_depth == "fast":
                return (
                    preferred_penalty,
                    tier_rank,
                    spec.speed_rank,
                    -spec.quality_rank,
                    spec.model_id,
                )

            # Deep: high-intensity reasoning may deliberately prefer a stronger
            # sufficient model, but still within compiled max_tier.
            if (
                research_depth == "deep"
                and requirement[
                    "reasoning_intensity"
                ]
                >= 4
            ):
                return (
                    preferred_penalty,
                    -tier_rank,
                    -spec.quality_rank,
                    spec.speed_rank,
                    spec.model_id,
                )

            # Balanced/unknown: smallest sufficient tier, then quality.
            return (
                preferred_penalty,
                tier_rank,
                -spec.quality_rank,
                spec.speed_rank,
                spec.model_id,
            )

        return sorted(
            eligible,
            key=score,
        )[0]

    def _select_escalation(
        self,
        *,
        eligible: Sequence[ModelSpec],
        current_model_id: str,
        policy: Mapping[str, Any],
        escalation_count: int,
    ) -> ModelSpec:
        if not policy[
            "allow_escalation"
        ]:
            raise ModelEscalationDeniedError(
                "Model escalation is disabled by compiled policy."
            )

        if escalation_count >= policy[
            "max_escalations_per_stage"
        ]:
            raise ModelEscalationDeniedError(
                "Model escalation limit has been reached."
            )

        if not current_model_id:
            raise ModelEscalationDeniedError(
                "current_model_id is required for escalation."
            )

        if current_model_id not in self._catalog:
            raise ModelEscalationDeniedError(
                "current_model_id is not in router catalog."
            )

        current = self._catalog[
            current_model_id
        ]

        stronger = [
            spec
            for spec in eligible
            if (
                TIER_RANK[
                    spec.tier
                ]
                > TIER_RANK[
                    current.tier
                ]
                or (
                    TIER_RANK[
                        spec.tier
                    ]
                    == TIER_RANK[
                        current.tier
                    ]
                    and spec.quality_rank
                    > current.quality_rank
                )
            )
        ]

        if not stronger:
            raise ModelEscalationDeniedError(
                "No strictly stronger eligible local model is available."
            )

        # Choose the smallest strictly stronger model, not automatically the
        # largest model in the catalog.
        stronger.sort(
            key=lambda spec: (
                TIER_RANK[
                    spec.tier
                ],
                spec.quality_rank,
                spec.speed_rank,
                spec.model_id,
            )
        )

        return stronger[0]

    # ------------------------------------------------------------------
    # Catalog
    # ------------------------------------------------------------------

    def _normalize_catalog(
        self,
        catalog: Mapping[
            str,
            Mapping[
                str,
                Any,
            ]
        ],
    ) -> Dict[str, ModelSpec]:
        if not isinstance(
            catalog,
            Mapping,
        ):
            raise ModelRouterValidationError(
                "model_catalog must be an object."
            )

        output = {}

        for raw_model_id, raw in catalog.items():
            if not isinstance(
                raw,
                Mapping,
            ):
                raise ModelRouterValidationError(
                    f"model_catalog[{raw_model_id!r}] must be an object."
                )

            model_id = _clean(
                raw_model_id
            )

            if not model_id:
                raise ModelRouterValidationError(
                    "model_catalog contains empty model_id."
                )

            model_name = _clean(
                raw.get(
                    "model_name"
                )
                or raw.get(
                    "name"
                )
            )

            if not model_name:
                raise ModelRouterValidationError(
                    f"{model_id}: model_name cannot be empty."
                )

            provider = _enum(
                raw.get(
                    "provider"
                )
                or "ollama"
            )

            tier = _enum(
                raw.get(
                    "tier"
                )
                or "small"
            )

            if tier not in TIER_RANK:
                raise ModelRouterValidationError(
                    f"{model_id}: invalid tier={tier!r}."
                )

            capabilities = tuple(
                sorted(
                    _string_set(
                        raw.get(
                            "capabilities"
                        )
                        or []
                    )
                )
            )

            enabled = raw.get(
                "enabled",
                True,
            )
            local = raw.get(
                "local",
                provider in ALLOWED_PROVIDERS,
            )
            paid = raw.get(
                "paid",
                False,
            )

            for label, value in (
                ("enabled", enabled),
                ("local", local),
                ("paid", paid),
            ):
                if not isinstance(
                    value,
                    bool,
                ):
                    raise ModelRouterValidationError(
                        f"{model_id}.{label} must be boolean."
                    )

            max_context_tokens = _optional_positive_int(
                raw.get(
                    "max_context_tokens"
                ),
                name=f"{model_id}.max_context_tokens",
            )

            speed_rank = raw.get(
                "speed_rank",
                100,
            )
            quality_rank = raw.get(
                "quality_rank",
                100,
            )

            for label, value in (
                ("speed_rank", speed_rank),
                ("quality_rank", quality_rank),
            ):
                if isinstance(
                    value,
                    bool,
                ):
                    raise ModelRouterValidationError(
                        f"{model_id}.{label} must be integer, not bool."
                    )

                try:
                    numeric = int(
                        value
                    )
                except (TypeError, ValueError) as exc:
                    raise ModelRouterValidationError(
                        f"{model_id}.{label} must be integer."
                    ) from exc

                if numeric < 0:
                    raise ModelRouterValidationError(
                        f"{model_id}.{label} must be >= 0."
                    )

                if label == "speed_rank":
                    speed_rank = numeric
                else:
                    quality_rank = numeric

            spec = ModelSpec(
                model_id=model_id,
                model_name=model_name,
                provider=provider,
                tier=tier,
                capabilities=capabilities,
                enabled=enabled,
                local=local,
                paid=paid,
                max_context_tokens=max_context_tokens,
                speed_rank=speed_rank,
                quality_rank=quality_rank,
                metadata=_json_safe(
                    raw.get(
                        "metadata"
                    )
                    or {}
                ),
            )

            if model_id in output:
                raise ModelRouterValidationError(
                    f"Duplicate model_id={model_id!r}."
                )

            output[
                model_id
            ] = spec

        return output

    @staticmethod
    def _default_catalog(
        *,
        default_model_name: str,
    ) -> Dict[str, Dict[str, Any]]:
        """
        Safe single-model default.

        Multiple tiers should normally come from PolicyRegistry/config.
        """
        model_name = (
            _clean(
                default_model_name
            )
            or _clean(
                os.getenv(
                    "OLLAMA_MODEL"
                )
            )
            or "llama3.1:8b"
        )

        return {
            "local_default": {
                "model_name": model_name,
                "provider": "ollama",
                "tier": "small",
                "capabilities": [
                    "json",
                    "reasoning",
                    "evidence_classification",
                ],
                "enabled": True,
                "local": True,
                "paid": False,
                "speed_rank": 100,
                "quality_rank": 100,
            }
        }

    @staticmethod
    def _no_eligible_message(
        *,
        task: str,
        rejected: Sequence[
            Mapping[
                str,
                Any,
            ]
        ],
    ) -> str:
        detail = "; ".join(
            (
                _clean(
                    item.get(
                        "model_id"
                    )
                )
                + "="
                + ",".join(
                    item.get(
                        "reasons"
                    )
                    or []
                )
            )
            for item in rejected
        )

        return (
            f"No eligible local model for task={task!r}."
            + (
                " Rejections: "
                + detail
                if detail
                else ""
            )
        )


# Compatibility alias.
DeterministicModelRouterService = ModelRouterService