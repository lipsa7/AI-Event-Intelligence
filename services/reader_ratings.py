"""Two yes/no questions per insight: "New to me?" and "Would I act on it?" (v13.20, 22 Nov 2026).

Why
---
The app has 0 human ratings in its database, so nothing shows whether a reader learns anything from
it that the news did not give them. Binary labels, not 1-5 scales: annotators "default to middle
values" on Likert scales (Hamel Husain and Eugene Yan on LLM evals). "New to me" is the reader's
test of novelty; "would act on it" is the strategist's test of decision value (Valona 2026: world-class
intelligence teams measure decisions influenced). Si, Yang & Hashimoto (2024) and the
Ideation-Execution Gap follow-up show ratings at reading time must sit beside outcome checks later
(forecast resolution), not replace them.

Stored as one JSON line per answer in data/reader_ratings.jsonl; the latest answer per insight and
question counts. Nothing is sent anywhere.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Mapping

VERSION = "reader-ratings-v1"
QUESTIONS = {"new_to_me": "New to me?", "would_act": "Would I act on it?"}
PATH_ENV = "EVENT_INTELLIGENCE_READER_RATINGS_PATH"


def _path() -> Path:
    return Path(os.getenv(PATH_ENV) or os.path.join("data", "reader_ratings.jsonl"))


def save(*, report_id: str, insight_id: str, question: str, answer: bool, insight_text: str = "",
         original: bool = True) -> dict[str, Any]:
    if question not in QUESTIONS:
        raise ValueError(f"unknown question {question!r}")
    row = {"version": VERSION, "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
           "report_id": str(report_id), "insight_id": str(insight_id), "question": question,
           "answer": bool(answer), "original": bool(original), "insight_text": str(insight_text)[:600]}
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return row


def latest() -> dict[tuple[str, str], Mapping[str, Any]]:
    """(insight_id, question) -> latest answer row."""
    path = _path()
    out: dict[tuple[str, str], Mapping[str, Any]] = {}
    if not path.exists():
        return out
    for text in path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(row, Mapping) and row.get("question") in QUESTIONS and row.get("insight_id"):
            out[(str(row["insight_id"]), str(row["question"]))] = row
    return out


def answer_for(insight_id: str, question: str) -> bool | None:
    row = latest().get((str(insight_id), question))
    return None if row is None else bool(row.get("answer"))
