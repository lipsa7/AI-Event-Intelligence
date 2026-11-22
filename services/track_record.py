"""The app's public track record, computed from its own saved data (v13.20, 22 Nov 2026).

Why
---
News sites and editorials publish forecasts without scoring them; forecasting platforms (Good
Judgment, Metaculus) earn credibility by showing every resolved forecast, misses included. The
research report of 21 Nov (section F, items 3 and 13) names this as the app's one defensible
difference from news, editorials and deep-research tools: verified non-obviousness plus a public,
scored track record. This module recomputes every figure from the stores on disk, so a number shown
to a reader always has a source:

* data/reports.sqlite3                - saved reports and their insights
* data/forecasts/forecasts.sqlite3    - registered forecasts and their resolutions (Brier score)
* data/reader_ratings.jsonl           - "New to me?" / "Would I act on it?" answers

Credibility thresholds shown beside the figures:
* Brier score: about 100 resolved binary forecasts before differences under 0.01 mean anything
  (MacKay, Significance 2025). Baselines: 0.25 = always 50%; about 0.12 = general public / best LLM on
  ForecastBench (ICLR 2025).
* A rated share: 95% interval half-width 1.96*sqrt(p(1-p)/n) - about +/-14 points at n=50, +/-10 at n=100.
"""
from __future__ import annotations

import json
import math
import re
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

VERSION = "track-record-v1"
BRIER_CREDIBLE_N = 100
RATING_CREDIBLE_N = 50
SAME_INSIGHT_JACCARD = 0.3   # word overlap: a LOWER bound - reworded repeats can be missed
_WORD = re.compile(r"[a-z0-9]{3,}")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _words(text: str) -> set[str]:
    return set(_WORD.findall(_clean(text).casefold()))


def _same(a: set[str], b: set[str]) -> bool:
    return bool(a and b) and len(a & b) / len(a | b) >= SAME_INSIGHT_JACCARD


def interval(yes: int, n: int) -> float:
    """95% half-width of a share (normal approximation), in percentage points."""
    if n <= 0:
        return 0.0
    p = yes / n
    return round(196 * math.sqrt(p * (1 - p) / n), 1)


def _reports(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with sqlite3.connect(str(path)) as connection:
        rows = connection.execute(
            "select r.report_id, r.research_question, r.state, r.created_at, v.payload_json from reports r "
            "join report_revisions v on v.report_revision_id = r.current_revision_id order by r.created_at").fetchall()
    out = []
    for report_id, question, state, created, payload in rows:
        try:
            data = json.loads(payload or "{}")
        except json.JSONDecodeError:
            data = {}
        out.append({"report_id": report_id, "topic": _clean(question), "state": state, "created_at": created,
                    "payload": data})
    return out


def _forecasts(path: Path) -> tuple[int, list[float]]:
    if not path.exists():
        return 0, []
    with sqlite3.connect(str(path)) as connection:
        made = connection.execute("select count(*) from forecasts").fetchone()[0]
        briers = [float(row[0]) for row in connection.execute(
            "select brier_score from resolutions where brier_score is not null").fetchall()]
    return int(made), briers


def compute(data_dir: str | Path = "data", ratings: Mapping[tuple[str, str], Mapping[str, Any]] | None = None) -> dict[str, Any]:
    from services import proof_line
    root = Path(data_dir)
    reports = _reports(root / "reports.sqlite3")
    originals: list[dict[str, Any]] = []
    background = 0
    with_original = 0
    for report in reports:
        items = (report["payload"].get("sections") or {}).get("hidden_insights") or []
        mine, backed = proof_line.split(items if isinstance(items, list) else [])
        background += len(backed)
        if mine:
            with_original += 1
        for item in mine:
            originals.append({"text": _clean(item.get("text")), "insight_id": _clean(item.get("insight_id")),
                              "report_id": report["report_id"], "date": _clean(report["created_at"])[:10]})
    unique: list[set[str]] = []
    repeats = 0
    for item in originals:
        words = _words(item["text"])
        if any(_same(words, seen) for seen in unique):
            repeats += 1
        else:
            unique.append(words)
    made, briers = _forecasts(root / "forecasts" / "forecasts.sqlite3")
    if ratings is None:
        from services import reader_ratings
        ratings = reader_ratings.latest()
    rated = {}
    for question in ("new_to_me", "would_act"):
        answers = [bool(row.get("answer")) for (_, q), row in ratings.items()
                   if q == question and row.get("original", True)]
        yes = sum(answers)
        rated[question] = {"n": len(answers), "yes": yes,
                           "share": round(100 * yes / len(answers), 1) if answers else None,
                           "plus_minus": interval(yes, len(answers)),
                           "credible": len(answers) >= RATING_CREDIBLE_N}
    topics = sorted({r["topic"].casefold() for r in reports if r["topic"]})
    return {
        "version": VERSION,
        "runs_saved": len(reports),
        "topics": topics,
        "runs_with_original_insight": with_original,
        "abstention_share": round(100 * (len(reports) - with_original) / len(reports), 1) if reports else None,
        "original_insights": len(originals),
        "unique_original_insights": len(unique),
        "repeated_original_insights": repeats,
        "background_findings_shown": background,
        "forecasts_made": made,
        "forecasts_resolved": len(briers),
        "brier": round(sum(briers) / len(briers), 3) if briers else None,
        "brier_credible": len(briers) >= BRIER_CREDIBLE_N,
        "ratings": rated,
        "recent_original_insights": originals[-10:][::-1],
    }


def summary_lines(record: Mapping[str, Any]) -> Iterable[str]:
    yield (f"Runs saved: {record['runs_saved']} on {len(record['topics'])} topic(s); "
           f"{record['runs_with_original_insight']} produced an original insight "
           f"(abstained: {record['abstention_share']}%).")
    yield (f"Original insights: {record['original_insights']} ({record['unique_original_insights']} unique, "
           f"{record['repeated_original_insights']} repeats by word overlap, a lower bound); background findings shown: "
           f"{record['background_findings_shown']}.")
    brier = record["brier"]
    yield (f"Forecasts: {record['forecasts_made']} made, {record['forecasts_resolved']} resolved"
           + (f", Brier {brier} (0.25 = always 50%; about 0.12 = public/best LLM on ForecastBench)"
              if brier is not None else ", no Brier score yet")
           + ("" if record["brier_credible"] else f" - not credible below {BRIER_CREDIBLE_N} resolved (MacKay 2025)")
           + ".")
    for key, label in (("new_to_me", "New to me"), ("would_act", "Would act on it")):
        row = record["ratings"][key]
        yield (f"{label}: " + (f"{row['share']}% of {row['n']} rated (+/-{row['plus_minus']} points)"
                               if row["n"] else "no ratings yet")
               + ("" if row["credible"] else f" - not credible below {RATING_CREDIBLE_N} ratings") + ".")
