# AI Event Intelligence

> **Status: Ongoing research prototype — under active empirical validation**
>
> AI Event Intelligence is an actively developed prototype exploring whether an AI research system can move beyond news summarization and produce genuinely non-obvious, evidence-grounded strategic insights.
>
> The research architecture and core pipeline are implemented, but the system is still being tested across real-world topics. Insight yield, retrieval effectiveness, and strategic-quality metrics should therefore be treated as **evaluation questions, not validated performance claims**.
>
> This repository is a product and technical case study, not a finished production application.

---

## Overview

Most news products are optimized for answering:

> **What happened?**

AI Event Intelligence explores a harder question:

> **What do multiple events, viewed together, imply — and is that implication actually supported strongly enough to call it a strategic insight?**

The system researches a user-selected topic, converts source material into structured events and evidence, identifies potentially meaningful relationships, investigates missing information, tests competing explanations, and then either:

- publishes a tiered strategic finding; or
- explicitly abstains when the evidence does not justify one.

The goal is **not** to always generate an answer.

The goal is to determine whether the available evidence supports something genuinely worth saying.

---

# The Product Problem

A reader following a fast-moving area such as artificial intelligence, energy, regulation, financial markets, supply chains, or technology may encounter dozens of individually useful stories.

The harder work remains manual:

- Which events are genuinely connected?
- Which apparent connections are coincidence?
- What underlying mechanism could explain the pattern?
- Is the pattern specific to one company or more general?
- What second-order consequences could follow?
- What evidence contradicts the thesis?
- Is a more ordinary explanation equally plausible?
- What information is still missing?
- What would make the thesis stronger or weaker?
- Is there actually a meaningful insight here at all?

Traditional news feeds generally leave this synthesis to the reader.

General-purpose LLMs can generate synthesis quickly, but without strong evidence controls they can also generate explanations that sound insightful without being sufficiently grounded.

AI Event Intelligence is an attempt to bridge that gap.

---

# Product Goal

The long-term product goal is:

> **Reliably produce genuinely useful, non-obvious, evidence-grounded strategic insights from distributed public events — without lowering the epistemic bar simply to produce an answer.**

This is intentionally harder than summarization.

A successful research run should not merely produce:

> “These developments indicate growing AI adoption.”

It should aim for something closer to:

```text
Specific events
        ↓
shared mechanism / constraint / dependency
        ↓
non-obvious structural implication
        ↓
evidence for and against the thesis
        ↓
observable consequence or watch condition
````

If the system cannot establish that chain, it should publish a weaker but honest finding or abstain.

---

# Target Users

The prototype is being designed around two related user groups.

## Serious news readers

People who want to go beyond headline aggregation and understand:

* connections across separate developments;
* underlying economic, operational, regulatory, or technological dynamics;
* emerging signals;
* second-order implications;
* what developments are worth monitoring next.

## Decision-oriented users

Analysts, product leaders, investors, strategy teams, operators, and researchers who may value:

* evidence traceability;
* explicit uncertainty;
* competing explanations;
* counterevidence;
* structured research paths;
* separation between factual observation and analytical inference.

The prototype is not intended to replace professional investment, legal, intelligence, regulatory, or policy analysis.

---

# Product Hypothesis

The core product hypothesis is:

If an AI research system:

1. converts news into structured event and evidence objects;
2. reasons across multiple distinct underlying events;
3. actively searches for missing evidence;
4. investigates potentially meaningful clusters;
5. tests alternatives and counterevidence;
6. separates candidate generation from publication authorization;
7. applies deterministic quality gates;
8. and abstains when those gates cannot be met;

then it may be able to produce more useful and trustworthy strategic synthesis than either:

* ordinary news summarization; or
* unconstrained LLM analysis.

This hypothesis is currently being tested empirically.

---

# How It Works

```text
User topic
    ↓
Source discovery
    ↓
Source fetching
    ↓
Structured event extraction
    ↓
Evidence construction
    ↓
Underlying-event deduplication
    ↓
Strategic event admission
    ↓
