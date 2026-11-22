"""LEOPARTS DQ200 with-board group only: exclude generic parts-search words.
Preserve all campaign negatives, 266 manual keywords, other groups, bids and ads.
"""
from datetime import datetime,timezone
from pathlib import Path
from copy import deepcopy
from collections import Counter
import re,sys,json,os,urllib.request,urllib.error
CID=716316525
GID=5826183946
CAP=4000000000
TERMS=["запчасти","автозапчасти"]
BASE="https://api.direct.yandex.com/json/v501/"
OUT=Path("data/direct_recovery_20261122/with_board_generic_parts_exclusion.json")
CG={"SelectionCriteria":{"Ids":[CID]},"FieldNames":["Id","Name","State","Type","NegativeKeywords"],
    "UnifiedCampaignFieldNames":["BiddingStrategy"],"Page":{"Limit":1000}}
GR={"SelectionCriteria":{"CampaignIds":[CID]},
    "FieldNames":["Id","CampaignId","Name","Status","NegativeKeywords","RegionIds","NegativeKeywordSharedSetIds","TrackingParams"],"Page":{"Limit":1000}}
KW={"SelectionCriteria":{"CampaignIds":[CID]},
    "FieldNames":["Id","CampaignId","AdGroupId","Keyword","State","Status"],"Page":{"Limit":1000}}
WRITE_ONCE=False
EXPECTED=None
def now():return datetime.now(timezone.utc).isoformat()
def save(x):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    t=OUT.with_suffix(".tmp")
    t.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8");t.replace(OUT)
