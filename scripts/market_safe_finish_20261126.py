"""Market-only exact photo append and guarded category corrections. No group, stock, price or advertising writes."""
import collections, copy, datetime, hashlib, json, subprocess, time
from pathlib import Path
import requests
import market_full_seo_20261125 as s
import market_growth_recovery_20261125 as r
OUT=Path("data/market_safe_finish_20261126.json")
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save():
 d["updated_utc"]=now()
 OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n")
 subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT)],check=True)
def count(st):return dict(collections.Counter(x.get("status") for x in st.values()))
def stock(ids):
 import yandex_market_client as ym
 result={}
 for i in range(0,len(ids),50):
  code,z=ym._request("POST",f"/v2/campaigns/{cam}/stats/skus",json_body={"shopSkus":ids[i:i+50]},retries=1)
  if code!=200 or z.get("status")!="OK":raise ValueError("Stock verification unavailable")
  for x in (z.get("result") or {}).get("shopSkus",[]):result[x.get("shopSku")]=x.get("warehouses") or []
 if set(result)!=set(ids):raise ValueError("Incomplete stock verification")
 return result
def protected(m,c,st,photo=False):
 x=r.protected(m,c,st)
 if photo:x["offer"].pop("pictures",None)
 return x
def present(v):return v.get("valueId") is not None or bool(str(v.get("value") or "").strip())
def sameval(x,y):
 if y.get("valueId"):return x.get("valueId")==y["valueId"]
 return str(x.get("value") or "").strip()==str(y.get("value") or "").strip() and x.get("unitId")==y.get("unitId")
def valsmatch(c,vals):
 actual=s.groups(c);expected=collections.defaultdict(list)
 for x in vals:expected[int(x["parameterId"])].append(x)
 return all(len(actual.get(k,[]))==len(vs) and all(any(sameval(x,y) for x in actual[k]) for y in vs) for k,vs in expected.items()) and not any(k not in expected and any(present(x) for x in vs) for k,vs in actual.items())
def groupkey(c):return str(c.get("groupId") or "").strip().casefold()
biz,cam=s.account()
if biz!=194752944 or int(cam)!=141377963:raise ValueError("Wrong account")
if OUT.exists() and json.loads(OUT.read_text()).get("external_writes",0):raise ValueError("Existing write journal: do not repeat")
mm,cc,ss=r.read_all(biz,cam)
arch=s.get_mappings(biz,True);ids=[v["offer"]["offerId"] for v in arch]
ac=s.batched_read(biz,"offer-cards",ids) if ids else []
if {x["offerId"] for x in ac}!=set(ids):raise ValueError("Incomplete archived cards")
groups=collections.defaultdict(list)
for k,c in list(cc.items())+[(v["offerId"],v) for v in ac]:
 if groupkey(c):groups[groupkey(c)].append(k)
before_stock=stock(list(mm))
d={"business_id":biz,"campaign_id":int(cam),"started_utc":now(),"counts_before":count(ss),"operations":[],"blocked":{},"external_writes":0}
save()
# Verified exact file A015; append, never remove or change existing gallery.
photo=json.loads(Path("data/market_media_preview_20261126/review.json").read_text())["records"]
p=next(x for x in photo if x["id"]=="A015" and x["sku"]=="0AM325025B")
sku=p["sku"]
stop=False
try:
 m,c,st=s.live(biz,cam,sku);o=m["offer"]
 if st.get("status")!="PUBLISHED" or c.get("cardStatus")!="HAS_CARD_CAN_UPDATE":raise ValueError("Not independently editable")
 if o.get("pictures")!=p["current_urls"]:raise ValueError("Gallery changed since visual review")
 if not p["same_bytes_as_local"] or p["near_duplicate"] or p["url_error"]:raise ValueError("Unverified asset")
 res=requests.get(p["url"],timeout=25);res.raise_for_status()
 if hashlib.sha256(res.content).hexdigest()!=p["source_sha256"]:raise ValueError("Image bytes changed")
 goal=p["current_urls"]+[p["url"]];pre=protected(m,c,st,True);params=s.grouped_norm(c);cat=s.catid(m,c)
 rec={"sku":sku,"kind":"photo_append","status":"submission_started","before":p["current_urls"],"after_expected":goal,"checks":{}}
 d["operations"].append(rec);d["external_writes"]+=1;save()
 rec["response"]=s.post(f"/v2/businesses/{biz}/offer-mappings/update",{"offerMappings":[{"offer":{"offerId":sku,"pictures":goal}}]},retries=1)
 rec["status"]="submitted";save()
 for i in range(7):
  if i:time.sleep(5)
  a,b,z=s.live(biz,cam,sku)
  diff=pre!=protected(a,b,z,True) or params!=s.grouped_norm(b) or cat!=s.catid(a,b)
  rec["checks"]={"protected_unchanged":not diff,"gallery_exact":a["offer"].get("pictures")==goal,"campaign_status":z.get("status"),"card_status":b.get("cardStatus"),"pictures":a["offer"].get("pictures")}
  if diff or z.get("status")!="PUBLISHED":rec["status"]="protected_drift";stop=True;break
  if rec["checks"]["gallery_exact"]:
   rec["status"]="verified" if b.get("cardStatus")=="HAS_CARD_CAN_UPDATE" else "saved_pending_review";break
 else:rec["status"]="pending_or_unchanged"
except Exception as e:
 d["blocked"][sku]=str(e)[:350]
 if any(x["sku"]==sku for x in d["operations"]):stop=True
