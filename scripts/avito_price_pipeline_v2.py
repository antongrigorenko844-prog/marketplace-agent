"""LEOPARTS generic Avito approved-price pipeline v2.

One owner-signed JSON manifest created from their Excel: inspect first, apply
only after successful inspect. Identity: Avito ID (not title or mutable SKU).
- Stage Avito-only main source B prices, D site freezes and feed AH prices.
- Stage title X and per-channel persistent text only when user approved.
- Validate effective Tilda prices BEFORE any release publication (including the
  independent site-only override JSON).
- Publish exact feed atomically with rollback backup and preserve GitHub source.
- Execute price-only seller calls in bounded concurrent workers; one readback.
- Old (manual) ads are updated by their Avito IDs, not forcibly taken over.
- Never change Ozon/WB/YM, stock, photos, SKU identity or purchase costs.

No artificial price multipliers or unattended formula.
"""
from __future__ import annotations
from pathlib import Path
from hashlib import sha256
from io import BytesIO
from tempfile import NamedTemporaryFile
from datetime import datetime,timezone
from typing import Any
import json,sys,subprocess

from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import site_pricing
import avito_client as av
from scripts.apply_dq200_prices_20261120 import get_avito_asset
from scripts.avito_bulk_price_engine import (
    PriceEdit, preflight, apply, AvitoPriceGuardError,
    assert_site_prices_unchanged, RateGate)
from scripts.apply_avito_owner_excel_20261124 import (
    data_rows, unchanged_except, publish_feed)

REQ=ROOT/"data/avito_price_pipeline_v2_request.json"
OUT=ROOT/"data/avito_price_pipeline_v2_result.json"
OVERRIDE=ROOT/"data/avito_site_price_overrides.xlsx"
FEED=ROOT/"data/avito_feed.xlsx"
CONTENT=ROOT/"data/content_manifest.json"
AUD=ROOT/"data/avito_price_audit_20261124.json"
KEEP_NONCOMMERCIAL=("stock","photos","purchase_cost","site","marketplace")
EXPECTED_TARGET_KEYS={"avito_id","kind","sku","expected_price",
                      "approved_price","expected_title","expected_site_price",
                      "approved_title","approved_description"}

def guard(ok,why):
    if not ok:raise AvitoPriceGuardError(why)
def read(path):return json.loads(path.read_text(encoding="utf8"))
def digest(raw):return sha256(raw).hexdigest()
def money(x):
    try:return round(float(x),2)
    except(TypeError,ValueError):return None
def save(report):
    report["checked_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("AVITO_PRICE_PIPELINE_V2",json.dumps({
      "status":report.get("status"),"mode":report.get("mode"),
      "counts":report.get("counts"),"error":report.get("error")},
      ensure_ascii=False),flush=True)

def seller():
    items=av.list_all_items(per_page=25,max_pages=45)
    by_id={int(row["id"]):row for row in items if row.get("id") is not None}
    guard(len(by_id)==len(items) and len(by_id)>=100,
          "Live seller API list missing items or duplicate Avito IDs")
    return by_id

