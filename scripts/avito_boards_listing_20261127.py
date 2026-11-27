"""One owner-requested electronic-board assortment via existing Avito autoload.

Reuses the proven existing tools-XML asset publication mechanism. --apply adds
one Ad while preserving every old XML byte and the complete autoload profile.
--preflight and --readback perform no publication. No forced imports, existing
listing edits, alternative IDs, billing changes, or duplicate evasion.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import io
import json
import os
import re
import sys
import zipfile
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests
from openpyxl import load_workbook
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
import avito_client as av
import avito_feed as af
import content_pipeline as p
from sales_availability import quantity_for_sale
from scripts.apply_dq200_prices_20261120 import gh, rename_asset

FEED_ID = 'leoparts-avito-electronic-boards-20261127-01'
FEED_NAME = 'LEOPARTS электронные платы DQ200'
ASSET = 'avito_tools_feed.xml'
BASE = 'https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/'
URL = BASE + ASSET
PRIMARY = BASE + 'avito_feed.xlsx'
SKUS = tuple('0AM927769' + x for x in 'DEFGK')
PRICES = dict(zip(SKUS, (36500, 39500, 39500, 39500, 39500)))
PRICE = 36500
ANCHOR = '0AM927769D'
DRAFT = ROOT / 'drafts/avito_electronic_boards_20261127.json'
OUT = ROOT / 'data/avito_boards_listing_20261127_result.json'
STAGE = ROOT / 'data/avito_electronic_boards_20261127.xml'
BACKUP = ROOT / 'data/avito_boards_listing_20261127_before_tools.xml'
AD_PATTERN = re.compile(rb'<Ad(?:\s[^>]*)?>.*?</Ad>', re.S)
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
            'target_tools_feed_registered': URL in [f['feed_url'] for f in value['feeds_data']],
            'feeds_sha256': sha(canonical(value['feeds_data'])),
            'schedule_sha256': sha(canonical(schedule_key(value['schedule']))),
            'schedule': value['schedule']}


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
                'oem_action': 'actual primary offer D at 36500; other four executions listed separately',
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


def read_draft(normalize_encoding=False):
    d = json.loads(DRAFT.read_text(encoding='utf-8'))
    require(d.get('feed_id', d.get('draft_id')) == FEED_ID, 'Draft feed identity differs')
    require(d.get('owner_requested_create') is True or (d.get('publication') or {}).get('owner_requested_create') is True, 'Owner creation request not recorded')
    require(d['price_rub'] == PRICE and d.get('price_anchor_sku', d.get('price_basis_sku')) == ANCHOR, 'Owner-approved 36500 price/anchor differs')
    boards = d['boards']
    require([b['sku'] for b in boards] == list(SKUS), 'Draft assortment differs')
    require(all(b['oem'] == b['sku'] and b['price_rub'] == PRICES[b['sku']] for b in boards), 'Draft OEM or per-board price differs')
    require(0 < len(d['title']) <= 50 and 0 < len(d['description']) < 7500, 'Text length invalid')
    require(all(sku in d['description'] for sku in SKUS), 'Description omits an exact OEM')
    require(d.get('oem') == ANCHOR and d.get('primary_offer_sku') == ANCHOR, 'Primary real offer must be D with its actual OEM')
    require(d.get('delivery_field') in ('', None), 'No documented delivery override was supplied')
    require(d.get('media_reviewed') is True, 'Media visual review not complete')
    gallery = d['gallery']
    require(1 <= len(gallery) <= 10 and [r['position'] for r in gallery] == list(range(1, len(gallery)+1)), 'Gallery order/count invalid')
    require(len({r['sha256'] for r in gallery}) == len(gallery), 'Duplicate final gallery images')
    expected_path = ROOT / d['media_expected_path']
    expected = json.loads(expected_path.read_text())
    manifest_path = ROOT / d['media_manifest_path']
    manifest = json.loads(manifest_path.read_text())
    require(manifest['renderer']['font_sha256'] == expected['renderer']['font_sha256'], 'Reviewed font files differ')
    require(manifest['source_count'] == expected['source_count'] and manifest['gallery_count'] == expected['gallery_count'], 'Prepared media counts differ')
    require(manifest.get('all_eligible_sources_in_gallery') and manifest.get('all_originals_retained') and manifest.get('all_individuals_watermarked'), 'Media completeness proof missing')
    actual_sources = {s['filename']: s for s in manifest['sources']}
    for s in expected['sources']:
        actual = actual_sources.get(s['filename'], {})
        require(actual.get('sha256') == s['sha256'] and actual.get('publish') == s['publish'], 'Source identity/publication decision differs')
        require(actual.get('proof', {}).get('outside_mask_identical') is True, 'Original pixel preservation not verified')
    changed_encoding = False
    expected_gallery = {r['position']: r for r in expected['gallery']}
    for row in gallery:
        path = (ROOT / row['repository_path']).resolve()
        require(path.is_relative_to(ROOT) and path.is_file(), 'Gallery file unavailable or outside project')
        raw = path.read_bytes()
        approved = expected_gallery[row['position']]
        with Image.open(io.BytesIO(raw)) as im:
            im.load()
            pixels = sha(im.convert('RGBA').tobytes())
            require(list(im.size) == approved['dimensions'] == row['dimensions'], 'Reviewed image dimensions changed')
            require(pixels == approved['pixel_sha256_rgba'] == row['pixel_sha256_rgba'], 'Reviewed image pixels changed')
        actual_sha = sha(raw)
        if actual_sha != row['sha256']:
            require(normalize_encoding, 'PNG encoding differs; pixel-verified normalization required')
            row['sha256'] = actual_sha
            row['asset_name'] = 'avito-boards-20261127-' + path.stem + '-' + actual_sha[:12] + '.png'
            changed_encoding = True
        require(len(raw) <= 25*1024*1024, 'Image exceeds template file limit')
        require(row.get('watermark_verified') is True, 'Watermark unverified')
        require(row['asset_name'].startswith('avito-boards-20261127-') and '/' not in row['asset_name'], 'Unsafe/nonunique gallery asset name')
    if changed_encoding:
        # Only a lossless PNG representation difference is permitted after exact
        # reviewed RGBA pixels/dimensions were established for every image.
        p.write_json(DRAFT, d)
        J['media_encoding_normalized_after_exact_pixel_verification'] = True
        save(DRAFT)
    J['media_summary'] = {k: manifest.get(k) for k in ('source_count', 'unique_source_count', 'eligible_source_count', 'gallery_count', 'all_eligible_sources_in_gallery', 'all_originals_retained', 'all_individuals_watermarked')}
    J['media_quarantined'] = manifest.get('quarantined', [])
    return d


class AutoloadReadError(RuntimeError):
    def __init__(self, path, status, code=None, retry_after=None, message=None):
        super().__init__('Autoload read failed')
        self.safe_details = {'read_error_type': type(self).__name__,
                             'endpoint': path, 'http_status': status,
                             'api_code': code, 'retry_after': retry_after,
                             'api_message': message}


def autoload_read(path, params=None):
    require(path.startswith('/autoload/'), 'Unexpected read-only endpoint')
    for attempt in range(2):
        response = requests.get(av.config.avito_api_base + path,
                                headers=av._headers(), params=params, timeout=30)
        if response.status_code == 401 and attempt == 0:
            av._get_token(force_refresh=True)
            continue
        if not response.ok:
            code = None
            message = None
            try:
                body = response.json()
                error = body.get('error', {}) if isinstance(body, dict) else {}
                code = error.get('code') if isinstance(error, dict) else None
                code = code or (body.get('code') if isinstance(body, dict) else None)
                message = error.get('message') if isinstance(error, dict) else None
                message = message or (body.get('message') if isinstance(body, dict) else None)
                if isinstance(message, str):
                    message = re.sub(r'Bearer\s+\S+', 'Bearer [redacted]', message, flags=re.I)
                    message = re.sub(r'\S+@\S+', '[email redacted]', message)[:240]
                else:
                    message = None
            except ValueError:
                pass
            raise AutoloadReadError(path, response.status_code, code,
                                    response.headers.get('Retry-After'), message)
        return response.json() if response.content else {}
    raise ValueError('Read-only authentication refresh failed')


def read_error_details(exc):
    return getattr(exc, 'safe_details', {'read_error_type': type(exc).__name__})


def report_items(kind, first=None):
    base = '/autoload/v4/uploads/' + kind
    first = first or autoload_read(base)
    result = []
    for page in range(1, 51):
        response = autoload_read(base+'/items', params={'page': page, 'perPage': 50})
        rows = response.get('items')
        require(isinstance(rows, list), 'Report rows unavailable')
        result.extend(rows)
        pages = (response.get('meta') or {}).get('pages')
        if len(rows) < 50 or isinstance(pages, int) and page >= pages:
            break
    else:
        raise ValueError('Report pagination incomplete')
    # Exact binding and core item fields independently establish identity.
    # Avoid a second immediate metadata request to this rate-limited endpoint.
    return first, result


def verify_received(raw, draft, image_urls):
    root = ET.fromstring(raw)
    require(root.tag == 'Ads', 'Unexpected received XML root')
    ads = [ad for ad in root.findall('Ad') if ad.findtext('Id') == FEED_ID]
    require(len(ads) == 1, 'Exact new XML Ad not unique')
    ad = ads[0]
    require(ad.find('AvitoId') is None, 'Never bind the new Ad to an existing Avito ID')
    require(ad.findtext('Title') == draft['title'] and ad.findtext('Description') == draft['description'], 'New text differs')
    require(ad.findtext('Price') == str(PRICE), 'New price differs')
    require(ad.findtext('OEM') == ANCHOR, 'Primary real offer OEM differs')
    require([x.get('url') for x in ad.findall('Images/Image')] == image_urls, 'New gallery differs')
    require(ad.findtext('ListingFee') == 'Package' and ad.findtext('AdStatus') == 'Free', 'Fee/promotion settings differ')
    for tag, value in declared_fields(draft).items():
        require(ad.findtext(tag) == value, 'New declared XML field differs: ' + tag)
    require(ad.find('Delivery') is None, 'Unsupported delivery XML override')
    return True


def declared_fields(draft):
    # Tags and enums confirmed by the exact official transmission schema,
    # retrieved in the initial preflight, and checked again before publishing.
    return {'Category': draft['category'], 'GoodsType': draft['goods_type'],
            'AdType': draft['ad_type'], 'ProductType': draft['part_for'],
            'SparePartType': draft['part_kind'], 'TransmissionSparePartType': draft['transmission_part_type'],
            'Condition': draft['condition'], 'Brand': draft['brand'], 'OEM': ANCHOR}


def validate_official_fields(draft, fields):
    by_tag = {x['tag']: x for x in fields['fields']}
    vals = {**declared_fields(draft), 'Id': FEED_ID, 'Address': 'confirmed existing seller address',
            'Title': draft['title'], 'Description': draft['description'], 'Price': str(PRICE),
            'ListingFee': 'Package', 'AdStatus': 'Free'}
    for tag, value in vals.items():
        require(tag in by_tag, 'Tag missing from official schema: ' + tag)
        options = [v.get('value') for c in by_tag[tag]['content'] for v in c.get('values', [])]
        require(not options or value in options, 'Value missing from official schema: ' + tag)
    require(vals['Condition'] == 'Новое' and vals['OEM'] == ANCHOR and vals['Brand'], 'New-board required identity missing')
    compatible = by_tag.get('CompatibleCars', {})
    dependency_text = ' '.join(t for c in compatible.get('content', []) for t in c.get('dependencies_text', []))
    require('Б/У' in dependency_text and 'Brand' in dependency_text and 'OEM' in dependency_text,
            'CompatibleCars conditional requirement changed; do not invent vehicle models')
    unconditional = {tag for tag, f in by_tag.items() if tag != 'CompatibleCars'
                     and any(c.get('required') is True for c in f.get('content', []))}
    require(unconditional <= set(vals) | {'Images'}, 'New mandatory field unresolved: ' + ', '.join(sorted(unconditional-set(vals)-{'Images'})))


def original_ads(raw):
    root = ET.fromstring(raw)
    require(root.tag == 'Ads' and root.get('target') == 'Avito.ru' and root.get('formatVersion') == '3', 'Unexpected tools XML format')
    blobs = AD_PATTERN.findall(raw)
    require(len(blobs) == len(root) and all(c.tag == 'Ad' for c in root), 'Unexpected XML children')
    ids = [ET.fromstring(blob).findtext('Id') for blob in blobs]
    require(all(ids) and len(ids) == len(set(ids)), 'Missing or duplicate existing XML Id')
    require({'tool-dq200-acc-restore', 'tool-dsg-pressure-hp'} <= set(ids), 'Existing two tools absent')
    return root, dict(zip(ids, blobs))


def build(draft, rows, image_urls, original, official):
    root, old_ads = original_ads(original)
    require(FEED_ID not in old_ads, 'New Ad already exists; read back instead of duplicating')
    validate_official_fields(draft, official)
    source = rows[ANCHOR]
    address = source[af.COL_ADDRESS-1]
    require(isinstance(address, str) and address.strip(), 'Confirmed seller address unavailable')
    ad = ET.Element('Ad')
    values = {'Id': FEED_ID, 'Address': address, 'Title': draft['title'],
              'Description': draft['description'], 'Price': str(PRICE),
              **declared_fields(draft), 'ListingFee': 'Package', 'AdStatus': 'Free'}
    for tag, value in values.items():
        ET.SubElement(ad, tag).text = value
    images = ET.SubElement(ad, 'Images')
    for url in image_urls:
        ET.SubElement(images, 'Image', {'url': url})
    new_ad = ET.tostring(ad, encoding='utf-8', xml_declaration=False)
    ending = re.search(rb'</Ads>\s*\Z', original)
    require(ending is not None, 'Unique tools XML closing tag not found')
    inserted = b'\n' + new_ad + b'\n'
    raw = original[:ending.start()] + inserted + original[ending.start():]
    require(raw[:ending.start()] + raw[ending.start()+len(inserted):] == original, 'Existing XML bytes changed')
    new_root, new_ads = original_ads(raw)
    require(len(new_root) == len(root)+1 and set(new_ads) == set(old_ads) | {FEED_ID}, 'Append changed existing IDs/count')
    require(all(new_ads[ident] == blob for ident, blob in old_ads.items()), 'Existing Ad bytes changed')
    verify_received(raw, draft, image_urls)
    return raw


def release_asset(release, name):
    matches = [a for a in release.get('assets', []) if a.get('name') == name]
    require(len(matches) == 1, 'Release asset not unique: ' + name)
    return matches[0]


def exact_asset_bytes(asset):
    # A version query avoids observing stale CDN bytes after an asset rename.
    url = asset['browser_download_url'] + '?boards_asset=' + str(asset['id'])
    raw = public_bytes(url)
    require(asset.get('digest') in (None, 'sha256:'+sha(raw)), 'Current asset digest mismatch')
    return raw


def account_config_fingerprint(value):
    return sha(canonical({'feeds': value['feeds_data'], 'schedule': schedule_key(value['schedule']),
                          'report_email': value['report_email'], 'autoload_enabled': value['autoload_enabled'],
                          'allow_pay_over_limit': value['allow_pay_over_limit'], 'uploadMode': value.get('uploadMode')}))


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
        item = {'kind': kind}
        try:
            report = autoload_read('/autoload/v4/uploads/' + kind)
            item.update({k: report.get(k) for k in ('upload_id', 'status', 'started_at')})
            try:
                report, rows = report_items(kind, first=report)
                matches = [r for r in rows if str(r.get('ad_id')) == FEED_ID]
                item['matches'] = [{k: r.get(k) for k in ('ad_id', 'avito_id', 'avito_status', 'status', 'section', 'messages', 'url')} for r in matches]
            except Exception as exc:
                item.update(read_error_details(exc))
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
        except Exception as exc:
            item.update(read_error_details(exc))
        evidence.append(item)
        if item.get('http_status') == 429:
            break
    J['reports'] = evidence
    binding = {'matches': []}
    try:
        response = autoload_read('/autoload/v2/items/avito_ids', params={'query': FEED_ID})
        binding['matches'] = [{k: row.get(k) for k in ('ad_id', 'avito_id')}
                              for row in response.get('items', [])
                              if str(row.get('ad_id')) == FEED_ID]
    except Exception as exc:
        binding.update(read_error_details(exc))
    J['exact_feed_binding'] = binding
    hits = [r for e in evidence for r in e.get('matches', [])]
    ids = {int(r['avito_id']) for r in hits + binding['matches'] if str(r.get('avito_id') or '').isdigit()}
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
    elif all(e.get('read_error_type') for e in evidence) and binding.get('read_error_type'):
        J['status'] = 'submitted_status_unverified'
    else:
        J['status'] = 'registered_pending_scheduled_processing'
    if draft:
        draft['status'] = J['status'].upper()
        draft['publication'].update(avito_id=J.get('new_avito_id'), status=J['status'],
                                    autoload_content_verified=J.get('autoload_content_verified', False),
                                    live_gallery_verified=J.get('live_gallery_verified', False))
        p.write_json(DRAFT, draft)
    J.pop('error', None)
    J.pop('error_type', None)


def media_archive(draft, release):
    folder = (ROOT / draft['media_manifest_path']).parent
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6) as z:
        for path in sorted(folder.rglob('*')):
            if not path.is_file():
                continue
            info = zipfile.ZipInfo(path.relative_to(folder).as_posix(), date_time=(2026, 11, 27, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())
    raw = buffer.getvalue()
    name = 'avito-boards-20261127-complete-' + sha(raw)[:12] + '.zip'
    return {'url': upload_asset(release, name, raw, 'application/zip'), 'sha256': sha(raw), 'bytes': len(raw)}


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
        require(URL in [x['feed_url'] for x in before['feeds_data']], 'Existing tools XML is not registered')
        config_sha = account_config_fingerprint(before)
        release = gh('GET', '/releases/tags/product-photos').json()
        stable = [a for a in release['assets'] if a.get('name') == ASSET]
        if not stable and J.get('publication_intent') and args.apply:
            # Only restore an exact known original if a prior interruption left
            # the stable name temporarily absent. Do not guess or republish.
            originals = [a for a in release['assets'] if a.get('id') == J.get('original_asset_id') and a.get('name') == J.get('backup_asset_name')]
            require(len(originals) == 1 and sha(exact_asset_bytes(originals[0])) == J.get('tools_before_sha256'), 'Cannot safely recover missing stable asset')
            rename_asset(originals[0]['id'], ASSET)
            J['recovered_original_stable_asset'] = True
            raise ValueError('Interrupted publication restored original stable XML; inspect journal before continuing')
        require(len(stable) == 1, 'Existing tools XML asset is not unique')
        old_asset = stable[0]
        original = exact_asset_bytes(old_asset)
        _, old_ads = original_ads(original)
        if FEED_ID in old_ads:
            d = json.loads(DRAFT.read_text())
            urls = J.get('gallery_urls') or [BASE+r['asset_name']+'?v='+r['sha256'][:10] for r in d['gallery']]
            verify_received(original, d, urls)
            J.update(feed_url=URL, gallery_urls=urls, photos_in_feed=len(urls), public_feed_verified=True,
                     status='new_ad_in_existing_feed_pending_processing', profile_unchanged=True)
            readback()
            save(DRAFT)
            return 0
        if args.readback:
            raise ValueError('Stable tools XML does not contain this new Ad; readback will not publish it')
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
        J['publication_route'] = 'append_one_ad_to_existing_tools_xml_preserve_every_old_byte'
        J['existing_tools_ids'] = sorted(old_ads)
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
        if J.get('publication_intent'):
            require(sha(original) == J.get('tools_before_sha256') and old_asset['id'] == J.get('original_asset_id'), 'Uncertain prior publication; do not overwrite unexpected stable XML')
            J['prior_intent_reconciled_original_still_stable'] = True
        d = read_draft(normalize_encoding=True)
        J['draft_valid'] = True
        J.pop('draft_issue', None)
        draft_sha = sha(canonical(d))
        require(J.get('draft_sha256') in (None, draft_sha), 'Prepared draft changed; reconcile existing stage first')
        official = category_schema()
        validate_official_fields(d, official)
        J['official_category_schema'] = official
        other_feeds = {f['feed_url']: sha(public_bytes(f['feed_url'])) for f in before['feeds_data'] if f['feed_url'] != URL}
        image_urls = []
        for image in d['gallery']:
            raw = (ROOT/image['repository_path']).read_bytes()
            mime = 'image/png' if image['asset_name'].lower().endswith('.png') else 'image/jpeg'
            u = upload_asset(release, image['asset_name'], raw, mime)
            image_urls.append(u + '?v=' + image['sha256'][:10])
            require(sha(public_bytes(image_urls[-1])) == image['sha256'], 'Versioned media differs')
        raw = build(d, rows, image_urls, original, official)
        STAGE.parent.mkdir(parents=True, exist_ok=True)
        STAGE.write_bytes(raw)
        BACKUP.write_bytes(original)
        J.update(status='new_ad_xml_prepared', draft_sha256=draft_sha, gallery_urls=image_urls,
                 photos_in_feed=len(image_urls), feed_sha256=sha(raw), tools_before_sha256=sha(original),
                 original_asset_id=old_asset['id'], existing_ads_byte_identical=True,
                 other_feed_sha256=other_feeds, profile_fingerprint=config_sha)
        save(STAGE, BACKUP)
        J['complete_media_archive'] = media_archive(d, release)
        stage_name = 'avito_tools_feed.boards_20261127_' + sha(raw)[:16] + '.xml'
        upload_asset(release, stage_name, raw, 'application/xml')
        stage = release_asset(release, stage_name)
        J.update(status='new_ad_staged_not_published', feed_url=URL, staged_asset_id=stage['id'], stage_asset_name=stage_name)
        save()
        again = profile()
        require(account_config_fingerprint(again) == config_sha, 'Autoload account configuration changed concurrently')
        latest_release = gh('GET', '/releases/tags/product-photos').json()
        latest = release_asset(latest_release, ASSET)
        require(latest['id'] == old_asset['id'] and exact_asset_bytes(latest) == original, 'Tools XML changed concurrently')
        require(all(sha(public_bytes(u)) == h for u, h in other_feeds.items()), 'Another existing feed changed concurrently')
        backup_name = 'avito_tools_feed.before_boards_20261127_' + str(old_asset['id']) + '.xml'
        require(not any(a.get('name') == backup_name and a['id'] != old_asset['id'] for a in latest_release['assets']), 'Backup name conflict')
        J.update(status='append_publication_intent', publication_intent=True, backup_asset_name=backup_name)
        save()
        p.current_main()
        rename_asset(old_asset['id'], backup_name)
        try:
            rename_asset(stage['id'], ASSET)
        except Exception:
            check = gh('GET', '/releases/tags/product-photos').json()
            current_stable = [a for a in check['assets'] if a.get('name') == ASSET]
            if not current_stable:
                rename_asset(old_asset['id'], ASSET)
            raise
        final_release = gh('GET', '/releases/tags/product-photos').json()
        final_asset = release_asset(final_release, ASSET)
        require(final_asset['id'] == stage['id'] and exact_asset_bytes(final_asset) == raw, 'Stable appended XML bytes not verified')
        verify_received(raw, d, image_urls)
        after = profile()
        require(account_config_fingerprint(after) == config_sha, 'Existing profile changed')
        require(all(sha(public_bytes(u)) == h for u, h in other_feeds.items()), 'Other feed bytes changed')
        after_items = listing_snapshot()
        J['protected_existing_fields_unchanged'] = all(after_items.get(ident) == value for ident, value in current.items())
        J.update(status='append_published_pending_scheduled_processing', public_feed_verified=True,
                 profile_unchanged=True, other_feeds_unchanged=True, feed_publications=1, seller_writes=0,
                 seller_profile_writes=0, oneoff_import_triggered=False, new_publication_utc=p.stamp())
        d['status'] = 'SUBMITTED_VIA_EXISTING_AUTOLOAD_PENDING_PROCESSING'
        d['publication'].update(submitted=True, feed_url=URL, feed_id=FEED_ID)
        p.write_json(DRAFT, d)
        # Status-only fields are not part of future media/content identity.
        J['draft_sha256'] = sha(canonical(d))
        save(DRAFT)
        readback()
        save(DRAFT)
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
