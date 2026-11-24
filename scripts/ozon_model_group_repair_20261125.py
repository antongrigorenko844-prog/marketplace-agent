"""Ozon only: repair clearly invalid existing variant groups.
Approved by owner after model audit. The ONLY writable product field is 9048,
a descriptive model label. No new products, no changes to SKU, price, stock,
manufacturer codes, media, brand, categories or descriptions.
A successful attribute task is NOT a confirmed split. Canary first.
"""
import collections,copy,datetime,json,sys,time,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client as oz
import ozon_full_seo_20261125 as seo
from ozon_description_guard import differences
DATA=ROOT/"data"
AUDIT=DATA/"ozon_model_groups_audit_20261125.json"
OUT=DATA/"ozon_model_group_repair_20261125.json"
MODEL=9048
CANARY="0am325025hfe"
# Do not create new combined models. Retain existing anchors and do not
# assume two similarly named v1/v2 offers have identical physical contents.
# Clearly different products are detached to their own descriptive model.
MOVE={
"3651862555":["0am325025hfe"],
"3651869562":["6t30","0AM325473"],
"3644410476":["salnikprivod-l","salnikprivod-r","salnikvhod","salnikvihod","salnik-tolkatelya-alum","tolkateli-salniki-alum-komplekt","salniki-shtokov-2sht","salniki-shtokov-orig"],
"3651862708":["stalnaya-vstavka-plity","plita-filtr-pylniki","plita-filtr-pylniki-orig","plita-filtr-pylniki-sal","plita-filtr-salniki-orig","plita-filtr-v2","plita-pylniki-salniki"],
"3651865771":["prokladka-vtulki-niz-dq200","prokladka-vtulki-verh-dq200"],
"3651865850":["plastiny-solenoid-orig"],
"3651865869":["0am325091f-set"],
"3651866069":["0am325091e-set"],
"3651867334":["pylniki-shtokov-orig","pylniki-shtokov-orig-v2","porshni-usil-pylniki-kryshki","porshni-usil-pylniki-kryshki-v2"],
"3651867989":["tolkateli-4-salniki-4"],
"4102654369":["EA888gen3","EA888gen3-filter-kit"],
"4172860487":["0am325477ae"],
}
# Two pairs with matching titles/composition need physical duplicate review.
# Keep them in the support worklist rather than creating extra duplicates.
DUPLICATE_REVIEW={
"salnik-shtoka-orig","salnik-shtoka-orig-v2",
"plita-pylniki-salniki-orig","plita-pylniki-salniki-orig-v2",
"pylniki-shtokov-orig","pylniki-shtokov-orig-v2",
"porshni-usil-pylniki-kryshki","porshni-usil-pylniki-kryshki-v2",
}
ALLOWED_WARNING={"double_without_merger_offer"}
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d,persist=False):
 d["updated_utc"]=now()
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n");tmp.replace(OUT)
 if persist:subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT.relative_to(ROOT))],cwd=ROOT,check=True)
def live(row):
 k=row["sku"];pid=row["product_id"]
 a=seo.direct_read(k,pid);i=seo.one(oz.get_product_info_list,k,pid)
 pr=seo.one(oz.get_prices_and_stocks,k);st=seo.one(oz.get_stocks,k)
 if i.get("is_archived") or a.get("model_info")!=i.get("model_info") or str(a.get("sku"))!=str(i.get("sku")):raise ValueError("Archived or inconsistent identity")
 if not a.get("model_info",{}).get("model_id"):raise ValueError("Model identity unavailable")
 return a,i,pr,st
def protect(a,i,pr,st):
 p=seo.protected(a,pr,[MODEL])
 p["fields"].pop("model_info",None)
 # Group identity is the intended change; retain all other stable fields.
 p["commercial_info"]={k:i.get(k) for k in ("price","old_price","min_price","is_archived","barcodes")}
 p["stock"]=st
 return p
def same(p,q):
 return differences(p,q)
def info_groups(rows):
 found=seo.exact_map(oz.get_product_info_list(list(rows)))
 if set(found)!=set(rows):raise ValueError("Incomplete catalog/model census")
 result={}
 for k,x in found.items():
  if seo.ids(x)!=rows[k]["product_id"]:raise ValueError("Catalog product identity changed")
  result[k]={"model_id":str((x.get("model_info") or {}).get("model_id") or ""),"count":(x.get("model_info") or {}).get("count"),"sku":str(x.get("sku") or ""),"status":(x.get("statuses") or {}).get("status_name"),"moderation":(x.get("statuses") or {}).get("moderate_status"),"errors":[{"code":e.get("code"),"level":e.get("level")} for e in x.get("errors",[])]}
 return result
