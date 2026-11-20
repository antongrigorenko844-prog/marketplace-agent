"""LEOPARTS 2026-11: one fixed price operation. Run in marketplace-agent repository root."""
import argparse
import json
import logging
import re
import sys
import time
from dataclasses import dataclass
from decimal import Decimal as D, ROUND_HALF_UP
from pathlib import Path


@dataclass(frozen=True)
class Target:
    new: int
    before: int
    wb_before: str
    ozon_id: int
    wb_id: int
    market_sku: str


TARGETS = {
    'WHT001922': Target(1250, 1350, '1350.1', 1790034042, 670862930, '5887588650'),
    '0am325066ad': Target(1270, 1350, '1349.95', 1790389479, 670855859, '4390304549'),
    '0am325025z': Target(3490, 4350, '4350.22', 2075148750, 670925480, '4483525332'),
    '122390AK-AF': Target(990, 1350, '1349.95', 1540006723, 670925538, '4332438001'),
    '01X301127C': Target(1250, 1350, '1350', 1790079835, 670860387, '4384124370'),
    '0am325025u': Target(3490, 5350, '5349.82', 2099149069, 670860803, '4490648841'),
    '0am325066AC': Target(1750, 1850, '1850.23', 1812060620, 670862819, '4390303473'),
    '0am325025ae': Target(4490, 4850, '4850.02', 1570312812, 670925563, '4332447227'),
    '0AM325219C': Target(2990, 4350, '4349.95', 1508698298, 670925525, '4332446816'),
    '0am325025P': Target(990, 1350, '1349.95', 1817507275, 670861565, '4392869596'),
    'filtr-setka-0b5-orig': Target(1990, 2350, '2350.31', 6288287961, 1949842440, '6250724843'),
    '02E 305 045': Target(2490, 3350, '3349.98', 1820208940, 837943386, '5882321872'),
    'separatornaya-plastina-dq200': Target(1250, 1350, '1350', 6447289313, 1722879969, '229050598090334208'),
}
CENT = D('0.01')


def amount(value):
    value = D(str(value))
    if not value.is_finite() or value < 0:
        raise ValueError("invalid monetary value")
    return value


def sources(root):
    from openpyxl import load_workbook
    for filename, sheet, cols in [('master_control', 'Товары', (7, 10)),
                                  ('ozon_catalog', 'Ozon', (3,)), ('wb_catalog', 'WB', (5,))]:
        book = load_workbook(root / 'data' / (filename + '.xlsx'), read_only=True, data_only=True)
        try:
            found = set()
            for row in book[sheet].values:
                sku = row[0]
                if sku not in TARGETS:
                    continue
                if sku in found or any(amount(row[i]) != TARGETS[sku].new for i in cols):
                    raise ValueError('local duplicate or different target in ' + filename + ': ' + sku)
                found.add(sku)
            if found != set(TARGETS):
                raise ValueError('missing target in ' + filename)
        finally:
            book.close()


YM_CONTROL = '02e305045'  # Separate DSG6 housing, read-only.


def unique(rows, key, scoped=False, allowed=TARGETS):
    result = {}
    for row in rows:
        sku = row[key]
        if sku not in allowed:
            if scoped:
                raise ValueError('response contains an unrequested identifier')
            continue
        if sku in result:
            raise ValueError('duplicate identifier in response')
        result[sku] = row
    return result


def wb_goal(target, discount):
    pct = amount(discount)
    if pct >= 100 or pct != pct.to_integral_value():
        raise ValueError('invalid WB discount')
    factor = (100 - pct) / 100
    base = int((D(target) / factor).to_integral_value(rounding=ROUND_HALF_UP))
    return base, (base * factor).quantize(CENT, rounding=ROUND_HALF_UP)


def read_ozon():
    import ozon_client
    rows = unique(ozon_client.get_prices_and_stocks(list(TARGETS)), 'offer_id', scoped=True)
    ans = {}
    for sku,r in rows.items():
        t = TARGETS[sku]
        p = r['price']
        if r['product_id'] != t.ozon_id or p.get('currency_code') != 'RUB':
            raise ValueError('unexpected Ozon identity or currency')
        ans[sku] = dict(price=amount(p['price']), old=amount(p['old_price']),
                        minimum=amount(p['min_price']), seller=amount(p['marketing_seller_price']))
    return ans


def read_wb():
    import wb_client
    rows = unique(wb_client.get_prices(limit=1000), 'vendorCode')
    ans = {}
    for sku,r in rows.items():
        if r['nmID'] != TARGETS[sku].wb_id or r['currencyIsoCode4217'] != 'RUB':
            raise ValueError('unexpected WB identity or currency')
        prices = {(amount(s['price']), amount(s['discountedPrice'])) for s in r['sizes']}
        if len(prices) != 1 or r.get('editableSizePrice'):
            raise ValueError('size-specific or unavailable WB prices')
        base, price = prices.pop()
        ans[sku] = dict(base=base, price=price, discount=r['discount'])
    return ans


