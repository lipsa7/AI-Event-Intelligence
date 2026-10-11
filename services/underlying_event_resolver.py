"""Canonical duplicate-story resolver for Event Intelligence (v6.20.3).

The resolver's job is deliberately narrow: decide when multiple EVENT rows are
reports of the same real-world occurrence, and assign that occurrence a stable
underlying_event_id.

Integrity rules:
- actor/family/date similarity is never enough on its own;
- actor names and generic action/family words never count as distinctive overlap;
- duplicate merging uses complete linkage, so A~B and B~C cannot drag unrelated
  A and C into one group;
- the persistent ID is anchored on invariant occurrence facts (actor, family and
  distinctive occurrence anchor), not article/event row IDs, report count or
  earliest report week;
- adding another report of the same occurrence must not rename it;
- different same-day actions by the same actor must remain separate.

v2.2 (v6.20.3) decides "same occurrence" the way news-clustering and event
coreference systems do: TF-IDF cosine of the reports' wording within the run,
a time window, and argument-compatibility vetoes (two different named targets,
or two different named subjects of a templated sentence, are two events).
Measured on 93 hand-labelled pairs of this app's own extracted events
(tests/data/dedup_gold_pairs.json): precision 0.93, recall 0.75
(v2.0: 0.50 / 0.03; v1: 0.38 / 0.46).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from collections import Counter
from typing import Any, Callable, Mapping, Sequence

VERSION = "underlying-event-resolver-v2.2"

GENERIC_WORDS = frozenset("""
about after again against also among because being between could current during from have into more
most other over said says than that their there these this those through under using which while with
would report reports source sources event events market markets industry industries company companies
technology technologies announced announcement launch launches launched latest news development
developments update updates move moves action actions plans plan strategy strategic business businesses
firm firms group groups project projects program programmes initiative initiatives product products
service services system systems platform platforms startup startups deal deals transaction transactions
agreement agreements operation operations facility facilities site sites plant plants unit units
""".split())

# These words describe the event family/action. They are useful for deciding the
# family but must never be the lexical reason two occurrences are called the same.
ACTION_WORDS = frozenset("""
acquire acquires acquired acquiring acquisition acquisitions buy buys bought buying purchase purchases
purchased purchasing takeover takeovers merge merges merged merging merger mergers invest invests
invested investing investment investments fund funds funded funding financing finance raises raised
raise partner partners partnered partnering partnership partnerships sign signs signed signing contract
contracts contracted agreement agreements launch launches launched launching open opens opened opening
expand expands expanded expanding expansion expansions build builds built building deploy deploys
deployed deploying deployment deployments announce announces announced announcing approve approves
approved approving approval approvals regulate regulates regulated regulation regulations ban bans
banned banning cut cuts cutting increase increases increased increasing reduce reduces reduced reducing
sell sells sold selling spin spins spun split splits close closes closed closing hire hires hired hiring
appoint appoints appointed appointing sue sues sued suing lawsuit lawsuits settle settles settled settling complete completes completed completing
""".split())

TOPIC_WORDS = frozenset({"ai", "artificial", "intelligence", "tech", "new"})

# Function words (a capitalized "The"/"She" at the start of a sentence is not a name).
STOPWORDS = frozenset("""
a an the and or but if then so as at by for from in into of on onto to with without within about above
after again against all also am among are as be been before being below between both can could did do
does doing down during each few further had has have having he her here hers herself him himself his how
i its itself just me more most my myself no nor not now off once only other our ours out over own same
she should some such than that their theirs them themselves there these they this those through too
under until up very was we were what when where which while who whom why will would you your yours
it's he's she's they're we're isn't wasn't according said says say told tells earlier later last next
since still yet even already today yesterday tomorrow week weeks month months year years beyond
""".split())

# Trailing publisher tags in headlines: "... - SiliconANGLE", "... | TechCrunch".
_PUBLISHER_TAIL = re.compile(r"\s+[-|\u2013\u2014]\s+[^-|\u2013\u2014]{2,40}$")
_CORPORATE_SUFFIXES = frozenset({"inc", "inc.", "corp", "corp.", "corporation", "co", "co.", "ltd", "ltd.", "llc",
                                 "plc", "pbc", "group", "holdings", "ai", "technologies", "platforms", "sa", "ag", "gmbh"})


def _stem(token: str) -> str:
    """Light suffix stripping so 'euros'/'euro', 'training'/'train' match; numbers
    keep their digits ('500m' -> '500')."""
    if token[:1].isdigit():
        return re.sub(r"[a-z]+$", "", token) or token
    for suffix in ("ing", "ed", "es", "s"):
        if len(token) > len(suffix) + 3 and token.endswith(suffix):
            return token[: -len(suffix)]
    return token


def _strip_publisher(text: str) -> str:
    from services.aside_filter import strip_furniture  # photo credits, "Read more:", nav tails
    text = strip_furniture(text)
    stripped = _PUBLISHER_TAIL.sub("", text)
    return stripped if len(stripped) >= 20 else text


def _norm_actor(actor: str) -> str:
    """'OpenAI Group PBC' / 'OpenAI', 'Gallatin AI Inc' / 'Gallatin' -> one actor key."""
    words = _clean(actor).casefold().replace(",", " ").split()
    while len(words) > 1 and words[-1] in _CORPORATE_SUFFIXES:
        words.pop()
    return " ".join(words)

# v2.2: textual similarity, as in streaming news clustering (Miranda et al.,
# EMNLP 2018: TF-IDF cosine over words and named entities plus a time feature,
# compared with a threshold) and cross-document event coreference (Bugert et
# al., 2021: lexical similarity of the action dominates, time is next). Values
# chosen on 93 hand-labelled pairs from this app's own extracted events; a
# threshold sweep (0.35-0.50 / 0.15-0.25) is in the v6.20.3 README.
COSINE_SAME_STORY = 0.40
COSINE_SAME_ACTOR = 0.20     # enough when actor and event family already agree
MAX_DAYS_APART = 31


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _date_key(event: Mapping[str, Any]) -> str:
    raw = _clean(
        event.get("event_date")
        or event.get("date")
        or event.get("event_start_at")
        or event.get("published_at")
        or event.get("event_time")
        or event.get("event_time_text")
    )
    match = re.search(r"\b(20\d{2})[-/](\d{1,2})[-/](\d{1,2})\b", raw)
    return f"{match.group(1)}-{int(match.group(2)):02d}-{int(match.group(3)):02d}" if match else ""


def _numbers(event: Mapping[str, Any]) -> set[str]:
    blob = " ".join(_clean(event.get(k)) for k in ("title", "summary", "description"))
    return set(re.findall(r"\$?\d[\d,.]*\s?(?:million|billion|bn|m|%|percent)?", blob, flags=re.I))


def _date_distance_days(left: str, right: str) -> int | None:
    if not left or not right:
        return None
    try:
        a = date.fromisoformat(left)
        b = date.fromisoformat(right)
    except ValueError:
        return None
    return abs((a - b).days)


def _raw_words(text: str) -> list[str]:
    return re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", text or "")


def _actor_tokens(actor: str) -> set[str]:
    return {w.casefold() for w in _raw_words(actor)}


class UnderlyingEventResolver:
    def __init__(
        self,
        *,
        actor_key: Callable[[Mapping[str, Any]], str],
        family: Callable[[Mapping[str, Any]], str],
        topic: str = "",
    ) -> None:
        self.actor_key = actor_key
        self.family = family
        self.topic_words = {
            t.casefold()
            for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9_-]{2,}", _clean(topic))
        } | TOPIC_WORDS

    def _text(self, event: Mapping[str, Any]) -> str:
        return " ".join(
            _clean(event.get(k))
            for k in ("title", "summary", "description", "event_type", "event_time_text")
        )

    def tokens(self, event: Mapping[str, Any]) -> set[str]:
        """High-information lexical tokens after generic/topic/action filtering."""
        actor = _clean(self.actor_key(event))
        actor_words = _actor_tokens(actor)
        blob = self._text(event).casefold()
        return {
            t
            for t in re.findall(r"[a-z0-9][a-z0-9_-]{3,}", blob)
            if t not in GENERIC_WORDS
            and t not in ACTION_WORDS
            and t not in STOPWORDS
            and t not in self.topic_words
            and t not in actor_words
            and not t.isdigit()
        }

    def occurrence_anchor(self, event: Mapping[str, Any]) -> str:
        return self._anchor(event)[0]

    def named_anchor(self, event: Mapping[str, Any]) -> str:
        """v2.2: the anchor only when it is the named OBJECT of the action (a
        capitalized word after the action verb, not the first word of the
        sentence): the acquisition target, partner or product. Only such an
        anchor may veto a merge or define the persistent ID."""
        anchor, strong = self._anchor(event)
        return anchor if strong else ""

    def _anchor(self, event: Mapping[str, Any]) -> tuple[str, bool]:
        """Best deterministic occurrence object from the source wording.

        Prefer a named object after the event action (the acquisition target,
        partner, product, place, etc.). This makes IDs stable across ordinary
        paraphrases such as ``acquires Widgetco`` and ``completes acquisition of
        robotics startup Widgetco``. If the sentence is passive, fall back to a
        named token outside the actor. Only when no named token exists do we use
        the first distinctive lexical token.
        """
        actor_words = _actor_tokens(_clean(self.actor_key(event)))
        title = _strip_publisher(event.get("title") or event.get("summary") or event.get("description"))
        raw_words = _raw_words(title)

        def eligible(raw: str) -> bool:
            token = raw.casefold()
            return bool(
                token
                and token not in GENERIC_WORDS
                and token not in ACTION_WORDS
                and token not in STOPWORDS
                and token not in self.topic_words
                and token not in actor_words
                and not token.isdigit()
            )

        action_positions = [i for i, raw in enumerate(raw_words) if raw.casefold() in ACTION_WORDS]
        if action_positions:
            start = action_positions[0] + 1
            after = raw_words[start:]
            named = [raw.casefold() for raw in after if eligible(raw) and raw[:1].isupper()]
            if named:
                return named[0], True
            lexical = [raw.casefold() for raw in after if eligible(raw)]
            if lexical:
                return lexical[0], False
        # Not after an action verb: a weak anchor (never vetoes, never defines the ID).
        for raw in raw_words[1:]:
            if eligible(raw) and raw[:1].isupper():
                return raw.casefold(), False
        for raw in raw_words:
            if eligible(raw):
                return raw.casefold(), False
        return "", False

    def subject(self, event: Mapping[str, Any]) -> str:
        """The first word of the title when it is a capitalized name (the
        sentence's subject in most news sentences); '' otherwise."""
        words = _raw_words(_strip_publisher(event.get("title") or event.get("summary")))
        if not words:
            return ""
        first = words[0]
        token = first.casefold()
        if (not first[:1].isupper() or token in STOPWORDS or token in GENERIC_WORDS
                or token in ACTION_WORDS or token in self.topic_words):
            return ""
        return token

    def proper_tokens(self, event: Mapping[str, Any]) -> set[str]:
        """Named-object anchors (targets, places, products) from source wording.

        Capitalization is only a helpful signal; generic/action/actor words are
        still excluded. This is intentionally conservative, not a general NER.
        """
        actor_words = _actor_tokens(_clean(self.actor_key(event)))
        out: set[str] = set()
        text = " ".join([_strip_publisher(event.get("title"))] + [_clean(event.get(k)) for k in ("summary", "description")])
        for position, raw in enumerate(_raw_words(text)):
            token = raw.casefold()
            if not raw[:1].isupper() or token in STOPWORDS:
                continue
            if (
                token in GENERIC_WORDS
                or token in ACTION_WORDS
                or token in self.topic_words
                or token in actor_words
                or token.isdigit()
            ):
                continue
            out.add(token)
        return out

    def text_terms(self, event: Mapping[str, Any]) -> list[str]:
        blob = " ".join([_strip_publisher(event.get("title"))] + [_clean(event.get(k)) for k in ("summary", "description")]).casefold()
        return [_stem(t) for t in re.findall(r"[a-z0-9][a-z0-9_-]{2,}", blob)
                if t not in GENERIC_WORDS and t not in STOPWORDS and t not in self.topic_words
                and not re.fullmatch(r"(19|20)\d{2}", t)]

    @staticmethod
    def _tfidf(term_lists: Mapping[str, list[str]]) -> dict[str, dict[str, float]]:
        n = len(term_lists)
        df = Counter(t for terms in term_lists.values() for t in set(terms))
        vectors = {}
        for key, terms in term_lists.items():
            tf = Counter(terms)
            vec = {t: (1.0 + math.log(c)) * (math.log((n + 1) / (df[t] + 1)) + 1.0) for t, c in tf.items()}
            norm = math.sqrt(sum(v * v for v in vec.values())) or 1.0
            vectors[key] = {t: v / norm for t, v in vec.items()}
        return vectors

    @staticmethod
    def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
        if len(left) > len(right):
            left, right = right, left
        return sum(v * right.get(t, 0.0) for t, v in left.items())

    @staticmethod
    def pair_matches(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
        """Return True only when two rows are plausibly the same occurrence (v2.2)."""
        cosine = UnderlyingEventResolver._cosine(left.get("vector") or {}, right.get("vector") or {})
        left_anchor, right_anchor = _clean(left.get("primary_anchor")), _clean(right.get("primary_anchor"))
        left_proper = set(left.get("proper_tokens") or ())
        right_proper = set(right.get("proper_tokens") or ())
        same_family = bool(left.get("family") and left.get("family") == right.get("family")
                           and left.get("family") not in {"other", "unknown"})
        same_actor = bool(left.get("actor") and left.get("actor") == right.get("actor"))
        date_distance = _date_distance_days(_clean(left.get("date")), _clean(right.get("date")))
        if date_distance is not None and date_distance > MAX_DAYS_APART:
            return False
        # Argument incompatibility: each report names a different object (target,
        # product, person) and neither mentions the other's -> different occurrences.
        if (left_anchor and right_anchor and left_anchor != right_anchor
                and left.get("anchor_named") and right.get("anchor_named")
                and left_anchor not in right_proper and right_anchor not in left_proper):
            return False
        # Different named subjects of otherwise templated sentences ("Boko spoke
        # with theCUBE..." / "Singh spoke with theCUBE...") are different events.
        left_subject, right_subject = _clean(left.get("subject")), _clean(right.get("subject"))
        if (left_subject and right_subject and left_subject != right_subject
                and left_subject not in right_proper and right_subject not in left_proper):
            return False
        if cosine >= COSINE_SAME_STORY:
            return True
        if same_actor and same_family:
            if left_anchor and left_anchor == right_anchor and left.get("anchor_named"):
                return True
            return cosine >= COSINE_SAME_ACTOR
        return False

    def resolve(
        self,
        events: Sequence[Mapping[str, Any]],
    ) -> tuple[dict[str, str], dict[str, dict[str, Any]]]:
        """Returns ({event_id: underlying_event_id}, {underlying_event_id: metadata})."""
        by_id = {
            _clean(e.get("event_id")).upper(): e
            for e in events
            if isinstance(e, Mapping) and _clean(e.get("event_id"))
        }
        ids = sorted(by_id)
        vectors = self._tfidf({eid: self.text_terms(by_id[eid]) for eid in ids})
        features: dict[str, dict[str, Any]] = {}
        for eid in ids:
            event = by_id[eid]
            features[eid] = {
                "actor": _norm_actor(self.actor_key(event)),
                "family": _clean(self.family(event)),
                "source": _clean(event.get("source_id")).upper(),
                "date": _date_key(event),
                "tokens": self.tokens(event),
                "proper_tokens": self.proper_tokens(event),
                "primary_anchor": self.occurrence_anchor(event),
                "anchor_named": bool(self.named_anchor(event)),
                "subject": self.subject(event),
                "numbers": _numbers(event),
                "vector": vectors.get(eid, {}),
            }

        match: dict[tuple[str, str], bool] = {}
        scored: list[tuple[float, str, str]] = []
        for i, left in enumerate(ids):
            for right in ids[i + 1 :]:
                ok = self.pair_matches(features[left], features[right])
                match[(left, right)] = match[(right, left)] = ok
                if ok:
                    shared = features[left]["tokens"] & features[right]["tokens"]
                    union = features[left]["tokens"] | features[right]["tokens"]
                    proper_shared = features[left]["proper_tokens"] & features[right]["proper_tokens"]
                    anchor_shared = bool(
                        features[left].get("primary_anchor")
                        and features[left].get("primary_anchor") == features[right].get("primary_anchor")
                    )
                    similarity = len(shared) / len(union) if union else 0.0
                    # Occurrence/named-anchor matches sort ahead of lexical matches.
                    scored.append((-(2.0 if (anchor_shared or proper_shared) else similarity), left, right))

        # Complete linkage prevents transitive chaining from combining distinct
        # occurrences through one ambiguous middle report.
        group_of = {eid: {eid} for eid in ids}
        for _, left, right in sorted(scored):
            a, b = group_of[left], group_of[right]
            if a is b:
                continue
            if all(match.get((x, y), False) for x in a for y in b):
                merged = a | b
                for eid in merged:
                    group_of[eid] = merged
        groups = {frozenset(g) for g in group_of.values()}

        event_to_group: dict[str, str] = {}
        metadata: dict[str, dict[str, Any]] = {}
        for members_frozen in sorted(groups, key=lambda g: sorted(g)):
            members = sorted(members_frozen)
            group_id = self._stable_id(members, features, taken=metadata)
            dates = sorted({features[e]["date"] for e in members if features[e]["date"]})
            figures = sorted({n for e in members for n in features[e]["numbers"]})
            anchor_tokens = self._group_anchor_tokens(members, features)
            metadata[group_id] = {
                "underlying_event_id": group_id,
                "member_event_ids": members,
                "member_count": len(members),
                "source_ids": sorted({features[e]["source"] for e in members if features[e]["source"]}),
                "dates": dates,
                "actor_keys": sorted({features[e]["actor"] for e in members if features[e]["actor"]}),
                "event_families": sorted({features[e]["family"] for e in members if features[e]["family"]}),
                "occurrence_anchor_tokens": anchor_tokens,
                "reported_figures": figures if len(members) > 1 else [],
                "resolver_version": VERSION,
            }
            for eid in members:
                event_to_group[eid] = group_id
        return event_to_group, metadata

    @staticmethod
    def _group_anchor_tokens(
        members: Sequence[str],
        features: Mapping[str, Mapping[str, Any]],
    ) -> list[str]:
        """Stable occurrence anchors, preferring named tokens.

        The anchor is deliberately independent of member EVENT ids, report count
        and report week. A token reported by more sources wins; ties are lexical,
        so adding a semantically equivalent report that repeats the same named
        target does not rename the occurrence.
        """
        primary = Counter(
            _clean(features[eid].get("primary_anchor"))
            for eid in members
            if _clean(features[eid].get("primary_anchor")) and features[eid].get("anchor_named")
        )
        proper = Counter(
            token for eid in members for token in (features[eid].get("proper_tokens") or set())
        )
        lexical = Counter(token for eid in members for token in (features[eid].get("tokens") or set()))

        if primary:
            top_n = max(primary.values())
            anchors = sorted(token for token, count in primary.items() if count == top_n)
            return anchors[:2]

        if proper:
            top_n = max(proper.values())
            anchors = sorted(token for token, count in proper.items() if count == top_n)
            # Usually target/place/product; two anchors reduce accidental collision.
            return anchors[:2]

        if lexical:
            top_n = max(lexical.values())
            anchors = sorted(token for token, count in lexical.items() if count == top_n)
            return anchors[:2]

        return []

    @classmethod
    def _stable_id(
        cls,
        members: Sequence[str],
        features: Mapping[str, Mapping[str, Any]],
        taken: Mapping[str, Any],
    ) -> str:
        actors = Counter(features[e].get("actor") for e in members if features[e].get("actor"))
        families = Counter(features[e].get("family") for e in members if features[e].get("family"))
        anchors = cls._group_anchor_tokens(members, features)

        def top(counter: Counter) -> str:  # ties broken by value, not by member order
            return sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] if counter else ""

        facts: dict[str, Any] = {
            "actor": top(actors),
            "family": top(families),
            "anchor": anchors,
        }

        # Absolute fallback only when no factual occurrence anchor exists.
        if not facts["actor"] and not anchors:
            dates = sorted(features[e].get("date") for e in members if features[e].get("date"))
            facts["date"] = dates[0] if dates else ""
            facts["members"] = list(members)

        base = "UNDERLYINGEVENT-" + hashlib.sha256(
            json.dumps(facts, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()[:24].upper()
        # Groups reaching this point were already separated by complete linkage.
        # If two distinct groups share the same actor/family/anchor signature,
        # they must still receive different IDs (for example repeat partnerships
        # months apart). Cross-run continuity is reconciled by EventMemoryService.
        group_id, suffix = base, 2
        while group_id in taken:
            group_id = f"{base}-{suffix}"
            suffix += 1
        return group_id
