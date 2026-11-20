"""Read all Ozon score records; apply only individually corroborated physical facts."""
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timezone
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ozon_client as oz

SPECS = json.loads(r'''{"0am325025С":{"id":1508696317,"sku":1950722078,"cat":17028756,"type":971100605,"fields":[{"name":"Вид техники","value":"Легковые автомобили","source":4384,"needle":"Уплотнительное кольцо гидроаккумулятора мехатроника DSG7 DQ200","old":null,"override":false}]},"0am325025n":{"id":2397703853,"sku":2615648706,"cat":17028756,"type":970984897,"fields":[{"name":"Вид техники","value":"Легковые автомобили","source":4384,"needle":"Соленоид переключения передач (Shift) мехатроника DSG7 DQ200","old":null,"override":false}]},"separatornaya-plastiny-solenoid-dq200":{"id":6447289387,"sku":6161285981,"cat":17028756,"type":971100605,"fields":[{"name":"Вид техники","value":"Легковые автомобили","source":4384,"needle":"Сепараторная пластина — 1 шт.; пластины на соленоиды — 2 шт.","old":null,"override":false}]}}''')
OUT = Path('/tmp/ozon-publication')
OUT.mkdir(exist_ok=True)
BLOCKED = {'0cw0am','TEST001','0BH325183','0BH325183 B','0BH325183B','korpus-filtra-0bh325159','DQ500','Dq500','02e305045'}
assert not (set(SPECS) & BLOCKED)
EXPECTED_NAMES = {'Количество в упаковке, шт','Количество, штук','OEM-номер','Вид техники','Расположение детали','Длина, мм','Материал'}


def capture_score(skus):
    scores={}
    ids=sorted(set(skus))
    for n in range(0,len(ids),100):
        raw=oz._post('/v1/product/rating-by-sku',{'skus':ids[n:n+100]})
        scores.update({int(p['sku']):p for p in raw.get('products',[])})
    return scores


def groups(score):
    return {g['key']:g['rating'] for g in score.get('groups',[])}


def attrs(raw):
    return {a['id']:a['values'] for a in raw.get('attributes',[]) if a.get('values')}


def val(values):
    return '; '.join(str(x['value']) for x in values or [] if x.get('value') is not None)


def card_by(rows):
    return {r['offer_id']:r for r in rows if r.get('offer_id')}


def overall_facts(excl, r):
    return {k:r.get(k) for k in ('name','sku','description_category_id','type_id','height','width','depth','weight','primary_image','images','complex_attributes')} | {'other':{k:v for k,v in attrs(r).items() if k not in excl}}


def prices(keys):
    return {k:(v.get('price') or {}).get('price') for k,v in card_by(oz.get_prices_and_stocks(keys)).items()}


def save(fn,data): (OUT/fn).write_text(json.dumps(data,ensure_ascii=False,indent=2))


