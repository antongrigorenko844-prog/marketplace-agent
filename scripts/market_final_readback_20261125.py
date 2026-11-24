"""Strict Market-only readback, exact false-OEM cleanup and safe remaining singleton migrations.
Uses existing protection and archived-group census. Never changes price, stock, group,
brand, description, media, existing SKU or composition; no automatic repeat of mutations.
"""
import collections, copy, datetime, json, sys, time, subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import market_full_seo_20261125 as s
import market_growth_recovery_20261125 as r
import market_final_optimization_20261125 as f
import yandex_market_client as ym
OUT=ROOT/"data/market_final_readback_20261125.json"
DATA=ROOT/"data"
OEM=60134505
# Exact current false values: numbers of the compatible board, not the separate part.
# No replacement guesses. Compatibility must already exist in a factual field.
CLEAR={
 "chip-02e927770":("02E927770AL","чип электронной платы","02E927770AL"),
 "chip-0am927769d":("0AM927769D","чип электронной платы","0AM927769D"),
 "datchik-peredach-dq200":("0AM927769D","датчик положения передач","0AM927769D"),
 "datchik-scepleniya-dq200":("0AM927769D","датчик хода сцепления","0AM927769D"),
 "fpc-shleif-dq200":("0AM927769D","fpc-шлейф","0AM927769D"),
 "kolodka-solenoidov-dq200":("0AM927769D","колодка разъёмов","0AM927769D"),
 "6t30-solenoids-gen2":("6t30-solenoids-gen2","соленоид",""),
 "6t30-tcm-gen1":("6t30-tcm-gen1","блок управления",""),
 "6t30-tcm-gen2":("6t30-tcm-gen2","блок управления",""),
}
# Old values verified in prior snapshots. Resolve enum display text in live source
# schema and retain it as a labeled fact; never pass old enum IDs to the new category.
CATEGORY={
 "salnikprivod-r":(76973709,"сальник",33760790,"сальник привода правый"),
 "salnikprivod-l":(76973709,"сальник",33760790,"сальник привода левый"),
 "salnikvhod":(76973709,"сальник",33760790,"сальник входного вала"),
 "pylniki-shtokov-orig":(71340918,"пыльник",None,"пыльники штоков"),
 "plita-filtr-pylniki-orig":(17701923,"комплект",34068515,"плита"),
 "plita-filtr-orig":(90442,"комплект",34057636,"плита"),
 "tolkateli-salniki-alum-komplekt":(65422456,"комплект",None,"толкатели"),
 "plita-filtr-salniki-orig":(13477846,"комплект",None,"плита"),
}
# Source-only fields to preserve in text. OE references remain references, not a new OEM.
MOVE={**f.TEXT_MAP,27143691:(57046341,"Назначение из прежней категории"),
      83923646:(57046341,"Ранее указанные справочные OE-коды")}
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d,persist=False):
 d["updated_at"]=now()
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n");tmp.replace(OUT)
 if persist:subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT.relative_to(ROOT))],cwd=ROOT,check=True)
def load(name):
 return json.loads((DATA/name).read_text())
def normalize(g):return f.norm({int(k):v for k,v in g.items()})
def match(g,p):
 vals=g.get(int(p["parameterId"]),[])
 if not p.get("value") and not p.get("valueId"):return not any(v.get("value") or v.get("valueId") for v in vals)
 return len(vals)==1 and normalize({p["parameterId"]:vals})==normalize({p["parameterId"]:[p]})
def protected_diff(pre,after):
 return [k for k in pre if pre[k]!=after.get(k)]
def snapshot(biz,cam):
 m,c,st=r.read_all(biz,cam)
 return m,c,st
def scope(biz,m,c,st,sku,master,members):
 if sku not in m or sku not in master:raise ValueError("No exact master/live SKU")
 o=m[sku]["offer"];card=c[sku]
 if o.get("archived") or st[sku].get("status")=="DISABLED_BY_PARTNER":raise ValueError("Archived or intentionally disabled")
 if card.get("cardStatus") not in ("HAS_CARD_CAN_UPDATE","HAS_CARD_CAN_UPDATE_ERRORS"):raise ValueError("Moderation pending or not editable")
 if f.grp(card) and members.get(f.grp(card))!=[sku]:raise ValueError("Non-singleton group protected")
 return o,card
