"""Scoped repair: publish previously approved seven DQ200 rod/sleeve prices
on WB and Yandex Market ONLY; Ozon, site, Avito and all other SKUs untouched.
"""
from __future__ import annotations
import json
import sys
import time
from pathlib import Path
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
REPORT=ROOT/"data/approved_rods_market_repair_20261122_result.json"
MANIFEST=ROOT/"data/approved_rods_filters_20261122.json"
SKU_PRICE={
  "0am325091e":1990,"0am325091f":1990,"0am325025D":1490,
  "0am325025u":2690,"0am325025z":2690,
  "0am325091e-set":4990,"0am325091f-set":4990,
}
OLD={
  "0am325091e":3850,"0am325091f":3350,"0am325025D":3350,
  "0am325025u":3490,"0am325025z":3490,
  "0am325091e-set":8850,"0am325091f-set":8850,
}
def ensure():
    req=json.loads(MANIFEST.read_text())
    if req.get("marketplace_prices")!=SKU_PRICE:raise ValueError("Owner approval mismatch")
def num(v):
    try: return float(str(v))
    except (ValueError,TypeError):return None
def close(a,b,tol=1.5):return a is not None and abs(float(a)-float(b))<=tol
def emit(ch,value):
    try:obj=json.loads(REPORT.read_text())
    except (FileNotFoundError,ValueError):obj={}
    obj[ch]=value
    obj["at"]=datetime.now(timezone.utc).isoformat()
    REPORT.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"channel":ch,"result":value},ensure_ascii=False,default=str),flush=True)
