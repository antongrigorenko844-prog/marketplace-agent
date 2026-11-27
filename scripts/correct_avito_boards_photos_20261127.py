"""Owner-authorized Title + Images correction of one existing Avito Ad.

Reuses the existing registered XML feed and its guarded release publication.
Never appends an Ad, changes an Avito binding, triggers imports, or writes seller
profile settings. Readback needs committed JSON only, not generated media files.
"""
from __future__ import annotations

import argparse
import copy
import io
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import avito_boards_listing_20261127 as base

p, gh, rename_asset = base.p, base.gh, base.rename_asset
require, sha, canonical = base.require, base.sha, base.canonical
FEED_ID, TARGET_ID = base.FEED_ID, 8580863654
TITLE = '0AM927769D/E/F/G/K'
OLD_TITLE = 'Электронные платы DSG7 DQ200 0AM D E F G K'
COUNT = 18
REQUEST = ROOT / 'data/avito_boards_photo_correction_20261127_request.json'
OUT = ROOT / 'data/avito_boards_photo_correction_20261127_result.json'
STAGE = ROOT / 'data/avito_boards_photo_correction_20261127.xml'
BACKUP = ROOT / 'data/avito_boards_photo_correction_20261127_before.xml'
TITLE_RE = re.compile(rb'<Title(?:\s[^>]*)?>.*?</Title>', re.S)
IMAGES_RE = re.compile(rb'<Images(?:\s[^>]*)?>.*?</Images>', re.S)
ASSET_PREFIX = 'avito-boards-originals-20261127-'
J = {}


def save(*paths):
    J['updated_utc'] = p.stamp()
    p.write_json(OUT, J)
    p.checkpoint([OUT, *paths], live=True)
    print('AVITO_BOARDS_CORRECTION', J.get('status'), flush=True)


def local_path(value):
    path = (ROOT / value).resolve()
    require(path.is_relative_to(ROOT) and path.is_file(), 'Required local file unavailable or outside repository')
    return path


def read_request():
    r = json.loads(REQUEST.read_text())
    require(r.get('version') == 1 and r.get('owner_authorized') is True, 'Owner correction authorization missing')
    require(r.get('feed_id') == FEED_ID and r.get('target_avito_id') == TARGET_ID, 'Correction identity differs')
    require(r.get('title') == TITLE and r.get('expected_before_title') == OLD_TITLE, 'Exact approved title differs')
    require(r.get('expected_price_rub') == base.PRICE == 36500, 'Approved unchanged price differs')
    require(r.get('media_reviewed') is True, 'Owner-reviewed original photos not confirmed')
    gallery = r['gallery']
    require(len(gallery) == COUNT and [x['position'] for x in gallery] == list(range(1, COUNT+1)), 'Exactly eighteen ordered photographs required')
    require([x['sku'] for x in gallery] == [base.ANCHOR]*6 + [s for s in base.SKUS[1:] for _ in range(3)], 'Five-card source order differs')
    require(len({x['source_repository_path'] for x in gallery}) == COUNT, 'Repeated source path')
    require(len({x['asset_name'] for x in gallery}) == COUNT, 'Repeated photo asset')
    for x in gallery:
        require(re.fullmatch(r'[0-9a-f]{64}', x['sha256']) is not None, 'Photo SHA invalid')
        require(x['asset_name'].startswith(ASSET_PREFIX) and '/' not in x['asset_name'] and x['asset_name'].endswith(x['sha256'][:12]+'.png'), 'Immutable correction asset name invalid')
        require(x.get('watermark_verified') is True, 'Photo watermark review missing')
    return r


def gallery_urls(r):
    return [base.BASE+x['asset_name']+'?v='+x['sha256'][:10] for x in r['gallery']]


def target_blob(raw):
    root, ads = base.original_ads(raw)
    require(FEED_ID in ads, 'Existing target Ad absent; never create a replacement')
    blob = ads[FEED_ID]
    ad = ET.fromstring(blob)
    require(ad.find('AvitoId') is None and ad.findtext('Id') == FEED_ID, 'Existing feed identity changed')
    require(ad.findtext('Price') == '36500' and ad.findtext('OEM') == base.ANCHOR, 'Protected price or primary OEM differs')
    require(ad.findtext('ListingFee') == 'Package' and ad.findtext('AdStatus') == 'Free', 'Protected fee/promotion differs')
    require(len(ad.findall('Title')) == 1 and len(ad.findall('Images')) == 1, 'Target mutable XML blocks not unique')
    require(len(TITLE_RE.findall(blob)) == 1 and len(IMAGES_RE.findall(blob)) == 1, 'Target XML block spelling unsupported')
    return root, ads, blob, ad


