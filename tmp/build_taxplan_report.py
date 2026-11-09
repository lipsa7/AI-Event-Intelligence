import sys, re, html
from pathlib import Path
sys.path.insert(0, str(Path('tmp/report-libs').resolve()))
from reportlab.pdfgen import canvas
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, KeepTogether, KeepInFrame
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.graphics.shapes import Drawing, Rect, String

root = Path.cwd()
src = root / 'reports/taxplan-performance-2026-10-28.md'
out = root / 'output/pdf'
out.mkdir(parents=True, exist_ok=True)
styles = getSampleStyleSheet()
styles.add(ParagraphStyle(name='BodyCustom', fontName='Helvetica', fontSize=9.2, leading=13.5, spaceAfter=8, textColor=colors.HexColor('#25364a')))
styles.add(ParagraphStyle(name='CellCustom', fontName='Helvetica', fontSize=8, leading=10.5, textColor=colors.HexColor('#25364a')))
styles.add(ParagraphStyle(name='SectionCustom', fontName='Helvetica-Bold', fontSize=22, leading=26, spaceAfter=18, textColor=colors.HexColor('#123754')))
styles.add(ParagraphStyle(name='SubCustom', fontName='Helvetica-Bold', fontSize=12, leading=16, spaceBefore=10, spaceAfter=8, textColor=colors.HexColor('#087e8b')))
styles.add(ParagraphStyle(name='BulletCustom', parent=styles['BodyCustom'], leftIndent=10, firstLineIndent=-7))

def markup(s):
    s = html.escape(s)
    return re.sub(r'(https://\S+)', lambda m: '<link href="'+m.group(1)+'" color="#087e8b">'+m.group(1)+'</link>', s)

def p(s, style='BodyCustom'):
    return Paragraph(markup(s), styles[style])

def chart():
    d=Drawing(490,105)
    vals=[('Organic Search',295),('Direct',212),('Paid Search',62),('Cross-network',57),('Paid Social',23)]
    d.add(String(0,94,'Engagement time per active user (seconds)',fontName='Helvetica-Bold',fontSize=10,fillColor=colors.HexColor('#123754')))
    for i,(name,val) in enumerate(vals):
        y=72-i*16
        d.add(String(0,y+3,name,fontSize=9,fillColor=colors.HexColor('#25364a')))
        d.add(Rect(112,y,val*0.95,11,fillColor=colors.HexColor('#087e8b') if i==0 else colors.HexColor('#5d90b5'),strokeColor=None))
        d.add(String(120+val*.95,y+3,str(val),fontSize=9,fillColor=colors.HexColor('#25364a')))
    return d

story=[]
lines=src.read_text(encoding='utf-8').splitlines()
i=0
while i<len(lines):
    line=lines[i].strip()
    if not line:
        i+=1;continue
    if line.startswith('# '):
        if story: story.append(PageBreak())
        story.append(p(line[2:],'SectionCustom'))
        if line.startswith('# 2.'): story.append(chart()); story.append(Spacer(1,8))
        i+=1;continue
    if line.startswith('## '):
        story.append(p(line[3:],'SubCustom'));i+=1;continue
    if line.startswith('|'):
        rows=[]
        while i<len(lines) and lines[i].strip().startswith('|'):
            cells=[s.strip() for s in lines[i].strip().strip('|').split('|')]
            if not all(re.fullmatch(r'[-:]+', s) for s in cells): rows.append([p(s,'CellCustom') for s in cells])
            i+=1
        n=len(rows[0]); w=499.27
        if n==3: widths=[w*.25,w*.34,w*.41]
        elif n==5: widths=[w*.32,w*.18,w*.16,w*.18,w*.16]
        elif n==4: widths=[w*.24,w*.25,w*.28,w*.23]
        else: widths=[w*.31,w*.69]
        t=Table(rows,colWidths=widths,repeatRows=1,hAlign='LEFT')
        t.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),colors.HexColor('#dcebf1')),('VALIGN',(0,0),(-1,-1),'TOP'),('ROWBACKGROUNDS',(0,1),(-1,-1),[colors.HexColor('#f3f7fa'),colors.white]),('BOTTOMPADDING',(0,0),(-1,-1),7),('TOPPADDING',(0,0),(-1,-1),7),('LEFTPADDING',(0,0),(-1,-1),8),('RIGHTPADDING',(0,0),(-1,-1),8),('LINEBELOW',(0,0),(-1,0),.7,colors.HexColor('#087e8b'))]))
        t.setStyle(TableStyle([('BOTTOMPADDING',(0,0),(-1,-1),5),('TOPPADDING',(0,0),(-1,-1),5)]))
        story.extend([t,Spacer(1,12)]);continue
    if line.startswith('- '): story.append(p('- '+line[2:],'BulletCustom'))
    else: story.append(p(line))
    i+=1

def footer(c,doc):
    c.saveState()
    c.setStrokeColor(colors.HexColor('#cbd9e3'));c.line(48,45,547,45)
    c.setFont('Helvetica',8);c.setFillColor(colors.HexColor('#536879'))
    c.drawString(48,31,'TAXPLAN ADVISOR  |  Performance & improvement  |  29 Oct 2026')
    c.drawRightString(547,31,str(doc.page))
    c.restoreState()

target=out/'TaxPlan-Advisor-Performance-and-Action-Report.pdf'
doc=SimpleDocTemplate(str(target),pagesize=A4,rightMargin=48,leftMargin=48,topMargin=44,bottomMargin=60,title='TaxPlan Advisor - Performance and Action Report',author='Business performance review')
section_groups=[]
section=[]
for flowable in story:
    if isinstance(flowable,PageBreak):
        section_groups.append(section); section=[]
    else: section.append(flowable)
if section: section_groups.append(section)
paginated=[]
for idx,section in enumerate(section_groups):
    if idx: paginated.append(PageBreak())
    paginated.append(KeepInFrame(499.27,720,section,mode='shrink',hAlign='LEFT',vAlign='TOP'))
doc.build(paginated,onFirstPage=footer,onLaterPages=footer)

import pymupdf
pdf=pymupdf.open(target)
qa=root/'tmp/pdfs';qa.mkdir(parents=True,exist_ok=True)
for num,page in enumerate(pdf):
    page.get_pixmap(matrix=pymupdf.Matrix(1.15,1.15)).save(str(qa/f'page-{num+1:02}.png'))
    for block in page.get_text('blocks'):
        if block[1]<0 or block[3]>842: raise RuntimeError('Text outside page')
print('PDF pages:',len(pdf))
for num,page in enumerate(pdf):
    text=page.get_text();print(num+1,len(text),text[:85].replace('\n',' '))
from PIL import Image, ImageOps, ImageDraw
thumbs=[]
for num in range(len(pdf)):
    im=Image.open(qa/f'page-{num+1:02}.png').convert('RGB');im.thumbnail((298,421))
    tile=Image.new('RGB',(318,450),'#d6dfe7');tile.paste(im,((318-im.width)//2,8));ImageDraw.Draw(tile).text((12,430),f'Page {num+1}',fill='black');thumbs.append(tile)
cols=3;rows=(len(thumbs)+cols-1)//cols
sheet=Image.new('RGB',(cols*318,rows*450),'white')
for idx,im in enumerate(thumbs):sheet.paste(im,((idx%cols)*318,(idx//cols)*450))
sheet.save(qa/'contact-sheet.png')
assert sum([4042,1596,1182,1018,682,279,51,36,28,20])==8934
print('Report and arithmetic checks complete:',target)
