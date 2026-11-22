# v10: reader standard for insights (15 Nov 2026)

## Why

Of about 90 saved insights, the owner kept one: the 22 Oct breach-logs insight. What made it good
was a concrete fact (a three-month delay), a plain mechanism (the developer, not the government,
detected the breach) and a consequence for named parties. The app wrote it as a theory label
("asymmetric telemetry bottleneck ... exclusive diagnostic control"), and the claim check rejected
it because the label went beyond what the sources said.

What the saved runs showed:
- Only 5 of the 90 were ever released, and those 5 were restatements of the sources.
- Since October the claim check logged 90 decisions and passed 13.
- In the 14 Nov run, the main insight path and the briefing used the whole 260-call cap. The
  hypothesis lab, which generates about 20 candidates and ranks them, was skipped.

## What changed

1. **The owner's standard is now part of every generation prompt** (`services/reader_standard.py`).
   It is the first instruction in the main generator and in the lab's generation and evolution
   steps:
   - the insight leads with a concrete consequence, then a plain mechanism, then a "Watch for" or
     "Forecast:" line;
   - no theory labels, no overstatement, no stitching together unconnected events;
   - her verdicts on five earlier insights, as good and bad examples with the reason for each.
2. **A model-free check before any grounding call.** A candidate whose own text has no concrete,
   checkable fact (a date, delay, number, amount or named document) cannot be a hidden insight.
   - On the five rated insights, this separates the kept and "okay" ones from the weak and
     rejected ones.
   - On all 90 saved insights it rejects 48.
   - In the lab, plain anchored theses are checked first, and theses with a theory label in their
     first sentence are checked last.
3. **Calls held for the hypothesis lab.** In a deep run with a call cap, 60 calls (at most 30% of
   the cap) are kept for the lab, and other stages are refused once only those calls remain.
   Whatever the lab does not use is released for later stages
   (`EVENT_INTELLIGENCE_LAB_RESERVED_CALLS` changes the number).
4. **Leverage stays required** (the owner's product bar since 10 Oct), but it goes in the leverage
   fields. The insight text says it in plain words after the consequence.

## Not changed

- The claim check's rules for facts and conclusions.
- The judges, the report layout and the dashboard.

## How to check it worked, after one normal deep run (not a replay)

- **Run log:** look for `lab_reserved_calls=60` on the call-cap line, and a lab status other than
  `time_budget`.
- **Report, insight funnel:** rejections with reason `reader_standard_no_concrete_fact` show
  candidates that had no concrete fact.
- **Lab funnel:** `reader_standard_no_concrete_fact` counts theses dropped before grounding.
- **Insight text:** each released insight and lab thesis should open with a dated or numbered
  consequence, not a label.

## Tests

`tests/test_reader_standard.py` (9 tests) covers the verdicts, the anchor rules, the prompts, the
lab ordering and the call reservation. Three older lab test fixtures were given a date so that they
still test what they were written for. The full suite has the same 68 failures and collection
errors as before the change (missing spaCy, Streamlit and similar packages), with none added.

# v10.1: more reports released, less overreach (15 Nov 2026, evening)

## Why reports came out with nothing released

- **14 Nov:** the whole report was blocked by the release policy (`RED_TEAM_FAILED`,
  `RED-TEAM-REQUIRED`).
  - The 260-call cap was used up before the final checks ran, so the red team could not review the
    report.
  - The red-team agent catches each batch's error itself, so the existing at-the-cap fallback never
    ran.
- **10 Oct to 14 Nov:** 133 premises failed the claim check across 55 attempts. 122 of them were
  lines the app wrote itself from the leverage fields (70 incentive lines, 48 leverage lines, 4
  "X controls or holds Y" lines), not facts from the sources.
- **On 14 Nov,** three of four candidates failed on one such line alone, for example "Donald Trump
  controls or holds a voluntary accord", with 92-93% of their other claims backed.
- **Of the 29 failed insights rated "good",** 25 contained an overstating word ("exclusive",
  "forces", "structural") that the sources did not use, or a number they did not contain.

## What changed

1. **Calls held for the final checks.** Report grounding and the red team now have 40 calls (at
   most 20% of the cap) that no other stage can use.
   - If the red team still fails after calls were refused, the existing no-call review is used.
   - Change the number with `EVENT_INTELLIGENCE_FINAL_CHECK_RESERVED_CALLS`.
2. **Unbacked leverage lines no longer sink a backed insight.** When the only failed claims are the
   app's own leverage lines, the claims are checked again without them.
   - If that passes, the insight is shown without leverage, with the note "The sources did not back
     who controls what, so the leverage is not shown."
   - A failed source premise or conclusion still blocks.
3. **An overreach check before any claim check, with no model call.**
   - It finds overstating words the cited records do not use, and numbers they do not contain (for
     example "Executive Order 14434" or "a 12-to-72-hour rule").
   - The candidate goes back for repair with exactly those words named. Models fix a located error
     far more reliably than they find one (Tyen et al. 2024).
   - Deadline years in forecasts ("before 2027") are allowed.
   - Lab theses with overreach are checked last.
4. **Scope, added-detail and motive rules in the reader standard:**
   - insights stay about the one company, government or incident the records describe;
   - no number, order number, deadline or rule that no record states;
   - no motive unless a record states it.

## Replaying 14 Nov through the new rules (the claim verdicts are saved; nothing was re-run)

- The red team gets its calls, so the report is no longer blocked.
- Of the four candidates that failed only on leverage lines:
  - two are stopped by the concrete-fact check, one of which is the liability insight the owner
    rejected;
  - two would pass with leverage withheld. One of these first goes back for repair because the
    overreach check flags "74", but it would pass that check, since the 74% figure is in the
    sources.
- **This raises the release rate; it does not by itself make insights good.** One of the newly
  passing candidates links the voluntary accord to a startup's $1 billion raise, a causal link no
  source makes.

## Tests

- `tests/test_reader_standard.py` now has 17 tests.
- One older call-cap test sets the final-check reserve to 0 so it keeps testing what it was written
  for.
- The full suite has the same 68 failures and collection errors as before these changes, with none
  added.

# v10.2: predictions are judged as inferences, not as quoted facts (15 Nov 2026, night)

## The bugs

Every displayed sentence, every lab thesis and every actor move goes to the same source checker
(`services/grounding_service.py`). Its instructions had no rule for predictions, so a sentence
such as "Australia will set a fixed reporting deadline" was judged like a factual claim: "the
evidence does not state this", so unsupported. In practice this meant:

- **Display check:** after an insight passed the claim check, a forward-looking sentence shown
  with it was deleted unless the checker called it backed (14 Nov: four sentences removed,
  including one about the voluntary accord and training pauses).
- **Hypothesis lab:** a thesis is verified only when the whole thesis passes, so a thesis ending in
  a forecast could not be verified. The v10 prompt asks for that forecast sentence, so this would
  have got worse.
- **Actor moves:** a predicted move had to be backed or follow from the facts; the checker
  treated "not stated" as "not backed".

A second bug of the same kind: when a sentence was marked as needing two records and the checker
cited one, its verdict was forced down to "partly supported". That made a single-source lab thesis
unverified and deleted single-source display sentences, though the claim gate (v4.0) says a
single-source judgment is labelled, not blocked.

## The fixes

1. **A forecast rule in the checker.** A prediction is judged on its premises and its reasoning:
   - it passes as "follows from the sources" when the sources back its premises and the expectation
     follows from them;
   - "partly supported" when a premise is missing or it is stated too strongly;
   - "unsupported" when its premises are not in the sources or it does not follow;
   - "contradicted" when a source shows it will not happen.
   "Not stated by the evidence" is no longer a reason on its own. The prompt version is now
   `grounding-v1.4-forecasts-judged-on-premises`.
2. **Only facts are downgraded for resting on one record.** Conclusions, mechanisms, connections
   and forecasts keep their verdict and are labelled single-source.

## Checked and found working as intended

- **The claim check (gate v4.0):** conclusions, mechanisms, incentives and forecasts are blocked
  only when a source contradicts them.
- **The two-source rule for conclusions:** it exists only in the legacy gate mode, which is not
  the default.
- **The forecast-already-answered check:** it rejects a forecast only when the sources already
  report its outcome.

## Still open

These affect quality rather than release:
- Exploration builds candidates from pairs and triples of different developments, which
  encourages linking unconnected news.
- A deep run makes only one candidate on the main path.
- The display check removes everything when it cannot run, for example at the call limit.

## Tests

- 2 new tests in `tests/test_reader_standard.py` (19 in total).
- Three older tests that pin the checker's prompt version now expect the new version.
- The full suite has the same 68 failures and collection errors as before, with none added.

# v10.3: no stitching, more candidates, honest labels at the call limit, sharper lab (15 Nov 2026)

1. **No stitching of unconnected news** (`_development_combinations`). Developments are combined
   into one candidate only when they share a named actor (generic words such as "government" or
   "companies" do not count). On the 14 Nov data the voluntary accord and the Simons Foundation
   grant are no longer paired; each is read on its own. When nothing is linked, the top two
   developments are each read in depth instead.
2. **Two candidates per deep run on the main path** (it was one). When the model returns two, each
   must rest on a different concrete fact and make a different point.
3. **The display check at the call limit labels instead of deleting.** When the check on the
   material shown with an insight is refused by the call limit, the material is kept with the note
   "not checked against the sources because the run reached its call limit; the insight's own
   claims were checked". Any other failure still removes unchecked material.
4. **Three new steps in the hypothesis lab:**
   - **Concrete-facts step:** one cheap call before generation lists up to 8 concrete facts a
     reader would find surprising: date gaps, numbers, reversals, missing parties, clauses. Every
     generation batch starts from them. Findings without a concrete fact, or citing an unknown
     fact, are dropped.
   - **Inference-strength grade:** each evolved thesis is graded "stated", "follows", "plausible"
     or "speculative". "Stated" (one fact already says it) and "speculative" (more than one
     unstated assumption, or a motive no fact gives) are dropped. "Plausible" ranks lower.
   - **Competing-explanation test:** when the rival explanation fits the facts as well, the thesis
     is labelled "possible - another explanation fits the facts as well" and ranks lower.
   - **Stitching check:** a thesis citing facts from different developments must name the fact
     that connects them, or it is dropped.
   - The dashboard shows each lab thesis's inference strength.

**Funnel counters (lab):** `concrete_facts_dropped`, `evolution_restates_a_fact`,
`evolution_speculative`, `evolution_stitched_without_link`.

**Tests:**
- 6 new tests in `tests/test_reader_standard.py` (25 in total).
- The older combination tests' sample events now name a shared actor, so they still test what they
  were written for.
- The narrowing test's stub skips the new concrete-facts call.
- The full suite has the same 68 failures and collection errors as before, with none added.

# v11: better inputs, and a free open-model judge aligned to your ratings (15 Nov 2026)

These are the four remaining recommendations from the insight-quality research report.

## 1. Better inputs (`services/hypothesis_evidence_service.py`, gap research)

- **The original document is now read.** A new search type, "primary", looks for the document the
  development is about: the bill, rule or accord text, or the company's or agency's own notice or
  filing. It is searched first. Before, the plan was told never to search for the development
  itself, so its own document was never read.
- **Questions first.** The plan now writes up to three sharp questions whose answers would most
  change what the development means. Each search answers one of them. The questions are saved
  with the research record (`questions`).
- **Not skipped when time is short.** When a run had too little time left, gap research was
  skipped entirely; it ran in 22 of the last 40 saved runs. Now a small pass runs instead: the
  primary document and a past case, at most four pages, top development only (warning
  `gap_research_light_time_budget`). It is skipped only when less than half of the analysis time
  reserve is left.
- The limits are now 7 searches and 14 pages per development (they were 6 and 12).

## 2. A judge on a free open model, not Gemini (`services/insight_judge.py`)

- Gemini writes the insights, so a Gemini judge would grade its own family's work. The judge uses:
  - **Groq's free tier** (set `GROQ_API_KEY`), model `INSIGHT_JUDGE_MODEL`, default
    `openai/gpt-oss-120b` (open weights); or
  - **a local Ollama model** (set `INSIGHT_JUDGE_PROVIDER=ollama`), model `OLLAMA_JUDGE_MODEL`,
    default `qwen2.5:14b`, which is completely free.
- A Gemini model name is refused. `INSIGHT_JUDGE_PROVIDER=off` turns the judge off.
- It gives a pass or fail against your standard, with one plain reason.
- It does not use the Gemini call limit.

## 3. Ratings, agreement and automatic prompt tuning (`judge_tools.py`)

- **Ratings file:** `data/insight_review_90.csv`. Put "pass" or "fail" in the `your_rating`
  column; a `your_note` column is added for one optional line. Your six verdicts are already
  filled in: rows 54, 41 and 42 pass; rows 10, 84 and 86 fail. At least 20 rated rows are needed;
  40 is better.
- **Commands:**
  - `python judge_tools.py status` shows how many rows are rated and whether the judge is aligned.
  - `python judge_tools.py measure` measures the judge's agreement with you on the held-out half,
    and lists the cases where it disagreed.
  - `python judge_tools.py tune` is the automatic tuning. The model rewrites the judge's
    instructions from the cases it got wrong, and each rewrite is scored on the tuning half. The
    best rewrite is kept only if it does no worse on the held-out half, which the tuning never
    sees.
- **Aligned** means: at least 10 held-out rows, accuracy at least 80%, Cohen's kappa (agreement
  beyond chance) at least 0.6, and the same model that was measured. The result is saved in
  `data/evaluation/judge_state.json`.

