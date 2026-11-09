# Design: memory pass for the strategic hypothesis lab

Status: proposal for review (23 Oct 2026). No code written.

## Problem

The lab only sees facts from the current run (in RUN-3BF777…: 21 briefing facts, 7 context
facts). Past coverage is stored but not used:

| Store | Contents (23 Oct) | Used today |
|---|---|---|
| `data/event_intelligence.db` `events` + `articles` | 1,502 events, 448 articles (Aug and 9 Oct 2026) | Not read by the analysis |
| `data/event_memory.sqlite3` | 58 events, 162 excerpts, all from RUN-ED2409… (9 Oct) | `earlier_related()`: at most 3 events per development, 2 excerpts each, briefing context only |
| knowledge graph | 0 entities, 0 relationships | No |

So the lab cannot say "this happened before, with this result" unless a web precedent search
happens to find the case. In the last run those searches found nothing usable.

## Goal

Give the lab dated earlier facts that share an **actor** or a **mechanism** with this run's
developments, so a hypothesis can cite "F24 (12 Aug 2026): …" and go through the same grounding
and gates as any other fact. The gates are not relaxed.

## Design

### 1. Retrieve candidates (local, no model call)

For each of the top developments (`PRECEDENT_DEVELOPMENTS`, currently 3):

- **Actor match.** Normalise actor names from the development's `actors` and from
  `events.entity_mentions` (role `actor`, entity_type `organization` or `person`; present on
  880 of 1,502 events). Exact match after normalisation, plus the aliases the canonical resolver
  already produces.
- **Mechanism match.** Map both sides onto a small fixed vocabulary built from fields that
  already exist: `event_type` (e.g. `regulation_or_policy_action`, `legal_action`,
  `acquisition_or_merger`) and `relationship_mentions.relation_type`, plus a keyword list per
  mechanism (disclosure delay, breach, ban or rename, fine, licence, export control, price cut).
- **Score** = actor match ×2 + mechanism match + keyword Jaccard (as `earlier_related` does now).
  Take the top 20 per development.
- **Sources.** Read both stores and deduplicate on (source_id, normalised snippet). Memory rows
  keep their existing `MEM-` evidence IDs. DB events get new `DBEV-<hash>` evidence records,
  with `source_excerpt` = the event's `snippets[0]` (an exact span from extraction).

### 2. Date and leakage rules (hard)

- **Date.** An event is usable only if it has a date: `event_start_at`, or else the article's
  `published_at`. `event_start_at` is null on 1,500 of 1,502 events; `published_at` covers 1,232.
  Undated events are dropped.
- **No future information.** Only events dated before the run's `as_of` are used. This matters
  for replays: a replay of the 9 Oct checkpoint must not see events collected later.
- **Not part of this run.** Events whose underlying event already belongs to the current run are
  excluded, as `earlier_related` already does.

### 3. Optional rerank (1 cheap call per development)

Send one `gemini-3.5-flash-lite` call with the development and the 20 candidates (title and
date). It returns at most 5 IDs with a one-line "same mechanism because …". This drops
same-name-but-unrelated matches (e.g. August AI-infrastructure financing stories that share
"OpenAI"). If the call fails, fall back to the top 5 by score.

### 4. Feed the lab

- **Fact entries.** Add up to 8 earlier facts in total to the lab's fact list as normal fact
  entries, with `gap_type = "earlier"` (the briefing already labels this "Earlier coverage in
  your tracked runs"). Each carries a `date` and an `earlier_event_title`.
- **Fact cap.** `_facts_text` uses `fact_rows(facts)` with the default `limit=40`
  (`strategic_hypothesis_lab.py:263`). Earlier facts are appended last, so they could be cut off.
  Either reserve slots for them, or put them in their own block of the prompt.
- **Prompt rule (one line).** An earlier fact may support a precedent or a pattern ("the same
  actor did X in August"). It is never evidence that the current event happened, and its date
  must appear in the claim.
- **Grounding.** Include the `MEM-` and `DBEV-` records in `records` for
  `_ground_claims_isolated`, so claims that cite earlier facts are checked against the stored
  quotes.

### 5. What does not change

- `CORE_PASSING`, convergence, promotion blockers and narrowing are unchanged.
- Earlier records keep `analytical_context_only=True` and
  `prohibited_uses=["observed_event_count", "sample_base_rate", …]`. The stored corpus is a
  biased sample (only what past topic searches returned), so "X of N past cases" is not allowed
  from memory alone.
- Earlier records do **not** count towards `convergence()` for a current development. They show
  a pattern over time; they do not independently confirm today's fact.

## Cost

- **Retrieval.** Local SQLite, no API calls. A full scan of 1,502 rows is well under a second.
- **Rerank.** Up to 3 flash-lite calls per run, roughly 3k input tokens each.
- **Lab prompts.** Up to 8 extra facts × ~80 tokens ≈ 0.6–1k extra input tokens per
  generation, evolution and narrowing call, on the pro model (10 pro calls in the last run).
- **Grounding.** The extra records add input tokens to the existing grounding calls. No new
  calls.
- **Overall.** A small fraction of the last run's ~1.2M prompt tokens. No extra web searches.

## Risks

| Risk | Mitigation |
|---|---|
| Old news shown as new | Date shown in every claim; earlier facts are labelled; `as_of` filter |
| Leakage into replays | Strict `as_of` cut-off; tested on the 9 Oct checkpoint |
| Wrong analogy (same actor, different mechanism) | Mechanism match + optional rerank; the lab's existing similarity/difference rule |
| Double counting the same story | Deduplicate across stores; exclude from convergence |
| False base rates | `sample_base_rate` stays prohibited for memory records |
| Topic drift (August AI-infrastructure stories) | Actor *and* mechanism required unless the rerank confirms |
| Memory stays tiny | Replays never write memory (correctly), and every run since 9 Oct was a replay. This pass only pays off once fresh runs resume. |
| More orchestrator code | Put retrieval in `event_memory_service.py` (new `related_for_lab()`), with one call site in `_strategic_hypothesis_lab` |

## How to evaluate (offline first)

1. Replay the 9 Oct checkpoint with the pass on and off. Memory is filtered to before 9 Oct, so
   only the August events are available. This tests the retrieval and leakage rules.
2. Count lab hypotheses that cite an earlier fact, and how many of those pass grounding.
3. Rate both arms blind with `compare_runs.py --blind-export` / `--blind-import`. Keep the pass
   only if the share of "useful" ratings rises.

## Open questions for review

- Should DB events (one extraction, no cross-run reconciliation) be used, or only
  `event_memory`?
- Is the rerank call worth it, or is actor + mechanism matching precise enough?
- Should the dashboard show which hypotheses rely on earlier facts?
