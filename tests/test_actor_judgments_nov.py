"""actor-strategy v5 (8 Nov 2026): a recommended move is a judgment - kept and labelled when its
premises are partly backed, removed when unsupported or contradicted (ICD 203). The 25 Oct run
removed 13 items, including every move for Instinct, for "not supported"."""
from __future__ import annotations

from services import actor_moves as moves
from tests.test_v6_28_actor_strategy_method import DRAFT, FACTS


def _run(status_for):
    actors = moves.parse(DRAFT, FACTS)
    checks = moves.claims(actors, FACTS)
    statuses = {}
    for c in checks:
        t = c["claim_text"]
        if t.startswith((moves.ALREADY_PREFIX, moves.SWAP_PREFIX)):
            statuses[c["claim_id"]] = "unsupported"
        else:
            statuses[c["claim_id"]] = status_for(c)
    return moves.sanitize(actors, checks, statuses, FACTS)


def test_partly_backed_moves_are_kept_and_labelled_as_judgment():
    out = _run(lambda c: "supported" if c["epistemic_type"] == "fact" else "partially_supported")
    a = out["actors"][0]
    assert a["choice"]["move"].startswith("Offer customers")
    assert a["choice"]["backing"].startswith("judgment") and a["choice"]["basis"] == "incentive"
    assert a["moves"][0]["backing"].startswith("judgment")
    assert a["powers"][0]["leverage_test"] != "passed"     # partly backed leverage never passes the test


def test_unsupported_or_contradicted_moves_are_still_removed():
    for status in ("unsupported", "contradicted"):
        out = _run(lambda c: "supported" if c["epistemic_type"] == "fact" else status)
        assert out["actors"] == []
        assert any("choice not supported" in r for r in out["removed"])


def test_inferred_incentive_is_kept_labelled_partial_rule_is_removed():
    def status(c):
        if c["claim_text"].startswith("The EU can fine"):
            return "inference_supported"
        return "supported" if c["epistemic_type"] == "fact" else "inference_supported"
    a = _run(status)["actors"][0]
    assert a["rules_and_incentives"][0]["backing"] == "follows from the cited facts"

    def partial(c):
        if c["claim_text"].startswith("The EU can fine"):
            return "partially_supported"
        return "supported" if c["epistemic_type"] == "fact" else "inference_supported"
    a = _run(partial)["actors"][0]
    assert a["rules_and_incentives"] == []


def test_dashboard_labels_judgments():
    from frontend import dashboard
    out = _run(lambda c: "supported" if c["epistemic_type"] == "fact" else "partially_supported")
    text = "\n".join(dashboard.actor_moves_markdown(out))
    assert "[judgment - its premises are partly backed by the cited facts]" in text
