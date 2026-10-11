"""Cross-run event memory (v6.20).

Every Deep run stores its admitted underlying events and their source
excerpts in a local SQLite file (data/event_memory.sqlite3, override with
EVENT_INTELLIGENCE_EVENT_MEMORY). Later runs retrieve earlier coverage of the
same actors or the same subject, so the analysis and the briefing can say
"this follows X reported on 5 Oct" and spot progressions across runs.

Underlying-event IDs come from the canonical resolver (fact-based and stable),
so the same occurrence seen in two runs is one row, updated, not duplicated.
Only source excerpts are stored as text (the sources' own words), with their
source id and date, so anything reused later is still traceable.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

VERSION = "event-memory-v2.1"
STOP = frozenset("""
about after also among because being between could during from have into more most other over said
says than that their there these this those through under using which while with would report company
companies market technology announced launch new latest news artificial intelligence acquire acquires acquired acquiring
acquisition acquisitions buy buys bought buying purchase purchases purchased purchasing merger mergers merge merged
deal deals agreement agreements partner partners partnership partnerships invest invests investment investments
""".split())


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9][a-z0-9-]{3,}", text.casefold()) if w not in STOP and not w.isdigit()}


def _named_anchor(event: Mapping[str, Any], actor: str) -> str:
    """The resolver's named occurrence object (acquisition target, partner,
    product) - the same definition the duplicate resolver uses; '' if none."""
    from services.underlying_event_resolver import UnderlyingEventResolver
    resolver = UnderlyingEventResolver(actor_key=lambda _e: actor, family=lambda _e: "")
    return resolver.named_anchor(event)


def _norm_actor(actor: str) -> str:
    from services.underlying_event_resolver import _norm_actor as norm
    return norm(actor)


def _day(value: Any):
    try:
        return date.fromisoformat(_clean(value)[:10])
    except ValueError:
        return None


class EventMemoryService:
    def __init__(self, path: str | os.PathLike | None = None) -> None:
        configured = _clean(os.getenv("EVENT_INTELLIGENCE_EVENT_MEMORY"))
        self.path = Path(path or configured or Path("data") / "event_memory.sqlite3")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    underlying_event_id TEXT PRIMARY KEY, title TEXT, actors TEXT, event_date TEXT,
                    topic TEXT, first_run_id TEXT, last_run_id TEXT, first_seen TEXT, last_seen TEXT,
                    source_ids TEXT, words TEXT);
                CREATE TABLE IF NOT EXISTS excerpts (
                    evidence_id TEXT PRIMARY KEY, underlying_event_id TEXT, source_id TEXT,
                    text TEXT, published_at TEXT, run_id TEXT);
                CREATE INDEX IF NOT EXISTS excerpts_by_event ON excerpts(underlying_event_id);
            """)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """Commit on success, roll back on error, and ALWAYS close. sqlite3's own
        context manager does not close the connection; on Windows an open handle
        locks the file (v6.20.8)."""
        db = sqlite3.connect(str(self.path), timeout=10)
        try:
            with db:
                yield db
        finally:
            db.close()

    MAX_DAYS_APART = 31

    def _reconcile_existing_underlying(
        self, db: sqlite3.Connection, *, proposed: str, actor: str, words: set[str], event_date: str
    ) -> str:
        """Persistent memory ID for one resolved occurrence (v6.20.3).

        1. The resolver ID is the primary identity: if memory already has it
           within 31 days of this report (or either side is undated), reuse it.
        2. The same resolver ID more than 31 days away is a repeat occurrence
           (same actor, type and target): reuse a dated variant of it within 31
           days if one exists, else create one.
        3. A new resolver ID is aliased to an existing row only for the same
           actor within 31 days AND the same named object (target/product) with
           overlapping wording (Jaccard >= 0.4); with no named object on either
           side, only when the wording overlaps strongly (Jaccard >= 0.6). This
           covers resolver changes between versions; when unsure it keeps a
           separate row (a duplicate row is less harmful than merging two events).
        """
        if not proposed:
            return proposed
        current_day = _day(event_date)
        anchors = {w for w in words if w.startswith("anchor:")}
        plain = {w for w in words if not w.startswith("anchor:")}

        def near(old_date: str) -> int | None:
            old_day = _day(old_date)
            if current_day is None or old_day is None:
                return 0
            distance = abs((current_day - old_day).days)
            return distance if distance <= self.MAX_DAYS_APART else None

        rows = db.execute("SELECT underlying_event_id, actors, words, event_date FROM events").fetchall()
        family_rows = [r for r in rows if str(r[0]) == proposed or str(r[0]).startswith(proposed + "-")]
        nearest = sorted(((near(r[3]), str(r[0])) for r in family_rows if near(r[3]) is not None))
        if nearest:
            return nearest[0][1]
        if family_rows:
            stamp = current_day.isoformat() if current_day else "undated"
            return f"{proposed}-{hashlib.sha256(f'{proposed}|{stamp}'.encode()).hexdigest()[:12].upper()}"
        actor_key = _norm_actor(actor)
        if not actor_key:
            return proposed
        best = None
        for underlying, actors_json, words_json, old_date in rows:
            distance = near(old_date)
            if distance is None:
                continue
            try:
                old_actors = {_norm_actor(x) for x in json.loads(actors_json or "[]")}
                old_words = set(json.loads(words_json or "[]"))
            except ValueError:
                continue
            if actor_key not in old_actors:
                continue
            old_anchors = {w for w in old_words if w.startswith("anchor:")}
            old_plain = {w for w in old_words if not w.startswith("anchor:")}
            union = plain | old_plain
            score = len(plain & old_plain) / len(union) if union else 0.0
            if anchors and old_anchors:
                if not anchors & old_anchors:
                    continue  # two different named objects: different occurrences
                if score < 0.4:
                    continue  # same target, but e.g. an acquisition vs a partnership
            elif score < 0.6:
                continue
            key = (score, -distance, str(underlying))
            if best is None or key > best:
                best = key
        return best[2] if best else proposed

    def remember(self, *, run_id: str, topic: str, events: Sequence[Mapping[str, Any]],
                 evidence: Sequence[Mapping[str, Any]], actor_of=None) -> int:
        """Upsert this run's underlying events and source excerpts.

        The canonical resolver ID is used whenever possible. If an older memory
        row clearly represents the same occurrence, the existing persistent ID is
        retained so later wording changes cannot duplicate longitudinal memory.
        """
        now = datetime.now(timezone.utc).isoformat()
        excerpts_by_event: dict[str, list[Mapping[str, Any]]] = {}
        for record in evidence or []:
            if not isinstance(record, Mapping) or _clean(record.get("evidence_type")) != "source_excerpt":
                continue
            excerpts_by_event.setdefault(_clean(record.get("event_id")).upper(), []).append(record)
        stored = 0
        aliases: dict[str, str] = {}
        decided: dict[str, str] = {}
        with self._connect() as db:
            for event in events or []:
                if not isinstance(event, Mapping):
                    continue
                event_id = _clean(event.get("event_id")).upper()
                proposed = _clean(event.get("underlying_event_id")).upper() or event_id
                excerpts = excerpts_by_event.get(event_id, [])
                if not proposed or not excerpts:
                    continue
                title = _clean(event.get("title") or event.get("summary") or event.get("event"))[:400]
                actor = _clean(actor_of(event) if callable(actor_of) else event.get("actor"))
                day = _clean(event.get("event_start_at") or event.get("published_at") or event.get("event_date"))[:10]
                words = _words(" ".join([title] + [_clean(x.get("claim") or x.get("text")) for x in excerpts]))
                anchor = _named_anchor(event, actor)
                if anchor:
                    words.add("anchor:" + anchor)
                # One decision per resolved occurrence per run, so its reports
                # can never be split across rows.
                if proposed in decided:
                    underlying = decided[proposed]
                else:
                    underlying = self._reconcile_existing_underlying(
                        db, proposed=proposed, actor=actor, words=words, event_date=day)
                    decided[proposed] = underlying
                if underlying != proposed:
                    aliases[proposed] = underlying
                row = db.execute(
                    "SELECT actors, source_ids, words, first_run_id, first_seen FROM events "
                    "WHERE underlying_event_id=?", (underlying,)
                ).fetchone()
                actors = set(json.loads(row[0])) if row else set()
                sources = set(json.loads(row[1])) if row else set()
                old_words = set(json.loads(row[2])) if row else set()
                actors |= {actor} if actor else set()
                sources |= {_clean(x.get("source_id")).upper() for x in excerpts if _clean(x.get("source_id"))}
                db.execute(
                    "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (underlying, title, json.dumps(sorted(actors)), day, _clean(topic),
                     row[3] if row else run_id, run_id, row[4] if row else now, now,
                     json.dumps(sorted(sources)), json.dumps(sorted(old_words | words))),
                )
                for record in excerpts:
                    text = _clean(record.get("source_excerpt") or record.get("claim") or record.get("text"))
                    if len(text) < 30:
                        continue
                    db.execute("INSERT OR IGNORE INTO excerpts VALUES (?,?,?,?,?,?)", (
                        _clean(record.get("evidence_id")).upper(), underlying, _clean(record.get("source_id")).upper(),
                        text[:1200], _clean(record.get("published_at") or day), run_id))
                stored += 1
        self.last_aliases = aliases
        return stored

    def earlier_related(self, *, development: Mapping[str, Any], current_run_id: str,
                        exclude_underlying: Sequence[str] = (), limit: int = 3) -> list[dict[str, Any]]:
        """Earlier events (from previous runs) sharing an actor or the subject, newest first."""
        actors = {_clean(a).casefold() for a in development.get("actors") or [] if _clean(a)}
        subject = _words(" ".join([_clean(development.get("title"))] +
                                  [_clean(f.get("text")) for f in development.get("facts") or [] if isinstance(f, Mapping)]))
        excluded = {_clean(x).upper() for x in exclude_underlying}
        scored = []
        with self._connect() as db:
            for (underlying, title, actors_json, day, last_run, words_json) in db.execute(
                    "SELECT underlying_event_id, title, actors, event_date, last_run_id, words FROM events"):
                if underlying in excluded or last_run == current_run_id:
                    continue
                their_actors = {a.casefold() for a in json.loads(actors_json or "[]")}
                their_words = {w for w in json.loads(words_json or "[]") if not w.startswith("anchor:")}
                overlap = len(subject & their_words) / len(subject | their_words) if subject | their_words else 0.0
                shared_actor = bool(actors & their_actors)
                if not shared_actor and overlap < 0.2:
                    continue
                scored.append((shared_actor, overlap, day or "", underlying, title))
            scored.sort(key=lambda x: (x[0], x[1], x[2]), reverse=True)
            output = []
            for shared_actor, overlap, day, underlying, title in scored[:limit]:
                excerpts = db.execute("SELECT evidence_id, source_id, text, published_at FROM excerpts "
                                      "WHERE underlying_event_id=? ORDER BY length(text) DESC LIMIT 2",
                                      (underlying,)).fetchall()
                for evidence_id, source_id, text, published in excerpts:
                    output.append({
                        # Evidence IDs are content hashes, so the same excerpt in this
                        # run has the same ID; a distinct prefix keeps them unique.
                        "evidence_id": "MEM-" + evidence_id, "original_evidence_id": evidence_id,
                        "source_id": source_id, "event_id": "",
                        "earlier_underlying_event_id": underlying, "evidence_type": "source_excerpt",
                        "evidence_role": "earlier_coverage", "text": text, "source_excerpt": text,
                        "published_at": published or day, "gap_type": "earlier",
                        "gap_for": _clean(development.get("title")), "analytical_context_only": True,
                        "direct_event_evidence": False, "earlier_event_title": title,
                        "match": "shared_actor" if shared_actor else "shared_subject",
                    })
        return output
