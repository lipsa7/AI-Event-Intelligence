"""Memory pass: earlier events as dated facts for the hypothesis lab (26 Oct 2026).

docs/DESIGN_memory_pass.md, revised for GPT's review. Retrieval is local and read-only (no model
call, nothing written, a missing store is skipped, never created). Safeguards:

1. Never corroboration: records carry gap_type "earlier" / evidence_role "earlier_coverage";
   _convergence_maps ignores them, so they add no event and no publisher to convergence.
2. Dates reach the prompt: strategic_hypothesis_lab renders earlier facts in their own dated
   block that the 40-fact cap never cuts.
3. Three dates, kept apart: occurred_on (the event), published_on (the source), available_since
   (when this app first stored it). A record is used only if it was published AND stored before
   the run's cut-off. For a replay the cut-off is the original checkpoint time, so a replay never
   sees anything collected later.
4. Earlier coverage is not a precedent: records are labelled "earlier_coverage"; an outcome
   analogy still needs a dated case with an observed outcome (editorial_briefing.select_cases).
5. Stored excerpts are checked: a database event is used only if its quoted snippet appears
   verbatim in the stored article text; memory excerpts are the sources' own words already.
   Model conclusions are never retrieved - only source excerpts.
6. One retrieval rule, two lanes: "same_actor" (a shared named actor and a shared mechanism or
   subject words) and "mechanism" (a shared mechanism, any actor, plus shared subject words). A
   match on actor alone, or on one generic word, is not enough.
7. Deduplicated by occurrence: one row per memory underlying event; a database event whose
   snippet repeats a memory excerpt (or another row) is dropped.
8. Frozen for comparisons: the selection (ids, content hashes, cut-off, version) is returned and
   stored with the report.
9. Off with EVENT_INTELLIGENCE_MEMORY_PASS=0.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

VERSION = "memory-pass-v1"
ENV = "EVENT_INTELLIGENCE_MEMORY_PASS"
MAX_PER_DEVELOPMENT = 3
MAX_TOTAL = 8
MIN_SHARED_WORDS = 2
ROOT = Path(__file__).resolve().parents[1]

# Mechanisms, matched on words in titles/excerpts and on the database's event types. Deliberately
# about what happened (a fine, a breach, a pledge), not the topic, so "rename" never matches
# "rename" unless the mechanism does (the off-target precedent problem).
MECHANISMS: dict[str, tuple[str, ...]] = {
    "breach_or_incident": ("breach", "breached", "hack", "hacked", "unauthori", "leak", "leaked", "incident",
                           "intrusion", "compromised"),
    "disclosure_or_reporting": ("disclos", "notif", "reporting rule", "alerted"),
    "fine_or_penalty": ("fine", "fined", "fines", "penalt", "sanction", "sanctions", "turnover"),
    "regulation_or_law": ("regulat", "law", "laws", "bill", "bills", "statute", "rule", "rules",
                          "mandate", "compliance"),
    "voluntary_pledge": ("pledge", "pledges", "accord", "voluntary", "commitment", "commitments",
                         "code of conduct", "self-regulat", "pact"),
    "government_partnership": ("for countries", "government partnership", "sovereign", "public sector",
                               "ministry"),
    "funding_round": ("raise", "raised", "raises", "funding", "valuation", "series", "venture", "investor",
                      "investors"),
    "acquisition": ("acqui", "merger", "takeover", "buyout"),
    "partnership": ("partner", "partners", "partnership", "alliance", "collaborat", "joint"),
    "research_grant": ("grant", "grants", "scientist", "scientists", "academic", "mathematicians", "researchers"),
    "ban_or_restriction": ("ban", "bans", "banned", "restrict", "restricted", "restrictions", "prohibit",
                           "export control", "blacklist"),
    "lawsuit": ("lawsuit", "sued", "court", "litigation", "settlement"),
    "safety_testing": ("safety", "audit", "audits", "red team", "evaluation", "security institute"),
}
EVENT_TYPE_MECHANISM = {
    "investment_or_funding": "funding_round", "fundraising": "funding_round",
    "acquisition_or_merger": "acquisition", "partnership_or_agreement": "partnership",
    "regulation_or_policy_action": "regulation_or_law", "legal_action": "lawsuit",
}


def enabled() -> bool:
    return os.getenv(ENV, "1") != "0"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _words(text: str) -> set[str]:
    from services.event_memory_service import _words as words
    return words(text)


def _norm_actor(actor: str) -> str:
    try:
        from services.event_memory_service import _norm_actor as norm
        return norm(actor)
    except Exception:
        return _clean(actor).casefold()


# Word stems match as prefixes ("regulat" -> regulation); every other cue matches whole words only.
STEMS = frozenset({"disclos", "notif", "penalt", "regulat", "self-regulat", "acqui", "collaborat",
                   "prohibit", "unauthori"})
_CUE_PATTERNS = {name: re.compile("|".join(r"\b" + re.escape(cue.strip()) + ("" if cue.strip() in STEMS else r"\b")
                                           for cue in cues))
                 for name, cues in MECHANISMS.items()}
SAME_OCCURRENCE_OVERLAP = 0.35   # this much shared wording with one current fact = the same news
MIN_SUBJECT_WORDS_MECHANISM_LANE = 3
CUE_WORDS = frozenset(w for cues in MECHANISMS.values() for cue in cues for w in re.findall(r"[a-z]{4,}", cue))
GENERIC_WORDS = frozenset("""million billion company companies year years month months today week said says
announced percent capital round total including according first new bringing co-led rise later led
will would could help make made model models launched launch raising community based provide provides
plans plan also more most other within""".split())


def mechanisms(text: str, event_type: str = "") -> set[str]:
    """Cues match at word starts only ("bill" never matches "billion")."""
    low = _clean(text).casefold()
    found = {name for name, pattern in _CUE_PATTERNS.items() if pattern.search(low)}
    if EVENT_TYPE_MECHANISM.get(_clean(event_type)):
        found.add(EVENT_TYPE_MECHANISM[_clean(event_type)])
    return found


def _when(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
    except ValueError:
        try:
            parsed = datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _sha(text: str) -> str:
    return hashlib.sha256(_clean(text).encode("utf-8")).hexdigest()


def _connect(path: Path):
    """Read-only; a missing store is skipped, never created."""
    if not path.is_file():
        return None
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def memory_path() -> Path:
    configured = _clean(os.getenv("EVENT_INTELLIGENCE_EVENT_MEMORY"))
    return Path(configured) if configured else ROOT / "data" / "event_memory.sqlite3"


def database_path() -> Path:
    url = _clean(os.getenv("EVENT_INTELLIGENCE_DATABASE_URL") or os.getenv("DATABASE_URL"))
    if url.startswith("sqlite:///"):
        path = Path(url[len("sqlite:///"):])
        return path if path.is_absolute() else ROOT / path
    return ROOT / "data" / "event_intelligence.db"


def cutoff(state: Mapping[str, Any], now: datetime | None = None) -> datetime:
    """The replay's original checkpoint time, else now. Nothing stored later is used."""
    replay = state.get("replayed_from") if isinstance(state.get("replayed_from"), Mapping) else {}
    return _when(replay.get("saved_at")) or _when(state.get("memory_as_of")) or now or datetime.now(timezone.utc)


