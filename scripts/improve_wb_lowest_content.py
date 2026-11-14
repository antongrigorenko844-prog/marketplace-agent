"""Improve two identified incomplete listings using only existing product facts."""
import sys,json,copy,time,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wb_client as wb
from content_updates import load
P=json.load(open('data/wb_lowest_content_20261115.json'));R=load()
OUT=Path('/tmp/wb-lowest-content');OUT.mkdir(exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2))
def read():return {r['vendorCode']:r for r in wb.list_cards()if r['vendorCode']in P}
before=read();save('before',before);prices=wb.get_prices();save('prices-before',prices)
assert set(before)==set(P)
payload=[];expected={};skipped={}
for sku,c in before.items():
 p=P[sku];assert c['nmID']==p['nmID'] and c['subjectID']==p['subject_id'],('Category not saved',sku,c['subjectID'])
 defs=wb.get_subject_characteristics(c['subjectID']);save('defs-'+str(c['subjectID']),defs);by={v['charcID']:v for v in defs}
 ch=copy.deepcopy(c.get('characteristics',[]));mapped={v['id']:v for v in ch};skipped[sku]=[]
 for v in p['fields']:
  if v['id'] not in by:skipped[sku].append(v['id']);continue
  if v['id'] in mapped:
   assert mapped[v['id']]['value']==v['value'],('Existing field differs; review',sku,v['id'])
  else:ch.append(v)
 assert len(ch)>=len(c.get('characteristics',[]))+2,('Not enough supported new facts',sku)
 q={k:copy.deepcopy(c.get(k))for k in ['nmID','vendorCode','brand','dimensions','sizes']}
 q.update(title=R[sku]['wb_title'],description=R[sku]['description'],characteristics=ch,kizMarked=c.get('kizMarked',False))
 expected[sku]=q;payload.append(q)
save('payload',payload);save('skipped',skipped)
res=wb.update_cards(payload);save('response',res);assert not res.get('error'),res
for _ in range(24):
 after=read()
 if all(after[s]['title']==expected[s]['title']and after[s]['description']==expected[s]['description'] and all({v['id']:v['value']for v in after[s]['characteristics']}.get(x['id'])==x['value']for x in expected[s]['characteristics'])for s in P):break
 time.sleep(5)
save('after',after);pa=wb.get_prices();save('prices-after',pa)
assert sorted(prices,key=lambda r:r['nmID'])==sorted(pa,key=lambda r:r['nmID']),'Prices changed during read-back'
result={}
for s,b in before.items():
 a=after[s];e=expected[s]
 for k in ['brand','subjectID','dimensions','sizes','photos','video']:assert b.get(k)==a.get(k),(s,k)
 assert a['title']==e['title']and a['description']==e['description'],(s,'text')
 assert all({v['id']:v['value']for v in a['characteristics']}.get(x['id'])==x['value']for x in e['characteristics']),(s,'characteristics')
 result[s]={'title':a['title'],'subjectID':a['subjectID'],'added_characteristics':len(a['characteristics'])-len(b['characteristics']),'verified':True}
save('result',{'cards':result,'prices_preserved':True,'stock_writes':0,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat()});print(json.dumps(result,ensure_ascii=False))
