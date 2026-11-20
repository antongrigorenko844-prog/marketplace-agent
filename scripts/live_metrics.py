"""Read LEOPARTS seller analytics, encrypt only a card-level statistical projection.

Run from the repository checkout. No creation of reports, no seller write methods.
"""
import argparse
import base64
import collections
import concurrent.futures
import contextlib
import datetime as dt
import json
import logging
import os
from pathlib import Path
import re
import sys
import tempfile
import threading
import time
from decimal import Decimal

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import requests

WINDOWS = (
    ("nov_1_19", "2026-11-01", "2026-11-19"),
    ("p3", "2026-10-16", "2026-11-12"),
    ("p2", "2026-09-18", "2026-10-15"),
    ("p1", "2026-08-21", "2026-09-17"),
)
START, END = "2026-11-01", "2026-11-19"
MSK = dt.timezone(dt.timedelta(hours=3))
UTC = dt.timezone.utc
OZ = "https://api-seller.ozon.ru"
WB = "https://statistics-api.wildberries.ru"
FUN = "https://seller-analytics-api.wildberries.ru"
COM = "https://feedbacks-api.wildberries.ru"
AV = "https://api.avito.ru"
YM = "https://api.partner.market.yandex.ru"
METHODS = {
    ("GET", WB + "/api/v1/supplier/orders"), ("GET", WB + "/api/v1/supplier/sales"),
    ("POST", FUN + "/api/analytics/v3/sales-funnel/products"),
    ("GET", COM + "/api/v1/feedbacks"), ("GET", COM + "/api/v1/questions"),
    ("POST", OZ + "/v1/analytics/data"), ("POST", OZ + "/v1/finance/accrual/by-day"),
    ("GET", AV + "/core/v1/accounts/self"), ("GET", AV + "/core/v1/items"),
}
STOP = None
LIMITS = {}
LOCK = threading.Lock()

class Gap(Exception):
    def __init__(self, reason, status=None, retry=None):
        self.reason, self.status, self.retry = reason, status, retry

def capture(fn):
    try:
        return {"status":"ok", "data":fn()}
    except Gap as err:
        return {"status":"gap","reason":err.reason,"http":err.status,"retry_after_seconds":err.retry}
    except Exception as err:
        # No third-party response bodies, tracebacks or order identifiers in the export/log.
        return {"status":"gap","reason":"local_"+type(err).__name__}

def call(method, url, *, headers=None, params=None, json=None):
    valid = ((method,url) in METHODS or method=="POST" and (
             re.fullmatch(re.escape(AV)+r"/stats/v1/accounts/[0-9]+/items",url) or
             re.fullmatch(re.escape(YM)+r"/v2/campaigns/[0-9]+/offers/stocks",url)))
    if not valid: raise Gap("route_not_allowed")
    # Enforce published cooldowns without blocking unrelated channels.
    with LOCK:
        due=LIMITS.get(url, 0)
    wait=max(0,due-time.monotonic())
    if STOP is not None and time.monotonic()+wait+15>STOP: raise Gap("time_budget")
    if wait: time.sleep(wait)
    try:
        r=requests.request(method,url,headers=headers,params=params,json=json,timeout=(5,20),allow_redirects=False)
    except requests.RequestException:
        raise Gap("network_error") from None
    if r.status_code==429:
        retry=r.headers.get("Retry-After") or r.headers.get("X-Ratelimit-Retry") or "60"
        retry=int(retry) if str(retry).isdigit() else 60
        with LOCK:LIMITS[url]=time.monotonic()+max(1,retry)
        raise Gap("rate_limited",429,retry)
    if r.status_code!=200:raise Gap("http_status",r.status_code)
    try: return r.json()
    except ValueError: raise Gap("invalid_json") from None

def arr(value):
    if not isinstance(value,list):raise Gap("unexpected_response")
    return value

def money(v):
    if isinstance(v,dict):
        if v.get("currency") not in (None,"RUB"):raise Gap("unexpected_currency")
        v=v.get("amount")
    return Decimal(str(v))

def amount(v):
    return float(v)

def daily_table(m,labels):
    return [dict(zip(labels,k),**{n:amount(v) if isinstance(v,Decimal) else v for n,v in s.items()})
            for k,s in sorted(m.items())]

def cooldown(url,seconds):
    with LOCK:LIMITS[url]=max(LIMITS.get(url,0),time.monotonic()+seconds)

