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
EMBED_OCR_SCALE = float(os.getenv('OCR_EMBED_SCALE', '3.0'))


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
    # This roll prints: "ਵਾਰਡ ਨੰ.: 77  ਬੂਥ ਨੰ.: 3". The Booth Number is the Part Number.
    # Never use 16/20/xxx, ws-xxxx, or the Ward Number as the Part Number.
    sample='\n'.join(doc[i].get_text('text') for i in range(min(3,len(doc))))
    patterns=[
        r'(?:Booth|ਬੂਥ|ਬਬਥ|ਬਥ)\s*(?:No\.?|ਨੰ\.?|ਨੰਬਰ|ਨਅ\.?|Number)?\s*[:\-]?\s*(\d{1,5})',
        r'\bOB\s*[:\-]?\s*(\d{1,5})\b',
    ]
    for pat in patterns:
        m=re.search(pat,sample,re.I)
        if m:return m.group(1)
    name=os.path.basename(PDF)
    m=re.search(r'(?:booth|bth|ob)[_\s-]*(\d{1,5})',name,re.I)
    return m.group(1) if m else ''


def normalize_gender(s):
    s=clean(s)
    if not s:return ''
    if re.search(r'(?:ਪੁਰਸ਼|ਪੁਰਸ਼|ਪਪਰਸ਼|ਪਰਸ਼|male|ਮਰਦ)',s,re.I):return 'ਪੁਰਸ਼'
    if re.search(r'(?:ਇਸਤਰੀ|ਇਸਤ੍ਰੀ|ਇਸਤਰਤ|female|ਔਰਤ)',s,re.I):return 'ਇਸਤਰੀ'
    if re.search(r'(?:ਹੋਰ|ਅਦਰ|other)',s,re.I):return 'ਹੋਰ'
    return s


def normalize_relation(s):
    s=clean(s)
    if not s:return ''
    # OCR/text-layer variants seen in this electoral-roll layout.
    if re.search(r'(?:ਪਿਤਾ|ਤਪਤਪ|father)',s,re.I):return 'ਪਿਤਾ'
    if re.search(r'(?:ਪਤੀ|ਪਤਤ|husband)',s,re.I):return 'ਪਤੀ'
    if re.search(r'(?:ਅਦਰ|ਹੋਰ|other)',s,re.I):return 'ਹੋਰ'
    if re.search(r'(?:ਮਾਤਾ|mother)',s,re.I):return 'ਮਾਤਾ'
    if re.search(r'(?:ਪੁੱਤਰ|ਪੁਤਰ|son)',s,re.I):return 'ਪੁੱਤਰ'
    if re.search(r'(?:ਪੁਤਰੀ|ਪੁੱਤਰੀ|daughter)',s,re.I):return 'ਪੁਤਰੀ'
    return s


