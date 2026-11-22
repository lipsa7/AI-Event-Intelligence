"""One story per exploration slot (v13.19, 22 Nov 2026).

Why
---
RUN-ACC0E1EDB8AA42C192CCA5A5 (replay of RUN-4367, topic "artificial intelligence") gave three of its
four exploration slots to ONE story: California's lawsuit against OpenAI over its agents hacking
Hugging Face arrived as "legal action taken over OpenAI agents hacking Hugging Face", "change,
chatgpt-maker, practices - OpenAI" and "internal, agents, evaluation - OpenAI" (the July incident the
lawsuit is about). The word-overlap merge (services/same_story.py) cannot join them: different outlets
frame the lawsuit and the incident in different words (measured on the checkpoint: 0.39 and 0.11
overlap, while the lawsuit and an unrelated Nvidia item also share "Hugging Face"). The generator then
read the same story three times and wrote the same kind of conclusion; 5 of 7 candidates restated it.

Whether two headlines are the same story or its direct follow-up (an incident and the lawsuit about
it) is a judgment, so ONE model call per run makes it, from each development's label and two lead
sentences. Developments in one family are joined, so the exploration reads every report of the story
together in one slot and the other slots go to other stories.

Rules
-----
* One call per run (cached by the set of development keys); nothing else is asked.
* Fail-open: on any model failure or unusable answer, the developments are left as they were.
* Only explicit families of 2+ listed numbers are joined; a family is never joined with another.
* The joined development keeps the first (highest-ranked) member's key and label.
"""
from __future__ import annotations

import json
from typing import Any, Callable, Mapping, Sequence

VERSION = "story-families-v1"
MAX_ITEMS = 12


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _lead(group: Mapping[str, Any]) -> str:
    seen: list[str] = []
    for event in group.get("events") or []:
        text = _clean(event.get("summary") or event.get("title"))[:200]
        if text and text not in seen:
            seen.append(text)
        if len(seen) >= 2:
            break
    return " / ".join(seen)


def prompt(groups: Sequence[Mapping[str, Any]]) -> str:
    lines = [f"{n}. {_clean(g.get('label') or g.get('key'))[:120]} :: {_lead(g)}"
             for n, g in enumerate(groups, start=1)]
    return (
        "Below are news developments, each with a label and one or two lead sentences from its reports.\n"
        "Find developments that are about the SAME specific story: the same incident, deal, case or decision, "
        "including its direct follow-ups (an incident and a lawsuit or investigation about that incident; an "
        "announcement and reports of the same announcement). Do NOT group developments that only share a "
        "company, a country, a theme or the topic (two different OpenAI stories are different stories).\n\n"
        + "\n".join(lines)
        + '\n\nReturn JSON {"families":[[1,4,5]]} listing ONLY groups of 2 or more numbers that are one story. '
          'Return {"families":[]} if every development is a different story.'
    )


def _parse(answer: Any) -> Any:
    if isinstance(answer, Mapping):
        return answer
    text = _clean(answer)
    first, last = text.find("{"), text.rfind("}")
    if first < 0 or last <= first:
        return {}
    try:
        return json.loads(text[first:last + 1])
    except json.JSONDecodeError:
        return {}


def families(groups: Sequence[Mapping[str, Any]], ask: Callable[[str], Any]) -> list[list[int]]:
    """0-based index lists (2+ members each, disjoint) of developments that are one story."""
    items = list(groups)[:MAX_ITEMS]
    if len(items) < 2:
        return []
    data = _parse(ask(prompt(items)))
    out: list[list[int]] = []
    used: set[int] = set()
    for family in data.get("families") or [] if isinstance(data, Mapping) else []:
        if not isinstance(family, (list, tuple)):
            continue
        members: list[int] = []
        for value in family:
            try:
                index = int(value) - 1
            except (TypeError, ValueError):
                continue
            if 0 <= index < len(items) and index not in used and index not in members:
                members.append(index)
        if len(members) >= 2:
            members.sort()
            used.update(members)
            out.append(members)
    return out


def join(groups: Sequence[dict[str, Any]], found: Sequence[Sequence[int]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(developments with each family joined into its first member, log of joins)."""
    absorbed: dict[int, int] = {}
    for family in found:
        for other in family[1:]:
            absorbed[other] = family[0]
    joined_log: list[dict[str, Any]] = []
    out: list[dict[str, Any]] = []
    for index, group in enumerate(groups):
        if index in absorbed:
            continue
        members = [i for i, target in absorbed.items() if target == index]
        if members:
            group = dict(group)
            group["event_ids"] = list(group.get("event_ids") or [])
            group["evidence_ids"] = list(group.get("evidence_ids") or [])
            group["events"] = list(group.get("events") or [])
            group["underlying"] = set(group.get("underlying") or set())
            group["actors"] = set(group.get("actors") or set())
            for i in sorted(members):
                other = groups[i]
                group["event_ids"] += [e for e in other.get("event_ids") or [] if e not in group["event_ids"]]
                group["evidence_ids"] += [e for e in other.get("evidence_ids") or [] if e not in group["evidence_ids"]]
                group["events"] += list(other.get("events") or [])
                group["underlying"] |= set(other.get("underlying") or set())
                group["actors"] |= set(other.get("actors") or set())
            joined_log.append({"kept": _clean(group.get("label") or group.get("key"))[:120],
                               "joined": [_clean(groups[i].get("label") or groups[i].get("key"))[:120]
                                          for i in sorted(members)]})
        out.append(group)
    return out, joined_log
