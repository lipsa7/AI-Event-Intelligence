"""v6.28: actor strategy by the method strategists use (researched 2026-10-10).

Crux (Rumelt) -> typed leverage (Helmer's 7 Powers / asymmetry) -> constraints and
incentives (Papic) -> other players' likely moves -> ONE choice with a trade-off
(Roger Martin; "strategy trendslop": LLM advice defaults to fashionable moves) ->
what would have to be true (Martin) -> coherent actions.
Checks: facts stated by sources; crux, leverage, others, choice and actions follow from
the cited facts; the choice is not already reported and not equally good for a rival.
Hidden insights are NOT cut (user, 2026-10-10).
"""

from __future__ import annotations

import inspect
import json
import pathlib

import services.research_orchestrator_service as ros
from services import actor_moves as moves

FACTS = [
    {"fact_id": "F1", "development": "OpenAI agent breach", "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24",
     "text": "OpenAI notified Australia on September 10, three months after its agent accessed Services Australia."},
    {"fact_id": "F2", "development": "EU AI Act", "evidence_id": "E2", "source_id": "S2", "date": "2026-10-01",
     "text": "Since August the Commission can fine a model provider 3% of worldwide turnover."},
    {"fact_id": "F3", "development": "Policy context", "evidence_id": "E3", "source_id": "S3", "date": "2026-09-30",
     "text": "Operators must report cyber incidents to Home Affairs under the SOCI Act."},
]
DRAFT = {"actors": [
    {"actor": "OpenAI",
     "crux": {"text": "Only OpenAI sees what its agents touched, and the penalty for slow disclosure falls on it in the "
                      "EU but on its customers in Australia.", "fact_ids": ["F1", "F2", "F3"]},
     "powers": [{"power": "cornered resource", "holder": "OpenAI", "benefit": "sole access to agent activity logs",
                 "barrier": "customers cannot see agent actions themselves", "fact_ids": ["F1"]}],
     "rules_and_incentives": [{"text": "The EU can fine a model provider 3% of worldwide turnover.", "fact_ids": ["F2"]}],
     "others": [{"actor": "Services Australia", "likely_move": "demand contractual notification deadlines",
                 "fact_ids": ["F1", "F3"]}],
     "choice": {"move": "Offer customers a 72-hour incident notice guarantee before regulators impose one",
                "basis": "incentive", "trade_off": "accepts liability exposure it now avoids in Australia",
                "why_now": "the Australian taskforce is still reviewing", "fact_ids": ["F1", "F2"]},
     "must_be_true": [{"condition": "OpenAI can detect agent incidents within 72 hours", "status": "not_yet",
                       "fact_ids": []},
                      {"condition": "The EU fine applies to model providers", "status": "true_now", "fact_ids": ["F2"]},
                      {"condition": "Customers value notice speed", "status": "true_now", "fact_ids": ["F1"]}],
     "actions": [{"move": "Publish an agent-incident detection SLA", "basis": "leverage",
                  "why_it_benefits": "turns its log access into a trust asset", "risk": "missed SLA",
                  "watch_for": "SLA in enterprise contracts", "fact_ids": ["F1"]}]},
]}


def _statuses(checks, unsupported=(), generic=False):
    out = {}
    for c in checks:
        t = c["claim_text"]
        if t.startswith(moves.ALREADY_PREFIX):
            out[c["claim_id"]] = "unsupported"
        elif t.startswith(moves.SWAP_PREFIX):
            out[c["claim_id"]] = "inference_supported" if generic else "unsupported"
        elif any(u in t for u in unsupported):
            out[c["claim_id"]] = "unsupported"
        else:
            out[c["claim_id"]] = "supported" if c["epistemic_type"] == "fact" else "inference_supported"
    return out


def test_prompt_follows_the_method():
    prompt = moves.build_prompt("ai", FACTS)
    for phrase in ("crux", "cornered_resource", "counter_positioning", "asymmetry", "trade_off", "must_be_true",
                   "coherent actions", "what is really going on", "would suit its rival just as"):
        assert phrase in prompt, phrase
    assert "monitor developments" in prompt   # named as a fashionable default to avoid


