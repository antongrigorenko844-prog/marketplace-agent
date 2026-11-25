"""Market-only continuation. Exact readback, bounded missing-parameter writes, no price/stock/group changes."""
import collections, copy, datetime, hashlib, json, time, subprocess
from pathlib import Path
import market_full_seo_20261125 as s
import market_growth_recovery_20261125 as r

OUT=Path("data/market_final_execution_20261126.json")
PLAN=Path("data/market_search_type_plan_20261126.json")
CONTENT=Path("data/market_content_enrichment_plan_20261126.json")
MAN=Path("data/content_manifest.json")
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(v): return hashlib.sha256(s.canon(v).encode()).hexdigest()
def save(d,commit=False):
    OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n")
    if commit: subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT)],check=True)
def read(ids):
    mm=s.index(s.batched_read(biz,"offer-mappings",ids),lambda x:x["offer"]["offerId"])
    cc=s.index(s.batched_read(biz,"offer-cards",ids),lambda x:x["offerId"])
    ss=s.campaign_status(cam,ids)
    if not set(mm)==set(cc)==set(ss)==set(ids): raise ValueError("Incomplete exact snapshot")
    return mm,cc,ss
def group_key(c): return str(c.get("groupId") or "").strip().casefold()
def present(v): return bool(v.get("valueId") is not None or str(v.get("value") or "").strip())
def match(vs,desired):
    good=[v for v in vs if present(v)]
    if len(good)!=1:return False
    v=good[0]
    if desired.get("valueId"): return v.get("valueId")==desired["valueId"]
    return str(v.get("value") or "").strip().casefold()==str(desired.get("value") or "").strip().casefold() and (not desired.get("unitId") or v.get("unitId")==desired["unitId"])
def fingerprint(m,c,st,omit=()):
    return {"protected":r.protected(m,c,st),"category":s.catid(m,c),"other_params":s.grouped_norm(c,omit)}
def media_count(o,k):return len(o.get(k) or [])
def count_status(st):return dict(collections.Counter(v.get("status") for v in st.values()))
def same_group(c,groups,k):return not group_key(c) or groups.get(group_key(c))==[k]
def independent_warning(c):
    return all(v.get("message")=="Не заполнено обязательное поле" and v.get("comment")=="Укажите код ТН ВЭД" for v in (c.get("warnings") or []))
def stock_snapshot(ids):
    # Read only, existing script uses this endpoint; collect quantity, not orders or buyers.
    try:
        import yandex_market_client as ym
        result={}
        for i in range(0,len(ids),50):
            block=ids[i:i+50]
            code,z=ym._request("POST",f"/v2/campaigns/{cam}/stats/skus",json_body={"shopSkus":block},retries=1)
            if code!=200 or z.get("status")!="OK": return {"available":False,"http":code}
            for x in (z.get("result") or {}).get("shopSkus",[]):
                result[x.get("shopSku")]=x.get("warehouses") or []
        return {"available":True,"coverage":len(result),"stocks":result}
    except Exception as e:return {"available":False,"error":str(e)[:180]}

biz,cam=s.account()
if biz!=194752944 or int(cam)!=141377963:raise ValueError("Wrong account")
plan=json.loads(PLAN.read_text()); cp=json.loads(CONTENT.read_text()); manifest=json.loads(MAN.read_text())["products"]
if plan["business_id"]!=biz or int(plan["campaign_id"])!=int(cam):raise ValueError("Wrong plan account")
previous=json.loads(OUT.read_text()) if OUT.exists() else {}
prior_ops=previous.get("operations",[])
d={"platform":"Yandex Market","business_id":biz,"campaign_id":int(cam),"started_utc":now(),"status":"running","operations":prior_ops,"new_requests":0,"attributes":{},"categories":{},"publication":{},"groups":{},"errors":{}}
mm,cc,ss=r.read_all(biz,cam);d["catalog_before"]=count_status(ss)
arch=s.get_mappings(biz,True);aids=[x["offer"]["offerId"] for x in arch]
ac=s.batched_read(biz,"offer-cards",aids) if aids else []
if {x["offerId"] for x in ac}!=set(aids):raise ValueError("Incomplete archived group membership")
groups={}
for k,c in list(cc.items())+[(v["offerId"],v) for v in ac]:
    if group_key(c):groups.setdefault(group_key(c),[]).append(k)
