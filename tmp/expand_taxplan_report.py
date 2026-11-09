from pathlib import Path
p=Path('reports/taxplan-performance-2026-10-28.md')
s=p.read_text(encoding='utf-8')
addition=Path('reports/taxplan-international-research-2026-10-28.md').read_text(encoding='utf-8')
s=s.split('\n# 12. International benchmark:')[0].rstrip()+'\n\n'+addition
new_evidence=Path('reports/taxplan-additional-evidence-2026-10-28.md').read_text(encoding='utf-8')
s=s.rstrip()+'\n\n'+new_evidence
p.write_text(s,encoding='utf-8')
sessions=[7471,3173,1750,1289,1122,525,448,206,56,52]
engaged=[5231,2143,1166,551,751,396,322,5,35,28]
assert sum(sessions)==16092
assert sum(engaged)==10628
assert 16136-sum(sessions)==44
assert 10612-sum(engaged)==-16
assert sum([6588,6043,2767,625,49,37])==16109
assert sum([4745,3572,1873,415,0,26])==10631
assert sum([39,17,11,4,1,1])==73
assert sum([19,7,5,4,2,1,1])==39
assert sum([6623,2065,27,9])==8724
assert sum([12,48,316])==376
assert s.count('# 20. Lead acquisition:')==1
print('Expanded report merged; session arithmetic verified.')
