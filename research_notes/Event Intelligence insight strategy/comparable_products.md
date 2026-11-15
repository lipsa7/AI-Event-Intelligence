# Comparable Products: How Commercial Intelligence Products Turn Events into "Insights", and Where They Stop Short of Original Strategic Inference

Scope note: Research was done 2026-10-23 with web search and fetch (about 20 calls). Most findings come from vendor press releases, blogs and product pages, so they are **marketing claims** unless marked otherwise. Independent verification of behaviour was rarely available. Several sources are from 2023-2025 and are marked with dates. Some products (Owler, Contify, Bloomberg's newer Terminal AI features after 2024, CB Insights' AI analyst internals) got only shallow or no coverage, as listed under Gaps.

## 1. Market and competitive intelligence products (AlphaSense/Tegus, Klue, Crayon, CB Insights, Feedly, Bloomberg)

### Takeaway
Market and CI products ground generative output in a curated corpus, either licensed content or their own datasets, and attach citations back to source documents. Their "insights" are mostly attributed summaries, extractions, rankings and enablement content such as battlecards and deal tips. They do not offer independent strategic inference with stated confidence. The closest thing to "what to do next" is sales-tactic advice (Klue Deal Tips, Crayon-generated talk tracks), not corporate strategy.

### Cited Findings
**AlphaSense (incl. Tegus, Generative Search, Deep Research)**
- AlphaSense and Tegus combined their expert-transcript libraries "as of July 2024". A Sept 2024 AlphaSense blog cites "a library of over 50,000 expert transcripts with audio" — [AlphaSense blog, 2024-09-02](https://www.alpha-sense.com/blog/product/ai-in-expert-insights)
- Third-party comparison sites put the combined Tegus library at about 260,000 transcripts across 27,000+ companies. This is unverified and conflicts with the 50k figure above, which may be dated or measured differently. Acquisition date is reported inconsistently (Dec 2023 vs 2024) — [ZoomInfo comparison](https://pipeline.zoominfo.com/sales/alphasense-vs-tegus); [Sacra](https://sacra.com/research/tegus)
- Tegus transcripts appear in Generative Search results and carry Tegus branding — [AlphaSense help center](https://help.alpha-sense.com/en/articles/7225201-expert-transcripts-overview)
- Generative Search is described as a chatbot "designed to summarize billions of data points". Smart Summaries apply genAI to document sets. Smart Synonyms (NLP) expand searches across expert-call transcripts. The 2024 blog says nothing about citation mechanics, grounding or hallucination controls — [AlphaSense blog](https://www.alpha-sense.com/blog/product/ai-in-expert-insights)
- Deep Research (announced 2025-06-10) is an agentic feature that runs "dozens of searches", draws on 500M+ business and financial documents, can add a customer's internal library to contrast internal and external views, and links back to sources through "granular citations" (SVP Product Chris Ackerson). The company markets it as working "like a team of highly skilled analysts", which is a vendor claim. Example use cases are M&A target screening and competitive landscape analysis — [AlphaSense press release](https://www.alpha-sense.com/press/alphasense-launches-deep-research-automating-in-depth-analysis-with-agentic-ai-on-high-value-content); [AlphaSense blog](https://www.alpha-sense.com/blog/product/introducing-deep-research/)
- A third-party developer profile describes AlphaSense inline citations that trace answers to source document, page and span, and sentence-level citations meant to reduce hallucination. This is secondary and unverified — [apis.io profile](https://providers.apis.io/providers/alphasense/); [altss profile](https://altss.com/profile/alphasense.md)
- User reviews (G2, mirrored on AWS Marketplace and SaaSworthy, 2021-2026) are mostly positive on accuracy and "easy to follow references". Complaints: the "generative grid can be a little hit and miss", results are sometimes irrelevant, "difficult to determine which sources are reliable", information overload ("tangential rabbit hole"), thin coverage of private, Russian/CIS and Asian companies, and cost — [G2 review](https://g2.com/products/alphasense/reviews/alphasense-review-10027827); [SaaSworthy](https://www.saasworthy.com/product/alphasense/reviews)

**Klue (Compete Agent)**
- Compete Agent (blog dated 2025-06-10) draws on the public web (competitor sites, review sites), Gong call transcripts, win-loss interviews, battlecards and internal docs, and CRM data — [Klue blog](https://klue.com/blog/compete-agent)
- Behaviours described: when a deal is marked Closed Won, it reads the Gong transcripts and publishes a "Win Story" card. It builds competitor profiles on request and refreshes them every 24h. It compares win-loss reasons across quarters. It scans live-deal transcripts for competitor mentions and cross-references them with Klue content. "Deal Tips" suggest competitive tactics in the seller's inbox. "Ask Klue" answers in Slack link to original sources and can include pricing points and "landmines to set" — [Klue blog](https://klue.com/blog/compete-agent)
- Klue claims "every output is backed by human oversight to ensure accuracy and trust". The blog gives no accuracy metrics and does not describe the review process — [Klue blog](https://klue.com/blog/compete-agent)
- Reviewers say Klue needs a dedicated CI or PMM owner to get full value, and pricing is opaque (around $16k/yr per Vendr data cited by a third party) — [ZoomInfo: Klue alternatives](https://pipeline.zoominfo.com/sales/klue-alternatives)

**Crayon**
- "AI importance scoring" sorts captured competitor changes and insights from high to low importance and pushes high-priority ones to inboxes or a daily email. User interactions (star, share, comment) retrain what counts as important. Example given: a competitor homepage change outranks a recent tweet — [Crayon blog](https://www.crayon.co/blog/crayon-ai)
- Crayon's CEO said the model flags about 3% of data points as high importance. This comes from an older funding article and may be outdated — [VentureBeat](https://venturebeat.com/business/market-intelligence-platform-crayon-raises-22m)
- Crayon's "Sparks" is reported to generate battlecards, talk tracks and SWOTs from raw intel. Users report noisy feeds that need manual filtering. This is third-party and unverified — [ZoomInfo: Crayon vs Klue](https://pipeline.zoominfo.com/sales/crayon-vs-klue)

**CB Insights (ChatCBI, Team of Agents)**
- ChatCBI is a Q&A tool over CB Insights' own proprietary datasets. Its product page has a "Straight to the source" section, and the API page shows a "Query Response Sources" heading. Neither page describes how granular the citations are — [ChatCBI page](https://cbinsights.com/chatcbi/); [CB Insights API](https://cbinsights.com/what-we-offer/api/)
- "Team of Agents" scouting compiles presentation-ready reports with citations — [CB Insights Team of Agents](https://cbinsights.com/team-of-agents)
- CB Insights has a partnership that puts its curated research into Perplexity — [CB Insights / Perplexity PR](https://www.cbinsights.com/perplexity-pr)

**Feedly (Leo; Market and Threat Intelligence)**
- Leo is Feedly's AI assistant. Market Intelligence is built around "AI Feeds" (NLP concept filters). One review claims 1,000+ AI models scan the open web, which is unverified — [Copy.ai review](https://copy.ai/go-to-market-tools/feedly-review); [Feedly docs category](https://docs.feedly.com/category/728-market-intelligence)
- Threat Intelligence: "Ask AI" can synthesise up to 25 reports and extract IoCs, TTPs and CVEs. "AI Insights" auto-extracts threat actors and malware, with export to STIX, MISP and JSON. "AI Actions" lets a user, for example, request threat actors and their TTPs as a table — [Feedly TI landing page, Mar 2025](https://feedly.com/threat-intelligence/landing/john-hammond-ai-video-mar25)
- Leo can summarise an article inside the ThreatConnect integration — [ThreatConnect marketplace](https://threatconnect.com/marketplace/feedly/)

**Bloomberg Terminal**
- AI-Powered Earnings Call Summaries launched 2024-01-22. Topics covered include guidance, capital allocation, hiring, macro, products, supply chain and demand — [TechMonitor](https://www.techmonitor.ai/digital-economy/ai-and-automation/bloombergs-ai-powered-earnings-call-summaries); [Fintech News SG](https://fintechnews.sg/84034/ai/bloomberg-introduces-ai-powered-earnings-call-summaries-for-enhanced-financial-analysis/)
- Bloomberg says the summaries are shown next to the original transcript. Andrew Skala said the tool is "more focused... on highlighting information and guiding analysts to relevant parts of call transcripts" than on pure summarising, as an effort to eliminate hallucinations. Bloomberg Intelligence analysts helped tune quality. Amanda Stent said humans tag topics, step in when the system is unsure, and check summaries to retrain models — [Tech Brew, 2024-02-02](https://www.techbrew.com/stories/2024/02/02/bloomberg-ai-tool-quarterly-earnings); [WatersTechnology](https://waterstechnology.com/node/7951936)

### Inferences
- The products share one design pattern: a curated or licensed corpus, retrieval, an LLM summary or extraction, and a citation link back. In this pattern "insight" means a faster, attributed reading of what sources say, not the system's own judgment.
- In CI tools "recommended action" means sales-enablement tactics (landmines, talk tracks, objection handling), grounded in the customer's own win-loss data. None of the products reviewed markets "what this company should do strategically next".
- Bloomberg's "guide the analyst to the source passage" framing is a deliberate retreat from free-form synthesis toward highlighting. It is the most explicitly conservative posture found.

### Gaps
- No independent accuracy or hallucination benchmarks were found for any product in this group.
- Owler and Contify were not researched because of tool budget; nothing is recorded about them.
- Bloomberg's newer Terminal genAI features (2025-2026, e.g. news summaries or ASKB-style assistants) were not found in these searches and remain unverified.
- How AlphaSense surfaces divergent expert views (e.g. bull vs bear experts) was not documented publicly. Not found.
- Neither Crayon's Sparks nor Feedly's "AI Actions" has official, detailed documentation in the results.

## 2. Event, threat and risk intelligence products (Recorded Future, Dataminr, Palantir, Strider, Seerist)

### Takeaway
Event and threat products put explicit layers in place that CI tools lack. Recorded Future has rule-based numeric risk scores and an entity graph. Seerist claims analyst-confirmed events with severity scores. Dataminr detects events and keeps live-updating briefs, with agentic "context" on top. Palantir grounds LLMs in an operational ontology and stages AI-proposed actions for human approval. Even so, published materials show no product presenting its own forward-looking strategic judgment with calibrated confidence. Predictive or "next steps" features appear only on roadmaps.

### Cited Findings
**Recorded Future**
- Risk scores run 0-99 and come from rule sets specific to each indicator type. The most severe triggered rule sets the base score: very malicious 90, malicious 65, suspicious 25, unusual 5. Scores are attributes of entities in the Intelligence Graph and update in real time from graph relationships — [Recorded Future browser extension page](https://www.recordedfuture.com/products/browser-extension/); [Intelligence Graph](https://www.recordedfuture.com/de/platform/intelligence-graph); [Security intelligence engine blog](https://www.recordedfuture.com/blog/security-intelligence-engine)
- "AI for Intelligence" launched April 2023, GPT-based. The company says it was trained on 40,000+ Insikt Group analyst notes — [Recorded Future press release, 2023-04-11](https://www.recordedfuture.com/newsroom/press-releases/041123)
- Enterprise AI for Intelligence is a natural-language assistant grounded in the Intelligence Cloud (open web, dark web, technical data, proprietary Insikt research). It uses OpenAI's model "to summarize content". It maps actor TTPs to MITRE ATT&CK, lists IoCs, links CVEs to remediation steps and playbooks, drafts CISO executive summaries, and suggests follow-up questions. The page describes no per-statement citation scheme and no human review of AI output. Planned features include aggregating commonalities across attacks — [Recorded Future blog](https://www.recordedfuture.com/blog/next-evolution-recorded-future-ai-powering-threat-intelligence)
- Insikt Group is the human research arm, created 2017. A 2024 keynote cited 80+ analysts, which is dated — [Wikipedia](https://en.wikipedia.org/wiki/Recorded_Future); [Dubex keynote PDF, 2024-09](https://www.dubex.dk/wp-content/uploads/2024/09/Keynote-Truve-Dubex-20240905.pdf)

**Dataminr**
- ReGenAI was announced 2024-04-18 and released 2024-04-30 for Pulse customers, initially covering globally significant events only. It "distills multidimensional events into concise event briefs that dynamically update". A customer called it "a living document". Event discovery comes from Dataminr's existing multimodal fusion AI over 1M+ public sources (text, image, video, audio, sensor). The release does not say whether briefs carry per-sentence citations or human review. It said "recommended customer actions" and customer-tailored impact assessments would come in **future** versions — [Dataminr press release](https://www.dataminr.com/press/dataminr-unveils-regenai-the-first-generative-ai-that-automatically-regenerates-in-real-time/)
- Sept 2024: ReGenAI grew to 10x more briefs, including hyperlocal physical-security events. Vendor claims analysis time fell from about 30 min to under 90 s — [Dataminr press](https://www.dataminr.com/press/announcement/dataminrs-regenai-expands-to-cover-hyperlocal-events-across-the-globe-delivering-10x-more-dynamically-updating-event-briefs/); [Security Sales](https://www.securitysales.com/news/regenai-from-dataminr-receives-significant-update/162202/)
- Intel Agents (April 2025 roadmap) are goal-oriented agents that "continuously ask and answer hundreds of questions about each event", decide where to look, and add a context layer on top of ReGenAI briefs. Cyber Intel Agents were GA by Sept 2025. Physical-world agents were in private beta, with GA expected Nov 2025 (not confirmed here). The roadmap includes tailoring context to a client's footprint and "predictive capabilities that anticipate likely next steps", which is future and unverified — [SiliconANGLE, 2025-09-29](https://siliconangle.com/2025/09/29/dataminr-expands-intel-agents-bring-agentic-ai-physical-world); [Dataminr agentic roadmap](https://www.dataminr.com/press/dataminr-unveils-agentic-ai-roadmap); [BigDATAwire, 2025-04-28](https://www.bigdatawire.com/2025/04/28/dataminr-bets-big-on-agentic-ai-for-the-future-of-real-time-data-intelligence)

**Palantir (Foundry, AIP, Ontology)**
- The Ontology integrates data as objects, links and actions so "humans and AI" work from the same operational model. The platform can automatically create "proposals" in the Ontology for operators to review and approve — [Palantir platform overview docs](https://palantir.com/docs/foundry/platform-overview/overview/)
- AIP Logic functions can be automated so Ontology edits are "automatically applied or staged for human review" — [Palantir AIP Logic docs](https://www.palantir.com/docs/foundry/logic/overview/)
- A third-party FDE guide says read-time permission controls do not cover the model's output or edits, and recommends extra marking or classification controls. This is secondary and unverified — [FDE Academy](https://fde.academy/blog/palantir-aip-explained-what-it-is-and-how-fdes-use-it)
- Palantir provides the infrastructure (ontology, logic, actions, approvals) rather than packaged strategic insight. Customers build the analytic logic. This is an inference from the docs above.

**Strider (Spark)**
- Spark is an in-product AI chat over Strider's proprietary state-sponsored-threat database (10B+ documents from 40,000+ sources) and can cross-reference customer internal data (May 2025). Strider claims investigations that took weeks now take minutes. Spark also lists "Deep Research" that gives "analyst-level intelligence on entities tied to the PRC, Russia, and Iran — delivered in hours, not days" (vendor claim) — [Strider blog](https://striderintel.com/blog/introducing-spark-ai-the-new-gateway-to-striders-strategic-intelligence); [Strider newsroom](https://www.striderintel.com/newsroom/strider-unveils-unprecedented-ai-powered-strategic-intelligence-capabilities-that-further-protect-organizations-from-state-sponsored-threats/); [Strider Spark page](https://striderintel.com/?p=8903)

**Seerist (Control Risks heritage)**
- Seerist claims "every event analyst-confirmed before it enters the platform": sourced, geolocated by hand, severity-scored, time-stamped, and tied to analyst context — [Seerist content page](https://www.seerist.com/platform/content)
- EventsAI classifies unstructured data into themes (conflict, unrest, terrorism, disasters, crime) — [Esri marketplace listing](https://esri.com/en-us/arcgis-marketplace/listing/products/ae50c2d2630547ba865b6378cc4a32ec)
- A customer integration guide says human analysts check severity, credibility and likely impact before alerts go out — [Safeture help](https://help.safeture.com/product-information/seerist-integration)
- Content builds on decades of Control Risks analyst assessment — [Carahsoft Seerist](https://www.carahsoft.com/seerist/solutions)

### Inferences
- Risk and event products put **scores** (Recorded Future 0-99, Seerist severity) on entities and events, not on analytic hypotheses. A score measures how malicious or severe a thing is. It is not the probability that a strategic interpretation is correct.
- Dataminr's evolution from detection to live briefs to agentic context, with "recommended actions" and "predict next steps" still on the roadmap, shows the industry treats prediction and recommendation as the hardest layer. It is promised but not yet shown publicly.
- Palantir sidesteps the inference problem by making AI output *proposals* inside a governed model that humans approve.

### Gaps
- No public evidence was found of per-sentence citation in Recorded Future AI or Dataminr ReGenAI briefs.
- Whether Dataminr physical Intel Agents reached GA (Nov 2025) and whether "recommended actions" shipped by Oct 2026 was not confirmed.
- Recorded Future's ownership change and any 2025-2026 AI updates were not researched here. Verify separately.
- No independent quality reviews (G2 or Gartner Peer Insights) were gathered for Recorded Future, Dataminr, Seerist or Strider.

## 3. Precedents and historical patterns, actor leverage and incentives, recommended actions ("what this company should do next")

### Takeaway
No product found publicly markets historical-precedent matching, actor-leverage or incentive analysis, or company-level strategic recommendations as an AI output. The closest examples are Klue's deal-level tactics, Recorded Future's remediation playbooks, Palantir's human-approved action proposals, and Dataminr's *roadmap* items for recommended actions and predicted next steps.

### Cited Findings
- Klue Deal Tips suggest competitive tactics and "landmines to set" in live deals, grounded in win-loss data and call transcripts — [Klue blog](https://klue.com/blog/compete-agent)
- Recorded Future AI links vulnerabilities to remediation steps and playbooks, and maps actor TTPs to ATT&CK for threat hunting — [Recorded Future blog](https://www.recordedfuture.com/blog/next-evolution-recorded-future-ai-powering-threat-intelligence)
- Dataminr listed "recommended customer actions" (2024) and "predictive capabilities that anticipate likely next steps" (2025) as future features — [Dataminr press, 2024](https://www.dataminr.com/press/dataminr-unveils-regenai-the-first-generative-ai-that-automatically-regenerates-in-real-time/); [SiliconANGLE 2025](https://siliconangle.com/2025/09/29/dataminr-expands-intel-agents-bring-agentic-ai-physical-world)
- Palantir AIP stages AI-generated Ontology edits as proposals for human review — [Palantir Logic docs](https://www.palantir.com/docs/foundry/logic/overview/)
- Recorded Future plans to aggregate commonalities across attacks, a pattern-like feature that is future and unverified — [Recorded Future blog](https://www.recordedfuture.com/blog/next-evolution-recorded-future-ai-powering-threat-intelligence)
- AlphaSense Deep Research is positioned for M&A screening and competitive landscape analysis, which is research synthesis rather than prescriptive recommendation — [AlphaSense press release](https://www.alpha-sense.com/press/alphasense-launches-deep-research-automating-in-depth-analysis-with-agentic-ai-on-high-value-content)

### Inferences
- Where recommendations exist, they are **operational and narrow**: a sales tactic, a patch, an ontology edit. Each has a clear success criterion and little liability exposure. Strategic recommendations ("Company X should acquire Y or reprice Z") are absent, likely because they are hard to ground in citations, carry reputational and liability risk, and are what analysts and consultants see as their own value.
- Precedent and base-rate reasoning, and actor incentive or leverage modelling, look like a white space in packaged commercial products. Any such reasoning happens inside Insikt, Control Risks or Bloomberg Intelligence human analyst content, not in the AI layer.

### Gaps
- No public documentation was found of any product doing structured historical-analogue retrieval ("similar past events and their outcomes") as an AI feature. It may exist inside enterprise deployments but is not publicly evidenced.
- No product was found that explicitly reports "nothing important happened" as a first-class output. Crayon's importance ranking (only about 3% flagged high) and alert thresholds are the nearest mechanism.

## 4. How products avoid hallucination (citations, humans in the loop, curated sources)

### Takeaway
Three techniques recur: (a) retrieval restricted to a curated, licensed or proprietary corpus, (b) showing the source passage next to the output or linking each claim to it, and (c) human analysts who curate inputs, tag training data, verify events or approve actions. The products advertise these mechanisms, but none publishes error rates.

### Cited Findings
- Curated corpus: CB Insights answers come only from its own datasets ([CB Insights API](https://cbinsights.com/what-we-offer/api/)). AlphaSense uses "high-value" licensed content plus expert transcripts ([AlphaSense press](https://www.alpha-sense.com/press/alphasense-launches-deep-research-automating-in-depth-analysis-with-agentic-ai-on-high-value-content)). Strider and Recorded Future use proprietary collections ([Strider](https://striderintel.com/blog/introducing-spark-ai-the-new-gateway-to-striders-strategic-intelligence); [Recorded Future](https://www.recordedfuture.com/blog/next-evolution-recorded-future-ai-powering-threat-intelligence))
- Citations: AlphaSense uses "granular citations" ([AlphaSense press](https://www.alpha-sense.com/press/alphasense-launches-deep-research-automating-in-depth-analysis-with-agentic-ai-on-high-value-content)). Ask Klue links to original sources ([Klue](https://klue.com/blog/compete-agent)). Bloomberg places the summary next to the transcript and highlights backing passages ([Tech Brew](https://www.techbrew.com/stories/2024/02/02/bloomberg-ai-tool-quarterly-earnings))
- Humans in the loop: Bloomberg's human tagging, intervention when the system is unsure, and BI analyst tuning ([WatersTechnology](https://waterstechnology.com/node/7951936)). Seerist's analyst-confirmed events ([Seerist](https://www.seerist.com/platform/content)). Klue's "human oversight" of every output ([Klue](https://klue.com/blog/compete-agent)). Palantir's human approval of proposals ([Palantir docs](https://www.palantir.com/docs/foundry/logic/overview/)). Recorded Future's model trained on Insikt analyst notes ([Recorded Future PR](https://www.recordedfuture.com/newsroom/press-releases/041123))
- User-side evidence of residual quality problems: AlphaSense reviewers report "hit and miss" generative grids and difficulty judging source reliability — [G2](https://g2.com/products/alphasense/reviews/alphasense-review-10027827)

### Inferences
- Citation linking verifies *attribution* (the source said X), not *inference* (X implies Y). That explains why vendors keep outputs close to attributed summary. Once a system makes original inference, citation-based verification no longer covers it.
- "Human in the loop" means different things across products: input curation (Seerist), training and tagging (Bloomberg, Recorded Future), output approval (Palantir, Klue). Vendor claims rarely say which.

### Gaps
- No vendor in this set publishes hallucination or error rates, or citation-precision metrics.
- How uncertainty is shown to users was not documented, beyond Bloomberg's statement that humans step in when the system is "unsure".

## 5. Common pattern across products: which layers exist and which are left to humans

### Takeaway
A four-layer stack is common: (1) **alerts and detection** (Dataminr, Crayon, Feedly, Seerist), (2) **attributed facts and summaries with citations** (all products), (3) **expert and analyst views as content** (Tegus transcripts, Insikt, Control Risks, Bloomberg Intelligence), and (4) **scored risk on entities and events** (Recorded Future, Seerist). The missing or human-reserved layer is **analytic judgment**: original, forward-looking inference about what an event means for a specific actor, with explicit confidence, precedents, incentive analysis and a recommended strategy.

### Cited Findings
- Detection and alerts: Dataminr's multimodal event detection over 1M+ sources ([Dataminr](https://www.dataminr.com/press/dataminr-unveils-regenai-the-first-generative-ai-that-automatically-regenerates-in-real-time/)). Crayon's importance-scored inbox alerts ([Crayon](https://www.crayon.co/blog/crayon-ai))
- Attributed summaries: Bloomberg earnings summaries ([Tech Brew](https://www.techbrew.com/stories/2024/02/02/bloomberg-ai-tool-quarterly-earnings)). ReGenAI briefs ([Dataminr](https://www.dataminr.com/press/dataminr-unveils-regenai-the-first-generative-ai-that-automatically-regenerates-in-real-time/)). Feedly Ask AI over up to 25 reports ([Feedly](https://feedly.com/threat-intelligence/landing/john-hammond-ai-video-mar25))
- Expert views as content: Tegus transcripts in Generative Search ([AlphaSense help](https://help.alpha-sense.com/en/articles/7225201-expert-transcripts-overview)). Insikt research grounding Recorded Future AI ([Recorded Future](https://www.recordedfuture.com/blog/next-evolution-recorded-future-ai-powering-threat-intelligence)). Control Risks assessment in Seerist ([Carahsoft](https://www.carahsoft.com/seerist/solutions))
- Scores: Recorded Future 0-99 rule-based risk ([Recorded Future](https://www.recordedfuture.com/products/browser-extension/)). Seerist severity scoring ([Seerist](https://www.seerist.com/platform/content))
- Judgment and action layer: offered only as human-approved proposals (Palantir), narrow tactics (Klue), or future roadmap items (Dataminr recommended actions and prediction) — sources as cited in Section 3

### Inferences
- Why the judgment layer stays with humans, inferred from the evidence: (a) citations cannot verify inference; (b) vendors' moats and brands rest on trust, as Bloomberg's explicit hallucination framing shows; (c) recommendations depend on client-specific context and carry liability; (d) the human analyst teams (Insikt, Control Risks, Bloomberg Intelligence) are themselves a product the vendors sell.
- Opportunity for an Event Intelligence product: a clearly labelled layer of *analytic judgment* separate from attributed facts. It would carry explicit confidence, precedent and base-rate evidence, actor leverage and incentives, and a "nothing material changed" outcome. The surveyed products do not publicly offer this, but it must be held to stricter grounding and evaluation than citations alone provide.
- "Nothing important found" is handled implicitly through thresholds and ranking (Crayon's about 3% high-importance; alert severity filters), not as an explicit, explained output.

### Gaps
- Gartner Peer Insights reviews were not retrieved. User-quality evidence comes mainly from G2 for AlphaSense and third-party comparisons for Klue and Crayon.
- How expert disagreement or confidence is shown, e.g. aggregating multiple expert calls into a consensus or dispersion view, was not found for any product.
- The October 2026 state of features announced in 2024-2025 (Dataminr GA dates, AlphaSense Deep Research updates, Bloomberg newer AI) was not verified.
