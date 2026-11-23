"""Read-only LEOPARTS Ozon Sep/Oct/Nov 2026: delivered vs ordered.
Never store buyer names, addresses, phone numbers or posting numbers.
Important: posting status grouped by ORDER CREATION month != delivered calendar month.
"""
from collections import Counter,defaultdict
from datetime import datetime,timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import json,math,sys
import ozon_client as oz

OUT=Path("data/ozon_delivered_3m_20261123.json")
NOW=datetime.now(timezone.utc)
MSK=ZoneInfo("Europe/Moscow")
TODAY=NOW.astimezone(MSK).date().isoformat()
MONTHS=("2026-09","2026-10","2026-11")
WINDOWS=(("2026-09","2026-08-31T21:00:00Z","2026-09-30T21:00:00Z"),
         ("2026-10","2026-09-30T21:00:00Z","2026-10-31T21:00:00Z"),
         ("2026-11","2026-10-31T21:00:00Z",NOW.isoformat()))
R={"status":"running","mode":"read_only","seller_mutations":0,
   "captured_utc":NOW.isoformat(),"captured_moscow":NOW.astimezone(MSK).isoformat(),
   "month_through":TODAY,"sections":{},"errors":[],
   "privacy":"Only aggregated counts/SKUs are stored; buyer fields and order identifiers excluded"}

