"""One owner-requested electronic-board assortment via existing Avito autoload.

Reuses the proven separate-feed mechanism. Only --apply can add one registered
feed; --preflight and --readback perform no seller writes. No forced imports,
existing-listing edits, alternative IDs, billing changes, or duplicate evasion.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

import requests
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import avito_client as av
import avito_feed as af
import content_pipeline as p
from sales_availability import quantity_for_sale
from scripts.apply_dq200_prices_20261120 import gh

FEED_ID = 'leoparts-avito-electronic-boards-20261127-01'
FEED_NAME = 'LEOPARTS электронные платы DQ200'
ASSET = 'avito_electronic_boards_20261127.xlsx'
BASE = 'https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/'
URL = BASE + ASSET
PRIMARY = BASE + 'avito_feed.xlsx'
SKUS = tuple('0AM927769' + x for x in 'DEFGK')
PRICES = dict(zip(SKUS, (36500, 39500, 39500, 39500, 39500)))
PRICE = 36500
ANCHOR = '0AM927769D'
DRAFT = ROOT / 'drafts/avito_electronic_boards_20261127.json'
OUT = ROOT / 'data/avito_boards_listing_20261127_result.json'
STAGE = ROOT / 'data/avito_electronic_boards_20261127.xlsx'
J = {}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def save(*paths):
    J['updated_utc'] = p.stamp()
    p.write_json(OUT, J)
    p.checkpoint([OUT, *paths], live=True)
    print('AVITO_BOARDS', J.get('status'), flush=True)


def api(method, path, **kwargs):
    # No transparent retry for an ambiguous POST. All write recovery reads first.
    return av._request(method, path, retries=1, **kwargs)


def public_bytes(url):
    parsed = urlsplit(url)
    require(parsed.scheme == 'https' and parsed.hostname == 'github.com'
            and url.startswith(BASE), 'Unapproved feed/media source host')
    r = requests.get(url, timeout=60)
    r.raise_for_status()
    return r.content


def schedule_key(value):
    return sorted((x.get('rate'), tuple(sorted(x.get('weekdays') or [])),
                   tuple(sorted(x.get('time_slots') or []))) for x in value or [])


def profile():
    value = api('GET', '/autoload/v2/profile')
    require(value.get('autoload_enabled') is True, 'Autoload not enabled')
    require(value.get('allow_pay_over_limit') is False, 'Paid-over-limit protection not confirmed')
    require(value.get('uploadMode') in (None, 'auto'), 'Unsupported existing upload mode')
    feeds = value.get('feeds_data') or []
    require(isinstance(feeds, list) and all(isinstance(f, dict) for f in feeds), 'Invalid feed configuration')
    urls = [f.get('feed_url') for f in feeds]
    require(PRIMARY in urls and len(urls) == len(set(urls)), 'Primary missing or feed URL duplicated')
    require(all(f.get('feed_name') for f in feeds), 'An existing feed has no name')
    require(bool(value.get('schedule')), 'Existing schedule unavailable')
    require(isinstance(value.get('report_email'), str) and '@' in value['report_email'], 'Report notification configuration unavailable')
    return value


def safe_profile(value):
    return {'autoload_enabled': value.get('autoload_enabled'),
            'allow_pay_over_limit': value.get('allow_pay_over_limit'),
            'registered_feed_count': len(value['feeds_data']),
            'new_feed_registered': URL in [f['feed_url'] for f in value['feeds_data']],
            'feeds_sha256': sha(canonical(value['feeds_data'])),
            'schedule_sha256': sha(canonical(schedule_key(value['schedule'])))}


def listing_snapshot():
    result = {}
    for page in range(1, 51):
        response = av.list_items(page=page, per_page=100)
        rows = response.get('resources') or response.get('items') or []
        require(isinstance(rows, list), 'Invalid seller item list')
        for item in rows:
            ident = int(item['id'])
            require(ident not in result, 'Repeated item while paginating')
            result[ident] = {k: item.get(k) for k in ('id', 'title', 'price', 'status', 'url')}
        if len(rows) < 100:
            return result
    raise ValueError('Seller item pagination incomplete')


def source_rows(raw):
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        sheet = book[af.SHEET_ADS]
        result = {}
        for row in sheet.iter_rows(min_row=af.FIRST_DATA_ROW, values_only=True):
            sku = str(row[af.COL_ID-1] or '')
            if sku in SKUS:
                require(sku not in result, 'Duplicate canonical board row')
                result[sku] = row
        require(set(result) == set(SKUS), 'Canonical board set differs')
        for sku, row in result.items():
            require(int(row[af.COL_PRICE-1]) == PRICES[sku], 'Canonical board price changed: ' + sku)
            require(row[af.COL_OEM-1] == sku, 'Canonical OEM differs: ' + sku)
            require(row[af.COL_CONDITION-1] == 'Новое', 'Condition changed: ' + sku)
        return result
    finally:
        book.close()


def stocks():
    book = load_workbook(ROOT/'data/master_control.xlsx', read_only=True, data_only=True)
    try:
        sheet = book['Товары']
        rows = list(sheet.iter_rows(values_only=True))
        headers = list(rows[0])
        sku_col = headers.index('Артикул (offer_id)')
        stock_col = headers.index('Остаток, шт.')
        result = {}
        for row in rows[1:]:
            sku = str(row[sku_col] or '')
            if sku in SKUS:
                require(sku not in result, 'Duplicate master board SKU')
                n = float(quantity_for_sale(sku, row[stock_col]))
                require(n > 0 and n.is_integer(), 'No canonical available stock: ' + sku)
                result[sku] = int(n)
        require(set(result) == set(SKUS), 'Missing board stock')
        return result
    finally:
        book.close()


def template_info():
    book = load_workbook(ROOT/'data/avito_template.xlsx', read_only=False, data_only=False)
    try:
        sheet = book[af.SHEET_ADS]
        def values(col):
            validations = [v for v in sheet.data_validations.dataValidation if sheet.cell(5, col).coordinate in v.sqref]
            if len(validations) != 1 or '!' not in str(validations[0].formula1):
                return []
            name, area = validations[0].formula1.split('!', 1)
            return [c.value for row in book[name.strip("'")][area] for c in row if c.value is not None]
        require('Package' in values(7) and 'Free' in values(8), 'Official fee/promotion enum changed')
        return {'listing_fee': 'Package', 'promotion': 'Free',
                'delivery_header': sheet.cell(2, 27).value,
                'delivery_allowed_values': values(27),
                'delivery_action': 'leave optional field blank; do not claim enabled or disabled',
                'oem_required_label': sheet.cell(3, af.COL_OEM).value,
                'oem_action': 'blank for truthful assortment; exact five OEMs in description',
                'photo_max_file_mb': 25}
    finally:
        book.close()


def category_schema():
    # Exact slug is already recorded by the project's official user-docs/tree
    # discovery in data/avito_paid_category_health.json.
    result = api('GET', '/autoload/v1/user-docs/node/transmissiia_i_privod/fields')
    fields = []
    for field in result.get('fields') or []:
        content = field.get('content') or []
        fields.append({'tag': field.get('tag'), 'label': field.get('label'),
                       'content': content})
    require(fields, 'Official transmission category schema unavailable')
    return {'slug': 'transmissiia_i_privod', 'node': result.get('node'), 'fields': fields}


def read_draft():
    d = json.loads(DRAFT.read_text(encoding='utf-8'))
    require(d.get('feed_id', d.get('draft_id')) == FEED_ID, 'Draft feed identity differs')
    require(d.get('owner_requested_create') is True or (d.get('publication') or {}).get('owner_requested_create') is True, 'Owner creation request not recorded')
    require(d['price_rub'] == PRICE and d.get('price_anchor_sku', d.get('price_basis_sku')) == ANCHOR, 'Owner-approved 36500 price/anchor differs')
    boards = d['boards']
    require([b['sku'] for b in boards] == list(SKUS), 'Draft assortment differs')
    require(all(b['oem'] == b['sku'] and b['price_rub'] == PRICES[b['sku']] for b in boards), 'Draft OEM or per-board price differs')
    require(0 < len(d['title']) <= 50 and 0 < len(d['description']) < 7500, 'Text length invalid')
    require(all(sku in d['description'] for sku in SKUS), 'Description omits an exact OEM')
    require(d.get('oem_field', d.get('oem')) in ('', None), 'Do not assign one SKU to entire assortment OEM field')
    require(d.get('delivery_field') in ('', None), 'No documented delivery override was supplied')
    require(d.get('media_reviewed') is True, 'Media visual review not complete')
    gallery = d['gallery']
    require(1 <= len(gallery) <= 10 and [r['position'] for r in gallery] == list(range(1, len(gallery)+1)), 'Gallery order/count invalid')
    require(len({r['sha256'] for r in gallery}) == len(gallery), 'Duplicate final gallery images')
    for row in gallery:
        path = (ROOT / row['repository_path']).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), 'Gallery file unavailable or outside project')
        raw = path.read_bytes()
        require(sha(raw) == row['sha256'], 'Gallery SHA mismatch')
        require(len(raw) <= 25*1024*1024, 'Image exceeds template file limit')
        require(row.get('watermark_verified') is True, 'Watermark unverified')
        require(row['asset_name'].startswith('avito-boards-20261127-') and '/' not in row['asset_name'], 'Unsafe/nonunique gallery asset name')
    return d


def report_items(kind):
    base = '/autoload/v4/uploads/' + kind
    first = api('GET', base)
    result = []
    for page in range(1, 51):
        response = api('GET', base+'/items', params={'page': page, 'perPage': 50})
        rows = response.get('items')
        require(isinstance(rows, list), 'Report rows unavailable')
        result.extend(rows)
        pages = (response.get('meta') or {}).get('pages')
        if len(rows) < 50 or isinstance(pages, int) and page >= pages:
            break
    else:
        raise ValueError('Report pagination incomplete')
    require(api('GET', base).get('upload_id') == first.get('upload_id'), 'Report changed while reading')
    return first, result


def verify_received(raw, draft, image_urls):
    book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    try:
        rows = [r for r in book[af.SHEET_ADS].iter_rows(min_row=af.FIRST_DATA_ROW, values_only=True) if str(r[af.COL_ID-1] or '') == FEED_ID]
        require(len(rows) == 1, 'Exact new feed row not unique')
        row = rows[0]
        require(row[af.COL_AVITO_ID-1] in (None, ''), 'New row must never bind an existing Avito ID')
        require(row[af.COL_TITLE-1] == draft['title'] and row[af.COL_DESCRIPTION-1] == draft['description'], 'New text differs')
        require(int(row[af.COL_PRICE-1]) == PRICE, 'New price differs')
        require(row[af.COL_OEM-1] in (None, ''), 'Assortment OEM must be blank')
        require(str(row[af.COL_IMAGE_URLS-1]).splitlines() == image_urls, 'New gallery differs')
        require(row[6] == 'Package' and row[7] == 'Free', 'Fee/promotion settings differ')
        return True
    finally:
        book.close()


def build(draft, rows, image_urls):
    book = load_workbook(ROOT/'data/avito_template.xlsx')
    sheet = book[af.SHEET_ADS]
    if sheet.max_row >= af.FIRST_DATA_ROW:
        sheet.delete_rows(af.FIRST_DATA_ROW, sheet.max_row-af.FIRST_DATA_ROW+1)
    source = rows[ANCHOR]
    copy_columns = (af.COL_ADDRESS, af.COL_CATEGORY, af.COL_GOODS_TYPE, af.COL_AD_TYPE,
                    af.COL_PART_FOR, af.COL_PART_KIND, af.COL_TRANSMISSION_PART_TYPE,
                    af.COL_CONDITION, af.COL_BRAND)
    for col in copy_columns:
        sheet.cell(af.FIRST_DATA_ROW, col, source[col-1])
    values = {af.COL_ID: FEED_ID, af.COL_TITLE: draft['title'],
              af.COL_DESCRIPTION: draft['description'], af.COL_PRICE: PRICE,
              af.COL_IMAGE_URLS: '\n'.join(image_urls), 7: 'Package', 8: 'Free'}
    for col, value in values.items():
        sheet.cell(af.FIRST_DATA_ROW, col, value)
    buffer = io.BytesIO()
    book.save(buffer)
    book.close()
    raw = buffer.getvalue()
    verify_received(raw, draft, image_urls)
    return raw


def upload_asset(release, name, raw, mime):
    existing = [a for a in release.get('assets', []) if a.get('name') == name]
    require(len(existing) <= 1, 'Duplicate release asset')
    if existing:
        asset = existing[0]
        require(asset.get('digest') in (None, 'sha256:'+sha(raw)), 'Existing staged asset digest differs')
    else:
        p.current_main()
        asset = gh('POST', release['upload_url'].split('{', 1)[0], params={'name': name}, data=raw,
                   headers={'Content-Type': mime}).json()
        release['assets'].append(asset)
    url = asset['browser_download_url']
    require(public_bytes(url) == raw, 'Hosted asset readback differs')
    return url


def readback():
    evidence = []
    draft = json.loads(DRAFT.read_text()) if DRAFT.exists() else None
    for kind in ('current', 'last_successful'):
        try:
            report, rows = report_items(kind)
            matches = [r for r in rows if str(r.get('ad_id')) == FEED_ID]
            item = {'kind': kind, 'upload_id': report.get('upload_id'), 'status': report.get('status'),
                    'matches': [{k: r.get(k) for k in ('ad_id', 'avito_id', 'avito_status', 'status', 'section', 'messages', 'url')} for r in matches]}
            if draft and J.get('gallery_urls'):
                for feed in report.get('feed_urls', []):
                    u = feed.get('url', '')
                    parsed = urlsplit(u)
                    if parsed.scheme == 'https' and parsed.hostname == 'api.avito.ru' and parsed.path.startswith('/autoload/v2/feed/content/'):
                        response = requests.get(u, headers={'Authorization': av._headers()['Authorization']}, timeout=30, allow_redirects=False)
                        if response.ok:
                            try:
                                item['received_exact_new_feed'] = verify_received(response.content, draft, J['gallery_urls'])
                            except Exception:
                                pass
            evidence.append(item)
        except Exception as exc:
            evidence.append({'kind': kind, 'read_error_type': type(exc).__name__})
    J['reports'] = evidence
    hits = [r for e in evidence for r in e.get('matches', [])]
    ids = {int(r['avito_id']) for r in hits if str(r.get('avito_id') or '').isdigit()}
    require(len(ids) <= 1, 'Multiple Avito IDs for stable new feed; do not retry')
    protected = set(J.get('protected_existing_ids', []))
    require(not ids.intersection(protected), 'New feed linked an existing listing; stop')
    if ids:
        ident = ids.pop()
        require(J.get('new_avito_id') in (None, ident), 'Assigned Avito ID changed')
        J['new_avito_id'] = ident
        found = listing_snapshot().get(ident)
        J['new_listing'] = found
        J['status'] = 'assigned_pending_or_not_active'
        if found and found.get('status') == 'active' and int(found.get('price') or 0) == PRICE:
            require(not draft or found.get('title') == draft['title'], 'Active title differs')
            J['status'] = 'active_price_title_verified'
            J['autoload_content_verified'] = any(e.get('received_exact_new_feed') for e in evidence)
            J['live_gallery_verified'] = False
    elif hits:
        J['status'] = 'autoload_report_received_no_assigned_id'
    else:
        J['status'] = 'registered_pending_scheduled_processing'


def main():
    global J
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--preflight', action='store_true')
    modes.add_argument('--apply', action='store_true')
    modes.add_argument('--readback', action='store_true')
    args = parser.parse_args()
    require(os.environ.get('GITHUB_REF') == 'refs/heads/main', 'Main only')
    p.current_main()
    J = json.loads(OUT.read_text()) if OUT.exists() else {'feed_id': FEED_ID, 'price_rub': PRICE, 'price_basis_sku': ANCHOR,
        'seller_writes': 0, 'started_utc': p.stamp(), 'status': 'preflight', 'new_avito_id': None}
    require(J['feed_id'] == FEED_ID and J['price_rub'] == PRICE, 'Saved scope differs')
    try:
        before = profile()
        J['profile'] = safe_profile(before)
        registered = URL in [x['feed_url'] for x in before['feeds_data']]
        if args.readback or registered:
            require(registered, 'New feed not registered; readback will not publish it')
            readback()
            save()
            return 0
        primary = public_bytes(PRIMARY)
        rows = source_rows(primary)
        current = listing_snapshot()
        J['canonical_stock'] = stocks()
        J['template'] = template_info()
        try:
            J['official_category_schema'] = category_schema()
        except Exception as exc:
            J['official_category_schema_error_type'] = type(exc).__name__
        J['existing_item_count'] = len(current)
        draft_source = json.loads(DRAFT.read_text()) if DRAFT.exists() else {}
        board_ids = {b['sku']: int(b['existing_avito_id']) for b in draft_source.get('boards', [])}
        J['board_listing_snapshot'] = {sku: current.get(board_ids.get(sku) or (int(row[af.COL_AVITO_ID-1]) if str(row[af.COL_AVITO_ID-1] or '').isdigit() else 0)) for sku, row in rows.items()}
        if board_ids:
            try:
                info = av.get_stock_info(list(board_ids.values()))
                J['avito_stock_probe'] = [{k: r.get(k) for k in ('itemId', 'item_id', 'quantity', 'stock', 'stockCount') if k in r} for r in info]
            except Exception as exc:
                J['avito_stock_probe_error_type'] = type(exc).__name__
        # These are counts/IDs only; no notification email, credentials or address is persisted.
        J['protected_existing_ids'] = sorted(current)
        J['protected_existing_sha256'] = sha(canonical(current))
        J['primary_feed_sha256'] = sha(primary)
        J['draft_present'] = DRAFT.exists()
        J['fourth_feed_limit_unconfirmed'] = len(before['feeds_data']) >= 3
        if args.preflight:
            J['status'] = 'read_only_preflight_complete'
            if DRAFT.exists():
                try:
                    read_draft()
                    J['draft_valid'] = True
                except Exception as exc:
                    J['draft_valid'] = False
                    J['draft_issue'] = str(exc)[:250]
            save()
            return 0
        require(not J.get('profile_write_intent'), 'Prior write uncertain; readback before any repeat')
        d = read_draft()
        draft_sha = sha(canonical(d))
        require(J.get('draft_sha256') in (None, draft_sha), 'Prepared draft changed; reconcile existing stage first')
        release = gh('GET', '/releases/tags/product-photos').json()
        image_urls = []
        for image in d['gallery']:
            raw = (ROOT/image['repository_path']).read_bytes()
            mime = 'image/png' if image['asset_name'].lower().endswith('.png') else 'image/jpeg'
            u = upload_asset(release, image['asset_name'], raw, mime)
            image_urls.append(u + '?v=' + image['sha256'][:10])
            require(sha(public_bytes(image_urls[-1])) == image['sha256'], 'Versioned media differs')
        if STAGE.exists() and J.get('feed_sha256'):
            raw = STAGE.read_bytes()
            require(sha(raw) == J['feed_sha256'], 'Saved feed bytes differ')
            verify_received(raw, d, image_urls)
        else:
            raw = build(d, rows, image_urls)
            STAGE.parent.mkdir(parents=True, exist_ok=True)
            STAGE.write_bytes(raw)
        J.update(status='new_feed_prepared', draft_sha256=draft_sha, gallery_urls=image_urls,
                 photos_in_feed=len(image_urls), feed_sha256=sha(raw))
        save(STAGE)
        upload_asset(release, ASSET, raw, 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        J.update(status='new_feed_hosted_not_registered', feed_url=URL)
        save()
        again = profile()
        require(again['feeds_data'] == before['feeds_data'] and schedule_key(again['schedule']) == schedule_key(before['schedule'])
                and again['report_email'] == before['report_email'], 'Profile changed during preparation')
        require(public_bytes(PRIMARY) == primary, 'Primary feed changed concurrently')
        # Preserve every configured source and all existing account settings.
        payload = {'autoload_enabled': True, 'feeds_data': copy.deepcopy(before['feeds_data']) + [{'feed_name': FEED_NAME, 'feed_url': URL}],
                   'schedule': before['schedule'], 'report_email': before['report_email']}
        J.update(status='new_feed_registration_intent', profile_write_intent=True)
        save()
        p.current_main()
        try:
            api('POST', '/autoload/v2/profile', json=payload)
            J['seller_writes'] = 1
        except Exception as exc:
            J['registration_error_type'] = type(exc).__name__
        after = profile()
        require(after['feeds_data'] == payload['feeds_data'], 'New feed registration not confirmed')
        require(schedule_key(after['schedule']) == schedule_key(before['schedule']) and after['report_email'] == before['report_email'], 'Existing schedule/notifications changed')
        require(public_bytes(PRIMARY) == primary, 'Primary hosted feed changed')
        after_items = listing_snapshot()
        J['protected_existing_fields_unchanged'] = all(after_items.get(ident) == value for ident, value in current.items())
        J.update(status='registered_pending_scheduled_processing', profile_registered=True)
        save()
        readback()
        save()
        return 0
    except Exception as exc:
        J['status'] = 'blocked_or_unverified'
        # Avoid recording upstream response text which can contain account details.
        J['error_type'] = type(exc).__name__
        if isinstance(exc, ValueError):
            J['error'] = str(exc)[:350]
        save()
        return 1


if __name__ == '__main__':
    sys.exit(main())
