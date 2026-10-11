# Event Intelligence v6.29: the methods with the strongest evidence (includes v6.28, v6.27.1)

Built from the deep research, which is included as `docs/RESEARCH_non_generic_strategic_insight_methods.md`.

## Why the output was "meh"
The 2026 HBR "strategy trendslop" study found that 7 leading AI models almost always pick the fashionable option: differentiation, augmentation, long-term. More company context barely helps. The fix has to be in the method, not only the model or the prompt.

## What v6.29 adds to "What each actor should do next"
1. **Rivals modelled separately (K-level reasoning, the strongest evidence found).**
   - Each actor's 1-2 key rivals or counterparties get their own call, based only on THEIR incentives and constraints. The call predicts their likely moves, with standard likelihood words.
   - The actor's move is then chosen as a best response to those predicted moves.
   - In the studies, this beat step-by-step prompting 0.65 to 0.32. Persona labels had no effect, so none are used, and the "senior strategy adviser" persona line was removed.
2. **Many candidates, then a tournament.**
   - The first pass now proposes 8-12 distinct moves across lenses: each power, each rule or incentive, a precedent, a response to each rival, and at least one unfashionable option (cost, short-term, retreat, concede).
   - Near-duplicates are removed, then the moves are ranked in pairwise matches. Each match is judged twice with the order swapped, and a split decision goes to the move citing more evidence.
   - Rubric: specific to this actor (the swap test), grounded in the facts, clear trade-off, anticipates rivals, feasible. Fashionable defaults lose.
3. **Best-response choice.**
   - The strongest case for the unfashionable option is argued first, then ONE move is chosen (no hybrids).
   - The move carries a likelihood in the standard US intelligence wording (ICD 203) and a separate confidence with its basis.
   - The rejected finalists are listed with why each lost, the "red herrings" practice. Then come conditions and actions.
4. **Dashboard.** Likely to succeed (with confidence), "Case for the unfashionable option", "Considered and rejected".

All v6.28 checks still apply: facts stated by sources, the swap test, not already reported, widely expected moves labelled. The hidden-insight loops are unchanged.

**Cost per run:**
- about 6 rival calls and up to 30 tournament judgements, on the briefing model;
- 3 choice calls on the Pro model.

**Log:** the `actor_moves=` line now includes "stages": rivals modelled, tournament matches and split decisions, and whether the best-response choice was applied.

## Tests
- `tests/test_v6_29_rivals_tournament.py`: 8 tests, all failing on v6.28.
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_29.zip .\_v6_29 -Force
python .\_v6_29\install.py --project .
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.28: actor strategy, the way strategists do it (includes v6.27.1)

## The method (researched 2026-10-10)
"What each actor should do next" now follows the method behind good strategic advice, instead of a free list of moves:
1. **The crux** (Rumelt, *Good Strategy / Bad Strategy*): the one pivotal challenge or opening the news creates for the actor.
2. **Typed leverage** (Helmer, *7 Powers*): which power is in play. Scale economies, network effects, counter-positioning, switching costs, brand, a cornered resource (sole access to logs, data, contracts or infrastructure), process power, or an exploitable asymmetry. Each is shown with what it gains and what stops others copying it.
3. **Rules and incentives:** what penalties, costs and money force (Papic: constraints over intentions).
4. **Other players:** what rivals, regulators or counterparties are likely to do, given THEIR incentives. Ignoring them is a named mark of bad strategy.
5. **One best move, with its trade-off** (Roger Martin). Research on "strategy trendslop" shows LLMs default to fashionable advice unless forced to choose. The prompt names those defaults to avoid.
6. **What would have to be true** (Martin): conditions for the move to work.
   - "true now" only when a source states it, and it is checked;
   - anything else becomes "not yet - watch for it".
7. **2 or 3 coherent actions** that carry the move out.

## Product practices used
- **Feedly AI Actions guide:** a role, an explicit output structure, a rationale for every judgment, and a cited fact for every claim.
- **Quid's SWOT agent:** context, then findings tied to evidence, then pressure from rivals, then recommendations each linked to a finding.
- **Consensus / AlphaSense:** every statement is shown with its source.

## Checks (one round, plus the existing not-obvious tests)
- Facts must be stated by their sources. The crux, the leverage reading, other players' moves, the best move and the actions must follow from the cited facts.
- **Swap test (new):** a best move that would be just as advantageous for a rival is generic, and is removed.
- A move already reported is removed. A move that headline readers or published experts already expect goes to "Widely expected".

**Not changed:** the hidden-insight loops stay on, as you asked.

## Tests
- `tests/test_v6_28_actor_strategy_method.py`: 6 tests, all failing on v6.27.1. Two v6.27 assertions were updated to the new shape (leverage is now a typed power).
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (after the current run finishes; project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_28.zip .\_v6_28 -Force
python .\_v6_28\install.py --project .
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.27.1: next steps must not be obvious (includes v6.27)

The next steps for each actor now go through the same "not meh" tests as the implications:
1. **Headline test.** One call writes what a headline reader would expect next. A step that view already says or implies is obvious.
2. **Expert test.** A step that published expert commentary already recommends is obvious.

Obvious steps are not shown as advice. They appear in one line per actor, "Widely expected (headline readers or published experts already expect these)", so nothing is hidden. The log line `actor_moves=` lists both groups.

## Tests
- `tests/test_v6_27_actor_next_steps.py`: now 8 tests, including one for this.
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_27_1.zip .\_v6_27_1 -Force
python .\_v6_27_1\install.py --project .
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.27: what each actor should do next (includes v6.26, v6.25, v6.24)

The objective, built directly: forward reasoning from present facts.
- **Precedent:** this happened last time with a comparable company, so it can happen here.
- **Leverage:** the existing situation gives this actor leverage.
- **Rules and incentives:** current rules and incentives make this move right for the company.

## New report section: "What each actor should do next"
For the 2 or 3 actors whose best move the news changes most:
- **Situation:** sentences quoted from the sources, with links.
- **Leverage:** what it controls that others need, or what it depends on.
- **Rules and incentives:** the rule, penalty, cost, revenue or deadline that decides what it gains or loses.
- **Next steps that would benefit it:** 2 or 3, each marked Precedent, Leverage or Rules & incentives, with why it pays, the risk and what to watch for. Past cases found by research can be cited ("after MOVEit, vendor-notice clauses spread").

**How it is made:**
- One strong-model call per run: the Pro model first, with 3.8 Flash and then 3.5 Flash as fallbacks. Set `ACTOR_MOVES_MODELS` to change the order.
- One round of checks:
  - the situation, leverage and rules must be stated by their sources;
  - a step must follow from its cited facts, and must not be something the news says already happened.
- An actor is shown only with at least one supported rule or leverage AND one supported step.
- None of the rewrite loops that flattened the hidden insights.

**Cost:** 1 Pro call plus about 10 to 20 checks on the cheapest model.

**Also:** the insight-writing model's timeout limit was raised from 90 to 180 seconds so the Pro model's 150-second budget applies.

**New log line:** `actor_moves=` (model used, steps drafted, steps shown, what was removed and why).

## Tests
- `tests/test_v6_27_actor_next_steps.py`: 7 tests, all failing on v6.26.
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_27.zip .\_v6_27 -Force
python .\_v6_27\install.py --project .
python -m pytest tests/test_v6_27_actor_next_steps.py tests/test_v6_26_leverage_incentives.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.26: leverage plus incentives (includes v6.25, v6.24)

## Back to the objective
The app exists for forward reasoning in three forms: precedent, leverage, and policy/incentive fit. Your bar: "you see the leverage for a thing and add incentives". The last versions sharpened filters and evidence displays but never made the insight itself take that form, so the candidates were juxtapositions ("X while Y") or strongly worded restatements.

## Changes
1. **Every hidden insight is a leverage-and-incentive judgment** (`leverage_analysis`):
   - a named holder controls something (resource, access, information, rule position, contract, deadline) that a named dependent needs;
   - the holder's incentive comes from a cited rule, penalty, cost or revenue (and the dependent's, if it differs);
   - the predicted move: what that makes someone rationally do, which the headlines do not say.

   **How it is enforced:**
   - The leverage and the incentives are checked as FACTS that the sources must state. Only the predicted move may be the app's own reasoning.
   - A candidate without this structure is sent back with that instruction, before any claim checks are spent on it.
   - A new worked example (Exemplar 5) teaches the shape in a different domain.
2. **The Pro model writes the insight.** gemini-3.1-pro-preview is first in the chain, with a 150-second timeout. If it is unavailable, the chain falls back to 3.8 Flash and then 3.5 Flash. Only the insight-writing calls use it (about 3 to 6 per run).
3. **Plain-meaning check.**
   - The conclusion is rewritten as one plain sentence.
   - If its own sources already state that plain version, it is rejected.
   - The headline and expert tests run on the plain version.
   - This catches the "Super Intelligence" case: plainly, "the bill bans what Trump now calls all AI", which the source article already said.
4. **Dashboard.** The insight shows Leverage / incentives / Therefore lines above the evidence table.
5. **`docs/PRODUCT_SPEC.md`** now opens with the objective and every settled decision, to be read before every build or question.