## 4. Head-to-head ranking (tournament)

- When the judge is aligned, it compares up to five lab ideas, and up to five released insights,
  in every pair. Each pair is asked in both orders, and a winner counts only when both orders agree.
- Items are then ordered by wins. The report records this under
  `analysis_metadata.judge_ranking`, and each insight records its score.
- Until the judge is aligned, it is not used and the order is unchanged.
- The judge never blocks or removes an insight, so it cannot make reports less frequent.

## Tests

- 9 new tests in `tests/test_v11_judge_and_inputs.py`. They use stub models, so no API key is
  needed.
- Three older tests that pinned the search count (6) or the list of search types now expect 7
  searches and the "primary" type.
- The full suite has the same 68 failures and collection errors as before, with none added.

# v12: computed gaps, rule-based stitching, fact brief, base-rate forecasts (16 Nov 2026)

These are from the research report "Improving Insights, Leverage and Forecasts in AI Event
Intelligence". Only the changes that fit the existing code are made; some of what the report
recommends was already in place (noted below).

## 1. Computed gaps (`services/delta_extractor.py`, new; no model calls)
- Code finds the surprises a reader can check, before any model is called:
  - date gaps inside one development, e.g. "in June" to "10 September": about 3 months;
  - a date mentioned in a record against the date the record was published (the breach insight
    you approved was exactly this);
  - two amounts of the same kind that differ by at least two times.
- These go to the main generator ("COMPUTED GAPS ... prefer an insight that explains one of
  them"), to the lab's concrete-facts step and to every lab generation round. Each gap names the
  facts or evidence IDs it comes from. The gap is a fact; why it happened must be written as
  inference.

## 2. Stitching decided by code (`strategic_hypothesis_lab.py`)
- A lab thesis that joins different developments is kept only when the developments share a
  named actor, or when the fact it cites as the link names an actor of each development.
- The model's own link claim is no longer trusted on its own. Developments' actors now travel
  with the lab's facts (`editorial_briefing.select_facts`).

## 3. Fact brief instead of nothing (tiered release)
- Every report now carries `computed_gaps`, with the evidence IDs behind each gap.
- When no insight is released, the dashboard shows them under "Fact brief: gaps the sources
  show", so the run still gives its strongest checkable finding.

## 4. Leverage as concrete positions (generator instructions)
- The claim check already treats leverage the way the research recommends: control is a fact,
  dependency must follow from the facts, and incentives are labelled assumptions.
- New in the instructions: "controls" must be a position a source documents. That is, who can
  block access (a licence, export control, API term or clause), who knows first (which party
  detected or received the information, and when), or who sets the rule.
- The dependent must come with how few alternatives it has: the other options the sources name,
  or "the sources name none". The leverage fields are unchanged, so every check still works.

## 5. Forecasts get a starting probability from the base rate (`services/forecast_store.py`)
- The forecast format already had a yes/no question, a deadline, resolution criteria, a base
  rate and ICD 203 likelihood words. But the model usually left the probability empty, so only 3
  forecasts were scorable in the ledger.
- Now, when the model gives the precedent counts with their sources but no number, the code
  computes (outcome cases + 1) / (comparable cases + 2). For example, 2 of 3 gives 60%. The basis
  is stated, so the forecast can be scored with a Brier score when it resolves.
- Not done:
  - several model samples per forecast: each sample is another model call against the call
    limit;
  - automatic resolution: the ledger deliberately allows no model-decided outcomes. Watch items
    are still followed up automatically, as before.

## 6. Red team: three targeted questions (`agents/red_team_agent.py`, v4.1)
- Is the lead fact really stated by a cited source?
- Do the premises come from unconnected developments?
- If the weakest step were false, would the conclusion still hold?

## 7. Judge (`services/insight_judge.py`, `judge_tools.py`)
- Your six verdicts are built into the code, matched by row number and date. They are used only
  where your ratings file has no rating of its own, so the ratings file never has to be replaced.
- Aligned now also needs at least 3 rated pass and 3 rated fail in the held-out half, and
  balanced accuracy of at least 80%. Balanced accuracy is the average of how often the judge is
  right on passes and on fails, so a judge that fails everything cannot look good.
- `python judge_tools.py suggest` lists the unrated insights most worth rating next, likely passes
  first, to grow the few positive examples.

## Already present, not changed
- Claims are checked against all of their cited evidence together, not sentence by sentence.
- Partly supported lab ideas are narrowed and checked again.
- Pairwise comparisons are asked in both orders.
- ICD 203 likelihood words are shown on the dashboard.
- The forecast ledger computes Brier scores.

## Tests
- 10 new tests in `tests/test_v12_gaps_leverage_forecasts.py`.
- Two earlier tests were updated for the stricter rules: the linked-developments example now
  names a real link, and the alignment example has pass and fail counts.
- The prompt version is `strategic-inference-v7.11.0-computed-gaps-reader-standard`.
- The full suite has the same 68 failures and collection errors as before, with none added.

# v12.1: fixes found by a simulated deep run (18 Nov 2026)

## How the run was done
- A full deep run of this code on the 106 saved October articles (157 events, 458 evidence
  records), with every model call answered by Claude through the app's own file proxy (the
  gemini_llm_service proxy mode).
