"""One tightly-scoped LEOPARTS negative-phrase adjustment.

Approved scope: campaign 716316525, append exactly two quoted queries
without altering or removing any of the current 130 phrases, and verify
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
PLAN=Path("data/direct_optimization_20261120/preflight.json")
OUT=Path("data/direct_optimization_20261120/exact_negatives_apply.json")
TERMS=('"02e мехатроник в сборе"','"ремонт дсг вологда"')
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
    def normalized(s):return set(re.sub(r'["+\[\]!-]',' ',str(s).lower()).split())
    for phrase in TERMS:
        for k in manual:
            if normalized(phrase).issubset(normalized(k.get("Keyword"))):
                raise RuntimeError("New negative may overlap manual keyword: "+str(k.get("Id")))
    return {"groups":len(groups),"manual_keywords":len(manual),"all_keywords":len(kw)}
def nonnegative(c):
    return {k:v for k,v in c.items() if k not in ("NegativeKeywords","Status")}
def main():
    global ALLOWED_WRITE
    rep={"status":"preflight","mode":"apply_only_two_exact_phrases","campaign_id":CID,
         "started_utc":now(),"new_phrases":list(TERMS),"write_attempted":False,
         "mutations":0,"workflow_sha":os.getenv("GITHUB_SHA"),"errors":[]}
    save(rep)
    try:
        if len(set(TERMS))!=2 or any(not(x.startswith('"') and x.endswith('"')) for x in TERMS):
            raise RuntimeError("Exact phrase scope invalid")
        if not PLAN.exists():raise RuntimeError("Required read-only preflight absent")
        planned=json.loads(PLAN.read_text(encoding="utf-8"))
        if (planned.get("status")!="preflight_verified_safe_to_propose" or planned.get("campaign_id")!=CID
            or planned.get("negative_count")!=130 or planned.get("candidates")!=list(TERMS)
            or planned.get("manual_keyword_count")!=266 or planned.get("autotargeting_condition_count")!=6
            or planned.get("external_mutations")!=0 or planned.get("errors")
            or len(planned.get("candidate_checks") or [])!=2
            or any(p.get("phrase") not in TERMS or p.get("present_exact") or p.get("lexical_keyword_overlap_approximation")
                   for p in planned["candidate_checks"])):
            raise RuntimeError("Read-only preflight does not permit exact two phrase operation")
        before=read_campaign()
        neg=before["NegativeKeywords"]["Items"]
        expected_pre=planned.get("negatives")
        if (len(neg)!=130 or neg!=expected_pre or fingerprint(neg)!=planned.get("negative_fingerprint_sha256")):
            if all(t in neg for t in TERMS):
                rep.update(status="already_present_no_write",before_count=len(neg));return 0
            raise RuntimeError("Current 130 negatives differ from strict preflight snapshot")
        if any(t in neg for t in TERMS):raise RuntimeError("Partial application / unexpected exact negative")
        if OUT.exists():
            previous=json.loads(OUT.read_text(encoding="utf-8"))
            if previous.get("write_attempted"):raise RuntimeError("Prior write attempted; no blind retry")
        inv=checked_inventory()
        proposed=neg+list(TERMS)
        if len(set(proposed))!=132 or sum(len(p.replace(" ","")) for p in proposed)>20000:
            raise RuntimeError("Proposed negatives invalid or too long")
        rep.update(before=before,before_count=130,before_sha256=fingerprint(neg),
            proposed_sha256=fingerprint(proposed),inventory=inv,
            "scope_note":"Only two quoted negatives added; no broad repair, board, or replacement words")
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
            unchanged_original_130=all(current.count(x)==1 for x in neg),
            exact_new_two=all(current.count(t)==1 for t in TERMS),
            no_extra_negatives=Counter(current)==Counter(proposed),
            nonnegative_campaign_fields_unchanged=(nonnegative(observed)==nonnegative(before)),
            finished_utc=now())
        if rep["no_extra_negatives"] and rep["nonnegative_campaign_fields_unchanged"]:
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
        print("Direct two exact negatives:",rep["status"],"write_attempted",rep["write_attempted"],"after",rep.get("after_count"),flush=True)
if __name__=="__main__":sys.exit(main())
