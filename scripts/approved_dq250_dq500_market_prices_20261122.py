"""LEOPARTS owner-approved DQ250/DQ500 prices to Ozon, WB and Yandex Market ONLY.
No changes to Site, Avito, costs, stocks, product metadata or other prices.
Usage: python scripts/approved_dq250_dq500_market_prices_20261122.py stage|publish
"""
from __future__ import annotations
import json
import os
import sys
import time
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
MANIFEST=ROOT/"data/approved_dq250_dq500_market_prices_20261122.json"
REPORT=ROOT/"data/approved_dq250_dq500_market_prices_20261122_result.json"
PRICE={
    "02e305045":1980, "02E305051C":1450, "02E 305 045":2880,
    "Dq500":1980, "filtr-dsg7-dq500":1450, "DQ500":2880,
}
OLD_MASTER={
    "02e305045":2350, "02E305051C":1850, "02E 305 045":2490,
    "Dq500":2350, "filtr-dsg7-dq500":1850, "DQ500":3850,
}
OLD_WB={
    "02e305045":[2350,3850], "02E305051C":[1850,1850.24],
    "02E 305 045":[2490,2490.03], "Dq500":[None],
    "filtr-dsg7-dq500":[1850,1850.16], "DQ500":[3850],
}
# Dq500 = smooth housing, DQ500 = smooth housing + filter; never casefold SKUs.
NAME_MARKERS={
    "02e305045":("корпус","DQ250"),
    "02E305051C":("фильтр","DQ250"),
    "02E 305 045":("корпус","фильтр","DQ250"),
    "Dq500":("корпус","DQ500"),
    "filtr-dsg7-dq500":("фильтр","DQ500"),
    "DQ500":("корпус","фильтр","DQ500"),
}
def now():return datetime.now(timezone.utc).isoformat()
def amount(v):
    try:return float(str(v).replace(" ","").replace(",","."))
    except (TypeError,ValueError):return None
def near(x,y,tol=1.0):
    return x is not None and y is not None and abs(float(x)-float(y))<=tol
def manifest():
    p=json.loads(MANIFEST.read_text(encoding="utf-8"))
    if p.get("channel_prices")!=PRICE or p.get("scope")!=["ozon","wildberries","yandex_market"] or p.get("preserve_site_avito") is not True:
        raise ValueError("No exact approved price manifest")
def status(name, data):
    try:r=json.loads(REPORT.read_text(encoding="utf-8"))
    except (FileNotFoundError,ValueError):r={}
    r[name]=data;r["last_checked_utc"]=now()
    REPORT.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"stage":name,"result":data},ensure_ascii=False,default=str),flush=True)

def rows_index(ws, keycol, required):
    d={}
    for n in range(2,ws.max_row+1):
        sku=ws.cell(n,keycol).value
        if isinstance(sku,str) and sku.strip() in PRICE:
            sku=sku.strip()
            if sku in d:raise ValueError("Duplicate exact SKU "+sku)
            d[sku]=n
    missing=set(required)-set(d)
    if missing:raise ValueError("Required SKU missing from "+ws.title+": "+repr(sorted(missing)))
    return d

def stage():
    from openpyxl import load_workbook
    manifest()
    plan=[
        ("data/master_control.xlsx","Товары",1),
        ("data/ozon_catalog.xlsx","Ozon",1),
        ("data/wb_catalog.xlsx","WB",1),
    ]
    pending=[];out={}
    for path,sheet,keycol in plan:
        wb=load_workbook(ROOT/path);ws=wb[sheet]
        required=set(PRICE) if "wb_catalog" not in path else set(PRICE)-{"Dq500"}
        rows=rows_index(ws,keycol,required)
        changes=[]
        for sku,r in rows.items():
            title=ws.cell(r,6 if "master_control" in path else 2).value
            if title:
                normalized=str(title).lower()
                if not all(t.lower() in normalized for t in NAME_MARKERS[sku]):
                    raise ValueError("Ambiguous exact SKU title in "+path+": "+sku)
            if "master_control" in path:
                cols=[(8,[OLD_MASTER[sku]]),(11,OLD_WB[sku])]
            elif "ozon_catalog" in path:
                cols=[(4,[OLD_MASTER[sku]])]
            else:
                cols=[(6,OLD_WB[sku])]
            for col,expected in cols:
                cell=ws.cell(r,col)
                before=amount(cell.value)
                if before is None and col==11 and sku=="Dq500":
                    continue # no confirmed WB listing, do NOT create a master WB price
                if before is None or not any(near(before,x) for x in expected+[PRICE[sku]]):
                    raise ValueError(f"Unexpected price in {path} {sku} {cell.coordinate}: {before}")
                if not near(before,PRICE[sku],.001):
                    cell.value=PRICE[sku]
                    changes.append({"sku":sku,"cell":cell.coordinate,"before":before,"after":PRICE[sku]})
        pending.append((path,wb,changes))
        out[path]={"sku_matches":len(rows),"changes":changes}
    # No workbook is saved until every required sheet, identity and source price passes preflight.
    for path,wb,changes in pending:
        if changes:wb.save(ROOT/path)
        wb.close()
    status("catalog",out)

