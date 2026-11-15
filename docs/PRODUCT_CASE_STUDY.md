# AI Event Intelligence

### From fragmented news to evidence-grounded strategic intelligence

## Overview

AI Event Intelligence is a portfolio product exploring a problem that appears across strategy, investing, competitive intelligence and executive decision-making:

> **People have access to enormous amounts of information, but still have to manually connect developments, test whether those connections are meaningful, understand second-order effects and decide what actually matters.**

Most news and AI research products are already good at answering questions such as:

* What happened?
* What are the latest developments?
* Can you summarize these articles?
* What are the major themes?

AI Event Intelligence is designed to go further:

* Why does this development matter?
* What other events does it connect to?
* Is the connection meaningful or merely coincidental?
* What mechanism might explain it?
* What evidence supports the interpretation?
* What is still unknown?
* What could happen next?
* What evidence would weaken the hypothesis?
* What should the user monitor going forward?

The long-term product direction is:

**News Intelligence → Strategic Intelligence → Continuous Decision Intelligence**

---

# 1. The Problem

Executives, analysts, investors, product leaders and strategy teams monitor information across:

* companies;
* competitors;
* technologies;
* regulation;
* supply chains;
* capital markets;
* infrastructure;
* customers;
* geopolitics.

The problem is rarely the absence of information.

The harder problem is synthesis.

Imagine four separate developments:

1. A hyperscaler signs a long-term energy agreement.
2. Data-center capital expenditure rises sharply.
3. Grid constraints begin delaying new projects.
4. Infrastructure-financing structures begin changing.

Each event is useful independently.

But a strategist may ask:

> Are these separate developments, or are they evidence of a deeper structural transition in how AI infrastructure is being built?

Answering that requires much more than summarization.

A human analyst must:

1. notice the possible relationship;
2. determine whether it is specific or generic;
3. identify the mechanism connecting the events;
4. gather supporting evidence;
5. investigate missing evidence;
6. consider competing explanations;
7. determine how much confidence is justified;
8. identify what should be monitored next.

The product is designed to reduce this research and synthesis burden.

---

# 2. Product Hypothesis

The central product hypothesis is:

> **Connecting events across sources, actors, mechanisms, domains and time can create substantially more decision value than summarizing those events independently.**

The product therefore treats an article as raw material rather than the primary unit of value.

The intended analytical progression is:

**Article**

→ **Structured Event**

→ **Evidence**

→ **Relationships**

→ **Candidate Pattern**

→ **Strategic Hypothesis**

→ **Evidence Validation**

→ **Governed Insight**

The long-term research architecture extends this further with stronger disconfirmation and structural-analysis techniques.

---

# 3. Target User

The initial target user is a professional who already spends meaningful time researching external developments.

Potential initial segments include:

* strategy analysts;
* competitive-intelligence professionals;
* investment/research analysts;
* product strategy teams;
* corporate-development teams.

A broader use case exists for sophisticated news consumers, but the initial product hypothesis is stronger for professional users whose work already requires cross-source synthesis.

## Job to Be Done

> **When important external developments are occurring, help me understand their strategic implications without manually researching and connecting dozens of fragmented sources.**

The product is currently a prototype, so this target segment remains a hypothesis to validate through structured user research rather than a claim of product-market fit.

---

# 4. Initial MVP

The first version followed a relatively simple AI research pipeline:

**Topic**

→ discover articles

→ extract events and entities

→ identify relationships

→ build structured context

→ generate a strategic report.

The report could include areas such as:

* executive summary;
* economic impact;
* policy and regulation;
* supply-chain implications;
* technology;
* market impact;
* risks;
* opportunities;
* scenarios;
* recommendations.

Technically, the pipeline could run.

But the output revealed a much more important product problem.

---

# 5. The First Major Product Failure

The system could generate reports that sounded intelligent while providing little incremental intelligence.

Weak outputs were equivalent to:

> AI investment is increasing.

> Regulation may create uncertainty.

> Companies will need to adapt.

These statements may be true.

They may even be well-grounded.

But they are not necessarily useful.

This led to one of the most important evaluation questions in the product:

> **Could this insight have been written without these particular events?**

If the answer is yes, then the system has probably summarized a broad theme rather than synthesized a meaningful event-specific insight.

That changed the product priority.

The goal stopped being:

> Generate a complete report.

The goal became:

> **Generate at least one genuinely useful and defensible strategic insight.**

---

# 6. The Insight Became the Core Product Object

A major design decision was to stop treating the report as the primary unit of analytical value.

