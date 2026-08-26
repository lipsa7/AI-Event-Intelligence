
"""
Deterministic policy-enforcement and release-gate service for the Event
Intelligence research pipeline.

Recommended location:
    services/policy_enforcement_service.py

Role
----
Compiled Blueprint / PolicyRegistry
            ↓
PolicyEnforcementService
            ↓
runtime operation authorization
+ stage-result validation
+ quality-gate evaluation
+ release validation
            ↓
allowed execution / blocked execution / final release decision

Core principle
--------------
LLMs interpret and reason.
Deterministic services authorize and enforce.
Evaluation verifies whether both behaved correctly.

This service is therefore an AUTHORITY component.

It does NOT:
- compile user preferences into policy;
- choose Fast vs Deep;
- choose a model;
- perform model inference;
- perform web search;
- ground claims;
- red-team claims;
- repair invalid outputs;
- silently lower standards when budget is exhausted.

Instead it checks whether an attempted operation or final artifact complies
with the already-compiled Blueprint and the product's hard enforcement floor.

Free/local-first hard floor
---------------------------
The product has zero mandatory external cost.

The hard enforcement floor is:

    paid_external_services_allowed = False
    paid_llm_calls_allowed = False
    paid_search_apis_allowed = False

A Blueprint may be equally or more restrictive. It may NOT weaken this floor.

Allowed default local/zero-cost execution paths include:
- Ollama/local LLM inference;
- RSS;
- self-hosted/local SearXNG;
- direct source HTTP fetch;
- injected local/callable search adapters;
- SQLite / ChromaDB / NetworkX / Streamlit / local Python.

Optional paid/cloud integrations may exist in the codebase, but this service
blocks them in the default product path.

Important runtime boundaries
----------------------------
1. Model calls require an already-issued ModelRouteDecision.
2. Agents cannot directly choose/override a model.
3. Model escalation requires ModelRouterService authorization.
4. Search-provider execution must satisfy the zero-cost search policy.
5. Refinement actions must be explicitly allowed and remain within the
   Blueprint's refinement limit.
6. Silent evidence/context truncation is forbidden.
7. Budget exhaustion never weakens grounding/red-team/release gates.

Release control
---------------
Final release requires all hard quality gates plus output-contract checks.

Typical release path:

    final reasoning
        ↓
    GroundingService
        ↓
    RedTeamAgent
        ↓
    report composition
        ↓
    PolicyEnforcementService.validate_release(...)
        ↓
    release_allowed = True | False

No other service should set release authorization to True.

Evidence completeness invariant
--------------------------------
When a finalized reasoning-context/evidence manifest is supplied:

    all_accepted - examined == empty
    relevant <= examined
    referenced <= final_reasoning_context <= examined
    unexamined == empty

Report evidence references must resolve to admitted/final-context evidence when
the report exposes evidence IDs.

Epistemic release rule
----------------------
Final released insight assessments may be:

    supported
    partially_supported
    inference_supported

The following cannot be released as accepted final insights:

    unsupported
    contradicted
    unresolved

This does not prevent a report from DISCUSSING contradictions or uncertainties
in dedicated counterevidence/uncertainty sections.

No private chain-of-thought is requested, persisted or returned.
"""

from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from dataclasses import asdict, dataclass
from typing import Any, Dict, Mapping, Optional, Sequence, Set


SERVICE_VERSION = "policy-enforcement-service-v2.0"
OUTPUT_SCHEMA_VERSION = "policy-enforcement-schema-v2.0"

HARD_COST_FLOOR = {
    "paid_external_services_allowed": False,
    "paid_llm_calls_allowed": False,
    "paid_search_apis_allowed": False,
}

LOCAL_LLM_PROVIDERS = {
    "ollama",
    "local",
}

ZERO_COST_SEARCH_PROVIDERS = {
    "searxng",
    "local_searxng",
    "self_hosted_searxng",
    "local",
    "self_hosted",
    "callable",
    "callable_search_provider",
    "rss",
    "direct_http",
    "none",
}