def _target(development: Mapping[str, Any]) -> dict[str, Any]:
    fact_texts = [_clean(f.get("text")) for f in development.get("facts") or [] if isinstance(f, Mapping)]
    text = f"{_clean(development.get('title'))} {' '.join(fact_texts)}"
    return {"title": _clean(development.get("title")), "words": _words(text),
            "fact_words": [w for w in (_words(t) for t in fact_texts) if w],
            "actors": {_norm_actor(a) for a in development.get("actors") or [] if _clean(a)} - {""},
            "mechanisms": mechanisms(text)}


def _match(target: Mapping[str, Any], *, words: set[str], actors: set[str], mechs: set[str]) -> dict[str, Any] | None:
    shared_words = target["words"] & words
    for fact in target.get("fact_words") or []:
        if len(fact & words) / len(fact | words) >= SAME_OCCURRENCE_OVERLAP:
            return None   # the same news as one current fact (another outlet), not earlier coverage
    shared_actors = target["actors"] & actors
    shared_mechs = target["mechanisms"] & mechs
    # Subject words, not mechanism words: "raised" and "million" make every funding story alike.
    subject = {w for w in shared_words if w not in CUE_WORDS and w not in GENERIC_WORDS}
    if shared_actors and (shared_mechs or len(subject) >= MIN_SHARED_WORDS):
        lane = "same_actor"
    elif shared_mechs and len(subject) >= MIN_SUBJECT_WORDS_MECHANISM_LANE:
        lane = "mechanism"
    else:
        return None
    score = 3 * bool(shared_actors) + 2 * len(shared_mechs) + min(len(shared_words), 6) / 6
    return {"lane": lane, "score": round(score, 3), "shared_actors": sorted(shared_actors),
            "shared_mechanisms": sorted(shared_mechs), "shared_words": sorted(shared_words)[:8]}


