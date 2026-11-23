"""Guarded alignment of 13 legacy Avito listings to canonical LEOPARTS prices.

Owner-approved 2026-11-24, EXACT Avito IDs only. No feeds, main ads,
catalog workbooks, site, stock, or photos are changed. Fails closed if title,
SKU, existing target main price or old price changed.
Modes: inspect, apply, verify.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import json, sys, time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import avito_client as av
from scripts.related_price_inspect_20261122 import parse

REQ = ROOT / "data/avito_legacy_13_prices_20261124_request.json"
OUT = ROOT / "data/avito_legacy_13_prices_20261124_result.json"
AUDIT = ROOT / "data/avito_price_audit_20261124.json"
INDEX = ROOT / "data/avito_catalog_listing_index_20261124.json"
POLICY = ROOT / "data/pricing_policy.json"
OVERRIDES = ROOT / "data/avito_site_price_overrides.xlsx"
SOURCE_GUARDS = (
    "data/master_control.xlsx", "data/kits.xlsx",
    "data/avito_site_price_overrides.xlsx", "data/avito_feed.xlsx",
    "data/avito_links.json", "data/pricing_policy.json",
)

def utc():return datetime.now(timezone.utc).isoformat()
def check(expr, message):
    if not expr:
        raise RuntimeError("GUARD_BLOCKED: " + message)
def digest(path):
    return sha256((ROOT/path).read_bytes()).hexdigest()
def money(v):
    try:return round(float(v),2)
    except (ValueError, TypeError):return None
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def save(result):
    result["checked_utc"]=utc()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("LEGACY_13_PRICE_ALIGNMENT",json.dumps({
        "status":result.get("status"),"mode":result.get("mode"),
        "counts":result.get("counts"),"error":result.get("error")},
        ensure_ascii=False),flush=True)

def overrides():
    sheets=parse(OVERRIDES)
    ws=next((x["rows"] for x in sheets if x["name"]=="Цены Avito и сайт"),[])
    check(bool(ws),"Avito source overrides sheet not found")
    result={}
    for row in ws[1:]:
        if not row or not row[0]: continue
        sku=str(row[0]).strip()
        check(sku not in result,"duplicate source SKU "+sku)
        result[sku]=money(row[1] if len(row)>1 else None)
    return result

def read_live():
    live=av.list_all_items(statuses=["active"],per_page=100,max_pages=12)
    rows={int(x["id"]):x for x in live if x.get("id") is not None}
    check(len(rows)==len(live),"Duplicate live Avito ID")
    return rows

def preflight():
    request=load(REQ)
    audit=load(AUDIT)
    index=load(INDEX)
    policy=load(POLICY)
    check(request.get("mode") in ("inspect","apply","verify"),"Unknown mode")
    check(request.get("owner_approved") is True and
          request.get("target_channel")=="avito" and
          request.get("price_changes_only") is True and
          request.get("freeze_main_prices") is True and
          request.get("stock_unchanged") is True and
          request.get("site_prices_unchanged") is True and
          request.get("feed_unchanged") is True and
          request.get("skip_ambiguous_22") is True and
          request.get("expected_count")==13 and
          len(request.get("targets") or [])==13,
          "Exact owner authorization is missing")
    check(policy.get("mode")=="manual_prices_only" and
          policy.get("automatic_avito_site_repricing") is False,
          "Automatic pricing freeze was changed")
    check(audit.get("mode")=="read_only" and audit.get("live_api_error") is None and
          not audit.get("binding_errors"), "No audited source")
    check(index.get("old_active_ads_grouped")==14 and
          index.get("old_active_ads_pending_review")==22,
          "Legacy catalog mapping changed")
    bysku={x["sku"]:x for x in audit["products"]}
    canonical={x["sku"]:x for x in index["groups"]}
    links=load(ROOT/"data/avito_links.json")
    linked={int(v) for k,v in links.items() if not k.startswith("_")}
    source_price=overrides()
    old_ids=[int(t["legacy_avito_id"]) for t in request["targets"]]
    check(len(set(old_ids))==13,"Duplicate legacy ID")
    check(not (linked & set(old_ids)),"Target already taken over by feed")
    check(not ({t["primary_avito_id"] for t in request["targets"]} & set(old_ids)),
          "Target would modify a canonical main Avito listing")
    check(len({(t["sku"],t["legacy_avito_id"]) for t in request["targets"]})==13,"Duplicate pair")
    list_all=read_live()
    result={}
    for t in request["targets"]:
        sku=t["sku"]; aid=int(t["legacy_avito_id"]); main=int(t["primary_avito_id"])
        expected=money(t["previous_live_price_rub"]); target=money(t["approved_price_rub"])
        check(sku in canonical and sku in bysku and sku in source_price,
              "Missing authoritative main catalog SKU "+sku)
        group=canonical[sku]
        check(any(int(x["avito_id"])==aid for x in group["legacy_secondary_ads"]),
              "Legacy ad absent from review group "+str(aid))
        check(group["primary_avito_id"]==main and
              money(group["configured_price_rub"])==target and
              money(bysku[sku]["avito_cfg"])==target and
              money(source_price[sku])==target,
              "Main catalog approved price differs "+sku)
        check(aid in list_all and main in list_all,
              "Missing active listing "+str(aid)+"/"+str(main))
        old=list_all[aid]; p=list_all[main]
        check(old.get("status")=="active" and p.get("status")=="active",
              "Listing not active "+str(aid))
        check(str(old.get("title") or "").strip()==str(t["legacy_title"]).strip(),
              "Legacy ad title changed "+str(aid))
        check(money(p.get("price"))==target and
              money(t["primary_live_price_rub"])==target,
              "Main canonical listing price not approved "+sku)
        liveprice=money(old.get("price"))
        check(liveprice in (expected,target),
              "Legacy ad price changed outside approval: "+str(aid))
        check(expected is not None and target is not None and target>0,
              "Invalid price "+str(aid))
        result[str(aid)]={"sku":sku,"old_price":expected,"approved_price":target,
                         "title":str(old.get("title"))[:140],"primary_id":main,
                         "current_price":liveprice,"status":old.get("status"),
                         "url":old.get("url")}
    check(sum(int(x["current_price"]!=x["approved_price"]) for x in result.values())<=13,
          "Unexpected legacy target set")
    return request, result, {k:digest(k) for k in SOURCE_GUARDS}, list_all

def verify_snapshot(before,guards):
    # Re-read ALL active seller offers; verify each old price and main price.
    fresh=read_live()
    checks={}
    for aid, t in before.items():
        num=int(aid)
        old=fresh.get(num)
        main=fresh.get(t["primary_id"])
        checks[aid]={"sku":t["sku"],
                     "expected":t["approved_price"],
                     "before":t["current_price"],
                     "after":money(old.get("price")) if old else None,
                     "status":old.get("status") if old else "missing",
                     "primary_price_after":money(main.get("price")) if main else None,
                     "matches_target": bool(old and old.get("status")=="active" and
                                            money(old.get("price"))==t["approved_price"] and
                                            main and money(main.get("price"))==t["approved_price"])}
    sources={k:digest(k)==value for k,value in guards.items()}
    changed_non_targets=[
        {"id":k,"before":money(v.get("price")),
         "after":money(fresh.get(k,{}).get("price"))}
        for k,v in fresh.items() if k not in {int(a) for a in before}
        and k in before_all and money(before_all[k].get("price"))!=money(v.get("price"))
    ]
    return checks,sources,changed_non_targets

def main():
    global before_all
    result={"mode":None,"status":"starting","seller_price_writes":0,
            "site_writes":0,"feed_writes":0,"stock_writes":0,"main_listing_price_writes":0,
            "targets":{},"price_update_receipts":{}}
    try:
        req,targets,guards,before_all=preflight()
        result.update({"mode":req["mode"],"status":"preflight_passed",
                       "targets":targets,"guarded_source_files":list(guards)})
        result["counts"]={"approved":len(targets),
                          "already_at_target":sum(x["current_price"]==x["approved_price"] for x in targets.values()),
                          "need_change":sum(x["current_price"]!=x["approved_price"] for x in targets.values())}
        save(result)
        if req["mode"]=="inspect":
            result["status"]="inspect_ok_no_seller_writes";save(result);return
        if req["mode"]=="apply":
            for aid,t in targets.items():
                if t["current_price"]==t["approved_price"]:
                    result["price_update_receipts"][aid]={"status":"already_correct"}
                    continue
                try:
                    answer=av._request("POST",f"/core/v1/items/{aid}/update_price",
                                       json={"price":int(t["approved_price"])},retries=1)
                    good=bool((answer.get("result") or {}).get("success") is True)
                    result["price_update_receipts"][aid]={"status":"accepted" if good else "not_confirmed"}
                    if good:
                        result["seller_price_writes"]+=1
                except Exception as exc:
                    result["price_update_receipts"][aid]={"status":"error",
                                                          "error_type":type(exc).__name__}
                save(result)
        verified={}
        safe={}
        non_target=[]
        for attempt in range(1,7):
            if attempt>1:time.sleep(4)
            verified,safe,non_target=verify_snapshot(targets,guards)
            result["live_verify"]=verified
            result["source_files_unchanged"]=safe
            result["other_live_price_changes_observed"]=non_target[:10]
            result["counts"].update(
                {"verified":sum(bool(x["matches_target"]) for x in verified.values()),
                 "unverified":sum(not x["matches_target"] for x in verified.values()),
                 "source_unchanged":all(safe.values())})
            result["status"]="verified_13_of_13" if (
                all(x["matches_target"] for x in verified.values())
                and all(safe.values()) and not non_target
                ) else "pending_or_partial_verification"
            save(result)
            if result["status"]=="verified_13_of_13":break
        if result["status"]!="verified_13_of_13":
            raise RuntimeError("One or more guarded old prices not confirmed; inspect result")
    except Exception as exc:
        result["status"]="blocked_or_partial_review_required"
        result["error"]={"type":type(exc).__name__,"message":str(exc)[:260]}
        save(result)
        raise

if __name__=="__main__":main()
