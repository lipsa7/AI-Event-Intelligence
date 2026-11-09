"""Strategic Hypothesis Lab v1.1.

Purpose
-------
Raise the quality ceiling before the normal InsightEvaluator sees candidates.
The lab does not replace grounding or release gates. It creates a broad set of
structurally different hypotheses, forces explicit mechanisms/alternatives,
evolves the strongest candidates, and returns only structured material that can
be grounded by the orchestrator.

Design principles
-----------------
1. Start from anomalies, dependencies and constraints, not recommendations.
2. Generate across different lenses so 20 candidates are not paraphrases.
3. Make every thesis state why it is non-obvious, an alternative explanation,
   a falsifier and an observable watch signal.
4. Prefer a change in the reader's model of the system over generic advice.
5. Keep unsupported but interesting theses visible as hypotheses; grounding
   decides whether a thesis may enter the verified evaluator pool.
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

VERSION = "strategic-hypothesis-lab-v1.6-blind-user-hypotheses"
GENERATION_TARGET_PER_BATCH = 10
MAX_RAW = 24
MAX_EVOLVED = 8
MAX_VERIFIED = 6
MAX_PROMISING = 6
NARROW_MAX = 3  # v1.2: blocked/unsupported candidates restated narrowly and re-checked

LENS_GROUPS = (
    (
        "contradiction/non-reconciliation",
        "hidden constraint or bottleneck migration",
        "dependency and control-point shift",
        "incentive/risk asymmetry",
        "unit-economics or cost-curve change",
        "timing mismatch / option value",
    ),
    (
        "regulatory arbitrage or policy feedback",
        "coordination failure / collective-action problem",
        "substitution and switching-cost shift",
        "second-order consequence / feedback loop",
        "counter-positioning / strategic response",
        "mechanism-based historical analogy",
    ),
)

_GENERIC_PATTERNS = (
    r"\bshould (?:focus|invest|monitor|collaborate|cooperate|improve|ensure)\b",
    r"\bcompetition (?:is|will be) increasing\b",
    r"\bregulation (?:will|could|may) (?:increase|create) (?:cost|costs|risk|risks)\b",
    r"\bai (?:is|will be) transformative\b",
    r"\bcompanies? need(?:s)? to adapt\b",
)


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _json(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    text = str(value or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.I | re.S).strip()
    try:
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
            return parsed if isinstance(parsed, dict) else {}
        except Exception:
            return {}


def _unique(values: Sequence[Any]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        item = _clean(value)
        key = item.casefold()
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def _listish(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return list(value)
    return [value]


def _walk_values(node: Any, keys: set[str]) -> list[str]:
    """Collect identifier-like values from nested fact metadata without inventing links."""
    out: list[str] = []
    if isinstance(node, Mapping):
        for key, value in node.items():
            norm = str(key).casefold().replace('-', '_')
            if norm in keys:
                for item in _listish(value):
                    if isinstance(item, (str, int)):
                        text = _clean(item)
                        if text and text not in out:
                            out.append(text)
            if isinstance(value, (Mapping, list, tuple)):
                for item in _walk_values(value, keys):
                    if item not in out:
                        out.append(item)
    elif isinstance(node, (list, tuple)):
        for value in node:
            for item in _walk_values(value, keys):
                if item not in out:
                    out.append(item)
    return out


def fact_rows(facts: Sequence[Mapping[str, Any]], limit: int = 40) -> list[dict[str, Any]]:
    """Normalize facts while retaining every explicit evidence/source identifier shape."""
    out: list[dict[str, Any]] = []
    for n, fact in enumerate(facts or []):
        if not isinstance(fact, Mapping):
            continue
        fid = _clean(fact.get("fact_id") or fact.get("id") or f"F{n + 1}")
        text = _clean(fact.get("text") or fact.get("statement") or fact.get("claim"))
        if not text:
            continue
        citations: list[dict[str, str]] = []
        for cite in _listish(fact.get("citations")) + _listish(fact.get("sources")) + _listish(fact.get("evidence")):
            if isinstance(cite, Mapping):
                citations.append({
                    "evidence_id": _clean(cite.get("evidence_id") or cite.get("id")),
                    "source_id": _clean(cite.get("source_id")),
                    "publisher": _clean(cite.get("publisher")),
                    "date": _clean(cite.get("date") or cite.get("published_at"))[:10],
                })
        explicit_evidence_ids = _walk_values(
            fact,
            {"evidence_id", "evidence_ids", "supporting_evidence_id", "supporting_evidence_ids"},
        )
        explicit_source_ids = _walk_values(fact, {"source_id", "source_ids"})
        for eid in explicit_evidence_ids:
            if not any(_clean(c.get("evidence_id")) == eid for c in citations):
                citations.append({"evidence_id": eid, "source_id": "", "publisher": "", "date": ""})
        out.append({
            "fact_id": fid,
            "text": text[:900],
            "citations": citations,
            "source_ids": _unique(explicit_source_ids + [c.get("source_id") for c in citations]),
            # Memory pass: coverage from earlier runs keeps its dates into the prompt.
            "earlier": _clean(fact.get("gap_type")) == "earlier",
            "date": _clean(fact.get("date") or fact.get("published_at"))[:10],
            "occurred_on": _clean(fact.get("occurred_on"))[:10],
        })
        if len(out) >= limit:
            break
    return out


def _evidence_text(record: Mapping[str, Any]) -> str:
    return _clean(
        record.get("text") or record.get("statement") or record.get("claim")
        or record.get("summary") or record.get("excerpt") or record.get("exact_excerpt")
    )


def _token_overlap(a: str, b: str) -> float:
    ta = {x for x in re.findall(r"[a-z0-9]{3,}", _clean(a).casefold())}
    tb = {x for x in re.findall(r"[a-z0-9]{3,}", _clean(b).casefold())}
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / max(1, min(len(ta), len(tb)))


def evidence_ids_for_fact_ids(
    facts: Sequence[Mapping[str, Any]],
    fact_ids: Sequence[str],
    evidence_records: Sequence[Mapping[str, Any]] = (),
) -> list[str]:
    """Resolve local briefing fact IDs back to durable EVID-* records.

    v1.0 only understood fact['citations'/'sources'][].evidence_id. The real
    pipeline also emits evidence_ids/supporting_evidence_ids directly and, for
    some context facts, only source IDs. This resolver keeps exact IDs first and
    uses source+text matching only as a conservative fallback.
    """
    wanted = {x.casefold() for x in _unique(fact_ids)}
    rows = [r for r in fact_rows(facts, limit=250) if r["fact_id"].casefold() in wanted]
    out: list[str] = []
    wanted_sources: dict[str, list[str]] = {}
    for row in rows:
        for cite in row.get("citations") or []:
            eid = _clean(cite.get("evidence_id"))
            if eid and eid.upper().startswith("EVID-") and eid not in out:
                out.append(eid)
        for sid in row.get("source_ids") or []:
            wanted_sources.setdefault(_clean(sid), []).append(row.get("text", ""))

    # Exact fact-ID metadata on evidence records is safe and preferred.
    for record in evidence_records or []:
        if not isinstance(record, Mapping):
            continue
        eid = _clean(record.get("evidence_id") or record.get("id"))
        if not eid or not eid.upper().startswith("EVID-") or eid in out:
            continue
        record_fact_ids = {x.casefold() for x in _walk_values(record, {"fact_id", "fact_ids"})}
        if wanted & record_fact_ids:
            out.append(eid)
            continue

        # Conservative fallback: same source AND substantial lexical overlap.
        sid = _clean(record.get("source_id"))
        if sid and sid in wanted_sources:
            body = _evidence_text(record)
            if body and max((_token_overlap(body, text) for text in wanted_sources[sid]), default=0.0) >= 0.55:
                out.append(eid)
    return out


def premises_for_item(
    item: Mapping[str, Any],
    facts: Sequence[Mapping[str, Any]],
    evidence_records: Sequence[Mapping[str, Any]] = (),
) -> list[dict[str, Any]]:
    """Create the non-empty premise contract required by EvidenceDiscoveryAgent."""
    wanted = {x.casefold() for x in _unique(item.get("fact_ids") or [])}
    premises: list[dict[str, Any]] = []
    for row in fact_rows(facts, limit=250):
        if row["fact_id"].casefold() not in wanted:
            continue
        eids = evidence_ids_for_fact_ids(facts, [row["fact_id"]], evidence_records)
        premises.append({
            "premise_id": f"LAB-P-{row['fact_id']}",
            "text": row["text"],
            "premise_type": "factual",
            "evidence_ids": eids,
        })
    if not premises:
        # Fail closed but keep the discovery contract valid; the model may search
        # the exact thesis premise rather than crashing before any search occurs.
        premises.append({
            "premise_id": "LAB-P-THESIS",
            "text": _clean(item.get("anomaly_or_trigger") or item.get("thesis")),
            "premise_type": "factual",
            "evidence_ids": [],
        })
    return premises


def dedupe_facts(facts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """The same sentence from several sources is one fact (RUN-3E2FE: F6=F8, F10=F12, F14=F16),
    keeping every source's evidence id with it. IDs are not renumbered, so citations stay valid."""
    out: list[dict[str, Any]] = []
    by_text: dict[str, dict[str, Any]] = {}
    for fact in facts or []:
        if not isinstance(fact, Mapping):
            continue
        key = re.sub(r"\W+", " ", _clean(fact.get("text")).casefold()).strip()
        if key and key in by_text:
            kept = by_text[key]
            extra = _walk_values(fact, {"evidence_id", "evidence_ids"})
            kept["evidence_ids"] = _unique(list(kept.get("evidence_ids") or []) + extra)
            continue
        row = dict(fact)
        row["evidence_ids"] = _unique(list(row.get("evidence_ids") or [])
                                      + _walk_values(fact, {"evidence_id", "evidence_ids"}))
        out.append(row)
        if key:
            by_text[key] = row
    return out


