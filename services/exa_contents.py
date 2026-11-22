"""Exa contents fallback for pages the direct fetch cannot read (v13.3, 20 Nov 2026).

Why
---
In RUN-87BF (14 Nov) many publisher pages refused the app's honest user agent (HTTP 403, 429) or
returned a bot wall of 53-133 characters, so good sources were dropped. The app does not spoof a
browser. Instead, when the direct fetch fails, it asks Exa's contents endpoint for the same URL.

Request shape follows Exa's own build-with-exa skill (references/contents.md):
    POST https://api.exa.ai/contents  {"urls": [url], "text": true}
- known URL -> /contents (not /search);
- text, highlights and summary are top-level on /contents; only one is sent. `text` because the
  page replaces a full direct fetch and has to pass the same evidence checks;
- HTTP 200 does not mean the URL succeeded: `statuses` is checked for every call.

Cost control (owner's rule: free credits only, never a paid or unlimited run)
---------------------------------------------------------------------------
- Used only when EXA_API_KEY is set (environment, or EXA_API_KEY=... in the project .env;
  only that one line is read, and the key is never logged).
- Spending is counted in units of $0.001 BEFORE each call (a failed call still counts), at Exa's
  list prices (usagepricing.com, checked against exa.ai/pricing, Jul 2026): contents $1 per 1,000
  pages = 1 unit; search $7 per 1,000 plus its 10 results' highlights = 17 units (rounded up).
- Per run: at most EXA_MAX_CALLS_PER_RUN units (default 600 = $0.60; 0 turns Exa off). v13.13: was 300;
  RUN-FF8F59D2 (20 Nov) used 292 of 300 by mid-run, so later blocked pages and failed searches got no Exa.
- Per calendar month, across ALL runs: at most EXA_MAX_CALLS_PER_MONTH units (default 5,000 = $5),
  counted in data/exa_usage.json, which survives restarts. That is half of the $10 monthly free
  credit, so even a doubled price stays free.
- Once Exa answers 401, 402 or 403 (key wrong, credits used up) or 429 (rate limit), Exa is switched
  off for the rest of that run; nothing is retried.
- No payment method is stored with Exa, so Exa itself blocks requests when credits run out
  (exa.ai/docs/reference/billing) - it cannot charge.
- v13.22 (22 Nov 2026): the per-run and monthly caps are OFF by default. RUN-ACC0E1ED had 33 searches
  refused by the 600-unit run cap while only $1.42 of the free credit had been spent; because Exa cannot
  charge without a payment method, the caps only cost research. Set EXA_MAX_CALLS_PER_RUN or
  EXA_MAX_CALLS_PER_MONTH in .env to bring a cap back; EXA_FALLBACK=0 still turns Exa off.
- EXA_FALLBACK=0 turns it off entirely.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

ENDPOINT = "https://api.exa.ai/contents"
KEY_ENV = "EXA_API_KEY"
CAP_ENV = "EXA_MAX_CALLS_PER_RUN"
MONTH_CAP_ENV = "EXA_MAX_CALLS_PER_MONTH"
DEFAULT_MONTH_CAP = 10 ** 9   # v13.22: no monthly cap (see below)
USAGE_FILE = Path(__file__).resolve().parent.parent / "data" / "exa_usage.json"
STOP_CODES = (401, 402, 403, 429)
SWITCH_ENV = "EXA_FALLBACK"
DEFAULT_CAP = 10 ** 9   # v13.22: no per-run cap (see below)
CONTENTS_UNITS = 1
SEARCH_UNITS = 17
SEARCH_ENDPOINT = "https://api.exa.ai/search"
TIMEOUT_SECONDS = 25.0

_LOCK = threading.Lock()
_STATE: Dict[str, int] = {"used": 0, "failed": 0, "refused": 0, "stopped": 0, "pages": 0, "searches": 0}


class ExaContentsError(Exception):
    """Exa could not return usable text for the URL (never fatal to a run)."""


def _key_from_dotenv() -> str:
    """Read only the EXA_API_KEY line of the project .env (no other line is parsed or kept)."""
    candidates = [Path(__file__).resolve().parent.parent / ".env", Path.cwd() / ".env"]
    seen: set[str] = set()
    for path in candidates:
        if str(path) in seen or not path.is_file():
            continue
        seen.add(str(path))
        try:
            for raw in path.read_text(encoding="utf-8-sig", errors="ignore").splitlines():
                line = raw.strip()
                if line.casefold().startswith("export "):
                    line = line[7:].strip()
                if not line.startswith(KEY_ENV) or "=" not in line:
                    continue
                name, value = line.split("=", 1)
                if name.strip() != KEY_ENV:
                    continue
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                if value.strip():
                    return value.strip()
        except OSError:
            continue
    return ""


def api_key() -> str:
    return (os.getenv(KEY_ENV) or "").strip() or _key_from_dotenv()


def cap() -> int:
    try:
        return max(0, int(os.getenv(CAP_ENV) or DEFAULT_CAP))
    except ValueError:
        return DEFAULT_CAP


def month_cap() -> int:
    try:
        return max(0, int(os.getenv(MONTH_CAP_ENV) or DEFAULT_MONTH_CAP))
    except ValueError:
        return DEFAULT_MONTH_CAP


def _month() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m")


def _read_month_count(path: Path) -> int:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return int(data.get(_month()) or 0)
    except Exception:
        return 0


def _write_month_count(path: Path, count: int) -> None:
    try:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                data = {}
        except Exception:
            data = {}
        data[_month()] = int(count)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def month_used() -> int:
    return _read_month_count(USAGE_FILE)


def enabled() -> bool:
    return (os.getenv(SWITCH_ENV, "1") != "0" and cap() > 0 and month_cap() > 0 and not _STATE["stopped"]
            and bool(api_key()))


def reset_run_budget() -> None:
    """Called at the start of each run so the per-run cap applies per run (the monthly cap does not reset)."""
    with _LOCK:
        _STATE.update(used=0, failed=0, refused=0, stopped=0, pages=0, searches=0)


def usage() -> Dict[str, int]:
    with _LOCK:
        return dict(_STATE, cap=cap(), month_used=_read_month_count(USAGE_FILE), month_cap=month_cap())


def _take_call(units: int = CONTENTS_UNITS) -> bool:
    """Count the call's cost BEFORE it is made (a call that fails still counts), against both caps."""
    with _LOCK:
        month = _read_month_count(USAGE_FILE)
        if _STATE["used"] + units > cap() or month + units > month_cap():
            _STATE["refused"] += 1
            if _STATE["refused"] == 1:
                logger.warning("Exa budget reached (run %d/%d units, month %d/%d); no more Exa calls this run",
                               _STATE["used"], cap(), month, month_cap())
            return False
        _STATE["used"] += units
        _write_month_count(USAGE_FILE, month + units)
        return True


