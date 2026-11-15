# Event Intelligence: session summary (23–24 Oct 2026)

Everything discussed and done in this session, in one place. Companion files (all in `docs/` unless noted):

| File | What it is |
|---|---|
| `PLAN_next_two_weeks.md` | The agreed recovery plan (definition of done, rules, fixes A–E) |
| `DESIGN_memory_pass.md` | Proposal for feeding past events into the hypothesis lab (on hold; needs the revisions in section 7) |
| `reports/Event Intelligence insight strategy.md` | Research report: methods, comparable products, gaps, root causes |
| `REVIEW_funnel_and_fixes_ACD.md` | Code review package with full diff: funnel + fixes A, C, D |
| `REVIEW_fixes_B_E.md` | Code review package with full diff: fixes B and E |
| `research_notes/Event Intelligence insight strategy/` | The four research notes behind the report |

Nothing is committed. No paid model calls were made in this session.

---

## 1. The objective (your rules)

The app researches a topic and turns news events into **non-obvious, evidence-grounded strategic insight**:

- **Precedent:** "because this happened last time in a similar case, this can happen to this company."
- **Leverage and incentives:** "current regulations and incentives give actor X leverage, so move Y is right for it."
- **Hidden cross-actor connections** between developments.

Settled rules for the report:

- The report is never blank.
- The landscape and forward-looking implications always render.
- A historical analogy renders when one is available.
- A hidden insight renders when a genuine connection exists.
- What credible experts or organisations say can always be shown.
- An insight backed by sources should be displayed.

GPT's cautions, which this session accepted:

- "Not contradicted" is not "supported".
- Finding no counter-evidence is not confirmation.
- "Source-supported hypothesis" and "new strategic insight" are different labels.

## 2. What the last run showed (RUN-3BF777…, 23 Oct, 12:36)

- **Outcome:** topic "artificial intelligence", OpenAI profile, 33 minutes, 48 sources, about 253 model calls and about 1.27M prompt tokens. It **abstained with 0 insights**.
- **The one "verified" hypothesis (EV1-N) was trivial:** "the rename and the ban bill use conflicting terms". Narrowing had cut its consequence, yet it kept the original's 10/10 critic score.
- **The stronger ideas were rejected:**
  - **EV3:** OpenAI, not Australia, controlled when the breach was discovered, so Australia's 12–72 hour reporting rule couldn't start until OpenAI chose to tell it.
  - **EV2:** providers prioritise investigations for jurisdictions with the biggest fines.
  - **EV4:** governments put up with AI-vendor failures they would punish in other software suppliers.
  - **EV1:** the naming collision paralyses procurement.

  You judged EV2–EV4 "decent / nice".
- **Assessed individually:** EV3 is the closest near miss. EV2 contains an unsupported causal leap (the triage motive). EV4's "muted response" conflicts with the sources. EV1's "paralysis" is a leap.
- **Empty sections:** the historical analogy (0 cases), expert commentary, counter-evidence and the hidden-insight slot. The dashboard said "None this run" while the good ideas sat in a collapsed list.

## 3. Why two months didn't converge

1. **Frozen input.** Every run since 9 Oct replayed **one checkpoint** (`data/checkpoints/RUN-ED2409…json`, September news, 63 events). Replays never write cross-run memory.
2. **Unused stores.** `data/event_intelligence.db` holds 1,502 events from 448 articles that the analysis never reads. Cross-run memory holds 58 events from one run. The knowledge graph has 0 entities.
3. **No acceptance test.** No output was ever rated (`data/insight_ratings.jsonl` did not exist), so every change was judged by another model's opinion.
4. **A review loop that only adds checks.** About 49 v6.x versions, about 7 stacked gates on the hidden-insight path, a ~23,000-line orchestrator, and 2 git commits.
5. **Early October insights were published before the per-step claim gate existed.** All of their mechanism steps were "unsupported". They read better because the system was allowed to reason beyond the sources.
6. **Cost.** About $5k of Gemini credits in October. Credits ran out on 23 Oct (402 RESOURCE_EXHAUSTED).