The **Insight** became the core object.

A report may contain several insights, but each candidate insight can be structured and evaluated independently.

The implemented candidate schema includes concepts such as:

* insight type;
* premises;
* mechanism;
* feasibility;
* supporting evidence IDs;
* counterevidence IDs;
* counter-incentive evidence IDs;
* alternative explanations;
* critical unknowns;
* event-specific anchors;
* cluster coherence information;
* disconfirming conditions;
* revision/refinement lineage.

This made it possible to evaluate individual strategic claims instead of asking whether an entire report merely “looks good.”

---

# 7. Generation Is Not Release

Generative AI naturally tends toward answering every question.

That behavior is dangerous in strategic intelligence.

The product therefore separates:

**candidate generation**

from

**publishability.**

A candidate insight can fail minimum publishability requirements and never become a released finding.

This establishes an important product principle:

> **No strategic insight is better than a persuasive but weakly supported strategic insight.**

The system therefore supports abstention.

A run can process sources, events and evidence yet still conclude that no candidate deserves publication.

That is treated as an intended product outcome rather than merely a system failure.

---

# 8. Tiered Findings

Not every candidate needs to be treated as equally strong.

The product uses tiered findings and publishability gating so that outputs can reflect different levels of analytical maturity.

The broader principle is:

> **The system should communicate the epistemic state of an insight rather than flattening everything into equally confident prose.**

A candidate that has good structure but unresolved evidence gaps should not be presented as equivalent to a strongly grounded finding.

---

# 9. Descriptive vs Prospective Insights

The code explicitly distinguishes:

* **descriptive insights**;
* **prospective insights**.

This matters because the evidence burden is different.

A descriptive insight might be:

> Several cloud providers increased data-center investment during the same period.

That can often be directly supported by observed events.

A prospective insight might be:

> Energy control could become an increasingly important strategic differentiator for AI infrastructure companies.

No source may state that exact conclusion.

The system therefore needs to reason from evidence without pretending the inference itself is directly observed.

---

# 10. Evidence Roles for Prospective Reasoning

The current insight structure supports multiple evidence concepts around a candidate.

These include:

### Direct supporting evidence

Evidence supporting a factual premise.

### Mechanism

Why one development may plausibly lead to another.

### Feasibility

Whether the proposed development appears possible.

### Counterevidence

Evidence that weakens or contradicts the hypothesis.

### Counter-incentive evidence

Evidence showing why an actor may not behave in the way hypothesized.

### Alternative explanations

Other explanations that could account for the same observed pattern.

### Critical unknowns

Information that remains unresolved and prevents stronger confidence.

These distinctions allow the product to avoid reducing all reasoning to:

> “I found three articles, therefore the conclusion is true.”

---

# 11. Gap-Driven Evidence Discovery

The research process evolved away from generic topic search.

Instead of simply asking:

> Find more information about this topic.

the system can reason about why a candidate failed or what evidence is still missing, then generate targeted refill queries.

The current implementation includes strategic corpus-refill logic that can:

* identify missing evidence around a candidate;
* generate targeted follow-up queries;
* retrieve additional source material;
* reassess whether the new information materially improves the candidate.

This produces a research loop closer to:

**Candidate**

→ identify gap

→ targeted query

→ fetch source

→ assess materiality

→ update evidence context

→ reassess candidate.

This is one of the major changes from the original summarization-oriented architecture.

---

# 12. Material Information Gain

Not every relevant search result improves an insight.

Suppose the unresolved question is:

> Does owning more infrastructure materially change the economics of AI deployment?

A newly retrieved article saying:

> AI infrastructure spending continues to grow

is related to the topic.

But it does not resolve the missing premise.

The system therefore includes a material-information assessment.

The important distinction is:

**Topical relevance**

does not necessarily equal

**epistemic progress.**

Research should continue only when new information materially contributes to resolving the strategic question.

---

# 13. Bounded Research Rather Than Endless Agent Loops

Agentic research can become inefficient very quickly:

search

→ fetch

→ reason

→ search again

→ fetch again

→ another refinement

→ timeout.

The product therefore treats research as bounded.

The system uses execution-budget controls and can restrict:

* search activity;
* research depth;
* refinement;
* inference calls;
* time allocated to different stages.

The purpose is not to maximize the number of research actions.

It is to maximize useful intelligence within a controlled execution budget.

---

# 14. Fast and Deep Modes

The product supports distinct Fast and Deep paths.

The modes differ primarily in research depth and resource allocation.

## Fast Mode

Designed for lower latency and bounded investigation.

