"""Market-only final continuation. Existing-card changes only; fail closed.
No prices, stocks, ads, descriptions, images, brands, group edits or SKU creation.
Reuses existing account, schema, review and protected snapshot guards.
"""
import collections, copy, datetime, json, sys, time, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import market_full_seo_20261125 as s
import market_growth_recovery_20261125 as r
import market_growth_decision_report as fin
import yandex_market_client as ym

OUT=ROOT/"data/market_final_optimization_20261125.json"
DEC=ROOT/"data/market_growth_decision_20261125.json"
PLAN=ROOT/"data/market_growth_next_plan_20261125.json"
REC=ROOT/"data/market_growth_recovery_20261125.json"
CAT={
 "Ea888gen3":("обратный клапан масляного фильтра","обратный клапан"),
 "0AM927769N":("прокладка","прокладки"),
 "0am325091e-set":("комплект","комплект"),
 "0am325091f-set":("комплект","комплект"),
 "podshipnik-vilki-6":("подшипник","подшипник"),
 "korpus-filtra-0bh325159":("корпус","корпус"),
 "salnikprivod-l":("сальник","сальник"),
 "salnikprivod-r":("сальник","сальник"),
 "salnikvhod":("сальник","сальник"),
 "vtulki-shtokov-2sht":("втулка","втулки"),
 "pylniki-shtokov-orig":("пыльник","пыльники"),
 "plita-filtr-salniki-orig":("комплект","плита"),
 "tolkateli-salniki-alum-komplekt":("комплект","толкатели"),
 "plita-filtr-orig":("комплект","плита"),
 "plita-filtr-pylniki-orig":("комплект","плита"),
}
# Explicit physical types; only fill a missing field, never replace a correct value.
TYPES={
 "6t30-solenoids-gen2":("соленоид","соленоид"),
 "6t30-tcm-gen1":("блок управления","блок управления"),
 "6t30-tcm-gen2":("блок управления","блок управления"),
 "01X301127C":("болт","болт"),
 "0am325025L":("крышка","крышка"),
 "0am325025p":("соленоид","соленоид"),
 "0am325066ac":("манжета","сальник"),
 "0am325066ae":("гидроплита","гидроплита"),
 "0b5":("фильтр АКПП","фильтр"),
 "0BH325":("корпус","корпус"),
 "0BH325183B":("корпус","корпус"),
 "122390AKAF":("пыльник","пыльник"),
 "filtr-setka-0b5-orig":("фильтр АКПП","фильтр"),
 "jf015":("комплект","комплект"),
 "plastina-solenoid-dq200":("пластина","пластина"),
 "prokladka-kryshki-dq200":("прокладка","прокладка"),
 "pylniki-shtokov-orig-v2":("пыльник","пыльник"),
 "remkomplekt-shtokov-orig-v2":("ремкомплект","ремкомплект"),
 "salnik-shtoka-orig":("сальник","сальник"),
 "salnik-shtoka-orig-v2":("сальник","сальник"),
 "salniki-shtokov-orig":("сальник","сальник"),
 "salnikvihod":("сальник","сальник"),
 "separatornaya-plastina-dq200":("пластина","пластина"),
 "0am325025Y":("болт","болт"),
 "0AM325473":("соленоид","соленоид"),
 "0am325025e":("заглушка","заглушка"),
}
TYPES={**r.TYPE_PLAN,**TYPES}
# Exact old category fields with known equivalent textual destinations, only
# for singleton category moves; no inferred vehicle models or fake OEMs.
TEXT_MAP={23820510:(57046341,"Вид техники"),16613317:(57046341,"Марка автомобиля"),
          14871214:(57046341,"Название цвета от производителя"),
          13887626:(57046341,"Цвет товара"),
          16382542:(7351754,"Комплектация"),
          34857690:(57046341,"Вид расходников")}
OLD_TYPE_EXPECT={
 "0am325091e-set":36778538,"0am325091f-set":36778538,
 "podshipnik-vilki-6":36778774,"korpus-filtra-0bh325159":38075950,
 "salnikprivod-l":33760790,"salnikprivod-r":33760790,"salnikvhod":33760790,
}
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d,persist=False):
 d["updated_at"]=now()
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n");tmp.replace(OUT)
 if persist:subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT.relative_to(ROOT))],cwd=ROOT,check=True)
def norm(g):
 # Ignore only dictionary display labels when stable valueId is supplied.
 return {int(k):sorted([s.canon({a:b for a,b in v.items() if not (a=="value" and v.get("valueId"))}) for v in vs]) for k,vs in g.items() if any(v.get("value") or v.get("valueId") for v in vs)}
