"""One rate-limited dated Market sales funnel request; never modify offers."""
import datetime as dt, json, sys, time, io, zipfile, collections
from pathlib import Path
from urllib.parse import urlsplit
import requests
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import market_full_seo_20261125 as s
import yandex_market_client as ym
OUT=ROOT/"data/market_dated_funnel_20261125.json"
PREV=ROOT/"data/market_growth_analytics_20261125.json"
PERIOD={"dateFrom":"2026-10-26","dateTo":"2026-11-24"}
def now():return dt.datetime.now(dt.timezone.utc)
def save(d):
 d["updated_at"]=now().isoformat()
 tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n");tmp.replace(OUT)
def extract(x):
 if isinstance(x,list):
  for v in x:yield from extract(v)
 elif isinstance(x,dict):
  if "offerId" in x and ("shows" in x or "orderItems" in x):yield x
  else:
   for v in x.values():
    if isinstance(v,(list,dict)):yield from extract(v)
def run():
 biz,cam=s.account()
 if biz!=194752944 or int(cam)!=141377963:raise ValueError("Wrong account")
 old=json.loads(OUT.read_text()) if OUT.exists() else {}
 prior=json.loads(PREV.read_text()) if PREV.exists() and PREV.stat().st_size else {}
 if old.get("status")=="DONE" and old.get("period")==PERIOD and old.get("daily_rows"):return
 d={"platform":"Yandex Market","business_id":biz,"campaign_id":int(cam),"period":PERIOD,"read_only":True,"external_content_writes":0,"status":"initial","errors":{}}
 # Actual orders from the earlier successful read, without conflating fee,
 # subsidies and customer payment. No new order generation or changes.
 orders=(prior.get("order_stats") or {}).get("rows",[])
 if orders:
  comm=collections.defaultdict(float);pay=collections.defaultdict(float);sub=collections.defaultdict(float)
  statuses=collections.Counter();counts=collections.defaultdict(lambda:{"ordered_units":0,"delivered_units":0,"cancelled_units":0})
  for o in orders:
   statuses[str(o.get("status"))]+=1
   for it in o.get("items",[]):
    k=it.get("sku")
    if not k:continue
    n=int(it.get("count") or 0);counts[k]["ordered_units"]+=n
    if o.get("status")=="DELIVERED":counts[k]["delivered_units"]+=n
    if str(o.get("status","")).startswith("CANCELLED"):counts[k]["cancelled_units"]+=n
   for a,key,field in [(comm,"commissions","actual"),(pay,"payments","total"),(sub,"subsidies","amount")]:
    for x in o.get(key,[]):
     if isinstance(x.get(field),(int,float)):a[str(x.get("type"))+":"+str(x.get("source") or x.get("operationType") or "")]+=x[field]
  d["actual_order_snapshot"]={"orders":len(orders),"statuses":dict(statuses),"sku_counts":dict(counts),"commissions_by_type":dict(comm),"payment_flows_by_type":dict(pay),"subsidies_by_type":dict(sub),"warning":"Order-status snapshot, separate fee and payment flows; no invented allocation or net profit."}
 save(d)
 rid=old.get("report_id")
 previous=prior.get("sales_report") or {}
 if not rid and previous.get("report_id") and (prior.get("period") or {})=={"from":PERIOD["dateFrom"],"to":PERIOD["dateTo"]}:rid=previous["report_id"]
 # Never generate a second report within the stricter official ten-minute limit.
 if not rid:
  recent=old.get("attempted_at") or old.get("last_attempt_at") or prior.get("finished_utc")
  # A definite HTTP 400 mutual-exclusion rejection did not generate a report.
  # Permit exactly one corrected request; all subsequent rate limits retain cooldown.
  correction=old.get("status")=="generation_error" and "Both businessId and campaignId are specified" in str(old.get("errors",{}))
  if correction:recent=prior.get("finished_utc")
  if recent:
   try:
    if (now()-dt.datetime.fromisoformat(recent)).total_seconds()<600:
     d["status"]="rate_limit_cooldown";d["last_attempt_at"]=recent;save(d);return
   except ValueError:pass
  d["attempted_at"]=now().isoformat();save(d)
  try:
   # Report generation only; no seller catalog mutation. One HTTP request, no retry.
   token,_=ym._creds()
   response=requests.post(ym.BASE_URL+"/v2/reports/shows-sales/generate",headers=ym._headers(token),params={"format":"JSON"},json={"campaignId":int(cam),**PERIOD,"grouping":"OFFERS"},timeout=60)
   d["generation_http"]=response.status_code
   d["retry_after"]=response.headers.get("Retry-After")
   try:x=response.json()
   except ValueError:x={}
   if response.status_code!=200 or x.get("status")!="OK":
    d["generation_api_errors"]=[{k:e.get(k) for k in ("code","message")} for e in x.get("errors",[]) if isinstance(e,dict)]
    raise ValueError("HTTP "+str(response.status_code)+" "+ym._errors_text(x)[:240])
   rid=(x.get("result") or {}).get("reportId")
   if not rid:raise ValueError("No report identifier")
  except Exception as e:
   d["status"]="rate_limited" if any(k in str(e) for k in ("420","429")) else "generation_error"
   d["errors"]["generate"]=str(e)[:300];save(d);print(d["status"]);return
 d["report_id"]=rid;d["status"]="submitted";save(d)
 try:
  for _ in range(18):
   code,body=ym._request("GET",f"/v2/reports/info/{rid}",retries=1)
   if code!=200 or body.get("status")!="OK":raise ValueError(f"Report info HTTP {code}")
   result=body.get("result") or {};d["status"]=result.get("status");d["sub_status"]=result.get("subStatus");save(d)
   if d["status"]=="FAILED":break
   if d["status"]=="DONE":
    u=result.get("file")
    if not u or urlsplit(u).scheme!="https":raise ValueError("Missing secure report URL")
    response=requests.get(u,timeout=40);response.raise_for_status()
    if len(response.content)>15*1024*1024:raise ValueError("Report too large")
    fields={"day","month","year","offerId","offerName","categoryName","brandName","byMskuShows","visibilityIndex","shows","showsWithPromotion","showsShare","clicks","clicksWithPromotion","toCartConversion","toCart","toCartWithPromotion","toCartShare","orderItems","orderItemsWithPromotion","orderItemsTotalAmount","orderItemsTotalAmountWithPromotion","toOrderConversion","orderItemsShare","orderItemsDeliveredCount","orderItemsDeliveredTotalAmount","orderItemsDeliveredFromOrderedCount","orderItemsDeliveredFromOrderedTotalAmount","orderItemsCanceledCount","orderItemsCanceledByCreatedAtCount","orderItemsReturnedCount","orderItemsReturnedByCreatedAtCount","orderItemsDeliveredCountWithPromotion","orderItemsDeliveredTotalAmountWithPromotion","orderItemsDeliveredFromOrderedTotalAmountWithPromotion"}
    daily=[]
    with zipfile.ZipFile(io.BytesIO(response.content)) as z:
     for n in z.namelist():
      if not n.lower().endswith(".json"):continue
      if z.getinfo(n).file_size>30*1024*1024:raise ValueError("Report entry too large")
      daily.extend({k:v for k,v in row.items() if k in fields} for row in extract(json.loads(z.read(n))))
    if not daily:raise ValueError("No recognized dated funnel rows; format requires review")
    if any(not all(k in row for k in ("day","month","year","offerId")) for row in daily):raise ValueError("Missing dated SKU key")
    keys=[(row["year"],row["month"],row["day"],row["offerId"]) for row in daily]
    if len(keys)!=len(set(keys)):raise ValueError("Duplicate dated SKU rows; review report structure")
    d["daily_rows"]=daily;d["row_count"]=len(daily)
    d["totals"]={k:sum(x[k] for x in daily if isinstance(x.get(k),(int,float))) for k in ("shows","clicks","toCart","orderItems","orderItemsTotalAmount","orderItemsDeliveredCount","orderItemsDeliveredTotalAmount","orderItemsCanceledCount","orderItemsReturnedCount")}
    break
   time.sleep(8)
 except Exception as e:d["errors"]["readback"]=str(e)[:300];d["status"]="readback_pending_or_error"
 save(d);print(json.dumps({"status":d["status"],"rows":d.get("row_count"),"totals":d.get("totals"),"errors":d["errors"]},ensure_ascii=False))
if __name__=="__main__":run()
