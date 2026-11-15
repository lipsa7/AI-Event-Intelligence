"""v6.29: the strongly evidenced methods from the deep research
(reports/Non generic strategic insight methods.md):
- each key rival modelled SEPARATELY from its own incentives (K-level reasoning; no personas);
- many candidate moves, de-duplicated, ranked in an order-swapped pairwise tournament;
- a best-response choice made after arguing the unfashionable option, with ICD 203
  likelihood wording, a separate confidence, and the rejected alternatives.
"""

from __future__ import annotations

import json
import pathlib

import services.research_orchestrator_service as ros
from services import actor_moves as moves

FACTS = [
    {"fact_id": "F1", "development": "OpenAI agent breach", "evidence_id": "E1", "source_id": "S1", "date": "2026-09-24",
     "text": "OpenAI notified Australia on September 10, three months after its agent accessed Services Australia."},
    {"fact_id": "F2", "development": "EU AI Act", "evidence_id": "E2", "source_id": "S2", "date": "2026-10-01",
     "text": "Since August the Commission can fine a model provider 3% of worldwide turnover."},
]


def cand(move, n=1):
    return {"move": move, "basis": "incentive", "trade_off": "costs money", "fact_ids": ["F1", "F2"][:n]}


def test_prompt_drops_the_persona_and_asks_for_spread_candidates():
    prompt = moves.build_prompt("ai", FACTS)
    assert "senior strategy adviser" not in prompt and "You are" not in prompt
    assert "UNFASHIONABLE option" in prompt and '"candidates"' in prompt


def test_rival_is_modelled_from_its_own_incentives():
    prompt = moves.rival_prompt("Services Australia", "OpenAI", FACTS)
    assert "Services Australia's own incentives" in prompt and "not from what would suit OpenAI" in prompt
    assert "persona" not in prompt.casefold() and "you are" not in prompt.casefold()
    parsed = moves.parse_rival(json.dumps({"moves": [
        {"move": "Demand 72-hour notice clauses", "why": "SOCI duty", "likelihood": "likely", "fact_ids": ["F1"]},
        {"move": "Ban AI vendors", "why": "anger", "likelihood": "maybe", "fact_ids": ["F9"]}]}), FACTS)
    assert parsed == [{"move": "Demand 72-hour notice clauses", "why": "SOCI duty", "likelihood": "likely",
                       "fact_ids": ["F1"]}]


def test_dedupe_removes_rewordings():
    out = moves.dedupe([cand("Offer a 72-hour incident notice guarantee"),
                        cand("Offer a 72-hour incident notice guarantee to customers"),
                        cand("Lobby the EU to cap fines")])
    assert [c["move"] for c in out] == ["Offer a 72-hour incident notice guarantee", "Lobby the EU to cap fines"]


def test_tournament_swaps_order_and_breaks_ties_by_evidence():
    pool = [cand(f"Move {i}", n=1 + (i == 3)) for i in range(8)]
    calls = []

    def judge(a, b):
        calls.append((a["move"], b["move"]))
        return "A"          # always picks the first shown: every match is a split decision

    finalists, log = moves.tournament(pool, judge, top=3)
    assert log["matches"] * 2 == len(calls) and log["split_decisions"] == log["matches"]
    assert ("Move 0", "Move 1") in calls and ("Move 1", "Move 0") in calls      # order swapped
    assert len(finalists) <= 3 and any(f["move"] == "Move 3" for f in finalists)  # more evidence wins ties


def test_consistent_judge_picks_the_better_move():
    pool = [cand("generic"), cand("specific one"), cand("generic two"), cand("generic three")]

    def judge(a, b):
        return "A" if "specific" in a["move"] else "B" if "specific" in b["move"] else ""

    finalists, log = moves.tournament(pool, judge, top=1)
    assert [f["move"] for f in finalists] == ["specific one"]


