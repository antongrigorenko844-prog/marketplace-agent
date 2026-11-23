"""Update only owner-confirmed old DQ200 rod-kit ads to matching canonical prices.

Offers 4220729305 (two rods + two sleeves either size) and 4445230063
(one rod + sleeve either size). Two canonical length variants have the same
Avito price for each kit. Keep the historical ads; never create new ads or
change canonical listings. Three-step inspect/apply/verify with fail-close.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
import json, sys, time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.related_price_inspect_20261122 import parse
import avito_client as av
REQ=ROOT/"data/avito_verified_rods_price_20261124_request.json"
OUT=ROOT/"data/avito_verified_rods_price_20261124_result.json"
REG=ROOT/"data/avito_legacy_catalog_candidates_20261124.json"
AUD=ROOT/"data/avito_price_audit_20261124.json"
def check(ok,why):
    if not ok:raise RuntimeError("GUARD_BLOCKED: "+why)
def num(value):
    try:return round(float(value),2)
    except(TypeError,ValueError):return None
def live():
    # API listing cap: fetch in 25-item pages; never rely on stale JSON as current state.
    arr=av.list_all_items(per_page=25,max_pages=40)
    result={int(x["id"]):x for x in arr if x.get("id") is not None}
    check(len(result)==len(arr),"duplicate ID in seller API listing")
    return result
def price_sources():
    pages=parse(str(ROOT/"data/avito_site_price_overrides.xlsx"))
    ws=next((s["rows"] for s in pages if s["name"]=="Цены Avito и сайт"),[])
    check(bool(ws),"missing canonical price spreadsheet")
    return {str(row[0]).strip():num(row[1]) for row in ws[1:] if len(row)>1 and row[0] is not None}
def prepare():
    request=json.loads(REQ.read_text(encoding="utf-8"))
    check(request.get("mode") in ("inspect","apply","verify"),"unrecognized mode")
    check(request.get("owner_approved") is True and request.get("change_only_old_ad_prices") is True and
          request.get("create_new_ads") is False and request.get("change_main_ads") is False and
          request.get("change_stock") is False and request.get("change_site") is False and
          request.get("change_feeds") is False and len(request.get("targets",[]))==2,
          "approval scope changed")
    p=json.loads((ROOT/"data/pricing_policy.json").read_text(encoding="utf-8"))
    check(p.get("mode")=="manual_prices_only" and p.get("automatic_avito_site_repricing") is False,
          "pricing freeze changed")
    reg=json.loads(REG.read_text(encoding="utf-8"))
    aud=json.loads(AUD.read_text(encoding="utf-8"))
    master=price_sources()
    canonical={x["sku"]:x for x in aud["products"]}
    links=json.loads((ROOT/"data/avito_links.json").read_text(encoding="utf-8"))
    managed_ids={int(v) for k,v in links.items() if not k.startswith("_")}
    current=live()
    expected={4220729305:("sync_to_common_canonical_price_3450",3450.0,5890.0,
                         ("0am325091e-set","0am325091f-set")),
              4445230063:("sync_to_common_canonical_price_1880",1880.0,2890.0,
                         ("0am325025u","0am325025z"))}
    check({int(t["old_avito_id"]) for t in request["targets"]}==set(expected),
          "unexpected legacy listing ID")
    for t in request["targets"]:
        old=int(t["old_avito_id"])
        action,target,before,skus=expected[old]
        regrow=next((e for e in reg["entries"] if e["avito_id"]==old),None)
        check(regrow is not None and regrow.get("owner_price_instruction")==action and
              regrow.get("owner_information_status")=="composition_verified" and
              set(regrow["owner_confirmed_related_master_skus"])==set(skus),
              "owner confirmation not recorded for "+str(old))
        check(old not in managed_ids,"old ad is already managed by canonical feed")
        check(tuple(t["target_master_skus"])==skus and num(t["approved_price_rub"])==target and
              num(t["expected_old_price_rub"])==before and
              all(num(master.get(s))==target and num(canonical.get(s,{}).get("avito_cfg"))==target for s in skus),
              "canonical price mismatch for "+str(old))
        check(old in current and current[old].get("status")=="active" and
              num(current[old].get("price")) in (before,target),
              "old ad missing/inactive or current price unexpected for "+str(old))
        check(current[old].get("title")==t["expected_title"],"old title changed")
        for s in skus:
            active=canonical[s].get("mapped",{})
            check(active.get("id") in current and
                  current[active["id"]].get("status")=="active" and
                  num(current[active["id"]].get("price"))==target,
                  "canonical main variant missing/different price "+s)
    return request,current
def save(data):
    data["checked_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("AVITO_CONFIRMED_RODS",json.dumps({"mode":data.get("mode"),
        "status":data.get("status"),"summary":data.get("summary"),
        "error":data.get("error")},ensure_ascii=False),flush=True)
def main():
    result={"mode":"unset","status":"starting","seller_price_writes":0,
            "new_ads":0,"main_price_changes":0,"feed_changes":0,"stock_changes":0,
            "site_changes":0,"targets":[]}
    try:
        request,before=prepare()
        result["mode"]=request["mode"]
        for t in request["targets"]:
            aid=int(t["old_avito_id"])
            result["targets"].append({"id":aid,"old_live_price":num(before[aid]["price"]),
                 "target_price":num(t["approved_price_rub"]), "title":before[aid]["title"],
                 "status_before":before[aid]["status"]})
        result["status"]="preflight_ok"
        save(result)
        if request["mode"]=="inspect":
            result["status"]="inspect_ok_no_writes";save(result);return
        if request["mode"]=="apply":
            for t in request["targets"]:
                aid=int(t["old_avito_id"])
                target=num(t["approved_price_rub"])
                if num(before[aid]["price"])==target:
                    continue
                reply=av._request("POST",f"/core/v1/items/{aid}/update_price",
                                  json={"price":int(target)},retries=1)
                check((reply.get("result") or {}).get("success") is True,
                      "API update not accepted "+str(aid))
                result["seller_price_writes"]+=1
                save(result)
        for attempt in range(6):
            if attempt:time.sleep(4)
            fresh=live()
            checks=[]
            for t in request["targets"]:
                aid=int(t["old_avito_id"])
                target=num(t["approved_price_rub"])
                old=fresh.get(aid)
                checks.append({"id":aid,"price":num(old.get("price")) if old else None,
                    "status":old.get("status") if old else "missing",
                    "matched":bool(old and old["status"]=="active" and num(old["price"])==target)})
            result["checks"]=checks
            result["summary"]={"verified":sum(x["matched"] for x in checks),"count":2}
            result["status"]="verified_2_of_2" if all(x["matched"] for x in checks) else "pending"
            save(result)
            if result["status"]=="verified_2_of_2":return
        raise RuntimeError("Live confirmation incomplete")
    except Exception as exc:
        result["status"]="blocked_or_partial"
        result["error"]={"type":type(exc).__name__,"message":str(exc)[:210]}
        save(result)
        raise
if __name__=="__main__":main()
