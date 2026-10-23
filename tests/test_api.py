import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://127.0.0.1:8787'


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=20) as r:
        return json.loads(r.read().decode())


def main():
    health = get('/api/health')
    assert health['ok'] is True
    assert health['counts']['products'] == 225903
    assert health['counts']['offers'] == 331506
    assert health['counts']['shops'] == 6476

    result = get('/api/search?q=%D8%B3%DB%8C%D9%85%20%D9%88%20%DA%A9%D8%A7%D8%A8%D9%84')
    assert result['items'], 'search should return products'
    pid = result['items'][0]['product_id']

    detail = get('/api/products/' + pid)
    assert detail['product']['product_id'] == pid
    assert 'offers' in detail
    if detail['offers']:
        offer = detail['offers'][0]
        assert offer['stock_status'] == 'unknown'
        assert offer['seller_response_status'] == 'unknown'
        assert 'contacts' not in offer

    meta = get('/api/meta')
    assert meta['locations']
    print('PASS: health, search, product offers, unknown-state policy, no-contact exposure')


if __name__ == '__main__':
    main()
