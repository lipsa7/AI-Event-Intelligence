"""Screen search results before any page is fetched; top up with Exa (v13.15, 20 Nov 2026).

Why
---
RUN-FF8F59D21FDD4ACDB17E5B8F (topic "artificial intelligence") filled about a third of its 20 source
slots with pages that mention the topic's words but report no event: the trailer for a film called
"Artificial", "Artificial Intelligence for the Z80", Cambridge Dictionary's word of the year, a
release-site post, a survivalist blog. The topic filter (embeddings, or keywords without Ollama)
keeps them because they ARE about AI in a loose sense. Whether a headline reports a concrete event -
an organisation, government, court or company doing something - is a judgment, so one model call
makes it for the whole list, from headline, description and site only, before any fetch.

Rules
-----
* One call per discovery search; nothing is fetched to decide.
* Fail-open: if the model fails, or would drop every result, all results are kept.
* Dropped headlines and the reason are returned, so the run log shows what was screened out.
* ``exa_top_up``: on the first search of a run, when fewer than ``target`` news results remain,
  Exa search adds results (within its free-credit budget, services/exa_contents.py), and they are
  screened the same way.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

VERSION = "discovery-screen-v1"
MAX_ITEMS = 60
EXA_QUERIES = 2


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _site(item: Mapping[str, Any]) -> str:
    url = _clean(item.get("canonical_url") or item.get("url") or item.get("link"))
    return urlparse(url).netloc.replace("www.", "") or _clean(item.get("source_name") or item.get("source"))


def prompt(topic: str, items: Sequence[Mapping[str, Any]]) -> str:
    lines = []
    for n, item in enumerate(items, start=1):
        desc = _clean(item.get("description") or item.get("snippet"))[:220]
        lines.append(f"{n}. [{_site(item)}] {_clean(item.get('title'))[:200]}" + (f" - {desc}" if desc else ""))
    return (
        f"Research topic: {topic}\n"
        "Below are search results (site, headline, description). For each, decide from these alone whether it "
        "REPORTS A CONCRETE NEWS EVENT relevant to the topic: a named company, government, regulator, court, "
        "lab or other organisation did, decided, announced, disclosed, filed, ruled or published something, or a "
        "dated measurable change happened. SKIP results that only mention the topic's words: films, TV, books, "
        "games and their trailers or reviews; hobby projects and tutorials or how-tos; product reviews, deals "
        "and listicles; dictionary, quiz or culture pieces; satire; pages that are a login, access or browser "
        "check; general opinion with no new event. When unsure, keep it.\n\n"
        + "\n".join(lines)
        + '\n\nReturn JSON {"skip":[{"n":3,"why":"film trailer"}]} listing ONLY the results to skip.'
    )


def screen(topic: str, candidates: Sequence[Mapping[str, Any]], ask: Any = None,
           min_items: int = 3) -> tuple[list[dict], dict]:
    """(kept candidates, summary). Never raises; never drops everything."""
    items = [dict(c) for c in candidates if isinstance(c, Mapping)]
    summary: dict[str, Any] = {"version": VERSION, "checked": 0, "kept": len(items), "dropped": 0}
    if ask is None or len(items) < min_items:
        summary["status"] = "skipped"
        return items, summary
    head, tail = items[:MAX_ITEMS], items[MAX_ITEMS:]
    try:
        reply = ask(prompt(topic, head))
        data = reply if isinstance(reply, Mapping) else json.loads(
            str(reply or "{}").strip().strip("`").removeprefix("json").strip() or "{}")
        skip: dict[int, str] = {}
        for row in data.get("skip") or []:
            if isinstance(row, Mapping):
                try:
                    n = int(row.get("n"))
                except (TypeError, ValueError):
                    continue
                if 1 <= n <= len(head):
                    skip[n] = _clean(row.get("why"))[:60]
    except Exception as error:
        summary["status"] = f"failed:{type(error).__name__}"
        return items, summary
    kept = [item for n, item in enumerate(head, start=1) if n not in skip] + tail
    summary["checked"] = len(head)
    if not kept:
        summary["status"] = "kept_all:model_dropped_everything"
        return items, summary
    summary.update(status="ok", kept=len(kept), dropped=len(skip),
                   dropped_examples=[{"title": _clean(head[n - 1].get("title"))[:120], "site": _site(head[n - 1]),
                                      "why": why} for n, why in sorted(skip.items())][:20])
    return kept, summary


def _plain_queries(topic: str, queries: Sequence[str]) -> list[str]:
    """Natural-language queries for Exa (NewsAPI's AND/OR syntax is not used there)."""
    topic = _clean(topic)
    out = [f"{topic} news: government, regulator, court or company action",
           f"{topic} latest developments announced this month"]
    return out[:EXA_QUERIES]


def exa_top_up(topic: str, kept: Sequence[Mapping[str, Any]], queries: Sequence[str], *, target: int,
               ask: Any = None, search: Any = None) -> list[dict[str, Any]]:
    """Extra candidates from Exa search when fewer than `target` remain, screened like the rest."""
    if len(kept) >= target:
        return []
    if search is None:
        from services import exa_contents
        search = exa_contents.search
    known = {_clean(k.get("canonical_url") or k.get("url")).rstrip("/") for k in kept}
    added: list[dict[str, Any]] = []
    for query in _plain_queries(topic, queries):
        try:
            found = search(query)
        except Exception:
            found = None
        if not found:
            continue
        for row in found:
            url = _clean(row.get("url")).rstrip("/")
            if not url or url in known:
                continue
            known.add(url)
            added.append({"url": row.get("url"), "canonical_url": row.get("url"), "title": row.get("title"),
                          "description": row.get("snippet"), "snippet": row.get("snippet"),
                          "published_at": row.get("published_at"), "source_name": _site(row),
                          "provider": "exa_search", "_discovery_query": query, "_discovery_query_index": 99})
        if len(kept) + len(added) >= target:
            break
    if added and ask is not None:
        added, _ = screen(topic, added, ask=ask, min_items=1)
    return added
