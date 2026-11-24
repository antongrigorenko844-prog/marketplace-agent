"""One-shot Ozon recovery probe. No mass writes, no relaxed grouping/KIZ rules.
Only a uniquely identified, inexpensive, already normalized singleton is eligible.
Preserve and compare all non-description attributes, model membership, SKU,
commercial identifiers and price snapshots. Abort if an earlier intent exists.
"""
import argparse
from collections import Counter
from pathlib import Path
import json
import os
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import content_pipeline as pipeline
import content_sync as sync
import content_manifest as manifest
import content_master_sync as master
import ozon_client as oz
from content_transport import guard
from content_ozon_diagnosis import attribute_diff,selected_attributes

ROOT=pipeline.ROOT
REQUEST=ROOT/'data/ozon_description_recovery_request.json'
OUT=ROOT/'reports/content-ozon-recovery'
POLICY=ROOT/'data/content_description_policy.json'
LOW_RISK=('0AM301212A','0AM325120A','0AM325413A','0AM325443D','0AM325467J',
          '01X301127C','WHT001922','0am325025q','0am325025r','0am325025l',
          'filtr-dsg7-dq200','prokladka-kryshki-dq200','pylnik-shtoka-dq200',
          'klipsa-datchika-peredach-dq200','salnik-shtoka-orig','salniki-tolkatelya-4sht')
CONTROL=('0am325025H','6t30')
ATTR_FIELDS=('id','offer_id','product_id','sku','name','barcode','barcodes',
             'description_category_id','type_id','height','depth','width',
             'dimension_unit','weight','weight_unit','primary_image','images',
             'color_image','pdf_list','model_info','attributes_with_defaults')
INFO_FIELDS=('id','product_id','offer_id','sku','name','barcodes','description_category_id',
             'type_id','is_archived','is_autoarchived','sources','model_info','currency_code')

def attrs(row,aid):
    return [a for a in row.get('attributes',[]) if a.get('id')==aid]

def single_value(row,aid):
    items=attrs(row,aid)
    if len(items)!=1 or len(items[0].get('values',[]))!=1:return None
    return str(items[0]['values'][0].get('value') or '')

def singleton(row):
    group=row.get('model_info') or {}
    try:return int(group.get('count'))==1 and int(group.get('model_id'))>0
    except (TypeError,ValueError):return False

def normalized_candidate(row,counts):
    val=single_value(row,9048)
    sku=row.get('offer_id','')
    if not singleton(row):return 'model is not a verified singleton'
    if not val or sku.casefold() not in val.casefold():return 'model name not already SKU-specific'
    if counts[val]!=1:return 'model name shared in the observed catalog'
    if single_value(row,23173)!='1':return 'KIZ quantity 1 not already present'
    return None

def protected_changes(before,after,info_before,info_after):
    changes=[]
    for f in ATTR_FIELDS:
        x,y=before.get(f),after.get(f)
        if f=='attributes_with_defaults':
            x=sorted(str(i) for i in (x or []) if str(i)!='4191')
            y=sorted(str(i) for i in (y or []) if str(i)!='4191')
        if x!=y:changes.append('card:'+f)
    changes.extend('attribute:'+str(x['id'])+':'+x['kind'] for x in attribute_diff(before,after) if x['kind']!='order_only')
    changes.extend('info:'+f for f in INFO_FIELDS if info_before.get(f)!=info_after.get(f))
    return changes

def pick_price(rows,sku):
    hits=[r for r in rows if r.get('offer_id')==sku]
    if len(hits)!=1:raise ValueError('Price row absent or ambiguous')
    p=hits[0].get('price')
    if not isinstance(p,dict):raise ValueError('Unknown price response shape')
    # Avoid marketplace co-funding and dynamic marketing discounts; seller
    # controlled prices must not change.
    keep={k:p[k] for k in ('price','old_price','min_price','currency_code','vat') if k in p}
    if 'price' not in keep:raise ValueError('Seller price missing')
    return keep

def one(rows,sku):
    return sync.one(rows,'offer_id',sku)

def read_card(sku):
    with guard([],allow_write=False,descriptions_only=True):
        card=one(oz.get_product_names([sku]),sku)
        info=one(oz.get_product_info_list([sku]),sku)
        price=pick_price(oz.get_prices_and_stocks([sku]),sku)
    if info.get('is_archived') or info.get('is_autoarchived'):
        raise ValueError('Archived product forbidden')
    return card,info,price