PROMPT_FACT_LIMIT = 40
EARLIER_RULE = (
    "EARLIER COVERAGE (from previous runs; each is dated). Use these only for 'this happened before' "
    "reasoning - a pattern, a repeat by the same actor, or a comparable case - and name the date in "
    "the claim. They are never evidence that this run's events happened, and an earlier announcement "
    "is not an outcome unless the fact states what followed:")


def earlier_label(row: Mapping[str, Any]) -> str:
    when = [f"occurred {row['occurred_on']}"] if row.get("occurred_on") else []
    when += [f"reported {row['date']}"] if row.get("date") else []
    return f"[{row['fact_id']}] ({'; '.join(when) or 'date not recorded'}) {row['text']}"


def _facts_text(facts: Sequence[Mapping[str, Any]]) -> str:
    """Current facts (capped at PROMPT_FACT_LIMIT), then earlier coverage in its own dated
    block, which the cap never cuts (memory pass)."""
    rows = fact_rows(facts, limit=250)
    current = [r for r in rows if not r.get("earlier")][:PROMPT_FACT_LIMIT]
    earlier = [r for r in rows if r.get("earlier")]
    text = "\n".join(f"[{r['fact_id']}] {r['text']}" for r in current)
    if earlier:
        text += "\n\n" + EARLIER_RULE + "\n" + "\n".join(earlier_label(r) for r in earlier)
    return text


# v1.4 (resource-dependence theory, Pfeffer & Salancik): leverage needs dependence, few alternatives
# AND asymmetry. The lab asked for the counterparty's alternatives but never required or checked them,
# and never asked whether the holder needs the counterparty just as much.
LEVERAGE_KEYS = ("holder", "counterparty", "dependency", "control_point", "outside_option", "holder_dependence")
LEVERAGE_TEST = (
    "LEVERAGE TEST: it is leverage only if (1) the counterparty needs what the holder controls, (2) the counterparty "
    "has few realistic alternatives - outside_option states them as the facts show them, or 'none named in the facts' "
    "(never 'no outside option exists'), and (3) the counterparty needs the holder more than the holder needs the "
    "counterparty - holder_dependence states what the holder needs back and why it is less. If the alternatives are "
    "easy or the dependence is mutual, it is not a leverage thesis.")


# v1.5 (TMGBench; Lamparth et al. wargames): LLMs predict a counterpart's move reliably only when it
# has an obvious best response, and persona/motive framing does not change what they predict. So a
# predicted move must rest on what that actor has ALREADY done (revealed behaviour in the facts or
# past cases); a move with no such basis is shown as a guess.
MOVE_BASIS_RULE = (
    "PREDICTED MOVES: likely_actor_move and likely_countermove must follow from what that actor has ALREADY done - "
    "its revealed behaviour in the facts or past cases - not from guesses about its motives. actor_move_basis and "
    "countermove_basis: the past action, as a fact states it, that each move follows from (cite that fact in "
    "fact_ids); an empty string if there is none, and the move is then shown as a guess.")

# v1.5 (Co-Scientist: scientists add their own hypotheses to the tournament): the user's own
# hypotheses enter the evolution stage under the same rules and checks. Off unless a file is given.
USER_HYPOTHESES_ENV = "EVENT_INTELLIGENCE_USER_HYPOTHESES"
MAX_USER_HYPOTHESES = 5