It may skip expensive analytical stages that are not essential to the immediate MVP objective.

## Deep Mode

Allows more extensive research and validation.

It can perform more evidence discovery and deeper reasoning before finalization.

A key product principle is:

> **Fast and Deep should differ in analytical depth, not in basic truthfulness standards.**

If Fast mode does not evaluate something, the product should prefer:

> Not assessed.

over generating unsupported completeness.

---

# 15. Constraint Analysis: Designed, Not Currently Active

A broader Constraint Agent was considered as part of deeper analytical validation.

However, the current MVP explicitly skips agentic constraint analysis in both relevant execution paths.

This was a deliberate product-engineering trade-off.

Before proving that the core system can produce consistently useful strategic insights, adding another expensive LLM-based analytical stage would increase:

* latency;
* orchestration complexity;
* failure modes;
* debugging difficulty.

Constraint analysis therefore remains a deeper-validation direction rather than a currently active product capability.

This reflects a broader prioritization principle:

> **Do not run an AI component simply because it exists architecturally. It must create enough incremental user value to justify its cost and complexity.**

---

# 16. Why Everything Is Not Agentic

A major architecture decision is to avoid using an LLM for problems that software can solve more reliably.

The product follows the principle:

> **Use LLMs for semantic ambiguity. Use deterministic software for invariants.**

LLMs are appropriate where interpretation is required.

Deterministic components are preferable where there is a correct structural answer.

---

# 17. Where LLM Reasoning Adds Value

Examples include:

### Event extraction

Understanding who did what, to whom, when and in what context.

### Relationship interpretation

Determining whether an event represents:

* investment;
* partnership;
* dependency;
* regulation;
* supply;
* competition;
* ownership;
* another semantic relationship.

### Strategic inference

Looking across several events and asking:

> Is there an underlying pattern?

### Mechanism reasoning

Explaining why several developments may be causally or strategically related.

### Evidence assessment

Determining whether a passage actually supports a particular premise.

### Alternative explanations

Identifying other plausible interpretations of the same evidence.

These tasks contain ambiguity and semantic judgment, which are areas where LLMs can add real value.

---

# 18. Where Deterministic Systems Are Better

Examples include:

* ID validation;
* schema enforcement;
* evidence-reference checks;
* provenance;
* execution budgets;
* version identifiers;
* release rules;
* lifecycle states;
* hash-based revision identifiers;
* structural integrity checks.

For example, if an insight references an evidence ID, the system should not ask a model:

> Do you think this evidence exists?

It should check the evidence registry.

Similarly, if a required field is missing, schema validation should fail deterministically rather than asking another model to interpret whether the output is “close enough.”

This leads to a broader architecture principle:

> **The LLM should not be responsible for proving the correctness of the control plane around its own output.**

---

# 19. Why More Agents Are Not Automatically Better

Every additional agent can add:

* latency;
* token usage;
* timeout risk;
* output variance;
* malformed responses;
* coordination failures;
* debugging complexity;
* compounding hallucination.

Therefore the product does not treat agent count as a proxy for sophistication.

A simpler system with:

* fewer targeted model calls;
* better evidence;
* stronger deterministic validation;
* clearer observability;

may create more user value than a complex multi-agent system.

The design question is:

> **Does this additional autonomous reasoning step improve the quality of the final user decision enough to justify the added latency, cost and uncertainty?**

---

# 20. Control Plane vs Intelligence Plane

The architecture can be understood in two layers.

## Intelligence Plane

Tasks that benefit from semantic reasoning:

**Extraction**

→ relationship interpretation

→ candidate insight generation

→ mechanism reasoning

→ evidence interpretation

→ strategic synthesis.

## Control Plane

Tasks that should remain deterministic:

**Schemas**

→ IDs

→ budgets

→ provenance

→ versioning

→ release policy

→ lifecycle management.

This hybrid architecture is particularly important for enterprise AI because reliability cannot depend entirely on prompt compliance.

---

# 21. Provenance

The product treats provenance as a core trust requirement.

A strategic conclusion should be traceable back through the objects that contributed to it.

Conceptually:

**Source**

→ **Event**

→ **Evidence**

→ **Premise**

→ **Candidate Insight**

→ **Released Finding**

The current system includes provenance-oriented structures and evidence identifiers that support this lineage.

The goal is not merely to show citations.

The goal is to know:

> **Which evidence contributed to which analytical claim?**

---

# 22. Source Independence: Current Guardrail vs Future Detection

One important governance rule already exists conceptually in the reasoning layer:

