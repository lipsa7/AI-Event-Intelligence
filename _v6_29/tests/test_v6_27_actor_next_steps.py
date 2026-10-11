"""v6.27: what each main actor should do next - the product's objective, built directly.

Objective (user, last month and 2026-10-10): forward reasoning from present facts -
precedent ("this happened last time with a comparable company, so it can happen here"),
leverage ("the existing situation gives this actor leverage"), and policy/incentive fit
("with current regulations and incentives this move is right for the company"); "say what
next steps it should take considering the situation, what will benefit it".

One strong-model call (Pro first) writes per actor: situation, leverage, rules and
incentives, and next steps with their basis (precedent / leverage / incentive), risk and
watch signal. One round of checks: facts must be stated by their sources; each step must
follow from its cited facts and must not already be reported.
"""

from __future__ import annotations

import json
import logging
import pathlib

import services.research_orchestrator_service as ros
from services import actor_moves as moves
from services import editorial_briefing as brief

FACTS = [
    {"fact_id": "F1", "development": "OpenAI agent breach", "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24",
     "text": "OpenAI notified Australia on September 10, three months after its agent accessed Services Australia."},
    {"fact_id": "F2", "development": "EU AI Act", "evidence_id": "E2", "source_id": "S2", "date": "2026-10-01",
     "text": "Since August the Commission can fine a model provider 3% of worldwide turnover."},
    {"fact_id": "F3", "development": "Policy context", "evidence_id": "E3", "source_id": "S3", "date": "2026-09-30",
     "text": "Operators must report cyber incidents to Home Affairs under the SOCI Act."},
    {"fact_id": "H1", "development": "Past case (2023)", "evidence_id": "E4", "source_id": "S4", "date": "2023",
     "text": "In 2023 MOVEit customers bore breach notices for a vendor flaw. What followed: vendor-notice clauses spread."},
]
DRAFT = {"actors": [
    {"actor": "Services Australia",
     "situation": [{"text": "It learned of the breach three months later.", "fact_ids": ["F1"]}],
     "leverage": {"text": "It controls a government contract OpenAI wants to keep.", "fact_ids": ["F1"]},
     "rules_and_incentives": [{"text": "It must report cyber incidents under the SOCI Act.", "fact_ids": ["F3"]}],
     "moves": [
         {"basis": "precedent", "move": "Require AI vendors to notify incidents within 72 hours by contract",
          "why_it_benefits": "after MOVEit, vendor-notice clauses spread", "risk": "vendors push back",
          "watch_for": "new procurement clauses", "fact_ids": ["F1", "H1"]},
         {"basis": "incentive", "move": "Already notified Home Affairs", "why_it_benefits": "SOCI duty",
          "risk": "", "watch_for": "", "fact_ids": ["F3"]},
         {"basis": "nonsense", "move": "Monitor developments", "why_it_benefits": "generic", "risk": "",
          "watch_for": "", "fact_ids": ["F99"]}]},
    {"actor": "Unknown Co", "situation": [], "leverage": {}, "rules_and_incentives": [],
     "moves": [{"basis": "leverage", "move": "x", "why_it_benefits": "y", "fact_ids": ["F2"]}]},
]}


def test_parse_drops_unknown_ids_and_keeps_basis():
    actors = moves.parse(json.dumps(DRAFT), FACTS)
    assert [a["actor"] for a in actors] == ["Services Australia", "Unknown Co"]
    sa = actors[0]
    assert [m["basis"] for m in sa["moves"]] == ["precedent", "incentive"]   # F99-only move dropped
    assert sa["powers"][0]["fact_ids"] == ["F1"]   # v6.28: leverage is a typed power


def _statuses(checks, fail_texts=(), already=()):
    out = {}
    for c in checks:
        text = c["claim_text"]
        if c["claim_text"].startswith(moves.ALREADY_PREFIX):
            out[c["claim_id"]] = "supported" if any(a in text for a in already) else "unsupported"
        elif any(f in text for f in fail_texts):
            out[c["claim_id"]] = "unsupported"
        else:
            out[c["claim_id"]] = "supported" if c["epistemic_type"] == "fact" else "inference_supported"
    return out


def test_checks_keep_supported_steps_and_drop_already_reported_ones():
    actors = moves.parse(DRAFT, FACTS)
    checks = moves.claims(actors, FACTS)
    rule = next(c for c in checks if c["_target"][1] == "rules_and_incentives")
    assert set(rule["evidence_ids"]) == {"E1", "E2", "E3", "E4"}     # a rule may be stated by any fact
    out = moves.sanitize(actors, checks, _statuses(checks, already=("Already notified",)), FACTS)
    assert [a["actor"] for a in out["actors"]] == ["Services Australia"]  # Unknown Co has no rule/leverage
    sa = out["actors"][0]
    assert [m["move"] for m in sa["moves"]] == ["Require AI vendors to notify incidents within 72 hours by contract"]
    assert sa["moves"][0]["sources"][1]["fact_id"] == "H1"
    assert any("already reported" in r for r in out["removed"])


def test_unsupported_leverage_is_removed_but_rule_keeps_the_actor():
    actors = moves.parse(DRAFT, FACTS)
    checks = moves.claims(actors, FACTS)
    out = moves.sanitize(actors, checks, _statuses(checks, fail_texts=("controls a government contract",)), FACTS)
    assert out["actors"][0]["leverage"] == {} and out["actors"][0]["rules_and_incentives"]


