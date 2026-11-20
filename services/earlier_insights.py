"""Insights the owner has already been shown (v13.12, 20 Nov 2026).

Why
---
The same insight - platforms learned of an AI-agent breach about three months after OpenAI's July
attack - came back run after run (owner, 20 Nov: "I have seen this insight 12 times"). Nothing in
the pipeline knew what earlier reports had shown, and NewsAPI's 30-day window keeps returning the
same story, so every run rebuilt the same thesis and spent its checks on it.

What it does
------------
* ``load(topic)``: insight texts from earlier reports, the same topic's first, then other topics (data/reports.sqlite3: the
  hidden insights, lab theses and briefing implications of each report's current revision) and the
  owner's rating sheet (data/insight_review_90.csv, column ``insight``).
* ``repeats(texts, earlier, ask)``: for each new insight, the earlier insight it repeats, or "".
  Word overlap alone does NOT catch repeats: on the owner's data the 20 Nov breach-delay candidate
  shared at most 22% of its weighted words with the earlier versions of the same thesis (they reword
  it: "three-month latency", "multi-month operational delay", "telemetry bottleneck"). So the closest
  earlier insights by word overlap (the shortlist) are put to the model in ONE call that asks whether
  each new insight makes the same core claim - same mechanism about the same actors - in other words.
  Without a model, only near-verbatim copies (``match``) are caught.

A repeat is not shown again; a genuinely new development on the same story still can be, because the
test is on the insight's wording and content, not on the story.
"""
from __future__ import annotations

import csv
import json
import math
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Mapping, Sequence

from services import same_story

DATA = Path(__file__).resolve().parent.parent / "data"
REPEAT_SHARE = 0.50
REPEAT_SHARE_WITH_NAMES = 0.33
MIN_SHARED_NAMES = 2
MAX_EARLIER = 400


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _texts_of_report(payload: Mapping[str, Any]) -> list[str]:
    sections = payload.get("sections") if isinstance(payload.get("sections"), Mapping) else {}
    out: list[str] = []
    for key in ("hidden_insights", "strategic_findings"):
        for item in sections.get(key) or []:
            if isinstance(item, Mapping):
                out.append(_clean(item.get("insight") or item.get("text") or item.get("statement")))
    lab = sections.get("strategic_hypotheses") if isinstance(sections.get("strategic_hypotheses"), Mapping) else {}
    for key in ("verified", "promising_unverified"):
        for item in lab.get(key) or []:
            if isinstance(item, Mapping):
                out.append(_clean(item.get("thesis")))
    briefing = sections.get("editorial_briefing") if isinstance(sections.get("editorial_briefing"), Mapping) else {}
    for item in briefing.get("implications") or []:
        if isinstance(item, Mapping):
            out.append(_clean(item.get("text")))
    return [t for t in out if len(t) >= 40]


def load(topic: str = "", data_dir: Path | None = None) -> list[str]:
    """Earlier insight texts for this topic, newest first, without duplicates. Never raises."""
    root = data_dir or DATA
    texts: list[str] = []
    try:
        db = sqlite3.connect(f"file:{root / 'reports.sqlite3'}?mode=ro", uri=True)
        try:
            rows = db.execute(
                "SELECT r.research_question, v.payload_json FROM reports r JOIN report_revisions v "
                "ON v.report_revision_id = r.current_revision_id ORDER BY r.created_at DESC").fetchall()
        finally:
            db.close()
        wanted = _clean(topic).casefold()
        # v13.14 review: reports on OTHER topics count too ("AI governance" insights are repeats in an
        # "artificial intelligence" run); the same topic's come first, so the prompt list shows them.
        same, other = [], []
        for question, payload in rows:
            try:
                found = _texts_of_report(json.loads(payload))
            except Exception:
                continue
            (same if not wanted or _clean(question).casefold() == wanted else other).extend(found)
        texts += same + other
    except Exception:
        pass
    try:
        with open(root / "insight_review_90.csv", newline="", encoding="utf-8-sig") as handle:
            texts += [_clean(row.get("insight")) for row in csv.DictReader(handle) if _clean(row.get("insight"))]
    except Exception:
        pass
    seen: set[str] = set()
    unique = []
    for text in texts:
        key = text.casefold()[:200]
        if key not in seen:
            seen.add(key)
            unique.append(text)
    return unique[:MAX_EARLIER]


def _tokens(text: str) -> tuple[set[str], set[str]]:
    return same_story.story_words({"title": "", "summary": text})