def label(row):
 # Name is factual, not a made-up manufacturer number. SKU is only a
 # disambiguator in this internal model label, never OEM or part number.
 return row["name"]+" ["+row["sku"]+"]"
def plan(rows,base,d):
 targets=[]
 for mid,keys in MOVE.items():
  for k in keys:
   x=rows[k]
   if k in DUPLICATE_REVIEW:
    d["holds"][k]="Potential physical duplicate; review exact composition before detaching";continue
   if k not in base or base[k]["model_id"]!=mid or base[k]["count"]<=1:
    d["holds"][k]="Current model differs or already separate; no blind change";continue
   if x["model_id"]!=mid:
    d["holds"][k]="Audit model mismatch";continue
   targets.append(k)
 return sorted(targets,key=lambda k:(k!=CANARY,k))
def check(op,rows):
 k=op["sku"]
 a,i,pr,st=live(rows[k]);current=protect(a,i,pr,st)
 before=op["protected"];diff=same(before,current)
 model=i["model_info"];mid=str(model["model_id"]);count=model.get("count")
 attr_values=seo.values(a,MODEL)
 old=op["before_label"];want=op["new_label"]
 # Ozon may append an exact offer suffix automatically. This is not a new
 # model label supplied by us, and is recorded explicitly.
 match=attr_values==[want] or (len(attr_values)==1 and attr_values[0].startswith(want) and attr_values[0][len(want):].replace("_"+k,"")=="")
 status=(i.get("statuses") or {})
 errors=[e for e in i.get("errors",[]) if e.get("code") not in ALLOWED_WARNING or e.get("level")=="ERROR_LEVEL_ERROR"]
 op["readback"]={"protected_diff":diff,"label_matches":match,"label_actual":attr_values,"previous_model":op["before_model"],"model_id":mid,"model_count":count,"moderation":status.get("moderate_status"),"status":status.get("status_name"),"new_non_group_errors":[{"code":e.get("code"),"level":e.get("level")} for e in errors],"review_metadata_before":op.get("review_metadata_before"),"review_metadata_after":{k:v for k,v in i.items() if "review" in k.lower() or "rating" in k.lower()}}
 if diff or errors or (attr_values!=old and not match):
  op["status"]="protected_drift_or_new_error";return False,True
 if match and count==1 and status.get("moderate_status")=="approved" and status.get("status_name")==op["before_status"]:
  op["status"]="split_verified";return True,False
 op["status"]="pending_model_readback";return False,False
def prepare(k,rows,base,submitted,d):
 if k in submitted:return None
 row=rows[k]
 try:
  a,i,pr,st=live(row)
  model=i["model_info"];mid=str(model["model_id"])
  if mid!=base[k]["model_id"] or model.get("count",0)<=1:raise ValueError("Model changed or already separate")
  if seo.typ(a)!=tuple(row["category"]):raise ValueError("Category drift")
  stat=i.get("statuses") or {}
  if stat.get("moderate_status")!="approved" or stat.get("status_name")!="Продаётся":raise ValueError("Not approved and selling")
  errors=i.get("errors") or []
  if any(e.get("code") not in ALLOWED_WARNING or e.get("level")=="ERROR_LEVEL_ERROR" for e in errors):raise ValueError("Non-group moderation/validation error")
  defs=seo.schema(seo.typ(a));field=defs.get(MODEL,{})
  if "Название модели" not in field.get("name","") or field.get("dictionary_id") or field.get("is_complex") or field.get("is_collection"):raise ValueError("Model attribute not confirmed writable free text")
  old=seo.values(a,MODEL);wanted=label(row)
  if len(old)!=1 or not wanted or len(wanted)>250 or a.get("name")!=row["name"]:raise ValueError("Unexpected current model label/title")
  if old==[wanted]:raise ValueError("Label already set, do not repeat")
  # Never create another shared label or touch the genuine manufacturer code.
  if any(wanted in x.get("group_label",[]) for j,x in rows.items() if j!=k):raise ValueError("New label collision")
  return {"sku":k,"status":"ready","before_model":mid,"before_count":model["count"],"before_label":old,"new_label":wanted,"before_status":stat.get("status_name"),"protected":protect(a,i,pr,st),"review_metadata_before":{k:v for k,v in i.items() if "review" in k.lower() or "rating" in k.lower()},"payload":{"offer_id":k,"attributes":[{"id":MODEL,"complex_id":0,"values":[{"dictionary_value_id":0,"value":wanted}]}]}}
 except Exception as e:d["holds"][k]=str(e)[:350];return None