Corpus sufficiency assessment
    ↓
┌───────────────────────────────────────────┐
│ If evidence is insufficient:              │
│                                           │
│ • zero-event bootstrap                    │
│ • mandatory corpus refill                 │
│ • cluster-specific expansion              │
│ • candidate-specific evidence rescue      │
└───────────────────────────────────────────┘
    ↓
Strategic clustering
    ↓
Strategic inference
    ↓
Alternative / counterevidence testing
    ↓
Grounding + deterministic publication gates
    ↓
Tiered strategic finding
            OR
Honest abstention
```

---

# Research Retries Are Not Generation Retries

A major design principle is that retries should improve the **information state**, not simply reroll the model until it produces something that passes.

The intended pattern is:

```text
insufficient evidence
        ↓
new research query
        ↓
new source
        ↓
new event / evidence
        ↓
material information gain
        ↓
reassess thesis
```

Not:

```text
same evidence
    ↓
generate again
    ↓
generate again
    ↓
keep whichever answer sounds best
```

Repeated synthesis over identical evidence would inflate apparent success without improving the underlying research.

The prototype therefore treats evidence discovery and bounded research recovery as first-class product capabilities.

---

# Strategic Findings

The system does not label every useful observation a "Hidden Insight."

Findings are tiered according to analytical strength.

## Hidden Insight

The highest bar.

A Hidden Insight should be:

* grounded in specific events;
* supported by multiple evidence records;
* dependent on those particular events;
* structurally non-obvious;
* explained through a meaningful mechanism;
* scoped appropriately;
* falsifiable;
* accompanied by observable watch signals;
* robust enough to survive competing explanations and publication gates.

The project does **not yet claim a measured Hidden Insight success rate**.

That is one of the main empirical questions currently being evaluated.

---

## Underlying Dynamic

A meaningful structural mechanism or dynamic supported by the evidence, even where the strongest novelty requirements for a Hidden Insight are not met.

---

## Strategic Connection

A defensible and strategically relevant connection between separate developments without claiming a deeper hidden structural insight than the evidence supports.

---

## Early Signal

A prospective or emerging development with meaningful evidence but substantial unresolved uncertainty.

An Early Signal should explicitly surface:

* what is known;
* what remains unknown;
* what would strengthen the thesis;
* what would weaken it;
* what to watch next.

---

# Evidence Before Narrative

A central architectural decision is to make evidence a first-class research object rather than allowing the final prose to become the research record.

Strategic findings can reference:

```text
supporting evidence
feasibility evidence
analogical evidence
counterevidence
counter-incentive evidence
alternative-explanation evidence
```

The strategic reasoning layer is not permitted to create factual evidence simply because a thesis requires it.

This separates:

```text
candidate generation
```

from:

```text
publication authorization
```

A model can propose an interesting idea.

The system still has to decide whether that idea deserves to be published.

---

# Missing Evidence Is a Research Problem

A central product principle is:

> **A missing piece of evidence should first trigger research, not immediately be interpreted as proof that no strategic pattern exists.**

Deep research therefore contains several bounded recovery mechanisms.

---

## Zero-Event Bootstrap

If initial Deep retrieval produces sources but no usable structured events, the system can perform an additional bounded search before declaring retrieval failure.

```text
initial sources
    ↓
0 valid events
    ↓
bootstrap search
    ↓
additional sources
    ↓
