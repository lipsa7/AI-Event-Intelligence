# AI Event Intelligence: product spec (settled)

## OBJECTIVE AND SETTLED DECISIONS (read before every build or question - do not re-ask)
- Objective: forward reasoning from present facts, in three forms: (1) precedent - "this happened before with a comparable
  company, so watch for it here"; (2) leverage - what an existing situation lets an actor do: who holds something others
  depend on; (3) policy/incentive fit - "given current rules and incentives, this move is right for this company".
- The bar for an insight (user, 2026-10-10): "you see the leverage for a thing and add incentives". Two facts side by side,
  a theme, or strongly worded restatement are NOT insights. Judge the plain meaning, not the wording.
- An insight backed by its evidence is shown with that evidence even when no counter-evidence is found, or the search for
  it cannot finish (labelled; confidence capped at moderate).
- Never a blank report: landscape and briefing always; implications every run.
- Backed analysis is SHOWN, with its citations (user, last month and 2026-10-10: "if an analysis is backed, give it";
  "what credible experts or orgs say can always be shown"). Being already argued by experts, stated by a source or
  expected from the headlines is a LABEL, never a reason to hide or demote. Unbacked claims are still removed. The
  search for an original hidden insight keeps running (loops not cut).
- Sources must be credible (user, 2026-10-10: "a random fb comment doesn't count"; "don't block genuine credible
  things"). Social media, user pages and video/podcast pages are never used; known official, research, news and
  professional sources are rated credible; other sites are kept but labelled "unrated" and never count as an expert
  view. The user can add trusted or blocked domains in data/credible_sources.txt and data/blocked_sources.txt.
- The product's main output is strategic decisions based on leverage (who holds what others depend on, each side's
  incentives, the move that follows). Expert-backed insights are the backup: shown, ranked, labelled, never
  suppressed (user, 2026-10-10).
- Insights are judged by the evaluator (v6.31): evidence checks first; then 12 yes/no checks (0-12); then
  order-swapped pairwise ranking (a model compares two insights more reliably than it scores one); provenance
  labels instead of a novelty kill switch; calibrated against the user's own verdicts (tests/data/insight_gold.json).
- When analysis has gaps, the app fetches the missing facts.
- No Gemini cost cuts that reduce output quality; but do not waste credits (no unnecessary runs).
- Research how established products / methods solve a problem before building. One package per run-and-fix round.
- The user installs packages; GPT reviews each package; attach every package in chat AND place it in the project folder;
  delete superseded zips.


Every package is checked against this file. Its README says which section it changes.

## The report is never blank
Four sections. Each has its own bar, and each is gated separately. A failed hidden insight empties only section 4, never the report.

| # | Section | When it renders | Bar |
|---|---|---|---|
| 1 | Event landscape | Always | Admitted events with actors, evidence and sources |
| 2 | Editorial briefing + forward-looking implications (precedent, leverage, policy/incentive fit, trend) | Always | Each sentence cites source facts and is grounded. Implications are labelled analysis, are inferences the cited facts support, and say what to watch and over what horizon. If analysis can't run, the section shows the source statements. |
| 3 | Historical analogy | When a comparable case is retrieved, dated and cited | Dated case (a year) from a fetched source, with an observed outcome quoted from that source (how it resolved); the comparison and the key difference are both grounded |
| 4 | Hidden cross-actor insight | Only when it clears its bar | Claim-level gate: premises supported; mechanism and conclusion backed by 2+ independent sources; a final-wording challenge search; no forecast leakage; the conclusion is not a restatement of a source |

If section 4 fails: "None this run". The run is still a completed report. A candidate that only restates a source is never shown as a separate "main finding"; at most it appears among the checked connections.

Every displayed field is grounded, including an implication's watch signal and horizon (an unsupported one is dropped). If section 2 can't be produced, it shows a plain status message; it is never silently omitted.

