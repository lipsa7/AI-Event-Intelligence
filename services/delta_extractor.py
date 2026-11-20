"""Computed gaps: the surprises code can find before any model is called (v12, 16 Nov 2026).

The owner's one approved insight was a computed gap: an AI-agent breach in June that governments
learned of in September, about three months later. Intelligence "indicators" work and
data-driven hypothesis generation (HypoGeniC, Zhou et al. 2024) both start from a mismatch
between what was expected and what happened. This module finds such mismatches in the
evidence text with plain code (no model calls):

* date gaps: two dates mentioned for one development (e.g. "occurred in June", "reported on
  10 September"), or a date mentioned in a record versus the date the record was published;
* amount gaps: two amounts of the same kind (dollars, percentages) in one development that
  differ by at least two times.

Each gap names the facts it comes from, so the generator can cite them. The model is asked to
EXPLAIN a gap, never to invent one.
"""
from __future__ import annotations

import re
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

VERSION = "delta-extractor-v1.2-relative-dates"
MIN_GAP_DAYS = 28
MAX_GAPS = 6

MONTHS = {name: index for index, name in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
     "november", "december"], start=1)}
MONTHS.update({name[:3]: index for name, index in list(MONTHS.items())})
MONTHS["sept"] = 9

_MONTH = r"(january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sept|sep|oct|nov|dec)\.?"
_DAY_MONTH_YEAR = re.compile(rf"\b(\d{{1,2}})\s+{_MONTH}(?:,?\s+(20\d\d))?\b", re.I)
_MONTH_DAY_YEAR = re.compile(rf"\b{_MONTH}\s+(\d{{1,2}})(?:st|nd|rd|th)?(?:,?\s+(20\d\d))?\b", re.I)
_MONTH_YEAR = re.compile(rf"\b(?:in|since|during|by|until|from|early|late|mid-?)\s+{_MONTH}(?:\s+(20\d\d))?\b", re.I)
_ISO = re.compile(r"\b(20\d\d)-(\d\d)-(\d\d)(?!\d)")  # v13: also "2026-09-24T11:52:32"
_RELATIVE = re.compile(r"\b(earlier this month|this month|last month|the previous month|earlier in the month)\b", re.I)
_AMOUNT = re.compile(r"(?:\$|US\$|USD\s?|€|£)\s?(\d+(?:[.,]\d+)?)\s*(trillion|billion|million|bn|m|tn|k)?\b", re.I)
_PERCENT = re.compile(r"\b(\d+(?:\.\d+)?)\s?(?:%|percent\b)", re.I)
_SCALE = {"trillion": 1e12, "tn": 1e12, "billion": 1e9, "bn": 1e9, "million": 1e6, "m": 1e6, "k": 1e3}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _iso(value: Any) -> date | None:
    match = _ISO.search(_clean(value))
    if not match:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


def mentioned_dates(text: str, published: date | None = None) -> list[tuple[date, bool, str]]:
    """Dates written in the text: (date, exact_day, the words used). A month without a year takes the
    publication year, or the year before when that month would fall after publication."""
    found: dict[tuple[int, int, int], tuple[date, bool, str]] = {}

    def add(year: str | None, month: str, day: str | None, words: str) -> None:
        month_no = MONTHS.get(month.casefold().rstrip("."))
        if not month_no:
            return
        if year:
            year_no = int(year)
        elif published:
            year_no = published.year - (1 if month_no > published.month else 0)
        else:
            return
        try:
            value = date(year_no, month_no, int(day) if day else 15)
        except ValueError:
            return
        key = (value.year, value.month, value.day if day else 0)
        found.setdefault(key, (value, bool(day), _clean(words)))

    for match in _ISO.finditer(text):
        try:
            value = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            found.setdefault((value.year, value.month, value.day), (value, True, match.group(0)))
        except ValueError:
            pass
    for match in _DAY_MONTH_YEAR.finditer(text):
        add(match.group(3), match.group(2), match.group(1), match.group(0))
    for match in _MONTH_DAY_YEAR.finditer(text):
        add(match.group(3), match.group(1), match.group(2), match.group(0))
    for match in _MONTH_YEAR.finditer(text):
        month_no = MONTHS.get(match.group(1).casefold().rstrip("."))
        if month_no and not any(k[1] == month_no for k in found):   # a day-precise mention wins
            add(match.group(2), match.group(1), None, match.group(0))
    if published:
        # v13 (19 Nov 2026): dates written relative to the article ("earlier this month", "last
        # month"), placed by its publication date. "The breach happened in June ... notified earlier
        # this month" (published 24 Sep) is the owner's three-month delay.
        for match in _RELATIVE.finditer(text):
            phrase = match.group(0)
            if "last month" in phrase.casefold() or "previous month" in phrase.casefold():
                year, month = (published.year, published.month - 1) if published.month > 1 else (published.year - 1, 12)
            else:
                year, month = published.year, published.month
            day = min(15, published.day) if "this month" in phrase.casefold() else 15
            value = date(year, month, day)
            if not any(k[0] == value.year and k[1] == value.month for k in found):
                found[(value.year, value.month, 0)] = (value, False, _clean(phrase))
    return sorted(found.values(), key=lambda item: item[0])


