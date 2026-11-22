"""LEOPARTS Direct immediate snapshot, all read-only, compare Nov 22 targeting changes.
Every external request is a GET/read: Yandex Direct campaigns/keywords/adgroups/ads
and Reporting API; Yandex Metrica aggregated reporting only. No mutations.
"""
import csv, hashlib, io, json, os, sys, time
from collections import Counter, defaultdict
from datetime import datetime, timezone, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0,str(Path(__file__).resolve().parent))
import direct_preflight_20261120 as direct
import direct_metrika_read_20261120 as mt

CID=716316525
COUNTER_ID=113839860
TODAY=datetime.now(ZoneInfo("Europe/Moscow")).date()
BEFORE=(TODAY-timedelta(days=6)).isoformat()
TO=TODAY.isoformat()
CHANGED="2026-11-22"
OUT=Path("data/direct_current_20261123_live.json")
GOALS=[675271839,675271996,675272154,675272284,675487735,675506966,675507028,682257540,686020680]
def stamp():return datetime.now(timezone.utc).isoformat()
def save(d):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def value(s):
    if s is None or str(s).strip() in ("","--","-"):return None
    try:
        x=Decimal(str(s).replace("\u00a0","").replace(",","."));return x if x.is_finite() else None
    except Exception:return None
def totals(rows):
    result={}
    for k in ("Cost","Clicks","Impressions","Conversions"):
        nums=[value(x.get(k)) for x in rows]
        result[k]=str(sum((x for x in nums if x is not None),Decimal(0)))
        result[k+"_reported_rows"]=sum(x is not None for x in nums)
    return result
def aggreg(rows,by,limit=80):
    bins=defaultdict(list)
    for item in rows:bins[tuple(item.get(k,"") for k in by)].append(item)
    result=[dict(zip(by,k))|totals(v) for k,v in bins.items()]
    result.sort(key=lambda x:value(x["Cost"]) or Decimal(0),reverse=True)
    return result[:limit]
def report(kind,fields,alias,search_only=False):
    filters=[{"Field":"CampaignId","Operator":"IN","Values":[str(CID)]}]
    if search_only:filters.append({"Field":"AdNetworkType","Operator":"EQUALS","Values":["SEARCH"]})
    params={"SelectionCriteria":{"DateFrom":BEFORE,"DateTo":TO,"Filter":filters},
        "FieldNames":fields,"ReportName":"leoparts-current-"+alias+"-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),
        "ReportType":kind,"DateRangeType":"CUSTOM_DATE","Format":"TSV","IncludeVAT":"YES"}
    for attempt in range(1,13):
        code,raw,_=direct.request("reports","read",params,reports=True)
        if code==200:
            reader=csv.DictReader(io.StringIO(raw),delimiter="\t")
            if not reader.fieldnames or not {"CampaignId","Cost","Clicks"}.issubset(reader.fieldnames):
                raise RuntimeError("Unexpected Yandex Report headers: "+alias)
            rows=list(reader)
            if any(row.get("CampaignId")!=str(CID) or None in row for row in rows):
                raise RuntimeError("Invalid report row: "+alias)
            return rows
        if code not in (201,202):raise RuntimeError("Report failed HTTP "+str(code))
        time.sleep(min(5,attempt))
    raise RuntimeError("Report still processing: "+alias)
