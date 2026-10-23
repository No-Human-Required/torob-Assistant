import json
import math
import sqlite3
from pathlib import Path
from urllib.parse import parse_qs, urlparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = Path(__file__).resolve().parent
DB = ROOT / 'torob.db'


def db():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
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
    return d


def shop(row):
    if not row: return None
    d = dict(row)
    for k in ('category_names','payment_options','weekly_hours'):
        d[k] = jload(d.get(k), [] if k != 'weekly_hours' else {})
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
    return [x for x in (q or '').replace('ي','ی').replace('ك','ک').lower().split() if len(x) > 1]


def search_products(con, q, category='', limit=24):
    terms = tokenize(q)
    params=[]; where=[]
    if category:
        where.append('category_name = ?'); params.append(category)
    if terms:
        for t in terms:
            where.append('(lower(persian_title) LIKE ? OR lower(COALESCE(brand_name,\'\')) LIKE ? OR lower(COALESCE(model,\'\')) LIKE ? OR lower(COALESCE(category_name,\'\')) LIKE ?)')
            x=f'%{t}%'; params += [x,x,x,x]
    sql='SELECT * FROM products' + (' WHERE '+ ' AND '.join(where) if where else '') + ' ORDER BY product_id LIMIT ?'
    params.append(min(max(int(limit),1),60))
    rows=[product(r) for r in con.execute(sql,params)]
    return rows


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

class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_GET(self):
        u=urlparse(self.path); p=u.path; q=parse_qs(u.query); con=db()
        try:
            if p=='/api/health': self.send_json({'ok':True,'db':DB.name,'counts':summary(con)}); return
            if p=='/api/search': self.send_json({'items':search_products(con,q.get('q',[''])[0],q.get('category',[''])[0],q.get('limit',[24])[0])}); return
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
                self.send_json({'categories':cats,'locations':locs,'counts':summary(con)}); return
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