> **Different source or group IDs should not automatically be treated as independent corroboration.**

For example:

A Reuters article may be republished by several sites.

Four URLs do not necessarily represent four independent information sources.

The current system therefore avoids allowing the model to assume independence purely from different IDs.

However, fully automated syndication/source-family detection is not yet implemented.

That remains a planned capability.

A future source-independence layer could track:

* source family;
* syndicated origin;
* common reporting lineage;
* near-duplicate text;
* primary vs derivative reporting.

This would allow independence to be determined programmatically rather than merely enforced as a reasoning guardrail.

---

# 23. Revision IDs and Refinement Lineage

The current system supports within-run refinement lineage.

A candidate can receive a deterministic:

* `insight_revision_id`;
* `parent_revision_id`.

This allows the system to track how a candidate changed as it was refined during a research run.

Conceptually:

**Candidate revision 1**

→ additional research

→ **Candidate revision 2**

→ additional refinement

→ **Candidate revision 3**

This is useful for auditability within one execution.

It is important to distinguish this from a future capability:

> Persistent cross-run hypothesis history over days, weeks or months.

That broader intelligence registry is part of the roadmap rather than a currently completed capability.

---

# 24. Execution Budgets

Execution cost is not treated only as an infrastructure concern.

It is a product decision.

The system passes a shared budget through inference activities and uses different execution behavior depending on Fast or Deep mode.

The budget can constrain research before the pipeline consumes excessive time on optional investigation.

The broader principle is:

> **Research is discretionary. Final validation is not.**

A research system should not consume the entire execution window discovering more sources and then have no time left to determine whether its final conclusion is publishable.

Budget exhaustion is therefore an execution condition, not evidence that a hypothesis is correct.

---

# 25. Generation ≠ Publication

The current orchestration contains minimum publishability logic.

A candidate may exist internally yet still fail to become a released finding.

The system therefore distinguishes:

**Generated**

from

**Publishable.**

This matters because a generative model may produce something syntactically complete and persuasive even when its evidence structure is insufficient.

A product that surfaces strategic recommendations should not treat model completion as equivalent to analytical success.

---

# 26. Governance Philosophy

Governance is designed as part of the product architecture rather than as a disclaimer placed beneath the answer.

Important principles include:

### Fail closed

If a critical publishability requirement is not satisfied, the system should withhold the finding.

### Preserve evidence lineage

Claims should retain traceability to their underlying evidence.

### Separate structural validity from semantic interpretation

A model should not be the sole validator of its own reasoning.

### Preserve uncertainty

An unresolved premise should remain unresolved rather than being converted into confident prose.

### Budget research explicitly

Research depth should not be allowed to grow without control.

### Version important system behavior

Changes to prompts, models, schemas and policies can change outputs and should eventually be traceable.

---

# 27. Red-Team Stage

The orchestration architecture includes Red Team as a governance dependency.

Its purpose is to challenge a candidate before publication.

The exact internal Red Team taxonomy depends on the specific module implementation and should not be overstated beyond what has been verified.

The intended design direction is to test issues such as:

* overstatement;
* unsupported causal reasoning;
* unsupported motive attribution;
* weak analogies;
* ignored alternatives;
* magnitude exaggeration;
* confidence that exceeds the evidence.

The important product decision is:

> **A candidate should be challenged before release rather than merely polished before release.**

---

# 28. Evaluation Is Multi-Layered

There is no single “AI accuracy” metric for this product.

Different stages require different evaluation questions.

| Layer              | Core question                                                  |
| ------------------ | -------------------------------------------------------------- |
| Retrieval          | Did the system find relevant information?                      |
| Evidence discovery | Did it find evidence needed to close the real gap?             |
| Extraction         | Did it correctly structure the source content?                 |
| Grounding          | Does the evidence support the premise?                         |
| Inference          | Does the reasoning follow from the evidence?                   |
| Insight quality    | Is the result specific and useful?                             |
| Ranking            | Are the best insights shown first?                             |
| Product            | Do users actually value the output?                            |
| Operations         | Can the system deliver reliably within acceptable constraints? |

This separation is important because an improvement in one layer can hide deterioration elsewhere.

---

# 29. Evidence Discovery Evaluation

A dedicated evidence-discovery evaluator is designed around the full research loop:

**Grounding gap**

→ query planning

→ search

→ source fetch

→ evidence admission

→ material-information check

→ grounding reassessment

→ stop or continue.

Evaluation covers evidence precision, recall and F1 overall and by role.

Relevant roles include:

