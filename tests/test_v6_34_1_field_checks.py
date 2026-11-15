"""v6.34.1 - fixes from the GPT review of v6.34:
1. 'Verified' covered only thesis and mechanism, but the dashboard also showed the leverage chain,
   likely move and countermove. Each is now checked on its own and shown with its own status.
2. A narrowed rewrite inherited the original's countermove, novelty note, watch signal and
   falsifier. Every displayed field now comes from the rewrite (or is left empty).
3. Supporting quotes now carry their source link, publisher and credibility rating.
4. The end-to-end test runs the REAL GroundingService (its deterministic checks: evidence IDs must
   be from the supplied set, statuses validated), with a word-overlap classifier standing in for
   the model. It shows an unsupported rewrite stays unverified. It cannot show how well the live
   model judges support - only a live run and the gold set measure that.
(The installer changes - edited tests are not overwritten; obsolete tests removed only when they
match the known versions, after a backup - are checked by installing the package, not here.)
"""

from __future__ import annotations

import json
import re

import services.research_orchestrator_service as ros
from services import strategic_hypothesis_lab as lab
from services.grounding_service import GroundingService
from tests.test_v6_34_narrow_and_show import BLOCKED, FACTS, NARROWED, _dashboard, _orchestrator, _run


def _overlap_classifier(prompt: str):
    """Stand-in for the model: 'supported' only if the claim's content words appear in the evidence."""
    claim = re.search(r"Claim:\n(.*?)\n\nMulti-evidence", prompt, re.S).group(1)
    start = prompt.index("Evidence records:\n") + len("Evidence records:\n")
    records, _ = json.JSONDecoder().raw_decode(prompt[start:])
    body = " ".join(json.dumps(r) for r in records).casefold()
    words = [w for w in re.findall(r"[a-z]{5,}", claim.casefold())]
    hits = [w for w in words if w in body]
    ok = words and len(hits) / len(words) >= 0.75
    ids = [r["evidence_id"] for r in records if any(w in json.dumps(r).casefold() for w in hits)][:2]
    return {"status": "supported" if ok else "unsupported", "supporting_evidence_ids": ids if ok else [],
            "contradicted_evidence_ids": [], "attribution_valid": True, "temporal_valid": True,
            "numeric_valid": True, "unsupported_parts": [] if ok else [f"not stated: {claim[:80]}"],
            "rationale": "word overlap"}


def _real_checker_run(narrow_reply):
    obj, asked = _orchestrator(narrow_reply)
    obj.grounding_service = GroundingService(support_classifier=_overlap_classifier)
    return _run(obj), asked


def test_real_checker_rejects_an_unsupported_rewrite():
    unsupported = dict(NARROWED, thesis="OpenAI secretly sold Australian health records to advertisers through its agent.",
                       mechanism="Agent access -> data resale -> advertising revenue")
    out, _ = _real_checker_run(unsupported)
    assert out["verified"] == [] and out["narrowing"]["still_unverified"] == 1
    attempt = out["promising_unverified"][0]["narrowed_attempt"]
    # 26 Oct 2026 typed mechanism steps: the starting step ("Agent access") is backed, its two links
    # are not, so the chain is partly backed rather than unsupported - and still rejected.
    assert attempt["grounding"] == {"thesis": "unsupported", "mechanism": "partially_supported"}


def test_real_checker_verifies_a_backed_rewrite_and_checks_each_displayed_field():
    backed = dict(NARROWED, thesis="Australia relied on OpenAI's own report to learn what its agent accessed, and "
                                   "reviewing the agent's activity logs costs OpenAI $500,000 a day.",
                  mechanism="OpenAI reviewing activity logs -> Australia relied on OpenAI's report",
                  likely_actor_move="Australia relied on OpenAI's report about what its agent accessed.",
                  likely_countermove="Regulators impose criminal penalties on every agent vendor worldwide.")
    out, _ = _real_checker_run(backed)
    assert [v["id"] for v in out["verified"]] == ["EVOLVED_H2-N"]
    checks = out["verified"][0]["field_checks"]
    assert checks["likely_actor_move"] == "supported"
    assert checks["likely_countermove"] == "unsupported"          # shown, but labelled as analysis only
    assert "leverage" in checks


def test_rewrite_does_not_inherit_the_originals_fields():
    original = dict(BLOCKED, likely_countermove="AI companies refuse access, citing trade secrets.",
                    why_not_obvious="Regulators assume they can always audit.", watch_signal="All-clear statements.",
                    falsifier="A human audit finishes on time.")
    out = lab.parse_narrow(dict(NARROWED, likely_countermove="", watch_signal="Log-access clauses in renewals.",
                                falsifier="Australia completes its own review."), original, FACTS)
    assert out["likely_countermove"] == "" and out["why_not_obvious"] == ""
    assert out["watch_signal"] == "Log-access clauses in renewals." and out["falsifier"] == "Australia completes its own review."
    still = dict(NARROWED, falsifier="It is physically impossible to audit otherwise.")
    assert lab.parse_narrow(still, original, FACTS) is None             # overstated anywhere: rejected


def test_quotes_carry_source_links_and_fields_show_their_own_status():
    obj = ros.ResearchOrchestratorService.__new__(ros.ResearchOrchestratorService)
    quote = obj._quote_with_source({"evidence_id": "E1", "source_url": "https://www.oaic.gov.au/x",
                                    "text": "Notify within 30 days."})
    assert quote["url"] == "https://www.oaic.gov.au/x" and quote["publisher"] == "oaic.gov.au"
    assert quote["credibility"] == "official source"
    dashboard, calls, saved = _dashboard()
    try:
        item = dict(BLOCKED, status="verified", supporting_evidence=[quote],
                    field_checks={"leverage": "supported", "likely_actor_move": "unsupported"})
        text = "\n".join(dashboard.hypothesis_lines(item, True))
        assert "(oaic.gov.au, official source)](https://www.oaic.gov.au/x)" in text.replace("[", "(", 1) or \
            "[oaic.gov.au, official source](https://www.oaic.gov.au/x)" in text
        assert "*(stated by the sources)*" in text
        assert "Likely move:** Regulators demand direct access to the logs. *(not backed by the cited sources - analysis only)*" in text
    finally:
        import sys
        for name, mod in saved.items():
            sys.modules.pop(name, None) if mod is None else sys.modules.__setitem__(name, mod)
