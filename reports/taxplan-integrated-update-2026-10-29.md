# 37. Updated evidence and business interpretation - 29 October

Scope: all 36 new attachments were visually reviewed, compared within both new batches and matched to corresponding earlier tables and charts. Together with the previous inventory, 212 files were checked by hash; none is an exact duplicate file. Repeated information is counted once. The supplied 50-page secondary report remains reviewed separately in sections 32-36.

The new screenshots crop the reporting date/filter header. The changes below compare displayed snapshots; they are not verified daily increments, cohort growth rates or evidence that repairs were implemented. Earlier values remain dated observations rather than being silently overwritten.

| Metric | Earlier comparable display | 29 October capture | Interpretation |
| --- | --- | --- | --- |
| Total events / total users, Events table | 197,221 / 8,917 | 197,882 / 8,947 | +661 events / +30 total users; scope and processing must match before daily reporting. |
| page_view / scroll event counts | 71,971 / 20,302 | 72,235 / 20,380 | +264 / +78; activity exists, but this is not additional sales. |
| user_engagement / session_start events | 18,109 / 16,043 | 18,278 / 16,122 | +169 / +79; session_start event count is not the Sessions metric. |
| first_visit events | 8,860 | 8,893 | +33 events; do not substitute for new-user count, even if totals happen to match. |
| form_start events / users | 7,056 / 1,313 | 7,077 / 1,324 | More starts are displayed; no ordered, validated lead funnel is established. |
| form_submit events / users | 6,688 / 508 | 6,688 / 508 | Unchanged. This alone cannot establish a broken form or zero accepted leads. |
| Cohort new users | 8,859 | 8,893 | +34, while reported transactions and revenue remain zero. |
| Engagement time per session | 1m 21s | 1m 20s | A one-second displayed difference is not a meaningful performance verdict. |

The event table is captured at 150221, with continuation at 150230/150344. gtm.dom remains 20,290, analytics 11,807 and web_vitals 9,742. These static cumulative counts cannot prove tags were removed or repaired. The 150458 page-path table agrees with the event table on 72,235 views and 197,882 events. The later title and content-group tables show 72,239 views and 197,888 events. Preserve the +4 views / +6 events discrepancy rather than forcing agreement; intervening collection is a hypothesis.

Overall business conclusion remains unchanged: there is recorded audience activity, but no new screenshot establishes qualified cases, paid customers, acquisition cost or profitable growth. Existing payment and lifecycle reconciliation stays ahead of increasing spend. The new content-group evidence creates an additional reporting fix, described in section 39.

# 38. Cross-image validation: what agrees and what remains open

| Cross-check | Result | Consequence for recommendations |
| --- | --- | --- |
| 144656 primary channel versus 144746 default channel | Both show 8,893 new users; visible rows agree: Direct 3,995, Cross-network 1,594, Paid Social 1,182, Paid Search 1,006, Organic Search 688. | Corroborating dimensions; not two independent user populations. No new channel conversion claim. |
| 144827 source versus channel table | google/cpc 2,542 and google/organic 659; Direct 3,995. | Source and channel groupings differ. Do not equate google/cpc with the Paid Search-only channel. |
| 145257 source-platform rows | 3,509+2,495+2,300+534+55 = 8,893 exactly. | Visible platform totals reconcile. 'Unlabeled' is not automatically wasted acquisition or missing paid attribution. |
| 150006 activity versus 150019 stickiness | 14/637=2.20%; 14/117=11.97%; 117/637=18.37%, matching 2.2%, 12.0%, 18.4%. | Internal arithmetic corroborates this snapshot. These ratios are not annual tax-client renewal or cohort retention. |
| 150458 versus 150756/150837 | Page-path views/events differ from later title/group totals by 4/6; active users remain 8,942. | Keep capture-specific totals. Match filters, dates and exports before treating it as a collection defect. |
| Events users versus page-report users | 8,947 total users versus 8,942 active users. | Different metrics; do not report five missing people. |
| 150150 and 150419 anomaly cards versus earlier cards | Still describe 18 and 15 October, respectively. | Repeated historical alerts, not fresh 29 October incidents. Generated text cannot prove a new page launch or automation. |
| 150756 title versus path views | Old marketplace title still 21,367 views from 799 users; ITR service path remains 3,473 views. | Title counts cannot be mapped one-to-one to URL counts. Test route/title associations before changing page titles. |

