"""LEOPARTS: Direct + Metrica 30 full days plus intraday, exact campaign only.
All external API calls read-only. Report request is a read, not a campaign write.
Preserve missing metrics and full paid query rows, not truncated top lists.
"""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from collections import defaultdict,Counter
from decimal import Decimal
from zoneinfo import ZoneInfo
import csv,io,json,sys,time,re
sys.path.insert(0,str(Path(__file__).resolve().parent))
import direct_commercial_audit_20261124 as a
import direct_conversion_reconciliation_20261120 as reconciliation
import leoparts_purchase_readiness as readiness
import direct_preflight_20261120 as api
import direct_metrika_read_20261120 as mt
ROOT=Path("data/direct_performance_20261125")
OUT=ROOT/"live_30d.json"
CID=716316525;COUNTER=113839860;FID=4061297
TODAY=datetime.now(ZoneInfo("Europe/Moscow")).date()
START=TODAY-timedelta(days=30)
END=TODAY-timedelta(days=1)
METRICS=("Impressions","Clicks","Cost","Conversions")
def stamp():return datetime.now(timezone.utc).isoformat()
def save(d):
 ROOT.mkdir(parents=True,exist_ok=True)
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n");tmp.replace(OUT)
def num(x):return a.value(x)
def aggregate(rows,fields):
 r={}
 for k in fields:
  v=[num(x.get(k)) for x in rows];n=[x for x in v if x is not None]
  r[k]=str(sum(n,Decimal(0))) if n else None
  r[k+"_numeric_rows"]=len(n)
 return r
def group(rows,keys,fields=METRICS):
 buckets=defaultdict(list)
 for row in rows:buckets[tuple(row.get(k,"") for k in keys)].append(row)
 return [dict(zip(keys,k))|aggregate(v,fields) for k,v in sorted(buckets.items(),key=lambda kv:str(kv[0]))]