def api(service,method,payload):
    global WRITE_ONCE
    if method=="get" and ((service=="campaigns" and payload==CG) or (service=="adgroups" and payload==GR) or (service=="keywords" and payload==KW)):pass
    elif method=="update" and service=="adgroups" and payload==EXPECTED and not WRITE_ONCE:
        WRITE_ONCE=True
    else:raise RuntimeError("Forbidden or repeated operation")
    token=os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("Direct token missing")
    hdr={"Authorization":"Bearer "+token,"Content-Type":"application/json; charset=utf-8","Accept-Language":"en"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):hdr["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    req=urllib.request.Request(BASE+service,method="POST",headers=hdr,data=json.dumps({"method":method,"params":payload},ensure_ascii=False).encode())
    try:
        with urllib.request.urlopen(req,timeout=55) as r:
            blob=r.read(3*1024*1024+1)
            if len(blob)>3*1024*1024:raise RuntimeError("Oversize response")
            data=json.loads(blob)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError("API HTTP "+str(code)) from None
    except Exception:raise RuntimeError("API transport/parse failure, do not retry update") from None
    if data.get("error"):raise RuntimeError("API error "+str(data["error"].get("error_code")))
    v=data.get("result")
    if not isinstance(v,dict) or v.get("LimitedBy"):raise RuntimeError("Incomplete response")
    return v
def read():
    c=api("campaigns","get",CG).get("Campaigns")
    g=api("adgroups","get",GR).get("AdGroups")
    k=api("keywords","get",KW).get("Keywords")
    if not isinstance(c,list) or len(c)!=1 or not isinstance(g,list) or len(g)!=6 or not isinstance(k,list) or len(k)!=272:
        raise RuntimeError("Inventory count changed")
    c=c[0]
    if c.get("Id")!=CID or c.get("Name")!="Leoparts | DQ200 | Товарная ЕПК | Поиск | Тест" or c.get("State")!="ON" or c.get("Type")!="UNIFIED_CAMPAIGN":
        raise RuntimeError("Campaign identity/state mismatch")
    s=(c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}
    if (((s.get("Search") or {}).get("HighestPosition") or {}).get("WeeklySpendLimit")!=CAP or
        (s.get("Search") or {}).get("BiddingStrategyType")!="HIGHEST_POSITION" or
        (s.get("Network") or {}).get("BiddingStrategyType")!="SERVING_OFF"):
        raise RuntimeError("Campaign budget/strategy changed")
    n=(c.get("NegativeKeywords") or {}).get("Items")
    if not isinstance(n,list) or len(n)<626:raise RuntimeError("Campaign negatives inventory unexpectedly changed")
    if sum(x.get("Keyword")=="---autotargeting" for x in k)!=6 or len(set(x["Id"] for x in k))!=272:
        raise RuntimeError("Keyword inventory has drifted")
    if any(x.get("CampaignId")!=CID for x in g+k):raise RuntimeError("Cross-campaign data")
    return c,sorted(g,key=lambda a:a["Id"]),sorted(k,key=lambda a:a["Id"])
def main():
    global EXPECTED
    result={"status":"preflight","campaign":CID,"group":GID,"terms":TERMS,
            "started_utc":now(),"write_attempted":False,"errors":[]}
    save(result)
    try:
        c,g,k=read()
        group=next((x for x in g if x["Id"]==GID),None)
        if not group or group.get("Name")!="DQ200 | Мехатроники с платой | Поиск":
            raise RuntimeError("With-board group identity not confirmed")
        before=list((group.get("NegativeKeywords") or {}).get("Items") or [])
        if len(set(before))!=len(before):raise RuntimeError("Group negatives duplicated")
        from_group=[x["Keyword"] for x in k if x["AdGroupId"]==GID and x["Keyword"]!="---autotargeting"]
        conflicts=[kw for kw in from_group if any(term in set(re.findall(r"[a-zа-яё0-9]+",kw.lower())) for term in TERMS)]
        if conflicts:raise RuntimeError("Generic negative may block protected manual keyword: "+repr(conflicts[:3]))
        result.update(group_negatives_before=before,manual_keywords_in_group=len(from_group),
                      validated_no_keyword_overlap=True,all_keywords=272,campaign_negatives=len(c["NegativeKeywords"]["Items"]))
        desired=before+[x for x in TERMS if x not in before]
        if desired==before:result["status"]="already_applied";return 0
        if len(set(desired))!=len(desired) or sum(len(x.replace(" ","")) for x in desired)>4096:
            raise RuntimeError("Invalid negative phrases")
        result["desired_group_negatives"]=desired
        EXPECTED={"AdGroups":[{"Id":GID,"NegativeKeywords":{"Items":desired}}]}
        save(result)
        cc,gg,kk=read()
        if cc!=c or gg!=g or kk!=k:raise RuntimeError("Concurrent changes detected")
        result["write_attempted"]=True;result["write_started_utc"]=now();save(result)
        try:
            reply=api("adgroups","update",EXPECTED)
            info=reply.get("UpdateResults") or []
            result["api_accepted"]=len(info)==1 and info[0].get("Id")==GID and not info[0].get("Errors")
        except Exception as e:result["errors"].append(str(e)[:250])
        nc,ng,nk=read()
        ngroup=next(x for x in ng if x["Id"]==GID)
        actual=(ngroup.get("NegativeKeywords") or {}).get("Items") or []
        intact=all(x==y for x,y in zip([z for z in g if z["Id"]!=GID],[z for z in ng if z["Id"]!=GID]))
        expected_group=deepcopy(group);expected_group["NegativeKeywords"]={"Items":desired}
        result.update(group_negatives_after=actual,campaign_unchanged=(nc==c),
            manual_keywords_unchanged=(nk==k),other_groups_unchanged=intact,
            only_target_field_changed=(ngroup==expected_group),verified_utc=now())
        ok=Counter(actual)==Counter(desired) and nc==c and nk==k and intact and ngroup==expected_group
        result["status"]="applied_verified" if ok else "requires_review"
        return 0 if ok else 1
    except Exception as e:
        result["status"]="blocked_or_unverified";result["errors"].append(str(e)[:280]);return 1
    finally:
        result["finished_utc"]=now();save(result)
        print("Generic parts group negatives",result["status"],result["write_attempted"],flush=True)
if __name__=="__main__":sys.exit(main())
