"""Promote the exact reviewed Avito XLSX. Read seller titles before and after."""
from pathlib import Path
import sys
import time
from hashlib import sha256
from io import BytesIO
import json
import requests
from openpyxl import load_workbook

sys.path.insert(0, str(Path.cwd()))
from avito_client import list_all_items
from content_manifest import load
from scripts.apply_dq200_prices_20261120 import get_avito_asset, gh, rename_asset, STABLE_AVITO_URL

BEFORE = '736ba41724e57489d002a05b7c61159ea554fe9437b5fcebd09bf157c6b6d216'
AFTER = 'b5b91537c816fa7cc2e689845faf692a853e13e0335742e7e7fa73558339c5cd'
STABLE_NAME = 'avito_feed.xlsx'
TARGETS = {
    '0AM927769D': 8302770239,
    '0AM927769E': 8433757157,
    '0AM927769F': 8575177586,
    '0AM927769G': 8546015515,
    '0AM927769K': 8469152964,
    'fpc-shleif-dq200': 8576744875,
    'shleif-platy-dq200': 8547332649,
    'osnovanie-platy-dq200': 8403945697,
}


def source_titles(blob):
    with BytesIO(blob) as stream:
        book = load_workbook(stream, read_only=True, data_only=True)
        try:
            rows = [r for r in book['Объявления'].values if r[3] in TARGETS]
            if len(rows) != len(TARGETS) or {r[3] for r in rows} != TARGETS.keys():
                raise ValueError('Wrong row identities')
            return {r[3]: r[23] for r in rows}
        finally:
            book.close()


def published_titles():
    ads = {int(a['id']): a for a in list_all_items(statuses=['active'])}
    if any(item_id not in ads for item_id in TARGETS.values()):
        raise ValueError('Target no longer active: preserving publication')
    return {sku: ads[item_id]['title'] for sku, item_id in TARGETS.items()}


def verified_download(url, content):
    for delay in (0, 2, 5, 10):
        time.sleep(delay)
        try:
            r = requests.get(url, params={'v_titles': time.time_ns()}, timeout=25)
            if r.ok and r.content == content:
                return
        except requests.RequestException:
            pass
    raise RuntimeError('Asset readback differs')


def main():
    proposed = Path('data/avito_feed.xlsx').read_bytes()
    if sha256(proposed).hexdigest() != AFTER:
        raise ValueError('Reviewed file differs')
    expected = source_titles(proposed)
    manifest = load()
    if {s: manifest[s]['text']['avito']['title'] for s in TARGETS} != expected:
        raise ValueError('Persistent channel source differs')
    release, asset, current = get_avito_asset()
    actual = published_titles()
    print('AVITO_TITLES_BEFORE', json.dumps(actual, ensure_ascii=False), flush=True)
    if current != proposed:
        if sha256(current).hexdigest() != BEFORE:
            raise ValueError('Stable release changed; leaving existing asset in place')
        old = source_titles(current)
        if any(actual[s] not in (old[s], expected[s]) for s in TARGETS):
            raise ValueError('Seller modified a target title; preserving publication')
        stage_name = 'avito_feed.reviewed_titles_' + AFTER[:12] + '.xlsx'
        stage = next((a for a in release['assets'] if a['name'] == stage_name), None)
        if stage is None:
            stage = gh('POST', release['upload_url'].partition('{')[0], params={'name': stage_name},
                       data=proposed, headers={'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}).json()
        verified_download(stage['browser_download_url'], proposed)
        _, last_asset, latest = get_avito_asset()
        if last_asset['id'] != asset['id'] or latest != current:
            raise ValueError('Public asset changed while staging')
        backup = 'avito_feed.before_titles_' + str(asset['id']) + '.xlsx'
        rename_asset(asset['id'], backup)
        try:
            rename_asset(stage['id'], STABLE_NAME)
        except Exception:
            rename_asset(asset['id'], STABLE_NAME)
            raise
    verified_download(STABLE_AVITO_URL, proposed)
    print('AVITO_TITLE_FEED_VERIFIED', AFTER, flush=True)
    # The normal profile imports the stable URL. Do not start an account-wide upload.
    observed = published_titles()
    print('AVITO_TITLES_READBACK', json.dumps(observed, ensure_ascii=False), flush=True)
    print('ACTIVE_TITLE_MATCHES', sum(observed[s] == expected[s] for s in TARGETS), '/', len(TARGETS), flush=True)


if __name__ == '__main__':
    main()
