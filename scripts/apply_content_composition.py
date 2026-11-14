"""Apply reviewed composition fields only; preserve commercial and media fields."""
import sys,json,copy,time,datetime
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ozon_client as oz,wb_client as wb,yandex_market_client as ym
P=json.load(open('data/content_composition_batch_20261115.json'))
R=json.load(open('data/content_updates.json'))
OUT=Path('/tmp/content-composition');OUT.mkdir(exist_ok=True)
def save(n,x):
    (OUT/(n+'.json')).write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str))
def oval(r):return next((x['values'][0]['value']for x in r.get('attributes',[])if x['id']==4384 and x.get('values')),None)
def wval(r):return next((x['value']for x in r.get('characteristics',[])if x['id']==378533),None)
def normalized_composition(value):
    if value is None:return None
    return [' '.join(part.replace('ё','е').split()) for item in value for part in item.split(';') if part.strip()]
def wb_composition_matches(row, target):
    return normalized_composition(wval(row)) == normalized_composition(target)
def canonical_attributes(values):
    result=copy.deepcopy(values)
    for v in result:
        if isinstance(v.get('values'),list):v['values']=sorted(v['values'],key=lambda x:json.dumps(x,sort_keys=True))
    return sorted(result,key=lambda x:x['id'])
def wb_cards():
    for attempt in range(4):
        try:return wb.list_cards()
        except Exception as e:
            if attempt==3 or not any(s in str(e)for s in ['503','502','504','429']):raise
            time.sleep(10*(attempt+1))
def oa(ids):
    out=[]
    for i in range(0,len(ids),100):out+=oz._post('/v4/product/info/attributes',{'filter':{'offer_id':ids[i:i+100],'visibility':'ALL'},'limit':1000})['result']
    return out
def ozon():
    ids=[k for k,v in P.items()if v['composition']!=v['ozon_before']]
    before=oa(ids);save('ozon-before',before);assert {r['offer_id']for r in before}==set(ids)
    prices=oz.get_prices_and_stocks(ids);save('ozon-prices-before',prices);stocks=oz.get_stocks(ids);save('ozon-stocks-before',stocks)
    payload=[]
    for r in before:
        sku=r['offer_id'];assert oval(r)in[P[sku]['ozon_before'],P[sku]['composition']],('conflict',sku)
        if oval(r)!=P[sku]['composition']:payload.append({'offer_id':sku,'attributes':[{'id':4384,'complex_id':0,'values':[{'value':P[sku]['composition']}]}]})
    save('ozon-payload',payload)
    if payload:save('ozon-response',oz.update_attributes(payload))
    for _ in range(24):
        after=oa(ids)
        if {r['offer_id']for r in after}==set(ids) and all(oval(r)==P[r['offer_id']]['composition']for r in after):break
        time.sleep(10)
    save('ozon-after',after);save('ozon-prices-after',oz.get_prices_and_stocks(ids));save('ozon-stocks-after',oz.get_stocks(ids))
    for old in before:
        new=next(r for r in after if r['offer_id']==old['offer_id'])
        for key in ['name','height','width','depth','weight','primary_image','images','type_id','description_category_id']:
            assert old.get(key)==new.get(key),(old['offer_id'],key)
        assert canonical_attributes([x for x in old['attributes']if x['id']!=4384])==canonical_attributes([x for x in new['attributes']if x['id']!=4384]),(old['offer_id'],'other attributes')
    info=oz.get_product_info_list(ids);save('ozon-info-after',info)
    save('ozon-ratings-after',oz._post('/v1/product/rating-by-sku',{'skus':[int(r['sku'])for r in info if r.get('sku')]}))
    return {sku:any(r['offer_id']==sku and oval(r)==P[sku]['composition']for r in after)for sku in ids}
