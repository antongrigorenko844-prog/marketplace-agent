"""Check actual Tilda VIN submission virtual pageviews after owner's test.

Yandex Metrica read-only aggregate URL paths. Never stores query strings, raw
visitor data, OAuth tokens, or identifiable form fields. Compare known prior
16-20 Nov baseline to today's pageview counts. Does not simulate any visits.
"""
from datetime import datetime,timezone
from pathlib import Path
from urllib.parse import urlencode,urlsplit
import json,os,sys,urllib.request,urllib.error

CID=113839860
ROOT="https://api-metrika.yandex.net/stat/v1/data"
OUT=Path("data/direct_optimization_20261120/metrika_vin_form_route_audit.json")
PRIOR=Path("data/direct_optimization_20261120/metrika_tilda_virtual_pageviews.json")
KNOWN={
    "/tilda/form5246286101/submitted/":"VIN priority form submitted",
    "/tilda/form5171350301/submitted/":"Other form submitted",
    "/tilda/form5260805101/submitted/":"Checkout form submitted",
    "/tilda/form5260805101/payment/":"Checkout started (not purchase)",
}
def utc():return datetime.now(timezone.utc).isoformat()
def save(x):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(x,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUT)
def get(params):
    token=os.getenv("YANDEX_METRIKA_TOKEN","")
    if not token:raise RuntimeError("YANDEX_METRIKA_TOKEN absent")
    url=ROOT+"?"+urlencode(params,safe=",")
    req=urllib.request.Request(url,headers={
        "Authorization":"OAuth "+token,"User-Agent":"LEOPARTS-VIN-route-readonly"},
        method="GET")
    try:
        with urllib.request.urlopen(req,timeout=55) as resp:raw=resp.read(360000)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError("Metrica HTTP "+str(code)) from None
    except Exception as e:
        raise RuntimeError("Metrica transport "+type(e).__name__) from None
    try:return json.loads(raw)
    except ValueError:raise RuntimeError("Metrica invalid JSON") from None
def virtual_pages(day1,day2):
    data=get({"ids":CID,"date1":day1,"date2":day2,
              "dimensions":"ym:pv:URL","metrics":"ym:pv:pageviews",
              "filters":"ym:pv:URL=@'tilda/form'","accuracy":"full",
              "lang":"ru","limit":1000})
    rows=data.get("data")
    if not isinstance(rows,list) or len(rows)>1000 or data.get("total_rows",0)>1000:
        raise RuntimeError("URL report missing or incomplete")
    matched={}
    anonymous_other_pageviews=0
    for row in rows:
        name=str((row.get("dimensions") or [{}])[0].get("name") or "")
        vals=row.get("metrics") or []
        if len(vals)!=1 or not isinstance(vals[0],(int,float)):
            raise RuntimeError("Unexpected pageview count")
        parsed=urlsplit(name)
        path=parsed.path if parsed.scheme else "/"+name.split("?")[0].split("#")[0].split("/",1)[-1]
        # Extract ONLY four known, non-personal virtual paths.
        hit=next((p for p in KNOWN if path.endswith(p)),None)
        if hit:matched[hit]=matched.get(hit,0)+vals[0]
        else:anonymous_other_pageviews+=vals[0]
    return {"pageviews_by_known_path":{p:matched.get(p,0) for p in KNOWN},
            "other_tilda_form_pageviews":anonymous_other_pageviews,
            "total_filtered_rows":len(rows),"report_sampled":data.get("sampled")}
def goal_hits(day1,day2):
    ids=[686020680,675506966,675272284,682257540]
    met=["ym:s:visits"]+[f"ym:s:goal{g}visits" for g in ids]
    data=get({"ids":CID,"date1":day1,"date2":day2,
              "metrics":",".join(met),"accuracy":"full","lang":"ru","limit":1})
    t=data.get("totals")
    if not isinstance(t,list) or len(t)!=len(met):raise RuntimeError("Incorrect totals")
    return dict(zip(["visits"]+list(map(str,ids)),t))
def main():
    doc={"status":"running","counter_id":CID,
        "mode":"read_only","submitted_real_forms":0,
        "checked_at_utc":utc(),"date":"2026-11-20","errors":[]}
    save(doc)
    try:
        pre=json.loads(PRIOR.read_text(encoding="utf-8"))
        if pre.get("status")!="complete" or pre.get("counter_id")!=CID:
            raise RuntimeError("No trustworthy earlier baseline")
        doc["historical_baseline_16_to_20"]={
            "snapshot_utc":pre.get("finished_utc"),
            "rows":pre.get("sections",{}).get("tilda_forms",{}).get("data",{}).get("rows")}
        doc["fresh_tilda_form_pages"]=virtual_pages("2026-11-20","2026-11-20")
        doc["fresh_goal_visits"]=goal_hits("2026-11-20","2026-11-20")
        doc["status"]="complete"
    except Exception as e:
        doc["errors"].append(str(e)[:180])
        doc["status"]="error"
    doc["finished_utc"]=utc()
    save(doc)
    print("Metrica Tilda VIN form routes",doc["status"],
          doc.get("fresh_tilda_form_pages",{}).get("pageviews_by_known_path",{}),
          "goal",doc.get("fresh_goal_visits",{}).get("686020680"),flush=True)
    return 0 if doc["status"]=="complete" else 1
if __name__=="__main__":sys.exit(main())
