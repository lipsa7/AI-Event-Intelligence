"""v13.27: the remaining items of the 22 Nov research report - offline, no model calls."""
import json

from services import editorial_briefing, par_check, run_metrics, variant_view


def test_base_rate_is_similarity_weighted_and_weak_below_three():
    rated = [{"similarity": 8, "outcome_followed": "yes"}, {"similarity": 2, "outcome_followed": "no"},
             {"similarity": 5, "outcome_followed": "unknown"}]
    out = variant_view.base_rate(rated)
    assert out["rate"] == 0.8 and out["cases_used"] == 2 and "weak" in out["text"]
    assert variant_view.base_rate([])["rate"] is None


def test_rate_analogues_ignores_unknown_case_ids_and_bad_answers():
    cases = [{"case_id": "E1", "text": "a"}, {"case_id": "E2", "text": "b"}, {"case_id": "E3", "text": "c"}]
    reply = json.dumps({"cases": [{"case_id": "E1", "similarity": 9, "outcome_followed": "yes"},
                                  {"case_id": "E2", "similarity": 3, "outcome_followed": "yes"},
                                  {"case_id": "E3", "similarity": 6, "outcome_followed": "no"},
                                  {"case_id": "ZZ", "similarity": 10, "outcome_followed": "no"}]})
    out = variant_view.rate_analogues(["story"], cases, ask=lambda _p: reply)
    assert out["cases_used"] == 3 and out["rate"] == 0.67 and "usable" in out["text"]
    broken = variant_view.rate_analogues(["story"], cases, ask=lambda _p: (_ for _ in ()).throw(RuntimeError()))
    assert broken["rate"] is None and broken["error"] == "RuntimeError"


def test_premortem_returns_key_assumptions():
    reply = json.dumps({"reasons": [{"reason": "x", "already_in_evidence": False}],
                        "key_assumptions": [{"assumption": "FTC keeps the case", "undermined_by": "settlement"}]})
    out = variant_view.premortem({"insight": "i", "forecast_contract": {"question": "q", "deadline": "2027-01-01"}},
                                 [], ask=lambda _p: reply)
    assert out["killed"] is False and out["key_assumptions"][0]["assumption"] == "FTC keeps the case"
    assert "KEY ASSUMPTIONS CHECK" in variant_view.premortem_prompt({"insight": "i"}, [])


def test_head_to_head_both_orders_and_tie_counts_half():
    examples = [{"insight": "Approved specific insight about the FTC", "my_rating": "good"}]
    answers = iter([json.dumps({"better": "A"}), json.dumps({"better": "tie"})])
    out = par_check.head_to_head("Candidate about the FTC", ask=lambda _p: next(answers), examples=examples)
    assert out["win_probability"] == 0.75 and out["orders_answered"] == 2
    none = par_check.head_to_head("x", ask=lambda _p: "{}", examples=[])
    assert none["win_probability"] is None and none["reason"] == "no_exemplar"


def test_briefing_prompt_anchors_on_released_insights():
    insight = {"text": "Meta must settle before the trial", "metadata": {
        "leverage_analysis": {"holder": "New Mexico AG", "controls": "trial date"},
        "forecast_contract": {"question": "Will Meta settle?", "deadline": "2027-02-01T00:00:00"},
        "what_to_watch": [{"signal": "docket filings"}]}}
    lines = editorial_briefing.anchor_lines([insight])
    assert "actor: New Mexico AG" in lines[0] and "by 2027-02-01" in lines[0] and "docket filings" in lines[0]
    prompt = editorial_briefing.build_prompt("topic", [], [], anchors=[insight])
    assert "VERIFIED INSIGHTS OF THIS RUN" in prompt and "name who" in prompt
    assert "VERIFIED INSIGHTS OF THIS RUN" not in editorial_briefing.build_prompt("topic", [], [])


def test_objective_line_reports_estimates_not_proof():
    insights = [{"text": "a", "metadata": {
        "par_check": {"passed": True, "available": True, "head_to_head": {"win_probability": 0.75}},
        "variant_novelty": {"checked": True, "new": True}}}]
    report = {"hidden_insights": insights}
    out = run_metrics.compute(state={}, report=report)
    objective = out["objective"] if "objective" in out else out.get("metrics", {}).get("objective")
    assert objective["rubric_passed"] == "1/1" and objective["new_vs_consensus"] == "1/1"
    assert objective["win_probability_vs_approved_example"] == 0.75


def test_unjudged_insight_does_not_count_toward_met():
    from datetime import datetime, timedelta, timezone
    near = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    item = {"text": "OpenAI faces $2,500 per violation.", "leverage_analysis": {"holder": "OpenAI"},
            "par_check": {"passed": True, "available": False, "reason": "not_judged:no_outside_model"},
            "forecast_contract": {"question": "Will OpenAI settle?", "deadline": near}}
    out = run_metrics.compute(state={}, report={"hidden_insights": [item]})
    assert out["objective"]["met"] is False and out["objective"]["rubric_passed"] == "not judged"
