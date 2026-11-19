"""Owner-approved DQ200 prices. The initial inspect mode is strictly read-only."""
from __future__ import annotations
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit, urlunsplit
import requests
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REQUEST = ROOT / 'data/dq200_price_change_request_20261120.txt'
TARGETS = {'0am325025HXE': 71310, '0am325025HXF': 64500, '0am325025HXG': 71310, '0am325025HXK': 71310}
KEEP = {'0am325025HX': 64500}
AVITO_IDS = {'0am325025HX': 8294158112, '0am325025HXE': 8396041349, '0am325025HXF': 8347440081, '0am325025HXG': 8548835790, '0am325025HXK': 8396258649}
NEW_PRICE = 68500
UA = {'User-Agent': 'Mozilla/5.0 LEOPARTS price verification'}

def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()

def digest(b):
    return hashlib.sha256(b).hexdigest()

def git_blob(b):
    return hashlib.sha1(b'blob ' + str(len(b)).encode() + b'\0' + b).hexdigest()

def read_book(path, sheet, sku_col, price_cols):
    wb = load_workbook(path, read_only=True, data_only=False)
    ws = wb[sheet]
    result = {}
    for row in ws.iter_rows(values_only=True):
        key = str(row[sku_col - 1] or '').strip()
        if key in set(TARGETS) | set(KEEP):
            if key in result:
                raise RuntimeError('duplicate SKU in workbook')
            result[key] = {name: row[col - 1] if col <= len(row) else None for name, col in price_cols.items()}
    wb.close()
    if set(result) != set(TARGETS) | set(KEEP):
        raise RuntimeError('target SKU missing from workbook')
    return result

def fetch_site_records():
    import site_links
    parts = set()
    recids = set()
    for path in ('/catalog', '/'):
        r = requests.get('https://leoparts-akpp.ru' + path, headers=UA, timeout=35)
        if r.ok:
            found_parts, found_recids = site_links._store_parts(r.text)
            parts |= found_parts
            recids |= found_recids
    if not parts:
        raise RuntimeError('no Tilda store blocks found')
    products = {}
    for part in sorted(parts):
        slice_no = 1
        for _ in range(10):
            r = requests.get('https://store.tildaapi.com/api/getproductslist/', headers=UA, timeout=35,
                params={'storepartuid': part, 'recid': next(iter(sorted(recids)), ''), 'c': '', 'getparts': 'true', 'getoptions': 'true', 'slice': slice_no, 'size': 100})
            r.raise_for_status()
            data = r.json()
            for p in data.get('products') or []:
                if p.get('sku'):
                    uid = str(p.get('uid') or p.get('sku'))
                    products[uid] = p
            if not data.get('nextslice') or not data.get('products'):
                break
            next_no = int(data['nextslice'])
            if next_no <= slice_no:
                raise RuntimeError('invalid Tilda pagination')
            slice_no = next_no
    result = {}
    for sku in set(TARGETS) | set(KEEP):
        matches = [p for p in products.values() if str(p.get('sku') or '').casefold() == sku.casefold()]
        if len(matches) != 1:
            raise RuntimeError('Tilda SKU missing or ambiguous: ' + sku)
        p = matches[0]
        result[sku] = {key: p.get(key) for key in ('uid', 'sku', 'externalid', 'external_id', 'title', 'price', 'priceold', 'quantity', 'inv', 'url', 'descr', 'text', 'gallery', 'editions') if key in p}
        result[sku]['response_keys'] = sorted(p)
    all_prices = {uid: {'sku': p.get('sku'), 'price': p.get('price')} for uid, p in products.items()}
    return {'checked_at_utc': now(), 'product_count': len(products), 'store_parts': sorted(parts), 'targets': result, 'all_prices': all_prices}

def avito_preflight():
    import avito_client
    result = {'checked_at_utc': now()}
    try:
        items = avito_client.list_all_items(per_page=100, max_pages=10)
        result['targets'] = {}
        wanted = {int(v): k for k, v in AVITO_IDS.items()}
        for item in items:
            if int(item.get('id') or 0) in wanted:
                sku = wanted[int(item['id'])]
                result['targets'][sku] = {k: item.get(k) for k in ('id', 'title', 'price', 'status', 'url')}
        result['total_items_read'] = len(items)
    except Exception as exc:
        result['items_error_type'] = type(exc).__name__
    try:
        profile = avito_client._request('GET', '/autoload/v2/profile')
        result['profile'] = {k: profile.get(k) for k in ('autoload_enabled', 'schedule') if k in profile}
        feeds = profile.get('feeds_data') or []
        result['profile']['feeds_data'] = []
        for feed in feeds:
            if not isinstance(feed, dict):
                continue
            clean = {k: feed[k] for k in ('feed_name', 'name') if k in feed}
            for key in ('feed_url', 'url'):
                if key in feed:
                    parsed = urlsplit(str(feed[key]))
                    clean[key] = urlunsplit((parsed.scheme, parsed.hostname or '', parsed.path, '', ''))
            result['profile']['feeds_data'].append(clean)
        result['profile_keys'] = sorted(profile)
    except Exception as exc:
        result['profile_error_type'] = type(exc).__name__
    try:
        status = avito_client._request('GET', '/autoload/v4/uploads/current')
        result['current_upload'] = {k: status.get(k) for k in ('id', 'upload_id', 'status', 'startedAt', 'finishedAt', 'created_at', 'updated_at', 'started_at', 'finished_at') if k in status}
        result['current_upload_keys'] = sorted(status)
    except Exception as exc:
        result['current_upload_error_type'] = type(exc).__name__
    return result

