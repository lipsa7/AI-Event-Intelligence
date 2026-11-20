"""v10 reader standard (15 Nov 2026): the owner's verdicts as tests.

The owner kept one insight of about 90 (the 22 Oct breach-logs insight), called one "only okay",
one weak and rejected two. A candidate's own text must carry a concrete, checkable fact; this
separates her kept and okay items from the weak and rejected ones.
"""
import csv
import json
from pathlib import Path

from services import reader_standard as rs
from services import strategic_hypothesis_lab as lab

ROOT = Path(__file__).resolve().parents[1]
REVIEW = ROOT / "data" / "insight_review_90.csv"

# The owner's verdicts, by row number in data/insight_review_90.csv.
KEPT = "54"            # breach logs, 22 Oct
OKAY = ("41", "42")     # naming collision, 10 Oct
WEAK = "10"            # dual-use contradiction, 9 Oct
REJECTED = ("86", "84")  # liability shifts to deployers; philanthropy shaping standards


def _review_rows():
    with REVIEW.open(encoding="utf-8-sig") as handle:
        return {row["no"]: row["insight"] for row in csv.DictReader(handle)}


def test_owner_verdicts_are_separated():
    rows = _review_rows()
    assert rs.assess(rows[KEPT])["passes"]
    for number in OKAY:
        assert rs.assess(rows[number])["passes"]
    assert not rs.assess(rows[WEAK])["passes"]
    for number in REJECTED:
        assert not rs.assess(rows[number])["passes"]


def test_kept_insight_original_wording_is_flagged_as_theory_label():
    # She kept the idea but not the wording ("asymmetric telemetry bottleneck").
    result = rs.assess(_review_rows()[KEPT])
    assert not result["plain_wording"]
    assert {"asymmetric", "telemetry", "bottleneck"} <= set(result["theory_labels"])


def test_rewritten_breach_insight_passes_with_plain_wording():
    text = ("Governments learned of the AI-agent breach about three months after it happened. The model "
            "developer, not the government, detected it in August and reported it in September.")
    result = rs.assess(text)
    assert result["passes"] and result["plain_wording"] and result["score"] == 2


def test_anchor_kinds():
    assert rs.assess("68 companies signed a voluntary accord.")["passes"]
    assert rs.assess("The Ban Artificial Superintelligence Act would cover it.")["passes"]
    assert rs.assess("An executive order renamed AI.")["passes"]
    assert rs.assess("It was reported on 10 September.")["passes"]
    assert rs.assess("It raised $15 million.")["passes"]
    # Not concrete: model names, a lower-case generic document, vague durations.
    assert not rs.assess("GPT-4 and B2B agents are growing fast.")["passes"]
    assert not rs.assess("Firms signed a voluntary accord with executives.")["passes"]
    assert not rs.assess("A multi-month lag exposes a gap.")["passes"]


def test_standard_text_is_in_both_generation_prompts():
    facts = [{"fact_id": "F1", "text": "A fact.", "date": "2026-09-10", "source_id": "S1"}] * 1
    assert "WHAT THE READER VALUES" in lab.generation_prompt("ai", facts, ["contradiction"], batch=1)
    assert "WHAT THE READER VALUES" in lab.evolution_prompt("ai", [{"id": "B1H1", "thesis": "t"}], facts)
    from agents import strategic_inference_agent as agent
    assert "WHAT THE READER VALUES" in rs.READER_STANDARD
    assert agent.PROMPT_VERSION.endswith("reader-standard")


def _row(row_id, thesis, critic):
    return {
        "id": row_id, "lens": "x", "thesis": thesis, "fact_ids": ["F1", "F2"],
        "mechanism": "A -> B -> C", "novel_proposition": "n", "why_not_obvious": "w",
        "alternative_explanation": "a", "falsifier": "f", "watch_signal": "s",
        "critic_scores": {k: critic for k in ("mental_model_change", "mechanism_depth",
                                              "surprise_nonobviousness", "strategic_consequence",
                                              "falsifiability")},
    }


