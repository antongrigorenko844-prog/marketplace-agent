"""One bounded DQ200 with-board autotargeting change: EXACT ON, NARROW OFF.
Campaign 716316525, keyword 205826183946, group 5826183946.
Read, compare-and-update only this criterion, verify others unchanged, never blind retry.
"""
import json,os,sys,urllib.request,urllib.error,hashlib
from pathlib import Path
from copy import deepcopy
from datetime import datetime,timezone

CID=716316525
TARGET=205826183946
GROUP=5826183946
CAP=4000000000
BASE="https://api.direct.yandex.com/json/v501/"
OUT=Path("data/direct_recovery_20261122/with_board_exact_fix.json")
CAM_GET={"SelectionCriteria":{"Ids":[CID]},"FieldNames":["Id","Name","Type","State","Status","NegativeKeywords"],
"UnifiedCampaignFieldNames":["BiddingStrategy"],"Page":{"Limit":1000}}
KW_GET={"SelectionCriteria":{"CampaignIds":[CID]},
"FieldNames":["Id","Keyword","CampaignId","AdGroupId","State","Status","AutotargetingCategories"],
"AutotargetingSettingsCategoriesFieldNames":["Exact","Narrow","Alternative","Accessory","Broader"],
"AutotargetingSettingsBrandOptionsFieldNames":["WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"],
"Page":{"Limit":1000}}
WRITE_ONCE=False
PLANNED=None