def masked(blob):
    return IMAGES_RE.sub(b'<Images/>', TITLE_RE.sub(b'<Title/>', blob))


def semantic(element):
    return {'tag': element.tag, 'attributes': sorted(element.attrib.items()), 'text': element.text,
            'tail': element.tail, 'children': [semantic(x) for x in element]}


def protected_fields(ad):
    return {'tag': ad.tag, 'attributes': sorted(ad.attrib.items()), 'text': ad.text,
            'children': [semantic(x) for x in ad if x.tag not in ('Title', 'Images')]}


def patch_xml(raw, r, urls):
    root, old_ads, blob, ad = target_blob(raw)
    require(ad.findtext('Title') == r['expected_before_title'], 'Target source title changed before correction')
    require(len(urls) == COUNT and len(set(urls)) == COUNT, 'Eighteen unique photo URLs required')
    title = ET.Element('Title'); title.text = r['title']
    images = ET.Element('Images')
    for url in urls:
        ET.SubElement(images, 'Image', {'url': url})
    changed = TITLE_RE.sub(lambda _: ET.tostring(title, encoding='utf-8'), blob, count=1)
    changed = IMAGES_RE.sub(lambda _: ET.tostring(images, encoding='utf-8'), changed, count=1)
    require(masked(changed) == masked(blob), 'Target bytes outside Title/Images changed')
    require(protected_fields(ET.fromstring(changed)) == protected_fields(ad), 'Parsed protected target fields changed')
    staged = base.AD_PATTERN.sub(lambda m: changed if ET.fromstring(m.group(0)).findtext('Id') == FEED_ID else m.group(0), raw)
    new_root, new_ads, _, new_ad = target_blob(staged)
    require(len(new_root) == len(root) and set(new_ads) == set(old_ads), 'XML listing set/count changed')
    require(all(new_ads[k] == v for k, v in old_ads.items() if k != FEED_ID), 'Another Ad changed')
    require(staged.replace(changed, blob, 1) == raw, 'Feed bytes outside exact target changed')
    require(new_ad.findtext('Title') == r['title'], 'Title roundtrip failed')
    require([x.get('url') for x in new_ad.findall('Images/Image')] == urls, 'Photo URL roundtrip failed')
    return staged, {'protected_target_bytes_sha256': sha(masked(blob)),
                    'protected_target_fields_sha256': sha(canonical(protected_fields(ad))),
                    'description_sha256': sha((ad.findtext('Description') or '').encode()),
                    'other_ads_sha256': {k: sha(v) for k, v in old_ads.items() if k != FEED_ID},
                    'ad_count': len(root)}


def verify_target(raw, r, urls, allow_old=False):
    root, ads, blob, ad = target_blob(raw)
    require(J.get('protected_target_bytes_sha256') == sha(masked(blob)), 'Target protected bytes differ from correction baseline')
    require(J.get('protected_target_fields_sha256') == sha(canonical(protected_fields(ad))), 'Target protected parsed fields differ')
    require(J.get('ad_count') == len(root) and J.get('other_ads_sha256') == {k: sha(v) for k, v in ads.items() if k != FEED_ID}, 'Other XML Ad content differs')
    received_urls = [x.get('url') for x in ad.findall('Images/Image')]
    desired = ad.findtext('Title') == r['title'] and received_urls == urls
    if not desired:
        require(allow_old and ad.findtext('Title') == r['expected_before_title'] and received_urls == J.get('original_gallery_urls'), 'Unexpected target title or photo revision')
    return desired


