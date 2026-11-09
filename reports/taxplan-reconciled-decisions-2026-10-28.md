# 28. Cross-screenshot review: actions supported now

Second-pass review, 28 October 2026. All 176 referenced original files were located and indexed. Every image was visually revisited through 22 labelled review sheets; critical lead/event tables were also reopened individually. Four files show unrelated sidebar/disk interfaces; 172 show TaxPlan reporting or reporting controls. Overlapping views corroborate evidence, not additional customers. This is a screenshot-level review, not a transaction or implementation audit.

Sections 28-31 govern the confidence and execution conditions of earlier recommendations. Supported means the evidence justifies the stated next action, not that a root cause or business uplift has been proved.

| Action / owner | Evidence compared | Final decision |
|---|---|---|
| Reconcile lead lifecycle / CRM + analytics | 103323/103342: 73 lead-users and 0 qualified/converted; CRM 2026-10-27 101535: 93 wins | Keep reconciliation first. Align dates, eligible cases and identity. Do not calculate 93/73 or assume every CRM win should appear in this property. |
| Audit existing accepted-lead collection / engineering | Lead views record 73 users; 113042 records 6,688 form_submit events across 508 users | Verify the existing trigger. Do not add duplicate collection or treat all forms as enquiries. One person can create multiple legitimate cases; user counts and case counts are separate units. |
| Reconcile collected money / finance + engineering | CRM paid tile/WhatsApp count versus zero GA4 transactions, purchasers and revenue across sales/cohort views | Keep order/receipt reconciliation. Determine which payments should reach GA4 under consent and matching limits; do not claim the business has no revenue. |
| Reconcile campaign cost / marketing operations | 113415/113427: 0/12 join coverage in a filtered report | Keep one-campaign/day import or external ledger pilot. Verify actual spend, mapping and currency. Zero displayed imported cost is not zero advertising spend. |
| Preserve commercially useful paid traffic / marketing + sales | 103323: Paid Search 39 and Paid Social 17 lead-users; acquisition views show weaker engagement for some paid cohorts | Reject a blanket cut. Keep investigation and capped tests; use eligible qualified-case cost and collected contribution before deciding which cohort to scale or pause. |
| Test mobile journeys / product + engineering | 105820: 6,623 mobile users in an 8,697-user funnel-entry population; 113042: existing web_vitals events | Keep phone walkthroughs and inspection of measured performance. No mobile conversion defect or need for a native app is proved. |

The 73 lead-users recur across channel, source, platform, campaign and ad-group views. This supports the displayed total, not the business validity of each enquiry. Google defines New leads as unique users with generate_lead [H1, rechecked 28 October 2026]. Record accepted cases in the CRM separately; a universal one-user-to-one-case join would lose legitimate repeat service requests.

The paid share is descriptive: (39+17)/73 = 76.71%. It is not an incremental lift, close rate, profit share or reason to increase budgets before qualification and cost are reconciled. Organic Search's stronger engagement and four displayed lead-users justify improving its path to enquiry, not declaring it the most profitable channel.

# 29. Cross-screenshot review: conditional fixes and rejected readings

The following proposals survive only with a diagnostic gate. Do the check first; implement the corresponding change only when it identifies a defect or unmet customer need.

| Candidate fix | Evidence and competing explanation | Required gate |
|---|---|---|
| Repair duplicate events/forms | 6,688 submissions from 508 users; technical-looking event names | Inspect form type, role, backend acceptance and retries. Legitimate repeat operational forms are possible; remove duplication only when reproduced. |
| Remove internal/test activity from marketing | localhost referrers; /whatsapp-admin, dashboard, login and profile activity | Verify host and authenticated role. Referrers are not the collection hostname; clients and consultants can legitimately use account pages. Retain separate operational reporting. |
| Repair unset landing/source fields | 938 unset landing sessions; 201 unset source sessions; 49 blank-platform sessions | Inspect each field's event sequence, session association and configuration. Do not add these overlapping counts or assume one universal cause. |
| Change ecommerce funnel | Lead/CRM activity exists while product/cart/checkout/purchase measurement is zero | Map the actual service journey. Use a custom enquiry-to-paid funnel or real checkout events. Reject confirmed 100% customer abandonment and fabricated cart/shipping events. |
| Repair SEO titles/URLs | Page-title and page-path totals differ; fbclid and functional parameters appear | Map hostname/path/title/year first. Reporting normalisation and search canonicalisation are separate actions; preserve functional parameters and attribution. |
| Change calculator rules | Retrieved rule text differs from researched amendment; no output tests supplied | Tax owner verifies year-specific law, explanatory copy and numerical fixtures. Correct affected text/logic only; do not replace historical constants indiscriminately. |
| Add routing, portal or reminders | Six unassigned CRM records; public TaxPlan features and international workflow examples | Inspect current functionality, ageing, ownership and service completion. Configure existing capabilities before replacement; measure actual bottlenecks. |
| Expand language/local/retention features | Browser-language ranks, local actions and rolling activity ratios | Pilot preferred-language support, appointments and service-cycle renewal. Reject inferred language preference, answered calls, office visits or financial churn from those charts alone. |

Not supported by screenshots: blocking foreign countries, declaring a bot attack, claiming a new login-page launch, replacing the CRM, launching a native app, expanding into foreign tax jurisdictions, treating every '(not set)' Google Ads row as tracking loss, or buying a feature solely because a benchmark provider advertises it.

Seven-market benchmarking remains useful as a menu of workflow patterns. It does not establish competitor conversion rates, implementation quality, Indian integration permissions or guaranteed revenue improvements. Foreign country activity in GA4 is separate from the selection of countries for feature research.

# 30. Discrepancy register and what is actually resolved

