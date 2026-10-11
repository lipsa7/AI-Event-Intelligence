"""v6.21: convergent-evidence insights, key assumptions, analytic confidence,
and per-run Gemini usage.

Tradecraft basis: ICD 203 (separate assumptions from judgments; state
analytic confidence) and the CIA Tradecraft Primer's Key Assumptions Check.
An insight whose facts are all quoted from sources, whose conclusion follows
from them, and whose observations come from >= 3 independent news events and
>= 2 publishers may keep up to 3 unsourced reasoning steps, shown as labelled
assumptions. Contradiction always blocks.
"""

from __future__ import annotations

import json
import logging

import pytest

from services import claim_support_gate as gate
from services.research_orchestrator_service import ResearchOrchestratorService as R
from tests.test_claim_support_gate import DEEP_PROFILE, candidate, claim, grounding, make_orchestrator
from tests.test_verified_connection_release import _codes, _decide, blueprint  # noqa: F401  (fixture)


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


# Five observations, four news events (E1 reported twice), three publishers.
EVIDENCE = [
    {"evidence_id": "EV-1", "source_id": "S-1", "event_id": "EVENT-1", "source_url": "https://www.bloomberg.com/a",
     "text": "OpenAI detected an internal server incident the same day."},
    {"evidence_id": "EV-1B", "source_id": "S-1B", "event_id": "EVENT-1B", "source_url": "https://reuters.com/a",
     "text": "OpenAI said the internal incident was contained on March 27."},
    {"evidence_id": "EV-2", "source_id": "S-2", "event_id": "EVENT-2", "source_url": "https://reuters.com/b",
     "text": "The breach took place in June and was discovered by OpenAI in August."},
    {"evidence_id": "EV-3", "source_id": "S-3", "event_id": "EVENT-3", "source_url": "https://theguardian.com/c",
     "text": "Australia was informed only on September 10."},
    {"evidence_id": "EV-4", "source_id": "S-4", "event_id": "EVENT-4", "source_url": "https://theguardian.com/d",
     "text": "Australia set up a taskforce to review the breach."},
]
EVENTS = [{"event_id": "EVENT-1", "underlying_event_id": "U-INTERNAL"},
          {"event_id": "EVENT-1B", "underlying_event_id": "U-INTERNAL"},   # same story as EVENT-1
          {"event_id": "EVENT-2", "underlying_event_id": "U-BREACH"},
          {"event_id": "EVENT-3", "underlying_event_id": "U-NOTICE"},
          {"event_id": "EVENT-4", "underlying_event_id": "U-TASKFORCE"}]
EVENT_MAP = {"EV-1": "U-INTERNAL", "EV-1B": "U-INTERNAL", "EV-2": "U-BREACH", "EV-3": "U-NOTICE",
             "EV-4": "U-TASKFORCE"}
PUBLISHER_MAP = {"EV-1": "bloomberg.com", "EV-1B": "reuters.com", "EV-2": "reuters.com", "EV-3": "theguardian.com",
                 "EV-4": "theguardian.com"}
SOURCES = {e["evidence_id"]: e["source_id"] for e in EVIDENCE}


def premises(*ids):
    return [claim(f"P{i}", "fact", "supported", supporting=(eid,)) for i, eid in enumerate(ids, start=1)]


def convergent(*mechanism_statuses, premise_ids=("EV-1", "EV-2", "EV-3", "EV-4")):
    mechanisms = [claim(f"M{i}", "mechanism", status, f"Reasoning step {i}.", supporting=("EV-2", "EV-3"))
                  for i, status in enumerate(mechanism_statuses, start=1)]
    return grounding(*premises(*premise_ids), *mechanisms,
                     claim("I1", "hidden_insight", "inference_supported", supporting=("EV-2", "EV-3")))


# 1 gate rule ------------------------------------------------------------

