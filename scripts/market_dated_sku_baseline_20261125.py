"""Summarize dated Market funnel and link exact SKU economics; read-only to Market."""
import collections, datetime as dt, json, re, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
DATA=ROOT/"data"
BASE=DATA/"market_dated_funnel_20261125.json"
FIN=DATA/"market_final_financial_plan_20261125.json"
OUT=DATA/"market_dated_sku_baseline_20261125.json"
FIELDS=("shows","clicks","toCart","orderItems","orderItemsTotalAmount",
        "orderItemsDeliveredCount","orderItemsDeliveredTotalAmount",
        "orderItemsDeliveredFromOrderedCount","orderItemsDeliveredFromOrderedTotalAmount",
        "orderItemsCanceledCount","orderItemsCanceledByCreatedAtCount",
        "orderItemsReturnedCount","orderItemsReturnedByCreatedAtCount")
MONTHS={name:i for i,variants in enumerate([
 ("январь","января","jan","january"),("февраль","февраля","feb","february"),
 ("март","марта","mar","march"),("апрель","апреля","apr","april"),
 ("май","мая","may"),("июнь","июня","jun","june"),("июль","июля","jul","july"),
 ("август","августа","aug","august"),("сентябрь","сентября","sep","september"),
 ("октябрь","октября","oct","october"),("ноябрь","ноября","nov","november"),
 ("декабрь","декабря","dec","december")],1) for name in variants}
def date_of(row):
 day=str(row["day"]).strip();month=str(row["month"]).strip();year=int(row["year"])
 for fmt in ("%Y-%m-%d","%d-%m-%Y","%d.%m.%Y","%d.%m.%y","%d/%m/%Y"):
  try:return dt.datetime.strptime(day,fmt).date()
  except ValueError:pass
 if day.isdigit():
  m=int(month) if month.isdigit() else MONTHS.get(month.casefold())
  if m:return dt.date(year,m,int(day))
 for fmt in ("%Y-%m-%d","%Y-%m","%m-%Y"):
  try:
   m=dt.datetime.strptime(month,fmt).month
   if day.isdigit():return dt.date(year,m,int(day))
  except ValueError:pass
 raise ValueError("Unrecognized date: "+repr((day,month,year)))
def agg(rows):
 out={}
 for field in FIELDS:
  vals=[r[field] for r in rows if isinstance(r.get(field),(int,float)) and not isinstance(r.get(field),bool)]
  out[field]=round(sum(vals),2) if vals else None
 a,b,c,o=(out[k] for k in ("shows","clicks","toCart","orderItems"))
 def ratio(x,y):return round(100*x/y,2) if x is not None and y is not None and y>0 else None
 out["ctr_pct"]=ratio(b,a);out["show_to_cart_pct"]=ratio(c,a);out["cart_to_order_units_pct"]=ratio(o,c)
 out["show_to_order_units_pct"]=ratio(o,a)
 return out
