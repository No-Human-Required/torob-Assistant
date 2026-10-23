import json
import math
import os
import sqlite3
import urllib.error
import urllib.request
import uuid
import datetime
import re
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
    c.executescript("""
    CREATE TABLE IF NOT EXISTS inventory_records (inventory_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL, shop_id TEXT NOT NULL, variant_key TEXT NOT NULL DEFAULT 'default', stock_qty INTEGER, quantity_scope TEXT NOT NULL DEFAULT 'store_sellable', stock_source TEXT NOT NULL DEFAULT 'unknown', stock_confirmed_at TEXT, baseline_qty INTEGER, supply_cycle_id TEXT, version INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL, UNIQUE(product_id, shop_id, variant_key));
    CREATE TABLE IF NOT EXISTS inventory_events (event_id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL UNIQUE, inventory_id INTEGER NOT NULL, event_type TEXT NOT NULL, qty INTEGER, source TEXT NOT NULL, actor_label TEXT NOT NULL DEFAULT 'seller_demo', created_at TEXT NOT NULL, payload TEXT);
    CREATE TABLE IF NOT EXISTS inventory_alerts (alert_id INTEGER PRIMARY KEY AUTOINCREMENT, inventory_id INTEGER NOT NULL, alert_type TEXT NOT NULL, cycle_id TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'open', snoozed_until TEXT, created_at TEXT NOT NULL, UNIQUE(inventory_id, alert_type, cycle_id));
    CREATE TABLE IF NOT EXISTS buyer_subscriptions (subscription_id INTEGER PRIMARY KEY AUTOINCREMENT, subscriber_key TEXT NOT NULL, product_id TEXT NOT NULL, shop_id TEXT, channel TEXT NOT NULL CHECK(channel IN ('browser','sms')), destination TEXT, max_price REAL, location_label TEXT, min_stock INTEGER NOT NULL DEFAULT 1, consent_at TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','paused','cancelled','expired')), expires_at TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_subscriptions_match ON buyer_subscriptions(product_id, status);
    CREATE TABLE IF NOT EXISTS notifications (notification_id INTEGER PRIMARY KEY AUTOINCREMENT, subscription_id INTEGER NOT NULL, event_id INTEGER NOT NULL, subscriber_key TEXT NOT NULL, channel TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, delivery_status TEXT NOT NULL DEFAULT 'pending', read_at TEXT, created_at TEXT NOT NULL, UNIQUE(subscription_id, event_id, channel));
    CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(subscriber_key, notification_id);
    CREATE TABLE IF NOT EXISTS discrepancy_reports (report_id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL, shop_id TEXT, issue_type TEXT NOT NULL, note TEXT, status TEXT NOT NULL DEFAULT 'registered', created_at TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS seller_reviews (review_id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id TEXT NOT NULL, product_id TEXT, freshness_score REAL NOT NULL DEFAULT 0, accuracy_score REAL NOT NULL DEFAULT 0, response_score REAL NOT NULL DEFAULT 0, evidence_count INTEGER NOT NULL DEFAULT 0, badge_status TEXT NOT NULL DEFAULT 'insufficient_history', rank_factor REAL NOT NULL DEFAULT 1, reason TEXT, calculated_at TEXT NOT NULL, UNIQUE(shop_id, product_id));
    CREATE TABLE IF NOT EXISTS policy_config (policy_key TEXT PRIMARY KEY, policy_value TEXT NOT NULL, version INTEGER NOT NULL DEFAULT 1, updated_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS appeals (appeal_id INTEGER PRIMARY KEY AUTOINCREMENT, shop_id TEXT NOT NULL, report_id INTEGER, reason TEXT NOT NULL, evidence TEXT, status TEXT NOT NULL DEFAULT 'registered', created_at TEXT NOT NULL, request_id TEXT NOT NULL UNIQUE);
    CREATE TABLE IF NOT EXISTS audit_log (audit_id INTEGER PRIMARY KEY AUTOINCREMENT, request_id TEXT NOT NULL, actor TEXT NOT NULL, action TEXT NOT NULL, entity_type TEXT NOT NULL, entity_id TEXT, payload TEXT, created_at TEXT NOT NULL);
    CREATE INDEX IF NOT EXISTS idx_discrepancy_shop_status ON discrepancy_reports(shop_id,status);
    CREATE INDEX IF NOT EXISTS idx_inventory_shop ON inventory_records(shop_id,updated_at);
    """)
    now = now_iso()
    defaults = {'stock_alert_ratio':'0.25','inventory_fresh_days':'7','report_response_days':'3','badge_min_evidence':'5','rank_weight':'0.15'}
    for k,v in defaults.items():
        c.execute('INSERT OR IGNORE INTO policy_config(policy_key,policy_value,updated_at) VALUES (?,?,?)',(k,v,now))
    # Keep old databases compatible with the richer discrepancy workflow.
    for col, definition in [('review_note','TEXT'),('seller_response','TEXT'),('reviewed_at','TEXT'),('reviewed_by','TEXT')]:
        try: c.execute(f'ALTER TABLE discrepancy_reports ADD COLUMN {col} {definition}')
        except sqlite3.OperationalError: pass
    # Optional column: lets a reloaded conversation keep the product cards/s images.
    cols=[r[1] for r in c.execute('PRAGMA table_info(chat_messages)')]
    if 'products' not in cols:
        try: c.execute('ALTER TABLE chat_messages ADD COLUMN products TEXT')
        except sqlite3.OperationalError: pass
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


