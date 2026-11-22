"""v13.29 (research report of 22 Nov, P1, P2, P4): variant perception checked by code, forecast
diagnosticity, load-bearing numbers. No model calls."""
import json
from datetime import datetime, timedelta, timezone

from services import base_rates, variant_view

NEAR = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
VIEW = {"claims": [{"claim": "California sued OpenAI over 80 loss-of-control incidents and the Hugging Face attack",
                    "source": "EVID-NEWS1"}]}
RECORDS = [
    {"evidence_id": "EVID-NEWS1", "event_id": "EV1", "text": "California sued OpenAI over 80 loss-of-control incidents."},
    {"evidence_id": "EVID-COMPLAINT", "event_id": "EV1",
     "text": "The complaint alleges OpenAI marketed agentic browsing to enterprises in March, citing 80 incidents."},
    {"evidence_id": "EVID-RUNWIDE", "event_id": "",
     "text": "Hyperscalers spent $121.4 billion on AI infrastructure in the quarter."},
]


def vp(**over):
    base = {"consensus_quote": "California sued OpenAI over 80 loss-of-control incidents",
            "overlooked_evidence_id": "EVID-COMPLAINT",
            "why_consensus_misses_it": "coverage leads with the incidents, not the marketing",
            "mechanism_chain": [{"step": "OpenAI marketed agentic browsing to enterprises", "evidence_ids": ["EVID-COMPLAINT"]},
                                {"step": "promotion is the hook for California nuisance liability, so OpenAI's "
                                         "exposure turns on the 80 incidents tied to that use", "evidence_ids": ["EVID-NEWS1"]}],
            "rival_hypothesis": "the suit is leverage for a settlement on disclosure only",
            "load_bearing_assumption": "the court reads promotion, not manufacture, as the act"}
    base.update(over)
    return base


def cand(text="OpenAI's exposure turns on its promotion of agentic browsing tied to 80 incidents.", **over):
    out = {"insight": text, "leverage_analysis": {"holder": "OpenAI"},
           "forecast_contract": {"question": "Will the court let the nuisance count proceed?",
                                 "deadline": NEAR, "resolution_criteria": "docket"},
           "variant_perception": vp()}
    out.update(over)
    return out


def test_a_complete_variant_perception_passes():
    assert variant_view.perception_problems(cand(), VIEW, RECORDS) == []


def test_each_part_is_checked():
    assert variant_view.perception_problems(cand(variant_perception={}), VIEW, RECORDS) == ["no_variant_perception"]
    assert "consensus_not_quoted" in variant_view.perception_problems(
        cand(variant_perception=vp(consensus_quote="everyone thinks OpenAI is in trouble")), VIEW, RECORDS)
    assert "overlooked_fact_used_by_consensus" in variant_view.perception_problems(
        cand(variant_perception=vp(overlooked_evidence_id="EVID-NEWS1")), VIEW, RECORDS)
    assert "overlooked_fact_off_story" in variant_view.perception_problems(
        cand(variant_perception=vp(overlooked_evidence_id="EVID-RUNWIDE")), VIEW, RECORDS)
    short = vp(mechanism_chain=[{"step": "OpenAI marketed it", "evidence_ids": ["EVID-COMPLAINT"]}])
    assert "mechanism_chain_too_short" in variant_view.perception_problems(cand(variant_perception=short), VIEW, RECORDS)
    assert {"no_rival_hypothesis", "no_load_bearing_assumption"} <= set(variant_view.perception_problems(
        cand(variant_perception=vp(rival_hypothesis="", load_bearing_assumption="")), VIEW, RECORDS))


def test_a_stapled_number_fails_the_chain_check():
    text = "OpenAI faces a fight while hyperscalers spent $121.4 billion on AI infrastructure."
    problems = variant_view.perception_problems(cand(text=text), VIEW, RECORDS)
    assert any(p.startswith("number_not_in_chain:") for p in problems)


def test_a_routine_forecast_is_not_diagnostic():
    reply = json.dumps({"p_outside_view": 0.95, "p_if_thesis_true": 0.96, "p_if_thesis_false": 0.94})
    out = variant_view.diagnosticity(cand(), [lambda p: reply, lambda p: reply])
    assert out["checked"] and set(out["problems"]) == {"forecast_not_uncertain", "forecast_not_diagnostic"}
    good = json.dumps({"p_outside_view": 0.45, "p_if_thesis_true": 0.7, "p_if_thesis_false": 0.25})
    assert variant_view.diagnosticity(cand(), [lambda p: good])["problems"] == []
    assert "Federal Judicial Center" in variant_view.diagnosticity_prompt(cand())