def test_lab_drops_unanchored_theses_and_puts_plain_ones_first():
    facts = [
        {"fact_id": "F1", "text": "The agent accessed the portal in June.", "date": "2026-09-10", "source_id": "S1"},
        {"fact_id": "F2", "text": "OpenAI reported the breach on 10 September.", "date": "2026-09-10", "source_id": "S2"},
    ]
    reply = json.dumps({"hypotheses": [
        _row("E1", "Developers retain exclusive diagnostic control over agent oversight.", 2),
        _row("E2", "The June access reveals an asymmetric telemetry bottleneck for regulators.", 2),
        _row("E3", "Governments learned of the breach three months after it happened, because only "
                   "OpenAI could see what its agent did.", 1),
    ]})
    stats = {}
    out = lab.parse_evolution(reply, facts, stats=stats)
    assert [item["id"] for item in out] == ["E3", "E2"]
    assert stats["reader_standard_no_concrete_fact"] == 1
    assert out[0]["reader_standard"]["plain_wording"]


def test_call_cap_holds_calls_for_the_lab():
    import pytest
    from services import gemini_llm_service as g

    g.set_call_budget(10)
    g.reserve_calls("hypothesis_lab", 4)
    with g.stage("hidden_insights"):
        for _ in range(6):
            g.consume_call()
        with pytest.raises(g.GeminiCallBudgetExceeded):
            g.consume_call()          # only the lab's 4 held calls remain
    with g.stage("hypothesis_lab"):
        for _ in range(4):
            g.consume_call()
        assert g.reserved_calls("hypothesis_lab") == 0
        with pytest.raises(g.GeminiCallBudgetExceeded):
            g.consume_call()          # the whole cap is now used
    g.set_call_budget(None)


def test_released_reservation_returns_calls_to_other_stages():
    from services import gemini_llm_service as g

    g.set_call_budget(5)
    g.reserve_calls("hypothesis_lab", 3)
    g.reserve_calls("hypothesis_lab", 0)
    with g.stage("judge"):
        for _ in range(5):
            g.consume_call()
    g.set_call_budget(None)


def test_deep_run_reserves_lab_calls(monkeypatch):
    from services import gemini_llm_service as g
    from services.research_orchestrator_service import ResearchOrchestratorService

    monkeypatch.setenv("EVENT_INTELLIGENCE_MAX_GEMINI_CALLS", "260")
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    service._apply_call_cap("deep")
    assert service._lab_reserved_calls == 60 and g.reserved_calls("hypothesis_lab") == 60
    service._apply_call_cap("fast")
    assert service._lab_reserved_calls == 0 and g.reserved_calls("hypothesis_lab") == 0
    monkeypatch.setenv("EVENT_INTELLIGENCE_MAX_GEMINI_CALLS", "100")
    service._apply_call_cap("deep")
    assert service._lab_reserved_calls == 30   # at most 30% of the cap
    g.set_call_budget(None)


def test_overreach_names_unsourced_words_and_numbers():
    cited = ["OpenAI discovered the breach in August and told Australia on September 10, 2026.",
             "The agent accessed the portal in June."]
    found = rs.overreach("OpenAI holds exclusive logs and forces governments to act under Executive Order "
                         "14434 within a 12-to-72-hour window.", cited)
    assert found == {"absolutes": ["exclusive", "forces"], "specifics": ["14434", "12", "72"]}
    # A gap worked out from two dated facts, model names and sourced numbers are all fine.
    assert rs.overreach("Australia learned of it about three months later, on September 10, 2026; "
                        "GPT-4 was not involved.", cited) == {"absolutes": [], "specifics": []}
    # A strong word is allowed when a cited record uses it.
    assert rs.overreach("Only OpenAI could see it.", ["Only OpenAI could see the logs."])["absolutes"] == []
    brief = " ".join(rs.overreach_brief(found))
    assert '"exclusive"' in brief and "14434" in brief and "narrow" in brief


def test_kept_insight_is_flagged_on_the_word_the_checker_rejected():
    # The 22 Oct checker failed the breach-logs insight on "exclusive" control.
    found = rs.overreach(_review_rows()[KEPT], ["OpenAI discovered the breach in August."])
    assert "exclusive" in found["absolutes"]


def test_orchestrator_overreach_uses_cited_records():
    from services.research_orchestrator_service import ResearchOrchestratorService

    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    evidence = [
        {"evidence_id": "EVID-A", "text": "Only the developer reported the breach on 10 September."},
        {"evidence_id": "EVID-B", "text": "A different story mentions 72 hours and exclusive deals."},
    ]
    candidate = {"insight": "Only the developer reported it, under an exclusive 72-hour rule.",
                 "supporting_evidence_ids": ["EVID-A"]}
    assert service._overreach_found(candidate, evidence) == {"absolutes": ["exclusive"], "specifics": ["72"]}
    clean = {"insight": "Only the developer reported it on 10 September.", "supporting_evidence_ids": ["EVID-A"]}
    assert service._overreach_found(clean, evidence) == {}


