"""11 Nov 2026 (Claude-proxy replay): an evolution reply without critic_scores dropped every
hypothesis as 'low critic'. Unscored rows now continue, flagged, to the lab's own checks."""
import json

from services import strategic_hypothesis_lab as lab

FACTS = [{"fact_id": "F1", "evidence_id": "EVID-1", "text": "The accord has no penalties."},
         {"fact_id": "F2", "evidence_id": "EVID-2", "text": "An order directs a federal disclosure standard."}]


def _row(**extra):
    row = {"id": "E1", "thesis": "The accord may become the federal baseline.", "novel_proposition": "x",
           "why_not_obvious": "y", "mechanism": "A (F1) -> B (F2)", "fact_ids": ["F1", "F2"],
           "falsifier": "z", "watch_signal": "w", "lens": "incentive",
           "alternative_explanation": "The accord may simply be symbolic."}
    row.update(extra)
    return row


def test_unscored_and_low_scored_rows_are_kept_and_flagged():
    stats = {}
    low = {k: 1 for k in ("mental_model_change", "mechanism_depth", "surprise_nonobviousness",
                          "strategic_consequence", "falsifiability")}
    reply = json.dumps({"hypotheses": [_row(), _row(id="E2", critic_scores=low, thesis="State preemption will push disclosure duties onto federal procurement terms.")]})
    out = lab.parse_evolution(reply, FACTS, stats=stats)
    assert [x["critic_total"] for x in out] == [None, 5]
    assert stats["evolution_unscored"] == 1 and stats["evolution_low_critic_kept"] == 1
    assert lab.critic_rank(out[0]) == 7
