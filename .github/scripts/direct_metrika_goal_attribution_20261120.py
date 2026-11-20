"""Read-only per-goal attribution for LEOPARTS Metrica 113839860.
Aggregates only. No visitor-level data, changes, or secret output.
"""
from pathlib import Path
import importlib.util,json,os,sys
from datetime import datetime,timezone

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("read_metrika",HERE/"direct_metrika_read_20261120.py")
mt=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mt)

GOALS=[675272154,675272284,675487735,678989864,678989865,675506966,675507028,682257540]
COUNTER=113839860
OUT=Path("data/direct_optimization_20261120/metrika_goal_attribution_20261120.json")
METRICS=["ym:s:visits"]+["ym:s:goal"+str(n)+"visits" for n in GOALS]
FIELDS=["visits"]+[str(n) for n in GOALS]
def now():return datetime.now(timezone.utc).isoformat()
def save(doc):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def group(dim):
    p={"ids":str(COUNTER),"date1":"2026-11-16","date2":"2026-11-20",
       "metrics":",".join(METRICS),"dimensions":dim,"accuracy":"full","limit":"100","lang":"ru"}
    d=mt.call("/stat/v1/data",p)
    vals=d.get("data")
    if not isinstance(vals,list) or len(vals)>100:raise RuntimeError("Invalid aggregate row count")
    rows=[]
    for it in vals:
        q=it.get("metrics")
        if not isinstance(q,list) or len(q)!=len(METRICS):raise RuntimeError("Wrong metric count")
        rows.append({"dimensions":[str(e.get("name",""))[:100] for e in it.get("dimensions",[])],
                     "values":dict(zip(FIELDS,q))})
    totals=d.get("totals")
    if not isinstance(totals,list) or len(totals)!=len(METRICS):raise RuntimeError("Wrong totals")
    return {"dimensions":dim,"rows":rows,"totals":dict(zip(FIELDS,totals)),
            "sampled":d.get("sampled"),"sample_share":d.get("sample_share"),
            "source":"Metrica Reporting API, aggregate data"}
def main():
    result={"status":"running","mode":"read_only","counter_id":COUNTER,
       "goal_ids":GOALS,"start_utc":now(),"date_from":"2026-11-16",
       "date_to":"2026-11-20","sections":{},"errors":[],"mutations":0}
    save(result)
    if not os.getenv("YANDEX_METRIKA_TOKEN"):
        result["status"]="no_secret";result["end_utc"]=now();save(result);return 1
    for name,dim in [("date","ym:s:date"),
                     ("traffic_source","ym:s:lastTrafficSource"),
                     ("utm","ym:s:lastUTMSource,ym:s:lastUTMCampaign")]:
        try:result["sections"][name]={"status":"ok","data":group(dim)}
        except Exception as e:
            result["sections"][name]={"status":"error","error":str(e)[:230]}
            result["errors"].append(name)
        save(result)
    result["status"]="complete" if not result["errors"] else "partial"
    result["end_utc"]=now()
    result["caution"]="Goal visits can overlap; clicks and form goals are not verified paid orders."
    save(result)
    print("LEOPARTS Metrica attributed goals",result["status"],result["errors"],flush=True)
    return 0 if result["status"] in ("complete","partial") else 1
if __name__=="__main__":sys.exit(main())