def enum_text(v,definition):
 if not definition:raise ValueError("Missing original schema")
 if v.get("unitId"):raise ValueError("Unreviewed source unit")
 val=v.get("value")
 if v.get("valueId"):
  hits=[x for x in definition.get("values",[]) if x.get("id")==v["valueId"]]
  if len(hits)!=1 or not hits[0].get("value"):raise ValueError("Cannot resolve original enum "+str(v["valueId"]))
  resolved=hits[0]["value"]
  if val and str(val).casefold()!=str(resolved).casefold():raise ValueError("Original enum label mismatch")
  val=resolved
 if not isinstance(val,str) or not val.strip():raise ValueError("No original textual value")
 return val
def plan_category(biz,sku,o,card):
 oldcat,typ,old_type,needle=CATEGORY[sku]
 # preserve review/model guards from existing migration code.
 if o.get("ugc"):raise ValueError("Existing UGC metadata protected")
 rev=r.review_count(biz,sku)
 if not rev.get("available") or not rev.get("complete") or rev.get("count_at_least")!=0:raise ValueError("Zero reviews not confirmed")
 if needle not in o.get("name","").casefold():raise ValueError("Exact physical title mismatch")
 source=s.definition_map(biz,oldcat);dest=s.definition_map(biz,s.TRANS)
 old=s.groups(card);expected=copy.deepcopy(old)
 before_type=old.get(21194330,[])
 if old_type is None:
  if before_type:raise ValueError("Unexpected old type")
 elif len(before_type)!=1 or before_type[0].get("valueId")!=old_type:raise ValueError("Old type changed")
 p=r.type_value(dest,typ);expected[21194330]=[p];changes={21194330:[p]};moved=[]
 for k in list(old):
  if k==21194330:continue
  vs=old[k]
  if k in dest:
   if any(not s.valid_value(v,dest[k]) for v in vs if v.get("value") or v.get("valueId")):raise ValueError("Incompatible common attribute "+str(k))
   continue
  active=[v for v in vs if v.get("value") or v.get("valueId")]
  if active:
   if k not in MOVE:raise ValueError("Unreviewed source field "+str(k))
   target,label=MOVE[k]
   # Keep exact confirmed source text, including original values of color/vehicle fields.
   decoded=[{"value":enum_text(v,source.get(k))} for v in active]
   f.append_text(expected,target,label,decoded,dest);changes[target]=expected[target]
   moved.append({"source":k,"destination":target,"values":decoded})
  expected.pop(k,None)
 if any(x.get("required") and not expected.get(k) for k,x in dest.items()):raise ValueError("Destination mandatory field absent")
 # Official API retains common category values; send only changed attributes.
 return changes,expected,moved,rev
def compare_previous(biz,m,c,st):
 old=r.OLD if hasattr(r,"OLD") else DATA/"market_full_seo_20261125.json"
 decision=load("market_growth_decision_20261125.json")
 rec=load("market_growth_recovery_20261125.json");last=load("market_final_optimization_20261125.json")
 out={}
 for op in rec.get("operations",[]):
  k=op["sku"]
  try:
   current=s.groups(c[k]);want=next(p for p in op["payload"]["offersContent"][0]["parameterValues"] if p["parameterId"]==21194330)
   pre=op["before_protected"];diff=protected_diff(pre,r.protected(m[k],c[k],st[k]))
   # Recovery snapshots omit the old value of type; compare other fields.
   oldp=s.groups(op["before"]);other=normalize({i:v for i,v in oldp.items() if i!=21194330})==normalize({i:v for i,v in current.items() if i!=21194330})
   ok=match(current,want) and other and not diff and s.catid(m[k],c[k])==op["target"]
   out[k]={"kind":"type","value_matches":match(current,want),"other_attributes_preserved":other,"protected_diff":diff,"status":st[k].get("status"),"card_status":c[k].get("cardStatus"),"verified":ok and c[k].get("cardStatus")=="HAS_CARD_CAN_UPDATE"}
  except Exception as e:out[k]={"kind":"type","error":str(e)[:300],"verified":False}
 for op in last.get("operations",[]):
  k=op["sku"]
  try:
   current=s.groups(c[k]);expected=normalize(op["expected"]);diff=protected_diff(op["before_protected"],r.protected(m[k],c[k],st[k]))
   ok=normalize(current)==expected and not diff and s.catid(m[k],c[k])==op["target"]
   out[k]={"kind":op["kind"],"category":s.catid(m[k],c[k]),"target":op["target"],"parameters_match":normalize(current)==expected,"protected_diff":diff,"status":st[k].get("status"),"card_status":c[k].get("cardStatus"),"verified":ok and c[k].get("cardStatus")=="HAS_CARD_CAN_UPDATE"}
  except Exception as e:out[k]={"kind":op["kind"],"error":str(e)[:300],"verified":False}
 return out
