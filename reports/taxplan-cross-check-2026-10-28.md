# TaxPlan Advisor: cross-check of evidence and proposed fixes

28 October 2026. Supplement to the 28-page performance report.

Updated after the second pass: all 176 original files were located, hashed and visually revisited using 22 labelled review sheets; critical lead and event tables were also reopened individually. Four files concern unrelated sidebar/disk interfaces. Comparisons were grouped by metric, period, attribution scope and proposed action, rather than comparing every unrelated pair. No underlying account, export, code, payment or implementation was tested. Sections 28-31 of the main report contain the final decisions, discrepancies and NOT RUN execution gates. Original-file coverage is distinct from proof that every proposed root cause is correct.

## Findings that survive comparison

| Proposed action | Cross-check | Verdict and boundary |
|---|---|---|
| Reconcile CRM stages with analytics | CRM shows 93 wins; GA4 shows 73 new lead-users and zero qualified/converted leads | Keep. Compare matched dates and IDs; do not calculate 93/73 or call every win missing from GA4. |
| Verify the accepted-enquiry trigger | Lead-acquisition tables already contain 73 lead-users; forms show 6,688 submissions across 508 users | Keep as an audit of existing collection. Do not add another lead tag blindly or treat submissions as accepted enquiries. |
| Connect verified receipts to orders and attribution | GA4 revenue, purchasers and transactions are zero; CRM has a clipped paid-revenue tile and paid-via-WhatsApp count | Keep. Zero recorded analytics revenue is not zero business revenue. CRM/finance reconciliation precedes financial claims. |
| Reconcile campaign spend | Filtered import view shows 0/12 coverage and no joined cost/click/impression data | Keep within that displayed scope. This does not prove zero spend or that every integration is absent. |
| Investigate weak paid cohorts | Paid Search supplies 39 lead-users and Paid Social 17, while some paid cohorts have very weak engagement | Keep investigation; reject a blanket paid-budget cut. Cost, qualification and collected margin must decide. |
| Improve organic journeys | Major first-user Organic Search engagement is strong; only four new lead-users appear under Organic Search | Keep as an experiment. Strong engagement is not proof of the best commercial channel. |
| Choose an appropriate service funnel | Standard ecommerce steps are zero while lead capture and CRM activity exist | Keep custom service measurement or genuine ecommerce events as applicable. Reject confirmed 100% customer abandonment and artificial cart/shipping events. |
| Prioritise mobile testing | Mobile is 76.15% of the purchase-journey entry-user population | Keep. This does not establish a mobile defect, conversion rate or share of all sessions. Device rows exceed the unique total; do not sum them as unique people. |
| Check staff and development activity | Admin/login/dashboard activity and localhost referrers appear alongside real service traffic | Keep classification and diagnostic work. Do not exclude all these sessions without verified roles/hosts; real customers can use login/dashboard. |
| Inspect missing landing/source fields | 938 unset landing sessions, 201 unset source sessions and a separate blank-platform row | Keep distinct investigations. Do not add the counts or assume one cause or a literal missing webpage. |
| Audit event and page identity | Technical-looking events, repeated submissions, changing titles and high repeat operational-page views | Keep. Duplicate firing, loops and title reuse remain hypotheses until reproduced. Existing web_vitals collection should be inspected before installing more tracking. |
| Review calculator rule versioning | Publicly retrieved explanatory text and the researched amendment differ | Keep tax-owner review of year, text and calculation. This is not a tested algorithm defect; retain rules for historical years. |
| Use retention, language and local discovery appropriately | Activity ratios, browser languages and Business Profile actions appear in different views | Keep service-cycle renewal, preferred-language and appointment experiments. Daily activity is not financial retention; language is not declared preference; call clicks/directions are not answered calls/visits. |
| Adopt benchmark workflow patterns | Selected official vendor pages describe intake, expert routing, document tasks, status and reminders | Keep as design candidates. Existing TaxPlan capabilities must be checked first; vendor claims do not establish uplift or Indian legal/integration suitability. |

## Comparisons that remain unresolved

- The report preserves inconsistent visible row totals rather than manufacturing a reconciliation. Examples include first-user channel rows summing above the displayed user total and engaged-session rows summing above their displayed total. Exports, filters, identity, aggregation and processing state are needed to establish why.
- Cross-network has one new lead-user in a channel view but zero in the Google Ads network view. These dimensions are not interchangeable, and this difference is not proof of a broken connector. A matched export is required.
- All-events key-event rates change across lead-report dimension views (including 6.59%, 6.82%, 6.58% and 7.26%). The screenshots do not establish why. Do not use these as accepted-lead conversion rates or compare them as one stable KPI.
- Counts increase slightly across captures (for example, 197,215 to 197,221 events). Live processing is a possible explanation, not a confirmed diagnosis for every discrepancy.
- Capture-specific dates, cropped periods, partial-day reporting and unopened warning details limit cross-report comparisons. CRM wins and Business Profile interactions cannot be silently assigned the annual GA4 period.
- The October anomaly cards provide investigation locations, not verified causes. Neither bot traffic, a new page launch nor campaign success is established by the generated explanation.
- Six CRM leads are marked unassigned, but the other status tiles do not partition all 5,701 records. This warrants an ageing/ownership check; it does not establish that assignment is the main sales bottleneck.

## Status of the fixes

The recommendations hold up as prioritised diagnostic and implementation specifications, with the boundaries above. Their root causes, technical compatibility and business results remain unverified. They are not production-tested fixes or a guarantee that every issue has been found.

Keep the order: reconcile definitions and scope; trace a real accepted enquiry and verified payment; establish spend and financial reconciliation; reproduce suspected defects; implement the smallest supported repair; validate consent, retries and refunds; then assess clean cohorts and commercial outcomes. Feature purchases and budget changes should follow those gates.