def validate_manifest(manifest):
    guard(manifest.get("schema_version")==2 and
          manifest.get("mode") in ("inspect","apply") and
          manifest.get("owner_approved") is True and
          manifest.get("channel")=="avito" and
          manifest.get("preserve_other_channels") is True and
          manifest.get("preserve_stock") is True and
          manifest.get("preserve_photos") is True and
          manifest.get("preserve_costs") is True and
          manifest.get("freeze_effective_site_prices") is True,
          "Missing owner-approved Avito-only scope and safety flags")
    items=manifest.get("targets") or []
    guard(isinstance(items,list) and len(items)>0 and len(items)<=250,
          "Target list invalid, empty, or unexpectedly large")
    seen=set()
    for item in items:
        guard(isinstance(item,dict) and
              not (set(item)-EXPECTED_TARGET_KEYS),
              "Unexpected/unsafe target fields")
        ident=item.get("avito_id")
        guard(type(ident) is int and ident>0 and ident not in seen,
              "Missing/duplicate Avito listing ID")
        seen.add(ident)
        guard(item.get("kind") in ("main","legacy"),
              f"{ident}: must be main or legacy")
        guard(type(item.get("expected_price")) is int
              and type(item.get("approved_price")) is int
              and 1<=item["approved_price"]<=500000
              and 1<=item["expected_price"]<=500000,
              f"{ident}: invalid seller prices")
        guard(isinstance(item.get("expected_title"),str)
              and item["expected_title"].strip(),
              f"{ident}: original title is required for identity check")
        if item["kind"]=="main":
            guard(isinstance(item.get("sku"),str) and item["sku"].strip(),
                  f"{ident}: main SKU missing")
            guard(money(item.get("expected_site_price")) is not None,
                  f"{ident}: website price baseline is required")
        else:
            guard(not item.get("approved_title") and
                  not item.get("approved_description"),
                  f"{ident}: old manual ad text changes require separate editorial review")
        for field in ("approved_title","approved_description"):
            if item.get(field) is not None:
                guard(isinstance(item[field],str) and item[field].strip(),
                      f"{ident}: blank text request")
        if item.get("approved_title"):
            guard(len(item["approved_title"])<=50,
                  f"{ident}: title above 50-character Avito limit")
    policy=read(ROOT/"data/pricing_policy.json")
    guard(policy.get("mode")=="manual_prices_only" and
          policy.get("automatic_avito_site_repricing") is False,
          "Automatic reprice freeze changed")
    return items

