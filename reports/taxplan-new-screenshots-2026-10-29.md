# Additional screenshot review - 29 October 2026

36 new filenames checked against the previous 176-file inventory: 212 files exist; no identical SHA-256 file hashes. All 36 attachments visually reviewed in the conversation. Different file hashes do not prove different information. Exact-file checks are recorded in taxplan-image-hashes-2026-10-29.json. No files removed.

## Repeated content and useful overlaps

- 145922 and 150050 repeat the same engagement-per-active-user card: 2m 27s, engaged sessions per active user 1.2, realtime two users and the same visible page split. Different framing; redundant for numerical extraction.
- 150230 and 150344 repeat the same event-table lower rows. 150344 additionally includes scroll, gtm.dom and user_engagement above the repeated rows. Both overlap 150221; 150230 supplies form_submit beyond that capture's cropped bottom.
- 144656 and 144746 show the same visible channel values, but the dimension changes from first-user primary channel group to first-user default channel group. They are corroborating alternate views, not duplicate report dimensions.
- 144707 and 144756 repeat lower channel values, with the latter including Organic Social. Keep the relationship to the different channel dimensions.
- 144827/144836, 144954/145002, 150458/150719 and 150756/150806 are upper/lower table continuations with useful overlaps, not wholly redundant images.
- 145941 and 145952 share an overview and right-hand event card but select different left-hand chart metrics (Views versus Event count). Retain both.
- Graphs resembling 28 October captures are updated or alternate reporting views. Do not discard merely because the curves look similar.

## Evidence changes from 28 October

The report date range/filter header is cropped in these captures. Values below are differences between displayed snapshots, not verified daily increments or proof that an implemented fix worked.

| Metric | Earlier displayed value | New displayed value | Difference |
| --- | --- | --- | --- |
| Event-table total events | 197,221 | 197,882 | +661 |
| Event-table total users | 8,917 | 8,947 | +30 |
| page_view events | 71,971 | 72,235 | +264 |
| scroll events | 20,302 | 20,380 | +78 |
| user_engagement events | 18,109 | 18,278 | +169 |
| session_start events | 16,043 | 16,122 | +79 |
| first_visit events | 8,860 | 8,893 | +33 |
| form_start events / users | 7,056 / 1,313 | 7,077 / 1,324 | +21 / +11 |
| form_submit events / users | 6,688 / 508 | 6,688 / 508 | No displayed change |
| New users, acquisition cohorts | 8,859 | 8,893 | +34 |

gtm.dom remains 20,290; analytics 11,807; web_vitals 9,742. Unchanged cumulative values do not prove these events were disabled. All visible event revenue and acquisition-cohort revenue remain INR 0; transaction counts remain zero. This does not establish zero business receipts.

The path table (150458) shows 72,235 views and 197,882 events; the later title/content-group tables (150756/150837) show 72,239 views and 197,888 events. Keep the four-view/six-event difference in the discrepancy register; later capture time is a possible explanation, not confirmed causation. Both show 8,942 active users. Event-table total users (8,947) and page-report active users (8,942) are different metrics.

New users by platform (145257) sum exactly to 8,893: Unlabeled 3,509; Google Ads 2,495; Manual 2,300; Meta Ads 534; Other Ads 55. These are first-user platform groups, not channel totals or incremental paid leads. Paid Other's channel value is now 70 versus 51 in the older cohort table; flag the changed snapshot, not a campaign profitability conclusion.

## New content-group evidence

150837: of 72,239 views, 71,964 (99.62%) have '(not set)' content group. The other displayed group label is blank: 275 views, 55 active users, 691 events and 14 key events. Totals reconcile for views (71,964+275), events (197,197+691=197,888) and key events (4,156+14=4,170). User rows overlap: 8,909+55=8,964 versus 8,942 total active users. Do not add users across groups as distinct people.

The grouping is not useful for interpreting service families in this displayed report. This adds a concrete reporting-configuration priority to the existing segmentation recommendation: define explicit marketing/service/calculator/customer-portal groups, with staff identified by authorised role data rather than URL alone; use a separate explicit label for unknown/unmapped pages. Confirm mapping on initial and subsequent page views and validate the resulting report before expanding it. Screenshots do not reveal the current tag configuration or explain the blank label.

150006 activity card shows 637 users over 30 days, 117 over seven days and 14 over one day. Ratios are 14/637=2.20%, 14/117=11.97% and 117/637=18.37%, matching 150019's 2.2%, 12.0% and 18.4%. Earlier stickiness values used a different displayed snapshot; this change is not proof of improved customer renewal or retention.

The repeated anomaly cards still refer to 15 and 18 October. Reappearance on 29 October does not establish a fresh incident or validate the generated 'new login page launch' explanation. No lead, payment, campaign-cost or fix-acceptance results were supplied in these 36 images.

Status: findings integrated into sections 37-41 of the main report, with new international research, fix alternatives and acceptance gates. No live fixes applied.
