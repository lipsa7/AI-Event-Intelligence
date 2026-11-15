"""Contract test (14 Nov 2026): every check that reads a checker verdict must agree with
services/claim_status.py for EVERY verdict. If a verdict is added or its meaning changes, this
fails instead of a later paid run. Basis: MAST (Cemri et al. 2025) - inter-stage misalignment."""
import pytest

from services import claim_status as cs
from services import claim_support_gate as gate
from services import hypothesis_tier, non_obviousness

SOURCES = {"EVID-A": "S1", "EVID-B": "S2"}
FACT = {"claim_id": "R-P1", "epistemic_type": "fact", "grounding_status": "supported",
        "claim_text": "The accord has no penalties.", "supporting_evidence_ids": ["EVID-A", "EVID-B"],
        "unsupported_parts": []}


def claim(cid, kind, status, ids=("EVID-A",)):
    return {"claim_id": cid, "epistemic_type": kind, "grounding_status": status, "claim_text": "X will do Y.",
            "supporting_evidence_ids": list(ids), "unsupported_parts": []}


def grounding(*claims):
    return {"review_status": "complete", "claims": list(claims)}


@pytest.fixture(autouse=True)
def two_lane(monkeypatch):
    monkeypatch.delenv("EVENT_INTELLIGENCE_GATE_MODE", raising=False)


def test_the_lanes_cover_every_verdict_exactly_once():
    assert set(cs.ALL) == cs.FACT_PASSES | cs.FACT_PASSES_LABELLED | cs.FACT_BLOCKS
    assert not (cs.FACT_PASSES & cs.FACT_BLOCKS) and not (cs.FACT_PASSES_LABELLED & cs.FACT_BLOCKS)
    assert set(cs.ALL) == cs.JUDGMENT_RESTATES_SOURCES | cs.JUDGMENT_INFERRED | cs.JUDGMENT_BLOCKS
    assert not (cs.JUDGMENT_INFERRED & cs.JUDGMENT_BLOCKS) and not (cs.JUDGMENT_INFERRED & cs.JUDGMENT_RESTATES_SOURCES)


@pytest.mark.parametrize("status", cs.ALL)
def test_gate_fact_lane(status):
    verdict = gate.evaluate(grounding(FACT, claim("R-P2", "fact", status),
                                      claim("R-INSIGHT-01", "hidden_insight", "inference_supported")), SOURCES)
    assert verdict["passed"] == (status not in cs.FACT_BLOCKS), verdict["reasons"]


@pytest.mark.parametrize("status", cs.ALL)
def test_gate_judgment_lane(status):
    for kind in ("hidden_insight", "mechanism"):
        rows = [FACT, claim("R-X-01", kind, status)]
        if kind != "hidden_insight":
            rows.append(claim("R-INSIGHT-01", "hidden_insight", "inference_supported"))
        verdict = gate.evaluate(grounding(*rows), SOURCES)
        assert verdict["passed"] == (status not in cs.JUDGMENT_BLOCKS), (kind, verdict["reasons"])


@pytest.mark.parametrize("status", cs.ALL)
def test_restatement_and_inference_checks_agree(status):
    g = grounding(claim("R-INSIGHT-01", "hidden_insight", status))
    assert gate.restates_sources(g) == (status in cs.JUDGMENT_RESTATES_SOURCES)
    assert bool(non_obviousness.inferred_conclusions(g)) == (status in cs.JUDGMENT_INFERRED)
    # a conclusion is never both a restatement and an inference
    assert not (gate.restates_sources(g) and non_obviousness.inferred_conclusions(g))


@pytest.mark.parametrize("status", cs.ALL)
def test_explanatory_fallback_shows_only_backed_facts(status):
    facts = gate.verified_facts(grounding(claim("R-P2", "fact", status)))
    assert bool(facts) == (status in cs.FACT_PASSES)