## 4. Where supported material was lost (confirmed bugs)

| # | Loss | Evidence |
|---|---|---|
| A | Move/countermove basis written as bare fact IDs ("F7") was grounded as the literal string | 8 of 10 basis fields in the last run |
| B | Focus-org research ran after the briefing, so its Capital One 2019 case (found 12:23) missed the analogies (chosen 12:21) | logs, `_attach_spec_sections` order |
| C | Quoted outside-expert assessments were tagged `reported_claim`, so expert commentary was empty | `hypothesis_evidence_service.py` mapping |
| D | Narrowed rewrites inherited the original's critic score | `parse_narrow` |
| — | Leverage graded as one combined "fact" (control + dependency) | fixed by GPT's lab patch, not installed |
| — | Off-target precedent queries (e.g. "EPA 2015 waters rule" for an AI rename) | logs |
| — | 21 of 51 gap-research fetches failed on Google grounding-redirect URLs | logs |
| — | Lab generation prompt shows only 40 facts (`fact_rows` default limit) | code |
| — | Evaluator scores saturate (10/10 for trivia, 0 for the strategic-action items) | saved report |

GPT corrected one point from my first review. `inference_supported` **already exists** and the gate accepts it for mechanisms and conclusions. The problem is narrower than "original inference can never pass": typing errors, evidence handoffs and how strictly the model judges support.

## 5. Research: how this can be done, and how others do it

The full report is in `reports/Event Intelligence insight strategy.md`.

- **Intelligence tradecraft (ICD 203):** judgments not stated by any source are allowed. They must be labelled (information, assumption or judgment), state their linchpin assumptions, keep likelihood separate from confidence, and list alternatives and indicators. The professional answer is to label, not to delete.
- **Forecasting:** reference classes and base rates (Tetlock, Flyvbjerg). A single analogy generates a hypothesis, not a conclusion.
- **Strategy:** BATNA and walk-away options, game-theoretic reasoning, scenario robustness.
- **LLM research:** strict attribution checkers count a claim as supported only when it is stated directly. The suggested design is to verify premises against sources and judge the reasoning step separately. Rank ideas by pairwise comparison rather than strict per-idea filters, as Google's AI co-scientist does.
- **Products:** AlphaSense, Klue, Bloomberg, Dataminr, Recorded Future and Palantir rely mostly on proprietary data plus human analysts. GPT's correction: AlphaSense advertises strategy planning and Palantir offers recommended actions, so it is wrong to say that nobody does this. None publicly proves reliable original strategic judgment.

Caveats: product evidence is mostly vendor marketing, and some figures come from search snippets.

## 6. The agreed plan (`PLAN_next_two_weeks.md`, approved by GPT with corrections)

- **Definition of done (initial milestone):**
  - At least 2 of 3 fresh runs on different topics produce an item you rate *useful*.
  - No surfaced item presents a known false or contradicted premise as fact.
  - Every run gives an intelligible report or an explanation of the failure.

  Usefulness and correctness are rated separately.
- **Rules for two weeks:**
  - No new gates.
  - Funnel logging first.
  - One change at a time.
  - No paid replays.
  - Do not install the old patch ZIPs; their input hashes are outdated.
- **Order:** funnel → A → C → D → B → E (done), then fresh runs and your ratings, then the memory pass.

## 7. Memory-pass design (`DESIGN_memory_pass.md`, on hold)

**Idea:** retrieve past events that share an actor or a mechanism with this run's developments, from both stores, and feed them to the lab as dated earlier facts.

The cost is small: local SQLite queries plus about 1k extra tokens per lab prompt.

**Required revisions from GPT's review** (two of them confirmed in code):

