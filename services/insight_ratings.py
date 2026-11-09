"""Your ratings of report items (v6.35) - the human measure for experiments.

Each rating is one line in data/insight_ratings.jsonl: which report, which item, its text,
useful / meh / poor, and your one-line reason. Ratings are what decide whether a change
(for example the organisation profile) is kept; the model judge comes second.

Ratings also become pairwise "human" gold pairs for eval_insight_judge.py: an item you rated
useful beats one you rated meh or poor.
"""

from __future__ import annotations

import hashlib
import itertools
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

VERSION = "insight-ratings-v1.1-blind"
RATINGS = ("useful", "meh", "poor")
RANK = {"useful": 2, "meh": 1, "poor": 0}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def path() -> Path:
    custom = _clean(os.getenv("EVENT_INTELLIGENCE_RATINGS"))
    return Path(custom) if custom else Path(__file__).resolve().parents[1] / "data" / "insight_ratings.jsonl"


def item_key(item: Mapping[str, Any]) -> str:
    """Stable key for an item within a report: its ref if it has one, else its text."""
    return _clean(item.get("ref") or item.get("id")) or _clean(item.get("text") or item.get("thesis"))[:200]


def rating_items(sections: Mapping[str, Any]) -> list[dict[str, str]]:
    """Every item worth rating: the ranked insights, then the lab hypotheses not already ranked.
    v6.36.6: shared by the dashboard form and compare_runs.py's blind export."""
    items, seen = [], set()
    evaluation = sections.get("insight_evaluation") if isinstance(sections.get("insight_evaluation"), Mapping) else {}
    for item in evaluation.get("items") or []:
        if isinstance(item, Mapping) and _clean(item.get("text")):
            items.append({"key": item_key(item), "kind": _clean(item.get("kind")), "text": _clean(item.get("text")),
                          "evidence": [_clean(e) for e in item.get("evidence") or [] if _clean(e)],
                          "limitations": _clean((item.get("substance") or {}).get("key_assumption")),
                          "label": f"#{item.get('rank')} · {_clean(item.get('provenance_label'))}"})
            seen.add(_clean(item.get("text")).casefold())
    lab = sections.get("strategic_hypotheses") if isinstance(sections.get("strategic_hypotheses"), Mapping) else {}
    for group, label in (("verified", "Verified hypothesis"), ("promising_unverified", "Hypothesis not yet backed")):
        for item in lab.get(group) or []:
            text = _clean(isinstance(item, Mapping) and item.get("thesis"))
            if text and text.casefold() not in seen:
                seen.add(text.casefold())
                items.append({"key": f"lab:{_clean(item.get('id'))}", "kind": "strategic_hypothesis",
                              "text": text, "label": label,
                              "evidence": [_clean(e) for e in item.get("supporting_evidence") or [] if _clean(e)],
                              "limitations": "; ".join(_clean(e) for e in item.get("promotion_blockers") or [])
                                              or "Hypothesis: assess the supplied evidence; do not assume the conclusion."})
    return items[:20]


def save(entries: Iterable[Mapping[str, Any]], target: Path | None = None) -> int:
    """Append ratings. v6.35.1 (GPT review): a blank reason keeps the item's earlier reason
    instead of erasing it."""
    target = target or path()
    previous = {(r.get("report_id", ""), r.get("item_key") or r.get("text", "")): r for r in load(target)}
    target.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    count = 0
    with open(target, "a", encoding="utf-8") as stream:
        for entry in entries:
            rating = _clean(entry.get("rating")).casefold()
            if rating not in RATINGS or not _clean(entry.get("text")):
                continue
            reason = _clean(entry.get("reason"))[:400]
            earlier = previous.get((_clean(entry.get("report_id")), _clean(entry.get("item_key")) or _clean(entry.get("text"))))
            if not reason and earlier:
                reason = _clean(earlier.get("reason"))[:400]
            row = {"version": VERSION, "rated_at": now, "rating": rating,
                   "report_id": _clean(entry.get("report_id")), "run_id": _clean(entry.get("run_id")),
                   "item_key": _clean(entry.get("item_key")), "kind": _clean(entry.get("kind")),
                   "text": _clean(entry.get("text"))[:1200], "reason": reason,
                   "profile_used": bool(entry.get("profile_used")),
                   # v6.36.6: rated without knowing which run the item came from (compare_runs.py --blind-*)
                   "blind": bool(entry.get("blind"))}
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
            count += 1
    return count


def load(source: Path | None = None) -> list[dict[str, Any]]:
    """Latest rating per (report, item); later ratings replace earlier ones."""
    source = source or path()
    if not source.exists():
        return []
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("rating") in RATINGS:
            latest[(row.get("report_id", ""), row.get("item_key") or row.get("text", ""))] = row
    return list(latest.values())


def summary(ratings: Iterable[Mapping[str, Any]], report_id: str) -> dict[str, Any]:
    rows = [r for r in ratings if r.get("report_id") == report_id]
    counts = {name: sum(1 for r in rows if r["rating"] == name) for name in RATINGS}
    return {"rated": len(rows), **counts,
            "useful_share": round(counts["useful"] / len(rows), 2) if rows else None}


def gold_pairs(ratings: Iterable[Mapping[str, Any]], limit: int = 60) -> list[dict[str, Any]]:
    """Pairs where you rated one item higher than another (useful > meh > poor).

    v6.35.1 (GPT review): only within ONE report - the same question and decision context. A useful
    item for one decision does not outrank a poor item from a different report or goal."""
    by_report: dict[str, list[Mapping[str, Any]]] = {}
    for r in ratings:
        if _clean(r.get("text")) and _clean(r.get("report_id")):
            by_report.setdefault(_clean(r.get("report_id")), []).append(r)
    combos = [pair for rows in by_report.values() for pair in itertools.combinations(rows, 2)]
    pairs = []
    for a, b in combos:
        if RANK[a["rating"]] == RANK[b["rating"]] or a["text"] == b["text"]:
            continue
        winner = "A" if RANK[a["rating"]] > RANK[b["rating"]] else "B"
        digest = hashlib.sha256((a["text"] + "\x00" + b["text"]).encode("utf-8")).hexdigest()[:10]
        pairs.append({"id": "U-" + digest, "gap": abs(RANK[a["rating"]] - RANK[b["rating"]]),
                      "a": a["text"], "b": b["text"], "human_winner": winner,
                      "basis": f"your ratings: {a['rating']} vs {b['rating']}"
                               + (f" ({a['reason'] or b['reason']})" if (a.get("reason") or b.get("reason")) else ""),
                      "source": "insight_ratings", "report_id": a["report_id"]})
    # Bigger gaps first (useful vs poor), so a small sample is the most informative.
    pairs.sort(key=lambda p: -p["gap"])
    return pairs[:limit]