def report(start,end,kind,fields,label,goals=None,extra_filters=None):
 params={"SelectionCriteria":{"DateFrom":start.isoformat(),"DateTo":end.isoformat(),"Filter":[{"Field":"CampaignId","Operator":"IN","Values":[str(CID)]}]+(extra_filters or [])},"FieldNames":fields,"ReportName":"leoparts-30d-"+label+"-"+datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f"),"ReportType":kind,"DateRangeType":"CUSTOM_DATE","Format":"TSV","IncludeVAT":"YES"}
 if goals:
  params["Goals"]=[str(x) for x in goals];params["AttributionModels"]=["LC"]
 for attempt in range(10):
  code,raw,_=api.request("reports","read",params,reports=True)
  if code==200:
   reader=csv.DictReader(io.StringIO(raw),delimiter="\t")
   if not reader.fieldnames or not {"CampaignId","Date","Cost","Clicks"}<=set(reader.fieldnames):raise ValueError("Unexpected report columns")
   rows=list(reader)
   if any(x.get("CampaignId")!=str(CID) or not start.isoformat()<=x.get("Date","")<=end.isoformat() or None in x for x in rows):raise ValueError("Report scope mismatch")
   return rows
  if code not in (201,202):raise ValueError("Report HTTP "+str(code))
  time.sleep(min(8,attempt+2))
 raise ValueError("Report processing limit")
def run():
 d={"platform":"Direct","campaign_id":CID,"counter_id":COUNTER,"feed_id":FID,"mode":"strict_read_only","external_mutations":0,"started_utc":stamp(),"full_days":[START.isoformat(),END.isoformat()],"today":TODAY.isoformat(),"sections":{},"errors":{}}
 save(d)
 def go(k,fn):
  try:d["sections"][k]={"status":"ok","data":fn()}
  except Exception as e:d["sections"][k]={"status":"error","error":api.scrub(e)};d["errors"][k]=api.scrub(e)
  save(d)
 api.ALLOWED=tuple(set(api.ALLOWED)|{"ads","feeds","sitelinks"})
 go("campaign",a.campaign_state)
 go("keywords",api.keywords)
 go("groups",api.groups)
 def daily():
  rows=report(START,TODAY,"CAMPAIGN_PERFORMANCE_REPORT",["Date","CampaignId",*METRICS],"campaign")
  windows={}
  for n in (7,14,30):
   lo=(TODAY-timedelta(days=n)).isoformat()
   windows[str(n)]={"from":lo,"to":END.isoformat(),"metrics":aggregate([x for x in rows if lo<=x["Date"]<=END.isoformat()],METRICS)}
  return {"by_date":group(rows,["Date"]),"full_windows":windows,"intraday":aggregate([x for x in rows if x["Date"]==TODAY.isoformat()],METRICS)}
 go("daily",daily)
 def queries():
  paid=[];daily_all=[];chunks=[];errors=[]
  start=START
  while start<=TODAY:
   end=min(start+timedelta(days=6),TODAY)
   try:
    rows=report(start,end,"SEARCH_QUERY_PERFORMANCE_REPORT",["Date","CampaignId","AdGroupId","Query","CriterionType","MatchedKeyword",*METRICS],"query-"+start.isoformat())
    hits=[x for x in rows if (num(x.get("Cost")) or 0)>0 or (num(x.get("Clicks")) or 0)>0]
    paid.extend(hits);daily_all.extend(group(rows,["Date"]))
    chunks.append({"from":start.isoformat(),"to":end.isoformat(),"rows":len(rows),"paid_rows":len(hits),"metrics":aggregate(rows,METRICS)})
   except Exception as e:errors.append({"from":start.isoformat(),"to":end.isoformat(),"error":api.scrub(e)})
   start=end+timedelta(days=1)
  return {"coverage_complete":not errors,"chunks":chunks,"errors":errors,"paid_rows":paid,"daily":daily_all,"paid_grouped":group(paid,["Query","AdGroupId","CriterionType","MatchedKeyword"]),"paid_by_date_criterion":group(paid,["Date","CriterionType"])}
 go("queries",queries)
 goals=[675272284,675487735,675506966,675507028,682257540,685988392,686020680,675271839,675271996,675272154]
 def goalreport():
  rows=report(START,TODAY,"ADGROUP_PERFORMANCE_REPORT",["Date","CampaignId","AdGroupId","Impressions","Clicks","Cost","Conversions"],"goal-attribution",goals=goals)
  fields=sorted({k for x in rows for k in x if k.startswith("Conversions_")})
  return {"goals":goals,"attribution":"LC","fields":fields,"rows":rows,"totals":aggregate(rows,["Impressions","Clicks","Cost"]+fields),"by_group":group(rows,["AdGroupId"],["Impressions","Clicks","Cost"]+fields),"note":"Empty/-- remains missing, not zero; goal attribution does not prove paid transaction."}
 go("goal_attribution",goalreport)
 def metrika():
  gs=mt.call("/management/v1/counter/113839860/goals").get("goals",[])
  active=[x for x in gs if x.get("status")=="Active"]
  metrics=["ym:s:visits","ym:s:bounceRate","ym:s:pageDepth","ym:s:avgVisitDurationSeconds"]+["ym:s:goal"+str(g["id"])+"visits" for g in active]
  common={"ids":str(COUNTER),"date1":START.isoformat(),"date2":TODAY.isoformat(),"metrics":",".join(metrics),"accuracy":"full","limit":"100","lang":"ru"}
  results={}
  for k,dim,fil in [("paid_utm_by_day","ym:s:date","ym:s:lastUTMCampaign=='dq200_feed_search'"),("all_by_source","ym:s:lastTrafficSource",None),("paid_by_device","ym:s:deviceCategory","ym:s:lastUTMCampaign=='dq200_feed_search'"),("all_utm","ym:s:lastUTMSource,ym:s:lastUTMCampaign",None)]:
   try:results[k]={"status":"ok","data":mt.sanitize_stats(mt.call("/stat/v1/data",common|{"dimensions":dim}|({"filters":fil} if fil else {})))}
   except Exception as e:results[k]={"status":"error","error":api.scrub(e)}
  return {"goals":[{"id":g.get("id"),"name":g.get("name"),"type":g.get("type"),"status":g.get("status"),"conditions":[{k:c.get(k) for k in ("type","url","condition","formId","selector") if k in c} for c in (g.get("conditions") or []) if isinstance(c,dict)]} for g in gs],"metrics":metrics,"reports":results}
 go("metrika",metrika)
 go("purchase_counter",readiness.counter_options)
 go("inventory_webhook_counts",readiness.tilda_form_dispatch_counts)
 def feed_ads():
  f=api.get("feeds",{"SelectionCriteria":{"Ids":[FID]},"FieldNames":["Id","Name","SourceType","Status","UpdatedAt","NumberOfItems","TitleAndTextSources"],"UrlFeedFieldNames":["Url"],"Page":{"Limit":100}},"Feeds")
  if len(f)!=1 or f[0]["Id"]!=FID:raise ValueError("Unexpected feed")
  ad=api.get("ads",{"SelectionCriteria":{"CampaignIds":[CID]},"FieldNames":["Id","CampaignId","AdGroupId","Type","State","Status","StatusClarification"],"ResponsiveAdFieldNames":["Titles","Texts","Href","SitelinkSetId","SitelinksModeration"],"ShoppingAdFieldNames":["FeedId","FeedProcessingStatus","SitelinkSetId","SitelinksModeration","FeedFilterConditions"],"Page":{"Limit":1000}},"Ads")
  ids=sorted({z["SitelinkSetId"] for x in ad for z in [x.get("ResponsiveAd") or {},x.get("ShoppingAd") or {}] if z.get("SitelinkSetId")})
  sets=api.get("sitelinks",{"SelectionCriteria":{"Ids":ids},"FieldNames":["Id","Sitelinks"],"Page":{"Limit":1000}},"SitelinksSets") if ids else []
  return {"feed":f,"ads":[dict(x,Id=str(x["Id"])) for x in ad],"sitelink_sets":sets}
 go("feed_ads",feed_ads)
 def segments():
  rows=report(START,TODAY,"CUSTOM_REPORT",["Date","CampaignId","AdGroupId","AdNetworkType","Device","Impressions","Clicks","Cost","Conversions"],"segment")
  return {"by_day_group_device_network":group(rows,["Date","AdGroupId","AdNetworkType","Device"])}
 go("segments",segments)
 d["status"]="complete" if not d["errors"] and d["sections"].get("queries",{}).get("data",{}).get("coverage_complete") else "partial"
 d["finished_utc"]=stamp();save(d)
 # Separate small summary for review even if full report exceeds connector limit.
 s={"started_utc":d["started_utc"],"finished_utc":d["finished_utc"],"status":d["status"],"dates":[d["full_days"],d["today"]],"sections":{k:v["status"] for k,v in d["sections"].items()},"errors":d["errors"]}
 for k in ("daily","goal_attribution","feed_ads","purchase_counter","inventory_webhook_counts"):
  v=d["sections"].get(k,{}).get("data")
  if v:
   s[k]={i:j for i,j in v.items() if i!="rows"} if isinstance(v,dict) else v
 q=d["sections"].get("queries",{}).get("data",{})
 s["query_coverage"]={k:q.get(k) for k in ("chunks","errors","coverage_complete","paid_by_date_criterion")}
 s["query_paid_count"]=len(q.get("paid_rows",[]))
 (ROOT/"live_30d_summary.json").write_text(json.dumps(s,ensure_ascii=False,indent=2,default=str)+"\n")
 print(json.dumps({"status":d["status"],"errors":d["errors"],"dates":s["dates"],"query_paid_count":s["query_paid_count"],"daily":s.get("daily",{}).get("full_windows")},ensure_ascii=False))
if __name__=="__main__":run()
