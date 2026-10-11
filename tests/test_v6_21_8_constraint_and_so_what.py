"""v6.21.8: consensus vs constraint, and the "so what" test.

Replay RUN-7CD0B58AA15D4B5E83571188:
- the first candidate ("tightening reporting mandates that require immediate
  notification are structurally incompatible with developers' multi-month
  detection latency") rested on one development plus a cited rule (the SOCI
  Act's mandatory cyber-incident reporting, found by gap research). The
  non-obviousness test rejected every single-development conclusion, although
  the strategic prompt allows single-actor forensic/constraint insights;
- repair rounds then removed every consequence, leaving "X while Y" - two
  facts side by side, which the user rightly called an opinion, not an insight.

Standards applied (research notes in README):
- constraints over preferences (Papic, Geopolitical Alpha): the value is the
  gap between the consensus expectation and what a binding constraint forces;
- the intelligence "so what" / BLUF standard: a fact without what it changes,
  for whom, is noise;
- checkable forecasts (Halawi et al. 2024): a dated question or watch signal.
"""

from __future__ import annotations

import inspect

import pytest

import services.research_orchestrator_service as ros
from services import claim_support_gate as gate
from services import non_obviousness as nob
from tests.test_claim_support_gate import DEEP_PROFILE, PASSING, candidate, claim, grounding as make_grounding, \
    make_orchestrator
from tests.test_v6_21_4_search_coverage import _events_and_evidence