* direct-premise evidence;
* analogical-mechanism evidence;
* feasibility evidence;
* counterevidence;
* counter-incentive evidence;
* alternative-explanation evidence.

Additional dimensions include:

* required search-mode coverage;
* query quality;
* duplicate-query rate;
* irrelevant search results;
* irrelevant fetched sources;
* materiality classification;
* prospective mechanism retrieval;
* feasibility retrieval;
* premise resolution;
* search saturation;
* budget-policy compliance.

Duplicate-underlying-source detection is a planned evaluation dimension rather than a fully implemented syndication-detection capability.

---

# 30. Insight Quality Evaluation

A grounded statement can still be a bad insight.

For example:

> Microsoft is investing in AI.

This may be completely true.

It may also provide almost no strategic value.

Insight quality therefore needs separate dimensions.

| Dimension                 | What it asks                                          |
| ------------------------- | ----------------------------------------------------- |
| Grounding                 | Is the claim supported?                               |
| Specificity               | Does it make a concrete claim?                        |
| Novelty                   | Does it add incremental intelligence?                 |
| Non-obviousness           | Is the connection worth surfacing?                    |
| Strategic relevance       | Does the conclusion matter?                           |
| Mechanism quality         | Does it explain why?                                  |
| Evidence diversity        | Is the conclusion based on more than repetition?      |
| Counterevidence awareness | Are weakening signals represented?                    |
| Alternative explanations  | Are other interpretations acknowledged?               |
| Uncertainty quality       | Are unknowns represented honestly?                    |
| Actionability             | Could this change investigation or decision behavior? |
| Coherence                 | Do the premises actually support the conclusion?      |

One of the most useful human-evaluation prompts remains:

> **Could this insight have been written without these specific events?**

---

# 31. Human Evaluation

Because the product is still at prototype stage, human evaluation remains particularly important.

A reviewer can assess each output on questions such as:

* Is the insight grounded?
* Is it specific?
* Is it actually derived from the retrieved events?
* Is it non-obvious?
* Does it explain a plausible mechanism?
* Are uncertainties visible?
* Does it overstate causality?
* Would an analyst consider it worth reading?
* Would it change what the analyst investigates next?

This human evaluation can later become a benchmark dataset for comparing pipeline versions.

---

# 32. Model Metrics vs Product Metrics

A central product-management principle is:

> **Technical model quality and product value are not the same thing.**

A system could achieve:

* strong evidence precision;
* good citation accuracy;
* low hallucination;

while still producing boring insights.

Conversely, a product could produce fascinating narratives that are poorly grounded.

Both are failures.

The product therefore needs two metric families:

## Model/system quality metrics

Are outputs technically defensible?

## Product metrics

Do users actually gain enough value to return and use the product?

---

# 33. North Star Metric

The proposed North Star is:

## Decision-Useful Insight Rate

> **The percentage of surfaced insights that are sufficiently credible, specific and useful that the user would investigate, monitor or incorporate into a decision.**

At MVP stage this can rely heavily on structured human evaluation.

At later stages it can incorporate behavioral signals such as:

* save;
* follow;
* deeper investigation;
* repeated access;
* user-confirmed usefulness.

---

# 34. Product Metrics

## Activation

Percentage of users who:

**submit a topic/question → reach their first released insight.**

A stronger activation definition may require:

**released insight + evidence inspection or follow-up interaction.**

---

## Time to First Useful Insight

This is more meaningful than time to first token.

Measure:

> Time from submitting the research question to receiving the first insight judged useful.

The product is a research system rather than a conversational autocomplete product, so a slightly slower answer can be acceptable if the incremental analytical value is substantially higher.

---

## Useful Insight Rate

**Useful insights / surfaced insights**

A report with three strong insights is preferable to one with fifteen generic ones.

---

## Evidence Inspection Rate

How often users inspect supporting evidence.

This should not be interpreted alone.

High inspection may mean:

* users value transparency;

or

* users distrust the conclusions.

It should be combined with usefulness and trust feedback.

---

## Follow-Up Question Rate

Whether an insight motivates useful deeper research.

Examples:

* Why is this happening?
* What evidence supports this?
* What would invalidate it?
* Which competitors are exposed?
* What should I watch next?

---

## Save / Follow Rate

How often users consider an insight important enough to retain or monitor.

---

## Return-to-Topic Rate

Whether users return to the same topic as new developments emerge.

This becomes particularly important once persistent workspaces exist.

---

## Retention

Potential future metrics:

* 7-day return;
* 30-day return;
* monitored-topic retention.

---

## Reliability

