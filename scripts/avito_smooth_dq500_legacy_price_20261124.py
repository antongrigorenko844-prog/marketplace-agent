"""Owner-approved only price change: old smooth DQ500 housing 4348814868, 3490 -> 980.

Uses existing canonical Dq500 (not DQ500 kit, not 0BH325183 ribbed).
Inspects before applying; never changes feed, stock, main listing, site, photos.
"""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import json,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.related_price_inspect_20261122 import parse
import avito_client as av
REQ=ROOT/"data/avito_smooth_dq500_legacy_price_request_20261124.json"
OUT=ROOT/"data/avito_smooth_dq500_legacy_price_result_20261124.json"
REG=ROOT/"data/avito_legacy_catalog_candidates_20261124.json"
AUD=ROOT/"data/avito_price_audit_20261124.json"
OLD_ID=4348814868
MAIN_ID=8344886348
SKU="Dq500"
BEFORE=3490
TARGET=980
TITLE="Корпус фильтра DQ500 DSG7"
def check(b,msg):
    if not b: raise RuntimeError("GUARD_BLOCKED: "+msg)
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def money(x):
    try:return round(float(x),2)
    except (ValueError,TypeError): return None
def current():
    items=av.list_all_items(per_page=25,max_pages=40)
    d={int(z["id"]):z for z in items if z.get("id") is not None}
    check(len(items)==len(d),"Duplicate seller listing IDs")
    return d
def source_price():
    sheets=parse(str(ROOT/"data/avito_site_price_overrides.xlsx"))
    ws=next((z["rows"] for z in sheets if z["name"]=="Цены Avito и сайт"),[])
    rows=[z for z in ws[1:] if z and str(z[0]).strip()==SKU]
    check(len(rows)==1,"Expected exactly one canonical price row for Dq500")
    return money(rows[0][1])
def inspect():
    req=load(REQ)
    check(req.get("mode") in ("inspect","apply") and
          req.get("owner_approved") is True and req.get("old_id")==OLD_ID
          and req.get("main_id")==MAIN_ID and req.get("sku")==SKU
          and req.get("expected_before")==BEFORE and req.get("target_price")==TARGET
          and req.get("price_only") is True
          and req.get("change_stock") is False and req.get("change_feed") is False
          and req.get("change_main") is False and req.get("change_site") is False
          and req.get("create_ads") is False,"Owner price scope missing/changed")
    policy=load(ROOT/"data/pricing_policy.json")
    check(policy.get("mode")=="manual_prices_only" and
          policy.get("automatic_avito_site_repricing") is False,
          "Pricing policy changed")
    reg=load(REG)
    own=[x for x in reg["entries"] if x["avito_id"]==OLD_ID]
    check(len(own)==1 and own[0].get("owner_information_status")=="composition_verified"
          and own[0].get("owner_price_instruction")=="sync_to_main_catalog_Dq500_980"
          and own[0].get("owner_confirmed_related_master_skus")==[SKU],
          "Owner confirmation changed")
    audit=load(AUD)
    main=[z for z in audit["products"] if z["sku"]==SKU]
    check(len(main)==1 and main[0]["avito_cfg"]==TARGET
          and main[0]["mapped"].get("id")==MAIN_ID,
          "Canonical Dq500 SKU mapping changed")
    check(source_price()==TARGET,"Canonical price override no longer 980")
    links=load(ROOT/"data/avito_links.json")
    check(OLD_ID not in [int(v) for k,v in links.items() if not k.startswith("_")],
          "Old listing already controlled by autoload feed")
    all_ads=current()
    check(OLD_ID in all_ads and MAIN_ID in all_ads,"Old/main active listing missing")
    old,main=all_ads[OLD_ID],all_ads[MAIN_ID]
    check(old.get("status")=="active" and main.get("status")=="active",
          "Old/main not active")
    check(old.get("title")==TITLE,"Old ad title changed")
    check(money(main.get("price"))==TARGET,"Canonical live price changed")
    check(money(old.get("price")) in (BEFORE,TARGET),"Old live price changed unexpectedly")
    return req,all_ads
def save(result):
    result["checked_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("AVITO_SMOOTH_DQ500_PRICE",json.dumps({k:result.get(k) for k in
          ("status","mode","old_price","new_price","error")},ensure_ascii=False),flush=True)
def main():
    result={"mode":None,"status":"started","old_id":OLD_ID,
            "seller_price_writes":0,"main_price_changes":0,"stock_changes":0,
            "feed_changes":0,"site_changes":0,"new_ads":0}
    try:
        req,items=inspect()
        result["mode"]=req["mode"]
        result["old_price"]=money(items[OLD_ID].get("price"))
        result["main_price_before"]=money(items[MAIN_ID].get("price"))
        result["status"]="preflight_ok";save(result)
        if req["mode"]=="inspect":
            result["status"]="inspect_ok_no_writes";save(result);return
        if result["old_price"]!=TARGET:
            reply=av._request("POST",f"/core/v1/items/{OLD_ID}/update_price",
                              json={"price":TARGET},retries=1)
            check((reply.get("result") or {}).get("success") is True,
                  "Seller did not acknowledge the exact price")
            result["seller_price_writes"]=1;save(result)
        for attempt in range(6):
            if attempt:time.sleep(4)
            observed=current()
            old=observed.get(OLD_ID,{})
            main=observed.get(MAIN_ID,{})
            result["new_price"]=money(old.get("price"))
            result["main_price_after"]=money(main.get("price"))
            result["old_status_after"]=old.get("status")
            good=(old.get("status")=="active" and
                  result["new_price"]==TARGET and
                  main.get("status")=="active" and
                  result["main_price_after"]==TARGET)
            result["status"]="verified_1_of_1" if good else "pending"
            save(result)
            if good:return
        raise RuntimeError("Seller price readback did not confirm target")
    except Exception as exc:
        result["status"]="blocked_or_partial"
        result["error"]={"type":type(exc).__name__,"message":str(exc)[:250]}
        save(result)
        raise
if __name__=="__main__":main()
