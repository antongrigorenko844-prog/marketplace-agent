"""Remove (not raise) campaign 716316525 weekly budget when Direct API supports it.

User requested removing the weekly limit. One account, one campaign, preserve
search strategy, disable RСЯ as before, negative keywords and all other fields.
Tries documented-style null first, then empty HighestPosition only if the first
request explicitly failed AND readback verifies no change. Do not set fake
unlimited budget; do not change bidding strategy. No blind retry on unknown result.
"""
import json, os, sys, urllib.request, urllib.error, hashlib
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

CID=716316525
NAME="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест"
PREVIOUS_LIMIT=4000000000
API="https://api.direct.yandex.com/json/v501/campaigns"
OUTPUT=Path("data/direct_optimization_20261120/budget_limit_removal.json")
PARAMS={"SelectionCriteria":{"Ids":[CID]},
        "FieldNames":["Id","Name","Type","State","Status","Currency","NegativeKeywords"],
        "UnifiedCampaignFieldNames":["BiddingStrategy","CounterIds","PriorityGoals","AttributionModel"],
        "Page":{"Limit":1000}}
def stamp():return datetime.now(timezone.utc).isoformat()
def scrub(value):
    text=str(value)
    for k,v in os.environ.items():
        if len(v)>8 and any(w in k for w in ("TOKEN","SECRET","PASSWORD","API_KEY")):
            text=text.replace(v,"[redacted]")
    return text[:360]
def persist(report):
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUTPUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUTPUT)
def api(method,params):
    if method=="get" and params!=PARAMS:raise RuntimeError("Unsafe GET query")
    if method=="update":
        c=params.get("Campaigns",[])
        if (list(params)!=["Campaigns"] or len(c)!=1 or set(c[0])!={"Id","UnifiedCampaign"}
            or c[0].get("Id")!=CID or set(c[0]["UnifiedCampaign"])!={"BiddingStrategy"}):
            raise RuntimeError("Unsafe UPDATE object")
        st=c[0]["UnifiedCampaign"]["BiddingStrategy"]
        if set(st)!={"Search","Network"} or st["Search"].get("BiddingStrategyType")!="HIGHEST_POSITION":
            raise RuntimeError("Strategy change disallowed")
        if st["Network"]!={"BiddingStrategyType":"SERVING_OFF"}:
            raise RuntimeError("Network change disallowed")
        if set(st["Search"])!={"BiddingStrategyType","HighestPosition"}:
            raise RuntimeError("Unexpected Search mutation")
        hp=st["Search"]["HighestPosition"]
        if hp not in ({"WeeklySpendLimit":None},{}):
            raise RuntimeError("Only unset WeeklySpendLimit is allowed")
    if method not in ("get","update"):raise RuntimeError("Unsupported method")
    token=os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("Missing YANDEX_DIRECT_TOKEN")
    headers={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):headers["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    req=urllib.request.Request(API,method="POST",
       data=json.dumps({"method":method,"params":params},ensure_ascii=False).encode("utf-8"),headers=headers)
    try:
        with urllib.request.urlopen(req,timeout=55) as res:
            body=res.read(256000)
    except urllib.error.HTTPError as e:
        status=e.code
        body=e.read(256000)
        e.close()
        try:
            error=json.loads(body.decode("utf-8-sig")).get("error") or {}
            code=error.get("error_code")
            detail=error.get("error_detail") or error.get("error_string")
        except Exception:
            code=None;detail=None
        raise RuntimeError("HTTP "+str(status)+" error_code="+str(code)+" detail="+scrub(detail)) from None
    except Exception as e:
        raise RuntimeError("Transport error or response unavailable: "+scrub(type(e).__name__)) from None
    try:d=json.loads(body.decode("utf-8-sig"))
    except Exception:raise RuntimeError("Invalid JSON response") from None
    if d.get("error"):raise RuntimeError("API error_code="+str(d["error"].get("error_code"))+
         " detail="+scrub(d["error"].get("error_detail") or d["error"].get("error_string"))) from None
    if not isinstance(d.get("result"),dict):raise RuntimeError("No result object")
    return d["result"]
def get():
    result=api("get",PARAMS)
    rows=result.get("Campaigns")
    if result.get("LimitedBy") or not isinstance(rows,list) or len(rows)!=1:raise RuntimeError("Unexpected campaign retrieval")
    item=rows[0]
    if (item.get("Id")!=CID or item.get("Name")!=NAME or item.get("Type")!="UNIFIED_CAMPAIGN"
        or item.get("Currency")!="RUB"):
        raise RuntimeError("Campaign identity changed")
    return item
