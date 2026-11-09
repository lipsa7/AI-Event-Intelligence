# Plan: getting to the objective in two weeks

Status: proposal for review (23 Oct 2026). No code changed. Every fix below is local; none needs
a paid run to test.

## 1. Why two months have not converged

- **Review loop with no fixed target.** Each AI review adds a check, and none is removed:
  49 v6.x versions, `research_orchestrator_service.py` at about 23,000 lines, 2 git commits.
- **No acceptance test.** No output has ever been rated (`data/insight_ratings.jsonl` does not
  exist), so changes are judged by another model's opinion.
- **Frozen input.** Every run since 9 Oct replayed `data/checkpoints/RUN-ED2409…json`
  (September news).
- **Useful material is hidden or lost.** The lab's best hypotheses sit in a collapsed "not yet
  backed" section. Several bugs drop supported material (section 4).

## 2. Definition of done

"Working" means: **on fresh topics, most runs show at least one item the user rates *useful*,
each item honestly labelled, and no report is blank.** It does not mean every run gets a
hidden insight through every check.

Acceptance test, built before any further tuning:

1. **Pick examples.** The user picks about 10 good and 10 weak items from the saved reports
   (published items, verified hypotheses and rejected hypotheses), each with a one-line reason.
   Candidate good item: EV3 in RUN-3BF777 (OpenAI, not Australia, controlled when the breach was
   discovered). Candidate weak item: EV1-N (terminology clash).
2. **Store them.** Save them as ratings in `data/insight_ratings.jsonl` (dashboard form, or
   `compare_runs.py --blind-export` / `--blind-import`).
3. **Accept or reject changes against them.** A change is accepted if, on the saved cases, good
   items are kept or surfaced and no item with a false premise is promoted. Deterministic bugs
   (section 4) are fixed regardless, because they can be shown without a rating.
4. **Extend later.** Grow the set from fresh runs on new topics. Do not require 100–300 new paid
   outputs first.

## 3. Rules for the two weeks

- **No new gates or checks.** Reviews may report bugs; any new constraint waits until it can be
  shown to improve the acceptance test.
- **Track the funnel.** Add one log line per gate: candidates in, passed, rejected (with
  reasons). Without it, nobody can tell which check is costing the most.
- **One change at a time.** Each change gets a git commit and a test.
- **No paid replay** of the old checkpoint to test these fixes.

## 4. Fixes (narrow forms agreed in review)

Apply after the lab_dependency patch is installed. A and D touch
`services/strategic_hypothesis_lab.py`, which that patch hash-checks.

### A. Bare fact-ID basis

- **Problem.** `actor_move_basis` / `countermove_basis` written as "F7" or "F16, F17" are grounded
  as the literal text ("'F7' is just an identifier"). This hit EV2, EV3 and EV4 in RUN-3BF777.
- **Fix.** Add a lab helper that, when a basis consists only of fact IDs, replaces it with those
  facts' text from the candidate's own fact table (`fact_rows(facts, limit=250)`), e.g.
  "F7: <text>; F18: <text>".
  - Prose containing "(F3, F5)" stays as it is.
  - Unknown IDs are not dropped silently. The field is marked `unresolved_fact_ids`, and the
    basis check reads `not_checked`, not `unsupported`.
- **Where.** Call the helper in `ground_item` before `lab.grounding_claims`
  (`research_orchestrator_service.py:3603`), so grounding and display use the same text.
- **Tests.** IDs only → resolved. Mixed prose → unchanged. Unknown ID → flagged, not dropped.

### B. Focus-organisation research runs after the briefing

- **Problem.** `_focus_research` runs inside `_actor_moves` (`:3355`), after `_editorial_briefing`
  (`:3915`). In RUN-3BF777 it found the Capital One 2019 case at 12:23; the briefing had already
  chosen analogies from 0 cases at 12:21.