## Tests
- `tests/test_v6_26_leverage_incentives.py`: 7 tests, all failing on v6.25. One is built from the "Super Intelligence" case.
- Updated older tests:
  - the gate's fixture candidate now carries a leverage analysis;
  - fake models now answer the plain-rewrite prompt;
  - two call-count checks now include the plain-meaning check.
- The full test folder shows the same 30 old failures and 7 collection errors.

## Before you run
- **Gemini credits are needed.** The Pro model costs more per call than Flash, but only for the few insight-writing calls.
- If the credits are empty, the run releases the landscape-only report (model outage).

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_26.zip .\_v6_26 -Force
python .\_v6_26\install.py --project .
python -m pytest tests/test_v6_26_leverage_incentives.py tests/test_v6_25_disclose_unsearched.py tests/test_claim_support_gate.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.25: show the insight, disclose the gap (includes v6.24)

## The decision (agreed earlier)
An insight that its evidence backs is shown, with that evidence, even when contrary evidence cannot be found or the search for it cannot finish. This matches comparable products:
- **Consensus:** shows a claim with its supporting and disputing sources.
- **AlphaSense and Perplexity:** cite each statement.
- **US intelligence standard ICD 203:** judgments are published with a confidence level and the information gaps stated.

Until now the app withheld such an insight. That is what hid the "Super Intelligence" vs "Ban Artificial Superintelligence Act" insight when the search allowance ran out.

## Changes
1. **Show, labelled.** If the counter-evidence search for the final wording could not be completed:
   - the insight is shown, labelled "Counter-evidence check incomplete", with the reason;
   - confidence is capped at "moderate", and the basis line says the search could not be completed.
   - A search that ran and FOUND counterevidence still counts against the insight, as before.
2. **GPT review of v6.24, both points fixed:**
   - search results labelled with a site NAME ("YouTube", "Reddit", "X") are dropped too;
   - generic capitalised groups ("Government officials", "Tech Companies", "Users") no longer count as named parties in the pattern table.

## Tests
- `tests/test_v6_25_disclose_unsearched.py`: 6 tests. 5 fail on v6.24; the 6th is a guard that a completed search is not labelled.
- Two older tests were updated, because they asserted the old withhold rule this version replaces: `test_research_limit_releases_with_disclosure` and `test_incomplete_final_search_releases_with_disclosure`.
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_25.zip .\_v6_25 -Force
python .\_v6_25\install.py --project .
python -m pytest tests/test_v6_25_disclose_unsearched.py tests/test_v6_24_search_budget_and_patterns.py tests/test_claim_support_gate.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.24: search that lasts the whole run (includes v6.23)

## What replay RUN-FB8AB22DBDD548B5BB6D4898 showed
**Best result so far.** One candidate was a genuinely sharp insight: renaming all AI "Super Intelligence" collides with the Sanders/Casar "Ban Artificial Superintelligence Act", blurring what the ban covers. It was not shown because its counter-evidence search came back incomplete.

**Why: the Google search allowance ran out mid-run.**
- It was 12 grounded prompts for research and 6 for counter-evidence. The searches added in v6.21.4 and v6.22 need more.
- As a result:
  - the third story (EU AI Act) got no follow-up searches;
  - the expert search for the breach story never ran;
  - the counter-evidence search for that candidate returned "DISABLED", so it failed closed.

**Other problems:**
- Three of ten search slots for the breach story went to YouTube videos.
- The pattern table produced junk: "Trump acting on 11 targets: the notion; delegates; the lunch ...".

## Fixes
1. **Search allowance.** 30 prompts for research (3 stories x (6 searches + 1 follow-up) = 21 needed) and 24 for counter-evidence (up to 3 final-wording searches x 6 queries).
   - Both can be set with `GROUNDED_SEARCH_MAX_CALLS` and `GROUNDED_SEARCH_CHALLENGE_MAX_CALLS`.
   - If the allowance is ever spent, the log says `grounded_search_budget_exhausted`, and `gap_research=` / `challenge_search=` count the unsearched queries.
2. **No video or social results** (YouTube, TikTok, Instagram, Facebook, X, Reddit and similar) before pages are fetched, so the slot goes to the next article.
3. **Pattern table.** A target must be a named party (a proper name or an acronym), and a repeat needs the same action, e.g. "OpenAI agent accessed 3 different named parties: Services Australia; AIHW; NSW DCCEEW".

**Cost:** up to about 36 more grounded searches per run than before, when all of them are needed. The last run was 237 model calls, mostly the cheapest model; expect somewhat more.

## Tests
- `tests/test_v6_24_search_budget_and_patterns.py`: 8 tests, all failing on v6.23. The pattern tests use this run's actual junk.
- One v6.23 fixture was updated to the stricter rule (same action).
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_24.zip .\_v6_24 -Force
python .\_v6_24\install.py --project .
python -m pytest tests/test_v6_24_search_budget_and_patterns.py tests/test_v6_23_grid_and_evidence_table.py -q
python replay_run.py RUN-ED24092548084234B2300689
```
(Replays reuse the saved news of the original run, RUN-ED24092548084234B2300689.)

---

# Event Intelligence v6.23: what established intelligence products do (includes v6.22 and v6.21.8)

You asked me to look at products that already give good insights with the evidence behind them, and build what they do.

## What they do
- **AlphaSense.** Every statement links to the exact source snippet. Its "Generative Grid" asks the same questions of many documents and shows them as a table, so patterns appear across rows.
- **Consensus.** Each claim is shown with the sources that support or dispute it, and a meter of how many say yes or no.
- **Feedly, Recorded Future, GDELT.** They extract actor / action / target / time from every report and count across reports (business events, entity graphs).
- **CB Insights Mosaic.** Scores are computed from measured signals and back-tested against outcomes, not written by a model.
- **Dataminr ReGenAI.** Event briefs that update as an event unfolds.

**The common idea:** the insight comes from STRUCTURE across many reports (counts, delays, repeats), and each claim is shown with its evidence.

## What this version builds
1. **Pattern grid** (`services/pattern_grid.py`).
   - **Extraction:** one model call per development fills a row per report: actor, action, target, when it happened, when it was reported, amount, rule.
   - **Validation:** every cell must be copied from that report, and dates must agree with the copied text. Anything else is dropped.
   - **Patterns are computed, not written:**
     - one actor acting on 3 or more different targets;
     - one target affected by 2 or more actors;
     - the delay between event and report across cases;
     - one rule named across several actors.
   - **What it produces:** each pattern becomes a citeable record ("Computed across this run's reports: ...") that names the rows it came from. The briefing, implications and insight model can use it, and it can anchor the rule-and-number insight route.
   - **What it is not:** a pattern is never counted as an independent news event.
   - **Example:** in the last run this is the step that would have counted "OpenAI agents reached 5 different agencies, each reported about 3 months later".
2. **Claims-and-evidence table** on every released insight.
   - **Summary line:** "N records from M publishers back this; K point the other way".
   - **Table:** each claim (fact, reasoning step, conclusion, consequence), with how it is backed (stated by sources / follows from sources / assumption) and its sources (publisher, date, link).
   - **Quotes:** the quoted passages, and "Evidence pointing the other way".
3. **Prompt.** The insight model is told patterns exist and to prefer insights that rest on one.

**Cost:** about 3 extra model calls per run, one per development.

**Switch off the grid:** set `EVENT_INTELLIGENCE_PATTERN_GRID=0`.

**Not built:** back-testing against outcomes (CB Insights style). That needs months of stored runs. Event memory already stores them, so it can come later.

## Tests
- `tests/test_v6_23_grid_and_evidence_table.py`: 11 tests, all failing on v6.22.
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_23.zip .\_v6_23 -Force
python .\_v6_23\install.py --project .
python -m pytest tests/test_v6_23_grid_and_evidence_table.py tests/test_v6_22_expert_frontier.py tests/test_v6_21_8_constraint_and_so_what.py -q
python replay_run.py RUN-ED24092548084234B2300689
```
New log lines to check: `pattern_grid=` (rows and patterns per development) and `gap_research_sources=` (the expert-commentary pages).

---

# Event Intelligence v6.22: the bar is what published experts already say (includes v6.21.8)

Your verdict on the reporting-rule insight: "nice enough, but not standard, much better needed".

## Why it wasn't good enough
That point was already in published analysis before the run:
- Insurance Business, 5 Oct 2026, "Vendor AI breaches test how cyber notification clauses work in practice";
- Cloud Security Alliance research notes on AI-agent incident liability (2 and 3 Oct).

The experts had also gone further:
- five Australian agencies were affected, not one;
- Australian insurance law limits what late notice can cost a policyholder when a vendor kept silent;
- APRA warned in April that AI supplier contracts often lack incident-notification terms.

The app only checked "would a headline reader already conclude this?". That is too low a bar.

## How research systems judge novelty, and what this version does
- **Google co-scientist:** the Reflection agent searches the literature and compares each idea against what is known.
- **Sakana's AI Scientist:** a novelty check through literature search.
- **SciMON:** compares ideas against retrieved work and rewrites until they differ.

**The changes:**
1. **Expert-commentary search.**
   - One extra search per development for published analysis (law firms, think tanks, research groups, trade press) from the last 60 days. It always runs.
   - Its analysis passages are labelled "commentary".
   - The facts quoted on those pages are labelled "reported" and can be used in the briefing.