def now():return datetime.now(timezone.utc).isoformat()
def save(o):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(o,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def api(service,method,params):
    global WRITE_ONCE
    if method=="get" and ((service=="campaigns" and params==CAM_GET) or
                          (service=="keywords" and params==KW_GET)):pass
    elif method=="update" and service=="keywords" and PLANNED is not None and params==PLANNED and not WRITE_ONCE:
        WRITE_ONCE=True
    else:raise RuntimeError("Unsupported or repeated operation")
    token=os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("No Direct token configured")
    hdr={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):hdr["Client-Login"]=os.getenv("YANDEX_DIRECT_LOGIN")
    req=urllib.request.Request(BASE+service,data=json.dumps({"method":method,"params":params},ensure_ascii=False).encode(),headers=hdr,method="POST")
    try:
        with urllib.request.urlopen(req,timeout=60) as response:
            raw=response.read(3500000)
            if len(raw)>=3500000:raise RuntimeError("Oversize response")
            v=json.loads(raw)
    except urllib.error.HTTPError as err:
        code=err.code;err.close();raise RuntimeError("API HTTP "+str(code)) from None
    except Exception:raise RuntimeError("API transport or parse failure; do not repeat update") from None
    if v.get("error"):raise RuntimeError("API error code "+str(v["error"].get("error_code")))
    obj=v.get("result")
    if not isinstance(obj,dict) or obj.get("LimitedBy"):raise RuntimeError("Missing or paginated result")
    return obj
def current():
    c=api("campaigns","get",CAM_GET).get("Campaigns")
    k=api("keywords","get",KW_GET).get("Keywords")
    if not isinstance(c,list) or len(c)!=1 or not isinstance(k,list) or len(k)!=272:
        raise RuntimeError("Campaign or keyword inventory changed")
    c=c[0]
    if (c.get("Id")!=CID or c.get("Name")!="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест" or
        c.get("State")!="ON" or c.get("Type")!="UNIFIED_CAMPAIGN"):
        raise RuntimeError("Campaign identity/state mismatch")
    s=(c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}
    if ((s.get("Search") or {}).get("BiddingStrategyType")!="HIGHEST_POSITION" or
        ((s.get("Search") or {}).get("HighestPosition") or {}).get("WeeklySpendLimit")!=CAP or
        (s.get("Network") or {}).get("BiddingStrategyType")!="SERVING_OFF"):
        raise RuntimeError("Budget/strategy guard failed")
    neg=(c.get("NegativeKeywords") or {}).get("Items")
    if not isinstance(neg,list) or len(neg)<626:
        raise RuntimeError("Negative keywords not current")
    if len({x.get("Id") for x in k})!=272 or any(x.get("CampaignId")!=CID for x in k):
        raise RuntimeError("Unrecognized keyword set")
    k=sorted(k,key=lambda x:x["Id"])
    return c,k
def flags(rows):
    matched=[k for k in rows if k["Id"]==TARGET]
    if len(matched)!=1:raise RuntimeError("Target criterion not unique")
    item=matched[0]
    if item["AdGroupId"]!=GROUP or item["Keyword"]!="---autotargeting" or item["State"]!="ON":
        raise RuntimeError("Target criterion identity or state invalid")
    s=item.get("AutotargetingSettings") or {}
    cat=s.get("Categories") or {}
    brands=s.get("BrandOptions") or {}
    if set(cat)!={"Exact","Narrow","Alternative","Accessory","Broader"} or set(brands)!={"WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"}:
        raise RuntimeError("Target configuration incomplete")
    if any(v not in ("YES","NO") for v in list(cat.values())+list(brands.values())):
        raise RuntimeError("Unexpected category values")
    return item,cat,brands
def test(before,after,expected):
    a={x["Id"]:x for x in before};b={x["Id"]:x for x in after}
    if set(a)!=set(b):return False,False
    others=all(a[k]==b[k] for k in a if k!=TARGET)
    original=deepcopy(a[TARGET]);modified=deepcopy(b[TARGET])
    original["AutotargetingSettings"]=expected
    for row in ((original.get("AutotargetingCategories") or {}).get("Items") or []):
        if row.get("Category")=="EXACT":row["Value"]="YES"
        if row.get("Category")=="NARROW":row["Value"]="NO"
    return others,original==modified
def run():
    global PLANNED
    result={"status":"preflight","mode":"with_board_exact_only","campaign":CID,
            "group":GROUP,"keyword_id":TARGET,"started_utc":now(),"write_attempted":False,"errors":[]}
    save(result)
    try:
        before_c,before_k=current()
        item,cat,brands=flags(before_k)
        result["before_categories"]=cat
        result["before_brands"]=brands
        result["negative_count"]=len((before_c.get("NegativeKeywords") or {}).get("Items") or [])
        if cat=={"Exact":"YES","Narrow":"NO","Alternative":"NO","Accessory":"NO","Broader":"NO"}:
            result["status"]="already_exact_only";return 0
        if cat!={"Exact":"NO","Narrow":"YES","Alternative":"NO","Accessory":"NO","Broader":"NO"}:
            raise RuntimeError("Current with-board category state differs from audited state")
        planned=deepcopy(item["AutotargetingSettings"])
        planned["Categories"]["Exact"]="YES"
        planned["Categories"]["Narrow"]="NO"
        PLANNED={"Keywords":[{"Id":TARGET,"AutotargetingSettings":planned}]}
        result["planned_categories"]=planned["Categories"]
        save(result)
        check_c,check_k=current()
        if check_c!=before_c or check_k!=before_k:
            raise RuntimeError("Concurrent campaign modification detected; no write")
        result["write_attempted"]=True
        result["update_started_utc"]=now()
        save(result)
        try:
            res=api("keywords","update",PLANNED)
            outcome=res.get("UpdateResults") or []
            result["api_accepted"]=(len(outcome)==1 and outcome[0].get("Id")==TARGET and not outcome[0].get("Errors"))
        except Exception as e:result["errors"].append(str(e)[:250])
        after_c,after_k=current()
        others,target=test(before_k,after_k,planned)
        result.update(after_categories=flags(after_k)[1],other_keywords_unchanged=others,
                      exact_target_only=target,campaign_unchanged=(after_c==before_c),verified_utc=now())
        result["status"]="applied_verified" if (others and target and after_c==before_c) else "requires_manual_review"
        return 0 if result["status"]=="applied_verified" else 1
    except Exception as e:
        result["status"]="blocked_or_unverified";result["errors"].append(str(e)[:250]);return 1
    finally:
        result["finished_utc"]=now();save(result)
        print("With-board Exact-only",result["status"],"write_attempted",result["write_attempted"],flush=True)
if __name__=="__main__":sys.exit(run())