def wb():
    import wb_client
    all_rows=wb_client.get_prices(limit=1000)
    if not isinstance(all_rows,list):raise ValueError("WB row result is not a list")
    found={}
    for r in all_rows:
        if not isinstance(r,dict):continue
        sku=r.get("vendorCode")
        if sku in SKU_PRICE:
            if sku in found:raise ValueError("WB duplicate exact sku "+sku)
            found[sku]=r
    report={};batch=[]
    for sku,target in SKU_PRICE.items():
        row=found.get(sku)
        if not row:
            report[sku]={"status":"not_found"};continue
        sizes=row.get("sizes") or []
        if len(sizes)!=1 or row.get("editableSizePrice"):
            report[sku]={"status":"unexpected_sizes"};continue
        disc=int(row.get("discount") or 0)
        if not 0<=disc<100:
            report[sku]={"status":"bad_discount"};continue
        s=sizes[0]
        current=num(s.get("discountedPrice"))
        allowed=[OLD[sku],target]
        if sku=="0am325025u":allowed.append(5349.82)
        if not any(close(current,x) for x in allowed):
            report[sku]={"status":"unapproved_live_price","observed":current};continue
        if close(current,target):
            report[sku]={"status":"already_at_target","price":current,"nmID":row.get("nmID")};continue
        factor=Decimal(100-disc)/Decimal(100)
        base=int((Decimal(target)/factor).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        batch.append({"nmID":row["nmID"],"price":base,"discount":disc})
        report[sku]={"status":"ready","price_before":current,"nmID":row.get("nmID"),"submitted_base":base,"discount":disc}
    if batch:
        ack=wb_client.update_prices(batch)
        report["upload"]={"acknowledged":bool(isinstance(ack,dict) and (ack.get("data") or {}).get("id"))}
    time.sleep(12)
    again={r.get("vendorCode"):r for r in wb_client.get_prices(limit=1000) if isinstance(r,dict) and r.get("vendorCode") in SKU_PRICE}
    for sku in SKU_PRICE:
        sizes=(again.get(sku) or {}).get("sizes") or []
        actual=num(sizes[0].get("discountedPrice")) if len(sizes)==1 else None
        report[sku].update({"actual":actual,"verified":close(actual,SKU_PRICE[sku])})
    return report

def require_ok(status,response,name):
    if status!=200 or not isinstance(response,dict) or response.get("status","OK")!="OK":
        raise RuntimeError(name+" HTTP "+str(status))
    return response
def ym():
    import yandex_market_client as ym
    _,camp=ym._creds()
    biz=ym.get_business_id()
    response=require_ok(*ym._request("POST",f"/v2/businesses/{biz}/settings",json_body={}),"settings")
    settings=(response.get("result") or {}).get("settings") or {}
    if settings.get("onlyDefaultPrice") is not True:
        return {"status":"business_has_non_default_prices","onlyDefaultPrice":settings.get("onlyDefaultPrice")}
    shops=require_ok(*ym._request("GET","/v2/campaigns"),"campaign list").get("campaigns",[]) or []
    other=[int(q["id"]) for q in shops if int((q.get("business") or {}).get("id") or 0)==int(biz) and str(q.get("id"))!=str(camp)]
    for c in other:
        st=require_ok(*ym._request("POST",f"/v2/campaigns/{c}/offers",json_body={"offerIds":list(SKU_PRICE)}),"other shop")
        active={r.get("offerId") for r in (st.get("result") or {}).get("offers",[]) if r.get("status") not in ("ARCHIVED","DISABLED_BY_PARTNER")}
        if active: return {"status":"additional_active_store","store_id":c,"blocked_skus":sorted(active)}
    mapped={v["sku"]:v for v in ym.get_all_offers(archived=False) if v.get("sku") in SKU_PRICE}
    resp=require_ok(*ym._request("POST",f"/v2/campaigns/{camp}/offers",json_body={"offerIds":list(SKU_PRICE)}),"campaign offers")
    in_shop={r.get("offerId"):r for r in (resp.get("result") or {}).get("offers",[]) if r.get("offerId") in SKU_PRICE}
    resp=require_ok(*ym._request("POST",f"/v2/businesses/{biz}/offer-prices",json_body={"offerIds":list(SKU_PRICE)}),"business prices")
    in_business={r.get("offerId"):r for r in (resp.get("result") or {}).get("offers",[]) if r.get("offerId") in SKU_PRICE}
    report={};edits=[]
    for sku,target in SKU_PRICE.items():
        q=in_shop.get(sku);b=in_business.get(sku)
        if not q or not b or sku not in mapped or mapped[sku].get("archived") or q.get("unverified"):
            report[sku]={"status":"unverified_store_or_sku"};continue
        if str(mapped[sku].get("market_sku") or "")=="":
            report[sku]={"status":"no_market_mapping"};continue
        shopprice=q.get("basicPrice") or {};officeprice=b.get("price") or {}
        campaignprice=(q.get("campaignPrice") or {}).get("value")
        base=num(shopprice.get("value"));office=num(officeprice.get("value"))
        if campaignprice is not None or base is None or office is None or not close(base,office,0.01):
            report[sku]={"status":"price_override_or_inconsistent","basic":base,"office":office};continue
        if shopprice.get("currencyId") not in ("RUR","RUB") or officeprice.get("currencyId") not in ("RUR","RUB"):
            report[sku]={"status":"currency_unverified"};continue
        if not any(close(base,x,.01) for x in (target,OLD[sku])):
            report[sku]={"status":"unapproved_live_price","basic":base};continue
        report[sku]={"status":"already_at_target" if close(base,target,.01) else "ready","before":base}
        if close(base,target,.01):continue
        price={"value":target,"currencyId":"RUR"}
        for keep in ["discountBase","minimumForBestseller","excludedFromBestsellers"]:
            if officeprice.get(keep) is not None:price[keep]=officeprice[keep]
        edits.append({"offerId":sku,"price":price})
    if edits:
        result=require_ok(*ym._request("POST",f"/v2/businesses/{biz}/offer-prices/updates",json_body={"offers":edits}),"business price update")
        report["upload"]={"acknowledged":result.get("status")=="OK","submitted":len(edits)}
    time.sleep(7)
    rs=require_ok(*ym._request("POST",f"/v2/campaigns/{camp}/offers",json_body={"offerIds":list(SKU_PRICE)}),"campaign verify")
    rb=require_ok(*ym._request("POST",f"/v2/businesses/{biz}/offer-prices",json_body={"offerIds":list(SKU_PRICE)}),"office verify")
    sr={r.get("offerId"):r for r in (rs.get("result") or {}).get("offers",[])}
    br={r.get("offerId"):r for r in (rb.get("result") or {}).get("offers",[])}
    for sku in SKU_PRICE:
        sh=(sr.get(sku) or {}).get("basicPrice") or {}
        off=(br.get(sku) or {}).get("price") or {}
        actual=num(sh.get("value"));office=num(off.get("value"))
        report[sku].update({"basic":actual,"business":office,"verified":close(actual,SKU_PRICE[sku],.01) and close(office,SKU_PRICE[sku],.01)})
    return report
if __name__=="__main__":
    ensure()
    for name,fn in [("wildberries",wb),("yandex_market",ym)]:
        try:r=fn()
        except Exception as ex:
            import traceback
            f=traceback.extract_tb(ex.__traceback__)[-1]
            r={"status":"failed_or_unverified","error_type":type(ex).__name__,"file":str(f.filename).split("/")[-1],"line":f.lineno}
        emit(name,r)