# Offer-level conditions shared by the EXISTS guard and the sort aggregates, so a
# sorted result can never be ranked by a shop the active filters exclude.
def offer_conditions(warranty=False, payments=None, fast_delivery=False):
    conds=[]; params=[]
    payments=[x.strip() for x in (payments or []) if x and x.strip()]
    if warranty:
        conds.append("o.warranty IS NOT NULL AND o.warranty NOT IN ('','null','[]','{}')")
    if payments:
        conds.append('s.payment_options IS NOT NULL AND ('+' OR '.join('s.payment_options LIKE ?' for _ in payments)+')')
        params.extend([f'%{x}%' for x in payments])
    if fast_delivery:
        conds.append("lower(COALESCE(s.supports_fast_delivery,''))='true'")
    return conds, params


def haversine_sql():
    # Same great-circle distance as haversine(), expressed in SQLite math functions.
    return ("6371*2*asin(min(1.0,sqrt(power(sin((radians(s.lat)-radians(?))/2),2)"
            "+cos(radians(?))*cos(radians(s.lat))*power(sin((radians(s.lon)-radians(?))/2),2))))")


SORTS=('relevance','cheapest','nearest','newest')

# The snapshot contains clearly invalid prices (1 toman, up to 7.3e17 toman).
# "cheapest" only ranks prices inside this band, and the response says so.
PRICE_MIN=1000
PRICE_MAX=10_000_000_000


