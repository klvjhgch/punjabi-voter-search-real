import sys, json, re, os, traceback, time
from concurrent.futures import ThreadPoolExecutor, as_completed
import fitz
import pytesseract
from PIL import Image, ImageOps

PDF = sys.argv[1]

MARK_RE = re.compile(r'(?:\b16\s*/\s*20\s*/\s*\d{1,6}\b)', re.I)
WS_MARK_RE = re.compile(r'\bws\s*[-:]?\s*\d{2,8}\b', re.I)
WS_RE = re.compile(r'\bws\s*[-:]?\s*\d{2,8}\b', re.I)
EPIC_RE = re.compile(r'\b[A-Z]{2,5}[0-9]{6,10}\b', re.I)
SERIAL_RE = re.compile(r'^\s*(\d{1,5})\s*$')
REL_RE = re.compile(r'^(ਪਤੀ|ਪਿਤਾ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|ਮਾਂ|ਪਿਤਾ/ਪਤੀ|father|husband|wife|son|daughter|mother)\s*[:：-]?$', re.I)
LABEL_RE = re.compile(r'(ਨਾਮ|name|ਮਕਾਨ|house|ਉਮਰ|age|ਲਿੰਗ|gender|ਪਤੀ|ਪਿਤਾ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|father|husband|wife|son|daughter|mother)', re.I)

# Render/OCR can be tuned without changing code. Lower values are much faster on large rolls.
PAGE_SCALE = float(os.getenv('OCR_PAGE_SCALE', '1.25'))
CARD_SCALE = float(os.getenv('OCR_CARD_SCALE', '2.2'))
MAX_CARD_OCR = int(os.getenv('OCR_MAX_CARD_OCR', '2500'))
MAX_PARALLEL = max(1, min(int(os.getenv('OCR_PARALLEL', '2')), 4))
EMBED_OCR_SCALE = float(os.getenv('OCR_EMBED_SCALE', '2.0'))


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
    s=clean(s)
    return bool(MARK_RE.search(s) or WS_MARK_RE.search(s))


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


