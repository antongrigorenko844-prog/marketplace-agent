"""Continue Market-only SEO with fresh independent readback and guarded category endpoint.
No group change, creation, price, stock, media, description, or other channel writes.
Read-only finance, stock and publication diagnostics; do not persist buyer details.
"""
import sys,json,time,datetime,collections,copy,re,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import market_full_seo_20261125 as s
import yandex_market_client as ym
OUT=ROOT/"data/market_growth_recovery_20261125.json"
OLD=ROOT/"data/market_full_seo_20261125.json"
BUSINESS=194752944
TARGET=71380208
# Strict reviewed exact SKU and known physical type, not inferred from a code suffix.
CAT_TYPES={
 "porshni-usil-pylniki-kryshki":"ремкомплект",
 "porshni-usil-pylniki-kryshki-v2":"ремкомплект",
 "0cw325025h":"прокладка мехатроника",
 "0cw0amdsgdq200":"уплотнение электромотора мехатроника",
 "Dq200dsg7":"уплотнение насоса мехатроника",
 "0am325477ae":"пластина соленоидов",
 "Dq200dsg70am0cw":"ремкомплект",
 "Ea888gen3":"обратный клапан масляного фильтра",
}
# Missing type only, exact identity + matching live title evidence; no compatibility
# guesses. Values already present are never overwritten. Prefer known dictionary.
TYPE_PLAN={
 "0am325025":("ремкомплект","ремкомплект"),
 "02e305045":("корпус","корпус"),
 "0am325025ae":("комплект","крышка"),
 "plita-filtr":("комплект","гидроплита"),
 "0am325025G":("ремкомплект","ремкомплект"),
 "separatornaya-plastiny-solenoid-dq200":("комплект","пластин"),
 "0AM325219C":("крышка","крышка"),
 "prokladka-vtulki-niz-dq200":("кольцо","кольцо"),
 "prokladka-vtulki-verh-dq200":("кольцо","кольцо"),
 "prokladka-vtulki-dq200":("кольцо","кольц"),
 "0am325025e":("заглушка","заглушка"),
 "DQ500":("комплект","фильтр"),
 "0am325025С":("кольцо","кольцо"),
 "0am325066AC":("поршень штока","поршень"),
 "0am325091f":("шток","шток"),
 "0am325091e":("шток","шток"),
 "0am325025l":("толкатель","толкатель"),
 "0am325025P":("клипса","клипса"),
 "0AM325120A":("заглушка","заглушка"),
 "0am325066ad":("манжета","манжета"),
 "0AM301212A":("заглушка","заглушка"),
 "plita-filtr-pylniki":("комплект","гидроплита"),
 "plita-filtr-pylniki-sal":("комплект","гидроплита"),
 "plita-pylniki-salniki-orig":("комплект","гидроплита"),
 "plita-pylniki-salniki-orig-v2":("комплект","гидроплита"),
 "0am325025r":("кольцо","кольцо"),
 "02E 305 045":("комплект","фильтр"),
 "0AM325433E":("фильтр АКПП","фильтр"),
 "02E305051C":("фильтр АКПП","фильтр"),
 "filtr-dsg7-dq500":("фильтр АКПП","фильтр"),
 "0am325025H":("мехатроник","мехатроник"),
 "31705-1XF1A":("гидроблок вариатора","гидроблок"),
 "317053TX0C":("гидроблок вариатора","гидроблок"),
}
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d,commit=False):
 d["updated_utc"]=now()
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8");tmp.replace(OUT)
 if commit:subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT.relative_to(ROOT))],cwd=ROOT,check=True)
def model(m,c):
 a=m.get("mapping",{});b=c.get("mapping",{})
 if a.get("marketModelId")!=b.get("marketModelId") or not a.get("marketModelId"):raise ValueError("Model identity ambiguous")
 return a["marketModelId"]
def protected(m,c,st):
 return s.protected(m,c,st)|{"model":model(m,c)}
def normg(c,omit=()):
 return s.grouped_norm(c,omit)