Examples:

* successful-run rate;
* candidate-to-release rate;
* abstention rate;
* timeout rate;
* grounding failure rate;
* publishability rejection rate.

---

## Efficiency

Examples:

* total latency;
* time to useful insight;
* search rounds per released insight;
* number of model calls;
* source fetch volume;
* cost per useful insight.

---

# 35. Feedback and Personalization

The product includes foundations for explicit user feedback and preference-aware ranking.

A lightweight interaction can include:

* like;
* dislike;
* free-text comment.

Feedback can later be interpreted into reasons such as:

* useful;
* too obvious;
* unclear;
* missing evidence;
* too speculative.

The longer-term ranking architecture separates:

**objective insight quality**

from

**personal relevance.**

This is important.

A user may prefer regulatory insights, but a weak regulatory insight should not rank above a strong strategic insight simply because it matches that preference.

The intended sequence is:

**quality gate**

→ **personalized ranking.**

---

# 36. Ranking Metrics

Once sufficient interaction data exists, ranking can be evaluated using metrics such as:

* Mean Reciprocal Rank;
* Mean Average Precision;
* nDCG.

These answer:

> Did the product rank the most valuable insights toward the top?

They should not be confused with whether those insights are themselves grounded or correct.

---

# 37. Experimentation

Pipeline changes should be evaluated on matched research questions.

For example:

### Baseline

Generic retrieval + report generation.

### Variant

Gap-driven evidence discovery + material-information assessment + stronger publishability gating.

Then compare:

* useful-insight rate;
* grounding;
* novelty;
* unsupported-claim rate;
* latency;
* user preference;
* evidence retrieval quality.

Paired experiments are preferable because the same research questions are evaluated under both pipelines.

Potential statistical tools include:

* Wilson confidence intervals for rates;
* bootstrap confidence intervals;
* paired bootstrap comparisons;
* exact McNemar tests for paired binary outcomes;
* sign tests for paired ordinal judgments.

Probability-calibration metrics such as Brier Score or Expected Calibration Error should only be used if the system eventually outputs genuine calibrated probabilities.

Qualitative labels such as “moderate confidence” should not be treated as numerical probabilities.

---

# 38. Operational Observability

The system also needs operational metrics because apparent reasoning problems may originate elsewhere.

Potential metrics include:

* source-discovery success;
* fresh-source availability;
* search-provider failure rate;
* fetch success;
* extraction latency;
* inference latency;
* model timeout rate;
* fallback usage;
* number of evidence records admitted;
* evidence rejected;
* duplicate rate;
* candidate rejection reasons;
* budget consumption by stage;
* abstention frequency.

This is important because a weak output may result from:

* poor retrieval;
* incomplete source fetch;
* identifier mismatch;
* dropped evidence;
* timeout;
* schema failure;

rather than bad reasoning alone.

---

# 39. Current Capability vs Roadmap

A major design principle for this case study is to distinguish clearly between what exists today and what is planned.

## Implemented / MVP foundations

* topic-driven source discovery;
* article/source ingestion;
* structured event/entity extraction;
* relationship representation;
* descriptive/prospective insight types;
* structured candidate insight fields;
* gap-driven corpus refill;
* material-information assessment;
* bounded retry/refinement;
* execution budgets;
* Fast/Deep branching;
* minimum publishability gating;
* tiered findings;
* within-run revision IDs and parent lineage;
* evidence/provenance-oriented structures;
* user feedback foundations;
* preference/ranking foundations.

## Designed / next-stage capabilities

* formal Analysis of Competing Hypotheses;
* explicit null hypotheses;
* automated source-family/syndication detection;
* statistical significance testing for candidate connections;
* corpus-relative anchor rarity;
* programmatic graph bridge detection;
* economic-exposure mapping;
* persistent cross-run insight registry;
* continuous hypothesis monitoring;
* proactive intelligence;
* decision-oriented research.

---

# 40. Planned: Analysis of Competing Hypotheses

The current system contains counterevidence-oriented structures and disconfirming conditions, but it does not yet implement a formal ACH object.

A future version could require every high-materiality prospective insight to include:

* main hypothesis;
* null hypothesis;
* competing hypotheses;
* discriminating evidence;
* evidence inconsistent with each hypothesis;
* unresolved ambiguity.

Example:

### Main hypothesis

A hyperscaler is moving toward vertical integration of energy infrastructure.

### Null hypothesis

The observed transactions are independent responses to ordinary capacity growth.

### Alternative hypothesis

The activity represents temporary supply-risk mitigation rather than long-term vertical integration.

