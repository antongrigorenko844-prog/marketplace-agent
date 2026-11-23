"""Owner-confirmed remaining 14 old Avito listings -> canonical price sync.

All 22 formerly ambiguous old listings have owner-confirmed contents in registry.
Seven previously aligned; exactly 14 IDs remain for direct price-only update.
Do NOT change description, images, stock, site, feed, or main listing prices.
A matching owner-approved canonical SKU price or exact component sum is required.
"""
from __future__ import annotations
from datetime import datetime,timezone
from pathlib import Path
import json,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.related_price_inspect_20261122 import parse
import avito_client as av

REQ=ROOT/"data/avito_owner_14_legacy_prices_request_20261124.json"
OUT=ROOT/"data/avito_owner_14_legacy_prices_result_20261124.json"
REG=ROOT/"data/avito_legacy_catalog_candidates_20261124.json"
AUDIT=ROOT/"data/avito_price_audit_20261124.json"
ALLOWED=json.loads(r'''{"3965211558":{"title":"Ремкомплект (прокладки) мехатроника DSG7 DQ200 0am","old_price":3850,"new_price":4850,"canonical_skus":["0am325025m"],"policy":"sync_to_main_catalog_0am325025m_4850"},"3965336925":{"title":"Соленоид мехатроника DSG 7 DQ200 Bosch регулятор","old_price":3890,"new_price":5350,"canonical_skus":["0AM325025B"],"policy":"sync_to_main_catalog_0AM325025B_5350"},"3965565847":{"title":"Ремкомплект мехатроника DSG 7 DQ200 0AM 0CW","old_price":9800,"new_price":7850,"canonical_skus":["0am325025"],"policy":"sync_to_main_catalog_0am325025_7850"},"4125069388":{"title":"Гильза мехатроника DSG 7 DQ200 0AM 0CW новая","old_price":2990,"new_price":1650,"canonical_skus":["vtulki-shtokov-2sht"],"policy":"sync_to_main_catalog_vtulki-shtokov-2sht_1650"},"4125079566":{"title":"Пыльник штоков мехатроника DSG 7 DQ200 0AM 0CW","old_price":450,"new_price":950,"canonical_skus":["122390AK-AF"],"policy":"sync_to_main_catalog_122390AK-AF_950"},"4125283125":{"title":"Ремкомплект прокладки мехатроника DSG 7 DQ200 0AM","old_price":11890,"new_price":4850,"canonical_skus":["0am325025m"],"policy":"sync_to_main_catalog_0am325025m_4850"},"4189437762":{"title":"Ремкомплект мехатроника dsg 7 dq200 плита фильтр","old_price":9850,"new_price":7850,"canonical_skus":["0am325025"],"policy":"sync_to_main_catalog_0am325025_7850"},"4253030337":{"title":"Ремкомплект dq200 dsg7 0am гидроплита прокладки","old_price":9890,"new_price":7850,"canonical_skus":["0am325025"],"policy":"sync_to_main_catalog_0am325025_7850"},"4253114863":{"title":"Прокладки dsg 7 сепараторная пластина мехатроника","old_price":1290,"new_price":1350,"canonical_skus":["separatornaya-plastiny-solenoid-dq200"],"policy":"sync_to_main_catalog_separatornaya-plastiny-solenoid-dq200_1350"},"4284610725":{"title":"Заглушка коробки DSG7 DQ200 0AM 0CW 52м","old_price":890,"new_price":950,"canonical_skus":["0AM301212A"],"policy":"sync_to_main_catalog_0AM301212A_950"},"4284950339":{"title":"Соленоид dsg 7 dq200 0am 0cw регулятор давления","old_price":3850,"new_price":5350,"canonical_skus":["0AM325025B"],"policy":"sync_to_main_catalog_0AM325025B_5350"},"4573102274":{"title":"Ремкомплект dsg 7 dq200 усиленная плита прокладки","old_price":9890,"new_price":7850,"canonical_skus":["0am325025"],"policy":"sync_to_main_catalog_0am325025_7850"},"4445119215":{"title":"Ремкомплект мехатроника dq200 dsg7 0am 0cw","old_price":9800,"new_price":7850,"canonical_skus":["0am325025"],"policy":"sync_to_main_catalog_0am325025_7850"},"4444968174":{"title":"Крышка поддон мехатроника+прокладка DSG 7 DQ200","old_price":2890,"new_price":2300,"canonical_skus":["kryshka-mehatronika-dq200","prokladka-kryshki-dq200"],"policy":"sync_to_sum_two_ordinary_components_2300"}}''')