def search_products(con, q, category='', limit=24, brands=None, warranty=False, payments=None,
                    fast_delivery=False, sort='relevance', lat=None, lon=None):
    terms = tokenize(q)
    brands=[x.strip() for x in (brands or []) if x and x.strip()]
    sort=sort if sort in SORTS else 'relevance'
    lim = min(max(int(limit),1),60)

    where=[]; wparams=[]
    if category:
        where.append('p.category_name = ?'); wparams.append(category)
    if brands:
        where.append('p.brand_name IN ('+','.join('?' for _ in brands)+')'); wparams.extend(brands)
    for t in terms:
        where.append('(lower(p.persian_title) LIKE ? OR lower(COALESCE(p.brand_name,\'\')) LIKE ? OR lower(COALESCE(p.model,\'\')) LIKE ? OR lower(COALESCE(p.category_name,\'\')) LIKE ?)')
        x=f'%{t}%'; wparams += [x,x,x,x]

    conds, cparams = offer_conditions(warranty, payments, fast_delivery)
    has_filters = bool(conds)
    offer_sql = (' AND '+' AND '.join(conds)) if has_filters else ''

    # "nearest" needs a real origin; without one we fall back and say so.
    geo_ok = lat is not None and lon is not None
    applied = sort
    note = None
    if sort=='nearest' and not geo_ok:
        applied='relevance'
        note='برای مرتب‌سازی بر اساس نزدیکی، ابتدا «موقعیت من» را ثبت کنید.'

    # --- deterministic ranked sorts happen in SQL over the whole match set ---
    if applied in ('cheapest','newest','nearest'):
        # Build select list and its placeholders together so param order always matches SQL text.
        agg_parts=['o.product_id AS pid']
        agg_params=[]
        if applied=='cheapest':
            agg_parts.append('MIN(CASE WHEN o.price_toman BETWEEN ? AND ? THEN o.price_toman END) AS min_price')
            agg_params += [PRICE_MIN,PRICE_MAX]
        else:
            agg_parts.append('MIN(o.price_toman) AS min_price')
        agg_parts.append("MAX(CASE WHEN o.last_price_changed_at NOT IN ('','null') THEN o.last_price_changed_at END) AS newest_change")
        if applied=='nearest':
            agg_parts.append(f'MIN(CASE WHEN s.lat IS NOT NULL AND s.lon IS NOT NULL THEN {haversine_sql()} END) AS nearest_km')
            agg_params += [float(lat),float(lat),float(lon)]
        having=''
        if applied=='cheapest':
            # Drop products whose only prices were rejected as invalid.
            having=' HAVING MIN(CASE WHEN o.price_toman BETWEEN ? AND ? THEN o.price_toman END) IS NOT NULL'
            agg_params += [PRICE_MIN,PRICE_MAX]
        agg=f'''SELECT {", ".join(agg_parts)} FROM offers o JOIN shops s ON s.shop_id=o.shop_id
                WHERE 1=1{offer_sql}
                GROUP BY o.product_id{having}'''
        order={'cheapest':'a.min_price ASC',
               'newest':'a.newest_change IS NULL, a.newest_change DESC',
               'nearest':'a.nearest_km IS NULL, a.nearest_km ASC, a.min_price IS NULL, a.min_price ASC'}[applied]
        sql=f'''SELECT p.*, a.min_price AS _min_price, a.newest_change AS _newest_change
                {', a.nearest_km AS _nearest_km' if applied=='nearest' else ''}
                FROM products p JOIN ({agg}) a ON a.pid=p.product_id
                {'WHERE '+' AND '.join(where) if where else ''}
                ORDER BY {order} LIMIT ?'''
        sql_params = agg_params + cparams + wparams + [lim]
        rows=[]
        for r in con.execute(sql, sql_params):
            d=product(r)
            d['min_price']=d.pop('_min_price',None)
            d['newest_price_change']=d.pop('_newest_change',None)
            if '_nearest_km' in d:
                v=d.pop('_nearest_km'); d['nearest_km']=None if v is None else round(v,2)
            rows.append(d)
        if applied=='cheapest':
            note='فقط قیمت‌های ثبت‌شده در بازهٔ ۱٬۰۰۰ تا ۱۰ میلیارد تومان مرتب می‌شوند؛ ردیف‌های با قیمت آشکارا نامعتبر حذف شدند.'
        return {'items':rows,'sort':applied,'sort_requested':sort,'note':note}

    # --- default relevance path (unchanged behaviour) ---
    if has_filters:
        where.append('EXISTS (SELECT 1 FROM offers o JOIN shops s ON s.shop_id=o.shop_id '
                     'WHERE o.product_id=p.product_id'+offer_sql+')')
        wparams.extend(cparams)
    sql='SELECT p.* FROM products p' + (' WHERE '+ ' AND '.join(where) if where else '') + ' ORDER BY p.product_id LIMIT ?'
    rows=[product(r) for r in con.execute(sql, wparams+[lim*12])]
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
    return {'items':rows[:lim],'sort':'relevance','sort_requested':sort,'note':note}


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
        ir=con.execute("SELECT stock_qty,stock_source,stock_confirmed_at FROM inventory_records WHERE product_id=? AND shop_id=? AND variant_key='default'",(d.get('product_id'),d.get('s_id'))).fetchone()
        d['stock_qty']=ir['stock_qty'] if ir else None; d['stock_confirmed_at']=ir['stock_confirmed_at'] if ir else None; d['stock_source']=ir['stock_source'] if ir else 'unknown'
        d['stock_status']='available' if ir and ir['stock_qty'] is not None and ir['stock_qty']>0 else ('out_of_stock' if ir and ir['stock_qty']==0 else 'unknown'); d['seller_response_status']='unknown'; d['evidence_status']='seller_demo' if ir else 'registered'
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
    # Map payload: coordinates are registered snapshot values, not a verified entrance pin.
    for d in rows:
        lat=d.get('lat'); lon=d.get('lon')
        d['map']={
            'lat':lat,
            'lon':lon,
            'has_coords':lat is not None and lon is not None,
            'destination':(f"{lat},{lon}" if lat is not None and lon is not None else None),
            'pin_accuracy':'registered_shop_point',
        }
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


