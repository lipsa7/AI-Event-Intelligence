# Event Intelligence app: gap audit (read-only, 23 Oct 2026)

Method: read code and docs; read local data with venv Python in read-only mode (`sqlite3 ... ?mode=ro`). Nothing was run, installed or fetched. "Source" links below are local file paths, with line numbers where they apply. Data figures come from my own queries of the named files.

## 1. What the spec, research doc and README promise vs. what the code delivers

### Takeaway
The spec promises forward strategic reasoning (precedent, leverage plus incentives, hidden cross-actor links), a report that is never blank, and expert views that are always shown. What the app delivers is a fully gated pipeline in which almost every inferential element is removed or labelled "not supported". Every run since 9 Oct ended "abstained". Expert commentary and analogies are almost always empty.

### Cited Findings
- The spec's objective is forward reasoning in three forms: precedent, leverage and hidden connections. Its stated bar is "you see the leverage for a thing and add incentives". — [docs/PRODUCT_SPEC.md:4-10](docs/PRODUCT_SPEC.md)
- The spec says: "Never a blank report: landscape and briefing always; implications every run". It also says "what credible experts or orgs say can always be shown". — [docs/PRODUCT_SPEC.md:11-13, 35-41](docs/PRODUCT_SPEC.md)
- The spec says precedents are "searched for the top 3 developments in every Deep run", and that a precedent counts only with a dated, sourced case that has a quoted outcome and a stated key difference. — [docs/PRODUCT_SPEC.md:56](docs/PRODUCT_SPEC.md)
- The spec promises an expert frontier (v6.22): one expert-commentary search per development, with the report quoting "What experts are already saying". — [docs/PRODUCT_SPEC.md:168](docs/PRODUCT_SPEC.md)
- RESEARCH_IMPLEMENTATION.md states its own limits openly. It is "not evidence that the product's advice has improved". Offline tests "cover application behavior, not real-world insight quality". It also recommends building a small fixed human-judged example set "before spending on three more full replays". — [docs/RESEARCH_IMPLEMENTATION.md:3, 47-51](docs/RESEARCH_IMPLEMENTATION.md)
- RESEARCH_IMPLEMENTATION.md also says unsupported bridges "cannot earn mechanism support … merely because they are labelled assumptions". By design, an original inferential step therefore cannot be credited. — [docs/RESEARCH_IMPLEMENTATION.md:23](docs/RESEARCH_IMPLEMENTATION.md)
- Delivered results, from data/reports.sqlite3 (65 reports; current revision payload_json): all 20 reports from 9 Oct 12:41 through 23 Oct 07:06 have research_outcome "abstained" or "strategic_finding_released". After 10 Oct, every one is "abstained". hidden_insights=0 in every report after 5 Oct. — [data/reports.sqlite3](data/reports.sqlite3)
- In the latest report (REPORT-02404…, 23 Oct 07:06), the editorial briefing has headline="", analogies=0, case_count=0 and expert_commentary=0. Four sentences were removed by the check. — [data/reports.sqlite3](data/reports.sqlite3)
- In that same report, policy_fit.advised_organisation_addressed=false for the advised organisation OpenAI. — [data/reports.sqlite3](data/reports.sqlite3)
- Across the 20 reports after 9 Oct, expert_commentary was non-empty only twice (D6D88 and 30CAC, 1 item each). Analogies were non-empty only 3 times (1 item each). — [data/reports.sqlite3](data/reports.sqlite3)
- Three reports on 23 Oct (C91FC, 5B79F, CCF82) have briefing implications=0, lab raw_count=0 and evaluator eligible=0. In practice these are blank analysis reports. 5B79F and CCF82 have the same payload length (885,390 bytes), and their logs show `GeminiCreditsDepletedError`. — [data/reports.sqlite3](data/reports.sqlite3); [data/logs/system.log.*](data/logs/) (`elapsed=20.827s reasons=['strategic_model_unavailable:GeminiCreditsDepletedError']`)
- The README section list still contains "Planned Baseline Evaluation", "Why There Is No Demo Screenshot Yet" and "Under active validation". — [README.md](README.md) (section headers)

