# Analytic Methods for Turning News Events into Non-Obvious, Evidence-Grounded Strategic Judgments

Scope note: the notes cover intelligence tradecraft, forecasting research, strategy frameworks, "insight" criteria, and documented failure modes. Every cited item was read in the primary text unless it is marked as a secondary summary. Research date: 2026-10-23.

## 1. How US intelligence tradecraft separates source reporting from analytic judgment, and how it handles judgments no source states

### Takeaway
ICD 203 does not forbid judgments that go beyond the sources. It requires them: products "should not avoid difficult judgments". What it demands is labeling. Each statement is tagged as underlying information, assumption, or judgment. Linchpin assumptions are stated, along with what changes if they are wrong. Likelihood and confidence are reported separately, and indicators are given that would change the judgment. Structured techniques (KAC, ACH, Indicators, red team, What If?) are the procedures that produce and stress-test those judgments.

### Cited Findings
**ICD 203 status.** The current version is ICD 203, signed 2 January 2015 by DNI Clapper. It superseded the 21 June 2007 version and received technical amendments on 21 January 2022 covering the Analytic Ombuds and annual training. I found no newer revision as of Oct 2026. It sets five Analytic Standards: Objective; Independent of political consideration; Timely; Based on all available sources; Exhibits the nine Analytic Tradecraft Standards. — [ICD 203 (ODNI archive PDF)](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)

**The nine tradecraft standards, paraphrased from the text, with the implementable rule each implies:**
- (1) Describe the quality and credibility of sources, data and methods. Use ICD 206 source descriptors covering accuracy, completeness, possible denial and deception, age and currency, access, validation, motivation, bias and expertise. "Source summary statements" are "strongly encouraged". They give an overall assessment of the source base and say "which sources are most important to key analytic judgments." — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- (2) Express and explain uncertainty in two separate dimensions:
  - Likelihood is about the event.
  - Confidence is about the basis for the judgment: logic, quantity and quality of evidence, and topic understanding.
  - Products should note causes of uncertainty and how much a judgment depends on assumptions, and should identify indicators that would change uncertainty levels.
  - A confidence level and a degree of likelihood must not be combined in the same sentence. — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- **Mandatory likelihood scale.** Use one row consistently; mixing rows requires a disclaimer.

  | Row 1 | Row 2 | Range |
  |---|---|---|
  | almost no chance | remote | 01–05% |
  | very unlikely | highly improbable | 05–20% |
  | unlikely | improbable | 20–45% |
  | roughly even chance | roughly even odds | 45–55% |
  | likely | probable | 55–80% |
  | very likely | highly probable | 80–95% |
  | almost certain(ly) | nearly certain | 95–99% |

  — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- (3) **Distinguish underlying intelligence from assumptions and judgments.**
  - *Assumptions* = "suppositions used to frame or support an argument."
  - *Judgments* = "conclusions based on underlying intelligence information, analysis, and assumptions."
  - Assumptions must be stated explicitly when they are the "linchpin of an argument" or "bridge key information gaps."
  - Products should explain the implications if assumptions prove incorrect, and identify indicators that would alter the judgments. — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- (4) **Analysis of alternatives.** Identify and assess plausible alternative hypotheses. This matters especially under significant uncertainty, in forecasting, and when low-probability events could have high impact. Give indicators that would shift the likelihood of each alternative. — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- (5) **Customer relevance and implications.** "Products should add value by addressing prospects, context, threats, or factors affecting opportunities for action." This is the tradecraft basis for the "so what" requirement. — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- (6) **Clear argumentation.** Put the main analytic message up front. Draw it collectively from the individual judgments. Acknowledge "significant supporting and contrary information." — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- (7) **Explain change or consistency.** State whether judgments are consistent with prior analysis, a change from it, or initial coverage, and say how new information or reasoning drove any change. Avoid boilerplate. — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- (8) **Make accurate judgments.** Products "should present all judgments that would be useful to customers, and should not avoid difficult judgments in order to minimize the risk of being wrong." Address the "likelihood, timing, and nature of the outcome" so the judgment can be scored. This is an explicit anti-hedging rule. — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- (9) **Effective visuals.** — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)

**Key Assumptions Check (CIA Tradecraft Primer, March 2009).** A key assumption is "any hypothesis that analysts have accepted to be true and which forms the basis of the assessment."
- Method:
  1. Write down the current analytic line.
  2. Articulate all premises, stated and unstated, that must be true for it.
  3. Challenge each premise with these questions: How much confidence exists that it is correct, and why? What could undermine it? Is it really a key uncertainty? Could it have been true in the past but less so now?
  4. Refine the list to the assumptions that must be true and record which ones are unsupported.
