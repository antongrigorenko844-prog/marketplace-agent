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

for info in oz.get_product_info_list(list(PLAN)):
    st = info.get('statuses',{})
    print('STATUS ' + json.dumps({'offer':info.get('offer_id'),'status_name':st.get('status_name'),'moderation':st.get('moderate_status'),'warning':st.get('status_description'),'archived':info.get('is_archived'),'problems':[e.get('code') for e in info.get('errors',[])]},ensure_ascii=False), flush=True)
row=rows['0AM325413A']
for k in ['name','height','depth','width','weight','primary_image','images','description_category_id','type_id','complex_attributes']:
    print('ORIGINAL ' + json.dumps({'field':k,'value':row.get(k)},ensure_ascii=False), flush=True)
for a in row.get('attributes',[]):
    if a['id']!=4191: print('OTHER ' + json.dumps(a,ensure_ascii=False),flush=True)
