"""Create ONLY one dormant JS event goal for verified Tilda VIN success.

Counter 113839860; old 13 goals unchanged. No changes to Direct bidding,
primary URL goals, payment, or Tilda; the event will only work once the site
snippet is published in Tilda and successfully submits VIN. Never blind retry.
"""
from datetime import datetime, timezone
from pathlib import Path
import json, os, sys, urllib.error, urllib.request

COUNTER=113839860
BASE="https://api-metrika.yandex.net"
URL=f"{BASE}/management/v1/counter/{COUNTER}"
PRIOR=Path("data/direct_optimization_20261120/metrika_checkout_start_create.json")
DOM=Path("data/direct_optimization_20261120/tilda_browser_vin_dom.json")
HTML=Path("data/direct_optimization_20261120/TILDA_VIN_SUCCESS_SNIPPET.html")
OUT=Path("data/direct_optimization_20261120/metrika_vin_js_goal_create.json")
NAME="LEOPARTS | VIN — успешная отправка"
IDENTIFIER="LEOPARTS_VIN_SUCCESS_5246286101"
PAYLOAD={"goal":{"name":NAME,"type":"action",
                 "conditions":[{"type":"exact","url":IDENTIFIER}]}}
ALLOWED_POST=None
POST_CALLED=False

def utc():return datetime.now(timezone.utc).isoformat()
def save(obj):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUT)
def request(method,url,payload=None):
    global POST_CALLED
    if method=="GET" and url in (URL,URL+"/goals") and payload is None:
        pass
    elif method=="POST" and url==URL+"/goals" and ALLOWED_POST==PAYLOAD and payload==PAYLOAD and not POST_CALLED:
        POST_CALLED=True
    else:
        raise RuntimeError("Outside exact one-action-goal operation")
    token=os.getenv("YANDEX_METRIKA_WRITE_TOKEN")
    if not token:raise RuntimeError("Write token not available")
    headers={"Authorization":"OAuth "+token,"Accept":"application/json"}
    data=None
    if method=="POST":
        headers["Content-Type"]="application/json; charset=utf-8"
        data=json.dumps(payload,ensure_ascii=False).encode("utf-8")
    req=urllib.request.Request(url,data=data,headers=headers,method=method)
    try:
        with urllib.request.urlopen(req,timeout=40) as response:
            raw=response.read(524288)
    except urllib.error.HTTPError as e:
        code=e.code;e.close()
        raise RuntimeError("Metrica "+method+" HTTP "+str(code)) from None
    except Exception as e:
        raise RuntimeError("Metrica transport error "+type(e).__name__) from None
    try:
        obj=json.loads(raw.decode("utf-8-sig"))
    except Exception:
        raise RuntimeError("Invalid JSON response") from None
    if not isinstance(obj,dict):raise RuntimeError("Response is not object")
    return obj

def goals():
    doc=request("GET",URL+"/goals")
    items=doc.get("goals")
    if not isinstance(items,list) or not 1<=len(items)<=100:
        raise RuntimeError("Unexpected goal inventory")
    if len(set(g.get("id") for g in items))!=len(items):
        raise RuntimeError("Duplicate goal IDs")
    return items

def sig(items):
    fields=("id","name","type","conditions","status","goal_source","is_favorite")
    return {g["id"]:{f:g.get(f) for f in fields} for g in items}

def preflight():
    if not PRIOR.exists() or not DOM.exists() or not HTML.exists():
        raise RuntimeError("Missing prerequisite evidence/snippet")
    previous=json.loads(PRIOR.read_text(encoding="utf-8"))
    inspected=json.loads(DOM.read_text(encoding="utf-8"))
    snippet=HTML.read_text(encoding="utf-8")
    if (previous.get("status")!="applied_verified" or
        previous.get("counter_id")!=COUNTER or
        previous.get("after_goal_count")!=13 or
        previous.get("all_existing_goals_unchanged") is not True or
        inspected.get("status")!="complete" or
        inspected.get("http_status")!=200):
        raise RuntimeError("Previous Metrica or browser verification missing")
    form=(inspected.get("dom") or {}).get("rec_vin") or {}
    forms=form.get("forms") or []
    if (form.get("html_form_count")!=1 or
        forms[0].get("id")!="form5246286101" or
        "js-form-proccess" not in forms[0].get("css_class","") or
        not any(x.get("field_name")=="VIN" for x in form.get("fields",[])) or
        not any(x.get("type")=="submit" for x in form.get("buttons",[]))):
        raise RuntimeError("Published VIN form DOM cannot be verified")
    if (IDENTIFIER not in snippet or
        'tildaform:aftersuccess' not in snippet or
        "113839860" not in snippet):
        raise RuntimeError("Success-only site integration snippet missing")
    return {"rendered_vin_form_id":"form5246286101",
            "real_html_submit_button":True,
            "tilda_success_event_hook_present_in_snippet":True,
            "no_site_or_direct_changes":True}