1. **Memory must not count as corroboration of today's event.** `_convergence_maps` currently counts `earlier` records as independent events. *Confirmed.*
2. **Dates must reach the prompt.** `fact_rows` / `_facts_text` currently drop them. *Confirmed.*
3. **Separate the dates.** Track occurrence, publication and availability dates separately, and cut off strictly at the original checkpoint for historical tests.
4. **Coverage is not precedent.** Label earlier coverage separately from an outcome-bearing precedent.
5. **Verify stored excerpts.** Check them against article text, and never put old model conclusions into the evidence pool.
6. **Consistent retrieval rules.** Use separate lanes for the same actor's history and for comparable mechanisms involving other actors.
7. **Deduplicate by occurrence identity** across both stores.
8. **Freeze memory selections** for comparisons, with hashes recorded.
9. **Test locally first.** Test retrieval with saved data before any paid run.

## 8. Code changes made (not committed)

The full diffs and tests are in the two `REVIEW_*.md` files.

| Change | Files | What it does |
|---|---|---|
| **Insight funnel** | `services/insight_funnel.py` (new), lab parsers, orchestrator | Every report records each candidate's outcome (passed / rejected / downgraded / repaired) with ID and reason. Events, failed claims, gate attempts, pruned sentences and display states are counted separately. Missing history reads `not_recorded`. Logged as `insight_funnel=`. CLI: `venv/Scripts/python.exe -m services.insight_funnel [REPORT-ID]` (read-only). |
| **A** | `strategic_hypothesis_lab.py`, orchestrator `ground_item` | A basis made only of fact IDs is resolved to those facts' text. The cited facts' evidence goes to that basis claim only. Unknown IDs are flagged `not_checked`. |
| **C** | `hypothesis_evidence_service.py` | The review prompt defines an independent expert view (an outside expert, analyst or organisation). Statements by parties to the events stay `reported_claim`. |
| **D** | `strategic_hypothesis_lab.py`, orchestrator | Narrowed rewrites are unscored, and the original score is kept only as a labelled reference. Unscored items sort at the median, which is a provisional display order. |
| **B** | orchestrator | Focus research runs before the briefing when time allows (240 + 90 + 120 s); otherwise it runs late, as before. One search per run. The briefing inputs are unchanged except for the added past cases. Late cases are recorded in `case_found_after_briefing`. |
| **E** | `services/hypothesis_tier.py` (new), `frontend/dashboard.py` | The new **"Hypotheses to investigate"** section shows up to 3 qualifying near misses expanded: sourced quotes, reasoning step, assumptions, open points, "would be wrong if" and "watch for". It is labelled "Not insights" and nothing contradicted is shown. Your ratings set the order. On the last run it shows EV2, EV1 and EV3. |

**Tests:** 26 new tests pass. Frozen copies of the project before and after the edits (the pre-edit originals come from `event_intelligence_research_ner_update.zip`, whose lab file matches GPT's manifest hash) give the **same 59 failures** on the full suite. GPT independently re-ran the 16 tests for the funnel and A, C, D, and they passed.

**Known limits:**
- Claim-gate near misses are not yet in the new section.
- C's test proves the instruction is present, not that the model follows it.
- Old saved reports still show unresolved "F7" bases.

## 9. Status and next steps

1. **GPT review of B and E** (`REVIEW_fixes_B_E.md`). Pending.
2. **Look at the new section for free:** open the latest saved report in the dashboard.
3. **Rate items** in saved reports (useful / meh / poor, with a one-line reason). This builds the acceptance set.
4. **Budget before any paid run:**
   - a billing cap or alert in Google Cloud;
   - at most 3 fresh (non-replay) runs on different topics;
   - check and lower the per-run call ceiling;
   - move more stages to flash-lite.
5. **Rate those runs** against the definition of done, then decide whether to continue, simplify (closer to the early-October setup, with fewer calls and honest labels) or stop.
6. **Commits:** first commit the existing work as a baseline, excluding logs, `.pyc` files, databases and zips. Then commit each fix separately. Don't stage whole files that mix unrelated work.

## 10. Honest expectations

- The model's raw ideas are decent. The main problems have been grading, display and frozen input, not the model's ability to think.
- No product reliably produces *verified* original strategic insight. A realistic target is labelled hypotheses that are useful most of the time.
- The 1–2 week estimate is an estimate. Only your ratings on fresh runs will show whether quality is good enough.
