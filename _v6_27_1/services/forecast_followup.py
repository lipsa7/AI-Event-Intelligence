"""Forecast follow-up (v6.20.6).

Every checked forward-looking implication is recorded with an observable signal
and a deadline. Later Deep runs compare only genuinely newer sources against that
signal. Resolution is conservative:

- a forecast never confirms itself through another excerpt from one of its
  original source documents;
- only sources published after the forecast was made are eligible;
- observed/contradicted require a completed grounding decision;
- provider/grounding failures and checks deferred by the per-run call cap leave
  the forecast open, even after the nominal deadline;
- an expired forecast is recorded only when its deadline has passed and there is
  no unresolved relevant candidate that still needs a check.

Storage: data/forecasts/watch_items.sqlite3 (override with
EVENT_INTELLIGENCE_WATCH_ITEMS). Replays never write to it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, Sequence
from urllib.parse import urlsplit, urlunsplit

VERSION = "forecast-followup-v2"
DEFAULT_HORIZON_DAYS = 180
MAX_CHECKS_PER_RUN = 8
MAX_CANDIDATES = 3
MIN_SHARED_TERMS = 2

STOP = frozenset("""
a an the and or but if of on in to for with by at from as is are was were be been being will would could
should may might can that this these those it its their there here more most other into over under about
than then also any some such new report reports reported says said company companies firm firms market
markets ai artificial intelligence move moves start starts begin begins other others more less
""".split())

_TOPIC_FUNCTION_WORDS = frozenset("""
a an the and or but if of on in to for with by at from as is are was were be been being will would could
should may might can that this these those it its their there here more most other into over under about
than then also any some such new report reports reported says said company companies firm firms move
moves start starts begin begins other others more less
""".split())


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _terms(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9-]{2,}", _clean(text).casefold())
    out = set()
    for original in words:
        w = original
        if w in STOP or w.isdigit():
            continue
        for suffix in ("ing", "ed", "es", "s"):
            if len(w) > len(suffix) + 3 and w.endswith(suffix):
                w = w[: -len(suffix)]
                break
        out.add(w)
    return out


def _topic_terms(text: str) -> set[str]:
    """Terms for topic scoping.

    Forecast search terms intentionally treat common domain words such as
    ``ai/artificial/intelligence`` as noise, but using that same list for topic
    scoping made ``artificial intelligence`` an empty topic and therefore match
    every stored forecast. Topic scoping retains domain words and normalizes the
    common AI alias in both directions.
    """
    normalized = _clean(text).casefold()
    raw = {
        word
        for word in re.findall(r"[a-z0-9][a-z0-9-]{1,}", normalized)
        if word not in _TOPIC_FUNCTION_WORDS and not word.isdigit()
    }
    if re.search(r"\bai\b", normalized) or "artificial intelligence" in normalized:
        raw.update({"ai", "artificial", "intelligence"})
    return raw


def _citation_rows(raw: Any) -> list[Mapping[str, Any]]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw or "[]")
        except (TypeError, ValueError, json.JSONDecodeError):
            return []
    if not isinstance(raw, (list, tuple)):
        return []
    return [row for row in raw if isinstance(row, Mapping)]


def _canonical_url(value: Any) -> str:
    url = _clean(value)
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        parts = urlsplit(url)
        # Tracking/query fragments are not identity for self-confirmation.
        return urlunsplit((parts.scheme.casefold(), parts.netloc.casefold(), parts.path.rstrip("/"), "", ""))
    except Exception:
        return ""


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _source_id(record: Mapping[str, Any]) -> str:
    return _clean(record.get("source_id") or _mapping(record.get("metadata")).get("source_id")).upper()


def _source_url(record: Mapping[str, Any]) -> str:
    metadata = _mapping(record.get("metadata"))
    discovery = _mapping(record.get("discovery_metadata"))
    result = _mapping(discovery.get("search_result"))
    for value in (
        record.get("source_url"), record.get("url"), record.get("canonical_url"), record.get("final_url"),
        metadata.get("source_url"), metadata.get("url"), metadata.get("canonical_url"), metadata.get("final_url"),
        result.get("url"), result.get("canonical_url"),
    ):
        url = _canonical_url(value)
        if url:
            return url
    return ""


def _source_label(record: Mapping[str, Any]) -> str:
    metadata = _mapping(record.get("metadata"))
    discovery = _mapping(record.get("discovery_metadata"))
    result = _mapping(discovery.get("search_result"))
    for value in (
        record.get("source_name"), record.get("publisher"), record.get("publication"),
        metadata.get("source_name"), metadata.get("publisher"), metadata.get("publication"),
        result.get("source_name"), result.get("publisher"), result.get("domain"),
        record.get("title"), metadata.get("title"), result.get("title"),
    ):
        label = _clean(value)
        if label:
            return label[:160]
    return _source_id(record)


def horizon_until(horizon: Any, made_on: date) -> tuple[date, bool]:
    """Deadline for a horizon phrase; (date, parsed?). Unparsed -> 180 days."""
    text = _clean(horizon).casefold()
    if not text:
        return made_on + timedelta(days=DEFAULT_HORIZON_DAYS), False
    match = re.search(r"by (?:the )?end of (?:(q[1-4])\s+)?((?:19|20)\d{2})", text)
    if match:
        year = int(match.group(2))
        if match.group(1):
            month = int(match.group(1)[1]) * 3
            return (date(year, month, 1) + timedelta(days=31)).replace(day=1) - timedelta(days=1), True
        return date(year, 12, 31), True
    match = re.search(r"\b((?:19|20)\d{2})\b", text)
    if match and int(match.group(1)) >= made_on.year:
        return date(int(match.group(1)), 12, 31), True
    units = {"day": 1, "week": 7, "month": 30, "quarter": 92, "year": 365}
    numbers = {"a": 1, "one": 1, "two": 2, "three": 3, "four": 4, "six": 6, "nine": 9, "twelve": 12, "few": 3,
               "several": 6, "next": 1, "coming": 1}
    match = re.search(r"(\d+|a|one|two|three|four|six|nine|twelve|few|several|next|coming)"
                      r"(?:\s*(?:-|to|–)\s*(\d+))?\s*(day|week|month|quarter|year)s?", text)
    if match:
        low = int(match.group(1)) if match.group(1).isdigit() else numbers[match.group(1)]
        high = int(match.group(2)) if match.group(2) else low
        return made_on + timedelta(days=max(low, high) * units[match.group(3)]), True
    for phrase, days in (("near term", 90), ("near-term", 90), ("short term", 90), ("short-term", 90),
                         ("medium term", 365), ("medium-term", 365), ("long term", 1095), ("long-term", 1095),
                         ("this year", (date(made_on.year, 12, 31) - made_on).days),
                         ("next year", (date(made_on.year + 1, 12, 31) - made_on).days)):
        if phrase in text:
            return made_on + timedelta(days=max(days, 1)), True
    return made_on + timedelta(days=DEFAULT_HORIZON_DAYS), False


class ForecastFollowup:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        configured = _clean(os.getenv("EVENT_INTELLIGENCE_WATCH_ITEMS"))
        self.path = Path(path or configured or Path("data") / "forecasts" / "watch_items.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS watch_items (
                watch_id TEXT PRIMARY KEY, run_id TEXT, made_on TEXT, topic TEXT, development TEXT,
                basis TEXT, implication TEXT, watch_for TEXT, horizon TEXT, horizon_until TEXT,
                horizon_parsed INTEGER, conditions TEXT, actors TEXT, citations TEXT,
                status TEXT, status_run_id TEXT, status_on TEXT, status_evidence TEXT, checks INTEGER)""")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        """SQLite's built-in context manager does not close connections."""
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    # ------------------------------------------------------------ record
    def record(self, *, run_id: str, topic: str, implications: Sequence[Mapping[str, Any]],
               made_on: date, development_of: Callable[[Mapping[str, Any]], tuple[str, list[str]]] | None = None
               ) -> int:
        """Store this run's checked implications as open forecasts. Returns how many are new."""
        new = 0
        with self._connection() as db:
            for item in implications or []:
                if not isinstance(item, Mapping):
                    continue
                text = _clean(item.get("text"))
                signal = _clean(item.get("watch_for")) or text
                if not text:
                    continue
                watch_id = "WATCH-" + hashlib.sha256(
                    f"{_clean(topic).casefold()}|{text.casefold()}|{signal.casefold()}".encode()).hexdigest()[:16].upper()
                development, actors = development_of(item) if development_of else ("", [])
                until, parsed = horizon_until(item.get("horizon"), made_on)
                citations = [dict(c) for c in item.get("citations") or [] if isinstance(c, Mapping)]
                cursor = db.execute(
                    "INSERT OR IGNORE INTO watch_items VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (watch_id, run_id, made_on.isoformat(), _clean(topic), development,
                     _clean(item.get("basis")) or "trend", text, signal, _clean(item.get("horizon")),
                     until.isoformat(), int(parsed), _clean(item.get("conditions")), json.dumps(actors),
                     json.dumps(citations), "open", "", "", "", 0))
                new += cursor.rowcount
        return new

    def open_items(self, *, topic: str = "", limit: int = 200) -> list[dict[str, Any]]:
        with self._connection() as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM watch_items WHERE status='open' ORDER BY horizon_until LIMIT ?",
                              (limit,)).fetchall()
        items = [dict(r) for r in rows]
        if topic:
            wanted = _topic_terms(topic)
            scoped = []
            for item in items:
                stored = _clean(item.get("topic"))
                if not stored:
                    scoped.append(item)
                    continue
                theirs = _topic_terms(stored)
                exact = _clean(topic).casefold() == stored.casefold()
                union = wanted | theirs
                overlap = (len(wanted & theirs) / len(union)) if union else 0.0
                if exact or overlap >= 0.50:
                    scoped.append(item)
            items = scoped
        return items

    def all_items(self) -> list[dict[str, Any]]:
        with self._connection() as db:
            db.row_factory = sqlite3.Row
            return [dict(r) for r in db.execute("SELECT * FROM watch_items ORDER BY made_on DESC")]

    # ------------------------------------------------------------ check
    @staticmethod
    def candidates(item: Mapping[str, Any], evidence: Sequence[Mapping[str, Any]],
                   date_of: Callable[[Mapping[str, Any]], str]) -> list[Mapping[str, Any]]:
        """Return genuinely new source statements relevant to the watch signal."""
        citations = _citation_rows(item.get("citations"))
        cited_evidence = {_clean(c.get("evidence_id")).upper() for c in citations if _clean(c.get("evidence_id"))}
        cited_sources = {_clean(c.get("source_id")).upper() for c in citations if _clean(c.get("source_id"))}
        cited_urls = {
            _canonical_url(c.get("source_url") or c.get("url") or c.get("canonical_url"))
            for c in citations
        }
        cited_urls.discard("")
        want = _terms(item["watch_for"]) | _terms(item.get("development") or "")
        actors = {a.casefold() for a in json.loads(item.get("actors") or "[]") if a}
        made_on = item["made_on"]
        scored = []
        for record in evidence or []:
            if not isinstance(record, Mapping):
                continue
            if _clean(record.get("evidence_type")).casefold() not in {"source_excerpt", "quantitative_claim"}:
                continue
            eid = _clean(record.get("evidence_id")).upper()
            sid = _source_id(record)
            url = _source_url(record)
            if (not eid or eid in cited_evidence or sid in cited_sources or (url and url in cited_urls)
                    or record.get("analytical_context_only")):
                continue
            day = _clean(date_of(record))[:10]
            if not day or day <= made_on:
                continue
            text = _clean(record.get("source_excerpt") or record.get("claim") or record.get("text"))
            shared = want & _terms(text)
            if len(shared) < MIN_SHARED_TERMS:
                continue
            actor_hit = any(a in text.casefold() for a in actors)
            scored.append((actor_hit, len(shared), eid, record))
        scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
        return [r for _, _, _, r in scored[:MAX_CANDIDATES]]

    def check(self, *, run_id: str, topic: str, evidence: Sequence[Mapping[str, Any]], today: date,
              date_of: Callable[[Mapping[str, Any]], str],
              ground: Callable[[list[dict[str, Any]], list[Mapping[str, Any]]], Any],
              persist: bool = True) -> dict[str, Any]:
        """Check open forecasts against this run's evidence.

        External/model failure and call-budget deferral are fail-closed: the
        forecast remains open even if its nominal deadline has passed. A deadline
        can expire only after there is no unresolved candidate requiring a check.
        """
        items = self.open_items(topic=topic)
        results: list[dict[str, Any]] = []
        checked = 0
        deferred = 0
        failures = 0
        for item in items:
            outcome = {"watch_id": item["watch_id"], "implication": item["implication"],
                       "watch_for": item["watch_for"], "made_on": item["made_on"],
                       "horizon": item["horizon"], "horizon_until": item["horizon_until"],
                       "development": item["development"], "basis": item["basis"], "status": "open",
                       "evidence": []}
            candidates = self.candidates(item, evidence, date_of)
            unresolved_check = False
            records: list[Mapping[str, Any]] = []
            if candidates and checked >= MAX_CHECKS_PER_RUN:
                unresolved_check = True
                deferred += 1
                outcome["check"] = "deferred:run_check_budget"
            elif candidates:
                records = candidates
                checked += 1
                claim = {"claim_id": "WATCH-1",
                         "claim_text": "This has happened or has been reported: " + item["watch_for"],
                         "evidence_ids": [_clean(r.get("evidence_id")) for r in records],
                         "epistemic_type": "fact", "multi_evidence_required": False}
                try:
                    graded = (ground([claim], list(records)) or {}).get("claims") or []
                    status = _clean(graded[0].get("grounding_status")).casefold() if graded else ""
                    support = [_clean(x).upper() for x in (graded[0].get("supporting_evidence_ids") or [])] if graded else []
                    against = [_clean(x).upper() for x in (graded[0].get("contradicted_evidence_ids") or [])] if graded else []
                except Exception as error:
                    status, support, against = f"error:{type(error).__name__}", [], []
                    unresolved_check = True
                    failures += 1
                outcome["check"] = status
                by_id = {_clean(r.get("evidence_id")).upper(): r for r in records}
                if status == "supported":
                    outcome["status"] = "observed"
                    use = [by_id[i] for i in support if i in by_id] or list(records)
                elif status == "contradicted":
                    outcome["status"] = "contradicted"
                    use = [by_id[i] for i in against if i in by_id] or list(records)
                else:
                    use = []
                outcome["evidence"] = [{
                    "evidence_id": _clean(r.get("evidence_id")),
                    "source_id": _source_id(r),
                    "source": _source_label(r),
                    "source_url": _source_url(r),
                    "date": _clean(date_of(r))[:10],
                    "text": _clean(r.get("source_excerpt") or r.get("claim") or r.get("text"))[:500],
                } for r in use[:MAX_CANDIDATES]]
            if (outcome["status"] == "open" and item["horizon_until"] < today.isoformat()
                    and not unresolved_check):
                outcome["status"] = "expired"
            results.append(outcome)
            if persist:
                with self._connection() as db:
                    db.execute("UPDATE watch_items SET checks=checks+?, status=?, status_run_id=?, status_on=?, "
                               "status_evidence=? WHERE watch_id=?",
                               (1 if records else 0, outcome["status"],
                                run_id if outcome["status"] != "open" else "",
                                today.isoformat() if outcome["status"] != "open" else "",
                                json.dumps(outcome["evidence"]), item["watch_id"]))
        resolved = [r for r in results if r["status"] != "open"]
        summary = {"version": VERSION, "open_before": len(items), "checked_with_new_evidence": checked,
                   "deferred_due_check_budget": deferred, "check_failures": failures,
                   "observed": sum(r["status"] == "observed" for r in results),
                   "contradicted": sum(r["status"] == "contradicted" for r in results),
                   "expired": sum(r["status"] == "expired" for r in results),
                   "still_open": sum(r["status"] == "open" for r in results),
                   "persisted": persist}
        return {"summary": summary, "resolved": resolved,
                "open": [r for r in results if r["status"] == "open"][:10],
                "track_record": self.track_record()}

    def track_record(self) -> dict[str, Any]:
        """Running tally across all runs: how many forecast signals were later reported."""
        counts: dict[str, int] = {}
        for item in self.all_items():
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        decided = counts.get("observed", 0) + counts.get("contradicted", 0) + counts.get("expired", 0)
        return {"counts": counts, "decided": decided,
                "observed_share": round(counts.get("observed", 0) / decided, 2) if decided else None}


def today_utc() -> date:
    return datetime.now(timezone.utc).date()