def run():
 base=json.loads(BASE.read_text());fin=json.loads(FIN.read_text())
 if base.get("platform")!="Yandex Market" or base.get("business_id")!=194752944 or base.get("campaign_id")!=141377963 or base.get("status")!="DONE":raise ValueError("No complete exact Market report")
 start=dt.date.fromisoformat(base["period"]["dateFrom"]);end=dt.date.fromisoformat(base["period"]["dateTo"])
 raw=base["daily_rows"]
 if len(raw)!=base["row_count"]:raise ValueError("Incomplete row count")
 dates=[]
 for x in raw:dates.append((date_of(x),x))
 if any(not start<=day<=end for day,x in dates):raise ValueError("Report date outside requested window")
 keys=[(day,x["offerId"]) for day,x in dates]
 if len(keys)!=len(set(keys)):raise ValueError("Duplicate dated exact SKU")
 d={"platform":"Yandex Market","business_id":194752944,"campaign_id":141377963,"source":BASE.name,"period":base["period"],"report_id":base.get("report_id"),"source_row_count":len(raw),"report_date_count":len(set(day for day,x in dates)),"read_only":True,"generated_at":dt.datetime.now(dt.timezone.utc).isoformat(),"periods":{},"sku_rows":{}}
 for n in (7,14,30):
  lo=end-dt.timedelta(days=n-1)
  if lo<start:raise ValueError("Window not fully covered")
  d["periods"][str(n)]={"from":lo.isoformat(),"to":end.isoformat(),"metrics":agg([x for day,x in dates if day>=lo]),"distinct_report_days":len({day for day,x in dates if day>=lo})}
 groups=collections.defaultdict(list)
 for day,x in dates:groups[x["offerId"]].append((day,x))
 for sku in sorted(set(fin["rows"])|set(groups)):
  ff=fin["rows"].get(sku,{});rr=groups.get(sku,[])
  z={"sku":sku,"name":ff.get("name"),"catalog_status_snapshot":ff.get("status"),"stock_snapshot":ff.get("stock"),"price_snapshot":ff.get("price"),"content_rating_snapshot":ff.get("content_rating"),"purchase_cost_snapshot":ff.get("purchase_cost"),"bom_cost_snapshot":ff.get("bom_cost"),"conservative_unit_cost_scenario":ff.get("conservative_cost_scenario"),"current_unit_economics_scenario":ff.get("current_economics"),"absolute_CAC_ceiling_before_other_costs":ff.get("absolute_CAC_ceiling_before_other_costs"),"report_covered":bool(rr),"metrics":{}}
  for n in (7,14,30):
   lo=end-dt.timedelta(days=n-1)
   a=[x for day,x in rr if day>=lo];metric=agg(a)
   metric["report_rows"]=len(a)
   # Visibility is SKU-specific; aggregate across dated observations using market shows as weights.
   pairs=[]
   for x in a:
    w=x.get("byMskuShows")
    try:
     v=float(str(x.get("visibilityIndex","")).replace("%","").replace(",","."))
     if isinstance(w,(int,float)) and w>0 and 0<=v<=100:pairs.append((v,w))
    except (TypeError,ValueError):pass
   metric["weighted_visibility_index_as_reported"]=round(sum(v*w for v,w in pairs)/sum(w for v,w in pairs),2) if pairs else None
   z["metrics"][str(n)]=metric
  d["sku_rows"][sku]=z
 rows=list(d["sku_rows"].values())
 ranked=[x for x in rows if x["metrics"]["30"]["report_rows"]>0]
 carts=[x for x in ranked if (x["metrics"]["30"]["toCart"] or 0)>0 and x["metrics"]["30"]["orderItems"]==0]
 d["cart_without_orders"]=[{"sku":x["sku"],"name":x["name"],"metrics":x["metrics"]["30"],"price":x["price_snapshot"],"stock":x["stock_snapshot"]} for x in sorted(carts,key=lambda x:-(x["metrics"]["30"]["toCart"] or 0))]
 d["top_delivered"]=[{"sku":x["sku"],"name":x["name"],"delivered":x["metrics"]["30"]["orderItemsDeliveredCount"],"revenue":x["metrics"]["30"]["orderItemsDeliveredTotalAmount"],"unit_cost_scenario":x["purchase_cost_snapshot"]} for x in sorted(ranked,key=lambda x:-(x["metrics"]["30"]["orderItemsDeliveredTotalAmount"] or 0)) if (x["metrics"]["30"]["orderItemsDeliveredCount"] or 0)>0]
 d["coverage"]={"catalog":len(fin["rows"]),"report_skus":len(groups),"union":len(rows),"known_catalog_with_report":sum(x["report_covered"] for x in rows if x["sku"] in fin["rows"]),"missing_from_report":sum(not x["report_covered"] for x in rows),"report_skus_not_in_catalog":sorted(set(groups)-set(fin["rows"]))}
 d["notes"]=["Counts are units, not necessarily unique orders or customers; cart/order ratio is a descriptive aggregate.","Unknown/missing report rows remain null, not zero. Delivery/return event cohorts differ from orders placed in the period.","Catalog prices and modeled fees are from earlier API snapshot. Buyer revenue and settlement/subsidies are not interchangeable. Do not subtract modeled current fees from historic buyer revenue to claim net profit.","Use equal windows and same exact SKU cohort after verified changes; report full days separately from late moderation; no mixing Ozon data."]
 d["actual_order_snapshot"]=base.get("actual_order_snapshot")
 # Ensure aggregates reconcile to the original report's totals before publishing.
 for k,v in base.get("totals",{}).items():
  if d["periods"]["30"]["metrics"].get(k)!=v:raise ValueError("Aggregate total mismatch "+k)
 OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n")
 print(json.dumps({"coverage":d["coverage"],"periods":d["periods"],"cart_without_orders":[[x["sku"],x["metrics"]["toCart"],x["metrics"]["shows"]] for x in d["cart_without_orders"]],"top_delivered":d["top_delivered"][:12]},ensure_ascii=False))
if __name__=="__main__":run()
