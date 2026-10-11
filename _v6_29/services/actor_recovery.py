"""Recover the acting institution for events the extractor left without one (v6.20.4).

On this app's own data, 148 of 400 extracted events (37%) had no acting
institution, so admission dropped them as "specific_acting_institution_missing".
Most were not vague: the actor was a named person acting for an organization
("Nvidia's Jensen Huang said…", "Pedro Andrade, vice president of AI at
Talkdesk…"), a head of government or legislator ("President Donald Trump…",
"Senator Bernie Sanders…"), a country ("France and Germany pledged…"), or a
company the extractor typed "unknown" ("Poke launched…").

Rules, all evidence-based on the article's own text (no world knowledge):
1. A person with a stated affiliation in the same article -> that organization
   ("<Org>'s <Name>", "<Org> CEO <Name>", "<Name>, <title> at/of <Org>",
   "At <Org>, <title> <Name>").
2. A person with a stated public office in the same article ("President",
   "Prime Minister", "Senator", ...) -> that named official.
3. A country or government mention in the actor role -> that country.
4. A capitalized name in the actor role that opens the sentence
   ("Poke launched…") -> that name, unless it is a person without 1 or 2.
Vague groups ("executives", "lawmakers", "tech companies") stay without an
actor, as before. Affiliations are collected across all events of the same
article, because the title usually appears once ("President Donald Trump") and
later sentences say only "Trump".
"""

from __future__ import annotations

import re
from typing import Any, Iterable, Mapping

VERSION = "actor-recovery-v1"

PERSON_TYPES = {"person", "individual", "executive", "founder", "ceo", "employee", "spokesperson",
                "researcher", "analyst", "unverified"}
COUNTRY_TYPES = {"country", "nation", "state", "government", "government_body", "government_agency"}
OFFICE_TITLES = (
    r"President|Vice President|Prime Minister|Premier|Chancellor|Senator|Sen\.|Representative|Rep\.|"
    r"Congressman|Congresswoman|Governor|Gov\.|Minister|Secretary|Mayor|Speaker|King|Queen|Commissioner"
)
ROLE_WORDS = (
    r"CEO|chief executive(?: officer)?|chief [a-z]+ officer|CTO|CFO|COO|founder|co-founder|cofounder|"
    r"president|vice president|chair(?:man|woman)?|head|director|boss|spokesperson|spokesman|spokeswoman|"
    r"general counsel|executive|researcher|scientist|engineer|lead|manager|partner|analyst"
)
VAGUE = frozenset("""
executives executive officials official lawmakers leaders companies company firms firm industry
investors analysts researchers people users experts critics officer officers group groups team staff
employees workers government governments regulators agencies startups startup tech
the a an two three both several many some countries country nations nation leaders from of and
""".split())
LEADING_NOISE = re.compile(r"^(?:(?:The|A|An|At|In|On|For|Longtime|Former|Veteran|Senior|Then|Ex|Current|"
                           r"Interim|Acting|Chief|Top|Its|His|Her|Their)\s+)+")
TOPIC_ORGS = frozenset({"ai", "agi", "science", "technology", "research", "policy", "product", "engineering",
                        "safety", "security", "data", "machine learning"})
_NAME = r"[A-Z][\w.&'-]+(?:\s+[A-Z][\w.&'-]+){0,3}"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _text(event: Mapping[str, Any]) -> str:
    return " ".join(_clean(event.get(k)) for k in ("title", "summary", "description") if _clean(event.get(k)))


def _surname(name: str) -> str:
    parts = [p for p in re.split(r"\s+", name) if p]
    return parts[-1] if parts else ""