def submit(ops,rows,d,submitted):
 for op in ops:
  submitted.add(op["sku"]);op["status"]="submission_started"
 d["submitted"]=sorted(submitted);d["operations"]+=ops;d["external_write_requests"]+=1;save(d,True)
 try:
  payload={"items":[op["payload"] for op in ops]}
  assert all(set(x)=={"offer_id","attributes"} and len(x["attributes"])==1 and x["attributes"][0]["id"]==MODEL for x in payload["items"])
  result=oz._post("/v1/product/attributes/update",payload,retries=1)
  task=result.get("task_id") or (result.get("result") or {}).get("task_id")
  for op in ops:op["task_id"]=task;op["status"]="submitted"
  save(d,True)
  if task:
   status=seo.check_import(task,tries=4)
   for op in ops:op["import_status"]=status
  else:raise ValueError("No import task ID; no repeat")
 except Exception as e:
  for op in ops:op["submission_error"]=str(e)[:350];op["status"]="uncertain_no_retry"
  save(d,True);return False
 # Short bounded readback. Model recalculation can be asynchronous.
 for delay in (0,6,12,18):
  if delay:time.sleep(delay)
  ok=True;stop=False
  for op in ops:
   try:
    good,bad=check(op,rows);ok=ok and good;stop=stop or bad
   except Exception as e:op["readback_error"]=str(e)[:300];ok=False
  save(d,True)
  if stop:return False
  if ok:return True
 return False
def run():
 audit=json.loads(AUDIT.read_text())
 if audit.get("platform")!="Ozon" or audit.get("coverage",{}).get("exact_both")!=audit.get("coverage",{}).get("listed"):raise ValueError("Incomplete audit")
 rows=audit["rows"]
 old=json.loads(OUT.read_text()) if OUT.exists() else {}
 submitted=set(old.get("submitted",[]))
 d={"platform":"Ozon","owner_scope":"separate clearly different actual models; only 9048; no new merges","started_utc":now(),"status":"preflight","external_write_requests":0,"operations":old.get("operations",[]),"submitted":sorted(submitted),"holds":{}}
 base=info_groups(rows);d["before_model_census"]=base
 d["plan"]={mid:{"detach":v,"all_audited_members":audit["model_groups"][mid]["members"]} for mid,v in MOVE.items()}
 targets=plan(rows,base,d);d["eligible_plan"]=targets;save(d,True)
 canary_ok=False
 previous=next((op for op in d["operations"] if op["sku"]==CANARY),None)
 if previous:
  try:canary_ok,drift=check(previous,rows)
  except Exception as e:d["holds"][CANARY]=str(e)[:300]
 elif CANARY in targets:
  op=prepare(CANARY,rows,base,submitted,d)
  if op:canary_ok=submit([op],rows,d,submitted)
 # Do not use a successful API acknowledgment as proof of model split.
 # Do not change other cards until actual count=1 and protected state matches.
 if canary_ok:
  for k in targets:
   if k==CANARY:continue
   op=prepare(k,rows,base,submitted,d)
   if not op:continue
   if not submit([op],rows,d,submitted):break
 else:
  d["holds"]["remaining_plan"]="Canary split not independently verified; use documented Seller UI/support if model persists. No mass speculative edits."
 end=info_groups(rows);d["after_model_census"]=end
 for op in d["operations"]:
  k=op["sku"]
  if op["status"] not in ("split_verified","protected_drift_or_new_error"):
   try:check(op,rows)
   except Exception as e:op["final_readback_error"]=str(e)[:300]
 d["actual_splits"]=[k for k in rows if (base[k]["count"] or 0)>1 and end[k]["count"]==1 and end[k]["sku"]==base[k]["sku"]]
 d["lost_selling"]=[k for k in rows if base[k]["status"]=="Продаётся" and end[k]["status"]!="Продаётся"]
 d["identity_drift"]=[k for k in rows if base[k]["sku"]!=end[k]["sku"]]
 groups=collections.defaultdict(list)
 for k,x in end.items():
  if x["model_id"] and x["model_id"]!="0":groups[x["model_id"]].append(k)
 d["remaining_actual_groups"]={k:v for k,v in groups.items() if len(v)>1}
 d["status"]="reviewed" if canary_ok else "canary_not_confirmed"
 d["finished_utc"]=now();save(d,True)
 print(json.dumps({"status":d["status"],"operations":[[x["sku"],x["status"],x.get("readback")] for x in d["operations"]],"actual_splits":d["actual_splits"],"remaining_groups":len(d["remaining_actual_groups"]),"lost_selling":d["lost_selling"],"identity_drift":d["identity_drift"],"holds":d["holds"]},ensure_ascii=False))
if __name__=="__main__":run()