event extraction
```

Quality standards are not lowered during recovery.

---

## Strategic Corpus Refill

If the system has events but not enough strategically coherent structure, it can search again across additional development lanes.

Examples include:

* regulation;
* litigation;
* financing;
* acquisitions;
* commercial contracts;
* operations;
* infrastructure;
* capacity constraints;
* adoption;
* failures and delays;
* counterexamples.

The goal is not merely to obtain more documents.

The goal is to discover **materially new underlying events**.

---

## Cluster Expansion

If a particular event cluster appears strategically promising but incomplete, the system can perform targeted research into its missing dimensions.

Potential gaps include:

* outcomes;
* implementation;
* constraints;
* financing;
* customer behavior;
* regulatory responses;
* competing actors;
* counterexamples.

New information is assessed for material information gain before being admitted.

---

## Candidate-Specific Evidence Rescue

A candidate may already have a plausible structural basis but fail because important evidence is missing.

The system can then create a targeted research plan involving:

```text
main hypothesis
alternative explanations
falsifiers
critical unknowns
discrimination question
missing evidence
```

and search specifically for information that could strengthen, weaken, or falsify the candidate.

---

# Competing Explanations

Strategic analysis should not only ask:

> Is this evidence consistent with my thesis?

It should also ask:

> Is this evidence more consistent with my thesis than with a simpler alternative?

The current system already represents:

* alternative explanations;
* falsifiers;
* critical unknowns;
* counterevidence;
* discrimination questions.

A planned extension will formalize this into an evidence-by-hypothesis framework.

Conceptually:

| Evidence | H1: Strategic thesis | H0: Boring/null explanation | H2: Alternative |
| -------- | -------------------- | --------------------------- | --------------- |
| EVID-1   | Supports             | Weakens                     | Neutral         |
| EVID-2   | Supports             | Also supports               | Supports        |
| EVID-3   | Neutral              | Supports                    | Weakens         |

Evidence that fits every hypothesis has low diagnostic value.

Evidence that meaningfully distinguishes between them is more valuable.

---

# Connection Quality

A major research challenge is distinguishing:

```text
real structural connection
```

from:

```text
coincidental / generic similarity
```

The current implementation already restricts cross-cluster connections using structural conditions such as:

* shared dependencies;
* mechanism relationships;
* constraints;
* regulatory effects;
* transactions;
* substitution or switching relationships;
* meaningful named-entity relationships.

Same-topic overlap alone is not enough.

Same-actor overlap alone is not enough.

---

# Planned Connection-Specificity Evaluation

A future version will additionally ask:

> Is this proposed connection unusually specific relative to the rest of the corpus?

For example:

```text
"AI adoption"
appears across almost every event
→ weak discriminatory signal
```

versus:

```text
"power-grid interconnection delay"
appears only in a small subset of events
and connects the same infrastructure dependency
→ potentially much stronger signal
```

Planned metrics include:

* anchor frequency;
* mechanism frequency;
* dependency frequency;
* pair specificity;
* generic-bridge penalties.

Permutation/null-distribution testing may later become useful once the system has a sufficiently large persistent event corpus.

---

# Descriptive vs Prospective Analysis

The system distinguishes between two forms of strategic reasoning.

## Descriptive insight

A conclusion about a pattern that is already observable in the available evidence.

## Prospective insight

A thesis about what may happen next.

Prospective analysis should not pretend that a future event has already occurred.

Instead, the system evaluates evidence for:

* premises;
* mechanism;
* feasibility;
* constraints;
* magnitude;
* counter-incentives;
* alternative explanations;
* analogical support where available;
* observable confirming and disconfirming signals.

---

# Trust and Abstention

One of the most important product behaviors is the ability to return **no strategic finding**.

A candidate can be rejected because:

* too few independent evidence records exist;
* the proposed connection is generic;
* the mechanism is insufficiently established;
* event-specific anchors are missing;
* the conclusion could have been written without the actual events;
* the claim generalizes beyond the available actors;
* a strong comparative claim lacks comparative evidence;
* unsupported motive or causal attribution appears;
* competing explanations remain too strong;
* bounded research fails to close the relevant evidence gap.

The system therefore separates:

```text
successful research execution
```

from:

```text
successful Hidden Insight production
```

A research run can function correctly and still conclude:

> The available evidence does not justify a sufficiently strong strategic insight.

That is not necessarily a product failure.

---

# Provider Resilience

External research providers may fail due to:

* quota exhaustion;
* rate limiting;
* authentication problems;
* temporary provider availability.

The orchestrator contains a run-scoped discovery circuit breaker.

```text
provider works
    ↓
normal discovery

OR

terminal provider failure
    ↓