def verify(op,m,c,st,biz):
 k=op["sku"];after=r.protected(m[k],c[k],st[k]);diff=protected_diff(op["protected"],after)
 actual=normalize(s.groups(c[k]));expected=normalize(op["expected"]);old=normalize(op["old"])
 target=s.catid(m[k],c[k]);card=c[k].get("cardStatus")
 op["readback"]={"protected_diff":diff,"parameters_match":actual==expected,"category":target,"target":op["target"],"card_status":card,"campaign_status":st[k].get("status")}
 # category may show old ID before moderation even while parameters are already updated.
 if diff or actual not in (old,expected):op["status"]="protected_drift_review";return True
 if actual==expected and target==op["target"] and card=="HAS_CARD_CAN_UPDATE":
  if op["kind"]=="category":
   rev=r.review_count(biz,k);op["reviews_after"]=rev
   if rev!=op["reviews_before"]:op["status"]="reviews_unconfirmed";return True
  op["status"]="verified"
 else:op["status"]="pending_readback"
 return False
def run():
 biz,cam=s.account()
 if biz!=194752944 or int(cam)!=141377963:raise ValueError("Wrong Market account")
 old=load(OUT.name) if OUT.exists() else {}
 submitted=set(old.get("submitted",[]))
 d={"platform":"Yandex Market","business_id":biz,"campaign_id":int(cam),"started_at":now(),"status":"preflight","submitted":sorted(submitted),"operations":old.get("operations",[]),"blocked":{},"external_write_requests":0}
 m,c,st=snapshot(biz,cam)
 d["previous_readback"]=compare_previous(biz,m,c,st)
 d["before_counts"]=dict(collections.Counter(x.get("status") for x in st.values()))
 d["status"]="readback_complete";save(d,True)
 # No mutations if any previously attempted change caused protected drift.
 if any(x.get("protected_diff") for x in d["previous_readback"].values()):raise ValueError("Previous protected drift requires review")
 allids=set(m)|{x["offer"]["offerId"] for x in s.get_mappings(biz,True)}
 allc=s.index(s.batched_read(biz,"offer-cards",sorted(allids)),lambda x:x["offerId"])
 if set(allc)!=allids:raise ValueError("Incomplete active+archive group census")
 members=collections.defaultdict(list)
 for k,v in allc.items():
  if f.grp(v):members[f.grp(v)].append(k)
 master=s.wsrows(DATA/"master_control.xlsx");s.read_tree()
 priorplan=load("market_growth_next_plan_20261125.json")
 target_by_sku={x["sku"]:int(x["target"]) for x in priorplan["category_remaining"]}
 operations=[]
 for kind,items in (("oem_clear",CLEAR),("category",CATEGORY)):
  for k in items:
   key=kind+":"+k
   try:
    if key in submitted:raise ValueError("Previously submitted; readback only")
    o,card=scope(biz,m,c,st,k,master,members)
    cat=s.catid(m[k],card);oldp=s.groups(card)
    if kind=="oem_clear":
     if cat!=s.TRANS:raise ValueError("Unexpected category")
     wrong,needle,compat=CLEAR[k]
     if needle not in o.get("name","").casefold():raise ValueError("Physical part name mismatch")
     vals=s.existing_text(card,OEM)
     if not vals:raise ValueError("Already empty")
     if vals!=[wrong]:raise ValueError("OEM changed; do not delete")
     if compat and compat.casefold() not in " ".join(s.existing_text(card,57046341)).casefold():raise ValueError("Compatibility not independently retained")
     if not compat and wrong!=k:raise ValueError("Not exact internal SKU")
     defs=s.definition_map(biz,cat);field=defs.get(OEM,{})
     if field.get("type")!="TEXT" or field.get("required") or field.get("distinctive"):raise ValueError("OEM field not optional non-distinctive text")
     expected=copy.deepcopy(oldp);expected.pop(OEM,None)
     changes={OEM:[{"parameterId":OEM,"value":""}]};target=cat;moved=[];rev=None
    else:
     if cat==s.TRANS:raise ValueError("Already correct")
     if cat!=CATEGORY[k][0] or target_by_sku.get(k)!=s.TRANS:raise ValueError("Category changed or not on approved review list")
     target=s.TRANS;changes,expected,moved,rev=plan_category(biz,k,o,card)
    payload={"offerId":k,"categoryId":target,"parameterValues":[v for vs in changes.values() for v in vs]}
    op={"key":key,"sku":k,"kind":kind,"status":"ready","protected":r.protected(m[k],card,st[k]),"old":oldp,"expected":expected,"target":target,"before_category":cat,"payload":payload,"moved":moved,"reviews_before":rev}
    operations.append(op)
   except Exception as e:d["blocked"][key]=str(e)[:300]
 d["operations"]+=operations;save(d,True)
 stop=False
 # Single category changes avoid cross-item rejection; OEM-only clear can be isolated too.
 for op in operations:
  k=op["sku"]
  try:
   mm,cc,ss=s.live(biz,cam,k)
   if r.protected(mm,cc,ss)!=op["protected"] or normalize(s.groups(cc))!=normalize(op["old"]) or s.catid(mm,cc)!=op["before_category"] or cc.get("cardStatus") not in ("HAS_CARD_CAN_UPDATE","HAS_CARD_CAN_UPDATE_ERRORS") or ss.get("status")=="DISABLED_BY_PARTNER":raise ValueError("Live change since preflight")
  except Exception as e:op["status"]="preflight_changed";op["error"]=str(e)[:300];continue
  submitted.add(op["key"]);d["submitted"]=sorted(submitted);d["external_write_requests"]+=1;op["status"]="submission_started";save(d,True)
  try:
   result=s.post(f"/v2/businesses/{biz}/offer-cards/update",{"offersContent":[op["payload"]]},retries=1)
   errs=[e for x in result.get("results",[]) for e in x.get("errors",[])]
   if errs:op["status"]="api_item_error";op["error"]=errs
   else:op["status"]="submitted"
  except Exception as e:op["status"]="uncertain_no_retry";op["error"]=str(e)[:300]
  save(d,True)
  try:
   mm,cc,ss=s.live(biz,cam,k)
   stop=verify(op,{k:mm},{k:cc},{k:ss},biz)
  except Exception as e:op["readback_error"]=str(e)[:300]
  save(d,True)
  if stop:break
 m2,c2,st2=snapshot(biz,cam)
 for op in d["operations"]:
  if op.get("status") in ("submitted","pending_readback","uncertain_no_retry","submission_started"):
   try:stop=verify(op,m2,c2,st2,biz) or stop
   except Exception as e:op["readback_error"]=str(e)[:300]
 d["rows"]={}
 for k in m2:
  z=r.clean_snapshot(m2[k],c2[k],st2[k]);o=m2[k]["offer"];cs=st2[k];card=c2[k]
  z["campaign_details"]={a:cs.get(a) for a in ("available","errors","warnings","status")}
  z["business_offer_details"]={a:o.get(a) for a in ("archived","campaigns","sellingPrograms","condition","commodityCodes","certificates","weightDimensions")}
  z["card_diagnostics"]={a:card.get(a) for a in ("errors","warnings","cardStatus","recommendations","contentRating")}
  z["category_target"]=target_by_sku.get(k)
  if cs.get("status")=="DISABLED_AUTOMATICALLY":
   warnings=(cs.get("warnings") or [])+(card.get("warnings") or [])
   z["blocker"]="duplicate_variant" if any("Дубль варианта" in str(x) for x in warnings) else "specific_reason_not_returned" if not cs.get("errors") and not card.get("errors") else "explicit_API_errors"
  d["rows"][k]=z
 d["final_counts"]=dict(collections.Counter(x.get("status") for x in st2.values()))
 d["category_verified"]=[k for k,v in target_by_sku.items() if k in m2 and s.catid(m2[k],c2[k])==v]
 d["category_remaining"]=[k for k,v in target_by_sku.items() if k not in d["category_verified"]]
 d["recovered"]=[k for k,v in st.items() if v.get("status")=="DISABLED_AUTOMATICALLY" and st2.get(k,{}).get("status")=="PUBLISHED"]
 d["lost_publication"]=[k for k,v in st.items() if v.get("status")=="PUBLISHED" and st2.get(k,{}).get("status")!="PUBLISHED"]
 d["summary"]={"previous_verified":sum(x.get("verified",False) for x in d["previous_readback"].values()),"previous_count":len(d["previous_readback"]),"operations":dict(collections.Counter(x["kind"]+":"+x["status"] for x in d["operations"])),"remaining_categories":len(d["category_remaining"])}
 d["status"]="protected_drift_review" if stop else "processed_with_review";d["finished_at"]=now();save(d,True)
 print(json.dumps({"summary":d["summary"],"counts":d["final_counts"],"recovered":d["recovered"],"lost":d["lost_publication"]},ensure_ascii=False))
if __name__=="__main__":run()
