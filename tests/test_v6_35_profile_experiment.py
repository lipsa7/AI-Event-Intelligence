"""v6.35: organisation profile + decision goal as a measured experiment (GPT review, 2026-10-22).

- Behind a switch: no --profile = baseline, prompts unchanged; with a profile, the profiled
  organisation's prompts (moves, choice, hypothesis lab) carry its goal. The profile is never
  evidence. Rivals are reasoned about from their own position, not the user's goal.
- The report records which arm produced it.
- The user's ratings (dashboard) are the deciding measure; compare_runs.py puts them first.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import services.research_orchestrator_service as ros
from services import actor_moves as moves
from services import insight_ratings
from services import org_profile
from services import strategic_hypothesis_lab as lab

ROOT = Path(__file__).resolve().parents[1]
FACTS = [{"fact_id": "F1", "development": "breach", "text": "OpenAI notified Australia three months after the access.",
          "evidence_id": "E1", "date": "2026-09-24"}]


@pytest.fixture
def profile_file(tmp_path, monkeypatch):
    path = tmp_path / "p.json"
    path.write_text(json.dumps({"name": "OpenAI", "decision_goal": "Keep government contracts after the agent incidents.",
                                "depends_on": ["government trust"], "constraints": "EU AI Act fines"}), encoding="utf-8")
    monkeypatch.setenv(org_profile.ENV, str(path))
    return path


def test_profile_loads_validates_and_is_never_evidence(profile_file, tmp_path):
    profile = org_profile.load(profile_file)
    assert profile["constraints"] == ["EU AI Act fines"] and profile["depends_on"] == ["government trust"]
    block = org_profile.prompt_block(profile)
    assert "DECISION GOAL: Keep government contracts" in block and "NOT evidence" in block and "never cite it" in block
    assert org_profile.fingerprint(profile) == org_profile.fingerprint(dict(profile))
    bad = tmp_path / "bad.json"
    bad.write_text('{"name": "OpenAI"}', encoding="utf-8")
    with pytest.raises(org_profile.ProfileError, match="decision_goal"):
        org_profile.load(bad)
    assert org_profile.load(ROOT / "profiles" / "openai.json")["name"] == "OpenAI"       # shipped draft is valid


def test_baseline_prompts_have_no_profile(monkeypatch):
    monkeypatch.delenv(org_profile.ENV, raising=False)
    assert org_profile.active() is None and org_profile.summary(None) == {"used": False, "version": org_profile.VERSION}
    assert "ORGANISATION PROFILE" not in moves.build_prompt("ai", FACTS, focus="OpenAI")
    assert "ORGANISATION PROFILE" not in lab.generation_prompt("ai", FACTS, ["lens"], batch=1)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    assert obj._profile_block_for("OpenAI") == ""


def test_profile_reaches_the_profiled_organisations_prompts_only(profile_file):
    block = org_profile.prompt_block(org_profile.active())
    assert "DECISION GOAL" in moves.build_prompt("ai", FACTS, focus="OpenAI", profile=block)
    assert "DECISION GOAL" in lab.generation_prompt("ai", FACTS, ["lens"], batch=1, profile=block)
    assert "DECISION GOAL" in lab.evolution_prompt("ai", [], FACTS, profile=block)
    actor = {"actor": "OpenAI", "crux": {"text": "c"}}
    assert "DECISION GOAL" in moves.choose_prompt(actor, [], {}, FACTS, profile=block)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    assert "DECISION GOAL" in obj._profile_block_for("openai")
    assert obj._profile_block_for("Australian Government") == ""          # rivals keep their own view
    assert "DECISION GOAL" in obj._profile_block_for()                     # lab: the whole analysis


def test_actor_moves_advise_the_profiled_organisation(profile_file, monkeypatch):
    monkeypatch.delenv("EVENT_INTELLIGENCE_FOCUS_ORG", raising=False)
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: 0.0                    # no focus research, no strategy stages
    obj.ACTOR_MOVES_MIN_SECONDS = 0.0
    prompts = []
    obj._ask_strong_model = lambda prompt: (prompts.append(prompt) or json.dumps({"actors": []}), "pro")
    developments = [{"title": "breach", "actors": ["Australian Government"],
                     "facts": [{"text": FACTS[0]["text"], "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24"}]}]
    out = obj._actor_moves(topic="ai", developments=developments, evidence=[], state={})
    assert out["focus_organisation"]["name"] == "OpenAI"
    assert "ORGANISATION TO ADVISE: OpenAI" in prompts[0] and "DECISION GOAL: Keep government contracts" in prompts[0]


def test_ratings_are_saved_reread_latest_and_turned_into_your_pairs(tmp_path):
    target = tmp_path / "r.jsonl"
    rows = [{"report_id": "R1", "item_key": "a", "text": "Leverage move A", "rating": "useful", "reason": "acts on it"},
            {"report_id": "R1", "item_key": "b", "text": "Generic advice B", "rating": "poor"},
            {"report_id": "R1", "item_key": "c", "text": "Known fact C", "rating": "meh"},
            {"report_id": "R1", "item_key": "d", "text": "Skipped", "rating": "not rated"}]
    assert insight_ratings.save(rows, target) == 3
    insight_ratings.save([{"report_id": "R1", "item_key": "c", "text": "Known fact C", "rating": "poor"}], target)
    latest = insight_ratings.load(target)
    assert {r["item_key"]: r["rating"] for r in latest} == {"a": "useful", "b": "poor", "c": "poor"}
    assert insight_ratings.summary(latest, "R1") == {"rated": 3, "useful": 1, "meh": 0, "poor": 2, "useful_share": 0.33}
    pairs = insight_ratings.gold_pairs(latest)
    assert len(pairs) == 2 and all(p["human_winner"] in {"A", "B"} for p in pairs)
    winners = {(p["a"] if p["human_winner"] == "A" else p["b"]) for p in pairs}
    assert winners == {"Leverage move A"}
    assert pairs[0]["id"] == insight_ratings.gold_pairs(latest)[0]["id"]   # stable across runs


def test_report_records_the_experiment_arm_and_dashboard_offers_rating(profile_file):
    import inspect
    source = inspect.getsource(ros.ResearchOrchestratorService._attach_spec_sections)
    assert '["organisation_profile"] = org_profile.summary(' in source
    summary = org_profile.summary(org_profile.active(), str(profile_file))
    assert summary["used"] is True and summary["name"] == "OpenAI" and len(summary["fingerprint"]) == 12
    from tests.test_v6_34_narrow_and_show import _dashboard
    dashboard, calls, saved = _dashboard()
    try:
        sections = {"insight_evaluation": {"items": [{"rank": 1, "ref": "x", "kind": "strategic_decision",
                                                      "text": "OpenAI should offer log access.",
                                                      "provenance_label": "Strategic action"}]},
                    "strategic_hypotheses": {"promising_unverified": [{"id": "H2", "thesis": "Governments depend on OpenAI's logs."}]}}
        items = dashboard.rating_items(sections)
        assert [i["key"] for i in items] == ["x", "lab:H2"]
        dashboard.render_profile_note({"analysis_metadata": {"organisation_profile": summary}})
        assert any("Organisation profile used: OpenAI" in str(arg) for _, arg in calls)
    finally:
        for name, mod in saved.items():
            sys.modules.pop(name, None) if mod is None else sys.modules.__setitem__(name, mod)


def test_replay_stops_on_a_broken_profile(tmp_path, monkeypatch, capsys):
    import replay_run
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["replay_run.py", "RUN-X", "--profile", str(bad)])
    assert replay_run.main() == 2
    assert "Profile error" in capsys.readouterr().out


def test_compare_runs_puts_your_ratings_first(tmp_path):
    import compare_runs
    report = {"report_id": "R1", "run_id": "RUN-1", "sections": {
        "analysis_metadata": {"organisation_profile": {"used": True, "name": "OpenAI", "fingerprint": "abc"}},
        "insight_evaluation": {"items": [{"score": 9, "band": "useful insight", "strategic_type": "leverage_decision",
                                          "text": "t", "provenance_label": "p"}]},
        "strategic_hypotheses": {"verified": [{}], "promising_unverified": []}}}
    row = compare_runs.describe(report, [{"report_id": "R1", "rating": "useful", "text": "t"}])
    assert row["profile"] == "OpenAI / abc" and row["your_ratings"]["useful"] == 1
    assert row["leverage_decisions"] == 1 and row["hypotheses_verified"] == 1
    assert "YOUR ratings first" in compare_runs.__doc__
