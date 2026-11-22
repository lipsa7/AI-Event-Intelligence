"""Numbers and dates the news leaves out, from primary sources (v13.24, 22 Nov 2026).

Why
---
Research report of 22 Nov ("Making AI Event Intelligence's insights match the best original-analysis
publications", Q4/stage B): the app's insights read as cheap mainly because the generator works from
news, not numbers; a variant view usually comes from a number the narrative ignores - a comment-period
deadline, a filed obligation, a docket date (Papic: constraints are measurable; these sources are where
they are measured). RUN-ACC0E1ED had 2 computed gaps across 49 events and forecasts 9-16 months out,
because nothing in the evidence carried a near, checkable date.

Sources (documented, free):
* Federal Register API v1 - federalregister.gov/api/v1, no key. Rules and notices with publication,
  comment-close and effective dates: natural 2-12 week resolution dates.
* SEC EDGAR full-text search - efts.sec.gov/LATEST/search-index, no key, a declared User-Agent is
  required, at most 10 requests a second. Which company filed which form on which date about the story.
* CourtListener v4 search - courtlistener.com/api/rest/v4/search, only when COURTLISTENER_API_TOKEN is set
  (token auth; low default rate limits since 7 May 2026). Docket number, court and filing date.

Every record states only what the API returned (title, agency or company, form, dates, URL); nothing
is inferred. Records are citable context (gap_type "numbers"), never observed events. Any failure
returns no records - the run continues on news.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import urlencode

VERSION = "primary-data-v1"
TIMEOUT_SECONDS = 12
PER_SOURCE = 4
USER_AGENT_ENV = "SEC_USER_AGENT"
DEFAULT_USER_AGENT = "AI-Event-Intelligence research prototype (contact: set SEC_USER_AGENT in .env)"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _get_json(url: str, headers: Mapping[str, str] | None = None) -> Any:
    import requests
    response = requests.get(url, headers=dict(headers or {}), timeout=TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


def federal_register(query: str, fetch: Callable[..., Any] = _get_json, today: datetime | None = None) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc)
    params = [("conditions[term]", query), ("order", "newest"), ("per_page", str(PER_SOURCE)),
              ("conditions[publication_date][gte]", (today - timedelta(days=365)).strftime("%Y-%m-%d"))]
    for field in ("title", "type", "publication_date", "effective_on", "comments_close_on", "html_url", "agencies"):
        params.append(("fields[]", field))
    data = fetch("https://www.federalregister.gov/api/v1/documents.json?" + urlencode(params))
    out = []
    for doc in (data or {}).get("results") or []:
        title, url = _clean(doc.get("title")), _clean(doc.get("html_url"))
        if not title or not url:
            continue
        agencies = ", ".join(_clean(a.get("name")) for a in doc.get("agencies") or [] if isinstance(a, Mapping) and a.get("name"))
        parts = [f"Federal Register {(_clean(doc.get('type')) or 'document').lower()} \"{title}\""
                 + (f" by {agencies}" if agencies else "") + f", published {_clean(doc.get('publication_date'))}"]
        if doc.get("comments_close_on"):
            parts.append(f"comments close {_clean(doc.get('comments_close_on'))}")
        if doc.get("effective_on"):
            parts.append(f"effective {_clean(doc.get('effective_on'))}")
        out.append({"source": "federal_register", "url": url, "text": "; ".join(parts) + ".",
                    "date": _clean(doc.get("publication_date"))})
    return out


def sec_filings(query: str, fetch: Callable[..., Any] = _get_json, today: datetime | None = None) -> list[dict[str, Any]]:
    today = today or datetime.now(timezone.utc)
    params = {"q": f"\"{query}\"", "dateRange": "custom", "forms": "8-K,10-Q,10-K,S-1,DEF 14A",
              "startdt": (today - timedelta(days=180)).strftime("%Y-%m-%d"), "enddt": today.strftime("%Y-%m-%d")}
    data = fetch("https://efts.sec.gov/LATEST/search-index?" + urlencode(params),
                 headers={"User-Agent": os.getenv(USER_AGENT_ENV) or DEFAULT_USER_AGENT})
    out = []
    for hit in ((data or {}).get("hits") or {}).get("hits") or []:
        src = hit.get("_source") or {}
        names = [_clean(n) for n in src.get("display_names") or [] if _clean(n)]
        form, date, adsh = _clean(src.get("form")), _clean(src.get("file_date")), _clean(src.get("adsh"))
        ciks = [c for c in src.get("ciks") or [] if c]
        if not names or not form or not date or not adsh or not ciks:
            continue
        url = (f"https://www.sec.gov/Archives/edgar/data/{int(ciks[0])}/{adsh.replace('-', '')}/{adsh}-index.htm")
        out.append({"source": "sec_edgar", "url": url, "date": date,
                    "text": f"SEC EDGAR: {names[0]} filed form {form} on {date} that mentions \"{query}\"."})
        if len(out) >= PER_SOURCE:
            break
    return out


def court_dockets(query: str, fetch: Callable[..., Any] = _get_json) -> list[dict[str, Any]]:
    token = _clean(os.getenv("COURTLISTENER_API_TOKEN"))
    if not token:
        return []
    params = {"q": query, "type": "r", "order_by": "dateFiled desc"}
    data = fetch("https://www.courtlistener.com/api/rest/v4/search/?" + urlencode(params),
                 headers={"Authorization": f"Token {token}"})
    out = []
    for row in (data or {}).get("results") or []:
        case, filed = _clean(row.get("caseName")), _clean(row.get("dateFiled"))
        path = _clean(row.get("docket_absolute_url") or row.get("absolute_url"))
        if not case or not filed or not path:
            continue
        number, court = _clean(row.get("docketNumber")), _clean(row.get("court"))
        out.append({"source": "courtlistener", "url": "https://www.courtlistener.com" + path, "date": filed,
                    "text": f"Court docket: {case}" + (f", No. {number}" if number else "")
                            + (f", {court}" if court else "") + f", filed {filed}."})
        if len(out) >= PER_SOURCE:
            break
    return out


SOURCES = {"federal_register": federal_register, "sec": sec_filings, "court": court_dockets}


def collect(terms: Mapping[str, Any], fetch: Callable[..., Any] = _get_json) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """terms: {"federal_register": "...", "sec": "...", "court": "..."} -> (rows, receipt)."""
    rows: list[dict[str, Any]] = []
    receipt: dict[str, Any] = {}
    if os.getenv("EVENT_INTELLIGENCE_PRIMARY_DATA", "1").strip() in {"0", "false", "no"}:
        return rows, {"status": "off"}
    for key, function in SOURCES.items():
        query = _clean(terms.get(key))[:120]
        if not query:
            receipt[key] = "no_query"
            continue
        try:
            found = function(query, fetch=fetch)
            rows += found
            receipt[key] = {"query": query, "found": len(found)}
        except Exception as error:
            receipt[key] = {"query": query, "error": type(error).__name__}
    return rows, receipt


_BOILERPLATE = frozenset("""
federal register rule rules proposed final notice notices document documents published publication comments comment
close closes effective agency department office commission bureau administration service national united states
filing filings form forms report annual quarterly current docket dockets court case cases search results the and for
with from that this into under about their other which while been were have has its are was
""".split())


def _words(text: Any) -> set[str]:
    return {w for w in "".join(c.casefold() if c.isalnum() else " " for c in _clean(text)).split()
            if len(w) > 2 and not w.isdigit() and w not in _BOILERPLATE}


def relevant(rows: Sequence[Mapping[str, Any]], story_text: str, corpus: Sequence[str] = (),
             common_share: float = 0.25) -> tuple[list[dict[str, Any]], list[str]]:
    """v13.28.3: keep a primary record only if it is about THIS story. On 22 Nov the Federal Register
    search for California's lawsuit against OpenAI ran on "EU AI Act implementation regulation" (the
    search words come from a model call) and its 4 unrelated US notices were added to the story's evidence,
    where they counted as rules an insight could build on. A record is kept when its text shares at least
    two words with the story and at least one of them is distinctive (in at most a quarter of the run's
    records - "openai" or "artificial" in an AI run is not). Returns (kept, dropped texts)."""
    story = _words(story_text)
    docs = [_words(t) for t in corpus if _clean(t)]
    def common(word: str) -> bool:
        return bool(docs) and sum(1 for d in docs if word in d) > common_share * len(docs)
    kept, dropped = [], []
    for row in rows:
        shared = _words(row.get("text")) & story
        if len(shared) >= 2 and any(not common(w) for w in shared):
            kept.append(dict(row))
        else:
            dropped.append(_clean(row.get("text"))[:160])
    return kept, dropped


def records(rows: Sequence[Mapping[str, Any]], title: str) -> list[dict[str, Any]]:
    """Citable context records (same shape as services/pattern_grid.pattern_records)."""
    out = []
    seen: set[str] = set()
    for row in rows:
        text = _clean(row.get("text"))
        if not text or text in seen:
            continue
        seen.add(text)
        eid = "EVID-PRIMARY-" + hashlib.sha256((title + text).encode("utf-8")).hexdigest()[:20].upper()
        out.append({
            "evidence_id": eid, "source_id": "PRIMARY-" + _clean(row.get("source")).upper(), "event_id": "",
            "evidence_type": "primary_record", "evidence_role": "feasibility_evidence",
            "text": text, "source_excerpt": text, "claim": text, "source_url": _clean(row.get("url")),
            "published_at": _clean(row.get("date")), "publication_date_basis": "primary_source_api_field",
            "analytical_context_only": True, "direct_event_evidence": False,
            "gap_type": "numbers", "gap_for": title, "admission_status": "admitted", "claim_status": "reported_claim",
            "structured_payload": {"analytical_context_only": True, "retrieved_by": VERSION,
                                   "source_api": _clean(row.get("source")),
                                   "prohibited_uses": ["observed_event_count", "acting_actor_count"]},
        })
    return out
