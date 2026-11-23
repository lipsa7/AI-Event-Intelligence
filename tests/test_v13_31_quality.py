"""v13.31: analyst moves, link checks, case library, quality scorecard, calibration metrics, blind pairs.
No model or network calls."""
import json
import sys
from pathlib import Path

from services import analytic_moves, case_library, link_check, par_calibration, quality_scorecard, variant_view

RECORDS = [
    {"evidence_id": "E1", "event_id": "V1", "text": "Texas sued OpenAI demanding that a court-appointed evaluator approve new models."},
    {"evidence_id": "E2", "event_id": "V1", "text": "The evaluator must approve each ChatGPT model before release in Texas."},
    {"evidence_id": "EU", "event_id": "", "text": "The EU will start AI regulatory sandboxes on 10 November 2026."},
]


def test_moves_follow_the_story_signals_and_keep_an_anchor():
    view = {"labels": ["Texas sues OpenAI"], "claims": [{"claim": "a"}, {"claim": "b"}],
            "fact_rows": [{"kind": "money"}] * 4, "computed_facts": [{"kind": "money_ratio"}]}
    moves = analytic_moves.select(view, RECORDS)
    assert len(moves) == 4 and "mechanism_explainer" in moves and len(set(moves)) == 4
    assert "change_vs_prior" in analytic_moves.select(dict(view, earlier_theses=["OpenAI ..."]), RECORDS)


def test_move_prompt_states_the_goal_not_the_gate_thresholds():
    text = variant_view.move_instruction({"claims": [{"claim": "Texas sued", "source": "E1", "independent_sources": 3}],
                                          "cases": [{"case_id": "CASE-1", "name": "People v. ConAgra"}],
                                          "earlier_theses": ["OpenAI faces liability"]}, "mechanism_explainer")
    assert "MOVE: mechanism_explainer" in text and "held by 3 source(s)" in text and "CASE-1" in text
    assert "EARLIER THESES" in text and "25 words" not in text and "0.25" not in text


def test_stitched_link_fails_and_a_quoted_connected_chain_passes():
    stitched = [{"step": "Texas demands an evaluator for OpenAI models", "evidence_ids": ["E1"],
                 "quote": "a court-appointed evaluator approve new models"},
                {"step": "the EU starts sandboxes, so oversight overlaps", "evidence_ids": ["EU"],
                 "quote": "The EU will start AI regulatory sandboxes"}]
    assert link_check.problems(stitched, RECORDS) == ["link_not_connected:1->2"]
    good = [stitched[0], {"step": "OpenAI must hold each model for the Texas evaluator", "evidence_ids": ["E2"],
                          "quote": "The evaluator must approve each ChatGPT model before release"}]
    assert link_check.problems(good, RECORDS) == []
    made_up = [dict(good[0], quote="OpenAI agreed to delay every launch by a year")]
    assert link_check.problems(made_up, RECORDS) == ["link_quote_not_in_source:1"]
    assert link_check.problems([{"step": "x", "evidence_ids": ["E1"]}], RECORDS) == ["link_unquoted:1"]


def test_case_library_uses_only_approved_cases_and_retrieves_by_mechanism(tmp_path, monkeypatch):
    path = tmp_path / "cases.jsonl"
    rows = [{"case_id": "C1", "approved": True, "legal_theory": "public nuisance", "mechanism_tags": ["affirmative-promotion"],
             "key_holding": "liability for promoting lead paint", "outcome": "settled", "time_to_outcome_days": "7000"},
            {"case_id": "C2", "approved": True, "legal_theory": "antitrust monopoly", "mechanism_tags": ["search-default"],
             "key_holding": "exclusive defaults", "outcome": "liability found", "time_to_outcome_days": "1400"},
            {"case_id": "C3", "approved": False, "legal_theory": "public nuisance", "mechanism_tags": []}]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    monkeypatch.setenv(case_library.PATH_ENV, str(path))
    cases = case_library.load()
    assert [c["case_id"] for c in cases] == ["C1", "C2"]
    found = case_library.retrieve("state sues AI maker for public nuisance after promoting agents", cases, k=1)
    assert found[0]["case_id"] == "C1"
    assert case_library.base_rate(cases, "settled") == {"cases": 2, "share": 0.5, "median_days": 7000}


def test_case_from_memory_is_flagged():
    cand = {"insight": "x", "leverage_analysis": {"holder": "OpenAI"},
            "variant_perception": {"case_id": "CASE-MADEUP", "overlooked_evidence_id": "E2", "rival_hypothesis": "r",
                                   "load_bearing_assumption": "a", "mechanism_chain": []}}
    assert "case_not_in_library" in variant_view.perception_problems(cand, {"cases": [{"case_id": "C1"}]}, RECORDS)


