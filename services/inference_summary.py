"""The report always leads with an inference (26 Oct 2026).

The user's rule "the report is never blank" means the reader always gets some inference, not
only restated news. RUN-3E2FE released a report whose implications were all "widely expected",
so its only non-obvious reasoning sat further down in the hypothesis sections.

lead(): the strongest inference in a report, with its label, so the dashboard can show it first.
Fall-through order, strongest claim first; each keeps its own label and is never relabelled:

    hidden_insight        passed every check
    implication           forward implication beyond the headlines
    verified_hypothesis   lab hypothesis whose thesis and mechanism the sources back
    analysis              lab hypothesis: sourced facts, every link backed but one stated assumption
    hypothesis            "Hypotheses to investigate" (services/hypothesis_tier.py)
    analogy               a dated past case with an observed outcome
    expected_implication  backed, but headline readers already expect it
    move                  a next step for an actor, with its basis

inventory(): how many of each a report has, so a report without any inference is flagged
(inference_count == 0) instead of being counted as complete.
"""

from __future__ import annotations

from typing import Any, Mapping

VERSION = "inference-summary-v1"
LABELS = {
    "hidden_insight": "Hidden insight - every claim checked against its sources",
    "implication": "Forward implication - backed by the sources, beyond the headlines",
    "verified_hypothesis": "Hypothesis - its thesis and reasoning are backed by the sources",
    "analysis": "Analysis - the facts are sourced; one reasoning step is a stated assumption",
    "hypothesis": "Hypothesis to investigate - rests on sourced facts plus unstated reasoning",
    "analogy": "Historical analogy - a dated past case with an observed outcome",
    "expected_implication": "Expected implication - backed, but already implied by the headlines",
    "move": "Possible next step for an actor - with the facts it rests on",
}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _items(value: Any) -> list[Mapping[str, Any]]:
    return [v for v in value if isinstance(v, Mapping)] if isinstance(value, (list, tuple)) else []


def _candidates(sections: Mapping[str, Any], ratings: Mapping[str, str] | None = None):
    from services import hypothesis_tier
    brief = _map(sections.get("editorial_briefing"))
    lab = _map(sections.get("strategic_hypotheses"))
    for item in _items(sections.get("hidden_insights")):
        yield "hidden_insight", _clean(item.get("text")), item
    for item in _items(brief.get("implications")):
        yield "implication", _clean(item.get("text")), item
    # A rewritten (narrowed) hypothesis often only restates its facts: originals first.
    verified = sorted(_items(lab.get("verified")), key=lambda h: bool(h.get("critic_inherited_from")))
    for item in verified:
        yield "verified_hypothesis", _clean(item.get("thesis")), item
    for item in _items(lab.get("promising_unverified")):
        if item.get("status") == "analysis":
            yield "analysis", _clean(item.get("thesis")), item
    for item in hypothesis_tier.select(sections, ratings):
        if item.get("status") != "analysis":
            yield "hypothesis", _clean(item.get("thesis")), item
    for item in _items(brief.get("analogies")) or _items(sections.get("historical_analogies")):
        yield "analogy", _clean(item.get("text") or _map(item.get("case")).get("text")), item
    for item in _items(brief.get("expected_implications")):
        yield "expected_implication", _clean(item.get("text")), item
    for actor in _items(_map(sections.get("actor_moves")).get("actors")):
        for move in _items(actor.get("moves")):
            text = _clean(move.get("move"))
            if text:
                yield "move", f"{_clean(actor.get('actor'))}: {text}", move


def lead(sections: Mapping[str, Any], ratings: Mapping[str, str] | None = None) -> dict[str, Any] | None:
    """The strongest inference to show first, or None when the report has none."""
    for kind, text, item in _candidates(sections, ratings):
        if text:
            return {"kind": kind, "label": LABELS[kind], "text": text, "item": dict(item)}
    return None


def inventory(sections: Mapping[str, Any]) -> dict[str, Any]:
    counts = {kind: 0 for kind in LABELS}
    for kind, text, _ in _candidates(sections):
        if text:
            counts[kind] += 1
    non_obvious = sum(v for k, v in counts.items() if k not in {"expected_implication", "move"})
    return {"version": VERSION, "counts": counts, "inference_count": sum(counts.values()),
            "non_obvious_count": non_obvious, "lead_kind": (lead(sections) or {}).get("kind", "none")}
