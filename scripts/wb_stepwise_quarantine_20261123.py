"""WB owner-approved eight-price quarantine repair, gradual steps per seller guidance.
One WRITE per step, persist intent before POST, verify price/task, no blind replay.
No stock, media, product card, or other channel changes.
"""
from __future__ import annotations
import json,os,subprocess,sys,time
from datetime import datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client as wb
MAN=ROOT/"data/approved_wb_ym_prices_20261123.json"
PREV=ROOT/"data/approved_wb_ym_prices_20261123_result.json"
OUT=ROOT/"data/wb_stepwise_quarantine_20261123.json"
P={
"0am325025u":(670860803,5349.82,2880),
"0am325091e":(670925587,3849.78,1990),
"0am325091f":(670925476,3350.02,1990),
"0am325025D":(670854215,3350.13,1880),
"0am325091e-set":(1949826486,8850.28,4990),
"0am325091f-set":(1949828986,8850.28,4990),
"vtulki-shtokov-2sht":(1949877502,4850.02,2880),
"02e305045":(670914369,3850,1980)}
assert len(P)==8
def near(a,b,tol=2):return a is not None and abs(float(a)-float(b))<=tol
def stamp():return datetime.now(timezone.utc).isoformat()
def check(x,msg):
 if not x:raise RuntimeError("STOP: "+msg)
def save(d):
 d["updated_utc"]=stamp()
 OUT.write_text(json.dumps(d,indent=2,ensure_ascii=False,default=str)+"\n",encoding="utf8")
def persist():
 subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT.relative_to(ROOT))],cwd=ROOT,check=True,timeout=180)
def live():
 rows=wb.get_prices(limit=1000)
 check(isinstance(rows,list) and len(rows)>110,"WB price API incomplete")
 result={}
 for sku,(nm,old,target) in P.items():
  m=[r for r in rows if r.get("vendorCode")==sku]
  if len(m)!=1:result[sku]={"status":"missing_or_duplicate"};continue
  row=m[0];sizes=row.get("sizes") or []
  if len(sizes)!=1 or row.get("editableSizePrice"):
   result[sku]={"status":"size_problem"};continue
  check(int(row.get("nmID") or 0)==nm,"Unexpected nmID "+sku)
  disc=int(row.get("discount") or 0)
  check(0<=disc<100,"Invalid discount "+sku)
  result[sku]={"status":"ok","price":float(sizes[0]["discountedPrice"]),"discount":disc,"nmID":nm}
 return result
def readback(target):
 for seconds in (8,12,16):
  time.sleep(seconds)
  cur=live()
  if all(near(cur.get(s,{}).get("price"),t,2.5) for s,t in target.items()):
   return cur
 return live()
def task(uid):
 if not uid:return {}
 try:
  h=wb.get_price_upload_result(uid)
  return {"task":h.get("task"),"goods":[{"nmID":x.get("nmID"),"status":x.get("status"),
          "errorText":str(x.get("errorText") or "")[:220]} for x in h.get("goods") or []]}
 except Exception as e:return {"error_type":type(e).__name__}
def submit(d,name,targets,previous):
 before=live()
 plans=[];blocked={}
 for sku,value in targets.items():
  a=before.get(sku,{})
  if a.get("status")!="ok" or not near(a.get("price"),previous[sku],2):
   blocked[sku]="Unexpected current price or missing SKU";continue
  disc=a["discount"]
  base=int((Decimal(value)*Decimal(100)/Decimal(100-disc)).quantize(Decimal(1),rounding=ROUND_HALF_UP))
  check(0<base<999999,"Invalid WB base "+sku)
  plans.append({"nmID":a["nmID"],"price":base,"discount":disc})
 if not plans:
  d["steps"][name]={"status":"blocked_no_items","before":before,"blocked":blocked}
  save(d);persist();return {}
 d["steps"][name]={"status":"intent","target":targets,"before":before,"blocked":blocked,
                   "submitted_nmids":[r["nmID"] for r in plans]}
 save(d);persist()
 # ONE real POST. API retries explicitly disabled.
 print("WB_STEP_POST",name,len(plans),flush=True)
 try:
  r=wb.update_prices(plans,retries=1)
  uid=(r.get("data") or {}).get("id") if isinstance(r,dict) else None
  d["steps"][name]["upload_id"]=uid
  d["steps"][name]["status"]="acknowledged" if uid else "ambiguous_ack"
  d["physical_writes"]+=1
 except Exception as e:
  d["steps"][name]["status"]="unknown_http_outcome"
  d["steps"][name]["error"]=type(e).__name__+": "+str(e)[:200]
  save(d);persist();return {}
 save(d);persist()
 cur=readback(targets)
 d["steps"][name]["history"]=task(uid)
 d["steps"][name]["after"]=cur
 good=[s for s,t in targets.items() if near(cur.get(s,{}).get("price"),t,2.5)]
 d["steps"][name]["verified"]=good
 d["steps"][name]["status"]="verified" if len(good)==len(targets) else "partial"
 save(d);persist()
 print("WB_STEP_RESULT",name,len(good),"/",len(targets),flush=True)
 return {s:t for s,t in targets.items() if s in good}
def main():
 check(os.getenv("GITHUB_ACTIONS")=="true","Actions runner required")
 m=json.loads(MAN.read_text(encoding="utf8"))
 r=json.loads(PREV.read_text(encoding="utf8"))
 approved=m.get("today",{})|m.get("yesterday",{})
 assert m["approved_by_owner"] and m["scope"]==["wildberries","yandex_market"]
 for sku,(nm,old,new) in P.items():
  check(approved.get(sku)==new and sku in r["results"]["wb"]["unverified_skus"],"Unapproved SKU "+sku)
  check(near(r["results"]["wb"]["after"][sku]["current"],old),"Unmatched starting WB price "+sku)
 if OUT.exists():
  old=json.loads(OUT.read_text(encoding="utf8"))
  if old.get("steps"):
   print("WB_STEPWISE_NO_REPLAY",old.get("status"),flush=True)
   return
  d=old
 else:
  d={"status":"new","created_utc":stamp(),"physical_writes":0,
     "approved_targets":{s:p[2] for s,p in P.items()},"steps":{}}
  save(d);persist()
 starting=live()
 middle={}
 for sku,(nm,old,new) in P.items():
  check(starting.get(sku,{}).get("status")=="ok" and near(starting[sku].get("price"),old),"Unexpected live start "+sku)
  intermediate=int((Decimal(str(old))*Decimal("0.72")).quantize(Decimal(1),rounding=ROUND_HALF_UP))
  check(new<intermediate<old and intermediate/old>0.67 and new/intermediate>0.67,
        "Invalid protected price step "+sku)
  middle[sku]=intermediate
 d["intermediate"]=middle;save(d);persist()
 accepted_mid=submit(d,"middle",middle,{s:old for s,(nm,old,target) in P.items()})
 if not accepted_mid:
  d["status"]="partial_no_middle";save(d);persist();return
 targets={sku:P[sku][2] for sku in accepted_mid}
 accepted_final=submit(d,"final",targets,accepted_mid)
 d["status"]="verified" if len(accepted_final)==len(P) else "partial"
 d["verified_targets"]=accepted_final
 d["missing_targets"]=[s for s in P if s not in accepted_final]
 d["finished_utc"]=stamp()
 save(d);persist()
 print("WB_STEPWISE_FINAL",d["status"],len(accepted_final),"/8",flush=True)
if __name__=="__main__":main()
