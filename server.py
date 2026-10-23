import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'torob.db'


def load_env():
    """Load the local .env without requiring python-dotenv."""
    env = ROOT / '.env'
    if not env.exists(): return
    for line in env.read_text(encoding='utf-8-sig').splitlines():
        line=line.strip()
        if not line or line.startswith('#') or '=' not in line: continue
        k,v=line.split('=',1); v=v.strip().strip('"').strip("'")
        os.environ.setdefault(k.strip(), v)


load_env()


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    c.execute('''CREATE TABLE IF NOT EXISTS chat_messages (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        session_id TEXT NOT NULL,
        role TEXT NOT NULL CHECK(role IN ('user','assistant')),
        content TEXT NOT NULL,
        created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )''')
    c.execute('CREATE INDEX IF NOT EXISTS idx_chat_messages_session ON chat_messages(session_id, id)')
    return c


def clean(v):
    if v is None or str(v).strip() in ('', 'null', 'None', '[]', '{}'):
        return None
    return v


def jload(v, default=None):
    v = clean(v)
    if v is None: return default if default is not None else {}
    try: return json.loads(v)
    except Exception: return default if default is not None else {}


def product(row):
    if not row: return None
    d = dict(row)
    d['image_urls'] = jload(d.get('image_urls'), [])
    d['specs'] = jload(d.get('specs'), [])
    # 'null' / '' sentinels must surface as unknown, not as a literal brand/model.
    for k in ('brand_name','model','category_name','persian_title','description'):
        if k in d: d[k] = clean(d.get(k))
    return d


def shop(row):
    if not row: return None
    d = dict(row)
    for k in ('category_names','payment_options','weekly_hours'):
        d[k] = jload(d.get(k), [] if k != 'weekly_hours' else {})
    for k in ('name','address','location_label'):
        if k in d: d[k] = clean(d.get(k))
    # contacts intentionally never selected or returned.
    return d


def offer(row):
    if not row: return None
    d = dict(row)
    d['warranty'] = jload(d.get('warranty'), None)
    d['stock_status'] = 'unknown'
    d['seller_response_status'] = 'unknown'
    d['evidence_status'] = 'registered'
    return d


def tokenize(q):
    q = (q or '').replace('ي','ی').replace('ك','ک').lower().strip()
    toks = [x for x in q.split() if len(x) > 1]
    # A query of only 1-char tokens must not degrade into "return everything".
    return toks if toks else ([q] if q else [])


def search_products(con, q, category='', limit=24, brands=None, warranty=False, payments=None, fast_delivery=False):
    terms = tokenize(q)
    params=[]; where=[]
    brands=[x.strip() for x in (brands or []) if x and x.strip()]
    payments=[x.strip() for x in (payments or []) if x and x.strip()]
    if category:
        where.append('p.category_name = ?'); params.append(category)
    if brands:
        where.append('p.brand_name IN ('+','.join('?' for _ in brands)+')'); params.extend(brands)
    if terms:
        for t in terms:
            where.append('(lower(p.persian_title) LIKE ? OR lower(COALESCE(p.brand_name,\'\')) LIKE ? OR lower(COALESCE(p.model,\'\')) LIKE ? OR lower(COALESCE(p.category_name,\'\')) LIKE ?)')
            x=f'%{t}%'; params += [x,x,x,x]
    offer_filters=[]
    if warranty: offer_filters.append("o.warranty IS NOT NULL AND o.warranty NOT IN ('','null','[]','{}')")
    if payments:
        offer_filters.append('s.payment_options IS NOT NULL AND ('+' OR '.join('s.payment_options LIKE ?' for _ in payments)+')')
        params.extend([f'%{x}%' for x in payments])
    if fast_delivery: offer_filters.append("lower(COALESCE(s.supports_fast_delivery,''))='true'")
    if offer_filters:
        where.append('EXISTS (SELECT 1 FROM offers o JOIN shops s ON s.shop_id=o.shop_id WHERE o.product_id=p.product_id AND '+' AND '.join(offer_filters)+')')
    lim = min(max(int(limit),1),60)
    sql='SELECT p.* FROM products p' + (' WHERE '+ ' AND '.join(where) if where else '') + ' ORDER BY p.product_id LIMIT ?'
    params.append(lim*12)
    rows=[product(r) for r in con.execute(sql,params)]
    phrase=' '.join(terms)
    def score(p):
        t=(p.get('persian_title') or '').lower()
        b=(p.get('brand_name') or '').lower()
        c=(p.get('category_name') or '').lower()
        s=0
        if phrase and t==phrase: s+=1000
        if phrase and t.startswith(phrase): s+=400
        if phrase and phrase in t: s+=250
        if b and phrase and b in phrase: s+=120
        if c and phrase and phrase in c: s+=60
        for t2 in terms:
            if t2 in t: s+=25
        return -s
    rows.sort(key=score)
    return rows[:lim]