### Inferences
- The promise "never blank / experts always shown" fails whenever a model or search fails, and it also fails when every search returns nothing. The fallback shows source statements, not insight.
- The spec has turned into a changelog of 49 distinct v6.x versions (see section 5). The objective is restated, but no measurable acceptance target for "insight quality" exists.

### Gaps
- I did not read PRODUCT_SPEC end-to-end; I used a header and keyword sweep. The README's body text was not audited line by line.

## 2. Pipeline stages, hard filters, stacked gates, and which ones reject inferential claims

### Takeaway
Collection → extraction → developments → gap research → briefing → actor moves → hypothesis lab → claim gate / non-obviousness / final quality → evaluator → dashboard. Each stage grounds its claims against the source text. A forward-looking move, mechanism or leverage claim is "unsupported" almost by definition, because sources report what happened, not what an actor should do next. I count at least 7 stacked gates on the hidden-insight path, and 16 fail-closed sites in the orchestrator.

### Cited Findings
- **Orchestrator size.** research_orchestrator_service.py is 971,543 bytes, 23,259 lines and 289 methods. SERVICE_VERSION is "research-orchestrator-service-v3.31.0-research-substance-ner". — [services/research_orchestrator_service.py:29](services/research_orchestrator_service.py)
- **Order of the report-time stages.** `_attach_spec_sections` runs developments → (fallback precedent research) → `_editorial_briefing` → `_actor_moves` → `_strategic_hypothesis_lab` → historical_analogies, which is copied from briefing.analogies → forecast followups. — [services/research_orchestrator_service.py:3894-3960](services/research_orchestrator_service.py)
- **Claim support gate v2.0.** Hidden_insight and mechanism claims need 2 or more independent sources. A mechanism step that is "unsupported" or "partially_supported" becomes an assumption only if all of the following hold:
  - convergence is met, meaning at least 3 events and at least 2 publishers;
  - there are no other failure reasons;
  - nothing is contradicted;
  - there are at most 3 assumptions.

  Otherwise every assumption becomes a failure, with "unsourced_steps_without_convergent_observations" or "too_many_unverified_assumptions". — [services/claim_support_gate.py:72-97, 169-187, 245-280](services/claim_support_gate.py)
- **Non-obviousness (v6.20).** Four structural checks, any of which fails the insight:
  - the conclusion must not restate a source;
  - its support must span 2 or more developments;
  - a headline-level takeaway must not already state it;
  - no single development may support it alone.

  The check fails closed. — [docs/PRODUCT_SPEC.md:91](docs/PRODUCT_SPEC.md); [services/research_orchestrator_service.py:16791-16825](services/research_orchestrator_service.py)
- **Further gates:**
  - the expert frontier, fail closed: an insight must not be implied by expert commentary ([docs/PRODUCT_SPEC.md:168](docs/PRODUCT_SPEC.md));
  - counterevidence quality ([docs/PRODUCT_SPEC.md:137](docs/PRODUCT_SPEC.md));
  - `_final_quality_assessment` ([services/research_orchestrator_service.py:19021](services/research_orchestrator_service.py));
  - `_claim_supported_release` ([services/research_orchestrator_service.py:17311](services/research_orchestrator_service.py));
  - policy enforcement ([services/policy_enforcement_service.py](services/policy_enforcement_service.py), 91 KB).

  The orchestrator contains 16 "fail closed" sites. — grep count of services/research_orchestrator_service.py