def validate_media(r):
    manifest = json.loads(local_path(r['media_manifest_path']).read_text())
    expected = json.loads(local_path(r['media_expected_path']).read_text())
    source_map = json.loads(local_path(r['media_source_map_path']).read_text())
    require(manifest['renderer']['font_sha256'] == expected['font_sha256'] == r['renderer']['font_sha256'], 'Reviewed watermark font differs')
    require(manifest['source_count'] == manifest['gallery_count'] == COUNT and len(expected['gallery']) == COUNT, 'Reviewed media count differs')
    require(manifest.get('all_source_files_prepared') is True and manifest.get('no_photo_limit_assumed') is True, 'Source preparation proof absent')
    require(len(source_map['sources']) == COUNT and len(manifest['sources']) == COUNT, 'Source mapping incomplete')
    enc_changed = False
    for i, row in enumerate(r['gallery']):
        source, actual, approved, original = source_map['sources'][i], manifest['gallery'][i], expected['gallery'][i], manifest['sources'][i]
        require(row['position'] == actual['position'] == approved['position'] == i+1, 'Reviewed source order differs')
        for key in ('sku', 'source_repository_path', 'source_url', 'source_sha256'):
            require(row[key] == actual[key], 'Request/source identity differs: '+key)
        require(row['sku'] == source['sku'] and row['source_repository_path'] == source['repository_path'] and row['source_sha256'] == source['sha256'] and row['source_url'] == source['source_url'], 'Source map differs from requested gallery')
        require(original['sha256'] == source['sha256'] and original['repository_path'] == source['repository_path'], 'Prepared original identity differs')
        require(sha(local_path(row['source_repository_path']).read_bytes()) == row['source_sha256'], 'Original source file digest differs')
        proof = original['proof']
        require(all(proof.get(k) is True for k in ('outside_watermark_pixels_identical', 'dimensions_identical', 'no_resize', 'no_crop', 'no_layout_change', 'no_new_captions', 'alpha_preserved', 'color_profile_preserved')), 'Original-only watermark proof missing')
        require(proof.get('watermark_count_added') == 1 and actual.get('outside_mask_identical') is True and actual.get('watermarks_added') == 1, 'Exactly one watermark required')
        path = local_path(row['repository_path'])
        require(path == (local_path(r['media_manifest_path']).parent/actual['protected_path']).resolve(), 'Prepared photo path differs')
        raw = path.read_bytes()
        require(sha(raw) == actual['sha256'] and path.name == approved['filename'], 'Generated photo manifest differs')
        with base.Image.open(io.BytesIO(raw)) as image:
            image.load()
            require(list(image.size) == row['dimensions'] == approved['dimensions'] == actual['dimensions'], 'Reviewed dimensions differ')
            require(sha(image.convert('RGBA').tobytes()) == row['pixel_sha256_rgba'] == approved['pixel_sha256_rgba'] == actual['pixel_sha256_rgba'], 'Reviewed watermark photo pixels differ')
        require(len(raw) <= 25*1024*1024, 'Individual image exceeds established file size limit')
        mask_path = local_path(str(Path(r['media_manifest_path']).parent/original['mask_path']))
        require(sha(mask_path.read_bytes()) == actual['mask_sha256'] == approved['mask_sha256'], 'Reviewed watermark mask differs')
        actual_sha = sha(raw)
        if actual_sha != row['sha256']:
            row['sha256'] = actual_sha
            row['asset_name'] = ASSET_PREFIX+path.stem+'-'+actual_sha[:12]+'.png'
            enc_changed = True
    if enc_changed:
        p.write_json(REQUEST, r)
        J['lossless_encoding_normalized_after_exact_rgba_verification'] = True
        save(REQUEST)
    J['media_verification'] = {'source_count': COUNT, 'photos': COUNT, 'one_watermark_each': True,
                               'no_new_layout_crop_resize_captions': True, 'exact_reviewed_rgba': True,
                               'font_sha256': manifest['renderer']['font_sha256']}
    return source_map


def verify_current_source_cards(primary, r, source_map):
    rows = base.source_rows(primary)
    for sku in base.SKUS:
        current = str(rows[sku][base.af.COL_IMAGE_URLS-1] or '').splitlines()
        current = [s.strip() for s in current if s.strip()]
        requested = [x['source_url'] for x in r['gallery'] if x['sku'] == sku]
        mapped = source_map['avito_feed_evidence'][sku]['photo_urls']
        require(current == requested == mapped, 'Current source-card image URL/order changed: '+sku)
    J['current_source_card_urls_verified'] = True
    J['current_source_primary_sha256'] = sha(primary)


