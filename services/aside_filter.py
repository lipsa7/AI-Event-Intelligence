"""Recognise page furniture extracted as if it were an event (v6.20.4).

Measured on this app's own admitted events: about 6% were not events at all,
but photo captions ("WASHINGTON, DC - SEPTEMBER 29: U.S. President Donald Trump
waves…", "From left: …", "(Photo by … Getty Images)"), promotional or
navigation text ("Read more: …", "5 min read", "(* Disclosure below.)",
"… spoke with theCUBE Research's … during an exclusive broadcast"), or author
bios. Boilerplate removal before analysis is standard practice in news
pipelines (e.g. boilerplate/"main content" extraction, Kohlschütter et al.,
WSDM 2010); these are the leftovers that survived page cleaning.

aside_reason() returns a short code when the event text is furniture, else "".
strip_furniture() removes credit prefixes and navigation tails from text whose
remainder is real content ("(Kent Nishimura/AFP via Getty Images) · Amodei
published an essay…" -> "Amodei published an essay…").
"""

from __future__ import annotations

import re
from typing import Any, Mapping

VERSION = "aside-filter-v1"

_CREDIT_PREFIX = re.compile(
    r"^\s*(?:\((?:photo by|image:|credit:)?[^)]{0,80}(?:getty images|afp|reuters|ap photo|bloomberg|"
    r"shutterstock|via getty)[^)]{0,40}\)\s*(?:[|·•-]\s*)?)+"
    r"(?:image credits?:[^|·•]{0,80}?(?:getty images|afp|reuters)\s*)?"
    r"(?:[A-Z][A-Z .'/-]{3,60}via getty images\s*)?",
    re.IGNORECASE,
)
_LEADING_LABELS = re.compile(r"^\s*(?:read the rest|read more|related|also read|watch|listen)\s*:?\s+", re.IGNORECASE)
_SPEAKER_LABEL = re.compile(r"^\s*[A-Z][A-Z .'-]{3,40}\s*:\s+(?=[A-Z])")
_NAV_TAIL = re.compile(r"\s+(?:world\s+politics\s+tech\s+business|subscribe\s+now|sign\s+up\s+for).*$", re.IGNORECASE)

_CAPTION_DATELINE = re.compile(r"^[A-Z][A-Z .,'-]{2,40}\s+[-–—]\s+(?:JANUARY|FEBRUARY|MARCH|APRIL|MAY|JUNE|JULY|AUGUST|"
                               r"SEPTEMBER|OCTOBER|NOVEMBER|DECEMBER)\s+\d{1,2}\s*:")
_CAPTION_VERBS = re.compile(
    r"^(?:[A-Z][\w.'-]+(?:\s+[A-Z][\w.'-]+){0,4})(?:,[^,]{3,80},)?\s+"
    r"(?:speaks|looks on|looks to|waves|listens|walks|poses|arrives|gestures|smiles|stands|sits|holds up|"
    r"departs|attends|is seen|are seen)\b"
)
_PATTERNS = (
    ("photo_caption", re.compile(r"^\s*from left\s*:|\((?:left|right|center)\)", re.IGNORECASE)),
    ("photo_credit", re.compile(r"\(photo by|image credits?:|/getty images\)|via getty images", re.IGNORECASE)),
    ("promotion", re.compile(r"\b\d+\s+min(?:ute)?\s+read\b|\(\*\s*disclosure below\.?\)|exclusive broadcast on thecube|"
                             r"spoke with thecube research|\bsign up (?:for|to)\b|\bsubscribe (?:to|now)\b|"
                             r"\bwill (?:discuss|be discussed) (?:these|this|the)\b.*\b(?:summit|conference|event)\b",
                             re.IGNORECASE)),
    ("author_bio", re.compile(r"\bis (?:the )?[A-Z][\w ]{2,40}'s (?:executive |senior |managing |deputy )?"
                              r"(?:editor|reporter|correspondent|writer|columnist)\b")),
    ("related_link", re.compile(r"^\s*read more\s*:", re.IGNORECASE)),
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def strip_furniture(text: Any) -> str:
    out = _clean(text)
    for _ in range(3):
        before = out
        out = _CREDIT_PREFIX.sub("", out).strip(" |·•-")
        out = _LEADING_LABELS.sub("", out)
        out = _SPEAKER_LABEL.sub("", out)
        out = _NAV_TAIL.sub("", out)
        if out == before:
            break
    return out if len(out) >= 20 else _clean(text)


def aside_reason(event: Mapping[str, Any]) -> str:
    title = _clean(event.get("title") or event.get("summary"))
    if not title:
        return ""
    for code, pattern in _PATTERNS:
        if code == "photo_credit":
            # A credit followed by real content is content: judge the remainder.
            if pattern.search(title):
                rest = strip_furniture(title)
                if rest == title or len(rest) < 40 or _CAPTION_DATELINE.search(rest) or _CAPTION_VERBS.search(rest):
                    return code
            continue
        if pattern.search(title):
            return code
    if re.search(r"\bwill discuss\b", title, re.I) and re.search(r"\b(summit|conference|festival|expo)\b", title, re.I):
        return "promotion"
    stripped = strip_furniture(title)
    if _CAPTION_DATELINE.search(stripped):
        return "photo_caption"
    if _CAPTION_VERBS.search(stripped):
        return "photo_caption"
    return ""