def run():
    req=json.loads(REQUEST.read_text())
    if req.get('version')!=1 or req.get('mode')!='one_safe_probe' or os.getenv('GITHUB_REF')!='refs/heads/main':
        raise ValueError('Exact one-shot recovery request on main required')
    if not json.loads(POLICY.read_text()).get('paused_writes',{}).get('ozon'):
        raise ValueError('Expected Ozon mass-write hold')
    report_path=OUT/'summary.json'
    if report_path.exists():
        old=json.loads(report_path.read_text())
        if old.get('request_id')==req['request_id'] and old.get('write_intent'):
            print('Previous intent exists; do not repeat seller write')
            return 0
    pipeline.current_main()
    rules=manifest.load()
    _,master_rows,dupes=master.read_rows((ROOT/'data/master_control.xlsx').read_bytes(),'Товары',12)
    report={'request_id':req['request_id'],'revision':os.getenv('GITHUB_SHA'),
            'checked_utc':pipeline.stamp(),'mass_hold_retained':True,'write_intent':False,
            'seller_description_writes':0,'price_writes':0,'stock_writes':0,'candidates':{},'controls':{}}
    with guard([],allow_write=False,descriptions_only=True):
        listed=oz.list_products()
        offers=[r['offer_id'] for r in listed if r.get('offer_id') and not r.get('archived') and not r.get('is_archived')]
        live=[]
        for i in range(0,len(offers),50):live.extend(oz.get_product_names(offers[i:i+50]))
    if len(live)!=len(offers) or len(set(offers))!=len(offers) or len({r.get('offer_id') for r in live})!=len(offers):
        report['status']='census_incomplete_no_write'
        report.update(listed=len(offers),read=len(live))
        pipeline.write_json(report_path,report);pipeline.checkpoint([report_path],live=True);return 0
    by={r['offer_id']:r for r in live}
    report['census_count']=len(live)
    counts=Counter(single_value(r,9048) for r in live)
    # Compare actual model ID and model membership with the historical cache,
    # not just the text of attribute 9048.
    cached=json.loads((ROOT/'data/ozon_products.json').read_text()).get('names',[])
    historical={r.get('offer_id'):r for r in cached}
    for sku in CONTROL:
        if sku in by and sku in historical:
            a,z=historical[sku],by[sku]
            report['controls'][sku]={'historical_model_info':a.get('model_info'),'current_model_info':z.get('model_info'),
                'historical_sku':a.get('sku'),'current_sku':z.get('sku'),
                'attribute_changes':[{'id':v['id'],'kind':v['kind']} for v in attribute_diff(a,z)],
                'historical_default_ids':a.get('attributes_with_defaults',[]),'current_default_ids':z.get('attributes_with_defaults',[])}
    report['normalized_singletons']=[s for s,r in by.items() if not normalized_candidate(r,counts)]
    picked=None
    for sku in LOW_RISK:
        if sku not in by:continue
        row=by[sku]
        target=rules.get(sku,{}).get('text',{}).get('ozon',{}).get('description')
        reason=normalized_candidate(row,counts)
        if sku not in master_rows or sku in dupes:reason='no unambiguous master row'
        if not target:reason='no reviewed canonical description'
        if target and sync.description_value('ozon',row)==target:reason='live text already matches'
        report['candidates'][sku]={'reason':reason,'product_id':row.get('id'),'model_info':row.get('model_info'),
                                    'has_kiz_quantity':single_value(row,23173)=='1'}
        if reason or picked:continue
        try:
            a,i,p=read_card(sku)
            if float(p['price'])>3000:raise ValueError('Seller price exceeds narrow probe cap')
            if a.get('id')!=row.get('id') or protected_changes(row,a,i,i):raise ValueError('Preflight read changed')
            if normalized_candidate(a,counts):raise ValueError('Fresh read fails singleton guard')
            picked=(sku,target,a,i,p)
        except Exception as exc:report['candidates'][sku]['reason']=str(exc)[:300]
    if picked is None:
        report['status']='no_safe_eligible_probe'
        pipeline.write_json(report_path,report);pipeline.checkpoint([report_path],live=True);return 0
    sku,target,before,info_before,price_before=picked
    audit=[]
    evidence={'before':before,'info_before':info_before,'price_before':price_before,'writes':audit}
    sync.OUT=OUT
    sync.encrypt_report(evidence)
    report.update(sku=sku,product_id=before['id'],model_info_before=before.get('model_info'),
                  write_intent=True,status='submitted_intent_persisted',target_hash=pipeline.digest(target))
    pipeline.write_json(report_path,report)
    pipeline.checkpoint([report_path,OUT/'evidence.enc'],live=True)
    pipeline.current_main()
    # Exactly one HTTP attempt. No retry on ambiguous network result.
    payload={'items':[{'offer_id':sku,'attributes':[{'id':4191,'complex_id':0,
                          'values':[{'dictionary_value_id':0,'value':target}]}]}]}
    task=None
    try:
        with guard(audit,allow_write=True,descriptions_only=True):
            ack=oz._post('/v1/product/attributes/update',payload,retries=1)
            task=ack.get('task_id') or (ack.get('result') or {}).get('task_id')
        report['task_id']=task
    except Exception as exc:
        report['write_ack_error']=str(exc)[:300]
    report['seller_description_writes']=len(audit)
    report['status']='processing_no_blind_retry'
    # Persist acknowledgment and encrypted request before readback polling.
    sync.encrypt_report(evidence)
    pipeline.write_json(report_path,report)
    pipeline.checkpoint([report_path,OUT/'evidence.enc'],live=True)
    consecutive=0
    for attempt in range(12):
        time.sleep(5)
        try:
            if task:
                with guard([],allow_write=False,descriptions_only=True):
                    result=oz.get_import_status(int(task))
                report['task_result']=result.get('result',result)
            after,info_after,price_after=read_card(sku)
            evidence.update(after=after,info_after=info_after,price_after=price_after)
            changes=protected_changes(before,after,info_before,info_after)
            if price_before!=price_after:changes.append('seller_price')
            report.update(model_info_after=after.get('model_info'),protected_changes=changes,
                          text_matches=sync.description_value('ozon',after)==target)
            if changes:
                report['status']='protected_drift_mass_hold_retained'
                break
            if report['text_matches']:
                consecutive+=1
                if consecutive>=2:
                    report['status']='verified_singleton_probe_only'
                    break
            else:consecutive=0
        except Exception as exc:
            consecutive=0;report['read_error']=str(exc)[:300]
    report['checked_utc']=pipeline.stamp()
    sync.encrypt_report(evidence);pipeline.write_json(report_path,report)
    pipeline.checkpoint([report_path,OUT/'evidence.enc'],live=True)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report['status']=='verified_singleton_probe_only' else 1

if __name__=='__main__':
    sys.exit(run())
