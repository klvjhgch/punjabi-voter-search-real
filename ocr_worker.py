import sys, json, re, os, traceback, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import fitz
import pytesseract
from PIL import Image, ImageOps

PDF = sys.argv[1]

MARK_RE = re.compile(r'(?:\b\d{1,4}\s*/\s*\d{1,4}\s*/\s*\d{1,6}\b)', re.I)
WS_RE = re.compile(r'\bws\s*[-:]?\s*\d{2,8}\b', re.I)
EPIC_RE = re.compile(r'\b[A-Z]{2,5}[0-9]{6,10}\b', re.I)
SERIAL_RE = re.compile(r'^\s*(\d{1,5})\s*$')
REL_RE = re.compile(r'^(ਪਤੀ|ਪਿਤਾ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|ਮਾਂ|ਪਿਤਾ/ਪਤੀ|father|husband|wife|son|daughter|mother)\s*[:：-]?$', re.I)
LABEL_RE = re.compile(r'(ਨਾਮ|name|ਮਕਾਨ|house|ਉਮਰ|age|ਲਿੰਗ|gender|ਪਤੀ|ਪਿਤਾ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|father|husband|wife|son|daughter|mother)', re.I)

# Render/OCR can be tuned without changing code. Lower values are much faster on large rolls.
PAGE_SCALE = float(os.getenv('OCR_PAGE_SCALE', '1.45'))
CARD_SCALE = float(os.getenv('OCR_CARD_SCALE', '2.2'))
MAX_CARD_OCR = int(os.getenv('OCR_MAX_CARD_OCR', '2500'))
MAX_PARALLEL = max(1, min(int(os.getenv('OCR_PARALLEL', '2')), 4))


def progress(pct, stage, page=None, pages=None):
    obj = {'pct': int(max(0, min(99, pct))), 'stage': stage}
    if page is not None: obj['page'] = int(page)
    if pages is not None: obj['pages'] = int(pages)
    print('PROGRESS:' + json.dumps(obj, ensure_ascii=False), file=sys.stderr, flush=True)


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
    if LABEL_RE.search(s): return True
    return False


def part_no(doc):
    # Part/Booth is a document-level identifier. Never derive it from voter markers.
    sample = '\n'.join(doc[i].get_text('text') for i in range(min(5, len(doc))))
    patterns = [
        r'(?:Booth|ਬੂਥ|ਬਥ|बूथ)\s*(?:No\.?|ਨੰ\.?|ਨੰਬਰ|नं\.?|Number)?\s*[:\-]?\s*(\d{1,5})',
        r'(?:Part\s*(?:No\.?|Number)?|ਪਾਰਟ\s*(?:ਨੰ\.?|ਨੰਬਰ)?)\s*[:\-]?\s*(\d{1,5})'
    ]
    for pat in patterns:
        m = re.search(pat, sample, re.I)
        if m: return m.group(1)
    m = re.search(r'booth[_\s-]*(\d{1,5})', os.path.basename(PDF), re.I)
    return m.group(1) if m else ''


def text_fields(blocks, x0, y0):
    card=[b for b in blocks if b[0] >= x0-8 and b[0] <= x0+185 and b[1] >= y0-6 and b[1] <= y0+82]
    epic=''; house=''; age=None; gender=''; rel=''; candidates=[]
    for b in card:
        bx,by,bx1,by1,t=b[:5]; s=clean(t)
        if not s: continue
        em=EPIC_RE.search(s.upper())
        if em: epic=em.group(0).upper()
        if x0+18 <= bx <= x0+120 and y0+35 <= by <= y0+65 and not re.search(r'(ਨੰ|house|मकान|ਨਪਮ)',s,re.I):
            if not house: house=s
        if y0+48 <= by <= y0+82:
            m=re.search(r'(?<!\d)(\d{1,3})(?!\d)',s)
            if m and age is None: age=int(m.group(1))
            if re.search(r'(ਮਰਦ|ਔਰਤ|ਪੁਰਸ਼|ਪੁਰਸ਼|ਇਸਤਰੀ|female|male|other|ਲਿੰਗ|gender)',s,re.I): gender=s
        if x0-4 <= bx <= x0+40 and y0+18 <= by <= y0+52 and not is_noise(s): rel=s
        if not is_noise(s) and len(s) >= 2 and not EPIC_RE.search(s.upper()): candidates.append(s)
    # Best non-label name candidate from the card's text layer.
    pan=[s for s in candidates if re.search(r'[\u0A00-\u0A7F]',s)]
    name=(pan[0] if pan else (candidates[0] if candidates else ''))
    return name, epic, house, age, gender, rel