def require(x,reason):
    if not x:raise RuntimeError("GUARD_BLOCKED: "+reason)
def load(path):return json.loads(path.read_text(encoding="utf8"))
def money(v):
    try:return round(float(v),2)
    except (TypeError,ValueError):return None
def live():
    raw=av.list_all_items(per_page=25,max_pages=45)
    items={int(x["id"]):x for x in raw if x.get("id") is not None}
    require(len(raw)==len(items),"Duplicate seller IDs or malformed live API response")
    require(len(raw)>=110,"Seller API returned an incomplete item list")
    return items
def configured():
    pages=parse(str(ROOT/"data/avito_site_price_overrides.xlsx"))
    ws=next((x["rows"] for x in pages if x["name"]=="Цены Avito и сайт"),[])
    require(bool(ws),"Canonical Avito price override sheet missing")
    prices={}
    for row in ws[1:]:
        if row and len(row)>1 and row[0] is not None:
            sku=str(row[0]).strip()
            require(sku not in prices,"Duplicate canonical SKU in override "+sku)
            prices[sku]=money(row[1])
    return prices
def prepare():
    request=load(REQ)
    require(request.get("schema_version")==1 and
            request.get("owner_approved") is True and
            request.get("mode") in ("inspect","apply","verify") and
            request.get("expected_old_count")==14 and
            request.get("update_old_ad_prices_only") is True and
            request.get("disable_auto_reprice") is True and
            request.get("leave_main_ads_unchanged") is True and
            request.get("leave_site_unchanged") is True and
            request.get("leave_feed_unchanged") is True and
            request.get("leave_stock_unchanged") is True and
            request.get("leave_images_unchanged") is True and
            request.get("leave_text_unchanged") is True,
            "Owner-approved price-only limits changed")
    policy=load(ROOT/"data/pricing_policy.json")
    require(policy.get("mode")=="manual_prices_only" and
            policy.get("automatic_avito_site_repricing") is False,
            "Automatic pricing freeze not active")
    reqs=request.get("targets") or []
    require(len(reqs)==14 and len(ALLOWED)==14, "Expected 14 records")
    require({str(q["id"]) for q in reqs}==set(ALLOWED),
            "Changed allowlisted Avito IDs")
    for t in reqs:
        aid=str(t["id"]); a=ALLOWED[aid]
        for key in ("title","old_price","new_price","canonical_skus","policy"):
            require(t.get(key)==a[key],"Manifest target mismatch "+aid+":"+key)
    registry=load(REG)
    entries={str(x["avito_id"]):x for x in registry["entries"]}
    require(registry["owner_review_progress"].get("all_22_reviewed") is True,
            "Owner verification of 22 old ads missing")
    audit=load(AUDIT)
    primary={x["sku"]:x for x in audit["products"]}
    prices=configured()
    links=load(ROOT/"data/avito_links.json")
    managed={int(v) for k,v in links.items() if not k.startswith("_")}
    ads=live()
    require(not managed.intersection(int(k) for k in ALLOWED),
            "An old ad is already managed by main feed")
    checked={}
    for aid,expected in ALLOWED.items():
        ident=int(aid); e=entries.get(aid)
        require(e and e.get("owner_information_status")=="composition_verified_from_user_description"
                and e.get("owner_price_instruction")==expected["policy"]
                and e.get("owner_target_price_rub")==expected["new_price"]
                and e.get("owner_confirmed_related_master_skus")==expected["canonical_skus"],
                "Owner composition/price confirmation not present "+aid)
        require(ident in ads and ads[ident].get("status")=="active",
                "Old Avito listing missing/inactive "+aid)
        item=ads[ident]
        require(str(item.get("title") or "")==expected["title"],
                "Old listing title changed "+aid)
        current=money(item.get("price"))
        require(current in (money(expected["old_price"]),money(expected["new_price"])),
                "Old price outside scope "+aid)
        skus=expected["canonical_skus"]
        values=[]
        for sku in skus:
            require(sku in primary and sku in prices,"No exact SKU/source price "+sku)
            main_price=money(primary[sku].get("avito_cfg"))
            live_price=money(prices[sku])
            require(main_price==live_price and main_price is not None,
                    "Catalog audit/source price disagreement "+sku)
            values.append(main_price)
        expected_total=sum(values) if len(values)>1 else values[0]
        require(abs(expected_total-money(expected["new_price"]))<0.01,
                "Price is not main SKU or exact component sum "+aid)
        # Primary may be temporarily absent/blocked in API. Main *configured*
        # SKU is authoritative; never confuse absence with price mismatch.
        primary_status=[]
        for sku in skus:
            main_id=(primary[sku].get("mapped") or {}).get("id")
            if main_id and int(main_id) in ads and ads[int(main_id)].get("status")=="active":
                require(money(ads[int(main_id)].get("price"))==money(prices[sku]),
                        "Active canonical main price mismatch "+sku)
                primary_status.append({"sku":sku,"id":main_id,"status":"live_confirmed"})
            else:primary_status.append({"sku":sku,"id":main_id,"status":"main_catalog_source_only"})
        checked[aid]={"sku":skus,"before":current,"approved":money(expected["new_price"]),
            "title":expected["title"],"main_validation":primary_status,
            "old_status":"active"}
    return request,checked,ads