def crop_ocr(page, rect, psm=6):
    pix=page.get_pixmap(matrix=fitz.Matrix(6,6),clip=fitz.Rect(*rect),alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    # Preserve the original print strokes; light autocontrast helps compressed scans.
    img=ImageOps.autocontrast(ImageOps.grayscale(img))
    try:return clean(pytesseract.image_to_string(img,lang='pan',config=f'--oem 1 --psm {psm}'))
    except Exception:
        try:return clean(pytesseract.image_to_string(img,lang='pan+eng',config=f'--oem 1 --psm {psm}'))
        except Exception:return ''


def page_ocr_words(page, scale=4.0):
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    try:
        data=pytesseract.image_to_data(img,lang='pan',config='--oem 1 --psm 11',output_type=pytesseract.Output.DICT)
    except Exception:
        data=pytesseract.image_to_data(img,lang='pan+eng',config='--oem 1 --psm 11',output_type=pytesseract.Output.DICT)
    out=[]
    for i,t in enumerate(data.get('text',[])):
        t=clean(t)
        if not t: continue
        try: conf=float(data['conf'][i])
        except: conf=0
        out.append({'t':t,'x':int(data['left'][i])/scale,'y':int(data['top'][i])/scale,
                    'w':int(data['width'][i])/scale,'h':int(data['height'][i])/scale,'conf':conf})
    return out


def words_in(words,x1,y1,x2,y2):
    return sorted([w for w in words if x1<=w['x']<=x2 and y1<=w['y']<=y2],key=lambda w:(w['y'],w['x']))


def join_words(ws):
    if not ws:return ''
    # Tesseract may place two words on the same printed line at slightly different
    # y-coordinates. Cluster by baseline first, then preserve left-to-right order.
    ordered=sorted(ws,key=lambda w:(w['y'],w['x']))
    groups=[]
    for w in ordered:
        if not groups or abs(w['y']-groups[-1][0])>3.0:
            groups.append([w['y'],[w]])
        else:
            groups[-1][1].append(w)
            groups[-1][0]=(groups[-1][0]+w['y'])/2
    return clean(' '.join(w['t'] for _,items in groups for w in sorted(items,key=lambda z:z['x'])))


def normalize_name_text(s):
    s=clean(s)
    s=re.sub(r'[_|`~]+','',s)
    s=re.sub(r'\s*[-–—]+\s*$','',s)
    # A few stable OCR substitutions in this Punjabi electoral-roll font.
    s=re.sub(r'(?<!\S)ਕਮਾਰ(?!\S)','ਕੁਮਾਰ',s)
    s=re.sub(r'(?<!\S)ਕੋਰ(?!\S)','ਕੌਰ',s)
    return clean(s)


def exact_card_fields(page,x0,y0,ocr_words=None):
    blocks=page.get_text('blocks')
    # Tight vertical band prevents an adjacent row's EPIC/fields from leaking into this card.
    card=[b for b in blocks if b[0]>=x0-3 and b[0]<=x0+173 and b[1]>=y0-3 and b[1]<=y0+69]
    epic=''; house=''; age=None; gender=''; relation=''
    for b in card:
        bx,by,bx1,by1,t=b[:5]; ss=clean(t)
        em=EPIC_RE.search(ss.upper())
        if em and by<=y0+15: epic=em.group(0).upper()
        if abs(by-(y0+47))<7 and x0+25<=bx<=x0+85 and not re.search(r'(?:ਮਕਾਨ|ਮਪਪਨ|house|ਜਲਨਗ)',ss,re.I):
            house=ss
        if abs(by-(y0+59))<8 and x0+30<=bx<=x0+155:
            m=re.search(r'(?<!\d)(\d{1,3})(?!\d)',ss)
            if m: age=int(m.group(1))
            gender=normalize_gender(ss)
        if abs(by-(y0+32))<9 and x0-1<=bx<=x0+10:
            relation=normalize_relation(ss)
    if ocr_words:
        # Exact printed coordinates for this 3-column/10-row electoral-roll grid.
        nw=[w for w in words_in(ocr_words,x0+28,y0+14,x0+155,y0+25) if w['conf']>=45]
        fw=[w for w in words_in(ocr_words,x0+15,y0+24,x0+165,y0+42.5) if w['conf']>=15]
        name=join_words(nw)
        father=join_words(fw)
        # The father/relative line may include the relation label when OCR spans both.
        m=re.match(r'^(ਪਿਤਾ|ਪਤੀ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|father|husband|mother|son|daughter)\s+(.+)$',father,re.I)
        if m:
            relation=normalize_relation(m.group(1)); father=clean(m.group(2))
        # Drop common OCR punctuation/noise at the ends.
        name=normalize_name_text(name)
        father=normalize_name_text(father)
        # Remove obvious field labels accidentally captured by OCR.
        name=re.sub(r'^(?:ਨਾਮ|Name)\s*[:：-]?\s*','',name,flags=re.I)
        father=re.sub(r'^(?:ਪਿਤਾ|ਪਤੀ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|father|husband|mother|son|daughter)\s*[:：-]?\s*','',father,flags=re.I)
    return name if 'name' in locals() else '', father if 'father' in locals() else '', relation, epic, age, gender, house


def embedded_rows(page, pno, part, ocr_words=None):
    blocks=page.get_text('blocks'); rows=[]
    marker_blocks=[b for b in blocks if is_marker(b[4])]
    for b in marker_blocks:
        text=clean(b[4]); serial=None
        for l in reversed(lines(b[4])):
            if SERIAL_RE.fullmatch(l) and not MARK_RE.fullmatch(l): serial=int(l); break
        if serial is None: continue
        x0,y0=b[0],b[1]
        name,father,relation,epic,age,gender,house=exact_card_fields(page,x0,y0,ocr_words)
        rows.append({'serial_no':str(serial),'name':name,'father_husband':father,'relation':relation,'epic':epic,'age':age,'gender':gender,'house_no':house,'part_no':part,'page_no':pno,'photo_key':f'{pno}:{x0}:{y0}','raw_text':text})
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
    name,father,relation,epic,age,gender,house=exact_card_fields(page,x,y)
    if not name or not father:
        n,f,r,raw=ocr_card(page,x,y)
        name=name or n; father=father or f; relation=relation or r
    return {'serial_no':str(serial),'name':name,'father_husband':father,'relation':relation,'epic':epic,'age':age,'gender':gender,'house_no':house,'part_no':part,'page_no':pno,'photo_key':f'{pno}:{x}:{y}','raw_text':''}


def main():
    t0=time.time(); doc=fitz.open(PDF); total=len(doc); part=part_no(doc); rows=[]
    progress(2,'Opening PDF',0,total)
    for idx in range(total):
        pno=idx+1; page=doc[idx]
        text=page.get_text('text')
        # The embedded text has correct field geometry but corrupted Punjabi Unicode mapping.
        # One 4x page OCR pass restores the printed Punjabi names/relative names/gender.
        er=embedded_rows(page,pno,part,page_ocr_words(page,EMBED_OCR_SCALE) if text.strip() else None)
        if er:
            rows.extend(er)
            progress(10 + int(78*(pno/total)), 'Reading embedded text + Punjabi OCR', pno, total)
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