def _weights(token_sets: Sequence[set[str]]) -> dict[str, float]:
    freq = Counter(w for words in token_sets for w in words)
    total = max(1, len(token_sets))
    return {w: math.log((total + 1) / c) for w, c in freq.items()}


def match(text: str, earlier: Sequence[str]) -> str:
    """The earlier insight `text` repeats, or ""."""
    text = _clean(text)
    if not text or not earlier:
        return ""
    words, names = _tokens(text)
    pool = [_tokens(t) for t in earlier]
    idf = _weights([w for w, _ in pool] + [words])
    best, best_share = "", 0.0
    for (other_words, other_names), other_text in zip(pool, earlier):
        shared = words & other_words
        if len(shared) < 4:
            continue
        smaller = min(sum(idf[w] for w in words), sum(idf[w] for w in other_words)) or 1.0
        share = sum(idf[w] for w in shared) / smaller
        shared_names = names & other_names
        if share >= REPEAT_SHARE or (share >= REPEAT_SHARE_WITH_NAMES and len(shared_names) >= MIN_SHARED_NAMES):
            if share > best_share:
                best, best_share = other_text, share
    return best


SHORTLIST = 6
SHORTLIST_MIN_SHARE = 0.06


def shortlist(text: str, earlier: Sequence[str], k: int = SHORTLIST) -> list[int]:
    """Indexes of the earlier insights closest to `text` by weighted word overlap."""
    words, _ = _tokens(text)
    if not words:
        return []
    pool = [_tokens(t) for t in earlier]
    idf = _weights([w for w, _ in pool] + [words])
    scored = []
    for index, (other, _) in enumerate(pool):
        smaller = min(sum(idf[w] for w in words), sum(idf[w] for w in other)) or 1.0
        share = sum(idf[w] for w in words & other) / smaller
        if share >= SHORTLIST_MIN_SHARE:
            scored.append((share, index))
    return [index for _, index in sorted(scored, reverse=True)[:k]]


def repeats_prompt(texts: Sequence[str], earlier: Sequence[str], lists: Sequence[Sequence[int]]) -> str:
    blocks = []
    for n, (text, ids) in enumerate(zip(texts, lists), start=1):
        options = "\n".join(f"    E{i}: {_clean(earlier[i])[:500]}" for i in ids)
        blocks.append(f"NEW {n}: {_clean(text)[:700]}\n  EARLIER (shown to the reader before):\n{options}")
    return ("For each NEW insight, decide whether it makes the SAME CORE CLAIM as one of its EARLIER insights: "
            "the same mechanism or conclusion about the same actors or the same kind of event, even if worded "
            "differently or with a few added details. It is NOT the same when it is about a different "
            "development, or when it adds a new fact that changes the earlier conclusion (a new actor's "
            "response, a new number, a reversal).\n\n" + "\n\n".join(blocks)
            + '\n\nReturn JSON {"items":[{"new":1,"same_as":"E12 or empty","why":"few words"}]}')


def repeats(texts: Sequence[str], earlier: Sequence[str], ask: Any = None) -> list[str]:
    """For each text, the earlier insight it repeats ("" if none). One model call at most; never raises."""
    texts = [_clean(t) for t in texts]
    out = [match(t, earlier) for t in texts]
    if ask is None or not earlier:
        return out
    pending = [n for n, t in enumerate(texts) if t and not out[n]]
    lists = {n: shortlist(texts[n], earlier) for n in pending}
    pending = [n for n in pending if lists[n]]
    if not pending:
        return out
    try:
        reply = ask(repeats_prompt([texts[n] for n in pending], earlier, [lists[n] for n in pending]))
        data = reply if isinstance(reply, Mapping) else json.loads(str(reply or "{}").strip().strip("`")
                                                                     .removeprefix("json").strip() or "{}")
        for row in data.get("items") or []:
            if not isinstance(row, Mapping):
                continue
            try:
                n = pending[int(row.get("new")) - 1]
            except (TypeError, ValueError, IndexError):
                continue
            label = _clean(row.get("same_as")).upper().lstrip("E")
            if label.isdigit() and int(label) in lists[n]:
                out[n] = earlier[int(label)]
    except Exception:
        pass
    return out


def brief(repeated: str) -> list[str]:
    return ["[already shown] This repeats an insight the reader saw in an earlier report: "
            f"\"{_clean(repeated)[:300]}\". Do not restate it. Use a different development, or a new fact "
            "that changes that conclusion, and say what is new."]
