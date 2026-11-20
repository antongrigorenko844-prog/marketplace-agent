"""Publish verified buyer guides to the empty Ozon rich-content field."""
import hashlib
import json
import time
from pathlib import Path

import ozon_client as oz

ROOT = Path(__file__).resolve().parents[1]
PLAN = json.loads((ROOT / "data/selection_guides_review_20261121.json").read_text())
RICH = 11254

def text_field(row, field):
    return next((str(v.get('value') or '').strip() for a in row.get('attributes', []) if a['id'] == field
                 for v in a.get('values', []) if v.get('value')), '')

def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()

def source_ok(row, rule):
    description = text_field(row, 4191).casefold().replace('ё','е')
    return (row['id'] == rule['product'] and row.get('sku') == rule['sku']
            and digest(row['name']) == rule['title_hash']
            and digest(text_field(row, 4384)) == rule['composition_hash']
            and all(word in description for word in rule['terms']))

def guides(skus):
    return {s:json.loads((ROOT/'data'/'ozon_rich'/f'{s}.json').read_text()) for s in skus}

def content_rating(skus):
    try:
        response = oz._post('/v1/product/rating-by-sku', {'skus':[PLAN[s]['sku'] for s in skus]})
        return {str(r['sku']):r.get('rating') for r in response.get('products', [])}
    except (ValueError, KeyError, oz.OzonApiError):
        return {}

def protected(row):
    attrs = [a for a in row.get('attributes', []) if a['id'] != RICH]
    attrkey = lambda a:(a['id'],a.get('complex_id',0))
    return ([row.get(k) for k in ('name','description_category_id','type_id','primary_image','images','weight','height','width','depth')],
            sorted(attrs,key=attrkey),sorted(row.get('complex_attributes',[]),key=attrkey))

def equal_guide(row, data):
    value = text_field(row,RICH)
    if not value:
        return False
    try:
        return json.loads(value) == data
    except ValueError:
        return False

def part(skus, label):
    skip = {}
    drafts = guides(skus)
    before = {c['offer_id']:c for c in oz.get_product_names(skus)}
    info = {c['offer_id']:c for c in oz.get_product_info_list(skus)}
    todo = {}
    ident = set()
    for s in skus:
        row, i = before.get(s), info.get(s) or {}
        if not row or not source_ok(row,PLAN[s]) or i.get('is_archived') or i.get('is_autoarchived') or not i or i.get('visible', True) is False:
            skip[s] = 'source or selling status changed'
        elif equal_guide(row,drafts[s]):
            ident.add(s)
        elif text_field(row,RICH):
            skip[s] = 'rich content already exists'
        else:
            todo[s] = drafts[s]
    ratings_before = content_rating(skus)
    responses = {}
    if todo:
        items=[{'offer_id':s,'attributes':[{'complex_id':0,'id':RICH,'values':[{'value':json.dumps(doc,ensure_ascii=False)}]}]} for s,doc in todo.items()]
        ack = oz.update_attributes(items)
        task = ack.get('task_id') or (ack.get('result') or {}).get('task_id')
        if not task:
            raise RuntimeError('No import task ID')
        print('IMPORT', label, task, sorted(todo), flush=True)
        for attempt in range(18):
            out=oz.get_import_status(int(task))
            responses={r['offer_id']:r.get('status') for r in out.get('result',{}).get('items',[]) or out.get('items',[])}
            if set(responses)==set(todo) and all(v not in ('pending','processing','') for v in responses.values()):
                break
            time.sleep(4)
    checks={}
    for attempt in range(12 if todo else 1):
        after={c['offer_id']:c for c in oz.get_product_names(skus)}
        checks={s:equal_guide(after.get(s,{}),drafts[s]) for s in set(todo)|ident}
        if all(checks.values()) or attempt==11:
            break
        time.sleep(5)
    scores=content_rating(skus)
    verified=[]
    for s, matches in checks.items():
        if matches and protected(before[s])!=protected(after[s]):
            raise RuntimeError('Companion fields changed: '+s)
        if matches:
            verified.append(s)
    print('RICH_RECEIPT '+json.dumps({'batch':label,'new':sorted(set(verified)&set(todo)),'already':sorted(ident),
          'pending':sorted(set(todo)-set(verified)),'skipped':skip,'import_status':responses,
          'scores':{s:[ratings_before.get(str(PLAN[s]['sku'])),scores.get(str(PLAN[s]['sku']))] for s in verified}}),flush=True)
    return not (set(todo)-set(verified))

def main():
    first=['0am325091f-set','0am325091e-set']
    if not part(first,'pilot'):
        raise RuntimeError('Pilot still pending; no other requests')
    if not part([s for s in PLAN if s not in first],'batch'):
        raise RuntimeError('Some guides are awaiting verification')

if __name__=='__main__':
    main()