def prepare():
    catalog=oz.list_products()
    available=card_by(catalog)
    infos=card_by(oz.get_product_info_list(list(available)))
    active={k for k,i in infos.items() if k not in BLOCKED and not i.get('is_archived') and not i.get('is_autoarchived') and not available[k].get('archived') and (i.get('statuses',{}).get('status_name') or '').lower().replace('ё','е')=='продается'}
    wb_snapshot=json.loads((Path(__file__).resolve().parents[1]/'data'/'wb_cards.json').read_text())['cards']
    wb_source={w['vendorCode']:w for w in wb_snapshot if w.get('vendorCode') in SPECS}
    for exact in SPECS:
        facts=[a.get('value') for a in wb_source.get(exact,{}).get('characteristics',[]) if a.get('id')==15003115]
        if facts!=[['легковые автомобили']]:raise ValueError('Missing exact own WB vehicle type for '+exact)
    raw=card_by(oz.get_product_names(list(SPECS)))
    all_skus=[int(x['sku']) for x in catalog if x.get('sku')]
    scores=capture_score(all_skus)
    schema={}; dictionaries={}; approved={}; skipped=[]
    for k,candidate in SPECS.items():
        if k not in active:
            skipped.append([k,'not selling or outside live listing']);continue
        r=raw.get(k,{}); expected=candidate
        if r.get('id')!=expected['id'] or r.get('sku')!=expected['sku'] or r.get('type_id')!=expected['type'] or r.get('description_category_id')!=expected['cat']:
            skipped.append([k,'identity/category changed']);continue
        if any(tag in (r.get('name','')+' '+k).casefold() for tag in ('0bh','dq500','ребрист')):
            skipped.append([k,'excluded product family']);continue
        score=scores.get(r['sku'],{}); group=next((g for g in score.get('groups',[]) if g['key']=='other_attributes'),{})
        if not score: skipped.append([k,'score unavailable']);continue
        suggestions={a['name']:a['id'] for a in group.get('improve_attributes',[])}
        pair=(expected['cat'],expected['type'])
        if pair not in schema: schema[pair]={a['name']:a for a in oz.get_category_attributes(*pair)}
        current=attrs(r)
        for fact in candidate['fields']:
            name=fact['name']
            if name not in EXPECTED_NAMES: raise ValueError('Unexpected name')
            definition=schema[pair].get(name)
            if not definition: skipped.append([k,name,'category has no such field']);continue
            aid=int(definition['id'])
            if suggestions.get(name)!=aid and not fact['override']:
                skipped.append([k,name,'no current score recommendation']);continue
            if fact['needle'].casefold() not in val(current.get(fact['source'],[])).casefold():
                skipped.append([k,name,'current primary evidence differs']);continue
            old=val(current.get(aid,[]))
            if old!=str(fact['old'] or ''):
                skipped.append([k,name,'already populated or source changed']);continue
            if fact['override'] and not (k=='plastiny-solenoid-dq200-2sht' and aid==7202 and old=='30' and '2 шт' in r['name'] and '2 шт' in val(current.get(4384,[]))):
                skipped.append([k,name,'correction precondition failed']);continue
            target=fact['value']
            values=[{'value':target}]
            if definition.get('dictionary_id'):
                dk=pair+(aid,target)
                if dk not in dictionaries:
                    if len(target)>=2:
                        hits=oz.search_attribute_values(*pair,aid,target)
                    else:
                        hits=[]; last=0
                        for batch in range(15):
                            body={'description_category_id':pair[0],'type_id':pair[1],'attribute_id':aid,'language':'DEFAULT','limit':100,'last_value_id':last}
                            response=oz._post('/v1/description-category/attribute/values',body)
                            chunk=response.get('result') or []
                            hits.extend(chunk)
                            if any(str(v.get('value','')).strip()==target for v in chunk) or not response.get('has_next') or not chunk: break
                            last=int(chunk[-1]['id'])
                    dictionaries[dk]=[v for v in hits if str(v.get('value','')).strip().casefold()==target.casefold()]
                hits=dictionaries[dk]
                if len(hits)!=1:
                    skipped.append([k,name,'no unique exact Ozon dictionary match']);continue
                values=[{'value':hits[0]['value'],'dictionary_value_id':int(hits[0]['id'])}]
            elif definition.get('type')=='Integer' and not target.isdigit():
                raise ValueError('Expected integer for '+name)
            approved.setdefault(k,[]).append({'id':aid,'name':name,'values':values,'prev':old,'source_attr':fact['source'],'evidence':fact['needle']})
    return raw,scores,approved,{'known_offers':len(available),'live_info_rows':len(infos),'selling_excluding_delisted_test':len(active),'official_scores':len(scores),'candidate_offers':len(SPECS),'candidate_fields':sum(len(p['fields']) for p in SPECS.values()),'approved_offers':len(approved),'approved_fields':sum(map(len,approved.values())),'skipped':skipped}


