"""Quote-first premises (14 Nov 2026).

Why: on the 12 Nov run three of six candidates stated a fact its cited record did not back.
ALCE (Gao et al., EMNLP 2023) measured that writing a statement from background knowledge and
attaching a citation afterwards is the worst pattern for citation quality (citation recall
26.7 on ASQA, against 73.6 when the statement is written from the numbered passages).
Anthropic's guidance for long documents is the same remedy: "extract word-for-word quotes
first", then work only from the quotes.

What this module does, with no model call:
- the model is asked to copy a short span from the cited record into each premise ("quote");
- the quote is looked up as an exact substring of the evidence records (case, spacing and
  curly/straight quote marks ignored);
- found in a cited record: nothing changes;
- found in exactly one other record: that record is ADDED to the premise's citations (the model
  cited the wrong ID), unless the premise already has three (ALCE: "at most 3");
- found in several other records: too common a span to attribute - marked, nothing added
  (adding a record on a weak match would make a single-source fact look corroborated);
- found nowhere: the premise is marked, nothing is blocked - the normal claim check decides.

It can add one correct citation; it never removes a citation, a premise or a candidate.
Switch off with EVENT_INTELLIGENCE_QUOTE_FIRST=0.
"""
from __future__ import annotations

import os
import re
from typing import Any, Mapping, Sequence

VERSION = "quote-first-v1"
MAX_CITATIONS = 3          # ALCE: each statement cites at most 3 passages
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"', "”": '"', "–": "-", "—": "-",
                         " ": " "})

PROMPT_RULE = (
    "QUOTE FIRST - HOW TO WRITE EVERY PREMISE\n"
    "For each premise, work in this order: (1) pick the evidence record; (2) copy one short span from that record, "
    "word for word, into \"quote\"; (3) write \"text\" so that it says no more than the quoted span and the rest of "
    "that record say; (4) put that record's ID in evidence_ids (at most three IDs per premise). Do not write a premise "
    "from memory and look for a record afterwards. If no admitted record contains a span that states the fact, it is "
    "not a premise: leave it out, or put it in background_knowledge_component as an assumption.\n\n"
)
SCHEMA_OLD = '"premises":[{"premise_id":"P1","text":"","evidence_ids":[]},{"premise_id":"P2","text":"","evidence_ids":[]}]'
SCHEMA_NEW = ('"premises":[{"premise_id":"P1","quote":"span copied word for word from the cited record","text":"",'
              '"evidence_ids":[]},{"premise_id":"P2","quote":"","text":"","evidence_ids":[]}]')


def enabled() -> bool:
    return os.getenv("EVENT_INTELLIGENCE_QUOTE_FIRST", "1").strip().casefold() not in {"0", "false", "off", "no"}


def apply_to_prompt(prompt: str) -> str:
    """Add the rule and the quote field to the synthesis prompt. Unchanged when switched off or
    when the schema line is not present (so a prompt this was not written for is left alone)."""
    if not enabled() or SCHEMA_OLD not in prompt or PROMPT_RULE in prompt:
        return prompt
    marker = "RELEASE CONTRACT - "
    prompt = prompt.replace(SCHEMA_OLD, SCHEMA_NEW)
    return prompt.replace(marker, PROMPT_RULE + marker, 1) if marker in prompt else prompt


def _norm(value: Any) -> str:
    text = str(value or "").translate(_QUOTES).casefold()
    text = re.sub(r"\s+", " ", text).strip()
    return text.strip(" \"'.…")


def locate(quote: Any, evidence_text_by_id: Mapping[str, str], cited_ids: Sequence[str]) -> tuple[str, list[str]]:
    """("", []) no quote given; ("cited", ids) found in a cited record; ("elsewhere", [id]) found in
    exactly one other record; ("ambiguous", ids) found in several other records - too common a
    span to say which record it came from; ("not_found", []) found in none."""
    needle = _norm(quote)
    if not needle:
        return "", []
    cited = [str(i).upper() for i in cited_ids or []]
    texts = {str(k).upper(): _norm(v) for k, v in (evidence_text_by_id or {}).items()}
    in_cited = [i for i in cited if needle in texts.get(i, "")]
    if in_cited:
        return "cited", in_cited
    elsewhere = sorted(i for i, text in texts.items() if i not in cited and needle in text)
    if len(elsewhere) == 1:
        return "elsewhere", elsewhere
    return ("ambiguous", elsewhere) if elsewhere else ("not_found", [])


def check_premise(premise: dict[str, Any], quote: Any, evidence_text_by_id: Mapping[str, str]) -> str:
    """Record the quote check on a normalised premise (its "notes"), and add the one record that
    holds the quote when the model cited a different one. Never removes a citation the model
    gave, and adds nothing when the premise already has MAX_CITATIONS. Returns the status."""
    current = [str(i).upper() for i in premise.get("evidence_ids") or []]
    status, found = locate(quote, evidence_text_by_id, current)
    if not status:
        return ""
    note = f"quote_check={status}"
    if status == "elsewhere" and len(current) < MAX_CITATIONS:
        premise["evidence_ids"] = current + found
        note += "; citation_added_from_quote=" + found[0]
    existing = str(premise.get("notes") or "").strip()
    premise["notes"] = (existing + "; " if existing else "") + note
    return status