Duplicate handling: 145922/150050 repeat engagement content; 150230/150344 repeat lower event rows with extra rows in the latter. 144707/144756 overlap lower channel rows under different dimensions. Upper/lower table captures are retained where they add rows. 145941/145952 select different chart metrics and are not redundant. Full filenames, classifications and hashes are recorded in the updated inventory; no image was deleted.

Changes since earlier cohort tables include Paid Other new users 51 to 70, Organic Search 680 to 688 and Direct 3,991 to 3,995. These do not establish channel efficiency, new budget allocation or incremental lift. No current lead report was supplied to update the earlier 73 unique lead-users, and no current CRM image was supplied to update 93 Won. Preserve those older timestamps.

# 39. Content-group repair: alternatives and acceptance tests

New direct evidence, 150837: 71,964 of 72,239 views (99.62%) show '(not set)' content group. The remaining displayed label is blank: 275 views. Neither row supplies a useful named service grouping. This is an observed reporting limitation; absent configuration, empty values or inconsistent event parameters are possible causes, not confirmed diagnoses.

The table reconciles for views (71,964+275=72,239), events (197,197+691=197,888) and key events (4,156+14=4,170). Active-user row counts overlap: 8,909+55=8,964 versus 8,942 unique total. Do not sum them as distinct people. Likewise, 14 key events attached to the blank group do not mean 14 accepted leads or purchases.

Google documents content grouping through the content_group parameter, including URL-rule mapping in Tag Manager [K1]. Select the least disruptive approach that fits the existing site:

| Option | Suitable use | Limitation and decision |
| --- | --- | --- |
| URL-derived groups in a report/export | Immediate analysis of existing page-path records | Fast historical workaround; cannot reliably distinguish staff from clients or reconstruct missing commercial outcomes. Keep original data intact. |
| Tag Manager path-rule table | Stable public service/calculator routes and existing managed tags | Configure explicit matching order and a named fallback. Review first; test before publishing. |
| Application-provided page metadata | Dynamic routes, shared templates or overlapping service paths | More engineering work, but ties group/title to the actual rendered screen. Use when path rules are insufficient. |
| Separate product reporting | Portal usage and marketing need different reports or access arrangements | Start with segmentation; separate streams/properties only for a documented requirement, because joins and continuity become more complex. |

Proposed taxonomy: Marketing, ITR, GST, TDS, Registrations, Calculators, Resources, Account access, Client portal, Operations and Unmapped. Treat this as a design proposal until the route catalogue is verified. Put /services/returns/itr-filing-online under ITR; /services/returns/gstr under GST; /login under Account access. A portal page can belong to a legitimate customer. Identify staff using authorised role context separately.

Owner: analytics lead with engineering. Acceptance gate: selected representative URLs and journeys each produce the correct group, location and title on initial load, navigation, back/forward and reload; no duplicate page_view is introduced. If the application uses SPA navigation, follow one coordinated collection method and inspect route events [K2]. Test a public visitor, customer and staff role. Report unmapped coverage on the tested route set and after a full agreed monitoring window. A target such as at least 99% named groups is an internal proposal, not an industry benchmark.

Do not relabel all historic '(not set)' values in the source property, resend historic page views or assume collection changes rewrite past reports. An explicitly labelled historical path-derived report is the reviewable fallback. All configuration and production acceptance tests remain NOT RUN.

# 40. International feature research tied to the findings

Official product/help material checked 29 October 2026. These are selected market examples, not a representative survey, exhaustive world coverage or proof of superior conversion. A listed capability is documented by its vendor; commercial plan availability, Indian integrations and operational performance still require a trial. Country labels describe the service market/example, not exclusive availability or a country-level standard.

