"""Event dates (v6.20.4).

The extractor stores no date on events (event_start_at was always empty), so
every date-based check in the pipeline was silently inactive. This module gives
each event:

- published_at: the article's publication date (the standard time signal in
  news clustering / topic detection and tracking);
- event_start_at: the date the event happened, ONLY when the source text pins
  it to a day ("Sept. 22", "Tuesday", "yesterday", "2 days ago"), resolved
  relative to the publication date, the way temporal taggers anchor relative
  expressions to the document creation time (TimeML/TempEval; HeidelTime);
- event_time_resolved / event_date_precision: the same at month or year
  precision when that is all the text gives ("in July", "last month", "2025").

Vague phrases ("recently", "this summer", "in the coming years") stay unresolved.
Nothing is guessed beyond what the text states.
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any, Mapping

VERSION = "event-dates-v1"

MONTHS = {
    "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3, "apr": 4, "april": 4,
    "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7, "aug": 8, "august": 8, "sep": 9, "sept": 9,
    "september": 9, "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
}
WEEKDAYS = {"monday": 0, "tuesday": 1, "wednesday": 2, "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6}
_MONTH = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
_YEAR = r"((?:19|20)\d{2})"
FUTURE = {"future", "planned", "scheduled", "expected"}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def parse_publication_date(value: Any) -> date | None:
    text = _clean(value)
    if not text:
        return None
    match = re.search(r"((?:19|20)\d{2})-(\d{1,2})-(\d{1,2})", text)
    if match:
        try:
            return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            return None
    try:  # RFC 2822 dates from RSS feeds
        from email.utils import parsedate_to_datetime
        return parsedate_to_datetime(text).date()
    except (TypeError, ValueError, IndexError):
        pass
    for fmt in ("%d %B %Y", "%B %d, %Y", "%b %d, %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _month(word: str) -> int | None:
    return MONTHS.get(word.casefold().rstrip("."))


def _safe(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def resolve(time_text: Any, published: date | None, temporal_status: Any = "") -> tuple[str, str]:
    """(resolved value, precision) with precision in {"day", "month", "year", ""}.

    "day" values are ISO dates; "month" values are YYYY-MM; "year" values YYYY.
    """
    text = _clean(time_text).casefold()
    if not text:
        return "", ""
    future = _clean(temporal_status).casefold() in FUTURE

    match = re.search(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})", text)
    if match:
        value = _safe(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        return (value.isoformat(), "day") if value else ("", "")

    # "September 22, 2026", "Sept. 22", "Oct 7", "Tuesday, Sept. 30, 2025"
    match = re.search(_MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?![\d-])(?:,?\s+" + _YEAR + r")?", text)
    if not match:
        # "22 September", "12 September 2026", "OCTOBER 01 2026" handled above; "2 August this year"
        match2 = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"(?:,?\s+" + _YEAR + r")?", text)
        if match2:
            day, month, year = int(match2.group(1)), _month(match2.group(2)), match2.group(3)
            return _day_value(day, month, year, published, future)
    else:
        month, day, year = _month(match.group(1)), int(match.group(2)), match.group(3)
        return _day_value(day, month, year, published, future)

    if published is not None:
        if re.search(r"\b(today|tonight|this morning|this afternoon|this evening|earlier in the day)\b", text) \
                or re.fullmatch(r"(right )?now", text):
            return published.isoformat(), "day"
        if re.search(r"\byesterday\b", text):
            return (published - timedelta(days=1)).isoformat(), "day"
        match = re.search(r"\b(\d+|one|two|three|four|five|six|seven)\s+days?\s+ago\b", text)
        if match:
            words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7}
            n = int(match.group(1)) if match.group(1).isdigit() else words[match.group(1)]
            return (published - timedelta(days=n)).isoformat(), "day"
        match = re.search(r"\b(last\s+)?(monday|tuesday|wednesday|thursday|friday|saturday|sunday)\b", text)
        if match and not re.search(r"\b(next|every|each)\s+(monday|tuesday|wednesday|thursday|friday|saturday|sunday)", text):
            target = WEEKDAYS[match.group(2)]
            if future:  # "Sunday night" in a Sunday article is that night
                delta = (target - published.weekday()) % 7
                return (published + timedelta(days=delta)).isoformat(), "day"
            delta = (published.weekday() - target) % 7
            if match.group(1) and delta == 0:
                delta = 7
            return (published - timedelta(days=delta)).isoformat(), "day"
        if re.search(r"\b(earlier this month|this month|early this month)\b", text):
            return f"{published.year}-{published.month:02d}", "month"
        if re.search(r"\b(late last month|last month|earlier last month)\b", text):
            first = published.replace(day=1) - timedelta(days=1)
            return f"{first.year}-{first.month:02d}", "month"

    match = re.search(_MONTH + r"\s+" + _YEAR, text)
    if match:
        return f"{int(match.group(2))}-{_month(match.group(1)):02d}", "month"
    # A bare month name ("July", "Earlier in April", "this past June", "June of this year").
    match = re.search(r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b", text)
    if match and published is not None and not re.search(r"\b(next|last)\s+year\b", text):
        month = MONTHS[match.group(1)]
        year = published.year
        if not future and month > published.month:
            year -= 1
        if future and month < published.month:
            year += 1
        return f"{year}-{month:02d}", "month"
    match = re.fullmatch(r"(?:in\s+|late\s+|early\s+|mid-?|the\s+)?" + _YEAR, text)
    if match:
        return match.group(1), "year"
    return "", ""


def _day_value(day: int, month: int | None, year: str | None, published: date | None,
               future: bool) -> tuple[str, str]:
    if not month:
        return "", ""
    if year:
        value = _safe(int(year), month, day)
        return (value.isoformat(), "day") if value else ("", "")
    if published is None:
        return "", ""
    value = _safe(published.year, month, day)
    if value is None:
        return "", ""
    # Without a year, a past-tense date after publication refers to last year,
    # a future-tense date before publication to next year.
    if not future and value > published + timedelta(days=2):
        value = _safe(published.year - 1, month, day) or value
    elif future and value < published - timedelta(days=2):
        value = _safe(published.year + 1, month, day) or value
    return value.isoformat(), "day"


def date_event(event: Mapping[str, Any], published_at: Any) -> dict[str, Any]:
    """A copy of the event with published_at / event_start_at / precision filled."""
    out = dict(event)
    published = parse_publication_date(published_at)
    if published is not None and not _clean(out.get("published_at")):
        out["published_at"] = published.isoformat()
    published = published or parse_publication_date(out.get("published_at"))
    value, precision = resolve(out.get("event_time_text"), published, out.get("temporal_status"))
    if precision:
        out["event_time_resolved"] = value
        out["event_date_precision"] = precision
    if precision == "day" and not _clean(out.get("event_start_at")):
        out["event_start_at"] = value
        out["temporal_precision"] = "day"
    out["event_dates_version"] = VERSION
    return out
