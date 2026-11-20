"""Read-only aggregated check for Tilda form virtual pageviews in LEOPARTS Metrica."""
import importlib.util,json,os,sys
from datetime import datetime,timezone
from pathlib import Path
HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("metrika_read",HERE/"direct_metrika_read_20261120.py")
mt=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mt)
OUT=Path("data/direct_optimization_20261120/metrika_tilda_virtual_pageviews.json")
def now():return datetime.now(timezone.utc).isoformat()
def persist(r):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def limited_report(f):
    params={"ids":"113839860","date1":"2026-11-16","date2":"2026-11-20",
        "metrics":"ym:pv:pageviews","dimensions":"ym:pv:URL",
        "filters":f,"accuracy":"full","limit":"100","lang":"ru"}
    data=mt.call("/stat/v1/data",params)
    rows=data.get("data")
    if not isinstance(rows,list) or len(rows)>100:raise RuntimeError("Unexpected report rows")
    summary=[]
    for row in rows:
        vals=row.get("metrics")
        if not isinstance(vals,list) or len(vals)!=1:raise RuntimeError("Missing metric")
        name=str((row.get("dimensions") or [{}])[0].get("name",""))
        # Avoid storing querystring parameters that might contain personal data.
        import urllib.parse
        parsed=urllib.parse.urlsplit(name)
        safe_url=(parsed.netloc+parsed.path) if parsed.scheme else name.split("?")[0].split("#")[0]
        summary.append({"url_path":safe_url[:200],"pageviews":vals[0]})
    return {"rows":summary,"totals":data.get("totals"),
            "sampled":data.get("sampled"),"returned_rows":len(rows)}
def main():
    r={"status":"running","counter_id":113839860,"mode":"aggregate_read_only","started_utc":now(),
       "sections":{},"errors":[],"mutations":0}
    persist(r)
    if not os.getenv("YANDEX_METRIKA_TOKEN"):
        r["status"]="no_token";persist(r);return 1
    for name,flt in [("tilda_forms","ym:pv:URL=@'tilda/form'"),
                     ("submitted_pages","ym:pv:URL=@'/submitted'")]:
        try:r["sections"][name]={"status":"ok","data":limited_report(flt)}
        except Exception as e:
            r["sections"][name]={"status":"error","error":str(e)[:230]}
            r["errors"].append(name)
        persist(r)
    r["status"]="complete" if not r["errors"] else "partial"
    r["finished_utc"]=now()
    persist(r)
    print("Tilda Metrica virtual pageview read-only:",r["status"],r["errors"],flush=True)
    return 0
if __name__=="__main__":sys.exit(main())