def grp(c):return str(c.get("groupId") or "").strip().casefold()
def append_text(g,k,label,values,defs):
 if k not in defs or defs[k].get("type")!="TEXT" or defs[k].get("distinctive"):raise ValueError("No exact text destination")
 existing=g.get(k,[])
 if len(existing)>1 or any(v.get("valueId") or v.get("unitId") for v in existing):raise ValueError("Text destination not scalar")
 txt=str(existing[0].get("value") or "") if existing else ""
 for v in values:
  if v.get("valueId") or v.get("unitId") or not isinstance(v.get("value"),str):raise ValueError("Cannot safely translate dictionary/unit field")
  line=label+": "+v["value"]
  if line not in txt.split("\n"):txt=(txt+"\n" if txt else "")+line
 nv={"parameterId":k,"value":txt}
 if not s.valid_value(nv,defs[k]):raise ValueError("Destination text limit")
 g[k]=[nv]
def make_candidate(biz,sku,m,c,st,master,members,previous,category=None):
 o=m["offer"];oldcat=s.catid(m,c);old=s.groups(c);new=copy.deepcopy(old)
 if sku in previous:raise ValueError("Previously submitted; readback only")
 if o.get("archived") or st.get("status")=="DISABLED_BY_PARTNER":raise ValueError("Archived or manually disabled")
 if c.get("cardStatus") not in ("HAS_CARD_CAN_UPDATE","HAS_CARD_CAN_UPDATE_ERRORS"):raise ValueError("Card not editable / processing")
 if grp(c) and members.get(grp(c))!=[sku]:raise ValueError("Non-singleton group protected")
 title=str(o.get("name") or "").casefold()
 if not master.get(sku):raise ValueError("Exact master SKU missing")
 if category:
  target=int(category);val,needle=CAT[sku]
  if oldcat==target:raise ValueError("Category already correct")
  if o.get("ugc"):raise ValueError("Existing UGC metadata protected")
  reviews=r.review_count(biz,sku)
  if not reviews.get("available") or not reviews.get("complete") or reviews.get("count_at_least")!=0:raise ValueError("Cannot verify zero reviews")
  if not s.TREE.get(target,{}).get("leaf"):raise ValueError("Target not verified leaf")
 else:
  target=oldcat;val,needle=TYPES[sku];reviews=None
  if target!=s.TRANS:raise ValueError("Type plan only for transmission category")
  if old.get(21194330):raise ValueError("Type already present")
 if needle.casefold() not in title:raise ValueError("Exact live title does not confirm type")
 defs=s.definition_map(biz,target)
 typ=r.type_value(defs,val)
 if not s.valid_value(typ,defs[21194330]):raise ValueError("Invalid destination type")
 remapped=[]
 if category:
  # Only replace known legacy enum when exact title & reviewed original enum agree.
  prior_type=new.get(21194330,[])
  if prior_type:
   if len(prior_type)!=1:raise ValueError("Multiple old types")
   v=prior_type[0]
   if not s.valid_value(v,defs[21194330]):
    if OLD_TYPE_EXPECT.get(sku)!=v.get("valueId"):raise ValueError("Unreviewed incompatible type")
   elif (v.get("valueId")!=typ.get("valueId") or not typ.get("valueId")) and str(v.get("value") or "").casefold()!=val.casefold():
    raise ValueError("Different existing valid type, review")
  for k in list(new):
   if k==21194330:continue
   if k not in defs:
    vs=[v for v in new[k] if v.get("value") or v.get("valueId")]
    if vs:
     if k not in TEXT_MAP:raise ValueError("Unmapped nonempty source parameter "+str(k))
     dest,label=TEXT_MAP[k]
     append_text(new,dest,label,vs,defs);remapped.append({"source":k,"destination":dest,"values":vs})
    del new[k]
   elif any(not s.valid_value(v,defs[k]) for v in new[k] if v.get("value") or v.get("valueId")):
    raise ValueError("Destination rejects existing parameter "+str(k))
 else:
  # No change to any existing parameter during type-only edits.
  if any(k not in defs and any(v.get("value") or v.get("valueId") for v in vs) for k,vs in old.items()):raise ValueError("Existing schema mismatch")
 new[21194330]=[typ]
 if any(x.get("required") and not new.get(k) for k,x in defs.items()):raise ValueError("Missing required destination attribute")
 if any(not s.valid_value(v,defs[k]) for k,vs in new.items() if k in defs for v in vs if v.get("value") or v.get("valueId")):raise ValueError("Invalid complete payload")
 payload={"offerId":sku,"categoryId":target,"parameterValues":[v for k,vs in new.items() if k in defs for v in vs]}
 assert set(payload)=={"offerId","categoryId","parameterValues"}
 return {"sku":sku,"kind":"category" if category else "type","status":"ready","target":target,"before_category":oldcat,
         "before_protected":r.protected(m,c,st),"before_params":old,"payload":payload,"expected":new,
         "reviews_before":reviews,"remapped":remapped}
