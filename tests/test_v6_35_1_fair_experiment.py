"""v6.35.1 - GPT review of v6.35, before spending two runs on the experiment:
1. The draft profile stated the dependency the app should establish ("governments only learn from
   OpenAI's own logs"). The profile is now the decision plus widely documented background only.
2. compare_runs.py checks the pair: same checkpoint, organisation and software version, one baseline
   and one profile run; otherwise it refuses (unless --force).
3. Rating pairs are formed only within one report (same question and decision context).
4. Saving the form again no longer erases an earlier reason.
"""

from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path

import compare_runs
import services.research_orchestrator_service as ros
from services import insight_ratings
from services import org_profile

ROOT = Path(__file__).resolve().parents[1]


def test_shipped_profile_states_no_conclusions_the_app_must_establish():
    raw = json.loads((ROOT / "profiles" / "openai.json").read_text(encoding="utf-8"))
    assert not raw.get("depends_on") and not raw.get("others_depend_on_it_for") and not raw.get("priorities")
    loaded = org_profile.load(ROOT / "profiles" / "openai.json")                       # what reaches the prompts
    text = json.dumps([v for v in loaded.values() if v]).casefold()
    for loaded in ("only learn", "logs", "leverage", "depend", "trust"):
        assert loaded not in text, loaded
    assert raw["decision_goal"].startswith("Decide what OpenAI should do next")
    block = org_profile.prompt_block(org_profile.load(ROOT / "profiles" / "openai.json"))
    assert "Do not adopt any dependency, leverage or motive from the profile" in block


def _report(rid, replay="RUN-ED24", focus="OpenAI", version="v3.29.1", profile=False):
    return {"report_id": rid, "run_id": "RUN-" + rid, "sections": {"analysis_metadata": {
        "experiment": {"replay_of": replay, "focus_organisation": focus, "service_version": version, "topic": "ai"},
        "organisation_profile": {"used": profile, "name": "OpenAI", "fingerprint": "f" if profile else ""}}}}


def test_only_a_like_for_like_pair_is_compared():
    assert compare_runs.pair_problems(_report("A"), _report("B", profile=True)) == []
    assert any("different checkpoints" in p for p in compare_runs.pair_problems(_report("A"), _report("B", replay="RUN-X", profile=True)))
    assert any("different organisation" in p for p in compare_runs.pair_problems(_report("A"), _report("B", focus="Anthropic", profile=True)))
    assert any("different software versions" in p for p in compare_runs.pair_problems(_report("A"), _report("B", version="v9", profile=True)))
    assert any("both reports are baseline" in p for p in compare_runs.pair_problems(_report("A"), _report("B")))
    old = {"report_id": "OLD", "run_id": "", "sections": {}}
    assert any("no checkpoint record" in p for p in compare_runs.pair_problems(old, _report("B", profile=True)))


def test_latest_valid_pair_skips_unrelated_reports(tmp_path):
    db = tmp_path / "reports.sqlite3"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE report_revisions (report_id TEXT, revision_no INTEGER, created_at TEXT, payload_json TEXT)")
        for n, report in enumerate([_report("BASE"), _report("OTHER", replay="RUN-Z", profile=True),
                                    _report("PROF", profile=True), _report("UNRELATED", replay="RUN-Q")]):
            payload = {"run_id": report["run_id"], "sections": report["sections"]}
            conn.execute("INSERT INTO report_revisions VALUES (?, 1, ?, ?)",
                         (report["report_id"], f"2026-10-22T0{n}:00:00", json.dumps(payload)))
    assert compare_runs.latest_valid_pair(db) == ["BASE", "PROF"]


def test_rating_pairs_stay_within_one_report(tmp_path):
    target = tmp_path / "r.jsonl"
    insight_ratings.save([
        {"report_id": "R1", "item_key": "a", "text": "Useful in R1", "rating": "useful"},
        {"report_id": "R2", "item_key": "b", "text": "Poor in R2", "rating": "poor"},
        {"report_id": "R1", "item_key": "c", "text": "Meh in R1", "rating": "meh"}], target)
    pairs = insight_ratings.gold_pairs(insight_ratings.load(target))
    assert len(pairs) == 1 and {pairs[0]["a"], pairs[0]["b"]} == {"Useful in R1", "Meh in R1"}
    assert pairs[0]["report_id"] == "R1"


def test_saving_again_keeps_the_earlier_reason(tmp_path):
    target = tmp_path / "r.jsonl"
    insight_ratings.save([{"report_id": "R1", "item_key": "a", "text": "t", "rating": "useful",
                           "reason": "names who holds the logs"}], target)
    insight_ratings.save([{"report_id": "R1", "item_key": "a", "text": "t", "rating": "meh", "reason": ""}], target)
    row = insight_ratings.load(target)[0]
    assert row["rating"] == "meh" and row["reason"] == "names who holds the logs"
    dash = (ROOT / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert 'value=clean_text((earlier.get(item["key"]) or {}).get("reason"))' in dash


def test_report_records_what_a_comparison_must_hold_equal():
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert 'state["replay_of"] = _clean(checkpoint.get("run_id"))' in source
    assert '"replay_of": _clean(state.get("replay_of"))' in source and '"service_version": SERVICE_VERSION' in source


def test_v6_35_2_records_the_advised_organisation_and_rejects_missing_values():
    # v6.35.2 (GPT review): match on the organisation actually advised; missing values never match.
    assert any("does not record which organisation" in p
               for p in compare_runs.pair_problems(_report("A", focus=""), _report("B", focus="", profile=True)))
    assert any("does not record its software version" in p
               for p in compare_runs.pair_problems(_report("A", version=""), _report("B", version="", profile=True)))
    source = inspect.getsource(ros.ResearchOrchestratorService._attach_spec_sections)
    assert '"focus_organisation": _clean(focus_meta.get("name"))' in source and '"focus_chosen_by"' in source


def test_v6_35_2_auto_picked_organisation_is_recorded(monkeypatch):
    monkeypatch.delenv("EVENT_INTELLIGENCE_FOCUS_ORG", raising=False)
    monkeypatch.delenv(org_profile.ENV, raising=False)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._developments_to_watch = lambda events, evidence, state: []
    obj._editorial_briefing = lambda **k: {"mode": "model", "implications": [], "analogies": []}
    obj._actor_moves = lambda **k: {"focus_organisation": {"name": "OpenAI", "chosen_by": "auto"}, "actors": []}
    obj._forecast_followups = lambda **k: {}
    report = {"research_depth": "fast"}
    obj._attach_spec_sections(report=report, topic="ai", events=[], evidence=[], state={"replay_of": "RUN-ED24"})
    exp = report["analysis_metadata"]["experiment"]
    assert exp["focus_organisation"] == "OpenAI" and exp["focus_chosen_by"] == "auto"
    assert exp["requested_organisation"] == "" and exp["replay_of"] == "RUN-ED24"