def wb_transactions(kind,headers):
    endpoint=WB+"/api/v1/supplier/"+kind
    # Supplier statistics use lastChangeDate for the request, date for recognition.
    source=arr(call("GET",endpoint,headers=headers,params={"dateFrom":START+"T00:00:00","flag":0}))
    if len(source)>=80000:raise Gap("supplier_page_limit")
    by_id={}
    for row in source:
        key=row.get("saleID" if kind=="sales" else "srid")
        if not key:raise Gap("event_key_missing")
        tracker=by_id.get(key)
        if tracker is None or str(row.get("lastChangeDate",""))>=str(tracker.get("lastChangeDate","")):
            by_id[key]=row
    metrics=collections.defaultdict(collections.Counter)
    for key,row in by_id.items():
        day=str(row["date"])[:10]
        if not START<=day<=END:continue
        rec=metrics[(day,str(row["nmId"]),str(row.get("supplierArticle") or ""))]
        if kind=="sales":
            tag=str(key)[:1]
            if tag not in ("S","R"):rec["other_event_rows"]+=1;continue
            prefix="sale" if tag=="S" else "return"
            rec[prefix+"_units"]+=1
            for src,dest in (("priceWithDisc","seller"),("finishedPrice","customer"),("forPay","provisional_transfer")):
                if row.get(src) is not None:rec[prefix+"_"+dest+"_signed_rub"]+=money(row[src])
        else:
            rec["ordered_units"]+=1
            rec["cancelled_as_of_extraction"]+=int(bool(row["isCancel"]))
            if row.get("priceWithDisc") is not None:rec["ordered_seller_rub"]+=money(row["priceWithDisc"])
    return {"from":START,"to":END,"by":"date supplied by WB; seller Moscow calendar; last-change query from Nov 1; provisional","rows":daily_table(metrics,["date","nm_id","offer_id"])}

def wb_funnel(w):
    h,from_,to=w
    url=FUN+"/api/analytics/v3/sales-funnel/products"
    out=[]
    for offset in range(0,5000,100):
        data=call("POST",url,headers=h,json={"selectedPeriod":{"start":from_,"end":to},"skipDeletedNm":False,"limit":100,"offset":offset})
        cooldown(url,21)
        rows=arr(data["data"]["products"])
        for r in rows:
            product=r["product"]; selected=r["statistic"]["selected"]
            if not any(k in selected for k in ("openCount","cartCount","orderCount")):raise Gap("funnel_fields_missing")
            out.append({"nm_id":str(product["nmId"]),"offer_id":str(product.get("vendorCode") or ""),
                        **{k:selected[k] for k in ("openCount","cartCount","orderCount","orderSum","buyoutCount","buyoutSum","cancelCount","cancelSum","addToWishlistCount") if isinstance(selected.get(k),(int,float))},
                        **{k:product[k] for k in ("productRating","feedbackRating") if isinstance(product.get(k),(int,float))}})
        if len(rows)<100:
            if len({r["nm_id"] for r in out})!=len(out):raise Gap("duplicate_funnel_card")
            return out
    raise Gap("pagination_limit")

def windowed(fn):
    out={}; prior=None
    for name,a,b in WINDOWS:
        if prior in (401,402,403,404,429):
            out[name]={"status":"gap","reason":"endpoint_denied_or_limited","http":prior}
            continue
        out[name]=capture(lambda:fn(a,b));prior=out[name].get("http")
    return out

def wb_engagement(kind,h):
    start=int(dt.datetime.fromisoformat(START).replace(tzinfo=MSK).timestamp())
    finish=int((dt.datetime.fromisoformat(END).replace(tzinfo=MSK)+dt.timedelta(days=1)).timestamp())-1
    all_rows=collections.defaultdict(collections.Counter)
    for answered in ("true","false"):
        for skip in range(0,5000,500):
            data=call("GET",COM+"/api/v1/"+kind,headers=h,params={"isAnswered":answered,"take":500,"skip":skip,"order":"dateDesc","dateFrom":start,"dateTo":finish})
            if data.get("error"):raise Gap("api_flagged_error")
            rows=arr(data["data"][kind])
            for r in rows:
                when=dt.datetime.fromisoformat(r["createdDate"].replace("Z","+00:00"))
                day=when.astimezone(MSK).date().isoformat() if when.tzinfo else when.date().isoformat()
                if not START<=day<=END:continue
                nm=str((r.get("productDetails") or {}).get("nmId") or "unknown")
                t=all_rows[(day,nm)];t["answered" if answered=="true" else "unanswered"]+=1
                grade=r.get("productValuation")
                if kind=="feedbacks" and grade in (1,2,3,4,5):t["stars_"+str(grade)]+=1
            if len(rows)<500:break
        else:raise Gap("pagination_limit")
    return daily_table(all_rows,["date","nm_id"])