def save(result):
    result["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("AVITO_OWNER_14_ALIGN",json.dumps({k:result.get(k) for k in
      ("mode","status","stats","error")},ensure_ascii=False),flush=True)
def main():
    result={"mode":None,"status":"starting","seller_price_writes":0,
            "main_ad_changes":0,"site_changes":0,"feed_changes":0,"stock_changes":0,
            "text_changes":0,"image_changes":0,"new_ads":0,
            "price_receipts":{},"targets":{}}
    try:
        req,targets,before=prepare()
        result.update({"mode":req["mode"],"status":"preflight_ok","targets":targets})
        result["stats"]={"target_count":len(targets),
            "already_at_target":sum(x["before"]==x["approved"] for x in targets.values()),
            "requiring_write":sum(x["before"]!=x["approved"] for x in targets.values()),
            "live_seller_listed_total":len(before)}
        save(result)
        if req["mode"]=="inspect":
            result["status"]="inspect_ok_no_writes";save(result);return
        if req["mode"]=="apply":
            for aid,t in targets.items():
                if t["before"]==t["approved"]:
                    result["price_receipts"][aid]="already_correct"
                    continue
                try:
                    reply=av._request("POST",f"/core/v1/items/{aid}/update_price",
                         json={"price":int(t["approved"])},retries=3)
                    accepted=bool((reply.get("result") or {}).get("success") is True)
                    result["price_receipts"][aid]="accepted" if accepted else "api_not_confirmed"
                    if accepted:result["seller_price_writes"]+=1
                    save(result)
                    require(accepted,"Seller did not accept update "+aid)
                except Exception as exc:
                    result["price_receipts"][aid]="api_error_"+type(exc).__name__
                    save(result)
                    raise
                time.sleep(0.8)
        for attempt in range(1,8):
            if attempt>1:time.sleep(4)
            latest=live()
            found={}
            for aid,t in targets.items():
                old=latest.get(int(aid),{})
                actual=money(old.get("price"))
                found[aid]={"actual_price":actual,"target":t["approved"],
                   "active":old.get("status")=="active",
                   "verified":actual==t["approved"] and old.get("status")=="active"}
            result["live_verify"]=found
            result["stats"]["verified"]=sum(x["verified"] for x in found.values())
            result["stats"]["unverified"]=len(targets)-result["stats"]["verified"]
            result["status"]="verified_14_of_14" if all(x["verified"] for x in found.values()) else "partial_or_pending"
            save(result)
            if result["status"]=="verified_14_of_14":return
        raise RuntimeError("Not all 14 old ads reached canonical price in live readback")
    except Exception as exc:
        result["status"]="blocked_or_partial_review_required"
        result["error"]={"type":type(exc).__name__,"message":str(exc)[:250]}
        save(result)
        raise
if __name__=="__main__":main()