- **Actor moves.** Each actor's crux, powers, outside option, holder dependence, other players' moves and choice is grounded as a "connection" and must reach MOVE_BAR {"supported","inference_supported"}. Rules, incentives and "must be true" items need FACT_BAR {"supported"}. Anything that fails is moved into `removed`. — [services/actor_moves.py:42-43, 420-476, 510-576](services/actor_moves.py)
- **Removal counts from actor moves.** The removed list had 12, 24, 7, 7, 6, 8 and 6 entries in the reports from 10 Oct to 23 Oct. In the latest report all 6 removals concern OpenAI and the Australian taskforce: crux, leverage, choice and move were all "not supported", including "Ability to steer the taskforce's conclusion…". — [data/reports.sqlite3](data/reports.sqlite3) REPORT-02404 sections.actor_moves.removed
- **Abstention reasons.** Final rejection reasons across reports since 3 Oct:

  | Reason | Count |
  |---|---|
  | mechanism_step_not_supported | 15 |
  | conclusion_not_supported | 15 |
  | premise_not_supported | 13 |
  | unsourced_steps_without_convergent_observations | 9 |
  | grounding_unavailable | 3 |
  | GeminiCreditsDepleted | 2 |
  | too_many_unverified_assumptions | 1 |

  — [data/reports.sqlite3](data/reports.sqlite3) sections.abstention.final_rejection_reasons
- **Editorial briefing.** In the latest report, sentences_removed_by_check=4 and 3 implications survived. — [services/editorial_briefing.py:769](services/editorial_briefing.py); [data/reports.sqlite3](data/reports.sqlite3)

### Inferences
- The gates that reject inferential claims are these:
  - claim-gate mechanism and conclusion checks;
  - actor-moves MOVE_BAR;
  - the lab's -MOVE, -COUNTER, -ALTERNATIVES and -ASYMMETRY "connection" claims;
  - briefing sentence removal.

  Each of them asks a grounding model whether a source states the inference. The spec says original insights are allowed with labels, but the gates delete or fail them.
- Gates are stacked conjunctively, so the pass rate is the product of the individual pass rates. With 7 or more gates, a zero hidden-insight rate is the expected result, not an anomaly.

### Gaps
- The orchestrator is too large to trace fully, so the exact count of distinct gates on the strategic-finding (non-hidden) path was not established.

## 3. Verification of the 10 known findings

### Takeaway
All 10 are confirmed by the data. Where possible I added counts.

### Cited Findings
1. **Single checkpoint replayed.** data/checkpoints holds only RUN-ED24092548084234B2300689.json, saved 2026-10-09T12:33, with 63 events and 191 evidence records.
   - Every report from 9 Oct 12:41 onwards shares 185 of its 185-195 evidence IDs with that checkpoint.
   - Reports before 9 Oct overlap with it 0-12 times, so they used live news.
   - Reports from 22 and 23 Oct record `checkpoint=RUN-ED2409…`.
   - The logs show `checkpoints\\RUN-ED24092548084234B2300689.json` loaded 4 times on 23 Oct.

   — [data/checkpoints/](data/checkpoints/); [data/reports.sqlite3](data/reports.sqlite3); [data/logs/system.log.*](data/logs/)
2. **Unused stores.** data/event_intelligence.db holds articles=448, events=1502, entities=0, relationships=0 and relationship_observations=0; the file was last modified 9 Oct 18:48. data/event_memory.sqlite3 holds 58 events and 162 excerpts, all from run_id RUN-ED2409…. data/knowledge_graph/knowledge_graph.json has entities=0 and relationships=0, with schema 17. — [data/event_intelligence.db](data/event_intelligence.db); [data/event_memory.sqlite3](data/event_memory.sqlite3); [data/knowledge_graph/knowledge_graph.json](data/knowledge_graph/knowledge_graph.json)
3. **Original mechanism steps rejected.** Confirmed at [services/claim_support_gate.py:245-280](services/claim_support_gate.py) (MAX_KEY_ASSUMPTIONS=3 at line 79; CONVERGENCE_MIN_EVENTS=3 and _PUBLISHERS=2 at lines 75-76). "unsourced_steps_without_convergent_observations" appears 9 times in final rejection reasons.
4. **Early published insights had unsupported mechanisms.**
   - REPORT-EE08E (4 Oct) published a hidden insight with mechanism 4/4 "unsupported" and second-order effect "unsupported"; only its 4 facts were supported.
   - REPORT-16D77 (5 Oct 01:06) had mechanism 3/3 "unsupported".
   - REPORT-3C6F2 (5 Oct 01:42) had mechanism 3/3 "unsupported".
   - The gate field in grounding_by_insight is None in all three, so they predate the per-step gate.
   - Example text: "The transition of AI safety from pre-training alignment to runtime gating creates a structural market bifurcation…"

   — [data/reports.sqlite3](data/reports.sqlite3) sections.grounding_by_insight