PAID_OR_CREDENTIALLED_SEARCH_PROVIDERS = {
    "google",
    "google_custom_search",
    "google_cse",
    "brave",
    "brave_search",
    "bing",
    "serpapi",
    "tavily",
    "perplexity",
    "you_com",
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

ALLOWED_OPERATORS = {
    ">",
    ">=",
    "<",
    "<=",
    "==",
    "!=",
}

ALLOWED_RELEASE_EPISTEMIC_STATUSES = {
    "supported",
    "partially_supported",
    "inference_supported",
}

BLOCKED_RELEASE_EPISTEMIC_STATUSES = {
    "unsupported",
    "contradicted",
    "unresolved",
}

PROTECTED_FINALIZATION_STAGES = {
    "final_reasoning",
    "final_grounding",
    "red_team",
    "report",
    "report_generation",
    "audit",
    "release",
    "release_validation",
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


class PolicyEnforcementError(RuntimeError):
    """Base policy-enforcement error."""


class PolicyEnforcementValidationError(PolicyEnforcementError):
    """Malformed Blueprint/request/result."""


class PolicyViolationError(PolicyEnforcementError):
    """Attempted execution violates compiled policy or hard floor."""


@dataclass(frozen=True)
class PolicyDecision:
    decision_id: str
    decision_type: str
    allowed: bool
    action: str
    stage: str
    reason_codes: tuple[str, ...]
    violations: tuple[Mapping[str, Any], ...]
    warnings: tuple[Mapping[str, Any], ...]
    effective_policy: Mapping[str, Any]
    authorizes_release: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return _json_safe(
            asdict(self)
        )


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
    text = re.sub(r"[^a-z0-9_]+", "", text)

    while "__" in text:
        text = text.replace("__", "_")

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


def _unique_strings(
    values: Any,
    *,
    uppercase: bool = False,
) -> list[str]:
    if values is None:
        return []

    if isinstance(values, str):
        values = [values]

    if not isinstance(
        values,
        (list, tuple, set),
    ):
        return []

    output = []
    seen = set()

    for item in values:
        if isinstance(item, Mapping):
            item = (
                item.get("evidence_id")
                or item.get("insight_id")
                or item.get("insight_revision_id")
                or item.get("id")
                or ""
            )

        text = _clean(item)

        if uppercase:
            text = text.upper()

        if text and text not in seen:
            seen.add(text)
            output.append(text)

    return output


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

    raise PolicyEnforcementValidationError(
        f"{name} must be boolean."
    )


def _nonnegative_int(
    value: Any,
    *,
    name: str,
) -> int:
    if isinstance(value, bool):
        raise PolicyEnforcementValidationError(
            f"{name} must be integer, not bool."
        )

    if isinstance(value, float) and not value.is_integer():
        raise PolicyEnforcementValidationError(
            f"{name} must be integer."
        )

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PolicyEnforcementValidationError(
            f"{name} must be integer."
        ) from exc

    if number < 0:
        raise PolicyEnforcementValidationError(
            f"{name} must be >= 0."
        )

    return number


def _violation(
    code: str,
    message: str,
    *,
    path: str = "",
    severity: str = "hard",
) -> Dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "path": path,
        "severity": severity,
    }


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class PolicyEnforcementService:
    """
    Deterministic runtime and release authority.

    This service evaluates compliance. It does not perform the work being
    evaluated.
    """

    def __init__(
        self,
        *,
        enforce_free_local_floor: bool = True,
    ) -> None:
        self.enforce_free_local_floor = bool(
            enforce_free_local_floor
        )
        self._last_decision: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Blueprint validation
    # ------------------------------------------------------------------

    def validate_blueprint(
        self,
        blueprint: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """
        Validate a compiled Blueprint against the deterministic enforcement floor.

        Returns the effective enforcement policy.
        """
        blueprint = self._validate_mapping(
            blueprint,
            name="blueprint",
        )

        prohibited = _contains_prohibited_field(
            blueprint
        )

        if prohibited:
            raise PolicyEnforcementValidationError(
                "blueprint contains prohibited private/raw reasoning field "
                f"at {prohibited!r}."
            )

        violations = []
        warnings = []

        runtime_policy = self._section(
            blueprint,
            "runtime_policy",
        )
        output_contract = self._section(
            blueprint,
            "output_contract",
        )
        quality_policy = self._section(
            blueprint,
            "quality_policy",
        )
        escalation = self._section(
            blueprint,
            "escalation",
        )
        cost_policy = self._effective_cost_policy(
            blueprint
        )

        if runtime_policy:
            allow_silent = runtime_policy.get(
                "allow_silent_truncation",
                False,
            )

            if not isinstance(
                allow_silent,
                bool,
            ):
                violations.append(
                    _violation(
                        "INVALID_SILENT_TRUNCATION_FLAG",
                        "runtime_policy.allow_silent_truncation must be boolean.",
                        path="runtime_policy.allow_silent_truncation",
                    )
                )

            elif allow_silent:
                violations.append(
                    _violation(
                        "SILENT_TRUNCATION_FORBIDDEN",
                        "Silent evidence/context truncation is forbidden.",
                        path="runtime_policy.allow_silent_truncation",
                    )
                )

            max_rounds = runtime_policy.get(
                "max_refinement_rounds"
            )

            if max_rounds is not None:
                try:
                    _nonnegative_int(
                        max_rounds,
                        name="runtime_policy.max_refinement_rounds",
                    )
                except PolicyEnforcementValidationError as exc:
                    violations.append(
                        _violation(
                            "INVALID_REFINEMENT_LIMIT",
                            str(exc),
                            path="runtime_policy.max_refinement_rounds",
                        )
                    )

        # Baseline product contract.
        for key in (
            "require_evidence_trail",
            "require_uncertainty_section",
            "require_counterevidence_section",
            "stable_insight_ids",
            "preserve_revision_lineage",
        ):
            if key in output_contract:
                value = output_contract[
                    key
                ]

                if not isinstance(
                    value,
                    bool,
                ):
                    violations.append(
                        _violation(
                            "INVALID_OUTPUT_CONTRACT_FLAG",
                            f"output_contract.{key} must be boolean.",
                            path=f"output_contract.{key}",
                        )
                    )

        gates = quality_policy.get(
            "gates",
            [],
        )

        if gates is not None and not isinstance(
            gates,
            list,
        ):
            violations.append(
                _violation(
                    "INVALID_QUALITY_GATES",
                    "quality_policy.gates must be a list.",
                    path="quality_policy.gates",
                )
            )
            gates = []

        gate_ids = set()

        for index, gate in enumerate(
            gates
        ):
            try:
                normalized_gate = self._validate_gate(
                    gate,
                    index=index,
                )
                gate_ids.add(
                    normalized_gate[
                        "gate_id"
                    ]
                )
            except PolicyEnforcementValidationError as exc:
                violations.append(
                    _violation(
                        "INVALID_QUALITY_GATE",
                        str(exc),
                        path=f"quality_policy.gates[{index}]",
                    )
                )

        # Current product requires Grounding and Red Team as hard gates.
        for required_gate in (
            "GROUNDING-REQUIRED",
            "RED-TEAM-REQUIRED",
        ):
            if required_gate not in gate_ids:
                violations.append(
                    _violation(
                        "REQUIRED_QUALITY_GATE_MISSING",
                        f"Required hard quality gate {required_gate!r} is missing.",
                        path="quality_policy.gates",
                    )
                )

        allowed_actions = set(
            _unique_strings(
                escalation.get(
                    "allowed_actions"
                )
                or []
            )
        )

        unsupported_actions = (
            allowed_actions
            - ALLOWED_REFINEMENT_ACTIONS
        )

        if unsupported_actions:
            violations.append(
                _violation(
                    "UNSUPPORTED_ESCALATION_ACTION",
                    "Unsupported escalation action(s): "
                    + ", ".join(
                        sorted(
                            unsupported_actions
                        )
                    ),
                    path="escalation.allowed_actions",
                )
            )

        if (
            "model_escalation"
            in allowed_actions
        ):
            requires_router = escalation.get(
                "model_escalation_requires_router",
                True,
            )

            if requires_router is not True:
                violations.append(
                    _violation(
                        "MODEL_ESCALATION_MUST_REQUIRE_ROUTER",
                        "model_escalation must require ModelRouterService.",
                        path="escalation.model_escalation_requires_router",
                    )
                )

        # Hard cost floor cannot be weakened.
        if self.enforce_free_local_floor:
            for key, hard_value in HARD_COST_FLOOR.items():
                if cost_policy[
                    key
                ] is not hard_value:
                    violations.append(
                        _violation(
                            "COST_FLOOR_WEAKENED",
                            f"{key} may not be enabled in the free/local-first "
                            "product path.",
                            path=f"cost_policy.{key}",
                        )
                    )

        effective_policy = {
            "cost_policy": cost_policy,
            "runtime_policy": deepcopy(
                runtime_policy
            ),
            "quality_policy": deepcopy(
                quality_policy
            ),
            "output_contract": deepcopy(
                output_contract
            ),
            "escalation": deepcopy(
                escalation
            ),
            "free_local_floor_enforced": self.enforce_free_local_floor,
        }

        result = {
            "valid": not violations,
            "violations": violations,
            "warnings": warnings,
            "effective_policy": effective_policy,
            "metadata": {
                "service_version": SERVICE_VERSION,
                "output_schema_version": OUTPUT_SCHEMA_VERSION,
                "blueprint_fingerprint": _sha256(
                    blueprint
                ),
                "authorizes_release": False,
                "llm_call_performed": False,
                "search_performed": False,
                "private_chain_of_thought_stored": False,
            },
        }

        return result

    # ------------------------------------------------------------------
    # Runtime authorization
    # ------------------------------------------------------------------

    def authorize_operation(
        self,
        *,
        action: str,
        stage: str,
        blueprint: Mapping[str, Any],
        request: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Authorize one attempted runtime operation.

        Common actions:
            llm_call
            web_search
            source_fetch
            model_escalation
            refinement_action
            context_truncation
        """
        action = _enum(
            action
        )
        stage = _enum(
            stage
        )

        if not action:
            raise PolicyEnforcementValidationError(
                "action cannot be empty."
            )

        blueprint_validation = self.validate_blueprint(
            blueprint
        )

        request = (
            request
            if isinstance(
                request,
                Mapping,
            )
            else {}
        )

        prohibited = _contains_prohibited_field(
            request
        )

        if prohibited:
            raise PolicyEnforcementValidationError(
                "request contains prohibited private/raw reasoning field "
                f"at {prohibited!r}."
            )

        violations = list(
            blueprint_validation[
                "violations"
            ]
        )
        warnings = []
        reasons = []

        effective_policy = blueprint_validation[
            "effective_policy"
        ]

        if not violations:
            if action == "llm_call":
                self._authorize_llm_call(
                    request=request,
                    effective_policy=effective_policy,
                    violations=violations,
                    reasons=reasons,
                )

            elif action == "web_search":
                self._authorize_web_search(
                    request=request,
                    effective_policy=effective_policy,
                    violations=violations,
                    reasons=reasons,
                )

            elif action == "source_fetch":
                self._authorize_source_fetch(
                    request=request,
                    effective_policy=effective_policy,
                    violations=violations,
                    reasons=reasons,
                )

            elif action == "model_escalation":
                self._authorize_model_escalation(
                    request=request,
                    effective_policy=effective_policy,
                    violations=violations,
                    reasons=reasons,
                )

            elif action == "refinement_action":
                self._authorize_refinement_action(
                    request=request,
                    effective_policy=effective_policy,
                    violations=violations,
                    reasons=reasons,
                )

            elif action == "context_truncation":
                violations.append(
                    _violation(
                        "SILENT_TRUNCATION_FORBIDDEN",
                        "Context/evidence truncation cannot be used as a silent "
                        "overflow strategy.",
                    )
                )

            else:
                reasons.append(
                    "no_action_specific_policy_rule"
                )

        allowed = not violations

        decision = PolicyDecision(
            decision_id=_stable_id(
                "POLICYDECISION",
                {
                    "decision_type": "operation",
                    "action": action,
                    "stage": stage,
                    "request": request,
                    "blueprint_fingerprint": blueprint_validation[
                        "metadata"
                    ][
                        "blueprint_fingerprint"
                    ],
                    "allowed": allowed,
                    "violations": violations,
                },
            ),
            decision_type="operation",
            allowed=allowed,
            action=action,
            stage=stage,
            reason_codes=tuple(
                reasons
                or (
                    [
                        "policy_compliant"
                    ]
                    if allowed
                    else [
                        "policy_violation"
                    ]
                )
            ),
            violations=tuple(
                deepcopy(
                    violations
                )
            ),
            warnings=tuple(
                deepcopy(
                    warnings
                )
            ),
            effective_policy=deepcopy(
                effective_policy
            ),
            authorizes_release=False,
        ).to_dict()

        decision[
            "metadata"
        ] = {
            "service_version": SERVICE_VERSION,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "blueprint_fingerprint": blueprint_validation[
                "metadata"
            ][
                "blueprint_fingerprint"
            ],
            "request_fingerprint": _sha256(
                request
            ),
            "protected_finalization_stage": (
                stage
                in PROTECTED_FINALIZATION_STAGES
            ),
            "llm_call_performed": False,
            "search_performed": False,
            "modifies_research_depth": False,
            "chooses_model_route": False,
            "private_chain_of_thought_stored": False,
        }

        self._last_decision = deepcopy(
            decision
        )

        return decision

    def require_operation_allowed(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        decision = self.authorize_operation(
            **kwargs
        )

        if not decision[
            "allowed"
        ]:
            raise PolicyViolationError(
                "; ".join(
                    violation[
                        "code"
                    ]
                    + ": "
                    + violation[
                        "message"
                    ]
                    for violation in decision[
                        "violations"
                    ]
                )
            )

        return decision

    # ------------------------------------------------------------------
    # Stage-result validation
    # ------------------------------------------------------------------

    def validate_stage_result(
        self,
        *,
        stage: str,
        result: Mapping[str, Any],
        blueprint: Mapping[str, Any],
    ) -> Dict[str, Any]:
        """
        Validate common cross-stage invariants without redoing the stage's work.
        """
        stage = _enum(
            stage
        )
        result = self._validate_mapping(
            result,
            name="result",
        )

        blueprint_validation = self.validate_blueprint(
            blueprint
        )

        violations = list(
            blueprint_validation[
                "violations"
            ]
        )
        warnings = []

        prohibited = _contains_prohibited_field(
            result
        )

        if prohibited:
            violations.append(
                _violation(
                    "PRIVATE_REASONING_CONTAMINATION",
                    "Stage result contains prohibited private/raw reasoning field.",
                    path=prohibited,
                )
            )

        metadata = result.get(
            "metadata"
        )

        if isinstance(
            metadata,
            Mapping,
        ):
            if metadata.get(
                "authorizes_release"
            ) is True:
                violations.append(
                    _violation(
                        "UNAUTHORIZED_RELEASE_AUTHORITY",
                        f"Stage {stage!r} attempted to authorize release.",
                        path="metadata.authorizes_release",
                    )
                )

            if metadata.get(
                "modifies_research_depth"
            ) is True:
                violations.append(
                    _violation(
                        "RESEARCH_DEPTH_MUTATION_FORBIDDEN",
                        f"Stage {stage!r} attempted to mutate research_depth.",
                        path="metadata.modifies_research_depth",
                    )
                )

        if stage in {
            "reasoning_context",
            "final_reasoning_context",
        }:
            self._validate_reasoning_context_result(
                result=result,
                violations=violations,
            )

        if stage in {
            "strategic_inference",
            "constraint_analysis",
        }:
            self._validate_reasoning_candidate_result(
                stage=stage,
                result=result,
                violations=violations,
            )

        if stage in {
            "grounding",
            "final_grounding",
        }:
            self._validate_grounding_result(
                result=result,
                violations=violations,
            )

        if stage == "red_team":
            self._validate_red_team_result(
                result=result,
                violations=violations,
            )

        return {
            "valid": not violations,
            "stage": stage,
            "violations": violations,
            "warnings": warnings,
            "metadata": {
                "service_version": SERVICE_VERSION,
                "result_fingerprint": _sha256(
                    result
                ),
                "blueprint_fingerprint": blueprint_validation[
                    "metadata"
                ][
                    "blueprint_fingerprint"
                ],
                "authorizes_release": False,
            },
        }

    # ------------------------------------------------------------------
    # Quality gates
    # ------------------------------------------------------------------

    def evaluate_quality_gates(
        self,
        *,
        metrics: Mapping[str, Any],
        blueprint: Mapping[str, Any],
    ) -> Dict[str, Any]:
        metrics = self._validate_mapping(
            metrics,
            name="metrics",
        )

        blueprint_validation = self.validate_blueprint(
            blueprint
        )

        if not blueprint_validation[
            "valid"
        ]:
            return {
                "all_hard_gates_passed": False,
                "gate_results": [],
                "violations": blueprint_validation[
                    "violations"
                ],
                "metadata": {
                    "service_version": SERVICE_VERSION,
                    "authorizes_release": False,
                },
            }

        quality_policy = blueprint_validation[
            "effective_policy"
        ][
            "quality_policy"
        ]

        gates = quality_policy.get(
            "gates"
        ) or []

        gate_results = []
        violations = []

        for index, raw_gate in enumerate(
            gates
        ):
            gate = self._validate_gate(
                raw_gate,
                index=index,
            )

            metric_name = gate[
                "metric"
            ]

            metric_present = (
                metric_name
                in metrics
            )

            if not metric_present:
                passed = False
                actual = None
                reason = "metric_missing"
            else:
                actual = metrics[
                    metric_name
                ]

                passed = self._compare(
                    actual,
                    gate[
                        "operator"
                    ],
                    gate[
                        "threshold"
                    ],
                )
                reason = (
                    "gate_passed"
                    if passed
                    else "gate_failed"
                )

            result = {
                "gate_id": gate[
                    "gate_id"
                ],
                "metric": metric_name,
                "actual": _json_safe(
                    actual
                ),
                "operator": gate[
                    "operator"
                ],
                "threshold": _json_safe(
                    gate[
                        "threshold"
                    ]
                ),
                "gate_type": gate[
                    "gate_type"
                ],
                "on_fail": gate[
                    "on_fail"
                ],
                "passed": bool(
                    passed
                ),
                "reason": reason,
            }

            gate_results.append(
                result
            )

            if (
                not passed
                and gate[
                    "gate_type"
                ]
                == "hard"
            ):
                violations.append(
                    _violation(
                        "HARD_QUALITY_GATE_FAILED",
                        f"Hard quality gate {gate['gate_id']!r} failed.",
                        path=f"quality_policy.gates[{index}]",
                    )
                )

        return {
            "all_hard_gates_passed": not violations,
            "gate_results": gate_results,
            "violations": violations,
            "metadata": {
                "service_version": SERVICE_VERSION,
                "metrics_fingerprint": _sha256(
                    metrics
                ),
                "authorizes_release": False,
            },
        }

    # ------------------------------------------------------------------
    # Final release authority
    # ------------------------------------------------------------------

    def validate_release(
        self,
        *,
        blueprint: Mapping[str, Any],
        report: Mapping[str, Any],
        grounding_assessments: Any,
        red_team_assessments: Any,
        reasoning_context: Optional[Mapping[str, Any]] = None,
        quality_metrics: Optional[Mapping[str, Any]] = None,
        audit_receipt: Optional[Mapping[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Final deterministic release decision.

        This is the only method in this service that may return
        authorizes_release=True.
        """
        blueprint_validation = self.validate_blueprint(
            blueprint
        )

        report = self._validate_mapping(
            report,
            name="report",
        )

        violations = list(
            blueprint_validation[
                "violations"
            ]
        )
        warnings = []

        prohibited = _contains_prohibited_field(
            report
        )

        if prohibited:
            violations.append(
                _violation(
                    "PRIVATE_REASONING_CONTAMINATION",
                    "Final report contains prohibited private/raw reasoning field.",
                    path=prohibited,
                )
            )

        output_contract = blueprint_validation[
            "effective_policy"
        ][
            "output_contract"
        ]

        # --------------------------------------------------------------
        # Grounding assessments
        # --------------------------------------------------------------
        grounding_list = self._normalize_assessment_list(
            grounding_assessments,
            plural_keys=(
                "grounding_assessments",
                "assessments",
                "results",
            ),
        )

        grounding_compliance = bool(
            grounding_list
        )

        for index, assessment in enumerate(
            grounding_list
        ):
            self._validate_grounding_result(
                result=assessment,
                violations=violations,
                path_prefix=f"grounding_assessments[{index}]",
            )

            epistemic_status = _enum(
                assessment.get(
                    "epistemic_status"
                )
                or assessment.get(
                    "grounding_status"
                )
            )

            if epistemic_status in BLOCKED_RELEASE_EPISTEMIC_STATUSES:
                grounding_compliance = False
                violations.append(
                    _violation(
                        "NON_RELEASEABLE_EPISTEMIC_STATUS",
                        "Final insight assessment has non-releaseable "
                        f"epistemic_status={epistemic_status!r}.",
                        path=f"grounding_assessments[{index}].epistemic_status",
                    )
                )

            elif (
                epistemic_status
                and epistemic_status
                not in ALLOWED_RELEASE_EPISTEMIC_STATUSES
            ):
                grounding_compliance = False
                violations.append(
                    _violation(
                        "UNKNOWN_EPISTEMIC_STATUS",
                        f"Unknown release epistemic status={epistemic_status!r}.",
                        path=f"grounding_assessments[{index}].epistemic_status",
                    )
                )

        # --------------------------------------------------------------
        # Red-team assessments
        # --------------------------------------------------------------
        red_team_list = self._normalize_assessment_list(
            red_team_assessments,
            plural_keys=(
                "red_team_assessments",
                "assessments",
                "results",
            ),
        )

        red_team_complete = bool(
            red_team_list
        )

        for index, assessment in enumerate(
            red_team_list
        ):
            before = len(
                violations
            )

            self._validate_red_team_result(
                result=assessment,
                violations=violations,
                path_prefix=f"red_team_assessments[{index}]",
            )

            if len(
                violations
            ) > before:
                red_team_complete = False

        # --------------------------------------------------------------
        # Reasoning-context/evidence completeness
        # --------------------------------------------------------------
        final_evidence_ids: Optional[
            set[str]
        ] = None

        if reasoning_context is not None:
            reasoning_context = self._validate_mapping(
                reasoning_context,
                name="reasoning_context",
            )

            self._validate_reasoning_context_result(
                result=reasoning_context,
                violations=violations,
                require_finalized=True,
            )

            manifest = reasoning_context.get(
                "evidence_manifest"
            )

            if isinstance(
                manifest,
                Mapping,
            ):
                final_evidence_ids = set(
                    _unique_strings(
                        manifest.get(
                            "final_reasoning_context_ids"
                        ),
                        uppercase=True,
                    )
                )

        # --------------------------------------------------------------
        # Report/output contract
        # --------------------------------------------------------------
        self._validate_report_contract(
            report=report,
            output_contract=output_contract,
            violations=violations,
            warnings=warnings,
            final_evidence_ids=final_evidence_ids,
        )

        # --------------------------------------------------------------
        # Quality gates
        # --------------------------------------------------------------
        derived_metrics = dict(
            quality_metrics
            or {}
        )

        derived_metrics.setdefault(
            "grounding_compliance",
            grounding_compliance,
        )
        derived_metrics.setdefault(
            "red_team_complete",
            red_team_complete,
        )

        gate_result = self.evaluate_quality_gates(
            metrics=derived_metrics,
            blueprint=blueprint,
        )

        violations.extend(
            gate_result[
                "violations"
            ]
        )

        # Optional audit-receipt requirement.
        if output_contract.get(
            "require_audit_receipt"
        ) is True:
            if not isinstance(
                audit_receipt,
                Mapping,
            ):
                violations.append(
                    _violation(
                        "AUDIT_RECEIPT_REQUIRED",
                        "Output contract requires an audit receipt.",
                        path="audit_receipt",
                    )
                )

        release_allowed = not violations

        decision = PolicyDecision(
            decision_id=_stable_id(
                "RELEASEDECISION",
                {
                    "blueprint_fingerprint": blueprint_validation[
                        "metadata"
                    ][
                        "blueprint_fingerprint"
                    ],
                    "report_fingerprint": _sha256(
                        report
                    ),
                    "grounding": grounding_list,
                    "red_team": red_team_list,
                    "quality_metrics": derived_metrics,
                    "release_allowed": release_allowed,
                    "violations": violations,
                },
            ),
            decision_type="release",
            allowed=release_allowed,
            action="release",
            stage="release_validation",
            reason_codes=tuple(
                [
                    "all_hard_gates_and_output_contracts_passed"
                ]
                if release_allowed
                else [
                    "release_blocked_by_policy_or_quality_gate"
                ]
            ),
            violations=tuple(
                deepcopy(
                    violations
                )
            ),
            warnings=tuple(
                deepcopy(
                    warnings
                )
            ),
            effective_policy=deepcopy(
                blueprint_validation[
                    "effective_policy"
                ]
            ),
            authorizes_release=release_allowed,
        ).to_dict()

        decision[
            "release_allowed"
        ] = release_allowed

        decision[
            "quality_gate_results"
        ] = gate_result[
            "gate_results"
        ]

        decision[
            "derived_quality_metrics"
        ] = _json_safe(
            derived_metrics
        )

        decision[
            "metadata"
        ] = {
            "service_version": SERVICE_VERSION,
            "output_schema_version": OUTPUT_SCHEMA_VERSION,
            "report_fingerprint": _sha256(
                report
            ),
            "grounding_assessment_count": len(
                grounding_list
            ),
            "red_team_assessment_count": len(
                red_team_list
            ),
            "reasoning_context_checked": (
                reasoning_context
                is not None
            ),
            "audit_receipt_checked": bool(
                isinstance(
                    audit_receipt,
                    Mapping,
                )
            ),
            "authorizes_release": release_allowed,
            "llm_call_performed": False,
            "search_performed": False,
            "grounding_performed": False,
            "red_team_performed": False,
            "modifies_research_depth": False,
            "private_chain_of_thought_stored": False,
        }

        self._last_decision = deepcopy(
            decision
        )

        return decision

    def require_release_allowed(
        self,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        decision = self.validate_release(
            **kwargs
        )

        if not decision[
            "release_allowed"
        ]:
            raise PolicyViolationError(
                "; ".join(
                    violation[
                        "code"
                    ]
                    + ": "
                    + violation[
                        "message"
                    ]
                    for violation in decision[
                        "violations"
                    ]
                )
            )

        return decision

    def get_last_decision(
        self,
    ) -> Dict[str, Any]:
        return deepcopy(
            self._last_decision
        )

    # ------------------------------------------------------------------
    # Runtime action rules
    # ------------------------------------------------------------------

    def _authorize_llm_call(
        self,
        *,
        request: Mapping[str, Any],
        effective_policy: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        reasons: list[str],
    ) -> None:
        route = request.get(
            "model_route"
        )

        if not isinstance(
            route,
            Mapping,
        ):
            violations.append(
                _violation(
                    "MODEL_ROUTE_REQUIRED",
                    "LLM calls require an already-issued ModelRouteDecision.",
                    path="request.model_route",
                )
            )
            return

        provider = _enum(
            route.get(
                "provider"
            )
        )

        if provider not in LOCAL_LLM_PROVIDERS:
            violations.append(
                _violation(
                    "NON_LOCAL_LLM_PROVIDER_BLOCKED",
                    f"LLM provider {provider!r} is not allowed in the local-first "
                    "product path.",
                    path="request.model_route.provider",
                )
            )

        paid = route.get(
            "paid_model_selected"
        )

        if paid is not False:
            violations.append(
                _violation(
                    "PAID_LLM_BLOCKED",
                    "Model route must explicitly indicate "
                    "paid_model_selected=False.",
                    path="request.model_route.paid_model_selected",
                )
            )

        if route.get(
            "model_call_performed"
        ) is True:
            violations.append(
                _violation(
                    "ROUTE_DECISION_MUST_PRECEDE_CALL",
                    "ModelRouteDecision must authorize before the model call is "
                    "performed.",
                    path="request.model_route.model_call_performed",
                )
            )

        if route.get(
            "modifies_research_depth"
        ) is True:
            violations.append(
                _violation(
                    "RESEARCH_DEPTH_MUTATION_FORBIDDEN",
                    "Model route may not mutate research_depth.",
                    path="request.model_route.modifies_research_depth",
                )
            )

        if not violations:
            reasons.extend(
                [
                    "model_route_present",
                    "local_llm_provider",
                    "paid_llm_not_selected",
                ]
            )

    def _authorize_web_search(
        self,
        *,
        request: Mapping[str, Any],
        effective_policy: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        reasons: list[str],
    ) -> None:
        provider = _enum(
            request.get(
                "provider"
            )
        )

        paid = request.get(
            "paid"
        )

        requires_paid_key = request.get(
            "requires_paid_api_key",
            False,
        )

        if not isinstance(
            requires_paid_key,
            bool,
        ):
            violations.append(
                _violation(
                    "INVALID_SEARCH_COST_FLAG",
                    "requires_paid_api_key must be boolean.",
                    path="request.requires_paid_api_key",
                )
            )

        if provider in PAID_OR_CREDENTIALLED_SEARCH_PROVIDERS:
            violations.append(
                _violation(
                    "PAID_SEARCH_PROVIDER_BLOCKED",
                    f"Search provider {provider!r} is disabled in the default "
                    "zero-cost product path.",
                    path="request.provider",
                )
            )

        elif provider not in ZERO_COST_SEARCH_PROVIDERS:
            violations.append(
                _violation(
                    "UNRECOGNIZED_SEARCH_PROVIDER",
                    "Unknown search provider cannot be assumed zero-cost.",
                    path="request.provider",
                )
            )

        if paid is not None and paid is not False:
            violations.append(
                _violation(
                    "PAID_SEARCH_BLOCKED",
                    "Search request must not require payment.",
                    path="request.paid",
                )
            )

        if requires_paid_key is True:
            violations.append(
                _violation(
                    "PAID_SEARCH_CREDENTIAL_BLOCKED",
                    "Paid search API credentials are disabled.",
                    path="request.requires_paid_api_key",
                )
            )

        if not violations:
            reasons.extend(
                [
                    "zero_cost_search_provider",
                    "paid_search_api_not_used",
                ]
            )

    def _authorize_source_fetch(
        self,
        *,
        request: Mapping[str, Any],
        effective_policy: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        reasons: list[str],
    ) -> None:
        paid = request.get(
            "paid",
            False,
        )

        if not isinstance(
            paid,
            bool,
        ):
            violations.append(
                _violation(
                    "INVALID_FETCH_COST_FLAG",
                    "source_fetch paid flag must be boolean.",
                    path="request.paid",
                )
            )

        elif paid:
            violations.append(
                _violation(
                    "PAID_SOURCE_FETCH_BLOCKED",
                    "Paid source-fetch service is not permitted.",
                    path="request.paid",
                )
            )

        if not violations:
            reasons.append(
                "direct_or_zero_cost_source_fetch"
            )

    def _authorize_model_escalation(
        self,
        *,
        request: Mapping[str, Any],
        effective_policy: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        reasons: list[str],
    ) -> None:
        escalation = effective_policy[
            "escalation"
        ]

        allowed_actions = set(
            _unique_strings(
                escalation.get(
                    "allowed_actions"
                )
                or []
            )
        )

        if "model_escalation" not in allowed_actions:
            violations.append(
                _violation(
                    "MODEL_ESCALATION_NOT_ALLOWED",
                    "Blueprint does not allow model_escalation.",
                )
            )

        route = request.get(
            "model_route"
        )

        if not isinstance(
            route,
            Mapping,
        ):
            violations.append(
                _violation(
                    "MODEL_ESCALATION_ROUTE_REQUIRED",
                    "Model escalation requires a ModelRouterService route decision.",
                    path="request.model_route",
                )
            )
            return

        if route.get(
            "escalation_requested"
        ) is not True:
            violations.append(
                _violation(
                    "MODEL_ESCALATION_FLAG_REQUIRED",
                    "Escalation route must record escalation_requested=True.",
                    path="request.model_route.escalation_requested",
                )
            )

        if route.get(
            "escalation_applied"
        ) is not True:
            violations.append(
                _violation(
                    "MODEL_ESCALATION_NOT_APPLIED",
                    "Router did not apply a valid escalation.",
                    path="request.model_route.escalation_applied",
                )
            )

        if route.get(
            "paid_model_selected"
        ) is not False:
            violations.append(
                _violation(
                    "PAID_MODEL_ESCALATION_BLOCKED",
                    "Escalation cannot select a paid model.",
                    path="request.model_route.paid_model_selected",
                )
            )

        if _enum(
            route.get(
                "provider"
            )
        ) not in LOCAL_LLM_PROVIDERS:
            violations.append(
                _violation(
                    "NON_LOCAL_MODEL_ESCALATION_BLOCKED",
                    "Escalation cannot leave the local provider set.",
                    path="request.model_route.provider",
                )
            )

        if not violations:
            reasons.extend(
                [
                    "model_escalation_allowed_by_blueprint",
                    "model_router_authorized_escalation",
                    "local_unpaid_escalation",
                ]
            )

    def _authorize_refinement_action(
        self,
        *,
        request: Mapping[str, Any],
        effective_policy: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        reasons: list[str],
    ) -> None:
        requested_action = _enum(
            request.get(
                "refinement_action"
            )
            or request.get(
                "action"
            )
        )

        if requested_action not in ALLOWED_REFINEMENT_ACTIONS:
            violations.append(
                _violation(
                    "UNKNOWN_REFINEMENT_ACTION",
                    f"Unknown refinement action={requested_action!r}.",
                    path="request.refinement_action",
                )
            )
            return

        allowed = set(
            _unique_strings(
                effective_policy[
                    "escalation"
                ].get(
                    "allowed_actions"
                )
                or []
            )
        )

        if requested_action not in allowed:
            violations.append(
                _violation(
                    "REFINEMENT_ACTION_NOT_ALLOWED",
                    f"Blueprint does not allow refinement action "
                    f"{requested_action!r}.",
                    path="request.refinement_action",
                )
            )

        round_index = _nonnegative_int(
            request.get(
                "refinement_round",
                0,
            ),
            name="request.refinement_round",
        )

        max_rounds = effective_policy[
            "runtime_policy"
        ].get(
            "max_refinement_rounds",
            0,
        )

        max_rounds = _nonnegative_int(
            max_rounds,
            name="runtime_policy.max_refinement_rounds",
        )

        # round_index is 1-based in execution requests; zero means before first.
        if round_index > max_rounds:
            violations.append(
                _violation(
                    "REFINEMENT_ROUND_LIMIT_EXCEEDED",
                    f"Requested refinement round {round_index} exceeds "
                    f"max_refinement_rounds={max_rounds}.",
                    path="request.refinement_round",
                )
            )

        if not violations:
            reasons.extend(
                [
                    "refinement_action_allowed",
                    "refinement_round_within_policy",
                ]
            )

    # ------------------------------------------------------------------
    # Stage invariants
    # ------------------------------------------------------------------

    def _validate_reasoning_context_result(
        self,
        *,
        result: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        require_finalized: bool = False,
        path_prefix: str = "",
    ) -> None:
        manifest = result.get(
            "evidence_manifest"
        )

        if not isinstance(
            manifest,
            Mapping,
        ):
            violations.append(
                _violation(
                    "EVIDENCE_MANIFEST_REQUIRED",
                    "Reasoning context requires evidence_manifest.",
                    path=self._path(
                        path_prefix,
                        "evidence_manifest",
                    ),
                )
            )
            return

        accepted = set(
            _unique_strings(
                manifest.get(
                    "all_accepted_evidence_ids"
                ),
                uppercase=True,
            )
        )
        examined = set(
            _unique_strings(
                manifest.get(
                    "examined_evidence_ids"
                ),
                uppercase=True,
            )
        )
        relevant = set(
            _unique_strings(
                manifest.get(
                    "relevant_evidence_ids"
                ),
                uppercase=True,
            )
        )
        final_ids = set(
            _unique_strings(
                manifest.get(
                    "final_reasoning_context_ids"
                ),
                uppercase=True,
            )
        )
        referenced = set(
            _unique_strings(
                manifest.get(
                    "referenced_evidence_ids"
                ),
                uppercase=True,
            )
        )
        unexamined = set(
            _unique_strings(
                manifest.get(
                    "unexamined_evidence_ids"
                ),
                uppercase=True,
            )
        )

        context_finalized = bool(
            (
                result.get(
                    "metadata"
                )
                or {}
            ).get(
                "context_finalized",
                False,
            )
        )

        if require_finalized and not context_finalized:
            violations.append(
                _violation(
                    "FINALIZED_CONTEXT_REQUIRED",
                    "Release requires finalized reasoning context.",
                    path=self._path(
                        path_prefix,
                        "metadata.context_finalized",
                    ),
                )
            )

        if context_finalized or require_finalized:
            if accepted - examined:
                violations.append(
                    _violation(
                        "UNEXAMINED_ACCEPTED_EVIDENCE",
                        "Finalized reasoning context contains accepted evidence "
                        "that was not examined.",
                        path=self._path(
                            path_prefix,
                            "evidence_manifest",
                        ),
                    )
                )

            if unexamined:
                violations.append(
                    _violation(
                        "UNEXAMINED_EVIDENCE_LIST_NONEMPTY",
                        "Finalized reasoning context must have no unexamined "
                        "evidence IDs.",
                        path=self._path(
                            path_prefix,
                            "evidence_manifest.unexamined_evidence_ids",
                        ),
                    )
                )

            if not relevant <= examined:
                violations.append(
                    _violation(
                        "RELEVANT_NOT_SUBSET_OF_EXAMINED",
                        "relevant_evidence_ids must be a subset of examined.",
                        path=self._path(
                            path_prefix,
                            "evidence_manifest.relevant_evidence_ids",
                        ),
                    )
                )

            if not referenced <= final_ids:
                violations.append(
                    _violation(
                        "REFERENCED_NOT_IN_FINAL_CONTEXT",
                        "referenced evidence must be included in final reasoning "
                        "context.",
                        path=self._path(
                            path_prefix,
                            "evidence_manifest.referenced_evidence_ids",
                        ),
                    )
                )

            if not final_ids <= examined:
                violations.append(
                    _violation(
                        "FINAL_CONTEXT_NOT_SUBSET_OF_EXAMINED",
                        "final_reasoning_context_ids must be a subset of examined.",
                        path=self._path(
                            path_prefix,
                            "evidence_manifest.final_reasoning_context_ids",
                        ),
                    )
                )

    def _validate_reasoning_candidate_result(
        self,
        *,
        stage: str,
        result: Mapping[str, Any],
        violations: list[Dict[str, Any]],
    ) -> None:
        metadata = result.get(
            "metadata"
        )

        if isinstance(
            metadata,
            Mapping,
        ):
            if metadata.get(
                "source_independence_inferred_from_group_ids"
            ) is True:
                violations.append(
                    _violation(
                        "FALSE_SOURCE_INDEPENDENCE_INFERENCE",
                        f"{stage} inferred source independence from identifiers.",
                        path="metadata.source_independence_inferred_from_group_ids",
                    )
                )

            if metadata.get(
                "search_performed"
            ) is True:
                violations.append(
                    _violation(
                        "SEARCH_OUTSIDE_DISCOVERY_BOUNDARY",
                        f"{stage} must not perform evidence discovery search.",
                        path="metadata.search_performed",
                    )
                )

    def _validate_grounding_result(
        self,
        *,
        result: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        path_prefix: str = "",
    ) -> None:
        if not isinstance(
            result,
            Mapping,
        ):
            violations.append(
                _violation(
                    "INVALID_GROUNDING_ASSESSMENT",
                    "Grounding assessment must be an object.",
                    path=path_prefix,
                )
            )
            return

        if result.get(
            "metadata",
            {}
        ).get(
            "search_performed"
        ) is True:
            violations.append(
                _violation(
                    "GROUNDING_SEARCH_BOUNDARY_VIOLATION",
                    "GroundingService may not perform web/evidence discovery.",
                    path=self._path(
                        path_prefix,
                        "metadata.search_performed",
                    ),
                )
            )

        invalid_ids = (
            result.get(
                "evidence_manifest",
                {}
            ).get(
                "invalid_referenced_evidence_ids"
            )
            if isinstance(
                result.get(
                    "evidence_manifest"
                ),
                Mapping,
            )
            else []
        )

        if invalid_ids:
            violations.append(
                _violation(
                    "INVALID_GROUNDING_EVIDENCE_REFERENCE",
                    "Grounding assessment contains invalid referenced evidence IDs.",
                    path=self._path(
                        path_prefix,
                        "evidence_manifest.invalid_referenced_evidence_ids",
                    ),
                )
            )

        assessment_id = _clean(
            result.get(
                "grounding_assessment_id"
            )
        )

        if not assessment_id:
            violations.append(
                _violation(
                    "GROUNDING_ASSESSMENT_ID_REQUIRED",
                    "Final grounding assessment requires stable "
                    "grounding_assessment_id.",
                    path=self._path(
                        path_prefix,
                        "grounding_assessment_id",
                    ),
                )
            )

        epistemic_status = _enum(
            result.get(
                "epistemic_status"
            )
            or result.get(
                "grounding_status"
            )
        )

        if not epistemic_status:
            violations.append(
                _violation(
                    "GROUNDING_STATUS_REQUIRED",
                    "Grounding assessment requires epistemic_status.",
                    path=self._path(
                        path_prefix,
                        "epistemic_status",
                    ),
                )
            )

    def _validate_red_team_result(
        self,
        *,
        result: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        path_prefix: str = "",
    ) -> None:
        if not isinstance(
            result,
            Mapping,
        ):
            violations.append(
                _violation(
                    "INVALID_RED_TEAM_ASSESSMENT",
                    "Red-team assessment must be an object.",
                    path=path_prefix,
                )
            )
            return

        assessment_id = _clean(
            result.get(
                "red_team_assessment_id"
            )
            or result.get(
                "assessment_id"
            )
        )

        if not assessment_id:
            violations.append(
                _violation(
                    "RED_TEAM_ASSESSMENT_ID_REQUIRED",
                    "Red-team result requires a stable assessment ID.",
                    path=self._path(
                        path_prefix,
                        "red_team_assessment_id",
                    ),
                )
            )

        status = _enum(
            result.get(
                "status"
            )
            or result.get(
                "review_status"
            )
            or (
                result.get(
                    "metadata"
                )
                or {}
            ).get(
                "run_status"
            )
        )

        explicit_complete = result.get(
            "red_team_complete"
        )

        if explicit_complete is None:
            explicit_complete = (
                result.get(
                    "metadata"
                )
                or {}
            ).get(
                "red_team_complete"
            )

        if explicit_complete is not None and not isinstance(
            explicit_complete,
            bool,
        ):
            violations.append(
                _violation(
                    "INVALID_RED_TEAM_COMPLETE_FLAG",
                    "red_team_complete must be boolean.",
                    path=self._path(
                        path_prefix,
                        "red_team_complete",
                    ),
                )
            )

        complete_by_status = status in {
            "complete",
            "completed",
            "ok",
            "passed",
            "pass",
        }

        if (
            explicit_complete is not True
            and not complete_by_status
        ):
            violations.append(
                _violation(
                    "RED_TEAM_INCOMPLETE",
                    "Red-team assessment is not complete.",
                    path=path_prefix,
                )
            )

        if status in {
            "failed",
            "error",
            "incomplete",
            "blocked",
        }:
            violations.append(
                _violation(
                    "RED_TEAM_FAILED",
                    f"Red-team status={status!r} is not releaseable.",
                    path=self._path(
                        path_prefix,
                        "status",
                    ),
                )
            )

    # ------------------------------------------------------------------
    # Report contract
    # ------------------------------------------------------------------

    def _validate_report_contract(
        self,
        *,
        report: Mapping[str, Any],
        output_contract: Mapping[str, Any],
        violations: list[Dict[str, Any]],
        warnings: list[Dict[str, Any]],
        final_evidence_ids: Optional[set[str]],
    ) -> None:
        output_type = _enum(
            output_contract.get(
                "output_type"
            )
        )

        if output_type and output_type != "research_report":
            warnings.append(
                _violation(
                    "NON_STANDARD_OUTPUT_TYPE",
                    f"Output type is {output_type!r}; research-report-specific "
                    "section checks may not all apply.",
                    path="output_contract.output_type",
                    severity="monitor",
                )
            )

        if output_contract.get(
            "require_evidence_trail"
        ) is True:
            if not self._has_nonempty_section(
                report,
                (
                    "evidence_trail",
                    "evidence",
                    "sources",
                ),
            ):
                violations.append(
                    _violation(
                        "EVIDENCE_TRAIL_REQUIRED",
                        "Final report must include a non-empty evidence trail.",
                        path="report.evidence_trail",
                    )
                )

        if output_contract.get(
            "require_uncertainty_section"
        ) is True:
            if not self._has_nonempty_section(
                report,
                (
                    "uncertainty",
                    "uncertainties",
                    "critical_unknowns",
                ),
            ):
                violations.append(
                    _violation(
                        "UNCERTAINTY_SECTION_REQUIRED",
                        "Final report must include uncertainty/critical unknowns.",
                        path="report",
                    )
                )

        if output_contract.get(
            "require_counterevidence_section"
        ) is True:
            if not self._has_nonempty_section(
                report,
                (
                    "counterevidence",
                    "counterevidence_and_alternative_explanations",
                    "counterevidence_alternative_explanations",
                    "alternative_explanations",
                ),
            ):
                violations.append(
                    _violation(
                        "COUNTEREVIDENCE_SECTION_REQUIRED",
                        "Final report must include counterevidence or alternative "
                        "explanations.",
                        path="report",
                    )
                )

        required_sections = output_contract.get(
            "required_sections"
        )

        if required_sections is not None:
            for section in _unique_strings(
                required_sections
            ):
                if not self._has_nonempty_section(
                    report,
                    (
                        section,
                    ),
                ):
                    violations.append(
                        _violation(
                            "REQUIRED_REPORT_SECTION_MISSING",
                            f"Required report section {section!r} is missing/empty.",
                            path=f"report.{section}",
                        )
                    )

        insight_records = self._extract_report_insights(
            report
        )

        if output_contract.get(
            "stable_insight_ids"
        ) is True:
            for index, insight in enumerate(
                insight_records
            ):
                if not _clean(
                    insight.get(
                        "insight_id"
                    )
                ):
                    violations.append(
                        _violation(
                            "STABLE_INSIGHT_ID_REQUIRED",
                            "Report insight is missing insight_id.",
                            path=f"report.insights[{index}].insight_id",
                        )
                    )

        if output_contract.get(
            "preserve_revision_lineage"
        ) is True:
            for index, insight in enumerate(
                insight_records
            ):
                if not _clean(
                    insight.get(
                        "insight_revision_id"
                    )
                ):
                    violations.append(
                        _violation(
                            "INSIGHT_REVISION_ID_REQUIRED",
                            "Report insight is missing insight_revision_id.",
                            path=f"report.insights[{index}].insight_revision_id",
                        )
                    )

        report_evidence_ids = self._collect_report_evidence_ids(
            report
        )

        if final_evidence_ids is not None:
            invalid = (
                report_evidence_ids
                - final_evidence_ids
            )

            if invalid:
                violations.append(
                    _violation(
                        "REPORT_REFERENCES_OUTSIDE_FINAL_CONTEXT",
                        "Report references evidence IDs outside the finalized "
                        "reasoning context: "
                        + ", ".join(
                            sorted(
                                invalid
                            )
                        ),
                        path="report",
                    )
                )

    # ------------------------------------------------------------------
    # Gate helpers
    # ------------------------------------------------------------------

    def _validate_gate(
        self,
        gate: Mapping[str, Any],
        *,
        index: int,
    ) -> Dict[str, Any]:
        if not isinstance(
            gate,
            Mapping,
        ):
            raise PolicyEnforcementValidationError(
                "quality gate must be an object."
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

        gate_type = _enum(
            gate.get(
                "gate_type"
            )
            or "hard"
        )

        on_fail = _enum(
            gate.get(
                "on_fail"
            )
            or "block"
        )

        if not gate_id:
            raise PolicyEnforcementValidationError(
                "gate_id cannot be empty."
            )

        if not metric:
            raise PolicyEnforcementValidationError(
                "metric cannot be empty."
            )

        if operator not in ALLOWED_OPERATORS:
            raise PolicyEnforcementValidationError(
                f"unsupported operator={operator!r}."
            )

        if "threshold" not in gate:
            raise PolicyEnforcementValidationError(
                "threshold is required."
            )

        if gate_type not in ALLOWED_GATE_TYPES:
            raise PolicyEnforcementValidationError(
                f"unsupported gate_type={gate_type!r}."
            )

        if on_fail not in ALLOWED_GATE_ACTIONS:
            raise PolicyEnforcementValidationError(
                f"unsupported on_fail={on_fail!r}."
            )

        return {
            "gate_id": gate_id,
            "metric": metric,
            "operator": operator,
            "threshold": deepcopy(
                gate[
                    "threshold"
                ]
            ),
            "gate_type": gate_type,
            "on_fail": on_fail,
        }

    @staticmethod
    def _compare(
        actual: Any,
        operator: str,
        threshold: Any,
    ) -> bool:
        try:
            if operator == "==":
                return actual == threshold
            if operator == "!=":
                return actual != threshold
            if operator == ">":
                return actual > threshold
            if operator == ">=":
                return actual >= threshold
            if operator == "<":
                return actual < threshold
            if operator == "<=":
                return actual <= threshold
        except TypeError:
            return False

        raise PolicyEnforcementValidationError(
            f"Unsupported operator={operator!r}."
        )

    # ------------------------------------------------------------------
    # Cost policy
    # ------------------------------------------------------------------

    def _effective_cost_policy(
        self,
        blueprint: Mapping[str, Any],
    ) -> Dict[str, bool]:
        raw = blueprint.get(
            "cost_policy"
        )

        if raw is None:
            raw = {}

        if not isinstance(
            raw,
            Mapping,
        ):
            raise PolicyEnforcementValidationError(
                "blueprint.cost_policy must be an object when present."
            )

        output = {}

        for key, hard_default in HARD_COST_FLOOR.items():
            value = raw.get(
                key,
                hard_default,
            )

            if not isinstance(
                value,
                bool,
            ):
                raise PolicyEnforcementValidationError(
                    f"cost_policy.{key} must be boolean."
                )

            output[
                key
            ] = value

        return output

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_mapping(
        value: Any,
        *,
        name: str,
    ) -> Dict[str, Any]:
        if not isinstance(
            value,
            Mapping,
        ):
            raise PolicyEnforcementValidationError(
                f"{name} must be an object."
            )

        return deepcopy(
            dict(
                value
            )
        )

    @staticmethod
    def _section(
        blueprint: Mapping[str, Any],
        name: str,
    ) -> Dict[str, Any]:
        value = blueprint.get(
            name
        )

        if value is None:
            return {}

        if not isinstance(
            value,
            Mapping,
        ):
            raise PolicyEnforcementValidationError(
                f"blueprint.{name} must be an object."
            )

        return deepcopy(
            dict(
                value
            )
        )

    @staticmethod
    def _path(
        prefix: str,
        field: str,
    ) -> str:
        if not prefix:
            return field

        if not field:
            return prefix

        return (
            prefix
            + "."
            + field
        )

    @staticmethod
    def _has_nonempty_section(
        report: Mapping[str, Any],
        names: Sequence[str],
    ) -> bool:
        for name in names:
            value = report.get(
                name
            )

            if isinstance(
                value,
                str,
            ) and _clean(
                value
            ):
                return True

            if isinstance(
                value,
                Mapping,
            ) and value:
                return True

            if isinstance(
                value,
                (list, tuple)
            ) and len(
                value
            ) > 0:
                return True

        return False

    @staticmethod
    def _extract_report_insights(
        report: Mapping[str, Any],
    ) -> list[Mapping[str, Any]]:
        for key in (
            "hidden_insights",
            "insights",
            "candidate_insights",
            "released_insights",
        ):
            value = report.get(
                key
            )

            if isinstance(
                value,
                list,
            ):
                return [
                    item
                    for item in value
                    if isinstance(
                        item,
                        Mapping,
                    )
                ]

        return []

    @staticmethod
    def _collect_report_evidence_ids(
        report: Mapping[str, Any],
    ) -> set[str]:
        ids = set()

        def visit(
            node: Any,
        ) -> None:
            if isinstance(
                node,
                Mapping,
            ):
                for key, value in node.items():
                    normalized = _enum(
                        key
                    )

                    if normalized in {
                        "evidence_ids",
                        "supporting_evidence_ids",
                        "counterevidence_ids",
                        "counter_incentive_evidence_ids",
                        "analogical_evidence_ids",
                        "feasibility_evidence_ids",
                    }:
                        ids.update(
                            _unique_strings(
                                value,
                                uppercase=True,
                            )
                        )
                    else:
                        visit(
                            value
                        )

            elif isinstance(
                node,
                list,
            ):
                for item in node:
                    visit(
                        item
                    )

        visit(
            report
        )

        return ids

    @staticmethod
    def _normalize_assessment_list(
        value: Any,
        *,
        plural_keys: Sequence[str],
    ) -> list[Mapping[str, Any]]:
        if value is None:
            return []

        if isinstance(
            value,
            Mapping,
        ):
            for key in plural_keys:
                nested = value.get(
                    key
                )

                if isinstance(
                    nested,
                    list,
                ):
                    return [
                        item
                        for item in nested
                        if isinstance(
                            item,
                            Mapping,
                        )
                    ]

            return [
                value
            ]

        if isinstance(
            value,
            list,
        ):
            return [
                item
                for item in value
                if isinstance(
                    item,
                    Mapping,
                )
            ]

        raise PolicyEnforcementValidationError(
            "Assessment input must be an object or list of objects."
        )


# Compatibility alias.
ReleasePolicyEnforcementService = PolicyEnforcementService