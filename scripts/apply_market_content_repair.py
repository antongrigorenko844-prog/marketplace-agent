"""Restore typed characteristics and publish descriptions written for Market."""
import sys,json,copy,time,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import yandex_market_client as ym
import content_updates
from market_content import grouped,flatten,read_rows
PLAN=json.load(open('data/market_content_repair_20261115.json'))
RULES=content_updates.load()
OUT=Path('/tmp/market-content-repair');OUT.mkdir(exist_ok=True)
def save(n,x):
    (OUT/(n+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))
    print(n+': saved',flush=True)
def canonical(values):
    return sorted([json.dumps({k:v for k,v in x.items()if k in {'parameterId','unitId','valueId','value'}},sort_keys=True,ensure_ascii=False)for x in values])
def main():
    biz=ym.get_business_id();ids=list(PLAN)
    rows=read_rows(biz,'offer-mappings',ids);cards=read_rows(biz,'offer-cards',ids)
    save('mappings-before',rows);save('cards-before',cards)
    offers={r['offer']['offerId']:r['offer']for r in rows};byid={r['offerId']:r for r in cards}
    assert set(offers)==set(ids)==set(byid)
    targets={};desc_targets={};payload=[]
    for sku,p in PLAN.items():
        card=byid[sku];offer=offers[sku]
        assert card['mapping']['marketCategoryId']==p['category_id'],('category changed',sku)
        # Keep newer live values, restore absent fields and repair reviewed composition.
        values=grouped(p['parameterValues']);values.update(grouped(card.get('parameterValues',[])))
        planned=grouped(p['parameterValues'])
        for pid in [16382542,7351754]:
            if pid in planned:values[pid]=planned[pid]
        targets[sku]=flatten(values)
        update={'offerId':sku,'marketCategoryId':p['category_id'],'parameterValues':targets[sku]}
        if RULES.get(sku,{}).get('market_description'):
            description=RULES[sku]['market_description'];assert 500<=len(description)<=6000
            assert offer.get('description','')in[p['current_description'],description],('description changed',sku)
            update['description']=description;desc_targets[sku]=description
            params=copy.deepcopy(offer.get('params',[]))
            changed=False
            for entry in params:
                if entry['name']=='Аннотация':entry['value']=description;changed=True
            if changed:update['params']=params
        payload.append({'offer':update})
    save('payload',payload)
    status,res=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':payload})
    save('response',{'http_status':status,'body':res})
    assert status==200 and res.get('status')=='OK',(status,res)
    assert not any(r.get('errors')for r in res.get('results',[])),res
    final={}
    for attempt in range(30):
        after=read_rows(biz,'offer-mappings',ids);ca=read_rows(biz,'offer-cards',ids)
        offers_after={r['offer']['offerId']:r['offer']for r in after};cards_after={r['offerId']:r for r in ca}
        assert set(offers_after)==set(ids)==set(cards_after)
        final={}
        for sku in ids:
            old=offers[sku];new=offers_after[sku]
            for key in ['name','pictures','videos','weightDimensions','basicPrice','vendor','vendorCode','barcodes']:
                assert old.get(key)==new.get(key),('Unrelated field changed',sku,key)
            desired=grouped(targets[sku]);actual=grouped(cards_after[sku].get('parameterValues',[]))
            attrs_ok=all(canonical(actual.get(pid,[]))==canonical(v)for pid,v in desired.items())
            final[sku]={'description_verified':sku not in desc_targets or new.get('description')==desc_targets[sku],
                        'parameters_verified':attrs_ok,'missing_or_different_parameters':[pid for pid,v in desired.items()if canonical(actual.get(pid,[]))!=canonical(v)],
                        'rating_before':PLAN[sku]['baseline_rating'],'rating_after':cards_after[sku].get('contentRating'),
                        'rating_status':cards_after[sku].get('cardStatus')}
        save('mappings-after',after);save('cards-after',ca);save('result',{'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cards':final})
        verified=sum(v['description_verified']and v['parameters_verified']for v in final.values())
        regressions=[k for k,v in final.items()if (v['rating_after']or 0)<v['rating_before']]
        print(json.dumps({'attempt':attempt,'verified':verified,'total':len(ids),'rating_below_baseline':regressions},ensure_ascii=False),flush=True)
        if verified==len(ids)and not regressions:break
        time.sleep(15)
    assert all(v['description_verified']and v['parameters_verified']for v in final.values()),'Some text or characteristics not verified'
    assert not any((v['rating_after']or 0)<v['rating_before']for v in final.values()),'Some ratings still below baseline'
if __name__=='__main__':main()
