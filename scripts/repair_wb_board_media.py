"""Repair only board D gallery using an existing, verified product video CDN."""
import sys,json,time,datetime,hashlib,requests
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wb_client as wb
OUT=Path('/tmp/media-round3');OUT.mkdir(exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2))
SKU='0AM927769D';ID=670860344
VIDEO='https://v-1.ozone.ru/vod/video-73/01M60SXZSN6C0R9Z6AMCJW40BN/asset_3_h264.mp4'
all_before=wb.list_cards();cards={r['vendorCode']:r for r in all_before};before=cards[SKU];src=cards['0AM927769D-plata'];assert before['nmID']==ID and 'плата' in before['title'].lower()
images=[p['big']for p in src['photos']];assert len(images)==3
save('wb-before',all_before);pb=wb.get_prices();save('wb-prices-before',pb)
vr=requests.get(VIDEO,stream=True,timeout=30);vr.raise_for_status();assert b'ftyp'in next(vr.iter_content(64));vr.close()
r=wb.update_media(ID,images+[VIDEO]);save('wb-media-response',r);assert not r.get('error')
for _ in range(24):
 time.sleep(10);wa=wb.list_cards();after=next(r for r in wa if r['vendorCode']==SKU)
 if after.get('video') and len(after.get('photos',[]))==3:break
save('wb-after',wa);pa=wb.get_prices();save('wb-prices-after',pa)
for key in ['title','description','characteristics','brand','dimensions','sizes','subjectID']:
 assert before.get(key)==after.get(key),('unrelated field changed',key)
checks=[]
for a,b in zip(images,[p['big']for p in after['photos']]):
 x=requests.get(a,timeout=30);y=requests.get(b+'?v='+str(int(time.time())),timeout=30);x.raise_for_status();y.raise_for_status()
 checks.append({'source':a,'destination':b,'identical_bytes':x.content==y.content,'source_sha256':hashlib.sha256(x.content).hexdigest(),'destination_sha256':hashlib.sha256(y.content).hexdigest()})
res={'sku':SKU,'nmID':ID,'video':after.get('video'),'photos':len(after['photos']),'photo_checks':checks,'text_attributes_preserved':True,'price_snapshot_unchanged':sorted(pb,key=lambda r:r['nmID'])==sorted(pa,key=lambda r:r['nmID']),'at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
save('result',res);print(json.dumps(res,ensure_ascii=False));assert res['video'] and res['photos']==3