def wb_target(sku):return [R.get(sku,{}).get('wb_composition',P[sku]['composition'])]
def wildberries():
    ids=[k for k,v in P.items()if 'wb_before'in v];before=[r for r in wb_cards()if r['vendorCode']in ids];save('wb-before',before)
    assert {r['vendorCode']for r in before}==set(ids)
    save('wb-prices-before',wb.get_prices());payload=[]
    for r in before:
        sku=r['vendorCode'];target=wb_target(sku);assert wb_composition_matches(r,P[sku]['wb_before'])or wb_composition_matches(r,target),('conflict',sku)
        if wb_composition_matches(r,target):continue
        q={k:copy.deepcopy(r.get(k))for k in ['nmID','vendorCode','brand','title','description','dimensions','sizes']}
        q['kizMarked']=r.get('kizMarked',False);q['characteristics']=[copy.deepcopy(x)for x in r.get('characteristics',[])if x['id']!=378533]+[{'id':378533,'value':target}];payload.append(q)
    save('wb-payload',payload)
    if payload:
        res=wb.update_cards(payload);save('wb-response',res);assert not res.get('error'),res
    for _ in range(24):
        after=[r for r in wb_cards()if r['vendorCode']in ids]
        if {r['vendorCode']for r in after}==set(ids)and all(wb_composition_matches(r,wb_target(r['vendorCode']))for r in after):break
        time.sleep(10)
    save('wb-after',after);save('wb-prices-after',wb.get_prices());save('wb-upload-errors',wb.get_upload_errors())
    for old in before:
        new=next((r for r in after if r['vendorCode']==old['vendorCode']),None)
        if new is None:continue
        for k in ['title','description','brand','subjectID','dimensions','sizes','photos','video']:
            assert old.get(k)==new.get(k),(old['vendorCode'],k)
        assert sorted([x for x in old.get('characteristics',[])if x['id']!=378533],key=lambda x:x['id'])==sorted([x for x in new.get('characteristics',[])if x['id']!=378533],key=lambda x:x['id']),(old['vendorCode'],'other characteristics')
    return {sku:any(r['vendorCode']==sku and wb_composition_matches(r,wb_target(sku))for r in after)for sku in ids}
def market():
    ids=[k for k,v in P.items()if 'market_before'in v];biz=ym.get_business_id();path=f'/v2/businesses/{biz}/offer-mappings'
    status,before=ym._request('POST',path,json_body={'offerIds':ids});assert status==200;save('market-before',before)
    rows=before['result']['offerMappings'];assert {r['offer']['offerId']for r in rows}==set(ids)
    payload=[]
    for r in rows:
        o=r['offer'];sku=o['offerId'];current=[p for p in o.get('params',[])if p['name']=='Комплектация'];target=[{'name':'Комплектация','value':P[sku]['composition']}]
        assert current in[P[sku]['market_before'],target],('conflict',sku)
        params=[copy.deepcopy(p)for p in o.get('params',[])if p['name']!='Комплектация']+target
        for p in params:
            if p['name']=='Аннотация'and R[sku].get('description'):p['value']=R[sku].get('market_description',o.get('description',p['value']))
        if params!=o.get('params',[]):payload.append({'offer':{'offerId':sku,'params':params}})
    from market_content import preserve_typed_parameters
    if payload:preserve_typed_parameters(biz,payload)
    save('market-payload',payload)
    if payload:
        status,response=ym._request('POST',path+'/update',json_body={'offerMappings':payload});save('market-response',response);assert status==200 and response.get('status')=='OK',response
    for _ in range(24):
        status,after=ym._request('POST',path,json_body={'offerIds':ids});assert status==200
        rows_after=after['result']['offerMappings']
        if {r['offer']['offerId']for r in rows_after}==set(ids)and all(any(p['name']=='Комплектация'and p['value']==P[r['offer']['offerId']]['composition']for p in r['offer'].get('params',[]))for r in rows_after):break
        time.sleep(10)
    save('market-after',after)
    for old in rows:
        o=old['offer'];new=next(r['offer']for r in rows_after if r['offer']['offerId']==o['offerId'])
        for k in ['name','description','pictures','videos','weightDimensions','basicPrice','vendor','vendorCode','barcodes']:
            assert o.get(k)==new.get(k),(o['offerId'],k)
    return {sku:any(r['offer']['offerId']==sku and any(p['name']=='Комплектация'and p['value']==P[sku]['composition']for p in r['offer'].get('params',[]))for r in rows_after)for sku in ids}
def safe(t):
    n,fn=t
    try:return n,fn()
    except Exception as e:return n,{'error':str(e)[:2000]}
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=3)as pool:result=dict(pool.map(safe,[('ozon',ozon),('wb',wildberries),('market',market)]))
    result['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save('result',result)
    print(json.dumps(result,ensure_ascii=False))
    if any(isinstance(v,dict)and(v.get('error')or any(x is False for x in v.values()))for v in result.values()):raise SystemExit(1)