d["groups"]={g:v for g,v in groups.items() if len(v)>1}
d["coverage"]={"active":len(mm),"archived":len(aids)}
d["stock_before"]=stock_snapshot(list(mm))
# Baseline protected fields, exact IDs, parameters; all non-target cards checked after writes.
baseline={k:fingerprint(mm[k],cc[k],ss[k]) for k in mm if (mm[k].get("mapping") or {}).get("marketSku")}
desired={}
for k,v in plan["rows"].items():
    if k not in mm: continue
    cat=s.catid(mm[k],cc[k]);row=d["attributes"].setdefault(k,{"fields":{},"status":"pending"})
    if cat!=v["categoryId"]:
        row.update(status="blocked",reason="Category changed");continue
    defs=s.definition_map(biz,cat);df=defs.get(v["parameterId"]) or {}
    try:
        if df.get("name")!="Тип" or not df.get("filtering") or df.get("distinctive"):raise ValueError("Type is not independent search filter")
        if v["valueId"]:
            val=next(x for x in df.get("values",[]) if x["id"]==v["valueId"] and str(x.get("value")).casefold()==v["label"].casefold())
            p={"parameterId":v["parameterId"],"valueId":val["id"],"value":val["value"]}
        else:
            if not df.get("allowCustomValues"):raise ValueError("Custom value not permitted")
            p=r.type_value(defs,v["label"])
        if not s.valid_value(p,df):raise ValueError("Invalid typed value")
        desired.setdefault(k,{})[p["parameterId"]]=p
        row["source"]="reviewed exact SKU/type plan; dictionary or allowed custom"
    except Exception as exc:row.update(status="blocked",reason=str(exc)[:180])
# Approved manifest parameters: fill only genuinely empty fields, never overwrite or migrate.
for k,m in manifest.items():
    if k not in mm or not m.get("market_parameters"):continue
    v=m["market_parameters"];cat=s.catid(mm[k],cc[k])
    if cat!=v.get("category"):continue
    defs=s.definition_map(biz,cat)
    for p in v.get("set",[]):
        pid=p["parameterId"];df=defs.get(pid)
        if pid==200 or not df or not s.valid_value(p,df):continue
        if not any(present(x) for x in s.groups(cc[k]).get(pid,[])):
            desired.setdefault(k,{})[pid]=p
            d["attributes"].setdefault(k,{"fields":{},"status":"pending","source":"approved content manifest"})
# Build independent operations; no already accepted request is repeated.
submitted={(op["sku"],p["parameterId"],s.canon(p)) for op in prior_ops if op.get("status") in ("submission_started","submitted","verified","saved_pending_review","unconfirmed","uncertain") for p in op.get("patch",[])}
ready={}
for k,vals in desired.items():
    row=d["attributes"].setdefault(k,{"fields":{},"status":"pending"})
    c=cc[k];cat=s.catid(mm[k],c);g=s.groups(c);patch=[]
    for pid,p in vals.items():
        key=str(pid);row["fields"][key]={"target":p,"source":row.get("source")}
        if match(g.get(pid,[]),p):row["fields"][key]["status"]="already_matching";continue
        if any(present(x) for x in g.get(pid,[])):row["fields"][key]["status"]="existing_nonempty";continue
        if (k,pid,s.canon(p)) in submitted:row["fields"][key]["status"]="previously_submitted_wait_readback";continue
        patch.append(p)
    if not patch:row["status"]="no_write_needed";continue
    if ss[k].get("status")!="PUBLISHED" or c.get("cardStatus")!="HAS_CARD_CAN_UPDATE" or c.get("errors") or not independent_warning(c):
        row.update(status="blocked",reason="Not error-free editable published card");continue
    if not same_group(c,groups,k):row.update(status="blocked",reason="Multiple group members; shared characteristics must be preserved");continue
    if k not in baseline:row.update(status="blocked",reason="Identity not verified");continue
    ready[k]=patch;row["status"]="ready"
