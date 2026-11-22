"""v13.29.1: a failed judge never holds insights back; the repair sees the consensus; a story always passes
its best candidate on; the tools read GROQ_API_KEY from .env."""
import inspect

import services.research_orchestrator_service as ros
from services import outside_judge


def test_a_failed_editors_bar_judge_does_not_hold_insights_back():
    source = inspect.getsource(ros.ResearchOrchestratorService._par_check)
    i = source.index('"not_judged:judge_failed:" + model')
    assert 'if not result.get("available")' in source[i - 600:i] and '"passed": True' in source[i - 200:i]


def test_the_repair_is_shown_the_consensus_lines():
    source = inspect.getsource(ros.ResearchOrchestratorService._variant_select)
    assert "CONSENSUS[" in source and source.count("repair_lines") >= 4
    assert "self._variant_repair(item, problems, lines)" not in source


def test_a_story_passes_its_best_candidate_on_when_all_miss_a_check(monkeypatch):
    monkeypatch.setattr(outside_judge, "available", lambda *a, **k: False)
    monkeypatch.setattr(outside_judge, "available_simple", lambda *a, **k: False)
    obj = object.__new__(ros.ResearchOrchestratorService)
    obj._variant_on = lambda: True
    obj._variant_repair = lambda item, problems, lines, overlap="": None
    found = [{"insight": "A long enough insight without anything.", "metadata": {}},
             {"insight": "Second weak insight.", "metadata": {}}]
    entry = {}
    out = obj._variant_select(found, {"claims": [{"claim": "everyone says x", "source": "EVID-1"}]}, entry, [])
    assert len(out) == 1 and entry.get("variant_passed_on_with_problems") == 1
    assert out[0]["metadata"]["variant_passed_on_with_problems"]


def test_the_judge_module_loads_the_project_env():
    assert "_project_env.load()" in inspect.getsource(outside_judge)