- **Fix.** In `_attach_spec_sections`, before the briefing:
  - Choose the focus organisation (same logic as `:3347-3353`) and run `_focus_research`.
  - Store the results in `state["focus"]` and `state["focus_research"]`; `_actor_moves` reuses
    them instead of searching again.
  - Run it early only if the remaining time covers focus research + briefing + actor moves.
    Otherwise keep today's late position.
  - The briefing keeps its current context ordering; focus records are not sorted first there,
    which protects general-context coverage.
  - A retrieved case must still pass `brief.select_cases` (observed outcome, similarity and
    difference).
- **Tests.**
  - A case returned by focus research reaches `editorial_briefing.case_count`.
  - Exactly one focus search per run.
  - Low time budget → today's behaviour.

### C. Expert commentary definition

- **Problem.** The review prompt (`hypothesis_evidence_service.py:429-447`) has no category for
  a quoted outside expert's assessment. Such records are tagged `reported_claim` and become
  "reported" (`:730`), so `expert_commentary` came out empty.
- **Fix (prompt only; the mapping is unchanged).** Define `analysis` as an assessment,
  explanation or recommendation by the page author, or by a named or described expert, analyst
  or organisation that is not a party to the events, with the attribution kept in the quote. A
  statement by a party to the events stays `reported_claim`, even when it is an opinion. Site
  credibility does not change this.
- **Tests.** A prompt-contract test that the definition is present. Fixture records: an expert
  quote → commentary; a minister's complaint → reported.

### D. Inherited critic scores after narrowing

- **Problem.** `parse_narrow` copies the original item and pops grounding fields, but not
  `critic_scores` / `critic_total`. EV1-N kept the original's 10/10 although its consequence had
  been cut.
- **Fix.** Clear both fields on the narrowed item and set `critic_inherited_from` to the original
  ID. Displays and logs show the original score labelled "before narrowing". No new model call.
- **Test.** A narrowed item has no `critic_total` of its own.

### E. Show the hypothesis tier (display only)

- **Problem.** When nothing passes, the headline reads "Hidden cross-actor insight: None this
  run", and the lab's hypotheses sit in a collapsed expander.
- **Fix.**
  - Show up to 3 hypotheses expanded, under their own heading: "Hypotheses: premises from
    sources, reasoning is analysis".
  - A hypothesis qualifies if its thesis is `inference_supported` or `partially_supported`, it
    has no contradicted claim, and it has at least one supported fact premise.
  - Each one shows its sourced premises, the unsupported step labelled as an assumption, and what
    would disprove it.
  - The hidden-insight slot keeps its meaning: "none passed every check" stays when true. A
    hypothesis is never called an insight, and the abstention status is unchanged.
- **Where.** `render_strategic_hypotheses` / `render_main_finding` (`frontend/dashboard.py:4025,
  4120`).

## 5. Fresh data

- **Credits.** Gemini credits ran out on 23 Oct (two runs ended with 402 RESOURCE_EXHAUSTED).
  Restore them first.
- **Runs.** After A–E: one fresh (non-replay) run on each of 2–3 different topics. Rate the
  outputs against section 2.
- **Memory.** Fresh runs also start filling cross-run memory; replays never write to it.

## 6. Deferred: memory pass

`docs/DESIGN_memory_pass.md` is on hold until A–E are in. Two review findings are confirmed
in code and must be addressed in a revised design:

- **Memory counts as corroboration.** `_convergence_maps` (`research_orchestrator_service.py:17072`)
  counts analytical-context records as independent events unless the gap is commentary,
  commentary_unrated or pattern. Records with gap `earlier` are not excluded.
- **Dates are lost.** `fact_rows` (`strategic_hypothesis_lab.py:134-169`) keeps no top-level
  date, and `_facts_text` renders only the ID and text, so earlier facts lose their dates in the
  prompt.

Also from review: separate occurrence, publication and availability dates; label earlier
coverage separately from outcome-bearing precedents; verify stored excerpts against article
text; make actor and mechanism retrieval rules consistent; freeze memory selections for
comparisons; test retrieval locally before any paid run.

## 7. Questions for review

1. Is the definition of done in section 2 acceptable?
2. Are the qualifying conditions for the hypothesis tier in E strict enough, and loose enough to
   show items like EV3?
3. B: is "run early only if time allows, otherwise today's behaviour" the right fallback?
4. Should the per-gate funnel log ship with A–E or come first?
