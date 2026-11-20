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

BEFORE = '65e95855d3e3cdd7d743b3dbf6db01e3facb226452331dcf3a3519be79670e17'
AFTER = 'ba8be7545655722c594c4f3d7ad284db8e7963fd22e620e21738e80f5eef1eae'
STABLE_NAME = 'avito_feed.xlsx'
TARGETS = {
    "01X301127C": 8446880709,
    "0AM301212A": 8395404775,
    "0AM325413A": 8394615261,
    "0AM325467J": 8286536816,
    "0am325025l": 8366848631,
    "0am325025r": 8252362999,
    "0am325091e-set": 8345137262,
    "0am325091f-set": 8415982473,
    "WHT001922": 8461614430
}


def source_descriptions(blob):
    with BytesIO(blob) as stream:
        book = load_workbook(stream, read_only=True, data_only=True)
        try:
            rows = [r for r in book['Объявления'].values if r[3] in TARGETS]
            if len(rows) != len(TARGETS) or {r[3] for r in rows} != TARGETS.keys():
                raise ValueError('Wrong row identities')
            return {r[3]: r[16] for r in rows}
        finally:
            book.close()


def published_descriptions():
    ads = {int(a['id']): a for a in list_all_items(statuses=['active'])}
    if any(item_id not in ads for item_id in TARGETS.values()):
        raise ValueError('Target no longer active: preserving publication')
    return {sku: ads[item_id].get('description') for sku, item_id in TARGETS.items()}


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
    expected = source_descriptions(proposed)
    manifest = load()
    if {s: manifest[s]['text']['avito']['description'] for s in TARGETS} != expected:
        raise ValueError('Persistent channel source differs')
    release, asset, current = get_avito_asset()
    actual = published_descriptions()
    print('AVITO_ACTIVE_IDS', len(actual), 'DESCRIPTION_FIELDS_AVAILABLE', sum(v is not None for v in actual.values()), flush=True)
    if current != proposed:
        if sha256(current).hexdigest() != BEFORE:
            raise ValueError('Stable release changed; leaving existing asset in place')
        old = source_descriptions(current)
        if any(actual[s] is not None and actual[s] not in (old[s], expected[s]) for s in TARGETS):
            raise ValueError('Seller modified a target title; preserving publication')
        stage_name = 'avito_feed.reviewed_descriptions_' + AFTER[:12] + '.xlsx'
        stage = next((a for a in release['assets'] if a['name'] == stage_name), None)
        if stage is None:
            stage = gh('POST', release['upload_url'].partition('{')[0], params={'name': stage_name},
                       data=proposed, headers={'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}).json()
        verified_download(stage['browser_download_url'], proposed)
        _, last_asset, latest = get_avito_asset()
        if last_asset['id'] != asset['id'] or latest != current:
            raise ValueError('Public asset changed while staging')
        backup = 'avito_feed.before_descriptions_' + str(asset['id']) + '.xlsx'
        rename_asset(asset['id'], backup)
        try:
            rename_asset(stage['id'], STABLE_NAME)
        except Exception:
            rename_asset(asset['id'], STABLE_NAME)
            raise
    verified_download(STABLE_AVITO_URL, proposed)
    print('AVITO_DESCRIPTION_FEED_VERIFIED', AFTER, flush=True)
    # The normal profile imports the stable URL. Do not start an account-wide upload.
    observed = published_descriptions()
    print('SELLER_API_DESCRIPTION_FIELDS', sum(v is not None for v in observed.values()), '/', len(TARGETS), flush=True)
    print('ACTIVE_DESCRIPTION_MATCHES', sum(observed[s] == expected[s] for s in TARGETS), '/', len(TARGETS), flush=True)


if __name__ == '__main__':
    main()