- Not covered: news download and gap research, which need the internet and Gemini's Google search.
- Outcome: abstained, with no insight released. The generator was given 24 of the 458 records,
  mostly unrelated funding rounds. The lab's 4 ideas scored 5-6 out of 10 against its 7 out of 10
  bar.

## Fixes
1. **Website navigation text is no longer an event.** Examples: "Read more here about its
   fundraise ...", "READ MORE | MY STORY | ...". One such line was a top development. There were
   8 among the 1,620 saved events, with no false matches (`_is_page_boilerplate`, at event
   admission).
2. **Computed gaps reached the generator empty.** They were computed from the raw record's short
   excerpt instead of the wording the model sees. They are now computed from the digest wording,
   grouped by incident.
3. **A computed-gap route past the two-record pair rule.** In deep mode, every insight had to sit
   on a pre-built pair of records from different actors and sources
   (`deep_candidate_requires_coherent_support_cluster`), so a single-incident finding like the
   breach delay could never be released. An insight that cites every record of a computed gap,
   plus at least one more record, now counts as linked (reason `computed_gap:<kind>`). It may rest
   on a single source.
4. **No amount gaps from headline roundups.** Amounts are compared only within one sentence that
   holds exactly two of them.
5. **The allowed event IDs no longer cut off events the shown evidence cites.** That list was
   capped at 60 entries.
6. **Leverage claims are no longer built from sentences.** When the model writes "the sources name
   no party ..." in the dependent field, or "no record states ..." as an incentive, no garbled
   claim is created to fail the check. The dependent field is a name only, with alternatives in
   their own field.
7. **Actor moves:** texts are shortened at word or sentence boundaries, not mid-word, and a move
   and its reason are joined as one sentence.

## Tests
- `tests/test_v12_1_simulation_fixes.py` (4 tests).
- The full suite has the same 68 failures and collection errors as before, with none added.

# v13: stories chosen by insight potential, deep per-story evidence, several insights, quality gates (18-19 Nov 2026)

## Why runs kept producing nothing, although the "search more around a thin story" fix was in place
- **The follow-up searches went to the wrong stories.** Stories were ranked by how much coverage
  they had, so widely copied funding rounds came first. On 14 Nov the searches went to two funding
  rounds and a foundation grant.
- **What the searches found was cut away again.** The generator's evidence took at most 2 records
  per story across about 10 unrelated stories, and only events behind a story's few chosen facts
  counted as part of it.
- **The per-story analysis never ran when the main generator returned nothing.** That is the case
  it was built for: the run abstained before reaching it.
- **A deep run could release at most 1 insight.** This was a temporary "MVP" setting, never raised.
  Exploration also stopped at the first release.
- **The lab's prompt still told the model to discard its own ideas scored below 7 out of 10,** a
  month after the code stopped filtering by that score. A simulated run's lab returned nothing.
