"""Reversible Ozon sale stop for the one exact product approved by its owner."""
import json
import os
import sys
import time
from pathlib import Path
from datetime import datetime, timezone

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ozon_client as oz

OFFER = "0cw0am"
PRODUCT = 3224994742
SKU = 3237042002


def report(key, value):
    print("OZ_PAUSE", datetime.now(timezone.utc).isoformat(), key, json.dumps(value, ensure_ascii=False), flush=True)


def one(rows):
    matched = [row for row in rows if row.get("offer_id") == OFFER]
    if len(matched) != 1 or len(rows) != 1:
        raise ValueError("Expected single exact Ozon offer")
    return matched[0]


def safe_error(exc):
    return type(exc).__name__ + ": " + str(exc)[:280]


def snapshot():
    info = one(oz.get_product_info_list([OFFER]))
    if int(info["id"]) != PRODUCT or (info.get("sku") and int(info["sku"]) != SKU):
        raise ValueError("Exact offer-to-product identity failed")
    stock = one(oz.get_stocks([OFFER]))
    if int(stock["product_id"]) != PRODUCT:
        raise ValueError("Unexpected product in stocks response")
    status = info.get("statuses") or {}
    amounts = {}
    ids = set()
    for item in stock.get("stocks") or []:
        scheme = str(item.get("type") or "unknown").lower()
        pair = amounts.setdefault(scheme, [0, 0])
        pair[0] += int(item.get("present") or 0)
        pair[1] += int(item.get("reserved") or 0)
        if scheme in ("fbs", "rfbs", "real_fbs", "realfbs"):
            for w in item.get("warehouse_ids") or []:
                if str(w).isdigit(): ids.add(int(w))
            if item.get("warehouse_id"): ids.add(int(item["warehouse_id"]))
    return {"archived":bool(info.get("is_archived") or info.get("is_autoarchived")),
            "status":status.get("status_name"),"moderation":status.get("moderate_status"),
            "visibility":info.get("visibility_details"),"schemes_present_reserved":amounts}, ids


def wh_read():
    for body in ({"sku":[SKU],"limit":100}, {"skus":[SKU],"limit":100}, {"offer_id":[OFFER],"limit":100}):
        try:
            data = oz._post("/v2/product/info/stocks-by-warehouse/fbs", body, retries=1)
        except Exception:
            continue
        raw = data.get("result", data)
        items = raw if isinstance(raw,list) else raw.get("products",raw.get("items",raw.get("stocks",[])))
        ids = set()
        for row in items:
            if not isinstance(row, dict): continue
            if row.get("sku") and int(row["sku"]) != SKU: continue
            if row.get("offer_id") and row["offer_id"] != OFFER: continue
            # Accepted rows must be bound to the exact SKU, not just to a warehouse.
            if not row.get("sku") and row.get("offer_id") != OFFER: continue
            if row.get("warehouse_id"): ids.add(int(row["warehouse_id"]))
            for w in row.get("stocks", []) or []:
                if w.get("warehouse_id"):ids.add(int(w["warehouse_id"]))
        if ids: return ids
    return set()


def seller_count(s):
    return sum(n[0] for k,n in s["schemes_present_reserved"].items() if k in ("fbs","rfbs","real_fbs","realfbs"))


def seller_price():
    result=one(oz.get_prices_and_stocks([OFFER]))
    return (result.get("price") or {}).get("price")


before, warehouses = snapshot()
before_price = seller_price()
report("before", before)
if seller_count(before) and not before["archived"]:
    warehouses |= wh_read()
    if not warehouses:
        configured = os.environ.get("OZON_WAREHOUSE_ID", "").strip()
        if configured.isdigit() and int(configured) > 0: warehouses.add(int(configured))
    if not warehouses:
        warehouses = {int(w["warehouse_id"]) for w in oz.get_warehouses() if w.get("warehouse_id")}
    if warehouses:
        resp = oz.update_stocks([{"offer_id":OFFER, "stock":0, "warehouse_id":w} for w in sorted(warehouses)])
        rows = resp.get("result",[])
        report("seller_zero_ack", {"warehouses": len(warehouses), "updated":sum(x.get("updated") is True for x in rows), "errors":[str(e)[:180] for x in rows for e in (x.get("errors") or [])]})
        for i in range(15):
            s,_ = snapshot()
            if not seller_count(s): break
            if i<14:time.sleep(4)
    else:
        report("seller_zero_ack", {"warehouses":0, "note":"No FBS warehouse ID returned"})
post_stock,_=snapshot()
report("after_seller_stock",post_stock)
if not post_stock["archived"]:
    try:
        ack=oz.archive_products([PRODUCT])
        report("archive_ack", {"result":ack.get("result")})
    except Exception as exc:
        report("archive_error",safe_error(exc))
for i in range(24):
    after,_=snapshot()
    unavailable=after["archived"]
    if unavailable:break
    if i<23:time.sleep(5)
report("after",after)
report("verification",{"product_id":PRODUCT,"sku":SKU,"offer_id":OFFER,"seller_price_unchanged":bool(before_price) and before_price==seller_price(),"archived":after["archived"],"seller_available_zero":seller_count(after)==0,"fbo_present":after["schemes_present_reserved"].get("fbo",[0,0])[0]})
if not after["archived"] and (seller_count(after)>0 or any(n[0]>0 for n in after["schemes_present_reserved"].values())):raise RuntimeError("Sale stop not confirmed for every scheme")