The research system would then look for evidence that distinguishes these explanations rather than simply finding more evidence compatible with the preferred hypothesis.

---

# 41. Planned: Connection Significance

Another future improvement is to quantify whether an apparent event connection is actually unusual within the corpus.

Suppose several events share:

* Microsoft;
* AI;
* infrastructure.

Those anchors may be so common that their co-occurrence has little informational value.

A future structural layer could calculate:

* anchor frequency;
* co-occurrence frequency;
* rarity;
* corpus-relative cluster strength;
* random/permutation baselines.

The purpose would not be to prove causality.

It would answer a narrower question:

> **Is this structural connection sufficiently unusual to deserve semantic investigation?**

---

# 42. Planned: Graph Bridge Detection

The existing graph/event relationship foundations can support a stronger future computational discovery layer.

Instead of relying on the LLM to notice every cross-domain connection, graph analysis can first identify nodes that connect otherwise separate clusters.

For example:

**AI infrastructure cluster**

↔ **specific supplier / company / financing structure**

↔ **energy infrastructure cluster**

The system could then ask the LLM:

> Why might this structurally unusual bridge matter?

This follows the architecture principle:

> **Use computation to identify structure. Use LLMs to interpret meaning.**

---

# 43. Planned: Economic Exposure Mapping

Semantic similarity can miss second-order effects.

An event about semiconductor supply may later show up as:

> automotive production cuts

without using the same language as the original source.

A future exposure-mapping layer could explicitly enumerate:

* suppliers;
* customers;
* competitors;
* regulators;
* substitutes;
* financiers;
* infrastructure dependencies.

Then research those actors individually.

Example:

**AI data-center growth**

→ power demand

→ transformer demand

→ grid bottlenecks

→ geographic relocation

→ local policy competition.

This is designed to make second-order-effect discovery more systematic.

---

# 44. Product Roadmap

## Phase 1 — Prove Insight Quality

Current focus:

> **Can the system repeatedly produce at least one genuinely useful strategic insight?**

Priority areas:

* reliable source discovery;
* strategic candidate quality;
* evidence quality;
* gap-driven research;
* material-information gain;
* publishability;
* human evaluation;
* latency/reliability.

The objective is not feature breadth.

It is proving the core value proposition.

---

## Phase 2 — Strengthen Analytical Validation

Add:

* explicit null hypotheses;
* competing hypotheses;
* stronger counterevidence retrieval;
* source-independence detection;
* corpus-relative connection significance;
* deeper mechanism testing;
* richer evaluation benchmarks.

The goal is to make it harder for the system to manufacture a persuasive narrative from coincidental events.

---

## Phase 3 — Structural Intelligence

Add:

* graph community detection;
* bridge-node discovery;
* event-cluster analysis;
* economic-exposure mapping;
* second-order-effect search.

This moves part of pattern discovery away from pure LLM intuition and into programmatic structure analysis.

---

## Phase 4 — Persistent Intelligence Workspaces

Move beyond one-off runs.

A workspace can represent:

**AI Infrastructure**

or

**EU AI Regulation**

and retain:

* events;
* actors;
* previous insights;
* unresolved hypotheses;
* evidence history;
* critical unknowns.

The product can then answer:

> **What changed since I last looked?**

---

## Phase 5 — Persistent Hypothesis Monitoring

A hypothesis becomes a living object.

For each major insight:

**Current hypothesis**

**Evidence supporting it**

**Evidence weakening it**

**Critical unknowns**

**What would strengthen the thesis**

**What would invalidate it**

**Signals to monitor**

New evidence can create a new revision rather than silently rewriting history.

---

## Phase 6 — Proactive Intelligence

Instead of waiting for the user to rerun a topic, the system can identify material changes.

Examples:

> Three new events strengthened an existing infrastructure-consolidation hypothesis.

or:

> New regulation materially weakened a previously plausible market-entry scenario.

The product becomes a continuous monitoring system.

---

## Phase 7 — Decision Intelligence

The eventual product direction moves from:

> What is happening?

toward:

> What should I do?

A future Decision Agent could receive questions such as:

* Should we enter this market?
* Which competitor is most likely to vertically integrate?
* How could this regulation affect our strategy?
* Which assumption in our strategy is most at risk?

The system could then:

**decompose the decision**

→ identify relevant hypotheses

→ identify evidence gaps

→ perform targeted research

→ compare alternatives

→ challenge assumptions

→ build scenarios

→ expose uncertainty

→ recommend actions

→ monitor the assumptions underlying the recommendation.

