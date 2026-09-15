import sys, json, re, os, traceback
import fitz
import pytesseract
from PIL import Image, ImageOps, ImageFilter

PDF = sys.argv[1]

# Marker text printed on voter cards. It is NEVER used as a serial/part identifier.
MARK_RE = re.compile(r'(?:\b\d{1,4}\s*/\s*\d{1,4}\s*/\s*\d{1,6}\b)', re.I)
WS_RE = re.compile(r'\bws\s*[-:]?\s*\d{2,8}\b', re.I)
EPIC_RE = re.compile(r'\b[A-Z]{2,5}[0-9]{6,10}\b', re.I)
SERIAL_RE = re.compile(r'^\s*(\d{1,5})\s*$')
REL_RE = re.compile(r'^(ਪਤੀ|ਪਿਤਾ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|ਮਾਂ|ਪਿਤਾ/ਪਤੀ|father|husband|wife|son|daughter|mother)\s*[:：-]?$', re.I)


def clean(s):
    return re.sub(r'\s+', ' ', str(s or '')).strip()


def lines(s):
    return [clean(x) for x in str(s or '').splitlines() if clean(x)]


def is_marker(s):
    return bool(MARK_RE.search(clean(s)))


def is_noise(s):
    s = clean(s)
    if not s: return True
    if MARK_RE.search(s) or WS_RE.search(s) or EPIC_RE.fullmatch(s): return True
    if SERIAL_RE.fullmatch(s): return True
    if re.search(r'(ਨਾਮ|name|मकान|house|ਉਮਰ|age|ਲਿੰਗ|gender|ਪਤੀ|ਪਿਤਾ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|father|husband|wife|son|daughter|mother)', s, re.I):
        return True
    return False


def part_no(doc):
    # Part/Booth is a document-level identifier. Never derive it from voter markers.
    sample = ''
    for i in range(min(3, len(doc))):
        sample += '\n' + doc[i].get_text('text')
    m = re.search(r'(?:Booth|ਬੂਥ|ਬਥ|बूथ)\s*(?:No\.?|ਨੰ\.?|ਨੰਬਰ|नं\.?|Number)?\s*[:\-]?\s*(\d{1,5})', sample, re.I)
    if m: return m.group(1)
    m = re.search(r'(?:Part\s*(?:No\.?|Number)?|ਪਾਰਟ\s*(?:ਨੰ\.?|ਨੰਬਰ)?)\s*[:\-]?\s*(\d{1,5})', sample, re.I)
    if m: return m.group(1)
    m = re.search(r'booth[_\s-]*(\d{1,5})', os.path.basename(PDF), re.I)
    return m.group(1) if m else ''


def text_fields(blocks, x0, y0):
    # Existing PDFs have a compact voter card approximately 175x70 points.
    card=[b for b in blocks if b[0] >= x0-6 and b[0] <= x0+180 and b[1] >= y0-4 and b[1] <= y0+78]
    epic=''; house=''; age=None; gender=''; rel=''
    for b in card:
        bx,by,bx1,by1,t=b[:5]; s=clean(t)
        em=EPIC_RE.search(s.upper())
        if em: epic=em.group(0).upper()
        if x0+18 <= bx <= x0+115 and y0+38 <= by <= y0+63 and s and not re.search(r'(ਨੰ|house|मकान|ਨਪਮ)',s,re.I):
            if not house: house=s
        if y0+50 <= by <= y0+78:
            m=re.search(r'(?<!\d)(\d{1,3})(?!\d)',s)
            if m and age is None: age=int(m.group(1))
            if re.search(r'(ਮਰਦ|ਔਰਤ|ਪੁਰਸ਼|ਪੁਰਸ਼|ਇਸਤਰੀ|female|male|other|ਲਿੰਗ|gender)',s,re.I): gender=s
        if x0-4 <= bx <= x0+32 and y0+20 <= by <= y0+48 and not is_noise(s): rel=s
    return epic,house,age,gender,rel


