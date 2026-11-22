"""v13.23: consensus first, competing variant views, structure gate, pairwise tournament. No model calls."""
from datetime import datetime, timedelta, timezone

from services import variant_view

TODAY = datetime(2026, 11, 22, tzinfo=timezone.utc)


def cand(text, days=42, holder="OpenAI", question="Will OpenAI publish X?"):
    deadline = (TODAY + timedelta(days=days)).isoformat() if days is not None else ""
    return {"insight": text, "leverage_analysis": {"holder": holder},
            "premises": [{"text": "p", "evidence_ids": ["EVID-1"], "notes": "quote_check=cited"}],
            "forecast_contract": {"question": question, "deadline": deadline, "resolution_criteria": "public report"}}


GOOD = cand("California's 31-page complaint seeks $2,500 per violation; OpenAI will settle before the 2027 hearing.")


def test_structure_gate_accepts_a_checkable_variant_view():
    assert variant_view.structure_problems(GOOD, today=TODAY) == []


def test_structure_gate_rejects_what_cannot_be_checked_in_weeks():
    assert "no_number" in variant_view.structure_problems(cand("OpenAI will change its testing."), today=TODAY)
    assert variant_view.structure_problems(cand("Fine of $2,500 per violation.", days=270), today=TODAY) == ["call_horizon_270_days"]
    assert variant_view.structure_problems(cand("Fine of $2,500 per violation.", days=5), today=TODAY) == ["call_horizon_5_days"]
    assert "no_dated_call" in variant_view.structure_problems(cand("Fine of $2,500.", days=None), today=TODAY)
    assert "no_named_actor" in variant_view.structure_problems(
        cand("fines of $2,500 will rise.", holder=""), today=TODAY)


def test_consensus_map_is_cited_and_names_search_terms():
    records = [{"evidence_id": "EVID-1", "text": "California sued OpenAI on 16 Nov.", "gap_type": ""},
               {"evidence_id": "EVID-2", "text": "Analysts say testing is now a nuisance.", "gap_type": "commentary"}]
    analysis = [{"url": "https://example.org/note", "snippet": "Most lawyers expect a settlement within a year."}]
    seen = {}

    def ask(prompt):
        seen["p"] = prompt
        return {"claims": [{"claim": "Readers expect a long court fight.", "source": "EVID-1", "date": "2026-11-16"},
                           {"claim": "Lawyers expect a settlement.", "source": "CONS-1"},
                           {"claim": "Testing is a nuisance.", "source": "EVID-2"},
                           {"claim": "uncited", "source": ""}],
                "search_terms": {"federal_register": "AI agent safety", "sec": "OpenAI", "court": "California v. OpenAI"}}
    view = variant_view.consensus(["California v. OpenAI"], records, ask, analysis=analysis)
    assert [c["source"] for c in view["claims"]] == ["EVID-1", "CONS-1", "EVID-2"] and view["enough"] is True
    assert view["claims"][1]["url"] == "https://example.org/note"
    assert view["search_terms"]["court"] == "California v. OpenAI"
    assert "[CONS-1] (published analysis, https://example.org/note)" in seen["p"]
    broken = variant_view.consensus(["x"], records, lambda p: (_ for _ in ()).throw(RuntimeError("down")))
    assert broken["claims"] == [] and variant_view.instruction(broken) == ""


def test_instruction_lists_consensus_lenses_and_primary_records():
    view = {"claims": [{"claim": "Readers expect a long court fight.", "source": "EVID-1", "date": "2026-11-16"}]}
    text = variant_view.instruction(view, ["EVID-PRIMARY-ABC"])
    for part in ("CONSENSUS[1] (EVID-1, 2026-11-16): Readers expect a long court fight.", "DIFFERENT lens",
                 "constraints:", "value_chain:", "follow_the_money:", "structured_analogy:", "EVID-PRIMARY-ABC",
                 "2 to 12 weeks", "at most 35 plain words", "analytical_lenses"):
        assert part in text


