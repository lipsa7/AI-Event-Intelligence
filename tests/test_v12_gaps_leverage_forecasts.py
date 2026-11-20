"""v12 (16 Nov 2026): computed gaps, rule-based stitching, fact brief, base-rate probability,
red-team questions, concrete leverage positions, judge seeds and suggestions."""
import csv
import json

from services import delta_extractor as delta
from services import insight_judge as judge
from services import strategic_hypothesis_lab as lab
from services.forecast_store import ForecastStore

BREACH = [
    {"fact_id": "F1", "development": "breach", "date": "2026-09-12",
     "text": "The AI agent accessed public infrastructure in June, OpenAI said."},
    {"fact_id": "F2", "development": "breach", "date": "2026-09-12",
     "text": "OpenAI detected the activity in August and reported it on 10 September."},
    {"fact_id": "F3", "development": "funding", "date": "2026-10-02",
     "text": "Instinct raised $1 billion, after a $150 million round."},
]


def test_computes_the_breach_delay_and_an_amount_gap():
    gaps = delta.compute(BREACH)
    kinds = {g["development"]: g for g in gaps}
    assert kinds["breach"]["days"] >= 85 and "about 3 months" in kinds["breach"]["text"]
    assert kinds["funding"]["kind"] == "amount_gap" and kinds["funding"]["ratio"] == 6.7
    assert gaps[0]["development"] == "breach"                        # date gaps rank first


def test_month_without_year_takes_the_publication_year_or_the_year_before():
    from datetime import date
    found = delta.mentioned_dates("It began in November and ended on 3 February.", date(2026, 2, 20))
    assert [d[0] for d in found] == [date(2025, 11, 15), date(2026, 2, 3)]
    assert delta.compute([{"fact_id": "F1", "development": "x", "text": "Nothing dated here.",
                           "date": "2026-01-01"}]) == []


def test_lab_prompts_carry_the_computed_gaps():
    assert "COMPUTED GAPS" in lab.concrete_facts_prompt("ai", BREACH)
    assert "about 3 months" in lab.generation_prompt("ai", BREACH, ["contradiction"], batch=1)


def test_main_generator_prompt_includes_computed_gaps():
    import inspect
    from agents import strategic_inference_agent as agent
    source = inspect.getsource(agent.StrategicInferenceAgent._generate_synthesis)
    assert "delta_extractor.compute(" in source and "computed_gaps_text" in source


def _row(row_id, fact_ids, link="none", thesis="The October accord names no verifier, so compliance cannot be checked."):
    return {"id": row_id, "lens": "x", "thesis": thesis, "novel_proposition": "n", "why_not_obvious": "w",
            "mechanism": "A -> B -> C", "falsifier": "f", "watch_signal": "w", "alternative_explanation": "a",
            "fact_ids": fact_ids, "inference_strength": "follows", "rival_fits_as_well": "no",
            "link_fact_id": link,
            "critic_scores": {k: 2 for k in ("mental_model_change", "mechanism_depth", "surprise_nonobviousness",
                                             "strategic_consequence", "falsifiability")}}


def test_stitching_is_decided_by_shared_actors_not_the_models_claim():
    facts = [{"fact_id": "F1", "development": "Accord", "actors": ["OpenAI"], "text": "OpenAI signed an accord in October."},
             {"fact_id": "F2", "development": "Funding", "actors": ["Instinct"], "text": "Instinct raised $1 billion."},
             {"fact_id": "F3", "development": "Breach", "actors": ["OpenAI", "Australia"],
              "text": "OpenAI reported the breach in September."}]
    reply = json.dumps({"hypotheses": [_row("A", ["F1", "F2"], link="F2"),     # model claims a link; none exists
                                       _row("B", ["F1", "F3"], thesis="OpenAI reported the September breach "
                                            "while its October accord names no verifier.")]})   # share OpenAI
    stats = {}
    kept = [item["id"] for item in lab.parse_evolution(reply, facts, stats=stats)]
    assert kept == ["B"] and stats["evolution_stitched_without_link"] == 1


def test_forecast_gets_a_base_rate_probability_when_the_model_gives_counts():
    candidate = {"metadata": {"forecast_contract": {"question": "q", "deadline": "2027-06-30T00:00:00+00:00",
                                                    "resolution_criteria": "r"},
                              "likelihood_assessment": {"label": "moderate", "base_rate": {
                                  "comparable_cases": 3, "outcome_cases": 2, "evidence_ids": ["EVID-P1"]}}}}
    data = ForecastStore.contract(candidate)
    assert data["probability"] == 0.6 and "2 of 3" in data["numeric_basis"]
    unsourced = json.loads(json.dumps(candidate))
    unsourced["metadata"]["likelihood_assessment"]["base_rate"]["evidence_ids"] = []
    assert ForecastStore.contract(unsourced)["probability"] is None


def test_red_team_asks_the_three_targeted_questions_and_leverage_asks_for_positions():
    import inspect
    from agents import red_team_agent, strategic_inference_agent
    text = red_team_agent.RedTeamAgent._build_system_instruction()
    assert "LEAD FACT" in text and "STITCHING" in text and "WEAKEST ASSUMPTION" in text
    source = inspect.getsource(strategic_inference_agent)
    assert "who KNOWS FIRST" in source and "\"alternatives\":\"the other options the dependent has" in source


def test_fact_brief_is_attached_to_every_report():
    import inspect
    from services.research_orchestrator_service import ResearchOrchestratorService
    source = inspect.getsource(ResearchOrchestratorService._attach_spec_sections)
    assert 'report["computed_gaps"]' in source
    from pathlib import Path
    dashboard = Path(__file__).resolve().parent.parent / "frontend" / "dashboard.py"
    assert "def render_fact_brief" in dashboard.read_text(encoding="utf-8")   # Streamlit not needed


def test_judge_uses_built_in_verdicts_and_suggests_likely_passes(tmp_path):
    path = tmp_path / "ratings.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["no", "date", "your_rating", "insight"])
        writer.writeheader()
        writer.writerow({"no": "54", "date": "2026-10-22", "your_rating": "", "insight": "Three-month delay."})
        writer.writerow({"no": "86", "date": "2026-11-14", "your_rating": "pass", "insight": "Owner changed mind."})
        writer.writerow({"no": "41", "date": "2025-01-01", "your_rating": "", "insight": "A different idea."})
        writer.writerow({"no": "7", "date": "2026-10-09", "your_rating": "",
                         "insight": "Governments learned in September of a June breach."})
        writer.writerow({"no": "8", "date": "2026-10-09", "your_rating": "", "insight": "A structural asymmetry."})
    rated = {r["id"]: r["label"] for r in judge.load_ratings(path)}
    assert rated == {"54": True, "86": True}            # the file's own rating wins; dates must match
    suggested = judge.suggest_to_rate(path)
    assert suggested[0]["no"] == "7" and suggested[0]["likely_pass"]
    result = judge.agreement([{"id": "1", "text": "t", "label": False, "note": ""}] * 9
                             + [{"id": "2", "text": "u", "label": True, "note": ""}],
                             lambda prompt: json.dumps({"pass": False}), "std")
    assert result["accuracy"] == 0.9 and result["balanced_accuracy"] == 0.5     # fail-everything exposed