def main():
    global ALLOWED_POST
    if OUT.exists():
        try:prior=json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            print("Existing result unreadable, refuse duplicate")
            return 1
        if prior.get("write_attempted"):
            print("Previous goal creation attempt recorded; no blind retry")
            return 1
    audit={"status":"preflight","started_utc":utc(),
           "counter_id":COUNTER,"name":NAME,"goal_identifier":IDENTIFIER,
           "type":"action","write_attempted":False,"mutations":0,"errors":[],
           "effect_status":"inactive_until_Tilda_snippet_is_published_and_actual_form_success"}
    save(audit)
    try:
        audit["browser_confirmation"]=preflight()
        account=request("GET",URL).get("counter") or {}
        if account.get("id")!=COUNTER or account.get("permission") not in ("own","edit"):
            raise RuntimeError("Account lacks edit permissions")
        before=goals()
        if len(before)!=13:raise RuntimeError("Unexpected 13-goal preflight changed")
        ids={g["id"] for g in before}
        if not {685988392,675506966,675507028,675272284,682257540}.issubset(ids):
            raise RuntimeError("Existing checkout/VIN/purchase goals missing")
        if any(g.get("name")==NAME or (
           g.get("type")=="action" and
           any(isinstance(c,dict) and c.get("url")==IDENTIFIER for c in g.get("conditions",[])))
           for g in before):
            raise RuntimeError("Action goal already exists with this name or identifier")
        bs=sig(before)
        audit["previous_goal_count"]=len(before)
        audit["previous_goal_ids"]=sorted(bs)
        audit["submitted_payload_preview"]=PAYLOAD
        save(audit)
        if sig(goals())!=bs:raise RuntimeError("Concurrent goal edit detected")
        ALLOWED_POST=PAYLOAD
        audit["write_attempted"]=True
        audit["mutations"]=1
        audit["write_started_utc"]=utc()
        save(audit)
        try:
            reply=request("POST",URL+"/goals",PAYLOAD)
            new=reply.get("goal") or {}
            audit["accepted_goal_id"]=new.get("id")
            audit["api_response_ok"]=isinstance(new.get("id"),int)
        except Exception as e:
            audit["api_uncertain_error"]=str(e)[:150]
        after=goals()
        final=sig(after)
        added=set(final)-set(bs)
        matches=[g for g in after if g.get("name")==NAME and
                 g.get("type")=="action" and
                 g.get("conditions")==PAYLOAD["goal"]["conditions"]]
        audit["after_goal_count"]=len(after)
        audit["preserved_all_13_existing_goals"]=all(final.get(g)==old for g,old in bs.items())
        audit["exact_one_new_goal"]=len(added)==1 and len(after)==len(before)+1
        audit["new_goal_matches_config"]=len(matches)==1 and matches[0]["id"] in added
        audit["new_goal_id"]=matches[0]["id"] if len(matches)==1 else None
        if (audit["preserved_all_13_existing_goals"] and audit["exact_one_new_goal"]
            and audit["new_goal_matches_config"]):
            audit["status"]="created_verified_not_deployed"
            return 0
        audit["status"]="postwrite_verification_failed"
        return 1
    except Exception as e:
        audit["status"]="blocked_or_failed_no_retry"
        audit["errors"].append(str(e)[:230])
        return 1
    finally:
        audit["finished_utc"]=utc()
        save(audit)
        print("VIN success JS event:",audit["status"],"new_goal_id",
             audit.get("new_goal_id"),"write",audit["write_attempted"],flush=True)
if __name__=="__main__":
    sys.exit(main())