def main():
    mode=sys.argv[1]
    if mode not in ('check','apply'):raise ValueError('Unsupported mode')
    raw,scores,approved,summary=prepare()
    print('OZ_BATCH_COVERAGE',json.dumps(summary,ensure_ascii=False),flush=True)
    if mode=='check':
        save('plan.json',approved);save('coverage.json',summary)
        for k,p in approved.items():print('OZ_DRY',k,[(a['name'],a['values'][0]['value']) for a in p],flush=True)
        return
    if json.loads((OUT/'plan.json').read_text())!=approved:raise ValueError('Plan changed after dry run')
    if not approved: return
    before_prices=prices(list(approved))
    all_tasks=[]
    keys=list(approved)
    for off in range(0,len(keys),10):
        subset=keys[off:off+10]
        if set(subset)&BLOCKED:raise ValueError('Blocked SKU in submission')
        items=[{'offer_id':k,'attributes':[{'id':a['id'],'complex_id':0,'values':a['values']} for a in approved[k]]} for k in subset]
        ack=oz.update_attributes(items)
        task=ack.get('task_id') or (ack.get('result') or {}).get('task_id')
        if not task: raise ValueError('Ozon did not acknowledge')
        all_tasks.append(task)
        print('OZ_TASK',task,'count',len(subset),flush=True)
    task_receipts={}
    for task in all_tasks:
        statuses={}
        for attempt in range(24):
            data=oz.get_import_status(int(task))
            statuses={i.get('offer_id'):{'status':i.get('status'),'errors':i.get('errors') or []} for i in (data.get('result',{}).get('items') or [])}
            if statuses and all(str(i['status']).lower() not in ('pending','processing','') for i in statuses.values()):break
            if attempt<23:time.sleep(4)
        task_receipts[str(task)]=statuses
    def verified_snapshot():
        current=card_by(oz.get_product_names(keys))
        return current, {k:{a['id']:val(attrs(current.get(k,{})).get(a['id'],[]))==val(a['values']) for a in approved[k]} for k in keys}
    for n in range(18):
        current,verified=verified_snapshot()
        if all(all(z.values()) for z in verified.values()):break
        if n<17:time.sleep(4)
    after_prices=prices(keys)
    ids=[raw[k]['sku'] for k in keys]
    def scoreboard():
        after=capture_score(ids)
        result={}
        for k in keys:
            old=scores[raw[k]['sku']];score=after.get(raw[k]['sku'],{});exclude={z['id'] for z in approved[k]}
            result[k]={'sku':raw[k]['sku'],'product_id':raw[k]['id'],'score_before':old['rating'],'score_after':score.get('rating'),'group_before':groups(old),'group_after':groups(score),'fields':[{ 'id':z['id'],'name':z['name'],'before':z['prev'],'after':val(z['values']),'readback':verified[k][z['id']]} for z in approved[k]],'other_attributes_title_gallery_dimensions_equal':overall_facts(exclude,raw[k])==overall_facts(exclude,current[k]),'seller_price_equal':bool(before_prices.get(k)) and before_prices[k]==after_prices.get(k)}
        return result
    first=scoreboard();save('first_readback.json',{'utc':datetime.now(timezone.utc).isoformat(),'results':first,'tasks':task_receipts})
    for k,r in first.items():print('OZ_FIRST',k,r['score_before'],r['score_after'],all(f['readback'] for f in r['fields']),r['other_attributes_title_gallery_dimensions_equal'],r['seller_price_equal'],flush=True)
    time.sleep(65)
    final=scoreboard();save('second_readback.json',{'utc':datetime.now(timezone.utc).isoformat(),'results':final,'tasks':task_receipts})
    for k,r in final.items():print('OZ_FINAL',k,r['score_before'],r['score_after'],r['group_before'].get('other_attributes'),r['group_after'].get('other_attributes'),flush=True)
    if not all(all(a['readback'] for a in r['fields']) and r['other_attributes_title_gallery_dimensions_equal'] and r['seller_price_equal'] for r in final.values()):raise ValueError('Some checks pending; see receipt')

if __name__=='__main__':main()
