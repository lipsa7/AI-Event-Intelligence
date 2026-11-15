# Evidence-grounded strategic intelligence: research and implementation

Researched 23 October 2026. This is an implementation rationale, not evidence that the product's advice has improved. No paid model calls were made for this work.

## What comparable products actually demonstrate

AlphaSense describes analysis over company filings, earnings transcripts, broker research and expert interviews, with source-linked answers. Klue describes decision-specific competitive advice using buyer conversations, win/loss interviews and internal materials. These are evidence and workflow advantages; vendor descriptions are not independent demonstrations that arbitrary hidden strategic insights can be generated reliably.

Sources: [AlphaSense research workflow](https://www.alpha-sense.com/deep-research-for-users/), [Klue competitive intelligence workflow](https://klue.com/ai).

For this app, the justified design is to ask a particular decision question, retrieve evidence relevant to the missing reasoning step, and evaluate whether the conclusion changes that decision. It is not justified to assume a larger hypothesis tournament, more dramatic wording, or an enormous dependency graph will by itself solve quality.

## Findings and how they are applied

| Finding | Implementation | What it does not establish |
|---|---|---|
| Explicit goals help steer analysis. InsightBench reported 0.60 vs 0.40 for specific vs generic goals in one configuration. | Retain the optional organisation profile; compare it against a baseline with controlled code/settings and checkpoint contents. | InsightBench uses structured datasets with planted insights. Its improvement is not an expected gain for news-based strategy. |
| Sentences can mix supported facts with unsupported reasoning. | Keep the existing per-claim grounding and add a distinct substance audit: factual support, causal-bridge support, ordinary reading, added inference, decision consequence, rival explanation, discriminator and assumptions. | A model's support judgment is fallible; a citation is not proof of a causal mechanism. |
| Judges are sensitive to style and verbosity. | Require a literal plain claim before awarding informational lift; subsequent pairwise comparisons use the audited content and source excerpts without original rhetorical prose or provenance badges. Missing audits, factual support or preserved uncertainty cap scores. | Prompt normalization and fake-judge tests do not prove real-model invariance. Controlled paraphrase tests with real judgments remain necessary before trusting performance. |
| Intelligence analysis should expose assumptions and alternatives. | Unsupported bridges cannot earn mechanism support or informational lift merely because they are labelled assumptions. The dashboard shows the plain claim, decision change and key assumption. | Source-checked hypotheses are not automatically novel, decision-relevant or correct forecasts. |
| Retrieval and aggregation can improve forecasting in measured tasks. | Keep the existing bounded research stages; make their query planner seek the missing claim, primary documents, alternative explanations and counterevidence. | Forecast probability accuracy does not transfer automatically to open-ended strategic advice. This change does not add ensembles or additional research calls. |
| Historical analogies need validation and consequential differences. | Retain mechanism-matched search, dated outcomes, required analogy limitations and small-search-sample labels already present. | Search-picked examples are not representative base rates. |
| More context does not ensure better use of evidence. | Preserve evidence excerpts attached to individual ranked items; resolve lab evidence IDs to text rather than presenting opaque IDs as evidence. | This is not a new long-context retrieval model or a guarantee that all relevant information is found. |
| Human evaluation must separate substance from origin and keep experimental conditions stable. | Blind exports include evidence and assumptions without run origin or scores. Identical text with different evidence is not deduplicated. Imports reject mismatched row text. Reports record code/model control signatures and checkpoint-content hashes; comparison rejects changed controls. | A few reports from one story and one rater cannot establish general quality or a reliable statistical noise estimate. |

Sources: [InsightBench](https://arxiv.org/html/2407.06423v3), [FActScore](https://aclanthology.org/2023.emnlp-main.741/), [ALCE citation evaluation](https://aclanthology.org/2023.emnlp-main.398/), [LLM judge bias study, revised June 2026](https://arxiv.org/abs/2604.23178), [ODNI analytic standards](https://www.odni.gov/files/documents/ICD/ICD-203.pdf), [forecasting with retrieval](https://arxiv.org/html/2402.18563v1), [historical analogy generation](https://arxiv.org/abs/2409.14820), [Lost in the Middle](https://arxiv.org/abs/2307.03172).

## The stronger-wording problem

“The prime minister contacted a company's CEO” is an observation. “Governments have privatised cyber diplomacy” additionally claims a general institutional shift and a causal explanation. Checking the contact alone does not establish that shift.

The evaluator now asks for the ordinary reading, the genuinely additional inference, its support, and the decision it changes. If the new content is only a label, information lift is zero. If the causal bridge is an assumption, the item can remain visible as an observation or research lead but cannot earn supported-mechanism credit. There is no word-count reward; short hypotheses are not rejected just for failing length thresholds. Existing evidence, actor-specificity and leverage checks still apply.

There is no sound regular expression that can detect every empty metaphor or establish semantic equivalence. The tests verify that the application obeys the audit fields, that missing or unsupported audits cannot score highly, and that equal audit/evidence inputs receive equal scores and comparison views. They do not test the quality of live model-generated audits.

## Actor typing: actual fault and fix

Extraction selected the missing `en_core_web_sm` model. It then silently disabled strict NER and used the generative model's entity_type. The installed `en_core_web_trf` model was loaded and tested successfully offline.

The loader now uses that installed model if the default small model is absent and no explicit model was configured. Explicit settings are respected. No model or dependency is downloaded. Actor type comes only from an NER span covering the complete mention; mere overlap is insufficient. A GPE is a country, not automatically a government institution. Missing required NER stops extraction before model calls and cannot invoke the legacy fallback to bypass the requirement. An explicit non-strict mode retains only an unverified type and marks extraction incomplete. New tests cover missing models, NER errors, malformed spans, person/organisation disagreement and embedded/partial names.

## Primary evidence and bounded scope

The gap planner now specifically asks for applicable rule text (actor, obligation, effective date, conditions/exemptions and consequence), official incident disclosures, filings, transcripts, dependencies, switching costs and alternatives. It asks for evidence distinguishing the main explanation from its strongest rival, and forbids using later outcomes to justify earlier forecasts. These requirements use the existing search and exact-quote/date-validation paths.

This improves the research instructions; it is not a claim that the app now has access to AlphaSense's paid corpus, internal buyer interviews, or complete primary-document coverage. SEC documents can be accessed through official public submission/XBRL APIs without an API key, but this work does not install a new SEC connector. [SEC API documentation](https://www.sec.gov/search-filings/edgar-application-programming-interfaces).

Already implemented profile experiments, counterparty consequences, independent per-fact corroboration, analogy limits, source labels and bounded precedent-stage calls are retained rather than reimplemented. A persistent global dependency graph, additional model ensembles, paid data feeds and broader autonomous research are not justified until the smaller evidence/decision test succeeds.

## Validation and next decision

Offline tests cover application behavior, not real-world insight quality. Existing extractor fixtures now explicitly supply NER spans instead of relying on model-supplied types. Scoring fixtures include the new audit when testing old score caps. An unrelated old v5.3.2 version assertion was observed to fail against the existing v5.3.3 extractor; it was not changed to conceal the failure.

The full legacy suite has not been certified. Existing saved reports are preserved. Reports generated before this change must not be mixed with the new version in an A/B comparison.

Before spending on three more full replays, use saved reports to build a small fixed set of examples: useful supported inference, routine advice, dramatic restatement, unsupported causal story, contradicted hypothesis and ordinary factual briefing. Include plain/dramatic paraphrase pairs. First inspect the evidence and human verdicts; only then use a bounded evaluation of the relevant stage. Measure factual support, novelty over the ordinary reading, decision relevance, paraphrase stability and cost separately. These are proposed evaluation criteria, not published acceptance thresholds.

No live Gemini evaluation, research replay, dependency installation, environment-file edit or model download was performed. Reusing the existing calls with richer audit content can increase token cost even without increasing call count.
