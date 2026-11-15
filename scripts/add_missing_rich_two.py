"""Add only missing rich content for two approved offers; preserve every other field."""
import base64
import contextlib
import datetime
import io
import json
import os
import sys
import time
import traceback
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
TARGETS = ('0am325025', '0am325025x')
PROTECTED = ('0am325025H', '0AM927769D', '0AM927769F', '0am325025HX', '0am325025HXF')
APPLY = '--apply' in sys.argv
RICH_ID = 11254
result = {'targets': TARGETS, 'apply': APPLY, 'price_write_calls': 0, 'stock_write_calls': 0,
          'started_at': datetime.datetime.now(datetime.timezone.utc).isoformat()}
log = io.StringIO()

def save_evidence():
    key = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(12)
    pub = serialization.load_pem_public_key((ROOT / 'scripts/black_filter_result_public.pem').read_bytes())
    wrapped = pub.encrypt(key, padding.OAEP(mgf=padding.MGF1(hashes.SHA256()), algorithm=hashes.SHA256(), label=None))
    data = AESGCM(key).encrypt(nonce, json.dumps(result, ensure_ascii=False).encode(), None)
    out = ROOT / 'reports/rich-two-private.enc'
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps({k: base64.b64encode(v).decode() for k, v in {'key': wrapped, 'nonce': nonce, 'data': data}.items()}))

def attr(doc, aid):
    return [a for a in doc.get('attributes', []) if a['id'] == aid]

def nonrich(doc):
    return sorted([a for a in doc.get('attributes', []) if a['id'] != RICH_ID], key=lambda a: (a['id'], a.get('complex_id', 0)))

def rich_value(doc):
    rows = attr(doc, RICH_ID)
    vals = [v.get('value', '') for a in rows for v in a.get('values', []) if v.get('value', '').strip()]
    if not vals:
        return None
    assert len(vals) == 1, 'Unexpected rich-content structure'
    return json.loads(vals[0])

def main():
    import ozon_client as oc

    original_post = oc._post
    read_paths = {'/v4/product/info/attributes', '/v3/product/info/list', '/v5/product/info/prices', '/v4/product/info/stocks', '/v1/product/import/info'}
    result['api_calls'] = []

    def guarded_post(path, payload, **kwargs):
        if path == '/v1/product/attributes/update':
            assert APPLY, 'Mutation forbidden in read-only mode'
            assert set(payload) == {'items'} and 0 < len(payload['items']) <= 2
            for item in payload['items']:
                assert set(item) == {'offer_id', 'attributes'} and item['offer_id'] in TARGETS
                assert len(item['attributes']) == 1 and item['attributes'][0]['id'] == RICH_ID
        else:
            assert path in read_paths, 'Unexpected endpoint: ' + path
        result['api_calls'].append(path)
        return original_post(path, payload, **kwargs)

    oc._post = guarded_post

    def snapshot():
        cards = {c['offer_id']: c for c in oc.get_product_names(list(TARGETS + PROTECTED))}
        assert set(cards) == set(TARGETS + PROTECTED), 'Missing card in snapshot'
        return {'cards': cards, 'info': {c['offer_id']: c for c in oc.get_product_info_list(list(TARGETS))},
                'prices': oc.get_prices_and_stocks(list(TARGETS)), 'stocks': oc.get_stocks(list(TARGETS))}

    before = snapshot()
    result['before'] = before
    docs = {s: json.loads((ROOT / 'data/ozon_rich' / (s + '.json')).read_text()) for s in TARGETS}
    result['expected_rich'] = docs
    todo = []
    for sku, doc in docs.items():
        assert doc['version'] == 0.3 and len(doc['content']) == 6
        existing = rich_value(before['cards'][sku])
        # Never overwrite a pre-existing or concurrently added rich description.
        assert existing is None or existing == doc, 'Existing rich content differs: ' + sku
        photos = [before['cards'][sku].get('primary_image')] + before['cards'][sku].get('images', [])
        for block in doc['content']:
            for b in block.get('blocks', []):
                assert b['img']['src'] in photos and b['img']['srcMobile'] in photos
        if existing is None:
            todo.append({'offer_id': sku, 'attributes': [{'id': RICH_ID, 'complex_id': 0, 'values': [{'value': json.dumps(doc, ensure_ascii=False)}]}]})
    result['planned_items'] = todo
    save_evidence()  # Encrypted original state is written before any mutation.

    if APPLY and todo:
        result['response'] = oc.update_attributes(todo)
        save_evidence()
        task = result['response'].get('task_id') or result['response'].get('result', {}).get('task_id')
        assert task, 'No import task ID returned'
        for _ in range(24):
            time.sleep(5)
            status = oc.get_import_status(int(task))
            result['import_status'] = status
            rows = status.get('result', {}).get('items') or status.get('items') or []
            if rows and all(r.get('status') not in ('pending', 'processing', '') for r in rows):
                break
        assert rows and {r.get('offer_id') for r in rows} == {i['offer_id'] for i in todo}
        assert all(r.get('status') == 'imported' and not r.get('errors') for r in rows), 'Import not completed cleanly'
        for _ in range(24):
            check = {c['offer_id']: c for c in oc.get_product_names(list(TARGETS))}
            if all(rich_value(check[s]) == docs[s] for s in TARGETS):
                break
            time.sleep(5)
        assert all(rich_value(check[s]) == docs[s] for s in TARGETS), 'Rich content has not appeared in API'

    after = snapshot()
    result['after'] = after
    core = ('id', 'name', 'barcode', 'height', 'depth', 'width', 'dimension_unit', 'weight', 'weight_unit',
            'description_category_id', 'type_id', 'primary_image', 'images', 'pdf_list',
            'color_image', 'sku', 'barcodes')
    result['checks'] = {}
    for sku in TARGETS + PROTECTED:
        b, a = before['cards'][sku], after['cards'][sku]
        assert all(b.get(k) == a.get(k) for k in core), 'Non-rich field changed: ' + sku
        assert nonrich(b) == nonrich(a), 'Non-rich attributes changed: ' + sku
        # Ozon can reorder the video attributes even between two read-only calls.
        assert sorted(b.get('complex_attributes', []), key=lambda x: (x.get('complex_id', 0), x['id'])) == sorted(a.get('complex_attributes', []), key=lambda x: (x.get('complex_id', 0), x['id'])), 'Video attributes changed: ' + sku
        if sku in PROTECTED:
            assert attr(b, RICH_ID) == attr(a, RICH_ID), 'Protected rich content changed: ' + sku
        elif APPLY:
            assert rich_value(a) == docs[sku], 'Wrong final rich content: ' + sku
        result['checks'][sku] = {'other_content_preserved': True, 'rich_blocks': len((rich_value(a) or {}).get('content', []))}
    bp = {r['offer_id']: r.get('price') for r in before['prices']}
    ap = {r['offer_id']: r.get('price') for r in after['prices']}
    assert bp == ap, 'Price configuration changed during operation'
    result['prices_preserved'] = True
    result['stock_snapshot_equal'] = before['stocks'] == after['stocks']

try:
    with contextlib.redirect_stdout(log), contextlib.redirect_stderr(log):
        main()
except Exception:
    result['error'] = traceback.format_exc()
finally:
    result['log'] = log.getvalue()
    result['checked_at'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    save_evidence()
print('Targeted rich operation finished. Private evidence encrypted. Price and stock writes: zero.')
sys.exit(1 if result.get('error') else 0)
