from copy import deepcopy

import pytest

from services.policy_registry import PolicyRegistryService, PolicyRegistryValidationError
from services.blueprint_compiler import BlueprintCompiler, BlueprintValidationError
from services.policy_enforcement_service import PolicyEnforcementService
from services.execution_budget_service import ExecutionBudgetService


def _intent(depth="fast"):
    return {
        "intent_id": "INTENT-TEST",
        "intent_type": "research",
        "research_question": "What is changing underneath the events?",
        "analysis_mode": "research",
        "research_depth": depth,
        "requested_output": "research_report",
        "constraints": {},
        "source_preferences": {},
        "insight_preferences": {
            "desired_depth": "high",
            "preferred_patterns": ["mechanism", "counterevidence"],
        },
    }


def test_policy_registry_fast_and_deep_share_epistemic_floor(tmp_path):
    registry = PolicyRegistryService(
        db_path=str(tmp_path / "policy.sqlite3")
    )

    fast = registry.get_policy_snapshot(research_depth="fast")
    deep = registry.get_policy_snapshot(research_depth="deep")

    assert fast["execution_profile"]["max_run_seconds"] == 300
    assert deep["execution_profile"]["max_run_seconds"] == 1200

    for snapshot in (fast, deep):
        assert snapshot["cost_policy"] == {
            "paid_external_services_allowed": False,
            "paid_llm_calls_allowed": False,
            "paid_search_apis_allowed": False,
        }
        assert (
            snapshot["evidence_policy"][
                "different_group_ids_prove_independence"
            ]
            is False
        )
        assert (
            snapshot["prospective_reasoning_policy"][
                "direct_precedent_required"
            ]
            is False
        )
        assert snapshot["release_policy"]["grounding_required"] is True
        assert snapshot["release_policy"]["red_team_required"] is True

    registry.close()


def test_registry_rejects_paid_llm_policy():
    registry = PolicyRegistryService()
    bundle = registry.get_policy_bundle()["bundle"]
    bundle = deepcopy(bundle)
    bundle["cost_policy"]["paid_llm_calls_allowed"] = True

    with pytest.raises(PolicyRegistryValidationError):
        registry.register_policy_bundle(
            version="bad-paid-policy",
            bundle=bundle,
        )

    registry.close()


def test_blueprint_preserves_policy_modules_and_separates_depth(tmp_path):
    registry = PolicyRegistryService(
        db_path=str(tmp_path / "policy.sqlite3")
    )
    snapshot = registry.get_policy_snapshot(research_depth="fast")

    blueprint = BlueprintCompiler().compile(
        validated_intent=_intent("fast"),
        policy_snapshot=snapshot,
    )

    assert blueprint["execution_policy"]["research_depth"] == "fast"
    assert blueprint["reasoning_policy"]["desired_depth"] == "high"

    for key in (
        "cost_policy",
        "execution_profile",
        "evidence_discovery_policy",
        "model_routing_policy",
        "reasoning_context_policy",
        "evidence_policy",
        "prospective_reasoning_policy",
        "release_policy",
    ):
        assert key in blueprint

    registry.close()


def test_user_preference_cannot_change_research_depth(tmp_path):
    registry = PolicyRegistryService(
        db_path=str(tmp_path / "policy.sqlite3")
    )
    snapshot = registry.get_policy_snapshot(research_depth="fast")

    intent = _intent("fast")
    intent["insight_preferences"]["research_depth"] = "deep"

    with pytest.raises(BlueprintValidationError):
        BlueprintCompiler().compile(
            validated_intent=intent,
            policy_snapshot=snapshot,
        )

    registry.close()


def test_policy_enforcement_accepts_compiled_blueprint(tmp_path):
    registry = PolicyRegistryService(
        db_path=str(tmp_path / "policy.sqlite3")
    )
    snapshot = registry.get_policy_snapshot(research_depth="fast")

    blueprint = BlueprintCompiler().compile(
        validated_intent=_intent("fast"),
        policy_snapshot=snapshot,
    )

    result = PolicyEnforcementService().validate_blueprint(blueprint)

    assert result["valid"] is True
    registry.close()


@pytest.mark.xfail(
    reason=(
        "Current ExecutionBudgetService.from_blueprint reads research_depth "
        "from runtime/top-level fields, while BlueprintCompiler stores it in "
        "execution_policy. This test intentionally tracks that integration gap."
    ),
    strict=False,
)
def test_execution_budget_constructs_from_blueprint(tmp_path):
    registry = PolicyRegistryService(
        db_path=str(tmp_path / "policy.sqlite3")
    )
    snapshot = registry.get_policy_snapshot(research_depth="fast")
    blueprint = BlueprintCompiler().compile(
        validated_intent=_intent("fast"),
        policy_snapshot=snapshot,
    )

    budget = ExecutionBudgetService.from_blueprint(
        run_id="RUN-TEST",
        blueprint=blueprint,
        auto_start=True,
    )

    summary = budget.usage_summary()
    assert summary["run_id"] == "RUN-TEST"
    assert summary["profile"] == "fast"
    assert summary["budget_policy"]["max_run_seconds"] == 300

    budget.finalize("test_complete")
    registry.close()