## What the implications must do
The report's core value is forward reasoning grounded in evidence:
- **Precedent:** "When this happened before (dated, sourced case), this followed; the same setup now exists for company X, so watch for Y."
- **Leverage:** "The current situation (dependency, contract, rule, position) gives actor A leverage over B."
- **Policy / incentive fit:** "Given the current regulation, subsidy, tax treatment, procurement rule or zoning change, this move appears strategically attractive for company X." Reasoned as: policy (fact) → why it matters to X (fact) → economic or strategic effect (inference) → attractive move (inference) → what could make it wrong → what to watch.
- **Trend:** only when none of the above applies.

Precedents are searched for the top 3 developments in every Deep run (one bounded round each), whether or not a hidden insight passes. A precedent counts only with a dated, sourced case that has an observed, quoted outcome and states both its similarity and its key difference (the difference shown is itself grounded); it "raises the likelihood" or "makes X worth watching", never "X will happen".

Leverage and policy fit are structured, and each part is grounded separately: the position or rule exists and the actor controls it or is exposed to it (stated facts), and the mechanism, effect and move follow from them (inference). An unstructured "X has leverage over the market" or "this is right for the company" is refused.

## Gap research (v6.18)
For the top 3 developments of every Deep run, the app asks what the analysis is missing and searches for exactly that:
- **precedent:** an earlier comparable case and what followed;
- **policy:** the regulations, subsidies, tax, procurement, zoning rules or incentives that apply;
- **dependency:** who depends on whom;
- **numbers:** the size involved.

