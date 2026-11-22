"""LEOPARTS Ozon 2026-11-22 seller audit: read-only, aggregated; no buyer details.
Compare 2x14 complete days and 2x7 days by posting creation date, FBS vs FBO;
audit post-sale fees and advertising-related debits by day; inspect active sale actions
and quantities. No Ozon changes. Designed for GitHub Actions with existing tokens.
"""
from collections import Counter, defaultdict
from datetime import datetime, date, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import json, sys, math, os
import ozon_client
import price_audit

OUT=Path("data/ozon_recovery_20261122/live_diagnosis.json")
MSK=ZoneInfo("Europe/Moscow")
START="2026-10-25"
END="2026-11-21"
PREV14=("2026-10-25","2026-11-07")
RECENT14=("2026-11-08","2026-11-21")
PREV7=("2026-11-08","2026-11-14")
RECENT7=("2026-11-15","2026-11-21")
PAID=("Звёздные товары","Ускоренный сбор отзывов","Сбор первых отзывов",
      "Подписка Управление отзывами","Продвижение бренда","Гибкий график выплат",
      "Превышение индекса ошибок","Отгрузка в нерекомендованный слот")
NOW=datetime.now(timezone.utc)
doc={"status":"started","mode":"read_only","generated_utc":NOW.isoformat(),
     "time_zone":"Europe/Moscow","period":[START,END],
     "week_comparisons":{"prior_14":list(PREV14),"recent_14":list(RECENT14),
                         "prior_7":list(PREV7),"recent_7":list(RECENT7)},
     "external_mutations":0,"errors":[],"sections":{}}
def save():
    OUT.parent.mkdir(parents=True,exist_ok=True)
    tmp=OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    tmp.replace(OUT)
def nr(v):
    try:return float(v)
    except (ValueError,TypeError):return 0.
def parse_dt(s):
    if not isinstance(s,str) or not s:return None
    try:
        obj=datetime.fromisoformat(s.replace("Z","+00:00"))
        if obj.tzinfo is None: obj=obj.replace(tzinfo=timezone.utc)
        return obj
    except (ValueError,TypeError):return None
def created(p):
    for key in ("in_process_at","created_at","order_date","date"):
        d=parse_dt(p.get(key))
        if d:return d.astimezone(MSK).date().isoformat(),key
    return None,None
def summarize(postings,kind):
    days=defaultdict(lambda:{"orders":0,"units":0,"statuses":Counter(),"cancelled":0,
                              "delivered":0,"in_transit":0,"pending_seller":0})
    sku=defaultdict(lambda:defaultdict(int))
    idx=[];seen=set()
    example_fields=[]
    for p in postings:
        if not isinstance(p,dict):continue
        if not example_fields:example_fields=sorted(p.keys())
        num=str(p.get("posting_number") or "")
        if num and num in seen:continue
        if num:seen.add(num)
        day,source=created(p)
        if day is None:continue
        st=str(p.get("status") or "unknown").lower()
        qty=sum(max(0,int(pr.get("quantity") or 0)) for pr in (p.get("products") or []) if isinstance(pr,dict))
        days[day]["orders"]+=1;days[day]["units"]+=qty
        days[day]["statuses"][st]+=1
        if st in ("cancelled","canceled"):days[day]["cancelled"]+=1
        if st in ("delivered",):days[day]["delivered"]+=1
        if st in ("delivering","awaiting_deliver","awaiting_approve","arbitration"):days[day]["in_transit"]+=1
        if st in ("awaiting_packaging","awaiting_deliver","awaiting_registration"):days[day]["pending_seller"]+=1
        for item in p.get("products") or []:
            sku[str(item.get("offer_id") or "?")][day]+=max(0,int(item.get("quantity") or 0))
        deadline=parse_dt(p.get("shipment_date_without_delay"))
        if st in ("awaiting_packaging","awaiting_deliver") and deadline:
            idx.append({"deadline_msk":deadline.astimezone(MSK).isoformat(),
                        "status":st,"overdue":deadline<NOW,
                        "sku":[str(pr.get("offer_id") or "") for pr in (p.get("products") or [])[:3]]})
    for v in days.values():v["statuses"]=dict(v["statuses"])
    def period(a,b):
        rows=[v for d,v in days.items() if a<=d<=b]
        status=Counter()
        for row in rows:status.update(row["statuses"])
        return {"orders":sum(row["orders"] for row in rows),
                "units":sum(row["units"] for row in rows),
                "cancelled":sum(row["cancelled"] for row in rows),
                "delivered_current_status":sum(row["delivered"] for row in rows),
                "in_transit_current_status":sum(row["in_transit"] for row in rows),
                "pending_seller_current_status":sum(row["pending_seller"] for row in rows),
                "statuses":dict(status)}
    comparison={name:period(*win) for name,win in [
        ("prior_14",PREV14),("recent_14",RECENT14),("prior_7",PREV7),("recent_7",RECENT7)]}
    sku_compare=[]
    for offer,dd in sku.items():
        before=sum(qty for day,qty in dd.items() if PREV14[0]<=day<=PREV14[1])
        after=sum(qty for day,qty in dd.items() if RECENT14[0]<=day<=RECENT14[1])
        if before or after:sku_compare.append({"offer_id":offer,"prior_14_units":before,
                                                "recent_14_units":after,"delta_units":after-before})
    sku_compare.sort(key=lambda z:(z["delta_units"],-z["prior_14_units"]))
    return {"scheme":kind,"total_returned":len(postings),
            "posting_field_names":example_fields,
            "by_day":{k:days[k] for k in sorted(days)},
            "periods":comparison,"top_declining_skus":sku_compare[:30],
            "top_rising_skus":list(reversed(sku_compare[-20:])),
            "current_pending_with_known_deadline":len(idx),
            "overdue_pending_with_known_deadline":sum(r["overdue"] for r in idx),
            "pending_deadlines":sorted(idx,key=lambda x:x["deadline_msk"])[:12],
            "date_definition":"Order creation/in_process_at in Moscow; delivered_current_status is NOT delivery date"}
