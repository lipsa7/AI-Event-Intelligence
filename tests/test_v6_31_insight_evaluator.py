"""v6.31: the insight evaluator (services/insight_evaluator.py).

Evidence gate first (only checked items are judged), a 0-12 rubric of yes/no checks,
order-swapped pairwise ranking aggregated with Bradley-Terry (a model compares two insights
more reliably than it scores one alone - PAIRS, Chatbot Arena), provenance labels instead of
a novelty kill switch, and a gold set built from the user's own verdicts.
"""

from __future__ import annotations

import inspect
import json
import pathlib
import re

import services.research_orchestrator_service as ros
from services import insight_evaluator as ev

REPORT = {
    "hidden_insights": [
        {"insight": "Immediate-reporting mandates cannot work while developers take months to see what their agents did.",
         "source_backed": {"reasons": ["conclusion_already_in_expert_commentary"],
                           "expert_citations": [{"publisher": "insurancebusinessmag.com, established news"}]}},
        {"insight": "Only OpenAI holds the agent logs Services Australia needs to meet its reporting duty.",
         "insight_route": "constraint", "leverage_analysis": {"predicted_move": "Agencies will demand log access."}},
    ],
    "actor_moves": {"actors": [{"actor": "OpenAI", "crux": {"text": "Only OpenAI sees agent logs."},
                                "leverage": {"power": "cornered_resource", "benefit": "sole access to agent logs"},
                                "choice": {"move": "Offer agencies a 72-hour notice guarantee before rules force it",
                                           "trade_off": "accepts liability"},
                                "moves": [{"move": "Publish a detection SLA for government agents",
                                           "why_it_benefits": "turns log access into trust",
                                           "expert_sources": [{"publisher": "csis.org, research institution"}]}]}]},
    "editorial_briefing": {
        "implications": [{"text": "Agencies will add vendor-notice clauses to AI contracts at renewal.",
                          "novelty": "beyond_headlines"}],
        "expected_implications": [{"text": "Governments will tighten AI security rules after the breach."}]},
}


def test_every_checked_item_is_eligible_with_provenance():
    items = ev.candidates(REPORT)
    by = {i["text"][:30]: i for i in items}
    first = by["Immediate-reporting mandates c"]
    assert first["type"] == "analysis" and first["corroboration"] == ["insurancebusinessmag.com, established news"]
    assert by["Only OpenAI holds the agent lo"]["type"] == "leverage_decision"
    assert by["OpenAI should: Offer agencies "]["type"] == "leverage_decision"
    assert by["Agencies will add vendor-notic"]["type"] == "analysis"
    assert by["Governments will tighten AI se"]["widely_expected"] is True
    # v6.31.1: an expert agreeing with a leverage decision CORROBORATES it; it stays a leverage decision.
    backed_move = by["OpenAI should: Publish a detec"]
    assert backed_move["type"] == "leverage_decision" and backed_move["corroboration"] == ["csis.org, research institution"]
    assert backed_move["provenance_label"] == ("Strategic decision from leverage · "
                                               "Expert-corroborated (csis.org, research institution)")


def _answers(true_keys):
    return {key: key in true_keys for key, _, _ in ev.RUBRIC}


def test_rubric_is_twelve_yes_no_checks_and_incomplete_scores_are_dropped():
    assert len(ev.RUBRIC) == 12 and {d for _, d, _ in ev.RUBRIC} == set(ev.DIMENSIONS)
    items = [{"id": "I1"}, {"id": "I2"}]
    reply = json.dumps({"scores": [{"id": "I1", "answers": _answers({"A1", "A2", "C1", "C2", "F1"})},
                                   {"id": "I2", "answers": {"A1": True}}]})
    out = ev.parse_rubric(reply, items)
    assert out["I1"]["score"] == 5 and out["I1"]["dimensions"]["leverage_incentives"] == 2
    assert "I2" not in out
    assert ev.band(11) == "strong strategic insight" and ev.band(8) == "useful insight" and ev.band(2) == "weak or generic"
    assert "1-10" not in ev.rubric_prompt("q", [{"id": "I1", "kind": "k", "text": "t", "detail": ""}])


def _items(n):
    return [{"id": f"I{i}", "text": f"insight {i}", "provenance": "derived", "detail": "", "score": 12 - i}
            for i in range(1, n + 1)]


def test_position_biased_judge_yields_uncertain_not_a_ranking():
    calls = []

    def always_first(prompt):
        calls.append(prompt)
        return {"winner": "A"}

    out = ev.rank("q", _items(3), always_first)
    assert len(calls) == 6 and out["log"]["pairs"] == 3 and out["log"]["uncertain"] == 3
    assert max(out["strength"].values()) - min(out["strength"].values()) < 1e-6
    assert all(s == [0, 2] for s in out["stability"].values())


def test_consistent_judge_ranks_by_preference_with_bradley_terry():
    # The judge prefers the lower number (I1 best), whichever order it sees them in.
    def judge(prompt):
        a, b = re.findall(r"^(?:A|B) \[[^\]]*\]: insight (\d)", prompt, re.M)
        return {"winner": "A" if int(a) < int(b) else "B"}

    out = ev.rank("q", list(reversed(_items(4))), judge)
    order = sorted(out["strength"], key=lambda k: -out["strength"][k])
    assert order == ["I1", "I2", "I3", "I4"] and out["log"]["uncertain"] == 0
    assert all(s == [3, 3] for s in out["stability"].values())