d["planned_attributes"]=len(ready);save(d,True)
# Small independent batches. Use complete existing compatible values for observed
# account-specific sparse-update preservation, but never alter other fields.
def submit(ks):
    fresh_m,fresh_c,fresh_s=read(ks)
    items=[];ops=[]
    for k in ks:
        c=fresh_c[k];m=fresh_m[k];st=fresh_s[k];row=d["attributes"][k];patch=ready[k];pids={p["parameterId"] for p in patch};cat=s.catid(m,c)
        if fingerprint(m,c,st)!=baseline[k] or c.get("cardStatus")!="HAS_CARD_CAN_UPDATE" or c.get("errors") or not independent_warning(c) or not same_group(c,groups,k):
            row.update(status="blocked",reason="Fresh snapshot changed");continue
        defs=s.definition_map(biz,cat)
        retained=[p for pid,vs in s.groups(c).items() if pid not in pids for p in vs if pid in defs and present(p)]
        if any(not s.valid_value(p,defs[p["parameterId"]]) for p in retained):
            row.update(status="blocked",reason="Existing attributes cannot be safely carried forward");continue
        if any(pid not in defs and any(present(p) for p in vs) for pid,vs in s.groups(c).items()):
            row.update(status="blocked",reason="Existing field outside live schema");continue
        item={"offerId":k,"categoryId":cat,"parameterValues":retained+patch}
        assert all(p["parameterId"]!=200 for p in patch)
        rec={"sku":k,"status":"submission_started","kind":"attribute","patch":patch,"before":fingerprint(m,c,st,pids),"category":cat}
        d["operations"].append(rec);ops.append(rec);items.append(item);row["status"]="submission_started"
    if not items:return True
    d["new_requests"]+=1;save(d,True)
    try:
        result=s.post(f"/v2/businesses/{biz}/offer-cards/update",{"offersContent":items},retries=1)
        for op in ops:op["status"]="submitted";op["response"]=result
    except Exception as exc:
        for op in ops:op.update(status="uncertain",error=str(exc)[:300]);d["attributes"][op["sku"]].update(status="uncertain",reason=str(exc)[:300])
        save(d,True)
        return False
    # Response OK is not verification. One batch-wide read, then final re-read.
    for attempt in range(3):
        if attempt:time.sleep(12)
        after_m,after_c,after_s=read([o["sku"] for o in ops])
        complete=True
        for op in ops:
            k=op["sku"];c=after_c[k];m=after_m[k];st=after_s[k];pids={p["parameterId"] for p in op["patch"]}
            f=fingerprint(m,c,st,pids);diff=[z for z in f if f[z]!=op["before"][z]]
            exact=all(match(s.groups(c).get(p["parameterId"],[]),p) for p in op["patch"])
            op["checks"]={"protected_diff":diff,"fields_match":exact,"status":st.get("status"),"card_status":c.get("cardStatus")}
            if diff or st.get("status")!="PUBLISHED":
                op["status"]="protected_drift";d["attributes"][k]["status"]="protected_drift";save(d,True);return False
            op["status"]="verified" if exact and c.get("cardStatus")=="HAS_CARD_CAN_UPDATE" else "saved_pending_review" if exact else "unconfirmed"
            d["attributes"][k]["status"]=op["status"]
            if not exact:complete=False
        if complete:break
    save(d,True);return True
keys=sorted(ready);chunks=[keys[:1]]+[keys[i:i+5] for i in range(1,len(keys),5)] if keys else []
stop=False
for ks in chunks:
    if stop:
        for k in ks:d["attributes"][k].update(status="held_after_guard",reason="Prior batch uncertain or protected drift")
    elif not submit(ks):stop=True
# Final readback across all work, including previous text writes.
m2,c2,st2=r.read_all(biz,cam);d["catalog_after"]=count_status(st2)
d["content"]={}
for k,t in cp["rows"].items():
    if k not in m2:continue
    c=c2[k];o=m2[k]["offer"];st=st2[k]
    fields={f:o.get(f)==t["new_"+f] for f in ("description","name") if "new_"+f in t}
    d["content"][k]={"fields":fields,"status":"verified" if all(fields.values()) and c.get("cardStatus")=="HAS_CARD_CAN_UPDATE" and st.get("status")=="PUBLISHED" else "saved_pending_review" if all(fields.values()) and st.get("status")=="PUBLISHED" else "blocked_or_unconfirmed","card_status":c.get("cardStatus"),"campaign_status":st.get("status"),"errors":c.get("errors") or [],"warnings":c.get("warnings") or []}