def load_user_hypotheses(path: str | Path) -> list[dict[str, str]]:
    """One hypothesis per paragraph (blank line between), or one per line; at most 5."""
    text = Path(path).read_text(encoding="utf-8-sig")
    blocks = [b for b in re.split(r"\n\s*\n", text) if _clean(b)]
    if len(blocks) == 1:
        blocks = [line for line in text.splitlines() if _clean(line)]
    rows = [_clean(b)[:600] for b in blocks if not _clean(b).startswith("#") and len(_clean(b)) >= 20]
    return [{"id": f"U{n + 1}", "text": t} for n, t in enumerate(rows[:MAX_USER_HYPOTHESES])]


def active_user_hypotheses() -> list[dict[str, str]]:
    path = _clean(os.getenv(USER_HYPOTHESES_ENV))
    return load_user_hypotheses(path) if path else []


def user_source_id(user_id: Any) -> str:
    """U3 -> B0H3: looks like any raw hypothesis id in the evolution prompt (v1.6)."""
    match = re.fullmatch(r"U(\d+)", _clean(user_id))
    return f"B0H{match.group(1)}" if match else _clean(user_id)


def user_id_from_source(source_id: Any) -> str:
    match = re.fullmatch(r"B0H(\d+)", _clean(source_id))
    return f"U{match.group(1)}" if match else ""


def user_hypotheses_fingerprint(rows: Sequence[Mapping[str, Any]]) -> str:
    if not rows:
        return ""
    return hashlib.sha256(json.dumps([r.get("text") for r in rows]).encode("utf-8")).hexdigest()[:12]


def leverage_complete(lev: Mapping[str, Any]) -> bool:
    return all(_clean(lev.get(k)) for k in LEVERAGE_KEYS)


# Typed mechanism steps (26 Oct 2026): the first step is checked as a fact and each later step only
# has to follow from the facts; exactly one unbacked later step is shown as the stated assumption.
MECHANISM_RULE = ("Start the chain with an observation that a cited fact states (step A); put any general "
                  "knowledge or judgment in a later step, never in the first. Each step is checked on its own.")


def generation_prompt(topic: str, facts: Sequence[Mapping[str, Any]], lenses: Sequence[str], *, batch: int,
                      profile: str = "") -> str:
    lens_text = "\n".join(f"- {lens}" for lens in lenses)
    return f"""Research question: {topic}

You are the divergent hypothesis-generation stage of a strategic intelligence system.
Do NOT write a briefing and do NOT begin with recommendations. The ordinary facts are already known.
Your task is to discover propositions that would CHANGE a knowledgeable reader's mental model.

Generate {GENERATION_TARGET_PER_BATCH} materially different hypotheses using these lenses (cover every lens at least once):
{lens_text}

A strong hypothesis should usually explain something surprising: a contradiction, a hidden dependency, a shifted bottleneck,
a payoff/incentive mismatch, a control point, a feedback loop, a non-obvious second-order consequence, or why several events
that look separate are actually consequences of the same mechanism.

Reject banal outputs such as "regulation increases compliance costs", "competition is increasing", "X should cooperate",
or a summary of two articles. Recommendations are allowed only AFTER the structural thesis is established.

FACTS (use only these facts; cite their IDs):
{_facts_text(facts)}
{(chr(10) + profile + chr(10)) if profile else ""}
Return one JSON object with key "hypotheses". Each item must contain:
- id: B{batch}H1 etc.
- lens
- thesis: one precise proposition, 1-3 sentences
- novel_proposition: the part a knowledgeable reader probably would not infer from one source
- why_not_obvious
- anomaly_or_trigger: what observation needs explaining
- mechanism: an explicit A -> B -> C causal/strategic chain. {MECHANISM_RULE}
- fact_ids: 2-6 fact IDs that anchor the thesis (one is allowed only for a genuine internal contradiction)
- leverage: {{holder,counterparty,dependency,control_point,outside_option,holder_dependence}}; use empty strings if not a leverage thesis.
  {LEVERAGE_TEST}
- second_order_effect
- likely_actor_move
- actor_move_basis
- likely_countermove
- countermove_basis
  {MOVE_BASIS_RULE}
- alternative_explanation: strongest plausible rival explanation
- falsifier: an observation that would materially weaken/kill the thesis
- watch_signal: a concrete observable signpost
- research_question: the single missing question whose answer would best discriminate this thesis from the alternative

Before naming a mechanism, consider the ordinary explanation: two events occurring together does not show causation.
Use literal language rather than metaphors such as "quasi-sovereign" or "privatised diplomacy" unless the facts
establish the additional institutional change. Say which observation distinguishes your explanation from the rival.
State the specific decision or watch condition that changes. A new label for a familiar event is not an insight.
Do not pad. If the corpus cannot support {GENERATION_TARGET_PER_BATCH} strong distinct hypotheses, return fewer."""


def _candidate(row: Mapping[str, Any], valid_facts: set[str], *, fallback_id: str) -> dict[str, Any] | None:
    thesis = _clean(row.get("thesis") or row.get("insight"))
    mechanism = _clean(row.get("mechanism"))
    novel = _clean(row.get("novel_proposition"))
    why = _clean(row.get("why_not_obvious"))
    alt = _clean(row.get("alternative_explanation"))
    falsifier = _clean(row.get("falsifier"))
    watch = _clean(row.get("watch_signal"))
    fact_ids = [f for f in _unique(row.get("fact_ids") or []) if f.casefold() in valid_facts]
    lens = _clean(row.get("lens"))
    if not all((thesis, mechanism, novel, why)):
        return None
    if not alt or not falsifier or not watch or not fact_ids:
        return None
    if any(re.fullmatch(pattern, thesis, flags=re.I) for pattern in _GENERIC_PATTERNS):
        return None
    leverage = row.get("leverage") if isinstance(row.get("leverage"), Mapping) else {}
    lev = {k: _clean(leverage.get(k)) for k in LEVERAGE_KEYS}
    lev["complete"] = leverage_complete(lev)   # v1.4: alternatives and the holder's own dependence included
    return {
        "id": _clean(row.get("id")) or fallback_id,
        "lens": lens or "other",
        "thesis": thesis[:1400],
        "novel_proposition": novel[:900],
        "why_not_obvious": why[:900],
        "anomaly_or_trigger": _clean(row.get("anomaly_or_trigger"))[:900],
        "mechanism": mechanism[:1400],
        "fact_ids": fact_ids[:6],
        "leverage": lev,
        "second_order_effect": _clean(row.get("second_order_effect"))[:900],
        "likely_actor_move": _clean(row.get("likely_actor_move"))[:700],
        "actor_move_basis": _clean(row.get("actor_move_basis"))[:500],
        "likely_countermove": _clean(row.get("likely_countermove"))[:700],
        "countermove_basis": _clean(row.get("countermove_basis"))[:500],
        "alternative_explanation": alt[:900],
        "falsifier": falsifier[:700],
        "watch_signal": watch[:700],
        "research_question": _clean(row.get("research_question"))[:700],
    }


