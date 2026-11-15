"""Fill verified content attributes only; preserve commercial and media fields."""
import copy, json, time
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yandex_market_client as ym
from market_content import read_rows

PLAN = {
    '0AM325025B': (34063110, 'клапан'),
    '0AM325587E': (None, 'гидроаккумулятор'),
    '0am325025D': (33759630, 'втулка'),
    '0am325025f': (22269110, 'комплект'),
    '0am325025n': (34063110, 'клапан'),
    '0cw0am325025': (34071233, 'ремкомплект'),
    '0BH325183 B': (34060230, 'корпус'),
    'plita-filtr-v2': (34071233, 'ремкомплект'),
}

def run():
    biz = ym.get_business_id()
    ids = list(PLAN)
    before = read_rows(biz, 'offer-mappings', ids)
    cards_before = read_rows(biz, 'offer-cards', ids)
    offers = {r['offer']['offerId']: r['offer'] for r in before}
    cards = {r['offerId']: r for r in cards_before}
    assert set(offers) == set(cards) == set(ids)
    status, definitions = ym._request('POST', '/v2/category/71380208/parameters', params={'businessId': biz}, json_body={})
    assert status == 200 and definitions.get('status') == 'OK'
    defs = {p['id']: p for p in definitions['result']['parameters']}
    assert defs[21194330]['allowCustomValues'] is True
    valid_types = {v['id']: v['value'] for v in defs[21194330]['values']}
    targets, payload = {}, []
    for sku, (value_id, value) in PLAN.items():
        category = cards[sku]['mapping']['marketCategoryId']
        assert category == (70978558 if sku == '0cw0am325025' else 71380208), (sku, category)
        if value_id is not None: assert valid_types[value_id] == value
        values = [copy.deepcopy(p) for p in cards[sku].get('parameterValues', []) if p['parameterId'] not in [21194330, 33663230]]
        entry = {'parameterId': 21194330, 'value': value}
        if value_id is not None: entry['valueId'] = value_id
        values.extend([entry, {'parameterId': 33663230, 'unitId': 6145205, 'value': '1'}])
        if sku == 'plita-filtr-v2':
            values = [p for p in values if p['parameterId'] != 14805583]
            values.append({'parameterId': 14805583, 'unitId': 6145205, 'value': '2'})
        targets[sku] = values
        payload.append({'offer': {'offerId': sku, 'marketCategoryId': 71380208, 'parameterValues': values}})
    assert all(set(r['offer']) == {'offerId', 'marketCategoryId', 'parameterValues'} for r in payload)
    result = {'before': before, 'cards_before': cards_before, 'payload': payload}
    status, response = ym._request('POST', f'/v2/businesses/{biz}/offer-mappings/update', json_body={'offerMappings': payload})
    result['response'] = {'http_status': status, 'body': response}
    assert status == 200 and response.get('status') == 'OK'
    assert not any(r.get('errors') for r in response.get('results', [])), response
    for attempt in range(12):
        time.sleep(10)
        after = read_rows(biz, 'offer-mappings', ids)
        cards_after = read_rows(biz, 'offer-cards', ids)
        actual = {r['offerId']: r for r in cards_after}
        checks = {}
        for row in after:
            sku = row['offer']['offerId']
            old, new = offers[sku], row['offer']
            for field in ['name', 'description', 'pictures', 'videos', 'weightDimensions', 'basicPrice', 'vendor', 'vendorCode', 'condition', 'barcodes', 'commodityCodes']:
                assert old.get(field) == new.get(field), ('unrelated field changed', sku, field)
            got = {p['parameterId']: p for p in actual[sku].get('parameterValues', [])}
            checks[sku] = {
                'parameters_verified': all(all(got.get(p['parameterId'], {}).get(k) == v for k,v in p.items()) for p in targets[sku]),
                'category_verified': actual[sku]['mapping']['marketCategoryId'] == 71380208,
                'rating': actual[sku].get('contentRating'),
                'rating_status': actual[sku].get('contentRatingStatus'),
                'campaigns': new.get('campaigns'),
            }
        result.update({'after': after, 'cards_after': cards_after, 'checks': checks})
        if all(v['parameters_verified'] and v['category_verified'] for v in checks.values()): break
    result['verified'] = all(v['parameters_verified'] and v['category_verified'] for v in checks.values())
    return result
