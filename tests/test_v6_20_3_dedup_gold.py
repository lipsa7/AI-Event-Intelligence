"""v6.20.3: the duplicate-story resolver measured on hand-labelled pairs of this
app's own extracted events (tests/data/dedup_gold_pairs.json).

Pairwise precision = of the pairs the resolver merged, how many are truly the
same occurrence; recall = of the true same-occurrence pairs, how many it merged.
Earlier resolvers on the same set: v1 (v6.20.1) 0.38 / 0.46, v2 (GPT v6.20.2)
0.50 / 0.03. Both errors matter: a false merge hides a distinct development; a
missed merge lets two articles about one story pass as "two developments".
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.research_orchestrator_service import ResearchOrchestratorService as R  # noqa: E402

DATA = json.loads((Path(__file__).parent / "data" / "dedup_gold_pairs.json").read_text(encoding="utf-8"))


def scores():
    events, _ = R._annotate_underlying_events(events=DATA["events"], topic="artificial intelligence")
    group = {e["event_id"]: e["underlying_event_id"] for e in events}
    tp = fp = fn = 0
    for pair in DATA["pairs"]:
        if pair["label"] == "A":
            continue
        same = group[pair["a"]] == group[pair["b"]]
        if pair["label"] == "T":
            tp += same
            fn += not same
        else:
            fp += same
    return tp / (tp + fp), tp / (tp + fn), tp, fp, fn


def test_resolver_precision_and_recall_on_labelled_real_events():
    precision, recall, tp, fp, fn = scores()
    print(f"precision={precision:.2f} recall={recall:.2f} tp={tp} fp={fp} fn={fn}")
    assert precision >= 0.90, (precision, tp, fp, fn)
    assert recall >= 0.70, (recall, tp, fp, fn)


def test_every_merge_in_the_set_is_labelled():
    events, _ = R._annotate_underlying_events(events=DATA["events"], topic="artificial intelligence")
    labelled = {frozenset((p["a"], p["b"])) for p in DATA["pairs"]}
    groups = {}
    for e in events:
        groups.setdefault(e["underlying_event_id"], []).append(e["event_id"])
    unlabelled = [(a, b) for members in groups.values() for i, a in enumerate(members)
                  for b in members[i + 1:] if frozenset((a, b)) not in labelled]
    # A new merge nobody has judged means the measurement no longer covers the resolver.
    assert unlabelled == [], unlabelled[:5]
