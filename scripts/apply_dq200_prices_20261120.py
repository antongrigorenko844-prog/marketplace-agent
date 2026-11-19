"""Owner-approved DQ200 prices. The initial inspect mode is strictly read-only."""
from __future__ import annotations
import datetime as dt
import base64
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import zipfile
import xml.etree.ElementTree as ET
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
MANIFEST = ROOT / 'data/dq200_price_change_manifest_20261120.json'
REPO = 'antongrigorenko844-prog/marketplace-agent'
STABLE_AVITO_URL = 'https://github.com/' + REPO + '/releases/download/product-photos/avito_feed.xlsx'
WORKBOOK_EDITS = {
    'data/avito_site_price_overrides.xlsx': ('xl/worksheets/sheet1.xml', ['B31', 'C31', 'D31', 'B32', 'D32', 'B33', 'C33', 'D33', 'B34', 'C34', 'D34']),
    'data/avito_feed.xlsx': ('xl/worksheets/sheet2.xml', ['AH31', 'AH32', 'AH33', 'AH34']),
}

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

def fetch_site_records(edition_price_baseline=None):
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
                params={'storepartuid': part, 'recid': next(iter(sorted(recids)), ''), 'c': str(int(time.time() * 1000)), 'getparts': 'true', 'getoptions': 'true', 'slice': slice_no, 'size': 100})
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
    stable_fields = ('sku', 'externalid', 'external_id', 'title', 'descr', 'text', 'gallery', 'editions', 'quantity', 'inv', 'partuids', 'characteristics', 'json_options', 'pack_label', 'pack_m', 'pack_x', 'pack_y', 'pack_z', 'unit', 'url', 'priceold')
    all_prices = {}
    for uid, p in products.items():
        projection = {k: p.get(k) for k in stable_fields}
        row = {'sku': p.get('sku'), 'price': p.get('price'), 'non_price_sha256': digest(json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())}
        sku = str(p.get('sku') or '')
        if edition_price_baseline and sku in TARGETS:
            original_editions = {str(x.get('uid')): x for x in edition_price_baseline[sku].get('editions', [])}
            normalized = json.loads(json.dumps(projection))
            for edition in normalized.get('editions') or []:
                old = original_editions.get(str(edition.get('uid')))
                if old is not None and edition.get('sku') == old.get('sku') == sku and float(edition.get('price') or 0) == NEW_PRICE:
                    edition['price'] = old.get('price')
            row['non_price_sha256_with_baseline_edition_prices'] = digest(json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode())
        all_prices[uid] = row
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

def run_git(*args):
    proc = subprocess.run(['git', *args], cwd=ROOT, text=True, capture_output=True)
    if proc.returncode:
        raise RuntimeError('Git operation failed: ' + args[0])
    return proc.stdout.strip()

def commit_paths(paths, message):
    run_git('config', 'user.name', 'github-actions[bot]')
    run_git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    existing = [p for p in paths if (ROOT / p).is_file()]
    run_git('add', '--', *existing)
    staged = set(run_git('diff', '--cached', '--name-only').splitlines())
    if not staged:
        return run_git('rev-parse', 'HEAD')
    if not staged <= set(paths):
        raise RuntimeError('Unexpected staged path')
    run_git('commit', '-m', message)
    run_git('push', 'origin', 'HEAD:main')
    return run_git('rev-parse', 'HEAD')

def validate_workbook_delta(before, after, allowed_cells, worksheet_part):
    with zipfile.ZipFile(io.BytesIO(before)) as a, zipfile.ZipFile(io.BytesIO(after)) as b:
        if a.namelist() != b.namelist():
            raise RuntimeError('Workbook package structure changed')
        for name in a.namelist():
            old, new = a.read(name), b.read(name)
            if name != worksheet_part:
                if old != new:
                    raise RuntimeError('Unrelated workbook part changed: ' + name)
                continue
            def masked(data, is_after):
                text = data.decode('utf-8')
                for cell in allowed_cells:
                    pat = r'<c\b[^>]*\br="' + re.escape(cell) + r'"[^>]*(?:/>|>.*?</c>)'
                    text, count = re.subn(pat, '', text, flags=re.S)
                    if count not in ((1,) if is_after else (0, 1)):
                        raise RuntimeError('Missing or ambiguous edited cell: ' + cell)
                return text
            if masked(old, False) != masked(new, True):
                raise RuntimeError('Unrelated worksheet XML changed')