- Value claimed: it exposes faulty logic, uncovers "hidden relationships and links between key factors," and identifies "developments that would cause you to abandon an assumption."
- The primer's worked example is the 2002 DC sniper case. The working assumptions were white, male, acting alone, military training, and white van; several proved wrong. — [CIA Tradecraft Primer 2009](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Quality of Information Check.**
- Build a source database by type and date with strength and weakness notes.
- Check critical reporting for corroboration.
- Re-examine previously dismissed information.
- Flag recalled reporting and the judgments that depended on it.
- Check that ambiguous information is caveated.
- State the confidence that can be placed in sources. — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Indicators / Signposts of Change.**
- Identify competing hypotheses or scenarios.
- Build separate lists of expected activities, statements and events for each.
- Review the lists regularly.
- Favor the hypothesis with the most changed indicators.
- Two lists per hypothesis are useful: one showing the development *is* emerging and one showing it *is not*.
- The primer says indicators "depersonalize" disagreements and make the analytic line transparent and trackable for the customer. — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**ACH, 8 steps (Heuer, 1999).**
1. Brainstorm hypotheses with diverse analysts.
2. List evidence and arguments.
3. Build a hypotheses × evidence matrix and assess the *diagnosticity* of each item.
4. Refine the matrix by deleting non-diagnostic evidence.
5. Draw tentative conclusions "by trying to disprove the hypotheses rather than prove them."
6. Run sensitivity analysis on a few critical items: what if they are wrong or deceptive?
7. Report the relative likelihood of *all* hypotheses, not just the most likely one.
8. Identify milestones for future observation.

