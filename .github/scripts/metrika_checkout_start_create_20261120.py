"""Guarded Metrica operation: create a distinct checkout START goal (NOT a purchase).
Counter 113839860 only. Never changes old goals/Direct and never retries a
write without reconciling its saved proof. User granted write access.
"""
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import json, os, sys, urllib.error, urllib.request

CID=113839860
ENDPOINT=f"https://api-metrika.yandex.net/management/v1/counter/{CID}"
PRE=Path("data/direct_optimization_20261120/metrika_write_access_check.json")
OBS=Path("data/direct_optimization_20261120/metrika_tilda_virtual_pageviews.json")
OUT=Path("data/direct_optimization_20261120/metrika_checkout_start_create.json")
GOAL_NAME="LEOPARTS | Начало оплаты (не покупка)"
GOAL_PATH="/tilda/form5260805101/payment/"
PAYLOAD={"goal":{"name":GOAL_NAME,"type":"url","conditions":[{"type":"contain","url":GOAL_PATH}]}}
ALLOW_CREATE=False
POST_CALLED=False
def utc():return datetime.now(timezone.utc).isoformat()
def save(x):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUT)
def request(method,path,payload=None):
    global POST_CALLED
    if method=="GET" and path in (ENDPOINT,ENDPOINT+"/goals") and payload is None:
        pass
    elif (method=="POST" and path==ENDPOINT+"/goals"
          and ALLOW_CREATE and not POST_CALLED and payload==PAYLOAD):
        POST_CALLED=True
    else:
        raise RuntimeError("Unexpected API action: this script only creates one checkout-start goal")
    token=os.getenv("YANDEX_METRIKA_WRITE_TOKEN")
    if not token:raise RuntimeError("WRITE OAuth missing")
    h={"Authorization":"OAuth "+token,"Accept":"application/json"}
    body=None
    if method=="POST":
        h["Content-Type"]="application/json; charset=utf-8"
        body=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(path,method=method,headers=h,data=body)
    try:
        with urllib.request.urlopen(req,timeout=45) as response:
            raw=response.read(524288)
    except urllib.error.HTTPError as e:
        code=e.code;e.close()
        raise RuntimeError("Metrica "+method+" HTTP "+str(code)) from None
    except Exception as e:
        raise RuntimeError("Metrica transport "+type(e).__name__) from None
    try:
        data=json.loads(raw.decode("utf-8-sig"))
    except Exception:
        raise RuntimeError("Metrica response invalid JSON") from None
    if not isinstance(data,dict):raise RuntimeError("Missing response object")
    return data
def current():
    goals=request("GET",ENDPOINT+"/goals").get("goals")
    if not isinstance(goals,list) or not 2<=len(goals)<=100:
        raise RuntimeError("Unexpected goals inventory")
    ids=[g.get("id") for g in goals]
    if len(ids)!=len(set(ids)):raise RuntimeError("Duplicate goal IDs")
    return goals
def signature(goals):
    keys=("id","name","type","conditions","status","goal_source","is_favorite")
    return {g["id"]:{k:g.get(k) for k in keys} for g in goals}
def preflight():
    if not PRE.exists() or not OBS.exists():
        raise RuntimeError("Missing stored write-access or Tilda evidence")
    pre=json.loads(PRE.read_text(encoding="utf-8"))
    obs=json.loads(OBS.read_text(encoding="utf-8"))
    access=pre.get("checks",{}).get("counter",{})
    inventory=pre.get("checks",{}).get("goals",{})
    if not (pre.get("status")=="readable_and_account_edit_permission"
      and pre.get("counter_id")==CID and pre.get("token_present")
      and access.get("permission") in ("own","edit")
      and inventory.get("total_count")==12
      and inventory.get("checkout_start_goal_present") is False):
        raise RuntimeError("Write-access preflight not valid")
    rows=obs.get("sections",{}).get("tilda_forms",{}).get("data",{}).get("rows",[])
    observed=[x for x in rows if x.get("url_path","").endswith(GOAL_PATH)
             and x.get("pageviews",0)>=1]
    if obs.get("status")!="complete" or obs.get("counter_id")!=CID or len(observed)!=1:
        raise RuntimeError("Tilda checkout-start URL is not verified by Metrica")
    return {"source":"Metrica virtual pageviews","historical_event_count":observed[0]["pageviews"]}
