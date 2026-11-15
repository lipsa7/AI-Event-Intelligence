"""Count independent confirmation by where a report ORIGINATED, not by outlet (12 Nov 2026).

Basis: AP's standards require crediting the originating news organisation for material AP did
not gather itself; Pew ("How News Happens", 2010) found 83% of stories on major storylines
carried no new information; the Admiralty code's "confirmed" needs independent sources. So
"SiliconANGLE, citing The New York Times" is ONE source (the Times), and it is the same source
as a New York Times article on the same point.

Only named NEWS organisations count as an origin: "according to Google" attributes a claim to an
actor, which is a primary statement, not second-hand reporting.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

OUTLETS = {
    "nyt": ("new york times", "nytimes", "nyt"), "wsj": ("wall street journal", "wsj"),
    "ft": ("financial times",), "reuters": ("reuters",), "bloomberg": ("bloomberg",),
    "ap": ("associated press",), "washingtonpost": ("washington post",),
    "theinformation": ("the information",), "axios": ("axios",), "politico": ("politico",),
    "cnbc": ("cnbc",), "guardian": ("the guardian",), "bbc": ("bbc",), "techcrunch": ("techcrunch",),
    "theverge": ("the verge",), "wired": ("wired",), "semafor": ("semafor",), "cnn": ("cnn",),
    "economist": ("the economist",), "nikkei": ("nikkei",), "forbes": ("forbes",),
    "businessinsider": ("business insider",), "404media": ("404 media",), "theatlantic": ("the atlantic",),
}
DOMAINS = {"nytimes.com": "nyt", "wsj.com": "wsj", "ft.com": "ft", "reuters.com": "reuters",
           "bloomberg.com": "bloomberg", "apnews.com": "ap", "washingtonpost.com": "washingtonpost",
           "theinformation.com": "theinformation", "axios.com": "axios", "politico.com": "politico",
           "cnbc.com": "cnbc", "theguardian.com": "guardian", "bbc.com": "bbc", "bbc.co.uk": "bbc",
           "techcrunch.com": "techcrunch", "theverge.com": "theverge", "wired.com": "wired",
           "semafor.com": "semafor", "cnn.com": "cnn", "economist.com": "economist",
           "nikkei.com": "nikkei", "forbes.com": "forbes", "businessinsider.com": "businessinsider",
           "404media.co": "404media", "theatlantic.com": "theatlantic"}
_NAMES = "|".join(sorted((re.escape(n) for names in OUTLETS.values() for n in names), key=len, reverse=True))
_ATTRIBUTION = re.compile(
    r"(?:according to|citing|cited by|as reported (?:by|in)|reported by|first reported by|"
    r"a report (?:in|by|from)|reporting (?:by|from)|per)\s+(?:a\s+|an\s+|the\s+)?(?:report (?:in|by|from)\s+)?"
    r"(?:the\s+)?(" + _NAMES + r")\b"
    r"|\b(?:the\s+)?(" + _NAMES + r")(?:'s)?\s+(?:reported|first reported|reports|report said|has reported)\b",
    re.I)
_BY_NAME = {name: key for key, names in OUTLETS.items() for name in names}


def cited_origin(text: Any) -> str:
    """The news organisation a passage attributes its information to, or ''."""
    match = _ATTRIBUTION.search(str(text or ""))
    if not match:
        return ""
    return _BY_NAME.get((match.group(1) or match.group(2) or "").casefold(), "")


def publisher_origin(publisher: Any) -> str:
    domain = str(publisher or "").casefold().removeprefix("www.")
    return next((key for suffix, key in DOMAINS.items() if domain == suffix or domain.endswith("." + suffix)), "")


def origin_sources(evidence: Sequence[Mapping[str, Any]],
                   publishers: Mapping[str, str] | None = None) -> dict[str, str]:
    """evidence_id -> source key for independence counting: 'ORIGIN:<outlet>' when the record
    cites a news organisation or is published by one, else its own source_id."""
    out: dict[str, str] = {}
    for record in evidence or []:
        if not isinstance(record, Mapping):
            continue
        eid = str(record.get("evidence_id") or "").strip()
        if not eid:
            continue
        origin = cited_origin(record.get("source_excerpt") or record.get("text")) \
            or publisher_origin((publishers or {}).get(eid) or (publishers or {}).get(eid.upper()))
        out[eid] = f"ORIGIN:{origin}" if origin else str(record.get("source_id") or "").strip()
    return out