def financial():
    codes=price_audit._accrual_types(ozon_client)
    def name(f):return str(codes.get(str(f.get("type_id"))) or ("неизвестный тип "+str(f.get("type_id"))))
    by_day=defaultdict(lambda:defaultdict(float))
    debits=defaultdict(float)
    credits=defaultdict(float)
    count=defaultdict(int)
    by_sku=defaultdict(lambda:defaultdict(float))
    missing_id=Counter()
    day=date.fromisoformat(START)
    limit=date.fromisoformat(END)
    from datetime import timedelta
    while day<=limit:
        date_str=day.isoformat()
        data=price_audit._accruals_by_day(ozon_client,date_str)
        count[date_str]=len(data)
        for op in data:
            if not isinstance(op,dict):continue
            total=price_audit._money(op.get("total_amount",op.get("amount")))
            # Ozon non-item service = full operation total for the fee.
            fee=op.get("non_item_fee")
            if isinstance(fee,dict):
                nm=name(fee);by_day[date_str][nm]+=total
                if total<0:debits[nm]+=-total
                else:credits[nm]+=total
                continue
            # Item-level service fees by SKU; not the combined POSTING total.
            fees=(op.get("item_fees") or {}).get("fees") or []
            for item in fees:
                sku=str(item.get("sku") or "")
                for f in item.get("fees") or []:
                    nm=name(f)
                    amt=price_audit._money(f.get("accrued",f.get("amount")))
                    by_day[date_str][nm]+=amt
                    if amt<0:debits[nm]+=-amt
                    else:credits[nm]+=amt
                    if sku and sku!="0":by_sku[nm][sku]+=amt
        doc["sections"]["finance_progress"]={"days_checked":len(count),"through":date_str,"status":"in_progress"}
        if len(count)%7==0:save()
        day+=timedelta(days=1)
    date_fee={date_str:{n:round(v,2) for n,v in dayfees.items()} for date_str,dayfees in sorted(by_day.items())}
    allnames=set(debits)|set(credits)
    fees={n:{"debits_rub":round(debits[n],2),"credits_rub":round(credits[n],2),
             "net_rub":round(credits[n]-debits[n],2)}
          for n in sorted(allnames,key=lambda n:-debits[n])}
    def totals(w):
        return {n:round(sum(by_day.get(day,{}).get(n,0) for day in by_day if w[0]<=day<=w[1]),2)
                for n in allnames if sum(abs(by_day.get(day,{}).get(n,0)) for day in by_day if w[0]<=day<=w[1])>0}
    top_charges={}
    for n in PAID:
        if n in by_sku:
            top_charges[n]=sorted([{"sku":k,"net_rub":round(v,2)} for k,v in by_sku[n].items()],
                                  key=lambda x:x["net_rub"])[:20]
    return {"method":"/v1/finance/accrual/by-day","days_checked":len(count),
        "accruals_by_day":dict(count),"fees_by_day":date_fee,"fees":fees,
        "paid_costs":{n:fees[n] for n in PAID if n in fees},
        "periods":{label:totals(rng) for label,rng in (
            ("prior_14",PREV14),("recent_14",RECENT14),
            ("prior_7",PREV7),("recent_7",RECENT7))},
        "top_skus_for_paid_programs":top_charges,
        "note":"Finance accrual date differs from order date, and services may post after sale"}
