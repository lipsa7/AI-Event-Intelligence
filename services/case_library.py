"""A curated library of past cases, tagged by mechanism and outcome (v13.31).

Both research reports of 22 Nov: the past-case search failed on retrieval (free news providers failing,
NewsAPI out of coverage, pages unfetchable - 3 cases in 18 searches), so "stop relying on live search":
build 40-300 structured cases once, tagged by mechanism, legal theory and outcome, and retrieve them by
structure, not by keywords. Fields follow the reports' schema: case_id, name, jurisdiction, year,
plaintiff_type, defendant_sector, legal_theory, mechanism_tags, key_holding, outcome, outcome_date,
time_to_outcome_days, disanalogy_notes, source_url, approved.

Only entries the owner has approved ("approved": true) are used - drafts made by tools/build_case_library.py
are proposals. Green & Armstrong (2007): structured analogies beat unaided experts (46% vs 32%), and 60%
with two or more analogies; the forecast is taken mechanically from the most similar case's outcome.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

VERSION = "case-library-v1"
PATH_ENV = "EVENT_INTELLIGENCE_CASE_LIBRARY"
DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "case_library.jsonl"
_WORD = re.compile(r"[a-z0-9]+")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def load(path: Path | None = None, approved_only: bool = True) -> list[dict[str, Any]]:
    target = Path(os.getenv(PATH_ENV) or path or DEFAULT_PATH)
    if not target.exists():
        return []
    out = []
    for line in target.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict) and row.get("case_id") and (row.get("approved") or not approved_only):
            out.append(row)
    return out


def _bag(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.casefold()) if len(w) > 3}


def describe(case: Mapping[str, Any]) -> str:
    return " ".join(_clean(case.get(k)) for k in ("legal_theory", "plaintiff_type", "defendant_sector", "key_holding")) \
        + " " + " ".join(case.get("mechanism_tags") or [])


def retrieve(story_text: str, cases: Sequence[Mapping[str, Any]] | None = None, k: int = 3,
             embed: Any = None) -> list[dict[str, Any]]:
    """The k cases whose mechanism description is closest to the story (embeddings when available, else words)."""
    cases = list(cases if cases is not None else load())
    if not cases or not _clean(story_text):
        return []
    if embed is not None:
        try:
            scores = embed(story_text, [describe(c) for c in cases])
        except Exception:
            scores = None
    else:
        scores = None
    if scores is None:
        story = _bag(story_text)
        scores = [len(story & _bag(describe(c))) / max(1, len(_bag(describe(c)))) for c in cases]
    ranked = sorted(zip(scores, cases), key=lambda pair: -float(pair[0]))
    return [dict(case, similarity=round(float(score), 3)) for score, case in ranked[:k] if float(score) > 0]


def base_rate(cases: Sequence[Mapping[str, Any]], outcome_word: str) -> dict[str, Any]:
    """Share of retrieved cases whose outcome contains outcome_word, and their median time to outcome."""
    rows = [c for c in cases if _clean(c.get("outcome"))]
    if not rows:
        return {"cases": 0}
    hits = [c for c in rows if outcome_word.casefold() in _clean(c.get("outcome")).casefold()]
    days = sorted(int(c["time_to_outcome_days"]) for c in rows if str(c.get("time_to_outcome_days") or "").isdigit())
    return {"cases": len(rows), "share": round(len(hits) / len(rows), 2),
            "median_days": days[len(days) // 2] if days else None}
