"""Check LEOPARTS newly deployed VIN submission tracking after owner's test.

Read-only Yandex Metrica API: goal visits by day and source, no identifying
visitor details or login credentials. Distinguishes observed vs not yet visible.
"""
import json, os, sys, urllib.request, urllib.error, urllib.parse
from pathlib import Path
from datetime import datetime, timezone

COUNTER=113839860
GOAL_SUCCESS=686020680
GOAL_OLD=675506966
GOAL_FORM=675272284
GOAL_PURCHASE=682257540
GOALS=(GOAL_SUCCESS,GOAL_OLD,GOAL_FORM,GOAL_PURCHASE)
DATES=("2026-11-20","2026-11-20")
OUT=Path("data/direct_optimization_20261120/vin_test_metrika_verification.json")
ENDPOINT="https://api-metrika.yandex.net"

def now():return datetime.now(timezone.utc).isoformat()
def save(result):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def read(path,params=None):
    if path not in (f"/management/v1/counter/{COUNTER}/goals", "/stat/v1/data"):
        raise RuntimeError("Read-only Metrica allowlist enforced")
    token=os.getenv("YANDEX_METRIKA_TOKEN")
    if not token:raise RuntimeError("Metrica read OAuth token is missing")
    url=ENDPOINT+path
    if params:url+="?"+urllib.parse.urlencode(params,safe=",")
    req=urllib.request.Request(url,headers={"Authorization":"OAuth "+token,
        "Accept":"application/json","User-Agent":"LEOPARTS-VIN-test-readonly/1.0"},method="GET")
    try:
        with urllib.request.urlopen(req,timeout=45) as resp:
            raw=resp.read(524288)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError("Metrica HTTP "+str(code)) from None
    except Exception as e:
        raise RuntimeError("Metrica transport failure "+type(e).__name__) from None
    try:data=json.loads(raw)
    except Exception:raise RuntimeError("Metrica returned malformed JSON") from None
    if not isinstance(data,dict):raise RuntimeError("Metrica returned unexpected JSON")
    return data
def goal_configuration():
    data=read(f"/management/v1/counter/{COUNTER}/goals")
    goals=data.get("goals")
    if not isinstance(goals,list):raise RuntimeError("No goals list")
    g=[x for x in goals if x.get("id")==GOAL_SUCCESS]
    if len(g)!=1:raise RuntimeError("New VIN goal not found")
    x=g[0]
    return {"goal_id":x["id"],"name":str(x.get("name",""))[:120],
            "type":x.get("type"),"status":x.get("status"),
            "conditions":[{"type":a.get("type"),"url":str(a.get("url",""))[:100]}
                         for a in (x.get("conditions") or []) if isinstance(a,dict)]}
def report(name,dimensions):
    metrics=["ym:s:visits"]+[f"ym:s:goal{x}visits" for x in GOALS]
    params={"ids":str(COUNTER),"date1":DATES[0],"date2":DATES[1],
            "metrics":",".join(metrics),"dimensions":dimensions,
            "accuracy":"full","lang":"ru","limit":"100"}
    data=read("/stat/v1/data",params)
    rows=data.get("data")
    if not isinstance(rows,list) or len(rows)>100:raise RuntimeError("Unexpected Metrica rows")
    def vals(v):
        if not isinstance(v,list) or len(v)!=len(metrics):raise RuntimeError("Metric length mismatch")
        if any(not isinstance(x,(int,float)) or x<0 for x in v):
            raise RuntimeError("Invalid numeric metric")
        return dict(zip(["visits"]+list(map(str,GOALS)),v))
    result=[]
    for row in rows:
        dims=row.get("dimensions")
        if not isinstance(dims,list):raise RuntimeError("Missing dimensions")
        result.append({"dimensions":[{"name":str(x.get("name",""))[:100],
                                      "id":str(x.get("id",""))[:80]} for x in dims],
                       "values":vals(row.get("metrics"))})
    return {"name":name,"dimensions":dimensions,"totals":vals(data.get("totals")),
            "rows":result,"sampled":data.get("sampled"),"total_rows":len(result)}
def main():
    result={"status":"in_progress","mode":"read_only","counter_id":COUNTER,
            "goal_id":GOAL_SUCCESS,"date1":DATES[0],"date2":DATES[1],
            "read_at_utc":now(),"no_form_submissions_by_agent":True,
            "metrika_writes":0,"goal_visibility":"unknown","sections":{},"errors":[]}
    save(result)
    for name,func in (
        ("goal_definition",goal_configuration),
        ("by_date",lambda:report("date","ym:s:date")),
        ("by_traffic_source",lambda:report("source","ym:s:lastTrafficSource")),
        ("by_utm",lambda:report("utm","ym:s:lastUTMSource,ym:s:lastUTMCampaign"))
    ):
        try:result["sections"][name]={"status":"ok","data":func()}
        except Exception as e:
            result["sections"][name]={"status":"error","message":str(e)[:160]}
            result["errors"].append(name)
        save(result)
    daily=result["sections"].get("by_date",{})
    if daily.get("status")=="ok":
        v=daily["data"]["totals"][str(GOAL_SUCCESS)]
        result["observed_new_goal_visits"]=v
        result["goal_visibility"]="observed" if v>0 else "not_yet_observed"
    else:
        result["goal_visibility"]="unknown"
    result["status"]="complete" if not result["errors"] else "partial"
    result["finished_utc"]=now()
    result["caution"]="Observed VIN goal does not independently prove CRM receipt; nonzero could include other submissions. A zero can be caused by Metrica report latency."
    save(result)
    print("VIN TEST METRIKA:",result["status"],result["goal_visibility"],
          "new_goal_visits",result.get("observed_new_goal_visits"),
          "errors",result["errors"],flush=True)
    return 0 if result["status"]=="complete" else 1

if __name__=="__main__":sys.exit(main())