def map_points(con, q='', location=''):
    """Return real registered shop coordinates for the current product search.
    These are snapshot shop points, not invented pins or proof of today's stock.
    With no query we return the registered shops that have coordinates, so the
    map is never an empty placeholder.
    """
    if not (q or '').strip():
        sql = '''SELECT shop_id, name, location_label, lat, lon FROM shops
                 WHERE lat IS NOT NULL AND lon IS NOT NULL'''
        params = []
        if location:
            sql += ' AND (location_label LIKE ? OR address LIKE ?)'
            params += [f'%{location}%', f'%{location}%']
        rows = [dict(r) for r in con.execute(sql + ' LIMIT 160', params)]
        return [r for r in rows if r.get('lat') is not None and r.get('lon') is not None]

    result = search_products(con, q, limit=60)
    ids = [p.get('product_id') for p in result.get('items', [])]
    if not ids:
        return []
    marks = ','.join('?' for _ in ids)
    sql = f'''SELECT DISTINCT s.shop_id, s.name, s.location_label, s.lat, s.lon
              FROM offers o JOIN shops s ON s.shop_id=o.shop_id
              WHERE o.product_id IN ({marks}) AND s.lat IS NOT NULL AND s.lon IS NOT NULL'''
    params = ids
    if location:
        sql += ' AND s.location_label LIKE ?'; params.append(f'%{location}%')
    rows = [dict(r) for r in con.execute(sql + ' LIMIT 80', params)]
    return [r for r in rows if r.get('lat') is not None and r.get('lon') is not None]


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
            'cheapest_shop':({'name':cheapest.get('name'),'area':cheapest.get('location_label'),'lat':cheapest.get('lat'),'lon':cheapest.get('lon'),'address':cheapest.get('address')} if cheapest else None),
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
    rows=con.execute('SELECT role, content, products FROM chat_messages WHERE session_id=? ORDER BY id DESC LIMIT ?', (session_id, limit)).fetchall()
    out=[]
    for r in reversed(rows):
        item={'role':r['role'],'content':r['content']}
        if r['products']:
            try: item['products']=json.loads(r['products'])
            except Exception: pass
        out.append(item)
    return out


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
        for p in search_products(con,query,limit=8)['items']:
            if p['product_id'] not in seen:
                rows.append(p); seen.add(p['product_id'])
            if len(rows)>=8: break
        if len(rows)>=8: break
    context=[]
    for p in rows:
        offers=candidates(con,p['product_id'])[:3]
        context.append({'product_id':p['product_id'],'title':p.get('persian_title'),'brand':p.get('brand_name'),'category':p.get('category_name'),'offers':[{'shop':o.get('name'),'area':o.get('location_label'),'address':o.get('address'),'price_toman':o.get('price_toman'),'price_changed':o.get('last_price_changed_at'),'offer_rows':o.get('offer_rows',1)} for o in offers]})
    # Cards the user can actually see in the chat, with a registered image when present.
    chat_products=[]
    for p in rows[:6]:
        offs=candidates(con,p['product_id'])
        prices=[o['price_toman'] for o in offs if o.get('price_toman') is not None]
        chat_products.append({
            'product_id':p['product_id'],
            'title':p.get('persian_title'),
            'brand':p.get('brand_name'),
            'category':p.get('category_name'),
            'image':(p.get('image_urls') or [None])[0],
            'min_price':min(prices) if prices else None,
            'max_price':max(prices) if prices else None,
            'shop_count':len(offs),
            'stock_status':'unknown',
            'evidence_status':'registered',
        })
    system='''تو دستیار خرید حضوری ترب هستی. فقط بر اساس داده snapshot ارائه‌شده پاسخ بده. آفر ثبت‌شده موجودی امروز نیست؛ قیمت مربوط به زمان ثبت است؛ موجودی و پاسخ فروشنده نامعلوم است. شماره تماس، ادعای خرید/رزرو/ارسال و هویت تأییدشده نساز. اگر نتیجه‌ای نیست بگو این فقط محدودیت پوشش داده است. پاسخ فارسی، کوتاه و کاربردی باشد. محصولات را با نام، قیمت ثبت‌شده، محدوده و فروشگاه معرفی کن و برای تصمیم بهتر سؤال تکمیلی بپرس.'''
    payload={'model':model,'messages':[{'role':'system','content':system+'\nداده‌های جست‌وجوی فعلی:\n'+json.dumps(context,ensure_ascii=False)}]+conversation[-10:],'temperature':0.2}
    req=urllib.request.Request(base+'/chat/completions',data=json.dumps(payload,ensure_ascii=False).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+key},method='POST')
    try:
        with urllib.request.urlopen(req,timeout=60) as r: data=json.loads(r.read().decode())
        answer=data['choices'][0]['message']['content']
        if user:
            con.execute('INSERT INTO chat_messages(session_id,role,content) VALUES (?,?,?)',(session_id,'user',user))
        con.execute('INSERT INTO chat_messages(session_id,role,content,products) VALUES (?,?,?,?)',(session_id,'assistant',answer,json.dumps(chat_products,ensure_ascii=False)))
        con.commit()
        return {'answer':answer,'model':model,'session_id':session_id,'products':chat_products},200
    except urllib.error.HTTPError as e:
        detail=e.read().decode(errors='replace')[:500]
        return {'error':f'خطا از سرویس مدل ({e.code}): {detail}'},502
    except Exception as e:
        return {'error':f'ارتباط با سرویس مدل برقرار نشد: {e}'},502

def now_iso():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()

def safe_int(v, default=None):
    try:
        if v is None or str(v).strip() == '': return default
        n=int(v); return n if n >= 0 else default
    except Exception: return default