def publish_ozon():
    import ozon_client
    rows=ozon_client.get_prices_and_stocks(list(PRICE))
    seen={};dups=set()
    for x in rows:
        sku=x.get("offer_id")
        if sku in PRICE:
            if sku in seen:dups.add(sku)
            seen[sku]=x
    tosend=[];out={}
    for sku,target in PRICE.items():
        x=seen.get(sku)
        if not x or sku in dups:
            out[sku]={"status":"missing_or_duplicate"};continue
        p=x.get("price") or {}
        before=amount(p.get("price"))
        if not any(near(before,v,.01) for v in (OLD_MASTER[sku],target)):
            out[sku]={"status":"unexpected_live_price","observed":before};continue
        if p.get("currency_code") not in ("RUB",None):
            out[sku]={"status":"unexpected_currency"};continue
        if near(before,target,.01):
            out[sku]={"status":"already_at_target","observed":before};continue
        payload={"offer_id":sku,"price":str(target),"currency_code":"RUB"}
        old=amount(p.get("old_price"))
        if old is not None and old>target:
            payload["old_price"]=str(int(old))
        minimum=amount(p.get("min_price"))
        if minimum is not None and minimum>target:
            payload["min_price"]=str(int(target))
        tosend.append(payload)
        out[sku]={"status":"ready","before":before,"min_price_before":minimum}
    if tosend:
        response=ozon_client.update_prices(tosend)
        for x in (response.get("result") or []) if isinstance(response,dict) else []:
            sku=x.get("offer_id")
            if sku in out:
                out[sku]["acknowledged"]=bool(x.get("updated") and not x.get("errors"))
    for wait in (3,6,12):
        time.sleep(wait)
        latest={x.get("offer_id"):x for x in ozon_client.get_prices_and_stocks(list(PRICE))}
        for sku,target in PRICE.items():
            actual=amount((latest.get(sku) or {}).get("price",{}).get("price"))
            out[sku].update({"observed":actual,"verified":near(actual,target,.01)})
        if all(out[sku].get("verified") for sku in PRICE):break
    return out