This stage comes later deliberately.

A product should not automate strategic recommendations before its underlying intelligence layer is trustworthy.

---

# 45. Major Product Decisions

The most important decisions made during the project include:

1. Move beyond news summarization toward strategic intelligence.
2. Make the insight rather than the report the core unit of value.
3. Optimize for a small number of strong insights rather than many generic outputs.
4. Allow abstention.
5. Separate candidate generation from publication.
6. Introduce minimum publishability gates.
7. Distinguish descriptive from prospective insights.
8. Structure premises, mechanism, feasibility, counterevidence and critical unknowns.
9. Move from generic search toward evidence-gap-driven research.
10. Measure material information gain.
11. Bound research activity through execution budgets.
12. Use Fast and Deep modes for different research depths.
13. Skip expensive agentic stages when they do not yet justify their latency.
14. Use LLMs for semantic interpretation rather than every system decision.
15. Use deterministic logic for IDs, schemas, provenance and control-plane rules.
16. Preserve within-run refinement lineage.
17. Treat different source IDs as insufficient proof of source independence.
18. Separate model-quality metrics from product-success metrics.
19. Prioritize Decision-Useful Insight Rate over output volume.
20. Treat stronger competing-hypothesis analysis as roadmap rather than claiming it is already implemented.
21. Treat statistical connection testing as a future validation layer.
22. Treat graph bridge discovery as a future structural-analysis layer.
23. Treat persistent cross-run hypothesis history as a roadmap capability.
24. Delay decision automation until intelligence quality is sufficiently strong.

---

# 46. What I Learned

The biggest product lesson was:

> **AI sophistication is not the same as product value.**

More agents, longer prompts and deeper orchestration did not automatically produce better insights.

Several of the most important improvements came from asking different questions:

* Does this model call need to exist at all?
* Should this decision be deterministic?
* Is this evidence actually resolving a premise?
* Could this insight have been written without these events?
* Is this connection genuinely unusual?
* Are we mistaking multiple URLs for independent evidence?
* What remains unknown?
* Why did the candidate fail?
* Should the system search again?
* Should the system simply abstain?

The architecture therefore evolved from:

**Articles**

→ **LLM**

→ **Report**

toward:

**Sources**

→ **Structured Events**

→ **Evidence**

→ **Candidate Insight**

→ **Gap-Driven Research**

→ **Materiality Assessment**

→ **Publishability Gate**

→ **Released Finding**

with stronger structural and adversarial validation planned next.

---

# 47. Product Philosophy

The product is guided by several principles.

## Trust over output volume

The system should be comfortable withholding an insight.

## Interpretation should be earned

A plausible narrative is not automatically a good strategic conclusion.

## Use AI where semantic judgment matters

LLMs are valuable for ambiguity and interpretation.

They are not necessary for every control decision.

## Make uncertainty visible

Unknown information should remain unknown.

## Separate vision from implementation

A researched architecture or planned capability should not be presented as already validated.

## Optimize for user decision value

The real objective is not maximum AI reasoning.

It is:

> **maximum trustworthy, decision-useful intelligence per unit of user time, system latency and research effort.**

---

# 48. Current Status

AI Event Intelligence is an evolving prototype rather than a production intelligence platform.

The current MVP has working foundations around:

* research-topic input;
* source discovery;
* event/entity extraction;
* relationship representation;
* strategic inference;
* descriptive/prospective candidate schemas;
* structured supporting and challenging evidence fields;
* gap-driven corpus refill;
* material-information assessment;
* execution-budget controls;
* Fast/Deep execution paths;
* minimum publishability gating;
* tiered findings;
* within-run refinement lineage;
* evidence/provenance structures;
* user-feedback and ranking foundations.

The next major product challenge is not adding more features.

It is proving:

> **Can the system repeatedly generate strategic insights that are more specific, defensible and decision-useful than ordinary news summarization or generic LLM research?**

---

# 49. Vision

The long-term product is not intended to be another application that simply tells users what happened.

The goal is to maintain a living understanding of important developments, identify when previously separate signals begin to form a meaningful pattern, explain what that pattern could imply, show the supporting evidence and uncertainty, and continue monitoring whether the hypothesis becomes stronger or weaker over time.

The evolution is:

**News**

→ **Events**

→ **Connections**

→ **Hypotheses**

→ **Evidence**

→ **Strategic Intelligence**

→ **Continuous Decision Intelligence**

The ultimate product goal is:

> **Turn fragmented external information into continuously updated, inspectable and decision-useful intelligence.**