5. **Bare fact IDs grounded as claims.** In the latest lab, promising_unverified items have actor_move_basis = "F7", "F16", "F5" and countermove_basis = "F18", "F17", "F4". These are grounded literally as claims LAB-EV2-MOVEBASIS "F7" (fact, unsupported) and LAB-EV2-COUNTERBASIS "F18" (unsupported). The code inserts the basis text verbatim as a fact claim ([services/strategic_hypothesis_lab.py:605-609](services/strategic_hypothesis_lab.py)). The fact IDs are never resolved to their text.
6. **Expert commentary misclassified.** [services/hypothesis_evidence_service.py:730-733](services/hypothesis_evidence_service.py) maps a commentary-search record to gap_type "commentary" only if claim_kind is in {analysis, forecast}; every other kind becomes "reported". Credible-tier commentary is additionally required. Latest briefing: expert_commentary=0 and novelty_check.expert_commentary_records=0.
7. **Focus research comes too late.** `_actor_moves` calls `self._focus_research(focus["name"], …)` ([services/research_orchestrator_service.py:3340-3372](services/research_orchestrator_service.py)), and it is invoked after `_editorial_briefing` ([services/research_orchestrator_service.py:3915-3933](services/research_orchestrator_service.py)). So the briefing never sees focus-organisation precedents.
   - Latest focus research ran queries including "capital one 2019 aws breach notification timeline" and "solarwinds 2020 breach customer disclosure sec".
   - It returned records=0, past_cases=1, status "incomplete".
8. **No human ratings.** There is no data/insight_ratings.jsonl, although services/insight_ratings.py:32 expects it there. data/user_feedback.sqlite3 has 0 events. tests/data/insight_gold.json (insight-gold-v1.1) holds only 7 items and 2 human pairs.
9. **Off-target precedent queries.** On 23 Oct the logs show "epa 2015 waters of the united states rule impact" 10 times, and epa.gov WOTUS pages fetched and "reviewed" for the AI-rename development. The lab research queries were repeated 3 times each, for example "EU AI Act regulatory fine delayed reporting safety incident liability". — [data/logs/system.log.*](data/logs/); [data/research_receipts/RUN-3BF77758CFBE4FE985877DFA.json](data/research_receipts/RUN-3BF77758CFBE4FE985877DFA.json)
10. **Fetch failures on grounding redirects.** Of 51 gap-research sources on 23 Oct, 21 were fetch_failed, 13 not_reviewed and 17 reviewed.
    - All 21 fetch failures are on vertexaisearch.cloud.google.com, i.e. the grounding-api-redirect URLs.
    - Failure reasons: "unusable article text (N chars)" 12, missing date or source 6, "no article text" 5, HTTP 403 5, DNS 2, SSL 2.
    - Precedent sources: 7 failed, 3 not reviewed, 9 reviewed.

    — [data/logs/system.log.*](data/logs/) gap_research_sources lines

### Inferences
- Findings 1 and 2 together mean the last two weeks of tuning (v6.36.x) were done on 63 events from one news snapshot. Any "improvement" may be overfitting to that snapshot.
- Fixing finding 5 alone would not fix the lab, because the move and countermove "connection" claims also fail (finding 3 logic).

### Gaps
- The logs only cover 23 Oct 11:16-12:36, because of rotation. Fetch-failure rates for earlier runs are not available from the logs.

## 4. Additional gaps found

### Takeaway
Six more problems:
- the lab prompt truncates facts;
- the "verified" lab hypothesis is a restated fact;
- evaluator scores cluster at the extremes and rank trivial items first;
- a development title is a sentence fragment;
- rate limits and credit exhaustion hit the research providers;
- a single run costs about 33 minutes and over 1.3M prompt tokens.