def ocr_image(img, config='--oem 1 --psm 11'):
    try:
        return pytesseract.image_to_string(img, lang='pan+eng', config=config)
    except Exception:
        return pytesseract.image_to_string(img, lang='eng', config=config)


def ocr_card(page, x0, y0, scale=CARD_SCALE):
    r=fitz.Rect(max(0,x0-2),max(0,y0-2),min(page.rect.width,x0+180),min(page.rect.height,y0+78))
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale), clip=r, alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    g=ImageOps.autocontrast(ImageOps.grayscale(img))
    txt=ocr_image(g)
    ls=lines(txt)
    voter=''; relative=''; relation=''
    for i,l in enumerate(ls):
        if re.fullmatch(r'(ਨਾਮ|Name|नाम)\s*[:：-]?',l,re.I) and i+1<len(ls): voter=ls[i+1]
        if REL_RE.match(l):
            relation=l
            if i+1<len(ls) and not is_noise(ls[i+1]): relative=ls[i+1]
            if not voter:
                prev=[x for x in ls[:i] if not is_noise(x)]
                if prev: voter=prev[-1]
            break
    if not voter:
        cand=[l for l in ls if not is_noise(l) and len(l)>=2]
        pan=[l for l in cand if re.search(r'[\u0A00-\u0A7F]',l)]
        voter=pan[0] if pan else (cand[0] if cand else '')
    if not relative:
        for i,l in enumerate(ls):
            if REL_RE.match(l) and i+1<len(ls) and not is_noise(ls[i+1]):
                relation=l; relative=ls[i+1]; break
    return clean(voter),clean(relative),clean(relation),clean(txt)


def embedded_rows(page, pno, part):
    blocks=page.get_text('blocks')
    rows=[]
    marker_blocks=[b for b in blocks if is_marker(b[4])]
    for b in marker_blocks:
        text=clean(b[4]); ls=lines(b[4]); serial=None
        for l in ls:
            if SERIAL_RE.fullmatch(l) and not MARK_RE.fullmatch(l): serial=int(l); break
        if serial is None:
            near=sorted((abs(c[1]-b[1])+abs(c[0]-b[0]),c) for c in blocks if c is not b and abs(c[0]-b[0])<40 and abs(c[1]-b[1])<34)
            for _,c in near:
                val=clean(c[4])
                if SERIAL_RE.fullmatch(val): serial=int(val); break
        if serial is None: continue
        x0,y0=b[0],b[1]
        name0,epic,house,age,gender,rel=text_fields(blocks,x0,y0)
        # Embedded PDFs should not invoke Tesseract for every voter. OCR only when the text layer
        # failed to provide a plausible name/relative, which is the expensive path.
        if name0 and (re.search(r'[\u0A00-\u0A7F]', name0) or len(name0) >= 3):
            name,father,ocrrel,raw=name0,rel,rel,text
        else:
            name,father,ocrrel,raw=ocr_card(page,x0,y0)
        rows.append({'serial_no':str(serial),'name':name,'father_husband':father,'relation':ocrrel or rel,'epic':epic,'age':age,'gender':gender,'house_no':house,'part_no':part,'page_no':pno,'photo_key':f'{pno}:{x0}:{y0}','raw_text':raw})
    return rows