def test_tournament_judges_both_orders_and_ranks_by_wins():
    items = [dict(GOOD, insight="weak $1"), dict(GOOD, insight="strong $2"), dict(GOOD, insight="middle $3")]
    strength = {"weak": 0, "middle": 1, "strong": 2}
    asked = []

    def ask(prompt):
        asked.append(prompt)
        a_text = prompt.split("\n\nA: ")[1].split("\n\nB: ")[0]
        b_text = prompt.split("\n\nB: ")[1].split("\n\nReturn")[0]
        sa, sb = strength[a_text.split()[0]], strength[b_text.split()[0]]
        return {"better": "A" if sa > sb else "B"}
    ranked = variant_view.tournament(items, {"consensus": "c"}, ask)
    assert [r["insight"].split()[0] for r in ranked] == ["strong", "middle", "weak"]
    assert len(asked) == 6                       # 3 pairs x 2 orders
    assert ranked[0]["metadata"]["variant_tournament"] == {"wins": 4.0, "rank": 1, "of": 3}


def test_orchestrator_select_drops_uncheckable_and_keeps_the_winner(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    monkeypatch.setenv("EVENT_INTELLIGENCE_VARIANT_VIEW", "1")
    service = S.__new__(S)
    service._ask_briefing_model = lambda prompt: {"better": "B"}
    long_call = cand("Fine of $2,500 per violation.", days=300)
    other = cand("A second variant: 80 escapes means a 2027 consent decree for OpenAI.", days=60)
    near = dict(GOOD)
    near["forecast_contract"] = dict(GOOD["forecast_contract"],
                                     deadline=(datetime.now(timezone.utc) + timedelta(days=40)).isoformat())
    other["forecast_contract"] = dict(other["forecast_contract"],
                                      deadline=(datetime.now(timezone.utc) + timedelta(days=60)).isoformat())
    entry = {}
    kept = service._variant_select([long_call, near, other], {"consensus": "c"}, entry)
    assert len(kept) == 1 and entry["variant_kept"] == 1
    assert entry["variant_dropped"][0]["problems"][0].startswith("call_horizon_")
    assert kept[0]["metadata"]["variant_structure"] == ["ok"]


def test_off_switch_leaves_candidates_alone(monkeypatch):
    from services.research_orchestrator_service import ResearchOrchestratorService as S
    service = S.__new__(S)            # conftest sets EVENT_INTELLIGENCE_VARIANT_VIEW=0
    items = [cand("no number here", days=None)]
    assert service._variant_select(items, {"consensus": "c"}, {}) == items
    assert service._consensus_view(["x"], []) == {}


def test_run_reports_whether_the_objective_was_met():
    from services import run_metrics
    near = (datetime.now(timezone.utc) + timedelta(days=40)).isoformat()
    original = {"insight_revision_id": "R1", "text": "OpenAI faces $2,500 per violation.",
                "par_check": {"passed": True}, "leverage_analysis": {"holder": "OpenAI"},
                "forecast_contract": {"question": "Will OpenAI settle?", "deadline": near}}
    background = {"insight_revision_id": "R2", "text": "x", "source_backed": {"reasons": ["conclusion_restates_sources"]}}
    m = run_metrics.compute(state={}, report={"hidden_insights": [original, background]})
    assert m["objective"] == {"original_insights": 1, "at_editors_bar": 1, "at_bar_with_call_within_12_weeks": 1,
                              "background_findings": 1, "met": True, "rubric_passed": "1/1",
                              "new_vs_consensus": "not judged", "win_probability_vs_approved_example": None}
    assert "MET: 1 original insight(s)" in run_metrics.summary_lines(m)[0] and "estimate" in run_metrics.summary_lines(m)[0]
    none = run_metrics.compute(state={}, report={"hidden_insights": [background]})
    assert none["objective"]["met"] is False and "NOT MET" in run_metrics.summary_lines(none)[0]
