# 20. Lead acquisition: revised commercial interpretation

Update incorporating the five further batches received after the 80-image edition. The full review now covers 176 distinct screenshot files in nine batches. Several files show overlapping data; their values are not added together. The final sidebar-settings image is outside business scope. New findings below supersede any earlier uncertainty about whether initial GA4 leads are recorded.

GA4 records 73 new leads and zero qualified/converted leads. Google's New leads metric counts unique users triggering generate_lead, not raw submissions or necessarily unique CRM contacts [H1]. Initial lead measurement therefore exists, although its triggers, identity handling and business validity still need checking. The main lifecycle gap is after acquisition; zero reported outcomes cannot establish a zero real-world close rate.

| First-user channel | New leads | Share of recorded new leads |
|---|---|---|
| Paid Search | 39 | 53.42% |
| Paid Social | 17 | 23.29% |
| Direct | 11 | 15.07% |
| Organic Search | 4 | 5.48% |
| Cross-network | 1 | 1.37% |
| Referral | 1 | 1.37% |

Paid Search and Paid Social together account for 56/73 = 76.71% of recorded leads. This changes the commercial priority: preserve lead-producing activity while investigating quality and downstream outcomes. Organic Search's stronger engagement does not make it the largest lead source. Cross-network has substantial traffic but only one recorded lead in the channel view, which merits an objective/attribution review before additional investment.

Source/medium detail attributes 39 to google/cpc, 11 to Direct, 9 to fb/paid_social, 7 to ig/paid_social, 4 to google/organic, and one each to data-not-available, fb/paid and l.wl.co/referral. These visible rows also sum to 73. The platform view groups the same total as Google Ads 39, Meta Ads 17, Unlabeled 14 and Manual 3. These are alternative groupings, not extra leads.

The Google Ads network view shows Google search 39 and '(not set)' 34, with zero new leads displayed for Cross-network and Search partners. A nonzero user key event rate alongside zero new leads is possible because the selected metric includes other key events. Do not assume the network '(not set)' row means 34 lost Google Ads leads.

Ad-group lead counts are 19 for 'Ad group 1', 7 for ITR_50% OFF_Business_Search, 5 for TPA_Salary_ITR_Search_Aug, 4 for ITR_F&O_Search, 2 for TPA_Sale_On_Property_Search_Aug1, and one each for TPA_TAX_Search_Aug and the group labelled Performance Max. These sum to 39; '(not set)' adds 34 to reach 73. Obtain stable IDs before renaming or joining records; the campaign and ad-group scopes differ.

Do not adopt the displayed 6.59%, 6.82%, 6.58%, 7.26% or 6.61% user key event rates as lead-to-sale rates. They vary between views and are set to All events. Filter the exact business event and align dates, dimensions and eligible users. Compare the 73 recorded lead-users with accepted CRM enquiries by identity and time; do not divide 93 CRM wins by these 73 to produce a close rate.

# 21. Session sources and campaign-cost import repair

New session source/medium rows confirm that the poor 'paid' cohort is not just a first-user reporting effect. Engagement time below is per session, unlike the per-active-user measure in section 14.

| Session source / medium | Sessions | Engaged | Engagement rate | Time / session |
|---|---|---|---|---|
| google / organic | 2,969 | 2,006 | 67.56% | 1m 42s |
| google / cpc | 2,815 | 1,873 | 66.54% | 55s |
| ig / paid | 394 | 88 | 22.34% | 2s |
| fb / paid | 330 | 85 | 25.76% | 3s |
| ig / paid_social | 260 | 195 | 75.00% | 50s |
| fb / paid_social | 255 | 149 | 58.43% | 43s |
| (not set) | 201 | 0 | 0.00% | 2m 22s |
| bing / organic | 145 | 95 | 65.52% | 2m 04s |
| localhost:8080 / referral | 121 | 81 | 66.94% | 1m 07s |

The ig/paid_social rate is 52.66 percentage points above ig/paid; the Facebook difference is 32.67 points. Compare the actual ad cohorts, dates and destinations, not just the labels. The 201 '(not set)' sessions with zero engagement and positive average time sharpen the session-association investigation. They may contribute to Unassigned, but an event-level join is needed to confirm overlap; never add them to the earlier 206 Unassigned as separate cases.

Session source platform is populated: Unlabeled 6,588; Manual 6,043; Google Ads 2,767; Meta Ads 625; a blank-label row 49; Other Ads 37. The blank row is observed as blank; it must not be renamed '(not set)' without verification. These rows sum to 16,109, not the 16,136 total; their engaged counts sum to 10,631 versus 10,612. Preserve the discrepancies and inspect exports, processing state and aggregation before reconciling.