def api_schema():
    report = {'mode': 'api_schema', 'started_at_utc': now(), 'mutations': [], 'schemas': {}, 'errors': []}
    wanted = {'/core/v1/items/{item_id}/update_price', '/autoload/v1/upload', '/autoload/v4/uploads/current/items', '/autoload/v4/uploads/current'}
    for name in ('item', 'autoload'):
        try:
            r = requests.get('https://www.avito.ru/web/1/openapi/info/' + name, headers=UA, timeout=40)
            item = {'http_status': r.status_code}
            if r.ok:
                data = r.json()
                item['root_keys'] = sorted(data) if isinstance(data, dict) else []
                def walk(value):
                    if isinstance(value, str):
                        if value.lstrip().startswith('{'):
                            try:
                                yield from walk(json.loads(value))
                            except (ValueError, TypeError):
                                pass
                    elif isinstance(value, dict):
                        if 'paths' in value and isinstance(value['paths'], dict):
                            yield value
                        for val in value.values():
                            yield from walk(val)
                    elif isinstance(value, list):
                        for val in value:
                            yield from walk(val)
                specs = list(walk(data))
                item['spec_count'] = len(specs)
                for spec in specs:
                    paths = {k: v for k, v in spec['paths'].items() if k in wanted}
                    item.setdefault('selected_paths', {}).update(paths)
                    item['path_names'] = list(spec['paths'])
                    if paths:
                        item['components'] = spec.get('components', {})
                        item['definitions'] = spec.get('definitions', {})
            report['schemas'][name] = item
        except Exception as exc:
            report['errors'].append({'source': name, 'type': type(exc).__name__})
    report['finished_at_utc'] = now()
    (ROOT / 'data/dq200_price_api_schema_20261120.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'mode': 'api_schema', 'sources': {k: v.get('http_status') for k, v in report['schemas'].items()}, 'errors': len(report['errors'])}))
    return 0

def inspect():
    report = {'mode': 'inspect', 'started_at_utc': now(), 'mutations': [], 'errors': []}
    try:
        report['workbooks'] = {}
        for path, sheet, sku_col, price_cols in (
            ('data/avito_site_price_overrides.xlsx', 'Цены Avito и сайт', 1, {'avito': 2, 'fixed': 3, 'site_explicit': 4}),
            ('data/avito_feed.xlsx', 'Объявления', 4, {'avito': 34}),
        ):
            p = ROOT / path
            report['workbooks'][path] = {'git_blob_sha': git_blob(p.read_bytes()), 'sha256': digest(p.read_bytes()), 'targets': read_book(p, sheet, sku_col, price_cols)}
        report['site'] = fetch_site_records()
        report['avito'] = avito_preflight()
        report['tilda_credentials_present'] = all(os.getenv(k) for k in ('TILDA_COMMERCEML_URL', 'TILDA_COMMERCEML_USER', 'TILDA_COMMERCEML_PASSWORD'))
        report['status'] = 'inspected_no_changes'
    except Exception as exc:
        report['status'] = 'inspection_failed'
        report['errors'].append({'type': type(exc).__name__, 'message': str(exc) if not isinstance(exc, requests.RequestException) else 'HTTP request failed'})
    report['finished_at_utc'] = now()
    (ROOT / 'data/dq200_price_preflight_20261120.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': report['status'], 'mutations': 0, 'site_products': report.get('site', {}).get('product_count'), 'errors': len(report['errors'])}))
    return 0 if not report['errors'] else 1

def main():
    request = json.loads(REQUEST.read_text())
    if request.get('mode') == 'inspect':
        return inspect()
    if request.get('mode') == 'api_schema':
        return api_schema()
    raise RuntimeError('Requested mode has not been installed')

if __name__ == '__main__':
    raise SystemExit(main())