def test_thesis_is_shortened_in_code_and_contradictions_are_checked():
    long = {"insight": "Texas forces OpenAI, the maker of ChatGPT and other models used by many enterprises across the "
                       "country, to hold every release for a court-appointed evaluator. More text."}
    out = variant_view.apply_short_thesis(long, {"sentence": "Texas forces OpenAI to hold every release for an evaluator."}, 25)
    assert out["insight"] == "Texas forces OpenAI to hold every release for an evaluator. More text."
    assert variant_view.apply_short_thesis(long, {"sentence": " ".join(["word"] * 30)}, 25) is None
    reply = {"contradicted": True, "statement": "filed in March", "evidence_id": "E1", "record_says": "filed in May"}
    assert variant_view.contradiction(long, [], ask=lambda p: reply)["contradicted"] is True
    assert variant_view.contradiction(long, [], ask=lambda p: "?")["checked"] is False


def test_scorecard_maps_problems_to_rubric_items_and_aggregates():
    row = quality_scorecard.score_problems(["link_not_connected:1->2", "forecast_not_diagnostic"],
                                           {"forecast": True, "not_restated": True, "uncontradicted": True})
    assert row["linked"] == 0 and row["forecast"] == 0 and row["mechanism"] == 1 and not row["all_met"]
    unchecked = quality_scorecard.score_problems([], {})
    assert unchecked["forecast"] is None and unchecked["all_met"] is False
    good = quality_scorecard.score_problems([], {"forecast": True, "not_restated": True, "uncontradicted": True})
    log = {"exploration": {"combinations": [
        {"kept": [{"insight": "a", "rubric": good}], "computed_facts": 6, "consensus_sources": 10, "past_cases": 2,
         "variant_dropped": [{"insight": "b", "rubric": row}]}]}}
    card = quality_scorecard.run_scorecard(log, {"hidden_insights": []})
    assert card["candidates"] == 2 and card["share_meeting_every_item"] == 0.5
    assert card["most_failed_items"] == {"linked": 1, "forecast": 1} and card["computed_facts_per_story"] == 6.0
    assert "mean rubric" in quality_scorecard.lines(card)[0]
    lines = quality_scorecard.trend_lines([dict(card, report_id="R1", created_at="2026-11-22"),
                                           dict(card, report_id="R2", created_at="2026-11-23", mean_rubric_score=0.9)])
    assert "(+" in lines[1]


def test_calibration_reports_kappa_and_held_out_metrics():
    assert par_calibration.kappa([1, 1, 0, 0], [1, 1, 0, 0]) == 1.0
    assert par_calibration.kappa([1, 1, 1, 1], [1, 0, 1, 0]) == 0.0
    x = [[0.8] * 6 + [1.0] if i % 3 == 0 else [0.3] * 6 + [0.0] for i in range(80)]
    y = [1 if i % 3 == 0 else 0 for i in range(80)]
    held = par_calibration.holdout(x, y)
    assert held["kappa"] == 1.0 and held["n"] == 20


def test_blind_pairs_wilson_interval():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import blind_pairs
    low, high = blind_pairs.wilson(15, 30)
    assert 0.31 < low < 0.34 and 0.66 < high < 0.69


def test_generation_uses_the_selected_moves(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    monkeypatch.setenv("EVENT_INTELLIGENCE_ANALYTIC_MOVES", "1")
    service = S.__new__(S)
    lenses = service._generation_lenses({"labels": ["Texas sues OpenAI"], "_records": RECORDS, "claims": []})
    assert lenses and all(l["instruction"].startswith("MOVE: " + l["name"]) for l in lenses)
    assert lenses[0]["name"] in analytic_moves.MOVES


def test_review_fixes():
    # a missing variant perception fails four rubric items, not none
    row = quality_scorecard.score_problems(["no_variant_perception"], {})
    assert row["grounded_quotes"] == row["linked"] == row["mechanism"] == row["alternatives"] == 0
    # scheduled events are not forecasts
    sched = {"forecast_contract": {"question": "Will the hearing be held on 15 January 2027?"}}
    assert variant_view.call_problems(sched) == ["forecast_scheduled_event"]
    ruling = {"forecast_contract": {"question": "Will the court let the nuisance count proceed?"}}
    assert variant_view.call_problems(ruling) == []
    # an analogy must say where it differs
    cand = {"insight": "x", "variant_perception": {"case_id": "C1", "overlooked_evidence_id": "E2",
                                                   "rival_hypothesis": "r", "load_bearing_assumption": "a"}}
    assert "no_disanalogy" in variant_view.perception_problems(cand, {"cases": [{"case_id": "C1"}]}, RECORDS)


def test_owner_examples_in_move_prompt(monkeypatch):
    from services import par_check
    monkeypatch.setattr(par_check, "load_labels", lambda path=None: [
        {"insight": "Texas's evaluator demand gives OpenAI a 90-day release lag", "my_rating": "good"},
        {"insight": "Something generic", "my_rating": "generic restatement"}])
    text = variant_view.move_instruction({"labels": ["Texas OpenAI evaluator"], "claims": []}, "constraints")
    assert "INSIGHTS THE OWNER APPROVED" in text and "90-day release lag" in text and "Something generic" not in text


def test_case_library_tool_reads_v4_snippets():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    import build_case_library
    assert build_case_library._snippet({"opinions": [{"snippet": "held liable for promotion"}]}) == "held liable for promotion"
