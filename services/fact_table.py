"""Fact table: every dated figure in a story's records, and comparisons computed by code (v13.30).

Research report of 22 Nov ("Getting AI Event Intelligence to produce original insights"), Method 1 - the
largest expected gain in both reports: pull every number and date out of the text into a table, let code
(not the model) compute the gaps, rates and ratios, and make the model's job choosing which computed facts
matter. PAL (Gao et al., ICML 2023): having the model hand arithmetic to an interpreter beat step-by-step
reasoning by 15 points on GSM8K. The pipeline found about 2 computed gaps in 49 events, because
services/delta_extractor.py looks for one gap per development with a 28-day minimum.

This module (plain code, no model call):
* extract(records): every date, money amount, percentage and counted quantity ("80 incidents") with the
  evidence ID and the sentence it came from;
* compute(rows): date gaps between every pair of distinct dated events in the story (at least 7 days),
  how long after an event a record reported it, money ratios and differences and percentage-point gaps
  WITHIN one record, and counts per period when a count and a date span sit in the same record. Each computed fact names the evidence IDs it rests on;
* prompt_block(): the table for the generator, with the instruction to cite the evidence IDs and to state
  the computed figure as given.
Numbers the generator writes that are in this table (or computed from it) count as sourced for the number
checks in services/variant_view.py.
"""
from __future__ import annotations

import re
from datetime import date
from itertools import combinations
from typing import Any, Mapping, Sequence

from services import delta_extractor

VERSION = "fact-table-v1"
MIN_DATE_GAP_DAYS = 7
MAX_COMPUTED = 30
_COUNT = re.compile(r"\b(\d{1,3}(?:,\d{3})*|\d+)\s+(?:other\s+|more\s+|new\s+|separate\s+)?"
                    r"(incidents?|lawsuits?|suits?|cases?|complaints?|states?|users?|employees?|people|companies|"
                    r"models?|agents?|violations?|breaches|attacks?|deaths?|children|minors|accounts?|reports?|"
                    r"filings?|countries|jurisdictions|days|weeks|months)\b", re.I)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", _clean(text)) if s]


def extract(records: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple] = set()
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        eid = _clean(record.get("evidence_id"))
        if not eid:
            continue
        published = delta_extractor._iso(record.get("published_at") or record.get("source_published_at")
                                         or record.get("date"))
        for sentence in _sentences(record.get("text") or record.get("source_excerpt") or record.get("claim")):
            for value, exact, words in delta_extractor.mentioned_dates(sentence, published):
                key = (eid, "date", value.isoformat())
                if key not in seen:
                    seen.add(key)
                    rows.append({"evidence_id": eid, "kind": "date", "value": value.isoformat(), "exact_day": exact,
                                 "words": words, "sentence": sentence[:300]})
            for kind, value, words in delta_extractor._amounts(sentence):
                key = (eid, kind, value)
                if key not in seen:
                    seen.add(key)
                    rows.append({"evidence_id": eid, "kind": kind, "value": value, "words": words,
                                 "sentence": sentence[:300]})
            for match in _COUNT.finditer(sentence):
                try:
                    value = float(match.group(1).replace(",", ""))
                except ValueError:
                    continue
                unit = match.group(2).casefold().rstrip("s") or match.group(2).casefold()
                key = (eid, "count", value, unit)
                if 1 < value < 1e9 and key not in seen:
                    seen.add(key)
                    rows.append({"evidence_id": eid, "kind": "count", "value": value, "unit": unit,
                                 "words": match.group(0), "sentence": sentence[:300]})
        if published:
            key = (eid, "published", published.isoformat())
            if key not in seen:
                seen.add(key)
                rows.append({"evidence_id": eid, "kind": "published", "value": published.isoformat(),
                             "words": "published " + published.isoformat(), "sentence": ""})
    return rows


def _days(a: str, b: str) -> int:
    return abs((date.fromisoformat(b) - date.fromisoformat(a)).days)


def _money(value: float) -> str:
    for scale, word in ((1e12, "trillion"), (1e9, "billion"), (1e6, "million")):
        if value >= scale:
            return f"${value / scale:,.1f} {word}"
    return f"${value:,.0f}"