def campaign_state():
    c=direct.campaign()
    gs=direct.groups()
    ks=direct.keywords()
    negative=(c.get("NegativeKeywords") or {}).get("Items") or []
    if c.get("Id")!=CID or not isinstance(negative,list):
        raise RuntimeError("Unexpected campaign or negative list")
    s=(c.get("UnifiedCampaign") or {}).get("BiddingStrategy") or {}
    sr=(s.get("Search") or {})
    hp=sr.get("HighestPosition") or {}
    weekly=hp.get("WeeklySpendLimit")
    group_labels={str(g.get("Id")):str(g.get("Name")) for g in gs}
    direct.ALLOWED=(*direct.ALLOWED,"ads")
    ads=direct.get("ads",{
        "SelectionCriteria":{"CampaignIds":[CID]},
        "FieldNames":["Id","CampaignId","AdGroupId","Type","State","Status","StatusClarification"],
        "ResponsiveAdFieldNames":["Titles","Texts","Href","SitelinkSetId","SitelinksModeration"],
        "ShoppingAdFieldNames":["SitelinkSetId","SitelinksModeration","FeedId","FeedProcessingStatus"],
        "Page":{"Limit":1000}},"Ads")
    autotargeting=[{"id":k.get("Id"),"group":k.get("AdGroupId"),
        "group_name":group_labels.get(str(k.get("AdGroupId")),""),
        "settings":k.get("AutotargetingSettings"),
        "state":k.get("State")} for k in ks if k.get("Keyword")=="---autotargeting"]
    # Detailed autotargeting settings need specifically requested fields.
    setting_params={
       "SelectionCriteria":{"CampaignIds":[CID]},
       "FieldNames":["Id","CampaignId","AdGroupId","Keyword","State","AutotargetingCategories"],
       "AutotargetingSettingsCategoriesFieldNames":["Exact","Narrow","Alternative","Accessory","Broader"],
       "AutotargetingSettingsBrandOptionsFieldNames":["WithoutBrands","WithAdvertiserBrand","WithCompetitorsBrand"],
       "Page":{"Limit":1000}}
    full=direct.get("keywords",setting_params,"Keywords")
    auto_details=[{"id":k.get("Id"),"group":k.get("AdGroupId"),
        "name":group_labels.get(str(k.get("AdGroupId")),""),"state":k.get("State"),
        "categories":((k.get("AutotargetingSettings") or {}).get("Categories") or {}),
        "brands":((k.get("AutotargetingSettings") or {}).get("BrandOptions") or {})}
        for k in full if k.get("Keyword")=="---autotargeting"]
    return {
      "name":c.get("Name"),"state":c.get("State"),"status":c.get("Status"),
      "type":c.get("Type"),"negative_count":len(negative),
      "critical_negatives":{t:(t in negative) for t in ["сцепление","замена масла","запчасти","автозапчасти","ремонт дсг"]},
      "negatives_hash":hashlib.sha256(json.dumps(negative,ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
      "search_strategy":sr.get("BiddingStrategyType"),
      "weekly_limit_rub":None if weekly is None else float(Decimal(str(weekly))/Decimal(1_000_000)),
      "network_strategy":(s.get("Network") or {}).get("BiddingStrategyType"),
      "priority_goals":(c.get("UnifiedCampaign") or {}).get("PriorityGoals"),
      "counter_ids":(c.get("UnifiedCampaign") or {}).get("CounterIds"),
      "groups_count":len(gs),"manual_keywords":sum(k.get("Keyword")!="---autotargeting" for k in ks),
      "autotargeting_count":len(autotargeting),
      "groups":[{"id":g.get("Id"),"name":g.get("Name"),
          "status":g.get("Status"),
          "group_negative_count":len(((g.get("NegativeKeywords") or {}).get("Items") or [])),
          "group_negative_samples":((g.get("NegativeKeywords") or {}).get("Items") or [])[-10:]} for g in gs],
      "autotargeting":auto_details,
      "ads_count":len(ads),
      "ads":[{"id":a.get("Id"),"group":a.get("AdGroupId"),"type":a.get("Type"),
           "state":a.get("State"),"status":a.get("Status"),
           "status_details":a.get("StatusClarification"),
           "titles":((a.get("ResponsiveAd") or {}).get("Titles") or [])[:3],
           "text":((a.get("ResponsiveAd") or {}).get("Texts") or [])[:2],
           "href":((a.get("ResponsiveAd") or {}).get("Href") or "")[:250]} for a in ads],
    }
def metrika():
    metrics=["ym:s:visits"]+["ym:s:goal"+str(g)+"visits" for g in GOALS]
    cols=["visits"]+[str(g) for g in GOALS]
    result={}
    for name,dim in [("date","ym:s:date"),("utm_campaign","ym:s:lastUTMSource,ym:s:lastUTMCampaign")]:
        params={"ids":str(COUNTER_ID),"date1":BEFORE,"date2":TO,
                "metrics":",".join(metrics),"dimensions":dim,"accuracy":"full","limit":"150","lang":"ru"}
        d=mt.call("/stat/v1/data",params)
        if not isinstance(d.get("totals"),list) or len(d["totals"])!=len(cols):
            raise RuntimeError("Metrika goal metrics missing")
        records=[]
        for item in d.get("data",[]):
            if len(item.get("metrics") or [])!=len(cols):raise RuntimeError("Goal count mismatch")
            records.append({"dimensions":[str(v.get("name",""))[:100] for v in item.get("dimensions",[])],
                            "counts":dict(zip(cols,item["metrics"]))})
        result[name]={"rows":records,"totals":dict(zip(cols,d["totals"])),
                      "sampled":d.get("sampled"),"sample_share":d.get("sample_share")}
    return result
def run():
    doc={"status":"running","campaign_id":CID,"mode":"read_only","start_utc":stamp(),
         "moscow_date":TO,"period_from":BEFORE,"period_through":TO,
         "changes_at":"2026-11-22","read_errors":{},"sections":{},"external_mutations":0}
    save(doc)
    def go(k,f):
        try:
            doc["sections"][k]={"status":"ok","data":f()}
        except Exception as exc:
            doc["sections"][k]={"status":"error","error":direct.scrub(exc)}
            doc["read_errors"][k]=direct.scrub(exc)
        save(doc)
    go("campaign",campaign_state)
    go("daily",lambda:{"by_date":aggreg(report("CAMPAIGN_PERFORMANCE_REPORT",["Date","CampaignId","Impressions","Clicks","Cost","Conversions"],"daily"),["Date"],15)})
    def queries():
        rows=report("SEARCH_QUERY_PERFORMANCE_REPORT",["Date","CampaignId","AdGroupId","Query","CriterionType","MatchedKeyword","Impressions","Clicks","Cost","Conversions"],"query")
        paid=[r for r in rows if (value(r.get("Cost")) or Decimal(0))>0]
        recent=[r for r in paid if r.get("Date")>=CHANGED]
        return {"total_rows":len(rows),"paid_rows":len(paid),
                "paid":aggreg(paid,["Query","AdGroupId","CriterionType"],100),
                "after_changes_paid":aggreg(recent,["Query","AdGroupId","CriterionType"],80),
                "after_changes_totals":totals(recent),
                "after_changes_query_rows":len(recent),
                "by_criterion":aggreg(rows,["CriterionType","AdGroupId"],50)}
    go("queries",queries)
    def groups():
        rows=report("ADGROUP_PERFORMANCE_REPORT",
                    ["Date","CampaignId","AdGroupId","AdGroupName","Impressions","Clicks","Cost","Conversions"],"group")
        return {"by_group":aggreg(rows,["AdGroupId","AdGroupName"],30),
                "by_group_date":aggreg(rows,["Date","AdGroupId","AdGroupName"],75)}
    go("groups",groups)
    if os.getenv("YANDEX_METRIKA_TOKEN"):
        go("metrika",metrika)
    else:doc["sections"]["metrika"]={"status":"unavailable","reason":"Metrika read token not configured"}
    doc["status"]="complete" if not doc["read_errors"] else "partial"
    doc["finished_utc"]=stamp();save(doc)
    print(json.dumps({"status":doc["status"],"period":[BEFORE,TO],
        "read_errors":doc["read_errors"],"sections":{k:v["status"] for k,v in doc["sections"].items()},
        "budget_rub":(doc["sections"].get("campaign",{}).get("data") or {}).get("weekly_limit_rub")},ensure_ascii=False),flush=True)
    return 0 if doc["status"]=="complete" else 1
if __name__=="__main__":sys.exit(run())