def _mentions(event: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [m for m in event.get("entity_mentions") or [] if isinstance(m, Mapping)]


def _is_vague(name: str) -> bool:
    words = [w.casefold() for w in re.findall(r"[A-Za-z]+", name)]
    return not words or all(w in VAGUE or not w[:1].isalpha() for w in words) or name[:1].islower()


def article_context(events: Iterable[Mapping[str, Any]]) -> dict[str, dict[str, str]]:
    """{surname: {"org": ..., "office": full name}} learnt from one article's events."""
    context: dict[str, dict[str, str]] = {}
    for event in events:
        text = _text(event)
        # Every "Title Firstname Lastname" in the text, whether or not it was extracted as a mention.
        for match in re.finditer(rf"\b(?:{OFFICE_TITLES})\s+((?:[A-Z][\w.'-]+\s+){{0,2}}[A-Z][\w.-]+)", text):
            full = re.sub(r"'s$", "", _clean(match.group(1)))
            if full:
                entry = context.setdefault(_surname(full).casefold(), {})
                if len(full.split()) > len(entry.get("office", "").split()):
                    entry["office"] = full
        for mention in _mentions(event):
            if _clean(mention.get("entity_type")).casefold() not in PERSON_TYPES:
                continue  # affiliations and offices are learnt for people only
            name = _clean(mention.get("mention") or mention.get("name"))
            name = re.sub(r"^(?:" + OFFICE_TITLES + r")\s+", "", name)
            if not name or not name[:1].isupper() or len(name) < 3:
                continue
            surname = _surname(name)
            entry = context.setdefault(surname.casefold(), {})
            esc = re.escape(name)
            esc_s = re.escape(surname)
            patterns = [
                rf"({_NAME})'s\s+(?:{ROLE_WORDS})?\s*(?:{esc}|{esc_s})\b",
                rf"({_NAME})\s+(?:{ROLE_WORDS})\s+(?:{esc}|{esc_s})\b",
                rf"(?:{esc}|{esc_s}),\s+(?:the\s+)?(?:[\w-]+\s+){{0,4}}(?:{ROLE_WORDS})(?:\s+[\w-]+){{0,4}}?\s+(?:at|of)\s+(?:the\s+)?({_NAME})",
                rf"(?:{esc}|{esc_s}),\s+(?:the\s+)?(?:[\w-]+\s+){{0,4}}(?:{ROLE_WORDS})(?:\s+[\w-]+){{0,4}}\s+(?:at|of)\s+(?:the\s+)?({_NAME})",
                rf"(?:{esc}|{esc_s})\s+(?:will become|became|is|was|has been|, who is)\s+(?:an?\s+|the\s+)?({_NAME})'s\s+(?:[\w-]+\s+){{0,2}}(?:{ROLE_WORDS})",
                rf"\bAt\s+({_NAME}),\s+(?:[\w-]+\s+){{0,3}}(?:{ROLE_WORDS})\s+(?:{esc}|{esc_s})\b",
            ]
            for pattern in patterns:
                for match in re.finditer(pattern, text):
                    if entry.get("org"):
                        break
                    org = LEADING_NOISE.sub("", _clean(match.group(1)))
                    if (org and not _is_vague(org) and org.casefold() != name.casefold()
                            and org.casefold() not in TOPIC_ORGS and not re.search(OFFICE_TITLES, org)):
                        entry["org"] = org
            # Public office: capture the full name after the title as the text states it.
            for match in re.finditer(rf"\b(?:{OFFICE_TITLES})\s+((?:[A-Z][\w.'-]+\s+){{0,2}}{esc_s})\b", text):
                full = _clean(match.group(1))
                if len(full.split()) > len(entry.get("office", "").split()):
                    entry["office"] = full
    return context


def recover(event: Mapping[str, Any], context: Mapping[str, Mapping[str, str]]) -> tuple[str, str]:
    """(actor, reason) for an event with no acting institution; ("", "") if none is stated."""
    mentions = _mentions(event)
    actors = [m for m in mentions if _clean(m.get("role")).casefold() in {"actor", ""}]
    title = _clean(event.get("title") or event.get("summary"))
    for mention in actors:
        name = _clean(mention.get("mention") or mention.get("name"))
        name = re.sub(r"^(?:" + OFFICE_TITLES + r")\s+", "", name)
        kind = _clean(mention.get("entity_type")).casefold()
        if not name or _is_vague(name):
            continue
        known = context.get(_surname(name).casefold(), {}) if kind in PERSON_TYPES else {}
        # A public office wins over an affiliation, so one official keeps one
        # actor key across articles ("Donald Trump", not "U.S." in one and his
        # name in another).
        if known.get("office"):
            return known["office"], "public_office"
        if known.get("org"):
            return known["org"], "person_affiliation"
        if kind in COUNTRY_TYPES:
            return LEADING_NOISE.sub("", name), "country_or_government"
        if kind not in PERSON_TYPES and title.startswith(name.split()[0]):
            return name, "named_subject"
    return "", ""


def recover_events(events: list[dict[str, Any]], has_actor) -> dict[str, int]:
    """Fill primary_actor in place for events without one; returns counts by reason."""
    by_source: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        by_source.setdefault(_clean(event.get("source_id")), []).append(event)
    counts: dict[str, int] = {}
    contexts = {source: article_context(source_events) for source, source_events in by_source.items()}
    # One official, one name across the run: "Trump" in one article and "Donald
    # Trump" in another both become the longest name any article stated.
    longest: dict[str, str] = {}
    for context in contexts.values():
        for surname, entry in context.items():
            office = entry.get("office", "")
            if len(office.split()) > len(longest.get(surname, "").split()):
                longest[surname] = office
    for context in contexts.values():
        for surname, entry in context.items():
            if entry.get("office") and longest.get(surname):
                entry["office"] = longest[surname]
    # Run-level fallback: an affiliation or office stated in another article of
    # the run, used only when the name is unambiguous across the run.
    by_full: dict[str, dict[str, str]] = {}
    surnames: dict[str, set[str]] = {}
    for source_events in by_source.values():
        for event in source_events:
            for mention in _mentions(event):
                full = re.sub(r"^(?:" + OFFICE_TITLES + r")\s+", "", _clean(mention.get("mention")))
                if len(full.split()) >= 2 and full[:1].isupper():
                    surnames.setdefault(_surname(full).casefold(), set()).add(full.casefold())
    for context in contexts.values():
        for surname, entry in context.items():
            target = by_full.setdefault(surname, {})
            for key in ("office", "org"):
                if entry.get(key) and not target.get(key):
                    target[key] = entry[key]
    run_context = {s: e for s, e in by_full.items() if len(surnames.get(s, set())) <= 1}
    for source, source_events in by_source.items():
        context = {**run_context, **{k: v for k, v in contexts[source].items() if v}}
        for event in source_events:
            if has_actor(event):
                continue
            actor, reason = recover(event, context)
            if actor:
                event["primary_actor"] = actor
                event["actor_recovered_from"] = reason
                counts[reason] = counts.get(reason, 0) + 1
    return counts
