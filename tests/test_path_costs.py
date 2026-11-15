"""Per-path cost (Nov 2026): every Gemini attempt counts against the analysis path that started it,
each run logs one line to data/run_costs.jsonl, and path_value.py / compare_runs.py set each path's
cost beside your ratings of its items. No model calls."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import services.research_orchestrator_service as ros
from services import gemini_llm_service as gemini
from services import path_costs

ROOT = Path(__file__).resolve().parents[1]


def test_calls_count_against_the_outermost_path():
    gemini.stage_snapshot(reset=True)
    gemini.set_call_budget(None)
    with gemini.stage("briefing"):
        gemini.consume_call()
        gemini.record_usage("m", {"prompt_token_count": 100, "cached_content_token_count": 60,
                                  "candidates_token_count": 10})
        with gemini.stage("judge"):          # nested work is paid for by the path that started it
            gemini.consume_call()
    gemini.consume_call()
    snap = gemini.stage_snapshot(reset=True)
    assert snap["briefing"]["attempts"] == 2 and snap["briefing"]["cached_tokens"] == 60
    assert snap["pipeline"]["attempts"] == 1 and "judge" not in snap
    assert gemini.current_stage() == "pipeline"
    gemini.usage_snapshot(reset=True)


def test_per_model_usage_keeps_its_shape():
    gemini.usage_snapshot(reset=True)
    gemini.record_usage("m", {"prompt_token_count": 5, "cached_content_token_count": 3})
    assert gemini.usage_snapshot(reset=True)["m"] == {"calls": 1, "failed_calls": 0, "prompt_tokens": 5,
                                                      "output_tokens": 0, "thinking_tokens": 0}
    gemini.stage_snapshot(reset=True)


def test_cap_reports_refused_calls():
    gemini.set_call_budget(1)
    gemini.consume_call()
    try:
        gemini.consume_call()
        raise AssertionError("cap not enforced")
    except gemini.GeminiCallBudgetExceeded:
        pass
    assert gemini.call_budget() == {"limit": 1, "used": 1, "refused": 1}
    gemini.set_call_budget(None)
    gemini.stage_snapshot(reset=True)


def test_orchestrator_paths_are_wrapped():
    cls = ros.ResearchOrchestratorService
    for name, label in ros.STAGE_PATHS.items():
        assert getattr(cls.__dict__[name], "_stage_path", None) == label, name


def test_run_cost_line_is_written_for_a_real_run_only(tmp_path, monkeypatch):
    log = tmp_path / "run_costs.jsonl"
    monkeypatch.setenv("EVENT_INTELLIGENCE_RUN_COSTS", str(log))
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj.telemetry_service = None
    gemini.stage_snapshot(reset=True)
    with gemini.stage("hypothesis_lab"):
        gemini.consume_call()
    try:
        obj._emit_telemetry({"run_id": "", "topic": ""})          # unit-test style call: no line
    except Exception:
        pass
    assert not log.exists()
    with gemini.stage("hypothesis_lab"):
        gemini.consume_call()
    try:
        obj._emit_telemetry({"run_id": "RUN-X", "topic": "ai", "replay_of": "RUN-OLD"})
    except Exception:
        pass
    row = json.loads(log.read_text(encoding="utf-8").splitlines()[-1])
    assert row["run_id"] == "RUN-X" and row["by_path"]["hypothesis_lab"]["attempts"] == 1
    assert row["replay_of"] == "RUN-OLD" and row["stopped_at_cap"] is False


def test_path_value_sets_cost_beside_ratings(tmp_path):
    log = tmp_path / "run_costs.jsonl"
    log.write_text(json.dumps({"run_id": "RUN-1", "by_path": {
        "briefing": {"attempts": 20}, "hypothesis_lab": {"attempts": 40}, "judge": {"attempts": 18},
        "pipeline": {"attempts": 5}}}) + "\n", encoding="utf-8")
    ratings = [{"report_id": "REP-1", "kind": "implication", "rating": "useful"},
               {"report_id": "REP-1", "kind": "implication", "rating": "useful"},
               {"report_id": "REP-1", "kind": "strategic_hypothesis", "rating": "poor"},
               {"report_id": "REP-2", "kind": "implication", "rating": "useful"}]
    rows = {r["path"].split(":")[0]: r for r in path_costs.path_value("REP-1", path_costs.run_costs("RUN-1", log), ratings)}
    assert rows["briefing"] == {"path": "briefing", "rated": 2, "useful": 2, "attempts": 20,
                                "useful_per_10_attempts": 1.0}
    assert rows["hypothesis_lab"]["useful_per_10_attempts"] == 0.0
    assert rows["hidden_insights"]["useful_per_10_attempts"] is None
    assert rows["shared"]["attempts"] == 23
    text = "\n".join(path_costs.lines("REP-1", "RUN-1", ratings, log))
    assert "83 Gemini attempts" in text and "useful 2/2 rated, 20 attempts, 1.0 useful per 10 attempts" in text
    assert "No cost record" in path_costs.lines("REP-1", "RUN-OLD", ratings, log)[0]


def test_scripts_expose_the_cap_and_the_table():
    replay = (ROOT / "replay_run.py").read_text(encoding="utf-8")
    assert '"--max-calls"' in replay and "EVENT_INTELLIGENCE_MAX_GEMINI_CALLS" in replay
    assert "path_costs.lines(" in (ROOT / "compare_runs.py").read_text(encoding="utf-8")
    assert (ROOT / "path_value.py").exists()