def exact_binding(required=True):
    binding = {'matches': []}
    try:
        response = base.autoload_read('/autoload/v2/items/avito_ids', params={'query': FEED_ID})
        binding['matches'] = [{k: row.get(k) for k in ('ad_id', 'avito_id')}
                              for row in response.get('items', []) if str(row.get('ad_id')) == FEED_ID]
    except Exception as exc:
        binding.update(base.read_error_details(exc))
    J['exact_feed_binding'] = binding
    ids = {int(x['avito_id']) for x in binding['matches'] if str(x.get('avito_id') or '').isdigit()}
    require(not ids or ids == {TARGET_ID}, 'Exact feed binding points to an unexpected Avito ID')
    if required:
        require(ids == {TARGET_ID}, 'Exact autoload binding unavailable; no publication')
    return ids


def reports(r, urls):
    evidence, bindings = [], set()
    for kind in ('current', 'last_successful'):
        item = {'kind': kind, 'matches': [], 'received_corrected_xml': False}
        try:
            report = base.autoload_read('/autoload/v4/uploads/' + kind)
            item.update({k: report.get(k) for k in ('upload_id', 'status', 'started_at')})
        except Exception as exc:
            item.update(base.read_error_details(exc))
            evidence.append(item)
            if item.get('http_status') == 429:
                break
            continue
        try:
            report, rows = base.report_items(kind, first=report)
            matches = [x for x in rows if str(x.get('ad_id')) == FEED_ID]
            for row in matches:
                ident = row.get('avito_id')
                if str(ident or '').isdigit():
                    bindings.add(int(ident))
            item['matches'] = [{k: x.get(k) for k in ('ad_id', 'avito_id', 'avito_status', 'status', 'section', 'messages', 'url')} for x in matches]
        except Exception as exc:
            item.update(base.read_error_details(exc))
        for feed in report.get('feed_urls', []):
            u = feed.get('url', '')
            parsed = urlsplit(u)
            if parsed.scheme != 'https' or parsed.hostname != 'api.avito.ru' or not parsed.path.startswith('/autoload/v2/feed/content/'):
                continue
            try:
                response = base.requests.get(u, headers={'Authorization': base.av._headers()['Authorization']}, timeout=30, allow_redirects=False)
                if response.ok:
                    root = ET.fromstring(response.content)
                    hits = [x for x in root.findall('Ad') if x.findtext('Id') == FEED_ID]
                    if len(hits) == 1:
                        ad = hits[0]
                        actual_urls = [x.get('url') for x in ad.findall('Images/Image')]
                        item['received_target_title'] = ad.findtext('Title')
                        item['received_target_photo_count'] = len(actual_urls)
                        if ad.findtext('Title') == r['title'] and actual_urls == urls:
                            require(ad.find('AvitoId') is None and ad.findtext('Price') == '36500' and ad.findtext('OEM') == base.ANCHOR, 'Accepted target identity/price differs')
                            require(sha(canonical(protected_fields(ad))) == J.get('protected_target_fields_sha256'), 'Accepted target protected fields differ')
                            item['received_corrected_xml'] = True
            except ValueError:
                raise
            except Exception as exc:
                item['content_read_error_type'] = type(exc).__name__
        evidence.append(item)
        if item.get('http_status') == 429:
            break
    require(not bindings or bindings == {TARGET_ID}, 'Feed is bound to an unexpected Avito ID')
    return evidence, bindings


