"""Insight potential: which stories are worth digging into (v13, 18 Nov 2026).

Until v12 the app ranked stories (developments, clusters) by how much coverage they had: the
number of distinct events and sources. Startup funding rounds get a lot of near-identical
coverage, so they ranked first and received the follow-up searches and the analysis, though a
funding round rarely holds anything non-obvious. In a simulated deep run (18 Nov) this left the
generator with nothing it could back, and the owner's one kept insight (governments learned of an
AI-agent breach about three months late) came from government and incident news. The app serves any
topic; these signals are general (regulators, courts, lawsuits, recalls, outages, delays, gaps) and
the funding and launch penalties are switched off when the topic is about funding or launches.

This module scores a story by the signals that recur in intelligence practice and in the owner's
verdicts (research report "Fixing AI Event Intelligence", 18 Nov 2026):

* an official actor: government, regulator, court, legislature, standards body;
* incident or enforcement language: breach, incident, lawsuit, fine, ban, investigation;
* a computable gap: two dates far apart, or two amounts in one sentence (services/delta_extractor);
* change against a baseline: first, reverses, suspends, delays, withdraws;
* minus a funding-only or launch-only story.

Coverage still counts, but as a tie-breaker. The score and its parts are logged, so a run shows
why each story was chosen. No model calls.
"""
from __future__ import annotations

import re
from typing import Any, Iterable, Mapping, Sequence

VERSION = "insight-potential-v1.1-any-topic"

OFFICIAL = re.compile(
    r"\b(government|ministry|minister|department of|regulator|regulatory|commission|parliament|congress|senate|"
    r"legislat\w*|lawmakers?|court|judge|attorney general|prosecutor|agency|authority|ombudsman|white house|"
    r"executive order|federal register|ftc|fcc|sec\b|doj|nist|ofcom|ico\b|cnil|ai office|eu ai act|european "
    r"commission|european parliament|state of [A-Z]\w+|governor|treasury|commerce department|meity|niti aayog|"
    r"rbi\b|sebi\b|cma\b|aisi|ai safety institute|un\b|oecd|g7|council)\b", re.I)
INCIDENT = re.compile(
    r"\b(breach\w*|incident\w*|leak\w*|hack\w*|outage\w*|lawsuit\w*|sued|sues|litigation|fine[ds]?|penalt\w*|"
    r"ban(?:s|ned)?|probe[ds]?|investigat\w*|enforcement|violat\w*|recall\w*|harm\w*|misuse\w*|deepfake\w*|"
    r"complain\w*|warning letter|consent decree|settle\w*|subpoena\w*|injunction\w*|audit\w*|"
    r"non-?complian\w*|disclos\w*|reported to)\b", re.I)
CHANGE = re.compile(
    r"\b(first[- ]ever|for the first time|first time|revers\w*|suspend\w*|delay\w*|withdr[ae]w\w*|overturn\w*|"
    r"scrap\w*|halt\w*|paus\w*|U-turn|backtrack\w*|reject\w*|blocked|deadline|missed|postpon\w*|rescind\w*)\b",
    re.I)
FUNDING = re.compile(
    r"\b(rais(?:es|ed|ing)|series [a-f]\b|seed round|funding round|valuation|valued at|led by .{0,40}"
    r"(capital|ventures|partners)|investors?|unicorn)\b", re.I)
LAUNCH = re.compile(r"\b(launch(?:es|ed)?|unveil\w*|introduc\w*|rolls? out|released?|announces? (a |its )?new)\b",
                    re.I)

