"""v6.21.6: GPT's two reproduced v6.21.5 defects.

1. Exploration skipped untried combinations: after candidates on A+B and B+C,
   A+C was skipped; one A+B+C candidate blocked every pair. Only the exact
   combinations already used are skipped now.
2. The "already reported?" test ran only on records sharing two words with
   the implication; a paraphrase ("may shorten breach disclosure times" vs
   "now notifies regulators within 48 hours") was never checked. The
   implication's own cited records are always included now.
"""

from __future__ import annotations

from services import editorial_briefing as brief
from tests.test_claim_support_gate import make_orchestrator
from tests.test_v6_21_4_search_coverage import _events_and_evidence


def _uses(*clusters):
    return {"supporting_evidence_ids": [f"EVID-{c.upper()}-0" for c in clusters]}


def test_pairs_inside_used_developments_are_still_tried():
    obj = make_orchestrator(verdicts={})
    events, evidence = _events_and_evidence()
    after = obj._development_combinations(events, evidence, [_uses("dev_eu", "dev_breach"),
                                                            _uses("dev_breach", "dev_rename")], limit=6)
    keys = [c["keys"] for c in after]
    assert ["dev_eu", "dev_rename"] in keys
    assert ["dev_breach", "dev_eu"] not in keys and ["dev_breach", "dev_rename"] not in keys


def test_a_triple_candidate_does_not_block_its_pairs():
    obj = make_orchestrator(verdicts={})
    events, evidence = _events_and_evidence()
    after = obj._development_combinations(events, evidence, [_uses("dev_eu", "dev_breach", "dev_rename")], limit=6)
    keys = [c["keys"] for c in after]
    assert ["dev_breach", "dev_eu", "dev_rename"] not in keys
    assert ["dev_breach", "dev_eu"] in keys


def test_old_news_check_always_includes_the_cited_records():
    implication = {"text": "OpenAI may shorten breach disclosure times.",
                   "citations": [{"evidence_id": "EVID-NOTIFY"}]}
    evidence = [{"evidence_id": "EVID-NOTIFY", "text": "The company now notifies regulators within 48 hours."}]
    claims = brief.novelty_claims([implication], evidence, has_expected_view=False)
    reported = [c for c in claims if c["_test"] == "reported"]
    assert reported and reported[0]["evidence_ids"] == ["EVID-NOTIFY"]