def haversine(a,b,c,d):
    try:
        r=6371; p=math.pi/180
        x=math.sin((c-a)*p/2)**2 + math.cos(a*p)*math.cos(c*p)*math.sin((d-b)*p/2)**2
        return round(2*r*math.asin(math.sqrt(x)),2)
    except Exception: return None


def candidates(con, pid, lat=None, lon=None, budget=None, location=''):
    sql='''SELECT o.*, p.persian_title, p.brand_name, p.category_name, p.model,
           s.shop_id AS s_id, s.name, s.address, s.location_label, s.lat, s.lon,
           s.payment_options, s.weekly_hours, s.supports_fast_delivery, s.website_url
           FROM offers o JOIN products p ON p.product_id=o.product_id
           JOIN shops s ON s.shop_id=o.shop_id WHERE o.product_id=?'''
    params=[pid]
    if budget is not None:
        sql += ' AND (o.price_toman IS NULL OR o.price_toman <= ?)'; params.append(float(budget))
    if location:
        sql += ' AND s.location_label LIKE ?'; params.append(f'%{location}%')
    rows=[]
    for r in con.execute(sql,params):
        d=dict(r); d['warranty']=jload(d.get('warranty'),None); d['payment_options']=jload(d.get('payment_options'),[]); d['weekly_hours']=jload(d.get('weekly_hours'),{})
        d['stock_status']='unknown'; d['seller_response_status']='unknown'; d['evidence_status']='registered'
        d['distance_km']=haversine(float(lat),float(lon),float(d['lat']),float(d['lon'])) if lat is not None and lon is not None and d['lat'] is not None else None
        rows.append(d)
    # One (shop, product) pair may own up to 105 offer rows; collapse to one row per shop
    # using the minimum registered price, and expose how many rows were merged.
    merged={}
    for d in rows:
        k=d['s_id']
        cur=merged.get(k)
        if cur is None:
            d['offer_rows']=1; merged[k]=d
        else:
            cur['offer_rows']+=1
            if d['price_toman'] is not None and (cur['price_toman'] is None or d['price_toman']<cur['price_toman']):
                cur['price_toman']=d['price_toman']; cur['offer_id']=d['offer_id']
            if d['distance_km'] is not None: cur['distance_km']=d['distance_km']
    rows=list(merged.values())
    rows.sort(key=lambda x: (x['distance_km'] is None, x['distance_km'] or 0, x['price_toman'] is None, x['price_toman'] or 0))
    # Avoid exposing raw contacts and identify duplicates as offers, not shops.
    return rows[:80]


def alternatives(con, product_row, lat=None, lon=None, limit=10):
    category=product_row.get('category_name')
    terms=tokenize(product_row.get('persian_title',''))[:3]
    params=[]; where=[]
    if category: where.append('p.category_name=?'); params.append(category)
    if terms:
        where.append('('+' OR '.join('lower(p.persian_title) LIKE ?' for _ in terms)+')')
        params += [f'%{t}%' for t in terms]
    where.append('p.product_id <> ?'); params.append(product_row['product_id'])
    sql='''SELECT DISTINCT p.* FROM products p JOIN offers o ON o.product_id=p.product_id WHERE '''+' AND '.join(where)+' LIMIT ?'
    params.append(limit)
    return [product(r) for r in con.execute(sql,params)]


