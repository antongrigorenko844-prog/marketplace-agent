"""Owner-approved price-only two DQ200 reinforced plate listings #7 and #8.

Owner verified both old ads contain ONLY a reinforced hydraulic plate.
The #8 third-photo removal is a SEPARATE pending task; this script changes NO photos.
No writes to canonical listing, kits, site, feed or stock.
"""
from __future__ import annotations
from datetime import datetime, timezone
from pathlib import Path
import json,sys,time
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.related_price_inspect_20261122 import parse
import avito_client as av

REQ=ROOT/"data/avito_two_reinforced_plates_price_request_20261124.json"
OUT=ROOT/"data/avito_two_reinforced_plates_price_result_20261124.json"
REG=ROOT/"data/avito_legacy_catalog_candidates_20261124.json"
AUD=ROOT/"data/avito_price_audit_20261124.json"
OLD={4349334275:"Плита DQ200 0am 0cw (усиленная гидроплита DSG 7)",
     4220734432:"Гидроплита dsg 7 dq200 усиленная ремкомплект dsg7"}
MAIN_ID=8385926244
SKU="0am325066ae"  # reinforced hydraulic plate only; NOT a plate+gaskets kit
BEFORE=6490
AFTER=5350

def verify(b,msg):
    if not b:raise RuntimeError("GUARD_BLOCKED: "+msg)
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def money(val):
    try:return round(float(val),2)
    except (TypeError,ValueError):return None
def snapshot():
    # Avito API caps pagination: 25 instead of 100, all statuses, 40 pages.
    arr=av.list_all_items(per_page=25,max_pages=40)
    data={int(x["id"]):x for x in arr if x.get("id") is not None}
    verify(len(arr)==len(data),"Duplicate IDs in seller list")
    return data
def authoritative_source_price():
    book=parse(str(ROOT/"data/avito_site_price_overrides.xlsx"))
    ws=next((s["rows"] for s in book if s["name"]=="Цены Avito и сайт"),[])
    rows=[r for r in ws[1:] if r and str(r[0]).strip()==SKU]
    verify(len(rows)==1,"Canonical plate override missing or duplicated")
    return money(rows[0][1])
def preflight():
    request=load(REQ)
    verify(request.get("mode") in ("inspect","apply") and request.get("owner_approved") is True
           and request.get("price_only") is True
           and request.get("legacy_ids")==sorted(OLD)
           and request.get("expected_price")==BEFORE and request.get("new_price")==AFTER
           and request.get("canonical_sku")==SKU and request.get("canonical_main_id")==MAIN_ID
           and request.get("change_canonical") is False and request.get("change_other_kits") is False
           and request.get("change_feed") is False and request.get("change_site") is False
           and request.get("change_stock") is False and request.get("create_ads") is False,
           "Approval scope incorrect")
    policy=load(ROOT/"data/pricing_policy.json")
    verify(policy.get("mode")=="manual_prices_only" and
           policy.get("automatic_avito_site_repricing") is False,"Pricing policy unfrozen")
    registry=load(REG);rows={r["avito_id"]:r for r in registry["entries"]}
    for aid in OLD:
        o=rows.get(aid)
        verify(o is not None and o.get("owner_information_status")=="composition_verified"
               and o.get("owner_price_instruction")=="sync_to_main_catalog_0am325066ae_5350"
               and o.get("owner_confirmed_related_master_skus")==[SKU]
               and o.get("owner_instruction_keep_old_ad") is True,
               "Owner-only reinforced plate composition not confirmed "+str(aid))
    aud=load(AUD)
    canonical=[p for p in aud["products"] if p["sku"]==SKU]
    verify(len(canonical)==1 and money(canonical[0]["avito_cfg"])==AFTER
           and canonical[0]["mapped"].get("id")==MAIN_ID
           and authoritative_source_price()==AFTER,
           "Canonical plate source price changed")
    links=load(ROOT/"data/avito_links.json")
    managed={int(v) for k,v in links.items() if not k.startswith("_")}
    verify(not managed.intersection(OLD),"One legacy ID controlled by feed")
    live=snapshot()
    verify(MAIN_ID in live and money(live[MAIN_ID].get("price"))==AFTER
           and live[MAIN_ID].get("status")=="active","Canonical active listing changed")
    for aid,title in OLD.items():
        verify(aid in live and live[aid].get("status")=="active",
               "Old listing missing/inactive "+str(aid))
        verify(live[aid].get("title")==title,"Old title differs "+str(aid))
        verify(money(live[aid].get("price")) in (BEFORE,AFTER),
               "Old price changed outside approval "+str(aid))
    return request,live

def save(r):
    r["checked_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("DQ200_REINFORCED_PLATE_LEGACY_PRICE",json.dumps({k:r.get(k) for k in
          ("mode","status","seller_price_writes","checks","error")},ensure_ascii=False),flush=True)

def main():
    report={"mode":None,"status":"started","seller_price_writes":0,
            "main_price_writes":0,"other_kit_price_writes":0,
            "site_changes":0,"feed_changes":0,"stock_changes":0,"new_ads":0,
            "targets":{},"receipts":{}}
    try:
        req,live=preflight()
        report["mode"]=req["mode"]
        report["targets"]={str(aid):{"before":money(live[aid].get("price")),
             "new":AFTER,"status":live[aid]["status"],"title":live[aid]["title"]} for aid in OLD}
        report["status"]="preflight_ok";save(report)
        if req["mode"]=="inspect":
            report["status"]="inspect_ok_no_writes";save(report);return
        if req["mode"]=="apply":
            for aid in OLD:
                if money(live[aid].get("price"))==AFTER:
                    report["receipts"][str(aid)]="already_correct"
                    continue
                response=av._request("POST",f"/core/v1/items/{aid}/update_price",
                                     json={"price":AFTER},retries=1)
                ok=bool((response.get("result") or {}).get("success") is True)
                report["receipts"][str(aid)]="accepted" if ok else "not_confirmed"
                if ok:report["seller_price_writes"]+=1
                save(report)
                verify(ok,"Seller update was not accepted for "+str(aid))
        for n in range(7):
            if n:time.sleep(4)
            latest=snapshot()
            checks={}
            for aid in OLD:
                item=latest.get(aid,{})
                checks[str(aid)]={"status":item.get("status","missing"),
                   "price":money(item.get("price")),
                   "matched":item.get("status")=="active" and money(item.get("price"))==AFTER}
            primary=latest.get(MAIN_ID,{})
            report["checks"]=checks
            report["canonical_price_after"]=money(primary.get("price"))
            good=(all(x["matched"] for x in checks.values()) and
                  primary.get("status")=="active" and money(primary.get("price"))==AFTER)
            report["status"]="verified_2_of_2" if good else "pending"
            save(report)
            if good:return
        raise RuntimeError("Old live price readback not confirmed")
    except Exception as ex:
        report["status"]="blocked_or_partial"
        report["error"]={"type":type(ex).__name__,"message":str(ex)[:240]}
        save(report)
        raise

if __name__=="__main__":main()
