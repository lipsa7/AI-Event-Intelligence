# AI Event Intelligence Gold-Set Definitions

**Definition version:** `gold-semantics-v1.0`

Freeze this file before labeling. If any definition changes during the labeling
pass, increment the definition version and relabel every row created under the
old definition.

## Labeling protocol

1. Label from the event text and full source document only.
2. Do not inspect EVENT-* ids, current model outputs, old admission reasons or
   actor-sidecar outputs while labeling.
3. Label actor/identity/article/linkage/material-development first.
4. Never label `should_admit` manually; it is derived after labels are frozen.
5. Use `uncertain` rather than forcing certainty.
6. A positive semantic judgment should have a source span in
   `supporting_source_spans` when the support is not explicit in the event text.

## Article topic

### AI_PRIMARY
Artificial intelligence or AI-enabled technology is a primary subject of the
article and materially organizes the article's claims.

Positive: an article centered on an AI drug-discovery platform and its deals.
Negative: a general earnings article that mentions AI once.

### AI_MATERIAL
AI is not necessarily the article's main subject, but AI materially affects the
article's central development.

Positive: a semiconductor capacity article where the capacity is explicitly
being built for AI training/inference demand.
Negative: a generic data-center article with no evidenced AI relationship.

### AI_CONTEXTUAL
AI appears as surrounding context but does not materially determine the
article's central development.

### NOT_AI
The article does not establish a meaningful AI relationship.

### UNRESOLVED
The source does not support a confident article-topic judgment.

## Event AI linkage (gate-critical taxonomy)

### DIRECT_MATERIAL
The event itself materially involves AI or AI-enabled technology as part of the
action, asset, product, service, agreement or observed development.

Positive: `Company X signed a collaboration to use its generative-AI discovery platform.`
Negative: `Company X reported quarterly cash.`

### INDIRECT_MATERIAL
The source establishes a material causal, enabling, constraining or consequence
relationship between this event and AI, although AI is not the direct object of
the event action.

Positive: `A licensing transaction commercializes a molecule that the source
establishes was produced through the company's AI platform.`
Negative: `An AI company raised generic financing with no source evidence tying
that financing to AI activity.`

### CONTEXTUAL_OR_NONE
The article or actor may be AI-related, but this particular event lacks a
material source-supported AI relationship.

Positive: `A drug candidate completed patient dosing` where the article gives no
source-grounded reason that the dosing milestone itself materially involves AI.

### UNRESOLVED
The source is insufficient or genuinely ambiguous.

## Actor identity

### explicit
The source explicitly names the canonical institutional actor in the event or
an unambiguous equivalent construction.

### source_local_alias
A source-local alias/abbreviation explicitly maps to the canonical actor, e.g.
`China Medical System (CMS)` followed by `CMS`.

### strict_coreference
The mention and antecedent refer to the same entity, e.g. `Insilico Medicine ...
the Company ...`.

### bridging
The mention is a distinct subunit/related entity whose parent institutional
actor is source-grounded, e.g. `Insilico Medicine's UAE team` -> canonical actor
`Insilico Medicine`, relation `organizational_subunit_of`.

### unresolved
The source does not uniquely resolve the canonical actor.

## Material development

### YES
The event describes an observed or committed real-world action, change, outcome,
transaction, launch, approval, restriction, milestone or other material
development rather than stable background/boilerplate.

### NO
Stable background, generic description, boilerplate, opinion or non-development.

### UNRESOLVED
The source is insufficient or ambiguous.

## Derived admission contract

`should_admit = YES` only when all are true:

- article topic is `AI_PRIMARY` or `AI_MATERIAL`;
- event linkage is `DIRECT_MATERIAL` or `INDIRECT_MATERIAL`;
- actor role is `action_subject` or `passive_agent`;
- actor identity is `explicit`, `source_local_alias`, `strict_coreference` or `bridging`;
- a canonical actor is present;
- material development is `YES`.

Evidence count is deliberately **not** part of event admission. The downstream
StrategicInference two-support-record requirement applies at candidate/insight
scope, not as a per-event admission rule.
