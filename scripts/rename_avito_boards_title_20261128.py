"""Rename only Title on existing Avito 8580863654; never import or edit media."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import sys
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import avito_boards_listing_20261127 as base

p, gh, rename_asset = base.p, base.gh, base.rename_asset
require, sha, canonical = base.require, base.sha, base.canonical
FEED_ID, TARGET_ID = base.FEED_ID, 8580863654
OLD_TITLE = 'Электронная плата мехатроника DQ200 DSG7'
TITLE = 'Электронная плата мехатроника DQ200 DSG7 0AM927769D/E/F/G/K'
OUT = ROOT / 'data/avito_boards_title_20261128_result.json'
STAGE = ROOT / 'data/avito_boards_title_20261128.xml'
BACKUP = ROOT / 'data/avito_boards_title_20261128_before.xml'
TITLE_RE = re.compile(rb'<Title(?:\s[^>]*)?>.*?</Title>', re.S)
J = {}


def save(*paths):
    J['updated_utc'] = p.stamp()
    p.write_json(OUT, J)
    p.checkpoint([OUT, *paths], live=True)
    print('AVITO_BOARDS_TITLE', J.get('status'), flush=True)


def target(raw):
    _, ads = base.original_ads(raw)
    require(FEED_ID in ads, 'Existing target absent; never append an Ad')
    blob = ads[FEED_ID]
    ad = ET.fromstring(blob)
    require(ad.find('AvitoId') is None and ad.findtext('Id') == FEED_ID, 'Feed identity differs')
    require(ad.findtext('Price') == '36500' and ad.findtext('OEM') == base.ANCHOR, 'Price/OEM differs')
    require(ad.findtext('ListingFee') == 'Package' and ad.findtext('AdStatus') == 'Free', 'Fee/promotion differs')
    matches = list(TITLE_RE.finditer(blob))
    require(len(matches) == len(ad.findall('Title')) == 1, 'Title is not unique')
    require(ad.findtext('Title') in (OLD_TITLE, TITLE), 'Unexpected target title; no write')
    require(raw.count(blob) == 1, 'Target byte range is not unique')
    start = raw.index(blob)
    return ad, start + matches[0].start(), start + matches[0].end()


def protected_feed(raw):
    _, start, end = target(raw)
    return raw[:start] + b'<Title/>' + raw[end:]


def patch_title(raw):
    ad, start, end = target(raw)
    require(ad.findtext('Title') == OLD_TITLE, 'Source title already changed; read back')
    title = ET.Element('Title')
    title.text = TITLE
    replacement = ET.tostring(title, encoding='utf-8')
    changed = raw[:start] + replacement + raw[end:]
    require(changed[:start] == raw[:start] and changed[start+len(replacement):] == raw[end:], 'Non-Title bytes changed')
    require(protected_feed(changed) == protected_feed(raw), 'Protected feed bytes changed')
    require(target(changed)[0].findtext('Title') == TITLE, 'Title roundtrip failed')
    return changed


def exact_binding():
    response = base.autoload_read('/autoload/v2/items/avito_ids', params={'query': FEED_ID})
    matches = [{k: row.get(k) for k in ('ad_id', 'avito_id')}
               for row in response.get('items', []) if str(row.get('ad_id')) == FEED_ID]
    require(len(matches) == 1 and str(matches[0].get('avito_id')) == str(TARGET_ID), 'Exact existing Avito binding not confirmed')
    J['exact_feed_binding'] = {'matches': matches}


def live_target(*, enforce_expected=True):
    found = base.listing_snapshot().get(TARGET_ID)
    require(found and int(found.get('price') or 0) == 36500, 'Existing target/price unavailable')
    if enforce_expected:
        require(found.get('title') in (OLD_TITLE, TITLE), 'Live title is neither approved old nor new title')
    return found


def protected_draft(draft):
    return sha(canonical({k: v for k, v in draft.items() if k not in ('title', 'publication')}))


def sync_canonical(raw, found):
    draft = json.loads(base.DRAFT.read_text())
    journal = json.loads(base.OUT.read_text())
    require(draft['title'] in (OLD_TITLE, TITLE), 'Canonical title changed unexpectedly')
    require((draft.get('publication') or {}).get('avito_id') == journal.get('new_avito_id') == TARGET_ID, 'Canonical assigned ID differs')
    require(journal.get('feed_id') == FEED_ID and protected_draft(draft) == J['protected_draft_sha256'], 'Canonical protected content changed')
    draft['title'] = TITLE
    summary = {'journal': str(OUT.relative_to(ROOT)), 'title': TITLE, 'previous_title': OLD_TITLE,
               'status': J['status'], 'public_feed_verified': True,
               'live_title_verified': found.get('title') == TITLE,
               'observed_live_title': found.get('title'), 'updated_utc': p.stamp()}
    draft['publication']['title_update'] = summary
    journal['title_update'] = summary
    journal['feed_sha256'] = sha(raw)
    journal['draft_sha256'] = sha(canonical(draft))
    if isinstance(journal.get('new_listing'), dict):
        journal['new_listing']['title'] = found['title']
    p.write_json(base.DRAFT, draft)
    p.write_json(base.OUT, journal)
    save(base.DRAFT, base.OUT)


def readback(raw):
    ad, _, _ = target(raw)
    exact_binding()
    found = live_target(enforce_expected=False)
    desired = ad.findtext('Title') == TITLE
    if J.get('protected_feed_sha256'):
        require(sha(protected_feed(raw)) == J['protected_feed_sha256'], 'Non-Title feed bytes changed')
        require(sha(raw) == J['target_sha256' if desired else 'before_sha256'], 'Feed bytes differ from saved revision')
    require(not desired or J.get('protected_draft_sha256'), 'Changed feed has no saved baseline; no replay')
    J.update(public_feed_title=ad.findtext('Title'), target_listing=found,
             live_title_verified=found.get('title') == TITLE, public_feed_verified=desired)
    J['observed_live_title'] = found.get('title')
    J['requested_title'] = TITLE
    if not desired:
        J['status'] = 'title_not_published'
    elif found.get('status') == 'active' and found.get('title') == TITLE:
        J['status'] = 'active_title_verified'
    elif found.get('title') == OLD_TITLE:
        J['status'] = 'title_published_pending_processing'
    else:
        J['status'] = 'title_mismatch_observed'
    J.pop('error', None)
    J.pop('error_type', None)
    if desired:
        sync_canonical(raw, found)
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
    require(len(TITLE) == 59, 'Exact requested title changed')
    p.current_main()
    J = json.loads(OUT.read_text()) if OUT.exists() else {
        'feed_id': FEED_ID, 'target_avito_id': TARGET_ID, 'title': TITLE,
        'expected_before_title': OLD_TITLE, 'price_rub': 36500, 'started_utc': p.stamp(),
        'seller_profile_writes': 0, 'forced_imports': 0, 'status': 'prepared'}
    require(J.get('feed_id') == FEED_ID and J.get('target_avito_id') == TARGET_ID
            and J.get('title') == TITLE and J.get('expected_before_title') == OLD_TITLE, 'Saved scope differs')
    try:
        profile = base.profile()
        J['autoload_schedule'] = profile['schedule']
        J['title_submission_policy'] = 'owner_exact_verbatim_no_truncation'
        require([f['feed_url'] for f in profile['feeds_data']].count(base.URL) == 1, 'Existing tools feed not uniquely registered')
        profile_sha = base.account_config_fingerprint(profile)
        release = gh('GET', '/releases/tags/product-photos').json()
        asset = base.release_asset(release, base.ASSET)
        original = base.exact_asset_bytes(asset)
        ad, _, _ = target(original)
        if args.readback or ad.findtext('Title') == TITLE:
            readback(original)
            return 0
        require(not J.get('publication_intent'), 'Prior publication intent exists; read back and reconcile, never replay')
        exact_binding()
        found = live_target()
        require(found.get('status') == 'active' and found.get('title') == OLD_TITLE, 'Existing active old title required')
        draft = json.loads(base.DRAFT.read_text())
        journal = json.loads(base.OUT.read_text())
        require(draft['title'] == OLD_TITLE and journal.get('feed_id') == FEED_ID, 'Canonical source title/feed differs')
        require(journal.get('new_avito_id') == (draft.get('publication') or {}).get('avito_id') == TARGET_ID, 'Canonical assigned ID differs')
        base.verify_received(original, draft, journal['gallery_urls'])
        raw = patch_title(original)
        other_feeds = {f['feed_url']: sha(base.public_bytes(f['feed_url'])) for f in profile['feeds_data'] if f['feed_url'] != base.URL}
        STAGE.parent.mkdir(parents=True, exist_ok=True)
        STAGE.write_bytes(raw)
        BACKUP.write_bytes(original)
        stage_name = 'avito_tools_feed.title_20261128_' + sha(raw)[:16] + '.xml'
        backup_name = 'avito_tools_feed.before_title_20261128_' + str(asset['id']) + '.xml'
        J.update(status='title_patch_prepared', before_sha256=sha(original), target_sha256=sha(raw),
                 protected_feed_sha256=sha(protected_feed(original)), protected_draft_sha256=protected_draft(draft),
                 original_asset_id=asset['id'], stage_asset_name=stage_name, backup_asset_name=backup_name,
                 profile_fingerprint=profile_sha, other_feed_sha256=other_feeds,
                 only_title_changed=True, images_byte_identical=True, other_ads_byte_identical=True)
        save(STAGE, BACKUP)
        base.upload_asset(release, stage_name, raw, 'application/xml')
        stage = base.release_asset(release, stage_name)
        J.update(staged_asset_id=stage['id'], status='staged_not_published')
        save()
        latest_release = gh('GET', '/releases/tags/product-photos').json()
        latest = base.release_asset(latest_release, base.ASSET)
        require(latest['id'] == asset['id'] and base.exact_asset_bytes(latest) == original, 'Stable XML changed concurrently')
        require(base.account_config_fingerprint(base.profile()) == profile_sha, 'Profile changed concurrently')
        require(all(sha(base.public_bytes(u)) == h for u, h in other_feeds.items()), 'Another feed changed concurrently')
        require(live_target() == found, 'Live target changed before publication')
        require(not any(a.get('name') == backup_name for a in latest_release['assets']), 'Backup asset name conflict')
        J.update(publication_intent=True, status='title_publication_intent')
        save()
        p.current_main()
        exact_binding()
        rename_asset(asset['id'], backup_name)
        try:
            rename_asset(stage['id'], base.ASSET)
        except Exception:
            observed = gh('GET', '/releases/tags/product-photos').json()
            if not any(a.get('name') == base.ASSET for a in observed['assets']):
                rename_asset(asset['id'], base.ASSET)
            raise
        final = base.release_asset(gh('GET', '/releases/tags/product-photos').json(), base.ASSET)
        require(final['id'] == stage['id'] and base.exact_asset_bytes(final) == raw, 'Published XML readback differs')
        require(base.account_config_fingerprint(base.profile()) == profile_sha, 'Profile changed')
        require(all(sha(base.public_bytes(u)) == h for u, h in other_feeds.items()), 'Another feed changed')
        J.update(published_utc=p.stamp(), published_asset_id=final['id'], profile_unchanged=True, other_feeds_unchanged=True)
        readback(raw)
        return 0
    except Exception as exc:
        J.update(status='blocked_or_unverified', error_type=type(exc).__name__)
        if isinstance(exc, ValueError):
            J['error'] = str(exc)[:350]
        save()
        return 1


if __name__ == '__main__':
    sys.exit(main())