def sync_base(r, raw, status):
    draft = json.loads(base.DRAFT.read_text())
    journal = json.loads(base.OUT.read_text())
    require(draft['price_rub'] == 36500 and draft['oem'] == base.ANCHOR, 'Canonical protected draft identity differs')
    require(sha(draft['description'].encode()) == J['description_sha256'], 'Canonical description changed')
    require(journal.get('new_avito_id') == TARGET_ID and journal.get('feed_id') == FEED_ID, 'Canonical assigned Avito ID differs')
    draft['title'] = r['title']
    draft['gallery'] = copy.deepcopy(r['gallery'])
    draft['media_manifest_path'] = r['media_manifest_path']
    draft['media_expected_path'] = r['media_expected_path']
    draft['media_reviewed'] = True
    draft['media_summary'] = {'unique_source_files': COUNT, 'watermarked_individuals': COUNT, 'gallery_images': COUNT,
                              'source': 'exact existing five Avito source-card galleries', 'new_collages_created': False,
                              'all_requested_sources_retained': True}
    draft['status'] = status.upper()
    draft['publication'].update(submitted=True, avito_id=TARGET_ID, feed_id=FEED_ID, feed_url=base.URL,
                                status=status, owner_authorized_photo_title_correction=True,
                                no_other_listing_changes=True, autoload_content_verified=J.get('autoload_content_verified', False),
                                live_gallery_verified=False)
    draft['publication'].pop('no_existing_listing_changes', None)
    journal.update(new_avito_id=TARGET_ID, gallery_urls=gallery_urls(r), photos_in_feed=COUNT, feed_sha256=sha(raw),
                   status=status, public_feed_verified=True, autoload_content_verified=J.get('autoload_content_verified', False),
                   live_gallery_verified=False, photo_title_correction_journal=str(OUT.relative_to(ROOT)))
    journal['new_listing'] = copy.deepcopy(J.get('target_listing'))
    journal['media_summary'] = {'source_count': COUNT, 'unique_source_count': COUNT, 'eligible_source_count': COUNT,
                                'gallery_count': COUNT, 'all_eligible_sources_in_gallery': True,
                                'all_originals_retained': True, 'all_individuals_watermarked': True}
    if journal.get('media_quarantined'):
        journal.setdefault('historical_media_quarantined', []).extend(journal['media_quarantined'])
    journal['media_quarantined'] = []
    if journal.get('reports') and 'historical_reports_before_photo_correction' not in journal:
        journal['historical_reports_before_photo_correction'] = journal['reports']
    journal['reports'] = copy.deepcopy(J.get('reports', []))
    journal['exact_feed_binding'] = copy.deepcopy(J.get('exact_feed_binding', {}))
    journal['draft_sha256'] = sha(canonical(draft))
    journal['updated_utc'] = p.stamp()
    p.write_json(base.DRAFT, draft)
    p.write_json(base.OUT, journal)


def readback(r, raw):
    urls = gallery_urls(r)
    desired_public = verify_target(raw, r, urls, allow_old=True)
    evidence, report_bindings = reports(r, urls)
    bindings = exact_binding(required=False)
    require(not report_bindings or report_bindings == {TARGET_ID}, 'Report binding differs from approved target')
    J['reports'] = evidence
    J['binding_verified'] = bindings == {TARGET_ID}
    J['public_feed_corrected'] = desired_public
    current = base.listing_snapshot()
    found = current.get(TARGET_ID)
    J['target_listing'] = found
    J['live_gallery_verified'] = False
    J['autoload_content_verified'] = any(x.get('received_corrected_xml') for x in evidence)
    J['accepted_xml_photo_count'] = COUNT if J['autoload_content_verified'] else None
    if not found:
        J['status'] = 'target_listing_temporarily_unavailable'
    else:
        require(int(found.get('price') or 0) == 36500, 'Target price changed unexpectedly')
        require(found.get('title') in (OLD_TITLE, TITLE), 'Target title changed to an unapproved value')
        if found.get('status') == 'active':
            if found.get('title') == OLD_TITLE:
                J['status'] = 'active_pending_title_update'
            elif J['autoload_content_verified'] and bindings == {TARGET_ID}:
                J['status'] = 'active_title_and_received_xml_verified'
            else:
                J['status'] = 'active_title_verified_content_pending'
        else:
            J['status'] = 'assigned_pending_or_not_active'
    for evidence_row in evidence:
        if not evidence_row.get('received_corrected_xml'):
            continue
        for row in evidence_row.get('matches', []):
            statuses = {str(row.get(k) or '').lower() for k in ('status', 'avito_status')}
            section = row.get('section') or {}
            section_slug = str(section.get('slug') or '').lower() if isinstance(section, dict) else str(section).lower()
            errors = any(isinstance(m, dict) and str(m.get('type') or m.get('severity') or '').lower() == 'error'
                         for m in row.get('messages') or [])
            if statuses.intersection({'blocked', 'rejected', 'error', 'errors', 'failed'}) or section_slug.startswith('error') or errors:
                J['status'] = 'corrected_revision_blocked_or_rejected'
    J.pop('error', None); J.pop('error_type', None)
    if desired_public:
        sync_base(r, raw, J['status'])
        save(base.DRAFT, base.OUT)
    else:
        save()