def test_parse_types_the_leverage_and_requires_a_trade_off():
    actor = moves.parse(json.dumps(DRAFT), FACTS)[0]
    assert actor["powers"][0]["power"] == "cornered_resource"
    assert actor["choice"]["trade_off"].startswith("accepts liability")
    no_trade_off = json.loads(json.dumps(DRAFT))
    no_trade_off["actors"][0]["choice"]["trade_off"] = ""
    assert moves.parse(no_trade_off, FACTS)[0]["choice"] == {}


def test_supported_strategy_is_shown_with_sources():
    actors = moves.parse(DRAFT, FACTS)
    checks = moves.claims(actors, FACTS)
    out = moves.sanitize(actors, checks, _statuses(checks, unsupported=("Customers value notice speed",)), FACTS)
    a = out["actors"][0]
    assert a["crux"]["sources"] and a["powers"][0]["power"] == "cornered_resource"
    assert a["choice"]["move"].startswith("Offer customers")
    assert [c["status"] for c in a["must_be_true"]] == ["not_yet", "true_now", "not_yet"]  # unsupported -> watch
    assert a["moves"][0]["move"] == "Publish an agent-incident detection SLA"
    assert a["leverage"]["power"] == "cornered_resource"


def test_generic_choice_fails_the_swap_test():
    actors = moves.parse(DRAFT, FACTS)
    checks = moves.claims(actors, FACTS)
    swap = [c for c in checks if c["claim_text"].startswith(moves.SWAP_PREFIX)]
    assert swap and "Services Australia" in swap[0]["claim_text"]
    out = moves.sanitize(actors, checks, _statuses(checks, generic=True), FACTS)
    assert out["actors"][0]["choice"] == {}
    assert any("generic" in r for r in out["removed"])


def test_obvious_choice_moves_to_widely_expected():
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")

    class G:
        def ground_claims(self, *, claims, evidence_records):
            out = []
            for c in claims:
                if c["claim_id"].startswith("NOVELTY-EXPECTED"):
                    status = "supported" if "72-hour" in c["claim_text"] else "unsupported"
                elif c["claim_id"].startswith("NOVELTY"):
                    status = "unsupported"
                else:
                    status = _statuses([c])[c["claim_id"]]
                out.append({"claim_id": c["claim_id"], "grounding_status": status})
            return {"claims": out}

    obj.grounding_service = G()
    obj._strong_model_factory = lambda name: type("C", (), {"ask": lambda self, p, **k: json.dumps(DRAFT)})()
    obj.extraction_agent = type("A", (), {"llm": type("L", (), {
        "ask": lambda self, p, **k: json.dumps({"takeaways": ["Vendors will offer faster notice."]})})()})()
    developments = [{"title": "OpenAI agent breach", "facts": [
        {"text": FACTS[0]["text"], "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24"},
        {"text": FACTS[1]["text"], "evidence_id": "E2", "source_id": "S2", "date": "2026-10-01"}]}]
    state = {"gap_context_records": [{"evidence_id": "E3", "source_id": "S3", "gap_type": "policy", "gap_for": "x",
                                      "text": FACTS[2]["text"]}]}
    out = obj._actor_moves(topic="ai", developments=developments, evidence=[], state=state)
    actor = out["actors"][0]
    assert actor["choice"] == {} and actor["expected_moves"][0]["move"].startswith("Offer customers a 72-hour")
    assert [m["move"] for m in actor["moves"]] == ["Publish an agent-incident detection SLA"]


def test_dashboard_shows_the_method_and_hidden_insight_loops_stay_on():
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    for label in ("**The crux:**", "**Leverage (", "**Best move (analysis):**", "Trade-off:",
                  "**What would have to be true:**", "**Actions that carry it out:**", "is likely to:**"):
        assert label in dash, label
    assert ros.DEEP_CLAIM_GATE_EXTRA_SYNTHESES == 2 and ros.INSIGHT_EXPLORATION_COMBINATIONS == 3
    assert "def _claim_supported_release" in inspect.getsource(ros.ResearchOrchestratorService)