def ocr_image(img):
    # Punjabi+English first; fall back to English if the Punjabi traineddata is absent.
    cfg='--oem 1 --psm 11'
    try:
        return pytesseract.image_to_string(img, lang='pan+eng', config=cfg)
    except Exception:
        return pytesseract.image_to_string(img, lang='eng', config=cfg)


def ocr_card(page, x0, y0, scale=2.5):
    r=fitz.Rect(max(0,x0-2),max(0,y0-2),min(page.rect.width,x0+180),min(page.rect.height,y0+78))
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale), clip=r, alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    # Upscale/contrast lightly; don't alter the stored original photo.
    g=ImageOps.grayscale(img)
    g=ImageOps.autocontrast(g)
    txt=ocr_image(g)
    ls=lines(txt)
    voter=''; relative=''; relation=''
    for i,l in enumerate(ls):
        if re.fullmatch(r'(ਨਾਮ|Name|नाम)\s*[:：-]?',l,re.I) and i+1<len(ls):
            voter=ls[i+1]
        if REL_RE.match(l):
            relation=l
            if i+1<len(ls) and not is_noise(ls[i+1]): relative=ls[i+1]
            if not voter:
                prev=[x for x in ls[:i] if not is_noise(x)]
                if prev: voter=prev[-1]
            break
    if not voter:
        cand=[l for l in ls if not is_noise(l) and len(l)>=2]
        # Prefer Punjabi-containing candidates; otherwise first plausible line.
        pan=[l for l in cand if re.search(r'[\u0A00-\u0A7F]',l)]
        voter=(pan[0] if pan else (cand[0] if cand else ''))
    if not relative:
        # Common card order: name, relation label, relative name.
        for i,l in enumerate(ls):
            if REL_RE.match(l) and i+1<len(ls) and not is_noise(ls[i+1]):
                relation=l; relative=ls[i+1]; break
    return clean(voter),clean(relative),clean(relation),clean(txt)


def embedded_rows(page, pno, part):
    blocks=page.get_text('blocks')
    rows=[]
    # First try exact historical card blocks, then broad marker blocks.
    for b in blocks:
        text=clean(b[4])
        if not is_marker(text): continue
        # The actual serial is the standalone integer associated with this marker block.
        ls=lines(b[4]); serial=None
        for i,l in enumerate(ls):
            if SERIAL_RE.fullmatch(l) and not re.fullmatch(r'\d{1,4}\s*/\s*\d{1,4}\s*/\s*\d{1,6}',l):
                serial=int(l); break
        if serial is None:
            # Search nearby blocks for a small standalone integer, but never marker fragments.
            near=sorted((abs(c[1]-b[1])+abs(c[0]-b[0]),c) for c in blocks if c is not b and abs(c[0]-b[0])<35 and abs(c[1]-b[1])<30)
            for _,c in near:
                if SERIAL_RE.fullmatch(clean(c[4])):
                    serial=int(clean(c[4])); break
        if serial is None: continue
        x0,y0=b[0],b[1]
        epic,house,age,gender,rel=text_fields(blocks,x0,y0)
        name,father,ocrrel,raw=ocr_card(page,x0,y0)
        rows.append({'serial_no':str(serial),'name':name,'father_husband':father,'relation':ocrrel or rel,'epic':epic,'age':age,'gender':gender,'house_no':house,'part_no':part,'page_no':pno,'photo_key':f'{pno}:{x0}:{y0}','raw_text':raw})
    return rows