WEIGHTS = {"official_actor": 3.0, "incident_or_enforcement": 3.0, "computable_gap": 2.5, "change_vs_baseline": 1.5,
           "funding_only": -4.0, "launch_only": -2.0}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _texts(events: Iterable[Mapping[str, Any]], evidence: Iterable[Mapping[str, Any]]) -> list[str]:
    out = []
    for item in list(events or []) + list(evidence or []):
        if not isinstance(item, Mapping):
            continue
        for key in ("title", "summary", "text", "claim", "exact_excerpt"):
            text = _clean(item.get(key))
            if text:
                out.append(text)
        for actor in item.get("actors") or []:
            name = _clean(actor.get("name") if isinstance(actor, Mapping) else actor)
            if name:
                out.append(name)
    return out


TOPIC_IS_FUNDING = re.compile(r"\b(fund\w*|invest\w*|venture|vc|start-?ups?|raise[sd]?|valuations?|capital|"
                              r"deals?|m&a|acquisitions?|ipos?)\b", re.I)
TOPIC_IS_LAUNCHES = re.compile(r"\b(launch\w*|products?|releases?|models?|features?|devices?)\b", re.I)


def _real_incident(text: str, match: "re.Match[str]") -> bool:
    """v13.9 (20 Nov 2026): "disclosed" is incident language for a breach ("disclosed in September")
    but not in "Arceus Legal today disclosed that it has raised $17M" or "terms were not disclosed",
    which let funding stories escape the funding-only penalty."""
    if not match.group(0).casefold().startswith("disclos"):
        return True
    window = text[max(0, match.start() - 25):match.end() + 60]
    return not (FUNDING.search(window) or re.search(r"\b(not|terms|undisclosed)\b", window, re.I))


def score(events: Sequence[Mapping[str, Any]], evidence: Sequence[Mapping[str, Any]] = (),
          topic: str = "") -> dict[str, Any]:
    """Insight potential of one story from its events and evidence records.

    v13.1 (19 Nov 2026): the app serves ANY topic. The funding-only and launch-only penalties
    apply only when the topic is not itself about funding or launches: for "startup funding in
    India" a funding round is the story, not noise."""
    texts = _texts(events, evidence)
    blob = " ".join(texts)
    parts: dict[str, float] = {}
    if OFFICIAL.search(blob):
        parts["official_actor"] = WEIGHTS["official_actor"]
    if any(_real_incident(blob, m) for m in INCIDENT.finditer(blob)):
        parts["incident_or_enforcement"] = WEIGHTS["incident_or_enforcement"]
    if CHANGE.search(blob):
        parts["change_vs_baseline"] = WEIGHTS["change_vs_baseline"]
    try:
        from services import delta_extractor

        rows = [{"fact_id": f"T{i}", "development": "story", "text": text,
                 "date": _clean((list(events or []) + [{}])[0].get("published_at") if events else "")}
                for i, text in enumerate(texts[:40])]
        if delta_extractor.compute(rows, limit=1):
            parts["computable_gap"] = WEIGHTS["computable_gap"]
    except Exception:
        pass
    sentences = [t for t in texts if len(t) > 30]
    if sentences:
        funding_share = sum(1 for t in sentences if FUNDING.search(t)) / len(sentences)
        launch_share = sum(1 for t in sentences if LAUNCH.search(t)) / len(sentences)
        no_signal = not ({"official_actor", "incident_or_enforcement"} & set(parts))
        # "Only" means at least three quarters of the story's sentences: a story about a bank
        # deploying AI agents with one sentence on another bank's funding is not a funding story.
        if funding_share >= 0.75 and no_signal and not TOPIC_IS_FUNDING.search(topic or ""):
            parts["funding_only"] = WEIGHTS["funding_only"]
        elif launch_share >= 0.75 and no_signal and not TOPIC_IS_LAUNCHES.search(topic or ""):
            parts["launch_only"] = WEIGHTS["launch_only"]
    return {"score": round(sum(parts.values()), 2), "parts": parts}


def rank_key(potential: Mapping[str, Any], underlying_events: int, sources: int) -> tuple:
    """Sort key: insight potential first, coverage as the tie-breaker."""
    return (-float(potential.get("score") or 0.0), -int(underlying_events), -int(sources))
