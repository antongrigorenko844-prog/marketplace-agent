"""Add visually reviewed existing product images; preserve all other card fields."""
import sys,json,time,copy,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wb_client as wb
from video_sources import wb_video_for
P={'0AM325467J':json.load(open('data/wb_media_additions_20261115.json'))['0AM325467J']};OUT=Path('/tmp/wb-reviewed-media');OUT.mkdir(exist_ok=True)
def save(n,d):(OUT/(n+'.json')).write_text(json.dumps(d,ensure_ascii=False,indent=2))
def read():return {r['vendorCode']:r for r in wb.list_cards()if r['vendorCode']in P}
before=read();prices=wb.get_prices();save('before',before);save('prices-before',prices);assert set(before)==set(P)
for s,extra in P.items():
 c=before[s];assert len(c['photos'])==1,('Photo list changed, review required',s,len(c['photos']))
 video=wb_video_for(s,c.get('video',''))
 assert not c.get('video')or video.endswith('.mp4'),('Preserve video using original MP4',s)
 urls=[p['big']for p in c['photos']]+[x['url']for x in extra]+([video]if video else[])
 save('request-'+s,{'nmID':c['nmID'],'urls':urls});r=wb.update_media(c['nmID'],urls);save('response-'+s,r);assert not r.get('error'),r
for _ in range(24):
 after=read();save('after',after)
 if all(len(after[s].get('photos',[]))==1+len(P[s])and bool(after[s].get('video'))==bool(before[s].get('video'))for s in P):break
 time.sleep(10)
pa=wb.get_prices();save('prices-after',pa);assert sorted(prices,key=lambda r:r['nmID'])==sorted(pa,key=lambda r:r['nmID'])
result={}
for s,b in before.items():
 a=after[s]
 for k in ['title','description','brand','subjectID','dimensions','sizes']:assert b.get(k)==a.get(k),(s,k)
 assert sorted(b['characteristics'],key=lambda x:x['id'])==sorted(a['characteristics'],key=lambda x:x['id'])
 assert bool(b.get('video'))==bool(a.get('video')),(s,'video')
 assert len(a.get('photos',[]))==1+len(P[s]),(s,'photos')
 result[s]={'photos_before':len(b['photos']),'photos_after':len(a['photos']),'video_preserved':True,'non_media_fields_preserved':True}
save('result',{'cards':result,'prices_preserved':True,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat()});print(json.dumps(result,ensure_ascii=False))
