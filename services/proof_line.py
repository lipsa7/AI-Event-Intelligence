"""One line of evidence under each insight, instead of a claim of quality (v13.20, 22 Nov 2026).

Why
---
Readers trust news at 37% and AI chatbot answers at about 20% (Reuters Institute Digital News Report
2026). An insight app is worth reading over the news only if a reader can SEE why a finding is new and
backed: what it was checked against, how many independent stories and publishers it combines, how
many of its claims the sources support, whether the search for contrary evidence finished, and what
dated, checkable forecast it makes. The research report of 21 Nov (section F, item 12) proposes this
line: "Not found in N prior articles checked · K sources, P publishers · Claim support a/b ·
Forecast: ... (open) · Consensus checked: ...".

Everything here is read from the saved insight; nothing is called or recomputed.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

VERSION = "proof-line-v1"


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def is_original(item: Mapping[str, Any]) -> bool:
    """An insight the gates accepted as original. A 'source-backed' one restates its sources or
    published experts and is shown only as background (claim_support_gate v6.30.2)."""
    return not _mapping(item.get("source_backed")) and not _mapping(
        _mapping(item.get("metadata")).get("source_backed"))


def claim_support(item: Mapping[str, Any], grounding_by_insight: Mapping[str, Any] | None = None) -> tuple[int, int]:
    """(claims the sources support, claims checked) for one insight."""
    grounding = _mapping(_mapping(grounding_by_insight).get(_clean(item.get("insight_revision_id"))))
    claims = [c for c in grounding.get("claims") or [] if isinstance(c, Mapping)]
    if claims:
        good = {"supported", "inference_supported"}
        return sum(1 for c in claims if _clean(c.get("grounding_status")) in good), len(claims)
    summary = _mapping(_mapping(item.get("evidence_table")).get("summary"))
    stated, inferred = int(summary.get("stated") or 0), int(summary.get("inferred") or 0)
    total = stated + inferred + int(summary.get("assumptions") or 0)
    return stated + inferred, total


def parts(item: Mapping[str, Any], *, sources_read: int = 0,
          grounding_by_insight: Mapping[str, Any] | None = None) -> list[str]:
    out: list[str] = []
    if is_original(item):
        out.append(f"Not stated by any of the {sources_read} sources read" if sources_read
                   else "Not stated by its sources")
    else:
        reasons = " ".join(_mapping(item.get("source_backed")).get("reasons") or [])
        out.append("Already said by published analysis" if "expert" in reasons
                   else "Already said by its own sources (background, not an original insight)")
    confidence = _mapping(item.get("analytic_confidence"))
    events, publishers = int(confidence.get("independent_events") or 0), int(confidence.get("independent_publishers") or 0)
    if events or publishers:
        out.append(f"combines {events} {'story' if events == 1 else 'stories'} from "
                   f"{publishers} {'publisher' if publishers == 1 else 'publishers'}")
    good, total = claim_support(item, grounding_by_insight)
    if total:
        out.append(f"claim support {good}/{total}")
    assumptions = int(confidence.get("key_assumptions") or 0)
    if assumptions:
        out.append(f"{assumptions} reasoning {'step is an' if assumptions == 1 else 'steps are'} assumption"
                   + ("" if assumptions == 1 else "s"))
    check = _mapping(item.get("counterevidence_check"))
    status = _clean(check.get("status"))
    if status:
        out.append("contrary-evidence search " + ("incomplete" if status == "incomplete" else "done"))
    experts = item.get("expert_commentary_checked")
    if isinstance(experts, int) and experts > 0:
        out.append(f"checked against {experts} passages of published analysis")
    # v13.28: the editor's bar - trusted (calibrated on the user's ratings) or advisory (not yet trusted).
    par = _mapping(item.get("par_check")) or _mapping(_mapping(item.get("metadata")).get("par_check"))
    if par.get("available"):
        if par.get("trusted"):
            probability = par.get("calibrated_probability")
            out.append("passed the editor's bar" + (f" (chance you'd rate it good {probability:.0%})"
                                                     if isinstance(probability, (int, float)) else ""))
        elif par.get("advisory_verdict"):
            out.append(f"editor's bar (judge not yet checked against your ratings): {par['advisory_verdict']}"
                       + (f" - missing {_clean(par.get('missing')).replace('_', ' ')}"
                          if par["advisory_verdict"] == "fail" and _clean(par.get("missing")) not in {"", "none"} else ""))
    contract = _mapping(item.get("forecast_contract"))
    deadline = _clean(contract.get("deadline"))[:10]
    if _clean(contract.get("question")) and deadline:
        out.append(f"forecast open until {deadline}")
    return out


def line(item: Mapping[str, Any], *, sources_read: int = 0,
         grounding_by_insight: Mapping[str, Any] | None = None) -> str:
    return " · ".join(parts(item, sources_read=sources_read, grounding_by_insight=grounding_by_insight))


def split(items: Sequence[Any]) -> tuple[list[Mapping[str, Any]], list[Mapping[str, Any]]]:
    """(original insights, source-backed background findings)."""
    rows = [i for i in items or [] if isinstance(i, Mapping)]
    return [i for i in rows if is_original(i)], [i for i in rows if not is_original(i)]
