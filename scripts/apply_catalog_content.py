"""Apply reviewed descriptions only, with baseline guards and readback checks."""
import copy,json,sys,time,datetime as dt
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import content_updates
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym
PLAN=json.loads(Path('data/content_batch_20261115.json').read_text())
RULES=content_updates.load()
OUT=Path('/tmp/catalog-content-update');OUT.mkdir(exist_ok=True)
def save(name,obj):
    (OUT/(name+'.json')).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str))
    print(name+': saved',flush=True)
def desc(row):return next((a['values'][0].get('value','') for a in row.get('attributes',[]) if a['id']==4191 and a.get('values')),'')
def attrs(ids):
    out=[]
    for i in range(0,len(ids),100):out+=oz._post('/v4/product/info/attributes',{'filter':{'offer_id':ids[i:i+100],'visibility':'ALL'},'limit':1000})['result']
    return out
def ozon():
    ids=[k for k,v in PLAN.items() if 'ozon_description' in v]
    before=attrs(ids);save('ozon-before',before)
    assert {r['offer_id'] for r in before}==set(ids)
    for r in before:assert desc(r) in [PLAN[r['offer_id']]['ozon_description'],RULES[r['offer_id']]['description']],('changed since review',r['offer_id'])
    save('ozon-prices-before',oz.get_prices_and_stocks(ids));save('ozon-stocks-before',oz.get_stocks(ids))
    payload=[{'offer_id':r['offer_id'],'attributes':[{'id':4191,'complex_id':0,'values':[{'value':RULES[r['offer_id']]['description']}]}]} for r in before if desc(r)!=RULES[r['offer_id']]['description']]
    save('ozon-payload',payload)
    if payload:save('ozon-response',oz.update_attributes(payload))
    for _ in range(18):
        after=attrs(ids)
        if all(desc(r)==RULES[r['offer_id']]['description'] for r in after):break
        time.sleep(10)
    save('ozon-after',after);info=oz.get_product_info_list(ids);save('ozon-info-after',info)
    save('ozon-prices-after',oz.get_prices_and_stocks(ids));save('ozon-stocks-after',oz.get_stocks(ids))
    save('ozon-ratings-after',oz._post('/v1/product/rating-by-sku',{'skus':[int(r['sku']) for r in info if r.get('sku')]}))
    for old in before:
        new=next(r for r in after if r['offer_id']==old['offer_id'])
        for key in ['name','height','width','depth','weight','primary_image','images','complex_attributes','type_id','description_category_id']:
            oldv,newv=old.get(key),new.get(key)
            if key=='complex_attributes':
                oldv=sorted(oldv or [],key=lambda a:(a.get('complex_id',0),a.get('id',0)))
                newv=sorted(newv or [],key=lambda a:(a.get('complex_id',0),a.get('id',0)))
            assert oldv==newv,(old['offer_id'],'unrelated field changed',key)
    return {r['offer_id']:desc(r)==RULES[r['offer_id']]['description'] for r in after}
def wildberries():
    ids=[k for k,v in PLAN.items() if 'wb_description' in v]
    before=[r for r in wb.list_cards() if r['vendorCode'] in ids];save('wb-before',before);save('wb-prices-before',wb.get_prices())
    assert {r['vendorCode'] for r in before}==set(ids)
    payload=[]
    for r in before:
        sku=r['vendorCode'];assert r.get('description','') in [PLAN[sku]['wb_description'],RULES[sku]['description']],('changed since review',sku)
        if r.get('description')==RULES[sku]['description']:continue
        q={k:copy.deepcopy(r[k]) for k in ['nmID','vendorCode','brand','title','dimensions','sizes']};q['characteristics']=copy.deepcopy(r.get('characteristics',[]));q['kizMarked']=r.get('kizMarked',False);q['description']=RULES[sku]['description'];payload.append(q)
    save('wb-payload',payload)
    if payload:
        res=wb.update_cards(payload);save('wb-response',res);assert not res.get('error'),res
    for _ in range(18):
        after=[r for r in wb.list_cards() if r['vendorCode'] in ids]
        if {r['vendorCode'] for r in after}==set(ids) and all(r.get('description')==RULES[r['vendorCode']]['description'] for r in after):break
        time.sleep(10)
    save('wb-after',after);save('wb-prices-after',wb.get_prices())
    for old in before:
        new=next((r for r in after if r['vendorCode']==old['vendorCode']),None)
        if new is None:continue
        for k in ['title','brand','subjectID','dimensions','characteristics','sizes','photos','video']:
            oldv,newv=old.get(k),new.get(k)
            if k=='characteristics':
                oldv=sorted(oldv or [],key=lambda a:a['id']);newv=sorted(newv or [],key=lambda a:a['id'])
            assert oldv==newv,(old['vendorCode'],'unrelated field changed',k)
    return {sku:any(r['vendorCode']==sku and r.get('description')==RULES[sku]['description'] for r in after) for sku in ids}
def market():
    ids=[k for k,v in PLAN.items() if 'market_description' in v];biz=ym.get_business_id();path=f'/v2/businesses/{biz}/offer-mappings'
    status,before=ym._request('POST',path,json_body={'offerIds':ids});assert status==200;save('market-before',before)
    rows=before['result']['offerMappings'];assert {r['offer']['offerId'] for r in rows}==set(ids)
    payload=[]
    for r in rows:
        o=r['offer'];sku=o['offerId'];assert o.get('description','') in [PLAN[sku]['market_description'],RULES[sku]['description']],('changed since review',sku)
        new={'offerId':sku,'description':RULES[sku]['description']}
        params=copy.deepcopy(o.get('params',[]));changed=False
        for p in params:
            if p.get('name')=='Аннотация':p['value']=RULES[sku]['description'];changed=True
        if changed:new['params']=params
        payload.append({'offer':new})
    save('market-payload',payload);status,res=ym._request('POST',path+'/update',json_body={'offerMappings':payload});save('market-response',{'http_status':status,'body':res});assert status==200 and res.get('status')=='OK',res
    for _ in range(18):
        status,after=ym._request('POST',path,json_body={'offerIds':ids});assert status==200
        if all(r['offer'].get('description')==RULES[r['offer']['offerId']]['description'] for r in after['result']['offerMappings']):break
        time.sleep(10)
    save('market-after',after)
    for old in rows:
        o=old['offer'];new=next(r['offer'] for r in after['result']['offerMappings'] if r['offer']['offerId']==o['offerId'])
        for k in ['name','pictures','videos','weightDimensions','basicPrice','vendor','vendorCode','barcodes']:
            assert o.get(k)==new.get(k),(o['offerId'],'unrelated field changed',k)
    status,ratings=ym._request('POST',f'/v2/businesses/{biz}/offer-cards',json_body={'offerIds':ids,'withRecommendations':True});save('market-ratings-after',ratings)
    return {r['offer']['offerId']:r['offer'].get('description')==RULES[r['offer']['offerId']]['description'] for r in after['result']['offerMappings']}
def safe(name,fn):
    try:return name,fn()
    except Exception as e:return name,{'error':str(e)[:1500]}
if __name__=='__main__':
    with ThreadPoolExecutor(max_workers=3) as pool:result=dict(pool.map(lambda t:safe(*t),[('ozon',ozon),('wb',wildberries),('market',market)]))
    result['checked_at']=dt.datetime.now(dt.timezone.utc).isoformat();save('result',result);print(json.dumps(result,ensure_ascii=False))
    if any(isinstance(v,dict) and (v.get('error') or any(x is False for x in v.values())) for v in result.values()):raise SystemExit(1)
