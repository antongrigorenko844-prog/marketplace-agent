"""Read-only verification of displayed/quoted LEOPARTS kit prices across Ozon WB YM Tilda."""
from pathlib import Path
from datetime import datetime,timezone
import os,sys,json,time
from collections import defaultdict
import requests
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import site_links
S=(ROOT/"data/related_price_audit_20261122/audit.json")
A=json.loads(S.read_text(encoding="utf-8"))
OUT=ROOT/"data/related_price_audit_20261122/live_verify.json"
# Exact BOM counterpart list plus previously changed connected products.
FOCUS=["0am325091e-set","0am325091f-set","0am325025u","0am325025z",
       "klipsy-shtokov-2sht","0am325025P",
       "DQ500","Dq500","filtr-dsg7-dq500",
       "plita-filtr-pylniki-sal-orig","0am325066ae","0AM325433E","122390AKAF","0сц",
       "0am325025","0am325025H","0am325025x",
       "02E 305 045","02e305045","0AM325219C","0am325025ae",
       "filtr-setka-0b5-orig","0AM325120A"]
result={"started_at_utc":datetime.now(timezone.utc).isoformat(),"read_only":True,"price_changes":0,
        "focus_skus":FOCUS,"sources":{},"errors":[]}
def save():
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def f(x):
    try:return round(float(x),2)
    except (TypeError,ValueError):return None
def verify_ozon():
    import ozon_client
    raw=ozon_client.get_prices_and_stocks(FOCUS)
    rows={}
    for row in raw:
        k=row.get("offer_id")
        if k not in FOCUS or k in rows:continue
        p=row.get("price") or {}
        rows[k]={"price":f(p.get("price")),"seller_promotion":f(p.get("marketing_seller_price")),
                 "original_price":f(p.get("old_price")),"min_price":f(p.get("min_price")),
                 "currency":p.get("currency_code")}
    return {"source":"/v5/product/info/prices","rows":rows}
def verify_wb():
    import wb_client
    raw=wb_client.get_prices(limit=1000)
    rows={}
    for r in raw:
        k=r.get("vendorCode")
        if k not in FOCUS or k in rows:continue
        values=[(f(sz.get("price")),f(sz.get("discountedPrice"))) for sz in r.get("sizes") or []]
        rows[k]={"size_prices":values[:5],"discount":r.get("discount"),
                 "currency":r.get("currencyIsoCode4217"),"nm_id":r.get("nmID")}
    return {"source":"/api/v2/list/goods/filter","rows":rows}
def verify_yandex():
    import yandex_market_client as ym
    _, campaign = ym._creds()
    status,body=ym._request("POST",f"/v2/campaigns/{campaign}/offers",
                    json_body={"offerIds":FOCUS})
    if status!=200 or body.get("status") not in (None,"OK") or "result" not in body:
        raise RuntimeError(f"Market offer listing status HTTP {status}")
    arr=(body["result"] or {}).get("offers") or []
    rows={}
    for r in arr:
        k=r.get("offerId")
        if k not in FOCUS or k in rows:continue
        bp=r.get("basicPrice") or {}; cp=r.get("campaignPrice") or {}
        rows[k]={"basic":f(bp.get("value")),"campaign":f(cp.get("value")),
                 "status":r.get("status"),"unverified":r.get("unverified"),
                 "currency":bp.get("currencyId")}
    return {"source":"/v2/campaigns/{campaign}/offers","rows":rows}
def verify_tilda():
    ua={"User-Agent":"Mozilla/5.0 (compatible; LEOPARTS-price-review/1.0)"}
    ids=set();parts=set()
    for path in ("/catalog","/"):
        r=requests.get("https://leoparts-akpp.ru"+path,headers=ua,timeout=30)
        if r.ok:
            pt,rec=site_links._store_parts(r.text);parts|=pt;ids|=rec
    if not parts: raise RuntimeError("No catalog store part on public website")
    found=[]
    for part in sorted(parts):
        slice_no=1
        for _ in range(8):
            r=requests.get("https://store.tildaapi.com/api/getproductslist/",
                headers=ua,timeout=45,
                params={"storepartuid":part,"recid":next(iter(sorted(ids)),""),"c":str(int(time.time()*1000)),
                        "getparts":"true","getoptions":"true","slice":slice_no,"size":100})
            r.raise_for_status()
            body=r.json()
            found.extend(body.get("products") or [])
            nxt=body.get("nextslice")
            if not nxt or not body.get("products"):break
            if int(nxt)<=slice_no:raise ValueError("Looped Tilda product pages")
            slice_no=int(nxt)
    exact=defaultdict(list)
    for p in found:
        key=p.get("sku")
        if key in FOCUS:exact[key].append(p)
    rows={}
    for sku in FOCUS:
        matching=exact.get(sku,[])
        if len(matching)==1:
            p=matching[0]
            rows[sku]={"price":f(p.get("price")),"priceold":f(p.get("priceold")),
                       "product_uid":p.get("uid"),"title":str(p.get("title") or "")[:120]}
        elif matching:rows[sku]={"ambiguous_products":len(matching)}
    return {"source":"public Tilda catalog API", "catalog_products_total":len(found),
            "store_parts":len(parts),"rows":rows}
for name,fn in (("ozon",verify_ozon),("wb",verify_wb),("yandex",verify_yandex),("site",verify_tilda)):
    try:
        result["sources"][name]={"status":"ok",**fn()}
        print(name,"ok",len(result["sources"][name]["rows"]),flush=True)
    except Exception as e:
        result["errors"].append(name)
        result["sources"][name]={"status":"error","error_type":type(e).__name__,
                "note":"Read-only API source unavailable; do not infer live price"}
        print(name,"error",type(e).__name__,flush=True)
    save()
result["finished_at_utc"]=datetime.now(timezone.utc).isoformat()
result["status"]="complete" if not result["errors"] else "partial"
save()
print("LIVE_PRICE_DONE",result["status"],result["errors"],flush=True)