The filtered campaign-import validation report shows coverage 0/12, 8,688 events, 73 key events, zero imported clicks/impressions/cost, and 'No Campaign data' on the visible pairs. This establishes no joined campaign data in that displayed scope. It does not establish that the business spent zero, that all account integrations are absent, or that these 73 key events equal the 73 unique new lead-users.

Repair choices: (1) join platform spend exports to CRM outcomes outside GA4 for a controlled first reconciliation; (2) use a supported direct campaign import if its mapping supports the account's tags; (3) use a prepared daily CSV/Sheets/warehouse feed when historical variants require explicit mapping. Google's documentation identifies source, medium and date as required keys, currency when cost is included, and campaign ID/name as useful matching fields [H2].

For this account, preserve raw ig/fb/meta/an and paid/paid_social labels. Build a mapping to actual campaign and placement IDs; do not assume 'an' is a verified placement. Never copy the same campaign spend in full onto several source aliases. Allocate only with defensible platform detail, or report at a coarser reconciled level. Check account timezone, currency, date, case and campaign identifiers. Pilot one campaign/day; compare imported, matched and unmatched amounts, then scale. Cost joining and qualified-lead import solve separate problems.

# 22. Landing pages: intent, missing entries and staff use

The landing-page view reports 16,136 sessions, 8,911 active users, 8,859 new users, 4,156 key events and INR 0 revenue. These key events include All events and are not 4,156 customers. Landing-page session values describe the entry page; page-path view counts later in this report describe browsing throughout a visit. They cannot be used interchangeably.

| Landing page | Sessions | Share | Key events, all types | Engagement / session |
|---|---|---|---|---|
| Homepage / | 8,477 | 52.53% | 2,940 | 1m 17s |
| ITR filing service | 1,552 | 9.62% | 144 | 59s |
| (not set) | 938 | 5.81% | 26 | 45s |
| /dashboard | 868 | 5.38% | 91 | 1m 01s |
| /login | 706 | 4.38% | 98 | 1m 25s |
| GST returns service | 683 | 4.23% | 301 | 1m 04s |
| /whatsapp-admin | 328 | 2.03% | 228 | 7m 12s |
| /signup | 260 | 1.61% | 3 | 25s |
| /profile | 154 | 0.95% | 5 | 1m 36s |
| Registration services | 146 | 0.90% | 5 | 19s |

The homepage is the largest entry point, so service routing there deserves attention. Preserve a returning-client sign-in route while making the relevant enquiry action clear for new prospects. Test a relevant service landing page for paid campaigns rather than assuming every campaign should land on the homepage.

938 '(not set)' landing sessions is a larger concern than the earlier 201 source '(not set)' sessions. The two populations may overlap; they measure different missing fields. Google's existing guidance [G15] identifies missing page_view as one cause of an unset landing page. Inspect first event, route changes, consent transitions and server-event session association. Do not invent a literal '(not set)' webpage or redirect it.

The admin entry has 328 sessions from only 21 active users, 228 key events and lengthy engagement. This is a high-priority staff-role segmentation check, not automatic proof of contamination or a security exposure. Use authenticated roles to classify staff/admin activity, keep access control independent of analytics, and inspect whether administrative actions are marked as marketing key events. Login/profile activity can also belong to real customers or consultants; retain operational reporting separately.

The 15 October landing-page AI card says 270 homepage sessions against expected 20; its India detail says 21 to 272 and Direct detail 6 to 216. Page-view AI cards separately report 812 homepage views against expected 31, Chrome 38 to 744 and Google-device views 27 to 591. These are different metrics/windows with AI-generated explanations. Use them to locate an investigation, not to prove acquisition success, device fraud or a causal campaign.

Acceptance: a clean visit starts with a correctly associated page_view; accepted lead, service choice and payment can be attributed to that visit when consent permits. Staff, customer and consultant roles have explicit reporting treatment. A campaign/landing-page table shows unique accepted leads and paid outcomes, not all-key-event counts as a proxy.

# 23. Ecommerce: distinguish absent measurement from abandonment

The sales screens show INR 0 total/ecommerce revenue, zero purchasers and first-time purchasers, no purchased items, no transaction IDs and no promotion data. Acquisition-cohort tables show zero transactions and INR 0 average 120-day value across the displayed channels/sources/campaigns. These extend the evidence for missing or unrecorded commercial outcomes; they do not prove no customer paid outside GA4.

