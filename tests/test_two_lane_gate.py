"""claim-support-gate v4.0 (12 Nov 2026): facts are checked against sources; judgments
(conclusions, mechanism, leverage, incentives, forecasts) are blocked only when contradicted.
Basis: ALCE / FActScore (verify factual statements), ICD 203 (separate information from
judgments), ICD 206 and the Admiralty code (describe, don't block, single-source facts)."""
from services import claim_support_gate as gate

SOURCES = {"EVID-A": "S1", "EVID-B": "S2"}


def g(*rows):
    return {"review_status": "complete", "claims": [
        {"claim_id": cid, "epistemic_type": kind, "grounding_status": status, "claim_text": text,
         "supporting_evidence_ids": ids, "unsupported_parts": []}
        for cid, kind, status, text, ids in rows]}


FACT = ("R-P1", "fact", "supported", "The accord has no penalties.", ["EVID-A", "EVID-B"])


def test_prediction_not_stated_by_any_source_is_released_labelled():
    v = gate.evaluate(g(FACT, ("R-INSIGHT-01", "hidden_insight", "unsupported",
                               "OpenAI will settle with the FTC within a year.", ["EVID-A"]),
                        ("R-MECHANISM-01", "mechanism", "partially_supported", "Step.", ["EVID-A"])), SOURCES)
    assert v["passed"], v["reasons"]
    labels = {i["claim_id"]: i["label"] for i in v["key_assumptions"]}
    assert labels["R-INSIGHT-01"] == "judgment - not stated by the sources"
    assert v["confidence"].startswith("high")


def test_fabricated_or_contradicted_fact_still_blocks():
    bad = ("R-P2", "fact", "unsupported", "OpenAI was fined $1bn.", ["EVID-A"])
    concl = ("R-INSIGHT-01", "hidden_insight", "inference_supported", "X.", ["EVID-A"])
    assert not gate.evaluate(g(FACT, bad, concl), SOURCES)["passed"]
    contra = gate.evaluate(g(FACT, concl[:2] + ("contradicted",) + concl[3:]), SOURCES)
    assert not contra["passed"] and not contra["repairable"]


def test_single_source_and_partial_facts_are_labelled_not_blocked():
    one = ("R-P1", "fact", "supported", "The FTC is probing OpenAI.", ["EVID-A"])
    part = ("R-P2", "fact", "partially_supported", "The probe covers images.", ["EVID-A"])
    v = gate.evaluate(g(one, part, ("R-INSIGHT-01", "hidden_insight", "inference_supported", "X.", ["EVID-A"])),
                      SOURCES)
    assert v["passed"] and v["confidence"].startswith("moderate")
    kinds = {i["kind"] for i in v["key_assumptions"]}
    assert {"single_source", "partial_fact"} <= kinds


def test_needs_a_supported_fact_and_a_conclusion():
    only_judgment = gate.evaluate(g(("R-INSIGHT-01", "hidden_insight", "unsupported", "X.", [])), SOURCES)
    assert "no_factual_premise" in only_judgment["reasons"]
    no_concl = gate.evaluate(g(FACT), SOURCES)
    assert "no_conclusion_claim_checked" in no_concl["reasons"]


def test_legacy_mode_is_still_available(monkeypatch):
    monkeypatch.setenv("EVENT_INTELLIGENCE_GATE_MODE", "legacy")
    v = gate.evaluate(g(FACT, ("R-INSIGHT-01", "hidden_insight", "unsupported", "X.", ["EVID-A"])), SOURCES)
    assert not v["passed"]


def test_fact_entailed_by_its_records_passes_labelled():
    entailed = ("R-P1", "fact", "inference_supported", "The accord is voluntary.", ["EVID-A", "EVID-B"])
    v = gate.evaluate(g(entailed, ("R-INSIGHT-01", "hidden_insight", "unsupported", "X will happen.", [])), SOURCES)
    assert v["passed"], v["reasons"]
    assert any(i["kind"] == "entailed_fact" for i in v["key_assumptions"])


def test_forward_judgment_is_not_filed_as_restating_sources(monkeypatch):
    """13 Nov 2026: a conclusion the sources do not state is a judgment, not a restatement."""
    from services import claim_support_gate as gate, non_obviousness
    monkeypatch.delenv("EVENT_INTELLIGENCE_GATE_MODE", raising=False)
    for status in ("unsupported", "partially_supported", "inference_supported"):
        grounding = {"review_status": "complete", "claims": [{"claim_id": "C1", "epistemic_type": "hidden_insight",
                                 "grounding_status": status, "claim_text": "X will likely do Y."}]}
        assert not gate.restates_sources(grounding)
        assert len(non_obviousness.inferred_conclusions(grounding)) == 1
    stated = {"review_status": "complete", "claims": [{"claim_id": "C1", "epistemic_type": "hidden_insight",
                          "grounding_status": "supported", "claim_text": "X did Y."}]}
    assert gate.restates_sources(stated)
    assert non_obviousness.inferred_conclusions(stated) == []
    monkeypatch.setenv("EVENT_INTELLIGENCE_GATE_MODE", "legacy")
    old = {"review_status": "complete", "claims": [{"claim_id": "C1", "epistemic_type": "hidden_insight",
                       "grounding_status": "unsupported", "claim_text": "X will likely do Y."}]}
    assert gate.restates_sources(old) and non_obviousness.inferred_conclusions(old) == []


def test_explanatory_fallback_shows_only_backed_facts_with_sources():
    """13 Nov 2026: with nothing released, the reader still gets what the sources establish."""
    from services import claim_support_gate as gate
    from frontend import dashboard
    grounding = {"review_status": "complete", "claims": [
        {"claim_id": "F1", "epistemic_type": "fact", "grounding_status": "supported",
         "claim_text": "A controls the export licence.", "supporting_evidence_ids": ["E1"]},
        {"claim_id": "F2", "epistemic_type": "fact", "grounding_status": "unsupported",
         "claim_text": "B has no alternative supplier.", "supporting_evidence_ids": ["E2"]},
        {"claim_id": "F3", "epistemic_type": "fact", "grounding_status": "inference_supported",
         "claim_text": "B buys most of its input from A.", "supporting_evidence_ids": ["E3"]},
        {"claim_id": "C1", "epistemic_type": "hidden_insight", "grounding_status": "unsupported",
         "claim_text": "A will likely restrict supply.", "supporting_evidence_ids": ["E1", "E3"]}]}
    facts = gate.verified_facts(grounding)
    assert [f["text"] for f in facts] == ["A controls the export licence.", "B buys most of its input from A."]
    report = {"analysis_metadata": {"claim_support_gate": {"explanatory_fallback": {
        "label": "Explanatory - what the cited sources establish.",
        "facts": [{**facts[0], "sources": [{"publisher": "Reuters", "url": "https://example.org/a"}]}, facts[1]]}}}}
    lines = dashboard.explanatory_fallback_lines(report)
    assert lines[0].startswith("**Explanatory") and len(lines) == 3
    assert "[Reuters](https://example.org/a)" in lines[1] and "restrict supply" not in " ".join(lines)
    assert dashboard.explanatory_fallback_lines({"analysis_metadata": {"claim_support_gate": {}}}) == []
