# Post-fix 2026-11-22 verification: Yandex Direct read-only, no writes.
# Read-only campaign snapshot rerun requested 2026-11-22 to inspect current-day spend and search-query quality.
"""LEOPARTS campaign 716316525 fresh read-only optimization review, 20 Nov.
Only reads get/report endpoints, never updates bids, campaign, negatives or ads.
"""
import csv,io,json,time,os,sys,hashlib
from datetime import datetime,timezone,timedelta
from zoneinfo import ZoneInfo
from decimal import Decimal
from collections import defaultdict
from pathlib import Path
import direct_preflight_20261120 as direct

CID=716316525
TODAY=datetime.now(ZoneInfo("Europe/Moscow")).date()
FROM=(TODAY-timedelta(days=6)).isoformat()
THRU=TODAY.isoformat()
OUT=Path("data/direct_optimization_20261120/live_traffic_review.json")
def stamp():return datetime.now(timezone.utc).isoformat()
def amount(text):
    if text is None or str(text).strip() in ("","-","--"):return None
    try:return Decimal(str(text).strip().replace("\u00a0","").replace(",","."))
    except Exception:raise ValueError("Invalid numeric report cell")
def report(kind,fields,label,search_only=False):
    filters=[{"Field":"CampaignId","Operator":"IN","Values":[str(CID)]}]
    if search_only:filters.append({"Field":"AdNetworkType","Operator":"EQUALS","Values":["SEARCH"]})
    params={"SelectionCriteria":{"DateFrom":FROM,"DateTo":THRU,"Filter":filters},
        "FieldNames":fields,"ReportType":kind,"ReportName":"leoparts20-review-"+label+"-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
        "DateRangeType":"CUSTOM_DATE","Format":"TSV","IncludeVAT":"YES"}
    for attempt in range(16):
        status,raw,headers=direct.request("reports","read",params,reports=True)
        if status==200:
            dr=csv.DictReader(io.StringIO(raw),delimiter="\t")
            if not dr.fieldnames or len(dr.fieldnames)!=len(set(dr.fieldnames)) or "Cost" not in dr.fieldnames:
                raise RuntimeError("Report headers invalid: "+label)
            rows=list(dr)
            if any(None in x or x.get("CampaignId")!=str(CID) for x in rows):
                raise RuntimeError("Report contains malformed/cross-campaign rows")
            return {"columns":dr.fieldnames,"rows":rows}
        if status not in (201,202):raise RuntimeError("Reports returned "+str(status))
        time.sleep(min(8,attempt+1))
    raise RuntimeError("Reports not ready "+label)
def tot(rows):
    out={}
    for key in ("Impressions","Clicks","Cost","Conversions"):
        vals=[amount(row.get(key)) for row in rows]
        clean=[v for v in vals if v is not None]
        out[key]=str(sum(clean,Decimal(0))) if clean else (None if rows else "0")
        out[key+"_coverage"]=len(clean)
    return out
def agg(rows,keys):
    buckets=defaultdict(list)
    for row in rows:buckets[tuple(row.get(k,"") for k in keys)].append(row)
    ranked=[dict(zip(keys,k))|tot(v) for k,v in buckets.items()]
    ranked.sort(key=lambda x:Decimal(x.get("Cost") or "0"),reverse=True)
    return ranked
def classification(q):
    q=q.casefold().replace("ё","е")
    if any(t in q for t in ("ноутбук","ваканс","ремонт дсг вологда","02e мехатроник","скачать сайт","шиномонтажный станок")):
        return "likely_not_DQ200_product"
    if any(t in q for t in ("где отремонтировать","сервис","ремонт дсг","стоимость ремонта","работа по ремонту","снятие","установка","где починить")):
        return "service_or_diagnostic_intent"
    if any(t in q for t in ("купить","цен","стоимость","заказать","в наличии")) and any(t in q for t in ("мехатрон","dq200","0am","0cw")):
        return "commercial_product"
    if any(t in q for t in ("0am","dq200","0cw","мехатрон","дсг","dsg")):
        return "ambiguous_or_product"
    return "needs_human_validation"