def state(biz,cam,ids):
 m=s.index(s.get_mappings(biz),lambda x:x["offer"]["offerId"])
 c=s.index(s.batched_read(biz,"offer-cards",list(m)),lambda x:x["offerId"])
 st=s.campaign_status(cam,list(m))
 if set(m)!=set(c) or set(m)!=set(st) or not set(ids)<=set(m):raise ValueError("Incomplete readback")
 return m,c,st
def review(d,ops,m,c,st,biz):
 drift=False
 for op in ops:
  k=op["sku"]
  try:
   pre=op["before_protected"];after=r.protected(m[k],c[k],st[k])
   diff=[f for f in pre if pre[f]!=after.get(f)]
   actual=norm(s.groups(c[k]));expected=norm({int(k):v for k,v in op["expected"].items()})
   before=norm({int(k):v for k,v in op["before_params"].items()})
   cat=s.catid(m[k],c[k])
   op["readback"]={"protected_diff":diff,"category":cat,"parameters_match":actual==expected,"status":st[k].get("status"),"card_status":c[k].get("cardStatus")}
   # A currently old mapping during moderation is pending, not a reason to retry.
   if diff or (actual!=expected and actual!=before):
    op["status"]="protected_drift_review";drift=True
   elif cat==op["target"] and actual==expected and c[k].get("cardStatus")=="HAS_CARD_CAN_UPDATE":
    if op["kind"]=="category":
     rr=r.review_count(biz,k);op["reviews_after"]=rr
     if rr!=op["reviews_before"]:op["status"]="review_count_unconfirmed";drift=True;continue
    op["status"]="verified"
   elif op["status"] not in ("uncertain_no_retry","api_item_error"):
    op["status"]="pending_readback"
  except Exception as e:op["readback_error"]=str(e)[:300];op["status"]="uncertain_no_retry"
 return drift
def economics(row):
 a=row.get("current_economics"); b=row.get("optimal_economics")
 if not a:return {"purchase_cost":row.get("purchase_cost"),"status":"cost_or_tariff_missing"}
 x=a["contribution_before_ads_returns_overhead"]
 z={"purchase_cost":row["purchase_cost"],"current":a,"cac_break_even_ceiling_before_returns_overhead":max(0,x),
    "note":"Not actual net profit or safe ad budget. Subtract real returns, overhead and desired profit."}
 if b:
  y=b["contribution_before_ads_returns_overhead"]
  z["market_threshold_scenario"]=b
  z["required_unit_growth_pct"]=round(100*(x/y-1),2) if y>0 else None
 return z
