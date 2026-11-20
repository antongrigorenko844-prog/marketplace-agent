"""Read-only LEOPARTS Metrica goal troubleshooting; aggregate stats only.
No visitor-level identifiers, writes or OAuth tokens are logged or committed.
"""
from datetime import datetime,timezone
import importlib.util,json,os,sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("read_metrika",HERE/"direct_metrika_read_20261120.py")
metrika=importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrika)
CID=113839860
PRIORITY={675506966,675507028}
OUT=Path("data/direct_optimization_20261120/metrika_goal_diagnostics.json")
def now():return datetime.now(timezone.utc).isoformat()
def write(data):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def safe(s):
    value=str(s)
    for name,secret in os.environ.items():
        if len(secret)>8 and any(x in name for x in ("TOKEN","SECRET","PASSWORD","API_KEY")):
            value=value.replace(secret,"[redacted]")
    return value[:400]
def goals():
    res=metrika.call("/management/v1/counter/113839860/goals")
    items=res.get("goals")
    if not isinstance(items,list) or len(items)>100:
        raise RuntimeError("Invalid or unexpectedly large goal list")
    all_goals=[]
    for item in items:
        ident=item.get("id")
        if not isinstance(ident,int):continue
        g={"id":ident,"name":str(item.get("name",""))[:130],
           "type":str(item.get("type",""))[:40],
           "status":str(item.get("status",""))[:50],
           "auto":bool(item.get("auto",False)),
           "goal_source":str(item.get("goal_source",""))[:50]}
        if ident in PRIORITY:
            # Goal URL conditions are account configuration, not personal visit data.
            conds=item.get("conditions") or []
            g["url_conditions"]=[{"type":str(c.get("type",""))[:40],
                "url_pattern":str(c.get("url",""))[:300]} for c in conds if isinstance(c,dict)]
        all_goals.append(g)
    return all_goals
def report_goal_batch(ids):
    metrics=["ym:s:visits"]+["ym:s:goal"+str(i)+"visits" for i in ids]
    params={"ids":str(CID),"date1":"2026-11-16","date2":"2026-11-20",
            "metrics":",".join(metrics),"lang":"ru","accuracy":"full","limit":"1"}
    res=metrika.call("/stat/v1/data",params)
    t=res.get("totals")
    if not isinstance(t,list) or len(t)!=len(metrics):
        raise RuntimeError("Incorrect Metrica goal totals")
    return {str(k):v for k,v in zip(["visits"]+[str(i) for i in ids],t)}
def run():
    data={"status":"running","mode":"read_only","counter_id":CID,
          "started_utc":now(),"external_mutations":0,
          "requested_priority_goal_ids":sorted(PRIORITY),
          "errors":[]}
    write(data)
    if not os.getenv("YANDEX_METRIKA_TOKEN"):
        data["status"]="no_secret";data["finished_utc"]=now();write(data);return 1
    try:
        all_goals=goals()
        data["all_goals_metadata"]=all_goals
        data["goal_count"]=len(all_goals)
        data["priority_goals"]=[g for g in all_goals if g["id"] in PRIORITY]
        data["missing_priority_goal_ids"]=sorted(PRIORITY-{g["id"] for g in all_goals})
        if data["missing_priority_goal_ids"]:raise RuntimeError("Missing priority goals")
        write(data)
        ids=[g["id"] for g in all_goals]
        totals=[]
        for i in range(0,len(ids),6):
            sub=ids[i:i+6]
            try:
                totals.append({"ids":sub,"status":"ok","totals":report_goal_batch(sub)})
            except Exception as e:
                totals.append({"ids":sub,"status":"error","error":safe(e)})
        data["goal_visits_batches"]=totals
        data["active_goal_visits"]=[{"id":int(k),"visits":v} for item in totals if item.get("status")=="ok"
                                   for k,v in item["totals"].items() if k!="visits" and float(v)>0]
        data["status"]="complete" if all(b["status"]=="ok" for b in totals) else "partial"
        data["finished_utc"]=now()
    except Exception as e:
        data["status"]="failed";data["errors"].append(safe(e));data["finished_utc"]=now()
    write(data)
    print("Metrica goals read-only check:",data["status"],"goals",data.get("goal_count"),
          "nonzero",len(data.get("active_goal_visits",[])),"errors",len(data["errors"]),flush=True)
    return 0 if data["status"] in ("complete","partial") else 1
if __name__=="__main__":
    sys.exit(run())