def _span(days: int) -> str:
    if days < 60:
        return f"about {round(days / 7)} weeks"
    months = round(days / 30.4)
    return f"about {months} months" if months < 24 else f"about {round(days / 365)} years"


_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")


def _amount_pairs(text: str) -> list[tuple[str, tuple, tuple]]:
    """v12.1 (18 Nov 2026, simulated run): an amount gap only within ONE sentence that holds exactly
    two amounts of one kind. A headline roundup ("A raised $215M, B raised $30M, C ...") holds
    three or more and compares different companies, which is not a gap."""
    pairs = []
    for sentence in _SENTENCE.split(text):
        by_kind: dict[str, list[tuple[float, str]]] = {}
        for kind, value, words in _amounts(sentence):
            by_kind.setdefault(kind, []).append((value, words))
        for kind, values in by_kind.items():
            if len(values) == 2:
                low, high = sorted(values)
                pairs.append((kind, low, high))
    return pairs


def _amounts(text: str) -> list[tuple[str, float, str]]:
    out = []
    for match in _AMOUNT.finditer(text):
        try:
            value = float(match.group(1).replace(",", "")) * _SCALE.get((match.group(2) or "").casefold(), 1)
        except ValueError:
            continue
        if value >= 1000:
            out.append(("money", value, match.group(0).strip()))
    for match in _PERCENT.finditer(text):
        out.append(("percent", float(match.group(1)), match.group(0).strip()))
    return out


def compute(facts: Iterable[Mapping[str, Any]], *, id_key: str = "fact_id", group_key: str = "development",
            limit: int = MAX_GAPS) -> list[dict[str, Any]]:
    """Gaps inside each group (development). facts: {id_key, text, date (publication), group_key}."""
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for fact in facts or []:
        if isinstance(fact, Mapping) and _clean(fact.get("text")) and _clean(fact.get(id_key)):
            groups.setdefault(_clean(fact.get(group_key)) or _clean(fact.get(id_key)), []).append(fact)
    gaps: list[dict[str, Any]] = []
    for group, rows in groups.items():
        dated: list[tuple[date, bool, str, str]] = []           # (date, exact, words, fact id)
        published_of: dict[str, date] = {}
        amount_gaps: list[dict[str, Any]] = []
        for row in rows:
            fid = _clean(row.get(id_key))
            published = _iso(row.get("date") or row.get("published_at"))
            if published:
                published_of[fid] = published
            for value, exact, words in mentioned_dates(_clean(row.get("text")), published):
                if not published or value <= published:
                    dated.append((value, exact, words, fid))
            for kind, low, high in _amount_pairs(_clean(row.get("text"))):
                if low[0] > 0 and high[0] / low[0] >= 2 and low[1] != high[1]:
                    amount_gaps.append({"development": group, "kind": "amount_gap", "days": 0,
                                        "ratio": round(high[0] / low[0], 1), "fact_ids": [fid],
                                        "text": f"{high[1]} against {low[1]}: {round(high[0] / low[0], 1)} times "
                                                "larger (in one sentence of one record)"})
        best: dict[str, Any] | None = None
        if len(dated) >= 2:
            first, last = min(dated, key=lambda d: d[0]), max(dated, key=lambda d: d[0])
            days = (last[0] - first[0]).days
            if days >= MIN_GAP_DAYS:
                best = {"kind": "date_gap", "days": days, "fact_ids": sorted({first[3], last[3]}),
                        "text": f'"{first[2]}" to "{last[2]}": {_span(days)} apart'}
        for value, exact, words, fid in dated:
            published = published_of.get(fid)
            # v13: "since August 2025", "until June", "by March", "from May" describe a period or a
            # deadline, not something reported late; only a dated happening counts as a delay.
            if re.match(r"^(since|until|by|from|through)\b", words, re.I):
                continue
            if published:
                days = (published - value).days
                if days >= MIN_GAP_DAYS and (best is None or days > best["days"]):
                    best = {"kind": "reported_later", "days": days, "fact_ids": [fid],
                            "text": f'"{words}" was reported in a record published {published.isoformat()}: '
                                    f"{_span(days)} later"}
        if best:
            gaps.append({"development": group, **best})
        if amount_gaps:
            gaps.append(max(amount_gaps, key=lambda g: g["ratio"]))
    gaps.sort(key=lambda g: (-(g.get("days") or 0), -(g.get("ratio") or 0)))
    return gaps[:limit]


def prompt_block(gaps: Sequence[Mapping[str, Any]], *, cite: str = "fact IDs") -> str:
    if not gaps:
        return ""
    lines = [f"- [{', '.join(g['fact_ids'])}] {g['development'][:80]}: {g['text']}" for g in gaps]
    return ("COMPUTED GAPS (calculated by code from the dates and amounts in the sources; exact). These are the "
            f"strongest leads: prefer an insight that explains one of them and says what follows. Cite the {cite} "
            "shown. A gap is a fact; why it happened is your inference and must be written as one:\n"
            + "\n".join(lines) + "\n")