def create_notifications(con,event_id,product_id,shop_id,stock_qty):
    if stock_qty is None or stock_qty <= 0: return 0
    subs=con.execute("SELECT * FROM buyer_subscriptions WHERE product_id=? AND status='active' AND (expires_at IS NULL OR expires_at>?)",(str(product_id),now_iso())).fetchall()
    offer=con.execute('SELECT o.price_toman,s.location_label,s.name FROM offers o JOIN shops s ON s.shop_id=o.shop_id WHERE o.product_id=? AND o.shop_id=? ORDER BY o.price_toman IS NULL,o.price_toman LIMIT 1',(str(product_id),str(shop_id))).fetchone()
    made=0
    for sub in subs:
        if sub['shop_id'] and str(sub['shop_id']) != str(shop_id): continue
        if sub['location_label'] and (not offer or sub['location_label'] not in (offer['location_label'] or '')): continue
        if sub['max_price'] is not None and (not offer or offer['price_toman'] is None or offer['price_toman'] > sub['max_price']): continue
        body=f"برای محصول انتخابی شما در {offer['name'] if offer else 'فروشگاه ثبت‌شده'} موجودی اعلام شده است. این کالا رزرو نشده؛ قبل از حرکت شرایط نهایی را بررسی کنید."
        delivery='pending' if sub['channel']=='browser' else 'not_configured'
        cur=con.execute('INSERT OR IGNORE INTO notifications(subscription_id,event_id,subscriber_key,channel,title,body,delivery_status,created_at) VALUES (?,?,?,?,?,?,?,?)',(sub['subscription_id'],event_id,sub['subscriber_key'],sub['channel'],'شرط موجودی برقرار شد',body,delivery,now_iso()))
        made += cur.rowcount
    return made

def record_inventory_update(con,payload):
    pid=str(payload.get('product_id','')).strip(); sid=str(payload.get('shop_id','')).strip()
    if not pid or not sid: return {'error':'product_id و shop_id لازم است'},400
    if not con.execute('SELECT 1 FROM products WHERE product_id=?',(pid,)).fetchone(): return {'error':'محصول پیدا نشد'},404
    if not con.execute('SELECT 1 FROM shops WHERE shop_id=?',(sid,)).fetchone(): return {'error':'فروشگاه پیدا نشد'},404
    reqid=str(payload.get('request_id') or uuid.uuid4().hex)
    dupe=con.execute('SELECT event_id FROM inventory_events WHERE request_id=?',(reqid,)).fetchone()
    if dupe: return {'ok':True,'idempotent':True,'event_id':dupe['event_id']},200
    typ=payload.get('event_type') or payload.get('action') or 'count'
    if typ not in ('count','sale','supply','unknown','confirm'): return {'error':'event_type نامعتبر'},400
    old=con.execute("SELECT * FROM inventory_records WHERE product_id=? AND shop_id=? AND variant_key='default'",(pid,sid)).fetchone()
    before=None if not old or old['stock_qty'] is None else int(old['stock_qty']); qty=safe_int(payload.get('qty')); baseline=safe_int(payload.get('baseline_qty')); now=now_iso(); cycle=(old['supply_cycle_id'] if old else None) or uuid.uuid4().hex
    if payload.get('expected_version') is not None and old and safe_int(payload.get('expected_version')) != int(old['version']): return {'error':'تعارض نسخه؛ اطلاعات در فاصلهٔ بازکردن فرم تغییر کرده است','conflict':True,'current_version':old['version'],'current_qty':old['stock_qty']},409
    if typ=='unknown': new=None
    elif typ=='sale':
        if before is None or qty is None or qty>before: return {'error':'تعارض: موجودی معتبر و qty لازم است'},409
        new=before-qty
    elif typ=='supply':
        if qty is None or qty<1: return {'error':'تأمین جدید باید qty مثبت داشته باشد'},400
        new=qty; cycle=uuid.uuid4().hex; baseline=baseline or qty
    else:
        if qty is None: return {'error':'برای شمارش جدید qty لازم است'},400
        new=qty
        if not old: baseline=baseline or qty
    if baseline is None and old: baseline=old['baseline_qty']
    if baseline is None and new is not None: baseline=new
    if old:
        inv=old['inventory_id']; ver=int(old['version'])+1
        con.execute('UPDATE inventory_records SET stock_qty=?,stock_source=?,stock_confirmed_at=?,baseline_qty=?,supply_cycle_id=?,version=?,updated_at=? WHERE inventory_id=?',(new,'seller_demo',now,baseline,cycle,ver,now,inv))
    else:
        con.execute("INSERT INTO inventory_records(product_id,shop_id,stock_qty,quantity_scope,stock_source,stock_confirmed_at,baseline_qty,supply_cycle_id,version,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",(pid,sid,new,'store_sellable','seller_demo',now,baseline,cycle,1,now)); inv=con.execute('SELECT last_insert_rowid()').fetchone()[0]; ver=1
    cur=con.execute('INSERT INTO inventory_events(request_id,inventory_id,event_type,qty,source,created_at,payload) VALUES (?,?,?,?,?,?,?)',(reqid,inv,typ,qty,'seller_demo',now,json.dumps(payload,ensure_ascii=False))); eid=cur.lastrowid
    threshold=baseline*0.25 if baseline is not None else None; alert=None
    if new==0: alert='sold_out'
    elif threshold is not None and threshold>=1 and ((before is not None and before>threshold and new<=threshold) or (before is None and new<=threshold)): alert='low_stock'
    if alert: con.execute('INSERT OR IGNORE INTO inventory_alerts(inventory_id,alert_type,cycle_id,created_at) VALUES (?,?,?,?)',(inv,alert,cycle,now))
    made=create_notifications(con,eid,pid,sid,new) if (new is not None and new > 0 and (before is not None and before <= 0 or typ=='supply')) else 0; con.commit()
    return {'ok':True,'event_id':eid,'inventory_id':inv,'version':ver,'stock_qty':new,'stock_status':'available' if new and new>0 else ('out_of_stock' if new==0 else 'unknown'),'alert':alert,'notifications_created':made,'evidence_status':'seller_demo','note':'رویداد دمو است؛ همگام‌سازی زنده، رزرو یا خرید نیست.'},200

