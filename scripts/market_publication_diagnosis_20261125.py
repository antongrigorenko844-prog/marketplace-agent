"""LEOPARTS Market blocked-offer diagnostics and exact, non-distinctive metadata repair.
Only existing automatically disabled offers, no group, stock, price, media, brand,
description, SKU or product changes. No attempted reactivation by pretending errors are
seller hiding. Diagnose real warehouse placement and verify publication by fresh readback.
"""
import collections,copy,datetime,json,sys,time,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import yandex_market_client as ym
import market_full_seo_20261125 as s
import market_growth_recovery_20261125 as r
import market_final_optimization_20261125 as f
import market_final_readback_20261125 as prev
DATA=ROOT/"data";OUT=DATA/"market_publication_diagnosis_20261125.json"
CAM=141377963;BIZ=194752944
BASE=json.loads((DATA/"market_postmoderation_readonly_20261125.json").read_text())
TARGETS=set(BASE["automatic_blockers"])
# These codes refer to physically different products, confirmed by the exact
# live reference SKU title and existing source metadata, not by similar names.
CLEAR={
 "derzhatel-datchikov-dq200":("0AM927769N","держател","0AM927769N","проклад"),
 "klipsa-datchika-peredach-dq200":("0AM325025P","датчика положения передач","0am325025P","клипса штока"),
 "kryshka-platy-dq200":("0AM325219C","крышка электронной платы","0AM325219C","крышка мехатроника"),
}
CONTROL=["0AM927769D","0am325025HX","6t30","0AM325219C","0am325025P","0AM927769N","Ea888gen3","EA888","EA888gen3","vint-kollektora-ea888","0am325025","0AM325587E"]
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d,persist=False):
 d["updated_at"]=now()
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n");tmp.replace(OUT)
 if persist:subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT.relative_to(ROOT))],cwd=ROOT,check=True)
def read(d,key,method,path,body=None,params=None):
 # All calls here are documented READ methods. No generic retry loop.
 try:
  code,b=ym._request(method,path,json_body=body,params=params,retries=1)
  if code!=200 or b.get("status") not in ("OK",None):raise ValueError("HTTP "+str(code)+" "+ym._errors_text(b)[:200])
  return b.get("result",b)
 except Exception as e:d["diagnostic_errors"][key]=str(e)[:300];return None
def idsafe(x):return str(x) if x is not None else None
def protected(m,c,st):
 # Publication itself is expected to change if recovery succeeds.
 # Compare only stable protected fields plus exact marketSku/model/group.
 o=m["offer"];mp=m.get("mapping") or {}
 fields=("offerId","name","description","vendor","vendorCode","pictures","videos","manuals","barcodes","weightDimensions","basicPrice","purchasePrice","condition","commodityCodes","certificates","boxCount","manufacturerCountries","guaranteePeriod","params")
 return {"offer":{k:copy.deepcopy(o.get(k)) for k in fields},"identity":s.identity(m,c),"model":idsafe(mp.get("marketModelId")),"group":c.get("groupId"),"commercial":{k:copy.deepcopy(st.get(k)) for k in ("basicPrice","campaignPrice")}}
def summarize(m,c,st,k):
 o=m[k]["offer"];card=c[k];status=st[k];mapping=m[k].get("mapping") or {}
 # Keep only business evidence, not signed media URLs or personal details.
 return {"sku":k,"name":o.get("name"),"market_sku":idsafe(mapping.get("marketSku")),"model":idsafe(mapping.get("marketModelId")),"group":card.get("groupId"),"category":s.catid(m[k],card),"category_name":mapping.get("marketCategoryName"),"status":status.get("status"),"card_status":card.get("cardStatus"),"campaign_errors":status.get("errors"),"campaign_warnings":status.get("warnings"),"card_errors":card.get("errors"),"card_warnings":card.get("warnings"),"campaigns":o.get("campaigns"),"selling_programs":o.get("sellingPrograms"),"archived":o.get("archived"),"price":status.get("basicPrice"),"campaign_price":status.get("campaignPrice"),"vendor":o.get("vendor"),"has_description":bool(o.get("description")),"pictures_count":len(o.get("pictures") or []),"media_states":{k:[v.get("uploadState") for v in vs if isinstance(v,dict)] for k,vs in (o.get("mediaFiles") or {}).items() if isinstance(vs,list)},"barcodes_count":len(o.get("barcodes") or []),"vendor_code":o.get("vendorCode"),"weight_dimensions":o.get("weightDimensions"),"commodity_codes":o.get("commodityCodes"),"condition":o.get("condition"),"certificates_count":len(o.get("certificates") or []),"ugc":o.get("ugc"),"params":card.get("parameterValues",[]),"rating":card.get("contentRating"),"showcase_types":[x.get("showcaseType") for x in m[k].get("showcaseUrls",[]) if isinstance(x,dict)]}