def gh(method, path, **kwargs):
    token = os.environ.get('GH_TOKEN', '')
    if not token:
        raise RuntimeError('GitHub publication token unavailable')
    url = path if path.startswith('https://') else 'https://api.github.com/repos/' + REPO + path
    headers = {'Authorization': 'Bearer ' + token, 'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28'}
    headers.update(kwargs.pop('headers', {}))
    r = requests.request(method, url, headers=headers, timeout=90, **kwargs)
    if not r.ok:
        raise RuntimeError('GitHub publication HTTP ' + str(r.status_code))
    return r

def get_avito_asset():
    release = gh('GET', '/releases/tags/product-photos').json()
    found = [a for a in release.get('assets', []) if a.get('name') == 'avito_feed.xlsx']
    if len(found) != 1:
        raise RuntimeError('Current Avito publication asset not unique')
    asset = found[0]
    r = requests.get(asset['browser_download_url'], params={'price_read': str(int(time.time()))}, timeout=90)
    if not r.ok:
        raise RuntimeError('Current Avito feed download failed')
    return release, asset, r.content

def rename_asset(asset_id, name):
    path = '/releases/assets/' + str(asset_id)
    try:
        gh('PATCH', path, json={'name': name})
    except Exception:
        actual = gh('GET', path).json()
        if actual.get('name') != name:
            raise