def test_unanswered_diagnosticity_is_not_a_failure():
    assert variant_view.diagnosticity(cand(), [lambda p: "no json"]) == {"checked": False, "problems": [], "answers": []}


def test_decorative_number_by_ablation():
    assert variant_view.number_is_decorative(cand(), lambda p: '{"still_follows": true}') is True
    assert variant_view.number_is_decorative(cand(text="OpenAI must answer."), lambda p: '{"still_follows": true}') is None


def test_repair_carries_a_fixed_variant_perception():
    answer = json.dumps({"insight": "OpenAI's promotion of agentic browsing decides its 80-incident exposure.",
                         "variant_perception": vp(rival_hypothesis="a settlement play")})
    out = variant_view.apply_repair(cand(variant_perception={}), answer, ["no_variant_perception"])
    assert out["variant_perception"]["rival_hypothesis"] == "a settlement play"
    assert "variant_perception" in variant_view.repair_prompt(cand(), ["no_rival_hypothesis"], [])


def test_lens_rules_ask_for_the_variant_perception():
    text = variant_view.lens_instruction(VIEW, "constraints")
    assert "consensus_quote" in text and "at most 25 plain words" in text
    assert "CONSENSUS" not in variant_view.lens_instruction({"claims": []}, "constraints")
    assert base_rates.prompt_block().count("\n") >= 4


def test_select_repairs_a_non_diagnostic_forecast(monkeypatch):
    from services import outside_judge
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_PERCEPTION", "1")
    monkeypatch.setattr(outside_judge, "available", lambda *a, **k: True)
    monkeypatch.setattr(outside_judge, "model", lambda *a, **k: "openai/gpt-oss-120b")

    def ask(prompt, *a, **k):
        if prompt.startswith("You are a forecaster"):
            routine = "demurrer" in prompt
            return json.dumps({"p_outside_view": 0.95 if routine else 0.45,
                               "p_if_thesis_true": 0.96 if routine else 0.7,
                               "p_if_thesis_false": 0.94 if routine else 0.3})
        if prompt.startswith("Does"):
            return '{"new": true}'
        if prompt.startswith("Pre"):
            return '{"reasons": []}'
        if prompt.startswith("Remove every number"):
            return '{"still_follows": false}'
        return '{"better": "A"}'
    monkeypatch.setattr(outside_judge, "ask", ask)
    service = S.__new__(S)
    routine = cand(forecast_contract={"question": "Will OpenAI file a demurrer?", "deadline": NEAR})
    fixed = {"insight": routine["insight"], "variant_perception": vp(),
             "forecast_contract": {"question": "Will the court let the nuisance count proceed?",
                                   "deadline": NEAR[:10], "resolution_criteria": "docket"}}
    service._ask_briefing_model = lambda p: fixed
    entry = {}
    kept = service._variant_select([routine], VIEW, entry, RECORDS)
    assert len(kept) == 1 and "proceed" in kept[0]["forecast_contract"]["question"]
    assert kept[0]["metadata"]["variant_diagnosticity"]["problems"] == []


def test_select_repairs_a_forecast_only_diagnosticity_catches(monkeypatch):
    from services import outside_judge
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_PERCEPTION", "1")
    monkeypatch.setattr(outside_judge, "available", lambda *a, **k: True)
    monkeypatch.setattr(outside_judge, "model", lambda *a, **k: "openai/gpt-oss-120b")
    seen = []

    def ask(prompt, *a, **k):
        if prompt.startswith("You are a forecaster"):
            seen.append(prompt)
            vague = "issue a statement" in prompt
            return json.dumps({"p_outside_view": 0.9 if vague else 0.4, "p_if_thesis_true": 0.9 if vague else 0.7,
                               "p_if_thesis_false": 0.88 if vague else 0.3})
        return {"Does": '{"new": true}', "Pre": '{"reasons": []}'}.get(prompt[:4].strip(), '{"still_follows": false}')
    monkeypatch.setattr(outside_judge, "ask", ask)
    service = S.__new__(S)
    vague = cand(forecast_contract={"question": "Will OpenAI issue a statement about the suit?", "deadline": NEAR})
    service._ask_briefing_model = lambda p: {"insight": vague["insight"], "variant_perception": vp(),
                                             "forecast_contract": {"question": "Will the court let the nuisance count proceed?",
                                                                   "deadline": NEAR[:10], "resolution_criteria": "docket"}}
    kept = service._variant_select([vague], VIEW, {}, RECORDS)
    assert len(seen) == 2 and "proceed" in kept[0]["forecast_contract"]["question"]
    assert kept[0]["metadata"]["variant_diagnosticity"]["gap"] == 0.4