def analytics():
    body={"date_from":START,"date_to":END,"metrics":["ordered_units","revenue"],
          "dimension":["day"],"filters":[],"sort":[],"limit":1000,"offset":0}
    r=ozon_client._post("/v1/analytics/data",body,retries=2)
    a=(r.get("result") or {}).get("data") or []
    if not isinstance(a,list) or len(a)>200:raise ValueError("Unexpected analytics rows")
    return {"metric_order":["ordered_units","revenue"],"rows":[{"day":str((x.get("dimensions") or [{}])[0].get("id") or
           (x.get("dimensions") or [{}])[0].get("name") or ""),
           "ordered_units":(x.get("metrics") or [None,None])[0],
           "revenue":(x.get("metrics") or [None,None])[1]} for x in a]}
def promotions():
    entries=ozon_client.get_actions()
    return {"source":"/v1/actions","count":len(entries),
       "participating":[{"id":x.get("id"),"title":str(x.get("title") or "")[:150],
         "action_type":x.get("action_type"),"is_participating":x.get("is_participating"),
         "participating_products_count":x.get("participating_products_count"),
         "date_start":x.get("date_start"),"date_end":x.get("date_end")}
            for x in entries if x.get("is_participating") or int(x.get("participating_products_count") or 0)>0],
       "note":"This API lists sales promotions; absence does not prove paid subscriptions disabled"}
def inventory():
    prior=json.loads(Path("data/ozon_finance.json").read_text(encoding="utf-8"))
    top=[sku for sku,v in sorted((prior.get("items") or {}).items(),
          key=lambda x:-nr(x[1].get("revenue"))) if not sku.startswith("sku:")][:40]
    rows=ozon_client.get_stocks(top)
    stock=[]
    for x in rows:
        offered=str(x.get("offer_id") or "")
        vals=[{"type":v.get("type"),"present":v.get("present"),
               "reserved":v.get("reserved")} for v in (x.get("stocks") or []) if isinstance(v,dict)]
        stock.append({"offer_id":offered,"stock_types":vals})
    return {"focus_skus":len(top),"returned_skus":len(rows),"data":stock,
            "note":"Ozon stock snapshot; compare against master only after confirming type semantics"}

def run():
    save()
    since="2026-10-24T21:00:00Z"
    to="2026-11-21T21:00:00Z"
    for title,fn in [
        ("orders_fbs",lambda:summarize(ozon_client.get_fbs_orders_since(since,to),"FBS")),
        ("orders_fbo",lambda:summarize(ozon_client.get_fbo_orders_since(since,to),"FBO")),
        ("finance",financial),("analytics_orders",analytics),
        ("sale_promotions",promotions),("stocks_top_sellers",inventory)]:
        try:
            doc["sections"][title]={"status":"ok","data":fn()}
        except Exception as exc:
            doc["sections"][title]={"status":"error","error_type":type(exc).__name__,
                 "message":str(exc)[:240]}
            doc["errors"].append(title)
        save()
        print(title,doc["sections"][title]["status"],flush=True)
    doc["status"]="complete" if not doc["errors"] else "partial"
    doc["finished_utc"]=datetime.now(timezone.utc).isoformat()
    save()
    print("OZON_AUDIT",doc["status"],doc["errors"],flush=True)
if __name__=="__main__":run()
