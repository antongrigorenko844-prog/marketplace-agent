"""LEOPARTS guarded 22 Nov autotargeting cleanup: preserve core exact/narrow traffic."""
import json,os,sys,urllib.request,urllib.error,hashlib
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
CID=716316525
CAP=4_000_000_000
API="https://api.direct.yandex.com/json/v501/"
OUT=Path("data/direct_recovery_20261122/targeting_fix.json")
TARGETS={
  205826171512:{"group":5826171512,"off":["Broader","Accessory","Alternative"]},
  205826183946:{"group":5826183946,"off":["Broader","Accessory"]},
  205826320196:{"group":5826320196,"off":["Broader","Accessory"]},
}
CAM_GET={"SelectionCriteria":{"Ids":[CID]},
         "FieldNames":["Id","Name","Type","State","Status","NegativeKeywords"],
         "UnifiedCampaignFieldNames":["BiddingStrategy"],"Page":{"Limit":1000}}
KW_GET={"SelectionCriteria":{"CampaignIds":[CID]},
        "FieldNames":["Id","Keyword","CampaignId","AdGroupId","State","Status","AutotargetingCategories"],
        "AutotargetingSettingsCategoriesFieldNames":["Exact","Narrow","Alternative","Accessory","Broader"],
        "AutotargetingSettingsBrandOptionsFieldNames":["WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"],
        "Page":{"Limit":1000}}