def patch_catalog(manifest,live):
    items=validate_manifest(manifest)
    audit=read(AUD)
    primary={int(x["mapped"]["id"]):x for x in audit["products"]
             if (x.get("mapped") or {}).get("status")=="active"
             and (x.get("mapped") or {}).get("id")}
    approved=[PriceEdit(x["avito_id"],x["expected_price"],x["approved_price"],
             expected_title=x["expected_title"] if not x.get("approved_title") else None,
             sku=x.get("sku")) for x in items]
    plan=preflight(approved,live)
    for item in items:
        aid=item["avito_id"]
        guard(aid in live and live[aid]["status"]=="active",
              "Missing active Avito ID")
        title=live[aid].get("title")
        accepted_titles=(item["expected_title"],item.get("approved_title"))
        guard(title in accepted_titles,
              f"{aid}: live ad title was changed outside owner approval")
        if item["kind"]=="main":
            guard(aid in primary and primary[aid]["sku"]==item["sku"],
                  f"{aid}: main Avito ID not mapped to exact canonical SKU")
        else:
            guard(aid not in primary,
                  f"{aid}: legacy ad is now a primary listing; refuse take-over")

    ob=load_workbook(OVERRIDE)
    ow,ois=data_rows(ob,"Цены Avito и сайт",1,2)
    fb=load_workbook(FEED)
    fw,fis=data_rows(fb,"Объявления",4,5)
    content=read(CONTENT)
    guard(content.get("version")==1 and isinstance(content.get("products"),dict),
          "Content manifest invalid")
    updated_content=json.loads(json.dumps(content,ensure_ascii=False))
    # Read actual website values *before* touching the Avito workbook.
    site_before=site_pricing.load_price_overrides(channel="site")
    site_frozen={}
    allowed_ov=set()
    allowed_feed=set()
    ov_modified=False
    feed_modified=False
    text_modified=False
    main_count=0
    legacy_count=0
    source_updates=[]
    for item in items:
        aid=item["avito_id"]
        if item["kind"]=="legacy":
            legacy_count+=1
            continue
        main_count+=1
        sku=item["sku"]
        source=primary[aid]
        alias=source.get("source_feed_id") or sku
        guard(sku in ois and alias in fis,
              f"{aid}: missing override/feed row for {sku}/{alias}")
        oi,fi=ois[sku],fis[alias]
        before_price=money(ow.cell(oi,2).value)
        old_feed=money(fw.cell(fi,34).value)
        desired=item["approved_price"]
        expected=item["expected_price"]
        guard(before_price in (expected,desired) and
              old_feed in (expected,desired),
              f"{aid}: canonical price drift ({before_price},{old_feed})")
        site=money(site_before.get(sku))
        guard(site is not None and site==money(item["expected_site_price"]),
              f"{aid}: site baseline drift; expected "+
              f"{item['expected_site_price']}, actual {site}")
        site_frozen[sku]=site
        # Existing D may be blank (site inherits Avito B). Explicitly preserve
        # the old effective B/D fallback when changing B, before any publish.
        site_workbook=money(ow.cell(oi,4).value)
        if site_workbook is None:
            site_workbook=before_price
        guard(site_workbook is not None and site_workbook>0,
              f"{aid}: no valid B/D site fallback")
        for coord,col,value in (("B"+str(oi),2,desired),
                                ("D"+str(oi),4,site_workbook)):
            if money(ow.cell(oi,col).value)!=value:
                ov_modified=True
                ow.cell(oi,col).value=int(value)
            allowed_ov.add(("Цены Avito и сайт",coord))
        if old_feed!=desired:
            feed_modified=True
            fw.cell(fi,34).value=desired
        allowed_feed.add(("Объявления","AH"+str(fi)))
        if item.get("approved_title"):
            title=item["approved_title"]
            old_title=str(fw.cell(fi,24).value or "")
            guard(old_title in (item["expected_title"],title),
                  f"{aid}: canonical feed title drift")
            if old_title!=title:
                feed_modified=True
                fw.cell(fi,24).value=title
            allowed_feed.add(("Объявления","X"+str(fi)))
        if item.get("approved_description"):
            des=item["approved_description"]
            # There may be a different approved old description; its exact
            # value was not supplied, so only change when user explicitly
            # provided an editorial approval.
            if fw.cell(fi,17).value!=des:
                fw.cell(fi,17).value=des
                feed_modified=True
            allowed_feed.add(("Объявления","Q"+str(fi)))
        if item.get("approved_title") or item.get("approved_description"):
            original=content["products"].get(sku,{}).get("text",{}).get("avito",{})
            target=updated_content["products"].setdefault(sku,{}).setdefault("text",{}).setdefault("avito",{})
            for field,key in (("approved_title","title"),
                              ("approved_description","description")):
                if item.get(field):
                    if original.get(key) not in (None,item["expected_title"],item[field]) and key=="title":
                        raise AvitoPriceGuardError(f"{aid}: content manifest title drift")
                    if target.get(key)!=item[field]:
                        target[key]=item[field]
                        text_modified=True
        source_updates.append({"sku":sku,"avito_id":aid,"old":before_price,
                               "new":desired,"site_preserved":site})
    if main_count:
        guard(len(site_frozen)==len({x["sku"] for x in items if x["kind"]=="main"}),
              "Duplicate canonical SKU changes from separate seller IDs")
    changes={}
    for path,book,allowed,modified in (
        (OVERRIDE,ob,allowed_ov,ov_modified),
        (FEED,fb,allowed_feed,feed_modified),
    ):
        if not modified:
            changes[path]=path.read_bytes()
            continue
        buf=BytesIO();book.save(buf)
        raw=buf.getvalue()
        guard(len(raw)>1000,"Truncated staged workbook")
        unchanged_except(load_workbook(BytesIO(path.read_bytes())),
                         load_workbook(BytesIO(raw)),allowed)
        changes[path]=raw
    changes[CONTENT]=(
        (json.dumps(updated_content,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
        if text_modified else CONTENT.read_bytes()
    )
    # Strong BEFORE-PUBLISH invariant: check actual effective site prices by
    # loading the *staged* workbook, applying site-only JSON overrides.
    if main_count:
        with NamedTemporaryFile(suffix=".xlsx",delete=True) as tmp:
            tmp.write(changes[OVERRIDE])
            tmp.flush()
            stage_site=site_pricing.load_price_overrides(path=tmp.name,channel="site")
        assert_site_prices_unchanged(site_before,stage_site,list(site_frozen))
    changed=[str(path.relative_to(ROOT)) for path,raw in changes.items()
             if path.read_bytes()!=raw]
    return {
        "source_updates":source_updates,
        "price_edits":approved,
        "plan":plan,
        "site_frozen":site_frozen,
        "changes":changes,
        "source_modified_files":changed,
        "main_count":main_count,
        "legacy_count":legacy_count,
        "titles_to_verify":{x["avito_id"]:x["approved_title"] for x in items
                            if x.get("approved_title")},
        "read_before":live,
    }

def persist(changes,files):
    if not files:return
    for path,blob in changes.items():
        if str(path.relative_to(ROOT)) in files:
            path.write_bytes(blob)
    subprocess.run(["bash",".github/scripts/save_data.sh",*files],
                   cwd=ROOT,check=True,timeout=180)

def run():
    report={"status":"starting","seller_price_writes":0,
            "site_changes":0,"marketplace_changes":0,
            "stock_changes":0,"media_changes":0,
            "purchase_cost_changes":0}
    try:
        req=read(REQ)
        live=seller()
        prepared=patch_catalog(req,live)
        report.update({"mode":req["mode"],"status":"preflight_passed",
             "counts":{"requested":len(prepared["price_edits"]),
                       "main":prepared["main_count"],
                       "legacy":prepared["legacy_count"],
                       "source_changed_files":len(prepared["source_modified_files"])},
             "source_modified_files":prepared["source_modified_files"],
             "site_prices_verified":prepared["site_frozen"],
             "source_changes":prepared["source_updates"],
             "feed_sha_before":digest(FEED.read_bytes()),
             "feed_sha_after":digest(prepared["changes"][FEED])})
        if req["mode"]=="inspect":
            report["status"]="inspect_ok_no_seller_or_source_writes"
            save(report)
            return report

        # No mutation before this point. The full preflight includes site
        # baseline, full sheet identity, exact IDs, current prices, approved text.
        _,_,published=get_avito_asset()
        guard(published==FEED.read_bytes(),
              "Stable GitHub Release feed differs from repository")
        modified=prepared["source_modified_files"]
        if "data/avito_feed.xlsx" in modified:
            report["feed_publish"]=publish_feed(prepared["changes"][FEED])
        else:
            report["feed_publish"]={"status":"unchanged_verified"}
        persist(prepared["changes"],modified)
        report["sources_committed"]=True
        save(report)
        # Re-evaluate actual site prices after source commits as a second
        # line of defense, before making any seller-price API calls.
        actual_site=site_pricing.load_price_overrides(channel="site")
        assert_site_prices_unchanged(prepared["site_frozen"],actual_site,
                                     list(prepared["site_frozen"]))
        av._get_token()  # warm shared token cache outside threads
        def post(aid,price):
            return av._request(
                "POST",f"/core/v1/items/{aid}/update_price",
                json={"price":price},retries=2)
        def checkpoint(snapshot):
            report["progress"]={"sent":snapshot["sent"],
                                "accepted":snapshot["accepted"],
                                "verified":snapshot["verified"]}
            save(report)
        bulk=apply(
            prepared["price_edits"],prepared["read_before"],
            owner_approved=True,mode="apply",update_fn=post,
            read_live_fn=seller,checkpoint=checkpoint,
            workers=4,rate_gate=RateGate(requests_per_minute=100))
        report["bulk"]=bulk
        report["seller_price_writes"]=bulk["accepted"]
        report["counts"].update({"verified":bulk["verified"],
                                  "unverified":bulk["unverified"],
                                  "seller_requests":bulk["sent"]})
        if prepared["titles_to_verify"]:
            after=seller()
            report["titles"]={
                str(aid):{"expected":title,
                          "live":after.get(aid,{}).get("title"),
                          "verified":after.get(aid,{}).get("title")==title,
                          "feed_published":True}
                for aid,title in prepared["titles_to_verify"].items()}
        report["status"]="seller_prices_verified" if bulk["unverified"]==0 else \
                         "partial_seller_prices_need_review"
        save(report)
        guard(bulk["unverified"]==0,"Some seller price changes unconfirmed")
        return report
    except Exception as exc:
        report["status"]="blocked_or_partial_review_required"
        report["error"]={"type":type(exc).__name__,"message":str(exc)[:300]}
        save(report)
        raise

if __name__=="__main__":
    run()
