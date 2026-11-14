"""Read current content and rating for two explicitly selected products."""
import datetime as dt
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym

IDS = ['0am325025', '0am325025x']
OUT = Path('/tmp/content-focus')
OUT.mkdir(exist_ok=True)

def save(name, value):
    (OUT / (name + '.json')).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    print(name + ': saved', flush=True)

def main():
    attrs = oz.get_product_names(IDS)
    save('ozon-attributes', attrs)
    info = oz.get_product_info_list(IDS)
    save('ozon-info', info)
    save('ozon-prices', oz.get_prices_and_stocks(IDS))
    save('ozon-stocks', oz.get_stocks(IDS))
    skus = list({int(x['sku']) for x in info if x.get('sku')})
    save('ozon-ratings', oz._post('/v1/product/rating-by-sku', {'skus': skus}))
    for c,t in {(a['description_category_id'],a['type_id']) for a in attrs}:
        save(f'attributes-{c}-{t}', oz.get_category_attributes(c,t))
    save('wb-cards', [r for r in wb.list_cards() if r.get('vendorCode') in IDS])
    biz=ym.get_business_id()
    status,body=ym._request('POST', f'/v2/businesses/{biz}/offer-mappings', json_body={'offerIds':IDS})
    assert status==200, (status,body)
    save('market',body)
    save('meta', {'checked_at':dt.datetime.now(dt.timezone.utc).isoformat(),'mode':'read only','offers':IDS})

if __name__ == '__main__':
    main()