def test_unsourced_step_becomes_a_labelled_assumption_when_observations_converge():
    verdict = gate.evaluate(convergent("inference_supported", "unsupported"), SOURCES, EVENT_MAP, PUBLISHER_MAP)
    assert verdict["passed"], verdict["reasons"]
    assert verdict["convergence"] == {"events": 4, "publishers": 3, "met": True}
    assert [a["claim_id"] for a in verdict["key_assumptions"]] == ["M2"]
    assert verdict["key_assumptions"][0]["label"] == gate.ASSUMPTION_LABEL


def test_two_reports_of_one_story_count_once():
    verdict = gate.evaluate(convergent("unsupported", premise_ids=("EV-1", "EV-1B", "EV-2")),
                            SOURCES, EVENT_MAP, PUBLISHER_MAP)
    assert verdict["convergence"]["events"] == 2 and not verdict["passed"]
    assert "unsourced_steps_without_convergent_observations" in verdict["reasons"]


def test_one_publisher_is_not_convergence():
    publishers = {key: "reuters.com" for key in PUBLISHER_MAP}
    verdict = gate.evaluate(convergent("unsupported"), SOURCES, EVENT_MAP, publishers)
    assert not verdict["passed"] and verdict["convergence"]["publishers"] == 1


def test_contradiction_always_blocks():
    verdict = gate.evaluate(convergent("contradicted"), SOURCES, EVENT_MAP, PUBLISHER_MAP)
    assert not verdict["passed"] and "claim_contradicted_by_cited_evidence" in verdict["reasons"]


def test_more_than_three_assumptions_block():
    verdict = gate.evaluate(convergent(*["unsupported"] * 4), SOURCES, EVENT_MAP, PUBLISHER_MAP)
    assert not verdict["passed"] and "too_many_unverified_assumptions" in verdict["reasons"]


def test_unsupported_premise_or_conclusion_still_blocks():
    g = convergent("unsupported")
    g["claims"][0]["grounding_status"] = "partially_supported"
    assert not gate.evaluate(g, SOURCES, EVENT_MAP, PUBLISHER_MAP)["passed"]
    g = convergent("unsupported")
    g["claims"][-1]["grounding_status"] = "unsupported"
    assert not gate.evaluate(g, SOURCES, EVENT_MAP, PUBLISHER_MAP)["passed"]


def test_callers_without_event_map_keep_the_old_rule():
    verdict = gate.evaluate(convergent("unsupported"), SOURCES)
    assert not verdict["passed"] and "mechanism_step_not_supported_by_cited_evidence" in verdict["reasons"]


# 2 release record and confidence ---------------------------------------

def test_released_record_lists_assumptions_and_confidence():
    g = convergent("inference_supported", "unsupported")
    verdict = gate.evaluate(g, SOURCES, EVENT_MAP, PUBLISHER_MAP)
    out, out_grounding, _ = gate.sanitize_for_release(candidate("X"), g, verdict)
    assert "M2" not in [c["claim_id"] for c in out_grounding["claims"]]       # only sourced claims stay
    assert out_grounding["status"] in {"supported", "inference_supported"}
    meta = out["metadata"]
    assert [a["claim_id"] for a in meta["key_assumptions"]] == ["M2"]
    assert meta["analytic_confidence"]["level"] == "moderate"                 # 4 events < 5
    assert "4 independent news events from 3 publishers" in meta["analytic_confidence"]["basis"]


def test_high_confidence_needs_five_events_three_publishers_and_no_counterevidence():
    conv = {"events": 5, "publishers": 3, "met": True}
    assert gate.analytic_confidence(conv, assumptions=1, counterevidence=0) == "high"
    assert gate.analytic_confidence(conv, assumptions=2, counterevidence=0) == "moderate"
    assert gate.analytic_confidence(conv, assumptions=0, counterevidence=1) == "moderate"
    assert gate.analytic_confidence({"events": 4, "publishers": 3}, 0, 0) == "moderate"