def test_pairwise_prompt_puts_leverage_first_and_does_not_penalise_experts():
    prompt = ev.pairwise_prompt("q", {"text": "a", "provenance": "expert_backed", "detail": ""},
                                {"text": "b", "provenance": "leverage_decision", "detail": ""})
    assert "strategic decisions based on leverage" in prompt
    assert "Being already argued by experts is NOT a weakness" in prompt
    assert "Ignore which one is shown first" in prompt and '"tie"' in prompt


def test_evaluate_scores_ranks_and_labels_everything():
    def judge(prompt):
        if prompt.startswith("Research question: ai\n\nJudge each item"):
            ids = re.findall(r"^\[(I\d+)\]", prompt, re.M)
            rows = []
            for i in ids:
                text = re.search(rf"^\[{i}\] .*\n    Item: (.*)$", prompt, re.M).group(1)
                strong = "OpenAI" in text
                rows.append({"id": i, "answers": _answers({k for k, _, _ in ev.RUBRIC} if strong else {"A1"}),
                             "weakest": "x"})
            return {"scores": rows}
        a, b = re.findall(r"^(?:A|B) \[[^\]]*\]: (.*)$", prompt, re.M)
        return {"winner": "A" if ("OpenAI" in a, len(a)) >= ("OpenAI" in b, len(b)) else "B"}

    out = ev.evaluate(REPORT, "ai", judge)
    assert out["status"] == "complete" and out["eligible"] == 6 and len(out["items"]) == 6
    assert [i["rank"] for i in out["items"]] == [1, 2, 3, 4, 5, 6]
    assert all(i["provenance_label"] for i in out["items"]) and len(out["top"]) == 3
    assert out["pairwise"]["pairs"] == 10                      # all pairs among the top 5
    assert sum(1 for i in out["items"] if i["ranked_by"] == "rubric score") == 1
    assert {i["band"] for i in out["items"][:3]} == {"strong strategic insight"}
    assert any(i["corroboration"] for i in out["items"])                   # expert-backed kept, never removed


def test_judge_unavailable_never_breaks_the_report():
    def broken(prompt):
        raise RuntimeError("down")

    assert ev.evaluate(REPORT, "ai", broken)["status"] == "judge_unavailable"
    assert ev.evaluate({}, "ai", broken)["status"] == "no_items"


def test_gold_set_separates_your_verdicts_from_reference_pairs():
    gold = json.loads((pathlib.Path(__file__).resolve().parent / "data" / "insight_gold.json").read_text(encoding="utf-8"))
    assert "NOT a user verdict" in gold["items"]["REF"]["note"]
    assert all("REF" not in (p["a_id"], p["b_id"]) for p in gold["human_pairs"])          # synthetic never counts
    assert all(p["a_id"] in {"D2", "MIL", "SUPER"} and p["b_id"] in {"D2", "MIL", "SUPER"} for p in gold["human_pairs"])
    assert any("REF" in (p["a_id"], p["b_id"]) for p in gold["reference_pairs"])
    every = gold["human_pairs"] + gold["reference_pairs"]
    prefer = {(p["a"], p["b"]): p["human_winner"] for p in every}

    def agrees(prompt):          # a judge that always agrees with the expected winner, in either order
        a, b = [re.search(rf"^{x} \[[^\]]*\]: (.*)$", prompt, re.M).group(1) for x in ("A", "B")]
        if (a, b) in prefer:
            return {"winner": prefer[(a, b)]}
        return {"winner": {"A": "B", "B": "A", "TIE": "TIE"}[prefer[(b, a)]]}

    result = ev.agreement(gold, agrees)
    assert result["agreement_with_you"]["pairwise_accuracy"] == 1.0
    assert result["agreement_with_you"]["pairs"] == len(gold["human_pairs"]) == result["human_pairs"]
    assert result["reference_sanity"]["pairs"] == len(gold["reference_pairs"])
    biased = ev.agreement(gold, lambda p: {"winner": "A"})
    assert biased["agreement_with_you"]["order_flip_rate"] == 1.0


def test_judge_sees_the_evidence_and_the_labels():
    items = ev.candidates(REPORT, {"E9": "Services Australia learned of the breach three months late."})
    report = dict(REPORT, editorial_briefing={"implications": [
        {"text": "Agencies will add vendor-notice clauses to AI contracts at renewal.", "basis": "leverage",
         "citations": [{"evidence_id": "E9"}]}]})
    imp = [i for i in ev.candidates(report, {"E9": "Services Australia learned of the breach three months late."})
           if i["kind"] == "implication"][0]
    assert imp["type"] == "leverage_decision"
    assert imp["evidence"] == ["[source states] Services Australia learned of the breach three months late."]
    prompt = ev.rubric_prompt("ai", [imp])
    assert "Evidence (what the sources state):" in prompt and "three months late" in prompt
    assert "Strategic decision from leverage" in prompt
    assert "beyond what the listed evidence already states" in prompt
    assert "Evidence: [source states]" in ev.pairwise_prompt("ai", imp, items[0])


def test_report_and_dashboard_carry_the_evaluation():
    source = inspect.getsource(ros.ResearchOrchestratorService._persist_report)
    assert '"insight_evaluation": _json_safe(evaluation)' in source
    root = pathlib.Path(__file__).resolve().parents[1]
    dash = (root / "frontend" / "dashboard.py").read_text(encoding="utf-8")
    assert "Top strategic insights" in dash and "render_insight_ranking(sections)" in dash
    assert "Strategic value {item.get('score')}/12" in dash and "Pairwise stability" in dash
    assert (root / "eval_insight_judge.py").exists()
