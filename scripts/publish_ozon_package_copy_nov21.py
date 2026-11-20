"""Only change verified Ozon package contents and descriptions for the reviewed SKU list."""
import json
import time
from pathlib import Path

import openpyxl
import ozon_client as oz

FIELDS = {'description': 4191, 'composition': 4384}
PLAN = json.loads(Path('data/ozon_package_copy_nov21.json').read_text())
RECIPES = {
    '0am325025j': {'0AM325467J':1, '0am325477ae':2},
    '0am325025G': {'122390AK-AF':2, '0am325066ad':2, '0am325025q':2},
    '0am325025f': {'WHT001922':4, '01X301127C':3},
}

def validate_recipes():
    w = openpyxl.load_workbook('data/kits.xlsx', read_only=True, data_only=True)
    actual = {key: {} for key in RECIPES}
    for row in w.active.values:
        key, _, part, quantity = row[:4]
        if key in actual and part and quantity:
            actual[key][part] = actual[key].get(part, 0) + int(quantity)
    w.close()
    if actual != RECIPES:
        raise ValueError('Component mapping changed after review')

def read():
    return {c['offer_id']:c for c in oz.get_product_names(list(PLAN))}

def text_field(card, key):
    vals = [v.get('value','') for a in card.get('attributes',[]) if a['id']==FIELDS[key] for v in a.get('values',[])]
    if len(vals) > 1:
        raise ValueError('Unexpected multi-value text')
    return vals[0] if vals else ''

def other_content(card, keys):
    ignore = {FIELDS[k] for k in keys}
    ordinary = sorted((json.dumps(x, sort_keys=True, ensure_ascii=False) for x in card.get('attributes',[]) if x['id'] not in ignore))
    complex_fields = sorted((json.dumps(x, sort_keys=True, ensure_ascii=False) for x in card.get('complex_attributes',[])))
    return (card.get('name'), card.get('primary_image'), tuple(card.get('images',[])), card.get('type_id'), card.get('description_category_id'), ordinary, complex_fields)

def ratings(skus):
    output = oz._post('/v1/product/rating-by-sku', {'skus': skus})
    return {int(p['sku']):p.get('rating') for p in output.get('products',[])}

def main():
    validate_recipes()
    old = read()
    info = {c['offer_id']:c for c in oz.get_product_info_list(list(PLAN))}
    eligible = {}
    payload = []
    for key, rule in PLAN.items():
        row, state = old[key], info[key]
        if state.get('is_archived') or state.get('is_autoarchived') or state.get('statuses',{}).get('status_name','').replace('ё','е') != 'Продается':
            print('SKIP', key, 'not actively selling', state.get('statuses',{}).get('status_name'))
            continue
        if (row['id'], row['sku']) != (rule['id'],rule['sku']):
            raise ValueError('Ozon identity mismatch for ' + key)
        if not set(rule['set']) <= set(FIELDS):
            raise ValueError('Unexpected field in plan')
        if any(text_field(row,k) not in (v,rule['old'][k]) for k,v in rule['set'].items()):
            print('SKIP', key, 'live text changed after snapshot')
            continue
        fields = [{'id':FIELDS[k], 'complex_id':0,'values':[{'value':v}]} for k,v in rule['set'].items() if text_field(row,k) != v]
        if fields:
            payload.append({'offer_id':key,'attributes':fields})
        eligible[key] = rule
    if not eligible:
        raise RuntimeError('No eligible match')
    skus = [r['sku'] for r in eligible.values()]
    scores_before = ratings(skus)
    if payload:
        ack = oz.update_attributes(payload)
        if not ack or ack.get('error'):
            raise RuntimeError('Ozon rejected the text update')
        print('ACK', json.dumps(ack), flush=True)
    for attempt in range(40):
        after = read()
        ready = {k for k,r in eligible.items() if all(text_field(after[k],field)==v for field,v in r['set'].items())}
        if len(ready) == len(eligible): break
        if attempt < 39:time.sleep(8)
    scores_after = ratings(skus)
    for key in sorted(ready):
        spec=eligible[key]
        if other_content(old[key], spec['set']) != other_content(after[key], spec['set']):
            raise RuntimeError('Unrelated product content changed: ' + key)
        print('RECEIPT ' + json.dumps({'offer':key,'fields':list(spec['set']),'before':scores_before.get(spec['sku']),'after':scores_after.get(spec['sku']),'sku':spec['sku']},ensure_ascii=False), flush=True)
    if len(ready)!=len(eligible):
        raise RuntimeError('Waiting for seller readback: '+str(sorted(eligible.keys()-ready)))
if __name__ == '__main__':
    main()