The app's current use of the free news providers can't search years back or look for policy on demand (GDELT's index goes back to 2017, but its article list only returns the most recent three months of a search window), so Gemini's Google Search grounding is added as a search provider. Only its links are used. It is capped at 12 grounded prompts per run; one prompt can run more than one Google query, and the queries actually run are logged. Policy sources have no age limit, because a rule enacted years ago can still apply; whether it applies now is checked when the fact is used. Every page is fetched and validated (an exact quote, a publication date; for past cases, a dated passage and a quoted outcome) before it can become a fact in the briefing.

## Research flow (v6.19)
1. **Collect:** news discovery, event extraction, evidence, admission, cluster expansion. A checkpoint is saved at the end of this step (`data/checkpoints/`).
2. **Gap research:** runs before analysis (precedent, policy, dependency, numbers; free providers plus Google grounding). Found context feeds the analysis, the forecasts and the briefing. If the run budget is short, it moves to report time.
3. **Analysis:** hidden-insight synthesis and claim gate. Contrary-evidence research also uses Google grounding, with its own cap (6 prompts).
4. **Report:** the four sections.

`replay_run.py` re-runs steps 2–4 from a checkpoint, so fixes can be tested without paying again for step 1. `check_setup.py` makes one Google-grounded call to confirm the key and model before a paid run.

Policy pages without a publication date are accepted only for policy facts, and only when the page itself states when the rule took effect or that it is in force (with a year).

Each implication states its key assumption, i.e. what would make it wrong (Key Assumptions Check, CIA Tradecraft Primer), and the model weighs the case for and against before writing it (Halawi et al. 2024). That assumption is grounded like any shown field.

## Standing principles
- Diagnose before changing; one causal change per cycle; check against logged runs.
- Never lower a bar to get more output; fix the cause instead.
- Prefer established methods (cite them in the package README).
- Precision and honest exclusion over volume.
- Free news sources only; Gemini is the paid model.

## Built in v6.20
- Cross-run event memory (data/event_memory.sqlite3): source excerpts of each Deep run's events; later runs get "earlier coverage" context for their top developments.
- One shared resolver for duplicate stories (complete linkage, no chain-merging; IDs from event facts, not member articles).
- Structural non-obviousness check before a hidden insight is shown: (1) the conclusion must not restate a source; (2) its support must span at least two different developments; (3) a plain headline-level takeaway must not already state it; (4) no single development alone may support it. Failing any one means it is not shown as a hidden insight; it is shown as a verified connection only if its supporting evidence spans at least two different underlying developments (v6.20.3), otherwise section 4 says "None this run". No LLM "novelty score" is used (unreliable).

## Planned (not built yet)
- Mechanism novelty (whether the causal mechanism itself is new relative to past runs).

## v6.20.2 objective-integrity invariants

The following are release invariants, not optional quality preferences:

- Duplicate resolution requires a shared real-world occurrence, not merely the same actor/event family/date. Actor/action/topic words do not count as distinctive overlap.
- `underlying_event_id` must remain stable when additional reports of the same occurrence arrive; replayed checkpoints recompute IDs with the current resolver.
- Replays are read-only for collection/extraction and cross-run event memory.
- Gap-research context is available to strategic inference before Section 4 reasoning.
- A forward implication is eligible only with a non-empty grounded key condition describing what could make it wrong.
- Failure of hidden-insight non-obviousness does not itself qualify a finding as a strategic connection; the downgraded item must independently satisfy strategic-connection release rules.

- Repeated actions involving the same actor/counterparty at materially different times remain distinct occurrences; memory must not collapse them solely because the semantic signature repeats.

## v6.20.3 measured duplicate resolution

- The resolver is judged on hand-labelled pairs of the app's own extracted events (`tests/data/dedup_gold_pairs.json`, 93 pairs). Release floor: pairwise precision >= 0.90 and recall >= 0.70 (v6.20.3: 0.93 / 0.75). Any change to the resolver must re-run this test; a merge of an unlabelled pair must be labelled before release.
- "Connection between developments" means two different underlying developments (canonical resolver IDs), never two articles about one story.

## v6.20.4 inputs the analysis depends on (measured on the app's own events)

- Every event carries its article's publication date; an event date is set only when the text pins it to a day (resolved against the publication date). Month/year mentions are kept at that precision. Before v6.20.4 no event had a date, so every date check was inactive.
- Events without an acting institution get one only from the article's own text: a person's stated employer, a stated public office, a country, or a named subject. Vague groups stay without one. Release floor on the labelled set (tests/data/actor_recovery_labels.json): precision >= 0.90 (v6.20.4: 0.96, 72 of 148 recovered).
- Photo captions, promotions, related links and author bios are not admitted as events.

## v6.20.5 forecast follow-up

- Every checked forward-looking implication of a Deep run is recorded as an open forecast (data/forecasts/watch_items.sqlite3) with its watch signal, deadline (from its horizon; 180 days if none) and citations.
- Each later Deep run checks open forecasts against its own NEW sources (published after the forecast was made, not the forecast's own sources): "observed" only when the claim check finds a source stating the signal, "contradicted" when one states the opposite, "expired" past the deadline. A check that cannot run leaves the forecast open. At most 8 checks (8 model calls) per run.
- The briefing section shows "Follow-ups on earlier forecasts" with the reporting source, and a running track record. Replays never write to the ledger.

## v6.20.9 fixes from the first live Deep run (RUN-ED24092548084234B2300689)

- Every grounding call receives each evidence record once (first occurrence by evidence ID). Gap-research context records are in the run's evidence and in the gap context list; sending both made the claim check reject the whole briefing, so no implication or forecast survived.
- Gap research reads every page it finds: 4 queries x 2 results = 8 pages (was 5), within 160 s per development.
- A precedent is proven by the record's own dated passage (a case year before the current year), not by the page's publication date: a recent article describing a 2014 case is a valid source. Policy, dependency and numbers pages keep their date windows.
- Numbered or bulleted mechanism steps are checked without their list markers; a bare "1." is never a claim. Sentences are not split after "U.S.", "Sen." and similar abbreviations.
- The log line gap_research_sources= states, for each page found, whether it was reviewed, not reviewed (with the reason), not fetched, or failed to fetch.

## v6.20.10 fixes from the v6.20.9 replay (RUN-DFA27E66C51C4CDE8CD46B04)

- Briefing sentences are checked one at a time: a check that returns invalid output removes only its own sentence (fail closed) and is logged with its error; before, one such check removed every sentence.
- Counter-evidence disclosure (v6.25, product decision): an insight whose claims pass is shown even when its counter-evidence search could not be completed; it is labelled "Counter-evidence check incomplete" and its confidence is capped at moderate. A completed search that finds counterevidence still counts against it.
- Search allowance (v6.24): Google-grounded search allows 30 prompts per run for gap research and 24 for challenge searches (env GROUNDED_SEARCH_MAX_CALLS / GROUNDED_SEARCH_CHALLENGE_MAX_CALLS); a spent allowance is logged (grounded_search_budget_exhausted) and counted in the run log. Video and social pages are not research sources.
- Pattern grid (v6.23): per development, the same fields (actor, action, target, when, when reported, amount, rule) are extracted from every report, each cell copied from its report; patterns across rows (repeated actor, shared target, reporting delay, shared rule) are computed and become citeable "pattern" records (never observations).
- Claims-and-evidence table (v6.23): a released hidden insight shows each claim with how it is backed (stated / follows / assumption), publisher, date, quote and link, plus records pointing the other way.
- Organisation to advise (v6.30): the user names it (dashboard field, replay --org); otherwise the app picks the named organisation the news involves most (fallback; the reviewed products all take it from the user). Targeted gap research on it runs first; its facts lead the context; it is advised in depth, 1-2 others briefly.
- Advice saved and credits (v6.30.1): the 'what each actor should do next' section is now saved with the report (before, it was built and logged but the dashboard never received it); Google 402 'prepayment credits are depleted' fails at once instead of being retried 3 times on every model.
- Backed analysis shown with citations (v6.30.2): implications and next steps that published experts or organisations also argue stay in the main list with the expert source linked; a hidden-insight candidate that is backed but already stated by its sources, experts or headlines is shown (at most 2), labelled, with its evidence table, and the search for an original one continues; widely expected items are shown with their citations.
- Source credibility (v6.30.3): services/source_credibility.py rates each page's domain (official, research, news, professional, organisation's own site, press release, unrated, blocked), following Klue's source scoring and the IC Quality of Information Check. Blocked and video/podcast pages are dropped before they are read; facts from rated sources are used first; only credible sites count as expert views; every expert citation shows its rating.
- Insight evaluator (v6.31): services/insight_evaluator.py scores every shown item on 12 yes/no checks (decision relevance, mechanism, leverage and incentives, specificity, informational lift, actor specificity), ranks the top 5 by pairwise comparisons judged in both orders (flips = uncertain) aggregated with Bradley-Terry, labels provenance (strategic decision from leverage / derived / cross-event / expert-backed / stated by sources / widely expected), and shows 'Top strategic insights' first. eval_insight_judge.py measures the judge's agreement with the user's verdicts.
- Evaluator fixes (v6.31.1, GPT review): provenance has separate dimensions (type: leverage decision or analysis; derivation: derived / cross-event / stated by sources; corroboration: credible experts named; widely expected yes/no), so an expert agreeing with a leverage decision corroborates it rather than demoting it; the judge sees each item's evidence (what the sources state) for the rubric and the pairwise comparisons; only pairs the user decided count as agreement with the user, reference pairs are reported separately.
- Rivals and tournament (v6.29, deep research reports/Non generic strategic insight methods.md): each key rival is modelled in its own call from its own incentives (K-level reasoning, no personas); 8-12 candidate moves are de-duplicated and ranked in an order-swapped pairwise tournament; the final choice is a best response made after arguing the unfashionable option, with ICD 203 likelihood, separate confidence, and rejected alternatives shown.
- Actor strategy method (v6.28): per actor - crux (Rumelt), typed leverage (Helmer 7 Powers / asymmetry), rules and incentives, other players' likely moves, ONE best move with its trade-off (Martin; anti-"trendslop"), what would have to be true (true now = checked against sources; else watch), coherent actions. Checks: swap test (a move equally good for a rival is generic), not already reported, not widely expected. Hidden-insight loops stay on (user, 2026-10-10).
- Actor next steps (v6.27): every report has "What each actor should do next" - per main actor its situation, leverage, rules and incentives, and 2-3 next steps that would benefit it, each resting on precedent, leverage or incentive, with risk and watch signal. One strong-model call (Pro first, ACTOR_MOVES_MODELS); facts must be stated by their sources; a step must follow from its cited facts and must not already be reported.
- Leverage + incentives (v6.26): every hidden insight fills leverage_analysis (holder, what it controls, dependent, the
  holder's incentive from a cited rule/cost/revenue, predicted move); the leverage and incentives are checked as facts the
  sources must state; the predicted move may be inference. The insight-writing step runs on the Pro model first.
- Plain meaning (v6.26): the conclusion is rewritten as one plain sentence; it must not be stated by its own sources, and
  the headline and expert tests run on the plain version.
- Expert frontier (v6.22): gap research runs one expert-commentary search per development (published analysis, last 60 days). A hidden insight must not be stated or implied by that commentary (fail closed); implications already said by experts are labelled "widely expected"; the report quotes "What experts are already saying" with sources. Commentary is never a fact, never a constraint and never an independent observation.
- Hidden insight routes (v6.21.8): (a) cross-development - needs two different developments and no single one suffices; (b) consensus vs constraint - one development plus a cited context record (rule, deadline, dependency, number, past case) from gap research; the development's own records without that record must NOT support the conclusion, and it needs a dated forecast or a watch signal. Both: not obvious from the headlines.
- "So what" (v6.21.8): a hidden insight must keep a consequence (second-order effect or forecast) in the copy the reader sees; a finding reduced to facts side by side ("X while Y") is not released. Rewrites are told never to delete the consequence.
- A finding that passes every claim and quality check but was labelled by the model with another tier (e.g. "underlying_dynamic") is held to the hidden-insight bar (hidden-insight release rules + non-obviousness) and released as a hidden insight when it passes (v6.21.7); otherwise it does not count as released and exploration continues.
- A run whose claim-checked finding is neither a hidden insight nor a verified connection (e.g. tier "underlying_dynamic") is released as an abstention report (landscape, briefing, verified connections, "None this run"), never blocked. Safety net: if the final policy's only objection is a missing hidden-insight/connection section, the abstention report is released.
- Precedent pages without a publication date are reviewed; only their dated past-case records are kept.
- The dashboard's "question / depth submitted" line is written to data/logs/system.log; failed gap-research page fetches log their error text.

## v6.20.11 release audit and replay diagnostics (RUN-736F44E941C844D1843313C5)

- A hidden insight that passes every claim check is released even when the separate "connections" / "underlying" lists are empty: the insight states its own cross-actor chain (policy warning SECTION_COVERED_BY_HIDDEN_INSIGHT). Verified with the real report builder, release policy and compiled blueprint. The v6.20.10 abstention safety net never applies when a hidden insight is in the report.
- Every briefing check's verdict is logged (editorial_briefing_checks=), with how many implications the model wrote and how many were well formed. A check that hits the Gemini rate limit is retried once after a pause.
- Precedent searches name one specific earlier case (actor, action, year) instead of searching for the current development.

## v6.20.12 model outage (RUN-ABDC9848F29644CE9AB947B1)

- When every Gemini model is unavailable (rate limit or daily quota), the run releases the event landscape and a source-statement briefing with the note "the analysis model was unavailable", instead of failing with no report. Classification: analysis_model_unavailable. Set EVENT_INTELLIGENCE_RELEASE_ON_MODEL_OUTAGE=0 to restore the old failure.
- Gemini 429 handling honours Google's own retry delay ("Please retry in 41.2s", retryDelay), capped at 65 s; before, it retried after ~2 s and 4 s and gave up. Rate-limit warnings and the final error carry Google's message (which quota was hit) and reach data/logs/system.log.

## v6.21 convergent-evidence insights (claim gate v2.0)

Basis: ICD 203 analytic standards (separate assumptions from judgments, state analytic confidence) and the Key Assumptions Check of the CIA Tradecraft Primer.

- Facts (premises) must still be quoted from their sources; the conclusion must still be an inference from at least two independent sources; anything contradicted still blocks.
- A reasoning step that no source states no longer blocks the insight when its observations converge: supported premises from at least 3 different underlying news events (two reports of one story count once; a gap-research page counts as its own observation) and at least 2 different publishers (source domains). At most 3 such steps; each is shown under "Key assumptions (analysis, not stated by any source)" and removed from the released grounding record.
- Analytic confidence on every released insight: high with at least 5 independent events from at least 3 publishers, at most one assumption and no counterevidence; otherwise moderate. The basis is shown in words.
- The strategic model and its repair/alternative prompts are told this rule.
- Each run logs its Gemini usage: calls and tokens per model (gemini_usage_this_run=).

## v6.21.1 cost and implications (replay RUN-353F7E74B4E94FCD820FAFC4)

- Red-team semantic review covers the analysis a reader sees (connections, underlying dynamics, hidden insights, findings, second-order effects, counterevidence, what to watch, briefing, analogies, forecast follow-ups). It no longer re-reviews the evidence trail, the event landscape, internal diagnostics, empty sections or statements of unknowns against every evidence record (751 of 892 calls, 95% of input tokens in that replay). Deterministic checks still cover the whole report. EVENT_INTELLIGENCE_RED_TEAM_FULL=1 restores the full review.
- Implications (briefing v1.7): the rule, holder and relevance facts must be stated by the cited facts and the implication must follow from them; up to 2 reasoning steps (effect, move, mechanism) and the "could be wrong if" condition may go beyond the facts and are then shown marked "(assumption)". Anything contradicted removes the implication. (Replaces the v6.20.1 rule that removed an implication whose condition was not backed.)
- A precedent page published in the case's year dates the case (date basis "page_publication_date").

## v6.21.2 inline citations and implication facts (replay RUN-E8B8791954BF422AAD5837FD)

- A reasoning step, conclusion sentence or second-order sentence is checked against the records it cites inline ([EVID-...]) as well as the insight's list; the same for shown derivation sentences.
- An implication's stated facts (rule, holder, relevance) are checked against all the briefing's quoted facts; they must still be stated by one. The implication text itself is checked against its own cited facts.
- The briefing asks for 2 to 4 implications including a simple trend one; the analysis and alternative prompts steer away from conclusions that assert an unstated cause.

## v6.21.3 provenance and precedent dates (GPT review of v6.21.1/v6.21.2)

- A rule/holder/relevance fact backed by a quoted fact the implication did not cite is shown with that fact added to the implication's citations. If the checker names no backing record outside the implication's own citations, the implication is not shown.
- A page's publication year dates a historical case only when the passage around the quote (300 characters either side) names no other year; a retrospective article ("In 2012, ...") is not dated by its page.

## v6.21.4 search coverage for sections 3 and 4 (replay RUN-61A3D6B0BCDE4A67B32EB500)

- Past cases: up to two named-case searches per development (5 queries, 10 pages, 200 s). If no case with an observed outcome was found, one follow-up search: for what followed the case found, or (none found) for a different named case the model proposes. Same page review and quote/date validation. The log line historical_cases= gives, per retrieved case, whether it is usable and why not (no observed outcome, no similarity/difference, year).
- Hidden insight: when no candidate passes (including when the only candidate fails final quality before the claim gate), candidates are proposed one combination of developments at a time (top triple, then pairs of the developments with the most independent events; combinations already used by tried candidates are skipped; up to 3, EVENT_INTELLIGENCE_INSIGHT_EXPLORATION). Each sees only that combination's events plus gap-research context, goes through the same claim gate, and exploration stops at the first release. The log line insight_exploration= lists each combination and its result.

## v6.21.5 implications beyond the headlines (replay RUN-61A3D6B0BCDE4A67B32EB500)

- The briefing asks for 4 to 6 specific implications: named winners/losers, a second-order consequence, a number/date/threshold where the facts give one, and a context fact (rule, dependency, number, past case) where available.
- After the evidence checks, near-duplicates are removed and each implication gets two entailment tests (not an LLM novelty score): (1) a plain "what a headline reader expects next" view, written from the development titles alone, already says or implies it -> expected; (2) the run's most relevant records already state it happened or was reported -> already reported. Passing implications are shown (at most 4, specific ones first); others are shown under "Widely expected" (at most 2), so the section is never blank. If a test cannot run, the implication stays, marked untested. Log line: implication_novelty=.

## v6.21.6 exact combinations and old-news check (GPT review of v6.21.5)

- Hidden-insight exploration skips only the exact combinations of developments earlier candidates used (not every combination inside the union of used developments).
- The "already reported?" test always includes the implication's own cited records, plus up to 23 lexically related records; the checker decides paraphrases.