def wb():
    import wb_client
    h=wb_client._headers()
    return {"sales_nov":capture(lambda:wb_transactions("sales",h)), "orders_nov":capture(lambda:wb_transactions("orders",h)),
            "funnel":windowed(lambda a,b:wb_funnel((h,a,b))),
            "feedbacks_nov":capture(lambda:wb_engagement("feedbacks",h)),"questions_nov":capture(lambda:wb_engagement("questions",h))}

def oz_fin_day(when,h):
    last="";seen=set();skus=collections.defaultdict(collections.Counter); totals=collections.Counter()
    for _ in range(100):
        payload={"date":when}
        if last:payload["last_id"]=last
        d=call("POST",OZ+"/v1/finance/accrual/by-day",headers=h,json=payload)
        d=d.get("result",d); rows=arr(d["accruals"])
        for r in rows:
            category=r["accrued_category"]
            totals["ledger_net_rub"]+=money(r["total_amount"])
            if category!="POSTING":continue
            for item in r["posting"]["products"]:
                com=item.get("commission") or {}
                sale=com.get("sale_amount")
                if sale is None:continue
                rub=money(sale)
                if not rub:continue
                line=skus[(str(item["sku"]),)]
                prefix="delivered_accrual" if rub>0 else "reversal"
                line[prefix+"_units"]+=int(item["quantity"]); line[prefix+"_signed_rub"]+=rub
                if com.get("sale_commission") is not None:line["sale_commission_signed_rub"]+=money(com["sale_commission"])
        new=d.get("last_id")
        if not new:break
        if not rows or new in seen:raise Gap("accrual_cursor")
        seen.add(new);last=new
    else:raise Gap("pagination_limit")
    return {"date":when,"ledger_net_rub":amount(totals["ledger_net_rub"]),"sku_rows":daily_table(skus,["ozon_sku"])}

def oz_finance(h):
    # The archival daily ledger already covers Aug 21–Nov 12. Include Nov 1–12
    # again to compute the stand-alone Nov interval without blending snapshots.
    days=list(range(13,20))+list(range(1,13));out={}
    for n in days:
        d=f"2026-11-{n:02}";v=capture(lambda:oz_fin_day(d,h));out[d]=v
        if v.get("http") in (401,402,403,404,429):break
    return {"requested": [START,END],"days":out,"meaning":"ledger accruals, not orders or bank receipts; sold/reversed by sign of sale_amount"}

def oz_period(a,b,h,metrics):
    url=OZ+"/v1/analytics/data";out=[]
    for offset in range(0,10000,1000):
        payload={"date_from":a,"date_to":b,"dimension":["sku"],"metrics":metrics,"filters":[],"sort":[],"limit":1000,"offset":offset}
        try:r=call("POST",url,headers=h,json=payload)
        finally:cooldown(url,63)
        rows=arr(r["result"]["data"])
        for item in rows:
            vals=item["metrics"]
            if len(vals)!=len(metrics):raise Gap("metric_shape")
            out.append({"ozon_sku":str(item["dimensions"][0]["id"]),**dict(zip(metrics,vals))})
        if len(rows)<1000:return out
    raise Gap("pagination_limit")

def ozon():
    import ozon_client
    h=ozon_client._headers()
    finance=capture(lambda:oz_finance(h))
    base=["revenue","ordered_units"];traffic=["hits_view","hits_view_pdp","hits_tocart"]
    # Start with at least an up-to-date orders result even when premium is denied.
    orders=windowed(lambda a,b:oz_period(a,b,h,base))
    if orders["nov_1_19"].get("http") in (401,403,404):
        funnel={"status":"gap","reason":"analytics_unavailable"}
    else:funnel=windowed(lambda a,b:oz_period(a,b,h,traffic))
    return {"accruals":finance,"order_value_and_units":orders,"impressions_views_cart":funnel,"note":"ordered revenue is demand, not settled sales; sku is numeric Ozon SKU"}