These are reporting differences, not a list of confirmed implementation bugs. A known scope difference can explain why two figures should not be equated without explaining every numerical mismatch.

| Comparison | What can be concluded now | Remaining verification |
|---|---|---|
| 73 new lead-users versus 73 campaign-import key events | Different units and scopes; equal totals do not establish identity | Event-name breakdown and case-level linkage; never join the two merely by count |
| Cross-network: 1 channel lead versus 0 Google Ads network leads | Different dimensions; the report contains a real displayed difference | Same-window export with both dimensions and eligible IDs; no connector diagnosis from this alone |
| All-events key-event rates: 6.59%, 6.82%, 6.58%, 7.26%, 6.61% | Displayed rates vary while new-lead total remains 73 | Inspect denominators, filters, event selection, processing and aggregation; do not invent a cause or use these as lead conversion rates |
| First-user versus session results | They attribute different things: initial acquisition versus individual sessions | Do not sum or divide them into an invented funnel; retain separate views |
| 10 channel rows: 8,934 users versus 8,916 total | User rows cannot be assumed to form an additive partition | Check identity/aggregation and the unseen eleventh row; no invented correction |
| Session channel rows: 16,092 sessions and 10,628 engaged versus 16,136 and 10,612 | Both displayed differences are retained, not silently reconciled | Complete export and report settings; do not assign a negative remainder to the unseen channel |
| Session platform rows: 16,109 and 10,631 engaged versus 16,136 and 10,612 | A second displayed aggregation discrepancy exists | Same-date complete export; blank label remains blank pending inspection |
| 8,724 device-row users versus 8,697 unique funnel entrants | Device rows may overlap users; the 76.15% value applies to that population | Identity and device aggregation; do not call it a share of all sessions |
| Landing versus page-path/title key events | Different reporting dimensions; login 98 versus 917 is not an interchangeable count | Trace the event and entry page separately; map titles to paths rather than assume they identify the same page |

Smaller changes, including 197,215 to 197,221 events and 8,916 to 8,917 total users, can occur during a live reporting day. The cause of each change is not proved. Cropped date controls, partial-day processing and unopened warning details remain explicit limitations. CRM wins, short-period snapshots and Business Profile totals are not assigned the annual GA4 period without evidence.

Google Ads '(not set)' rows also contain people outside Google Ads. In the network lead view, 39 Google-search users plus 34 not-set users equals 73; this does not mean 34 Google leads were lost. Manual source-platform cards being empty is also compatible with a populated source-platform report: these are different fields.

References rechecked for this pass: H1, lead-report unique-user definitions; H2, campaign-import matching, currency and source/medium mapping. G15 could not be retrieved again in this pass; its earlier cited guidance is not newly verified. No research failure is represented as confirmation.

# 31. Final execution gates and acceptance record

All rows below have status NOT RUN against TaxPlan. They are concrete checks for its authorised team. Screenshot review cannot supply their results. Fix specifications are ready; production repairs and commercial outcomes remain unverified.

| Gate / owner | Run and retain evidence | Pass condition / action on failure |
|---|---|---|
| Define units / analytics + CRM | Dictionary of event, person, lead case, order, receipt and service completion; dates/timezones and consent eligibility | Separate counts and attribution scopes. A person may have multiple cases/orders; explain every join cardinality. |
| Accepted enquiry / engineering + sales | Valid, invalid, repeated and concurrent submissions for each enquiry form; API/CRM/event delivery records | One event per accepted new case under the defined contract; retries do not create duplicate cases. Failed/login/admin submissions are not new enquiries. |
| Lifecycle transition / CRM owner | Contact, qualify, disqualify, close, reopen and repeat-stage tests | Events follow real transitions with controlled reasons and idempotent transition IDs. Reopening or a new service case is not discarded as a duplicate person. |
| Payment integrity / finance + engineering | Success, failure, timeout, return redirect, webhook retry, deposit/balance and refund tests | Receipts reconcile to ledger. Count the chosen payment/order model consistently; do not emit full order value again for every instalment. Refunds/discounts/tax treatment are explicit. |
| Collection and consent / analytics | Initial load, route navigation, reload, denied/granted consent and supported server-event association | No duplicate page_view from rerenders; expected page/session context when eligible. Keep personal/tax data out of analytics and do not invent identifiers for denied consent. |
| Spend pilot / marketing operations + finance | One campaign/day: platform spend, resolved IDs/names, source/medium/date/currency, matched and unmatched totals | Reconcile imported or external spend once. Do not copy full spend onto several historical aliases. Google Ads linking and non-Google cost import are distinct routes. |
| Tax correctness / tax owner | Year-specific law reference and independently calculated edge cases; test inputs and expected outputs | Sign off applicable years and text/output consistency before promoting the affected calculator. Failed fixtures block that change, not unrelated services. |
| Commercial decision / management | Clean, mature cases with qualification, collected value, variable delivery cost and acquisition spend | Apply a documented cohort-specific keep/change/stop rule. If sample or maturity is insufficient, retain a capped pilot and record uncertainty. |

Suggested sequence: first establish the dictionary, one accepted-enquiry trace, one verified payment trace and one spend pilot; then repair reproducible collection/CRM defects; then optimise mobile intake, service landing pages and delivery workflows. Assess renewal by the relevant service cycle. No increase in tracking volume, engagement, directions or form submissions substitutes for paid service outcomes.

Coverage record: the accompanying screenshot inventory lists all 176 filenames, file hashes, review group and related report sections. Four unrelated files are explicitly excluded from business inference. The cross-check note records the distinction between supported actions and conditional root causes. This audit establishes evidence traceability, not a guarantee that screenshots expose every business issue.