### Cited Findings
- **Lab prompt truncation.** `_facts_text` calls `fact_rows(facts)` with the default limit=40, so the generation prompt sees at most 40 facts. Validation and lookup use limit=200 or 250. The checkpoint has 191 evidence records. — [services/strategic_hypothesis_lab.py:134, 200, 240, 262-264, 425, 504, 733](services/strategic_hypothesis_lab.py)
- **"Verified" lab hypothesis is a restated fact.** The only verified lab hypothesis in the latest report has likely_actor_move "The executive branch switches US government official communications to use the term 'super intelligence'". That move is the reported event itself, and its basis restates the announcement. The leverage_test is None. — [data/reports.sqlite3](data/reports.sqlite3) REPORT-02404 strategic_hypotheses.verified
- **Evaluator ranking.**
  - Latest scores are [10, 10, 3, 3, 1, 0, 0].
  - The two 10s are both the AI "super intelligence" rename terminology-clash item, derived and cross_event.
  - Both "strategic_action" items (what OpenAI and the Australian taskforce should do) score 0.
  - Earlier distributions: [7,7,7,7,7,7,2] on 23 Oct 05:25, and [9,7,7,7,7,7,7,5,1] on 22 Oct.
  - Only 1 pairwise comparison ran in the latest run.

  The rubric is 12 yes/no checks ([services/insight_evaluator.py:396-411](services/insight_evaluator.py)). — [data/reports.sqlite3](data/reports.sqlite3) sections.insight_evaluation
- **Development title fragment.** One development on 23 Oct is titled "The European Union has required them since August 2025, and since 2 Au…". — [data/logs/system.log.*](data/logs/) gap_research line
- **Rate limits.** On 23 Oct:
  - newsapi was RATE_LIMITED with NewsAPIQuotaError and status "disabled" (64 provider-status entries);
  - "gemini_search" appears with RATE_LIMITED 120 times;
  - gdelt 12 times.

  — [data/logs/system.log.*](data/logs/)
- **Credit exhaustion.** "prepaid credits are depleted" 402 RESOURCE_EXHAUSTED messages appear 38 or more times on 23 Oct. Two runs ended in about 21 seconds with 0 successful calls: RUN-A4EB57… had 11 failed flash and 4 failed pro calls. — [data/logs/system.log.*](data/logs/)
- **Cost and latency.** RUN-3BF77… made these Gemini calls:

  | Model | Calls | Prompt tokens |
  |---|---|---|
  | gemini-3.5-flash-lite | 167 | 877K |
  | gemini-3.6-flash | 75 | 162K |
  | gemini-3.1-pro-preview | 10 | 168K prompt / 36K output / 57K thinking |
  | others | 2 or more | — |

  Total is about 1.27M or more prompt tokens and about 253 or more calls. The run took elapsed=1991.849s, about 33 minutes, and still abstained. Another run took 1111.8s and abstained. — [data/logs/system.log.*](data/logs/)
- **Duplicate runs.** Seven replays of the same checkpoint ran in about 18 hours (22 Oct 13:06 → 23 Oct 07:06). — [data/reports.sqlite3](data/reports.sqlite3)

### Inferences
- The evaluator rewards whatever is closest to source text, such as the rename item, and punishes the actor-action items the product exists to produce. Ranking is anti-correlated with the objective.
- The 40-fact cap makes it likely that the lab's cross-actor connections miss most of the evidence. This is consistent with only 13-20 raw hypotheses per run.

### Gaps
- No per-call dollar cost is logged, so tokens are the only cost proxy.
- I could not verify why scores cluster, i.e. whether this is rubric design or judge behaviour, without running the judge.

## 5. Process and measurement issues

### Takeaway
Development has been patch-driven, following a "GPT review → version bump" loop with no external quality metric:
- 49 distinct v6.x versions are in the spec, 11 of them tagged "GPT review";
- 92 "# v6." patch comments are in the orchestrator;
- 4 backup snapshots and 3 patch zips are in the repo root;
- only 2 git commits exist.

