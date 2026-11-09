from pathlib import Path
import json

p=Path('reports/taxplan-performance-2026-10-28.md')
s=p.read_text(encoding='utf-8').split('\n# 28. Cross-screenshot review:')[0]
s=s.replace('Updated 28 October 2026 | 176 distinct screenshot files reviewed | Seven-market benchmark and implementation alternatives','Updated 28 October 2026 | 176 original files revisited | Seven-market benchmark, cross-check and conditional execution gates')
s=s.replace('Repair measurement and investigate weak paid traffic before increasing acquisition budgets.','Reconcile commercial outcomes and investigate weak paid cohorts before increasing acquisition budgets. Sections 28-31 record the second-pass cross-check and govern the confidence and execution conditions of all recommendations.')
s=s.replace('| P1: Operational funnel gaps | Six unassigned; no visible disqualification reasons. | Owner, next action, timestamps, loss reason and overdue escalation. |','| P1: Operational funnel check | Six marked unassigned; no visible disqualification reasons. Main bottleneck unproved. | Inspect ownership and ageing first; configure next action, timestamps, reasons and escalation where needed. |')
s=s.replace('| P0: Commerce absent |','| P0: Commercial outcomes unrecorded |')
s=s.replace('| P1: Missing sessions/pages |','| P1: Missing attribution/page fields |')
s=s.replace('| Abandonment on mobile |','| Possible mobile journey friction |')
s=s.rstrip()+'\n\n'+Path('reports/taxplan-reconciled-decisions-2026-10-28.md').read_text(encoding='utf-8')
p.write_text(s,encoding='utf-8')
cross=Path('reports/taxplan-cross-check-2026-10-28.md')
t=cross.read_text(encoding='utf-8')
t=t.replace("This pass checks the report's conclusions against the supplied screenshot evidence and competing report views. It is not an independent reinspection of every original image or an exhaustive comparison of every possible image pair. No underlying account, export, code, payment or implementation was tested. The report's 176-file coverage ledger is distinct from proof that every proposed root cause is correct.","Updated after the second pass: all 176 original files were located, hashed and visually revisited using 22 labelled review sheets; critical lead and event tables were also reopened individually. Four files concern unrelated sidebar/disk interfaces. Comparisons were grouped by metric, period, attribution scope and proposed action, rather than comparing every unrelated pair. No underlying account, export, code, payment or implementation was tested. Sections 28-31 of the main report contain the final decisions, discrepancies and NOT RUN execution gates. Original-file coverage is distinct from proof that every proposed root cause is correct.")
cross.write_text(t,encoding='utf-8')
rows=json.loads(Path('tmp/taxplan-audit/manifest.json').read_text())
def classify(i):
 if i in [1,2,3,176]:return ('Unrelated interface / excluded','Scope only')
 if i<=10:return ('Short-period overview / comparison','1, 2, 10, 30')
 if i==11:return ('CRM / unmatched period','1, 5, 28, 31')
 if i<=24:return ('Overview, cohort, audience or dimension controls','1, 4, 25, 30')
 if i<=32:return ('Search, geography and selected metrics','4, 25, 29')
 if i<=46:return ('Manual source, medium, campaign, empty cards','3, 21, 28-30')
 if i<=60:return ('Audience and first-user channels / medium','1, 2, 14, 30')
 if i<=72:return ('First-user sources, platform, campaign, network, ad groups','14, 20, 30')
 if i<=80:return ('Session channel comparisons / anomalies','15, 21, 30')
 if i<=87:return ('Session source and platform comparisons','21, 30')
 if i<=102:return ('Lead acquisition and dimension comparisons','20, 28, 30')
 if i<=106:return ('Landing page versus later page activity','22, 29, 30')
 if i<=121:return ('Cohort revenue and value coverage','23, 28, 30')
 if i<=132:return ('Sales / ecommerce / funnel / device','23, 28, 29')
 if i<=144:return ('Country, city, engagement, event overview, language','24, 25, 29')
 if i<=160:return ('Page paths/titles, retention, events and anomalies','24, 25, 29, 30')
 if i<=165:return ('Exact event and page corroboration','24, 28-30')
 if i<=170:return ('Business Profile actions and optional menus','25, 29')
 if i<=172:return ('Filtered campaign-import validation','21, 28, 30, 31')
 return ('Acquisition overview / realtime / rounded summary','1, 2, 25, 30')
lines=['# TaxPlan screenshot inventory and review record','','28 October 2026. All 176 distinct referenced filenames exist locally. All were visually revisited through labelled review sheets; key tables were reopened at original size. Four unrelated files are excluded from business conclusions. No two listed files have the same SHA-256 content hash; different captures can still show overlapping data. A hash establishes file identity, not authenticity of the underlying report.','','| ID | Original filename | Review group | Related report sections | SHA-256 |','|---|---|---|---|---|']
for i,r in enumerate(rows,1):
 group,sections=classify(i)
 lines.append(f"| {i} | {r['file']} | {group} | {sections} | {r['sha256']} |")
Path('reports/taxplan-screenshot-inventory-2026-10-28.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
assert s.count('# 28. Cross-screenshot review:')==1
assert len(rows)==176 and all(r['exists'] for r in rows)
assert len({r['sha256'] for r in rows})==176
assert 39+17+11+4+1+1==73
assert 19+7+5+4+2+1+1==39
assert sum([7471,3173,1750,1289,1122,525,448,206,56,52])==16092
assert sum([5231,2143,1166,551,751,396,322,5,35,28])==10628
print('Cross-check merged; 176 files present, 172 reporting/control images, 4 excluded; arithmetic checks passed.')