save()
# Category plan: only singleton active+archived membership, no public ratings or seller reviews,
# exact live old categories, full valid destination parameters. No changes to grouped variants.
target=71380208
s.read_tree();defs=s.definition_map(biz,target)
specs={"0am325025z":71391178,"0am325025u":71391178,"0AM927769N":17712007,"tolkateli-salniki-alum-komplekt":65422456}
for sku,oldcat in specs.items():
 if stop:d["blocked"][sku]="Stopped after uncertain or protected write";continue
 try:
  m,c,st=s.live(biz,cam,sku)
  if s.catid(m,c)==target:continue
  if s.catid(m,c)!=oldcat or st.get("status")!="PUBLISHED" or c.get("cardStatus")!="HAS_CARD_CAN_UPDATE":raise ValueError("Category/status changed; not editable")
  if groupkey(c) and groups.get(groupkey(c))!=[sku]:raise ValueError("Not verified singleton")
  if m["offer"].get("ugc"):raise ValueError("Public rating or reviews present")
  rv=r.review_count(biz,sku)
  if not (rv.get("available") and rv.get("complete") and rv.get("count_at_least")==0):raise ValueError("No verified zero seller reviews: "+str(rv))
  old=s.groups(c);vals=[];changes=[];additional=[]
  for k,vs in old.items():
   for v in vs:
    if not present(v):continue
    if k==21194330 and sku in ("0am325025z","0am325025u"):
     changes.append({"old":v,"new_type":"ремкомплект"});continue
    if k==16382542 and sku=="0AM927769N":
     if not v.get("value") or 7351754 in old:raise ValueError("Cannot transfer exact kit text")
     nv={"parameterId":7351754,"value":v["value"]}
     if 7351754 not in defs or not s.valid_value(nv,defs[7351754]):raise ValueError("Kit text invalid in destination")
     vals.append(nv);changes.append({"old":v,"new":nv});continue
    if sku=="tolkateli-salniki-alum-komplekt" and k in (34857690,14871214):
     if not v.get("value"):raise ValueError("Missing text for semantic preservation")
     additional.append(v["value"]);changes.append({"old":v,"preserved_in":57046341});continue
    if k not in defs or not s.valid_value(v,defs[k]):raise ValueError("Destination cannot retain existing parameter "+str(k))
    vals.append(v)
  if additional:
   if 57046341 in old or 57046341 not in defs:raise ValueError("Cannot preserve old descriptive attributes")
   nv={"parameterId":57046341,"value":"; ".join(additional)}
   if not s.valid_value(nv,defs[57046341]):raise ValueError("Invalid descriptive field")
   vals.append(nv)
  if not any(v["parameterId"]==21194330 for v in vals):vals.append(r.type_value(defs,"комплект" if sku=="0AM927769N" else "ремкомплект"))
  if any(x.get("required") and not any(v["parameterId"]==k for v in vals) for k,x in defs.items()):raise ValueError("Missing required destination parameter")
  if not all(s.valid_value(v,defs[v["parameterId"]]) for v in vals):raise ValueError("Destination schema rejection")
  pre=protected(m,c,st);rec={"sku":sku,"kind":"category","status":"submission_started","old_category":oldcat,"target":target,"reviews":rv,"semantic_remaps":changes,"before_params":c.get("parameterValues"),"target_params":vals}
  d["operations"].append(rec);d["external_writes"]+=1;save()
  try:
   rec["response"]=s.post(f"/v2/businesses/{biz}/offer-cards/update",{"offersContent":[{"offerId":sku,"categoryId":target,"parameterValues":vals}]},retries=1);rec["status"]="submitted";save()
   for i in range(8):
    if i:time.sleep(6)
    a,b,z=s.live(biz,cam,sku)
    if protected(a,b,z)!=pre or z.get("status")!="PUBLISHED":
     rec["status"]="protected_drift";rec["checks"]={"protected_unchanged":False,"campaign_status":z.get("status")};stop=True;break
    try:cat=s.catid(a,b)
    except ValueError:continue
    good=valsmatch(b,vals)
    rec["checks"]={"protected_unchanged":True,"category":cat,"all_semantics_verified":good,"campaign_status":z.get("status"),"card_status":b.get("cardStatus")}
    if cat==target and good:
     rec["status"]="verified" if b.get("cardStatus")=="HAS_CARD_CAN_UPDATE" else "saved_pending_review";break
    if b.get("cardStatus")=="HAS_CARD_CAN_UPDATE_ERRORS":rec["status"]="review";stop=True;break
   else:rec["status"]="pending_or_unchanged";stop=True
  except Exception as e:rec["status"]="uncertain_no_retry";rec["error"]=str(e)[:350];stop=True
 except Exception as e:d["blocked"][sku]=str(e)[:350]
 save()
# Full readback including untouched offers and commercial quantities.
ma,ca,sa=r.read_all(biz,cam);d["counts_after"]=count(sa)
d["stock_differences"]=[k for k,v in stock(list(ma)).items() if v!=before_stock.get(k)]
d["protected_changes"]=[]
for k in mm:
 try:
  if k not in ma:raise ValueError("Missing offer")
  photo=k==p["sku"] and any(x["sku"]==k and x["kind"]=="photo_append" for x in d["operations"])
  if protected(mm[k],cc[k],ss[k],photo)!=protected(ma[k],ca[k],sa[k],photo):d["protected_changes"].append(k)
 except Exception as e:d["protected_changes"].append({"sku":k,"reason":str(e)[:140]})
d["disabled_stock"]=sum(v.get("count",0) for k,z in sa.items() if z.get("status")=="DISABLED_AUTOMATICALLY" for w in before_stock.get(k,[]) for v in w.get("stocks",[]) if v.get("type")=="AVAILABLE")
d["finished_utc"]=now();d["status"]="finished";save()
