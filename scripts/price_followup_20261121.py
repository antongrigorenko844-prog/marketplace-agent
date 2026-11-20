"""Fixed LEOPARTS price follow-up. Only Market business prices can be written."""
import argparse
import json
import logging
import os
import re
import sys
import time
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path.cwd()))
from leoparts_market_prices_20261121 import TARGETS, YM_CONTROL, amount, read_ozon, read_wb, sources, wb_goal

KEYS = (*TARGETS, YM_CONTROL)
WB_SKU = '0am325025u'


class Stop(Exception):
    """Safe text only; never include raw server responses."""


def emit(**fields):
    print(json.dumps(fields, default=str, ensure_ascii=False), flush=True)


def clean(value):
    value = ' '.join(str(value or '').split())
    for name in ('YANDEX_MARKET_API_KEY', 'WB_API_TOKEN', 'OZON_API_KEY'):
        secret = os.getenv(name)
        if secret:
            value = value.replace(secret, '[redacted]')
    value = re.sub(r'https?://\S+|[\w./+%=-]{32,}|[\w.+-]+@[\w.-]+', '[redacted]', value)
    return value[:180]


def validated(reply, operation):
    code, body = reply
    if not isinstance(body, dict):
        raise Stop(f'{operation}: non-JSON response ({code})')
    if code != 200 or body.get('errors') or body.get('status', 'OK') != 'OK':
        errors = body.get('errors') or []
        safe = [{'code': clean(e.get('code')), 'message': clean(e.get('message'))} for e in errors[:2] if isinstance(e, dict)]
        raise Stop(f'{operation}: HTTP {code} {json.dumps(safe, ensure_ascii=False)}')
    return body


def read(ym, method, path, **kwargs):
    return validated(ym._request(method, path, **kwargs), path.split('/')[-1])


def pages(ym, method, path, section, body=None):
    params = {'limit': 100}
    rows, seen = [], set()
    while True:
        reply = read(ym, method, path, params=params, json_body=body)
        data = reply['result'] if method == 'POST' else reply
        page = data[section]
        if not isinstance(page, list):
            raise Stop('unverified paginated response')
        rows.extend(page)
        token = (data.get('paging') or {}).get('nextPageToken')
        if not token:
            pager = data.get('pager') or {}
            if pager.get('total') is not None and len(rows) != pager['total']:
                raise Stop('incomplete campaign enumeration')
            if len(page) == 100 and 'paging' not in data and not pager:
                raise Stop('missing pagination state')
            return rows
        if token in seen:
            raise Stop('repeated page token')
        seen.add(token)
        params['pageToken'] = token


def by_id(rows, keys):
    result = {}
    for row in rows:
        key = row['offerId']
        if key in result or key not in keys:
            raise Stop('ambiguous or unexpected seller offerId')
        result[key] = row
    return result


def shop(ym, campaign, keys):
    return by_id(pages(ym, 'POST', f'/v2/campaigns/{campaign}/offers', 'offers', {'offerIds': list(keys)}), keys)


def office(ym, business):
    return by_id(pages(ym, 'POST', f'/v2/businesses/{business}/offer-prices', 'offers', {'offerIds': list(KEYS)}), KEYS)


