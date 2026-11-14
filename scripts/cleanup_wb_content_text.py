"""Apply only reviewed wording corrections; preserve all other fields."""
import sys,json,copy,time,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wb_client as wb
P=json.load(open('data/wb_text_cleanup_20261115.json'))
OUT=Path('/tmp/wb-text-cleanup');OUT.mkdir(exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2))
def read():return {r['vendorCode']:r for r in wb.list_cards() if r['vendorCode'] in P}
before=read();save('before',before);prices=wb.get_prices();save('prices-before',prices)
assert set(before)==set(P)
expected={};payload=[]
for sku,c in before.items():
 p=P[sku];assert c['nmID']==p['nmID'],sku
 for k in ['title','description']:
  assert c.get(k)==p['before'][k] or c.get(k)==p.get(k),('Concurrent change',sku,k)
 ch=copy.deepcopy(c.get('characteristics',[]))
 if 'composition' in p:
  matches=[x for x in ch if x['id']==378533];assert len(matches)==1,(sku,'composition')
  old=next(x['value'] for x in p['before']['characteristics'] if x['id']==378533)
  assert matches[0]['value'] in [old,[p['composition']]],(sku,'composition changed')
  matches[0]['value']=[p['composition']]
 q={k:copy.deepcopy(c.get(k)) for k in ['nmID','vendorCode','brand','dimensions','sizes']}
 q.update(title=p.get('title',c['title']),description=p.get('description',c['description']),characteristics=ch,kizMarked=c.get('kizMarked',False))
 expected[sku]=q;payload.append(q)
save('payload',payload)
r=wb.update_cards(payload);save('response',r);assert not r.get('error'),r
def matches(a,e):return a['title']==e['title'] and a['description']==e['description'] and {x['id']:x['value'] for x in a['characteristics']}=={x['id']:x['value'] for x in e['characteristics']}
for _ in range(24):
 after=read()
 if all(matches(after[s],expected[s]) for s in P):break
 time.sleep(5)
save('after',after);pa=wb.get_prices();save('prices-after',pa)
assert sorted(prices,key=lambda r:r['nmID'])==sorted(pa,key=lambda r:r['nmID']),'Price snapshot changed'
result={}
for s,b in before.items():
 a=after[s];assert matches(a,expected[s]),(s,'Text readback failed')
 for k in ['brand','subjectID','dimensions','sizes','photos','video','kizMarked']:assert b.get(k)==a.get(k),(s,k)
 result[s]={'verified':True,'title':a['title'],'updated_fields':[k for k in ['title','description','composition'] if k in P[s]]}
save('result',{'cards':result,'prices_preserved':True,'stock_writes':0,'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat()})
print(json.dumps(result,ensure_ascii=False))
