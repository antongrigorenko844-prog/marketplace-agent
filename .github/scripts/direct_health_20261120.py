"""Read-only current Direct health check after approved two exact negative phrases.

Uses same narrow Direct API client as direct_preflight_20261120.py. Does not
modify campaigns, ads, keywords, budgets or seller data.
"""
import csv
import importlib.util
import io
import json
import os
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

BASE=Path(__file__).resolve().parent
imported=BASE/"direct_preflight_20261120.py"
spec=importlib.util.spec_from_file_location("leoparts_preflight_reader",imported)
reader=importlib.util.module_from_spec(spec)
spec.loader.exec_module(reader)

CID=716316525
OUT=Path("data/direct_optimization_20261120/health_current.json")
START="2026-11-16"
END="2026-11-20"
TWO=('"02e мехатроник в сборе"','"ремонт дсг вологда"')
def now():return datetime.now(timezone.utc).isoformat()
def write(doc):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def summary():
    options={"SelectionCriteria":{"DateFrom":START,"DateTo":END,
              "Filter":[{"Field":"CampaignId","Operator":"IN","Values":[str(CID)]}]},
             "FieldNames":["Date","CampaignId","Impressions","Clicks","Cost","Conversions"],
             "ReportName":"leoparts-health-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
             "ReportType":"CAMPAIGN_PERFORMANCE_REPORT",
             "DateRangeType":"CUSTOM_DATE","Format":"TSV","IncludeVAT":"YES"}
    for i in range(12):
        response,text,headers=reader.request("reports","read",options,reports=True)
        if response==200:
            rows=list(csv.DictReader(io.StringIO(text),delimiter="\t"))
            if any(r.get("CampaignId")!=str(CID) or
                   (r.get("Date") or "")<START or (r.get("Date") or "")>END for r in rows):
                raise RuntimeError("Out of scope campaign row")
            if not rows:raise RuntimeError("No daily rows returned")
            total=Decimal("0")
            days=[]
            for r in rows:
                cost=reader.num(r.get("Cost"))
                if cost is None:raise RuntimeError("Missing cost in day row")
                total+=cost
                days.append({k:r.get(k) for k in ("Date","Impressions","Clicks","Cost","Conversions")})
            return {"total_rub":str(total),"days":days,"rows":len(rows),
                    "includes_vat":True,"period":[START,END]}
        if response not in (201,202):raise RuntimeError("Non-success report status")
        import time
        time.sleep(min(7,i+1))
    raise RuntimeError("Report not ready")
def run():
    doc={"mode":"read_only","status":"started","campaign_id":CID,"start_utc":now(),
         "source":"live_Yandex_Direct_API","external_mutations":0,"errors":[]}
    write(doc)
    try:
        campaign=reader.campaign()
        existing=(campaign.get("NegativeKeywords") or {}).get("Items")
        if not isinstance(existing,list) or len(existing)!=len(set(existing)):
            raise RuntimeError("Invalid negative phrase representation")
        inv=reader.keywords()
        groups=reader.groups()
        budget=((campaign.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}).get("Search",{}).get("HighestPosition",{}).get("WeeklySpendLimit")
        if not isinstance(budget,int):raise RuntimeError("Weekly spend limit not provided")
        week=summary()
        week_limit=Decimal(budget)/Decimal(1000000)
        spent=Decimal(week["total_rub"])
        doc.update({
            "state":campaign.get("State"),"type":campaign.get("Type"),
            "campaign_weekly_spend_limit_rub":str(week_limit),
            "period_expense_with_vat_rub":str(spent),
            "arithmetic_difference_limit_less_spent_rub":str(week_limit-spent),
            "arithmetic_difference_is_not_billable_account_balance":True,
            "possible_rollover_or_reporting_lag":"Not established by this API check; weekly limit is not account balance",
            "weekly_cost_report":week,
            "negative_count":len(existing),
            "negative_fingerprint":reader.hashlib.sha256(json.dumps(existing,ensure_ascii=False,separators=(",",":")).encode()).hexdigest(),
            "exact_two_present":{p:existing.count(p)==1 for p in TWO},
            "manual_keywords":len([k for k in inv if k.get("Keyword")!="---autotargeting"]),
            "autotargeting_conditions":len([k for k in inv if k.get("Keyword")=="---autotargeting"]),
            "groups":len(groups),
            "metrika_token_configured":bool(os.getenv("YANDEX_METRIKA_TOKEN")),
        })
        if len(existing)!=132 or not all(doc["exact_two_present"].values()):
            doc["status"]="needs_review";doc["errors"].append("Expected 132 negatives including approved exact two")
        else:
            doc["status"]="verified"
    except Exception as e:
        doc["status"]="error";doc["errors"].append(reader.scrub(e))
    doc["finished_utc"]=now()
    write(doc)
    print("Direct health",doc["status"],"total",doc.get("period_expense_with_vat_rub"),
          "negatives",doc.get("negative_count"),"metrika_token",doc.get("metrika_token_configured"))
    return 0 if doc["status"]=="verified" else 1
if __name__=="__main__":
    raise SystemExit(run())
