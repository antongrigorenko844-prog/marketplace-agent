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

BEFORE = '730b2886216cd4988b46b980f2065277ef225ab05844c39d9fe67bee65e68c72'
AFTER = '65e95855d3e3cdd7d743b3dbf6db01e3facb226452331dcf3a3519be79670e17'
STABLE_NAME = 'avito_feed.xlsx'
TARGETS = {
    "02E927770AL": 8276437673,
    "0am325091e-set": 8345137262,
    "0am325091f-set": 8415982473,
    "datchik-peredach-dq200": 8403881830,
    "datchik-scepleniya-dq200": 8404614683,
    "klipsy-shtokov-2sht": 8403438057,
    "kolodka-solenoidov-dq200": 8522165852,
    "plita-filtr-pylniki-sal": 8447149597,
    "salnik-tolkatelya-alum": 8236193905,
    "salniki-shtokov-2sht": 8387549709,
    "tolkateli-salniki-alum-komplekt": 8304174872
}
KNOWN_DISPLAY = {
    "02E927770AL": "Мехатроник DSG6 DQ250 в сборе с электронной",
    "salniki-shtokov-2sht": "Сальники штоков мехатроника DSG7 DQ200, комплект",
    "tolkateli-salniki-alum-komplekt": "Толкатели вилок + сальники + усиленные"
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
        if any(actual[s] not in (old[s], expected[s], KNOWN_DISPLAY.get(s)) for s in TARGETS):
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