### Cited Findings
- **Spec versions.** docs/PRODUCT_SPEC.md references 49 distinct v6.x versions, from v6.17 to v6.36.7, and "GPT review" 11 times. — grep of [docs/PRODUCT_SPEC.md](docs/PRODUCT_SPEC.md)
- **Patch comments.** research_orchestrator_service.py contains 92 "# v6." comments. Its uncommitted diff against HEAD is +443/-15 lines. — [services/research_orchestrator_service.py](services/research_orchestrator_service.py)
- **Backups and patches.**
  - v6_backups/ holds 4 snapshots from 22-23 Oct (v6_35_2, v6_36_4, v6_36_7, research_ner_update).
  - The root holds 3 patch zips (event_intelligence_*_patch.zip, research_ner_update.zip).
  - git status shows the deleted _v6_27_1/ tree with CHANGES_since_* diffs.

  — repository root listing; gitStatus
- **Git history.** Only 2 commits exist: 26 Aug and 11 Oct. — `git log`
- **Tests.** There are 104 files in tests/. RESEARCH_IMPLEMENTATION says "The full legacy suite has not been certified" and records a known failing version assertion. — [docs/RESEARCH_IMPLEMENTATION.md:47](docs/RESEARCH_IMPLEMENTATION.md)
- **Human measurement.** 0 ratings, 0 feedback events, and a gold set of 7 items with 2 pairs (section 3, finding 8). The spec says labels are "calibrated against the user's own verdicts (tests/data/insight_gold.json)". — [docs/PRODUCT_SPEC.md:25](docs/PRODUCT_SPEC.md)

### Inferences
- Each review adds a constraint and none removes one, so the gate count only grows.
- Without a human verdict set, "progress" is measured by gate pass/fail. That metric is itself the cause of the zero outputs, a circular measurement.

### Gaps
- Commit-level history of when each gate was added is unavailable, because only 2 commits exist.

## 6. Why the objective has not been achieved: root causes ranked by impact

### Takeaway
In ranked order:
1. An architecture that requires sources to state the inference.
2. Stale and narrow input.
3. Research that fails to fetch or targets the wrong material.
4. No human ground truth.
5. Patch-driven process growth.

### Cited Findings
1. **Verification model contradicts the objective (architecture).** Original mechanism, move and leverage steps are graded by "does a source state this?" (claim gate 245-280; actor_moves MOVE_BAR at 42-43; lab connection claims at 590-615). The results: 0 hidden insights after 5 Oct, 0/20 released hidden insights after 9 Oct, and actor-move items removed in every run. The pre-gate insights of 4-5 Oct passed only because mechanisms were not gated. — sections 2 and 3
2. **No new data (process).** Two weeks of replays of one 63-event checkpoint. 1,502 events and 448 articles in event_intelligence.db, plus an empty knowledge graph, are unused. Cross-actor and precedent material therefore cannot grow. — section 3 findings 1 and 2
3. **Research loses good material (architecture and ops).**
   - 41% of gap sources fail to fetch (vertexaisearch redirects), and 25% are not reviewed.
   - Precedent queries go off-target (EPA WOTUS).
   - Commentary is reclassified as "reported".
   - Focus-organisation research runs after the briefing.
   - The lab sees 40 of 191 facts.
   - Fact-ID bases are grounded literally.
   - NewsAPI quota and Gemini credits run out.

   — sections 3 and 4
4. **No ground-truth measurement (measurement).** There are 0 human ratings. The evaluator ranks a terminology-clash item 10 and recommended actions 0. Success is defined by internal gates, so nobody can tell whether any change improves usefulness. — sections 4 and 5
5. **Process (patch loop).** 49 versions and a 970 KB orchestrator, with each review adding constraints. Runs cost about 33 minutes and about 1.3M tokens each, and the next replay is still on the same checkpoint. — section 5

### Inferences
- The smallest change with the highest leverage is a separate, labelled "analysis" tier. In that tier, facts must be supported and the inference only needs to be not contradicted and plausibly entailed, plus human rating of that tier. Without it, adding sources or models will not change the outcome under the current gates. This is my inference from the evidence; it was not tested.

### Gaps
- No test isolates the per-gate drop rate. A per-stage funnel (candidates in, passed, out) is not logged in a single place, so the relative contribution of each gate is estimated, not measured.