def type_value(defs,val):
 d=defs.get(21194330)
 if not d or d.get("name")!="Тип" or d.get("distinctive"):raise ValueError("Type schema mismatch")
 options=[x for x in d.get("values",[]) if str(x.get("value","")).casefold()==val.casefold()]
 if len(options)==1:return {"parameterId":21194330,"valueId":options[0]["id"],"value":options[0]["value"]}
 if not d.get("allowCustomValues"):raise ValueError("Type unavailable in official dictionary")
 return {"parameterId":21194330,"value":val}
def value_matches(c,p):
 vals=s.groups(c).get(p["parameterId"],[])
 return len(vals)==1 and (vals[0].get("valueId")==p.get("valueId") if p.get("valueId") else str(vals[0].get("value","")).casefold()==str(p["value"]).casefold())
def clean_snapshot(m,c,st):
 o=m["offer"];mp=m.get("mapping",{})
 return {"offerId":o["offerId"],"name":o.get("name"),"identity":s.identity(m,c),"model":mp.get("marketModelId"),"category":s.catid(m,c),"categoryName":mp.get("marketCategoryName"),"card_status":c.get("cardStatus"),"campaign_status":st.get("status"),"errors":st.get("errors",[]),"warnings":st.get("warnings",[]),"card_errors":c.get("errors",[]),"card_warnings":c.get("warnings",[]),"ugc":o.get("ugc"),"rating":c.get("contentRating"),"price":st.get("basicPrice"),"campaigns":o.get("campaigns"),"sellingPrograms":o.get("sellingPrograms"),"parameterValues":c.get("parameterValues",[])}
def same_price(a,b):return a.get("basicPrice")==b.get("basicPrice")
def read_all(biz,cam):
 m=s.index(s.get_mappings(biz),lambda r:r["offer"]["offerId"])
 c=s.index(s.batched_read(biz,"offer-cards",list(m)),lambda r:r["offerId"])
 st=s.campaign_status(cam,list(m))
 if set(m)!=set(c) or set(m)!=set(st):raise ValueError("Incomplete active snapshot")
 return m,c,st
def review_count(biz,sku):
 # Only store aggregate count, never personal text or buyer information.
 try:
  code,b=ym._request("POST",f"/v2/businesses/{biz}/goods-feedback",params={"limit":50},json_body={"offerIds":[sku]},retries=1)
  if code!=200 or b.get("status")!="OK":return {"available":False,"http":code}
  rr=b.get("result") or {}
  items=rr.get("feedbacks")
  if not isinstance(items,list):return {"available":False,"reason":"unexpected response shape"}
  return {"available":True,"count_at_least":len(items),"complete":not (rr.get("paging") or {}).get("nextPageToken")}
 except Exception as e:return {"available":False,"reason":str(e)[:180]}
