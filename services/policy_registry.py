"""
Versioned deterministic policy registry for the Event Intelligence pipeline.

Recommended location:
    services/policy_registry.py

Role
----
Product policy / governance configuration
            ↓
      PolicyRegistryService
            ↓
 immutable versioned policy bundle
            ↓
       policy snapshot
            ↓
      BlueprintCompiler
            ↓
 compiled execution Blueprint
            ↓
 PolicyEnforcementService / ModelRouterService / pipeline

Core boundary
-------------
PolicyRegistryService defines and versions product policy.

It does NOT:
- interpret user intent;
- interpret user feedback;
- compile the final Blueprint;
- authorize runtime operations;
- choose a model;
- perform LLM inference;
- perform web search;
- ground claims;
- authorize report release.

The intended authority split is:

    PolicyRegistry
        defines/version-controls what policy exists

    BlueprintCompiler
        deterministically combines policy with validated intent/preferences

    PolicyEnforcementService
        authorizes/checks actual execution and release

    ModelRouterService
        chooses the model within compiled routing policy

Free/local-first hard floor
---------------------------
This product has zero mandatory external cost.

Every registered policy bundle must preserve:

    paid_external_services_allowed = False
    paid_llm_calls_allowed = False
    paid_search_apis_allowed = False

A policy version that attempts to weaken this floor is rejected at registration.

Fast / Deep
-----------
Fast and Deep are execution profiles, not different epistemic standards.

Both profiles preserve the same:
- grounding requirement;
- red-team requirement;
- evidence-completeness rule;
- provenance requirement;
- counterevidence requirement;
- uncertainty requirement;
- source-independence semantics;
- release gates.

Profiles may vary:
- wall-clock budget;
- search rounds;
- query count;
- source-fetch count;
- number of insights sent to external discovery;
- refinement rounds;
- model-routing preference within the local model catalog.

Default profiles
----------------
Fast:
    300s total
    260s research soft deadline
    40s protected finalization reserve
    1 refinement round
    2 search rounds / insight
    6 queries / insight
    8 fetched sources / insight
    2 insights eligible for external discovery

Deep:
    1200s total
    1120s research soft deadline
    80s protected finalization reserve
    4 refinement rounds
    5 search rounds / insight
    25 queries / insight
    35 fetched sources / insight
    6 insights eligible for external discovery

These are versioned initial defaults and should be tuned through evaluation,
not changed ad hoc in agents.

Persistence
-----------
The registry uses local SQLite and immutable policy versions.

`db_path=":memory:"` is convenient for tests.
For durable local use:

    PolicyRegistryService(db_path="data/policy_registry.sqlite3")

Registering an existing version with different content fails closed.
Activation changes only the active-version pointer; historical versions remain.

BlueprintCompiler compatibility
-------------------------------
`get_policy_snapshot(...)` returns the contract expected by BlueprintCompiler:

    analysis_mode_policy
    product_governance
    hard_restrictions
    versions

It also returns canonical policy modules needed by the newer architecture:

    cost_policy
    execution_profile
    evidence_discovery_policy
    model_routing_policy
    reasoning_context_policy

BlueprintCompiler should preserve those canonical modules in the final Blueprint.
Older compiler versions may ignore unknown top-level snapshot fields; the
registry deliberately does not hide them for legacy compatibility.

No private chain-of-thought is requested, persisted or returned.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence


SERVICE_VERSION = "policy-registry-service-v1.0"
REGISTRY_SCHEMA_VERSION = "policy-registry-schema-v1.0"
DEFAULT_POLICY_BUNDLE_VERSION = "event-intelligence-policy-v1.0"

SUPPORTED_ANALYSIS_MODES = {
    "research",
}

SUPPORTED_RESEARCH_DEPTHS = {
    "fast",
    "deep",
}

HARD_COST_FLOOR = {
    "paid_external_services_allowed": False,
    "paid_llm_calls_allowed": False,
    "paid_search_apis_allowed": False,
}

REQUIRED_QUALITY_GATE_IDS = {
    "GROUNDING-REQUIRED",
    "RED-TEAM-REQUIRED",
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


class PolicyRegistryError(RuntimeError):
    """Base policy-registry error."""


class PolicyRegistryValidationError(PolicyRegistryError):
    """Policy document or snapshot violates the registry contract."""


class PolicyVersionConflictError(PolicyRegistryError):
    """A version identifier already exists with different content."""


class PolicyVersionNotFoundError(PolicyRegistryError):
    """Requested policy version does not exist."""


class PolicyActivationError(PolicyRegistryError):
    """Requested active-version change is invalid."""


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


def _sha256(value: Any) -> str:
    if isinstance(value, bytes):
        payload = value
    elif isinstance(value, str):
        payload = value.encode("utf-8")
    else:
        payload = _canonical_json_bytes(value)

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


def _utc_now_iso() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def _deep_merge(
    base: Mapping[str, Any],
    override: Mapping[str, Any],
) -> Dict[str, Any]:
    result = deepcopy(
        dict(base)
    )

    for key, value in override.items():
        if (
            isinstance(value, Mapping)
            and isinstance(result.get(key), Mapping)
        ):
            result[key] = _deep_merge(
                result[key],
                value,
            )
        else:
            result[key] = deepcopy(value)

    return result


def _contains_prohibited_field(
    value: Any,
    path: str = "",
) -> Optional[str]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = _enum(key)
            current = (
                f"{path}.{key}"
                if path
                else str(key)
            )

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
    if not isinstance(value, bool):
        raise PolicyRegistryValidationError(
            f"{name} must be boolean."
        )

    return value


def _positive_int(
    value: Any,
    *,
    name: str,
) -> int:
    if isinstance(value, bool):
        raise PolicyRegistryValidationError(
            f"{name} must be integer, not bool."
        )

    if isinstance(value, float) and not value.is_integer():
        raise PolicyRegistryValidationError(
            f"{name} must be integer."
        )

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PolicyRegistryValidationError(
            f"{name} must be integer."
        ) from exc

    if number <= 0:
        raise PolicyRegistryValidationError(
            f"{name} must be > 0."
        )

    return number


def _nonnegative_int(
    value: Any,
    *,
    name: str,
) -> int:
    if isinstance(value, bool):
        raise PolicyRegistryValidationError(
            f"{name} must be integer, not bool."
        )

    if isinstance(value, float) and not value.is_integer():
        raise PolicyRegistryValidationError(
            f"{name} must be integer."
        )

    try:
        number = int(value)
    except (TypeError, ValueError) as exc:
        raise PolicyRegistryValidationError(
            f"{name} must be integer."
        ) from exc

    if number < 0:
        raise PolicyRegistryValidationError(
            f"{name} must be >= 0."
        )

    return number


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []

    if isinstance(value, str):
        value = [value]

    if not isinstance(value, (list, tuple, set)):
        raise PolicyRegistryValidationError(
            "Expected string or sequence of strings."
        )

    result = []

    for item in value:
        text = _clean(item)

        if text and text not in result:
            result.append(text)

    return result


# ---------------------------------------------------------------------------
# Default product policy
# ---------------------------------------------------------------------------


def default_policy_bundle() -> Dict[str, Any]:
    """
    Canonical baseline policy bundle.

    This is product policy, not user preference.
    """
    return {
        "bundle_schema_version": REGISTRY_SCHEMA_VERSION,

        "analysis_modes": {
            "research": {
                "data_policy": {
                    "preserve_source_provenance": True,
                    "preserve_all_accepted_evidence": True,
                    "allow_external_search": True,
                },
                "execution_policy": {
                    "automatic_refinement_enabled": True,
                    "allow_targeted_retrieval_expansion": True,
                    "require_refinement_quality_recheck": True,
                },
                "reasoning_policy": {
                    "desired_depth": "standard",
                    "preferred_patterns": [
                        "cross_event_synthesis",
                        "mechanism",
                        "counterevidence",
                        "uncertainty",
                    ],
                    "avoid_patterns": [
                        "fact_restatement",
                        "unsupported_speculation",
                        "overconfident_prediction",
                    ],
                    "analytical_lenses": [],
                    "positive_examples": [],
                    "negative_examples": [],
                    "require_cross_event_synthesis": False,
                    "require_mechanism": True,
                    "require_second_order_effects": False,
                    "require_counterevidence": True,
                    "require_explicit_uncertainty": True,
                    "reject_fact_restatement_as_hidden_insight": True,
                    "use_positive_examples_for_quality_reference": True,
                    "prefer_concise_synthesis": False,
                },
                "selection_policy": {
                    "selector": "mmr",
                    "max_selected_clusters": 3,
                    "preserve_material_updates": True,
                },
                "runtime_policy": {
                    "overflow_strategy": "batch_then_consolidate",
                    "allow_silent_truncation": False,
                    "stop_on_no_new_evidence": True,
                    "stop_on_no_material_change": True,
                },
                "output_contract": {
                    "output_type": "research_report",
                    "require_evidence_trail": True,
                    "require_uncertainty_section": True,
                    "require_counterevidence_section": True,
                    "stable_insight_ids": True,
                    "preserve_revision_lineage": True,
                },
            }
        },

        "research_profiles": {
            "fast": {
                "execution_policy": {
                    "research_depth": "fast",
                },
                "runtime_policy": {
                    "max_run_seconds": 600,
                    "research_soft_deadline_seconds": 420,
                    "reserve_finalization_seconds": 180,
                    "max_refinement_rounds": 1,
                },
                "evidence_discovery_policy": {
                    "max_search_rounds_per_insight": 2,
                    "max_search_queries_per_insight": 4,
                    "max_sources_fetched_per_insight": 4,
                    "max_insights_for_external_discovery": 2,
                },
                "model_routing_policy": {
                    "profile_preference": "smallest_sufficient",
                },
            },
            "deep": {
                "execution_policy": {
                    "research_depth": "deep",
                },
                "runtime_policy": {
                    "max_run_seconds": 1200,
                    "research_soft_deadline_seconds": 1000,
                    "reserve_finalization_seconds": 200,
                    "max_refinement_rounds": 4,
                },
                "evidence_discovery_policy": {
                    "max_search_rounds_per_insight": 5,
                    "max_search_queries_per_insight": 25,
                    "max_sources_fetched_per_insight": 35,
                    "max_insights_for_external_discovery": 6,
                },
                "model_routing_policy": {
                    "profile_preference": "stronger_for_high_reasoning_tasks",
                },
            },
        },

        "product_governance": {
            "data_policy": {
                "preserve_source_provenance": True,
                "preserve_all_accepted_evidence": True,
            },
            "execution_policy": {
                "require_refinement_quality_recheck": True,
            },
            "reasoning_policy": {
                "require_mechanism": True,
                "require_counterevidence": True,
                "require_explicit_uncertainty": True,
                "reject_fact_restatement_as_hidden_insight": True,
            },
            "quality_policy": {
                "gates": [
                    {
                        "gate_id": "GROUNDING-REQUIRED",
                        "metric": "grounding_compliance",
                        "operator": "==",
                        "threshold": True,
                        "gate_type": "hard",
                        "on_fail": "block",
                    },
                    {
                        "gate_id": "RED-TEAM-REQUIRED",
                        "metric": "red_team_complete",
                        "operator": "==",
                        "threshold": True,
                        "gate_type": "hard",
                        "on_fail": "block",
                    },
                ],
            },
            "output_contract": {
                "output_type": "research_report",
                "require_evidence_trail": True,
                "require_uncertainty_section": True,
                "require_counterevidence_section": True,
                "stable_insight_ids": True,
                "preserve_revision_lineage": True,
            },
            "escalation": {
                "allowed_actions": [
                    "retrieve_support",
                    "retrieve_counterevidence",
                    "expand_graph_context",
                    "compare_baseline",
                    "refine_reasoning",
                    "broaden_evidence_search",
                    "model_escalation",
                ],
                "model_escalation_requires_router": True,
            },
        },

        "hard_restrictions": {
            "data_policy": {
                "preserve_source_provenance": True,
                "preserve_all_accepted_evidence": True,
            },
            "runtime_policy": {
                "overflow_strategy": "batch_then_consolidate",
                "allow_silent_truncation": False,
                "stop_on_no_new_evidence": True,
                "stop_on_no_material_change": True,
            },
            "quality_policy": {
                "gates": [
                    {
                        "gate_id": "GROUNDING-REQUIRED",
                        "metric": "grounding_compliance",
                        "operator": "==",
                        "threshold": True,
                        "gate_type": "hard",
                        "on_fail": "block",
                    },
                    {
                        "gate_id": "RED-TEAM-REQUIRED",
                        "metric": "red_team_complete",
                        "operator": "==",
                        "threshold": True,
                        "gate_type": "hard",
                        "on_fail": "block",
                    },
                ],
            },
            "output_contract": {
                "output_type": "research_report",
                "require_evidence_trail": True,
                "require_uncertainty_section": True,
                "require_counterevidence_section": True,
                "stable_insight_ids": True,
                "preserve_revision_lineage": True,
            },
            "escalation": {
                "model_escalation_requires_router": True,
            },
        },

        "cost_policy": deepcopy(
            HARD_COST_FLOOR
        ),

        "model_routing_policy": {
            "require_local": True,
            "allow_escalation": True,
            "max_escalations_per_stage": 1,
            "max_tier": "xlarge",
            "allowed_model_ids": [],
            "blocked_model_ids": [],
            "require_confirmed_availability": False,
            "task_overrides": {
                "prospective_reasoning": {
                    "minimum_tier": "medium",
                    "required_capabilities": [
                        "json",
                        "reasoning",
                        "strong_reasoning",
                    ],
                },
                "mechanism_analogy_analysis": {
                    "minimum_tier": "medium",
                    "required_capabilities": [
                        "json",
                        "reasoning",
                        "strong_reasoning",
                    ],
                },
                "red_team": {
                    "minimum_tier": "medium",
                    "required_capabilities": [
                        "json",
                        "reasoning",
                        "strong_reasoning",
                    ],
                },
                "grounding": {
                    "minimum_tier": "small",
                    "required_capabilities": [
                        "json",
                        "evidence_classification",
                    ],
                },
            },
        },

        "reasoning_context_policy": {
            "evidence_batch_max_characters": 16000,
            "structured_batch_max_characters": 14000,
            "preference_batch_max_characters": 8000,
            "batching_strategy": "complete_records_no_silent_truncation",
            "oversized_single_record_policy": "emit_one_record_batch_untruncated",
        },

        "evidence_policy": {
            "search_result_is_evidence": False,
            "search_snippet_is_evidence": False,
            "source_fetch_required_before_evidence_admission": True,
            "different_source_ids_prove_independence": False,
            "different_domains_prove_independence": False,
            "different_group_ids_prove_independence": False,
            "unknown_source_independence_preserved": True,
            "all_accepted_evidence_must_be_examined": True,
        },

        "prospective_reasoning_policy": {
            "direct_precedent_required": False,
            "factual_premises_must_be_grounded": True,
            "analogical_mechanism_evidence_allowed": True,
            "feasibility_check_required": True,
            "counter_incentive_search_required_when_material": True,
            "plausible_unprecedented_status_allowed": True,
        },

        "release_policy": {
            "allowed_final_epistemic_statuses": [
                "supported",
                "partially_supported",
                "inference_supported",
            ],
            "blocked_final_epistemic_statuses": [
                "unsupported",
                "contradicted",
                "unresolved",
            ],
            "grounding_required": True,
            "red_team_required": True,
            "evidence_trail_required": True,
            "uncertainty_required": True,
            "counterevidence_required": True,
            "stable_insight_ids_required": True,
            "revision_lineage_required": True,
        },
    }


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class PolicyRegistryService:
    """
    Local SQLite-backed immutable policy registry.
    """

    def __init__(
        self,
        *,
        db_path: str = ":memory:",
        install_default_policy: bool = True,
        default_policy_version: str = DEFAULT_POLICY_BUNDLE_VERSION,
    ) -> None:
        self.db_path = _clean(
            db_path
        ) or ":memory:"

        self.default_policy_version = _clean(
            default_policy_version
        )

        if not self.default_policy_version:
            raise PolicyRegistryValidationError(
                "default_policy_version cannot be empty."
            )

        if self.db_path != ":memory:":
            path = Path(
                self.db_path
            )
            path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

        self._connection = sqlite3.connect(
            self.db_path
        )
        self._connection.row_factory = sqlite3.Row

        self._initialize_schema()

        if install_default_policy:
            self._install_default_if_needed()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> "PolicyRegistryService":
        return self

    def __exit__(
        self,
        exc_type: Any,
        exc: Any,
        tb: Any,
    ) -> None:
        self.close()

    # ------------------------------------------------------------------
    # Registration / activation
    # ------------------------------------------------------------------

    def register_policy_bundle(
        self,
        *,
        version: str,
        bundle: Mapping[str, Any],
        activate: bool = False,
        notes: str = "",
        supersedes: str = "",
    ) -> Dict[str, Any]:
        """
        Register one immutable policy bundle.

        Re-registering the same version with identical content is idempotent.
        Re-registering the same version with different content fails closed.
        """
        version = _clean(
            version
        )

        if not version:
            raise PolicyRegistryValidationError(
                "version cannot be empty."
            )

        validated = self._validate_bundle(
            bundle
        )

        fingerprint = _sha256(
            validated
        )

        existing = self._connection.execute(
            """
            SELECT version, bundle_json, fingerprint, created_at, notes, supersedes
            FROM policy_bundles
            WHERE version = ?
            """,
            (
                version,
            ),
        ).fetchone()

        if existing is not None:
            if existing[
                "fingerprint"
            ] != fingerprint:
                raise PolicyVersionConflictError(
                    f"Policy version {version!r} already exists with different "
                    "content."
                )

            result = self._row_to_record(
                existing
            )

            if activate:
                self.activate_policy_bundle(
                    version
                )

            result[
                "idempotent_existing_version"
            ] = True

            return result

        supersedes = _clean(
            supersedes
        )

        if supersedes:
            self._require_version_exists(
                supersedes
            )

        created_at = _utc_now_iso()

        self._connection.execute(
            """
            INSERT INTO policy_bundles(
                version,
                bundle_json,
                fingerprint,
                created_at,
                notes,
                supersedes
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                version,
                json.dumps(
                    validated,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                fingerprint,
                created_at,
                _clean(notes),
                supersedes,
            ),
        )
        self._connection.commit()

        if activate:
            self.activate_policy_bundle(
                version
            )

        return self.get_policy_bundle(
            version
        )

    def activate_policy_bundle(
        self,
        version: str,
    ) -> Dict[str, Any]:
        version = _clean(
            version
        )

        self._require_version_exists(
            version
        )

        self._connection.execute(
            """
            INSERT INTO registry_state(key, value)
            VALUES ('active_policy_version', ?)
            ON CONFLICT(key)
            DO UPDATE SET value = excluded.value
            """,
            (
                version,
            ),
        )
        self._connection.commit()

        return {
            "active_policy_version": version,
            "activated_at": _utc_now_iso(),
            "metadata": {
                "service_version": SERVICE_VERSION,
                "policy_content_mutated": False,
            },
        }

    def get_active_version(self) -> str:
        row = self._connection.execute(
            """
            SELECT value
            FROM registry_state
            WHERE key = 'active_policy_version'
            """
        ).fetchone()

        if row is None:
            raise PolicyActivationError(
                "No active policy version is configured."
            )

        return _clean(
            row[
                "value"
            ]
        )

    # ------------------------------------------------------------------
    # Read APIs
    # ------------------------------------------------------------------

    def get_policy_bundle(
        self,
        version: str = "",
    ) -> Dict[str, Any]:
        version = (
            _clean(version)
            or self.get_active_version()
        )

        row = self._connection.execute(
            """
            SELECT version, bundle_json, fingerprint, created_at, notes, supersedes
            FROM policy_bundles
            WHERE version = ?
            """,
            (
                version,
            ),
        ).fetchone()

        if row is None:
            raise PolicyVersionNotFoundError(
                f"Unknown policy version={version!r}."
            )

        record = self._row_to_record(
            row
        )

        actual = _sha256(
            record[
                "bundle"
            ]
        )

        if actual != record[
            "fingerprint"
        ]:
            raise PolicyRegistryValidationError(
                f"Stored policy version {version!r} failed fingerprint validation."
            )

        return record

    def list_policy_versions(self) -> list[Dict[str, Any]]:
        active = ""

        try:
            active = self.get_active_version()
        except PolicyActivationError:
            pass

        rows = self._connection.execute(
            """
            SELECT version, fingerprint, created_at, notes, supersedes
            FROM policy_bundles
            ORDER BY created_at ASC, version ASC
            """
        ).fetchall()

        return [
            {
                "version": _clean(
                    row[
                        "version"
                    ]
                ),
                "fingerprint": _clean(
                    row[
                        "fingerprint"
                    ]
                ),
                "created_at": _clean(
                    row[
                        "created_at"
                    ]
                ),
                "notes": _clean(
                    row[
                        "notes"
                    ]
                ),
                "supersedes": _clean(
                    row[
                        "supersedes"
                    ]
                ),
                "active": (
                    _clean(
                        row[
                            "version"
                        ]
                    )
                    == active
                ),
            }
            for row in rows
        ]

    # ------------------------------------------------------------------
    # Snapshot API
    # ------------------------------------------------------------------

    def get_policy_snapshot(
        self,
        *,
        analysis_mode: str = "research",
        research_depth: str = "fast",
        version: str = "",
    ) -> Dict[str, Any]:
        """
        Materialize an immutable policy snapshot for one run.

        The snapshot contains both BlueprintCompiler-compatible policy layers and
        canonical newer policy modules.
        """
        analysis_mode = _enum(
            analysis_mode
        )
        research_depth = _enum(
            research_depth
        )

        if analysis_mode not in SUPPORTED_ANALYSIS_MODES:
            raise PolicyRegistryValidationError(
                f"Unsupported analysis_mode={analysis_mode!r}."
            )

        if research_depth not in SUPPORTED_RESEARCH_DEPTHS:
            raise PolicyRegistryValidationError(
                f"Unsupported research_depth={research_depth!r}."
            )

        record = self.get_policy_bundle(
            version
        )

        bundle = record[
            "bundle"
        ]

        try:
            analysis_base = bundle[
                "analysis_modes"
            ][
                analysis_mode
            ]

            profile = bundle[
                "research_profiles"
            ][
                research_depth
            ]

        except KeyError as exc:
            raise PolicyRegistryValidationError(
                "Registered policy bundle does not define requested analysis "
                "mode/profile."
            ) from exc

        # The profile only changes execution breadth/depth. It is merged into the
        # analysis-mode policy layer so current BlueprintCompiler versions can
        # carry research_depth and runtime limits.
        profile_blueprint_overlay = {
            "execution_policy": deepcopy(
                profile.get(
                    "execution_policy",
                    {}
                )
            ),
            "runtime_policy": deepcopy(
                profile.get(
                    "runtime_policy",
                    {}
                )
            ),
        }

        analysis_mode_policy = _deep_merge(
            analysis_base,
            profile_blueprint_overlay,
        )

        product_governance = deepcopy(
            bundle[
                "product_governance"
            ]
        )

        hard_restrictions = deepcopy(
            bundle[
                "hard_restrictions"
            ]
        )

        evidence_discovery_policy = deepcopy(
            profile[
                "evidence_discovery_policy"
            ]
        )

        model_routing_policy = _deep_merge(
            bundle[
                "model_routing_policy"
            ],
            profile.get(
                "model_routing_policy",
                {},
            ),
        )

        execution_profile = {
            "research_depth": research_depth,
            **deepcopy(
                profile[
                    "runtime_policy"
                ]
            ),
            "evidence_discovery": evidence_discovery_policy,
        }

        versions = {
            "policy_bundle_version": record[
                "version"
            ],
            "policy_registry_service_version": SERVICE_VERSION,
            "policy_registry_schema_version": REGISTRY_SCHEMA_VERSION,
            "analysis_mode_policy_version": (
                record[
                    "version"
                ]
                + ":analysis:"
                + analysis_mode
            ),
            "research_profile_version": (
                record[
                    "version"
                ]
                + ":profile:"
                + research_depth
            ),
            "product_governance_version": (
                record[
                    "version"
                ]
                + ":product-governance"
            ),
            "hard_restrictions_version": (
                record[
                    "version"
                ]
                + ":hard-restrictions"
            ),
        }

        semantic_snapshot = {
            "version": record[
                "version"
            ],
            "versions": versions,
            "analysis_mode": analysis_mode,
            "research_depth": research_depth,
            "analysis_mode_policy": analysis_mode_policy,
            "product_governance": product_governance,
            "hard_restrictions": hard_restrictions,
            "cost_policy": deepcopy(
                bundle[
                    "cost_policy"
                ]
            ),
            "execution_profile": execution_profile,
            "evidence_discovery_policy": evidence_discovery_policy,
            "model_routing_policy": model_routing_policy,
            "reasoning_context_policy": deepcopy(
                bundle[
                    "reasoning_context_policy"
                ]
            ),
            "evidence_policy": deepcopy(
                bundle[
                    "evidence_policy"
                ]
            ),
            "prospective_reasoning_policy": deepcopy(
                bundle[
                    "prospective_reasoning_policy"
                ]
            ),
            "release_policy": deepcopy(
                bundle[
                    "release_policy"
                ]
            ),
        }

        snapshot_id = _stable_id(
            "POLICYSNAPSHOT",
            semantic_snapshot,
        )

        result = {
            "policy_snapshot_id": snapshot_id,
            **semantic_snapshot,
            "policy_bundle_fingerprint": record[
                "fingerprint"
            ],
            "metadata": {
                "service_version": SERVICE_VERSION,
                "registry_schema_version": REGISTRY_SCHEMA_VERSION,
                "source_policy_created_at": record[
                    "created_at"
                ],
                "snapshot_created_at": _utc_now_iso(),
                "immutable_source_version": True,
                "fast_deep_same_epistemic_standards": True,
                "paid_external_services_allowed": False,
                "paid_llm_calls_allowed": False,
                "paid_search_apis_allowed": False,
                "policy_compiled_here": False,
                "operation_authorized_here": False,
                "model_routed_here": False,
                "release_authorized_here": False,
                "private_chain_of_thought_stored": False,
            },
        }

        result[
            "policy_snapshot_fingerprint"
        ] = self.snapshot_fingerprint(
            result
        )

        self.validate_policy_snapshot(
            result
        )

        return result

    # Alias matching architectural language.
    snapshot_for_blueprint_compiler = get_policy_snapshot

    def validate_policy_snapshot(
        self,
        snapshot: Mapping[str, Any],
    ) -> None:
        if not isinstance(
            snapshot,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "snapshot must be an object."
            )

        prohibited = _contains_prohibited_field(
            snapshot
        )

        if prohibited:
            raise PolicyRegistryValidationError(
                "snapshot contains prohibited private/raw reasoning field at "
                f"{prohibited!r}."
            )

        required = {
            "policy_snapshot_id",
            "version",
            "versions",
            "analysis_mode",
            "research_depth",
            "analysis_mode_policy",
            "product_governance",
            "hard_restrictions",
            "cost_policy",
            "execution_profile",
            "evidence_discovery_policy",
            "model_routing_policy",
            "reasoning_context_policy",
            "evidence_policy",
            "prospective_reasoning_policy",
            "release_policy",
            "policy_bundle_fingerprint",
        }

        missing = sorted(
            required
            - set(snapshot)
        )

        if missing:
            raise PolicyRegistryValidationError(
                "snapshot missing field(s): "
                + ", ".join(
                    missing
                )
            )

        analysis_mode = _enum(
            snapshot[
                "analysis_mode"
            ]
        )
        research_depth = _enum(
            snapshot[
                "research_depth"
            ]
        )

        if analysis_mode not in SUPPORTED_ANALYSIS_MODES:
            raise PolicyRegistryValidationError(
                "snapshot analysis_mode is invalid."
            )

        if research_depth not in SUPPORTED_RESEARCH_DEPTHS:
            raise PolicyRegistryValidationError(
                "snapshot research_depth is invalid."
            )

        cost_policy = snapshot[
            "cost_policy"
        ]

        if not isinstance(
            cost_policy,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "snapshot.cost_policy must be an object."
            )

        for key, required_value in HARD_COST_FLOOR.items():
            if cost_policy.get(
                key
            ) is not required_value:
                raise PolicyRegistryValidationError(
                    f"snapshot cost floor violation: {key}."
                )

        execution_profile = snapshot[
            "execution_profile"
        ]

        self._validate_execution_profile(
            research_depth,
            execution_profile,
        )

        expected_id = _stable_id(
            "POLICYSNAPSHOT",
            {
                key: deepcopy(
                    snapshot[
                        key
                    ]
                )
                for key in (
                    "version",
                    "versions",
                    "analysis_mode",
                    "research_depth",
                    "analysis_mode_policy",
                    "product_governance",
                    "hard_restrictions",
                    "cost_policy",
                    "execution_profile",
                    "evidence_discovery_policy",
                    "model_routing_policy",
                    "reasoning_context_policy",
                    "evidence_policy",
                    "prospective_reasoning_policy",
                    "release_policy",
                )
            },
        )

        if _clean(
            snapshot[
                "policy_snapshot_id"
            ]
        ) != expected_id:
            raise PolicyRegistryValidationError(
                "policy_snapshot_id failed semantic validation."
            )

        if (
            "policy_snapshot_fingerprint"
            in snapshot
            and _clean(
                snapshot[
                    "policy_snapshot_fingerprint"
                ]
            )
            != self.snapshot_fingerprint(
                snapshot
            )
        ):
            raise PolicyRegistryValidationError(
                "policy_snapshot_fingerprint mismatch."
            )

    def snapshot_fingerprint(
        self,
        snapshot: Mapping[str, Any],
    ) -> str:
        payload = deepcopy(
            dict(snapshot)
        )

        payload.pop(
            "policy_snapshot_fingerprint",
            None,
        )

        metadata = payload.get(
            "metadata"
        )

        if isinstance(
            metadata,
            Mapping,
        ):
            metadata = dict(
                metadata
            )

            # Creation time is operational metadata, not semantic policy.
            metadata.pop(
                "snapshot_created_at",
                None,
            )
            payload[
                "metadata"
            ] = metadata

        return _sha256(
            payload
        )

    # ------------------------------------------------------------------
    # Export / import
    # ------------------------------------------------------------------

    def export_policy_bundle(
        self,
        *,
        version: str = "",
    ) -> Dict[str, Any]:
        return self.get_policy_bundle(
            version
        )

    def import_policy_bundle(
        self,
        payload: Mapping[str, Any],
        *,
        activate: bool = False,
    ) -> Dict[str, Any]:
        if not isinstance(
            payload,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "import payload must be an object."
            )

        version = _clean(
            payload.get(
                "version"
            )
        )

        bundle = payload.get(
            "bundle"
        )

        if not version or not isinstance(
            bundle,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "import payload requires version and bundle."
            )

        expected = _clean(
            payload.get(
                "fingerprint"
            )
        )

        if expected and expected != _sha256(
            bundle
        ):
            raise PolicyRegistryValidationError(
                "Imported policy fingerprint mismatch."
            )

        return self.register_policy_bundle(
            version=version,
            bundle=bundle,
            activate=activate,
            notes=_clean(
                payload.get(
                    "notes"
                )
            ),
            supersedes=_clean(
                payload.get(
                    "supersedes"
                )
            ),
        )

    # ------------------------------------------------------------------
    # Bundle validation
    # ------------------------------------------------------------------

    def _validate_bundle(
        self,
        bundle: Mapping[str, Any],
    ) -> Dict[str, Any]:
        if not isinstance(
            bundle,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "policy bundle must be an object."
            )

        prohibited = _contains_prohibited_field(
            bundle
        )

        if prohibited:
            raise PolicyRegistryValidationError(
                "policy bundle contains prohibited private/raw reasoning field "
                f"at {prohibited!r}."
            )

        required = {
            "bundle_schema_version",
            "analysis_modes",
            "research_profiles",
            "product_governance",
            "hard_restrictions",
            "cost_policy",
            "model_routing_policy",
            "reasoning_context_policy",
            "evidence_policy",
            "prospective_reasoning_policy",
            "release_policy",
        }

        missing = sorted(
            required
            - set(bundle)
        )

        if missing:
            raise PolicyRegistryValidationError(
                "policy bundle missing field(s): "
                + ", ".join(
                    missing
                )
            )

        result = deepcopy(
            dict(bundle)
        )

        if _clean(
            result[
                "bundle_schema_version"
            ]
        ) != REGISTRY_SCHEMA_VERSION:
            raise PolicyRegistryValidationError(
                "Unsupported bundle_schema_version."
            )

        analysis_modes = result[
            "analysis_modes"
        ]

        if not isinstance(
            analysis_modes,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "analysis_modes must be an object."
            )

        for mode in SUPPORTED_ANALYSIS_MODES:
            if mode not in analysis_modes:
                raise PolicyRegistryValidationError(
                    f"analysis_modes is missing {mode!r}."
                )

            if not isinstance(
                analysis_modes[
                    mode
                ],
                Mapping,
            ):
                raise PolicyRegistryValidationError(
                    f"analysis_modes.{mode} must be an object."
                )

        profiles = result[
            "research_profiles"
        ]

        if not isinstance(
            profiles,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "research_profiles must be an object."
            )

        for depth in SUPPORTED_RESEARCH_DEPTHS:
            if depth not in profiles:
                raise PolicyRegistryValidationError(
                    f"research_profiles is missing {depth!r}."
                )

            self._validate_profile_definition(
                depth,
                profiles[
                    depth
                ],
            )

        # Deep must be at least as broad as Fast on research breadth knobs.
        self._validate_fast_deep_monotonicity(
            profiles[
                "fast"
            ],
            profiles[
                "deep"
            ],
        )

        cost_policy = result[
            "cost_policy"
        ]

        if not isinstance(
            cost_policy,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "cost_policy must be an object."
            )

        for key, required_value in HARD_COST_FLOOR.items():
            if key not in cost_policy:
                raise PolicyRegistryValidationError(
                    f"cost_policy missing {key!r}."
                )

            _strict_bool(
                cost_policy[
                    key
                ],
                name=f"cost_policy.{key}",
            )

            if cost_policy[
                key
            ] is not required_value:
                raise PolicyRegistryValidationError(
                    f"Free/local hard floor forbids {key}=True."
                )

        self._validate_governance_layers(
            result
        )

        self._validate_evidence_policy(
            result[
                "evidence_policy"
            ]
        )

        self._validate_prospective_policy(
            result[
                "prospective_reasoning_policy"
            ]
        )

        self._validate_release_policy(
            result[
                "release_policy"
            ]
        )

        self._validate_model_routing_policy(
            result[
                "model_routing_policy"
            ]
        )

        self._validate_reasoning_context_policy(
            result[
                "reasoning_context_policy"
            ]
        )

        return result

    def _validate_profile_definition(
        self,
        depth: str,
        profile: Any,
    ) -> None:
        if not isinstance(
            profile,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                f"research_profiles.{depth} must be an object."
            )

        for section in (
            "execution_policy",
            "runtime_policy",
            "evidence_discovery_policy",
            "model_routing_policy",
        ):
            if not isinstance(
                profile.get(
                    section
                ),
                Mapping,
            ):
                raise PolicyRegistryValidationError(
                    f"research_profiles.{depth}.{section} must be an object."
                )

        execution_depth = _enum(
            profile[
                "execution_policy"
            ].get(
                "research_depth"
            )
        )

        if execution_depth != depth:
            raise PolicyRegistryValidationError(
                f"Profile {depth!r} must set execution_policy.research_depth "
                f"to {depth!r}."
            )

        runtime = profile[
            "runtime_policy"
        ]

        max_run = _positive_int(
            runtime.get(
                "max_run_seconds"
            ),
            name=f"{depth}.max_run_seconds",
        )

        soft = _positive_int(
            runtime.get(
                "research_soft_deadline_seconds"
            ),
            name=f"{depth}.research_soft_deadline_seconds",
        )

        reserve = _positive_int(
            runtime.get(
                "reserve_finalization_seconds"
            ),
            name=f"{depth}.reserve_finalization_seconds",
        )

        _nonnegative_int(
            runtime.get(
                "max_refinement_rounds"
            ),
            name=f"{depth}.max_refinement_rounds",
        )

        if soft >= max_run:
            raise PolicyRegistryValidationError(
                f"{depth}: research soft deadline must be before hard deadline."
            )

        if reserve >= max_run:
            raise PolicyRegistryValidationError(
                f"{depth}: finalization reserve must be smaller than max run."
            )

        if soft > (
            max_run
            - reserve
        ):
            raise PolicyRegistryValidationError(
                f"{depth}: research soft deadline may not consume protected "
                "finalization reserve."
            )

        discovery = profile[
            "evidence_discovery_policy"
        ]

        for key in (
            "max_search_rounds_per_insight",
            "max_search_queries_per_insight",
            "max_sources_fetched_per_insight",
            "max_insights_for_external_discovery",
        ):
            _positive_int(
                discovery.get(
                    key
                ),
                name=f"{depth}.evidence_discovery_policy.{key}",
            )

    @staticmethod
    def _validate_fast_deep_monotonicity(
        fast: Mapping[str, Any],
        deep: Mapping[str, Any],
    ) -> None:
        fast_runtime = fast[
            "runtime_policy"
        ]
        deep_runtime = deep[
            "runtime_policy"
        ]

        for key in (
            "max_run_seconds",
            "research_soft_deadline_seconds",
            "reserve_finalization_seconds",
            "max_refinement_rounds",
        ):
            if int(
                deep_runtime[
                    key
                ]
            ) < int(
                fast_runtime[
                    key
                ]
            ):
                raise PolicyRegistryValidationError(
                    f"Deep profile cannot be smaller than Fast for {key}."
                )

        fast_discovery = fast[
            "evidence_discovery_policy"
        ]
        deep_discovery = deep[
            "evidence_discovery_policy"
        ]

        for key in (
            "max_search_rounds_per_insight",
            "max_search_queries_per_insight",
            "max_sources_fetched_per_insight",
            "max_insights_for_external_discovery",
        ):
            if int(
                deep_discovery[
                    key
                ]
            ) < int(
                fast_discovery[
                    key
                ]
            ):
                raise PolicyRegistryValidationError(
                    f"Deep profile cannot be smaller than Fast for discovery "
                    f"field {key}."
                )

    def _validate_execution_profile(
        self,
        depth: str,
        profile: Mapping[str, Any],
    ) -> None:
        if not isinstance(
            profile,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "execution_profile must be an object."
            )

        if _enum(
            profile.get(
                "research_depth"
            )
        ) != depth:
            raise PolicyRegistryValidationError(
                "execution_profile research_depth mismatch."
            )

        max_run = _positive_int(
            profile.get(
                "max_run_seconds"
            ),
            name="execution_profile.max_run_seconds",
        )

        soft = _positive_int(
            profile.get(
                "research_soft_deadline_seconds"
            ),
            name="execution_profile.research_soft_deadline_seconds",
        )

        reserve = _positive_int(
            profile.get(
                "reserve_finalization_seconds"
            ),
            name="execution_profile.reserve_finalization_seconds",
        )

        _nonnegative_int(
            profile.get(
                "max_refinement_rounds"
            ),
            name="execution_profile.max_refinement_rounds",
        )

        if soft > max_run - reserve:
            raise PolicyRegistryValidationError(
                "execution_profile consumes protected finalization reserve."
            )

        discovery = profile.get(
            "evidence_discovery"
        )

        if not isinstance(
            discovery,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "execution_profile.evidence_discovery must be an object."
            )

    def _validate_governance_layers(
        self,
        bundle: Mapping[str, Any],
    ) -> None:
        governance = bundle[
            "product_governance"
        ]
        hard = bundle[
            "hard_restrictions"
        ]

        if not isinstance(
            governance,
            Mapping,
        ) or not isinstance(
            hard,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "product_governance and hard_restrictions must be objects."
            )

        for layer_name, layer in (
            ("product_governance", governance),
            ("hard_restrictions", hard),
        ):
            quality = layer.get(
                "quality_policy"
            )

            if not isinstance(
                quality,
                Mapping,
            ):
                raise PolicyRegistryValidationError(
                    f"{layer_name}.quality_policy must be an object."
                )

            gates = quality.get(
                "gates"
            )

            if not isinstance(
                gates,
                list,
            ):
                raise PolicyRegistryValidationError(
                    f"{layer_name}.quality_policy.gates must be a list."
                )

            gate_ids = {
                _clean(
                    item.get(
                        "gate_id"
                    )
                )
                for item in gates
                if isinstance(
                    item,
                    Mapping,
                )
            }

            missing_gates = (
                REQUIRED_QUALITY_GATE_IDS
                - gate_ids
            )

            if missing_gates:
                raise PolicyRegistryValidationError(
                    f"{layer_name} missing required gate(s): "
                    + ", ".join(
                        sorted(
                            missing_gates
                        )
                    )
                )

        hard_runtime = hard.get(
            "runtime_policy"
        )

        if not isinstance(
            hard_runtime,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "hard_restrictions.runtime_policy must be an object."
            )

        if hard_runtime.get(
            "allow_silent_truncation"
        ) is not False:
            raise PolicyRegistryValidationError(
                "Hard restrictions must forbid silent truncation."
            )

        hard_data = hard.get(
            "data_policy"
        )

        if not isinstance(
            hard_data,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "hard_restrictions.data_policy must be an object."
            )

        if hard_data.get(
            "preserve_all_accepted_evidence"
        ) is not True:
            raise PolicyRegistryValidationError(
                "Hard restrictions must preserve all accepted evidence."
            )

        if hard_data.get(
            "preserve_source_provenance"
        ) is not True:
            raise PolicyRegistryValidationError(
                "Hard restrictions must preserve source provenance."
            )

        escalation = governance.get(
            "escalation"
        )

        if not isinstance(
            escalation,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "product_governance.escalation must be an object."
            )

        actions = set(
            _string_list(
                escalation.get(
                    "allowed_actions"
                )
                or []
            )
        )

        unknown = actions - ALLOWED_REFINEMENT_ACTIONS

        if unknown:
            raise PolicyRegistryValidationError(
                "Unknown governance escalation action(s): "
                + ", ".join(
                    sorted(
                        unknown
                    )
                )
            )

        if (
            "model_escalation"
            in actions
            and escalation.get(
                "model_escalation_requires_router"
            )
            is not True
        ):
            raise PolicyRegistryValidationError(
                "model_escalation must require ModelRouterService."
            )

    @staticmethod
    def _validate_evidence_policy(
        policy: Any,
    ) -> None:
        if not isinstance(
            policy,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "evidence_policy must be an object."
            )

        required_truths = {
            "search_result_is_evidence": False,
            "search_snippet_is_evidence": False,
            "source_fetch_required_before_evidence_admission": True,
            "different_source_ids_prove_independence": False,
            "different_domains_prove_independence": False,
            "different_group_ids_prove_independence": False,
            "unknown_source_independence_preserved": True,
            "all_accepted_evidence_must_be_examined": True,
        }

        for key, expected in required_truths.items():
            if policy.get(
                key
            ) is not expected:
                raise PolicyRegistryValidationError(
                    f"evidence_policy.{key} must be {expected!r}."
                )

    @staticmethod
    def _validate_prospective_policy(
        policy: Any,
    ) -> None:
        if not isinstance(
            policy,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "prospective_reasoning_policy must be an object."
            )

        required = {
            "direct_precedent_required": False,
            "factual_premises_must_be_grounded": True,
            "analogical_mechanism_evidence_allowed": True,
            "feasibility_check_required": True,
            "counter_incentive_search_required_when_material": True,
            "plausible_unprecedented_status_allowed": True,
        }

        for key, expected in required.items():
            if policy.get(
                key
            ) is not expected:
                raise PolicyRegistryValidationError(
                    f"prospective_reasoning_policy.{key} must be {expected!r}."
                )

    @staticmethod
    def _validate_release_policy(
        policy: Any,
    ) -> None:
        if not isinstance(
            policy,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "release_policy must be an object."
            )

        for key in (
            "grounding_required",
            "red_team_required",
            "evidence_trail_required",
            "uncertainty_required",
            "counterevidence_required",
            "stable_insight_ids_required",
            "revision_lineage_required",
        ):
            if policy.get(
                key
            ) is not True:
                raise PolicyRegistryValidationError(
                    f"release_policy.{key} must remain enabled."
                )

        allowed = set(
            _string_list(
                policy.get(
                    "allowed_final_epistemic_statuses"
                )
                or []
            )
        )

        blocked = set(
            _string_list(
                policy.get(
                    "blocked_final_epistemic_statuses"
                )
                or []
            )
        )

        if not {
            "supported",
            "partially_supported",
            "inference_supported",
        } <= allowed:
            raise PolicyRegistryValidationError(
                "release_policy allowed statuses are incomplete."
            )

        if not {
            "unsupported",
            "contradicted",
            "unresolved",
        } <= blocked:
            raise PolicyRegistryValidationError(
                "release_policy blocked statuses are incomplete."
            )

    @staticmethod
    def _validate_model_routing_policy(
        policy: Any,
    ) -> None:
        if not isinstance(
            policy,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "model_routing_policy must be an object."
            )

        if policy.get(
            "require_local"
        ) is not True:
            raise PolicyRegistryValidationError(
                "model_routing_policy.require_local must remain true."
            )

        allow_escalation = policy.get(
            "allow_escalation"
        )

        if not isinstance(
            allow_escalation,
            bool,
        ):
            raise PolicyRegistryValidationError(
                "model_routing_policy.allow_escalation must be boolean."
            )

        _nonnegative_int(
            policy.get(
                "max_escalations_per_stage"
            ),
            name="model_routing_policy.max_escalations_per_stage",
        )

    @staticmethod
    def _validate_reasoning_context_policy(
        policy: Any,
    ) -> None:
        if not isinstance(
            policy,
            Mapping,
        ):
            raise PolicyRegistryValidationError(
                "reasoning_context_policy must be an object."
            )

        for key in (
            "evidence_batch_max_characters",
            "structured_batch_max_characters",
            "preference_batch_max_characters",
        ):
            _positive_int(
                policy.get(
                    key
                ),
                name=f"reasoning_context_policy.{key}",
            )

        if policy.get(
            "batching_strategy"
        ) != "complete_records_no_silent_truncation":
            raise PolicyRegistryValidationError(
                "reasoning_context_policy must preserve complete-record batching."
            )

        if policy.get(
            "oversized_single_record_policy"
        ) != "emit_one_record_batch_untruncated":
            raise PolicyRegistryValidationError(
                "Oversized records must remain untruncated."
            )

    # ------------------------------------------------------------------
    # SQLite
    # ------------------------------------------------------------------

    def _initialize_schema(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS policy_bundles(
                version TEXT PRIMARY KEY,
                bundle_json TEXT NOT NULL,
                fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL,
                notes TEXT NOT NULL DEFAULT '',
                supersedes TEXT NOT NULL DEFAULT ''
            )
            """
        )

        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS registry_state(
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )

        self._connection.commit()

    def _install_default_if_needed(self) -> None:
        row = self._connection.execute(
            """
            SELECT version
            FROM policy_bundles
            WHERE version = ?
            """,
            (
                self.default_policy_version,
            ),
        ).fetchone()

        if row is None:
            self.register_policy_bundle(
                version=self.default_policy_version,
                bundle=default_policy_bundle(),
                activate=False,
                notes="Built-in baseline product policy.",
            )

        try:
            self.get_active_version()
        except PolicyActivationError:
            self.activate_policy_bundle(
                self.default_policy_version
            )

    def _require_version_exists(
        self,
        version: str,
    ) -> None:
        row = self._connection.execute(
            """
            SELECT 1
            FROM policy_bundles
            WHERE version = ?
            """,
            (
                version,
            ),
        ).fetchone()

        if row is None:
            raise PolicyVersionNotFoundError(
                f"Unknown policy version={version!r}."
            )

    @staticmethod
    def _row_to_record(
        row: sqlite3.Row,
    ) -> Dict[str, Any]:
        bundle = json.loads(
            row[
                "bundle_json"
            ]
        )

        return {
            "version": _clean(
                row[
                    "version"
                ]
            ),
            "bundle": bundle,
            "fingerprint": _clean(
                row[
                    "fingerprint"
                ]
            ),
            "created_at": _clean(
                row[
                    "created_at"
                ]
            ),
            "notes": _clean(
                row[
                    "notes"
                ]
            ),
            "supersedes": _clean(
                row[
                    "supersedes"
                ]
            ),
            "idempotent_existing_version": False,
        }


# Compatibility aliases.
PolicyRegistry = PolicyRegistryService