@pytest.mark.parametrize("status", cs.ALL)
def test_hypotheses_to_investigate_follow_the_judgment_lane(status):
    item = {"id": "H1", "grounding": {"thesis": status, "mechanism": "partially_supported"},
            "supporting_evidence": [{"evidence_id": "EVID-A", "text": "The accord has no penalties."}],
            "grounded_claims": [{"claim_id": "LAB-H1-THESIS", "claim_text": "t", "grounding_status": status}]}
    assert hypothesis_tier.qualifies(item) == (status not in cs.JUDGMENT_BLOCKS)
    assert not hypothesis_tier.qualifies({**item, "supporting_evidence": []})   # always needs a sourced premise


def test_unsupported_judgment_is_span_tested_on_the_records_it_rests_on():
    """14 Nov 2026: the checker returns no supporting records for a judgment the sources do not
    state; the span test must use its citations or the candidate's backed premises, not nothing."""
    import json
    events = [{"event_id": "EVENT-1", "underlying_event_id": "U-A", "title": "Acme signs chip deal"},
              {"event_id": "EVENT-3", "underlying_event_id": "U-B", "title": "Export limits added"}]
    evidence = [{"evidence_id": "EV-1", "event_id": "EVENT-1", "source_id": "S1", "text": "a"},
                {"evidence_id": "EV-3", "event_id": "EVENT-3", "source_id": "S3", "text": "c"}]

    def ground(claims, records):
        return {"claims": [{"grounding_status": "unsupported"} for _ in claims]}

    def ask(prompt):
        if "ONE plain sentence" in prompt:
            return json.dumps({"plain": prompt.split("Claim: ", 1)[1].split("\n\n", 1)[0]})
        return {"takeaways": ["Acme secured chip supply.", "Export limits were added."]}

    conclusion = {"claim_id": "C1", "epistemic_type": "hidden_insight", "grounding_status": "unsupported",
                  "claim_text": "Acme will use the deal to take share the limits free up.", "supporting_evidence_ids": []}
    premises = [{"claim_id": "P1", "epistemic_type": "fact", "grounding_status": "supported", "claim_text": "f1",
                 "supporting_evidence_ids": ["EV-1"]},
                {"claim_id": "P2", "epistemic_type": "fact", "grounding_status": "supported", "claim_text": "f2",
                 "supporting_evidence_ids": ["EV-3"]}]
    from_premises = non_obviousness.check(grounding=grounding(conclusion, *premises), evidence=evidence,
                                          events=events, ground=ground, ask=ask)
    assert from_premises["passed"], from_premises
    cited = dict(conclusion, cited_evidence_ids=["EV-1", "EV-3"])
    assert non_obviousness.check(grounding=grounding(cited), evidence=evidence, events=events,
                                 ground=ground, ask=ask)["passed"]
    one_story = non_obviousness.check(grounding=grounding(conclusion, premises[0]), evidence=evidence,
                                      events=events, ground=ground, ask=ask)
    assert one_story["reasons"] == ["conclusion_single_development"]     # still needs two developments


@pytest.mark.parametrize("status", cs.ALL)
def test_whole_release_loop_follows_the_contract(status):
    """End to end through the orchestrator's release loop (newness test stubbed to pass): a
    conclusion is released as an insight when inferred, shown as source-backed when it only
    restates its sources, and blocked only when contradicted."""
    from tests.test_claim_support_gate import make_orchestrator, release, candidate, grounding as g, claim as c
    obj = make_orchestrator(verdicts={"INSIGHTREV-ONE": lambda: g(
        c("P1", "fact", "supported"), c("M1", "mechanism", "partially_supported"), c("I1", "hidden_insight", status))})
    (released, _, _), state = release(obj, [candidate("ONE")])
    if status in cs.JUDGMENT_BLOCKS:
        assert released == []
    else:
        assert [r["finding_tier"] for r in released] == ["hidden_insight"]
        backed = bool((released[0].get("metadata") or {}).get("source_backed"))
        assert backed == (status in cs.JUDGMENT_RESTATES_SOURCES)
    if status == cs.UNSUPPORTED:
        labels = [k.get("label") for k in released[0]["metadata"].get("key_assumptions") or []]
        assert "judgment - not stated by the sources" in labels
