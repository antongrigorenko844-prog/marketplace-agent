"""Price only: Avito old ad 4125224328 is TWO rods without sleeves.

User reviewed it as 2 rods of either 99/101mm and asked to sync.
Two canonical individual rods each cost 1240 RUB on Avito, so exact
additive price for two is 2480. No separate main-kit SKU yet; do not
mistake for 2 rods + 2 sleeves, which is 3450. No feed/site changes.
"""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import json,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.related_price_inspect_20261122 import parse
import avito_client as av
REQ=ROOT/"data/avito_two_bare_rods_price_20261124_request.json"
OUT=ROOT/"data/avito_two_bare_rods_price_20261124_result.json"
REG=ROOT/"data/avito_legacy_catalog_candidates_20261124.json"
AUD=ROOT/"data/avito_price_audit_20261124.json"
OLD_ID=4125224328
EXPECTED_OLD=1490
SINGLE_PRICE=1240
TARGET=2*SINGLE_PRICE
SINGLES=("0am325091e","0am325091f")
def require(ok,message):
    if not ok:raise RuntimeError("GUARD_BLOCKED: "+message)
def money(v):
    try:return round(float(v),2)
    except(ValueError,TypeError):return None
def load(path):return json.loads(path.read_text(encoding="utf8"))
def live():return {int(a["id"]):a for a in av.list_all_items(per_page=25,max_pages=40) if a.get("id") is not None}
def save(r):
    r["checked_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("AVITO_2_BARE_RODS",json.dumps({k:r.get(k) for k in ("mode","status","before","after","error")},ensure_ascii=False),flush=True)
def inspect():
    req=load(REQ)
    require(req.get("mode") in ("inspect","apply") and req.get("owner_approved") is True,"owner approval")
    require(req.get("price_only") is True and req.get("old_avito_id")==OLD_ID and
            req.get("expected_old_price_rub")==EXPECTED_OLD and
            req.get("desired_price_rub")==TARGET and
            req.get("create_new_ads") is False and req.get("change_feed") is False and
            req.get("change_master") is False and req.get("change_stock") is False and
            req.get("change_site") is False and req.get("change_main") is False,"target not allowlisted")
    policy=load(ROOT/"data/pricing_policy.json")
    require(policy.get("mode")=="manual_prices_only" and policy.get("automatic_avito_site_repricing") is False,"pricing policy")
    reg=load(REG)
    old=next((x for x in reg["entries"] if x["avito_id"]==OLD_ID),None)
    require(old and old.get("owner_information_status")=="composition_verified" and
            old.get("owner_price_instruction")=="align_to_two_main_single_rod_prices" and
            old.get("owner_confirmed_composition")==[{"product":"шток мехатроника DQ200","qty":2}] and
            old["derived_price_basis"]["resulting_sale_price_rub"]==TARGET,
            "owner BOM or target changed")
    audit=load(AUD)
    canonical={x["sku"]:x for x in audit["products"]}
    control=parse(str(ROOT/"data/avito_site_price_overrides.xlsx"))
    rows=next((x["rows"] for x in control if x["name"]=="Цены Avito и сайт"),[])
    prices={str(row[0]).strip():money(row[1]) for row in rows[1:] if row and len(row)>1 and row[0]}
    require(all(money(prices.get(s))==SINGLE_PRICE and money(canonical.get(s,{}).get("avito_cfg"))==SINGLE_PRICE for s in SINGLES),
            "source-of-truth single-rod prices changed")
    links=load(ROOT/"data/avito_links.json")
    require(OLD_ID not in [int(v) for k,v in links.items() if not k.startswith("_")],"old listing already managed by feed")
    now=live()
    require(OLD_ID in now and now[OLD_ID].get("status")=="active","old 2-rod listing inactive")
    require(now[OLD_ID].get("title")=="Штоки dsg7 dq200 (шток дсг 7)","legacy title changed")
    require(money(now[OLD_ID].get("price")) in (EXPECTED_OLD,TARGET),"legacy price changed unexpectedly")
    for sku in SINGLES:
        cid=canonical[sku].get("mapped",{}).get("id")
        require(cid in now and now[cid].get("status")=="active" and
                money(now[cid]["price"])==SINGLE_PRICE and cid!=OLD_ID,
                "individual canonical rod listing unavailable "+sku)
    return req,now
def main():
    report={"mode":"unknown","status":"init","old_avito_id":OLD_ID,
            "seller_price_writes":0,"main_price_changes":0,"stock_changes":0,
            "site_changes":0,"feed_changes":0,"new_ads":0}
    try:
        req,before=inspect()
        report.update({"mode":req["mode"],"status":"preflight_ok","before":money(before[OLD_ID]["price"]),"desired":TARGET})
        save(report)
        if req["mode"]=="inspect":
            report["status"]="inspect_ok_no_writes";save(report);return
        if money(before[OLD_ID]["price"])!=TARGET:
            resp=av._request("POST",f"/core/v1/items/{OLD_ID}/update_price",
                             json={"price":TARGET},retries=1)
            require((resp.get("result") or {}).get("success") is True,"seller update not accepted")
            report["seller_price_writes"]=1
        for n in range(6):
            if n:time.sleep(4)
            latest=live()
            report["after"]=money(latest.get(OLD_ID,{}).get("price"))
            report["status_after"]=latest.get(OLD_ID,{}).get("status")
            if report["after"]==TARGET and report["status_after"]=="active":
                report["status"]="verified_1_of_1";save(report);return
            save(report)
        raise RuntimeError("seller readback price not confirmed")
    except Exception as exc:
        report["status"]="blocked_or_partial"
        report["error"]={"type":type(exc).__name__,"message":str(exc)[:200]}
        save(report)
        raise
if __name__=="__main__": main()
