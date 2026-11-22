"""Per-gate funnel: how many candidates entered and left each gate, and why (v13.28, 22 Nov 2026).

Research report of 22 Nov ("Getting AI Event Intelligence to produce original insights"), Method 0:
before loosening or adding anything, log per gate how many candidates went in and came out and the
reason for each rejection, then hand-label rejects as rightly or wrongly rejected - otherwise zero
releases cannot be told apart as a generation problem or a gate problem. Stacked gates multiply
(four gates at 60% each pass about 13%).

Built only from what a run already records (the claim-support gate log and the precedent research
receipts), so it works on saved reports and needs no model call.

    python tools/gate_funnel.py                 # the last 4 saved reports, plus totals
    python tools/gate_funnel.py --sheet         # also writes data/labels/rejects_<date>.csv to label
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Mapping, Sequence

VERSION = "gate-funnel-v1"


def _m(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _l(value: Any) -> list[Any]:
    return list(value) if isinstance(value, (list, tuple)) else []


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _reason(reason: Any) -> str:
    text = _clean(reason)
    return text.split(":", 1)[0] or "unknown"


def compute(gate_log: Mapping[str, Any], precedent_research: Sequence[Any] = ()) -> dict[str, Any]:
    """{stage: {"in": n, "out": n, "rejected": {reason: n}}} in pipeline order."""
    combos = [_m(c) for c in _l(_m(_m(gate_log).get("exploration")).get("combinations"))]
    lens_calls = [_m(c) for combo in combos for c in _l(combo.get("lens_calls"))]
    stages: dict[str, dict[str, Any]] = {}
    stages["stories_explored"] = {"in": len(combos),
                                  "out": sum(1 for c in combos if c.get("result") == "candidate"),
                                  "rejected": dict(Counter(_reason(c.get("result")) for c in combos
                                                           if c.get("result") != "candidate"))}
    if lens_calls:
        stages["lens_generation"] = {"in": len(lens_calls),
                                     "out": sum(1 for c in lens_calls if _clean(c.get("result")).startswith("candidate")),
                                     "rejected": dict(Counter(_reason(c.get("result")) for c in lens_calls
                                                              if not _clean(c.get("result")).startswith("candidate")))}
    variant_in = sum(int(c.get("variant_kept") or 0) + len(_l(c.get("variant_dropped"))) for c in combos)
    if variant_in:
        problems = Counter(_clean(p) for c in combos for d in _l(c.get("variant_dropped"))
                           for p in (_l(_m(d).get("problems")) or ["unknown"]))
        stages["variant_checks"] = {"in": variant_in, "out": sum(int(c.get("variant_kept") or 0) for c in combos),
                                    "rejected": dict(problems.most_common())}
    attempts = [_m(a) for a in _l(_m(gate_log).get("attempts"))]
    stages["claim_and_editor_checks"] = {
        "in": len(attempts), "out": sum(1 for a in attempts if a.get("passed")),
        "rejected": dict(Counter(_reason(r) for a in attempts if not a.get("passed")
                                 for r in (_l(a.get("reasons")) or ["unknown"])).most_common())}
    receipts = [_m(r) for r in precedent_research or []]
    if receipts:
        limits = Counter()
        for receipt in receipts:
            for item in _l(receipt.get("limitations")):
                text = _clean(item)
                if text.startswith("precedent"):
                    # "precedent: direct_precedent: gdelt: RATE_LIMITED" -> "gdelt: RATE_LIMITED"
                    limits[": ".join(text.split(": ")[2:]) or text] += 1
        stages["precedent_search"] = {"in": len(receipts),
                                      "out": sum(1 for r in receipts if int(r.get("case_count") or 0) > 0),
                                      "rejected": dict(limits.most_common())}
    for stage in stages.values():
        stage["pass_rate"] = round(stage["out"] / stage["in"], 2) if stage["in"] else None
    return stages


def reject_rows(gate_log: Mapping[str, Any], precedent_research: Sequence[Any] = (), report_id: str = "") -> list[dict[str, str]]:
    """Rows to label by hand: verdict = rightly | wrongly (and a note)."""
    rows: list[dict[str, str]] = []
    for combo in _l(_m(_m(gate_log).get("exploration")).get("combinations")):
        for dropped in _l(_m(combo).get("variant_dropped")):
            rows.append({"report_id": report_id, "stage": "variant_checks",
                         "reason": "; ".join(_clean(p) for p in _l(_m(dropped).get("problems"))),
                         "text": _clean(_m(dropped).get("insight")), "verdict": "", "note": ""})
    for attempt in _l(_m(gate_log).get("attempts")):
        attempt = _m(attempt)
        if not attempt.get("passed"):
            rows.append({"report_id": report_id, "stage": "claim_and_editor_checks",
                         "reason": "; ".join(_clean(r) for r in _l(attempt.get("reasons"))),
                         "text": _clean(attempt.get("insight")), "verdict": "", "note": ""})
    for receipt in precedent_research or []:
        receipt = _m(receipt)
        if int(receipt.get("case_count") or 0) == 0:
            queries = [_clean(_m(q).get("query")) for q in _l(receipt.get("queries")) if _m(q).get("gap") == "precedent"]
            followup = _m(receipt.get("precedent_followup"))
            rows.append({"report_id": report_id, "stage": "precedent_search",
                         "reason": "; ".join(_clean(x) for x in _l(receipt.get("limitations")) if _clean(x).startswith("precedent")),
                         "text": _clean(receipt.get("development"))[:300] + " | queries: " + " / ".join(
                             queries + ([_clean(followup.get("query"))] if followup.get("query") else [])),
                         "verdict": "", "note": ""})
    return rows


def total(funnels: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    out: dict[str, dict[str, Any]] = {}
    for funnel in funnels:
        for name, stage in funnel.items():
            row = out.setdefault(name, {"in": 0, "out": 0, "rejected": Counter()})
            row["in"] += int(stage.get("in") or 0)
            row["out"] += int(stage.get("out") or 0)
            row["rejected"].update(_m(stage.get("rejected")))
    for row in out.values():
        row["rejected"] = dict(row["rejected"].most_common())
        row["pass_rate"] = round(row["out"] / row["in"], 2) if row["in"] else None
    return out


def lines(funnel: Mapping[str, Any]) -> list[str]:
    out = []
    for name, stage in funnel.items():
        rejected = ", ".join(f"{k} {v}" for k, v in list(_m(stage.get("rejected")).items())[:6]) or "none"
        rate = stage.get("pass_rate")
        out.append(f"  {name}: {stage.get('in')} in -> {stage.get('out')} out"
                   f" ({'n/a' if rate is None else f'{rate:.0%}'}); rejected: {rejected}")
    return out