def scanned_candidates(page, pno):
    # OCR the whole page to find marker locations and serial numbers spatially.
    pix=page.get_pixmap(matrix=fitz.Matrix(2.0,2.0), alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    data=pytesseract.image_to_data(img, lang='pan+eng', config='--oem 1 --psm 11', output_type=pytesseract.Output.DICT)
    words=[]
    n=len(data.get('text',[]))
    for i in range(n):
        t=clean(data['text'][i])
        if not t: continue
        try: conf=float(data['conf'][i])
        except: conf=0
        x,y,w,h=[int(data[k][i]) for k in ('left','top','width','height')]
        words.append({'t':t,'x':x,'y':y,'w':w,'h':h,'conf':conf})
    # Group by approximate OCR line.
    lines_by=[]
    for w in sorted(words,key=lambda z:(z['y'],z['x'])):
        hit=None
        for ln in lines_by:
            if abs(w['y']-ln['y']) <= max(8,w['h']): hit=ln; break
        if hit: hit['words'].append(w); hit['y']=min(hit['y'],w['y'])
        else: lines_by.append({'y':w['y'],'words':[w]})
    candidates=[]
    for ln in lines_by:
        text=' '.join(w['t'] for w in sorted(ln['words'],key=lambda z:z['x']))
        if not is_marker(text):
            # Marker may be split into multiple OCR tokens; test concatenated text too.
            joined=''.join(w['t'] for w in sorted(ln['words'],key=lambda z:z['x']))
            if not is_marker(joined): continue
        ws=[w for w in ln['words']]
        x=min(w['x'] for w in ws)/2.0; y=min(w['y'] for w in ws)/2.0
        # Find standalone integer immediately before/after marker on same/nearby lines.
        serial=None; best=None
        for w in words:
            if not SERIAL_RE.fullmatch(w['t']): continue
            sx=w['x']/2.0; sy=w['y']/2.0
            dist=abs(sy-y)+0.25*abs(sx-x)
            if dist < 45 and dist>0 and (best is None or dist<best):
                serial=int(w['t']); best=dist
        if serial is not None:
            candidates.append((x,y,serial))
    # Deduplicate coordinates/serials.
    out=[]; seen=set()
    for x,y,s in sorted(candidates,key=lambda z:(z[1],z[0])):
        key=(s,round(x/10),round(y/10))
        if key in seen: continue
        seen.add(key); out.append((x,y,s))
    return out


def scanned_rows(page,pno,part):
    rows=[]
    try: candidates=scanned_candidates(page,pno)
    except Exception: candidates=[]
    for x,y,serial in candidates:
        # Convert image-derived coordinates to PDF points; candidate x/y already in PDF points.
        name,father,relation,raw=ocr_card(page,x,y,scale=2.8)
        # If OCR card returned only metadata, still preserve a record rather than silently losing serials.
        rows.append({'serial_no':str(serial),'name':name,'father_husband':father,'relation':relation,'epic':'','age':None,'gender':'','house_no':'','part_no':part,'page_no':pno,'photo_key':f'{pno}:{x}:{y}','raw_text':raw})
    return rows


def main():
    doc=fitz.open(PDF)
    part=part_no(doc)
    rows=[]
    # First pass: embedded text. If a page has no usable card markers, use OCR fallback.
    for pno in range(1,len(doc)+1):
        page=doc[pno-1]
        er=embedded_rows(page,pno,part)
        if er:
            rows.extend(er)
        else:
            # Don't OCR a cover/header page unless it contains a marker-like signal.
            txt=page.get_text('text')
            if not txt.strip() or is_marker(txt):
                rows.extend(scanned_rows(page,pno,part))
    # One row per actual boxed serial, sorted numerically. Marker strings never enter this key.
    unique={}
    for r in rows:
        s=clean(r.get('serial_no'))
        if SERIAL_RE.fullmatch(s):
            unique.setdefault(str(int(s)),r)
    rows=[unique[k] for k in sorted(unique,key=lambda z:int(z))]
    print(json.dumps({'ok':True,'part_no':part,'pages':len(doc),'rows':rows,'warnings':([] if rows else ['No voter card serials were detected. The PDF may use an unsupported scan/layout.'])},ensure_ascii=False))


if __name__=='__main__':
    try:
        main()
    except Exception as e:
        print(json.dumps({'ok':False,'error':str(e),'traceback':traceback.format_exc()[-3000:]},ensure_ascii=False))
        sys.exit(1)
