"""Only gallery mutations, with content and price guards and read-back evidence."""
import sys,json,time,copy,datetime,requests,os
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wb_client as wb
import yandex_market_client as ym
from market_content import read_rows,preserve_typed_parameters,grouped
OUT=Path('/tmp/media-round3');OUT.mkdir(exist_ok=True);P=json.load(open('data/media_round3_20261115.json'))
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str))
def main():
 b=ym.get_business_id();ids=list(P['market']);before=read_rows(b,'offer-mappings',ids);cards=read_rows(b,'offer-cards',ids);save('market-before',before);save('market-cards-before',cards);payload=[]
 for r in before:
  sku=r['offer']['offerId'];p=P['market'][sku];assert r['offer']['pictures']in[p['before'],p['pictures']],('live gallery changed',sku)
  payload.append({'offer':{'offerId':sku,'pictures':p['pictures']}})
 preserve_typed_parameters(b,payload);save('market-payload',payload)
 st,res=ym._request('POST',f'/v2/businesses/{b}/offer-mappings/update',json_body={'offerMappings':payload});save('market-response',{'status':st,'body':res});assert st==200 and res.get('status')=='OK'
 for _ in range(20):
  after=read_rows(b,'offer-mappings',ids);am={r['offer']['offerId']:r['offer']for r in after}
  if all(am[sku]['pictures']==p['pictures']for sku,p in P['market'].items()):break
  time.sleep(10)
 save('market-after',after);ca=read_rows(b,'offer-cards',ids);save('market-cards-after',ca);cm={r['offerId']:r for r in ca}
 for r in before:
  sku=r['offer']['offerId'];assert am[sku]['pictures']==P['market'][sku]['pictures']
  for k,v in r['offer'].items():
   if k not in ['pictures','cardStatus']:assert am[sku].get(k)==v,('unrelated offer field',sku,k)
 for c in cards:assert grouped(c.get('parameterValues',[]))==grouped(cm[c['offerId']].get('parameterValues',[]))
 wb_before=wb.list_cards();prices=wb.get_prices();save('wb-before',wb_before);save('wb-prices-before',prices);wm={r['vendorCode']:r for r in wb_before}
 for sku,p in P['wb'].items():
  r=wm[sku];assert r['nmID']==p['nmID'];assert r['photos']==p['before'],('live WB media changed',sku)
  response=wb.update_media(r['nmID'],p['images']);save('wb-photo-response',response);assert not response.get('error')
  time.sleep(3)
  with open('photos/0AM927769D/0AM927769D_video.mp4','rb')as video:
   vr=requests.post('https://content-api.wildberries.ru/content/v3/media/file',headers={'Authorization':os.environ['WB_API_TOKEN'],'X-Nm-Id':str(r['nmID']),'X-Photo-Number':'1'},files={'uploadfile':('board-d.mp4',video,'video/mp4')},timeout=180)
  save('wb-video-response',{'status':vr.status_code,'body':vr.json()});assert vr.ok and not vr.json().get('error')
 for _ in range(24):
  wa=wb.list_cards();wm2={r['vendorCode']:r for r in wa};r=wm2['0AM927769D'];old=wm['0AM927769D']
  if r.get('video') and r['photos']!=old['photos'] and len(r['photos'])==3:break
  time.sleep(10)
 save('wb-after',wa);pa=wb.get_prices();save('wb-prices-after',pa)
 for k in ['title','description','characteristics','brand','dimensions','sizes','subjectID']:assert old.get(k)==r.get(k),('WB unrelated changed',k)
 assert sorted(prices,key=lambda r:r['nmID'])==sorted(pa,key=lambda r:r['nmID']),'WB prices changed'
 result={'market':{sku:{'images_before':len(p['before']),'images_after':len(am[sku]['pictures']),'verified':True}for sku,p in P['market'].items()},'wb':{'0AM927769D':{'photos':len(r['photos']),'video':bool(r.get('video')),'changed':r['photos']!=old['photos']}},'at':datetime.datetime.now(datetime.timezone.utc).isoformat()};save('result',result);print(json.dumps(result,ensure_ascii=False));assert result['wb']['0AM927769D']['changed'] and r.get('video')
if __name__=='__main__':main()
