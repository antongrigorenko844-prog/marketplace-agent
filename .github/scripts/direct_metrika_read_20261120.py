"""Read-only Yandex Metrica onboarding audit for LEOPARTS.
Runs ONLY after separate YANDEX_METRIKA_TOKEN (metrika:read) is installed.
Fetches goal setup plus AGGREGATED visits and conversions; no visitor identifiers.
"""
import datetime
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

COUNTER=113839860
GOALS=(675506966,675507028)
HOST="https://api-metrika.yandex.net"
OUT=Path("data/direct_optimization_20261120/metrika_read_audit.json")
FROM="2026-11-16"
TO="2026-11-20"
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(obj):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def call(path,params=None):
    if path not in ("/management/v1/counter/113839860/goals","/stat/v1/data"):
        raise RuntimeError("Endpoint outside Metrica read-only allowlist")
    token=os.getenv("YANDEX_METRIKA_TOKEN","")
    if not token:raise RuntimeError("Separate metrika:read token unavailable")
    args=urllib.parse.urlencode(params or {},safe=",")
    url=HOST+path+("?" + args if args else "")
    req=urllib.request.Request(url,headers={"Authorization":"OAuth "+token,"User-Agent":"LEOPARTS-readonly-goal-audit/1.0"},method="GET")
    try:
        with urllib.request.urlopen(req,timeout=45) as response:
            blob=response.read(2*1024*1024+1)
    except urllib.error.HTTPError as err:
        code=err.code;err.close()
        raise RuntimeError("Metrica "+path+" HTTP "+str(code)) from None
    except Exception:
        raise RuntimeError("Metrica transport error") from None
    if len(blob)>2*1024*1024:raise RuntimeError("Metrica result too large")
    return json.loads(blob)
def sanitize_stats(res):
    # Store only aggregates by dimension, not individual visits or users.
    rows=res.get("data")
    if not isinstance(rows,list):raise RuntimeError("Metrica aggregate rows missing")
    if len(rows)>250:raise RuntimeError("Unbounded Metrica report")
    result=[]
    for row in rows:
        dims=[{"name":str(d.get("name",""))[:100],"id":str(d.get("id",""))[:80]}
            for d in (row.get("dimensions") or [])]
        metrics=row.get("metrics")
        if not isinstance(metrics,list) or any(not isinstance(x,(int,float)) for x in metrics):
            raise RuntimeError("Unusable Metrica metrics")
        result.append({"dimensions":dims,"metrics":metrics})
    return {"total_rows":len(rows),"rows":result,"totals":res.get("totals"),
            "sampled":res.get("sampled"),"sample_share":res.get("sample_share"),
            "contains_sensitive_user_data":False}
def report(label,dimensions,metrics):
    options={"ids":str(COUNTER),"date1":FROM,"date2":TO,"dimensions":dimensions,
      "metrics":",".join(metrics),"lang":"ru","accuracy":"full","limit":"100"}
    return {"metrics":metrics,"dimensions":dimensions,"retrieved_utc":now(),
            "result":sanitize_stats(call("/stat/v1/data",options))}
def run():
    result={"status":"in_progress","mode":"Metrica_read_only","counter_id":COUNTER,
        "goal_ids":list(GOALS),"started_utc":now(),
        "date1":FROM,"date2":TO,"errors":[],"sections":{},
        "account_writes":0,"uses_only_separate_token":True}
    if not os.getenv("YANDEX_METRIKA_TOKEN"):
        result.update(status="waiting_for_secret",finished_utc=now(),
            next_step="Create GitHub Actions secret YANDEX_METRIKA_TOKEN with OAuth scope metrika:read")
        save(result)
        print("Metrica read audit waiting for separate token; Direct untouched")
        return 0
    save(result)
    tasks={
        "goal_setup":lambda: goal_setup(),
        "goals_by_day":lambda: report("day","ym:s:date",METRICS),
        "goals_by_traffic_source":lambda: report("source","ym:s:lastTrafficSource",METRICS),
        "utm_campaigns":lambda: report("utm","ym:s:lastUTMSource,ym:s:lastUTMCampaign",METRICS),
    }
    for name,fn in tasks.items():
        try:
            result["sections"][name]={"status":"ok","retrieved_utc":now(),"data":fn()}
        except Exception as err:
            # No raw server body or OAuth credentials in GitHub output
            result["sections"][name]={"status":"failed","error":str(err)[:200]}
            result["errors"].append(name)
        save(result)
    result["status"]="complete" if not result["errors"] else "partial"
    result["finished_utc"]=now()
    result["interpretation_warning"]="Goal visits are not necessarily paid orders. Confirm goal conditions and actual transaction data before ROAS."
    save(result)
    print("Metrica read audit:",result["status"],"failed",result["errors"],flush=True)
    return 0
def goal_setup():
    response=call("/management/v1/counter/113839860/goals")
    goals=response.get("goals")
    if not isinstance(goals,list):raise RuntimeError("Metrica goals list not provided")
    result=[]
    for goal in goals:
        if goal.get("id") not in GOALS:continue
        conditions=goal.get("conditions") or []
        result.append({"id":goal.get("id"),"name":goal.get("name"),"type":goal.get("type"),
            "conditions_count":len(conditions),
            "conditions_operator_types":[{"type":str(x.get("type",""))[:90],
                 "operator":str(x.get("type",""))[:90]} for x in conditions if isinstance(x,dict)]})
    return {"requested_count":len(GOALS),"goals_found":result,"missing_ids":[g for g in GOALS if g not in [x["id"] for x in result]]}
METRICS=["ym:s:visits","ym:s:goal675506966visits","ym:s:goal675507028visits"]
if __name__=="__main__":sys.exit(run())