def test_best_response_choice_is_applied_with_likelihood_and_rejections():
    actor = {"actor": "OpenAI", "choice": cand("first pass"), "must_be_true": [], "moves": [],
             "crux": {"text": "only OpenAI sees agent logs"}}
    prompt = moves.choose_prompt(actor, [cand("Offer notice guarantee"), cand("Lobby EU")],
                                 {"Services Australia": [{"move": "Demand notice clauses", "likelihood": "likely"}]},
                                 FACTS)
    assert "unfashionable_case" in prompt and "best RESPONSE" in prompt and "not both, not a hybrid" in prompt
    assert "Demand notice clauses (likely)" in prompt
    reply = {"unfashionable_case": "Conceding EU fines may be cheaper than global guarantees.",
             "choice": {"move": "Offer a 72-hour notice guarantee to Australian agencies", "basis": "incentive",
                        "trade_off": "accepts liability it now avoids", "why_now": "taskforce review",
                        "likelihood": "likely", "confidence": "moderate", "confidence_basis": "two sources",
                        "fact_ids": ["F1"]},
             "rejected": [{"move": "Lobby EU", "why_not": "EU fines already in force"}],
             "must_be_true": [{"condition": "OpenAI can detect within 72 hours", "status": "not_yet", "fact_ids": []}],
             "actions": [{"move": "Publish a detection SLA", "basis": "leverage", "why_it_benefits": "trust",
                          "risk": "missed SLA", "watch_for": "SLA clauses", "fact_ids": ["F1"]}]}
    assert moves.apply_choice(actor, json.dumps(reply), FACTS) is True
    assert actor["choice"]["move"].startswith("Offer a 72-hour") and actor["choice"]["likelihood"] == "likely"
    assert actor["choice"]["confidence"] == "moderate" and actor["rejected"][0]["move"] == "Lobby EU"
    assert actor["unfashionable_case"].startswith("Conceding")
    assert moves.apply_choice(actor, "{}", FACTS) is False and actor["choice"]["move"].startswith("Offer")


def test_orchestrator_runs_rivals_tournament_and_choice(monkeypatch):
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    obj._remaining_discretionary_seconds = lambda: float("inf")
    asked = []

    def briefing(prompt):
        asked.append(prompt[:40])
        if prompt.startswith("Predict what"):
            return json.dumps({"moves": [{"move": "Demand notice clauses", "why": "SOCI", "likelihood": "likely",
                                          "fact_ids": ["F1"]}]})
        if prompt.startswith("Two candidate"):
            return json.dumps({"winner": "A"})
        return json.dumps({"takeaways": ["x"]})

    obj._ask_briefing_model = briefing
    draft = moves.parse({"actors": [{
        "actor": "OpenAI", "crux": {"text": "c", "fact_ids": ["F1"]},
        "others": [{"actor": "Services Australia", "likely_move": "?", "fact_ids": ["F1"]}],
        "choice": {"move": "First-pass move", "basis": "incentive", "trade_off": "t", "fact_ids": ["F1"]},
        "candidates": [cand(m) for m in ("Offer notice guarantees to agencies", "Lobby Brussels about fines",
                                          "Publish detection SLAs", "Cut agent sandbox access",
                                          "Settle with Services Australia", "Pause agent rollouts in Australia")]}]},
        FACTS)
    chooser = []

    def strong(prompt):
        chooser.append(prompt[:30])
        return json.dumps({"unfashionable_case": "u", "choice": {
            "move": "Best response move", "basis": "leverage", "trade_off": "gives up x", "likelihood": "likely",
            "fact_ids": ["F1"]}, "rejected": [], "must_be_true": [], "actions": []}), "pro"

    obj._ask_strong_model = strong
    stages = obj._actor_strategy_stages(draft, FACTS)
    assert stages[0]["rivals_modelled"] == ["Services Australia"]
    assert stages[0]["tournament"]["matches"] >= 2 and stages[0]["best_response"] is True
    assert draft[0]["choice"]["move"] == "Best response move"
    assert draft[0]["others"][0]["likely_move"] == "Demand notice clauses (likely)"
    assert any(p.startswith("Two candidate") for p in asked) and chooser[0].startswith("Decide the best next move")


def test_dashboard_shows_likelihood_unfashionable_case_and_rejections():
    dash = (pathlib.Path(__file__).resolve().parents[1] / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    for label in ("Likely to succeed:", "*Case for the unfashionable option (weighed before choosing):*",
                  "**Considered and rejected:**"):
        assert label in dash, label
