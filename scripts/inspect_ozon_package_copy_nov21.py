import difflib
import json
from scripts.publish_ozon_package_copy_nov21 import PLAN, read, text_field, ratings
import ozon_client as oz

rows = read()
scores = ratings([r['sku'] for r in PLAN.values()])
for key, rule in PLAN.items():
    row = rows[key]
    status = {}
    for field, wanted in rule['set'].items():
        actual = text_field(row,field)
        if actual == wanted:
            status[field] = 'verified'
        elif actual == rule['old'][field]:
            status[field] = 'original'
        else:
            status[field] = {'actual': actual[:1200], 'similarity': round(difflib.SequenceMatcher(None, actual, wanted).ratio(),2)}
    print('LIVE ' + json.dumps({'offer':key,'fields':status,'rating':scores.get(rule['sku'])},ensure_ascii=False),flush=True)

job = oz.get_import_status(6091072751)
for item in job.get('result',{}).get('items',[]):
    print('IMPORT ' + json.dumps({'offer':item.get('offer_id'), 'status':item.get('status'),'errors':[(e.get('code'),e.get('field'),e.get('attribute_id')) for e in item.get('errors',[])]},ensure_ascii=False),flush=True)