def seller_inbox(con, shop_id=''):
    rows=con.execute('''SELECT a.*,i.product_id,i.shop_id,i.stock_qty,i.baseline_qty,i.stock_confirmed_at,i.stock_source,
        p.persian_title FROM inventory_alerts a JOIN inventory_records i ON i.inventory_id=a.inventory_id
        JOIN products p ON p.product_id=i.product_id WHERE (?='' OR i.shop_id=?) AND a.status IN ('open','snoozed')
        ORDER BY CASE a.alert_type WHEN 'sold_out' THEN 0 ELSE 1 END,a.created_at DESC LIMIT 100''',(shop_id,shop_id)).fetchall()
    reports=con.execute("SELECT * FROM discrepancy_reports WHERE (?='' OR shop_id=?) AND status IN ('registered','in_review','needs_info') ORDER BY created_at DESC LIMIT 100",(shop_id,shop_id)).fetchall()
    return {'alerts':[dict(r) for r in rows],'reports':[dict(r) for r in reports], 'note':'کارتابل دمو؛ اعلان بیرونی فعال نیست.'}

def review_discrepancy(con, payload):
    rid=safe_int(payload.get('report_id')); status=payload.get('status')
    allowed=('in_review','needs_info','confirmed','rejected','resolved')
    if not rid or status not in allowed: return {'error':'report_id و وضعیت معتبر لازم است'},400
    row=con.execute('SELECT * FROM discrepancy_reports WHERE report_id=?',(rid,)).fetchone()
    if not row: return {'error':'گزارش پیدا نشد'},404
    now=now_iso(); note=str(payload.get('review_note') or '').strip() or None
    con.execute('UPDATE discrepancy_reports SET status=?,review_note=?,seller_response=?,reviewed_at=?,reviewed_by=? WHERE report_id=?',(status,note,payload.get('seller_response'),now,str(payload.get('reviewed_by') or 'seller_demo'),rid))
    con.execute('INSERT INTO audit_log(request_id,actor,action,entity_type,entity_id,payload,created_at) VALUES (?,?,?,?,?,?,?)',(str(payload.get('request_id') or uuid.uuid4().hex),'seller_demo','review_discrepancy','discrepancy',str(rid),json.dumps(payload,ensure_ascii=False),now))
    con.commit(); return {'ok':True,'report_id':rid,'status':status,'note':'وضعیت گزارش ثبت شد؛ گزارش به‌تنهایی موجودی یا رتبه را تغییر نمی‌دهد.'},200

def save_appeal(con,payload):
    sid=str(payload.get('shop_id') or '').strip(); reason=str(payload.get('reason') or '').strip(); req=str(payload.get('request_id') or uuid.uuid4().hex)
    if not sid or not reason: return {'error':'shop_id و دلیل اعتراض لازم است'},400
    if not con.execute('SELECT 1 FROM shops WHERE shop_id=?',(sid,)).fetchone(): return {'error':'فروشگاه پیدا نشد'},404
    cur=con.execute('INSERT OR IGNORE INTO appeals(shop_id,report_id,reason,evidence,created_at,request_id) VALUES (?,?,?,?,?,?)',(sid,safe_int(payload.get('report_id')),reason,payload.get('evidence'),now_iso(),req)); con.commit()
    return {'ok':True,'appeal_id':cur.lastrowid,'status':'registered','note':'اعتراض ثبت شد و اثر عمومی منفی ایجاد نمی‌کند.'},200

