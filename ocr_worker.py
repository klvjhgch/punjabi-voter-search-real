import sys,json,re,os
import fitz,pytesseract
from PIL import Image
PDF=sys.argv[1]
MARK_RE=re.compile(r'^\s*\d+/\d+/\d+\s*\n\s*ws-\d+\s*\n\s*(\d+)\s*$',re.I)
EPIC_RE=re.compile(r'^[A-Z]{2,5}\d{6,10}$',re.I)
REL_WORDS=re.compile(r'^(ਪਤੀ|ਪਿਤਾ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|mother|father|husband|wife|son|daughter)$',re.I)

def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()
def lines(s): return [x.strip() for x in str(s or '').splitlines() if x.strip()]

def part_no(doc):
    for i in (0,1):
        if i>=len(doc): break
        t=doc[i].get_text('text')
        m=re.search(r'(?:Booth|ਬਥ|बूथ)\s*(?:No\.?|ਨਅ\.?|नं\.?)?\s*[:\-]?\s*(\d+)',t,re.I)
        if m:return m.group(1)
    m=re.search(r'booth[_\s-]*(\d+)',os.path.basename(PDF),re.I)
    return m.group(1) if m else ''

def text_fields(blocks,x0,y0):
    card=[b for b in blocks if b[0]>=x0-4 and b[0]<x0+170 and b[1]>=y0-2 and b[1]<=y0+70]
    epic=''; house=''; age=None; gender=''; rel=''
    for b in card:
        bx,by,bx1,by1,t=b[:5]; s=clean(t)
        if x0+105<=bx<x0+175 and y0-2<=by<=y0+16 and EPIC_RE.match(s): epic=s.upper()
        if x0+25<=bx<x0+100 and y0+41<=by<=y0+59 and s and 'ਨਪਮ' not in s: house=s
        if x0+38<=bx<x0+120 and y0+53<=by<=y0+70 and 'ਉਮਰ' not in s:
            ls=lines(t)
            if ls:
                m=re.search(r'\b(\d{1,3})\b',ls[0])
                if m: age=int(m.group(1))
                if len(ls)>1: gender=ls[1]
        if x0-1<=bx<x0+24 and y0+25<=by<=y0+44 and s not in ('ਨਪਮ :','ਮਪਪਨ ਨ.:','ਉਮਰ :'): rel=s
    return epic,house,age,gender,rel

def ocr_card(page,x0,y0):
    r=fitz.Rect(x0,y0,x0+175,y0+70)
    pix=page.get_pixmap(matrix=fitz.Matrix(2,2),clip=r,alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    try: txt=pytesseract.image_to_string(img,lang='pan+eng',config='--psm 11')
    except Exception: txt=pytesseract.image_to_string(img,lang='eng',config='--psm 11')
    ls=lines(txt)
    # strip obvious metadata, retaining Punjabi names.
    voter=''; relative=''; relation=''
    for i,l in enumerate(ls):
        if l in ('ਨਾਮ','Name','नाम','ਨਾਮ :','Name:') and i+1<len(ls): voter=ls[i+1]
        if REL_WORDS.match(l):
            relation=l
            if i+1<len(ls): relative=ls[i+1]
            if not voter:
                prev=[x for x in ls[:i] if not re.search(r'(16/\d+/\d+|ws-\d+|^[A-Z]{2,5}\d{6,10}$|^\d+$)',x,re.I) and x not in ('ਨਾਮ','Name','मकान नੰ.:','ਮਕਾਨ ਨੰ.:')]
                if prev: voter=prev[-1]
            break
    if not voter:
        # fallback: choose the first likely Punjabi text line after metadata
        cand=[l for l in ls if not re.search(r'(16/\d+/\d+|ws-\d+|^[A-Z]{2,5}\d{6,10}$|^\d+$|ਨਾਮ|ਮਕਾਨ|ਉਮਰ|ਲਿੰਗ)',l,re.I)]
        if cand: voter=cand[0]
    return clean(voter),clean(relative),clean(relation)

doc=fitz.open(PDF); part=part_no(doc); rows=[]
for pno in range(2,len(doc)+1):
    page=doc[pno-1]; blocks=page.get_text('blocks'); marks=[]
    for b in blocks:
        m=MARK_RE.match(b[4])
        if m: marks.append((b[0],b[1],int(m.group(1))))
    for x0,y0,serial in marks:
        epic,house,age,gender,rel=text_fields(blocks,x0,y0)
        name,father,ocrrel=ocr_card(page,x0,y0)
        relation=ocrrel or rel
        rows.append({'serial_no':str(serial),'name':name,'father_husband':father,'relation':relation,'epic':epic,'age':age,'gender':gender,'house_no':house,'part_no':part,'page_no':pno,'photo_key':f'{pno}:{x0}:{y0}','raw_text':f'PHOTO|{x0}|{y0}|{x0+175}'})
# Keep one record per actual box serial, never by 16/20/xxx.
u={}
for r in rows:u.setdefault(r['serial_no'],r)
rows=[u[k] for k in sorted(u,key=lambda z:int(z))]
print(json.dumps({'part_no':part,'pages':len(doc),'rows':rows},ensure_ascii=False))