def summary(con):
    return {t: con.execute(f'SELECT COUNT(*) FROM {t}').fetchone()[0] for t in ('products','offers','shops')}


def spec_map(p):
    out={}
    for s in (p.get('specs') or []):
        if isinstance(s, dict):
            k=str(s.get('key') or '').strip(); v=str(s.get('value') or '').strip()
            if k and v: out[k]=v
    return out


def compare(con, ids, lat=None, lon=None):
    """Side-by-side facts for up to 3 products. Registered data only; stock stays unknown."""
    items=[]
    for pid in ids[:3]:
        row=con.execute('SELECT * FROM products WHERE product_id=?',(pid,)).fetchone()
        if not row: continue
        p=product(row)
        offs=candidates(con,pid,lat,lon)
        prices=[o['price_toman'] for o in offs if o.get('price_toman') is not None]
        cheapest=min(offs,key=lambda o:(o['price_toman'] is None, o['price_toman'] or 0)) if offs else None
        nearest=None
        if lat is not None and lon is not None:
            dist=[o for o in offs if o.get('distance_km') is not None]
            if dist: nearest=min(dist,key=lambda o:o['distance_km'])
        items.append({
            'product_id':pid,
            'title':p.get('persian_title'),
            'brand':p.get('brand_name'),
            'category':p.get('category_name'),
            'model':p.get('model'),
            'image':(p.get('image_urls') or [None])[0],
            'shop_count':len(offs),
            'min_price':min(prices) if prices else None,
            'max_price':max(prices) if prices else None,
            'cheapest_shop':({'name':cheapest.get('name'),'area':cheapest.get('location_label')} if cheapest else None),
            'nearest_km':(nearest.get('distance_km') if nearest else None),
            'nearest_shop':({'name':nearest.get('name')} if nearest else None),
            'warranty_offers':sum(1 for o in offs if o.get('warranty')),
            'fast_delivery_shops':sum(1 for o in offs if str(o.get('supports_fast_delivery')).lower()=='true'),
            'stock_status':'unknown',
            'seller_response_status':'unknown',
            'evidence_status':'registered',
            'specs':spec_map(p),
        })
    seen=[]; keys=[]
    for it in items:
        for k in it['specs']:
            if k not in seen: seen.append(k)
    for k in seen:
        if sum(1 for x in items if k in x['specs']) >= 2: keys.append(k)
    spec_rows=[{'key':k,'values':[it['specs'].get(k) for it in items]} for k in keys[:12]]
    return {'items':items,'spec_rows':spec_rows,'requested':len(ids),'returned':len(items)}


def chat_history(con, session_id, limit=20):
    rows=con.execute('SELECT role, content FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?', (session_id, limit)).fetchall()
    return [dict(r) for r in reversed(rows)]


