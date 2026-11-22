"""v13.21: actor moves, the judge and the final checks are off the shared Gemini call ceiling."""
import pytest

from services import gemini_llm_service as g


def deep(monkeypatch, cap=480):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    for name in ("EVENT_INTELLIGENCE_ACTOR_MOVES_MAX_CALLS", "EVENT_INTELLIGENCE_FINAL_CHECKS_MAX_CALLS",
                 "EVENT_INTELLIGENCE_JUDGE_MAX_CALLS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("EVENT_INTELLIGENCE_MAX_GEMINI_CALLS", str(cap))
    service = S.__new__(S)
    service._apply_call_cap("deep")
    return service


def test_checking_stages_run_after_the_shared_ceiling_is_used_up(monkeypatch):
    service = deep(monkeypatch)
    try:
        with g.stage("hidden_insights"):
            with pytest.raises(g.GeminiCallBudgetExceeded):
                for _ in range(1000):
                    g.consume_call()
        for stage_name in ("actor_moves", "judge", "final_checks"):
            with g.stage(stage_name):
                assert g.calls_available() is None          # no limit
                for _ in range(100):                        # e.g. all 24 move claims and more
                    g.consume_call()
        budget = g.own_stage_budget()
        assert budget["own_used"] == {"actor_moves": 100, "judge": 100, "final_checks": 100}
        assert service._own_stage_limits == {"actor_moves": "no limit", "final_checks": "no limit",
                                             "judge": "no limit"}
    finally:
        g.set_call_budget(None)


def test_a_limit_can_still_be_set_by_env(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_JUDGE_MAX_CALLS", "3")
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_MAX_GEMINI_CALLS", "480")
    service = S.__new__(S)
    service._apply_call_cap("deep")
    try:
        with g.stage("judge"):
            for _ in range(3):
                g.consume_call()
            with pytest.raises(g.GeminiCallBudgetExceeded):
                g.consume_call()
    finally:
        g.set_call_budget(None)


def test_nested_checking_stage_inside_another_path_is_still_free(monkeypatch):
    """The judge runs inside report building: any open stage on the stack counts."""
    deep(monkeypatch, cap=10)
    try:
        with g.stage("pipeline"):
            for _ in range(10 - 0):
                try:
                    g.consume_call()
                except g.GeminiCallBudgetExceeded:
                    break
            with g.stage("judge"):
                g.consume_call()
    finally:
        g.set_call_budget(None)