def warehouse_rows(x):
 if not x:return []
 return [{"id":w.get("id",w.get("warehouseId",w.get("partnerWarehouseId"))),"models":w.get("models"),"status":w.get("status"),"campaignId":w.get("campaignId"),"campaignIds":w.get("campaignIds"),"enabled":w.get("enabled"),"type":w.get("type")} for w in x.get("warehouses",[]) if isinstance(w,dict)]
def run():
 biz,cam=s.account()
 if biz!=BIZ or int(cam)!=CAM:raise ValueError("Wrong account")
 old=json.loads(OUT.read_text()) if OUT.exists() else {}
 submitted=set(old.get("submitted",[]));d={"platform":"Yandex Market","business_id":biz,"campaign_id":int(cam),"status":"diagnosis","checked_at":now(),"diagnostic_errors":{},"submitted":sorted(submitted),"operations":old.get("operations",[]),"market_write_requests":0}
 m,c,st=r.read_all(biz,cam)
 current={k for k,v in st.items() if v.get("status")=="DISABLED_AUTOMATICALLY"}
 ids=sorted((TARGETS|current|set(CONTROL))&set(m))
 d["before_counts"]=dict(collections.Counter(x.get("status") for x in st.values()))
 d["initial_targets"]=sorted(TARGETS)
 d["status_snapshot"]={k:summarize(m,c,st,k) for k in ids}
 # Include raw per-SKU official stock details, not inferred totals; distinguish
 # warehouse model and campaign scope before selecting the appropriate method.
 settings=read(d,"settings","POST",f"/v2/businesses/{biz}/settings",{})
 d["settings"]={k:settings.get(k) for k in ("warehouseModel","onlyDefaultPrice")} if settings else None
 camp=read(d,"campaign","GET",f"/v2/campaigns/{cam}")
 ca=(camp or {}).get("campaign",camp or {})
 d["campaign_settings"]={k:ca.get(k) for k in ("id","apiAvailability","placementType","status","warehouseId","state")}
 stat=read(d,"sku_stats","POST",f"/v2/campaigns/{cam}/stats/skus",{"shopSkus":ids})
 d["sku_stock_stats"]={x["shopSku"]:{"price":x.get("price"),"market_sku":idsafe(x.get("marketSku")),"warehouses":[{"id":w.get("id"),"stocks":w.get("stocks")} for w in x.get("warehouses",[])]} for x in (stat or {}).get("shopSkus",[]) if x.get("shopSku") in ids}
 model=(settings or {}).get("warehouseModel")
 if model=="WAREHOUSE":
  wh=read(d,"partner_warehouses","POST",f"/v3/businesses/{biz}/warehouses",{})
  d["warehouses"]=warehouse_rows(wh)
  d["partner_stocks"]={}
  for w in d["warehouses"][:12]:
   wid=w.get("id")
   if wid is None:continue
   data=read(d,"warehouse_stocks_"+str(wid),"POST",f"/v3/businesses/{biz}/offers/stocks",{"partnerWarehouseId":wid,"offerIds":ids})
   if data:d["partner_stocks"][str(wid)]={"offers":data.get("offers",[]),"paging":data.get("paging")}
 else:
  stocks=read(d,"campaign_warehouse_stocks","POST",f"/v2/campaigns/{cam}/offers/stocks",{"offerIds":ids})
  d["campaign_warehouse_stocks"]=stocks
 # Compare actual group membership including archived offers, not just names.
 allids=set(m)|{x["offer"]["offerId"] for x in s.get_mappings(biz,True)}
 allc=s.index(s.batched_read(biz,"offer-cards",sorted(allids)),lambda x:x["offerId"])
 if set(allc)!=allids:raise ValueError("Incomplete archive census")
 members=collections.defaultdict(list)
 for k,v in allc.items():
  if f.grp(v):members[f.grp(v)].append(k)
 group_keys={f.grp(c[k]) for k in current if k in c and f.grp(c[k])}
 d["groups"]={}
 for g in sorted(group_keys):
  group=members[g];entry={"members":group,"cards":[]}
  for k in group:
   if k not in m:entry["cards"].append({"sku":k,"archived_or_unavailable":True});continue
   cat=s.catid(m[k],c[k]);defs=s.definition_map(biz,cat)
   entry["cards"].append({"sku":k,"status":st[k].get("status"),"category":cat,"market_sku":idsafe(m[k].get("mapping",{}).get("marketSku")),"model":idsafe(m[k].get("mapping",{}).get("marketModelId")),"ugc":m[k]["offer"].get("ugc"),"distinctive_fields":[{"id":p,"name":x.get("name"),"values":s.groups(c[k]).get(p,[])} for p,x in defs.items() if x.get("distinctive")],"OEM":s.existing_text(c[k],60134505)})
  d["groups"][g]=entry
 d["status"]="diagnosed";save(d,True)
 # Apply only exact optional non-distinctive field corrections that were not
 # previously attempted. Never change groups, even if a duplicate warning exists.
 master=s.wsrows(DATA/"master_control.xlsx")
 newops=[]
 for k,(wrong,needle,ref,refneedle) in CLEAR.items():
  try:
   if k in submitted or k not in current:continue
   if k not in master or ref not in m:raise ValueError("Exact SKU source absent")
   if st[k].get("status")!="DISABLED_AUTOMATICALLY" or c[k].get("cardStatus")!="HAS_CARD_CAN_UPDATE":raise ValueError("Not editable disabled card")
   if members.get(f.grp(c[k]))!=[k]:raise ValueError("Not singleton")
   if needle not in m[k]["offer"]["name"].casefold() or refneedle not in m[ref]["offer"]["name"].casefold():raise ValueError("Physical identity mismatch")
   if s.existing_text(c[k],60134505)!=[wrong] or s.catid(m[k],c[k])!=s.TRANS:raise ValueError("Exact old OEM/category not matched")
   field=s.definition_map(biz,s.TRANS).get(60134505,{})
   if field.get("type")!="TEXT" or field.get("required") or field.get("distinctive"):raise ValueError("OEM field not safe")
   # Reference number belongs to a different physical product, not this one.
   # Preserve all compatibility and composition values; no guessed replacement.
   if wrong.casefold() not in (ref.casefold(),):raise ValueError("Reference SKU/code mismatch")
   expected=copy.deepcopy(s.groups(c[k]));expected.pop(60134505,None)
   newops.append({"sku":k,"kind":"oem_clear","status":"ready","protected":protected(m[k],c[k],st[k]),"expected":expected,"old":s.groups(c[k]),"category":s.TRANS,"payload":{"offerId":k,"categoryId":s.TRANS,"parameterValues":[{"parameterId":60134505,"value":""}]},"evidence":{"reference_sku":ref,"reference_name":m[ref]["offer"]["name"],"old_oem":wrong}})
  except Exception as e:d.setdefault("blocked_corrections",{})[k]=str(e)[:250]
 # The approved EA888 kit has three components; old typed composition omitted
 # filter. This is a factual metadata correction, NOT a change to actual contents.
 k="EA888gen3-filter-kit"
 try:
  if k not in submitted and k in current:
   approved=json.loads((DATA/"ea888_gen3_filter_kit_approved_20261123.json").read_text())
   if approved.get("sku")!=k or approved.get("publish_authorized") is not True:raise ValueError("Missing approval")
   parts={x["part"]:x["qty"] for x in approved["composition"]}
   if parts!={"EA888":1,"Ea888gen3":1,"filter-with-ring":1}:raise ValueError("Unexpected approved composition")
   if c[k].get("cardStatus")!="HAS_CARD_CAN_UPDATE" or s.catid(m[k],c[k])!=90442 or k not in master:raise ValueError("Unexpected card or source")
   before="Комплектация: Алюминиевый корпус масляного фильтра EA888 Gen3 — 1 шт.; обратный клапан — 1 шт."
   if s.existing_text(c[k],57046341)!=[before]:raise ValueError("Composition field changed; review")
   field=s.definition_map(biz,90442).get(57046341,{})
   p={"parameterId":57046341,"value":before.rstrip(".")+"; масляный фильтр с уплотнительным кольцом — 1 шт."}
   if field.get("type")!="TEXT" or field.get("distinctive") or not s.valid_value(p,field):raise ValueError("Composition field not safe")
   expected=copy.deepcopy(s.groups(c[k]));expected[57046341]=[p]
   newops.append({"sku":k,"kind":"approved_composition_metadata","status":"ready","protected":protected(m[k],c[k],st[k]),"expected":expected,"old":s.groups(c[k]),"category":90442,"payload":{"offerId":k,"categoryId":90442,"parameterValues":[p]},"evidence":{"source":"data/ea888_gen3_filter_kit_approved_20261123.json","composition":parts}})
 except Exception as e:d.setdefault("blocked_corrections",{})[k]=str(e)[:250]
 d["operations"]+=newops;save(d,True)
 for op in newops:
  k=op["sku"]
  try:
   mm,cc,ss=s.live(biz,cam,k)
   if protected(mm,cc,ss)!=op["protected"] or f.norm(s.groups(cc))!=f.norm(op["old"]) or ss.get("status")!="DISABLED_AUTOMATICALLY" or cc.get("cardStatus")!="HAS_CARD_CAN_UPDATE":raise ValueError("Preflight live drift")
  except Exception as e:op["status"]="not_submitted";op["error"]=str(e)[:200];continue
  submitted.add(k);d["submitted"]=sorted(submitted);d["market_write_requests"]+=1;op["status"]="submission_started";save(d,True)
  try:
   result=s.post(f"/v2/businesses/{biz}/offer-cards/update",{"offersContent":[op["payload"]]},retries=1)
   errs=[e for x in result.get("results",[]) for e in x.get("errors",[])]
   op["status"]="item_error" if errs else "submitted"
   if errs:op["error"]=errs
  except Exception as e:op["status"]="uncertain_no_retry";op["error"]=str(e)[:300]
  save(d,True)
  try:
   mm,cc,ss=s.live(biz,cam,k)
   diff=[a for a,v in op["protected"].items() if protected(mm,cc,ss).get(a)!=v]
   actual=f.norm(s.groups(cc));expected=f.norm({int(a):v for a,v in op["expected"].items()});before=f.norm(op["old"])
   op["readback"]={"protected_diff":diff,"params_match":actual==expected,"campaign_status":ss.get("status"),"card_status":cc.get("cardStatus")}
   if diff or actual not in (before,expected):op["status"]="drift_review";save(d,True);break
   if actual==expected and cc.get("cardStatus")=="HAS_CARD_CAN_UPDATE":op["status"]="verified"
   else:op["status"]="pending_readback"
  except Exception as e:op["readback_error"]=str(e)[:200]
  save(d,True)
 m2,c2,st2=r.read_all(biz,cam)
 for op in d["operations"]:
  k=op["sku"]
  if k not in submitted or k not in m2:continue
  try:
   diff=[a for a,v in op["protected"].items() if protected(m2[k],c2[k],st2[k]).get(a)!=v]
   actual=f.norm(s.groups(c2[k]));expected=f.norm({int(a):v for a,v in op["expected"].items()})
   op["final_readback"]={"protected_diff":diff,"params_match":actual==expected,"campaign_status":st2[k].get("status"),"card_status":c2[k].get("cardStatus")}
   if diff:op["status"]="drift_review"
   elif actual==expected and c2[k].get("cardStatus")=="HAS_CARD_CAN_UPDATE":op["status"]="verified"
  except Exception as e:op["final_readback_error"]=str(e)[:200]
 d["final_counts"]=dict(collections.Counter(x.get("status") for x in st2.values()))
 d["recovered"]=[k for k in TARGETS if st2.get(k,{}).get("status")=="PUBLISHED"]
 d["remaining"]={k:summarize(m2,c2,st2,k) for k in sorted(TARGETS) if k in m2 and st2[k].get("status")!="PUBLISHED"}
 d["lost_publication"]=[k for k in st if st[k].get("status")=="PUBLISHED" and st2.get(k,{}).get("status")!="PUBLISHED"]
 plan=json.loads((DATA/"market_growth_next_plan_20261125.json").read_text())
 d["remaining_categories"]=[x["sku"] for x in plan["category_remaining"] if x["sku"] not in m2 or s.catid(m2[x["sku"]],c2[x["sku"]])!=x["target"]]
 d["category_verified"]=[x["sku"] for x in plan["category_remaining"] if x["sku"] in m2 and s.catid(m2[x["sku"]],c2[x["sku"]])==x["target"]]
 d["status"]="readback_complete";d["finished_at"]=now();save(d,True)
 print(json.dumps({"counts":d["final_counts"],"recovered":d["recovered"],"remaining":len(d["remaining"]),"ops":[[x["sku"],x["status"]] for x in d["operations"]],"settings":d["settings"],"diagnostic_errors":d["diagnostic_errors"],"remaining_categories":len(d["remaining_categories"])},ensure_ascii=False))
if __name__=="__main__":run()