def chat_answer(con, messages, session_id=None):
    session_id=session_id or uuid.uuid4().hex
    stored=chat_history(con, session_id, 20)
    # Backward-compatible: old clients may still send their in-memory transcript.
    incoming=messages or []
    if not stored: stored=[m for m in incoming if m.get('role') in ('user','assistant')][-20:]
    user = next((m.get('content','') for m in reversed(incoming or stored) if m.get('role')=='user'), '')
    last_stored_user = next((m.get('content','') for m in reversed(stored) if m.get('role')=='user'), '')
    conversation = list(stored)
    if user and user != last_stored_user:
        conversation.append({'role':'user','content':user})
    key=os.getenv('OPENAI_API_KEY','').strip()
    base=os.getenv('OPENAI_BASE_URL','https://api.openai.com/v1').strip().rstrip('/')
    model=os.getenv('OPENAI_MODEL','gpt-4o-mini').strip()
    if not key or key.startswith('your-'):
        return {'error':'کلید مدل تنظیم نشده است. مقدار OPENAI_API_KEY را در فایل .env وارد کنید.'}, 503
    user = next((m.get('content','') for m in reversed(messages) if m.get('role')=='user'), '')
    stop={'میخواهم','می‌خواهم','میخوام','می‌خوام','یک','برای','تا','در','از','با','تومان','تهران','لطفا','لطفاً'}
    words=[w for w in tokenize(user) if w not in stop and not w.isdigit() and len(w)>1]
    queries=[' '.join(words[:2]), ' '.join(words[:1])] if words else [user]
    rows=[]; seen=set()
    for query in queries:
        for p in search_products(con,query,limit=8):
            if p['product_id'] not in seen:
                rows.append(p); seen.add(p['product_id'])
            if len(rows)>=8: break
        if len(rows)>=8: break
    context=[]
    for p in rows:
        offers=candidates(con,p['product_id'])[:3]
        context.append({'product_id':p['product_id'],'title':p.get('persian_title'),'brand':p.get('brand_name'),'category':p.get('category_name'),'offers':[{'shop':o.get('name'),'area':o.get('location_label'),'address':o.get('address'),'price_toman':o.get('price_toman'),'price_changed':o.get('last_price_changed_at'),'offer_rows':o.get('offer_rows',1)} for o in offers]})
    system='''تو دستیار خرید حضوری ترب هستی. فقط بر اساس داده snapshot ارائه‌شده پاسخ بده. آفر ثبت‌شده موجودی امروز نیست؛ قیمت مربوط به زمان ثبت است؛ موجودی و پاسخ فروشنده نامعلوم است. شماره تماس، ادعای خرید/رزرو/ارسال و هویت تأییدشده نساز. اگر نتیجه‌ای نیست بگو این فقط محدودیت پوشش داده است. پاسخ فارسی، کوتاه و کاربردی باشد. محصولات را با نام، قیمت ثبت‌شده، محدوده و فروشگاه معرفی کن و برای تصمیم بهتر سؤال تکمیلی بپرس.'''
    payload={'model':model,'messages':[{'role':'system','content':system+'\nداده‌های جست‌وجوی فعلی:\n'+json.dumps(context,ensure_ascii=False)}]+conversation[-10:],'temperature':0.2}
    req=urllib.request.Request(base+'/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=60) as r: data=json.loads(r.read().decode())
        answer=data['choices'][0]['message']['content']
        if user:
            con.execute('INSERT INTO chat_messages(session_id,role,content) VALUES (?,?,?)',(session_id,'user',user))
        con.execute('INSERT INTO chat_messages(session_id,role,content) VALUES (?,?,?)',(session_id,'assistant',answer))
        con.commit()
        return {'answer':answer,'model':model,'session_id':session_id},200
    except urllib.error.HTTPError as e:
        detail=e.read().decode(errors='replace')[:500]
        return {'error':f'خطا از سرویس مدل ({e.code}): {detail}'},502
    except Exception as e:
        return {'error':f'ارتباط با سرویس مدل برقرار نشد: {e}'},502

class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        u=urlparse(self.path); con=db()
        try:
            if u.path != '/api/chat': self.send_json({'error':'not found'},404); return
            try: body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')) or 0).decode())
            except Exception: self.send_json({'error':'درخواست چت نامعتبر است'},400); return
            result,status=chat_answer(con,body.get('messages',[]),body.get('session_id')); self.send_json(result,status)
        except Exception as e: self.send_json({'error':'خطای داخلی','detail':str(e)},500)
        finally: con.close()
    def do_GET(self):
        u=urlparse(self.path); p=u.path; q=parse_qs(u.query); con=db()
        try:
            if p=='/api/health': self.send_json({'ok':True,'db':DB.name,'counts':summary(con)}); return
            if p=='/api/search':
                brands=q.get('brand',[]); payments=q.get('payment',[])
                self.send_json({'items':search_products(con,q.get('q',[''])[0],q.get('category',[''])[0],q.get('limit',[24])[0],brands,q.get('warranty',['0'])[0]=='1',payments,q.get('fast_delivery',['0'])[0]=='1')}); return
            if p.startswith('/api/products/'):
                pid=p.rsplit('/',1)[-1]; row=con.execute('SELECT * FROM products WHERE product_id=?',(pid,)).fetchone()
                if not row: self.send_json({'error':'محصول پیدا نشد'},404); return
                self.send_json({'product':product(row),'offers':candidates(con,pid,q.get('lat',[None])[0],q.get('lon',[None])[0],q.get('budget',[None])[0] if q.get('budget',[None])[0] else None,q.get('location',[''])[0])}); return
            if p=='/api/alternatives':
                pid=q.get('product_id',[''])[0]; row=con.execute('SELECT * FROM products WHERE product_id=?',(pid,)).fetchone()
                self.send_json({'items':alternatives(con,product(row) if row else {},q.get('lat',[None])[0],q.get('lon',[None])[0])}); return
            if p=='/api/shops':
                loc=q.get('location',[''])[0]; rows=con.execute('SELECT * FROM shops WHERE location_label LIKE ? OR address LIKE ? LIMIT 60',(f'%{loc}%',f'%{loc}%')).fetchall() if loc else con.execute('SELECT * FROM shops LIMIT 60').fetchall()
                self.send_json({'items':[shop(r) for r in rows]}); return
            if p=='/api/meta':
                cats=[r[0] for r in con.execute('SELECT category_name FROM products GROUP BY category_name ORDER BY category_name LIMIT 300')]
                locs=[r[0] for r in con.execute('SELECT location_label FROM shops WHERE location_label IS NOT NULL GROUP BY location_label ORDER BY location_label')]
                brands=[r[0] for r in con.execute("SELECT brand_name FROM products WHERE brand_name IS NOT NULL AND brand_name!='null' GROUP BY brand_name ORDER BY COUNT(*) DESC, brand_name LIMIT 80")]
                payments=[r[0] for r in con.execute("SELECT DISTINCT json_extract(value,'$.provider_name') FROM shops, json_each(shops.payment_options) WHERE json_valid(shops.payment_options) AND json_extract(value,'$.provider_name') IS NOT NULL ORDER BY 1 LIMIT 30")]
                self.send_json({'categories':cats,'locations':locs,'brands':brands,'payments':payments,'counts':summary(con)}); return
            if p=='/api/compare':
                raw=q.get('ids',[''])[0]
                ids=[x.strip() for x in raw.split(',') if x.strip()]
                if len(ids) < 2:
                    self.send_json({'error':'برای مقایسه حداقل دو محصول لازم است'},400); return
                if len(ids) > 3:
                    self.send_json({'error':'حداکثر سه محصول قابل مقایسه است'},400); return
                self.send_json(compare(con, ids, q.get('lat',[None])[0], q.get('lon',[None])[0])); return
            if p=='/api/chat/history':
                sid=q.get('session_id',[''])[0]
                if not sid: self.send_json({'error':'session_id لازم است'},400); return
                self.send_json({'session_id':sid,'messages':chat_history(con,sid,50)}); return
            if p=='/':
                data=(ROOT/'public'/'index.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
            if p.startswith('/static/'):
                fp=ROOT/'public'/p.removeprefix('/static/')
                if fp.exists() and fp.is_file():
                    data=fp.read_bytes(); typ='text/css' if fp.suffix=='.css' else 'application/javascript'; self.send_response(200); self.send_header('Content-Type',typ); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
            self.send_json({'error':'not found'},404)
        except Exception as e:
            self.send_json({'error':'خطای داخلی','detail':str(e)},500)
        finally: con.close()
    def log_message(self, fmt, *args): pass

if __name__=='__main__':
    if not DB.exists():
        from ingest import build
        print('Building SQLite index...', build())
    print('http://127.0.0.1:8787')
    ThreadingHTTPServer(('127.0.0.1',8787),Handler).serve_forever()
