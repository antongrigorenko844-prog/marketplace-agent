"""Guarded targeted exact exclusions for three observed unqualified LEOPARTS queries.

Only campaign 716316525, previous 136 negatives preserved; no budget/bid/keyword/ad changes.
Never retry a write after an uncertain outcome.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import re
import sys
import urllib.error
import urllib.request

CID=716316525
NAME="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест"
API="https://api.direct.yandex.com/json/v501/"
BASELINE=Path("data/direct_optimization_20261120/oil_change_negative_apply.json")
TRAFFIC=Path("data/direct_optimization_20261120/live_traffic_review.json")
OUT=Path("data/direct_optimization_20261120/specific_noise_negatives_20261121.json")
TERMS=('"сколько стоит плата от машины"','"момент затяжки мехатроника dq200"','"дром запчасти"')
MAX_BYTES=2*1024*1024
CAMPAIGN_PARAMS={"SelectionCriteria":{"Ids":[CID]},
                 "FieldNames":["Id","Name","Type","State","Status","Currency","NegativeKeywords"],
                 "UnifiedCampaignFieldNames":["BiddingStrategy","CounterIds","PriorityGoals","AttributionModel"],
                 "Page":{"Limit":1000}}
KEYWORDS_PARAMS={"SelectionCriteria":{"CampaignIds":[CID]},
                 "FieldNames":["Id","CampaignId","AdGroupId","Keyword","State","Status"],
                 "Page":{"Limit":1000}}
GROUPS_PARAMS={"SelectionCriteria":{"CampaignIds":[CID]},
               "FieldNames":["Id","CampaignId","Name","Status","NegativeKeywords"],
               "Page":{"Limit":1000}}
SAFE_CALLS={"campaigns":CAMPAIGN_PARAMS,"keywords":KEYWORDS_PARAMS,"adgroups":GROUPS_PARAMS}
ALLOWED_WRITE=None
WRITE_CALLED=False
def now():return datetime.now(timezone.utc).isoformat()
def safe_error(e):
    message=str(e)
    for k,v in os.environ.items():
        if len(v)>8 and any(t in k for t in ("TOKEN","SECRET","API_KEY","PASSWORD")):message=message.replace(v,"[redacted]")
    return message[:380]
def save(rep):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(rep,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUT)
def fingerprint(items):
    return hashlib.sha256(json.dumps(items,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()
def request(service,method,params):
    global WRITE_CALLED
    if method=="get" and service in SAFE_CALLS and params==SAFE_CALLS[service]:
        pass
    elif method=="update" and service=="campaigns" and ALLOWED_WRITE is not None and params==ALLOWED_WRITE and not WRITE_CALLED:
        WRITE_CALLED=True
    else:
        raise RuntimeError("API request outside allowed campaign-only scope")
    token=os.getenv("YANDEX_DIRECT_TOKEN","")
    if not token:raise RuntimeError("Direct token absent")
    headers={"Authorization":"Bearer "+token,"Accept-Language":"en","Content-Type":"application/json; charset=utf-8"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):headers["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    req=urllib.request.Request(API+service,data=json.dumps({"method":method,"params":params},ensure_ascii=False).encode(),headers=headers,method="POST")
    try:
        with urllib.request.urlopen(req,timeout=50) as response:raw=response.read(MAX_BYTES+1)
    except urllib.error.HTTPError as e:
        code=e.code;e.close()
        raise RuntimeError(service+"."+method+" HTTP "+str(code)) from None
    except Exception:
        raise RuntimeError(service+"."+method+" transport error") from None
    if len(raw)>MAX_BYTES:raise RuntimeError("Oversize response")
    try: data=json.loads(raw)
    except (ValueError,UnicodeError):raise RuntimeError("Invalid JSON response") from None
    if data.get("error") or not isinstance(data.get("result"),dict):
        raise RuntimeError(service+"."+method+" API error "+str((data.get("error") or {}).get("error_code","missing result")))
    return data["result"]
def read_campaign():
    result=request("campaigns","get",CAMPAIGN_PARAMS)
    x=result.get("Campaigns")
    if result.get("LimitedBy") or not isinstance(x,list) or len(x)!=1:raise RuntimeError("Unexpected campaign inventory")
    c=x[0]
    if (c.get("Id")!=CID or c.get("Name")!=NAME or c.get("Type")!="UNIFIED_CAMPAIGN"
        or c.get("State")!="ON" or c.get("Status")!="ACCEPTED"):
        raise RuntimeError("Campaign identity/type/state mismatch")
    bidding=((c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {})
    if ((bidding.get("Search") or {}).get("BiddingStrategyType")!="HIGHEST_POSITION"
        or (bidding.get("Network") or {}).get("BiddingStrategyType")!="SERVING_OFF"
        or ((bidding.get("Search") or {}).get("HighestPosition") or {}).get("WeeklySpendLimit") is not None):
        raise RuntimeError("Weekly cap removal or search-only strategy was changed unexpectedly")
    neg=(c.get("NegativeKeywords") or {}).get("Items")
    if not isinstance(neg,list) or any(not isinstance(v,str) for v in neg) or len(set(neg))!=len(neg):
        raise RuntimeError("Invalid negative phrase list")
    return c
def checked_inventory():
    ks=request("keywords","get",KEYWORDS_PARAMS)
    gs=request("adgroups","get",GROUPS_PARAMS)
    kw=ks.get("Keywords"); groups=gs.get("AdGroups")
    if (ks.get("LimitedBy") or gs.get("LimitedBy")
        or not isinstance(kw,list) or len(kw)!=272
        or not isinstance(groups,list) or len(groups)!=6
        or any(k.get("CampaignId")!=CID for k in kw+groups)):
        raise RuntimeError("Keyword/group inventory unexpected")
    manual=[k for k in kw if k.get("Keyword")!="---autotargeting"]
    if len(manual)!=266:raise RuntimeError("Expected 266 manual keywords")
    # Conservative guard: block if all words of an exclusion appear in a manual keyword.
    # Any unexpected ambiguity must be handled by review, not by a destructive update.
    def toks(s):return set(re.findall(r"[a-zа-яё0-9]+",str(s).casefold()))
    overlaps=[]
    for phrase in TERMS:
        for k in manual:
            if toks(phrase).issubset(toks(k.get("Keyword",""))):
                overlaps.append({"negative":phrase,"keyword":k.get("Keyword"),"id":k.get("Id")})
    if overlaps:
        raise RuntimeError("Potential useful manual keyword overlap: "+repr(overlaps[:3]))
    return {"manual_keywords":len(manual),"all_keywords":len(kw),"groups":len(groups)}

def nonnegative(c):
    return {k:v for k,v in c.items() if k not in ("NegativeKeywords","Status")}

def main():
    global ALLOWED_WRITE
    if OUT.exists():
        try:
            previous=json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            print("Prior journal unreadable; refusing retry");return 1
        if previous.get("write_attempted"):
            print("Prior write attempt recorded: never retry blind");return 1
    rep={"status":"preflight","mode":"guarded_three_exact_negative_phrases",
         "campaign_id":CID,"new_phrases":list(TERMS),
         "started_utc":now(),"write_attempted":False,"mutations":0,
         "workflow_sha":os.getenv("GITHUB_SHA"),"errors":[]}
    save(rep)
    try:
        if len(TERMS)!=3 or len(set(TERMS))!=3 or any(
            not(t.startswith('"') and t.endswith('"') and len(t.strip('"').split())<=7)
            for t in TERMS
        ):
            raise RuntimeError("Expected only three explicit quoted phrases")
        if not BASELINE.exists() or not TRAFFIC.exists():
            raise RuntimeError("Baseline/audit evidence unavailable")
        base=json.loads(BASELINE.read_text(encoding="utf-8"))
        last=json.loads(TRAFFIC.read_text(encoding="utf-8"))
        if (base.get("status")!="applied_verified" or not base.get("no_extra_negatives")
            or not base.get("nonnegative_campaign_fields_unchanged")
            or base.get("after_count")!=136 or base.get("errors")
            or last.get("status")!="complete" or last.get("campaign_id")!=CID
            or last.get("negatives_count")!=136 or last.get("manual_keywords")!=266
            or last.get("weekly_limit_raw") is not None or last.get("errors")):
            raise RuntimeError("Stale/incompatible verified baseline")
        old136=(base.get("before") or {}).get("NegativeKeywords",{}).get("Items")
        if not isinstance(old136,list) or len(old136)!=135:
            raise RuntimeError("Previous applied baseline original 135 missing")
        old136=old136+["замена масла"]
        evidence=[]
        for phrase in TERMS:
            q=phrase[1:-1]
            found=[row for row in (last.get("query_paid") or [])
                   if str(row.get("Query","")).casefold()==q.casefold()]
            if len(found)!=1 or any(
                float(r.get("Cost",0))<=0
                or int(r.get("Clicks",0))<=0
                or float(r.get("Conversions",0))!=0
                for r in found
            ):
                raise RuntimeError("Missing zero-conversion paid query evidence: "+q)
            evidence.append({"query":q,"historical_cost_rub":found[0]["Cost"],
                             "historical_clicks":found[0]["Clicks"],
                             "observed_conversions":found[0]["Conversions"]})
        rep["evidence"]=evidence
        before=read_campaign()
        neg=(before.get("NegativeKeywords") or {}).get("Items") or []
        if (len(neg)!=136 or fingerprint(neg)!=base.get("after_sha256")
            or Counter(neg)!=Counter(old136)):
            if all(t in neg for t in TERMS):
                rep.update(status="already_present_no_write",before_count=len(neg))
                return 0
            raise RuntimeError("Live campaign differs from expected 136-term state")
        if any(t in neg for t in TERMS):
            raise RuntimeError("Unexpected partial prior application")
        inventory=checked_inventory()
        expected=neg+list(TERMS)
        if (len(set(expected))!=139 or
            sum(len(t.replace(" ","")) for t in expected)>20000):
            raise RuntimeError("Proposed negative phrase list invalid")
        rep.update(before_count=136,before_fingerprint=fingerprint(neg),
                   proposed_count=139,proposed_fingerprint=fingerprint(expected),
                   protected_inventory=inventory,
                   scope_note="Append only 3 exact zero-conversion irrelevant phrases; preserve 266 manual keywords and all bidding/ads.")
        save(rep)
        latest=read_campaign()
        if latest!=before:
            raise RuntimeError("Campaign changed during preflight; no write")
        payload={"Campaigns":[{"Id":CID,"NegativeKeywords":{"Items":expected}}]}
        ALLOWED_WRITE=payload
        rep["write_attempted"]=True
        rep["mutations"]=1
        rep["attempt_started_utc"]=now()
        save(rep)
        try:
            response=request("campaigns","update",payload)
            u=response.get("UpdateResults")
            rep["api_accepted"]=bool(isinstance(u,list) and len(u)==1 and
                                     u[0].get("Id")==CID and not u[0].get("Errors"))
        except Exception as e:
            rep["api_uncertain_error"]=safe_error(e)
        observed=read_campaign()
        current=(observed.get("NegativeKeywords") or {}).get("Items") or []
        rep.update(after_count=len(current),
                   after_fingerprint=fingerprint(current),
                   all_previous_136_preserved=all(current.count(x)==1 for x in neg),
                   all_three_new_present=all(current.count(t)==1 for t in TERMS),
                   no_extras=Counter(current)==Counter(expected),
                   all_other_campaign_fields_unchanged=nonnegative(observed)==nonnegative(before),
                   verified_utc=now())
        if all((rep["all_previous_136_preserved"],rep["all_three_new_present"],
                rep["no_extras"],rep["all_other_campaign_fields_unchanged"])):
            rep["status"]="applied_verified"
            return 0
        rep["status"]="after_requires_review"
        return 1
    except Exception as e:
        rep["errors"].append(safe_error(e))
        rep["status"]="blocked_or_failed_no_retry"
        return 1
    finally:
        rep["finished_utc"]=now()
        save(rep)
        print("EXACT_NOISE_EXCLUSIONS",rep["status"],
              "write_attempted",rep["write_attempted"],
              "negatives_after",rep.get("after_count"),flush=True)

if __name__=="__main__":
    sys.exit(main())