def compute(rows: Sequence[Mapping[str, Any]], limit: int = MAX_COMPUTED) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    # Month-only dates ("in June") count too - the owner's approved insight was a June event disclosed in
    # September - but only to whole months, and the text says "about".
    dates = [r for r in rows if r.get("kind") == "date"]
    distinct: dict[str, Mapping[str, Any]] = {}
    for row in sorted(dates, key=lambda r: (r["value"], not r.get("exact_day"))):
        distinct.setdefault(row["value"] if row.get("exact_day") else row["value"][:7], row)
    for a, b in combinations(sorted(distinct.values(), key=lambda r: r["value"]), 2):
        days = _days(a["value"], b["value"])
        if days < MIN_DATE_GAP_DAYS or (a["value"][:7] == b["value"][:7] and not (a.get("exact_day") and b.get("exact_day"))):
            continue
        exact = bool(a.get("exact_day") and b.get("exact_day"))
        months = round(days / 30.4)
        span = f"{days} days (about {round(days / 7)} weeks)" if exact else f"about {months} month{'s' if months != 1 else ''}"
        out.append({"kind": "date_gap", "days": days, "evidence_ids": sorted({a["evidence_id"], b["evidence_id"]}),
                    "figures": ([str(days), str(round(days / 7))] if exact else []) + [str(months)],
                    "text": f'{a["words"]} to {b["words"]}: {span}'})
    published = {r["evidence_id"]: r["value"] for r in rows if r.get("kind") == "published"}
    for row in [r for r in dates if r.get("exact_day")]:
        pub = published.get(row["evidence_id"])
        if pub and row["value"] < pub:
            days = _days(row["value"], pub)
            if days >= 28:
                out.append({"kind": "reported_later", "days": days, "evidence_ids": [row["evidence_id"]],
                            "figures": [str(days), str(round(days / 7)), str(round(days / 30.4))],
                            "text": f'{row["words"]} was reported in a record published {pub}: {days} days later'})
    # Amounts and percentages are compared only within one record: across records they are usually different
    # things (a quarterly spend and a market forecast), and a ratio between them is the stapling the second
    # report warns about. Dates may be compared across the story's records.
    money = [r for r in rows if r.get("kind") == "money"]
    for a, b in [(x, y) for x, y in combinations(money, 2) if x["evidence_id"] == y["evidence_id"]]:
        low, high = sorted((a, b), key=lambda r: r["value"])
        if low["value"] > 0 and high["value"] / low["value"] >= 1.5 and low["words"] != high["words"]:
            ratio = round(high["value"] / low["value"], 1)
            out.append({"kind": "money_ratio", "ratio": ratio, "evidence_ids": sorted({a["evidence_id"], b["evidence_id"]}),
                        "figures": [str(ratio), _money(high["value"] - low["value"])],
                        "text": f'{high["words"]} is {ratio} times {low["words"]} (difference {_money(high["value"] - low["value"])})'})
    percents = [r for r in rows if r.get("kind") == "percent"]
    for a, b in [(x, y) for x, y in combinations(percents, 2) if x["evidence_id"] == y["evidence_id"]]:
        if a["value"] != b["value"]:
            gap = round(abs(a["value"] - b["value"]), 1)
            out.append({"kind": "percentage_points", "points": gap, "evidence_ids": sorted({a["evidence_id"], b["evidence_id"]}),
                        "figures": [str(gap)], "text": f'{a["words"]} against {b["words"]}: {gap} percentage points apart'})
    by_record: dict[str, list[Mapping[str, Any]]] = {}
    for row in rows:
        by_record.setdefault(row["evidence_id"], []).append(row)
    for eid, items in by_record.items():
        record_dates = sorted({r["value"] for r in items if r.get("kind") == "date" and r.get("exact_day")})
        counts = [r for r in items if r.get("kind") == "count" and r.get("unit") not in {"day", "week", "month"}]
        if len(record_dates) >= 2 and counts:
            span = _days(record_dates[0], record_dates[-1])
            if span >= MIN_DATE_GAP_DAYS:
                for count in counts[:2]:
                    per_month = round(count["value"] / (span / 30.4), 1)
                    out.append({"kind": "rate", "per_month": per_month, "evidence_ids": [eid],
                                "figures": [str(per_month)],
                                "text": f'{count["words"]} over {span} days ({record_dates[0]} to {record_dates[-1]}): '
                                        f'about {per_month} a month'})
    unique: dict[str, dict[str, Any]] = {}
    for row in out:
        unique.setdefault(row["text"], row)
    out = sorted(unique.values(), key=lambda r: (-(r.get("days") or 0), -(r.get("ratio") or 0)))
    for n, row in enumerate(out[:limit], start=1):
        row["calc_id"] = f"CALC-{n}"
    return out[:limit]


def prompt_block(rows: Sequence[Mapping[str, Any]], computed: Sequence[Mapping[str, Any]], max_rows: int = 40) -> str:
    if not rows and not computed:
        return ""
    lines = ["FACT TABLE (every dated figure in this story's records; extracted by code):"]
    for row in list(rows)[:max_rows]:
        if row.get("kind") == "published":
            continue
        lines.append(f'- [{row["evidence_id"]}] {row["kind"]}: {row["words"]}')
    if computed:
        lines.append("COMPUTED BY CODE (exact; state these figures as given and cite their evidence IDs - do not "
                     "compute your own):")
        lines += [f'- {c["calc_id"]} [{", ".join(c["evidence_ids"])}]: {c["text"]}' for c in computed]
    return "\n".join(lines)


def figures(computed: Sequence[Mapping[str, Any]]) -> dict[str, set[str]]:
    """figure digits -> the evidence IDs the computed fact rests on (for the number checks)."""
    out: dict[str, set[str]] = {}
    for row in computed or []:
        for figure in row.get("figures") or []:
            digits = re.sub(r"[^\d.]", "", str(figure)).strip(".")
            if digits:
                out.setdefault(digits, set()).update(row.get("evidence_ids") or [])
    return out
