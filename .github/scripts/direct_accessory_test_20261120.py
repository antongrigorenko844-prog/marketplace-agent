"""One guarded direct tuning change: Accessory YES to NO for with-board DQ200 group.

Campaign 716316525, autotargeting keyword 205826183946 in group 5826183946.
All other categories, brand options, ads, keywords, groups, budgets and
negative phrases remain unchanged. An uncertain write is never retried.
"""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import hashlib,json,os,sys,urllib.error,urllib.request

CID=716316525
TARGET=205826183946
GROUP=5826183946
BASE="https://api.direct.yandex.com/json/v501/"
BASELINE=Path("data/direct_optimization_20261120/structure_review.json")
OUT=Path("data/direct_optimization_20261120/accessory_mech_with_board_apply.json")
CAMPAIGN_GET={"SelectionCriteria":{"Ids":[CID]},
    "FieldNames":["Id","Name","Type","State","Status","NegativeKeywords"],
    "UnifiedCampaignFieldNames":["BiddingStrategy"],"Page":{"Limit":1000}}
KEYWORDS_GET={"SelectionCriteria":{"CampaignIds":[CID]},
    "FieldNames":["Id","Keyword","CampaignId","AdGroupId","State","Status","AutotargetingCategories"],
    "AutotargetingSettingsCategoriesFieldNames":["Exact","Narrow","Alternative","Accessory","Broader"],
    "AutotargetingSettingsBrandOptionsFieldNames":["WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"],
    "Page":{"Limit":1000}}
UPDATE_ALLOWED=None
WRITE_ATTEMPTED=False
def now():return datetime.now(timezone.utc).isoformat()
def persist(data):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def api(service,method,params):
    global WRITE_ATTEMPTED
    if method=="get" and ((service=="campaigns" and params==CAMPAIGN_GET) or
                          (service=="keywords" and params==KEYWORDS_GET)):
        pass
    elif (method=="update" and service=="keywords" and
          UPDATE_ALLOWED is not None and params==UPDATE_ALLOWED and not WRITE_ATTEMPTED):
        WRITE_ATTEMPTED=True
    else:
        raise RuntimeError("Operation outside exact approved scope")
    token=os.getenv("YANDEX_DIRECT_TOKEN","")
    if not token:raise RuntimeError("YANDEX_DIRECT_TOKEN missing")
    h={"Authorization":"Bearer "+token,"Accept-Language":"en",
       "Content-Type":"application/json; charset=utf-8"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):h["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    req=urllib.request.Request(BASE+service,method="POST",headers=h,
        data=json.dumps({"method":method,"params":params},ensure_ascii=False).encode())
    try:
        with urllib.request.urlopen(req,timeout=55) as resp:raw=resp.read(3*1024*1024+1)
    except urllib.error.HTTPError as e:
        code=e.code;e.close()
        raise RuntimeError(service+"."+method+" HTTP "+str(code)) from None
    except Exception:
        raise RuntimeError(service+"."+method+" transport error") from None
    if len(raw)>3*1024*1024:raise RuntimeError("Unbounded API response")
    try:data=json.loads(raw)
    except Exception:raise RuntimeError("Non JSON API response") from None
    if data.get("error"):
        raise RuntimeError(service+"."+method+" API error code "+str(data["error"].get("error_code")))
    if not isinstance(data.get("result"),dict):raise RuntimeError("Missing result object")
    return data["result"]
def read_campaign():
    res=api("campaigns","get",CAMPAIGN_GET)
    rows=res.get("Campaigns")
    if res.get("LimitedBy") or not isinstance(rows,list) or len(rows)!=1:
        raise RuntimeError("Unexpected campaign count")
    c=rows[0]
    if c.get("Id")!=CID or c.get("Name")!="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест" or c.get("State")!="ON":
        raise RuntimeError("Campaign identity or state changed")
    if (c.get("Type")!="UNIFIED_CAMPAIGN"
        or len((c.get("NegativeKeywords") or {}).get("Items") or [])!=132
        or ((c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}).get("Search",{}).get("HighestPosition",{}).get("WeeklySpendLimit") is not None):
        raise RuntimeError("Campaign safety preflight failed")
    return c
def read_autos():
    resp=api("keywords","get",KEYWORDS_GET)
    rows=resp.get("Keywords")
    if resp.get("LimitedBy") or not isinstance(rows,list) or len(rows)!=272:
        raise RuntimeError("Keywords count changed or pagination required")
    if any(k.get("CampaignId")!=CID for k in rows):raise RuntimeError("Other campaign in keywords response")
    autos=[k for k in rows if k.get("Keyword")=="---autotargeting"]
    if len(autos)!=6:return None
    return sorted(autos,key=lambda a:a["Id"])
def normalized_auto(x):
    return {"Id":x["Id"],"AdGroupId":x["AdGroupId"],"Keyword":x["Keyword"],
            "State":x.get("State"),"AutotargetingSettings":x.get("AutotargetingSettings"),
            "AutotargetingCategories":x.get("AutotargetingCategories")}