def main():
    global J
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--readback', action='store_true')
    args = parser.parse_args()
    require(os.environ.get('GITHUB_REF') == 'refs/heads/main', 'Main only')
    p.current_main()
    J = json.loads(OUT.read_text()) if OUT.exists() else {'version': 1, 'feed_id': FEED_ID, 'target_avito_id': TARGET_ID,
        'started_utc': p.stamp(), 'status': 'prepared', 'seller_profile_writes': 0, 'forced_imports': 0}
    require(J.get('feed_id') == FEED_ID and J.get('target_avito_id') == TARGET_ID, 'Saved correction scope differs')
    try:
        r = read_request()
        before = base.profile()
        require(len(before['feeds_data']) == 3 and [x['feed_url'] for x in before['feeds_data']].count(base.URL) == 1, 'Three existing feed registrations required')
        config_sha = base.account_config_fingerprint(before)
        release = gh('GET', '/releases/tags/product-photos').json()
        stable = [x for x in release['assets'] if x.get('name') == base.ASSET]
        if not stable and J.get('publication_intent') and args.apply:
            old = [x for x in release['assets'] if x.get('id') == J.get('original_asset_id') and x.get('name') == J.get('backup_asset_name')]
            require(len(old) == 1 and sha(base.exact_asset_bytes(old[0])) == J.get('before_sha256'), 'Cannot restore uncertain original XML safely')
            p.current_main(); rename_asset(old[0]['id'], base.ASSET)
            J['recovered_original_stable_asset'] = True
            raise ValueError('Interrupted correction restored original XML; inspect journal before continuing')
        require(len(stable) == 1, 'Current tools XML asset not unique')
        asset = stable[0]
        original = base.exact_asset_bytes(asset)
        _, _, blob, ad = target_blob(original)
        desired = ad.findtext('Title') == TITLE and [x.get('url') for x in ad.findall('Images/Image')] == gallery_urls(r)
        if args.readback:
            require(J.get('protected_target_bytes_sha256'), 'Correction baseline unavailable for readback')
            readback(r, original)
            return 0
        if desired:
            verify_target(original, r, gallery_urls(r))
            exact_binding()
            J['target_listing'] = base.listing_snapshot().get(TARGET_ID)
            J['status'] = 'corrected_feed_published_pending_processing'
            sync_base(r, original, J['status'])
            save(base.DRAFT, base.OUT)
            return 0
        require(ad.findtext('Title') == OLD_TITLE, 'Unexpected current target title; no write')
        if J.get('publication_intent'):
            require(asset['id'] == J.get('original_asset_id') and sha(original) == J.get('before_sha256'), 'Prior correction publication uncertain; no replay')
        current = base.listing_snapshot()
        found = current.get(TARGET_ID)
        require(found and found.get('status') == 'active' and found.get('title') == OLD_TITLE and int(found.get('price') or 0) == 36500, 'Existing active target title/price not confirmed')
        draft = json.loads(base.DRAFT.read_text())
        base_journal = json.loads(base.OUT.read_text())
        require(base_journal.get('new_avito_id') == TARGET_ID and (draft.get('publication') or {}).get('avito_id') == TARGET_ID, 'Canonical assigned ID not confirmed')
        base.verify_received(original, draft, base_journal['gallery_urls'])
        exact_binding()
        J['binding_before'] = {'feed_id': FEED_ID, 'avito_id': TARGET_ID}
        source_map = validate_media(r)
        primary = base.public_bytes(base.PRIMARY)
        verify_current_source_cards(primary, r, source_map)
        request_sha = sha(canonical(r))
        require(J.get('request_sha256') in (None, request_sha), 'Prepared correction request changed; reconcile prior stage')
        other_feeds = {x['feed_url']: sha(base.public_bytes(x['feed_url'])) for x in before['feeds_data'] if x['feed_url'] != base.URL}
        urls = gallery_urls(r)
        raw, proof = patch_xml(original, r, urls)
        STAGE.parent.mkdir(parents=True, exist_ok=True)
        STAGE.write_bytes(raw); BACKUP.write_bytes(original)
        J.update(proof)
        J.update(status='prepared_exact_title_and_images_patch', request_sha256=request_sha, before_sha256=sha(original),
                 target_sha256=sha(raw), original_asset_id=asset['id'], original_gallery_urls=[x.get('url') for x in ad.findall('Images/Image')],
                 gallery_urls=urls, photos_in_feed=COUNT, profile_fingerprint=config_sha, other_feed_sha256=other_feeds,
                 other_listing_snapshot_sha256=sha(canonical({k:v for k,v in current.items() if k != TARGET_ID})),
                 price_unchanged=True, description_unchanged=True, other_ads_byte_identical=True)
        save(STAGE, BACKUP)
        for row, url in zip(r['gallery'], urls):
            hosted = base.upload_asset(release, row['asset_name'], local_path(row['repository_path']).read_bytes(), 'image/png')
            require(url == hosted+'?v='+row['sha256'][:10] and sha(base.public_bytes(url)) == row['sha256'], 'Immutable uploaded photo readback differs')
        stage_name = 'avito_tools_feed.photo_correction_20261127_'+sha(raw)[:16]+'.xml'
        base.upload_asset(release, stage_name, raw, 'application/xml')
        stage = base.release_asset(release, stage_name)
        J.update(status='staged_not_published', stage_asset_name=stage_name, staged_asset_id=stage['id'])
        save()
        require(base.account_config_fingerprint(base.profile()) == config_sha, 'Autoload profile changed concurrently')
        latest_release = gh('GET', '/releases/tags/product-photos').json()
        latest = base.release_asset(latest_release, base.ASSET)
        require(latest['id'] == asset['id'] and base.exact_asset_bytes(latest) == original, 'Current XML changed concurrently')
        require(all(sha(base.public_bytes(u)) == h for u,h in other_feeds.items()), 'Other feed changed concurrently')
        latest_target = base.listing_snapshot().get(TARGET_ID)
        require(latest_target == found, 'Target listing changed before publication')
        exact_binding()
        backup_name = 'avito_tools_feed.before_photo_correction_20261127_'+str(asset['id'])+'.xml'
        require(not any(x.get('name') == backup_name and x['id'] != asset['id'] for x in latest_release['assets']), 'Correction backup asset name conflict')
        J.update(status='correction_publication_intent', publication_intent=True, backup_asset_name=backup_name)
        save()
        p.current_main(); rename_asset(asset['id'], backup_name)
        try:
            rename_asset(stage['id'], base.ASSET)
        except Exception:
            observed = gh('GET', '/releases/tags/product-photos').json()
            if not any(x.get('name') == base.ASSET for x in observed['assets']):
                rename_asset(asset['id'], base.ASSET)
            raise
        final = base.release_asset(gh('GET', '/releases/tags/product-photos').json(), base.ASSET)
        require(final['id'] == stage['id'] and base.exact_asset_bytes(final) == raw, 'Corrected stable XML readback differs')
        verify_target(raw, r, urls)
        require(base.account_config_fingerprint(base.profile()) == config_sha, 'Autoload profile changed')
        require(all(sha(base.public_bytes(u)) == h for u,h in other_feeds.items()), 'Another feed changed')
        after = base.listing_snapshot()
        J['target_listing'] = after.get(TARGET_ID)
        J['other_listing_fields_unchanged'] = all(after.get(k) == v for k,v in current.items() if k != TARGET_ID)
        J.update(status='corrected_feed_published_pending_processing', published_utc=p.stamp(), published_asset_id=final['id'],
                 public_feed_corrected=True, profile_unchanged=True, other_feeds_unchanged=True,
                 feed_corrections=1, duplicate_ad_created=False, autoload_content_verified=False, live_gallery_verified=False)
        sync_base(r, raw, J['status'])
        save(base.DRAFT, base.OUT)
        return 0
    except Exception as exc:
        J.update(status='blocked_or_unverified', error_type=type(exc).__name__)
        if isinstance(exc, ValueError):
            J['error'] = str(exc)[:350]
        save()
        return 1


if __name__ == '__main__':
    sys.exit(main())