def _post(payload: Dict[str, Any], key: str, timeout: float, endpoint: str = ENDPOINT) -> Dict[str, Any]:
    request = urllib.request.Request(
        endpoint, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Content-Type": "application/json", "x-api-key": key})
    with urllib.request.urlopen(request, timeout=timeout) as response:   # noqa: S310 - fixed https endpoint
        return json.loads(response.read().decode("utf-8"))


def fetch_text(url: str, *, timeout: float = TIMEOUT_SECONDS, post=None) -> Optional[Dict[str, Any]]:
    """Page text for `url` from Exa, or None when Exa is off or the per-run cap is used.

    Raises ExaContentsError when Exa was called but returned no usable text for this URL.
    `post` is for tests (no network): post(payload, key, timeout) -> response dict."""
    if not enabled():
        return None
    if not _take_call():
        return None
    payload = {"urls": [url], "text": True}
    try:
        data = (post or _post)(payload, api_key(), timeout)
    except urllib.error.HTTPError as error:
        with _LOCK:
            _STATE["failed"] += 1
            if error.code in STOP_CODES:
                _STATE["stopped"] = 1     # key wrong, credits used up or rate-limited: no more calls this run
                logger.warning("Exa answered HTTP %s; Exa fallback off for the rest of this run", error.code)
        raise ExaContentsError(f"Exa contents HTTP {error.code}") from None
    except Exception as error:
        with _LOCK:
            _STATE["failed"] += 1
        raise ExaContentsError(f"Exa contents failed: {type(error).__name__}") from None
    statuses = [s for s in (data.get("statuses") or []) if isinstance(s, dict)]
    failed = [s for s in statuses if str(s.get("status") or "").casefold() not in ("", "success")]
    results = [r for r in (data.get("results") or []) if isinstance(r, dict)]
    text = " ".join(str((results[0] if results else {}).get("text") or "").split())
    if failed or not text:
        with _LOCK:
            _STATE["failed"] += 1
        reason = (failed[0].get("error") or failed[0].get("status")) if failed else "no text"
        if isinstance(reason, dict):
            reason = reason.get("tag") or reason.get("message") or "error"
        logger.info("exa_contents page_failed url=%s reason=%s", url[:160], str(reason)[:80])
        raise ExaContentsError(f"Exa contents: {reason}")
    first = results[0]
    with _LOCK:
        _STATE["pages"] += 1
    logger.info("exa_contents page_read url=%s chars=%d run_units=%d", url[:160], len(text), _STATE["used"])
    return {"url": str(first.get("url") or url), "text": text, "title": str(first.get("title") or ""),
            "published_at": str(first.get("publishedDate") or ""), "author": str(first.get("author") or "")}


SEARCH_OBJECTIVE = ("Find news reports and primary documents (filings, regulator or company statements) with facts "
                    "about this question. Rank original reporting and official sources first; exclude opinion "
                    "roundups, listicles and SEO pages. Pull dates, named actors, amounts and concrete actions.")


def search(query: str, *, timeout: float = TIMEOUT_SECONDS, post=None) -> Optional[list]:
    """v13.4 (20 Nov 2026): Exa search when the free news providers fail or find nothing - in RUN-87BF
    GDELT and NewsAPI were rate-limited (RATE_LIMITED, WebSearchRateLimitError), so counterevidence and
    precedent searches came back empty. Request as Exa's build-with-exa skill recommends for one step of
    a pipeline: query, type auto, contents.highlights true, and an objective; nothing else.

    Returns results in the app's search shape, or None when Exa is off or the budget is used."""
    query = " ".join(str(query or "").split())
    if not query or not enabled() or not _take_call(SEARCH_UNITS):
        return None
    payload = {"query": query, "type": "auto", "objective": SEARCH_OBJECTIVE, "contents": {"highlights": True}}
    try:
        if post is not None:
            data = post(payload, api_key(), timeout)
        else:
            data = _post(payload, api_key(), timeout, SEARCH_ENDPOINT)
    except urllib.error.HTTPError as error:
        with _LOCK:
            _STATE["failed"] += 1
            if error.code in STOP_CODES:
                _STATE["stopped"] = 1
                logger.warning("Exa answered HTTP %s; Exa off for the rest of this run", error.code)
        raise ExaContentsError(f"Exa search HTTP {error.code}") from None
    except Exception as error:
        with _LOCK:
            _STATE["failed"] += 1
        raise ExaContentsError(f"Exa search failed: {type(error).__name__}") from None
    with _LOCK:
        _STATE["searches"] += 1
    logger.info("exa_search query=%r results=%d run_units=%d", query[:120], len(data.get("results") or []),
                _STATE["used"])
    out = []
    for rank, row in enumerate([r for r in (data.get("results") or []) if isinstance(r, dict)], start=1):
        url = str(row.get("url") or "")
        if not url.startswith(("http://", "https://")):
            continue
        highlights = [" ".join(str(h).split()) for h in (row.get("highlights") or []) if str(h).strip()]
        out.append({"url": url, "title": str(row.get("title") or ""), "published_at": str(row.get("publishedDate") or ""),
                    "snippet": " ... ".join(highlights)[:1200], "provider": "exa_search", "rank": rank})
    return out