| Market / official reference | Documented pattern | Transferable solution and local validation |
| --- | --- | --- |
| India: Zoho CRM [K3, K4] | Assignment rules and criteria-based workflow tasks | Pilot ownership, next action and escalation in the existing CRM. Test every actual intake channel, retries, reassignment and unavailable owners. Six earlier unassigned leads justify checking, not declaring system failure. |
| US-market practice tools: TaxDome [K5] | Secure portal-based client document requests | Link each accepted case to a service checklist and missing-item queue. TaxPlan already has portal/vault activity; inspect existing completion before building another portal. |
| UK: TaxScouts [K6] | Accountant assignment and communication through the service platform/email | Make responsible expert and next step clear. Confirm existing expert workflows and measure accepted-case progress, not login counts. |
| Australia: Hnry [K7] | Invoicing and optional automatic invoice chasing | Use payment-linked reminders with stop-on-payment rules. Borrow the workflow; do not copy Australia's tax-remittance model into India. |
| Singapore: Sleek [K8] | Assigned accountant, accounting software access and digital document storage | Combine service ownership and documents with a customer-facing status view. Retain role-based access and existing secure storage. |
| Brazil: Conta Azul [K9] | Bank-to-ledger matching, including multiple and partial entries | Reconcile successful payments, partial receipts and refunds by stable IDs. Local bank connectors are not evidence of Indian compatibility. |
| Germany: Taxfix [K10] | Choice of software-led filing or independent expert assistance | Offer clear service scope and assisted escalation. Do not copy eligibility, prices, refund claims or tax rules across jurisdictions. |
| South Korea: Samjjeomsam [K11] | Free refund lookup within a paid filing service; individual outcomes vary | Test an eligibility/value explanation for riders, without guaranteeing refunds. Have an Indian tax expert validate any calculator and marketing statement. |

Research decision: prioritise structured ownership, verified receipts, service grouping and existing-portal completion. These patterns answer current evidence gaps more directly than native-app development, CRM migration or unvalidated AI scoring. No competitor documentation establishes how much uplift TaxPlan will obtain. Estimate total cost using implementation, seats, messaging/calls, payment charges, training and exportability; no unsupported cross-country price comparison is supplied.

# 41. Updated fix decisions, dependencies and research sources

| Issue / priority | Evidence strength and action | Validation required |
| --- | --- | --- |
| P0: commercial measurement | Repeated zero reported receipts plus older CRM wins. Retain lifecycle/payment reconciliation from sections 28-31. | Accepted case through payment/refund with ledger agreement; consent-aware attribution; no duplicate outcomes. |
| P1: service grouping | New direct evidence: 99.62% '(not set)' views and a blank remaining label. Add section 39 configuration work. | Correct groups on representative routes, no duplicate page views; measured unmapped coverage. |
| P1: form semantics | Starts increased in displayed snapshots; submits unchanged. Audit real lead-form acceptance and errors. | Valid, invalid and repeat attempts; distinguish account forms from enquiries. No completion rate from aggregate event ratios. |
| P1: shared portal and marketing reports | Repeated title/path and high-repeat portal activity. Segment actual roles and journeys. | Customer, consultant and staff tests; preserve legitimate customer activity. |
| P1: acquisition efficiency | Cohort counts changed; no new costs or paid outcomes. Retain targeted investigation of weak paid cohorts. | Same-creation-cohort spend, qualified cases, payment lag and contribution; capped experiment, no blanket budget increase. |
| P2: reporting health | +4 views/+6 events between captures; prior discrepancy register remains open. | Same dates/timezone/filters and completed processing; do not force integer totals to reconcile. |
| Conditional: retention and service features | Activity ratios reconcile; they do not measure annual repeat filing. Borrow checklist, ownership and payment-reminder patterns. | Completed case, paid renewal and service-specific follow-up; configure existing capabilities first. |

K1 - Google: create content groups. https://support.google.com/analytics/answer/11523339?hl=en

K2 - Google: measure single-page applications. https://developers.google.com/analytics/devguides/collection/ga4/single-page-applications

K3 - Zoho: assignment-rule FAQ. https://help.zoho.com/portal/en/kb/crm/faqs/automation/assignment-rules/articles/faqs-assignment

K4 - Zoho: workflow overview. https://www.zoho.com/crm/tutorials/workflow/overview.html

K5 - TaxDome: client requests. https://taxdome.com/client-requests

K6 - TaxScouts: services and assigned accountant. https://taxscouts.com/services/

K7 - Hnry: optional invoice chasing. https://hnry.com.au/resources/business/invoicing-made-easy-for-tradies/

K8 - Sleek Singapore: service inclusions. https://sleek.com/sg/all-services/

K9 - Conta Azul: reconciliation workflow options. https://ajuda.contaazul.com/hc/pt-br/sections/32422626419981-Fazer-concilia%C3%A7%C3%A3o-banc%C3%A1ria-completa

K10 - Taxfix: software and expert-service options. https://taxfix.de/kontakt/

K11 - Samjjeomsam: refund lookup and paid-service disclosure. https://app.3o3.co.kr/login

Research and documentary analysis updated 29 October. Recommendations cover the observed issue classes and practical alternatives; they are not a claim that every possible solution has been enumerated. Existing legal, tax-year and outreach review dependencies remain in force. No live change or acceptance test has been performed.
