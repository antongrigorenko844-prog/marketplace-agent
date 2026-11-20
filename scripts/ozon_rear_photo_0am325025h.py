"""Append the owner's rear-view photo to one Ozon gallery and read the result back."""
import hashlib
import json
import time
from pathlib import Path

import ozon_client as oz
import photo_host
from content_sync import equal_gallery, one, ozon_gallery, same_picture

OFFER = '0am325025H'
PRODUCT = 3171023605
SKU = 3190533531
PHOTO = Path('media/ozon/0am325025H/0am325025H_5.jpg')
DIGEST = 'b172348fa6324624e1d268af10f6bdf35a1c894095c6fb1726501745521c2ac4'
BASE = 'https://ir-20.ozone.ru/s3/'
EXPECTED = [BASE + name for name in (
    'multimedia-1-h/20386088333.jpg', 'multimedia-1-1/14848105789.jpg',
    'multimedia-1-0/14848100064.jpg', 'multimedia-1-h/21314625029.jpg',
)]

def card():
    row = one(oz.get_product_names([OFFER]), 'offer_id', OFFER)
    if (int(row['id']), int(row['sku'])) != (PRODUCT, SKU):
        raise ValueError('Offer does not match the verified product')
    return row

def video(row):
    return [a for a in row.get('complex_attributes', []) if a.get('id') in (21841, 21837)]

def rating():
    try:
        response = oz._post('/v1/product/rating-by-sku', {'skus':[SKU]})
        rows = response.get('products') or response.get('result', [])
        if isinstance(rows, dict):
            rows = rows.get('products') or rows.get('items') or []
        return next((r.get('rating') for r in rows if str(r.get('sku')) == str(SKU)), None)
    except (oz.OzonApiError, ValueError, TypeError):
        return None

def main():
    if hashlib.sha256(PHOTO.read_bytes()).hexdigest() != DIGEST:
        raise ValueError('Unexpected image contents')
    before = card()
    gallery = ozon_gallery(before)
    if len(gallery) not in (4, 5) or not equal_gallery(gallery[:4], EXPECTED):
        raise ValueError('Seller gallery has changed since review')
    if not video(before) or before.get('images360') or before.get('images_360') or before.get('color_image'):
        raise ValueError('Different special media on this card; recheck before writing')
    url = photo_host.upload_file(str(PHOTO), filename='0am325025H_rear_' + DIGEST[:10] + '.jpg')
    if any(same_picture(old, url) for old in gallery[:4]):
        raise ValueError('Duplicate of an existing gallery image')
    desired = gallery[:4] + [url]
    previous = rating()
    if len(gallery) == 5 and not equal_gallery(gallery, desired):
        raise ValueError('A different fifth image already exists')
    if len(gallery) == 4:
        response = oz._post('/v1/product/pictures/import', {'product_id':PRODUCT, 'images':desired}, retries=1)
        if not response or response.get('error'):
            raise RuntimeError('Seller did not acknowledge the gallery update')
        print('Seller acknowledged the single-product gallery request', flush=True)
    for attempt in range(28):
        after = card()
        observed = ozon_gallery(after)
        if len(observed) == 5 and equal_gallery(observed, desired):
            if video(after) != video(before):
                raise RuntimeError('Video verification failed')
            result = dict(offer_id=OFFER, product_id=PRODUCT, sku=SKU, total_photos=5,
                          cover_and_order_verified=True, video_verified=True,
                          photos=observed, source=url, rating_before=previous, rating_after=rating())
            print('GALLERY_RECEIPT ' + json.dumps(result, ensure_ascii=False), flush=True)
            return
        if attempt < 27:
            time.sleep(10)
    raise RuntimeError('Gallery not visible yet. Read seller status before another import.')

if __name__ == '__main__':
    main()