The purchase journey records 8,697 users at session start and zero at view product, cart, checkout and purchase. Its open-funnel toggle is on. The checkout journey has no observed checkout entrants; a 100% label on its first step is a funnel convention, not evidence of checkout starts. Both screenshots display warning icons whose details were not opened. Do not interpret these screens as verified 100% business abandonment.

Google's standard purchase journey depends on specific ecommerce events such as view_item, add_to_cart, begin_checkout and purchase [H3]. A tax-service business may legitimately accept a consultation or enquiry without a cart or shipping step. Select the report to fit the real journey; do not invent carts or shipping events merely to populate the template.

| Commercial route | Appropriate measurement | Acceptance test |
|---|---|---|
| Consultation / assisted enquiry | Service view, accepted enquiry, contact, qualification, quote, paid order and completion | One accepted case follows lifecycle without counting a click as a sale |
| Direct online service purchase | Real service selection and checkout events, verified purchase and refund | Successful and failed payments, redirects and webhook retries reconcile |
| WhatsApp / phone payment | CRM-originated order and confirmed receipt; compatible analytics/ad linkage if available | Payment linked to internal lead/order; no invented historical web session |
| Recurring compliance | Renewal order, service period and refund/cancellation handling | Each real renewal has one payment record and an explicit service period |

Use a stable service catalogue for item_id/item_name, service category and applicable year, without placing personal tax information in analytics. Capture coupon only when actually applied. Empty coupon/list/promotion cards can be normal if those features are unused. Purchase and refund should reconcile to a financial ledger; customer conversion, contract value and money collected are separate states.

Average 120-day value concerns recorded revenue over an early user-lifetime window [H4]. It is not an estimate of all future customer value. With no recorded transactions, its zero value cannot justify a zero-CAC target or prove the product has no lifetime value. After instrumentation, assess mature cohorts and retain a separately reconciled CRM/finance view of value.

Mobile priority is now supported by the purchase-journey entry population: 6,623 mobile users (76.15% of 8,697), 2,065 desktop, 27 tablet and 9 smart-TV users. Their sum is 8,724, exceeding the unique total by 27; users can appear on more than one device, and this is not a device-session share. Prioritise real mobile-browser tests of service choice, login, upload and payment return. The earlier uncertainty about device mix is superseded for this measured population, while device-specific conversion performance remains unknown.

# 24. Event quality, forms and page identity

The latest event report records 197,221 events across 8,917 total users; page reports show 8,912 active users. Earlier screenshots show 197,215 events and 8,916 total users. Preserve these capture-specific values: small changes during a live reporting day are not by themselves evidence of a tracking defect.

| Event | Event count | Users shown for event | Interpretation |
|---|---|---|---|
| page_view | 71,971 | 8,866 | Page-view events, not unique people or sessions |
| scroll | 20,302 | 4,014 | Interaction measure; no sales implication |
| gtm.dom | 20,290 | 3,412 | Technical-looking event; inspect sender and purpose |
| user_engagement | 18,109 | 6,092 | Engagement events |
| session_start | 16,043 | 8,842 | Event count differs from the sessions metric |
| analytics | 11,807 | 3,515 | Ambiguous custom name; inspect payload and trigger |
| web_vitals | 9,742 | 1,290 | Performance events exist; inspect actual values |
| first_visit | 8,860 | 8,813 | Event/identity measure, not new customers |
| form_start | 7,056 | 1,313 | May include different form types |
| form_submit | 6,688 | 508 | 13.17 submissions per event-user; not verified leads |

Recorded form_submit is not the same contract as accepted generate_lead [H1,H5]. Audit enquiry, login, signup, calculator and admin forms separately. Compare form ID, page, backend result, role and repetition. Candidate explanations include legitimate repeat use, multiple form types, retries, duplicate listeners or broad triggers. The screenshot alone cannot choose the cause.

Do not present 6,688/7,056 as a 94.8% lead-form completion rate: they are event counts across unverified form populations. Neither 508 submission-users nor 73 generate_lead-users is directly comparable to all 5,701 CRM records without matched scope, identity and dates. Validate a known enquiry from entry through server acceptance to one CRM ID and one generate_lead, and separately check rejected submissions and repeat attempts.

Audit event semantics and key-event configuration before adding more tags. Technical-looking names can reflect a catch-all event relay; verify rather than assume. Retain useful operational telemetry outside the acquisition scorecard. The existing web_vitals events mean the first performance step may be inspecting collected LCP/INP/CLS values, not installing another tracker. Test page_view once on initial load and once per intentional client-side navigation where applicable; do not count component rerenders as new page views.