def _memory_candidates(cut: datetime, exclude_runs: set[str], exclude_underlying: set[str]) -> list[dict[str, Any]]:
    db = _connect(memory_path())
    if db is None:
        return []
    out = []
    with closing(db):
        rows = db.execute("SELECT underlying_event_id, title, actors, event_date, first_run_id, first_seen, words "
                          "FROM events").fetchall()
        for underlying, title, actors_json, event_date, first_run, first_seen, words_json in rows:
            if underlying.upper() in exclude_underlying or first_run in exclude_runs:
                continue
            available = _when(first_seen)
            if available is None or available >= cut:
                continue
            excerpts = db.execute("SELECT evidence_id, source_id, text, published_at FROM excerpts "
                                  "WHERE underlying_event_id=? ORDER BY length(text) DESC", (underlying,)).fetchall()
            for evidence_id, source_id, text, published in excerpts:
                published_on = _when(published) or _when(event_date)
                if published_on is None or published_on >= cut or len(_clean(text)) < 40:
                    continue
                actors = {_norm_actor(a) for a in json.loads(actors_json or "[]")} - {""}
                out.append({"store": "event_memory", "occurrence": underlying.upper(),
                            "evidence_id": "MEM-" + _clean(evidence_id), "original_evidence_id": _clean(evidence_id),
                            "source_id": _clean(source_id), "text": _clean(text), "title": _clean(title),
                            "occurred_on": _clean(event_date)[:10], "published_on": published_on.date().isoformat(),
                            "available_since": available.date().isoformat(), "actors": actors,
                            "words": {w for w in json.loads(words_json or "[]") if not w.startswith("anchor:")},
                            "mechanisms": mechanisms(f"{title} {text}")})
                break   # one excerpt per occurrence: the longest usable one
    return out


def _database_candidates(cut: datetime, exclude_sources: set[str]) -> list[dict[str, Any]]:
    db = _connect(database_path())
    if db is None:
        return []
    out = []
    with closing(db):
        rows = db.execute(
            "SELECT e.event_id, e.source_id, e.title, e.event_type, e.event_start_at, e.entity_mentions, e.snippets, "
            "e.created_at, a.content, a.published_at, a.url FROM events e JOIN articles a ON a.source_id = e.source_id "
            "WHERE a.content IS NOT NULL AND a.published_at IS NOT NULL").fetchall()
    for event_id, source_id, title, event_type, start, mentions, snippets, created, content, published, url in rows:
        if _clean(source_id).upper() in exclude_sources:
            continue
        available, published_on = _when(created), _when(published)
        if available is None or published_on is None or available >= cut or published_on >= cut:
            continue
        body = _clean(content)
        try:
            quotes = [_clean(s) for s in json.loads(snippets or "[]") if _clean(s)]
        except (TypeError, ValueError):
            quotes = []
        quote = next((q for q in quotes if len(q) >= 40 and q in body), "")
        if not quote:
            continue   # safeguard 5: the excerpt must appear verbatim in the stored article
        try:
            actors = {_norm_actor(m.get("mention")) for m in json.loads(mentions or "[]")
                      if isinstance(m, Mapping) and m.get("role") == "actor"} - {""}
        except (TypeError, ValueError):
            actors = set()
        out.append({"store": "event_intelligence_db", "occurrence": _clean(event_id).upper(),
                    "evidence_id": "DBEV-" + _sha(f"{source_id}|{quote}")[:24].upper(), "original_evidence_id": "",
                    "source_id": _clean(source_id), "text": quote, "title": _clean(title), "source_url": _clean(url),
                    "occurred_on": _clean(start)[:10], "published_on": published_on.date().isoformat(),
                    "available_since": available.date().isoformat(), "actors": actors,
                    "words": _words(f"{title} {quote}"), "mechanisms": mechanisms(f"{title} {quote}", event_type)})
    return out


def _record(candidate: Mapping[str, Any], match: Mapping[str, Any], development_title: str) -> dict[str, Any]:
    return {
        "evidence_id": candidate["evidence_id"], "original_evidence_id": candidate["original_evidence_id"],
        "source_id": candidate["source_id"], "event_id": "", "evidence_type": "source_excerpt",
        "evidence_role": "earlier_coverage", "gap_type": "earlier", "earlier_kind": "earlier_coverage",
        "text": candidate["text"], "source_excerpt": candidate["text"], "source_url": candidate.get("source_url", ""),
        "published_at": candidate["published_on"], "occurred_on": candidate["occurred_on"],
        "available_since": candidate["available_since"], "earlier_event_title": candidate["title"],
        "gap_for": development_title, "memory_store": candidate["store"], "memory_match": dict(match),
        "analytical_context_only": True, "direct_event_evidence": False,
        "prohibited_uses": ["observed_event_count", "acting_actor_count", "sample_base_rate",
                            "current_event_corroboration"],
        "content_sha256": _sha(candidate["text"]),
    }