def search(c):
    return ((c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}).get("Search") or {}
def network(c):
    return ((c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}).get("Network") or {}
def limit(c):
    return (search(c).get("HighestPosition") or {}).get("WeeklySpendLimit")
def nonbudget(c):
    v=deepcopy(c)
    (((v.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}).get("Search") or {}).get("HighestPosition",{}).pop("WeeklySpendLimit",None)
    # Provider State/Status may change independently during review.
    v.pop("State",None);v.pop("Status",None)
    return v
def verify_before(c):
    neg=(c.get("NegativeKeywords") or {}).get("Items")
    if (c.get("State")!="ON" or c.get("Status")!="ACCEPTED"
        or search(c).get("BiddingStrategyType")!="HIGHEST_POSITION"
        or network(c).get("BiddingStrategyType")!="SERVING_OFF"
        or limit(c)!=PREVIOUS_LIMIT or not isinstance(neg,list) or len(neg)!=132):
        raise RuntimeError("Live state differs from exact expected 4,000-RUB campaign baseline")
    return neg
def payload(variant):
    hp={"WeeklySpendLimit":None} if variant=="explicit_null" else {}
    return {"Campaigns":[{"Id":CID,"UnifiedCampaign":{
        "BiddingStrategy":{"Search":{"BiddingStrategyType":"HIGHEST_POSITION","HighestPosition":hp},
                           "Network":{"BiddingStrategyType":"SERVING_OFF"}}}}]}
def run():
    if OUTPUT.exists():
        try:prior=json.loads(OUTPUT.read_text(encoding="utf-8"))
        except Exception:print("Existing result unreadable, refusing repeat");return 1
        if any(x.get("update_attempted") for x in prior.get("attempts",[])):
            print("Prior change attempt found, refusing blind repeat");return 1
    report={"status":"started","requested_change":"remove_campaign_weekly_budget_only",
            "campaign_id":CID,"started_utc":stamp(),"attempts":[],
            "old_limit_rub":4000,"new_limit_rub":None,"errors":[],
            "other_campaigns_touched":False,"change_strategy":False}
    persist(report)
    try:
        before=get()
        verify_before(before)
        report["state_before"]=before.get("State")
        report["negative_count_before"]=len((before.get("NegativeKeywords") or {}).get("Items") or [])
        report["unchanged_fingerprint"]=hashlib.sha256(json.dumps(nonbudget(before),ensure_ascii=False,sort_keys=True).encode()).hexdigest()
        persist(report)
        variants=["explicit_null","empty_highest_position"]
        for i,variant in enumerate(variants):
            entry={"variant":variant,"started_utc":stamp(),"update_attempted":True}
            report["attempts"].append(entry)
            persist(report)
            try:
                response=api("update",payload(variant))
                rec=response.get("UpdateResults")
                if (not isinstance(rec,list) or len(rec)!=1 or rec[0].get("Id")!=CID
                    or rec[0].get("Errors")):
                    entry["status"]="response_not_confirmed"
                else:
                    entry["status"]="accepted"
            except Exception as e:
                entry["status"]="error";entry["error"]=scrub(e)
            entry["finished_utc"]=stamp()
            persist(report)
            # ALWAYS reconcile readback; never assume an API error means no changes.
            after=get()
            current=limit(after)
            entry["readback_weekly_limit_raw"]=current
            entry["same_non_budget_fields"]=nonbudget(after)==nonbudget(before)
            entry["negative_count_after"]=len((after.get("NegativeKeywords") or {}).get("Items") or [])
            persist(report)
            if not entry["same_non_budget_fields"]:
                raise RuntimeError("Unexpected non-budget change: no further action")
            if current is None:
                report.update(status="weekly_limit_removed_verified",verified_at_utc=stamp(),
                              new_limit_rub=None)
                return 0
            if current!=PREVIOUS_LIMIT:
                raise RuntimeError("Unexpected nonzero weekly limit after update: "+str(current))
            # Only attempt next format when first failed or was a verified no-op.
            if variant=="explicit_null" and entry["status"]=="accepted":
                # Accepted but left old value: retrying other format is unsafe.
                break
        report["status"]="no_supported_unlimited_setting_confirmed"
        report["new_limit_rub"]=4000
        return 0
    except Exception as e:
        report["status"]="blocked_or_unverified"
        report["errors"].append(scrub(e))
        return 1
    finally:
        report["finished_utc"]=stamp()
        persist(report)
        print("Budget cap removal:",report["status"],"attempts",len(report["attempts"]),
              "new_limit_rub",report.get("new_limit_rub"),flush=True)
if __name__=="__main__":sys.exit(run())