open circuit
    ↓
do not repeatedly retry provider
    ↓
reuse persisted evidence where possible
    ↓
continue if corpus is sufficient
```

This prevents a known provider failure from consuming repeated research rounds.

---

# Current Architecture

Major components include:

```text
ResearchOrchestratorService

Discovery / Retrieval
├── WebSearchService
├── SourceFetchService
├── source persistence
└── provider circuit breaker

Knowledge Construction
├── ExtractionAgent
├── EvidenceBuilderAgent
├── EventService
├── EntityService
├── RelationshipService
└── GraphService

Strategic Research
├── underlying-event normalization
├── strategic event admission
├── corpus sufficiency assessment
├── zero-event bootstrap
├── strategic corpus refill
├── cluster expansion
├── StrategicInferenceAgent
├── candidate-specific evidence rescue
└── execution budget controls

Trust / Governance
├── GroundingService
├── quality / red-team checks
├── deterministic publication gates
├── SourceProvenanceService
├── InsightTraceService
└── audit metadata

Presentation
└── Streamlit prototype
```

For a deeper technical walkthrough:

[Architecture](docs/ARCHITECTURE.md)

---

# Evaluation

The project is now moving from primarily architectural validation into **empirical evaluation across repeated real-world research runs**.

No unvalidated success percentage is claimed in this repository.

The next evaluation cycle is intended to measure:

```text
first-pass Hidden Insight rate

research-recovered Hidden Insight rate

final genuine Hidden Insight rate

valid lower-tier finding rate

abstention rate

false / weak insight rate
```

The evaluation framework is documented here:

[Evaluation Framework](docs/EVALUATION.md)

---

# Core Evaluation Question

The most important question is not:

> How often can the LLM produce an insight?

It is:

> **Given bounded retrieval and research retries, how often can the system turn an initially inadequate information state into a genuinely good strategic insight without weakening the publication bar?**

This can be measured as a **Research Recovery Rate**:

```text
initially unsuccessful research runs
that become valid Hidden Insights
after materially new evidence is acquired
------------------------------------------------
runs eligible for additional research
```

---

# Planned Baseline Evaluation

The current prototype will be tested across a stable set of information-rich real-world topics.

For each run, the evaluation will record:

| Measure                     | Example                                                     |
| --------------------------- | ----------------------------------------------------------- |
| Initial corpus sufficient?  | Yes / No                                                    |
| First-pass Hidden Insight?  | Yes / No                                                    |
| Corpus refill used?         | Yes / No                                                    |
| Cluster expansion used?     | Yes / No                                                    |
| Evidence rescue used?       | Yes / No                                                    |
| Final tier                  | Hidden Insight / Dynamic / Connection / Signal / Abstention |
| Human-valid Hidden Insight? | Yes / No                                                    |
| Primary failure reason      | e.g. weak mechanism                                         |

The same topic set can then be rerun after major architecture changes to measure actual improvement rather than relying on intuition.

---

# Failure Analysis

Failed Hidden Insight attempts are themselves valuable product data.

Reasons will be categorized, for example:

```text
insufficient independent events

weak mechanism

generic connection

missing second-order evidence

insufficient source independence

null explanation remains equally plausible

prospective claim lacks feasibility evidence

retrieval provider unavailable

overly broad scope

correct abstention — no strong public evidence exists
```

These failure distributions will determine the research roadmap.

For example:

```text
many failures from missing exposed actors
→ prioritize economic-exposure retrieval

many failures from generic connections
→ prioritize connection-specificity scoring

many failures because H1 does not beat H0
→ prioritize hypothesis discrimination