def test_unchecked_shows_nothing():
    actors = moves.parse(DRAFT, FACTS)
    assert moves.sanitize(actors, moves.claims(actors, FACTS), None, FACTS)["actors"] == []


def test_past_cases_become_citeable_facts():
    cases = [{"case_id": "H1", "year": 2023, "text": "MOVEit breach.", "outcome": "clauses spread",
              "evidence_id": "E4"}]
    facts = moves.case_facts(cases)
    assert facts[0]["fact_id"] == "H1" and "What followed: clauses spread" in facts[0]["text"]
    prompt = moves.build_prompt("ai", FACTS)
    assert "precedent" in prompt and "past case" in prompt and "leverage" in prompt and "incentive" in prompt


class Grounding:
    def ground_claims(self, *, claims, evidence_records):
        return {"claims": [{"claim_id": c["claim_id"],
                            "grounding_status": ("unsupported" if c["claim_text"].startswith(moves.ALREADY_PREFIX)
                                                 else "supported" if c["epistemic_type"] == "fact"
                                                 else "inference_supported")} for c in claims]}


def test_orchestrator_uses_the_strong_model_first_and_logs(caplog):
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj.grounding_service = Grounding()
    obj._remaining_discretionary_seconds = lambda: float("inf")
    tried = []

    class Client:
        def __init__(self, name):
            self.name = name

        def ask(self, prompt, **kwargs):
            tried.append(self.name)
            if self.name == "gemini-3.1-pro-preview":
                raise RuntimeError("503")
            return json.dumps(DRAFT)

    obj._strong_model_factory = Client
    developments = [{"title": "OpenAI agent breach", "facts": [
        {"text": FACTS[0]["text"], "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24"}]}]
    state = {"gap_context_records": [{"evidence_id": "E3", "source_id": "S3", "gap_type": "policy",
                                      "gap_for": "x", "text": FACTS[2]["text"]}]}
    with caplog.at_level(logging.INFO):
        out = obj._actor_moves(topic="ai", developments=developments, evidence=[], state=state)
    assert tried[:2] == ["gemini-3.1-pro-preview", "gemini-3.8-flash"] and out["model"] == "gemini-3.8-flash"
    assert out["status"] == "checked"
    assert any(r.getMessage().startswith("actor_moves=") for r in caplog.records)


def test_report_always_attempts_the_section_and_dashboard_renders_it():
    import inspect
    source = inspect.getsource(ros.ResearchOrchestratorService._attach_spec_sections)
    assert 'report["actor_moves"]' in source
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "#### What each actor should do next" in dash and "render_actor_moves(sections)" in dash
    assert '"precedent": "Precedent"' in dash


# v6.27.1: the "not meh" tests on next steps ----------------------------------

def test_obvious_steps_are_moved_to_widely_expected_not_shown_as_advice():
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")

    class Grounding2:
        def ground_claims(self, *, claims, evidence_records):
            out = []
            for c in claims:
                text = c["claim_text"]
                if c["claim_id"].startswith("NOVELTY-EXPECTED"):
                    status = "supported" if "Tighten" in text else "unsupported"
                elif c["claim_id"].startswith("NOVELTY"):
                    status = "unsupported"
                elif text.startswith(moves.ALREADY_PREFIX):
                    status = "unsupported"
                else:
                    status = "supported" if c["epistemic_type"] == "fact" else "inference_supported"
                out.append({"claim_id": c["claim_id"], "grounding_status": status})
            return {"claims": out}

    obj.grounding_service = Grounding2()
    draft = {"actors": [{"actor": "Services Australia",
                         "situation": [{"text": "It learned of the breach late.", "fact_ids": ["F1"]}],
                         "leverage": {}, "rules_and_incentives": [{"text": "SOCI duty.", "fact_ids": ["F2"]}],
                         "moves": [{"basis": "incentive", "move": "Tighten security", "why_it_benefits": "obvious",
                                    "fact_ids": ["F1"]},
                                   {"basis": "precedent", "move": "Price vendor-notice delays into contracts",
                                    "why_it_benefits": "after MOVEit", "fact_ids": ["F1", "F2"]},
                                   {"basis": "leverage", "move": "Make notice speed a renewal condition",
                                    "why_it_benefits": "it holds the contract", "fact_ids": ["F1"]}]}]}
    obj._strong_model_factory = lambda name: type("C", (), {"ask": lambda self, p, **k: json.dumps(draft)})()

    class Briefing:
        def ask(self, prompt, **kwargs):
            return json.dumps({"takeaways": ["Agencies will tighten security."]})

    obj.extraction_agent = type("A", (), {"llm": Briefing()})()
    developments = [{"title": "OpenAI agent breach", "facts": [
        {"text": FACTS[0]["text"], "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24"}]}]
    state = {"gap_context_records": [{"evidence_id": "E3", "source_id": "S3", "gap_type": "policy",
                                      "gap_for": "x", "text": FACTS[2]["text"]}]}
    out = obj._actor_moves(topic="ai", developments=developments, evidence=[], state=state)
    actor = out["actors"][0]
    assert [m["move"] for m in actor["moves"]] == ["Price vendor-notice delays into contracts",
                                                   "Make notice speed a renewal condition"]
    assert [m["move"] for m in actor["expected_moves"]] == ["Tighten security"]
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "*Widely expected (headline readers or published experts already expect these):*" in dash