def read_ym():
    import yandex_market_client as ym
    _, campaign = ym._creds()
    # Verify the shop SKU against the stable Market SKU independently of a listing price.
    mapping = unique(ym.get_all_offers(archived=False), 'sku')
    keys = list(TARGETS) + [YM_CONTROL]
    st, payload = ym._request('POST', f'/v2/campaigns/{campaign}/offers',
                              json_body={'offerIds': keys})
    if st != 200 or payload.get('status') != 'OK' or 'offers' not in payload.get('result', {}):
        raise ValueError('no verified Market offer response')
    rows = unique(payload['result']['offers'], 'offerId', scoped=True, allowed=keys)
    ans = {}
    for sku in keys:
        row = rows.get(sku)
        if not row or row.get('unverified'):
            continue  # Catalog membership alone does not authorize creating a shop offer.
        if sku != YM_CONTROL and (sku not in mapping or str(mapping[sku]['market_sku']) != TARGETS[sku].market_sku):
            ans[sku] = {'unverified':True}
            continue
        basic = row.get('basicPrice') or {}
        campaign_price = row.get('campaignPrice') or {}
        price_fields = campaign_price if campaign_price.get('value') is not None else basic
        value = price_fields.get('value')
        if value is not None and price_fields.get('currencyId') not in ('RUR', 'RUB'):
            if sku == YM_CONTROL:
                continue
            ans[sku] = {'unverified':True}
            continue
        ans[sku] = dict(price=None if value is None else amount(value),
                        basic=basic.get('value'), campaign_price=campaign_price.get('value'),
                        shop_status=row.get('status'), old=amount(price_fields.get('discountBase') or 0),
                        vat=price_fields.get('vat'))
    return ans


def assess(channel, sku, row):
    t = TARGETS[sku]
    if not row or row.get('unverified'):
        return 'absent_or_unverified', None
    if channel == 'wb':
        base, final = wb_goal(t.new, row['discount'])
        if row['price'] == final and row['base'] == base:
            return 'verified', None
        if row['price'] != amount(t.wb_before):
            return 'different_live_price', None
        return 'ready', dict(nmID=t.wb_id, price=base, discount=row['discount'])
    if row['price'] == t.new:
        return 'verified', None
    # Market has an explicitly authorized shop-price target even without a previous price.
    if channel == 'ozon' and row['price'] != t.before:
        return 'different_live_price', None
    if channel == 'ozon':
        return 'ready', dict(offer_id=sku, price=str(t.new), old_price=format(row['old'],'f'), currency_code='RUB')
    p = dict(value=t.new, currencyId='RUR')
    if row['old']:
        p['discountBase'] = int(row['old']) if row['old'] == row['old'].to_integral_value() else float(row['old'])
    if row['vat'] is not None:
        p['vat'] = row['vat']
    return 'ready', dict(offerId=sku, price=p)


def safe_code(x):
    s = str(x or '')
    return s if re.fullmatch(r'[A-Za-z0-9_:-]{1,80}',s) else 'unspecified'


def submit(channel, batch):
    if channel == 'ozon':
        import ozon_client
        reply = ozon_client._post('/v1/product/import/prices', {'prices': batch}, retries=1)
        rows = unique(reply['result'], 'offer_id', scoped=True)
        if set(rows) != {r['offer_id'] for r in batch}:
            raise ValueError('incomplete Ozon acknowledgement')
        return {s: ','.join(safe_code(e.get('code')) for e in r.get('errors',[])) or 'no_confirmation'
                for s,r in rows.items() if not r.get('updated') or r.get('errors')}
    if channel == 'wb':
        import wb_client
        r = wb_client._request('POST',wb_client.config.wb_prices_base,'/api/v2/upload/task',
                               {'data':batch},retries=1)
        if r.get('error') or not isinstance((r.get('data') or {}).get('id'), int):
            raise ValueError('no WB acknowledgement')
        return {}
    import yandex_market_client as ym
    _, campaign = ym._creds()
    st, r = ym._request('POST', f'/v2/campaigns/{campaign}/offer-prices/updates',
                        json_body={'offers': batch})
    if st != 200 or r.get('status') != 'OK' or r.get('errors'):
        code = ','.join(safe_code(x.get('code')) for x in r.get('errors',[]) if isinstance(x,dict))
        return {x['offerId']:f'HTTP_{st}_{code}' for x in batch}
    return {}