def mutation(d,biz,cam,sku,target,p,kind,before,expected_group_count,guard_members):
 m,c,st=before
 cat=s.catid(m,c);pre=protected(m,c,st)
 beforeg=s.groups(c)
 if m["offer"].get("archived") or st.get("status")=="DISABLED_BY_PARTNER":raise ValueError("Archived/manually disabled")
 if c.get("cardStatus") not in ("HAS_CARD_CAN_UPDATE","HAS_CARD_CAN_UPDATE_ERRORS"):raise ValueError("Card undergoing moderation or locked")
 g=c.get("groupId")
 if guard_members.get(str(g or "").strip().casefold(),[])!=[sku] and g:raise ValueError("Group not verified singleton")
 defs=s.definition_map(biz,target)
 if not s.valid_value(p,defs[p["parameterId"]]):raise ValueError("Invalid type value")
 if kind=="category":
  if cat==target:return {"status":"already_correct"}
  if any(k not in defs and any(v.get("value") or v.get("valueId") for v in vs) for k,vs in beforeg.items()):raise ValueError("Category loses current typed fields")
  if any(not s.valid_value(v,defs[k]) for k,vs in beforeg.items() if k in defs for v in vs if v.get("value") or v.get("valueId")):raise ValueError("Destination rejects preserved field")
  if any(x.get("required") and k not in beforeg and k!=p["parameterId"] for k,x in defs.items()):raise ValueError("Missing required destination field")
  if m["offer"].get("ugc"):raise ValueError("Public rating/reviews present; no category retry without stronger preservation evidence")
  reviews=review_count(biz,sku)
  if not reviews.get("available") or not reviews.get("complete") or reviews.get("count_at_least")!=0:raise ValueError("No verified zero seller reviews for category test")
 else:
  if cat!=target or s.existing_text(c,p["parameterId"]) or beforeg.get(p["parameterId"]):raise ValueError("Type no longer missing")
 # Use official category-specific endpoint, complete compatible values included
 # to protect against account-specific sparse-update behavior.
 retained=[x for k,vs in beforeg.items() if k in defs and k!=p["parameterId"] for x in vs]
 vals=retained+[p]
 body={"offersContent":[{"offerId":sku,"categoryId":target,"parameterValues":vals}]}
 assert set(body)=={"offersContent"} and set(body["offersContent"][0])=={"offerId","categoryId","parameterValues"}
 rec={"sku":sku,"kind":kind,"status":"submission_started","before":clean_snapshot(m,c,st),"before_protected":pre,"target":target,"payload":body}
 d["operations"].append(rec);d["external_writes"]+=1;save(d,True)
 try:
  result=s.post(f"/v2/businesses/{biz}/offer-cards/update",body,retries=1)
  rec["response"]=result;rec["status"]="submitted";save(d,True)
  errs=[x.get("errors") for x in result.get("results",[]) if isinstance(x,dict) and x.get("errors")]
  if errs:raise ValueError("Item errors: "+str(errs)[:300])
  for attempt in range(8):
   if attempt:time.sleep(6)
   m2,c2,st2=s.live(biz,cam,sku);after=protected(m2,c2,st2)
   diff=[k for k in pre if pre[k]!=after[k]]
   changed_group=s.identity(m2,c2)!=s.identity(m,c)
   expected={k:vs for k,vs in beforeg.items() if k!=p["parameterId"]}
   actual={k:vs for k,vs in s.groups(c2).items() if k!=p["parameterId"]}
   same_other=all(sorted(actual.get(k,[]),key=s.canon)==sorted(vs,key=s.canon) for k,vs in expected.items()) and not any(k not in expected and any(v.get("value") or v.get("valueId") for v in vs) for k,vs in actual.items())
   good_value=value_matches(c2,p)
   actual_cat=s.catid(m2,c2)
   rec["after"]=clean_snapshot(m2,c2,st2)
   rec["checks"]={"protected_diff":diff,"other_attributes_unchanged":same_other,"group_preserved":not changed_group,"category_verified":actual_cat==target,"type_verified":good_value,"card_status":c2.get("cardStatus"),"campaign_status":st2.get("status")}
   if diff or not same_other:rec["status"]="protected_drift";break
   if actual_cat==target and good_value and c2.get("cardStatus")=="HAS_CARD_CAN_UPDATE":rec["status"]="verified";break
   if c2.get("cardStatus") in ("HAS_CARD_CAN_UPDATE_ERRORS","NO_CARD_ERRORS"):rec["status"]="review";break
  else:rec["status"]="pending_or_unchanged"
 except Exception as e:rec["status"]="uncertain_no_retry";rec["error"]=str(e)[:400]
 save(d,True)
 return rec
