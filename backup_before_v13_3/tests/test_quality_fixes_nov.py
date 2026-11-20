"""Nov 2026 quality fixes (no model calls).

1. Narrowing trades informativeness for checkability (Jiang et al., "Conformal Linguistic
   Calibration": generalising claims raises factual precision while losing specificity). On the
   25 Oct run the lab's "the Accord works as a liability shield" was narrowed to "the Accord asks for
   internal audits without penalties" - a restatement of the news. The bolder original now leads
   "Hypotheses to investigate", with the parts the sources do not back, and the narrowed version
   no longer calls it "overstated".
2. The grounding checker gets its fixed instructions first, then the evidence, then the one claim:
   Google's guidance for Gemini is to put the question after the context, and a fixed start is what
   Gemini's implicit caching discounts."""

from __future__ import annotations

import inspect

import services.grounding_service as g
from services import hypothesis_tier as tier


def _hyp(id_, narrowing="", quotes=1):
    return {"id": id_, "thesis": f"thesis {id_}", "narrowing": narrowing,
            "grounding": {"thesis": "partially_supported"},
            "supporting_evidence": [{"evidence_id": f"E{id_}{n}", "text": f"quote {id_} {n}"} for n in range(quotes)]}


def test_bolder_original_leads_the_hypotheses_to_investigate():
    sections = {"strategic_hypotheses": {"promising_unverified": [
        _hyp("A", quotes=3), _hyp("B", narrowing="narrowed_version_verified:B-N", quotes=1)]}}
    picked = [h["id"] for h in tier.select(sections)]
    assert picked[0] == "B" and tier.bolder_original(_hyp("B", "narrowed_version_verified:B-N"))
    rated = [h["id"] for h in tier.select(sections, {"lab:A": "useful"})]
    assert rated[0] == "A"                                   # your rating still comes first


def test_grounding_prompt_puts_the_claim_after_the_evidence():
    cls = next(c for c in vars(g).values() if isinstance(c, type) and "_build_prompt" in c.__dict__)
    prompt = cls.__new__(cls)._build_prompt(claim_id="C1", claim_text="CLAIM TEXT", epistemic_type="fact",
                                            evidence_records=[{"evidence_id": "E1", "text": "EVIDENCE TEXT"}],
                                            multi_evidence_required=False)
    assert prompt.index("Validation rules") < prompt.index("EVIDENCE TEXT") < prompt.index("CLAIM TEXT")
    assert prompt.index("Return exactly one JSON object") < prompt.index("EVIDENCE TEXT")
    other = cls.__new__(cls)._build_prompt(claim_id="C2", claim_text="OTHER", epistemic_type="connection",
                                           evidence_records=[{"evidence_id": "E1", "text": "EVIDENCE TEXT"}],
                                           multi_evidence_required=True)
    shared = prompt.split("Based only on the evidence records above")[0]
    assert other.startswith(shared)                          # the same evidence gives the same prompt start
    assert g.GROUNDING_PROMPT_VERSION == "grounding-v1.3-context-first"


def test_narrowed_version_does_not_call_the_original_overstated():
    import frontend.dashboard as dash   # noqa: F401  (source check only)
    source = inspect.getsource(dash.hypothesis_lines) if hasattr(dash, "hypothesis_lines") else ""
    assert "which was overstated" not in source and "The bolder first version" in source
