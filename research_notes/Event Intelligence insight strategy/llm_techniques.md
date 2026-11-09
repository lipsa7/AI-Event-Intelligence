# LLM Techniques for Novel but Evidence-Grounded Insights, Hypotheses and Forecasts from News/Event Corpora

Scope note: Researched 2026-10-23. Items marked **[verified this session]** were checked against a fetched page or search result. Items marked **[training knowledge, not re-fetched]** come from well-known papers I know but did not re-open in this session. The report writer should treat those as lower-confidence and spot-check the numbers before quoting them. Dates in parentheses show how old each result is.

---

## 1. How do deep-research products (OpenAI, Gemini, Perplexity, Elicit, Consensus, STORM) structure research and cite claims? Do they separate stated facts from inference?

### Takeaway
All of these systems run a plan, iterative search, read, synthesize loop and cite at the sentence or paragraph level. None that I found publicly marks a claim as "stated in source" versus "inferred by the model". Independent benchmarks show citation accuracy is still well short of perfect and varies by vendor. STORM's own analysis names over-association of unrelated facts as a failure mode, which is the same risk an insight engine has when it links events.

### Cited Findings
- **STORM (Stanford, NAACL 2024; older result).** The pipeline (1) discovers diverse perspectives on a topic, (2) simulates conversations where perspective-specific writers question a topic expert grounded in internet sources, and (3) curates what it collected into an outline before writing. Wikipedia editors judged STORM articles more organized (+25% absolute) and broader in coverage (+10%) than an outline-driven RAG baseline. Named failure modes are transfer of source bias and over-association of unrelated facts. — [Shao et al., "Assisting in Writing Wikipedia-like Articles From Scratch with LLMs" (arXiv 2402.14207)](https://arxiv.org/abs/2402.14207) **[verified this session]**
- **DeepResearch Bench (ICLR 2026).** 100 PhD-level tasks across 22 fields, scored on report quality (RACE) and citations (FACT). FACT extracts statement-source pairs, scrapes each source, and has an LLM judge whether the source supports the statement. It reports Citation Accuracy (share of supported citations) and Effective Citations (supported citations per task). Gemini-2.5-Pro Deep Research and OpenAI Deep Research showed a notable citation-accuracy gap behind Perplexity Deep Research. Gemini produced the most effective citations (about 111 per report), so volume and precision trade off. The authors caution that FACT scores depend on the scraping setup. — [Du et al., DeepResearch Bench (arXiv 2506.11763)](https://arxiv.org/abs/2506.11763); [ICLR 2026 poster](https://iclr.cc/virtual/2026/poster/10008065) **[verified this session, via search snippet]**
- A third-party leaderboard mirror lists Perplexity Sonar-Pro at about 79.7% citation accuracy. This is unverified against the paper. — [alphaXiv benchmark page](https://www.alphaxiv.org/benchmarks/university-of-science-and-technology-of-china/deepresearch-bench)
- **OpenAI deep research (Feb 2025; older).** An agentic browsing model (o3-based at launch) that plans, browses, and backtracks, then writes a cited report. OpenAI acknowledged it can hallucinate and has trouble telling authoritative information from rumor. — [OpenAI, "Introducing deep research"](https://openai.com/index/introducing-deep-research/) **[training knowledge, not re-fetched]**
- **Gemini Deep Research.** Shows the user an editable research plan before browsing, then iterates search and reasoning. **[training knowledge, not re-fetched]** — [Google Gemini Deep Research overview](https://gemini.google/overview/deep-research/)
- **Elicit / Consensus.** These are restricted to scholarly corpora (Semantic Scholar and similar). Elicit extracts per-paper fields into tables with sentence-level quotes supporting each extracted value. Consensus aggregates study findings into a "Consensus Meter". **[training knowledge, not re-fetched]** Neither publicly documents a fact-versus-inference label.

### Inferences
- No product publicly documents a "stated vs. inferred" tag on claims, so this is open design space. An event-intelligence system that labels each sentence (quoted/stated, aggregated, inferred) with its premise citations would be ahead of the commercial products.
- STORM's over-association failure is exactly the risk in cross-event insight generation. An engine that links events should check the link itself (the shared mechanism or actor), not only each endpoint.
- FACT-style auditing (extract claim-source pairs, scrape, have an LLM judge support) is cheap to reuse as an internal regression metric.

### Gaps
- I did not re-verify current (late-2026) product architectures or any official claim-type labeling. Product pages change often.
- I found no published per-vendor rate of inferential claims among cited sentences.

---

## 2. What does research on LLM hypothesis and idea generation show about novelty versus feasibility and grounding, and about filters killing good ideas?

### Takeaway
LLM ideas are rated *more novel* than expert ideas before anyone tests them, but that advantage disappears once the ideas are executed. Evaluating ideas on paper is a poor proxy for their value. The strongest systems (Google AI co-scientist) produce many candidates and rank them with pairwise tournaments instead of filtering each one in isolation. Both the Si et al. study and AI co-scientist report weak automatic evaluation and low diversity.

### Cited Findings
- **Si, Yang, Hashimoto (2024; ICLR 2025).** More than 100 NLP researchers took part in blind review. LLM ideas were rated significantly more novel than expert ideas (p<0.05) and slightly weaker on feasibility. LLM self-evaluation failed, and LLM generation lacked diversity. — [Si et al., "Can LLMs Generate Novel Research Ideas?" (arXiv 2409.04109)](https://arxiv.org/abs/2409.04109) **[verified this session]**
  - The same paper reports duplication: most of 4,000 generated seed ideas were near-duplicates, and only about 200 were unique. **[training knowledge, not re-fetched; check the exact count]**
- **Ideation-Execution Gap (Si, Hashimoto, Yang 2025; ICLR 2026).** 43 experts each spent more than 100 hours executing randomly assigned human or LLM ideas. After execution, LLM ideas lost significantly more score than human ideas on novelty, excitement, effectiveness and overall (p<0.05), which erased their ideation-stage advantage. On many metrics human ideas ranked higher after execution. — [arXiv 2506.20803](https://arxiv.org/abs/2506.20803); [ICLR 2026 poster](https://iclr.cc/virtual/2026/poster/10010564) **[verified this session]**
- **Google AI co-scientist (Feb 2025).** A Gemini 2.0 multi-agent system with Generation, Reflection, Ranking, Evolution, Proximity and Meta-review agents and a Supervisor that allocates compute. Hypotheses compete in an Elo tournament of pairwise "scientific debates", and the Evolution agent refines the top ones. Higher Elo correlated with higher accuracy on GPQA-diamond. Google notes that Elo is an auto-evaluation, not ground truth. On 15 expert-curated goals it beat other agentic and reasoning baselines on Elo. On 11 goals, experts rated its outputs higher for novelty and impact (small sample). Wet-lab results: AML drug-repurposing candidates such as KIRA6 inhibited tumor viability in cell lines; epigenetic targets for liver fibrosis showed activity in hepatic organoids (p<0.01); it independently re-derived an unpublished cf-PICI antimicrobial-resistance mechanism. Stated next steps include better literature review, factuality checking, and better auto-evaluation. — [Google Research blog](https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/); [arXiv 2502.18864](https://arxiv.org/abs/2502.18864) **[verified this session]**
- A Google DeepMind post (dated May 19, 2026) says the co-scientist is being offered to individual researchers as an experimental "Hypothesis Generation" tool. I could not verify the date independently. — [DeepMind blog](https://deepmind.google/blog/co-scientist-a-multi-agent-ai-partner-to-accelerate-research/)
- **SciMON (ACL 2024).** Generates ideas from literature, retrieving "inspirations" (semantic, knowledge-graph and citation neighbors) and iteratively optimizing for novelty against prior work. It found GPT-4 ideas had low technical depth and novelty without that iterative novelty step. — [Wang et al., arXiv 2305.14259](https://arxiv.org/abs/2305.14259) **[training knowledge, not re-fetched]**
- **MOOSE (2023/ACL Findings 2024).** Generates social-science hypotheses from raw web corpora with present-feedback, past-feedback and future-feedback loops. It is the closest analog to news-driven hypothesis generation. — [Yang et al., arXiv 2309.02726](https://arxiv.org/abs/2309.02726) **[training knowledge, not re-fetched]**
- **ResearchAgent (2024).** Iterates problem, method and experiment design over a citation graph plus an entity-centric knowledge store, with multiple LLM "reviewing agents" whose criteria are induced from human judgments. — [Baek et al., arXiv 2404.07738](https://arxiv.org/abs/2404.07738) **[training knowledge, not re-fetched]**
- **The AI Scientist (Sakana, 2024).** End-to-end idea, code, paper and automated review pipeline. Its automated reviewer reportedly reached near-human balanced accuracy on ICLR accept/reject decisions. Novelty checks use Semantic Scholar search. — [Lu et al., arXiv 2408.06292](https://arxiv.org/abs/2408.06292) **[training knowledge, not re-fetched]**

### Inferences
- **Generate wide, then rank pairwise.** A single strict grounding filter applied to each idea is the pattern most likely to kill valuable non-obvious ideas. Co-scientist's design keeps candidates in play and lets comparison and evolution select among them.
- **Novelty is easy for LLMs; value is not.** The ideation-execution gap suggests novelty scores at generation time overestimate quality. For event intelligence, the closest thing to "execution" is the outcome: did the forecast or inference come true? Tracking resolved outcomes is the only ground truth.
- Deduplication and diversity control (Si et al.'s near-duplicate finding, co-scientist's Proximity agent) are needed before ranking, or rankings get crowded by near-duplicate variants.

### Gaps
- I found no study that measures directly how many *ultimately correct* ideas a grounding filter rejects (its false-negative rate). This is the most important missing number for the trade-off.
- I did not fetch exact Elo-vs-GPQA numbers or expert-preference percentages for co-scientist. The PDF fetch failed because it returned binary content.

---

## 3. Attribution and fact verification (AIS, FActScore, SAFE, NLI): do strict checkers penalize valid inferences, and what methods support multi-hop or inferential claims?

### Takeaway
Standard attribution checks (AIS, FActScore, SAFE, NLI) ask whether a single source directly entails an atomic claim. By construction, this marks inferential or multi-hop claims as unsupported. Several papers name the problem ("attribution myopia", underestimated multi-hop explanations), and newer frameworks handle it by separating reasoning steps from verifiable claims, or by checking each premise and then the logic of the chain.

### Cited Findings
- **AIS (Rashkin et al., 2021/2023 CL).** A claim is attributable if "according to source P, s" holds for a generic human reader. It is defined in terms of explicit support. — [Measuring Attribution in NLG Models (arXiv 2112.12870)](https://arxiv.org/abs/2112.12870) **[training knowledge, not re-fetched]**
- **AttributionBench (2024).** The judging guidelines label a claim attributable only if each component is directly and explicitly supported. Support that requires significant interpretation or assumption counts as not attributable. Fine-tuned NLI-style judges still struggled, with roughly 80% macro-F1 at best in-distribution. **[the ~80% figure is training knowledge]** — [AttributionBench (arXiv 2402.15089)](https://arxiv.org/abs/2402.15089) **[guideline text verified this session via search snippet]**
- **FActScore (EMNLP 2023; older).** Splits output into atomic facts and checks each against a knowledge source (Wikipedia). — [Min et al., arXiv 2305.14251](https://arxiv.org/abs/2305.14251) **[training knowledge]**
- **SAFE (DeepMind, NeurIPS 2024).** An LLM splits responses into individual facts and checks each with multi-step Google Search. It agreed with crowd annotators 72% of the time on about 16k facts, won 76% of 100 sampled disagreements, and cost more than 20x less than humans. — [Wei et al., "Long-form factuality in LLMs" (arXiv 2403.18802)](https://arxiv.org/abs/2403.18802) **[verified this session]**
- A 2025 replication on 786 FActScore biography facts (Fleiss κ 0.77) measured human agreement at 84.5% for SAFE ($0.49 and 28 min per response), 89.3% for BAFE, and 70.0% for FActScore's own pipeline (limited to fixed Wikipedia pages). — [arXiv 2505.23295](https://arxiv.org/abs/2505.23295) **[verified this session via search snippet]**
- **"Attribution myopia" (LogicScore, 2026).** Claim-level attribution checks ignore the global logical integrity of long answers. An example system scored 92.85% attribution precision but only 35.11% on another dimension, with a broken entity-linking chain. — [arXiv 2601.15050](https://arxiv.org/abs/2601.15050) **[verified this session via search snippet; the metric naming should be checked in the paper]**
- **Multi-hop explanations underestimated (EMNLP 2021; older).** Scoring explanations against gold references substantially underestimates models, which often produce valid explanations that differ from the gold one. — [ACL Anthology 2021.emnlp-main.596](https://aclweb.org/anthology/2021.emnlp-main.596.pdf) **[verified this session via search snippet]**
- **Separating reasoning steps from verifiable claims (MURGAT, 2026).** First identifies sentences with directly observable claims that need grounding, then scores attribution only on those, so reasoning steps are not penalized. — [arXiv 2602.11509](https://arxiv.org/abs/2602.11509) **[verified this session via search snippet]**
- **Decomposition granularity matters (CLATTER, 2025).** Low atomicity yields long, complex sub-claims that make entailment classification harder. How claims are split changes how strictly they are scored. — [arXiv 2506.05243](https://arxiv.org/abs/2506.05243) **[verified this session via search snippet]**
- **Chain verification.** Chain-of-Verification (CoVe) drafts an answer, plans verification questions, answers them independently, and revises. — [Dhuliawala et al., arXiv 2309.11495](https://arxiv.org/abs/2309.11495) **[training knowledge]**

### Inferences
- **Recommended verification design for inferential insights:**
  1. Classify each sentence as *stated* (must be entailed by one cited source), *aggregated* (entailed by a union of cited sources), or *inferred*.
  2. For inferred claims, require an explicit premise list. Check each premise with NLI or AIS against its own source.
  3. Check the inference step separately, with an LLM judge for logical validity or plausibility given the premises, *not* entailment by a source.
  4. Label inferred claims as such in the output, with a confidence value.
- This avoids the known failure where a single NLI or AIS check rejects every non-obvious claim, while still catching unsupported premises, which is the actual hallucination risk.
- Split claims carefully: too coarse and NLI fails on complex sentences, too fine and the inference disappears.

### Gaps
- I found no source that quantifies how often strict attribution checkers reject *valid* multi-hop inferences (their false-rejection rate). That would need an in-house labeled set.
- I found no off-the-shelf "premise-level + inference-step" verifier benchmarked on news or geopolitical text.

---

## 4. How well do LLMs forecast from news, how do they retrieve base rates and precedents, and what are the temporal-leakage issues?

### Takeaway
Retrieval-augmented LLM forecasters approach but do not yet match crowd and superforecaster accuracy. FRI's late-2025 data put the best model at a Brier score of about 0.101 versus 0.081 for superforecasters, and FRI projected parity around late 2026. Gains come from news retrieval, explicit base-rate reasoning, ensembling, and blending with the crowd. Backtests are easily inflated by temporal leakage through date-filtered search and misread knowledge cutoffs.

### Cited Findings
- **Halawi, Zhang, Yueh-Han, Steinhardt (NeurIPS 2024; older).** The pipeline: an LM generates search queries (expansion plus decomposition into sub-questions) → NewsCatcher and Google News → GPT-3.5 rates relevance from title plus first 250 words (about 70% cost saving) → top-15 summaries → GPT-4 scratchpad (rephrase the question, argue for and against, weigh considerations, check calibration against base rates) plus a fine-tuned GPT-4 → trimmed-mean ensemble.
  - Dataset: 5,516 binary questions from Metaculus, GJOpen, INFER, Polymarket and Manifold (914 in the test set, all opened after the model cutoff).
  - Brier score: system 0.179 vs. crowd 0.149, but system+crowd blend 0.146 beats the crowd. The system roughly matches or beats the crowd when the crowd is uncertain (0.3 to 0.7: 0.238 vs. 0.240) and when all selective criteria hold (0.240 vs. 0.247).
  - Ablations: no fine-tuning → 0.186; no fine-tuning and no retrieval → 0.206.
  - Leakage controls: post-cutoff test set and retrieval dates set independently of resolution date. The authors found no evidence of leakage from post-training.
  - — [arXiv 2402.18563](https://arxiv.org/html/2402.18563v1) **[verified this session]**
- **ForecastBench (ICLR 2025).** A dynamic benchmark of questions that are unresolved at forecast time, which avoids leakage by design. On a 200-question subset: superforecasters 0.093, public 0.107, best LLM (Claude 3.5 Sonnet) 0.111. — [ICLR 2025 paper](https://proceedings.iclr.cc/paper_files/paper/2025/hash/ea74e45a229dac70b5b63b28d8934db6-Abstract-Conference.html); [arXiv 2409.19839](https://arxiv.org/abs/2409.19839) **[verified this session]**
- **FRI update (Oct 2025).** Best model GPT-4.5 Brier 0.101 vs. superforecasters 0.081. FRI projected LLM-superforecaster parity in late 2026 (95% CI Dec 2025 to Jan 2028). The median public forecast fell from #2 to #22 as LLMs passed it. Critics note that the superforecaster baseline comes from a one-time 2024 engagement and that the benchmark covers binary questions only. — [FRI Substack](https://forecastingresearch.substack.com/p/ai-llm-forecasting-model-forecastbench-benchmark) **[verified this session]** I did not get current (Oct 2026) leaderboard standings; check [forecastbench.org](https://www.forecastbench.org).
- **Pitfalls in Evaluating LM Forecasters (Paleka, Goel, Geiping, Tramèr; ICLR 2026).** Groups the problems into (a) whether results can be trusted given temporal leakage and (b) whether benchmark performance transfers to real-world forecasting. Unreliable date-restricted retrieval and misread knowledge cutoffs lead to overly optimistic backtests. The gold standard is to forecast live and score after resolution. — [arXiv 2506.00723](https://arxiv.org/abs/2506.00723) **[verified this session via search snippets]**
- A 2026 study (WorldReasoner) reports that without filtering by knowledge cutoff, parametric-only benchmarks overstate forecasting ability. **[summary only, unverified]** — [emergentmind 2606.11816](https://www.emergentmind.com/papers/2606.11816)

### Inferences
- An event-intelligence forecasting module should (1) retrieve explicit base rates and precedents, (2) ensemble several reasoning samples with a trimmed mean, (3) blend with external crowd or market prices where available, and (4) concentrate effort on questions where the crowd is uncertain.
- **Leakage hygiene:** store a timestamp on every document at ingestion. Evaluate only on post-cutoff, live-resolved questions. Do not trust a search engine's date filters, because pages get updated after publication.

### Gaps
- I did not obtain 2026 ForecastBench leaderboard values, so I cannot confirm whether parity was reached.
- I found no rigorous ablation isolating the value of *precedent and analogy retrieval* specifically, as distinct from general news retrieval.

---

## 5. How do event memory, temporal knowledge graphs and GraphRAG support "this happened before" reasoning, and what do they cost?

### Takeaway
GraphRAG (entity graph, community summaries, map-reduce) improves "global" sense-making answers over plain vector RAG, but indexing is expensive. Bi-temporal knowledge graphs (Zep/Graphiti) handle facts that change over time by invalidating old edges instead of deleting them, and report gains on temporal-reasoning memory benchmarks. Most of that evidence comes from vendors.

### Cited Findings
- **GraphRAG (Microsoft, 2024).** An LLM extracts an entity knowledge graph from documents, then pregenerates summaries for communities of closely related entities. At query time each community summary yields a partial answer, and these are combined into a final answer. On global questions over corpora of about 1M tokens, it substantially improves comprehensiveness and diversity over conventional RAG. — [Edge et al., arXiv 2404.16130](https://arxiv.org/abs/2404.16130) **[verified this session]**
  - Communities come from Leiden clustering. Reported win rates over naive RAG were about 72 to 83% on comprehensiveness. **[training knowledge, not re-fetched]**
  - Cost: Microsoft later released LazyGraphRAG, which defers LLM summarization to query time, with indexing cost reported at about 0.1% of full GraphRAG. — [Microsoft Research blog, LazyGraphRAG (Nov 2024)](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/) **[training knowledge, not re-fetched]**
- **Zep / Graphiti (2025).** A temporally aware knowledge graph engine. Its bi-temporal model tracks both when an event occurred and when it was ingested. Conflicting facts get an `invalid_at` timestamp on the old edge rather than being deleted. Reported results: Deep Memory Retrieval 94.8% vs. MemGPT 93.4%; LongMemEval accuracy up to +18.5% with 90% lower latency. These are vendor-reported, and Zep itself calls DMR limited. — [arXiv 2501.13956](https://arxiv.org/abs/2501.13956); [Zep blog](https://blog.getzep.com/zep-a-temporal-knowledge-graph-architecture-for-agent-memory) **[verified this session]**
- **Event-centric knowledge graphs (GDELT, EventKG).** GDELT codes news events with CAMEO actor and event codes, updated every 15 minutes. EventKG is a multilingual event-centric temporal knowledge graph built from Wikidata, DBpedia, YAGO and Wikipedia event lists. — [GDELT Project](https://www.gdeltproject.org/); [Gottschalk & Demidova, EventKG (arXiv 1804.04526)](https://arxiv.org/abs/1804.04526) **[training knowledge, not re-fetched]**

### Inferences
- A practical design for "this happened before": keep an event table (actor, action, target, time, location, source IDs) with valid-from and valid-to plus ingest time, as in Graphiti's bi-temporal model. Resolve entities to canonical IDs, then retrieve precedents by structured filter (same actor pair and action class) before falling back to embeddings. This partly addresses the surface-similarity problem described in section 6.
- GraphRAG-style community summaries suit periodic "what are the big themes" briefings. For per-event precedent lookup, a cheaper lazy approach or structured event retrieval is likely enough.

### Gaps
- I found no independent (non-vendor) benchmark comparing temporal knowledge graphs to vector RAG for *news* precedent retrieval.
- I did not find published cost-per-million-tokens figures for GraphRAG indexing on news-scale corpora, or entity-resolution accuracy for GDELT-style actor coding.

---

## 6. How reliable is LLM-as-judge for "insightfulness" and novelty, and how should pairwise judging, absolute scoring, human ratings and calibration be used?

### Takeaway
LLM judges are unreliable for novelty and insight. Si et al. found that LLM self-evaluation fails, and the ideation-execution gap shows even expert ratings at ideation time mislead. Pairwise or tournament comparison (co-scientist's Elo) is the most-used structure, but Elo is an internal metric and needs human or outcome anchoring. Known biases (position, verbosity, self-preference, prompt-template sensitivity) need controls, and agreement should be reported with chance-corrected statistics against a human-human baseline.

### Cited Findings
- Si et al. 2024: LLM self-evaluation of ideas fails and does not substitute for expert review. — [arXiv 2409.04109](https://arxiv.org/abs/2409.04109) **[verified this session]**
  - The best LLM judge's pairwise agreement with human reviewers (about 53%) was below human-human agreement (about 56%), and both are only modestly above chance. **[training knowledge, not re-fetched; check the numbers]**
- Execution results reverse ideation-time preferences, so ratings before execution (human or LLM) are a weak signal of value. — [arXiv 2506.20803](https://arxiv.org/abs/2506.20803) **[verified this session]**
- Co-scientist's Elo correlated with GPQA accuracy, but Google calls it an auto-evaluation without independent ground truth. — [Google Research blog](https://research.google/blog/accelerating-scientific-breakthroughs-with-an-ai-co-scientist/) **[verified this session]**
- A systematic study of LLM judges on alignment tasks found prompt templates significantly affect judge performance, and alignment with human evaluators was mediocre. — [arXiv 2408.13006](https://arxiv.org/html/2408.13006v1) **[verified this session via search snippet]**
- MT-Bench / Chatbot Arena (2023; older): strong judges (GPT-4) reach over 80% agreement with humans on pairwise chat preferences, about the human-human level, but show position, verbosity and self-enhancement biases. Mitigations include swapping order and averaging. — [Zheng et al., arXiv 2306.05685](https://arxiv.org/abs/2306.05685) **[training knowledge, not re-fetched]** This is general chat quality, not novelty, and should not be assumed to transfer.
- Practitioner guidance: use Cohen's kappa rather than raw agreement, report judge-human and human-human agreement separately, and measure judge self-consistency across runs. — [Arize blog](https://arize.com/blog/measuring-human-llm-judge-alignment/) (vendor, not peer-reviewed)
- The claim that pairwise judging is more consistent than absolute scoring, at O(N²) cost, comes from a vendor source and is not peer-reviewed evidence. — [ZeroEntropy](https://www.zeroentropy.dev/concepts/llm-as-judge/)

### Inferences
- Use LLM judges to *rank and triage* (pairwise, order-swapped, multiple judge models), not to make absolute "is this insightful?" gate decisions.
- Build a small human-rated calibration set (for example, 100 to 300 insights rated by analysts) and report kappa against the human-human baseline. Recheck after every prompt or model change.
- The final calibration signal is outcome-based: track which insights or forecasts resolved correctly, and use Brier score where possible.

### Gaps
- I found no peer-reviewed benchmark of LLM judges for *insightfulness of news/event analysis* specifically.
- I found no head-to-head study of pairwise versus absolute scoring for novelty judgments.

---

## 7. Can LLMs retrieve useful analogies and historical precedents?

### Takeaway
This is an early field with mostly 2025-2026 preprints. LLMs can produce plausible historical analogies, but agents retrieve analogies by surface features rather than underlying mechanism. Structural decomposition helps somewhat. Retrospective evaluation is contaminated because outcomes are in pretraining data.

### Cited Findings
- **Analogical Deep Research (2026).** Introduces the ADR benchmark. Agents find analogies poorly because they match surface features rather than underlying mechanisms. The proposed CANA framework (structural decomposition plus feedback) gives up to 10% improvement. — [alphaXiv 2607.13602](https://www.alphaxiv.org/abs/2607.13602.md) **[search snippet only]**
- **"Past Meets Present: Creating Historical Analogy with LLMs" (ACL 2025).** LLMs show good potential for historical analogy, improved by self-reflection that targets hallucination and stereotypes. — [ACL 2025 long paper](https://preview.aclanthology.org/setup/2025.acl-long.200) **[search snippet only]**
- An agents study (2026) builds geopolitical evidence packets combining historical analogs (e.g., 31), expert assessments (76) and simulation runs (736). — [arXiv 2608.00339](https://arxiv.org/pdf/2608.00339) **[search snippet only]**
- ForecastAgentSearch (2026) argues that most geopolitical forecasting systems use a single predictor or a fixed expert ensemble, and proposes a multi-expert agent search. — [arXiv 2606.31665](https://arxiv.org/pdf/2606.31665) **[search snippet only]**

### Inferences
- Retrieve precedents by structure (actor type, action class, stakes, sequence of moves) using the event knowledge graph from section 5, not by embedding similarity of article text alone.
- Have the model state *why* each precedent is analogous (the mechanism mapping) and *where it differs*, and verify that mapping as an inferred claim under section 3.

### Gaps
- None of the analogy papers was fully read in this session; the numbers are from snippets.
- I found no evidence on whether precedent retrieval measurably improves forecast Brier scores.