def main():
 if OUT.exists():
  oldout=json.loads(OUT.read_text())
  if oldout.get("external_writes"):raise ValueError("Existing operation journal; no blind rerun")
 old=json.loads(OLD.read_text())
 biz,cam=s.account()
 d={"platform":"Yandex Market","business_id":biz,"campaign_id":cam,"started_utc":now(),"external_writes":0,"status":"started","errors":{},"readback":{},"operations":[],"blocked":{}}
 save(d)
 s.read_tree()
 mm,cc,ss=read_all(biz,cam)
 d["initial_counts"]=dict(collections.Counter(x.get("status") for x in ss.values()))
 d["catalog_count"]=len(mm)
 master=s.wsrows(ROOT/"data/master_control.xlsx")
 # Preserve exact model ID as well as existing seller SKU/group, and check UGC.
 for b in old["batches"]:
  sku=b["sku"]
  try:
   m,c,st=mm[sku],cc[sku],ss[sku]
   pre=b["before"];mp=pre["mapping"];cp=pre["card"];sp=pre["campaign"]
   before=protected(mp,cp,sp);after=protected(m,c,st)
   diff=[k for k in before if before[k]!=after[k]]
   intended=b["payload"]["offerMappings"][0]["offer"]
   if b["kind"]=="attribute":
    oid=60134505
    goal=next(v["value"] for v in intended["parameterValues"] if v["parameterId"]==oid)
    done=s.existing_text(c,oid)==[goal]
    other=normg(cp,[oid])==normg(c,[oid])
   else:
    done=s.catid(m,c)==int(intended["marketCategoryId"]);other=normg(cp)==normg(c)
   d["readback"][sku]={"kind":b["kind"],"intended_verified":done,"other_attributes_preserved":other,"protected_diff":diff,"moderation":c.get("cardStatus"),"status":st.get("status"),"category":s.catid(m,c),"model":model(m,c),"ugc":m["offer"].get("ugc"),"oem":s.existing_text(c,60134505),"result":"verified" if done and not diff and other and c.get("cardStatus")=="HAS_CARD_CAN_UPDATE" else "review"}
  except Exception as e:d["readback"][sku]={"result":"read_error","reason":str(e)[:350]}
 d["disabled"]={}
 for sku,st in ss.items():
  if st.get("status") in ("DISABLED_AUTOMATICALLY","NO_STOCKS","DISABLED_BY_PARTNER"):
   try:
    x=clean_snapshot(mm[sku],cc[sku],st)
    x["master_stock"]=master.get(sku,{}).get("Остаток, шт.")
    x["master_cost"]=master.get(sku,{}).get("Закупка, ₽")
    x["publication_cause"]="duplicate_variant" if any("Дубль варианта" in str(z) for z in (st.get("warnings") or [])+(cc[sku].get("warnings") or [])) else ("no_stocks" if st.get("status")=="NO_STOCKS" else ("owner_disabled" if st.get("status")=="DISABLED_BY_PARTNER" else "reason_not_exposed"))
    d["disabled"][sku]=x
   except Exception as e:d["disabled"][sku]={"reason":str(e)[:300]}
 # True group memberships, including archived offers. No group writes.
 allids=set(mm)|{m["offer"]["offerId"] for m in s.get_mappings(biz,True)}
 allcards=s.index(s.batched_read(biz,"offer-cards",list(allids)),lambda x:x["offerId"])
 membership=collections.defaultdict(list)
 if set(allcards)!=allids:d["errors"]["groups"]="Incomplete archive/active group census"
 else:
  for sku,c in allcards.items():
   if c.get("groupId"):membership[str(c["groupId"]).strip().casefold()].append(sku)
 d["groups"]={k:sorted(v) for k,v in membership.items() if len(v)>1}
 d["variant_blockers"]={"distinctive": [{"id":x["id"],"name":x["name"]} for x in s.schema(biz,TARGET)["parameters"] if x.get("distinctive")],"resolution":"Existing variant groups are protected: no false alternate numbers or quantities, no group detach without confirmed UGC retention."}
 d["inventory_reconciliation"]=[{"sku":k,"market_status":v.get("campaign_status"),"master_stock":v.get("master_stock")} for k,v in d["disabled"].items()]
 # Audit actual 29-day orders with costs, not a claim of settled profit.
 try:
  orders=ym.get_orders_since(days_back=29)
  costs={}
  totals=collections.defaultdict(lambda:{"delivered_units":0,"item_price_x_qty":0.0,"cost_of_goods":0.0,"missing_cost_units":0,"cancelled_units":0})
  for o in orders:
   for it in o.get("items",[]):
    sku=it.get("offerId") or it.get("shopSku")
    if not sku:continue
    q=int(it.get("count") or 0);r=totals[sku]
    if o.get("status")=="CANCELLED":r["cancelled_units"]+=q;continue
    if o.get("status")!="DELIVERED":continue
    r["delivered_units"]+=q
    price=it.get("price")
    if isinstance(price,(float,int)):r["item_price_x_qty"]+=float(price)*q
    else:r["missing_price_units"]=r.get("missing_price_units",0)+q
    cost=master.get(sku,{}).get("Закупка, ₽")
    if isinstance(cost,(float,int)) and cost>0:r["cost_of_goods"]+=float(cost)*q
    else:r["missing_cost_units"]+=q
  d["finance"]={"period":"29 days through live order endpoint; creation-date selection","orders":len(orders),"status_counts":dict(collections.Counter(o.get("status") for o in orders)),"sku":dict(totals),"note":"Delivered order-item prices and purchase cost only; commissions, subsidies, returns, fulfillment and tax not reconciled; not net profit."}
 except Exception as e:d["errors"]["finance"]=str(e)[:400]
 d["status"]="readback_complete";save(d,True)
 # First category test on previously attempted existing single-card kit with no reviews.
 # Previous transfer was acknowledged but actual mapping did not move. No same endpoint retry.
 cat_success=False;drift=False
 if not d["errors"].get("groups"):
  for sku in CAT_TYPES:
   row=old["registry"].get(sku,{})
   plan=row.get("category_plan",{})
   if not plan.get("new"):continue
   if sku=="Ea888gen3":continue # independent OEM update first; category requires separate check
   if not cat_success and sku!="porshni-usil-pylniki-kryshki":
    d["blocked"][sku]="Await verified alternate category endpoint canary";continue
   try:
    m,c,st=s.live(biz,cam,sku)
    if s.catid(m,c)==plan["new"]:d["blocked"][sku]="Already correct; no repeat";continue
    if s.catid(m,c)!=plan.get("old"):raise ValueError("Category drift")
    rec=mutation(d,biz,cam,sku,plan["new"],type_value(s.definition_map(biz,plan["new"]),CAT_TYPES[sku]),"category",(m,c,st),1,membership)
    if sku=="porshni-usil-pylniki-kryshki":cat_success=rec["status"]=="verified"
    if rec["status"]=="protected_drift":drift=True;break
   except Exception as e:d["blocked"][sku]=str(e)[:350]
 # Independently fill missing physical type on already correctly categorized, published singleton
 # offers; stop on genuine protected drift. Existing groups/known values untouched.
 if not drift and not d["errors"].get("groups"):
  for sku,(val,needle) in TYPE_PLAN.items():
   try:
    m,c,st=s.live(biz,cam,sku)
    if st.get("status")!="PUBLISHED" or s.catid(m,c)!=TARGET:continue
    if s.groups(c).get(21194330):continue
    if needle not in m["offer"]["name"].lower() or not master.get(sku):raise ValueError("Exact master/title not confirmed")
    rec=mutation(d,biz,cam,sku,TARGET,type_value(s.definition_map(biz,TARGET),val),"type",(m,c,st),1,membership)
    if rec["status"]=="protected_drift":drift=True;break
   except Exception as e:d["blocked"][sku]=str(e)[:350]
 # Final actual publication snapshot (no changes to disabled, inventory or groups).
 try:
  m2,c2,st2=read_all(biz,cam)
  d["final_counts"]=dict(collections.Counter(x.get("status") for x in st2.values()))
  d["publication_recovered"]=[k for k,v in ss.items() if v.get("status")=="DISABLED_AUTOMATICALLY" and st2.get(k,{}).get("status")=="PUBLISHED"]
  d["publication_lost"]=[k for k,v in ss.items() if v.get("status")=="PUBLISHED" and st2.get(k,{}).get("status")!="PUBLISHED"]
 except Exception as e:d["errors"]["final_snapshot"]=str(e)[:350]
 d["summary"]={"readback":dict(collections.Counter(x.get("result") for x in d["readback"].values())),"operations":dict(collections.Counter((x["kind"]+":"+x["status"]) for x in d["operations"])),"blocked":len(d["blocked"])}
 d["status"]="protected_drift_review" if drift else "processed_with_review"
 d["finished_utc"]=now();save(d,True);print(json.dumps(d["summary"],ensure_ascii=False))
if __name__=="__main__":main()