READERS = dict(ozon=read_ozon, wb=read_wb, ym=read_ym)


def channel_run(channel, mode, delays=(0, 6, 12, 20, 30, 45)):
    read = READERS[channel]
    if channel == 'ozon' and mode == 'apply':
        rules_file = Path('data/ozon_overrides.json')
        if rules_file.exists():
            rules = json.loads(rules_file.read_text())
            if any((rules.get(sku) or {}).get('no_price_sync') for sku in TARGETS):
                raise ValueError('price synchronization disabled for a target')
    before = read()
    last_observation = before
    states = {}
    batch = []
    for sku in TARGETS:
        state, change = assess(channel, sku, before.get(sku))
        states[sku] = state
        if change is not None:
            batch.append(change)
    blocked = {}
    dispatched = {sku for sku, state in states.items() if state == 'ready'}
    if batch and mode == 'apply':
        try:
            blocked = submit(channel, batch)
        except Exception as ex:
            # Ambiguous transmission is followed only by GET/read-equivalent requests.
            print(channel, 'submission response unavailable:', type(ex).__name__)
        for delay in delays:
            if delay:
                time.sleep(delay)
            try:
                after = read()
            except Exception:
                continue
            for sku in TARGETS:
                old = before.get(sku) or {}
                new = after.get(sku) or {}
                state, _ = assess(channel, sku, new)
                if channel == 'ozon' and old and new and (old['minimum'], old['old']) != (new['minimum'], new['old']):
                    state = 'companion_price_changed'
                if channel == 'ozon' and state=='verified' and new['seller'] not in (0, TARGETS[sku].new):
                    state = 'base_verified_promotion_differs'
                if channel == 'ym' and old.get('shop_status') and new.get('shop_status') != old.get('shop_status'):
                    state = 'storefront_status_changed'
                if channel == 'wb' and old and new and old['discount'] != new['discount']:
                    state = 'discount_changed'
                if channel == 'ym' and old.get('price') and new.get('price') and old['old'] != new['old']:
                    state = 'discount_base_changed'
                if sku in blocked:
                    state = 'rejected_' + blocked[sku] if state != 'verified' else 'verified_after_conflicting_ack'
                states[sku] = state
            last_observation = after
            if all(states[sku] != 'ready' for sku in dispatched):
                break
        for sku in dispatched:
            if states[sku] == 'ready':
                states[sku] = 'write_not_yet_verified'
    rows = last_observation
    control_ok = True
    if channel == 'ym' and batch and mode == 'apply':
        control_ok = before.get(YM_CONTROL) == rows.get(YM_CONTROL)
        control_status = 'unchanged' if before.get(YM_CONTROL) else 'absent_in_response'
        if last_observation is before:
            control_status = 'readback_pending'
            control_ok = False
        print(json.dumps(dict(channel=channel,sku=YM_CONTROL,read_only_control=control_status if control_ok else 'changed_or_pending')))
    for sku,state in states.items():
        row = rows.get(sku) or {}
        if channel == 'ozon' and state == 'verified' and row['seller'] not in (0,TARGETS[sku].new):
            state = states[sku] = 'base_verified_promotion_differs'
        record = dict(channel=channel, sku=sku, status=state, target=TARGETS[sku].new,
                      observed=row.get('price'))
        if channel == 'ozon' and row:
            record.update(threshold=row['minimum'], seller_after_promotion=row['seller'],
                          threshold_above_target=row['minimum']>TARGETS[sku].new)
        if channel == 'ym' and row:
            record.update(basic_price=row.get('basic'), campaign_price=row.get('campaign_price'),
                          storefront_status=row.get('shop_status'))
        if channel == 'wb' and row:
            record['expected_with_discount'] = wb_goal(TARGETS[sku].new,row['discount'])[1]
        print(json.dumps(record,ensure_ascii=False,default=str))
    ok = {'verified', 'ready'} if mode=='preflight' else {'verified'}
    return control_ok and all(v in ok for v in states.values())


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=['local','preflight','apply','verify'])
    p.add_argument('--channel',choices=['all','ozon','wb','ym'],default='all')
    args=p.parse_args()
    root=Path.cwd()
    sys.path.insert(0,str(root))
    sources(root)
    if args.mode=='local':
        print('Verified local targets:',len(TARGETS))
        return 0
    results=[]
    for ch in READERS if args.channel=='all' else (args.channel,):
        try:
            results.append(channel_run(ch,args.mode))
        except Exception as exc:
            print(ch, 'stopped:',type(exc).__name__)
            results.append(False)
    return 0 if all(results) else 2


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    try:
        sys.exit(main())
    except Exception as exc:
        print('Operation stopped:',type(exc).__name__)
        sys.exit(2)