def save():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    draft=OUT.with_suffix(".tmp")
    draft.write_text(json.dumps(R,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    draft.replace(OUT)

def date_parse(v):
    if not isinstance(v,str) or not v:return None
    try:
        t=datetime.fromisoformat(v.replace("Z","+00:00"))
        return t if t.tzinfo else t.replace(tzinfo=timezone.utc)
    except (TypeError,ValueError):return None

def order_date(p,kind):
    for key in (("in_process_at","created_at") if kind=="FBS" else ("created_at","in_process_at")):
        d=date_parse(p.get(key))
        if d:return d.astimezone(MSK).date().isoformat()
    return None

def qty(v):
    try:return max(0,int(v))
    except (TypeError,ValueError):return 0

def money(v):
    try:
        x=float(str(v).replace(",","."))
        return x if math.isfinite(x) and x>=0 else None
    except (TypeError,ValueError):return None

def status(v):
    s=str(v or "unknown").lower()
    if s=="delivered":return "delivered"
    if s in ("cancelled","canceled"):return "cancelled"
    if s in ("delivering","awaiting_deliver","awaiting_packaging","awaiting_approve","not_accepted","arbitration","driver_pickup"):
        return "in_transit_or_pending"
    return "other"

def summarize(rows,start,end):
    group=[x for x in rows if start<=x["day"]<=end]
    stats=Counter()
    units=Counter()
    skus=defaultdict(Counter)
    daily=defaultdict(Counter)
    cancel=Counter()
    price_count=0
    price_total=0
    tentative_delivered_value=0.0
    for p in group:
        cat=p["status"]
        stats[cat]+=1;units[cat]+=p["units"];daily[p["day"]][cat]+=p["units"]
        if cat=="cancelled":cancel[p["reason"]]+=1
        for item in p["products"]:
            sku=item["sku"];n=item["qty"]
            skus[sku]["ordered"]+=n
            skus[sku][cat]+=n
            price_total+=1
            if item["price"] is not None:
                price_count+=1
                if cat=="delivered":tentative_delivered_value+=n*item["price"]
    return {
      "posting_count":len(group),"postings_by_current_status":dict(stats),
      "units_ordered":sum(x["units"] for x in group),
      "units_by_current_status":dict(units),
      "delivered_price_estimate_rub":round(tentative_delivered_value,2) if price_count==price_total and price_total else None,
      "price_coverage":{"with_price":price_count,"items":price_total},
      "daily_delivered_by_CREATION_date":{k:v["delivered"] for k,v in sorted(daily.items())},
      "cancel_reasons":dict(cancel.most_common(15)),
      "top_delivered_skus":[{"sku":k,"delivered_units":v["delivered"],"ordered_units":v["ordered"]}
                            for k,v in sorted(skus.items(),key=lambda x:(-x[1]["delivered"],x[0]))[:35]],
      "sku_stats":{k:dict(v) for k,v in skus.items()}
    }

def compact(s):
    a=dict(s)
    a.pop("sku_stats",None)
    return a

def cohorts():
    rows=[];seen=set();received={};missing_dates=Counter();unknown_qty=0
    for month,since,to in WINDOWS:
        for scheme,read in (("FBS",oz.get_fbs_orders_since),("FBO",oz.get_fbo_orders_since)):
            posts=read(since,to)
            received[scheme+"_"+month]=len(posts)
            for p in posts:
                if not isinstance(p,dict):continue
                no=str(p.get("posting_number") or "")
                if no and (scheme,no) in seen:continue
                if no:seen.add((scheme,no))
                day=order_date(p,scheme)
                if not day:
                    missing_dates[scheme]+=1;continue
                if day<"2026-09-01" or day>TODAY:continue
                items=[]
                for x in p.get("products") or []:
                    if not isinstance(x,dict):continue
                    items.append({"sku":str(x.get("offer_id") or "unknown")[:90],
                                  "qty":qty(x.get("quantity")),"price":money(x.get("price"))})
                ca=p.get("cancellation") or {}
                if not isinstance(ca,dict):ca={}
                reason=str(ca.get("cancel_reason") or ca.get("cancel_reason_name") or "не указано")[:95]
                total=sum(x["qty"] for x in items)
                if not total:unknown_qty+=1
                rows.append({"day":day,"scheme":scheme,"status":status(p.get("status")),
                             "units":total,"reason":reason,"products":items})
    output={"definition":"CREATION-month cohort with status at snapshot (not delivery-date calendar month)",
            "source":"Ozon posting FBS v4 / FBO v3; no seller writes",
            "received_api_rows":received,"deduplicated_postings":len(rows),
            "missing_order_dates":dict(missing_dates),"postings_without_quantity":unknown_qty,
            "months":{},"by_scheme":{},"first_15_days":{},"first_23_days":{},"sku_deltas":{}}
    for scheme in ("FBS","FBO","ALL"):
        part=rows if scheme=="ALL" else [r for r in rows if r["scheme"]==scheme]
        output["by_scheme"][scheme]={}
        output["first_15_days"][scheme]={}
        output["first_23_days"][scheme]={}
        for m in MONTHS:
            full=summarize(part,m+"-01",min(m+"-31",TODAY))
            output["by_scheme"][scheme][m]=compact(full)
            if scheme=="ALL":output["months"][m]=compact(full)
            for n,key in ((15,"first_15_days"),(23,"first_23_days")):
                output[key][scheme][m]=compact(summarize(part,m+"-01",min(m+f"-{n:02}",TODAY)))
    for label,a,b,n in (("oct_vs_sep","2026-09","2026-10",31),
                        ("nov_1_15_vs_oct_1_15","2026-10","2026-11",15)):
        x=summarize(rows,a+"-01",min(a+f"-{n:02}",TODAY))["sku_stats"]
        y=summarize(rows,b+"-01",min(b+f"-{n:02}",TODAY))["sku_stats"]
        d=[{"sku":sku,"before_delivered":x.get(sku,{}).get("delivered",0),
             "after_delivered":y.get(sku,{}).get("delivered",0),
             "before_ordered":x.get(sku,{}).get("ordered",0),
             "after_ordered":y.get(sku,{}).get("ordered",0),
             "delta_delivered":y.get(sku,{}).get("delivered",0)-x.get(sku,{}).get("delivered",0)}
            for sku in x.keys()|y.keys()]
        output["sku_deltas"][label]={
          "declines":sorted(d,key=lambda z:z["delta_delivered"])[:30],
          "growth":sorted(d,key=lambda z:-z["delta_delivered"])[:20]}
    output["caution"]="November is incomplete; recent orders can still be in transit. No actual delivery timestamps are present in posting list."
    return output

def analytic_deliveries():
    attempts=[]
    methods=[
      ("/v1/analytics/dashboard/sales",
       {"date_from":"2026-09-01","date_to":TODAY,"metrics":["delivered_units","ordered_units","revenue"],"grouping":"day"}),
      ("/v1/analytics/data",
       {"date_from":"2026-09-01","date_to":TODAY,"metrics":["delivered_units"],"dimension":["day"],
        "filters":[],"sort":[],"limit":200,"offset":0})
    ]
    for path,body in methods:
        try:
            ans=oz._post(path,body,retries=1)
            res=ans.get("result") or {}
            entries=res.get("data") or []
            if not isinstance(entries,list):raise ValueError("Data must be an array")
            valid=[]
            for x in entries:
                if not isinstance(x,dict):continue
                if path.endswith("/data"):
                    dim=(x.get("dimensions") or [{}])[0]
                    day=str(dim.get("id") or dim.get("name") or "")
                    raw=x.get("metrics") or []
                    value=raw[0] if raw else None
                else:
                    day=str(x.get("date") or x.get("day") or "")
                    value=x.get("delivered_units")
                if value is not None and len(day)>=10:
                    valid.append({"date":day[:10],"delivered_units":value})
            if valid:
                return {"status":"ok","source":path,"definition":"delivered_units by actual analytics date",
                        "monthly":{m:sum(float(v["delivered_units"]) for v in valid if v["date"].startswith(m)) for m in MONTHS},
                        "first_23_days":{m:sum(float(v["delivered_units"]) for v in valid if m+"-01"<=v["date"]<=m+"-23") for m in MONTHS},
                        "daily":valid,"attempts":attempts,
                        "warning":"Ozon analytical timezone may not equal Moscow; verify with seller cabinet for financial reconciliation."}
            attempts.append({"endpoint":path,"status":"no metric rows","rows":len(entries),
                             "returned_keys":list(res)[:10]})
        except Exception as ex:
            attempts.append({"endpoint":path,"error_type":type(ex).__name__,"error":str(ex)[:210]})
    return {"status":"unavailable","attempts":attempts,
            "warning":"Exact deliveries grouped by actual delivery date not accessible. Do not confuse order-month delivered-status cohorts with calendar-month deliveries."}

def main():
    save()
    for name,fn in (("analytics_delivered",analytic_deliveries),("posting_cohorts",cohorts)):
        try:
            data=fn()
            R["sections"][name]={"status":"ok","data":data}
        except Exception as e:
            R["sections"][name]={"status":"error","error_type":type(e).__name__,"message":str(e)[:220]}
            R["errors"].append(name)
        save()
        print("LEOPARTS_DELIVERED_3M",name,R["sections"][name]["status"],flush=True)
    R["status"]="complete" if not R["errors"] else "partial"
    R["finished_utc"]=datetime.now(timezone.utc).isoformat()
    save()
    print("LEOPARTS_DELIVERED_3M_FINAL",R["status"],R["errors"],flush=True)
    return 1 if R["errors"] else 0

if __name__=="__main__":
    sys.exit(main())