ALLOWED_UPDATE=None
WRITE_SENT=False
def now():return datetime.now(timezone.utc).isoformat()
def save(o):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    f=OUT.with_suffix(".tmp");f.write_text(json.dumps(o,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");f.replace(OUT)
def sha(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
def api(service,method,params):
    global WRITE_SENT
    if method=="get":
        if (service,params) not in (("campaigns",CAM_GET),("keywords",KW_GET)):raise RuntimeError("Unauthorized GET")
    elif method=="update":
        if service!="keywords" or ALLOWED_UPDATE is None or params!=ALLOWED_UPDATE or WRITE_SENT:
            raise RuntimeError("Unapproved update or unsafe retry")
        WRITE_SENT=True
    else:raise RuntimeError("Forbidden API operation")
    token=os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("Missing Direct token in workflow")
    h={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):h["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    req=urllib.request.Request(API+service,data=json.dumps({"method":method,"params":params},ensure_ascii=False).encode(),method="POST",headers=h)
    try:
        with urllib.request.urlopen(req,timeout=65) as resp:
            raw=resp.read(4*1024*1024+1)
            if len(raw)>4*1024*1024:raise RuntimeError("Response too large")
            obj=json.loads(raw)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError(service+" HTTP "+str(code)) from None
    except Exception:raise RuntimeError(service+" transport/error; do not retry write") from None
    if obj.get("error"):raise RuntimeError(service+" API error code "+str(obj["error"].get("error_code")))
    result=obj.get("result")
    if not isinstance(result,dict) or result.get("LimitedBy"):raise RuntimeError("Missing/incomplete result")
    return result
def read():
    c=api("campaigns","get",CAM_GET).get("Campaigns")
    k=api("keywords","get",KW_GET).get("Keywords")
    if not isinstance(c,list) or len(c)!=1 or not isinstance(k,list) or len(k)!=272:
        raise RuntimeError("Unexpected campaign/keyword count")
    c=c[0]
    if c.get("Id")!=CID or c.get("Name")!="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест" or c.get("State")!="ON":
        raise RuntimeError("Unexpected campaign identity/status")
    if c.get("Type")!="UNIFIED_CAMPAIGN":raise RuntimeError("Unexpected campaign type")
    s=((c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {})
    if ((s.get("Search") or {}).get("BiddingStrategyType")!="HIGHEST_POSITION" or
        ((s.get("Search") or {}).get("HighestPosition") or {}).get("WeeklySpendLimit")!=CAP or
        (s.get("Network") or {}).get("BiddingStrategyType")!="SERVING_OFF"):
        raise RuntimeError("Budget safety cap missing or campaign strategy changed")
    neg=(c.get("NegativeKeywords") or {}).get("Items")
    if not isinstance(neg,list) or len(neg)<626:raise RuntimeError("Negative inventory drift")
    if sum(x.get("Keyword")=="---autotargeting" for x in k)!=6 or len(set(x["Id"] for x in k))!=272:
        raise RuntimeError("Unexpected keyword identity/inventory")
    if any(x.get("CampaignId")!=CID for x in k):raise RuntimeError("Other campaign returned")
    return c,sorted(k,key=lambda x:x["Id"])
def baseline(k,log):
    by={x["Id"]:x for x in k}
    changes=[]
    for kid,config in TARGETS.items():
        item=by.get(kid)
        if not item or item["AdGroupId"]!=config["group"] or item["Keyword"]!="---autotargeting" or item.get("State")!="ON":
            raise RuntimeError("Targeted autotargeting ID or group mismatch")
        state=item.get("AutotargetingSettings") or {}
        cat=state.get("Categories") or {}
        brands=state.get("BrandOptions") or {}
        if set(cat)!={"Exact","Narrow","Alternative","Accessory","Broader"} or set(brands)!={"WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"}:
            raise RuntimeError("Incomplete current category or brand flags")
        if (cat["Exact"]!="YES" and cat["Narrow"]!="YES") or any(v not in ("YES","NO") for v in list(cat.values())+list(brands.values())):
            raise RuntimeError("Current targeted categories differ from expected")
        next_state=deepcopy(state)
        for flag in config["off"]:next_state["Categories"][flag]="NO"
        log["targets_before"].append({"keyword_id":kid,"group":config["group"],"flags":cat,"brands":brands})
        if next_state!=state:changes.append({"Id":kid,"AutotargetingSettings":next_state})
    return changes
def compare_keywords(before,after,updated):
    b={x["Id"]:x for x in before};a={x["Id"]:x for x in after}
    if set(b)!=set(a):return False,False
    unchanged_other=all(a[k]==b[k] for k in b if k not in updated)
    target_ok=True
    for kid,settings in updated.items():
        x=deepcopy(b[kid]);y=deepcopy(a[kid])
        x.pop("AutotargetingCategories",None)
        y.pop("AutotargetingCategories",None)
        x["AutotargetingSettings"]=settings
        if x!=y:target_ok=False
    return unchanged_other,target_ok
def run():
    global ALLOWED_UPDATE
    record={"status":"preflight","mode":"single_bounded_autotargeting_change",
            "campaign":CID,"start_utc":now(),"write_attempted":False,"targets_before":[],"errors":[]}
    save(record)
    try:
        before_c,before_k=read()
        changes=baseline(before_k,record)
        record.update(negative_count=len((before_c.get("NegativeKeywords") or {}).get("Items") or []),
                      before_campaign_sha=sha(before_c),before_keyword_sha=sha(before_k),
                      planned_changes=changes)
        if not changes:
            record["status"]="already_narrowed";return 0
        save(record)
        pre_c,pre_k=read()
        if pre_c!=before_c or pre_k!=before_k:raise RuntimeError("Concurrent edits detected before updating")
        ALLOWED_UPDATE={"Keywords":changes}
        record["write_attempted"]=True;record["write_started_utc"]=now();save(record)
        try:
            result=api("keywords","update",ALLOWED_UPDATE)
            res=result.get("UpdateResults") or []
            record["api_accepted"]=len(res)==len(changes) and {x.get("Id") for x in res}=={x["Id"] for x in changes} and all(not x.get("Errors") for x in res)
            record["warnings"]=[{"id":x.get("Id"),"codes":[w.get("Code") for w in x.get("Warnings") or []]} for x in res if x.get("Warnings")]
        except Exception as e:record["errors"].append(str(e)[:250])
        after_c,after_k=read()
        others,targets=compare_keywords(before_k,after_k,{x["Id"]:x["AutotargetingSettings"] for x in changes})
        record.update(campaign_unchanged=before_c==after_c,other_keywords_unchanged=others,
                      target_flags_verified=targets,after_campaign_sha=sha(after_c),after_keyword_sha=sha(after_k),
                      after_target_settings=[{"id":x["Id"],"categories":(x.get("AutotargetingSettings") or {}).get("Categories")}
                          for x in after_k if x["Id"] in TARGETS],
                      verified_utc=now())
        record["status"]="applied_verified" if before_c==after_c and others and targets else "requires_manual_review"
        return 0 if record["status"]=="applied_verified" else 1
    except Exception as e:
        record["status"]="blocked_or_unverified"
        record["errors"].append(str(e)[:260])
        return 1
    finally:
        record["finished_utc"]=now();save(record)
        print("Autotargeting cleanup",record["status"],"attempted",record["write_attempted"],flush=True)
if __name__=="__main__":sys.exit(run())