def test_lab_ranks_overreaching_thesis_below_a_plain_one():
    facts = [
        {"fact_id": "F1", "text": "The agent accessed the portal in June.", "date": "2026-09-10", "source_id": "S1"},
        {"fact_id": "F2", "text": "OpenAI reported the breach on 10 September.", "date": "2026-09-10", "source_id": "S2"},
    ]
    reply = json.dumps({"hypotheses": [
        _row("E1", "Governments learned of the breach in September because OpenAI held exclusive logs.", 2),
        _row("E2", "Governments learned of the breach in September, three months after the June access.", 1),
    ]})
    stats = {}
    out = lab.parse_evolution(reply, facts, stats=stats)
    assert [item["id"] for item in out] == ["E2", "E1"]
    assert out[1]["overreach"]["absolutes"] == ["exclusive"]
    assert stats["reader_standard_overreach"] == 1


def test_forecast_deadline_year_is_not_an_added_fact():
    assert rs.overreach("Forecast: Okta will add kill switches before 2027.", ["Okta announced agents."]) == {
        "absolutes": [], "specifics": []}
    assert rs.overreach("Under Executive Order 14434, agencies act by 2027.", ["An order."])["specifics"] == ["14434"]


def _claim(claim_id, kind, status):
    return {"claim_id": claim_id, "epistemic_type": kind, "grounding_status": status, "claim_text": claim_id,
            "supporting_evidence_ids": [] if status == "unsupported" else ["EVID-A", "EVID-B"],
            "contradicted_evidence_ids": [],
            "unsupported_parts": [] if status in {"supported", "inference_supported"} else [claim_id]}


def _grounding(*claims):
    from services import claim_support_gate as gate
    return {"review_status": "complete", "status": gate.aggregate(claims), "claims": list(claims)}


def test_only_unbacked_leverage_lines_are_dropped_and_the_insight_passes():
    # 14 Nov run: 14 of 15 claims backed; the one failure was "Donald Trump controls or holds a
    # voluntary accord", a line the app wrote from the leverage fields.
    from services import claim_support_gate as gate
    grounding = _grounding(
        _claim("REV-P1", "fact", "supported"),
        _claim("REV-M1", "mechanism", "inference_supported"),
        _claim("REV-INSIGHT-01", "hidden_insight", "inference_supported"),
        _claim("REV-CONTROL", "fact", "unsupported"),
    )
    verdict = gate.evaluate(grounding)
    assert not verdict["passed"]
    pruned = gate.without_unbacked_leverage(grounding, verdict)
    assert [c["claim_id"] for c in pruned["claims"]] == ["REV-P1", "REV-M1", "REV-INSIGHT-01"]
    assert gate.evaluate(pruned)["passed"]
    # The insight shown without leverage is not asked for leverage again.
    assert gate.leverage_reasons({"metadata": {"leverage_withheld": {"reason": "x"}}}) == []
    assert gate.leverage_reasons({})  # still required otherwise


def test_a_failed_source_premise_is_never_dropped():
    from services import claim_support_gate as gate
    grounding = _grounding(
        _claim("REV-P1", "fact", "unsupported"),
        _claim("REV-INSIGHT-01", "hidden_insight", "supported"),
        _claim("REV-CONTROL", "fact", "unsupported"),
    )
    assert gate.without_unbacked_leverage(grounding, gate.evaluate(grounding)) is None


def test_final_checks_and_lab_both_get_held_calls(monkeypatch):
    from services import gemini_llm_service as g
    from services.research_orchestrator_service import STAGE_PATHS, ResearchOrchestratorService

    assert STAGE_PATHS["_ground_report"] == STAGE_PATHS["_red_team"] == "final_checks"
    monkeypatch.setenv("EVENT_INTELLIGENCE_MAX_GEMINI_CALLS", "260")
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    service._apply_call_cap("deep")
    assert service._final_check_reserved_calls == 40 and g.reserved_calls("final_checks") == 40
    assert g.reserved_calls("hypothesis_lab") == 60
    with g.stage("hidden_insights"):
        for _ in range(160):
            g.consume_call()
        try:
            g.consume_call()
            raise AssertionError("main path used calls held for the lab and the final checks")
        except g.GeminiCallBudgetExceeded:
            pass
    with g.stage("final_checks"):
        for _ in range(40):
            g.consume_call()
    g.set_call_budget(None)