for k,row in d["attributes"].items():
    if k not in m2:continue
    vals=desired.get(k,{})
    for pid,p in vals.items():
        field=row["fields"].setdefault(str(pid),{"target":p})
        field["saved"]=match(s.groups(c2[k]).get(pid,[]),p)
    if vals and all(row["fields"][str(pid)].get("saved") for pid in vals) and row["status"] not in ("protected_drift",):
        row["status"]="verified" if c2[k].get("cardStatus")=="HAS_CARD_CAN_UPDATE" else "saved_pending_review"
d["protected_changes"]=[]
for k,b in baseline.items():
    if k not in m2:d["protected_changes"].append({"sku":k,"reason":"missing"});continue
    allowed={p["parameterId"] for op in d["operations"] if op["sku"]==k and op["status"] not in ("held_after_guard",) for p in op["patch"]}
    old=copy.deepcopy(b);old["other_params"]={z:v for z,v in old["other_params"].items() if int(z) not in allowed}
    new=fingerprint(m2[k],c2[k],st2[k],allowed)
    diff=[z for z in old if old[z]!=new[z]]
    if diff or ss[k].get("status")!=st2[k].get("status"):d["protected_changes"].append({"sku":k,"diff":diff,"status_before":ss[k].get("status"),"status_after":st2[k].get("status")})
d["stock_after"]=stock_snapshot(list(m2))
d["stock_differences"]=None
if d["stock_before"].get("available") and d["stock_after"].get("available") and d["stock_before"]["coverage"]==d["stock_after"]["coverage"]==len(m2):
    d["stock_differences"]=[k for k,v in d["stock_before"]["stocks"].items() if v!=d["stock_after"]["stocks"].get(k)]
# Fresh category and publication blockers. No fabricated alternative part numbers.
old=json.loads(Path("data/market_current_card_audit_20261125.json").read_text())
for k in old["categories_remaining"]:
    if k not in m2:continue
    cat=s.catid(m2[k],c2[k]);target=71051282 if k in ("EA888","EA888gen3") else 71380208
    d["categories"][k]={"current":cat,"target":target,"status":"already_correct" if cat==target else "manually_hidden_preserved" if st2[k].get("status")=="DISABLED_BY_PARTNER" else "needs_safe_migration","group":c2[k].get("groupId"),"name":m2[k]["offer"].get("name")}
for k,st in st2.items():
    if st.get("status")!="DISABLED_AUTOMATICALLY":continue
    c=c2[k];o=m2[k]["offer"];defs=s.definition_map(biz,s.catid(m2[k],c))
    g=s.groups(c)
    d["publication"][k]={"status":st.get("status"),"card_status":c.get("cardStatus"),"category":s.catid(m2[k],c),"group":c.get("groupId"),"warnings":(st.get("warnings") or [])+(c.get("warnings") or []),"errors":(st.get("errors") or [])+(c.get("errors") or []),"distinctive":[{"id":pid,"name":v.get("name"),"current":g.get(pid,[])} for pid,v in defs.items() if v.get("distinctive")],"required_missing":[v["name"] for pid,v in defs.items() if v.get("required") and not any(present(x) for x in g.get(pid,[]))],"media":{"pictures":media_count(o,"pictures"),"videos":media_count(o,"videos")}}
d["quality"]={"average_before":sum(c.get("contentRating") or 0 for k,c in cc.items() if ss[k].get("status")=="PUBLISHED")/max(1,d["catalog_before"].get("PUBLISHED",0)),"average_after":sum(c.get("contentRating") or 0 for k,c in c2.items() if st2[k].get("status")=="PUBLISHED")/max(1,d["catalog_after"].get("PUBLISHED",0))}
d["recommendations"]=dict(collections.Counter(x.get("type") for k,c in c2.items() if st2[k].get("status")=="PUBLISHED" for x in (c.get("recommendations") or [])))
d["summary"]={"content":dict(collections.Counter(v["status"] for v in d["content"].values())),"fields":{f:dict(collections.Counter(("saved" if v["fields"][f] else "not_saved")+":"+v["status"] for v in d["content"].values() if f in v["fields"])) for f in ("description","name")},"attributes":dict(collections.Counter(v["status"] for v in d["attributes"].values())),"attribute_values_saved":sum(p.get("saved",False) for v in d["attributes"].values() for p in v["fields"].values()),"disabled":len(d["publication"]),"protected_changes":len(d["protected_changes"])}
d["status"]="completed_with_review";d["finished_utc"]=now();save(d,True)
print(json.dumps(d["summary"],ensure_ascii=False))