- **Evidence records carried no publication date,** so the breach delay ("happened in June ...
  notified earlier this month") could not be computed.

## Changes
1. **Insight potential** (`services/insight_potential.py`, no model calls). A story scores for an
   official actor, incident or enforcement language, a computable gap and change against a
   baseline. It loses points when it is funding-only or launch-only (at least three quarters of its
   sentences). This score now ranks the developments, the follow-up search targets and the
   per-story exploration. A single event with an official actor and incident language qualifies for
   follow-up searches. On 10 Sep - 25 Oct news:
   - the top stories went from Birmingham Airport, an FBI keynote and a funding round;
   - to EU AI Act fines, the Australian breach and the FTC probe.
2. **Depth per story.** The top 3 developments get up to 8 records each (different sources first),
   up to two thirds of a 36-record packet. Every event of a development carries its rank.
   Near-duplicate sentences (syndicated copies) are merged before the packet is filled.
3. **Per-story exploration** groups events by verified clusters only, not by theme buckets such as
   "ai_general". It ranks stories by insight potential and keeps up to 2 candidates per story. It
   now runs when the main generator returns nothing, and continues until the run's limit.
4. **Up to 4 insights per deep run** (`EVENT_INTELLIGENCE_DEEP_MAX_INSIGHTS`, 1-8; was 1). Up to 5
   stories are explored (was 3). Every insight still passes every check.
5. **The lab** returns every idea that is neither a restatement nor speculation, with honest scores,
   and the code ranks them. The prompt no longer contradicts itself ("cover every lens" against
   "do not pad").
6. **Dates.**
   - Evidence records carry `source_published_at`.
   - The gap calculator reads timestamps such as "2026-09-24T11:52:32".
   - It reads relative dates: "earlier this month", "last month".

   The breach record now yields "'in June' was reported in a record published 2026-09-24: about 3
   months later".
7. **Earlier coverage** is cut at a sentence end, not mid-sentence.
8. **The topic box** defaults to "AI governance and regulation".
9. **Six governance and policy feeds** are added to `config/news_feeds.txt`. A feed that fails is
   skipped.

## Stopping old problems from coming back
- **`tests/test_v13_quality_regression.py`** runs on the saved news in `data/event_intelligence.db`,
  with no model calls. Each test pins an owner decision as an outcome:
  - the breach story is chosen for deep analysis (canary);
  - the top stories are not funding rounds;
  - exploration starts with high-potential stories;
  - the top story reaches the generator with at least 6 different records and no repeats;
  - several insights are allowed and exploration continues;
  - the lab prompt and code agree;
  - the breach delay is computed from the saved article.
- The canary was checked against the old ranking: it fails there and passes now.
- **`services/run_quality.py`** computes quality gates and a funnel for every run and saves them
  in the report's analysis metadata. Gates: several insights, no funding-only top stories, and
  exploration ran when nothing was released. A failed gate is logged as a warning.
  `python -m tools.run_quality` prints them for the latest report and exits 1 when a gate failed.

## Tests
- 8 new tests in `tests/test_v13_quality_regression.py`.
- Updated tests:
  - the old exploration count (3);
  - "stops at the first release";
  - the sample clusters now marked verified.
- Full suite: the same 68 failures and collection errors as before, none added.

## v13 additions (19 Nov 2026): bugs found as the simulated run reached the final checks
These are why the breach-delay insight failed after it was generated.
1. **The quality check could not see the generator's citations.** The generator stores them as
   `supporting_evidence_ids`, but the check (`insight_quality_service`) read only `evidence_ids`.
   An insight citing 10 records was marked "No supporting evidence records are attached" and sent
   to repair.
2. **A "Watch for" line turned a finding into a forecast.** The writing standard puts a signpost
   line on every insight, so the breach delay was labelled "prospective". It was then blocked for
   lacking feasibility research (`final_quality_prospective_feasibility_1_of_4`). Now an insight is
   treated as a forecast only when it predicts something outside its "Watch for" lines.
3. **The overreach check flagged dates as invented numbers.** "24 September" (from the article's
   publication date) and "September 24, 2026" were flagged. Days and years of written dates are now
   exempt.
4. **Version labels were listed as event IDs.** "EVENT-DATES-V1" was among the allowed event IDs.
   Only real event IDs are listed now.
5. **A number record's unit was repeated** ("fine percentage 3% %"). Number records now also show
   their source sentence.
6. **"Since August 2025" was treated as a delay.** Dates after since/until/by/from describe a period,
   not a late report, and are no longer computed as delays.

Simulated run state: the main generator wrote the breach-delay insight from the computed gap, and it
now passes the final quality check (it failed before because of 1-3). The run was stopped by a usage
limit before the release decision, so the number of released insights is not yet measured.

**Tests:** 11 outcome tests in `tests/test_v13_quality_regression.py`. Full suite: the same 68
failures and collection errors as before, none added.

# v13.1: any topic (19 Nov 2026)
The app serves any topic, not only AI governance.
- **`insight_potential.score(..., topic=...)`.** The funding-only and launch-only penalties apply only
  when the topic is not itself about funding or launches. For "startup funding in India", funding
  rounds are the story. The other signals are topic-neutral: official actors, incidents and
  enforcement, change and delays, computed gaps. The topic is passed to story ranking, follow-up
  search targets, exploration and the quality gates.
- **The topic box is no longer prefilled.** Its help text explains what makes a topic yield insights:
  a field with several active companies or regulators, in a few words. Too broad fills the run with
  unrelated funding and product news; too narrow leaves too few events to compare.
- **`CLAUDE.md`** (new, project root) gives the instructions that Claude Code reads at the start of
  every session: the objective, a definition of done that requires the outcome tests and
  `tools.run_quality` output, and the rules past bugs broke.
- **Tests:** 1 new test (12 in `tests/test_v13_quality_regression.py`). Full suite: no new failures.

# v13.2: cluster searches built for insights, for any topic (19 Nov 2026)
Clusters group a topic's news into stories, and the strongest clusters get targeted searches. That
is what lets a broad topic work. The searches now look for what hidden insights and next moves are
built from, written about each cluster's own subject, never the broad topic:
- **Round 1:** the original document or official statement (filing, notice, ruling, bill, contract,
  announcement); the dates (when it happened, when it was disclosed, deadlines); the official, legal
  or regulatory response.
- **Round 2:** other companies, governments or countries facing the same thing; the closest earlier
  case (precedent) and how it turned out; criticism, delays, reversals or disputed numbers.
- **Later rounds:** what the main actor said it will do next, or a deadline or vote still to come;
  the numbers; the newest reports.

Before, the angles were generic business angles ("customers", "competitors", "newest reports"),
which mostly returned more coverage of the same news. The backup template (used when no model is
available to write queries) follows the same angles. The prompt version is
`cluster-queries-v2-insight-angles`.

**Tests:** 1 new test (13 in `tests/test_v13_quality_regression.py`). Full suite: no new failures.

# v13.6: several distinct stories explored, copies of one story merged, honest quality gates (20 Nov 2026)
Found by reviewing the code and a simulated deep run on the saved corpus (10 Sep - 25 Oct 2026).
- **Every top story gets its own reading.** Exploration read at most two stories, so its limit of 5
  never came into play and a run could not reach several insights. Now each top-ranked story (by
  insight potential) gets its own single-story reading until the limit is reached
  (`_development_combinations`).
- **Copies of one story are merged** (new `services/same_story.py`). One story reported by several
  outlets arrived as several "stories" (the OpenAI / Australia breach as about ten), so exploration
  spent its slots on the same story. Two events are the same story when they share rare words
  (weighted by how rare each word is in the run), at least four words and at least two names; outlet
  names at the end of titles ("- SiliconANGLE") and bylines ("by Maria Deutscher") are ignored. A
  size guard (25 events, or 4% of the run) stops chaining. On the saved corpus the breach becomes one
  10-event story with no unrelated item. A first attempt that merged on one or two shared capitalised
  words was rejected: it built a "China" story mixing BRICS, a coal deal and a funding round.
  Known limits: a lone sentence with one name ("Despite the breach happening in June, Albanese
  said...") stays separate and can take one exploration slot; interview write-ups from one outlet
  that share its hosts' names are grouped together (low insight value).
- **Second route for single-story readings.** When no context record (rule, deadline, precedent)
  applies, the model derives the insight from the story's own records: a computed gap listed in the
  prompt, or two records that do not reconcile. In the simulation, three stories returned nothing
  because only the context-record route was offered.
- **No second opinion after an empty refinement call.** A refinement-stage call (exploration, repair
  or alternative candidate) that returns no candidate is not asked again; it doubled the calls for
  every story with nothing to say. Rejected candidates are still repaired.
- **Quality gates computed after the release decision.** The run-quality gates were computed before
  the release decision, so a run whose insights were then withheld still reported them as passed.
  They are recomputed in `_persist_report` with the withheld insights removed
  (`quality_gates.computed_after_release_decision = true`).

**Tests:** 15 outcome tests in `tests/test_v13_quality_regression.py` (2 new: several distinct
stories with one coherent breach story; outlet names and bylines do not merge unrelated funding
rounds, and no story exceeds the size guard). Full suite: the same 67 failures and collection errors
as GitHub 113edb6 (missing packages in the test machine), none added.

# v13.6.1: no story lost when copies of one story are merged (20 Nov 2026)
Review fix to v13.6. A group could belong to two stories (one shared with a larger group, one with a
smaller group). The smaller group was copied into the middle group after the middle group had
already been merged into the larger one, so its events were lost from exploration. Groups are now
joined transitively (union-find over group keys) and each merges straight into its final group.
**Tests:** 1 new test (16 in `tests/test_v13_quality_regression.py`); it fails on v13.6 and passes now.
Full suite: no new failures.

# v13.7: call budget and run time that allow several insights (20 Nov 2026)
Found by checking the 14 Nov run's call counts (data/run_costs.jsonl) against the budget code.
- **Optional work stops when its calls would be refused, not at 85% of the cap.** The ceiling
  refuses an ordinary stage once only the calls held for the hypothesis lab and the final checks
  remain (cap 260: after 160 calls), but the insight loop believed it could start more work until
  221 calls; every exploration story started in between failed on its first call. A stage with its
  own reserve (the briefing) was, in turn, stopped at 85% although its calls were held for it.
  `_call_cap_exhausted` now asks `gemini_llm_service.calls_available()` - the calls THIS stage can
  still make - and does not start optional work with fewer than 15
  (`EVENT_INTELLIGENCE_MIN_CALLS_OPTIONAL_WORK`).
- **Calls held for the stages after the insights** (focus-company research, briefing, actor moves,
  judge): 70 (`EVENT_INTELLIGENCE_AFTER_INSIGHTS_RESERVED_CALLS`, at most 20% of the cap), only
  when the cap is at least 480. Below 480 the insights keep priority, as before. The leftover goes
  back to the pool once those stages are done.
- **Deep run time limit 2400 s -> 4800 s** (`EVENT_INTELLIGENCE_DEEP_MAX_RUNTIME_SECONDS` still
  overrides it). The 14 Nov run, with one insight, already stopped actor moves and focus research on
  "time_budget"; checking each extra story's insight takes about 3-4 minutes.
- **Recommended cap for a deep run: 480 calls** (`EVENT_INTELLIGENCE_MAX_GEMINI_CALLS=480`). Basis,
  14 Nov run: pipeline 74, insight checks about 18 calls per candidate (92 for 5 candidates),
  precedent research 37, focus research 12, briefing 45, plus the lab (60) and final-check (40)
  reserves. Several insights need about 8-10 candidates checked. A lower cap still works; the run
  logs a warning and explores fewer stories.

**Tests:** 1 new test (17 in `tests/test_v13_quality_regression.py`). Full suite: no new failures.

# v13.8: a lone sentence about a story joins that story (20 Nov 2026)
- **`same_story._lone_reports_to_attach`.** A one-sentence report ("Despite the breach happening
  in June, Albanese said the tech giant only notified the government...") was too short to pass
  the same-story pair test, so it stayed a story of its own and took one of the five exploration
  slots (about 15 calls) for a story already being read. A lone report now joins a story of at
  least three events when: it names one of that story's core names (in at least half of the
  story's events, and with at least 80% of the name's mentions in the run inside that story - so
  "Albanese" counts, "Microsoft" or "American" do not); exactly one story qualifies; and at least
  25% of its weighted words occur in the story, including two uncommon non-name words ("breach",
  "incident"). On the saved corpus this joins exactly two breach reports and nothing else; the
  closest rejected cases scored at most 0.22. The breach is now one 12-event story and takes one
  exploration slot.

**Tests:** 1 new test (18 in `tests/test_v13_quality_regression.py`); it fails on v13.7 and passes
now. Full suite: no new failures.

# v13.8.1: focus research sees the calls held for it; call-budget tests (20 Nov 2026)
Review fix to v13.8. The early focus-company research decides whether to run from the calls it can
still make, but it ran outside its own path, so the calls held for it (the after-insights reserve)
counted as held for others and it was skipped although its calls were reserved. It now runs as
`focus_research` (`STAGE_PATHS`). **Tests:** 3 new (21 in `tests/test_v13_quality_regression.py`):
the whole budget at 480 calls (main path 310, optional work stops below 15, then the briefing 70,
lab 60 and final checks 40 all get their calls); focus research decides inside its own path (fails
on v13.8); no after-insights reserve below 480 (main path 160 at 260). Full suite: no new failures.

# v13.9: follow-up searches go to distinct stories, with insight angles in every round (20 Nov 2026)
- **One follow-up search slot per story** (`_distinct_story_targets`). The three clusters that get
  follow-up searches were the top three by insight potential, but copies of one story can sit in
  different clusters (each a single event). On the saved corpus the top eligible clusters held the
  FTC story twice and the breach twice. Each story now gets at most one slot (same-story test:
  `services/same_story.py`), so the slots reach three different stories.
- **Backup query template, rounds 3 and later.** When the model cannot write the queries, rounds 3+
  searched generic business angles ("launched", "partnership", "investment", "expands"), which
  return more product and funding coverage. They now follow the same angles as the model-written
  queries: the next step or deadline, the figures and timeline, the newest reports and responses.
- **"Disclosed" in a funding announcement is not an incident.** "Arceus Legal today disclosed that
  it has raised $17M" counted as incident language, so the story escaped the funding-only penalty.
  "Disclosed" next to funding words or "not"/"terms" no longer counts; a breach disclosure still does.

**Tests:** 3 new tests (24 in `tests/test_v13_quality_regression.py`); each fails on c71c735 and
passes now. Full suite: no new failures.

# v13.11: follow-up searches keep what they find (20 Nov 2026)
Found in the log of RUN-FF8F59D21FDD4ACDB17E5B8F (20 Nov, topic "artificial intelligence"). The
three stories chosen for follow-up searches were the right kind (a chatbot giving voters a wrong
deadline, Meta and Google losing control of agents, an assistant launch), and the searches found
24 on-topic events across 6 rounds - but only 1 was kept, and the searches stopped after 2 rounds
per story.
- **Why:** a new event counted only when the clustering, re-run on the grown corpus each round, put
  it in the target story's cluster, or when both carried the same mechanism or dependency tag. The
  three target stories were single-event clusters whose ids do not survive re-clustering, and most
  events carry no tags. Every other event was discarded - not added to the corpus at all.
- **Fix 1: follow-ups recognised by names.** A new event also belongs to the target story when it
  shares two distinctive names with it (or the target's actor and one distinctive name). A name is
  distinctive when it appears in at most 3 events or 10% of the corpus, so "OpenAI" or "Google" in
  an AI run do not tie unrelated stories together.
- **Fix 2: other on-topic finds are kept as context.** An event that passed the run's topic
  admission but is not part of the target story (a comparable case, a regulator's response
  elsewhere) is now kept in the corpus for the analysis, logged as `new_context_event_count`. It
  does not count as growth of the target story, so the stop rule is unchanged.

Still open, from the same log (not changed here): the precedent search reviewed relevant pages
(Reuters, March 2024: Google restricted Gemini's answers to election questions) but the review
model marked none as a past case; GDELT was rate-limited for all gap searches and NewsAPI only
covers the last 30 days, so precedent searches depend on Google search.

**Tests:** 2 new tests in `tests/test_cluster_expansion.py`; both fail on 368b421 and pass now.
Full suite: no new failures.

# v13.10 (included with v13.11): .env settings reach the run (20 Nov 2026)
Found in the log of RUN-FF8F59D21FDD4ACDB17E5B8F: no "gemini_call_cap" line, so the run had no
call ceiling although .env set EVENT_INTELLIGENCE_MAX_GEMINI_CALLS=480. The only .env loader
(agents/strategic_inference_agent.py) read the Gemini key and stopped. New services/project_env.py
loads every plain NAME=value line of the project .env at startup (the process environment wins;
values are never logged; not applied while pytest runs). The orchestrator calls it before its module
constants read the environment. **Tests:** tests/test_v13_10_project_env.py (2). Full suite: no new failures.

# v13.12: no insight shown twice; bot walls never saved as articles (20 Nov 2026)
Found in RUN-FF8F59D21FDD4ACDB17E5B8F (20 Nov) and the saved reports.
- **Insights earlier reports showed are not shown again** (owner: the breach-delay insight came back
  in about 12 runs). New services/earlier_insights.py reads the insights of earlier reports on the
  same topic (reports.sqlite3: hidden insights, lab theses, briefing implications) and the rating
  sheet (insight_review_90.csv). Word overlap does not catch these repeats: the 20 Nov candidate
  shared at most 22% of its weighted words with earlier versions of the same thesis. So the closest
  earlier insights by overlap are put to the model in ONE call that asks whether the new insight makes
  the same core claim in other words; a new fact that changes the earlier conclusion is not a repeat.
  Used (1) before any claim check in the release loop (reason `already_shown_in_earlier_report`; the
  next synthesis is told which insight it repeated), (2) on the lab's generated theses before
  evolution (funnel `repeats_of_earlier_reports`), and (3) in the lab prompt, which lists the 10 most
  recent earlier insights as "already shown". If the model fails, only near-verbatim copies are caught.
- **Bot walls are not articles.** "Federal Register :: Request Access", nature.com "Client Challenge"
  and psychologytoday "A quick browser check" were saved as articles. Their titles (checked whatever
  the page length) and wall hosts (unblock.federalregister.gov) now mark them as blocked, so the page
  is read through the Exa fallback instead.
**Tests:** tests/test_v13_12_earlier_insights.py (6) and 3 in tests/test_v13_3_exa_fallback.py.
Full suite: no new failures. Tests never read the owner's saved reports (conftest).

# v13.13: every run measured; Exa budget and logging; why a page gave no past case (20 Nov 2026)
From RUN-FF8F59D21FDD4ACDB17E5B8F (20 Nov) and the owner's request to measure each run.
- **Run metrics** (new services/run_metrics.py), computed where the report is saved, after the release
  decision; saved as `analysis_metadata.run_metrics`, logged as `run_metrics=` plus readable
  `run_metrics_summary |` lines, shown on the dashboard ("Run metrics" under the claim check) and
  printed by `python -m tools.run_quality` (`--last 5` compares runs). Groups: retrieval (sources,
  events kept by the topic check, research pages reviewed/kept, past cases, Exa pages/searches/units);
  insight funnel (checked, released, release rate, rejections by reason, repeats caught, lab theses);
  grounding (claim support rate = RAGAS faithfulness, citation coverage = ALCE, publishers per insight,
  share with a falsifier or watch signal); judge; cost (Gemini calls by stage, per released insight,
  run time). No model calls.
- **Exa per-run budget 300 -> 600 units** (about $0.60; the $5 monthly cap is unchanged). The 20 Nov run
  used 292 of 300 by mid-run. Every Exa page read, failed page and search is now logged, and a warning
  is logged once when the budget is reached.
- **Why a precedent page gave no past case.** The page review returns `case_note` (different mechanism,
  different entity, no stated year, no outcome...) and it is logged with the page in
  `gap_research_sources`. Reuters 2024 (Google restricting Gemini's election answers) was kept as a
  record but not as a past case on 20 Nov, and nothing said why.
- Not changed: skipping terms/policy pages before fetching. One of the four such pages on 20 Nov was
  relevant evidence (Google Cloud's generative-AI indemnity terms, for the "who bears the risk" question).
**Tests:** tests/test_v13_13_run_metrics.py (6). Full suite: no new failures.

# v13.14: computed-gap insights accepted, each explored story gets a repair (20 Nov 2026)
Both found in the insight stage of RUN-FF8F59D21FDD4ACDB17E5B8F (20 Nov). Exploration read four
different stories and wrote candidates; all were rejected, two of them by the code rather than the
evidence.
- **Computed-gap route in the non-obviousness check** (`services/non_obviousness.py`, version
  `non-obviousness-v5-computed-gap-route`; written as v13.11 but not part of v13.12/v13.13). The
  generator is told to build insights on computed gaps (the prompt's COMPUTED GAPS block, the v13.6
  second route), but test 1 accepted a one-story conclusion only when a context record (rule,
  deadline, precedent) was cited, so every such insight was rejected as
  `conclusion_single_development` - in this run "the public learned on 18 September that Google's
  Gemini agent breached three companies in May". A one-story conclusion now also qualifies when a
  date or amount gap is computed (`services/delta_extractor`) from its own supporting records. The
  plain-meaning, headline and expert-commentary tests still apply; the single-development ablation is
  skipped for this route (a gap computed from the story's records is supported by them by
  construction). One-story conclusions with neither a gap nor a context record are still rejected.
  (In this run the repeat check of v13.12 would also have stopped that candidate: it restates the
  breach-delay thesis shown before.)
- **One repair per explored story** (`_claim_supported_release`). The run-wide limit of extra
  attempts (`EVENT_INTELLIGENCE_DEEP_RELEASE_RETRIES`, 2) was used up by the first candidate's repair
  and alternative, so no explored candidate was ever repaired. The Forum AI election candidate - a
  new insight (procedural election questions slip past chatbots' election refusals, so models invent
  local deadlines) - failed on one premise ("Anthropic's Claude" where the source said "Claude") and
  was dropped. Each explored candidate whose failure is repairable now gets one repair of its own
  (logged as `exploration_repairs`), still bounded by the time and call budget.

**Tests:** 4 new (3 in `tests/test_v13_quality_regression.py`, 1 in
`tests/test_v6_21_4_search_coverage.py`); each of the passing-route tests fails on f37b1a5.
Full suite: no new failures.

# v13.14 review addition: repeats are checked across topics (20 Nov 2026)
The repeat check compared only with earlier reports of exactly the same topic text, so insights from an
"AI governance" run were not checked in an "artificial intelligence" run. Earlier reports of every topic
are now loaded, the same topic's first (so the prompt's "already shown" list still shows those); the
shortlist and the model call decide as before. **Tests:** test_v13_12_earlier_insights.py updated.

# v13.15: search results screened for real news before fetching; more Exa where free search is thin (20 Nov 2026)
From RUN-FF8F59D21FDD4ACDB17E5B8F ("artificial intelligence"): about a third of the 20 source slots went to
pages that mention the topic but report no event (the trailer for the film "Artificial", "Artificial
Intelligence for the Z80", Cambridge Dictionary's word of the year, a release-site post).
- **News-event screen** (new services/discovery_screen.py): after the topic filter and before any page is
  fetched, ONE model call per discovery search reads the headlines (site, headline, description) and names
  the results that do not report a concrete event (films, games, hobby projects, tutorials, reviews,
  listicles, dictionary/culture pieces, satire, access pages). Embedding relevance cannot do this: those
  pages are on topic. Fail-open (a failed call, or one that would drop everything, keeps all results);
  "when unsure, keep it". Dropped headlines and reasons are logged (`discovery_screen`) and saved in
  `source_discovery_screen`.
- **Exa top-up on the first search**: when fewer than max(max_sources, 12) news results remain, two Exa
  searches add results (new URLs only), screened the same way. Within the Exa free-credit budget.
- **Exa also when free search is thin**: research searches (gaps, precedents, challenge) now add Exa when the
  free providers return fewer than 3 results or one of them failed (GDELT was rate-limited on every gap
  search on 20 Nov while NewsAPI returned a few results, so Exa never ran for them). Before, only when
  they returned nothing.
**Tests:** tests/test_v13_15_discovery.py (5; real headlines from the 20 Nov run). Full suite: no new failures.

# v13.16: past cases no longer rejected for "different entity"; blocked stories read from another site (20 Nov 2026)
From RUN-4367E5FE6BE84506BBC92CBB (stopped when the Gemini prepaid credits ran out, 402).
- **Past cases.** The new case notes (v13.13) showed why no run has found a past case: the "SAME ENTITY"
  rule added on 20 Nov for name confusion (AMD Instinct vs the startup Instinct) was read as "a past case
  must involve the same actors". The 2018 ZTE ban reversal, the 2020 Huawei licence and the 2023 FTC
  investigation of OpenAI were all rejected as "different entity". The rule is now about name confusion
  only; a past case is expected to involve other actors and is judged on the mechanism, and its outcome
  may be its own resolution (ban lifted, suit settled).
- **Blocked by the site and by Exa.** Reuters answered 401 to the app and to Exa, losing three key
  stories (California sues OpenAI; Trump administration weighs forcing firms to disclose Chinese AI use;
  panel meeting scrapped). When both fail, the headline words in the URL are searched in Exa and the
  first report of the same story from another site is read (`retrieval_mode: exa_alternate_coverage`,
  `alternate_coverage_for`). It passes the same evidence checks. Within the Exa budget.
- Not changed: the Federal Register document over 35,000 characters is still skipped (deliberate limit on
  local extraction).
**Tests:** tests/test_v13_16_run_4367_fixes.py (4); test_v92 and the Exa fallback test updated. Full suite: no new failures.

# v13.17: Google search as the last resort for a blocked story (20 Nov 2026)
Owner: when a site blocks the app and Exa, and Exa cannot find the story elsewhere, use Google.
Chain for a page that cannot be read: (1) the publisher's page directly; (2) the same page through Exa;
(3) the same story on another site, found by Exa search on the headline words in the URL; (4) only when
Exa found no other site (Exa off, budget used, nothing found), the same search through Google (a capped
Gemini Google-Search helper of its own, default 8 searches a run, `EVENT_INTELLIGENCE_ALTERNATE_GOOGLE_SEARCHES`;
separate from the 30 research searches); (5) the GDELT context fallback. Another site's page is read
directly first, then through Exa. The source is marked `alternate_coverage_for`, `alternate_found_by`
(exa or google) and passes the same evidence checks. Google search uses Gemini: it counts toward the
call cap and needs Gemini credits (5,000 free Google queries a month on Gemini 3, then $14 per 1,000).
**Tests:** 4 more in tests/test_v13_16_run_4367_fixes.py (8). Full suite: no new failures.

## v13.18 (22 Nov 2026) - call budget after RUN-ACC0E1ED

RUN-ACC0E1EDB8AA42C192CCA5A5 (replay of RUN-4367, 480-call cap) used 285 of its 428 Gemini calls on
one-claim support checks (Flash-Lite, each resending the same ~1,500-token instructions). The stages
after the insights ran short: actor moves had 19 of 24 claim checks refused, the pairwise judge did
not run (judge_unavailable), the red team and report grounding fell back to their no-call checks.

- services/grounding_service.py: ground_claims sends up to 6 claims per request
  (EVENT_INTELLIGENCE_GROUNDING_BATCH, 1 = old behaviour). Same instructions, each claim with only
  its own evidence, same validation. A claim the batch does not answer validly is checked alone, so
  batching never changes what is accepted. `batched_checks()` does the same for callers that check
  claims one at a time on purpose (briefing, actor moves: one bad check must not void the rest).
  Only on the Gemini classifier; a supplied classifier (tests, local model) is unchanged.
  Log line: grounding_batched claims=N batches=B answered=A.
- research_orchestrator_service: after-insights reserve 70 -> 100 (max share 0.25); when it is
  released after the lab, up to 15 calls stay held for the judge (it ran after the release and found
  none left).
- Not in this version: precedent search (0 past cases again; poor retry queries) and feeding
  "already said by the sources" back into insight generation (5 of 7 candidates restated sources).
- tests/test_v13_18_grounding_batch.py (10 tests); reserve numbers updated in
  tests/test_v13_quality_regression.py. Full suite: same 55 failed / 6 errors as before (stale tests,
  missing libraries), 10 more passed.

## v13.19 (22 Nov 2026) - aimed at original insights (research report of 22 Nov)

RUN-ACC0E1ED released 0 original insights: 5 of 7 candidates restated their sources or rested on one
development, one story (California v. OpenAI) took 3 of 4 exploration slots, and 0 past cases were
accepted although the searches found good ones (Kia/Hyundai public-nuisance suits, Bonta v. Meta,
SEC v. SolarWinds) - the page reviewer rejected them for different actors or sector.

- services/story_families.py (new): one model call per run groups developments that are one story
  (an incident and the lawsuit about it, worded differently), joined before slots are given out, so
  each slot reads a different story. Word overlap could not see this (measured on the RUN-4367
  checkpoint: lawsuit vs incident 0.11, lawsuit vs an unrelated Nvidia item 0.16). Key-event discovery
  (EMNLP 2023 Findings) uses the same split: cheap candidates, an LLM decides same event / follow-up.
  Fail-open; log line story_families=...
- Exploration prompt gets an EXPECTED VIEW block: the run's published-analysis records and the
  developments' own reports, with the rule that the conclusion must not be stated by or follow
  directly from any of them (SciMON, ACL 2024: compare with retrieved prior work, ask for something
  significantly different).
- Precedent page review: compare ROLES not names, with four worked examples (Kia/Hyundai ACCEPT, Meta
  partial, FTC antitrust REJECT, SolarWinds ACCEPT); a case without its outcome on the page is still a
  case (the follow-up search looks for the outcome). Gentner, Rattermann & Forbus 1993 (surface vs
  relational similarity); Zheng et al. NeurIPS 2023 (few-shot raises judge consistency 65% -> 77.5%).
- Precedent retry query: the development is first written as a role-based mechanism, then one case
  with a known result is named and searched with a result word (settlement, ruling, ...).
- Claim-check batch 6 -> 4 (Cheng et al. 2023: drops "often occurring at b=6").
- Not verified offline: whether Gemini now accepts the logged precedent pages and whether the expected
  view lowers the restating share. Both need model calls; the story-family join is verified on the
  checkpoint (the lawsuit story gets 1 of 5 slots, was 3).
- tests/test_v13_19_originality.py (12 tests). Full suite: same 55 failed / 6 errors, 22 more passed.

## v13.20 (22 Nov 2026) - what makes the app worth reading over the news (research report of 21 Nov)

Readers trust news at 37% and AI chatbot answers at about 20% (Reuters Institute DNR 2026); summaries
are commoditised (Particle, Ground News, deep-research tools). The report's conclusion: the app's only
defensible difference is verified originality plus a public, scored track record - and a reader must
be able to SEE both.

- services/proof_line.py + dashboard: one line under every insight - whether it is stated by any of
  the N sources read, how many stories and publishers it combines, claim support a/b, how many steps
  are assumptions, whether the contrary-evidence search finished, the open forecast and its deadline.
- Dashboard: original insights first; findings their own sources already state are shown apart under
  "Already in the coverage (background, not counted as insights)". RUN-ACC0E1ED's two shown findings
  were of this kind and looked like insights.
- services/reader_ratings.py + dashboard: two yes/no questions per insight, "New to me?" and "Would I
  act on it?" (binary, Husain/Yan), saved to data/reader_ratings.jsonl.
- services/track_record.py, pages/3_Track_record.py, tools/audit.py: runs, runs with an original
  insight, abstention share, unique originals, forecasts made/resolved, Brier score, reader shares
  with 95% intervals - each with its credibility threshold (100 resolved forecasts, MacKay 2025; 50
  ratings = +/-14 points). Audit of this folder on 22 Nov: 68 runs on 1 topic, 22 with an original
  insight, 3 forecasts made, 0 resolved, 0 ratings.
- Note: replays never record forecasts (`if not replay`), so the track record grows only from live
  runs; reports saved before v6.30.2 carry no "already in the coverage" label and count as original.
- tests/test_v13_20_reader_value.py (6 tests); dashboard and track-record page rendered in Streamlit's
  AppTest without errors on the 22 Nov report.

## v13.21 (22 Nov 2026) - checking stages off the call cap

- Actor moves, the pairwise judge and the final checks (report grounding, red team) no longer count
  against the shared Gemini call ceiling and have no limit (services/gemini_llm_service.set_own_limit;
  optional EVENT_INTELLIGENCE_ACTOR_MOVES_MAX_CALLS / _FINAL_CHECKS_MAX_CALLS / _JUDGE_MAX_CALLS set
  one). In RUN-ACC0E1ED the ceiling cut 19 of 24 actor-move claim checks - never checked, so possibly
  the best moves - stopped the judge and pushed the red team and report grounding to no-call checks.
  These stages check a fixed list once, so without a limit they cannot loop. The shared ceiling still
  bounds the exploration and repair loops; its reserves are now lab 60 + briefing/focus 70.
- Contrary-evidence Google searches per run 24 -> 60 (both shown findings had an incomplete search;
  Google grounding has 5,000 free prompts a month).
- tests/test_v13_21_checks_off_the_cap.py; reserve numbers updated in two older tests.

## v13.22 (22 Nov 2026) - the editor's bar; Exa caps off

- services/par_check.py: before an ORIGINAL insight is released, one model call judges it against the
  user's own labelled examples (data/insight_review_90.csv: 4 approved, 4 rejected shown as examples)
  and a rubric from ICD 203 and the reader standard: beyond the sources, a specific mechanism, a
  consequence for a named actor, a checkable dated signal, plain substance, every step earned. Below the
  bar: not released, one repair with the missing element; if the judge cannot answer, the insight is
  held back (a quality bar does not fail open). EVENT_INTELLIGENCE_PAR_CHECK=0 turns it off.
- tools/calibrate_par.py: scores the judge on the user's held-out labels (true-positive and true-negative
  rates separately, Husain) before it is trusted - about 40 Flash calls.
- services/exa_contents.py: per-run and monthly Exa caps off by default. RUN-ACC0E1ED had 33 searches
  refused by the 600-unit run cap while $1.42 of the free credit had been spent; with no payment
  method Exa cannot charge (it stops at 402), so the caps only cost research. Env settings bring one back.
- tests/test_v13_22_par_check.py (6). Full suite: same 55 failed / 6 errors as before.

## v13.23 (22 Nov 2026) - variant view generator; the run checks itself against the objective

The objective: original insights a reader rates at par with publications that sell original analysis.
RUN-ACC0E1ED: 5 of 7 candidates restated their sources, forecasts ran 9-16 months, the briefing lines
were what any reader would guess. The generator prompt already asked for leverage, the outside view and
a forecast, but in one call for one candidate, with no statement of what readers already believe.

- services/variant_view.py, wired into each exploration slot:
  1. CONSENSUS (1 call): what informed readers of these reports already believe and expect next.
  2. The generator gets that consensus and is asked for up to THREE different variant views: the claim,
     the constraint the consensus ignores (Papic: political, economic, legal, geopolitical, time), a
     cited number, a named actor's forced move, a yes/no call resolvable in 2-12 weeks, and what you
     would see if the consensus is right instead (Steinhardt's variant perception; Marks's second-level
     thinking).
  3. Structure gate (no call): no number, no named actor, or no call within 14-84 days -> dropped.
  4. Pairwise tournament in both orders (co-scientist style) -> the winner goes to the claim-support,
     non-obviousness and editor's-bar gates.
  About 4 extra calls per slot (1 consensus + up to 6 pairwise judgments).
- services/run_metrics.py: first summary line of every run is "Objective MET / NOT MET": original
  insights that passed the editor's bar AND carry a call checkable within 12 weeks. MET needs >= 1.
- EVENT_INTELLIGENCE_VARIANT_VIEW=0 turns the variant stages off.
- tests/test_v13_23_variant_view.py (8). Full suite: same 55 failed / 6 errors as before.
- NOT verified: whether Gemini now writes insights at the bar. That needs the calibration of the
  editor's bar (tools/calibrate_par.py) and one live run; the run log's Objective line reports it.

## v13.24 (22 Nov 2026) - research report "Making AI Event Intelligence's insights match the best original-analysis publications"

Built from the report's implementation spec (stages A-H inside each exploration slot):
- A. Consensus MAP: 3-5 cited, dated claims of what coverage and analysts already believe, from the
  slot's records plus one Google-grounded search for published analysis (CONS-n ids, up to 10 a run,
  EVENT_INTELLIGENCE_CONSENSUS_SEARCHES). Also names search terms for primary records.
- B. services/primary_data.py: Federal Register (rules, comment-close and effective dates), SEC EDGAR
  full-text search (who filed which form when; SEC_USER_AGENT in .env recommended), CourtListener
  dockets (only with COURTLISTENER_API_TOKEN). Free APIs, not Gemini. Added as citable "numbers"
  records to the slot and the run, so the gates check what cites them. EVENT_INTELLIGENCE_PRIMARY_DATA=0 off.
- D. The generator must name which CONSENSUS[n] each candidate contradicts or refines, use a DIFFERENT
  lens per candidate (Papic constraints; Thompson value chain; follow the money; Green & Armstrong
  structured analogy), prefer primary-record dates and numbers, and make a 2-12 week call with a named
  resolution source; thesis in at most 35 plain words.
- E. Plain-language check (no call): thesis over 35 words or coined labels from the user's own labels
  ("structural asymmetry", "blind spot", ...) -> dropped.
- F. Novelty against the consensus map (1 call each, SciMON idea): restates it -> dropped.
- G. Pre-mortem (Klein; 1 call each): a likely failure reason already in the evidence -> dropped.
- H. Tournament (unchanged). Run line renamed "Objective (estimated, not proven ...)".
- try_variants.py: stage test of A-H on a saved run (no news retrieval), hard call ceiling.
- Not done from the spec: replacing the strategic-inference prompt itself with short per-lens calls
  (its output schema feeds every gate; kept, with the variant instruction appended); Green & Armstrong's
  mechanical base-rate rule; history-embedding novelty; retiring the hypothesis lab; briefing lines
  drawn from the winner. Tests: tests/test_v13_24_quality_stages.py (6). Full suite: same 55/6 as before.
- v13.24 also (user's direction, 22 Nov): (1) EVIDENCE FIRST - in each exploration slot a candidate is
  dropped before any judging unless every premise cites a record and its copied quote was found in an
  evidence record (quote_first's exact-substring check; no model). The old failure - an insight
  written first and found unsupported by the claim gate later - now stops at the cheapest point.
  (2) OUTSIDE JUDGE - services/outside_judge.py: novelty, pre-mortem, the tournament and the editor's
  bar (and tools/calibrate_par.py) are judged by a different model family: an open-source model on the
  user's own Ollama (localhost:11434, OpenAI-compatible endpoint; OUTSIDE_JUDGE_MODEL, else the first
  installed non-Google model - Gemma is excluded as Gemini's family). Gemini only generates. Without a
  running Ollama the run falls back to Gemini and logs "self-judged:gemini". Huang et al. ICLR 2024
  (self-correction without external feedback degrades), Zheng et al. NeurIPS 2023 (self-enhancement
  bias). tests/test_v13_24_outside_judge.py (4).

## v13.25 (22 Nov 2026) - every draft insight stays visible, with what it needs

User's rule: the run must produce insights that can be made better. Two ways candidates disappeared:
RUN-ACC0E1ED's 7 candidates were folded away under the two source-backed fallback findings (the
section opened only when nothing at all was released), and from v13.23 the exploration stages
(evidence first, structure, plain language, novelty, pre-mortem) drop variants before the claim check,
so they never reached the list of checked candidates and left no trace in the report.

- research_orchestrator_service `_variant_select`: a dropped variant's full text is kept in the run log
  (900 characters, was 160).
- dashboard: the section is now "Draft insights - written by this run, not verified, and what each one
  needs". It is open unless an ORIGINAL insight was released (a source-backed fallback no longer folds
  it away). It lists the candidates the claim check rejected and the variants dropped earlier, each with
  what it needs in plain words: "a call due within 2-12 weeks (its call is 340 days out)", "fact 2 quoted
  word for word from a source", "something the coverage does not already say", and so on.
- tests/test_v13_25_drafts.py (3).

## v13.25b (22 Nov 2026) - the quote rule no longer drops candidates

- The v13.24 evidence-first rule dropped a candidate in its exploration slot when a fact's copied quote
  was not found word for word in a source. That repeated the old failure - an insight thrown away
  instead of sourced - and it removed the candidate before the claim gate, whose challenge search
  attaches evidence and grounds the claims again. Now the quote check is recorded as a note
  (metadata evidence_check, e.g. premise_1_quote_not_found) and the candidate goes on. If the claim
  gate still finds no source, it is held back there and shown in the report's draft insights with its
  reason. tests/test_v13_24_outside_judge.py updated.

## v13.25c (22 Nov 2026) - repair, not drop; no self-judging

The v13.24 variant stages repeated the old mistake in more places than the quote rule: they threw
candidates away instead of improving them, against the research report they were built from.
- Rule misses (no number, no named actor, call outside 2-12 weeks, thesis over 35 words, jargon):
  the generator gets ONE targeted rewrite with exactly what is missing (variant_view.repair_prompt;
  report: "a rewrite runs once; the meaning is then re-verified"). Still failing -> held back and shown
  as a draft with what it needs.
- Restates the consensus: revised ONCE against the overlapping consensus claim, then re-checked
  (SciMON: revise against the prior work, do not discard).
- Pre-mortem: a failure reason already in the evidence now DOWNGRADES the candidate (ranked last), it
  does not kill it (report: "kills or downgrades").
- No outside open-source model running: novelty, pre-mortem, tournament and the editor's bar are
  SKIPPED and logged "not_judged:no_outside_model" - Gemini never judges its own insights.
  tools/calibrate_par.py refuses to run without the outside model.
- tests updated (test_v13_24_quality_stages, test_v13_22_par_check). Full suite: same 55 / 6 as before.

## v13.26 (22 Nov 2026) - one short generation call per lens (research report of 22 Nov, stage D)

The report's main recommendation, not done in v13.23-v13.25: the generator was one overloaded prompt -
about 38,000 characters of rules before the evidence, plus the variant-view rules appended on top - asked
for up to three candidates at once. Reasoning and instruction-following degrade with input length and
the number of instructions (Levy, Jacoby & Goldberg, ACL 2024; IFScale 2025), and LLM idea generation
collapses to a few themes unless diversity is forced (Si, Yang & Hashimoto, ICLR 2025).

- agents/strategic_inference_agent.py: `analyze(..., lenses=[...])`. With lenses, generation is ONE CALL
  PER LENS (`_lens_syntheses`): the lens and its rules, the story's instruction, the story's evidence and
  computed gaps, the allowed IDs, the five rule sections the validators enforce mechanically (evidence
  boundary, release contract, evidence linkage, support clusters, anchors - kept word for word) and the
  SAME output schema. One candidate per lens; a lens whose call fails is skipped. The evidence review and
  all normalisation are unchanged, so every check after it sees the same structure. Measured on a
  two-record story: each lens prompt 15,098 characters against 46,326 for the old prompt.
- services/variant_view.py `lens_instruction`: the rules for one lens - the consensus claim to contradict
  or refine (when the consensus map found one), the constraint or mechanism, a cited number or date, the
  actor and its forced move, a yes/no call resolvable in 2-12 weeks from a named public source, what you
  would see if the consensus is right, and a thesis in at most 35 plain words.
- research_orchestrator_service: each exploration slot passes the four lenses of variant_view.LENSES
  (constraints, value chain, follow the money, structured analogy); the slot log records each lens call
  (`lens_calls`: lens, result, prompt size). The old single prompt is used when
  EVENT_INTELLIGENCE_LENS_GENERATION=0 or the variant stages are off (EVENT_INTELLIGENCE_VARIANT_VIEW=0).
- try_variants.py prints each lens call's result.
- Cost: an exploration slot makes four generation calls on the Pro model instead of one (about three
  times the input tokens of that step); the evidence review is still done once per slot.
- Not verified offline: whether Gemini fills the schema as completely from the short prompt as from the
  long one (a candidate missing derivation fields is rejected by the normaliser as too thin). Check with
  try_variants.py on a saved checkpoint before a full run.
- tests/test_v13_26_lens_generation.py (4).

## v13.27 (22 Nov 2026) - the remaining items of the 22 Nov research report

- Base rate from structured analogies (Green & Armstrong, IJF 2007): services/variant_view.py
  `analogue_cases`, `rate_analogues`, `base_rate`. The outside model rates each date-checked past case 0-10
  on mechanism and says whether the expected outcome followed; the base rate is computed here
  (similarity-weighted, unknowns excluded, "weak" under 3 cases) and put into every lens instruction.
  Without the outside model it is "not rated" - Gemini never rates its own analogies.
- Hypothesis lab retired when lens generation and the variant stages are on (its call reserve freed);
  EVENT_INTELLIGENCE_KEEP_LAB=1 brings it back.
- Indicators: each candidate gives 2-3 what-to-watch signals naming the public source to watch.
- Key Assumptions Check (CIA Tradecraft Primer) added to the pre-mortem; shown under the insight as
  "Must be true for the call".
- Head to head with the user's most similar approved insight (services/par_check.py `head_to_head`), both
  orders, tie = half. Below 0.5 the insight is held back with ONE repair (missing
  "below_approved_example", the exemplar quoted as the fix) - not dropped. Runs only on the outside model.
- Briefing implications anchored on this run's original released insights (actor, constraint, dated call,
  indicators): services/editorial_briefing.py `anchor_lines`, `build_prompt(..., anchors=...)`.
- Repeat-of-earlier-run check uses the outside model when it runs (Gemini only as fallback, recorded).
- Run log objective line now gives three estimates: editor's bar passed/judged, new vs consensus, average
  win probability against the approved example. An insight the editor's bar did not judge (no outside
  model) no longer counts toward MET.
- Not done: story slots favouring stories with numbers - slots are chosen before the numbers are read.
- tests/test_v13_27_report_items.py (7); two older tests updated for the new fields.

## v13.27.1 (22 Nov 2026) - the generator side of v13.26 restored

v13.27 included v13.26's exploration side (each slot passes `lenses=` to the generator) but not its
generator side (agents/strategic_inference_agent.py `analyze(..., lenses=...)`, `_lens_syntheses`). With
the default settings every exploration call raised TypeError (logged as failed:TypeError) and, because
v13.27 retires the hypothesis lab whenever lens generation is on, a run would have explored nothing.
- agents/strategic_inference_agent.py: v13.26's lens generation (unchanged since 1d27cb3, so the file is
  v13.26's).
- tests/test_v13_26_lens_generation.py restored, plus a guard test that both sides exist together.
- try_variants.py prints each lens call again.

## v13.27.2 (22 Nov 2026) - bugs found in the paths the test setup switches off

The test setup turns the editor's bar, the variant stages and the outside judge off, so these paths only
run together in a live run. Reviewed with an exploration slot run end to end on the saved corpus (fake
model answers, no API calls) and the release loop with every stage on.
- **Two candidates per story go on, the rest are drafts** (`VARIANT_KEEP` 1 -> 2). With four lens
  candidates per slot and no outside judge there is no tournament, so the one "winner" was simply the
  first lens's candidate and the other three were discarded unseen - three Pro calls wasted per slot.
  Candidates beyond the top two are now listed as drafts ("ranked_below_slot_top").
- **A date or number word counts as a number** (`variant_view._NUMBER`). Only digits counted, so "the
  June breach was disclosed in September, three months later" failed "no_number" and cost a rewrite
  call, although the lens rules ask for "a cited number or date". Month names and number words count now
  - except "May", "one", "half", "double" and short month names, which are ordinary words ("OpenAI may face
  one more suit" would otherwise pass); beside a digit the digit counts anyway.
- **A single watch signal given as text** was split into its letters in the briefing anchors
  (`editorial_briefing.anchor_lines`).
- `variant_view.base_rate` ignores malformed entries instead of raising.
- tests/test_v13_27_2_fixes.py (4); each fails on 5cf6628. Two older tests (test_v13_23_variant_view,
  test_v13_24_outside_judge) now expect two kept per story.

## v13.27.3 (22 Nov 2026) - the local judge never picks an embedding model

- services/outside_judge.py: Ollama lists the most recently changed model first; on the user's laptop that
  is nomic-embed-text, which cannot answer a prompt, so every outside check (editor's bar, novelty,
  head-to-head, base rate, pre-mortem, tournament) would have failed. Embedding models are skipped and the
  largest installed chat model is used (llama3.1:8b on that laptop). OUTSIDE_JUDGE_MODEL or
  OLLAMA_JUDGE_MODEL still choose a model explicitly.
- tests/test_v13_27_3_judge_model.py (1).

## v13.28 (22 Nov 2026) - per-gate funnel and an editor's bar calibrated on the user's ratings

Research report of 22 Nov ("Getting AI Event Intelligence to produce original insights"), Methods 0 and 8.
- services/gate_funnel.py + tools/gate_funnel.py: for each saved report, how many candidates entered and
  left each gate (stories explored, lens calls, variant checks, claim and editor checks, precedent search)
  and why; `--sheet` writes the rejects to data/labels/rejects_<date>.csv to mark rightly / wrongly. Every
  run now logs the same funnel (`gate_funnel |` lines) and stores it in run_metrics. First result over the
  last 6 reports: 22 candidates reached the claim check, 0 passed (14 premise not supported by cited
  evidence, 6 restating sources); precedent search found cases in 3 of 18 searches, and the failures are
  retrieval failures (free providers failing 8, pages not fetchable 6, NewsAPI out of coverage 4, GDELT
  rate-limited 3), not reviewer strictness.
- services/par_check.py: the judge also scores the six rubric items 0-10; its examples are the user's 3
  most similar approved and 3 most similar rejected insights (TF-IDF, plain code), not a fixed set; the
  head-to-head exemplar comes from all approved insights.
- services/par_calibration.py: logistic regression from the item scores to the user's ratings, 5-fold
  cross-validated, threshold at cross-validated precision >= 0.7 (report target; van den Burg et al. 2025:
  a mapping fitted on ~100 labels raised agreement ~142%, few-shot ~1%). tools/calibrate_par.py scores every
  labelled row with the local model (never Gemini), fits and saves data/par_calibration.json;
  `--exemplars fixed` is the A/B against the old fixed examples.
- research_orchestrator_service `_par_check`: with a trusted calibration for the installed model the
  calibrated probability decides; without one the judge is ADVISORY - its verdict is shown on the insight
  ("editor's bar (judge not yet checked against your ratings): fail - missing ...") and nothing is held
  back by an unproven judge (EVENT_INTELLIGENCE_PAR_ADVISORY_UNTIL_TRUSTED=0 restores holding back).
- Each run logs `rate_next |` - the up to 5 candidates whose probability is nearest 0.5 (Method 8.4).
- run_metrics: an advisory pass does not count toward MET.
- tests/test_v13_28_gates_and_calibration.py (8); test_v13_22_par_check updated for advisory mode.
