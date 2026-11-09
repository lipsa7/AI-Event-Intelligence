"""Hypotheses to investigate (plan fix E, 23 Oct 2026).

When nothing passes every check, the lab's near misses were hidden in a collapsed list while
the report said "None this run". This tier shows up to MAX_SHOWN of them, expanded, but never
as insights: each separates what the sources establish from what is unresolved, states the
reasoning step and what would disprove it.

An item qualifies when:
- its thesis is stated by, follows from, or is partly backed by the sources (supported,
  inference_supported or partially_supported);
- nothing in it is contradicted by a source, so a contradiction is never shown as a premise;
- at least one source quote supports part of it.

Order: your own rating of the same saved item first (useful, then unrated, then meh/poor),
then how many distinct source quotes support it. Critic scores are not used: they saturate
(most items score 10), and a rewritten item is unscored.
"""

from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

VERSION = "hypothesis-tier-v1.1-supported-thesis"
MAX_SHOWN = 3
# A thesis the sources fully state also qualifies when its item failed verification elsewhere
# (RUN-3E2FE: EVOLVED_H1, thesis "supported", mechanism partly backed, was hidden).
QUALIFYING_THESIS = frozenset({"supported", "inference_supported", "partially_supported"})
PASSED = frozenset({"supported", "inference_supported"})
RATING_ORDER = {"useful": 0, "": 1, "meh": 2, "poor": 3}
FIELD_LABELS = {"LEVERAGE": "Leverage", "CONTROL": "Control point", "DEPENDENCY": "Dependency",
                "ALTERNATIVES": "Alternatives", "ASYMMETRY": "Who needs whom more",
                "MOVEBASIS": "Past behaviour behind the move", "COUNTERBASIS": "Past behaviour behind the countermove",
                "MOVE": "Likely move", "COUNTER": "Countermove"}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _claims(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    return [c for c in _list(item.get("grounded_claims")) if isinstance(c, Mapping)]


def contradicted(item: Mapping[str, Any]) -> bool:
    grounding = _map(item.get("grounding"))
    return ("contradicted" in {_clean(grounding.get("thesis")), _clean(grounding.get("mechanism"))}
            or any(_clean(c.get("grounding_status")) == "contradicted" or _list(c.get("contradicted_evidence_ids"))
                   for c in _claims(item)))


def premises(item: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    """Source quotes that support part of the item: one per evidence record, and the same
    passage stored under two evidence ids is counted once."""
    seen, out = set(), []
    for quote in _list(item.get("supporting_evidence")):
        if isinstance(quote, Mapping) and _clean(quote.get("text")):
            keys = {_clean(quote.get("text")).casefold()} | ({_clean(quote.get("evidence_id"))}
                                                            if _clean(quote.get("evidence_id")) else set())
            if not keys & seen:
                seen |= keys
                out.append(quote)
    return out


def qualifies(item: Mapping[str, Any]) -> bool:
    return (_clean(_map(item.get("grounding")).get("thesis")) in QUALIFYING_THESIS
            and not contradicted(item) and bool(premises(item)))


def _is_link(claim: Mapping[str, Any]) -> bool:
    """A reasoning link of a typed mechanism (…-MECH-L2): an assumption when unbacked, not a fact."""
    return bool(re.search(r"-MECH-L\d+$", _clean(claim.get("claim_id"))))


def open_points(item: Mapping[str, Any]) -> list[str]:
    """Checked facts and fields the sources do not establish, in plain words."""
    out = []
    for claim in _claims(item):
        claim_id = _clean(claim.get("claim_id"))
        suffix = claim_id.rsplit("-", 1)[-1]
        if suffix in {"THESIS", "MECH"} or _is_link(claim) or _clean(claim.get("grounding_status")) in PASSED:
            continue
        label = "Starting fact of the reasoning" if claim_id.endswith("-MECH-S1") else FIELD_LABELS.get(suffix, suffix.title())
        out.append(f"{label}: {_clean(claim.get('claim_text'))}")
    for field, ids in _map(item.get("unresolved_fact_ids")).items():
        out.append(f"{FIELD_LABELS.get('MOVEBASIS' if field == 'actor_move_basis' else 'COUNTERBASIS')}: "
                   f"cites unknown fact {', '.join(ids)} (not checked)")
    return out


def assumptions(item: Mapping[str, Any]) -> list[str]:
    """What the reasoning adds that no source states (the thesis and mechanism checks)."""
    out = [_clean(x) for x in _list(item.get("key_assumptions")) if _clean(x)]   # analysis: the one stated link
    for claim in _claims(item):
        if _is_link(claim) and _clean(claim.get("grounding_status")) not in PASSED:
            out.append(_clean(claim.get("claim_text")))
        elif _clean(claim.get("claim_id")).rsplit("-", 1)[-1] in {"THESIS", "MECH"}:
            out += [_clean(x) for x in _list(claim.get("unsupported_parts")) if _clean(x)
                    and not _clean(x).startswith("The claim requires multiple supporting evidence records")]
    return list(dict.fromkeys(out))


def select(sections: Mapping[str, Any], ratings: Mapping[str, str] | None = None) -> list[dict[str, Any]]:
    """The hypotheses to show, best first. ratings: {item_key: useful|meh|poor} for this report
    (item_key "lab:<id>", as services/insight_ratings.py stores lab items)."""
    ratings = ratings or {}
    lab = _map(sections.get("strategic_hypotheses"))
    pool = [h for h in _list(lab.get("promising_unverified")) if isinstance(h, Mapping) and qualifies(h)]
    # Your rating first; then analysis (sourced facts, one stated assumption) before other near misses.
    ranked = sorted(enumerate(pool), key=lambda pair: (
        RATING_ORDER.get(_clean(ratings.get(f"lab:{_clean(pair[1].get('id'))}")), 1),
        pair[1].get("status") != "analysis", -len(premises(pair[1])), pair[0]))
    return [dict(item, _rating=_clean(ratings.get(f"lab:{_clean(item.get('id'))}"))) for _, item in ranked[:MAX_SHOWN]]


def coverage(item: Mapping[str, Any]) -> str:
    quotes = premises(item)
    publishers = {_clean(q.get("publisher")).casefold() for q in quotes if _clean(q.get("publisher"))}
    return (f"{len(quotes)} source quote{'s' if len(quotes) != 1 else ''} from "
            f"{len(publishers) or 'unknown'} publisher{'s' if len(publishers) != 1 else ''}")
