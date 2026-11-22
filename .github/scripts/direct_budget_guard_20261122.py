"""LEOPARTS 22 Nov 2026: safely restore old 4000-RUB weekly limit, only this campaign."""
from datetime import datetime, timezone
from pathlib import Path
from copy import deepcopy
import hashlib, json, os, sys, urllib.request, urllib.error
CID=716316525
NAME="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест"
CAP=4_000_000_000
API="https://api.direct.yandex.com/json/v501/campaigns"
OUT=Path("data/direct_recovery_20261122/budget_guard.json")
GET={"SelectionCriteria":{"Ids":[CID]},
     "FieldNames":["Id","Name","Type","State","Status","Currency","NegativeKeywords"],
     "UnifiedCampaignFieldNames":["BiddingStrategy","CounterIds","PriorityGoals","AttributionModel"],
     "Page":{"Limit":1000}}
UPDATE={"Campaigns":[{"Id":CID,"UnifiedCampaign":{"BiddingStrategy":{
    "Search":{"BiddingStrategyType":"HIGHEST_POSITION","HighestPosition":{"WeeklySpendLimit":CAP}},
    "Network":{"BiddingStrategyType":"SERVING_OFF"}}}}]}
attempted=False
def now():return datetime.now(timezone.utc).isoformat()
def save(o):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    f=OUT.with_suffix(".tmp");f.write_text(json.dumps(o,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");f.replace(OUT)
def api(method,payload):
    global attempted
    if method=="get" and payload==GET:pass
    elif method=="update" and payload==UPDATE and not attempted:attempted=True
    else:raise RuntimeError("unsafe API operation or retry")
    token=os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("Direct credential unavailable")
    h={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):h["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    req=urllib.request.Request(API,data=json.dumps({"method":method,"params":payload},ensure_ascii=False).encode(),headers=h,method="POST")
    try:
        with urllib.request.urlopen(req,timeout=50) as resp:
            raw=resp.read(512001)
            if len(raw)>512000:raise RuntimeError("oversize")
            response=json.loads(raw)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError("API HTTP "+str(code)) from None
    except Exception:raise RuntimeError("API transport or response failure; do not retry write") from None
    if response.get("error"):raise RuntimeError("API error "+str(response["error"].get("error_code")))
    v=response.get("result")
    if not isinstance(v,dict):raise RuntimeError("Invalid result")
    return v
def campaign():
    result=api("get",GET)
    r=result.get("Campaigns")
    if result.get("LimitedBy") or not isinstance(r,list) or len(r)!=1:raise RuntimeError("Campaign missing or duplicated")
    c=r[0]
    if (c.get("Id")!=CID or c.get("Name")!=NAME or c.get("Type")!="UNIFIED_CAMPAIGN" or c.get("Currency")!="RUB"):
        raise RuntimeError("Unexpected campaign identity")
    return c
def strategy(c):return (c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}
def limit(c):return ((strategy(c).get("Search") or {}).get("HighestPosition") or {}).get("WeeklySpendLimit")
def normalized(c):
    c=deepcopy(c);c.pop("State",None);c.pop("Status",None)
    search=c["UnifiedCampaign"]["BiddingStrategy"]["Search"]
    hp=search.get("HighestPosition")
    if hp is not None:
        hp.pop("WeeklySpendLimit",None)
        if not hp:search.pop("HighestPosition",None)
    return c
def digest(o):return hashlib.sha256(json.dumps(o,ensure_ascii=False,sort_keys=True).encode()).hexdigest()
def run():
    log={"status":"preflight","campaign":CID,"cap_rub":4000,"started_utc":now(),"write_attempted":False,"errors":[]}
    save(log)
    try:
        before=campaign();s=strategy(before)
        negatives=(before.get("NegativeKeywords") or {}).get("Items")
        if (before.get("State")!="ON" or (s.get("Search") or {}).get("BiddingStrategyType")!="HIGHEST_POSITION"
            or (s.get("Network") or {}).get("BiddingStrategyType")!="SERVING_OFF"
            or not isinstance(negatives,list) or len(negatives)<626):
            raise RuntimeError("Campaign state/targeting baseline mismatch")
        original=limit(before)
        log.update(before_cap_micro=original,negative_count=len(negatives),before_fingerprint=digest(normalized(before)))
        if original==CAP:log["status"]="already_guarded";return 0
        if original is not None:raise RuntimeError("Unexpected existing weekly limit: no overwrite")
        save(log)
        log.update(write_attempted=True,write_started_utc=now());save(log)
        try:
            result=api("update",UPDATE)
            rows=result.get("UpdateResults",[])
            log["api_accepted"]=len(rows)==1 and rows[0].get("Id")==CID and not rows[0].get("Errors")
        except Exception as e:log["errors"].append(str(e)[:250])
        after=campaign()
        log.update(after_cap_micro=limit(after),nonbudget_unchanged=normalized(after)==normalized(before),
            after_negative_count=len((after.get("NegativeKeywords") or {}).get("Items") or []),
            verified_utc=now())
        log["status"]="applied_verified" if limit(after)==CAP and log["nonbudget_unchanged"] else "requires_review"
        return 0 if log["status"]=="applied_verified" else 1
    except Exception as e:
        log["status"]="blocked_or_unverified";log["errors"].append(str(e)[:250]);return 1
    finally:
        log["finished_utc"]=now();save(log);print("Budget guard",log["status"],log["write_attempted"],flush=True)
if __name__=="__main__":sys.exit(run())
