"""Meaning-based novelty: how close is a candidate to what is already said? (v13.30, 22 Nov 2026)

Research report of 22 Nov ("Getting AI Event Intelligence to produce original insights"), Methods 5 and 6:
embed each candidate and compute its maximum cosine similarity to (a) the consensus lines and published
analysis found for the story and (b) the app's earlier insights; a candidate that is too close is sent back
with its three nearest neighbours to revise (SciMON, ACL 2024: novelty rose for 57.8% of ideas that went
through further iterations, and no iteration reduced it), at most twice. Si, Yang & Hashimoto (ICLR 2025)
used a 0.8 similarity threshold for duplicate ideas.

Why it matters here: on 22 Nov the judge called "OpenAI faces overlapping pre-release oversight from Texas
and the EU" new because the story's consensus map had 4 claims and 1 piece of analysis, none about the EU -
while "AI firms face a patchwork of state and international rules" is among the commonest points in AI
policy coverage. Comparing by meaning against a wider set (the wider consensus search, v13.30, and every
earlier insight) catches that kind of restatement without a model call.

Embeddings: the local nomic-embed-text model through services/vector_service.py (274 MB; small enough for
the owner's laptop). If it cannot be reached the check is skipped and logged, never faked.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Sequence

VERSION = "semantic-novelty-v1"
logger = logging.getLogger("event_intelligence.services.semantic_novelty")
# Si et al. (ICLR 2025) treat ideas at 0.8 cosine or above as duplicates. A candidate this close is revised
# against its nearest neighbours; it is held back only if it is still a near-copy (BLOCK) after the revisions.
REVISE_AT = 0.80
BLOCK_AT = 0.92
MAX_REVISIONS = 2
_SERVICE: dict[str, Any] = {}


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split())


def _service() -> Any:
    if "service" not in _SERVICE:
        service = None
        if os.getenv("EVENT_INTELLIGENCE_SEMANTIC_NOVELTY", "1").strip() not in {"0", "false", "no"}:
            try:
                from services.vector_service import VectorService
                candidate = VectorService()
                service = candidate if candidate.available() else None
            except Exception as error:
                logger.info("semantic novelty unavailable: %s", type(error).__name__)
        _SERVICE["service"] = service
    return _SERVICE["service"]


def reset() -> None:
    _SERVICE.clear()


def thresholds() -> tuple[float, float]:
    def number(name: str, default: float) -> float:
        try:
            return float(os.getenv(name, "") or default)
        except ValueError:
            return default
    return number("EVENT_INTELLIGENCE_NOVELTY_REVISE_AT", REVISE_AT), number("EVENT_INTELLIGENCE_NOVELTY_BLOCK_AT", BLOCK_AT)


def reference_texts(view: Mapping[str, Any], earlier: Sequence[str] = ()) -> list[dict[str, str]]:
    rows = []
    for claim in view.get("claims") or []:
        if isinstance(claim, Mapping) and _clean(claim.get("claim")):
            rows.append({"kind": "consensus", "text": _clean(claim.get("claim"))[:600]})
    for item in view.get("analysis") or []:
        if isinstance(item, Mapping) and _clean(item.get("snippet") or item.get("title")):
            rows.append({"kind": "published:" + _clean(item.get("angle") or "analysis"),
                         "text": _clean(item.get("snippet") or item.get("title"))[:600]})
    for text in earlier or []:
        if _clean(text):
            rows.append({"kind": "earlier insight", "text": _clean(text)[:600]})
    return rows


def check(candidate_text: str, references: Sequence[Mapping[str, str]], service: Any = None) -> dict[str, Any]:
    """{"checked", "max", "nearest": [{kind, text, similarity}] (top 3), "too_close", "near_copy"}."""
    service = service if service is not None else _service()
    text = _clean(candidate_text)
    if service is None or not text or not references:
        return {"checked": False, "reason": "no_embeddings" if service is None else "nothing_to_compare"}
    try:
        scores = service.similarities(text, [r["text"] for r in references])
    except Exception as error:
        return {"checked": False, "reason": "embedding_failed:" + type(error).__name__}
    ranked = sorted(zip(scores, references), key=lambda pair: -float(pair[0]))
    revise_at, block_at = thresholds()
    top = float(ranked[0][0]) if ranked else 0.0
    return {"checked": True, "max": round(top, 3),
            "nearest": [{"kind": r["kind"], "text": r["text"][:300], "similarity": round(float(s), 3)}
                        for s, r in ranked[:3]],
            "too_close": top >= revise_at, "near_copy": top >= block_at}


def revision_brief(result: Mapping[str, Any]) -> str:
    lines = "; ".join(f"[{n['kind']}, {n['similarity']}] {n['text'][:200]}" for n in result.get("nearest") or [])
    return ("it says nearly what these already say (closest first): " + lines
            + ". Keep its facts, and add the step none of them states: a cited rule, deadline, number, past case "
              "or second story, and what it forces for a named actor")
