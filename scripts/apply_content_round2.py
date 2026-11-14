"""Content-only changes with live state guards. Never writes prices or stock."""
import sys,json,copy,time,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wb_client as wb
import yandex_market_client as ym
from market_content import read_rows,preserve_typed_parameters,grouped
PLAN=json.load(open('data/content_round2_20261115.json'));OUT=Path('/tmp/content-round2');OUT.mkdir(exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str))
def retry(fn):
 for i in range(5):
  try:return fn()
  except Exception:
   if i==4:raise
   time.sleep(10)
def market():
 p=PLAN['market'];biz=ym.get_business_id();ids=list(p);before=read_rows(biz,'offer-mappings',ids);cb=read_rows(biz,'offer-cards',ids)
 save('market-before',before);save('market-cards-before',cb);payload=[]
 for row in before:
  o=row['offer'];sku=o['offerId'];target=p[sku];q={'offerId':sku,'commodityCodes':target['commodityCodes']}
  for k in ['name','description']:
   if k in target:
    assert o.get(k)in[target['before'].get(k),target[k]],('live field changed',sku,k)
    q[k]=target[k]
  # Preserve legacy annotation consistently but also explicit typed parameters.
  if 'description'in q:
   params=copy.deepcopy(o.get('params',[]))
   for x in params:
    if x.get('name')=='Аннотация':x['value']=q['description']
   if params:q['params']=params
  payload.append({'offer':q})
 preserve_typed_parameters(biz,payload);save('market-payload',payload)
 status,res=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':payload})
 save('market-response',{'http_status':status,'body':res});assert status==200 and res.get('status')=='OK',(status,res)
 assert not any(x.get('errors')for x in res.get('results',[])),res
 result={}
 for _ in range(18):
  after=read_rows(biz,'offer-mappings',ids);ca=read_rows(biz,'offer-cards',ids);am={r['offer']['offerId']:r['offer']for r in after};cm={r['offerId']:r for r in ca}
  result={}
  for old in before:
   o=old['offer'];sku=o['offerId'];n=am[sku];t=p[sku]
   for k in ['pictures','videos','weightDimensions','basicPrice','purchasePrice','vendor','vendorCode','barcodes','boxCount','condition']:
    assert o.get(k)==n.get(k),('unrelated field changed',sku,k)
   result[sku]={'fields_verified':all(n.get(k)==t[k]for k in ['name','description','commodityCodes']if k in t),'rating_before':t['baseline_rating'],'rating_after':cm[sku].get('contentRating'),'rating_status':cm[sku].get('contentRatingStatus'),'card_status':cm[sku].get('cardStatus')}
  if all(x['fields_verified']for x in result.values()):break
  time.sleep(10)
 save('market-after',after);save('market-cards-after',ca);save('market-result',result)
 for old in cb:
  now=cm[old['offerId']];assert grouped(old.get('parameterValues',[]))==grouped(now.get('parameterValues',[])),('typed characteristics changed',old['offerId'])
 assert all(x['fields_verified']for x in result.values()),result
 return result
def wildberries():
 p=PLAN['wb'];before=[r for r in retry(wb.list_cards)if r['vendorCode']in p];save('wb-before',before);prices=retry(wb.get_prices);save('wb-prices-before',prices)
 assert {r['vendorCode']for r in before}==set(p)
 payload=[];targets={}
 for r in before:
  sku=r['vendorCode'];t=p[sku];assert r['nmID']==t['nmID'];assert r.get('description','')in[t['description_before'],t['description']],('live description changed',sku)
  ch=copy.deepcopy(r.get('characteristics',[]));by={x['id']:x for x in ch}
  for x in t['add_characteristics']:
   if x['id']in by:assert by[x['id']]['value']==x['value'],('new characteristic already changed',sku,x['id'])
   else:ch.append(x)
  targets[sku]={x['id']:x['value']for x in ch}
  q={k:copy.deepcopy(r.get(k))for k in ['nmID','vendorCode','brand','title','dimensions','sizes']};q.update(description=t['description'],characteristics=ch,kizMarked=r.get('kizMarked',False));payload.append(q)
 save('wb-payload',payload)
 for i in range(0,len(payload),100):
  r=wb.update_cards(payload[i:i+100]);save('wb-response-'+str(i),r);assert not r.get('error'),r;time.sleep(1)
 result={}
 for _ in range(24):
  after=[r for r in retry(wb.list_cards)if r['vendorCode']in p];am={r['vendorCode']:r for r in after};result={}
  for old in before:
   sku=old['vendorCode'];n=am.get(sku,{})
   if n:
    for k in ['title','brand','subjectID','dimensions','sizes','photos','video']:
     assert old.get(k)==n.get(k),('unrelated field changed',sku,k)
   ch={x['id']:x['value']for x in n.get('characteristics',[])}
   result[sku]={'description_verified':n.get('description')==p[sku]['description'],'characteristics_verified':all(ch.get(k)==v for k,v in targets[sku].items()),'added_fields':len(p[sku]['add_characteristics'])}
  if all(r['description_verified']and r['characteristics_verified']for r in result.values()):break
  time.sleep(10)
 save('wb-after',after);pa=retry(wb.get_prices);save('wb-prices-after',pa);save('wb-errors',wb.get_upload_errors());save('wb-result',result)
 assert sorted(prices,key=lambda r:r['nmID'])==sorted(pa,key=lambda r:r['nmID']),'Price snapshot changed'
 assert all(r['description_verified']and r['characteristics_verified']for r in result.values()),'Some WB fields not verified'
 return result
if __name__=='__main__':
 result={}
 for name,fn in [('market',market),('wb',wildberries)]:
  try:r=fn();result[name]={'ok':True,'count':len(r)}
  except Exception as e:result[name]={'ok':False,'error':str(e)[:1800]}
  save('result',result);print(name,result[name],flush=True)
 result['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save('result',result)
 if any(isinstance(v,dict)and not v.get('ok')for v in result.values()):raise SystemExit(1)