class Handler(BaseHTTPRequestHandler):
    def send_json(self, obj, status=200):
        raw=json.dumps(obj,ensure_ascii=False).encode('utf-8')
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8'); self.send_header('Content-Length',str(len(raw))); self.end_headers(); self.wfile.write(raw)
    def do_POST(self):
        u=urlparse(self.path); con=db()
        try:
            try: body=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0')) or 0).decode())
            except Exception: self.send_json({'error':'درخواست JSON نامعتبر است'},400); return
            if u.path == '/api/chat':
                result,status=chat_answer(con,body.get('messages',[]),body.get('session_id')); self.send_json(result,status); return
            if u.path == '/api/notify/subscribe':
                key=str(body.get('subscriber_key') or '').strip(); pid=str(body.get('product_id') or '').strip(); channel=body.get('channel','browser')
                if not key or not pid or channel not in ('browser','sms') or not body.get('consent'):
                    self.send_json({'error':'شناسه، محصول، کانال و رضایت صریح لازم است'},400); return
                if not con.execute('SELECT 1 FROM products WHERE product_id=?',(pid,)).fetchone(): self.send_json({'error':'محصول پیدا نشد'},404); return
                dest=str(body.get('destination') or '').strip() or None
                if channel=='sms' and (not dest or not re.fullmatch(r'\+?\d{10,15}',dest)):
                    self.send_json({'error':'شمارهٔ پیامک معتبر لازم است'},400); return
                now=now_iso()
                existing=con.execute("SELECT subscription_id FROM buyer_subscriptions WHERE subscriber_key=? AND product_id=? AND channel=? AND status IN ('active','paused') ORDER BY subscription_id DESC LIMIT 1",(key,pid,channel)).fetchone()
                if existing:
                    self.send_json({'ok':True,'subscription_id':existing['subscription_id'],'existing':True,'channel':channel,'delivery_status':'browser_polling' if channel=='browser' else 'sms_adapter_not_configured'}); return
                cur=con.execute('''INSERT INTO buyer_subscriptions(subscriber_key,product_id,shop_id,channel,destination,max_price,location_label,min_stock,consent_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)''',(key,pid,body.get('shop_id') or None,channel,dest,body.get('max_price') or None,body.get('location_label') or None,max(1,safe_int(body.get('min_stock'),1)),now,now,now)); con.commit()
                self.send_json({'ok':True,'subscription_id':cur.lastrowid,'channel':channel,'delivery_status':'browser_polling' if channel=='browser' else 'sms_adapter_not_configured','note':'اعلان فقط پس از رویداد معتبر موجودی ساخته می‌شود؛ کالا رزرو نمی‌شود.'}); return
            if u.path == '/api/inventory/update':
                result,status=record_inventory_update(con,body); self.send_json(result,status); return
            if u.path == '/api/inventory/confirm':
                body['event_type']='confirm'; result,status=record_inventory_update(con,body); self.send_json(result,status); return
            if u.path == '/api/seller/review':
                result,status=review_discrepancy(con,body); self.send_json(result,status); return
            if u.path == '/api/seller/appeal':
                result,status=save_appeal(con,body); self.send_json(result,status); return
            if u.path == '/api/notify/action':
                sid=safe_int(body.get('subscription_id')); action=body.get('action'); key=str(body.get('subscriber_key') or '')
                if action not in ('pause','cancel','resume'): self.send_json({'error':'action نامعتبر'},400); return
                status={'pause':'paused','cancel':'cancelled','resume':'active'}[action]
                cur=con.execute('UPDATE buyer_subscriptions SET status=?,updated_at=? WHERE subscription_id=? AND subscriber_key=?',(status,now_iso(),sid,key)); con.commit(); self.send_json({'ok':cur.rowcount>0,'status':status}); return
            if u.path == '/api/notify/read':
                key=str(body.get('subscriber_key') or ''); nid=safe_int(body.get('notification_id'))
                cur=con.execute('UPDATE notifications SET read_at=? WHERE notification_id=? AND subscriber_key=?',(now_iso(),nid,key)); con.commit(); self.send_json({'ok':cur.rowcount>0}); return
            if u.path == '/api/discrepancies':
                pid=str(body.get('product_id') or '').strip(); issue=body.get('issue_type'); key=str(body.get('request_id') or uuid.uuid4().hex)
                if not pid or issue not in ('out_of_stock','price_mismatch','variant_mismatch','purchase_condition'): self.send_json({'error':'گزارش ناقص است'},400); return
                cur=con.execute('INSERT OR IGNORE INTO discrepancy_reports(product_id,shop_id,issue_type,note,created_at,request_id) VALUES (?,?,?,?,?,?)',(pid,body.get('shop_id'),issue,body.get('note'),now_iso(),key)); con.commit(); self.send_json({'ok':True,'report_id':cur.lastrowid,'status':'registered','note':'گزارش خصوصی است و موجودی یا رتبه را خودکار تغییر نمی‌دهد.'}); return
            self.send_json({'error':'not found'},404)
        except Exception as e: self.send_json({'error':'خطای داخلی','detail':str(e)},500)
        finally: con.close()
    def do_GET(self):
        u=urlparse(self.path); p=u.path; q=parse_qs(u.query); con=db()
        try:
            if p=='/api/notifications':
                key=q.get('subscriber_key',[''])[0]
                rows=con.execute('SELECT * FROM notifications WHERE subscriber_key=? ORDER BY notification_id DESC LIMIT 50',(key,)).fetchall()
                self.send_json({'items':[dict(r) for r in rows]}); return
            if p=='/api/notify/subscriptions':
                key=q.get('subscriber_key',[''])[0]
                rows=con.execute('SELECT subscription_id,product_id,shop_id,channel,max_price,location_label,min_stock,status,consent_at,expires_at FROM buyer_subscriptions WHERE subscriber_key=? ORDER BY subscription_id DESC',(key,)).fetchall()
                self.send_json({'items':[dict(r) for r in rows]}); return
            if p=='/api/seller/inbox':
                shop_id=q.get('shop_id',[''])[0]
                self.send_json(seller_inbox(con, shop_id)); return
            if p=='/api/seller/policy':
                rows=con.execute('SELECT policy_key,policy_value,version,updated_at FROM policy_config ORDER BY policy_key').fetchall()
                self.send_json({'items':[dict(r) for r in rows],'note':'مقادیر سیاست نسخه‌دار و قابل تنظیم‌اند؛ سیاست نهایی محسوب نمی‌شوند.'}); return
            if p=='/api/health': self.send_json({'ok':True,'db':DB.name,'counts':summary(con)}); return
            if p=='/api/search':
                brands=q.get('brand',[]); payments=q.get('payment',[])
                result=search_products(con,q.get('q',[''])[0],q.get('category',[''])[0],q.get('limit',[24])[0],
                                       brands,q.get('warranty',['0'])[0]=='1',payments,
                                       q.get('fast_delivery',['0'])[0]=='1',
                                       q.get('sort',['relevance'])[0],
                                       q.get('lat',[None])[0],q.get('lon',[None])[0])
                self.send_json(result); return
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
            if p=='/api/map-points':
                self.send_json({'items':map_points(con, q.get('q',[''])[0], q.get('location',[''])[0]), 'evidence_status':'registered', 'note':'نقاط واقعی فروشگاه‌های دارای آفر در snapshot؛ موجودی امروز تأیید نشده است.'}); return
            if p=='/api/top-categories':
                # Real category names ordered by how many products actually have offers,
                # so the header strip / quick filter never offers an empty category.
                try: n = int(q.get('limit',['12'])[0] or 12)
                except Exception: n = 12
                n = min(max(n,1),40)
                rows = con.execute('''
                    SELECT p.category_name,
                           COUNT(*) AS products,
                           SUM(CASE WHEN oc.cnt IS NOT NULL THEN 1 ELSE 0 END) AS with_offers
                    FROM products p
                    LEFT JOIN (SELECT product_id, COUNT(*) AS cnt FROM offers GROUP BY product_id) oc
                           ON oc.product_id = p.product_id
                    WHERE p.category_name IS NOT NULL AND p.category_name != ''
                    GROUP BY p.category_name
                    ORDER BY with_offers DESC, products DESC
                    LIMIT ?
                ''', (n,)).fetchall()
                self.send_json({'items':[{'name':r[0],'products':r[1],'with_offers':r[2]} for r in rows]}); return
            if p=='/api/suggest':
                # Bounded, snapshot-grounded typeahead: real categories, brands and product
                # titles that exist in the DB. Never invents a suggestion.
                term = (q.get('q',[''])[0] or '').strip()
                if len(term) < 2:
                    self.send_json({'items':[]}); return
                like = f'%{term}%'
                out = []
                cats = con.execute('''SELECT category_name, COUNT(*) n FROM products
                    WHERE category_name LIKE ? GROUP BY category_name ORDER BY n DESC LIMIT 4''', (like,)).fetchall()
                for r in cats: out.append({'type':'category','label':r[0],'hint':f'{r[1]:,} محصول','value':r[0]})
                brands = con.execute('''SELECT brand_name, COUNT(*) n FROM products
                    WHERE brand_name LIKE ? AND brand_name != 'null' GROUP BY brand_name ORDER BY n DESC LIMIT 3''', (like,)).fetchall()
                for r in brands: out.append({'type':'brand','label':r[0],'hint':f'برند · {r[1]:,} محصول','value':r[0]})
                prods = con.execute('''SELECT persian_title, product_id FROM products
                    WHERE persian_title LIKE ? ORDER BY LENGTH(persian_title) LIMIT 5''', (like,)).fetchall()
                for r in prods: out.append({'type':'product','label':r[0][:70],'hint':'محصول','value':r[0]})
                self.send_json({'items':out[:10]}); return
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
                    ext=fp.suffix.lower().lstrip('.')
                    typ={'css':'text/css','js':'application/javascript','png':'image/png','jpg':'image/jpeg',
                         'jpeg':'image/jpeg','svg':'image/svg+xml','gif':'image/gif','woff2':'font/woff2','woff':'font/woff'}.get(ext,'application/octet-stream')
                    data=fp.read_bytes(); self.send_response(200); self.send_header('Content-Type',typ); self.send_header('Content-Length',str(len(data))); self.end_headers(); self.wfile.write(data); return
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