Additional guidance:
- Evidence "consistent with all the hypotheses... may have no diagnostic value."
- Keep "unproven" hypotheses (no evidence for them) separate from "disproved" ones (positive evidence against them). Do not drop unproven hypotheses early, especially deception.
- More than about 7 hypotheses becomes unmanageable; group them.
- "Note the absence of evidence as well as its presence." — [Heuer, Psychology of Intelligence Analysis, CIA CSI 1999](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

The primer's ACH version adds two steps. First, ask "what evidence is not being seen but would be expected for a given hypothesis." Second, tally inconsistent evidence to find the weakest hypotheses. — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Contrarian and imaginative techniques (primer):**
- **Devil's Advocacy.** Outline the mainline judgment and its assumptions. Pick the assumptions most open to challenge. Check for questionable evidence, deception, or gaps.
- **Team A/Team B.** Build the best case for each hypothesis with explicit assumptions, evidence and logic, then present to a "jury of peers."
- **High-Impact/Low-Probability.**
  1. Define the outcome.
  2. Devise plausible pathways.
  3. Insert triggers, such as leader health, disasters or shocks.
  4. Brainstorm with analysts who have a broad mix of experience.
  5. Set indicators per pathway.
  6. Identify factors that would deflect or encourage the outcome.
- **What If?**
  1. Assume the event has happened.
  2. Pick triggering events.
  3. Build the argument chain.
  4. "Think backwards" through the concrete steps that must occur.
  5. Set indicators per pathway.
  6. Weigh consequences.
  7. Monitor.
- **Outside-In Thinking.** List STEEP forces the actor cannot control. Then list the factors it can influence; in business these are market, customers, competitors, suppliers and partners. Then assess each force's actual impact on the evidence.
- **Red Team.** Put analysts in the adversary's "cultural, organizational, and personal setting" to avoid "mirror-imaging." Write first-person questions, such as "How would I perceive incoming information... to whom would I look for an opinion?" Draft the adversary's own policy papers.
- **Alternative Futures.** Select the two most critical and uncertain forces as axes, form a 2×2 matrix, write a narrative for each quadrant, and derive signposts. Policymakers then test strategies against all four worlds.
- **Recommended sequence.** Brainstorming and KAC at project start. Then What If?/HILP, Outside-In, Team A/B and Red Team. Devil's Advocacy and ACH near the end. — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Heuer on going beyond available information.** He names three strategies:
- situational logic,
- theory,
- comparison with historical situations.

He says analysis is closer to medical diagnosis than to assembling a mosaic. The analyst forms hypotheses from indicators and specialized knowledge of how the system works. Most failures are failures of analysis, not collection: information is "discounted, misinterpreted, ignored... because it fails to fit a prevailing mental model." — [Heuer 1999](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

### Inferences
- Each output sentence can be treated as a typed record:
  - `type ∈ {source_fact, expert_view, assumption, analytic_judgment}`,
  - `supporting_evidence_ids`,
  - for judgments, `likelihood` (an ICD 203 band) and `confidence` (High/Moderate/Low, based on evidence quality, quantity and logic) as separate fields that are never merged.
  This directly implements ICD 203 standards 2 and 3. An "original" judgment is legitimate when its type is `analytic_judgment` and it has a traceable chain of evidence plus explicit assumptions.
- "Always show what experts already say" maps to the source summary statement plus standard (6), which requires acknowledging supporting and contrary information. "Never blank" maps to standard (8): do not avoid difficult judgments. The tradecraft answer to thin evidence is a low-confidence judgment that states its gaps, not silence.
- A minimum viable tradecraft pipeline per judgment:
  1. KAC: list linchpin assumptions and what breaks if each is false.
  2. ACH-lite: at least 2–3 rival explanations or moves, and the evidence that discriminates between them.
  3. Indicators: 2–3 observables that would confirm or refute the judgment.
  4. Change note: new, changed, or consistent with the prior report.
- Red-team "first-person policy papers" give a concrete prompt pattern for leverage and move reasoning: generate the actor's own memo recommending move Y, given its constraints.

### Gaps
- I could not retrieve the ICD 206 text. The dni.gov URL returned HTML instead of a PDF. Details of the ICD 206 source-descriptor format and source-summary-statement format are known here only through ICD 203's references to them.
- I did not verify the confidence-level definitions (High/Moderate/Low) as used in published IC products, such as NIE "What We Mean When We Say" pages. ICD 203 itself does not define the levels.
- I did not read Heuer & Pherson's *Structured Analytic Techniques* (3rd ed.) directly. It is paywalled.

## 2. What forecasting research says about precedents, base rates, analogy selection, and analogy limits

### Takeaway
Start from the outside view, a reference-class distribution, then adjust with case specifics. Choose a class "broad enough to be statistically meaningful but narrow enough to be truly comparable." Make numeric and granular probabilities and update them often. Treat single historical analogies as hypothesis generators, not conclusions. They should be vetted with Known/Unclear/Presumed sorting and a similarities–differences table.

### Cited Findings
**Reference class forecasting, 3 steps (Flyvbjerg 2006, building on Kahneman & Tversky 1979 and Lovallo & Kahneman 2003):**
1. Identify a reference class of past, similar cases. It must be "broad enough to be statistically meaningful but narrow enough to be truly comparable."
2. Establish a probability distribution of outcomes for the class from credible empirical data.
3. Place the specific case in that distribution.

In statistical terms this is "regressing forecasters' best guess toward the average of the reference class and expanding their estimate of credible interval toward the corresponding interval for the class." — [Flyvbjerg 2006, arXiv:1302.3642](https://arxiv.org/pdf/1302.3642)

**The inside view and the planning fallacy.** The inside view focuses on the specifics of the case and leads to systematic optimism. Kahneman's curriculum example shows the gap: the team's inside-view estimate was 18–30 months. The outside-view base rate was that about 40% of similar teams never finished and the rest took 7–10 years. The project actually took 8 years. Flyvbjerg names a second cause of inaccurate forecasts besides optimism bias: "strategic misrepresentation," meaning deliberate misstatement driven by incentives. — [Flyvbjerg 2006](https://arxiv.org/pdf/1302.3642); [Lovallo & Kahneman, "Delusions of Success," HBR 2003](https://hbr.org/2003/07/delusions-of-success-how-optimism-undermines-executives-decisions)

**Worked calibration via the UK Department for Transport uplifts.**
- Roads: reference class N=172. A 15% uplift gives a 50% risk of overrun; a 32% uplift gives 20% risk (the 80th percentile); a 45% uplift gives 10% risk.
- Rail: N=46. Uplifts are 40% at the 50th percentile and 57% at the 80th, rising to 68% at 10% risk.
- Downward adjustment is allowed only with evidence that the current team is better than the reference class.
- Edinburgh Tram Line 2: the promoter estimated about £320m. The reference-class forecast gave a 50th percentile of £357m and an 80th of £400m, and Arup noted even those were likely too low. Per the Wikipedia summary, the line actually opened in 2014 at £776m (£628m in 2004 prices). — [Flyvbjerg 2006](https://arxiv.org/pdf/1302.3642); [Wikipedia: Reference class forecasting](https://en.wikipedia.org/wiki/Reference_class_forecasting)
- **The "reference class problem" / "reference class tennis."** Different plausible classes give different base rates, and there is no formal selection procedure. — [Wikipedia: Reference class forecasting](https://en.wikipedia.org/wiki/Reference_class_forecasting)

**Good Judgment Project evidence** (secondary synthesis of Tetlock/Mellers papers):
- Forecasts tagged as using "comparison classes" (the outside view) averaged a Brier score of 0.17, versus 0.26 for the next-best tag.
- A one-hour training module had effects that lasted at least a year.
- About 70% of superforecasters kept their status the following year; the year-to-year correlation was 0.65.
- Making more predictions on each question (frequent updating) and deliberation time improved accuracy.
- The GJP aggregate beat the control group by more than 60% and an IC prediction market by 25–30%.
- Superforecasters at 300 days out beat regular forecasters at 100 days.
- Accuracy declines toward chance at around 5 years.
- Superforecasters make "many small updates, with occasional large ones."
- Extremizing aggregation is still debated. — [AI Impacts, "Evidence on good forecasting practices from the GJP"](https://aiimpacts.org/?p=1283)

**Tetlock & Gardner's "Ten Commandments"** include:
- (1) triage: focus on "Goldilocks zone" questions;
- (2) Fermi-ize: break the problem into knowable and unknowable sub-parts;
- (3) balance inside and outside views. Example: if about 55% of mergers fail to meet expectations, start from that figure and then adjust. — [Farnam Street summary](https://fs.blog/2015/12/ten-commandments-for-superforecasters/); [LessWrong repost](https://www.lesswrong.com/posts/dvYeSKDRd68GcrWoe/ten-commandments-for-aspiring-superforecasters); [Nomura/Tetlock deck](https://www.nomura.com/events/10th-annual-global-quantitative-investment-strategies-conference/resources/upload/How_much_better_can_we_get_Tetlock.pdf)

**Precision has value.** Rounding numeric probabilities into the bins implied by verbal phrases, including the phrases the IC recommends, measurably reduced accuracy. The data were 888,328 GJP forecasts, and the loss was robust to scoring rule, time horizon and forecaster type. Friedman, Baker, Mellers, Tetlock & Zeckhauser, *ISQ* 62 (2018): 410–422. I read the abstract and summary, not the full paper. — [HKS summary](https://www.hks.harvard.edu/publications/value-precision-probability-assessment-evidence-large-scale-geopolitical-forecasting); [paper PDF](https://scholar.harvard.edu/sites/scholar.harvard.edu/files/rzeckhauser/files/value_of_precision.pdf)

**Heuer on analogy:**
- Comparison means "unknown elements of the present are assumed to be the same as known elements of the historical precedent."
- It rests on one or a few cases, so it "cannot be validated nearly as well as generalizations inferred from many comparable cases."
- "Inferences based on comparison with a single analogous situation probably are more prone to error than most other forms of inference."
- "The most productive uses of comparative analysis are to suggest hypotheses and to highlight differences, not to draw conclusions." Comparison should not be the basis for conclusions unless thorough analysis confirms the cases are comparable "in all relevant respects."
- Quoting Jervis: "historical analogies often precede, rather than follow, a careful analysis."
- Quoting May: policymakers "seize upon the first [analogy] that comes to mind... do not research more widely... [nor] ask in what ways it might be misleading." Heuer's response is "analyze rather than analogize." — [Heuer 1999, ch. 4](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

**Analogy vetting methods (Studies in Intelligence 70:3, Sept 2026, Handy):**
1. Identify the fundamentals of the current case first, so that surface similarities do not dominate.
2. Use the Neustadt–May "Known / Unclear / Presumed" sort, and fix the case in time to avoid overly broad analogies.
3. Run a **Similarities–Differences table**: list the critical elements and mark each analog element as analogous, weak, or not analogous, with qualifications. The worked example is George Ball's 1964 memo rejecting the Korea analogy for Vietnam on legitimacy, host-government stability, allied commitment, resolve and war context.
4. Use **Reflection-mapping**: the past case sits above a "meridian" and the current case below it. Evidence, inferences, assumptions and gaps are mapped, and indicators and opportunities are extracted.

The article warns about vividness bias, citing Khong's *Analogies at War* on Munich and Korea.
- Its example output has a defined pattern: a past mechanism, a current parallel, the stated limits of the analog, and an implication for action. In the example, as in Iran 1979, a religious opposition adopting nationalist narratives means things could unravel quickly, so watch the opposition.
- It also cites Jervis's "circularity": collection stays poor because analysts believe the situation is stable. — [Handy, "Analogical Reasoning in Intelligence Analysis," Studies in Intelligence 70(3), 2026](https://www.cia.gov/resources/csi/static/6-Analogical_Reasoning_in_Intelligence_Analysis.pdf)

### Inferences
- Output (a), "this happened in a similar case, so it can happen to this company," should be built in two tiers:
  1. **Base-rate tier.** When a class of N comparable cases can be assembled, report "in X of N comparable cases, outcome O followed within T." Show the class definition, the inclusion rule, and how sensitive the rate is to an alternative class.
  2. **Single-analog tier.** When N is small, label the output "analog-suggested hypothesis," not a forecast. Require a Similarities–Differences table with at least 3–5 critical elements, and state the key difference that could break the analogy.

  Confidence should be capped at Low or Moderate when the judgment rests on a single analogy, which follows Heuer.
- Implement "reference class tennis" as a check: compute the base rate under two or more class definitions, one broader and one narrower. If they diverge, report the range and lower confidence.
- Store numeric probabilities internally even when you display ICD 203 words. Rounding costs accuracy, per Friedman et al. Enable scoring via Brier against later outcomes. This is a natural use for the existing forecast follow-up machinery.
- Fermi decomposition gives "original but supported" judgments a natural structure. Break the claim into sub-claims, each tied to evidence or to a base rate, so the novelty sits in the combination while each part stays traceable.

### Gaps
- I did not read Kahneman & Lovallo's 2003 HBR piece in full or Flyvbjerg's later work, such as *How Big Things Get Done* (2023) and the "fat tails" papers. Newer arguments, such as that overrun distributions are fat-tailed and therefore poorly summarized by means, are not covered here.
- I found no authoritative, quantitative rule for the minimum N of a reference class, and no formal algorithm for selecting one.
- The GJP effect sizes come from a secondary synthesis by AI Impacts, not from the original Mellers et al. papers.

## 3. Strategy and competitive-analysis frameworks for leverage and incentive reasoning, and how a recommended move is validated

### Takeaway
Leverage reasoning in this literature comes down to three things:
- **Alternatives.** BATNA: power comes from the quality of one's walk-away option.
- **Added value and game structure.** Brandenburger & Nalebuff's PARTS: what each player brings, and which rules, tactics and scope can be changed.
- **The actor's own perspective.** Red teaming avoids mirror-imaging.

Moves are validated by testing them against several scenarios (Shell, Alternative Futures), by pre-mortems, and by indicators, not by a single forecast.

### Cited Findings
**BATNA (Fisher & Ury, *Getting to Yes*, Harvard PON):**
- A negotiator's BATNA, not money or influence, is the "principal source of power."
- How to build one: list the actions available if there is no agreement, develop the promising ones into concrete alternatives, and pick the best.
- BATNA parallels the game-theoretic "disagreement point."
- It is distinct from a reservation price or bottom line: it compares concrete options.
- Advice for weaker parties: refuse unwise deals and maximize the power they do have. — [Wikipedia: BATNA](https://en.wikipedia.org/wiki/Best_alternative_to_a_negotiated_agreement); [LitCharts, Getting to Yes ch. 6](https://litcharts.com/lit/getting-to-yes/chapter-6-what-if-they-are-more-powerful-develop-your-batna-best-alternative-to-a-negotiated-agreement)

**PARTS / The Right Game (Brandenburger & Nalebuff, HBR July–Aug 1995).** These are secondary summaries.
- **Players:** customers, suppliers, competitors, complementors (the Value Net).
- **Added value:** what a player contributes. A firm can raise its own added value or lower others'.
- **Rules:** contracts, regulations, norms of interaction.
- **Tactics:** actions that shape perceptions, such as signaling.
- **Scope:** game boundaries, which can be enlarged or linked to adjacent games.

The framing treats the game as something a player can change, not only play. — [thinkinsights.net PARTS](https://thinkinsights.net/strategy/parts); [getAbstract, Co-opetition](https://www.getabstract.com/en/summary/co-opetition/1834); [ESB 1995 article citing the HBR piece](https://esb.nu/ondernemen-als-spel/)

**Shell scenario planning (Pierre Wack):**
- Separate *predetermined elements* from *critical uncertainties*. Predetermined elements are events that have already happened or are nearly certain, whose consequences have not yet played out. Wack's example is Ganges rainfall that will reach downstream cities on a knowable schedule.
- Scenarios aim to change managers' mental models, not to predict.
- Wack warned that forecasts fail precisely at major shifts.
- Method: identify trends and uncertainties, build divergent worlds, add the predetermined elements to all of them, check internal consistency, then test decisions against the set. — [Burt, "Pre-determined elements... legacy of Pierre Wack," Strathprints](https://strathprints.strath.ac.uk/id/eprint/4284); [Davis, "How does Shell do scenarios?" (PDF)](https://www.pik-potsdam.de/ateam/avec/peyresq2005/talks/0921/leemans/literature/davis_how_does_shell_do_scenarios.pdf); [Wilkinson & Kupers / CMR "Three decades of scenario planning in Shell" (2005)](https://www.akademi.audit.gov.my/images/PDF/NotaKursus/Sekolah_Pengurusan/three%20decades%20of%20scenario%20planning%20in%20shell%20-%20california%20management%20review%2048_1%202005.pdf). These Shell items come from search summaries; I did not read the full PDFs.

**Alternative Futures (IC version).** Two critical uncertainties form a 2×2 matrix. "Policymakers can then consider how current decisions or strategies would fare in each of the four worlds and identify alternative policies that might work better either across all the futures or in specific ones." — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Outside-In Thinking** separates the forces an actor cannot influence from the levers it can, and the primer explicitly lists market, customers, competition, suppliers and partners for business. — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Red Team and mirror-imaging.** Analysts "impute to a foreign actor the same motives, values, or understanding" they hold. Red Team corrects this by modeling the actor's own pressures, peers and information environment. — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Pre-mortem (Klein, HBR Sept 2007).** Imagine the plan has already failed, then generate the reasons why. It is grounded in "prospective hindsight" (Mitchell, Russo & Pennington 1989), which increased the *number* of reasons generated by about 30%. Popular claims that it makes people "30% more accurate" overstate the study. Veinott et al. (2010, N=178) found it reduced overconfidence more than comparison methods did. The academic literature on it is thin. — [Jason Collins review of the premortem evidence](https://corporate.jasoncollins.blog/premortem); [Rework summary](https://resources.rework.com/libraries/project-management/pre-mortem)

### Inferences
- For output (b), "regulations and incentives give actor X leverage, so move Y is right," a traceable template is:
  1. **Actor's BATNA and its counterparties' BATNAs.** What each can do if there is no deal, with evidence.
  2. **Added value.** What X controls that others cannot easily replace, with evidence.
  3. **Rules.** The specific regulation, contract or deadline that shifts the BATNAs. Cite the rule itself as a source fact.
  4. **Move Y.** It changes a PARTS element in X's favor.
  5. **Red-team check.** How would the counterparty respond, in first person?
  6. **Validation.** Does Y still look good across 2–4 scenarios? What is the pre-mortem failure reason? Which indicators would show that Y is working or failing?

  The output must label step 4 as an `analytic_judgment` and steps 1–3 as `source_fact` or `assumption`.
- Wack's predetermined elements are a strong pattern for non-obvious and well-supported claims. Take a fact already reported, such as a rule that takes effect on date D or a contract that expires. Project its consequences that have not yet unfolded, which sources usually do not spell out. The novelty is in the projection, and the support is the reported fact plus the causal mechanism.
- For the system, "validating a move" in these frameworks means robustness and falsifiability: scenario robustness plus pre-mortem plus indicators. Avoid claiming optimality.

### Gaps
- I did not retrieve primary or secondary sources for Porter's five forces, for war-gaming methods (e.g., RAND or the Naval War College), or for the McKinsey "so what" / Pyramid Principle practice within the tool-call budget. These remain unsourced here, and the report writer should not rely on these notes for them.
- No source reviewed gives a quantitative validation record for recommended strategic moves, such as hit rates of scenario-based strategy versus forecast-based strategy.
- ZOPA and reservation-price mechanics were not covered by sources found.

## 4. How these practices define and test "insight" versus "summary"; criteria for a valuable judgment

### Takeaway
Across these sources, an insight is a judgment that changes the reader's mental model or options for action and that could be shown wrong:
- ICD 203 requires "implications," "opportunities for action," and precision on likelihood, timing and nature.
- Wack's goal is changing mental models.
- Klein's empirical insight paths are connections, contradictions, and creative desperation.

Summary is the opposite: describing the situation and leaving the judgment to the reader. Heuer explicitly names that as an evasion.

### Cited Findings
**Analysis versus description (Heuer).** He calls it "another tempting option": "to avoid judgment by simply describing the current situation, identifying alternatives, and letting the intelligence consumer make the judgment." — [Heuer 1999, ch. 4](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

**Value criteria in ICD 203.** The relevant standards are:
- customer relevance and implications, "add value by addressing prospects, context, threats, or factors affecting opportunities for action" (standard 5);
- a main message up front (6);
- an explicit statement of change from prior judgments (7);
- precision on "likelihood, timing, and nature" so that "clarity of meaning permits assessment for accuracy" (8).

Taken together these give relevance, novelty versus prior coverage, and falsifiability. — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)

**Klein's Triple Path Model of insight.** It was built from 120 case studies (*Seeing What Others Don't*, 2013). Insights arise through three paths:
- **connections:** linking seemingly unrelated facts;
- **contradictions:** noticing that an expected belief is wrong;
- **creative desperation:** abandoning a weak anchor when stuck.

The book's five triggers are connections, coincidences, curiosities, contradictions and creative desperation. An insight changes "core beliefs" or anchors. The method is case narrative, not experiment. — [Gary Klein, insight page](https://gary-klein.com/insight); [YourStory on the five triggers](https://yourstory.com/2019/07/insights-triggers-enablers-gary-klein); [thorprojects commentary](https://thorprojects.com/?p=9903)

**Scenarios as mental-model change.** Wack's purpose was to alter managers' mental models, not to forecast. — [Burt/Strathprints](https://strathprints.strath.ac.uk/id/eprint/4284)

**Primer on hidden links.** KAC "uncover[s] hidden relationships and links between key factors." HILP and Outside-In surface "hidden relationships between key factors and assumptions" and external forces that analysts miss when they think "from the inside" out. — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Diagnosticity as a value test.** Evidence, and by extension a claim, that is consistent with every hypothesis does not discriminate between them. Value lies in items that shift relative likelihoods. — [Heuer 1999, ch. 8](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

### Inferences
- A testable "non-obviousness" rubric can be assembled from these sources. A candidate insight should pass at least 3 of the following:
  - (i) **Not stated by any retrieved source.** Check semantic overlap with the source set; this is novelty relative to "what experts say," per ICD 203 std 7.
  - (ii) **Decision relevance.** It names an action or opportunity for a specific actor (std 5).
  - (iii) **Falsifiable.** It specifies likelihood, timeframe and nature, plus at least one indicator (std 8 and the Indicators technique).
  - (iv) **Diagnostic.** It discriminates between at least two plausible futures or explanations (Heuer).
  - (v) **Mental-model change.** It contradicts a stated expert or consensus view, or connects two actors or events that no source links (Klein's contradiction and connection paths; Wack).
- Output (c), the hidden cross-actor connection, is Klein's "connection path." To make it traceable, require that each endpoint of the connection be sourced. Only the link itself is the analytic judgment, and the mechanism must be stated: shared supplier, shared regulator, a common rule, a counterparty, or a correlated exposure.
- A "summary detector" can flag outputs whose claims are all `source_fact` or `expert_view` with no `analytic_judgment`. That is the Heuer "describe and let the consumer decide" pattern. Such outputs should trigger a forced low-confidence judgment step instead of being returned as is.

### Gaps
- I found no formal, validated metric for "insight" in the intelligence or forecasting literature. Klein's model is qualitative. Tetlock's work scores accuracy, not novelty.
- I did not find a published IC rubric for scoring "value added" beyond the ODNI Rating Criteria. Those are referenced in ICD 203 but were not retrieved.

## 5. Documented failure modes

### Takeaway
The main failures fall into four groups:
- **Mindset-driven failures:** satisficing, selective perception, anchoring, vividness and availability, mirror-imaging, and analogy misuse.
- **Optimism and strategic misrepresentation** in forecasts.
- **Insufficient use of base rates.**
- **The opposite failure: avoiding judgment.** This means vague or rounded probabilities, or description in place of analysis, which ICD 203 and Friedman et al. show is itself costly.

### Cited Findings
**Satisficing, incrementalism, consensus, reasoning by analogy, and maxims.** These are Alexander George's sub-optimal strategies, which Heuer applies to analysts. He calls satisficing the most prevalent. It has three weaknesses:
- selective perception from focusing on a single hypothesis,
- failure to generate the full set of hypotheses (people are "rather poor at thinking of all the possibilities"),
- a focus on confirming rather than disconfirming evidence. — [Heuer 1999, ch. 4](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

**Vividness, availability and anchoring:**
- "The single, vivid case... outweighs a much larger body of statistical evidence."
- Statistical evidence gets too little weight.
- Availability makes recallable events seem more probable.
- Anchoring on a starting point, such as a prior analysis, under-adjusts. — [Heuer 1999, ch. 10 and 12](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

**Expectation effects:**
- "It takes more information, and more unambiguous information, to recognize an unexpected phenomenon than an expected one."
- Early judgments distort later perception. — [Heuer 1999, ch. 2](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

**More information does not equal more accuracy.** In the horse-race handicapper study, accuracy stayed flat as the amount of information grew, while confidence rose. Experts' self-insight into how they weight variables "is usually faulty." — [Heuer 1999, ch. 5](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

**Analogy misuse:**
- Policymakers are "one generation behind," fighting the last war (May).
- Examples: the Munich and Korea analogies for Vietnam, and the Vietnam analogy against the Gulf War, where the terrain made the analogy fail.
- Single-analog inference is among the most error-prone kinds. — [Heuer 1999](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf); [Handy 2026](https://www.cia.gov/resources/csi/static/6-Analogical_Reasoning_in_Intelligence_Analysis.pdf)

**Mirror-imaging:** imputing one's own rationality to the actor. — [CIA Tradecraft Primer](https://www.cia.gov/resources/csi/static/Tradecraft-Primer-apr09.pdf)

**Rejecting deception or unproven hypotheses because no evidence was seen.** "If deception is planned well... one should not expect to find evidence of it readily at hand." — [Heuer 1999, ch. 8](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

**Planning fallacy and strategic misrepresentation.** Examples: students expected to outperform 84% of their peers, and project cost overruns were systematic. — [Flyvbjerg 2006](https://arxiv.org/pdf/1302.3642)

**Over-provisioning as a side effect of reference-class uplifts.** Large reserves can themselves induce overspending ("reserves will be spent simply because they are there"). They must be paired with incentives and controls. — [Flyvbjerg 2006](https://arxiv.org/pdf/1302.3642)

**Over-hedging and vagueness:**
- ICD 203 forbids avoiding difficult judgments "to minimize the risk of being wrong" and bans mixing likelihood and confidence in one sentence. — [ICD 203](https://archive.dni.gov/files/documents/ICD/ICD-203.pdf)
- Coarsening probabilities into verbal bins measurably lowers accuracy. — [Friedman et al. 2018 (HKS)](https://www.hks.harvard.edu/publications/value-precision-probability-assessment-evidence-large-scale-geopolitical-forecasting)
- Describing instead of judging is an evasion. — [Heuer 1999](https://www.cia.gov/resources/csi/static/Pyschology-of-Intelligence-Analysis.pdf)

**Horizon limits.** Expert accuracy approaches chance at around 5 years. — [AI Impacts GJP synthesis](https://aiimpacts.org/?p=1283)

**Overclaiming technique efficacy.** One example is the "pre-mortem = 30% more accurate" claim. — [Jason Collins](https://corporate.jasoncollins.blog/premortem)

### Inferences
- Map each failure mode to a guardrail, with two goals: let original judgments through, and keep the system from collapsing into empty hedging.

  | Failure mode | Guardrail |
  |---|---|
  | Satisficing | Require ≥2 rival hypotheses or moves per judgment |
  | Analogy misuse | Require a similarities–differences table, and cap single-analog confidence at Moderate |
  | Base-rate neglect | Fetch or compute a reference class before case-specific reasoning |
  | Mirror-imaging | Red-team first-person rewrite for any actor-move claim |
  | Vividness | Down-weight a single vivid precedent when a larger class contradicts it |
  | Hedging | Never suppress a judgment for low confidence; emit it labeled "Low confidence" with explicit gaps, and keep numeric probabilities internally |

- A gate that blocks any claim not stated by a source would reproduce the "describe and let the consumer decide" failure. ICD 203 resolves this tension by labeling and confidence-tiering instead of blocking. The claim-support gate should therefore check that every *premise* is sourced and that the *inferential step* and its assumptions are stated, not that the conclusion itself appears in a source.
- Narrative coherence is a known risk in Heuer's sense: a vivid story outweighs statistics. ACH's "disprove rather than prove" step and the sensitivity check on critical evidence (ACH step 6) are the procedural countermeasures and can be automated as LLM passes.

### Gaps
- I did not source Taleb's "narrative fallacy" directly. Heuer's vividness and causal-coherence chapters cover the mechanism instead.
- Tetlock's *Expert Political Judgment* (2005) findings on hedgehogs versus foxes were not retrieved directly.
- I found no published empirical study measuring the cost of over-restrictive, "nothing to report" analytic outputs. The case against over-hedging rests on ICD 203's normative text and on the Friedman et al. precision results.