def publish_avito_asset(new_bytes, report):
    release, old, old_bytes = get_avito_asset()
    evidence = {'old_asset_id': old['id'], 'old_sha256': digest(old_bytes), 'new_sha256': digest(new_bytes)}
    report['avito_feed_publication'] = evidence
    if old_bytes == new_bytes:
        evidence['status'] = 'already_published_verified'
        return
    expected = json.loads(MANIFEST.read_text())['files']['data/avito_feed.xlsx']['before_sha256']
    if digest(old_bytes) != expected:
        raise RuntimeError('Published Avito feed changed since approved snapshot')
    suffix = digest(new_bytes)[:10]
    temporary_name = 'avito_feed.price_20261120_' + suffix + '.xlsx'
    old_backup_name = 'avito_feed.previous_20261120_' + str(old['id']) + '.xlsx'
    upload_url = release['upload_url'].split('{', 1)[0]
    uploaded = gh('POST', upload_url, params={'name': temporary_name}, data=new_bytes,
        headers={'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}).json()
    evidence['new_asset_id'] = uploaded['id']
    check = requests.get(uploaded['browser_download_url'], timeout=90)
    if not check.ok or check.content != new_bytes:
        raise RuntimeError('Staged Avito feed failed byte verification')
    rename_asset(old['id'], old_backup_name)
    try:
        rename_asset(uploaded['id'], 'avito_feed.xlsx')
    except Exception:
        rename_asset(old['id'], 'avito_feed.xlsx')
        raise
    verified = False
    for attempt in range(5):
        check = requests.get(STABLE_AVITO_URL, params={'price_revision': suffix, 'check': attempt}, timeout=90)
        if check.ok and check.content == new_bytes:
            verified = True
            break
        time.sleep(4)
    if not verified:
        evidence['status'] = 'asset_replaced_public_readback_pending'
        raise RuntimeError('Stable Avito feed readback not yet confirmed')
    evidence['status'] = 'published_and_bytes_verified'
    gh('DELETE', '/releases/assets/' + str(old['id']))
    import photo_host
    photo_host._remember_upload('avito_feed.xlsx', suffix, STABLE_AVITO_URL + '?v=' + suffix)

def tilda_price_payloads(before):
    from xml.sax.saxutils import escape
    import tilda_commerceml as tc
    stamp = dt.datetime.now().strftime('%Y-%m-%dT%H:%M:%S')
    prefix = '<?xml version="1.0" encoding="UTF-8"?>\n<КоммерческаяИнформация ВерсияСхемы="2.05" ДатаФормирования="' + stamp + '">'
    import_parts = [prefix, '<Каталог СодержитТолькоИзменения="true"><Ид>' + tc.CATALOG_ID + '</Ид><ИдКлассификатора>' + tc.CLASSIFIER_ID + '</ИдКлассификатора><Товары>']
    for sku in TARGETS:
        item = before['targets'][sku]
        if item.get('externalid') != sku or item.get('sku') != sku:
            raise RuntimeError('Tilda external ID mismatch')
        import_parts.append('<Товар><Ид>' + escape(sku) + '</Ид><Артикул>' + escape(sku) + '</Артикул><Наименование>' + escape(str(item['title'])) + '</Наименование></Товар>')
    import_parts.append('</Товары></Каталог></КоммерческаяИнформация>')
    import_xml = '\n'.join(import_parts)
    parts = [prefix, '<ПакетПредложений СодержитТолькоИзменения="true">', '<Ид>' + tc.CATALOG_ID + '#offers</Ид>', '<ИдКаталога>' + tc.CATALOG_ID + '</ИдКаталога>', '<ИдКлассификатора>' + tc.CLASSIFIER_ID + '</ИдКлассификатора>', '<ТипыЦен><ТипЦены><Ид>' + tc.PRICE_TYPE_ID + '</Ид><Наименование>Розничная</Наименование></ТипЦены></ТипыЦен><Предложения>']
    for sku in TARGETS:
        item = before['targets'][sku]
        if item.get('externalid') != sku or item.get('sku') != sku:
            raise RuntimeError('Tilda external ID mismatch')
        parts.append('<Предложение><Ид>' + escape(sku) + '</Ид><Артикул>' + escape(sku) + '</Артикул><Наименование>' + escape(str(item['title'])) + '</Наименование><Цены><Цена><ИдТипаЦены>' + tc.PRICE_TYPE_ID + '</ИдТипаЦены><ЦенаЗаЕдиницу>68500</ЦенаЗаЕдиницу><Валюта>RUB</Валюта><Единица>шт</Единица><Коэффициент>1</Коэффициент></Цена></Цены></Предложение>')
    parts.append('</Предложения></ПакетПредложений></КоммерческаяИнформация>')
    offers_xml = '\n'.join(parts)
    assert not any(tag in import_xml + offers_xml for tag in ('<Количество>', '<Группы>', '<Описание>', '<Картинка>'))
    assert {x.findtext('Ид') for x in ET.fromstring(import_xml).findall('./Каталог/Товары/Товар')} == set(TARGETS)
    assert len(ET.fromstring(offers_xml).findall('./ПакетПредложений/Предложения/Предложение')) == 4
    return import_xml, offers_xml

def publish_tilda_prices(before, report):
    import tilda_commerceml as tc
    if all(float(before['targets'][sku]['price']) == NEW_PRICE for sku in TARGETS):
        report['tilda_publication'] = {'status': 'already_correct'}
        return
    import_xml, offers_xml = tilda_price_payloads(before)
    event = {'status': 'started', 'target_skus': list(TARGETS), 'import_products': 4, 'offers': 4, 'steps': [], 'payload_sha256': {'import': digest(import_xml.encode()), 'offers': digest(offers_xml.encode())}}
    report['tilda_publication'] = event
    session = requests.Session()
    session.auth = (os.environ['TILDA_COMMERCEML_USER'], os.environ['TILDA_COMMERCEML_PASSWORD'])
    base_url = os.environ['TILDA_COMMERCEML_URL']
    def check(r, step, allowed=('success',)):
        if r.status_code != 200:
            raise RuntimeError('Tilda ' + step + ' HTTP ' + str(r.status_code))
        first = r.text.strip().splitlines()[0].strip().lower() if r.text.strip() else ''
        if first not in allowed:
            raise RuntimeError('Tilda ' + step + ' response ' + first[:40])
        return first
    auth = session.get(tc._url(base_url, type='catalog', mode='checkauth'), timeout=60)
    check(auth, 'checkauth')
    lines = auth.text.strip().splitlines()
    if len(lines) >= 3 and lines[1].strip() and lines[2].strip() and lines[1].strip() not in session.cookies:
        session.cookies.set(lines[1].strip(), lines[2].strip())
    init = session.get(tc._url(base_url, type='catalog', mode='init'), timeout=60)
    if init.status_code != 200 or init.text.lower().startswith(('failure', 'error')):
        raise RuntimeError('Tilda init rejected')
    for name, body in (('import0_1.xml', import_xml), ('offers0_1.xml', offers_xml)):
        r = session.post(tc._url(base_url, type='catalog', mode='file', filename=name), data=body.encode(), headers={'Content-Type': 'application/octet-stream'}, timeout=90)
        check(r, 'file')
        event['steps'].append({'step': 'file', 'filename': name, 'status': 'success'})
    for name in ('import0_1.xml', 'offers0_1.xml'):
        deadline = time.monotonic() + 240
        while True:
            r = session.get(tc._url(base_url, type='catalog', mode='import', filename=name), timeout=90)
            state = check(r, 'import', ('success', 'progress'))
            if state == 'success':
                event['steps'].append({'step': 'import', 'filename': name, 'status': state})
                break
            if time.monotonic() > deadline:
                raise RuntimeError('Tilda import still processing')
            time.sleep(4)
    event['status'] = 'accepted_waiting_readback'

def update_avito_prices(report):
    import avito_client
    results = []
    report['avito_price_updates'] = results
    current = avito_preflight()
    targets = current.get('targets', {})
    if set(targets) != set(TARGETS) | set(KEEP):
        raise RuntimeError('Avito live target identity check incomplete')
    if int(targets['0am325025HX']['price']) != KEEP['0am325025HX']:
        raise RuntimeError('Protected Avito D price changed')
    for sku, old in TARGETS.items():
        item = targets[sku]
        if int(item['id']) != AVITO_IDS[sku] or int(item['price']) not in (old, NEW_PRICE):
            raise RuntimeError('Avito target changed since approval: ' + sku)
        if int(item['price']) == NEW_PRICE:
            results.append({'sku': sku, 'id': item['id'], 'status': 'already_correct'})
            continue
        try:
            response = requests.post('https://api.avito.ru/core/v1/items/' + str(item['id']) + '/update_price', headers=avito_client._headers(), json={'price': NEW_PRICE}, timeout=40)
            row = {'sku': sku, 'id': item['id'], 'http_status': response.status_code, 'status': 'accepted_waiting_readback' if response.ok else 'rejected'}
            if response.status_code == 429:
                row['retry_after'] = response.headers.get('Retry-After')
            results.append(row)
            if not response.ok:
                break
        except requests.RequestException:
            results.append({'sku': sku, 'id': item['id'], 'status': 'response_unknown_readback_required'})
            break

def update_price_snapshots(after_site):
    paths = []
    p = ROOT / 'data/site_prices.json'
    before = json.loads(p.read_text())
    updated = dict(before)
    for sku in TARGETS:
        value = float(after_site['targets'][sku]['price'])
        if value != NEW_PRICE:
            raise RuntimeError('Cannot publish unverified site price')
        updated[sku.lower()] = value
    p.write_text(json.dumps(updated, ensure_ascii=False, indent=2) + '\n')
    paths.append('data/site_prices.json')
    for name in ('feeds/dq200.yml', 'feeds/dq200_candidate.yml'):
        p = ROOT / name
        old = p.read_text()
        new = old
        for sku in TARGETS:
            pattern = r'(<offer\b[^>]*\bid="' + re.escape(sku.lower()) + r'"[^>]*>.*?<price>)([^<]+)(</price>)'
            new, count = re.subn(pattern, lambda m: m.group(1) + '68500.0' + m.group(3), new, flags=re.S)
            if count != 1:
                raise RuntimeError('Expected one Direct feed offer: ' + sku)
        old_root, new_root = ET.fromstring(old), ET.fromstring(new)
        old_offers = {x.attrib['id']: x for x in old_root.findall('./shop/offers/offer')}
        new_offers = {x.attrib['id']: x for x in new_root.findall('./shop/offers/offer')}
        if set(old_offers) != set(new_offers):
            raise RuntimeError('Direct feed offer set changed')
        for sku in TARGETS:
            new_offers[sku.lower()].find('price').text = old_offers[sku.lower()].findtext('price')
        if ET.tostring(old_root) != ET.tostring(new_root):
            raise RuntimeError('Unrelated Direct feed fields changed')
        p.write_text(new)
        paths.append(name)
    audit_path = ROOT / 'feeds/dq200_stock_audit.json'
    if audit_path.exists():
        data = json.loads(audit_path.read_text())
        changed = 0
        for row in data.get('offers', []):
            if row.get('sku') in {s.lower() for s in TARGETS}:
                row['price_rub'] = float(NEW_PRICE)
                changed += 1
        if changed != 4:
            raise RuntimeError('Feed audit row set mismatch')
        audit_path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        paths.append('feeds/dq200_stock_audit.json')
    return paths

def verified_site_delta(before, after):
    if set(before['all_prices']) != set(after['all_prices']):
        raise RuntimeError('Site product set changed during price operation')
    wanted_uids = {str(before['targets'][s]['uid']) for s in TARGETS}
    unexpected = []
    for uid, old in before['all_prices'].items():
        new = after['all_prices'][uid]
        new_nonprice = new.get('non_price_sha256_with_baseline_edition_prices', new.get('non_price_sha256'))
        if old.get('non_price_sha256') != new_nonprice:
            raise RuntimeError('Site non-price data changed during operation: ' + str(old['sku']))
        if uid not in wanted_uids and float(old['price'] or 0) != float(new['price'] or 0):
            unexpected.append(old['sku'])
    if unexpected:
        raise RuntimeError('Other site prices changed during operation: ' + ','.join(unexpected[:6]))
    for sku in set(TARGETS) | set(KEEP):
        expected = NEW_PRICE if sku in TARGETS else KEEP[sku]
        if float(after['targets'][sku]['price']) != expected:
            return False
        for field in ('uid', 'sku', 'externalid', 'title', 'priceold', 'descr', 'text', 'gallery', 'editions', 'quantity', 'inv'):
            if field == 'editions' and sku in TARGETS:
                old_editions = before['targets'][sku].get(field) or []
                new_editions = after['targets'][sku].get(field) or []
                if [{k: v for k, v in x.items() if k != 'price'} for x in old_editions] != [{k: v for k, v in x.items() if k != 'price'} for x in new_editions]:
                    raise RuntimeError('Non-price edition data changed: ' + sku)
                if not all(float(x.get('price') or 0) == NEW_PRICE for x in new_editions):
                    return False
                continue
            if before['targets'][sku].get(field) != after['targets'][sku].get(field):
                raise RuntimeError('Unrelated target product field changed: ' + sku + '/' + field)
    return True

def apply_prices():
    report = {'mode': 'apply', 'started_at_utc': now(), 'owner_approved_price': NEW_PRICE, 'target_skus': list(TARGETS), 'protected': KEEP, 'errors': [], 'status': 'started'}
    try:
        manifest = json.loads(MANIFEST.read_text())
        if manifest.get('target_skus') != list(TARGETS) or manifest.get('price') != NEW_PRICE:
            raise RuntimeError('Approved manifest scope mismatch')
        prepared = {}
        for path, spec in manifest['files'].items():
            if path not in ('data/avito_site_price_overrides.xlsx', 'data/avito_feed.xlsx'):
                raise RuntimeError('Unexpected workbook destination')
            before = (ROOT / path).read_bytes()
            worksheet_part, cells = WORKBOOK_EDITS[path]
            if spec.get('changed_cells') != cells or spec.get('worksheet_part') != worksheet_part:
                raise RuntimeError('Workbook cell allowlist mismatch')
            new = base64.b64decode(spec['after_base64'], validate=True)
            if digest(new) != spec['after_sha256'] or git_blob(new) != spec['after_git_blob_sha']:
                raise RuntimeError('Approved workbook hash mismatch')
            if digest(before) != spec['after_sha256']:
                if digest(before) != spec['before_sha256']:
                    raise RuntimeError('Workbook source changed; no replacement performed')
                validate_workbook_delta(before, new, cells, worksheet_part)
            prepared[path] = new
        if set(prepared) != {'data/avito_site_price_overrides.xlsx', 'data/avito_feed.xlsx'}:
            raise RuntimeError('Incomplete approved workbooks')
        before_site = fetch_site_records()
        report['site_before'] = before_site
        for sku, old in {**TARGETS, **KEEP}.items():
            allowed = (old, NEW_PRICE) if sku in TARGETS else (old,)
            if float(before_site['targets'][sku]['price']) not in allowed:
                raise RuntimeError('Site price changed since approval')
        report['avito_before'] = avito_preflight()
        if set(report['avito_before'].get('targets', {})) != set(TARGETS) | set(KEEP):
            raise RuntimeError('Avito preflight incomplete')
        for sku, old in {**TARGETS, **KEEP}.items():
            item = report['avito_before']['targets'][sku]
            if item['id'] != AVITO_IDS[sku] or int(item['price']) not in ((old, NEW_PRICE) if sku in TARGETS else (old,)):
                raise RuntimeError('Avito price changed since approval')
        profile = report['avito_before'].get('profile', {})
        urls = [f.get('feed_url') for f in profile.get('feeds_data', [])]
        if profile.get('autoload_enabled') is not True or STABLE_AVITO_URL not in urls:
            raise RuntimeError('Expected active Avito feed not configured')
        _, asset, live_feed = get_avito_asset()
        allowed_feed_hashes = {manifest['files']['data/avito_feed.xlsx']['before_sha256'], manifest['files']['data/avito_feed.xlsx']['after_sha256']}
        if digest(live_feed) not in allowed_feed_hashes:
            raise RuntimeError('Published Avito feed differs from approved source')
        if not all(os.getenv(k) for k in ('TILDA_COMMERCEML_URL', 'TILDA_COMMERCEML_USER', 'TILDA_COMMERCEML_PASSWORD')):
            raise RuntimeError('Tilda publication credentials missing')
        for path, payload in prepared.items():
            (ROOT / path).write_bytes(payload)
        rows = read_book(ROOT / 'data/avito_site_price_overrides.xlsx', 'Цены Avito и сайт', 1, {'avito': 2, 'fixed': 3, 'site_explicit': 4})
        for sku in TARGETS:
            assert rows[sku] == {'avito': NEW_PRICE, 'fixed': 'да', 'site_explicit': NEW_PRICE}
        assert rows['0am325025HX'] == {'avito': 64500, 'fixed': 'да', 'site_explicit': None}
        report['source_commit'] = commit_paths(list(prepared), 'Set approved E F G K mechatronic site and Avito prices to 68500; preserve D')
        report['source_prices_saved'] = True
        publish_avito_asset(prepared['data/avito_feed.xlsx'], report)
        commit_paths(['data/photo_upload_cache.json'], 'Record verified Avito price feed asset')
        publish_tilda_prices(before_site, report)
        site_ok = False
        for attempt in range(6):
            after_site = fetch_site_records(edition_price_baseline=before_site['targets'])
            report['site_after'] = after_site
            if verified_site_delta(before_site, after_site):
                site_ok = True
                break
            time.sleep(6)
        report['site_verified'] = site_ok
        if site_ok:
            changed_paths = update_price_snapshots(after_site)
            report['feed_commit'] = commit_paths(changed_paths, 'Refresh four DQ200 advertising prices from verified live site')
        update_avito_prices(report)
        avito_ok = False
        for attempt in range(5):
            after_avito = avito_preflight()
            report['avito_after'] = after_avito
            got = after_avito.get('targets', {})
            avito_ok = set(got) == set(TARGETS) | set(KEEP) and all(int(got[s]['price']) == NEW_PRICE for s in TARGETS) and int(got['0am325025HX']['price']) == 64500
            if avito_ok:
                break
            time.sleep(7)
        report['avito_verified'] = avito_ok
        report['status'] = 'applied_and_verified' if site_ok and avito_ok else 'source_saved_live_verification_pending'
    except Exception as exc:
        report['status'] = 'partial_or_blocked'
        report['errors'].append({'type': type(exc).__name__, 'message': str(exc) if isinstance(exc, (RuntimeError, AssertionError)) else 'Operation failed; see stage status'})
    report['finished_at_utc'] = now()
    (ROOT / 'data/dq200_price_apply_20261120.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report.get(k) for k in ('status', 'source_prices_saved', 'site_verified', 'avito_verified', 'errors')}, ensure_ascii=False))
    return 0 if report['status'] == 'applied_and_verified' else 1

def verify_prices():
    report = {'mode': 'verify', 'started_at_utc': now(), 'external_mutations': [], 'errors': []}
    try:
        original = json.loads((ROOT / 'data/dq200_price_apply_20261120.json').read_text())
        after_site = fetch_site_records(edition_price_baseline=original['site_before']['targets'])
        report['site'] = after_site
        report['site_verified'] = verified_site_delta(original['site_before'], after_site)
        after_avito = avito_preflight()
        report['avito'] = after_avito
        got = after_avito.get('targets', {})
        report['avito_verified'] = set(got) == set(TARGETS) | set(KEEP) and all(int(got[s]['price']) == NEW_PRICE for s in TARGETS) and int(got['0am325025HX']['price']) == 64500
        if report['site_verified']:
            paths = update_price_snapshots(after_site)
            report['feed_commit'] = commit_paths(paths, 'Verify four live site prices and refresh matching DQ200 feed prices')
        report['status'] = 'applied_and_verified' if report['site_verified'] and report['avito_verified'] else 'verification_pending'
    except Exception as exc:
        report['status'] = 'verification_failed'
        report['errors'].append({'type': type(exc).__name__, 'message': str(exc) if isinstance(exc, RuntimeError) else 'Verification failed'})
    report['finished_at_utc'] = now()
    (ROOT / 'data/dq200_price_verify_20261120.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report.get(k) for k in ('status', 'site_verified', 'avito_verified', 'errors')}, ensure_ascii=False))
    return 0 if report['status'] == 'applied_and_verified' else 1

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
    if request.get('mode') == 'apply':
        return apply_prices()
    if request.get('mode') == 'verify':
        return verify_prices()
    raise RuntimeError('Requested mode has not been installed')

if __name__ == '__main__':
    raise SystemExit(main())
