"""Ozon FBS/FBO: read-only cancellation causes and aged-delivery cohorts.
Only aggregate allowed operational fields; never persist buyer, address, order IDs.
"""
import json,os,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone,date
from zoneinfo import ZoneInfo
from pathlib import Path
import ozon_client
OUT=Path("data/ozon_recovery_20261122/delivery_cancellation_diagnosis.json")
MSK=ZoneInfo("Europe/Moscow")
now=datetime.now(timezone.utc)
CUTOFF="2026-11-21"
def time(s):
    if not isinstance(s,str):return None
    try:
        v=datetime.fromisoformat(s.replace("Z","+00:00"))
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    except (ValueError,TypeError):return None
def order_day(p):
    for k in ("in_process_at","created_at"):
        t=time(p.get(k))
        if t:return t.astimezone(MSK).date().isoformat()
    return None
def clean_label(x):
    return str(x or "не указано").replace("\n"," ").strip()[:100]
def bucket(day):
    if "2026-10-25"<=day<="2026-11-07":return "prior_14"
    if "2026-11-08"<=day<="2026-11-21":return "recent_14"
    return None
def run_scheme(posts,scheme):
    reasons=defaultdict(Counter);substates=defaultdict(Counter)
    cancel_sku=defaultdict(Counter)
    in_transit_aged=defaultdict(Counter)
    in_transit_center=defaultdict(Counter)
    in_transit_age=defaultdict(Counter)
    trans_sku=defaultdict(Counter)
    created=Counter();delivered=Counter();cancelled=Counter()
    created_keys=set()
    for x in posts:
        if not isinstance(x,dict):continue
        day=order_day(x)
        b=bucket(day or "")
        if not b:continue
        created[b]+=1
        st=clean_label(x.get("status")).lower()
        skus=[clean_label(p.get("offer_id")) for p in x.get("products") or [] if isinstance(p,dict)]
        if st=="cancelled":
            cancelled[b]+=1
            r=x.get("cancellation") or {}
            if not isinstance(r,dict):r={}
            # Ozon cancellation reason. No buyer identities or free-text comments.
            for k in r:created_keys.add(k)
            reason=clean_label(r.get("cancel_reason") or r.get("cancel_reason_name") or r.get("cancel_reason_id"))
            initiator=clean_label(r.get("cancellation_initiator") or r.get("initiator") or r.get("cancel_type"))
            reasons[b][reason+" | "+initiator]+=1
            substates[b][clean_label(x.get("substatus"))]+=1
            for sku in skus:cancel_sku[b][sku]+=1
        if st=="delivered":delivered[b]+=1
        if st=="delivering":
            entered=time(x.get("received_at_sorting_center"))
            ship_date=time(x.get("shipment_date_without_delay"))
            age=(now.date()-date.fromisoformat(day)).days
            agebucket=("8+days" if age>=8 else "0-7days")
            in_transit_aged[b][agebucket]+=1
            in_transit_age[b][str(min(age,30))]+=1
            state=("center_timestamp_present" if entered else "center_timestamp_missing")
            in_transit_center[b][state]+=1
            if age>=8:
                for sku in skus:trans_sku[b][sku]+=1
    return {"scheme":scheme,"count_returned":len(posts),"created":dict(created),
            "delivered_current":dict(delivered),"cancelled_current":dict(cancelled),
            "cancellation_fields_available":sorted(created_keys),
            "cancellation_reason_counts":{k:dict(v) for k,v in reasons.items()},
            "cancellation_substatus_counts":{k:dict(v) for k,v in substates.items()},
            "cancellation_top_skus":{k:dict(v.most_common(15)) for k,v in cancel_sku.items()},
            "delivering_age_buckets":{k:dict(v) for k,v in in_transit_aged.items()},
            "delivering_by_age_days":{k:dict(v) for k,v in in_transit_age.items()},
            "delivering_center_timestamp":{k:dict(v) for k,v in in_transit_center.items()},
            "older_8_day_delivering_skus":{k:dict(v.most_common(20)) for k,v in trans_sku.items()},
            "caution":"These are statuses TODAY for cohorts of created orders; not actual delivery timestamps"}
def main():
    report={"status":"reading","mode":"read_only","started_utc":now.isoformat(),"period":["2026-10-25","2026-11-21"],"errors":[],"sections":{},"mutations":0}
    OUT.parent.mkdir(parents=True,exist_ok=True)
    for typ,fn in (("FBS",ozon_client.get_fbs_orders_since),("FBO",ozon_client.get_fbo_orders_since)):
        try:
            posts=fn("2026-10-24T21:00:00Z","2026-11-21T21:00:00Z")
            report["sections"][typ]={"status":"ok","data":run_scheme(posts,typ)}
        except Exception as e:
            report["sections"][typ]={"status":"error","error_type":type(e).__name__,"message":str(e)[:200]}
            report["errors"].append(typ)
    report["status"]="complete" if not report["errors"] else "partial"
    report["finished_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Ozon delivery causes",report["status"],report["errors"],flush=True)
if __name__=="__main__":main()
