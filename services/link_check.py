"""Checks that a mechanism chain holds together, by code (v13.31).

Both research reports of 22 Nov:
* "Fix grounding at the source": the generator quotes the words each premise rests on, and code checks the
  quote is really in the cited record (14 of 22 candidates that reached the claim check failed on a premise
  not supported by its cited evidence). MiniCheck (Tang, Laban & Durrett) would add an entailment check but
  needs memory the owner's laptop does not have free; the verbatim quote is the part code can enforce.
* Anti-stitching: "require every link A -> B to cite one evidence record that mentions both A and B";
  "reject the chain if consecutive links share no entity, instrument or citation". On 22 Nov a winner joined
  EU AI regulatory sandboxes to a Texas lawsuit; the sandbox record mentions neither Texas nor OpenAI.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Sequence

VERSION = "link-check-v1"
MIN_QUOTE_WORDS = 5
_STOP = {"The", "This", "That", "These", "Those", "It", "Its", "A", "An", "And", "But", "If", "In", "On", "At",
         "By", "For", "From", "To", "Of", "With", "As", "When", "While", "Because", "So", "Watch", "Forecast",
         "January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
         "November", "December", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _norm(text: Any) -> str:
    return " ".join("".join(c.casefold() if c.isalnum() else " " for c in _clean(text)).split())


def entities(text: Any) -> set[str]:
    """Capitalised names (OpenAI, Texas, Federal Trade Commission -> federal trade commission) and acronyms."""
    found = set()
    for match in re.finditer(r"\b([A-Z][A-Za-z0-9&.\-]+(?:\s+[A-Z][A-Za-z0-9&.\-]+)*)\b", _clean(text)):
        words = [w for w in match.group(1).split() if w not in _STOP]
        if words:
            found.add(" ".join(words).casefold())
            found.update(w.casefold() for w in words if len(w) > 2)
    return found


def problems(chain: Sequence[Mapping[str, Any]], records: Sequence[Mapping[str, Any]]) -> list[str]:
    rows = {_clean(r.get("evidence_id")): r for r in records or [] if isinstance(r, Mapping)}
    steps = [s for s in chain or [] if isinstance(s, Mapping) and _clean(s.get("step"))]
    out: list[str] = []
    for n, step in enumerate(steps, start=1):
        quote = _norm(step.get("quote"))
        cited = [rows[_clean(e)] for e in step.get("evidence_ids") or [] if _clean(e) in rows]
        if len(quote.split()) < MIN_QUOTE_WORDS:
            out.append(f"link_unquoted:{n}")
        elif not any(quote in _norm(r.get("text") or r.get("source_excerpt") or r.get("claim")) for r in cited):
            out.append(f"link_quote_not_in_source:{n}")
    for n in range(1, len(steps)):
        a, b = steps[n - 1], steps[n]
        ids_a = {_clean(e) for e in a.get("evidence_ids") or []}
        ids_b = {_clean(e) for e in b.get("evidence_ids") or []}
        if ids_a & ids_b:
            continue
        names_a = entities(a.get("step")) | {x for e in ids_a if e in rows for x in entities(rows[e].get("text"))}
        names_b = entities(b.get("step"))
        text_b = " ".join(_clean(rows[e].get("text")).casefold() for e in ids_b if e in rows)
        text_a = " ".join(_clean(rows[e].get("text")).casefold() for e in ids_a if e in rows)
        # the record(s) behind link B must mention something from link A, or A's records something of B
        if not (any(x in text_b for x in names_a if len(x) > 2) or any(x in text_a for x in names_b if len(x) > 2)):
            out.append(f"link_not_connected:{n}->{n + 1}")
    return out