def verify_prior(goals):
    if len(goals)!=12:raise RuntimeError("Goal count not expected 12")
    names={x.get("name") for x in goals}
    if GOAL_NAME in names:raise RuntimeError("Goal already named checkout-start")
    if any(g.get("type")=="url" and any(
        isinstance(c,dict) and c.get("url")==GOAL_PATH for c in g.get("conditions",[]))
           for g in goals):
        raise RuntimeError("Checkout-start URL already tracked by another goal")
    required={675506966:"/tilda/form5246286101/submitted/",
              675507028:"/tilda/form5260805101/submitted/"}
    by_id={x["id"]:x for x in goals}
    for gid,path in required.items():
        existing=by_id.get(gid,{})
        if (existing.get("type")!="url" or existing.get("conditions")!=
            [{"type":"contain","url":path}]):
            raise RuntimeError("Primary goal changed, will not mutate")
def main():
    global ALLOW_CREATE
    if OUT.exists():
        try:old=json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            print("Prior goal proof unreadable: no operation",flush=True)
            return 1
        if old.get("write_attempted"):
            print("Prior write attempt exists: do not retry",flush=True)
            return 1
    report={"status":"preflight","mode":"single_intermediate_checkout_goal",
            "counter_id":CID,"goal_name":GOAL_NAME,
            "path_contains":GOAL_PATH,"not_a_purchase":True,
            "user_requested_metrica_improvements":True,
            "started_utc":utc(),"write_attempted":False,
            "mutation_count":0,"errors":[]}
    save(report)
    try:
        report["evidence"]=preflight()
        counter=request("GET",ENDPOINT).get("counter",{})
        if counter.get("id")!=CID or counter.get("permission") not in ("own","edit"):
            raise RuntimeError("Counter permission changed")
        before=current()
        verify_prior(before)
        previous=signature(before)
        report["existing_goal_ids"]=sorted(previous)
        report["existing_goal_count"]=len(before)
        report["exact_payload"]=PAYLOAD
        save(report)
        # Final live read immediately before write.
        if signature(current())!=previous:
            raise RuntimeError("Concurrent goal changes detected")
        ALLOW_CREATE=True
        report["write_attempted"]=True
        report["mutation_count"]=1
        report["post_started_utc"]=utc()
        save(report)
        try:
            response=request("POST",ENDPOINT+"/goals",PAYLOAD)
            new_goal=response.get("goal",{})
            report["api_response_new_goal_id"]=new_goal.get("id")
            report["api_accepted"]=isinstance(new_goal.get("id"),int)
        except Exception as e:
            report["api_result_uncertain"]=str(e)[:180]
        after=current()
        final=signature(after)
        new_ids=set(final)-set(previous)
        report["after_goal_count"]=len(after)
        report["all_existing_goals_unchanged"]=all(final.get(g)==val for g,val in previous.items())
        matches=[g for g in after if g.get("name")==GOAL_NAME
                 and g.get("type")=="url"
                 and g.get("conditions")==PAYLOAD["goal"]["conditions"]]
        report["one_new_goal_added"]=len(after)==len(before)+1 and len(new_ids)==1
        report["exact_new_goal_verified"]=len(matches)==1 and matches[0]["id"] in new_ids
        report["new_goal_id"]=matches[0]["id"] if len(matches)==1 else None
        if (report["one_new_goal_added"] and report["exact_new_goal_verified"]
            and report["all_existing_goals_unchanged"]):
            report["status"]="applied_verified"
            return 0
        report["status"]="postwrite_review_required"
        return 1
    except Exception as e:
        report["status"]="blocked_or_failed_no_retry"
        report["errors"].append(str(e)[:240])
        return 1
    finally:
        report["finished_utc"]=utc()
        save(report)
        print("Metrica checkout-start goal:",report["status"],
              "write_attempted:",report["write_attempted"],
              "goal_id:",report.get("new_goal_id"),flush=True)
if __name__=="__main__":sys.exit(main())
