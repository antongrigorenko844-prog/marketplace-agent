"""Repair unavailable existing product videos; preserve every non-video field."""
import sys,json,time,datetime,requests
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import yandex_market_client as ym
from market_content import read_rows,preserve_typed_parameters,grouped
OUT=Path('/tmp/market-video-repair');OUT.mkdir(exist_ok=True)
def save(n,d):(OUT/(n+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2))
P=json.load(open('data/market_video_repair_20261115.json'));biz=ym.get_business_id();ids=list(P)
before=read_rows(biz,'offer-mappings',ids);cards=read_rows(biz,'offer-cards',ids);save('before',before);save('cards-before',cards)
for u in {u for p in P.values()for u in p['videos']}:
 r=requests.get(u,stream=True,timeout=40);r.raise_for_status();assert b'ftyp'in next(r.iter_content(64));r.close()
payload=[]
for r in before:
 o=r['offer'];p=P[o['offerId']];assert o.get('videos')in[p['before'],p['videos']]
 payload.append({'offer':{'offerId':o['offerId'],'videos':p['videos']}})
preserve_typed_parameters(biz,payload);save('payload',payload)
code,body=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':payload});save('response',{'status':code,'body':body});assert code==200 and body.get('status')=='OK'
for _ in range(30):
 time.sleep(10);after=read_rows(biz,'offer-mappings',ids);am={r['offer']['offerId']:r['offer']for r in after};save('after',after)
 states={s:[v.get('uploadState')for v in am[s].get('mediaFiles',{}).get('videos',[])]for s in ids};print(states,flush=True)
 if all(v and all(x=='UPLOADED'for x in v)for v in states.values()):break
ca=read_rows(biz,'offer-cards',ids);save('cards-after',ca);cm={r['offerId']:r for r in ca}
for r in before:
 o=r['offer'];s=o['offerId'];assert am[s]['videos']==P[s]['videos']
 for k,v in o.items():
  if k not in ['videos','mediaFiles','cardStatus']:assert am[s].get(k)==v,('unrelated field changed',s,k)
for c in cards:assert grouped(c.get('parameterValues',[]))==grouped(cm[c['offerId']].get('parameterValues',[]))
res={'states':states,'non_video_fields_preserved':True,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()};save('result',res);print(json.dumps(res,ensure_ascii=False))
