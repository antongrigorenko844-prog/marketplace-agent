"""Read-only verification. Output is captured and encrypted, never public."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import requests
import avito_client as av
import ozon_client as oz

ids = ['0BH325183B', '0BH325', 'korpus-filtra-0bh325159']
for p in oz.get_product_info_list(ids):
    print('OZON', json.dumps({k: p.get(k) for k in ['offer_id', 'stocks', 'is_archived']}, ensure_ascii=False), flush=True)
for p in av.list_all_items():
    title = p.get('title', '').lower()
    if 'корпус' in title and 'dq500' in title:
        print('AVITO', json.dumps({k: p.get(k) for k in ['id', 'title', 'status', 'url']}, ensure_ascii=False), flush=True)
for p in av.get_autoload_report_items().get('items', []):
    if any(s in json.dumps(p, ensure_ascii=False) for s in ids):
        print('AVITO TARGET', json.dumps(p, ensure_ascii=False)[:1600], flush=True)
sl = None
seen = set()
for _ in range(20):
    params = {'storepartuid': '419144347374', 'recid': '1928468643', 'size': 100, 'c': int(time.time() * 1000)}
    if sl:
        params['slice'] = sl
    r = requests.get('https://store.tildaapi.com/api/getproductslist/', params=params, timeout=60)
    r.raise_for_status()
    body = r.json()
    page = body.get('products', [])
    fresh = [p for p in page if p['uid'] not in seen]
    for p in fresh:
        if p.get('sku') in ids:
            print('TILDA', json.dumps({k: p.get(k) for k in ['sku', 'quantity', 'url']}, ensure_ascii=False), flush=True)
    seen.update(p['uid'] for p in fresh)
    sl = body.get('nextslice')
    if not sl or not fresh:
        break
print('CHECK COMPLETE', flush=True)
