"""Read-only investigation of Ozon attribute drift. Cached comparison is
historical evidence, not a substitute for the original pre-write snapshot."""
import argparse
import collections
import datetime
import hashlib
import json
import os
from pathlib import Path
import time
from content_transport import guard
from content_sync import one, description_value
from content_manifest import load
import ozon_client as oz

ROOT=Path(__file__).resolve().parent

def canonical(value):
    return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))

def groups(row,key):
    result=collections.defaultdict(list)
    for a in row.get(key,[]):
        if key=='attributes' and a.get('id')==4191: continue
        result[(a.get('complex_id',0),a.get('id'))].append(a)
    return result

def attribute_diff(a,b):
    changes=[]
    for key in ('attributes','complex_attributes'):
        old,new=groups(a,key),groups(b,key)
        for pair in sorted(old.keys()|new.keys(),key=str):
            x,y=old.get(pair,[]),new.get(pair,[])
            if x==y: continue
            # Report nested value-order changes separately, but do not exempt
            # them from the write policy: actual values require review.
            def multiset(rows):
                norm=[]
                for attr in rows:
                    val=dict(attr)
                    if isinstance(val.get('values'),list):
                        val['values']=sorted(val['values'],key=canonical)
                    norm.append(canonical(val))
                return sorted(norm)
            changes.append({'group':key,'complex_id':pair[0],'id':pair[1],
                            'kind':'added' if not x else 'removed' if not y else
                            'order_only' if multiset(x)==multiset(y) else 'value_or_metadata_changed',
                            'before_sha256':hashlib.sha256(canonical(x).encode()).hexdigest(),
                            'after_sha256':hashlib.sha256(canonical(y).encode()).hexdigest()})
    return changes

def selected_attributes(row):
    """Only the two observed non-description product attributes."""
    return {str(a['id']):[{'value':v.get('value'),'dictionary_value_id':v.get('dictionary_value_id')}
                         for v in a.get('values',[])]
            for a in row.get('attributes',[]) if a.get('id') in (9048,23173)}

def run(skus):
    if not skus or len(skus)>10 or len(skus)!=len(set(skus)) or set(skus)-set(load()):
        raise ValueError('Exact approved diagnostic SKU list required')
    report={'mode':'read_only','writes':0,'cached_source':'data/ozon_products.json (historical, not original pre-write evidence)',
            'revision':os.getenv('GITHUB_SHA'),'products':{}}
    cached=json.loads((ROOT/'data/ozon_products.json').read_text())['names']
    with guard([],allow_write=False):
        first=oz.get_product_names(skus)
        time.sleep(3)
        second=oz.get_product_names(skus)
        # A separate comparison cohort helps distinguish platform-wide schema
        # changes from changes confined to products recently edited.
        controls=['0am325025G','0am325025q','0AM325467J','0am325066ad','0am325025m','0AM301212A','0am325025ae','0am325025HX']
        controls=[s for s in controls if s not in skus and any(r.get('offer_id')==s for r in cached)]
        current_controls=oz.get_product_names(controls) if controls else []
        report['comparison_cohort']={}
        for sku in controls:
            try:
                old=one(cached,'offer_id',sku);cur=one(current_controls,'offer_id',sku)
                report['comparison_cohort'][sku]={'historical':selected_attributes(old),'current':selected_attributes(cur),'change_ids':[v['id'] for v in attribute_diff(old,cur)]}
            except Exception as exc:report['comparison_cohort'][sku]={'error':str(exc)[:300]}
        for sku in skus:
            try:
                old=one(cached,'offer_id',sku);a=one(first,'offer_id',sku);b=one(second,'offer_id',sku)
                defs=oz.get_category_attributes(a['description_category_id'],a['type_id'])
                if isinstance(defs,dict): defs=defs.get('result',[])
                names={x.get('id'):x.get('name') for x in defs if isinstance(x,dict)}
                c=attribute_diff(old,b);d=attribute_diff(a,b)
                for change in c+d: change['attribute_name']=names.get(change['id'])
                report['products'][sku]={'current_description_matches':description_value('ozon',b)==load()[sku]['text']['ozon']['description'],
                                         'cached_to_live_changes':c,'repeated_read_changes':d,
                                         'historical_group_and_kiz':selected_attributes(old),
                                         'live_group_and_kiz':selected_attributes(b),
                                         'cached_description_changed':description_value('ozon',old)!=description_value('ozon',b),
                                         'write_hold_unchanged':True}
            except Exception as exc: report['products'][sku]={'error':str(exc)[:1000]}
    report['checked_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    out=ROOT/'reports/content-ozon-diagnosis';out.mkdir(parents=True,exist_ok=True)
    (out/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False))
    return int(any('error' in x for x in report['products'].values()))

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--article',required=True)
    args=parser.parse_args()
    raise SystemExit(run(args.article.split(',')))