def scope(ym):
    token, campaign = ym._creds()
    campaign = int(campaign)
    if token.startswith('y0_'):
        raise Stop('OAuth cannot establish visibility of every store in this business')
    shops = pages(ym, 'GET', '/v2/campaigns', 'campaigns')
    catalog = {int(s['id']): int(s['business']['id']) for s in shops}
    if len(catalog) != len(shops) or campaign not in catalog:
        raise Stop('incomplete target campaign identity')
    business = catalog[campaign]
    config = read(ym, 'POST', f'/v2/businesses/{business}/settings', json_body={})['result']
    shop_settings = read(ym, 'GET', f'/v2/campaigns/{campaign}/settings')['settings']
    if not isinstance(shop_settings, dict) or not shop_settings:
        raise Stop('campaign settings unavailable')
    if int(config['info']['id']) != business or config['settings'].get('currency') not in (None, 'RUR'):
        raise Stop('business or account currency mismatch')
    unified = config['settings'].get('onlyDefaultPrice')
    others = [c for c, b in catalog.items() if b == business and c != campaign]
    emit(channel='ym', only_default_price=unified, stores_in_business=1 + len(others), campaign_settings_read=True)
    if unified is not True:
        raise Stop('onlyDefaultPrice is not true: business price write excluded')
    blocked = set()
    for c in others:
        found = shop(ym, c, TARGETS)
        # A temporarily out-of-stock, suppressed or checking offer can later resume sales.
        blocked.update(k for k, r in found.items() if r.get('status') not in ('ARCHIVED','DISABLED_BY_PARTNER'))
    return business, campaign, blocked


def price(row, field):
    p = (row or {}).get(field) or {}
    if p and p.get('currencyId', 'RUR') != 'RUR':
        raise Stop('unexpected currency')
    return p


def guard(p):
    return tuple(p.get(k) for k in ('discountBase','minimumForBestseller','excludedFromBestsellers'))


def change(sku, business_row, shop_row):
    b = price(business_row, 'price')
    sp = price(shop_row, 'basicPrice')
    cp = price(shop_row, 'campaignPrice')
    if cp.get('value') is not None or shop_row.get('unverified'):
        raise Stop('unexpected campaign override or unverified offer')
    if not b and sp.get('value') is not None:
        raise Stop('business price record is missing for an existing basic price')
    if b and b.get('value') is None:
        raise Stop('business price value unavailable')
    new = TARGETS[sku].new
    if b.get('value') is not None and amount(b['value']) == new:
        return None
    p = {'value': new, 'currencyId': 'RUR'}
    # Both fields can be reset by omission under the Market API contract.
    for field in ('discountBase', 'minimumForBestseller'):
        if b.get(field) is not None:
            numeric = amount(b[field])
            if numeric <= 0:
                raise Stop('unexpected existing extra price value')
            if field == 'discountBase' and numeric != numeric.to_integral_value():
                raise Stop('noninteger discountBase')
            p[field] = b[field]
    return {'offerId': sku, 'price': p}


def submit_once(ym, business, batch):
    import requests
    token, _ = ym._creds()
    reply = requests.post(f'{ym.BASE_URL}/v2/businesses/{business}/offer-prices/updates',
                          headers=ym._headers(token), json={'offers': batch}, timeout=60,
                          allow_redirects=False)
    body = validated((reply.status_code, reply.json()), 'business price update')
    if body.get('status') != 'OK':
        raise Stop('unconfirmed business price acknowledgement')


def snap(shop_row, office_row):
    return (price(shop_row, 'basicPrice').get('value'), price(shop_row, 'campaignPrice').get('value'),
            (shop_row or {}).get('status'), price(office_row, 'price').get('value'), guard(price(office_row,'price')))


