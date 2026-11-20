"""LEOPARTS read-only: reconcile Metrica goal definitions with Direct goal attribution.
Only campaign 716316525 and counter 113839860; no visitor IDs, no mutations.
"""
import csv, io, json, os, sys, time, urllib.error, urllib.request
from pathlib import Path
from datetime import datetime,timezone
from decimal import Decimal
CID=716316525
COUNTER=113839860
FROM,TO="2026-11-16","2026-11-20"
GOALS=[675272284,675487735,675506966,675507028,682257540]
OUT=Path("data/direct_optimization_20261120/conversion_reconciliation.json")
def utc():return datetime.now(timezone.utc).isoformat()
def save(o):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    temp=OUT.with_suffix(".tmp")
    temp.write_text(json.dumps(o,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    temp.replace(OUT)
def direct_reports():
    token=os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:raise RuntimeError("No Direct OAuth token")
    fields=["Date","CampaignId","AdGroupId","Impressions","Clicks","Cost","Conversions"]
    params={"SelectionCriteria":{"DateFrom":FROM,"DateTo":TO,
        "Filter":[{"Field":"CampaignId","Operator":"IN","Values":[str(CID)]}]},
        "FieldNames":fields,"ReportType":"ADGROUP_PERFORMANCE_REPORT",
        "ReportName":"leoparts-goal-reconciliation-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
        "DateRangeType":"CUSTOM_DATE","Format":"TSV","IncludeVAT":"YES",
        "Goals":[str(g) for g in GOALS],"AttributionModels":["LC"]}
    h={"Authorization":"Bearer "+token,"Accept-Language":"en",
       "Content-Type":"application/json; charset=utf-8",
       "processingMode":"auto","returnMoneyInMicros":"false",
       "skipReportHeader":"true","skipReportSummary":"true"}
    if os.getenv("YANDEX_DIRECT_LOGIN"):h["Client-Login"]=os.environ["YANDEX_DIRECT_LOGIN"]
    for i in range(15):
        req=urllib.request.Request("https://api.direct.yandex.com/json/v501/reports",
             headers=h,method="POST",data=json.dumps({"params":params}).encode())
        try:
            with urllib.request.urlopen(req,timeout=65) as resp:
                code=resp.status;raw=resp.read(1024*1024+1)
        except urllib.error.HTTPError as err:
            code=err.code;err.close()
            if code not in (201,202):raise RuntimeError("Direct Report HTTP "+str(code)) from None
        if code==200:
            if len(raw)>1024*1024:raise RuntimeError("Unexpected report size")
            s=raw.decode("utf-8-sig")
            reader=csv.DictReader(io.StringIO(s),delimiter="\t")
            columns=reader.fieldnames or []
            if "CampaignId" not in columns or "Cost" not in columns:
                raise RuntimeError("Missing required report columns")
            rows=list(reader)
            if any(x.get("CampaignId")!=str(CID) or x.get("Date")<FROM or x.get("Date")>TO
                   for x in rows):raise RuntimeError("Report scope mismatch")
            cols=[name for name in columns if name.startswith("Conversions")]
            total={}
            for key in ["Impressions","Clicks","Cost"]+cols:
                numeric=[];missing=0
                for row in rows:
                    v=str(row.get(key,"")).strip()
                    if v in ("","--","-"):missing+=1;continue
                    try:n=Decimal(v.replace(",","."))
                    except Exception:raise RuntimeError("Invalid numeric report cell") from None
                    numeric.append(n)
                total[key]={"numeric_total":str(sum(numeric)) if numeric else None,
                            "nonmissing_cells":len(numeric),"missing_cells":missing}
            return {"status":"ok","report_type":params["ReportType"],
                    "requested_goals":GOALS,"attribution":"LC",
                    "rows":len(rows),"columns":columns,"totals":total,
                    "sample_rows":[{k:x.get(k) for k in ("Date","AdGroupId")} |
                        {key:x.get(key) for key in cols} for x in rows[:25]],
                    "include_vat":True}
        if code not in (201,202):raise RuntimeError("Unexpected status")
        time.sleep(min(8,i+2))
    raise RuntimeError("Direct Report not ready")
def metrika_goals():
    token=os.getenv("YANDEX_METRIKA_TOKEN")
    if not token:raise RuntimeError("No separate Metrica OAuth token")
    req=urllib.request.Request("https://api-metrika.yandex.net/management/v1/counter/"+str(COUNTER)+"/goals",
             method="GET",headers={"Authorization":"OAuth "+token})
    try:
        with urllib.request.urlopen(req,timeout=45) as resp:doc=json.load(resp)
    except urllib.error.HTTPError as e:
        code=e.code;e.close();raise RuntimeError("Metrica goals HTTP "+str(code)) from None
    goals=doc.get("goals")
    if not isinstance(goals,list):raise RuntimeError("No goals")
    result=[]
    for x in goals:
        if x.get("id") not in GOALS:continue
        fields={"id":x["id"],"name":str(x.get("name",""))[:100],
                "type":x.get("type"),"status":x.get("status"),
                "conditions":[],"condition_count":len(x.get("conditions") or [])}
        for c in (x.get("conditions") or [])[:5]:
            if not isinstance(c,dict):continue
            # Only allow known non-sensitive goal configuration fields.
            fields["conditions"].append({k:str(c.get(k))[:220] for k in
                ("type","url","formId","formName","form","selector","xpath","condition")
                if k in c})
        fields["other_condition_keys"]=[sorted(set(c)-{
            "type","url","formId","formName","form","selector","xpath","condition"})
            for c in (x.get("conditions") or [])[:5] if isinstance(c,dict)]
        result.append(fields)
    return result
def main():
    result={"status":"running","mode":"strict_read_only","started_utc":utc(),
        "campaign_id":CID,"counter_id":COUNTER,"report_period":[FROM,TO],
        "mutations":0,"sections":{},"errors":[]}
    save(result)
    for name,fn in [("metrika_goal_definitions",metrika_goals),
                    ("direct_goal_specific_report",direct_reports)]:
        try:result["sections"][name]={"status":"ok","result":fn()}
        except Exception as e:
            result["sections"][name]={"status":"error","error":str(e)[:200]}
            result["errors"].append(name)
        save(result)
    result["status"]="complete" if not result["errors"] else "partial"
    result["finished_utc"]=utc()
    save(result)
    print("GOAL_RECONCILIATION",result["status"],"errors",result["errors"],flush=True)
    return 0
if __name__=="__main__":sys.exit(main())