def plan_update(target):
    settings=deepcopy(target.get("AutotargetingSettings") or {})
    categories=settings.get("Categories")
    brands=settings.get("BrandOptions")
    if not isinstance(categories,dict) or not isinstance(brands,dict):
        raise RuntimeError("Autotargeting flags not explicitly returned")
    expected={"Exact","Narrow","Alternative","Accessory","Broader"}
    if set(categories)!=expected or set(brands)!={"WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"}:
        raise RuntimeError("Missing targeting flag; cannot safely preserve")
    if categories["Accessory"]!="YES" or any(v not in ("YES","NO") for v in list(categories.values())+list(brands.values())):
        raise RuntimeError("Accessory not enabled or invalid settings")
    settings["Categories"]["Accessory"]="NO"
    return {"Keywords":[{"Id":TARGET,"AutotargetingSettings":settings}]},settings
def main():
    global UPDATE_ALLOWED
    if OUT.exists():
        try:p=json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:print("Recorded result unreadable: no repeat");return 1
        if p.get("write_attempted"):
            print("Recorded prior update attempt: no blind retry");return 1
    report={"status":"preflight","mode":"bounded_one_keyword_update","campaign":CID,"group":GROUP,
            "autotargeting_keyword_id":TARGET,"started_utc":now(),"write_attempted":False,
            "errors":[],"target_change_only":"AutotargetingSettings.Categories.Accessory: YES -> NO"}
    persist(report)
    try:
        if not BASELINE.exists():raise RuntimeError("Missing independent baseline")
        baseline=json.loads(BASELINE.read_text(encoding="utf-8"))
        baselines=baseline.get("autotargeting") or []
        if (baseline.get("status")!="complete" or baseline.get("campaign_id")!=CID
            or len(baselines)!=6 or baseline.get("campaign",{}).get("NegativeKeywordCount")!=132):
            raise RuntimeError("Invalid baseline snapshot")
        before_campaign=read_campaign()
        before=read_autos()
        if before is None:raise RuntimeError("Expected 6 autotargeting criteria")
        observed=[normalized_auto(x) for x in before]
        if observed!=sorted(baselines,key=lambda x:x["Id"]):
            raise RuntimeError("Current targeting settings differ from read-only baseline")
        targets=[x for x in before if x["Id"]==TARGET]
        if len(targets)!=1 or targets[0]["AdGroupId"]!=GROUP or targets[0].get("State")!="ON":
            raise RuntimeError("Target criterion state changed")
        payload,expected_settings=plan_update(targets[0])
        report.update(baseline_autotargeting_sha256=digest(observed),
            before_settings=targets[0]["AutotargetingSettings"],
            expected_settings=expected_settings,
            campaign_before_fingerprint=digest(before_campaign))
        persist(report)
        # Pre-write fresh read to detect a conflicting configuration change.
        prewrite_campaign=read_campaign()
        prewrite=read_autos()
        if prewrite_campaign!=before_campaign or prewrite!=before:
            raise RuntimeError("Concurrent changes detected: no write")
        UPDATE_ALLOWED=payload
        report["write_attempted"]=True
        report["write_started_utc"]=now()
        persist(report)
        try:
            response=api("keywords","update",payload)
            items=response.get("UpdateResults")
            report["api_accepted"]=bool(isinstance(items,list) and len(items)==1 and items[0].get("Id")==TARGET and not items[0].get("Errors"))
            report["api_warnings_codes"]=[w.get("Code") for w in (items[0].get("Warnings") or [])] if isinstance(items,list) and items else []
        except Exception as e:
            report["api_uncertain_error"]=str(e)[:240]
        after_campaign=read_campaign()
        after=read_autos()
        if after is None:raise RuntimeError("Post-write autotargeting criteria missing")
        before_by={x["Id"]:x for x in before}
        after_by={x["Id"]:x for x in after}
        after_target=after_by[TARGET]
        expected=deepcopy(before_by[TARGET])
        expected["AutotargetingSettings"]=expected_settings
        # Deprecated field should reflect the same Accessory category.
        legacy=expected.get("AutotargetingCategories")
        if isinstance(legacy,dict):
            for row in legacy.get("Items",[]):
                if row.get("Category")=="ACCESSORY":row["Value"]="NO"
        other_same=all(before_by[k]==after_by[k] for k in before_by if k!=TARGET)
        exactly_target=expected==after_target
        campaign_same=before_campaign==after_campaign
        report.update(after_settings=after_target.get("AutotargetingSettings"),
            other_five_autotargetings_unchanged=other_same,
            exact_expected_target=exactly_target,campaign_fields_unchanged=campaign_same,
            verified_utc=now())
        if exactly_target and other_same and campaign_same:
            report["status"]="applied_verified"
            return 0
        report["status"]="after_requires_review"
        return 1
    except Exception as e:
        report["status"]="blocked_or_failed_no_retry"
        report["errors"].append(str(e)[:260])
        return 1
    finally:
        report["finished_utc"]=now()
        persist(report)
        print("Accessory category controlled test",report["status"],"write",report["write_attempted"],flush=True)
if __name__=="__main__":
    sys.exit(main())