Page totals are 71,971 views, 8.08 views per active user and 2m 27s engagement per active user. Repeated use is concentrated on /dashboard (7,894 views, 290 active users), /profile (3,601; 215), /vault (2,052; 144) and /my-earnings (1,622; 165). Their roles may include clients and consultants. Distinguish successful task completion from repeated work, refreshes and loops.

The top title has 21,367 views from 799 active users; a personalised-ecosystem title has 7,681 from 183 with 20m 34s engagement. These titles cannot be assumed to mean the same URL as /. Build a hostname + stable page-path + title + role mapping. Title changes and reused titles fragment reporting; they do not alone prove duplicate SEO pages. The /login page's 917 all-type key events in a page view and 98 in a landing view describe different attribution scopes, not interchangeable counts.

# 25. Audience, retention and local discovery

Country activity is led by India at about 8.1K active users, followed by the US 556, China 43, UAE 40, Ireland 28, Sweden 21 and Brazil 18. These are approximate/partial rows. Country presence does not establish residency, nationality, bot activity, customer demand or paid targeting. Test NRI-specific enquiries only against actual service eligibility and qualified demand; do not interpret foreign visits as permission to expand tax services abroad.

City activity is Hyderabad 625, Bengaluru 610, Mumbai 566, Ahmedabad 539, Pune 527, Delhi 501 and Indore 380. New-user and returning-user views have different rankings: Virar 115 and Vasai-Virar 106 lead the displayed returning cities. Evaluate serviceable enquiries, consultation scheduling and delivery capacity by city; do not create location pages that imply unverified physical offices.

Language charts show English predominating, Hindi second in active/new users, with smaller Gujarati, Chinese, Marathi, Bengali and Kannada rows. Language reflects browser/device settings [H6], not a confirmed preferred consultation language. The earlier absence of language evidence is superseded, but business need remains unproven. Start with plain English plus a preferred-language question and a measured Hindi-assistance pilot; fund full localisation only when completion and qualified demand justify it.

Stickiness shows DAU/MAU 0.5%, DAU/WAU 2.2% and WAU/MAU 22.7%. These are rolling activity ratios [H7], not percentages of paying customers retained. Account for the current partial day, annual filing seasonality and staff traffic. The 42-day engagement chart's near-zero tail is a reason to inspect cohort sizes and return behaviour; it does not establish revenue churn or failure of every customer to return.

Business Profile reports 376 interactions: 12 call actions, 48 website clicks and 316 direction requests. These sum to 376; the interaction mix is 3.19%, 12.77% and 84.04% respectively. Its screenshot date control is cropped, so do not silently compare its period with the annual GA4 period. Google defines Calls as clicks on the call button, not answered calls; directions do not establish office visits or sales [H8].

Local-service actions: verify accurate business details, services, opening hours and appointment route; tag the profile's website link consistently; record answered/missed enquiries and booked appointments in the CRM. Review whether directions match the business model and actual service location. Where appropriate, ask clients how they found the service without replacing measurable attribution. The empty 'menus' card is not a priority defect for a tax-services business.

Real-time acquisition cards show four active users in India at capture. This is a momentary operational snapshot, not a daily baseline or a growth result. The existing 100% Web platform card remains a platform-composition metric, not evidence of complete sales coverage.

# 26. Revised priorities and verification checklist

The new screenshots refine, rather than remove, the earlier priorities. The business has recorded leads and substantial mobile activity, but the financial and lifecycle connection remains unverified.

| Priority | Concrete action | Alternatives / acceptance gate |
|---|---|---|
| P0: Lead-to-outcome gap | Verify the existing generate_lead trigger; join the 73 lead-users to eligible CRM cases; add qualified/closed outcomes | Start with a secure matched export; automate after semantics pass; no duplicate lifecycle events |
| P0: Commerce absent | Map real enquiry-to-order and direct-payment routes | Custom service funnel or real ecommerce events as applicable; do not fabricate cart/shipping; ledger reconciles |
| P0: Cost not joined | Pilot import or external reconciliation for one campaign/day | Check mapping, dates, currency and duplicate allocation; displayed zero is not used as actual spend |
| P1: Key-event ambiguity | Audit which actions are marked key events, including staff/login and form events | Separate intent, accepted lead and commercial outcomes; future config changes labelled by date |
| P1: Form volume | Test each form type and success/retry path | Accepted enquiry generates one lead; invalid or administrative forms do not |
| P1: Missing sessions/pages | Inspect 938 unset landing sessions, 201 unset source sessions and 49 blank-platform sessions | Evaluate overlap; replay consent/navigation/server-event cases; do not sum populations |
| P1: Mobile completion | Walk through enquiry, account access, document upload and payment on real phones | Use existing web_vitals data if valid; test errors and save/resume before app investment |
| P1: Paid-cohort quality | Compare paid and paid_social campaigns with CRM quality and spend | Preserve lead-producing campaigns; alter only cohorts supported by matched evidence |
| P2: Local and repeat value | Pilot profile appointment tracking and service-specific reminders | Measure booked/attended appointments and service-cycle renewal, not directions or daily activity alone |