many failures with genuinely insufficient public evidence
→ preserve abstention rather than weaken standards
```

---

# Product Metrics

The project deliberately avoids using:

```text
number of insights generated
```

as its primary success metric.

That would reward over-generation.

A stronger eventual metric is:

## Useful Grounded Finding Rate

Conceptually:

```text
completed research runs producing
a grounded finding that a human evaluator
rates as useful / non-obvious
------------------------------------------
completed research runs
```

Hidden Insight yield should be measured separately because it represents the highest analytical tier.

---

# Current Prototype Status

This repository should be understood as an **ongoing research prototype**, not a polished product release.

## Implemented research capabilities

The current codebase includes:

* user-defined research topics;
* source discovery;
* source fetching;
* persisted-source fallback;
* structured event extraction;
* evidence construction;
* underlying-event deduplication;
* strategic event admission;
* corpus sufficiency checks;
* Deep zero-event recovery;
* mandatory corpus refill;
* strategic cluster expansion;
* strategic inference;
* tiered strategic findings;
* competing explanations;
* counterevidence;
* candidate-specific evidence rescue;
* grounding;
* deterministic publication gates;
* explicit abstention;
* provider circuit breaking;
* research traces;
* execution-budget controls.

## Under active validation

The following are currently being evaluated through real runs:

* genuine Hidden Insight yield;
* value of bounded research retries;
* corpus refill effectiveness;
* cluster expansion effectiveness;
* candidate rescue effectiveness;
* quality of lower-tier findings;
* abstention quality;
* retrieval-provider reliability;
* latency and research cost.

## Planned research

Major planned capabilities include:

* connection-specificity / rarity scoring;
* stronger hypothesis discrimination;
* economic-exposure retrieval;
* graph-topology-based bridge scoring;
* historical analogy retrieval;
* persistent precedent memory;
* larger automated regression datasets;
* stronger evaluation dashboards;
* UX and report-presentation refinement.

---

# Why There Is No Demo Screenshot Yet

The current focus is research quality rather than presentation polish.

The interface and report presentation are still being refined, and screenshots will be added only after the output and user experience are sufficiently representative of the intended product.

The repository therefore currently emphasizes:

```text
product problem
+
research architecture
+
quality controls
+
evaluation methodology
+
engineering evolution
```

rather than presenting the prototype as a finished application.

---

# Product Case Study

The full case study covers:

* original problem discovery;
* target users;
* product hypothesis;
* architecture evolution;
* major failures encountered;
* research and retrieval design;
* strategic finding design;
* trust and abstention;
* evaluation strategy;
* trade-offs;
* roadmap;
* lessons from building the prototype.

[Read the Product Case Study](docs/PRODUCT_CASE_STUDY.md)

---

# Roadmap

```text
Current prototype
V3.26.x research orchestration
+
V7.9 strategic inference
+
tiered findings
+
research recovery
+
grounding / abstention
        ↓
Empirical baseline across repeated topics
        ↓
Connection specificity
+
Hypothesis discrimination
        ↓
Economic exposure retrieval
        ↓
Graph structural bridge scoring
        ↓
Historical analogy retrieval
        ↓
Persistent precedent library
        ↓
Longitudinal intelligence system
```

The roadmap is intended to be evidence-driven.

Features should be prioritized according to measured failure modes rather than added solely because they appear theoretically useful.

---

# Technology

The prototype uses a combination of:

* Python
* Streamlit
* SQLAlchemy
* Gemini
* local LLM inference
* web/news retrieval
* structured event and evidence schemas
* graph-based entities and relationships
* deterministic validation
* provenance and trace services
* bounded agentic research workflows

---

# Repository Documentation

* [Product Case Study](docs/PRODUCT_CASE_STUDY.md)
* [Architecture](docs/ARCHITECTURE.md)
* [Evaluation Framework](docs/EVALUATION.md)

---

# Important Disclaimer

AI Event Intelligence is a research prototype.

Outputs may be incomplete or incorrect because of:

* incomplete public information;
* source availability;
* retrieval-provider limitations;
* extraction errors;
* model limitations;
* imperfect entity or event resolution;
* evolving evaluation criteria.

The system is not intended to provide investment, legal, regulatory, security, medical, or other professional advice.

Its purpose is to explore a product and research question:

> **Can an evidence-grounded AI research system discover genuinely useful strategic connections across events while remaining willing to say “the evidence is not strong enough” when it cannot?**