def _checker(status, ids, prompts=None):
    from services.grounding_service import GroundingService

    def classifier(prompt):
        if prompts is not None:
            prompts.append(prompt)
        return {"status": status, "supporting_evidence_ids": ids, "contradicted_evidence_ids": [],
                "attribution_valid": True, "temporal_valid": True, "numeric_valid": True,
                "unsupported_parts": [], "rationale": "x"}
    return GroundingService(support_classifier=classifier)


CHECK_EVIDENCE = [{"evidence_id": "EVID-1", "text": "OpenAI reported the breach on 10 September.", "source_id": "S1"},
                  {"evidence_id": "EVID-2", "text": "Australia set up a taskforce.", "source_id": "S2"}]


def test_checker_judges_forecasts_on_premises_not_on_being_stated():
    prompts = []
    service = _checker("inference_supported", ["EVID-1", "EVID-2"], prompts)
    service.ground_claims(claims=[{"claim_id": "F-1", "epistemic_type": "hidden_insight",
                                   "claim_text": "Australia will set a fixed reporting deadline.",
                                   "evidence_ids": ["EVID-1", "EVID-2"]}], evidence_records=CHECK_EVIDENCE)
    assert "Forecasts and predicted moves" in prompts[0]
    assert "never by itself a reason to" in prompts[0]


def test_one_record_does_not_downgrade_a_judgment_but_does_downgrade_a_fact():
    service = _checker("inference_supported", ["EVID-1"])
    judged = service.ground_claims(claims=[{
        "claim_id": "J-1", "epistemic_type": "hidden_insight", "multi_evidence_required": True,
        "claim_text": "Officials learn of agent incidents on the developer's timetable.",
        "evidence_ids": ["EVID-1", "EVID-2"]}], evidence_records=CHECK_EVIDENCE)
    assert judged["claims"][0]["grounding_status"] == "inference_supported"
    fact_service = _checker("supported", ["EVID-1"])
    fact = fact_service.ground_claims(claims=[{
        "claim_id": "P-1", "epistemic_type": "fact", "multi_evidence_required": True,
        "claim_text": "OpenAI reported the breach on 10 September.",
        "evidence_ids": ["EVID-1", "EVID-2"]}], evidence_records=CHECK_EVIDENCE)
    assert fact["claims"][0]["grounding_status"] == "partially_supported"


# ---- v10.3 -----------------------------------------------------------------------------

def _evolved(row_id, thesis, fact_ids, strength="follows", rival="no", link="none"):
    row = _row(row_id, thesis, 2)
    row.update({"fact_ids": fact_ids, "inference_strength": strength, "rival_fits_as_well": rival,
                "link_fact_id": link})
    return row


DEV_FACTS = [
    {"fact_id": "F1", "development": "Accord", "text": "Six companies signed a voluntary accord in October.",
     "date": "2026-10-05", "source_id": "S1"},
    {"fact_id": "F2", "development": "Accord", "text": "The accord names no verifier.", "date": "2026-10-05",
     "source_id": "S2"},
    {"fact_id": "F3", "development": "Funding", "text": "Instinct raised $1 billion in October.",
     "date": "2026-10-02", "source_id": "S3"},
]
# v12: developments carry their named actors; the shared-investor case shares one.
DEV_FACTS[0]["actors"] = DEV_FACTS[1]["actors"] = ["OpenAI"]
DEV_FACTS[2]["actors"] = ["Instinct"]
DEV_FACTS.append({"fact_id": "F4", "development": "Funding", "actors": ["Instinct"], "date": "2026-10-02",
                  "source_id": "S4", "text": "An OpenAI fund led Instinct's October round."})


def test_lab_drops_restatements_speculation_and_unlinked_stitching():
    reply = json.dumps({"hypotheses": [
        _evolved("E1", "Six companies signed an accord in October that names no verifier.", ["F1", "F2"], "stated"),
        _evolved("E2", "The October accord shows firms plan to evade rules.", ["F1", "F2"], "speculative"),
        _evolved("E3", "The October accord enabled Instinct's $1 billion raise.", ["F1", "F3"]),
        _evolved("E4", "The October accord names no verifier, so compliance cannot be checked.", ["F1", "F2"]),
    ]})
    stats = {}
    out = lab.parse_evolution(reply, DEV_FACTS, stats=stats)
    assert [item["id"] for item in out] == ["E4"]
    assert stats["evolution_restates_a_fact"] == 1
    assert stats["evolution_speculative"] == 1
    assert stats["evolution_stitched_without_link"] == 1
    assert out[0]["strength_label"] == "follows from the cited facts"