# 3 the real pipeline ----------------------------------------------------

def test_convergent_insight_is_released_through_the_real_pipeline_and_policy(blueprint):  # noqa: F811
    factory = lambda: convergent("inference_supported", "unsupported")  # noqa: E731
    obj = make_orchestrator(verdicts={"INSIGHTREV-C": factory})
    item = candidate("C")
    item["premises"] = [{"premise_id": f"P{i}", "text": e["text"], "evidence_ids": [e["evidence_id"]]}
                        for i, e in enumerate(EVIDENCE, start=1)]
    item["supporting_evidence_ids"] = [e["evidence_id"] for e in EVIDENCE]
    released, evidence, grounded = obj._claim_supported_release(
        topic="artificial intelligence", candidates=[item], grounded={"INSIGHTREV-C": factory()}, events=EVENTS,
        evidence=list(EVIDENCE), graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE,
        state={"warnings": []})
    assert [r["insight_revision_id"] for r in released] == ["INSIGHTREV-C"]
    report = obj._build_report(topic="artificial intelligence", run_id="RUN-T21", depth="deep",
                               events=[dict(e, title=e["event_id"]) for e in EVENTS], evidence=evidence,
                               constraints={}, graph_context={}, insights=released, grounding=grounded,
                               strategic_connections=[])
    shown = report["hidden_insights"][0]
    assert [a["claim_id"] for a in shown["key_assumptions"]] == ["M2"]
    assert shown["analytic_confidence"]["level"] == "moderate"
    decision = _decide(blueprint, report, grounding=grounded)
    assert decision["release_allowed"] is True, _codes(decision)


def test_convergence_maps_use_underlying_events_and_publisher_domains():
    obj = make_orchestrator(verdicts={})
    events_map, publishers = obj._convergence_maps(EVIDENCE + [
        {"evidence_id": "GAP-1", "source_id": "S-9", "analytical_context_only": True,
         "source_url": "https://www.dlapiper.com/x"}], EVENTS)
    assert events_map["EV-1"] == events_map["EV-1B"] == "U-INTERNAL"
    assert events_map["GAP-1"] == "CONTEXT-S-9"
    assert publishers["EV-1"] == "bloomberg.com" and publishers["GAP-1"] == "dlapiper.com"


# 4 shown to the reader --------------------------------------------------

def test_dashboard_lines_for_assumptions_and_confidence():
    import pathlib
    source = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "def key_assumption_lines" in source and "render_key_assumptions(item)" in source
    assert "reasoning step(s) are labelled as assumptions" in source


# 5 per-run Gemini usage -------------------------------------------------

def test_usage_is_counted_and_logged_once_per_run(caplog):
    from services import gemini_llm_service as gemini
    gemini.usage_snapshot(reset=True)
    gemini.record_usage("gemini-3.5-flash", {"prompt_token_count": 1000, "candidates_token_count": 200,
                                             "thoughts_token_count": 50})
    gemini.record_usage("gemini-3.5-flash", {"prompt_token_count": 500})
    gemini.record_usage("gemini-3.1-pro-preview", None, failed=True)
    obj = make_orchestrator(verdicts={})
    obj.telemetry_service = None
    state = {"run_id": "RUN-USAGE"}
    with caplog.at_level(logging.INFO):
        obj._emit_telemetry(state)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("gemini_usage_this_run="))
    logged = json.loads(line.split("=", 1)[1])
    assert logged["models"]["gemini-3.5-flash"] == {"calls": 2, "failed_calls": 0, "prompt_tokens": 1500,
                                                    "output_tokens": 200, "thinking_tokens": 50}
    assert logged["models"]["gemini-3.1-pro-preview"]["failed_calls"] == 1
    assert logged["total_calls"] == 2 and logged["total_output_tokens"] == 250
    assert gemini.usage_snapshot() == {}                                     # reset for the next run