def scanned_candidates(page):
    # One OCR pass per page. A modest DPI is intentional: it cuts RAM/CPU substantially on Render.
    pix=page.get_pixmap(matrix=fitz.Matrix(PAGE_SCALE,PAGE_SCALE), alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    data=pytesseract.image_to_data(img, lang='pan+eng', config='--oem 1 --psm 11', output_type=pytesseract.Output.DICT)
    words=[]; n=len(data.get('text',[]))
    for i in range(n):
        t=clean(data['text'][i])
        if not t: continue
        try: conf=float(data['conf'][i])
        except: conf=0
        x,y,w,h=[int(data[k][i]) for k in ('left','top','width','height')]
        words.append({'t':t,'x':x,'y':y,'w':w,'h':h,'conf':conf})
    lines_by=[]
    for w in sorted(words,key=lambda z:(z['y'],z['x'])):
        hit=None
        for ln in lines_by:
            if abs(w['y']-ln['y']) <= max(8,w['h']): hit=ln; break
        if hit: hit['words'].append(w); hit['y']=min(hit['y'],w['y'])
        else: lines_by.append({'y':w['y'],'words':[w]})
    candidates=[]
    for ln in lines_by:
        ws=sorted(ln['words'],key=lambda z:z['x'])
        text=' '.join(w['t'] for w in ws); joined=''.join(w['t'] for w in ws)
        if not (is_marker(text) or is_marker(joined)): continue
        x=min(w['x'] for w in ws)/PAGE_SCALE; y=min(w['y'] for w in ws)/PAGE_SCALE
        serial=None; best=None
        for w in words:
            if not SERIAL_RE.fullmatch(w['t']): continue
            sx=w['x']/PAGE_SCALE; sy=w['y']/PAGE_SCALE
            dist=abs(sy-y)+0.25*abs(sx-x)
            if 0 < dist < 55 and (best is None or dist<best): serial=int(w['t']); best=dist
        if serial is not None: candidates.append((x,y,serial))
    out=[]; seen=set()
    for x,y,s in sorted(candidates,key=lambda z:(z[1],z[0])):
        key=(s,round(x/12),round(y/12))
        if key in seen: continue
        seen.add(key); out.append((x,y,s))
    return out


def scanned_row(page,pno,part,item):
    x,y,serial=item
    name,father,relation,raw=ocr_card(page,x,y)
    return {'serial_no':str(serial),'name':name,'father_husband':father,'relation':relation,'epic':'','age':None,'gender':'','house_no':'','part_no':part,'page_no':pno,'photo_key':f'{pno}:{x}:{y}','raw_text':raw}


def main():
    t0=time.time(); doc=fitz.open(PDF); total=len(doc); part=part_no(doc); rows=[]
    progress(2,'Opening PDF',0,total)
    for idx in range(total):
        pno=idx+1; page=doc[idx]
        text=page.get_text('text')
        er=embedded_rows(page,pno,part)
        if er:
            rows.extend(er)
            progress(10 + int(78*(pno/total)), 'Reading embedded text', pno, total)
            continue
        # OCR only pages that actually need it. Cover/header pages are skipped when there is no
        # marker signal; image-only voter pages are OCR'd once, not once for every text block.
        signal=bool(text.strip()) and is_marker(text)
        if not text.strip() or signal:
            try:
                candidates=scanned_candidates(page)
            except Exception as e:
                candidates=[]
                print('OCR_PAGE_ERROR:'+str(e), file=sys.stderr, flush=True)
            if candidates:
                # Keep parallelism conservative; Tesseract is CPU-heavy.
                if len(candidates) <= MAX_CARD_OCR:
                    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
                        futs=[ex.submit(scanned_row,page,pno,part,c) for c in candidates]
                        for fut in as_completed(futs): rows.append(fut.result())
                else:
                    print(f'OCR_CARD_LIMIT: page {pno} has {len(candidates)} candidates; limiting to {MAX_CARD_OCR}', file=sys.stderr, flush=True)
                    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as ex:
                        futs=[ex.submit(scanned_row,page,pno,part,c) for c in candidates[:MAX_CARD_OCR]]
                        for fut in as_completed(futs): rows.append(fut.result())
        progress(10 + int(78*(pno/total)), 'OCR / text extraction', pno, total)
    doc.close()
    unique={}
    for r in rows:
        s=clean(r.get('serial_no'))
        if SERIAL_RE.fullmatch(s): unique.setdefault(str(int(s)),r)
    rows=[unique[k] for k in sorted(unique,key=lambda z:int(z))]
    progress(95,'Data extraction',total,total)
    print(json.dumps({'ok':True,'part_no':part,'pages':total,'rows':rows,'warnings':([] if rows else ['No voter card serials were detected. The PDF may use an unsupported scan/layout.']),'elapsed_seconds':round(time.time()-t0,1)},ensure_ascii=False))


if __name__=='__main__':
    try: main()
    except Exception as e:
        print(json.dumps({'ok':False,'error':str(e),'traceback':traceback.format_exc()[-3000:]},ensure_ascii=False))
        sys.exit(1)