def avito():
    import avito_client
    h=avito_client._headers()
    account=call("GET",AV+"/core/v1/accounts/self",headers=h)["id"]
    if not str(account).isdigit():raise Gap("missing_account_id")
    ids=set()
    for page in range(1,51):
        d=call("GET",AV+"/core/v1/items",headers=h,params={"page":page,"per_page":100})
        rr=arr(d.get("resources",d.get("items")))
        old=len(ids);ids.update(int(r["id"]) for r in rr)
        if len(rr)<100:break
        if len(ids)==old:raise Gap("items_pagination")
    else:raise Gap("pagination_limit")
    ids=sorted(ids)
    def stats(a,b):
        out=[]
        for i in range(0,len(ids),200):
            chunk=ids[i:i+200]
            resp=call("POST",AV+f"/stats/v1/accounts/{account}/items",headers=h,
                      json={"dateFrom":a,"dateTo":b,"fields":["uniqViews","uniqContacts","uniqFavorites"],"itemIds":chunk,"periodGrouping":"day"})
            rows=arr(resp["result"]["items"])
            for item in rows:
                key=int(item.get("itemId") or item.get("id"))
                if key not in chunk:raise Gap("unexpected_item")
                for r in arr(item["stats"]):
                    day=r["date"][:10]
                    if not a<=day<=b:raise Gap("out_of_window")
                    out.append({"item_id":key,"date":day,**{k:r[k] for k in ("uniqViews","uniqContacts","uniqFavorites") if isinstance(r.get(k),(int,float))}})
        return out
    return {"listed_item_count":len(ids),"listed_item_ids":ids,"item_day":windowed(stats),"universe":"listing IDs visible in current account listing pages, including returned inactive rows; unreturned historical listings unknown"}

def yandex_stock():
    import yandex_market_client as ym
    token,camp=ym._creds()
    out=[];cursor=None;seen=set()
    for _ in range(100):
        params={"limit":100}
        if cursor:params["pageToken"]=cursor
        d=call("POST",YM+f"/v2/campaigns/{int(camp)}/offers/stocks",headers=ym._headers(token),params=params,json={})
        if d.get("status")!="OK":raise Gap("api_flagged_error")
        result=d["result"]
        for wh in arr(result["warehouses"]):
            for e in arr(wh["offers"]):
                out.append({"sku":str(e["offerId"]),"warehouse_id":str(wh.get("warehouseId") or ""),
                            "stocks":{str(s["type"]):int(s["count"]) for s in arr(e["stocks"])}})
        cursor=(result.get("paging") or {}).get("nextPageToken")
        if not cursor:return out
        if cursor in seen:raise Gap("pagination_cursor")
        seen.add(cursor)
    raise Gap("pagination_limit")

def encrypt(result,pub,output):
    secret=AESGCM.generate_key(bit_length=256)
    nonce=os.urandom(12)
    wrapped=pub.encrypt(secret,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    payload=json.dumps(result,ensure_ascii=False,separators=(",",":"),allow_nan=False).encode()
    cipher=AESGCM(secret).encrypt(nonce,payload,None)
    envelope={k:base64.b64encode(x).decode("ascii") for k,x in (("key",wrapped),("nonce",nonce),("data",cipher))}
    output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w",dir=output.parent,delete=False) as f:json.dump(envelope,f);tmp=f.name
    os.replace(tmp,output)

def main():
    global STOP
    parser=argparse.ArgumentParser()
    parser.add_argument("--public-key",type=Path,required=True);parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    pub=serialization.load_pem_public_key(args.public_key.read_bytes())
    if not isinstance(pub,rsa.RSAPublicKey) or pub.key_size<2048:raise ValueError("Need RSA public key (2048 bits or above)")
    sys.path.insert(0,str(Path.cwd()))
    logging.disable(logging.CRITICAL);STOP=time.monotonic()+570
    result={"started_utc":dt.datetime.now(UTC).isoformat(),"periods":[{"key":k,"from":a,"to":b} for k,a,b in WINDOWS],"periods_overlap":True,"channels":{}}
    tasks={"wb":wb,"ozon":ozon,"avito":avito,"market_current_stocks":yandex_stock}
    with open(os.devnull,"w") as nul,contextlib.redirect_stdout(nul),contextlib.redirect_stderr(nul):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            futures={pool.submit(capture,fn):name for name,fn in tasks.items()}
            for f in concurrent.futures.as_completed(futures):result["channels"][futures[f]]=f.result()
    result["completed_utc"]=dt.datetime.now(UTC).isoformat()
    encrypt(result,pub,args.output)
    print("Read-only diagnostics finished. Encrypted output is ready.")
if __name__=="__main__":main()
