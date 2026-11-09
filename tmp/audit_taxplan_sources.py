from pathlib import Path
import json, hashlib, sys
sys.path.insert(0, str(Path('tmp/report-libs').resolve()))
from PIL import Image, ImageDraw

groups = [
('2026-10-22','161909'),('2026-10-25','160500 164257'),
('2026-10-26','185235 185326 185339 185350 185403 193439 194025'),
('2026-10-27','101535 154813 154905 154945 154957 155107 155131 155253 155307 155339 155358 155410'),
('2026-10-28','091611 091628 091835 091922 092143 092229 092327 092443 093120 093142 093250 093308 093331 093347 093409 093808 093828 093841'),
('2026-10-28','093858 093909 093947 094001 094018 094129 094235 094312 094359 094453 094530 094552 095017 095039 095109 095121 095132 095211 095229 095411'),
('2026-10-28','095837 095851 095902 095928 095941 100111 100126 100135 100204 100226 100810 100821 100853 100912 100950 101024 101505 101519 101530 101540'),
('2026-10-28','101600 101618 101631 101642 101704 101715 101726 103305 103323 103342 103401 103418 103438 103458 103510 103544 103602 103612 103719 103728'),
('2026-10-28','103752 103847 103956 104011 104023 104033 104243 104305 104315 104342 104355 104403 104422 105048 105116 105127 105152 105211 105231 105301'),
('2026-10-28','105312 105548 105603 105613 105626 105704 105723 105806 105820 105852 105903 105919 112130 112145 112156 112210 112228 112242 112251 112306'),
('2026-10-28','112316 112331 112342 112356 112457 112508 112523 112533 112544 112620 112631 112641 112727 112835 112853 112904 112913 112942 113004 113021'),
('2026-10-28','113042 113058 113138 113153 113204 113230 113242 113252 113314 113338 113415 113427 113511 113525 113541 122855')]
base=Path('C:/Users/DELL/Pictures/Screenshots')
out=Path('tmp/taxplan-audit');out.mkdir(exist_ok=True)
rows=[]
for date,times in groups:
 for time in times.split():
  p=base/f'Screenshot {date} {time}.png'
  row={'file':p.name,'path':str(p),'exists':p.exists()}
  if p.exists():
   im=Image.open(p);row.update(width=im.width,height=im.height,sha256=hashlib.sha256(p.read_bytes()).hexdigest())
  rows.append(row)
assert len(rows)==176 and len({x['file'] for x in rows})==176
(out/'manifest.json').write_text(json.dumps(rows,indent=2),encoding='utf-8')
for start in range(0,len(rows),8):
 sheet=Image.new('RGB',(1600,1680),'#d6dfe7')
 for i,row in enumerate(rows[start:start+8]):
  if not row['exists']:continue
  im=Image.open(row['path']).convert('RGB');im.thumbnail((780,385))
  x=(i%2)*800;y=(i//2)*420
  sheet.paste(im,(x+10,y+25))
  ImageDraw.Draw(sheet).text((x+10,y+7),f"{start+i+1}: {row['file']}",fill='black')
 sheet.save(out/f'sheet-{start//8+1:02}.png')
print(json.dumps({'manifest_files':len(rows),'available':sum(x['exists'] for x in rows),'missing':[x['file'] for x in rows if not x['exists']],'sheets':22}))
