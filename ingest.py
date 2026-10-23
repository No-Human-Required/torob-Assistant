import csv
import json
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT.parent / 'torob-dataset' / 'torob-dataset'
DB = ROOT / 'torob.db'

csv.field_size_limit(10**9)


def connect():
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    con.execute('PRAGMA journal_mode=WAL')
    con.execute('PRAGMA synchronous=NORMAL')
    return con


def ensure_schema(con):
    con.executescript('''
    CREATE TABLE IF NOT EXISTS products (
      product_id TEXT PRIMARY KEY, brand_name TEXT, category_name TEXT,
      image_urls TEXT, model TEXT, persian_title TEXT, specs TEXT
    );
    CREATE TABLE IF NOT EXISTS shops (
      shop_id TEXT PRIMARY KEY, address TEXT, category_names TEXT, instagram TEXT,
      lat REAL, location_label TEXT, lon REAL, name TEXT, payment_options TEXT,
      supports_fast_delivery TEXT, website_url TEXT, weekly_hours TEXT
    );
    CREATE TABLE IF NOT EXISTS offers (
      offer_id TEXT PRIMARY KEY, description TEXT, last_price_changed_at TEXT,
      price_toman REAL, product_id TEXT, seller_title TEXT, shop_id TEXT, warranty TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_products_title ON products(persian_title);
    CREATE INDEX IF NOT EXISTS idx_products_category ON products(category_name);
    CREATE INDEX IF NOT EXISTS idx_offers_product ON offers(product_id);
    CREATE INDEX IF NOT EXISTS idx_offers_shop ON offers(shop_id);
    CREATE INDEX IF NOT EXISTS idx_offers_product_shop ON offers(product_id, shop_id);
    CREATE INDEX IF NOT EXISTS idx_shops_location ON shops(location_label);
    CREATE INDEX IF NOT EXISTS idx_shops_geo ON shops(lat, lon);
    ''')


def load_table(con, table, filename, columns, parser=None):
    if con.execute(f'SELECT 1 FROM {table} LIMIT 1').fetchone():
        return 0
    path = DATA / filename
    if not path.exists():
        raise FileNotFoundError(path)
    placeholders = ','.join('?' for _ in columns)
    sql = f'INSERT OR IGNORE INTO {table} ({",".join(columns)}) VALUES ({placeholders})'
    count = 0
    batch = []
    with path.open(encoding='utf-8', newline='') as f:
        for row in csv.DictReader(f):
            values = [row.get(c) for c in columns]
            if parser:
                values = parser(values)
            batch.append(values)
            if len(batch) >= 2000:
                con.executemany(sql, batch)
                count += len(batch)
                batch.clear()
        if batch:
            con.executemany(sql, batch)
            count += len(batch)
    return count


def offer_values(values):
    values[3] = float(values[3]) if values[3] not in (None, '', 'null') else None
    return values


def build():
    con = connect()
    ensure_schema(con)
    counts = {}
    with con:
        counts['products'] = load_table(con, 'products', 'products.csv',
            ['product_id','brand_name','category_name','image_urls','model','persian_title','specs'])
        counts['shops'] = load_table(con, 'shops', 'shops.csv',
            ['shop_id','address','category_names','instagram','lat','location_label','lon','name','payment_options','supports_fast_delivery','website_url','weekly_hours'])
        counts['offers'] = load_table(con, 'offers', 'offers.csv',
            ['offer_id','description','last_price_changed_at','price_toman','product_id','seller_title','shop_id','warranty'], offer_values)
    con.close()
    return counts


if __name__ == '__main__':
    print(json.dumps(build(), ensure_ascii=False))