2. **Beyond-the-experts test.**
   - A hidden insight that the commentary states or implies is rejected (fail closed).
   - The rewrite is told: start from what the experts say, then find what follows that they have not said, or show with cited facts where they are wrong.
3. **Commentary is never evidence.**
   - It is not a fact, not a constraint (cannot open the rule route) and not an independent observation (does not count toward convergence).
4. **Implications.** One already said by experts goes under "Widely expected", marked "(experts already say this)".
5. **Report.**
   - New section, "What experts are already saying", with up to 4 exact quotes, each with its date and source link.
   - A released hidden insight states it was checked against N quoted expert passages.
6. **Strategic prompt.**
   - New "expert frontier" rule.
   - The model now sees each context record's type.
   - Context slots raised from 8 to 12.

**Cost:** about 1 extra search and 2 page reviews per development, plus 1 check per candidate and per implication.

**Still open:** this run missed that five agencies were hit. The commentary search will now pick up facts like that ("reported"), but wider news coverage is the next target.

## Tests
- `tests/test_v6_22_expert_frontier.py`: 14 tests, all failing on v6.21.8.
- Three older tests were updated: gap fixtures now include a commentary page; the query budget went from 5 to 6.
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (project folder, venv active, app stopped, after the current run finishes)
```powershell
Expand-Archive .\event_intelligence_v6_22.zip .\_v6_22 -Force
python .\_v6_22\install.py --project .
python -m pytest tests/test_v6_22_expert_frontier.py tests/test_v6_21_8_constraint_and_so_what.py tests/test_v6_17_report_sections.py -q
python replay_run.py RUN-ED24092548084234B2300689
```
A replay re-runs gap research, so the new expert-commentary search runs on the same news, and the result can be compared directly with the last run.

---

# Event Intelligence v6.21.8: insights with a "so what" (includes v6.21.7, not yet installed)

Your verdict on the last insight: "AI developers push military agents while they can't contain their commercial agents" is an opinion, not an insight. Correct: it put two facts side by side and said nothing about what follows.