def market(ym, apply, delays=(0, 8, 16, 30, 45, 60)):
    business, campaign, blocked = scope(ym)
    shops = shop(ym, campaign, KEYS)
    offices = office(ym, business)
    mappings = {row['sku']: row['market_sku'] for row in ym.get_all_offers(archived=False) if row['sku'] in TARGETS}
    batch, allowed = [], []
    for sku in TARGETS:
        if sku in blocked or sku not in shops or str(mappings.get(sku)) != TARGETS[sku].market_sku:
            emit(channel='ym', sku=sku, status='another_store_enabled' if sku in blocked else 'not_verified_in_shop')
            continue
        try:
            operation = change(sku, offices.get(sku), shops[sku])
        except Stop as exc:
            emit(channel='ym', sku=sku, status=str(exc))
            continue
        if operation:
            batch.append(operation)
        allowed.append(sku)
    before = {k:snap(shops.get(k), offices.get(k)) for k in KEYS}
    if apply and batch:
        try:
            submit_once(ym, business, batch)
        except Stop as exc:
            emit(channel='ym', status='server_rejected_or_acknowledgement_missing', detail=str(exc))
        except Exception as exc:
            emit(channel='ym', status='request_outcome_unknown', detail=type(exc).__name__)
    for delay in delays if apply and batch else (0,):
        if delay:
            time.sleep(delay)
        shops, offices = shop(ym, campaign, KEYS), office(ym, business)
        last = {k:snap(shops.get(k), offices.get(k)) for k in KEYS}
        if all(last[s][0] == TARGETS[s].new and last[s][3] == TARGETS[s].new for s in allowed):
            break
    good = True
    for sku in allowed:
        basic, override, state, unified, companions = last[sku]
        baseline = before[sku]
        ok = (basic == unified == TARGETS[sku].new and override == baseline[1]
              and state == baseline[2] and companions == baseline[4])
        good &= ok
        emit(channel='ym', sku=sku, target=TARGETS[sku].new, business=unified, basic=basic,
             campaign_price=override, storefront=state, companion_values_unchanged=companions == baseline[4],
             status='verified' if ok else 'pending_or_different')
    ctrl = last[YM_CONTROL]
    emit(channel='ym', sku=YM_CONTROL, control_before=before[YM_CONTROL][0], control_after=ctrl[0],
         control_in_store=YM_CONTROL in shops, unchanged=ctrl==before[YM_CONTROL])
    return len(allowed)==len(TARGETS) and ctrl==before[YM_CONTROL] and good


def inspect_wb():
    import wb_client as wb
    key = TARGETS[WB_SKU].wb_id
    row = read_wb()[WB_SKU]
    hits, offset = [], 0
    while True:
        path = f'/api/v2/quarantine/goods?limit=100&offset={offset}'
        data = wb._request('GET',wb.config.wb_prices_base,path)
        if data.get('error'):
            raise Stop('WB quarantine response error')
        rows = data['data']['quarantineGoods']
        if not isinstance(rows, list):
            raise Stop('invalid WB quarantine response')
        hits.extend(x for x in rows if x['nmID']==key)
        offset += len(rows)
        if len(rows)<100:
            break
        if offset>100000:
            raise Stop('incomplete WB quarantine enumeration')
    details = [{k:r.get(k) for k in ('oldPrice','newPrice','oldDiscount','newDiscount')} for r in hits]
    emit(channel='wb', sku=WB_SKU, nmID=key, observed=row['price'], expected=wb_goal(TARGETS[WB_SKU].new,row['discount'])[1],
         quarantine='present' if hits else 'absent', entries=details)


def inspect_ozon():
    observed = read_ozon()
    for sku, t in TARGETS.items():
        row = observed[sku]
        emit(channel='ozon', sku=sku, target=t.new, base=row['price'], seller_after_promotion=row['seller'],
             old=row['old'], minimum=row['minimum'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['preflight','apply','local'])
    mode=parser.parse_args().mode
    sources(Path.cwd())
    if mode=='local':
        print('Verified catalog source and fixed targets:',len(TARGETS))
        return 0
    passed = True
    for name, fn in [('wb',inspect_wb),('ozon',inspect_ozon)]:
        try:
            fn()
        except Exception as exc:
            emit(channel=name,status='read_failed',detail=str(exc) if isinstance(exc,Stop) else type(exc).__name__)
            passed = False
    import yandex_market_client as ym
    try:
        complete = market(ym,mode=='apply')
    except Exception as exc:
        emit(channel='ym',status='stopped',detail=str(exc) if isinstance(exc,Stop) else type(exc).__name__)
        return 2
    return 0 if passed and (complete or mode=='preflight') else 2


if __name__=='__main__':
    logging.disable(logging.CRITICAL)
    sys.exit(main())