def ocr_name_pair(page, x0, y0):
    """OCR only the printed name + relative-name lines of one voter card.
    This avoids the expensive full-page OCR that caused 35%/timeouts on Render.
    """
    rect=fitz.Rect(x0+25, y0+15, x0+132, y0+47)
    pix=page.get_pixmap(matrix=fitz.Matrix(4.5,4.5), clip=rect, alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    img=ImageOps.grayscale(img)
    try:
        txt=pytesseract.image_to_string(img,lang='pan',config='--oem 1 --psm 6')
    except Exception:
        txt=pytesseract.image_to_string(img,lang='pan+eng',config='--oem 1 --psm 6')
    vals=[normalize_name_text(x) for x in str(txt).splitlines() if clean(x)]
    vals=[x for x in vals if not re.search(r'^(?:ਨਾਮ|ਪਿਤਾ|ਪਤੀ|ਮਾਤਾ|ਮਕਾਨ|ਉਮਰ|ਲਿੰਗ|Name|Father|Husband)',x,re.I)]
    return (vals[0] if len(vals)>0 else '', vals[1] if len(vals)>1 else '')

# Punjabi -> Roman/English transliteration. Original Punjabi is retained in `name` and
# `father_husband`; the Roman forms are stored separately for display/search.
VOWELS={'ਾ':'aa','ਿ':'i','ੀ':'ee','ੁ':'u','ੂ':'oo','ੇ':'e','ੈ':'ai','ੋ':'o','ੌ':'au'}
CONS={'ਕ':'k','ਖ':'kh','ਗ':'g','ਘ':'gh','ਙ':'ng','ਚ':'ch','ਛ':'chh','ਜ':'j','ਝ':'jh','ਞ':'ny','ਟ':'t','ਠ':'th','ਡ':'d','ਢ':'dh','ਣ':'n','ਤ':'t','ਥ':'th','ਦ':'d','ਧ':'dh','ਨ':'n','ਪ':'p','ਫ':'ph','ਬ':'b','ਭ':'bh','ਮ':'m','ਯ':'y','ਰ':'r','ਲ':'l','ਵ':'v','ੜ':'r','ਸ':'s','ਹ':'h','ਸ਼':'sh','ਸ਼':'sh','ਖ਼':'kh','ਗ਼':'gh','ਜ਼':'z','ਫ਼':'f','ਲ਼':'l','ਕ਼':'q'}
SIGNS={'ੰ':'n','ਂ':'n','ੱ':'','਼':'','੍':''}

def romanize_gurmukhi(text):
    text=clean(text)
    out=[]; i=0
    while i<len(text):
        ch=text[i]
        if ch.isspace():
            out.append(' '); i+=1; continue
        if ch in VOWELS:
            # Independent vowel signs are uncommon at word start; keep readable Roman form.
            out.append(VOWELS[ch]); i+=1; continue
        if ch in ('ੰ','ਂ'):
            out.append('n'); i+=1; continue
        if ch=='ੱ':
            # Add a consonant doubling marker only when there is a previous consonant.
            if out and out[-1] and out[-1][-1].isalpha(): out[-1]+=out[-1][-1]
            i+=1; continue
        if ch=='਼': i+=1; continue
        if ch=='੍': i+=1; continue
        if ch in CONS:
            base=CONS[ch]; i+=1
            # Nukta is represented as a separate sign in some OCR outputs.
            if i<len(text) and text[i]=='਼': i+=1
            # Consonant cluster: virama suppresses inherent a and the next consonant follows.
            if i<len(text) and text[i]=='੍':
                i+=1
                if i<len(text) and text[i] in CONS:
                    out.append(base); continue
            if i<len(text) and text[i] in VOWELS:
                out.append(base+VOWELS[text[i]]); i+=1
            else:
                out.append(base+'a')
            continue
        if ch in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-': out.append(ch)
        i+=1
    result=''.join(out)
    result=re.sub(r'\ba\b','',result)
    result=re.sub(r'([A-Za-z])a(?=\s|$)',r'\1',result)
    result=re.sub(r'aa','a',result); result=re.sub(r'ee','i',result); result=re.sub(r'oo','u',result)
    result=re.sub(r'(?i)saingh','singh',result); result=re.sub(r'(?i)saingha','singh',result)
    replacements={
      'Ravee':'Ravi','Prakaas':'Prakash','Baladev':'Baldev','Jasavindar':'Jaswinder',
      'Kumaaree':'Kumari','Kumaar':'Kumar','Raaj':'Raj','Varindar':'Varinder',
      'Rajindar':'Rajinder','Jagan Naath':'Jagannath','Chaman Laal':'Chaman Lal',
      'Jasavanta':'Jaswant','Santos':'Santosh','Jagadees':'Jagdish','Manindar':'Maninder',
      'Samaser':'Samsher','Paradeep':'Pradeep','Vije':'Vijay','Ravindar':'Ravinder',
      'Surindar':'Surinder','Davindar':'Davinder','Haravindar':'Harvinder','Guravindar':'Gurvinder'
    }
    words=[]
    for w in result.split(): words.append(replacements.get(w,w))
    result=' '.join(words)
    return ' '.join(w[:1].upper()+w[1:] if w else w for w in result.split())


def normalize_name_text(s):
    s=clean(s)
    s=re.sub(r'[_|`~]+','',s)
    s=re.sub(r'\s*[-–—]+\s*$','',s)
    s=re.sub(r'(?<!\S)ਕਮਾਰ(?!\S)','ਕੁਮਾਰ',s)
    s=re.sub(r'(?<!\S)ਕੋਰ(?!\S)','ਕੌਰ',s)
    return clean(s)


def page_name_ocr_words(page, scale=2.0):
    # OCR only the voter-card body, not the header/footer/photos. One pass per page is
    # substantially faster than launching Tesseract once per voter card.
    clip=fitz.Rect(0,65,575,805)
    pix=page.get_pixmap(matrix=fitz.Matrix(scale,scale),clip=clip,alpha=False)
    img=Image.frombytes('RGB',[pix.width,pix.height],pix.samples)
    data=pytesseract.image_to_data(img,lang='pan',config='--oem 1 --psm 6',output_type=pytesseract.Output.DICT)
    out=[]
    for i,t in enumerate(data.get('text',[])):
        t=clean(t)
        if not t: continue
        try: conf=float(data['conf'][i])
        except: conf=0
        x=int(data['left'][i])/scale; y=int(data['top'][i])/scale+65
        w=int(data['width'][i])/scale; h=int(data['height'][i])/scale
        out.append({'t':t,'x':x,'y':y,'w':w,'h':h,'conf':conf})
    return out

def card_ocr_from_words(words,x0,y0):
    # Names and relative names are the two printed lines immediately below the labels.
    def get(y1,y2,x1=x0+27,x2=x0+133):
        ws=[w for w in words if x1<=w['x']<=x2 and y1<=w['y']<=y2 and w['conf']>=20]
        return clean(' '.join(w['t'] for w in sorted(ws,key=lambda z:(z['y'],z['x']))))
    name=get(y0+15,y0+29)
    father=get(y0+29,y0+45)
    return normalize_name_text(name),normalize_name_text(father)

def exact_card_fields(page,x0,y0,ocr_words=None):
    blocks=page.get_text('blocks')
    # This roll uses a fixed 3-column card grid. The text layer contains reliable
    # geometry for EPIC/age/gender/house, while Punjabi names are OCR'd from tiny crops.
    card=[b for b in blocks if b[0]>=x0-3 and b[0]<=x0+173 and b[1]>=y0-3 and b[1]<=y0+69]
    epic=''; house=''; age=None; gender=''; relation=''
    for b in card:
        bx,by,bx1,by1,t=b[:5]; ss=clean(t)
        em=EPIC_RE.search(ss.upper())
        if em and by<=y0+15: epic=em.group(0).upper()
        if abs(by-(y0+47))<8 and x0+20<=bx<=x0+90 and not re.search(r'(?:ਮਕਾਨ|ਮਪਪਨ|house|ਜਲਨਗ)',ss,re.I):
            house=ss
        if abs(by-(y0+59))<9 and x0+25<=bx<=x0+155:
            m=re.search(r'(?<!\d)(\d{1,3})(?!\d)',ss)
            if m: age=int(m.group(1))
            gender=normalize_gender(ss)
        if abs(by-(y0+32))<10 and x0-1<=bx<=x0+12:
            relation=normalize_relation(ss)
    if ocr_words:
        name,father=card_ocr_from_words(ocr_words,x0,y0)
    else:
        name,father=ocr_name_pair(page,x0,y0)
    # If OCR accidentally includes the field label, remove it.
    name=re.sub(r'^(?:ਨਾਮ|Name)\s*[:：-]?\s*','',name,flags=re.I)
    father=re.sub(r'^(?:ਪਿਤਾ|ਪਤੀ|ਮਾਤਾ|ਪੁੱਤਰ|ਪੁਤਰੀ|father|husband|mother|son|daughter)\s*[:：-]?\s*','',father,flags=re.I)
    return name,father,relation,epic,age,gender,house


def embedded_rows(page, pno, part, ocr_words=None):
    blocks=page.get_text('blocks'); rows=[]
    marker_blocks=[b for b in blocks if MARK_RE.search(clean(b[4]) or '') or WS_MARK_RE.search(clean(b[4]) or '')]
    for b in marker_blocks:
        text=clean(b[4]); serial=None
        for l in reversed(lines(b[4])):
            if SERIAL_RE.fullmatch(l) and not MARK_RE.fullmatch(l): serial=int(l); break
        if serial is None: continue
        x0,y0=b[0],b[1]
        name,father,relation,epic,age,gender,house=exact_card_fields(page,x0,y0,ocr_words)
        rows.append({'serial_no':str(serial),'name':name,'name_en':romanize_gurmukhi(name),'father_husband':father,'father_husband_en':romanize_gurmukhi(father),'relation':relation,'epic':epic,'age':age,'gender':gender,'house_no':house,'part_no':part,'page_no':pno,'photo_key':f'{pno}:{x0}:{y0}','raw_text':text})
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
        try:
            n,f=ocr_name_pair(page,x,y)
            name=name or n; father=father or f
        except Exception: pass
    return {'serial_no':str(serial),'name':name,'name_en':romanize_gurmukhi(name),'father_husband':father,'father_husband_en':romanize_gurmukhi(father),'relation':relation,'epic':epic,'age':age,'gender':gender,'house_no':house,'part_no':part,'page_no':pno,'photo_key':f'{pno}:{x}:{y}','raw_text':''}


def main():
    t0=time.time(); doc=fitz.open(PDF); total=len(doc); part=part_no(doc); rows=[]
    progress(2,'Opening PDF',0,total)
    for idx in range(total):
        pno=idx+1; page=doc[idx]
        text=page.get_text('text')
        # The embedded text has correct field geometry but corrupted Punjabi Unicode mapping.
        # One 4x page OCR pass restores the printed Punjabi names/relative names/gender.
        er=embedded_rows(page,pno,part,None)
        if er:
            rows.extend(er)
            progress(10 + int(78*(pno/total)), 'Reading embedded text + Punjabi OCR', pno, total)
            continue
        # OCR only pages that actually need it. Cover/header pages are skipped when there is no
        # marker signal; image-only voter pages are OCR'd once, not once for every text block.
        signal=bool([b for b in page.get_text('blocks') if MARK_RE.search(clean(b[4]) or '') or WS_MARK_RE.search(clean(b[4]) or '')])
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