## What the replay showed
1. The FIRST candidate was the right shape: "reporting rules that require immediate notification are incompatible with developers taking months to detect what their own agents did". It rested on one news story plus a rule found by gap research (Australia's SOCI Act mandatory cyber-incident reporting).
   - The check rejected every one-story conclusion. The prompt allowed them; the gate did not.
2. Each rewrite was told to drop unsourced steps. That stripped the consequence until only "X while Y" was left.

## How established analysts do it, and what this version builds
- **Constraints over intentions (Marko Papic, *Geopolitical Alpha*).** The insight is the gap between what the consensus expects and what a binding constraint (a rule, deadline, dependency, number) forces.
- **The intelligence "so what" standard (BLUF writing).** A fact without what it changes, for whom, is noise.
- **Checkable forecasts (Halawi et al. 2024).** A dated question or a watch signal.

**The changes:**
1. **A second route for hidden insights: consensus vs constraint.**
   - One development plus a cited context record (rule, deadline, dependency, number or past case) can qualify.
   - Test: the conclusion is re-checked WITHOUT the constraint record. If the news alone already supports it, the constraint added nothing and it fails.
   - It must also not be obvious from the headlines, and it needs a dated forecast or a watch signal.
   - The cross-development route is unchanged.
2. **The "so what" test.**
   - The copy you would see must keep a consequence: a second-order effect or a forecast.
   - A finding reduced to facts side by side is not released. The rewrite is told why.
   - Rewrites are told never to delete the consequence. An unsourced step may stay as a labelled assumption under the existing convergence rule.
3. **Search.** Exploration now also tries the top development read against the context records, with its own instruction.
4. **Prompt.** A fourth insight mode, a 21st lens and a fourth exemplar. The exemplar teaches the shape, not a story.
5. **Dashboard.** The insight says how it was derived: a cited rule applied to one development, or a combination of developments.

**Not built:** a head-to-head ranking tournament between candidates (Google co-scientist style). The pipeline produces one candidate at a time, so there is nothing to rank yet. The gate was the real cause.

## Tests
- `tests/test_v6_21_8_constraint_and_so_what.py`: 15 tests.
  - Includes a replay-shaped case built from this run's real records (the SOCI rule and the OpenAI breach reports).
  - 14 fail on v6.21.7. One is a guard: a single-story conclusion without a constraint still fails.
- One v6.21.4 test was updated: it asserted that every exploration combination has 2 or more developments, which this version deliberately changes.
- The full test folder shows the same 30 old failures and 7 collection errors.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21_8.zip .\_v6_21_8 -Force
python .\_v6_21_8\install.py --project .
python -m pytest tests/test_v6_21_8_constraint_and_so_what.py tests/test_v6_21_7_promotion_and_topup.py tests/test_claim_support_gate.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.21.7: fixes from replay RUN-7CD0B58AA15D4B5E83571188 (includes v6.21.6, v6.21.5, v6.21.4)

1. **A real cross-actor insight was found, passed every check, and was thrown away.**
   - **What happened:** the repaired candidate ("AI developers are pushing militarised agents for warfighting while they cannot contain their commercial agents from accessing government networks, or detect it for months") passed every claim and quality check. The model had labelled it "underlying_dynamic". That label has no place in the report, but the finding still counted as released, so exploration stopped. The run then discarded it and showed "None this run".
   - **The fix:** a finding like that is now held to the hidden-insight bar: the hidden-insight release rules plus the non-obviousness test.
     - If it passes, it is shown as the hidden insight.
     - If it fails, it no longer counts as released, and exploration keeps trying other combinations of developments.
2. **Only 2 implications were written although 4 to 6 were asked for.** If the briefing model writes fewer than 4, one more request asks for the missing ones on developments the first set did not cover. New log line: `editorial_briefing_topup=`.

## Tests
- `tests/test_v6_21_7_promotion_and_topup.py`: 6 tests, all failing on v6.21.6.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over v6.21.6 (what you have).

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21_7.zip .\_v6_21_7 -Force
python .\_v6_21_7\install.py --project .
python -m pytest tests/test_v6_21_7_promotion_and_topup.py tests/test_v6_21_6_combinations_and_old_news.py tests/test_claim_support_gate.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.21.6: GPT's two v6.21.5 fixes (includes v6.21.5 and v6.21.4)

1. **Hidden-insight exploration skipped useful combinations.**
   - **The defect:** after candidates built on developments A+B and B+C, the untried A+C was skipped, and one A+B+C candidate blocked every pair.
   - **The fix:** only the exact combinations already used are skipped.
2. **The "already reported?" test could miss paraphrases.**
   - **The defect:** it only looked at records sharing two words with the implication.
   - **The fix:** it now always includes the implication's own cited records, plus related ones, and the checker judges paraphrases.

**Not changed:** near-duplicate removal is still word-based. GPT rated this less critical. A fully meaning-based version would need extra model calls for every pair of implications.

## Tests
- `tests/test_v6_21_6_combinations_and_old_news.py`: 3 tests, all failing on v6.21.5.
- Three older test files had their stand-in checkers updated so that the novelty tests report "nothing already said". They are now always run on cited records.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over v6.21.3 (what you have) and over v6.21.5.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21_6.zip .\_v6_21_6 -Force
python .\_v6_21_6\install.py --project .
python -m pytest tests/test_v6_21_6_combinations_and_old_news.py tests/test_v6_21_5_implication_novelty.py tests/test_v6_21_4_search_coverage.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.21.5: implications beyond the headlines (includes v6.21.4)

Your verdict on the first two live implications ("strict EU enforcement may push providers to prioritise compliance", "scrutiny will lead to stricter oversight"): people already know this. They were checked and true, but obvious, and partly already in last month's news.

## How similar systems handle this, and what this version does
The best-studied case is LLM research-idea generators: the Stanford study of 100+ researchers (Si, Yang & Hashimoto, 2024) and the "AI Scientist"-style novelty checks. They:
1. **over-generate:** produce many candidates, because most LLM ideas are obvious or duplicates;
2. **remove near-duplicates;**
3. **filter against what is already known.**

The same research shows that asking an LLM to score "how novel is this?" agrees poorly with experts. So the filter here uses yes/no **entailment** tests, the kind of check the app's claim checker already does reliably.

**This version:**
- **More, sharper implications.** The briefing asks for **4 to 6** implications, each:
  - naming who gains or loses;
  - giving a second-order consequence;
  - using a number, date or threshold where the facts have one;
  - resting on a rule, dependency, number or past case where available.
- **Near-duplicates removed.**
- **Two tests per implication:**
  1. **Expected?** One extra call writes "what a headline reader would expect next" from the development titles alone. If that already says or implies the implication, it's obvious.
  2. **Already reported?** If the run's most relevant source records already state that it happened, it's old news.
- **What you see:**
  - Implications that pass both tests appear normally, specific ones first (at most 4).
  - The rest appear under **"Widely expected"** (at most 2), so the section is never blank.
  - If a test can't run, the implication stays, marked as untested.
- **Cost:** 1 extra call, plus about 2 small checks per implication.
- **New log line:** `implication_novelty=` shows the reader's expected view and each implication's result.

Your two examples would land under "Widely expected".

## Also in this package (v6.21.4, not yet installed on your machine)
- Two named past-case searches per development, plus a follow-up search for the case's outcome or a different case.
- Hidden-insight exploration across combinations of developments.

## Tests
- `tests/test_v6_21_5_implication_novelty.py`: 6 tests, all failing on v6.21.4.
- One assertion in `tests/test_v6_21_2_citations_and_implications.py` was updated for the 4–6 request.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over v6.21.3 (what you have) and over v6.21.4.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21_5.zip .\_v6_21_5 -Force
python .\_v6_21_5\install.py --project .
python -m pytest tests/test_v6_21_5_implication_novelty.py tests/test_v6_21_4_search_coverage.py tests/test_v6_21_2_citations_and_implications.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.21.4: searching properly for past cases and hidden insights

Your v6.21.3 replay (RUN-61A3D6B0BCDE4A67B32EB500):
- **Implications:** 2 shown, the first on a live run. Both kept their facts, with the "could be wrong if" condition marked as an assumption.
- **Cost and time:** 49 Gemini calls, 5 minutes.
- **What it also showed:** sections 3 and 4 depended on single guesses.

## Section 3: past cases
- **The problem:** 3 past cases were found (the 2017 USDA order twice, Google's GDPR fine), but none could be shown, most likely because the pages didn't report what happened next. There was only one past-case search per development and no second try.
- **Two named cases per development.** The search plan names two different earlier cases per development (5 queries and 10 pages per development, instead of 4 and 8).
- **One follow-up search.** If no case WITH an observed outcome was found, one more search runs:
  - for what followed the case that was found, or
  - if none was found, for a different named case.
- **Same checks:** page review and quote/date checks are unchanged.
- **New log line:** `historical_cases=` says, for each case found, whether it can be shown and why not.

## Section 4: hidden insight
- **The problem:** the only candidate failed the quality check before reaching the claim check, so no other idea was ever tried.
- **The fix:** when no candidate passes, the app now proposes candidates one combination of developments at a time:
  - the three biggest developments together, then pairs of them;
  - combinations the earlier candidates already used are skipped;
  - up to 3 combinations;
  - each candidate sees only its combination's events, plus gap-research facts.
- **Same check, capped cost:** every candidate goes through the same claim check, and exploration stops at the first release. That's at most 3 extra analysis calls.
- **New log line:** `insight_exploration=` lists every combination tried and what came of it, so "None this run" means the main combinations were tried.
- **Setting:** `EVENT_INTELLIGENCE_INSIGHT_EXPLORATION` sets how many combinations (0 turns it off).

## Tests
- `tests/test_v6_21_4_search_coverage.py`: 9 tests, all failing on v6.21.3.
- One assertion in `tests/test_v6_20_11_release_audit.py` was updated for the two-case search plan.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over your exact v6.21.3 files and re-run.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21_4.zip .\_v6_21_4 -Force
python .\_v6_21_4\install.py --project .
python -m pytest tests/test_v6_21_4_search_coverage.py tests/test_v6_21_3_provenance_and_dates.py tests/test_v6_20_11_release_audit.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.21.3: GPT's two trust fixes (includes v6.21.2 and v6.21.1)

Fixes exactly the two defects GPT reproduced, nothing else.

1. **Provenance of rescued implication facts.**
   - **The defect:** since v6.21.2, a rule or relevance statement can be proven by any quoted fact in the briefing. But the reader saw only the implication's original citation (e.g. the delay article), not the fact that actually proved the rule (Article 55).
   - **The fix:** the backing fact is now added to the implication's citations. If the checker names no backing record outside the implication's own citations, the implication is not shown.
2. **Precedent dates.**
   - **The defect:** a 2017 article saying "In 2012, USDA changed ..." could be accepted as a 2017 case if the model returned 2017.
   - **The fix:** the page year now dates a case only when the passage around the quote (300 characters either side) names no other year. An explicit year in the quote still counts as before.

## Tests
- `tests/test_v6_21_3_provenance_and_dates.py`: 6 tests, 4 of which fail on v6.21.2. The other 2 guard that contemporaneous reports and explicit years still work.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over v6.21.1 (what you have installed) and over v6.21.2.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21_3.zip .\_v6_21_3 -Force
python .\_v6_21_3\install.py --project .
python -m pytest tests/test_v6_21_3_provenance_and_dates.py tests/test_v6_21_2_citations_and_implications.py tests/test_v6_21_1_cost_and_implications.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.21.2: citations inside reasoning steps are checked

Fixes from the v6.21.1 replay (RUN-E8B8791954BF422AAD5837FD).

**That replay:**
- **Cost:** 71 Gemini calls and 0.38M input tokens, against 892 calls and 15M tokens before, about 40 times less.
- **Briefing:** worked.
- **Hidden insight:** rejected, correctly. All three candidates rested on a cause no source states: Anthropic's 24% classifier compute cost, offered as the reason OpenAI took months to spot its agent's breach.

## Fixes
1. **Inline citations reach the check.** When Gemini cites records inside a step ("... two-month blind spot [EVID-B168...]"), the checker got only the insight's general list and reported those records as "not supplied". Each sentence is now checked against its own inline records too, for insights, second-order effects and shown derivation text.
2. **Implication facts.** The only implication (EU AI Act incentive) passed on its effect, moves, condition, watch signal and horizon. It failed only on its rule sentence, which merged two quoted facts while citing one. The rule, holder and relevance statements are now checked against all the briefing's quoted facts. They must still be stated by one of them; the implication text is still checked against its own facts.
3. **Prompts:**
   - The briefing asks for 2 to 4 implications, including at least one simple trend implication, so one failed check doesn't empty the section.
   - The analysis and alternative-candidate prompts say that a conclusion asserting an unstated cause is the commonest rejection. They ask for what the facts jointly show, with any cause kept as a labelled assumption.

## Tests
- `tests/test_v6_21_2_citations_and_implications.py`: 4 tests, all failing on v6.21.1.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over your exact v6.21.1 files and re-run.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21_2.zip .\_v6_21_2 -Force
python .\_v6_21_2\install.py --project .
python -m pytest tests/test_v6_21_2_citations_and_implications.py tests/test_v6_21_1_cost_and_implications.py tests/test_v6_21_convergent_insights.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.21.1: about 90% cheaper runs, and implications that can pass

Fixes from your first v6.21 replay (RUN-353F7E74B4E94FCD820FAFC4), found with the new cost log.

## 1. Cost: the final review was 95% of the bill
- That replay made **892 Gemini calls using 15.0 million input tokens**.
- **751 of those calls (14.3M tokens)** were the final red-team review.
- **What it was re-reviewing:**
  - the evidence trail, which is the source records themselves;
  - the 58-item event landscape, made of source statements already checked as facts;
  - the internal diagnostics;
  - empty sections and "critical unknowns".
- **Why that was so costly:** an item with no citation was reviewed against every evidence record, in batches of 24.

**Fix:** the review now covers the analysis a reader sees:
- connections, underlying dynamics, hidden insights and findings;
- second-order effects, counterevidence and what to watch;
- the briefing, analogies and forecast follow-ups.

Its rule-based checks still read the whole report. In the test, the review makes over 10 times fewer calls. On your replay that would have been about 90% fewer tokens. To get the old full review back, set `EVENT_INTELLIGENCE_RED_TEAM_FULL=1`.

## 2. Implications: same rule as v6.21's hidden insights
Gemini wrote one implication: "EU AI Act fines + OpenAI's three-month notification delay → incentive to report faster". Its facts passed. It was removed because its reasoning steps and its "could be wrong if" condition were checked as if a source had to state them.

**Now:**
- The rule, the actor and why it matters must be stated by the facts.
- The implication must follow from them.
- Up to 2 reasoning steps (effect, move, mechanism) and the condition may go further, and are shown marked **"(assumption)"**.
- Anything a source contradicts still removes the implication.
- The briefing prompt now explains this and asks for 2 to 4 implications.

**This reverses one earlier rule:** v6.20.1 removed an implication whose condition wasn't backed by a source. A "could be wrong if" is a hypothetical, which no source states, so it is now shown as an assumption instead.

## 3. Past cases dated by their page
The search found a real precedent: the 2017 USDA order to avoid the term "climate change", for the "super intelligence" rename. It was refused because the Guardian's 2017 article, reporting it as news, doesn't repeat "2017" in the quoted passage. A page published in the case's year now dates the case.

## Tests
- `tests/test_v6_21_1_cost_and_implications.py`: 7 tests, 5 of which fail on v6.21. The other 2 are guards: the rule-based checks still see the whole report, and an unsupported rule or implication text still removes the implication.
- One test in `tests/test_v6_19_replay_and_research.py` was updated for the reversed condition rule.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over your exact v6.21 files and re-run.
- This adds one managed file, `agents/red_team_agent.py`. The installer accepts only your current copy, which I hash-checked on your machine.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21_1.zip .\_v6_21_1 -Force
python .\_v6_21_1\install.py --project .
python -m pytest tests/test_v6_21_1_cost_and_implications.py tests/test_v6_21_convergent_insights.py tests/test_v6_19_replay_and_research.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.21: convergent-evidence insights

## The change
Until now, every reasoning step of a hidden insight had to be stated by a source. Real news almost never states the "why", so every candidate was rejected, including the best one in your last good run.

v6.21 applies the analysts' rule you described, made precise. The basis is the US intelligence analytic standards (ICD 203), which separate assumptions from judgments and state a confidence level, and the Key Assumptions Check from the CIA Tradecraft Primer.

**An insight is released when:**
- every fact it uses is quoted from its source (unchanged);
- the conclusion is an inference that at least two independent sources support but none states (unchanged);
- the facts come from at least 3 different news events and at least 2 different publishers. Two articles about one story count once; a fact found by gap research counts as its own observation;
- nothing is contradicted (unchanged);
- at most 3 reasoning steps are unsourced. These are shown under **Key assumptions (analysis, not stated by any source)**, never as fact.

**Every released insight also shows:**
- **its confidence:** high with 5 or more events from 3 or more publishers, at most one assumption and no counterevidence; moderate otherwise;
- **the reason for that confidence, in words**, e.g. "4 independent news events from 3 publishers point to this; 1 reasoning step is an assumption; no counterevidence was found."

**What else changed:**
- The analysis model and its repair prompts are told this rule, so they write candidates to meet it.
- The non-obviousness test (not a restatement, needs more than one development) still applies.

**Tested through the real pipeline:** the claim gate, the report builder, and the release check with your project's settings. An insight with one unsourced step and four converging events is released, with its assumption and confidence shown.

## Also in this version
- **Per-run Gemini usage.** The log line `gemini_usage_this_run=` gives calls and tokens per model, plus the number of Google Search grounding prompts. Your Google Cloud bill per SKU shows what those cost.
- **Includes v6.20.11 and v6.20.12:**
  - a passed insight is released;
  - every briefing check is logged;
  - rate limits are retried using Google's own delay, and the log records Google's reason;
  - a Gemini outage still gives you the event landscape.

## Not built: the free test mode
I said I would build a no-cost test mode. On inspection it can't test what matters: the prompts contain the run date, so cached model answers don't match, and a run with no model answers only tests the outage path. Instead, each release path is now tested offline with the real report builder and release check, which is what caught the blocked-insight bug in v6.20.11.

## Tests
- `tests/test_v6_21_convergent_insights.py`: 13 tests, 12 of which fail on v6.20.12. They cover:
  - assumptions are allowed only with convergence;
  - two reports of one story count once;
  - one publisher is not enough;
  - contradiction always blocks;
  - more than 3 assumptions block;
  - older callers keep the old rule;
  - the released record;
  - the confidence levels;
  - the real-pipeline release;
  - usage logging.
- One line of `tests/test_claim_support_gate.py` was updated because the repair prompt now has the rule appended.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over v6.20.11 (what you have now) and over v6.20.12, with Windows line endings, and re-run.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_21.zip .\_v6_21 -Force
python .\_v6_21\install.py --project .
python -m pytest tests/test_v6_21_convergent_insights.py tests/test_v6_20_12_model_outage.py tests/test_v6_20_11_release_audit.py tests/test_claim_support_gate.py -q
```
After topping up Gemini, replay the same run:
```powershell
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.20.12: Gemini rate limits handled, and an outage still gives you a report

**Update after you said the key is paid.** A paid key also has per-minute limits. Your app was likely failing on those, not on a daily quota:
- **Retry timing:** when Google says "Please retry in 41s", the app missed that wording and retried after about 2 s and then 4 s. It then gave up on that model and moved to the next one, and the next. It now waits as long as Google asks, up to 65 s.
- **Logging:** Google's own error text, which names the limit that was hit, now goes into `data/logs/system.log`. Before, the Gemini service's warnings never reached the log file.

This adds one managed file, `services/gemini_llm_service.py`. The installer accepts only your current copy, which I hash-checked on your machine.

Your v6.20.11 replay (RUN-ABDC9848F29644CE9AB947B1) never reached the new code. Every Gemini model returned "rate limit reached" within the first minute: gap research, analysis, all 7 models. Nothing in v6.20.11 was tested by that run.

It did show one more gap against "the report is never blank": when the analysis model is unavailable, the run failed with no report at all, even though its sources and events were already collected and valid.

**Fix:** the run now releases the event landscape, with a briefing made of plain source statements, and states that "the analysis model was unavailable (rate limit or daily quota)". It never invents analysis. To get the old behaviour back, set `EVENT_INTELLIGENCE_RELEASE_ON_MODEL_OUTAGE=0`.

## Tests
- `tests/test_v6_20_12_model_outage.py`: 4 tests. All fail on v6.20.11 and pass here.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over your exact v6.20.11 files.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_12.zip .\_v6_20_12 -Force
python .\_v6_20_12\install.py --project .
python -m pytest tests/test_v6_20_12_model_outage.py tests/test_v6_20_11_release_audit.py -q
```
Then replay (if it still hits limits, the log now says which one):
```powershell
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.20.11: release audit and replay diagnostics

Your v6.20.10 replay (RUN-736F44E941C844D1843313C5) was the first run to release a report with a checked briefing, in 9 minutes. This version closes what it showed was still missing, plus one fault found by audit before any run hit it.

## 1. The audit: a passed hidden insight would have been lost
No live run has yet released a hidden insight, so I tested that path with the real report builder, the real release policy and your compiled blueprint.

- **What I found:** a hidden insight that passes every check is blocked if the report's separate "connections" list is empty. In your replays that list was empty.
- **Why it mattered:** v6.20.10's "never blank" safety net would then have turned that run into an abstention, silently discarding the one finding the app exists to produce.
- **Fix:**
  - the release check now accepts a hidden insight without a separate connections list, since the insight states the connection itself (each step is claim-checked);
  - the safety net never applies when a hidden insight is present;
  - without a hidden insight, connections are still required exactly as before.

This adds one managed file: `services/policy_enforcement_service.py`. The installer accepts only your current copy of it, which I hash-checked on your machine.

## 2. Why the briefing had 0 implications
The replay's briefing passed checks for its synthesis sentences, but showed no implications, and the log could not say why.

- **Logging:** the new `editorial_briefing_checks=` line records:
  - how many implications Gemini wrote;
  - how many were well formed;
  - each check's verdict against what it needed.
- **Rate limit:** a check that hits Gemini's rate limit (one did) is now retried once after 20 s instead of being lost.

## 3. Past-case searches
All the pages found were read and none contained a dated earlier case, because the searches were about the current event ("ai web crawler unauthorized access…"). The search plan now names one specific earlier case it knows (actor, action, year) and searches for a report of that case.

The case only counts if a fetched page quotes it with its year, as before. The model's own knowledge chooses what to search for; it is never used as evidence.

## Tests
- `tests/test_v6_20_11_release_audit.py`: 8 tests, 5 of which fail on v6.20.10. The other 3 check that nothing loosened, for example that connections are still required without a hidden insight.
- The full test folder shows the same 30 old failures and 7 collection errors.
- The installer was tested over your exact v6.20.10 files and re-run.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_11.zip .\_v6_20_11 -Force
python .\_v6_20_11\install.py --project .
python -m pytest tests/test_v6_20_11_release_audit.py tests/test_v6_20_10_replay_fixes.py tests/test_verified_connection_release.py -q
python replay_run.py RUN-ED24092548084234B2300689
```

---

# Event Intelligence v6.20.10: fixes from the v6.20.9 replay

Your replay (RUN-DFA27E66C51C4CDE8CD46B04) confirmed parts of v6.20.9:
- the "1." / "2." fragments are gone;
- every page found was read;
- the new log line showed why each page did or didn't become evidence.

It also exposed three new faults. Each fault was reproduced in a test before it was fixed; 8 of the 9 new tests fail on v6.20.9, and all pass here.

## What went wrong, and the fix

1. **The briefing was still lost.**
   - **Cause:** a different fault from v6.20.9's. The claim checker voids the whole batch when any single sentence's check comes back invalid. A briefing has about 20 sentences, so one bad check removed all of them.
   - **Fix:** sentences are now checked one at a time. A failed check removes only its own sentence, and the log records which one and why. The number of model calls is unchanged.
2. **The report was blocked, breaking "the report is never blank".**
   - **Cause:** a repaired finding passed the claim check: "official disclosures establish a multi-month delay". The app labelled it an "underlying dynamic". The final release check only knows two kinds of finding, hidden insights and verified connections, so it blocked the whole report. In your first run the same finding came out as a verified connection, which is why that run was released.
   - **Fix:** such a run is now released as an abstention report: event landscape, briefing, verified connections, and "None this run" for the hidden insight. A safety net also releases the abstention report whenever the only objection is a missing hidden insight or connection.
   - **Side effect:** a run like this also skips the 17-minute red-team pass.
3. **Past cases were still 0.**
   - **Cause:** 3 of the 8 past-case pages, including Dark Reading's article on the OpenAI agent and the GDPR enforcement tracker, were refused only because the page had no publication date.
   - **Fix:** such pages are now read, but only records that quote a dated past case are kept from them.
4. **Logging.**
   - What you submit in Streamlit (question and depth) is now written to `data/logs/system.log`.
   - Failed gap-research page fetches now log their error text. The replay had 5 Google redirect links fail, and the next run will say why.

## Still true (not bugs)
- The hidden-insight candidate was rejected for real reasons: it relied on steps the sources don't state ("a 24% compute premium discourages real-time monitoring", "50 PB log trails"). That is the check working.
- Some searches return off-topic pages. The new log makes this visible: each page is listed with what it contributed.

## Tests
- `tests/test_v6_20_10_replay_fixes.py`: 9 tests, 8 of which fail on v6.20.9. One test in `tests/test_v6_20_9_run_fixes.py` was updated because claims are now checked one at a time.
- The full test folder shows the same 30 old failures and 7 collection errors. None are new.
- The installer was tested over v6.20.9 (your current files, hash-checked on your machine) and over v6.20.8, both with Windows line endings, and re-run.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_10.zip .\_v6_20_10 -Force
python .\_v6_20_10\install.py --project .
python -m pytest tests/test_v6_20_10_replay_fixes.py tests/test_v6_20_9_run_fixes.py tests/test_v6_17_report_sections.py -q
python replay_run.py RUN-ED24092548084234B2300689
```
The replay takes about 25 minutes, or less if it abstains before the red-team pass.

---

# Event Intelligence v6.20.9: fixes from your first live Deep run

Your Deep run on v6.20.8 (RUN-ED24092548084234B2300689) produced a report, but two of the four sections lost their substance. Each fault was found in that run's log and reproduced in a test before fixing. Each new test fails on v6.20.8 and passes here.

## What went wrong, and the fix

1. **Briefing: 0 implications, 0 forecasts.** The log showed `Editorial briefing: grounding failed: GroundingValidationError`.
   - **Cause:** the 9 facts found by gap research were sent to the claim checker twice: once in the run's evidence and once as gap context. The checker refuses duplicate record IDs, so the whole briefing fell back to plain source statements.
   - **Fix:** every claim check now receives each record once. The same guard was added to the forecast follow-up and non-obviousness checks.

2. **Historical analogy: 0 past cases.**
   - **Cause, part 1:** each development's searches found 8 pages, but only 5 were read. Every precedent search showed "result not reviewed (source_fetch_budget_exhausted)".
   - **Fix, part 1:** all 8 are now read, and the time limit is 160 s per development (your run used about 35 s).
   - **Cause, part 2:** a precedent page published in the last 31 days was refused, even when it described a dated past case. Google results ignore date filters, so most precedent pages are recent.
   - **Fix, part 2:** a precedent is now proven by its own dated passage, which must name a year before this one, not by when the page was published. Policy, dependency and numbers pages keep their date limits.

3. **Hidden insight: steps split into "1.", "2.".**
   - **Cause:** numbered mechanism steps were split into bare "1." and "2." claims, which always failed the check. That counted against every hidden-insight attempt.
   - **Fix:** list markers are removed before the check. Sentences are also no longer split after "U.S.", "Sen." and similar abbreviations.
   - **Still true:** some steps were genuinely unsupported, and those still fail. That is the check working.

4. **Diagnosis.** A new log line, `gap_research_sources=`, says for every page found whether it was:
   - reviewed;
   - refused, with the reason;
   - not fetched;
   - or failed to fetch.

   The gap-research log line now lists all limitations instead of the first 4.

## Tests
- `tests/test_v6_20_9_run_fixes.py` has 14 tests: 10 fail on v6.20.8, and 4 guards pass on both. The guards check that the relaxed precedent rule does not accept a current-year case, and that the other gaps still enforce their date windows.
- The full test folder shows the same 30 old failures and 7 collection errors as before. None are new.
- The installer was tested over v6.20.8, including a Windows line-ending file, and re-run (it reports "already installed").

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_9.zip .\_v6_20_9 -Force
python .\_v6_20_9\install.py --project .
python -m pytest tests/test_v6_20_9_run_fixes.py tests/test_v6_17_report_sections.py tests/test_v6_19_replay_and_research.py -q
```

## Then re-check the same run without collecting again
```powershell
python replay_run.py RUN-ED24092548084234B2300689
```
The replay reuses the saved sources and events. It still calls Gemini for gap research, analysis and the briefing, so it takes several minutes. When it finishes, tell me and I will read the log.

---

# Event Intelligence v6.20.8: event memory closes its database connections

**Why:** your v6.20.7 test run on Windows had 9 failures, all in the event-memory tests and all with the same error, `PermissionError: [WinError 32] … memory.sqlite3 is being used by another process`.

**The cause:** the event memory (`services/event_memory_service.py`) opened its SQLite database with `with sqlite3.connect(...)`. That form commits but never closes the connection. On Windows an open connection locks the file, so the tests could not delete their temporary databases. Linux does not lock files, which is why my test runs and GPT's did not show it. The forecast module already had this fixed; the memory module did not.

**What it affected:** only test cleanup. The app's behaviour was unaffected, but connections were left open until Python cleaned them up, so this fixes the app too.

**The fix:**
- every memory connection now always closes;
- three test lines that opened their own connections now close them;
- a new test fails if any memory connection is left open. It fails on v6.20.7 and passes here.

Nothing else changed. It installs over v6.20.7, which you have now, and over every earlier version.

## Install (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_8.zip .\_v6_20_8 -Force
python .\_v6_20_8\install.py --project .
python -m pytest tests/test_v6_20_resolver_memory_nonobvious.py tests/test_v6_20_5_forecast_followup.py -q
```
Then run Deep. Your `check_setup.py` already printed OK.

---

## v6.20.7 (included): the two v6.20.6 builds merged into one

GPT and I each built a "v6.20.6" fixing the same forecast follow-up problems. Two different packages with the same name would be confusing, so this merges them. **Install this one only.** It installs over your current v6.17.3, over every version up to v6.20.5, and over either v6.20.6.

## What was compared, and what was kept
I ran each build against the other's tests:
- **GPT's code against my tests:** 28 of 29 passed. The one failure was the source name. Your pipeline's evidence records carry only an internal source ID, so GPT's label fell back to that ID.
- **My code against GPT's tests:** 24 of 30 passed. GPT's build adds three things mine lacked:
  1. self-confirmation is also blocked by the original article URL, not only its source ID;
  2. AI topic aliases ("AI" = "artificial intelligence");
  3. counters for deferred and failed checks.

  The other three failures were only different output field names.

**v6.20.7 = GPT's `forecast_followup.py` and dashboard, plus my lookup of the publisher and link from the article store**, so the report shows "TechCrunch" with a link instead of "SOURCE-…".

Both builds fixed the same core problems, and all of those fixes are in this one:
- a forecast is never confirmed by its own article;
- a failed check never expires a forecast;
- forecasts not checked because the limit of 8 was reached never expire;
- runs follow up only forecasts from the same topic;
- database connections are always closed.

**One behaviour to know:** topic matching uses word overlap (GPT's rule), so "AI chips" and "AI regulation" runs share forecasts. That costs at most a few checks, and a forecast still needs a new source that states its signal before it counts as observed.

## Tests
- `tests/test_v6_20_5_forecast_followup.py` has 33 tests: GPT's 30 plus 3 of mine (same-topic scoping including the AI alias, connections closed, publisher from the article store).
- The full test folder shows the same 30 failures and 7 collection errors as before v6.16. None of them are new.
- The installer was tested on your v6.17.3 files, on v6.19, on v6.20.5, on my v6.20.6 and on GPT's v6.20.6.

## Install and run (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_7.zip .\_v6_20_7 -Force
python .\_v6_20_7\install.py --project .
python -m pytest tests/test_v6_20_5_forecast_followup.py tests/test_v6_20_4_dates_actors_asides.py tests/test_v6_20_3_dedup_gold.py tests/test_v6_20_resolver_memory_nonobvious.py tests/test_v6_19_replay_and_research.py tests/test_v6_17_report_sections.py tests/test_claim_support_gate.py -q
python check_setup.py
```
If it prints OK, run Deep.

---

## v6.20.6 (Claude build, included): forecast follow-up fixes

This fixes the problems GPT found in v6.20.5's forecast follow-up. I reproduced each one before fixing it. Nothing else changed. It installs over v6.15.2 through v6.20.5, including your current v6.17.3. The changes are in `CHANGES_since_v6_20_5.diff`.

These fixes matter from the **second** Deep run on. The first run only records forecasts, so you can run Deep on either v6.20.5 or v6.20.6.

1. **The forecast's own article never confirms it.** Any record from the same article (same `source_id`) is now excluded, not just the exact evidence record.
2. **A failed check never expires a forecast.** Before, a model error after the deadline marked it expired; now it stays open.
3. **The per-run cap never expires unchecked forecasts.** Forecasts that had relevant new evidence but weren't checked because the limit of 8 was reached stay open for the next run. Before, all 10 in GPT's case were marked expired.
4. **Forecasts are followed up only by runs on the same topic.** Topics are matched as normalized text. Before, "artificial intelligence" was made entirely of ignored words, so an AI run picked up forecasts from any topic, such as banking regulation.
5. **Forecast database connections are always closed**, so no file locks are left on Windows.
6. **The follow-up shows who reported it:** the publisher and a link, instead of an internal source ID.

## Tests
- `tests/test_v6_20_5_forecast_followup.py` now has 29 tests. The 6 new ones cover the six points above, and 5 of them fail on v6.20.5. The connection test is a guard; it can't fail on Linux.
- The full test folder shows the same 30 failures and 7 collection errors as before v6.16. None of them are new.
- The installer was tested on your v6.17.3 files and on every version from v6.19 to v6.20.5.

## Install and run (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_6.zip .\_v6_20_6 -Force
python .\_v6_20_6\install.py --project .
python check_setup.py
```
If it prints OK, run Deep.

---

## v6.20.5 (included): forecast follow-up

This adds follow-up on forecasts: each Deep run's implications are recorded, and later runs check whether what they said to watch for has actually been reported. It includes everything in v6.20.4 and installs over v6.15.2 through v6.20.4, including your current v6.17.3. The changes are in `CHANGES_since_v6_20_4.diff`.

## How it works (`services/forecast_followup.py`)
**Method.** This follows established forecasting practice:
- each judgement names indicators or signposts that would show it is coming true, and analysts re-check them over time (CIA Tradecraft Primer, 2009);
- every forecast has resolution criteria and a deadline, and is resolved only on public evidence, never on the forecaster's opinion (IARPA ACE / Good Judgment Project; Tetlock & Gardner, *Superforecasting*, 2015).

**Recording.** After the briefing is checked, each implication is stored in `data/forecasts/watch_items.sqlite3` with:
- what to watch for;
- a deadline from its time frame: "6–12 months" gives 12 months, "next quarter" gives 92 days, "by the end of 2027" gives 31 Dec 2027, and no time frame gives 180 days;
- its conditions and its citations.

The same implication repeated in a later run is stored only once.

**Checking.** Each later Deep run checks the open forecasts:
- **Which sources count:** only news published after the forecast was made, and never the forecast's own sources. A run cannot confirm its own prediction.
- **Pre-filter:** a cheap filter finds the source statements about the same subject, at most 3 per forecast.
- **The check:** the app's usual claim check (the same one used for every shown sentence) asks whether a source states that the signal happened:
  - stated → **observed**, shown with the source and date;
  - the opposite is stated → **contradicted**;
  - deadline passed with neither → **expired**.
- **Failure:** if the check fails (model error), the forecast stays open.
- **Cost:** at most 8 checks per run, which is at most 8 model calls, and nothing when there's no relevant new evidence.

**In the report.** "Follow-ups on earlier forecasts" appears under the implications. It shows each resolved forecast with the reporting source, how many are still open, and a running track record (what share of signals were later reported).
- On your first run it says no earlier forecasts exist yet and that this run's have been recorded.
- Replays never write to the forecast record.
- The log line is `forecast_followup=`.
- To turn it off, set `EVENT_INTELLIGENCE_FORECAST_FOLLOWUP=0`.

## Tests
- New: `tests/test_v6_20_5_forecast_followup.py`, 23 tests. They cover:
  - deadlines from time frames;
  - storing each forecast once;
  - observed with its source;
  - only later news counting;
  - contradicted and expired;
  - a failed check staying open;
  - unsupported staying open;
  - replay and Fast runs never writing;
  - the 8-call cap;
  - the orchestrator recording and then following up across two runs, with the date taken from the event;
  - a failure never breaking the report;
  - the dashboard section.
- The v6.17 and v6.19 test files now give tests their own temporary forecast and memory files, so running the tests never touches your real data.
- The full test folder shows the same 30 failures and 7 collection errors as before v6.16. None of them are new.
- The installer was tested on your v6.17.3 files and on every version from v6.19 to v6.20.4.

## Install and run (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_5.zip .\_v6_20_5 -Force
python .\_v6_20_5\install.py --project .
python -m pytest tests/test_v6_20_5_forecast_followup.py tests/test_v6_20_4_dates_actors_asides.py tests/test_v6_20_3_dedup_gold.py tests/test_v6_20_resolver_memory_nonobvious.py tests/test_v6_19_replay_and_research.py tests/test_v6_17_report_sections.py tests/test_claim_support_gate.py -q
python check_setup.py
```
If `check_setup.py` prints OK, run one Deep research. The first run records forecasts; follow-ups appear from the second Deep run on.

---

## v6.20.4 (included): event dates, recovered actors, page-furniture filter

This builds the three must-fix gaps found by checking your own data. It includes everything in v6.20.3 and installs over v6.15.2 through v6.20.3, including your current v6.17.3. Every change was measured on the last 400 events your app extracted, with no Gemini calls. The changes are in `CHANGES_since_v6_20_3.diff`.

## 1. Every event now has a date
- **Before:** the extractor hard-coded the event date as empty, so all 400 events had none. Every date check (time windows, "cause before effect", dated facts, "this follows what was reported on…", cross-run memory windows) quietly did nothing.
- **Now** (`services/event_dates.py`):
  - **Publication date:** each event gets its article's publish date. All 400 had one available. This is the standard time signal in news clustering.
  - **Event date:** set only when the text pins a day ("Sept. 22", "Tuesday", "yesterday", "3 days ago"). It is worked out from the publication date, the way temporal taggers anchor relative expressions to the document date (TimeML/TempEval, HeidelTime).
  - **Month or year mentions** ("in July", "last month", "2025") are kept at that precision and never become a fake day.
  - **Vague phrases** ("recently", "this summer") stay blank.
- **Result on your 400 events:**
  - 400 got a publication date;
  - 112 got a day-precise event date;
  - 64 got a month and 25 a year.

  I checked every distinct phrase by eye.

## 2. Missing actors recovered from the article's own text
- **Before:** 148 of 400 events (37%) had no actor, and admission drops those.
- **Now** (`services/actor_recovery.py`). Only what the article states is used:
  - **A person's stated employer:** "Nvidia's Jensen Huang" → Nvidia; "Pedro Andrade, vice president of AI at Talkdesk" → Talkdesk.
  - **A stated public office:** "President Donald Trump" → Donald Trump, also for later sentences in the run that say only "Trump".
  - **A country:** "France and Germany pledged…" → France.
  - **A named subject the extractor mistyped:** "Poke launched…" → Poke.
  - **Vague groups stay without an actor:** "executives", "lawmakers", "tech companies".
  - **People with no stated role stay without one too:** "Bill Gates said…".
- **Result:**
  - 72 of the 148 recovered, 69 of them correct (precision **0.96**, hand-checked; labels saved in `tests/data/actor_recovery_labels.json`);
  - events passing admission went from 166 to 212 of 400 before the filter below.

## 3. Page furniture is no longer admitted as events
- **Filtered out** (`services/aside_filter.py`):
  - photo captions ("WASHINGTON, DC - SEPTEMBER 29: … waves", "From left: …", "(left)");
  - promotions ("5 min read", "(* Disclosure below.)", theCUBE interview blurbs, "will discuss at the summit");
  - related links ("Read more: …") and author bios.
- **Kept, with the furniture stripped off:** content that merely starts with a photo credit or a speaker label, e.g. "(Kent Nishimura/AFP via Getty Images) · Amodei published an essay…" or "NERMEEN SHAIKH : On Tuesday, President Trump…". The duplicate detector reads the cleaned text.
- **Result:** 9 of the 212 admitted events were furniture and are now excluded, leaving 203. Real events (acquisitions, funding rounds, launches) are never flagged; there is a test for this.

## Duplicate detection after these changes
With actors recovered, duplicate detection on the labelled pairs gives precision **0.91** and recall **0.79** (v6.20.3: 0.93 / 0.75). The two new merges were checked and labelled.

## Tests
- New: `tests/test_v6_20_4_dates_actors_asides.py`, 40 tests:
  - date phrases taken from your data;
  - the publication-date formats your app stores;
  - run events getting their article date;
  - the actor precision floor on the labelled set, which fails if any recovery changes without being labelled;
  - recovery rules;
  - furniture vs real events.
- The full test folder shows the same 30 failures and 7 collection errors as before v6.16. None of them are new.
- The installer was tested on your v6.17.3 files and on v6.19, v6.20, v6.20.1, v6.20.2 and v6.20.3.

## Install and run (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_4.zip .\_v6_20_4 -Force
python .\_v6_20_4\install.py --project .
python -m pytest tests/test_v6_20_4_dates_actors_asides.py tests/test_v6_20_3_dedup_gold.py tests/test_v6_20_resolver_memory_nonobvious.py tests/test_v6_19_replay_and_research.py tests/test_v6_17_report_sections.py tests/test_claim_support_gate.py -q
python check_setup.py
```
If `check_setup.py` prints OK, run one Deep research and tell me when it's finished. I'll read `data/logs/system.log` (look for `actor_recovery=`, `gap_research`, `non_obviousness`, `editorial_briefing=`) and the report.

## Still to build after the Deep run
- **Forecast follow-up:** record each briefing implication with its watch signal and time frame, and have later runs check them against new events and cross-run memory. It needs memory from at least one run first.
- **Mechanism novelty**, and a scored test set for briefing and insight quality. Both need real Deep output first.

---

## v6.20.3 (included): GPT's v6.20.2 plus a measured duplicate-story resolver

This keeps all of GPT's v6.20.2 fixes and corrects three problems I found by testing v6.20.2 on concrete cases and on your own extracted events. It installs over v6.15.2 through v6.20.2, including your current v6.17.3.

## What was tested, and how
GPT's v6.20.2 tests passed, but its fixtures were written to fit its own rules. So I measured the duplicate-story resolver on **your app's own data**:
- the last 400 events in `data/event_intelligence.db`;
- **93 hand-labelled pairs**: is this the same real-world occurrence or not;
- labels saved in `tests/data/dedup_gold_pairs.json` and checked by `tests/test_v6_20_3_dedup_gold.py` on every install.

| Resolver | Precision (merges that are right) | Recall (true duplicates found) |
|---|---|---|
| v1 (v6.20.1) | 0.38 | 0.46 |
| v2 (GPT v6.20.2) | 0.50 | **0.03** |
| **v2.2 (this build)** | **0.93** | **0.75** |

- **Why both numbers matter.**
  - A wrong merge hides a separate development.
  - A missed merge lets two articles about one story count as "two developments", which lets a fake hidden insight through. v6.20.2's 0.03 recall made this the normal case.
- **Check on run-sized batches:** in batches of about 80 events, like a real run, the result is 0.92 / 0.72.

## Fixes
1. **Duplicate-story resolver v2.2.** Built on the standard methods:
   - **Similarity of wording:** TF-IDF cosine across the run's events, the method used in streaming news clustering (Miranda et al., EMNLP 2018) and in cross-document event coreference, where similarity of the action wording dominates (Bugert et al., 2021).
   - **Time window:** reports more than 31 days apart are never merged.
   - **Argument compatibility:** two reports naming different objects of the action ("acquires Widgetco" vs "acquires Lumen"), or different named subjects of a templated sentence ("Boko spoke with theCUBE…" vs "Singh spoke with theCUBE…"), are different events.
   - **Text cleanup:** publisher tags are stripped ("- SiliconANGLE"), a capitalised first word is not treated as a name, light stemming is applied (euros/euro), and company suffixes are normalised ("OpenAI Group PBC" = "OpenAI").
   - **Thresholds:** chosen with a sweep (same story ≥ 0.35 / 0.40 / 0.45 / 0.50; same actor and type ≥ 0.15 / 0.20 / 0.25). The chosen 0.40 / 0.20 had the best precision–recall balance once every new merge was labelled.
   - **IDs:** still stable as reports arrive, including an older report from a different week. Ties no longer depend on row order.
2. **A failed hidden insight is shown as a connection only if it really spans two developments.**
   - **The problem in v6.20.2:** the downgrade relied on the model's `distinct_event_count`, which counts article rows. Two articles about one story therefore passed. GPT's test only passed because it set that count to 1 by hand.
   - **Now:**
     - a finding the non-obviousness test traced to one development is never shown as a connection;
     - any downgraded finding needs supporting evidence from two different underlying events.
   - **Tests:** the new tests fail on v6.20.2 and pass here.
3. **Memory never splits one event, and never merges two.**
   - **The problem in v6.20.2:** memory used its own anchor guess. It could give a new row to an event whose resolver ID was already stored, and it decided row by row, so one event's reports could be split across rows.
   - **Now:**
     - one decision is made per event per run;
     - an ID that is already stored is reused when within 31 days;
     - the same ID more than 31 days apart becomes a dated repeat occurrence;
     - a different ID is aliased only for the same actor, the same named target and overlapping wording.

## Kept from GPT's v6.20.2 (checked)
- Replay never writes to memory.
- Checkpoint v2 records the resolver version, and replay recomputes event IDs.
- Orchestrator version strings identify the build. This one is `v3.26.4.7-v6203-dedup-measured`.
- Every v6.20.1 fix: gap facts reach the analysis, the Google challenge search, replay without collection, and mandatory "could be wrong if".

## Tests
- 215 tests pass in the eight v6.16–v6.20.3 test files.
- The full test folder shows the same 30 failures and 7 collection errors as before v6.16. None of them are new.
- The installer was tested on your v6.17.3 files, on v6.19, v6.20, v6.20.1 and GPT's v6.20.2. The full suite was run on the copy produced by the installer.

## What has not been proven yet (needs your Deep run)
- Whether Google-grounded search returns usable past-case and policy pages for your topic. `check_setup.py` tests the plumbing with one call.
- The quality of the briefing and the hidden insight on live news.

## Install and run (project folder, venv active, app stopped)
```powershell
Expand-Archive .\event_intelligence_v6_20_3.zip .\_v6_20_3 -Force
python .\_v6_20_3\install.py --project .
python -m pytest tests/test_v6_20_3_dedup_gold.py tests/test_v6_20_resolver_memory_nonobvious.py tests/test_v6_19_replay_and_research.py tests/test_v6_17_report_sections.py tests/test_claim_support_gate.py -q
python check_setup.py
```
If `check_setup.py` prints OK, run one Deep research. Then tell me, and I'll read `data/logs/system.log` and the report.

---

## v6.20.2 (GPT, included) — objective-integrity release

This package keeps the full v6.19/v6.20 product behavior and closes the remaining deterministic integrity defects before the live Deep validation run.

## Product objective enforced by this build

A Deep run is intended to perform this chain:

1. collect and admit current developments;
2. merge multiple reports of the same real-world occurrence without merging different occurrences;
3. research missing **precedent, policy/incentive, dependency and numeric context before strategic inference**;
4. feed that context into strategic reasoning and search contrary evidence with a separate bounded Google-grounded budget;
5. produce the four-section report contract:
   - **Event Landscape** — always present;
   - **Forward Briefing** — always present, with grounded precedent, leverage, policy/incentive-fit or trend implications where justified; every implication must include a grounded condition that could make it wrong;
   - **Historical Analogy** — only when a dated historical case with a source-reported outcome is established;
   - **Hidden cross-actor insight** — only when the conclusion genuinely needs multiple underlying developments and is not already stated by one source/headline/development; otherwise the section says `None this run` and a connection is shown only if it independently passes connection rules.
6. save trustworthy underlying events for later-run memory. Replays are read-only: they reuse the checkpoint and do not collect new news or write another memory observation.

## v6.20.2 integrity fixes

### Duplicate-story identity

`UnderlyingEventResolver` is now `underlying-event-resolver-v2`.

- actor names and generic action/family words do not count as distinctive overlap;
- same actor + same event type + same date is not enough to merge two stories;
- different same-day targets such as `Acme acquires Widgetco` and `Acme acquires Lumen` remain different occurrences;
- complete linkage prevents transitive A~B~C chaining;
- the persistent ID is based on occurrence facts rather than EVENT row IDs, report count or earliest report week;
- adding another report, including an older report across a week boundary, does not rename the same occurrence in the regression cases;
- repeated actions involving the same actor and same counterparty months apart remain separate developments, with cross-run memory assigning a distinct persistent occurrence ID.

### Cross-run memory

`EventMemoryService` is now `event-memory-v2`.

- it uses the canonical resolver ID;
- its migration safety-net requires the same explicit occurrence anchor before aliasing an old ID, so common context such as `robotics/platform/warehouse` cannot merge a different target;
- replay never calls the memory writer.

### Replay and checkpoints

- checkpoint contract is `pre-analysis-checkpoint-v2`;
- v1 checkpoints remain readable;
- replay recomputes underlying-event IDs locally with the current resolver instead of trusting older resolver output;
- resolver provenance is written to the checkpoint;
- strategic collection rescue and source discovery remain disabled during replay;
- replay does not create a new cross-run memory observation.

### Hidden-insight downgrade

A failed non-obviousness test is no longer automatically relabelled as a verified connection. The downgraded candidate must separately pass the normal strategic-connection release rules. A conclusion supported by only one underlying development therefore cannot be displayed as a “connection between developments.”

## v6.20.1 behavior retained

- gap-research facts are added to StrategicInference on top of the normal event-evidence packet (bounded to eight context records);
- early and final contrary-evidence research share the separate six-prompt Google-grounded challenge budget;
- replays cannot enter fresh collection/extraction rescue;
- a forward implication without a non-empty, grounded `conditions` / “could be wrong if” field is removed;
- official policy pages without publication metadata may be used only when the page itself contains an exact, validated effective/in-force date statement;
- pre-analysis gap research runs in the current app configuration and logs `gap_research remaining_seconds_at_gap_start=`.

## v6.20 capabilities retained

- canonical duplicate-story resolution shared by the orchestrator and StrategicInference;
- local cross-run event memory for Deep runs;
- deterministic non-obviousness gate for Section 4;
- Google-grounded gap research;
- `check_setup.py` one-call setup verification;
- pre-analysis checkpoints and `replay_run.py` for cheaper analysis/report replays.

## Install

From the project root with the virtual environment active and Streamlit stopped:

```powershell
Expand-Archive .\event_intelligence_v6_20_2.zip .\_v6_20_2 -Force
python .\_v6_20_2\install.py --project .
python -m pytest tests/test_v6_20_resolver_memory_nonobvious.py tests/test_v6_19_replay_and_research.py tests/test_v6_17_report_sections.py tests/test_v6_16_developments.py tests/test_claim_support_gate.py tests/test_cluster_expansion.py tests/test_development_clusters.py -q
python check_setup.py
```

If `check_setup.py` prints `OK`, run one **Deep** research query. That live run is the validation of external Google-grounded source availability and real-world output usefulness; it is not another prerequisite for the code paths above to exist.

## What to inspect after the Deep run

The log/report should make it possible to distinguish retrieval, integration and reasoning failures. In particular inspect:

- `gap_research remaining_seconds_at_gap_start=`
- `gap_research=` / gap status for precedent, policy, dependency and numbers
- grounded-search prompt/query/failure counts
- `editorial_briefing=`
- non-obviousness / hidden-insight release decisions
- `event_memory` entries and resolver version

The intended acceptance criterion is not merely “a report was returned.” The run should produce specific, source-traceable strategic reasoning when evidence supports it, and abstain honestly when it does not.