def run():
 biz,cam=s.account()
 if biz!=194752944 or int(cam)!=141377963:raise ValueError("Wrong account")
 before=json.loads(DEC.read_text());plan=json.loads(PLAN.read_text());prev=json.loads(REC.read_text())
 if any(x.get("platform")!="Yandex Market" for x in (before,plan,prev)):raise ValueError("Wrong report scope")
 # Refuse to repeat previously submitted operations, even where no final readback.
 prior={x["sku"] for x in prev.get("operations",[]) if x.get("status")!="already_correct"}
 old=json.loads(OUT.read_text()) if OUT.exists() else {}
 prior|=set(old.get("submitted_skus",[]))
 prior|={x["sku"] for x in old.get("operations",[]) if x.get("status") not in ("ready","preflight_only")}
 d={"platform":"Yandex Market","business_id":biz,"campaign_id":int(cam),"started_at":now(),"external_write_requests":0,"operations":[],"submitted_skus":sorted(prior),"category_review":{},"type_review":{},"rows":{},"status":"preflight"}
 save(d)
 m,c,st=state(biz,cam,[])
 # Fail closed on incomplete active+archived membership.
 ids=set(m)|{x["offer"]["offerId"] for x in s.get_mappings(biz,True)}
 allc=s.index(s.batched_read(biz,"offer-cards",sorted(ids)),lambda x:x["offerId"])
 if set(allc)!=ids:raise ValueError("Incomplete archive census")
 members=collections.defaultdict(list)
 for k,v in allc.items():
  if grp(v):members[grp(v)].append(k)
 s.read_tree()
 master=s.wsrows(ROOT/"data/master_control.xlsx")
 d["before_counts"]=dict(collections.Counter(x.get("status") for x in st.values()))
 ops=[]
 cat_skus={x["sku"] for x in plan["category_remaining"]}
 for x in plan["category_remaining"]:
  k=x["sku"]
  try:
   if k not in m:raise ValueError("Not in active catalog")
   if s.catid(m[k],c[k])==x["target"]:raise ValueError("Already correct")
   if k not in CAT:raise ValueError("Requires group/UGC or special semantic review")
   op=make_candidate(biz,k,m[k],c[k],st[k],master,members,prior,x["target"]);ops.append(op)
   d["category_review"][k]="ready"
  except Exception as e:d["category_review"][k]=str(e)[:300]
 for k in TYPES:
  try:
   if k not in m or k in cat_skus:continue
   op=make_candidate(biz,k,m[k],c[k],st[k],master,members,prior);ops.append(op);d["type_review"][k]="ready"
  except Exception as e:d["type_review"][k]=str(e)[:300]
 # Prioritize blocked items and then safe category migrations. No concurrent
 # writes to an existing variant group. Keep checkpoints prior to each POST.
 ops.sort(key=lambda o:(st[o["sku"]].get("status")!="DISABLED_AUTOMATICALLY",o["kind"]!="category",o["sku"]))
 d["operations"]=ops
 save(d,True)
 stop=False
 for i in range(0,len(ops),4):
  batch=ops[i:i+4]
  # Refresh identity, current parameters and protected content before each batch.
  mm,cc,ss=state(biz,cam,[x["sku"] for x in batch])
  ready=[]
  for op in batch:
   k=op["sku"]
   try:
    if r.protected(mm[k],cc[k],ss[k])!=op["before_protected"] or norm(s.groups(cc[k]))!=norm(op["before_params"]) or s.catid(mm[k],cc[k])!=op["before_category"] or cc[k].get("cardStatus") not in ("HAS_CARD_CAN_UPDATE","HAS_CARD_CAN_UPDATE_ERRORS") or ss[k].get("status")=="DISABLED_BY_PARTNER":
     raise ValueError("Live drift since preflight")
    op["status"]="submission_started";ready.append(op)
   except Exception as e:op["status"]="preflight_only";op["error"]=str(e)
  if not ready:continue
  d["submitted_skus"]=sorted(set(d["submitted_skus"])|{x["sku"] for x in ready})
  d["external_write_requests"]+=1;save(d,True)
  try:
   response=s.post(f"/v2/businesses/{biz}/offer-cards/update",{"offersContent":[x["payload"] for x in ready]},retries=1)
   for op in ready:op["status"]="submitted"
   for item in response.get("results",[]):
    for op in ready:
     if item.get("offerId")==op["sku"] and item.get("errors"):op["status"]="api_item_error";op["error"]=item["errors"]
  except Exception as e:
   for op in ready:op["status"]="uncertain_no_retry";op["error"]=str(e)[:300]
  save(d,True)
  time.sleep(8)
  mm,cc,ss=state(biz,cam,[x["sku"] for x in ready])
  stop=review(d,ready,mm,cc,ss,biz)
  save(d,True)
  if stop:break
 # Final readback covers the entire catalog, including previous pending items.
 m2,c2,st2=state(biz,cam,[])
 review(d,[x for x in ops if x["status"] in ("submitted","pending_readback","uncertain_no_retry","api_item_error")],m2,c2,st2,biz)
 for k in m2:
  row=before["rows"].get(k,{})
  z=r.clean_snapshot(m2[k],c2[k],st2[k])
  blocked=st2[k].get("status")=="DISABLED_AUTOMATICALLY"
  z["publication_reason"]=("duplicate_variant_warning" if any("Дубль варианта" in str(v) for v in (z["warnings"] or [])+(z["card_warnings"] or [])) else ("API_does_not_expose_specific_cause" if blocked and not z["errors"] and not z["card_errors"] else ("inspect_returned_errors" if blocked else None)))
  z["financial_snapshot"]=economics(row)
  z["stock_previous_snapshot"]=row.get("stock")
  z["price_competition_previous_snapshot"]=row.get("price_competition")
  z["previous_funnel_undated"]=row.get("baseline")
  z["content_rating_below_50"]=isinstance(z["rating"],(int,float)) and z["rating"]<50
  z["component_oem_review"]=k in s.COMPONENTS and bool(s.existing_text(c2[k],60134505))
  if k in ("6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2"):z["component_oem_review"]=True
  d["rows"][k]=z
 d["final_counts"]=dict(collections.Counter(x.get("status") for x in st2.values()))
 d["recovered"]=[k for k in st if st[k].get("status")=="DISABLED_AUTOMATICALLY" and st2.get(k,{}).get("status")=="PUBLISHED"]
 d["lost_publication"]=[k for k in st if st[k].get("status")=="PUBLISHED" and st2.get(k,{}).get("status")!="PUBLISHED"]
 d["summary"]=dict(collections.Counter(x["kind"]+":"+x["status"] for x in ops))
 d["status"]="protected_drift_review" if stop else "processed_with_review"
 d["finished_at"]=now();save(d,True)
 print(json.dumps({"status":d["status"],"before":d["before_counts"],"after":d["final_counts"],"summary":d["summary"],"recovered":d["recovered"],"lost":d["lost_publication"]},ensure_ascii=False))
if __name__=="__main__":run()