def test_lab_keeps_linked_developments_and_labels_a_rival_that_fits():
    reply = json.dumps({"hypotheses": [
        _evolved("E1", "The October accord and the $1 billion raise share one investor.", ["F1", "F4"], link="F4"),
        _evolved("E2", "The October accord names no verifier, so compliance cannot be checked.", ["F1", "F2"],
                 rival="yes"),
    ]})
    out = lab.parse_evolution(reply, DEV_FACTS, stats={})
    labels = {item["id"]: item["strength_label"] for item in out}
    assert labels["E2"].startswith("possible")
    assert "E1" in labels
    assert [item["id"] for item in out][0] == "E1"     # the rival-fits one ranks lower


def test_concrete_facts_step_parses_and_feeds_generation():
    reply = json.dumps({"concrete_facts": [
        {"fact_ids": ["F1"], "finding": "Six companies signed in October, but the accord names no verifier.",
         "expected": "an accord would name who checks it", "surprise": 3},
        {"fact_ids": ["F9"], "finding": "Unknown fact id.", "surprise": 2},
        {"fact_ids": ["F3"], "finding": "A startup raised money.", "surprise": 1},
    ]})
    stats = {}
    found = lab.parse_concrete_facts(reply, DEV_FACTS, stats=stats)
    assert [f["fact_ids"] for f in found] == [["F1"]]          # unknown id and no concrete fact dropped
    assert stats["concrete_facts_dropped"] == 2
    prompt = lab.generation_prompt("ai", DEV_FACTS, ["contradiction"], batch=1, concrete_facts=found)
    assert "START FROM THESE CONCRETE FACTS" in prompt and "names no verifier" in prompt
    assert "concrete facts" in lab.concrete_facts_prompt("ai", DEV_FACTS)
    assert "inference_strength" in lab.evolution_prompt("ai", [{"id": "B1H1", "thesis": "t"}], DEV_FACTS)


def test_developments_combine_only_when_they_share_a_named_actor():
    from services.research_orchestrator_service import ResearchOrchestratorService

    def event(eid, cluster, actors, n):
        return {"event_id": eid, "strategic_cluster_id": cluster, "strategic_cluster_status": "verified",
                "underlying_event_id": eid, "actors": actors,
                "title": cluster}
    events = [event("E1", "accord", ["OpenAI", "White House"], 1), event("E2", "accord", ["Anthropic"], 1),
              event("E3", "funding", ["Instinct"], 1), event("E4", "breach", ["OpenAI", "Australia"], 1),
              event("E5", "breach", ["OpenAI"], 1)]
    evidence = [{"evidence_id": f"EV{i}", "event_id": f"E{i}"} for i in range(1, 6)]
    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    combos = service._development_combinations(events, evidence, limit=5)
    keys = [tuple(c["keys"]) for c in combos]
    assert ("accord", "breach") in keys                       # share OpenAI
    assert not any("funding" in k and len(k) > 1 for k in keys)   # Instinct shares no actor
    assert "('accord', 'funding')" not in str(keys)


def test_display_check_at_call_limit_labels_instead_of_deleting():
    from services.gemini_llm_service import GeminiCallBudgetExceeded
    from services.research_orchestrator_service import ResearchOrchestratorService

    class Refusing:
        def ground_claims(self, **_):
            raise GeminiCallBudgetExceeded("Gemini call ceiling of 260 reached; call not made.")

    service = ResearchOrchestratorService.__new__(ResearchOrchestratorService)
    service.grounding_service = Refusing()
    candidate = {"insight_revision_id": "REV", "insight": "x", "supporting_evidence_ids": ["EVID-A"],
                 "metadata": {"novel_proposition": "Officials learn of agent incidents months late."}}
    out, _, notes = service._ground_display(candidate, [{"evidence_id": "EVID-A", "text": "t"}], [])
    assert out["metadata"]["novel_proposition"] == "Officials learn of agent incidents months late."
    assert out["metadata"]["display_check"]["status"] == "not_checked_call_limit"
    assert notes["not_checked_call_limit"] is True


def test_deep_runs_ask_for_two_candidates():
    import inspect
    from agents import strategic_inference_agent as agent
    source = inspect.getsource(agent)
    assert "rest on a DIFFERENT concrete fact" in source
    assert 'if reasoning_guidance.get("desired_depth") not in {"deep", "very_deep"} else 2' in source