def retrieve(developments: Sequence[Mapping[str, Any]], state: Mapping[str, Any], *,
             now: datetime | None = None) -> dict[str, Any]:
    """Selected earlier-coverage records and the selection metadata (never raises)."""
    cut = cutoff(state, now)
    meta: dict[str, Any] = {"version": VERSION, "enabled": enabled(), "cutoff": cut.isoformat(),
                            "stores": {}, "selected": [], "errors": []}
    if not meta["enabled"]:
        return {"records": [], "meta": meta}
    run_id = _clean(state.get("run_id"))
    replay = state.get("replayed_from") if isinstance(state.get("replayed_from"), Mapping) else {}
    exclude_runs = {x for x in (run_id, _clean(replay.get("run_id"))) if x}
    exclude_underlying = {_clean(e.get("underlying_event_id")).upper()
                          for e in state.get("events") or [] if isinstance(e, Mapping)} - {""}
    # Every source this run selected, not only those that produced admitted evidence (RUN-3E2FE:
    # a Simons Foundation article selected today came back as "earlier" coverage).
    exclude_sources = ({_clean(e.get("source_id")).upper()
                        for e in list(state.get("events") or []) + list(state.get("evidence") or [])
                        if isinstance(e, Mapping)}
                       | {_clean(x).upper() for x in list(state.get("source_ids") or [])
                          + list(state.get("selected_source_ids") or [])}) - {""}
    candidates: list[dict[str, Any]] = []
    for name, loader in (("event_memory", lambda: _memory_candidates(cut, exclude_runs, exclude_underlying)),
                         ("event_intelligence_db", lambda: _database_candidates(cut, exclude_sources))):
        try:
            rows = loader()
            meta["stores"][name] = len(rows)
            candidates += rows
        except Exception as error:   # a broken store never stops the run
            meta["errors"].append(f"{name}:{type(error).__name__}")
    targets = [_target(d) for d in developments or [] if isinstance(d, Mapping) and _clean(d.get("title"))]
    records: list[dict[str, Any]] = []
    used_occurrences: set[str] = set()
    used_texts: set[str] = set()
    for target in targets:
        scored = []
        for candidate in candidates:
            match = _match(target, words=candidate["words"], actors=candidate["actors"],
                           mechs=candidate["mechanisms"])
            if match:
                scored.append((match["score"], candidate["published_on"], candidate, match))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        taken = 0
        for _, _, candidate, match in scored:
            key = _sha(candidate["text"])
            if candidate["occurrence"] in used_occurrences or key in used_texts:
                continue   # safeguard 7: one record per occurrence and per passage
            used_occurrences.add(candidate["occurrence"])
            used_texts.add(key)
            records.append(_record(candidate, match, target["title"]))
            taken += 1
            if taken >= MAX_PER_DEVELOPMENT or len(records) >= MAX_TOTAL:
                break
        if len(records) >= MAX_TOTAL:
            break
    meta["selected"] = [{"evidence_id": r["evidence_id"], "store": r["memory_store"], "lane": r["memory_match"]["lane"],
                         "published_on": r["published_at"], "available_since": r["available_since"],
                         "content_sha256": r["content_sha256"], "for": r["gap_for"][:120]} for r in records]
    meta["selection_sha256"] = _sha(json.dumps(meta["selected"], sort_keys=True))
    return {"records": records, "meta": meta}


def as_facts(records: Iterable[Mapping[str, Any]], start: int = 1) -> list[dict[str, Any]]:
    """Earlier-coverage records as lab facts M1, M2, ... (rendered in their own dated block)."""
    return [{"fact_id": f"M{n}", "development": f"Earlier coverage: {_clean(r.get('earlier_event_title'))[:120]}",
             "text": _clean(r.get("text"))[:600], "date": _clean(r.get("published_at"))[:10],
             "occurred_on": _clean(r.get("occurred_on"))[:10], "evidence_id": _clean(r.get("evidence_id")),
             "source_id": _clean(r.get("source_id")), "gap_type": "earlier"}
            for n, r in enumerate(records, start=start)]
