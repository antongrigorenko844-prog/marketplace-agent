"""Publish the reviewed Avito feed without changing any other release asset."""
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import sys
import time
import requests
from openpyxl import load_workbook

sys.path.insert(0, str(Path.cwd()))
from scripts.apply_dq200_prices_20261120 import gh, rename_asset, REPO, STABLE_AVITO_URL

BEFORE = {'fbbeb743d4756cc43fda37ae4fd2b2081c474ae794e6f297f04afbfd36152018','56744490a3e67d66e6bc9f35e47d18280cb58be64d85e2906878731af1fe5149'}
AFTER = '736ba41724e57489d002a05b7c61159ea554fe9437b5fcebd09bf157c6b6d216'
CHANGED = {'0am325025u': 2350, '0am325025ae': 2350, '0AM325219C': 1850,
           '0am325025P': 450, 'filtr-setka-0b5-orig': 950, '02e305045': 950,
           '02E 305 045': 1450, 'DQ500': 1450}
STABLE = 'avito_feed.xlsx'
STAGED = 'avito_feed.reviewed_20261121_' + AFTER[:12] + '.xlsx'


def reviewed_cells(blob):
    wb = load_workbook(BytesIO(blob), data_only=True, read_only=True)
    try:
        found = {}
        for row in wb['Объявления'].values:
            sku = row[3]
            if sku in CHANGED:
                if sku in found:
                    raise ValueError('ambiguous SKU')
                found[sku] = row[33]
        return found == CHANGED
    finally:
        wb.close()


def download(url):
    response = requests.get(url, params={'rev': time.time_ns()}, timeout=35)
    response.raise_for_status()
    return response.content


def main():
    current = Path('data/avito_feed.xlsx').read_bytes()
    if sha256(current).hexdigest() != AFTER or not reviewed_cells(current):
        raise ValueError('reviewed feed source differs')
    release = gh('GET', '/releases/tags/product-photos').json()
    assets = {a['name']:a for a in release['assets']}
    live = download(STABLE_AVITO_URL)
    if reviewed_cells(live):
        print('STABLE_FEED_CONFIRMED reviewed eight prices', flush=True)
        return
    if sha256(live).hexdigest() not in BEFORE:
        raise ValueError('stable feed externally revised; preserving until normal feed rebuild')
    old = assets[STABLE]
    backup = 'avito_feed.before_20261121_' + str(old['id']) + '.xlsx'
    upload = assets.get(STAGED)
    if not upload:
        upload = gh('POST', release['upload_url'].partition('{')[0], params={'name': STAGED},
                    data=current, headers={'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}).json()
    if download(upload['browser_download_url']) != current:
        raise ValueError('staged file differs')
    # Maintain a recoverable copy of the previous asset during and after promotion.
    rename_asset(old['id'], backup)
    try:
        rename_asset(upload['id'], STABLE)
    except Exception:
        rename_asset(old['id'], STABLE)
        raise
    for delay in (0, 3, 8, 15):
        time.sleep(delay)
        try:
            blob = download(STABLE_AVITO_URL)
            if blob == current:
                print('STABLE_FEED_CONFIRMED reviewed eight prices', flush=True)
                return
        except requests.RequestException:
            continue
    raise RuntimeError('new release asset accepted; public readback pending')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('STABLE_FEED_PENDING', type(exc).__name__)
        sys.exit(1)