First review pack for the internal team: a KPI/event dictionary; one anonymised accepted enquiry traced to an order; matched spend and receipts for one campaign/day; a list of current key events; mobile failure observations; and an exception list of missing IDs/dates. These are implementation requirements for the authorised team, not requests for this chat to access their systems.

Decision gates remain: tax-owner sign-off on affected calculator years; no duplicates in controlled enquiry/payment/refund tests; transparent consent eligibility; successful production event delivery; finance/CRM reconciliation with documented exceptions. Do not use the latest screenshots to backfill invented historical orders or to conclude that zero tracked purchases means zero real revenue.

Recommended management view now has three explicitly separate layers: acquisition quality and source; accepted/qualified customer cases; and verified money/service delivery. Add mobile, role, geography and language cuts only where sample size supports decisions. A single blended 'conversion rate' cannot represent all three layers.

# 27. Additional evidence ledger and definitions

All 96 newly supplied files after the 80-image edition were inspected as attachments. Exact filename stems below are on 2026-10-28. Distinct filenames can show the same metrics; their counts are evidence coverage, not sample size. Repeated earlier uploads remain counted once.

| New batch | Exact screenshot time stems | Coverage |
|---|---|---|
| 5: 20 files | 101600, 101618, 101631, 101642, 101704, 101715, 101726, 103305, 103323, 103342, 103401, 103418, 103438, 103458, 103510, 103544, 103602, 103612, 103719, 103728 | Session sources/platforms, lead channels/source/platform/campaign/network, anomaly cards |
| 6: 20 files | 103752, 103847, 103956, 104011, 104023, 104033, 104243, 104305, 104315, 104342, 104355, 104403, 104422, 105048, 105116, 105127, 105152, 105211, 105231, 105301 | Ad-group leads, landing pages, zero-revenue cohort views |
| 7: 20 files | 105312, 105548, 105603, 105613, 105626, 105704, 105723, 105806, 105820, 105852, 105903, 105919, 112130, 112145, 112156, 112210, 112228, 112242, 112251, 112306 | Sales, ecommerce funnels, device mix, geography, engagement and event overview |
| 8: 20 files | 112316, 112331, 112342, 112356, 112457, 112508, 112523, 112533, 112544, 112620, 112631, 112641, 112727, 112835, 112853, 112904, 112913, 112942, 113004, 113021 | Stickiness/language, page paths/titles, retention and event trends |
| 9: 16 files | 113042, 113058, 113138, 113153, 113204, 113230, 113242, 113252, 113314, 113338, 113415, 113427, 113511, 113525, 113541, 122855 | Exact event counts, page corroboration, Business Profile, campaign-import validation, acquisition; final sidebar excluded |

Definitions verified against official Google documentation on 28 October 2026. Cropped date controls and unopened warning details remain limitations. Small changes during capture are retained; historical attribution scopes are not forced into a single funnel.

- H1 - Lead report and unique-user event definitions. https://support.google.com/analytics/answer/16376749
- H2 - Campaign data import, mapping and validation. https://support.google.com/analytics/answer/10071305
- H3 - Purchase journey, required events and open/closed funnels. https://support.google.com/analytics/answer/13128171
- H4 - Acquisition overview and average 120-day value. https://support.google.com/analytics/answer/13410671
- H5 - Enhanced measurement and form interactions. https://support.google.com/analytics/answer/9216061
- H6 - Event parameters, including browser/device language. https://support.google.com/analytics/table/13594742
- H7 - Rolling user stickiness ratios. https://support.google.com/analytics/answer/12993725
- H8 - Business Profile performance definitions. https://support.google.com/business/answer/9918094

Arithmetic checks: 39+17+11+4+1+1=73; paid share 56/73=76.71%; 75.00%-22.34%=52.66 percentage points; 58.43%-25.76%=32.67 points; 12+48+316=376; device rows sum to 8,724 versus 8,697 unique funnel entrants. These are descriptive results, not causal estimates or forecasts.