def publish_wb():
    import wb_client
    current=wb_client.get_prices(limit=1000)
    found={};duplicates=set()
    for x in current:
        sku=x.get("vendorCode")
        if sku in PRICE:
            if sku in found:duplicates.add(sku)
            found[sku]=x
    out={};batch=[]
    for sku,target in PRICE.items():
        x=found.get(sku)
        if not x or sku in duplicates:
            out[sku]={"status":"no_confirmed_WB_listing" if not x else "duplicate"};continue
        sizes=x.get("sizes") or []
        if len(sizes)!=1 or x.get("editableSizePrice"):
            out[sku]={"status":"size_specific_prices"};continue
        s=sizes[0];actual=amount(s.get("discountedPrice"))
        allowed=OLD_WB[sku]+[target]
        if not any(near(actual,z,1.5) for z in allowed):
            out[sku]={"status":"unapproved_live_price","observed":actual};continue
        if near(actual,target,1.5):
            out[sku]={"status":"already_at_target","observed":actual,"verified":True};continue
        disc=int(x.get("discount") or 0)
        if not 0<=disc<100:
            out[sku]={"status":"bad_discount"};continue
        factor=Decimal(100-disc)/Decimal(100)
        base=int((Decimal(target)/factor).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        batch.append({"nmID":x["nmID"],"price":base,"discount":disc})
        out[sku]={"status":"ready","before":actual,"nmID":x["nmID"],"base_submitted":base,"unchanged_discount":disc}
    taskid=None
    if batch:
        reply=wb_client.update_prices(batch)
        taskid=(reply.get("data") or {}).get("id") if isinstance(reply,dict) else None
        out["price_upload"]={"count":len(batch),"acknowledged":bool(taskid),"upload_id":taskid}
    for wait in (8,14,20):
        time.sleep(wait)
        latest={r.get("vendorCode"):r for r in wb_client.get_prices(limit=1000) if r.get("vendorCode") in PRICE}
        for sku,target in PRICE.items():
            r=latest.get(sku);s=(r or {}).get("sizes") or []
            val=amount(s[0].get("discountedPrice")) if len(s)==1 else None
            out[sku].update({"observed":val,"verified":near(val,target,1.5)})
        if all(out[s].get("verified") or out[s].get("status")=="no_confirmed_WB_listing" for s in PRICE):break
    if taskid and any(not out[s].get("verified") and out[s].get("status")=="ready" for s in PRICE):
        try:
            history=wb_client.get_price_upload_result(taskid)
            out["upload_history"]={"task":history.get("task"),"goods":[{"nmID":x.get("nmID"),"status":x.get("status"),"errorText":str(x.get("errorText") or "")[:150]} for x in (history.get("goods") or []) if x.get("nmID") in [v.get("nmID") for k,v in out.items() if k in PRICE]]}
        except Exception as e:out["upload_history_error"]=type(e).__name__
    return out

def req(ym,method,path,**kwargs):
    code,data=ym._request(method,path,**kwargs)
    if code!=200 or not isinstance(data,dict) or data.get("status","OK")!="OK" or data.get("errors"):
        raise RuntimeError("Market returned unverified response for "+path.split("/")[-1])
    return data

def publish_market():
    import yandex_market_client as ym
    _,campaign=ym._creds()
    business=ym.get_business_id()
    settings=req(ym,"POST",f"/v2/businesses/{business}/settings",json_body={})
    if (settings.get("result") or {}).get("settings",{}).get("onlyDefaultPrice") is not True:
        return {"status":"business_prices_are_not_unified_skip_all"}
    shops=req(ym,"GET","/v2/campaigns").get("campaigns") or []
    siblings=[int(x["id"]) for x in shops if str((x.get("business") or {}).get("id"))==str(business) and str(x["id"])!=str(campaign)]
    for sib in siblings:
        q=req(ym,"POST",f"/v2/campaigns/{sib}/offers",json_body={"offerIds":list(PRICE)})
        present=[r.get("offerId") for r in (q.get("result") or {}).get("offers",[]) if r.get("status") not in ("ARCHIVED","DISABLED_BY_PARTNER")]
        if present:return {"status":"other_shop_active_skip_all","conflicting_skus":present}
    mapping={}
    for x in ym.get_all_offers(archived=False):
        if x.get("sku") in PRICE:
            sku=x["sku"]
            if sku in mapping:raise ValueError("Duplicate Yandex mapping "+sku)
            mapping[sku]=x
    r=req(ym,"POST",f"/v2/campaigns/{campaign}/offers",json_body={"offerIds":list(PRICE)})
    sr={x.get("offerId"):x for x in (r.get("result") or {}).get("offers",[])}
    r=req(ym,"POST",f"/v2/businesses/{business}/offer-prices",json_body={"offerIds":list(PRICE)})
    br={x.get("offerId"):x for x in (r.get("result") or {}).get("offers",[])}
    report={};updates=[]
    for sku,target in PRICE.items():
        shop=sr.get(sku);business_row=br.get(sku)
        if not shop or not business_row or shop.get("unverified") or sku not in mapping or mapping[sku].get("archived") or not mapping[sku].get("market_sku"):
            report[sku]={"status":"catalog_not_verified"};continue
        if (shop.get("campaignPrice") or {}).get("value") is not None:
            report[sku]={"status":"campaign_price_override"};continue
        basic=shop.get("basicPrice") or {};office=business_row.get("price") or {}
        b=amount(basic.get("value"));o=amount(office.get("value"))
        if b is None or o is None or not near(b,o,.01):
            report[sku]={"status":"business_and_shop_not_equal","shop":b,"business":o};continue
        if basic.get("currencyId") not in ("RUR","RUB") or office.get("currencyId") not in ("RUR","RUB"):
            report[sku]={"status":"unexpected_currency"};continue
        if not any(near(o,v,.01) for v in (OLD_MASTER[sku],target)):
            report[sku]={"status":"unapproved_live_price","observed":o};continue
        if near(o,target,.01):
            report[sku]={"status":"already_at_target","observed":o,"verified":True};continue
        new={"value":target,"currencyId":"RUR"}
        for k in ("discountBase","minimumForBestseller","excludedFromBestsellers"):
            if office.get(k) is not None:new[k]=office[k]
        updates.append({"offerId":sku,"price":new})
        report[sku]={"status":"ready","before":o}
    if updates:
        ack=req(ym,"POST",f"/v2/businesses/{business}/offer-prices/updates",json_body={"offers":updates})
        report["upload"]={"sent":len(updates),"acknowledged":ack.get("status")=="OK"}
    for wait in (4,8,14):
        time.sleep(wait)
        q=req(ym,"POST",f"/v2/campaigns/{campaign}/offers",json_body={"offerIds":list(PRICE)})
        sr={x.get("offerId"):x for x in (q.get("result") or {}).get("offers",[])}
        q=req(ym,"POST",f"/v2/businesses/{business}/offer-prices",json_body={"offerIds":list(PRICE)})
        br={x.get("offerId"):x for x in (q.get("result") or {}).get("offers",[])}
        for sku,target in PRICE.items():
            shop=sr.get(sku) or {};office=br.get(sku) or {}
            val=amount((shop.get("basicPrice") or {}).get("value"))
            ov=amount((office.get("price") or {}).get("value"))
            report[sku].update({"observed_shop":val,"observed_business":ov,"verified":near(val,target,.01) and near(ov,target,.01)})
        if all(report[s].get("verified") for s in PRICE):break
    return report

def publish():
    manifest()
    for name,fn in (("ozon",publish_ozon),("wildberries",publish_wb),("yandex_market",publish_market)):
        try:data=fn()
        except Exception as e:data={"status":"failed_or_unverified","error_type":type(e).__name__}
        status(name,data)

if __name__=="__main__":
    cmd=sys.argv[1] if len(sys.argv)>1 else "stage"
    if cmd=="stage":stage()
    elif cmd=="publish":publish()
    else:raise SystemExit("Expected stage|publish")
