"""Typed mechanism steps and the "analysis" status (26 Oct 2026). No model calls.

A chain "A -> B -> C" used to be graded as one claim, so one unsourced background step failed a
good hypothesis (EV3, RUN-3BF777). Now the starting fact must be stated by a source, each link
only has to follow, and exactly one unbacked link makes the item "analysis" with that link shown
as its stated assumption. Contradictions and two or more unbacked links still fail.
"""
from services import hypothesis_tier, inference_summary, insight_funnel
from services import strategic_hypothesis_lab as lab

ITEM = {"id": "EV3", "thesis": "The vendor, not the victim, controls when a breach is discovered.",
        "mechanism": "An OpenAI agent accessed a government site -> Breaches are usually found by victims "
                     "-> The victim learned months later from the vendor",
        "fact_ids": ["F7"], "supporting_evidence": [{"evidence_id": "E1", "text": "It took three months to alert."}]}


def grounded(start="supported", l2="unsupported", l3="inference_supported", thesis="inference_supported"):
    claims = lab.grounding_claims(ITEM, ["E1", "E2"])
    status = {"THESIS": thesis, "S1": start, "L2": l2, "L3": l3}
    return [dict(c, grounding_status=status.get(c["claim_id"].rsplit("-", 1)[-1], "supported")) for c in claims]


def test_mechanism_is_split_into_a_starting_fact_and_links():
    claims = {c["claim_id"]: c for c in lab.grounding_claims(ITEM, ["E1", "E2"])}
    assert claims["LAB-EV3-MECH-S1"]["epistemic_type"] == "fact"
    assert claims["LAB-EV3-MECH-L2"]["epistemic_type"] == "mechanism"
    assert claims["LAB-EV3-MECH-L2"]["claim_text"].startswith("Given that An OpenAI agent accessed")
    assert "LAB-EV3-MECH" not in claims
    single = lab.grounding_claims(dict(ITEM, mechanism="no arrows here"), ["E1"])
    assert any(c["claim_id"] == "LAB-EV3-MECH" for c in single)          # unchanged when not a chain


def test_one_unbacked_link_makes_analysis_with_that_link_as_assumption():
    final = lab.finalize(ITEM, grounded(), [])
    assert final["status"] == "analysis" and final["grounding"]["mechanism"] == lab.ASSUMPTION_BRIDGED
    assert final["key_assumptions"] == ["Given that An OpenAI agent accessed a government site, "
                                        "Breaches are usually found by victims"]
    assert not lab.needs_narrowing(final)


def test_every_link_backed_is_verified():
    final = lab.finalize(ITEM, grounded(l2="inference_supported"), [])
    assert final["status"] == "verified" and final["key_assumptions"] == []


def test_still_rejected_when_it_should_be():
    assert lab.finalize(ITEM, grounded(l3="unsupported"), [])["status"] == "promising_unverified"   # two links
    assert lab.finalize(ITEM, grounded(start="partially_supported"), [])["status"] == "promising_unverified"
    assert lab.finalize(ITEM, grounded(l3="contradicted"), [])["grounding"]["mechanism"] == "contradicted"
    assert lab.finalize(ITEM, grounded(thesis="partially_supported"), [])["status"] == "promising_unverified"


def test_legacy_single_mechanism_claim_grades_as_before():
    rows = [{"claim_id": "LAB-X-THESIS", "grounding_status": "supported"},
            {"claim_id": "LAB-X-MECH", "grounding_status": "inference_supported"}]
    assert lab.finalize({"id": "X"}, rows, [])["status"] == "verified"


def test_analysis_is_shown_labelled_first_and_counted_as_passed():
    final = dict(lab.finalize(ITEM, grounded(), []), grounded_claims=grounded())
    other = dict(final, id="EV9", status="promising_unverified", key_assumptions=[])
    sections = {"strategic_hypotheses": {"verified": [], "promising_unverified": [other, final]}}
    assert [h["id"] for h in hypothesis_tier.select(sections)] == ["EV3", "EV9"]
    assert "Breaches are usually found by victims" in hypothesis_tier.assumptions(final)[0]
    assert not any("Breaches" in p for p in hypothesis_tier.open_points(final))   # a link is not a fact
    lead = inference_summary.lead(sections)
    assert lead["kind"] == "analysis" and "stated assumption" in lead["label"]
    inv = inference_summary.inventory(sections)
    assert inv["counts"]["analysis"] == 1 and inv["counts"]["hypothesis"] == 1     # not counted twice
    rows = insight_funnel.build(sections)["stages"]["hypothesis_lab"]["candidates"]
    assert ("EV3", "passed") in [(r["id"], r["outcome"]) for r in rows]
