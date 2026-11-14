"""Copy two existing, reviewed Market kit videos to the matching Ozon/WB cards."""
import sys,json,time,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ozon_client as oz
import wb_client as wb
OUT=Path('/tmp/kit-videos');OUT.mkdir(exist_ok=True)
P={'0am325025':'https://v-1.ozone.ru/vod/video-68/01JVWWQC04GNGFDWSX7GBA3R37/asset_2_h264.mp4','0am325025m':'https://v-1.ozone.ru/vod/video-67/01JWE2YHZXN25D0WEFQ5CJM8MN/asset_2_h264.mp4'}
def save(n,d):(OUT/(n+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2))
def videos(c):return [v['value']for a in c.get('complex_attributes',[])if a.get('id')==21841 for v in a.get('values',[])]
ob=oz.get_product_names(list(P));pb=oz.get_prices_and_stocks(list(P));sb=oz.get_stocks(list(P));save('ozon-before',ob);save('ozon-prices-before',pb);save('ozon-stocks-before',sb)
batch=[]
for c in ob:
 s=c['offer_id'];assert not videos(c)or videos(c)==[P[s]],('existing video changed',s)
 if not videos(c):batch.append({'offer_id':s,'attributes':[{'id':21837,'complex_id':100001,'values':[{'dictionary_value_id':0,'value':c['name'][:100]}]},{'id':21841,'complex_id':100001,'values':[{'dictionary_value_id':0,'value':P[s]}]}]})
if batch:
 r=oz.update_attributes(batch);save('ozon-response',r);task=r.get('task_id')or r.get('result',{}).get('task_id');assert task
 for _ in range(24):
  st=oz.get_import_status(task);save('ozon-task',st);items=st.get('result',{}).get('items',[])
  if items and all(r.get('status')in['imported','failed']for r in items):assert all(r['status']=='imported'for r in items),st;break
  time.sleep(10)
for _ in range(24):
 oa=oz.get_product_names(list(P))
 if all(videos(c)for c in oa):break
 time.sleep(10)
save('ozon-after',oa);pa=oz.get_prices_and_stocks(list(P));sa=oz.get_stocks(list(P));save('ozon-prices-after',pa);save('ozon-stocks-after',sa);om={r['offer_id']:r for r in oa}
for b in ob:
 a=om[b['offer_id']];assert videos(a)
 for k in ['name','images','primary_image','depth','width','height','weight','type_id','description_category_id']:assert b.get(k)==a.get(k),(b['offer_id'],k)
for b in ob:
 a=om[b['offer_id']];assert {x['id']:x for x in b['attributes']}=={x['id']:x for x in a['attributes']},(b['offer_id'],'attributes')
wb_before=wb.list_cards();wm={r['vendorCode']:r for r in wb_before};wpb=wb.get_prices();save('wb-before',wb_before);save('wb-prices-before',wpb)
for s,url in P.items():
 c=wm[s]
 if not c.get('video'):
  r=wb.update_media(c['nmID'],[r['big']for r in c['photos']]+[url]);save('wb-response-'+s,r);assert not r.get('error')
for _ in range(24):
 time.sleep(10);wa=wb.list_cards();am={r['vendorCode']:r for r in wa}
 if all(am[s].get('video')for s in P):break
save('wb-after',wa);wpa=wb.get_prices();save('wb-prices-after',wpa)
for s in P:
 assert am[s].get('video'),('WB video pending',s)
 for k in ['title','description','photos','characteristics','brand','dimensions','sizes','subjectID']:assert wm[s].get(k)==am[s].get(k),(s,k)
ppb={r['offer_id']:r['price']for r in pb};ppa={r['offer_id']:r['price']for r in pa}
res={'ozon':{s:videos(om[s])for s in P},'wb':{s:am[s].get('video')for s in P},'prices_unchanged':ppb==ppa and sorted(wpb,key=lambda r:r['nmID'])==sorted(wpa,key=lambda r:r['nmID']),'ozon_stocks_unchanged':sb==sa,'non_media_fields_preserved':True,'at':datetime.datetime.now(datetime.timezone.utc).isoformat()};save('result',res);print(json.dumps(res,ensure_ascii=False))