def _count(stats: dict[str, int] | None, key: str, n: int = 1) -> None:
    """Insight funnel (services/insight_funnel.py): losses no report records otherwise."""
    if stats is not None and n:
        stats[key] = stats.get(key, 0) + n


def parse_generation(reply: Any, facts: Sequence[Mapping[str, Any]],
                     stats: dict[str, int] | None = None) -> list[dict[str, Any]]:
    data = _json(reply)
    valid = {r["fact_id"].casefold() for r in fact_rows(facts, limit=200)}
    out: list[dict[str, Any]] = []
    rows = data.get("hypotheses") or []
    _count(stats, "generated_rows", len(rows))
    for n, row in enumerate(rows):
        if not isinstance(row, Mapping):
            _count(stats, "generated_invalid")
            continue
        item = _candidate(row, valid, fallback_id=f"H{n + 1}")
        if item:
            out.append(item)
        else:
            _count(stats, "generated_invalid")
    unique = dedupe(out)
    _count(stats, "generated_duplicates", len(out) - len(unique))
    _count(stats, "generated_over_cap", max(0, len(unique) - MAX_RAW))
    return unique[:MAX_RAW]


def dedupe(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Cheap lexical dedupe; model evolution handles semantic overlap."""
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items or []:
        item = dict(raw)
        words = re.findall(r"[a-z0-9]+", _clean(item.get("thesis")).casefold())
        key = " ".join(words[:18])
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


def evolution_prompt(topic: str, items: Sequence[Mapping[str, Any]], facts: Sequence[Mapping[str, Any]],
                     profile: str = "", user_hypotheses: Sequence[Mapping[str, Any]] = ()) -> str:
    entries = list(items)
    if user_hypotheses:
        # v1.6 (SycEval 2025: models side with the user in ~58% of cases, Gemini ~62%, more so when the
        # input sounds authoritative): the user's hypotheses are mixed in UNLABELLED, in the same schema,
        # at a shuffled position, so the model cannot defer to them. Their ids are mapped back in code.
        fields = list(items[0].keys()) if items else ["id", "lens", "thesis"]
        blank = {key: "" for key in fields if key not in {"fact_ids", "leverage"}}
        disguised = [dict(blank, id=user_source_id(u.get("id")), lens="other", thesis=_clean(u.get("text")))
                     for u in user_hypotheses]
        rng = random.Random(len(entries) * 31 + len(disguised))
        for row in disguised:
            entries.insert(rng.randint(0, len(entries)), row)
    serialized = json.dumps(entries, ensure_ascii=False, indent=2)
    return f"""Research question: {topic}

You are the adversarial evolution stage. Below are divergent hypotheses generated from the same evidence.
Do not reward eloquence. Your job is to produce at most {MAX_EVOLVED} mature hypotheses that could plausibly be 9/10 strategic analysis.

For each candidate:
1. Attack the mechanism and its weakest causal bridge.
2. Compare it with its alternative explanation.
3. Ask whether it really changes the reader's model or merely sounds clever.
4. Merge complementary candidates only when their mechanisms genuinely compose.
5. Narrow claims that outrun the facts; do not invent bridging facts.
6. Push one step further into consequence/countermove only where the mechanism supports it.
7. Preserve a falsifier and concrete watch signal.
8. Do NOT infer deliberate intent from timing, rhetoric, naming overlap or political coincidence unless a fact directly supports intent.
9. Do NOT claim a legal loophole from branding/nomenclature alone; verify the operative statutory definition or frame it as a research question.
10. Avoid absolutes such as "physically impossible", "no outside option", "uniquely capable", "all", or "universally" unless the facts establish them. Narrow scale claims instead.
11. Separate an observed fact (for example huge review volume) from the stronger inference (for example regulator dependence).
12. {LEVERAGE_TEST}
13. {MOVE_BASIS_RULE}

A 9/10 candidate should reveal an overlooked constraint/control point/incentive or explain a surprising pattern, have a defensible
mechanism, tell us who gains/loses or what changes next, and be falsifiable. A generic recommendation is not a candidate.
Mechanism format: {MECHANISM_RULE}

FACTS:
{_facts_text(facts)}
{(chr(10) + profile + chr(10)) if profile else ""}
RAW HYPOTHESES:
{serialized}

Return JSON {{"hypotheses":[...]}} using the same schema as the input, plus:
- source_ids: IDs of raw hypotheses used
- evolution_note: what was strengthened/merged/narrowed
- critic_scores: integers 0-2 for mental_model_change, mechanism_depth, surprise_nonobviousness, strategic_consequence, falsifiability

Only return candidates with total critic score >= 7/10. Do not fabricate facts."""


def parse_evolution(reply: Any, facts: Sequence[Mapping[str, Any]],
                    stats: dict[str, int] | None = None) -> list[dict[str, Any]]:
    data = _json(reply)
    valid = {r["fact_id"].casefold() for r in fact_rows(facts, limit=200)}
    out: list[dict[str, Any]] = []
    rows = data.get("hypotheses") or []
    _count(stats, "evolution_rows", len(rows))
    for n, row in enumerate(rows):
        if not isinstance(row, Mapping):
            _count(stats, "evolution_invalid")
            continue
        item = _candidate(row, valid, fallback_id=f"E{n + 1}")
        if not item:
            _count(stats, "evolution_invalid")
            continue
        scores = row.get("critic_scores") if isinstance(row.get("critic_scores"), Mapping) else {}
        normalized = {k: max(0, min(2, int(scores.get(k, 0) or 0))) for k in (
            "mental_model_change", "mechanism_depth", "surprise_nonobviousness", "strategic_consequence", "falsifiability"
        )}
        total = sum(normalized.values())
        if total < 7:
            _count(stats, "evolution_low_critic")
            continue
        item["critic_scores"] = normalized
        item["critic_total"] = total
        item["source_ids"] = _unique(row.get("source_ids") or [])[:6]
        item["evolution_note"] = _clean(row.get("evolution_note"))[:700]
        item["from_user"] = [u for u in (user_id_from_source(x) for x in item["source_ids"]) if u]   # v1.5/1.6
        out.append(item)
    unique = dedupe(out)
    _count(stats, "evolution_duplicates", len(out) - len(unique))
    unique.sort(key=lambda x: (-int(x.get("critic_total", 0)), -len(x.get("fact_ids") or [])))
    _count(stats, "evolution_over_cap", max(0, len(unique) - MAX_EVOLVED))
    return unique[:MAX_EVOLVED]


def to_research_candidate(
    item: Mapping[str, Any],
    evidence_ids: Sequence[str],
    *,
    facts: Sequence[Mapping[str, Any]] = (),
    evidence_records: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Shape compatible with HypothesisEvidenceService/EvidenceDiscoveryAgent.

    v1.0 omitted insight_type and, critically, premises; the discovery agent
    requires at least one premise and raised EvidenceDiscoveryValidationError.
    """
    return {
        "insight_id": f"LAB-{_clean(item.get('id'))}",
        "insight_type": "prospective",
        "insight": _clean(item.get("thesis")),
        "premises": premises_for_item(item, facts, evidence_records),
        "mechanism": {"description": _clean(item.get("mechanism"))},
        "second_order_effect": _clean(item.get("second_order_effect")),
        "critical_unknowns": [x for x in (_clean(item.get("research_question")), _clean(item.get("falsifier"))) if x],
        "alternative_explanations": [x for x in (_clean(item.get("alternative_explanation")),) if x],
        "supporting_evidence_ids": list(evidence_ids),
        "counterevidence_ids": [],
        "metadata": {
            "origin": VERSION,
            "lens": _clean(item.get("lens")),
            "novel_proposition": _clean(item.get("novel_proposition")),
            "why_not_obvious": _clean(item.get("why_not_obvious")),
        },
    }


DISPLAYED_FIELDS = ("leverage", "leverage_alternatives", "leverage_asymmetry", "likely_actor_move",
                    "likely_countermove")  # v1.3: each checked on its own; v1.4: the leverage test too
LEVERAGE_TEST_PARTS = ("leverage", "leverage_alternatives", "leverage_asymmetry")


def leverage_sentence(lev: Mapping[str, Any]) -> str:
    holder, counterparty = _clean(lev.get("holder")), _clean(lev.get("counterparty"))
    control, dependency = _clean(lev.get("control_point")), _clean(lev.get("dependency"))
    if not (holder and counterparty and (control or dependency)):
        return ""
    return (f"{holder} controls {control or 'something'}"
            + (f", and {counterparty} depends on it for {dependency}" if dependency else f", which {counterparty} needs")
            + ".")


def sort_by_critic(items: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """Critic score first, then supporting evidence. An unscored item (a narrowed rewrite) is
    placed at the median of the scored ones: unscored is not a low score."""
    scores = [x["critic_total"] for x in items if isinstance(x.get("critic_total"), int)]
    neutral = sorted(scores)[len(scores) // 2] if scores else 0
    return sorted(items, key=lambda x: (
        -(x["critic_total"] if isinstance(x.get("critic_total"), int) else neutral),
        -len(x.get("supporting_evidence_ids") or [])))


BASIS_FIELDS = ("actor_move_basis", "countermove_basis")
_FACT_REF = r"[A-Za-z]{1,3}\d{1,3}"
_ONLY_FACT_REFS = re.compile(rf"^\W*{_FACT_REF}(?:\W+(?:and\W+)?{_FACT_REF})*\W*$", re.I)


def resolve_basis_refs(item: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    """A move basis written only as fact IDs ("F7", "F16, F17") is replaced by those facts'
    text from this candidate's fact table, so grounding checks the statement rather than the
    literal "F7" (RUN-3BF777: EV2-EV4 failed that way). Prose citing "(F3, F5)" is unchanged.
    An unknown ID is never dropped silently: the basis is left out of grounding and its field
    is reported as not_checked, with the IDs in unresolved_fact_ids."""
    out = dict(item)
    table = {r["fact_id"].casefold(): r["text"] for r in fact_rows(facts, limit=250)}
    unresolved: dict[str, list[str]] = {}
    resolved: dict[str, list[str]] = {}   # the basis cites these facts, so their evidence goes with it
    for field in BASIS_FIELDS:
        text = _clean(item.get(field))
        if not text or not _ONLY_FACT_REFS.match(text):
            continue
        ids = _unique(re.findall(_FACT_REF, text))
        missing = [x for x in ids if x.casefold() not in table]
        out[field + "_as_written"] = text
        if missing:
            unresolved[field] = missing
            out[field] = ""
        else:
            out[field] = "; ".join(f"{x}: {table[x.casefold()]}" for x in ids)[:1500]
            resolved[field] = ids
    if unresolved:
        out["unresolved_fact_ids"] = unresolved
    if resolved:
        out["basis_fact_ids"] = resolved
    return out


def grounding_claims(item: Mapping[str, Any], evidence_ids: Sequence[str],
                     basis_evidence_ids: Mapping[str, Sequence[str]] | None = None) -> list[dict[str, Any]]:
    """Thesis and mechanism decide 'verified'. v1.3 (GPT review of v6.34): the leverage chain,
    likely move and countermove are shown too, so each is checked separately and shown with
    its own status - a field the sources do not back is labelled, never presented as verified.
    basis_evidence_ids: evidence of the facts a resolved basis cites (resolve_basis_refs), added
    to that basis claim only - a basis may cite a fact outside the candidate's own fact_ids."""
    base = f"LAB-{_clean(item.get('id'))}"
    ids = list(evidence_ids)
    basis_evidence_ids = basis_evidence_ids or {}
    claims = [
        {"claim_id": base + "-THESIS", "claim_text": _clean(item.get("thesis")),
         "epistemic_type": "hidden_insight", "evidence_ids": ids, "multi_evidence_required": len(ids) >= 2},
    ]
    steps = mechanism_steps(item.get("mechanism"))
    if len(steps) >= 2:
        # Typed steps (26 Oct 2026): the starting observation is a fact and must be stated by a
        # source; each link only has to follow from the sourced facts. Graded as one claim, one
        # unsourced background step failed the whole chain (EV3, RUN-3BF777).
        claims.append({"claim_id": base + "-MECH-S1", "claim_text": steps[0], "epistemic_type": "fact",
                       "evidence_ids": ids, "multi_evidence_required": False})
        for n in range(1, len(steps)):
            claims.append({"claim_id": f"{base}-MECH-L{n + 1}",
                           "claim_text": f"Given that {steps[n - 1]}, {steps[n]}",
                           "epistemic_type": "mechanism", "evidence_ids": ids, "multi_evidence_required": False})
    else:
        claims.append({"claim_id": base + "-MECH", "claim_text": _clean(item.get("mechanism")),
                       "epistemic_type": "mechanism", "evidence_ids": ids, "multi_evidence_required": len(ids) >= 2})
    lev = item.get("leverage") if isinstance(item.get("leverage"), Mapping) else {}
    sentence = leverage_sentence(lev)
    # GPT lab-dependency patch, merged 26 Oct 2026: control is a factual premise; dependency is a
    # separate inference. A sourced control point or an absence of contradiction never establishes it.
    holder, counterparty = _clean(lev.get("holder")), _clean(lev.get("counterparty"))
    control, dependency = _clean(lev.get("control_point")), _clean(lev.get("dependency"))
    if holder and control:
        claims.append({"claim_id": base + "-CONTROL", "claim_text": f"{holder} controls {control}.",
                       "epistemic_type": "fact", "evidence_ids": ids, "multi_evidence_required": False})
    if holder and counterparty and control and dependency:
        claims.append({"claim_id": base + "-DEPENDENCY",
                       "claim_text": f"{counterparty} depends on {holder}'s {control} for {dependency}.",
                       "epistemic_type": "connection", "evidence_ids": ids, "multi_evidence_required": False})
    from services.leverage_validation import alternatives_known
    if sentence and alternatives_known(lev.get("outside_option")):
        claims.append({"claim_id": base + "-ALTERNATIVES", "evidence_ids": ids, "multi_evidence_required": False,
                       "epistemic_type": "connection",
                       "claim_text": f"{_clean(lev.get('counterparty'))}'s realistic alternatives to "
                                     f"{_clean(lev.get('holder'))} for this are limited: {_clean(lev.get('outside_option'))}"})
    if sentence and _clean(lev.get("holder_dependence")):
        claims.append({"claim_id": base + "-ASYMMETRY", "evidence_ids": ids, "multi_evidence_required": False,
                       "epistemic_type": "connection",
                       "claim_text": f"{_clean(lev.get('holder'))} depends on {_clean(lev.get('counterparty'))} less "
                                     f"than the reverse; what it needs back: {_clean(lev.get('holder_dependence'))}"})
    for suffix, key, basis in (("-MOVEBASIS", "likely_actor_move", "actor_move_basis"),
                               ("-COUNTERBASIS", "likely_countermove", "countermove_basis")):
        if _clean(item.get(key)) and _clean(item.get(basis)):   # v1.5: the past behaviour is a stated fact
            basis_ids = _unique(ids + [_clean(x) for x in basis_evidence_ids.get(basis) or []])
            claims.append({"claim_id": base + suffix, "claim_text": _clean(item.get(basis)), "epistemic_type": "fact",
                           "evidence_ids": basis_ids, "multi_evidence_required": False})
    for suffix, key in (("-MOVE", "likely_actor_move"), ("-COUNTER", "likely_countermove")):
        if _clean(item.get(key)):
            claims.append({"claim_id": base + suffix, "claim_text": _clean(item.get(key)),
                           "epistemic_type": "connection", "evidence_ids": ids, "multi_evidence_required": False})
    return claims


_INTENT_PATTERNS = (
    r"\bcalculated\b", r"\bdesigned to\b", r"\bdeliberately\b", r"\bintentional(?:ly)?\b",
    r"\bpoison pill\b", r"\bweaponiz(?:e|es|ed|ing)\b", r"\bto evade\b", r"\bto nullify\b",
)
_ABSOLUTE_PATTERNS = (
    r"\bphysically impossible\b", r"\buniquely capable\b", r"\bno (?:other|outside) option\b",
    r"\bmust blindly trust\b", r"\buniversal(?:ly)?\b", r"\bentirely\b",
)
_LEGAL_SEMANTIC_PATTERNS = (
    r"\blegal loophole\b", r"\bstatutory paradox\b", r"\bimmunity from\b",
    r"\bfalls? outside .* definition\b", r"\bsemantic (?:wedge|poison pill)\b",
)


def promotion_blockers(
    item: Mapping[str, Any],
    thesis_status: str,
    mechanism_status: str,
) -> list[str]:
    """Deterministic anti-cleverness gate for unusually strong speculative wording.

    Risky causal/legal/intent language can sit in either the thesis or the
    mechanism.  Promotion therefore requires direct support for BOTH core
    claims before such language is allowed into the verified pool.  This is
    intentionally conservative: an inference-supported bridge may remain
    promising, but it cannot masquerade as a verified structural fact.
    """
    text = " ".join(_clean(item.get(k)) for k in (
        "thesis", "novel_proposition", "mechanism", "likely_actor_move", "likely_countermove"
    ))
    blockers: list[str] = []
    direct = thesis_status == "supported" and mechanism_status == "supported"
    if any(re.search(p, text, flags=re.I) for p in _INTENT_PATTERNS) and not direct:
        blockers.append("direct_evidence_required_for_intent_attribution")
    if any(re.search(p, text, flags=re.I) for p in _ABSOLUTE_PATTERNS) and not direct:
        blockers.append("absolute_or_exclusive_claim_requires_direct_support")
    if any(re.search(p, text, flags=re.I) for p in _LEGAL_SEMANTIC_PATTERNS) and not direct:
        blockers.append("legal_semantic_mechanism_requires_direct_statutory_support")
    return blockers


def mechanism_steps(mechanism: Any) -> list[str]:
    """'A -> B -> C' as its steps (also accepts → and =>)."""
    return [s for s in (_clean(x) for x in re.split(r"\s*(?:->|→|=>)\s*", _clean(mechanism))) if s]


PASSING = frozenset({"supported", "inference_supported"})
ASSUMPTION_BRIDGED = "assumption_bridged"   # every step backed except one stated link


def mechanism_grade(grounded_claims: Sequence[Mapping[str, Any]]) -> tuple[str, list[str]]:
    """Mechanism status from its typed steps, and the one unbacked link if there is exactly one.

    contradicted         any step contradicted by a source
    supported/inference  the starting fact is backed and every link follows
    assumption_bridged   the starting fact is backed and exactly ONE link is unbacked: shown as
                         analysis with that link as its stated assumption (ICD 203 key assumption)
    partially_supported / unsupported otherwise. Legacy single -MECH claims grade as before."""
    rows = {_clean(c.get("claim_id")): c for c in grounded_claims or [] if isinstance(c, Mapping)}
    legacy = next((c for k, c in rows.items() if k.endswith("-MECH")), None)
    if legacy is not None:
        return _clean(legacy.get("grounding_status")) or "unsupported", []
    start = next((c for k, c in rows.items() if k.endswith("-MECH-S1")), None)
    links = [c for k, c in sorted(rows.items()) if re.search(r"-MECH-L\d+$", k)]
    if start is None:
        return "unsupported", []
    statuses = [_clean(start.get("grounding_status"))] + [_clean(c.get("grounding_status")) for c in links]
    if "contradicted" in statuses:
        return "contradicted", []
    unbacked = [c for c in links if _clean(c.get("grounding_status")) not in PASSING]
    start_backed = _clean(start.get("grounding_status")) in PASSING
    if start_backed and not unbacked:
        return ("supported" if all(s == "supported" for s in statuses) else "inference_supported"), []
    if start_backed and len(unbacked) == 1 and links:
        return ASSUMPTION_BRIDGED, [_clean(unbacked[0].get("claim_text"))]
    return ("partially_supported" if any(s in PASSING for s in statuses) else "unsupported"), []


def finalize(item: Mapping[str, Any], grounded_claims: Sequence[Mapping[str, Any]], facts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    statuses = {_clean(c.get("claim_id")): _clean(c.get("grounding_status")) for c in grounded_claims or []}
    support_ids = _unique([
        eid for claim in grounded_claims or [] if isinstance(claim, Mapping)
        for eid in (claim.get("supporting_evidence_ids") or [])
    ])
    thesis_status = next((v for k, v in statuses.items() if k.endswith("-THESIS")), "unsupported")
    mechanism_status, key_assumptions = mechanism_grade(grounded_claims)
    blockers = promotion_blockers(item, thesis_status, mechanism_status)
    verified = (
        thesis_status in PASSING
        and mechanism_status in PASSING
        and not blockers
    )
    # Analysis: the thesis follows from the sources and the chain holds except one stated link.
    analysis = (not verified and thesis_status in PASSING and mechanism_status == ASSUMPTION_BRIDGED
                and not blockers)
    field_checks = {}
    lev = item.get("leverage") if isinstance(item.get("leverage"), Mapping) else {}
    for suffix, field in (("-LEVERAGE", "leverage"), ("-ALTERNATIVES", "leverage_alternatives"),
                          ("-ASYMMETRY", "leverage_asymmetry"), ("-MOVEBASIS", "actor_move_basis"),
                          ("-COUNTERBASIS", "countermove_basis"), ("-MOVE", "likely_actor_move"),
                          ("-COUNTER", "likely_countermove")):
        status = next((v for k, v in statuses.items() if k.endswith(suffix)), "")
        present = {"leverage": leverage_sentence(lev), "leverage_alternatives": _clean(lev.get("outside_option")),
                   "leverage_asymmetry": _clean(lev.get("holder_dependence"))}.get(field, _clean(item.get(field)))
        if status or present:
            field_checks[field] = status or "not_checked"
    for field in (item.get("unresolved_fact_ids") or {}):   # cited an unknown fact ID: not checkable
        field_checks[field] = "not_checked"
    # GPT lab-dependency patch (merged): fresh split checks supersede a legacy combined check.
    # Missing components fail closed; inference_supported is valid for dependency, never for control.
    split = any(k.endswith(("-CONTROL", "-DEPENDENCY")) for k in statuses)
    if leverage_sentence(lev):
        control = next((v for k, v in statuses.items() if k.endswith("-CONTROL")), "not_checked")
        dependency = next((v for k, v in statuses.items() if k.endswith("-DEPENDENCY")), "not_checked")
        field_checks["leverage_control"] = control
        field_checks["leverage_dependency"] = dependency
        if split:
            if "contradicted" in (control, dependency):
                field_checks["leverage"] = "contradicted"
            elif control == "supported" and dependency in PASSING:
                field_checks["leverage"] = dependency
            else:
                field_checks["leverage"] = "unsupported"
        elif not all(_clean(lev.get(k)) for k in ("holder", "counterparty", "control_point", "dependency")):
            field_checks["leverage"] = "not_checked"
    from services.leverage_validation import result as leverage_result
    # Unknown alternatives must never become evidence of limited alternatives.
    if leverage_sentence(lev):
        leverage_test = leverage_result(lev, field_checks)
    else:
        leverage_test = ""
    result = dict(item)
    result.update({
        "field_checks": field_checks,
        "leverage_test": leverage_test,
        "status": "verified" if verified else "analysis" if analysis else "promising_unverified",
        "grounding": {"thesis": thesis_status, "mechanism": mechanism_status},
        "key_assumptions": key_assumptions if analysis else [],
        "supporting_evidence_ids": support_ids,
        "evidence_ids": _unique(item.get("resolved_evidence_ids") or evidence_ids_for_fact_ids(facts, item.get("fact_ids") or [])),
        "promotion_blockers": blockers,
        "quality_origin": VERSION,
    })
    return result


# v1.2 narrow-and-retry -------------------------------------------------------------
# A strong idea is often blocked by its WORDING ("physically impossible", "calculated",
# "legal loophole") or one overstated bridge, while its core - who holds what, who depends
# on it, what move follows - is backed. Intelligence practice (ICD 203; Key Assumptions
# Check) is to state the judgment at the strength the evidence supports, not to drop it.
# One bounded rewrite per candidate, then the SAME grounding and promotion checks.

def _blocked_terms(text: str) -> list[str]:
    found = []
    for pattern in _INTENT_PATTERNS + _ABSOLUTE_PATTERNS + _LEGAL_SEMANTIC_PATTERNS:
        match = re.search(pattern, text, flags=re.I)
        if match:
            found.append(match.group(0))
    return found


def needs_narrowing(final: Mapping[str, Any]) -> bool:
    if final.get("status") in {"verified", "analysis"} or final.get("narrowed_from"):
        return False
    grounding = final.get("grounding") if isinstance(final.get("grounding"), Mapping) else {}
    if "contradicted" in {_clean(grounding.get("thesis")), _clean(grounding.get("mechanism"))}:
        return False                       # contradicted by evidence: not rescued by rewording
    return bool(final.get("promotion_blockers")) or any(
        _clean(grounding.get(k)) in {"unsupported", "partially_supported", "insufficient_evidence"}
        for k in ("thesis", "mechanism"))


def narrow_prompt(final: Mapping[str, Any], facts: Sequence[Mapping[str, Any]],
                  grounded_claims: Sequence[Mapping[str, Any]] = ()) -> str:
    wanted = {x.casefold() for x in _unique(final.get("fact_ids") or [])}
    rows = [r for r in fact_rows(facts, limit=250) if r["fact_id"].casefold() in wanted]
    problems = []
    for claim in grounded_claims or []:
        if isinstance(claim, Mapping) and _clean(claim.get("grounding_status")) not in {"supported", "inference_supported"}:
            parts = "; ".join(_clean(x) for x in claim.get("unsupported_parts") or [] if _clean(x))
            problems.append(f"- {_clean(claim.get('claim_id'))}: {_clean(claim.get('grounding_status'))}"
                            + (f" ({parts[:300]})" if parts else ""))
    for blocker in final.get("promotion_blockers") or []:
        problems.append(f"- blocked: {blocker}")
    terms = _blocked_terms(" ".join(_clean(final.get(k)) for k in ("thesis", "mechanism", "novel_proposition",
                                                                       "likely_actor_move", "likely_countermove")))
    lev = final.get("leverage") if isinstance(final.get("leverage"), Mapping) else {}
    return (
        "NARROW THIS STRATEGIC HYPOTHESIS TO WHAT THE FACTS SUPPORT.\n"
        "Keep its strategic core - the holder, the counterparty, what the counterparty depends on, the control point, "
        "and the move that follows - but state every part only as strongly as the facts below establish. Rules:\n"
        "1. Remove intent claims (calculated, designed to, deliberately, weaponize, poison pill) unless a fact states the intent.\n"
        "2. Remove absolutes (physically impossible, uniquely capable, entirely, universally, no other option); use the "
        "narrower observed claim (e.g. 'only OpenAI holds the logs' if a fact says so, not 'impossible to audit').\n"
        "3. Remove legal-loophole or naming arguments unless a fact quotes the statute.\n"
        "4. Cite only the fact IDs listed; add no new facts, numbers or actors.\n"
        "5. A move or effect that does not follow from these facts is dropped, not softened into 'may'.\n"
        "6. If nothing strategic survives, return {\"drop\": true}.\n"
        "7. Mechanism format: " + MECHANISM_RULE + "\n\n"
        f"ORIGINAL THESIS: {_clean(final.get('thesis'))}\n"
        f"ORIGINAL MECHANISM: {_clean(final.get('mechanism'))}\n"
        f"LEVERAGE: holder={_clean(lev.get('holder'))}; counterparty={_clean(lev.get('counterparty'))}; "
        f"dependency={_clean(lev.get('dependency'))}; control_point={_clean(lev.get('control_point'))}; "
        f"outside_option={_clean(lev.get('outside_option'))}; holder_dependence={_clean(lev.get('holder_dependence'))}\n"
        + LEVERAGE_TEST + "\n"
        f"LIKELY MOVE: {_clean(final.get('likely_actor_move'))} (based on: {_clean(final.get('actor_move_basis')) or 'nothing cited'})\n"
        + MOVE_BASIS_RULE + "\n"
        + (f"WORDS THAT BLOCKED IT: {', '.join(terms)}\n" if terms else "")
        + ("WHAT FAILED:\n" + "\n".join(problems) + "\n" if problems else "")
        + "FACTS:\n" + "\n".join(earlier_label(r) if r.get("earlier") else f"[{r['fact_id']}] {r['text']}"
                                 for r in rows) +
        '\n\nRewrite EVERY field below for the narrower claim (an empty string if it no longer follows); '
        'do not carry over wording from the original.\n'
        'Return JSON: {"thesis":"...","mechanism":"A -> B -> C","novel_proposition":"...","why_not_obvious":"...",'
        '"likely_actor_move":"...","actor_move_basis":"...","likely_countermove":"...","countermove_basis":"...",'
        '"second_order_effect":"...","watch_signal":"...",'
        '"falsifier":"...","leverage":{"holder":"","counterparty":"","dependency":"","control_point":"",'
        '"outside_option":"","holder_dependence":""},"fact_ids":["F1"],"what_was_narrowed":"one sentence"} or {"drop": true}'
    )


def parse_narrow(reply: Any, final: Mapping[str, Any], facts: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    data = reply if isinstance(reply, Mapping) else _json(reply)
    if not isinstance(data, Mapping) or data.get("drop"):
        return None
    thesis, mechanism = _clean(data.get("thesis")), _clean(data.get("mechanism"))
    if len(thesis) < 30 or not mechanism:
        return None
    if _blocked_terms(" ".join((thesis, mechanism, _clean(data.get("likely_actor_move"))))):
        return None                        # still overstated: not retried again
    allowed = {x.casefold(): x for x in _unique(final.get("fact_ids") or [])}
    fact_ids = [allowed[x.casefold()] for x in _unique(data.get("fact_ids") or []) if x.casefold() in allowed]
    if not fact_ids:
        return None
    lev_in = data.get("leverage") if isinstance(data.get("leverage"), Mapping) else {}
    leverage = {k: _clean(lev_in.get(k))[:300] for k in LEVERAGE_KEYS}
    leverage["complete"] = leverage_complete(leverage)
    out = dict(final)
    for key in ("status", "grounding", "supporting_evidence_ids", "promotion_blockers", "grounded_claims",
                "supporting_evidence", "resolved_evidence_ids", "evidence_ids", "field_checks",
                "narrowing", "narrowed_attempt", "leverage_test", "critic_scores", "critic_total",
                "actor_move_basis_as_written", "countermove_basis_as_written", "unresolved_fact_ids",
                "basis_fact_ids"):
        out.pop(key, None)
    # The critic scored the original claim, not this rewrite: the rewrite is unscored, and the
    # original's score is kept only as a labelled reference (never reused for ranking).
    out["critic_inherited_from"] = _clean(final.get("id"))
    out["critic_total_before_narrowing"] = final.get("critic_total")
    # v1.3 (GPT review): every displayed field comes from the rewrite; nothing is inherited from the
    # overstated original. A field the rewrite leaves empty is shown as absent, not as the old text.
    rewritten = {key: _clean(data.get(key))[:900] for key in (
        "novel_proposition", "why_not_obvious", "likely_countermove", "second_order_effect", "watch_signal",
        "actor_move_basis", "countermove_basis",
        "falsifier", "alternative_explanation", "anomaly_or_trigger", "research_question")}
    if _blocked_terms(" ".join(rewritten.values())):
        return None
    out.update(rewritten)
    out.update({
        "id": _clean(final.get("id")) + "-N",
        "thesis": thesis[:900], "mechanism": mechanism[:900],
        "likely_actor_move": _clean(data.get("likely_actor_move"))[:500],
        "leverage": leverage, "fact_ids": fact_ids,
        "narrowed_from": {"thesis": _clean(final.get("thesis"))[:900],
                          "what_was_narrowed": _clean(data.get("what_was_narrowed"))[:300],
                          "original_grounding": dict(final.get("grounding") or {}),
                          "original_blockers": list(final.get("promotion_blockers") or [])},
    })
    return out
