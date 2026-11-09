"""Insight funnel (services/insight_funnel.py): losses traceable by id, never mixing units."""
from services import insight_funnel as funnel
from services import strategic_hypothesis_lab as lab


def sections():
    return {
        "strategic_hypotheses": {
            "status": "complete", "raw_count": 5, "evolved_count": 3,
            "funnel": {"generated_rows": 7, "generated_invalid": 2, "evolution_low_critic": 1},
            "narrowing": {"tried": 2, "verified": 1, "dropped": 1},
            "verified": [{"id": "EV1-N", "thesis": "Narrowed claim.", "critic_inherited_from": "EV1"}],
            "promising_unverified": [
                {"id": "EV1", "thesis": "Original claim.", "grounding": {"thesis": "inference_supported",
                                                                       "mechanism": "partially_supported"},
                 "field_checks": {"likely_actor_move": "supported", "countermove_basis": "unsupported"},
                 "narrowing": "narrowed_version_verified:EV1-N"},
                {"id": "EV3", "thesis": "Discovery inversion.", "grounding": {"thesis": "inference_supported",
                                                                            "mechanism": "partially_supported"},
                 "promotion_blockers": ["absolute_or_exclusive_claim_requires_direct_support"],
                 "narrowing": "nothing_strategic_survived_or_still_overstated"},
            ],
        },
        "insight_evaluation": {"promising_unverified": [{"insight_id": "INSIGHT-1", "text": "Gate near miss."}]},
        "promising_hypotheses": [{"insight_id": "INSIGHT-1", "text": "Gate near miss.",
                                  "failure_reasons": ["premise_not_supported_by_cited_evidence"]}],
        "analysis_metadata": {"claim_support_gate": {
            "abstention_reasons": ["claim_support_gate:premise_not_supported_by_cited_evidence"],
            "attempts": [{"failed_claims": [{"claim_id": "C-LEV", "claim_text": "X controls Y.",
                                             "epistemic_type": "fact", "grounding_status": "partially_supported"}]},
                         {"failed_claims": [{"claim_id": "C-MECH", "claim_text": "So Z.",
                                             "epistemic_type": "mechanism", "grounding_status": "unsupported"}]}]}},
        "editorial_briefing": {"mode": "checked_analysis", "case_count": 0, "sentences_removed_by_check": 4,
                               "implications": [{"text": "Taskforce will recommend windows.", "novelty": "beyond_headlines"}],
                               "expected_implications": [{"text": "Terms diverge.", "novelty_reasons": ["already_reported"]}],
                               "analogies": [], "expert_commentary": []},
        "actor_moves": {"status": "checked", "removed": ["OpenAI leverage not supported: steer the taskforce"],
                        "actors": [{"actor": "OpenAI", "moves": [{"move": "Explain the delay.", "basis": "incentive"}],
                                    "expected_moves": [{"move": "Apologise."}],
                                    "rejected": [{"move": "Argue a technicality.", "why_not": "Ignores EU fines."}]}]},
    }


def rows(stage, unit="candidates"):
    return [(r["id"], r["outcome"]) for r in stage[unit]]


def test_one_candidate_row_per_distinct_id_with_events_separate():
    result = funnel.build(sections())
    lab_stage = result["stages"]["hypothesis_lab"]
    assert rows(lab_stage) == [("EV1-N", "passed"), ("EV1", "repaired"), ("EV3", "rejected")]
    assert result["tally"]["hypothesis_lab"]["distinct_candidate_ids"] == 3
    assert [r["id"] for r in lab_stage["events"]] == ["EV1", "EV3"]
    ev3 = lab_stage["candidates"][2]
    assert "mechanism=partially_supported" in ev3["reason"] and "blocked:absolute" in ev3["reason"]
    assert "rewritten from EV1, unscored" in lab_stage["candidates"][0]["reason"]


def test_display_states_are_their_own_unit():
    lab_stage = funnel.build(sections())["stages"]["hypothesis_lab"]
    assert ("EV3", "collapsed") in rows(lab_stage, "display")
    assert ("INSIGHT-1", "collapsed") in rows(lab_stage, "display")
    assert ("EV1-N", "shown") in rows(lab_stage, "display")
    data = sections()
    data["strategic_hypotheses"]["verified"] = []
    states = {o for _, o in rows(funnel.build(data)["stages"]["hypothesis_lab"], "display")}
    assert states == {"shown"}


def test_attempts_claims_and_sentences_are_counted_in_their_own_units():
    result = funnel.build(sections())
    gate = result["tally"]["claim_support_gate"]
    assert gate["attempts"] == 2 and gate["failed_claims"] == 2 and gate["candidates"] == {"rejected": 1}
    brief = result["tally"]["editorial_briefing"]
    assert brief["sentences_pruned"] == 4 and brief["candidates"] == {"passed": 1, "downgraded": 1}
    moves = result["tally"]["actor_moves"]
    assert moves["candidates"] == {"passed": 1, "downgraded": 1, "rejected": 1} and moves["failed_claims"] == 1


def test_missing_records_say_not_recorded_rather_than_zero():
    data = sections()
    del data["strategic_hypotheses"]["funnel"]
    del data["editorial_briefing"]["sentences_removed_by_check"]
    del data["analysis_metadata"]["claim_support_gate"]["attempts"]
    result = funnel.build(data)
    assert result["stages"]["hypothesis_lab"]["losses_before_grounding"] == funnel.NOT_RECORDED
    assert result["stages"]["editorial_briefing"]["sentences_pruned"] == funnel.NOT_RECORDED
    assert result["stages"]["claim_support_gate"]["attempts"] == funnel.NOT_RECORDED
    assert "attempts" not in result["tally"]["claim_support_gate"]
    empty = funnel.build({})
    assert empty["stages"]["editorial_briefing"]["mode"] == funnel.NOT_RECORDED


def test_summary_lists_rejected_candidates_only():
    summary = funnel.summary(funnel.build(sections()))
    assert summary["hypothesis_lab"]["rejected_ids"] == ["EV3"]
    assert summary["claim_support_gate"]["rejected_ids"] == ["INSIGHT-1"]


def row(n, **extra):
    base = {"thesis": f"Distinct hypothesis number {n} about a shifted dependency between two actors.",
            "mechanism": "A -> B -> C", "novel_proposition": "p", "why_not_obvious": "w",
            "alternative_explanation": "a", "falsifier": "f", "watch_signal": "s", "fact_ids": ["F1"]}
    base.update(extra)
    return base


FACTS = [{"fact_id": "F1", "text": "A fact."}]


def test_lab_parsers_count_their_drops():
    stats = {}
    reply = {"hypotheses": [row(1), row(1), {"thesis": "missing fields"}, "not a row"]}
    assert len(lab.parse_generation(reply, FACTS, stats=stats)) == 1
    assert stats == {"generated_rows": 4, "generated_invalid": 2, "generated_duplicates": 1}
    scores = {k: 2 for k in ("mental_model_change", "mechanism_depth", "surprise_nonobviousness",
                             "strategic_consequence", "falsifiability")}
    low = {k: 1 for k in scores}
    stats = {}
    evolved = lab.parse_evolution({"hypotheses": [row(2, critic_scores=scores), row(3, critic_scores=low)]},
                                  FACTS, stats=stats)
    assert len(evolved) == 1 and stats["evolution_low_critic"] == 1 and stats["evolution_rows"] == 2


def test_lab_parsers_unchanged_without_stats():
    assert len(lab.parse_generation({"hypotheses": [row(1)]}, FACTS)) == 1