@pytest.fixture(autouse=True)
def _private_ledgers(tmp_path, monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_WATCH_ITEMS", str(tmp_path / "watch_items.sqlite3"))
    monkeypatch.setenv("EVENT_INTELLIGENCE_EVENT_MEMORY", str(tmp_path / "event_memory.sqlite3"))


# The run's own records (texts verbatim from RUN-7CD0B58AA15D4B5E83571188).
EVENTS = [{"event_id": "EVENT-B373", "underlying_event_id": "U-BREACH",
           "title": "OpenAI agent accessed Australian government systems"},
          {"event_id": "EVENT-0420", "underlying_event_id": "U-BREACH",
           "title": "Albanese says OpenAI took months to report breach"}]
NEWS_1 = {"evidence_id": "EVID-B168", "event_id": "EVENT-B373", "source_id": "S1",
          "text": "The breach took place back in June, was discovered by OpenAI in August, and Australia was "
                  "informed about it only on September 10 - about three months after the fact."}
NEWS_2 = {"evidence_id": "EVID-4AE2", "event_id": "EVENT-0420", "source_id": "S2",
          "text": "Despite the breach happening in June, Albanese said the tech giant only notified the government "
                  "about the incident earlier this month."}
SOCI = {"evidence_id": "EVID-CA90", "source_id": "S3", "analytical_context_only": True, "gap_type": "policy",
        "text": "The agency prescribed that cybersecurity incidents, including those involving AI, must be reported "
                "to the Department of Home Affairs under the SOCI Act (Mandatory Cyber Incident Reporting)."}
EVIDENCE = [NEWS_1, NEWS_2, SOCI]
CONCLUSION = ("Australia's mandatory cyber-incident reporting obligation cannot be met by an AI developer whose "
              "own detection of agent actions lags by two months, so operators that use such agents carry the "
              "reporting liability.")


def grounding(support):
    return {"claims": [{"claim_id": "C-1", "claim_text": CONCLUSION, "epistemic_type": "hidden_insight",
                        "grounding_status": "inference_supported", "supporting_evidence_ids": support,
                        "evidence_ids": support}]}


def checker(alone_status):
    """Obvious-summary check: not stated. Single-development check: alone_status, recorded with its records."""
    calls = []

    def ground(claims, records):
        ids = sorted(r["evidence_id"] for r in records)
        calls.append((claims[0]["claim_id"], ids))
        status = "unsupported" if claims[0]["claim_id"].startswith("OBV") else alone_status
        return {"claims": [{"claim_id": c["claim_id"], "grounding_status": status} for c in claims]}
    return ground, calls


def _plain_echo(prompt):
    """v6.26: the plain-language rewrite step; this fake returns the claim unchanged."""
    import json as _json
    claim = prompt.split("Claim: ", 1)[1].split("\n\n", 1)[0]
    return _json.dumps({"plain": claim})


def ask(prompt):
    if "ONE plain sentence" in prompt:
        return _plain_echo(prompt)
    return '{"takeaways": ["OpenAI agents breached Australian systems.", "Australia is unhappy with the delay."]}'


# 1 constraint route -----------------------------------------------------

def test_one_development_plus_a_cited_rule_passes_and_the_rule_is_ablated():
    ground, calls = checker("unsupported")
    out = nob.check(grounding=grounding(["EVID-B168", "EVID-4AE2", "EVID-CA90"]), evidence=EVIDENCE,
                    events=EVENTS, ground=ground, ask=ask)
    assert out["passed"] and out["route"] == "constraint"
    assert out["claims"][0]["constraint_evidence_ids"] == ["EVID-CA90"]
    alone = [ids for cid, ids in calls if cid == "ALONE-1"]
    assert alone == [["EVID-4AE2", "EVID-B168"]]          # the rule was removed for the ablation


def test_constraint_that_adds_nothing_fails():
    ground, _ = checker("inference_supported")              # the news alone already says it
    out = nob.check(grounding=grounding(["EVID-B168", "EVID-CA90"]), evidence=EVIDENCE, events=EVENTS,
                    ground=ground, ask=ask)
    assert not out["passed"] and out["reasons"] == ["conclusion_does_not_need_the_constraint"]
    assert nob.brief(out["reasons"])


def test_one_development_without_a_constraint_still_fails():
    ground, _ = checker("unsupported")
    out = nob.check(grounding=grounding(["EVID-B168", "EVID-4AE2"]), evidence=EVIDENCE, events=EVENTS,
                    ground=ground, ask=ask)
    assert out["reasons"] == ["conclusion_single_development"]


def test_news_records_are_never_constraints():
    assert nob.is_constraint(SOCI) and not nob.is_constraint(NEWS_1)
    assert not nob.is_constraint({"evidence_id": "X", "event_id": "E", "analytical_context_only": True})


def test_single_development_reasons_cover_the_constraint_failure():
    assert "conclusion_does_not_need_the_constraint" in ros.ResearchOrchestratorService.SINGLE_DEVELOPMENT_REASONS


# 2 the "so what" test ---------------------------------------------------

def test_juxtaposition_without_consequence_fails():
    item = {"insight": "AI developers push military agents while they cannot contain commercial agents.",
            "second_order_effect": "", "metadata": {}}
    assert gate.so_what_reasons(item) == ["no_consequence_so_what"]
    assert "X while Y" in gate.so_what_brief(["no_consequence_so_what"])[0]


def test_constraint_route_needs_a_checkable_signal():
    item = {"second_order_effect": "Operators carry the liability.", "metadata": {}}
    assert gate.so_what_reasons(item, "constraint") == ["constraint_insight_needs_checkable_signal"]
    item["metadata"] = {"insight_derivation": {"watch_signals": ["Home Affairs guidance on AI-agent incidents"]}}
    assert gate.so_what_reasons(item, "constraint") == []
    dated = {"forecast_contract": {"question": "Will Home Affairs issue guidance?", "deadline": "2027-03-31T00:00:00+00:00"}}
    assert gate.so_what_reasons(dated, "constraint") == []
    assert gate.so_what_reasons({"forecast_contract": {"question": "q"}}, "constraint") == [
        "constraint_insight_needs_checkable_signal"]


NO_CONSEQUENCE = lambda: make_grounding(  # noqa: E731 - every second-order sentence unsupported
    claim("P1", "fact", "supported"),
    claim("M1", "mechanism", "inference_supported"),
    claim("S1", "second_order_effect", "unsupported", "Invented consequence."),
    claim("I1", "hidden_insight", "inference_supported"),
)


def _release(obj, item, monkeypatch, verdict=PASSING):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    monkeypatch.setattr(ros, "INSIGHT_EXPLORATION_COMBINATIONS", 0)
    events, evidence = _events_and_evidence()
    state = {"warnings": [], "run_id": "RUN-SOWHAT"}
    released, _, _ = obj._claim_supported_release(
        topic="ai", candidates=[item], grounded={item["insight_revision_id"]: verdict()}, events=events,
        evidence=evidence, graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE, state=state)
    return released, state["claim_support_gate"]["attempts"][0]


def test_finding_whose_consequence_was_pruned_is_not_released(monkeypatch):
    item = candidate("JUX")
    item["second_order_effect"] = "Invented consequence."      # the checker marks it unsupported -> pruned
    obj = make_orchestrator(verdicts={"INSIGHTREV-JUX": NO_CONSEQUENCE})
    released, attempt = _release(obj, item, monkeypatch, NO_CONSEQUENCE)
    assert released == []
    assert attempt["so_what"]["reasons"] == ["no_consequence_so_what"]
    assert any("no_consequence_so_what" in r for r in attempt["reasons"])


def test_finding_with_a_consequence_is_released(monkeypatch):
    obj = make_orchestrator(verdicts={"INSIGHTREV-OK": PASSING})
    released, attempt = _release(obj, candidate("OK"), monkeypatch)
    assert len(released) == 1 and attempt["so_what"]["reasons"] == []
    assert released[0]["second_order_effect"] == "Supported consequence."


def test_constraint_route_release_needs_a_watch_signal(monkeypatch):
    obj = make_orchestrator(verdicts={"INSIGHTREV-C": PASSING}, nonobvious={"passed": True, "route": "constraint"})
    released, attempt = _release(obj, candidate("C"), monkeypatch)
    assert released == [] and attempt["so_what"]["reasons"] == ["constraint_insight_needs_checkable_signal"]

    item = candidate("C2")
    item["metadata"]["insight_derivation"] = {"watch_signals": ["Home Affairs issues AI-agent incident guidance"]}
    obj = make_orchestrator(verdicts={"INSIGHTREV-C2": PASSING}, nonobvious={"passed": True, "route": "constraint"})
    released, _ = _release(obj, item, monkeypatch)
    assert len(released) == 1 and released[0]["metadata"]["insight_route"] == "constraint"


# 3 search and instructions ---------------------------------------------

def test_exploration_tries_the_top_development_against_the_context_records():
    obj = make_orchestrator(verdicts={})
    events, evidence = _events_and_evidence()               # includes one context record (EVID-RULE)
    combos = obj._development_combinations(events, evidence, limit=6)
    assert len(combos[0]["keys"]) == 3 and len(combos[1]["keys"]) == 1
    without_context = [r for r in evidence if not r.get("analytical_context_only")]
    assert all(len(c["keys"]) > 1 for c in obj._development_combinations(events, without_context, limit=6))


def test_single_development_exploration_uses_the_constraint_instruction(monkeypatch):
    monkeypatch.setattr(ros, "DEEP_CLAIM_GATE_EXTRA_SYNTHESES", 0)
    events, evidence = _events_and_evidence()
    obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": lambda: {"claims": []}}, new_candidates=[[], [], []])
    obj._claim_supported_release(
        topic="ai", candidates=[candidate("ONE")], grounded={"INSIGHTREV-ONE": {"claims": []}}, events=events,
        evidence=evidence, graph_context={}, constraints={}, blueprint={}, profile=DEEP_PROFILE,
        state={"warnings": [], "run_id": "RUN-CI"})
    instructions = [c["refinement_context"]["instruction"] for c in obj.analyze_calls]
    assert any(i.startswith("CONSENSUS VS CONSTRAINT") for i in instructions)


def test_rewrite_instructions_keep_the_consequence():
    assert "NEVER delete the consequence" in ros.CLAIM_REPAIR_INSTRUCTION
    assert "CONSENSUS VS CONSTRAINT" in ros.CLAIM_ALTERNATIVE_INSTRUCTION
    assert "only places facts side by side" in ros.INSIGHT_EXPLORATION_INSTRUCTION


def test_strategic_prompt_has_the_constraint_mode_and_exemplar():
    import agents.strategic_inference_agent as agent
    source = inspect.getsource(agent)
    assert "FOUR VALID INSIGHT MODES" in source and "4. CONSENSUS VS CONSTRAINT MODE" in source
    assert "EXEMPLAR 4" in source and "U. CONSENSUS VS CONSTRAINT" in source


def test_report_and_dashboard_say_how_the_insight_was_derived():
    import pathlib
    source = inspect.getsource(ros.ResearchOrchestratorService)
    assert '"insight_route": _clean(insight_metadata.get("insight_route"))' in source
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "ROUTE_NOTES" in dash and 'item.get("insight_route")' in dash
