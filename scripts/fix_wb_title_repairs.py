"""Restore omitted kit components and incomplete nouns in WB titles."""
import sys,json,copy,time,datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import wb_client as wb
from content_updates import load
P=json.load(open('data/wb_title_repairs_20261115.json'));R=load();OUT=Path('/tmp/wb-title-repairs');OUT.mkdir(exist_ok=True)
def save(n,v):(OUT/(n+'.json')).write_text(json.dumps(v,ensure_ascii=False,indent=2))
def cards():
 for n in range(4):
  try:return [r for r in wb.list_cards()if r['vendorCode']in P]
  except Exception:
   if n==3:raise
   time.sleep(10)
def main():
 before=cards();save('before',before);prices=wb.get_prices();save('prices-before',prices)
 assert {r['vendorCode']for r in before}==set(P)
 payload=[]
 for r in before:
  sku=r['vendorCode'];target=R[sku]['wb_title'];assert len(target)<=60
  assert r['title']in[P[sku]['before'],target],('title changed',sku)
  if r['title']==target:continue
  q={k:copy.deepcopy(r.get(k))for k in ['nmID','vendorCode','brand','description','dimensions','sizes','characteristics']}
  q['kizMarked']=r.get('kizMarked',False);q['title']=target;payload.append(q)
 save('payload',payload)
 if payload:
  res=wb.update_cards(payload);save('response',res);assert not res.get('error'),res
 for _ in range(24):
  after=cards()
  if {r['vendorCode']for r in after}==set(P)and all(r['title']==R[r['vendorCode']]['wb_title']for r in after):break
  time.sleep(10)
 save('after',after);pa=wb.get_prices();save('prices-after',pa);assert sorted(prices,key=lambda r:r['nmID'])==sorted(pa,key=lambda r:r['nmID']),'Prices changed during verification'
 result={}
 for old in before:
  new=next(r for r in after if r['vendorCode']==old['vendorCode'])
  for k in ['description','brand','subjectID','dimensions','sizes','photos','video']:
   assert old.get(k)==new.get(k),(old['vendorCode'],k)
  assert sorted(old['characteristics'],key=lambda x:x['id'])==sorted(new['characteristics'],key=lambda x:x['id'])
  result[old['vendorCode']]={'before':old['title'],'after':new['title'],'verified':new['title']==R[old['vendorCode']]['wb_title']}
 save('result',{'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'cards':result})
 assert all(r['verified']for r in result.values()),'Some titles not updated'
 print(json.dumps(result,ensure_ascii=False))
if __name__=='__main__':main()
