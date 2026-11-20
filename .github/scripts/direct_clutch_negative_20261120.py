"""One narrowly scoped LEOPARTS campaign-level exclusion of clutch queries.

Approved scope: campaign 716316525, append precisely the single unquoted word `сцепление` (all grammatical forms),
without altering or removing any of the current 134 phrases, and verify
read-back. No updates to budgets, bids, groups, ads, feed, or keywords.
Never blindly retry after an uncertain API outcome.
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
PLAN=Path("data/direct_optimization_20261120/info_service_negatives_apply.json")
OUT=Path("data/direct_optimization_20261120/clutch_negative_apply.json")
TERMS=("сцепление",)
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
    kw=ks.get("Keywords");groups=gs.get("AdGroups")
    if (ks.get("LimitedBy") or gs.get("LimitedBy") or not isinstance(kw,list) or not isinstance(groups,list)
        or len(kw)!=272 or len(groups)!=6 or any(x.get("CampaignId")!=CID for x in kw+groups)):
        raise RuntimeError("Keyword or group inventory changed")
    manual=[x for x in kw if x.get("Keyword")!="---autotargeting"]
    if len(manual)!=266:raise RuntimeError("Manual keyword count changed")
    # Broad negative can be ignored when its word is present in a manual key.
    # Check all grammatical variants conservatively, not just an exact token.
    clashing=[{"Id":k.get("Id"),"Keyword":k.get("Keyword"),"AdGroupId":k.get("AdGroupId")}
        for k in manual if re.search(r"сцепл[а-яё]*",str(k.get("Keyword","")).lower())]
    if clashing:
        raise RuntimeError("Manual keyword contains clutch root; avoid ignored negative rule: "
                           +repr(clashing[:5]))
    return {"groups":len(groups),"manual_keywords":len(manual),"all_keywords":len(kw)}
def nonnegative(c):
    return {k:v for k,v in c.items() if k not in ("NegativeKeywords","Status")}
def main():
    global ALLOWED_WRITE
    # An existing attempted write is immutable evidence; never repeat the write.
    if OUT.exists():
        try:
            prior=json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            print("Existing proof unreadable; refusing another write",flush=True)
            return 1
        if prior.get("write_attempted"):
            print("Prior attempt exists: refusing any repeated mutation",flush=True)
            return 1
    rep={"status":"preflight","mode":"append_one_broad_clutch_root","campaign_id":CID,
         "started_utc":now(),"new_phrases":list(TERMS),"write_attempted":False,
         "mutations":0,"workflow_sha":os.getenv("GITHUB_SHA"),"errors":[]}
    save(rep)
    try:
        if TERMS!=("сцепление",):
            raise RuntimeError("Unexpected requested negative")
        if not PLAN.exists():raise RuntimeError("Required read-only preflight absent")
        planned=json.loads(PLAN.read_text(encoding="utf-8"))
        if (planned.get("status")!="applied_verified" or planned.get("write_attempted") is not True
            or planned.get("after_count")!=134 or planned.get("before_count")!=132
            or planned.get("no_extra_negatives") is not True
            or planned.get("nonnegative_campaign_fields_unchanged") is not True
            or planned.get("errors")):
            raise RuntimeError("Prior 134-negative verified baseline does not match")
        previous_before=(planned.get("before") or {}).get("NegativeKeywords") or {}
        previous_original=previous_before.get("Items")
        old_two=('"ремонт дсг"','"признаки износа сцепления дсг 7 dq200"')
        if (not isinstance(previous_original,list) or len(previous_original)!=132
            or any(term in previous_original for term in old_two)):
            raise RuntimeError("Prior evidence does not contain the required 132 original phrases")
        expected_previous=previous_original+list(old_two)
        if fingerprint(expected_previous)!=planned.get("after_sha256"):
            raise RuntimeError("Prior campaign fingerprint failed")
        before=read_campaign()
        neg=before["NegativeKeywords"]["Items"]
        if (len(neg)!=134 or neg!=expected_previous
            or fingerprint(neg)!=planned.get("after_sha256")):
            if "сцепление" in neg:
                rep.update(status="already_present_no_write",before_count=len(neg))
                return 0
            raise RuntimeError("Current 134 negatives differ from confirmed baseline")
        bidding=((before.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {})
        if (bidding.get("Search",{}).get("BiddingStrategyType")!="HIGHEST_POSITION"
            or bidding.get("Network",{}).get("BiddingStrategyType")!="SERVING_OFF"
            or bidding.get("Search",{}).get("HighestPosition",{}).get("WeeklySpendLimit") is not None):
            raise RuntimeError("Campaign weekly budget or strategy changed unexpectedly")
        rep["baseline"]= {"source":str(PLAN),"before_count":len(neg),
                           "fingerprint":fingerprint(neg),
                           "protected_manual_keys":266}
        if any(t in neg for t in TERMS):raise RuntimeError("Partial application / unexpected exact negative")
        inv=checked_inventory()
        proposed=neg+list(TERMS)
        if len(set(proposed))!=135 or sum(len(p.replace(" ","")) for p in proposed)>20000:
            raise RuntimeError("Proposed negatives invalid or too long")
        rep.update(before=before,before_count=134,before_sha256=fingerprint(neg),
            proposed_sha256=fingerprint(proposed),inventory=inv,
            scope_note="Only unquoted single-word сцепление appended; all 134 pre-existing negative phrases preserved")
        save(rep)
        fresh=read_campaign()
        if fresh["NegativeKeywords"]["Items"]!=neg or nonnegative(fresh)!=nonnegative(before):
            raise RuntimeError("Campaign changed between preflight and final pre-write GET")
        payload={"Campaigns":[{"Id":CID,"NegativeKeywords":{"Items":proposed}}]}
        ALLOWED_WRITE=payload
        rep["write_attempted"]=True;rep["mutations"]=1;rep["attempt_started_utc"]=now();save(rep)
        try:
            res=request("campaigns","update",payload)
            u=res.get("UpdateResults")
            if not isinstance(u,list) or len(u)!=1 or u[0].get("Id")!=CID or u[0].get("Errors"):
                rep["api_result_valid"]=False
            else: rep["api_result_valid"]=True
        except Exception as e:
            rep["update_uncertain_error"]=safe_error(e)
        observed=read_campaign()
        current=observed["NegativeKeywords"]["Items"]
        rep.update(after_count=len(current),after_sha256=fingerprint(current),
            unchanged_original_134=all(current.count(x)==1 for x in neg),
            single_clutch_word_present=(current.count("сцепление")==1),
            no_extra_negatives=Counter(current)==Counter(proposed),
            nonnegative_campaign_fields_unchanged=(nonnegative(observed)==nonnegative(before)),
            finished_utc=now())
        if rep["no_extra_negatives"] and rep["single_clutch_word_present"] and rep["nonnegative_campaign_fields_unchanged"] :
            rep["status"]="applied_verified"+("_after_uncertain_response" if rep.get("update_uncertain_error") else "")
            return 0
        rep["status"]="unexpected_after_requires_review"
        return 1
    except Exception as e:
        rep["errors"].append(safe_error(e))
        rep["status"]="blocked_or_failed_no_retry"
        return 1
    finally:
        rep["finished_utc"]=now()
        save(rep)
        print("Direct all-clutch campaign negative:",rep["status"],"write_attempted",rep["write_attempted"],"after",rep.get("after_count"),flush=True)
if __name__=="__main__":sys.exit(main())