def run():
    obj={"status":"reading","captured_utc":stamp(),"campaign_id":CID,"mode":"read_only","errors":[],"external_mutations":0}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    def save():OUT.write_text(json.dumps(obj,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    save()
    try:
        c=direct.campaign()
        negatives=(c.get("NegativeKeywords") or {}).get("Items") or []
        if len(negatives)<136 or not {"сцепление","замена масла"}.issubset(set(negatives)):
            raise RuntimeError("Missing reviewed negatives (clutch / oil change) or too few current negatives")
        keys=direct.keywords();groups=direct.groups()
        if len(keys)!=272 or len(groups)!=6:raise RuntimeError("Keywords/group inventory changed")
        daily=report("CAMPAIGN_PERFORMANCE_REPORT",["Date","CampaignId","Impressions","Clicks","Cost","Conversions"],"daily")
        queries=report("SEARCH_QUERY_PERFORMANCE_REPORT",["Date","CampaignId","AdGroupId","Query","CriterionType","MatchedKeyword","Impressions","Clicks","Cost","Conversions"],"queries")
        cats=report("ADGROUP_PERFORMANCE_REPORT",["Date","CampaignId","AdGroupId","AdGroupName","CriterionType","TargetingCategory","Impressions","Clicks","Cost","Conversions"],"cats",True)
        total=tot(daily["rows"])
        paid=agg([x for x in queries["rows"] if (amount(x.get("Cost")) or Decimal(0))>0],["Query","AdGroupId","CriterionType"])
        today=agg([x for x in queries["rows"] if x.get("Date")==THRU and (amount(x.get("Cost")) or Decimal(0))>0],["Query","AdGroupId","CriterionType"])
        for item in paid:item["intent_estimate"]=classification(item["Query"])
        for item in today:item["intent_estimate"]=classification(item["Query"])
        by_group=agg(queries["rows"],["AdGroupId","CriterionType"])
        obj.update(status="complete",started_from=FROM,through=THRU,
            read_utc=stamp(),state=c.get("State"),type=c.get("Type"),
            negatives_count=len(negatives),
            negative_phrases=negatives,
            negative_fingerprint=hashlib.sha256(json.dumps(negatives,ensure_ascii=False,separators=(",",":")).encode()).hexdigest(),
            autotargeting_conditions=sum(x.get("Keyword")=="---autotargeting" for x in keys),
            manual_keywords=sum(x.get("Keyword")!="---autotargeting" for x in keys),
            groups=[{"id":g.get("Id"),"name":g.get("Name"),"negative_count":len((g.get("NegativeKeywords") or {}).get("Items") or [])} for g in groups],
            weekly_limit_raw=(((c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}).get("Search") or {}).get("HighestPosition",{}).get("WeeklySpendLimit"),
            daily=agg(daily["rows"],["Date"]),overall=total,
            query_report_rows=len(queries["rows"]),query_paid=paid[:250],
            query_today_paid=today,
            by_group_criterion=by_group,
            by_auto_category=agg(cats["rows"],["CriterionType","TargetingCategory"]),
            difference_campaign_less_search_query_cost=str(Decimal(total.get("Cost") or "0")-Decimal(tot(queries["rows"]).get("Cost") or "0")),
            note="Queries/categories and campaign reports can have different coverage; intent labels are only rough rules. Current day is incomplete.")
    except Exception as e:
        obj["status"]="error";obj["errors"].append(direct.scrub(e))
    finally:
        obj["finished_utc"]=stamp();save()
        print("Live review",obj["status"],"spent",obj.get("overall",{}).get("Cost"),
              "today",obj.get("daily",[])[-1:],"paid_queries",len(obj.get("query_paid",[])),flush=True)
    return 0 if obj["status"]=="complete" else 1
if __name__=="__main__":sys.exit(run())
