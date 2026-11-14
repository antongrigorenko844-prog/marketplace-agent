"""Replace a rejected image and a broken placeholder for one exact offer."""
import sys, json, time, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import yandex_market_client as ym
from market_content import read_rows, preserve_typed_parameters, grouped

SKU = '0cw0am325025'
URL = 'https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/0cw0am325025-market-20261115.png'
OLD = ['https://cdn1.ozone.ru/s3/multimedia-1-k/8529517784.jpg', 'https://avatars.mds.yandex.net/get-mpic/5145248/img_id6010841102857138334.png/orig']
OUT = Path('/tmp/market-kit-photo'); OUT.mkdir(exist_ok=True)
def save(name, value):
    (OUT / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2))

b = ym.get_business_id()
before = read_rows(b, 'offer-mappings', [SKU])[0]
card = read_rows(b, 'offer-cards', [SKU])[0]
save('before', before); save('card-before', card)
assert before['offer']['pictures'] in [OLD, [URL]], 'Gallery changed; inspect before replacing'
payload = preserve_typed_parameters(b, [{'offer': {'offerId': SKU, 'pictures': [URL]}}])
save('request', payload)
if before['offer']['pictures'] != [URL]:
    st, res = ym._request('POST', f'/v2/businesses/{b}/offer-mappings/update', json_body={'offerMappings': payload})
    save('response', {'http': st, 'body': res})
    assert st == 200 and res.get('status') == 'OK', (st, res)
    assert not any(r.get('errors') for r in res.get('results', [])), res
for _ in range(24):
    after = read_rows(b, 'offer-mappings', [SKU])[0]
    if after['offer'].get('pictures') == [URL]: break
    time.sleep(5)
ca = read_rows(b, 'offer-cards', [SKU])[0]
save('after', after); save('card-after', ca)
assert after['offer']['pictures'] == [URL]
for key in ['name', 'description', 'basicPrice', 'purchasePrice', 'weightDimensions', 'vendor', 'vendorCode', 'barcodes', 'params', 'videos']:
    assert before['offer'].get(key) == after['offer'].get(key), ('Unexpected change', key)
assert grouped(card.get('parameterValues', [])) == grouped(ca.get('parameterValues', [])), 'Characteristics changed'
save('result', {'sku': SKU, 'pictures_verified': True, 'price_and_other_content_preserved': True, 'stock_writes': 0, 'checked_at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'rating': ca.get('contentRating'), 'rating_status': ca.get('contentRatingStatus')})
print('Verified replacement of rejected picture and removal of placeholder for', SKU)
