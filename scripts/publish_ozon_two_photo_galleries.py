"""Append reviewed gallery photos for two exact Ozon variants."""
import json
import time
from urllib.parse import urlsplit

import ozon_client as oz
from content_sync import equal_gallery, one, ozon_gallery, same_picture

RULES = {
    '0AM325467J': {
        'id': 2646654575, 'sku': 2804936350,
        'cover': 'https://ir-20.ozone.ru/s3/multimedia-1-t/14848103945.jpg',
        'add': [
            'https://avatars.mds.yandex.net/get-mpic/15382907/2a00000199170558eabd9b4c24f1ca4e7df2/orig',
            'https://avatars.mds.yandex.net/get-mpic/16456541/2a00000199170558e8b072b70827b088aed6/orig',
        ],
    },
    'filtr-setka-0b5-orig': {
        'id': 6288287961, 'sku': 5755927856,
        'cover': 'https://ir-20.ozone.ru/s3/multimedia-1-o/14848101816.jpg',
        'add': ['https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/reviewed-filtr-setka-0b5-orig-9-20261115.jpg'],
    },
}

def fetch_cards():
    return oz.get_product_names(list(RULES))

def ratings():
    result = oz._post('/v1/product/rating-by-sku', {'skus': [r['sku'] for r in RULES.values()]})
    return {int(row['sku']): row.get('rating') for row in result.get('products', [])}

def videos(card):
    fields = [*card.get('attributes', []), *card.get('complex_attributes', [])]
    for field in card.get('complex_attributes', []):
        fields.extend(field.get('attributes', []))
    return sorted(set((field['id'], urlsplit(v['value']).path) for field in fields
                      if field.get('id') in (21841, 21842)
                      for v in field.get('values', []) if v.get('value', '').startswith('https://')))

def prepare(rows):
    plans = {}
    for offer_id, rule in RULES.items():
        card = one(rows, 'offer_id', offer_id)
        if (int(card['id']), int(card['sku'])) != (rule['id'], rule['sku']):
            raise ValueError('Variant mismatch: ' + offer_id)
        gallery = ozon_gallery(card)
        if not gallery or not same_picture(gallery[0],rule['cover']):
            raise ValueError('Cover changed: ' + offer_id)
        if card.get('color_image') or card.get('images360') or card.get('images_360'):
            raise ValueError('Unexpected special media: ' + offer_id)
        desired = [gallery[0], *rule['add']]
        for i, photo in enumerate(desired):
            if any(same_picture(photo, earlier) for earlier in desired[:i]):
                raise ValueError('Duplicate images: ' + offer_id)
        complete = equal_gallery(gallery, desired)
        if len(gallery) != 1 and not complete:
            raise ValueError('Unexpected new photos: ' + offer_id)
        clip = videos(card)
        if bool(clip) != (offer_id == '0AM325467J'):
            raise ValueError('Unexpected video baseline: ' + offer_id)
        plans[offer_id] = (desired, complete, clip)
    return plans

def main():
    plans = prepare(fetch_cards())
    baseline = ratings()
    for offer_id, (wanted, complete, clips) in plans.items():
        print('BASELINE', offer_id, baseline.get(RULES[offer_id]['sku']), 'video', clips, flush=True)
        if not complete:
            payload = {'offer_id': offer_id, 'primary_image': wanted[0], 'images': wanted[1:], 'color_image':''}
            answer = oz._post('/v2/product/pictures/import', {'items':[payload]}, retries=1)
            if not answer or answer.get('error'):
                raise RuntimeError('No seller acknowledgment: ' + offer_id)
            print('ACK', offer_id, flush=True)

    pending = dict(plans)
    for attempt in range(30):
        rows = fetch_cards()
        for offer_id, (wanted, complete, clips) in list(pending.items()):
            card = one(rows, 'offer_id', offer_id)
            gallery = ozon_gallery(card)
            if not equal_gallery(gallery, wanted) or videos(card) != clips:
                print('WAIT', offer_id, len(gallery), videos(card) == clips, flush=True)
                continue
            sku = RULES[offer_id]['sku']
            receipt = dict(offer_id=offer_id, sku=sku, product_id=RULES[offer_id]['id'],
                           prior_count=1, verified_count=len(gallery), seller_gallery=gallery,
                           sources=RULES[offer_id]['add'], original_order=True, videos=clips,
                           rating_before=baseline.get(sku), rating_after=ratings().get(sku))
            print('RECEIPT ' + json.dumps(receipt), flush=True)
            del pending[offer_id]
        if not pending:
            return
        if attempt < 29:
            time.sleep(10)
    raise RuntimeError('Moderation not verified for: ' + ', '.join(pending))

if __name__ == '__main__':
    main()
