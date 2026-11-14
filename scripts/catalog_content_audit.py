"""Full content baseline. Read-only; no customer data or platform mutations."""
import datetime as dt
import json
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym
import avito_client as av
OUT=Path('/tmp/catalog-content-audit'); OUT.mkdir(exist_ok=True)
def save(name,value):
    (OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str))
    print(name+': saved',flush=True)
def ozon():
    attrs=oz.get_product_names(); save('ozon-attributes',attrs)
    ids=[r['offer_id'] for r in attrs]
    info=oz.get_product_info_list(ids);save('ozon-info',info)
    save('ozon-prices',oz.get_prices_and_stocks(ids));save('ozon-stocks',oz.get_stocks(ids))
    skus=sorted({int(r['sku']) for r in info if r.get('sku')})
    rating=[]
    for i in range(0,len(skus),100):rating.append(oz._post('/v1/product/rating-by-sku',{'skus':skus[i:i+100]}))
    save('ozon-ratings',rating)
    for c,t in sorted({(r['description_category_id'],r['type_id']) for r in attrs}):
        save(f'ozon-defs-{c}-{t}',oz.get_category_attributes(c,t))
    return len(attrs)
def wildberries():
    rows=wb.list_cards();save('wb-cards',rows);save('wb-prices',wb.get_prices())
    save('wb-upload-errors',wb.get_upload_errors())
    for sid in sorted({r['subjectID'] for r in rows}):
        save(f'wb-defs-{sid}',wb.get_subject_characteristics(sid));time.sleep(.7)
    return len(rows)
def market():
    biz=ym.get_business_id()
    counts={}
    for kind,key,body in [('offer-mappings','offerMappings',{'archived':False}),('offer-cards','offerCards',{'withRecommendations':True})]:
        rows=[];token=None;seen=set()
        for _ in range(100):
            params={'limit':100}
            if token:params['pageToken']=token
            status,data=ym._request('POST',f'/v2/businesses/{biz}/{kind}',params=params,json_body=body)
            assert status==200,(status,data)
            result=data.get('result',{}); rows+=result.get(key,[])
            token=result.get('paging',{}).get('nextPageToken')
            if not token:break
            assert token not in seen,'Repeated pagination token'
            seen.add(token)
        save('market-'+kind,rows);counts[kind]=len(rows)
    return counts
def avito():
    rows=av.list_all_items();save('avito-items',rows)
    save('avito-autoload',av.get_autoload_report_items())
    return len(rows)
def tilda():
    rows=[];sl=None;seen=set()
    for _ in range(20):
        params={'storepartuid':'419144347374','recid':'1928468643','size':100,'c':int(time.time()*1000)}
        if sl:params['slice']=sl
        r=requests.get('https://store.tildaapi.com/api/getproductslist/',params=params,timeout=60);r.raise_for_status();b=r.json()
        page=b.get('products',[])
        fresh=[p for p in page if p['uid'] not in seen]
        rows+=fresh;seen.update(p['uid'] for p in fresh)
        sl=b.get('nextslice')
        if not sl or not fresh:break
    save('tilda-products',rows);save('tilda-paging',{k:b.get(k) for k in ['total','slice','nextslice']})
    return len(rows)
def inventory():
    paths=[]
    for folder in ['photos','photos_avito','data/ozon_rich']:
        p=Path(folder)
        if p.exists(): paths += [{'path':str(f),'size':f.stat().st_size} for f in p.rglob('*') if f.is_file()]
    save('repo-media',paths)
    for name in ['market_video_cache','ozon_overrides','removed_products','content_updates']:
        p=Path('data')/(name+'.json')
        if p.exists():save('repo-'+name,json.loads(p.read_text()))
    return len(paths)
def safe(name,fn):
    try:return name,{'ok':True,'count':fn()}
    except Exception as e:return name,{'ok':False,'error':str(e)[:1200]}
if __name__=='__main__':
    tasks=[('ozon',ozon),('wb',wildberries),('market',market),('avito',avito),('tilda',tilda),('inventory',inventory)]
    with ThreadPoolExecutor(max_workers=6) as pool:results=dict(pool.map(lambda t:safe(*t),tasks))
    results['checked_at']=dt.datetime.now(dt.timezone.utc).isoformat()
    save('result',results);print(json.dumps(results,ensure_ascii=False))
    if any(isinstance(v,dict) and not v.get('ok') for v in results.values()):raise SystemExit(1)
