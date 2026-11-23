"""Safe Avito paid-tariff relaunch. Only append three specifically approved 6T30 transmission listings.
Other ads, prices, stock and media are immutable. Tools and EA888 require another category template.
"""
from __future__ import annotations
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone

import requests
from openpyxl import load_workbook
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import avito_client as av
import avito_feed as feed
from scripts.apply_dq200_prices_20261120 import gh, get_avito_asset, rename_asset, STABLE_AVITO_URL
from scripts.publish_avito_titles_20261121 import verified_download

REQUEST = ROOT / "data/avito_paid_relaunch_request.json"
BATCH = ROOT / "data/product_intake_v2/requests/leoparts-five-20261123.json"
FEED = ROOT / "data/avito_feed.xlsx"
REPORT = ROOT / "data/avito_paid_relaunch_result.json"
TARGETS = ("6t30-solenoids-gen2", "6t30-tcm-gen1", "6t30-tcm-gen2")
TOOL_SKUS = ("tool-dq200-acc-restore", "tool-dsg-pressure-hp")
PHOTO_LIMIT = 15 * 1024 * 1024


def require(ok, why):
    if not ok:
        raise ValueError(why)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def safe_api(path):
    try:
        return av._request("GET", path, retries=1)
    except Exception as exc:
        return {"_error": type(exc).__name__, "reason": str(exc)[:250]}


def row_map(ws):
    rows = {}
    for row in ws.iter_rows(min_row=5):
        sku = row[feed.COL_ID - 1].value
        if sku is None:
            continue
        sku = str(sku).strip()
        require(sku not in rows, "Duplicate feed Id " + sku)
        rows[sku] = row[0].row
    return rows


def validate_media(item):
    media = item["media"]
    require(len(media) == 1 and media[0]["source"] == "public_url", "Unexpected 6T30 media specification")
    m = media[0]
    r = requests.get(m["url"], timeout=90)
    require(r.status_code == 200 and len(r.content) < PHOTO_LIMIT, "Media fetch failed for " + item["sku"])
    actual = digest(r.content)
    require(actual == m["sha256"], "Approved SHA256 mismatch for " + item["sku"])
    with Image.open(BytesIO(r.content)) as im:
        w,h = im.size
        require(w >= 400 and h >= 400, "Media too small for "+item["sku"])
    return {"sha256": actual, "image_size": [w,h],
            "square_for_avito": abs(w-h) / max(w,h) <= 0.04, "url": m["url"]}


def candidate_row(ws, next_row, item, media_url, seller_address):
    sku = item["sku"]
    title = item["wb_title"]
    require("Gen2" in title if sku.endswith("gen2") else "Gen1" in title, "Product generation missing from title")
    kind = feed.classify_transmission_part_type(title)
    require(kind == "Электронное управление КПП", "Product is not a verified transmission electronic part: "+sku)
    require(len(title) <= 50, "Title longer than known Avito 50-character limit "+sku)
    # "Производитель" and "Номер OEM" omitted: 6T30 applies to several GM cars,
    # and the internal SKU is not a truthful automotive OEM part number.
    values = {
        feed.COL_ADDRESS: seller_address,
        feed.COL_ID: sku,
        feed.COL_CATEGORY: feed.CATEGORY_VALUE,
        feed.COL_DESCRIPTION: item["description"],
        feed.COL_IMAGE_URLS: media_url,
        feed.COL_TITLE: title,
        feed.COL_PRICE: int(item["prices"]["avito"]),
        feed.COL_GOODS_TYPE: feed.GOODS_TYPE_VALUE,
        feed.COL_AD_TYPE: feed.DEFAULT_AD_TYPE,
        feed.COL_PART_FOR: feed.PART_FOR_VALUE,
        feed.COL_PART_KIND: feed.PART_KIND_VALUE,
        feed.COL_TRANSMISSION_PART_TYPE: kind,
        feed.COL_CONDITION: feed.DEFAULT_CONDITION,
    }
    for col, value in values.items():
        ws.cell(row=next_row,column=col,value=value)


def approved_manifest():
    d = json.loads(BATCH.read_text(encoding="utf-8"))
    require(d.get("approved") is True and d.get("policy",{}).get("never_recalculate_approved_prices") is True,
            "Owner-approved five-SKU manifest required")
    return {x["sku"]: x for x in d["items"]}


def run():
    req = json.loads(REQUEST.read_text(encoding="utf-8"))
    mode=req.get("mode")
    require(mode in ("inspect","apply"), "Unknown execution mode")
    require(req.get("owner_confirmed_paid") is True and req.get("freeze_existing_ads") is True,
            "Owner tariff confirmation and immutable existing ads required")
    require(req.get("approved_new_skus") == list(TARGETS), "Approved SKU set changed")
    items = approved_manifest()
    result = {"checked_at_utc": datetime.now(timezone.utc).isoformat(), "mode": mode,
              "approved_new_skus": list(TARGETS), "existing_ad_price_writes": 0,
              "existing_ad_stock_writes": 0, "existing_ad_photo_writes": 0,
              "other_new_products": {sku:"blocked: separate Avito workshop equipment template needed"
                                     for sku in TOOL_SKUS},
              "engine_products":"blocked: separate engine parts template needed"}
    profile = safe_api("/autoload/v2/profile")
    result["autoload"] = {
        "api_access": "_error" not in profile,
        "enabled": profile.get("autoload_enabled"),
        "profile_error": profile.get("reason","")[:200],
        "feed_link_correct": STABLE_AVITO_URL in json.dumps(profile,ensure_ascii=False),
        "schedule_configured": bool(profile.get("schedule")),
    }
    last = safe_api("/autoload/v4/uploads/last_successful")
    result["last_successful_upload"] = {k: last.get(k) for k in
        ("upload_id","status","started_at","section_stats","feed_urls") if k in last}
    if "_error" in last:
        result["last_successful_upload"]["error"] = last["_error"]
    current = safe_api("/autoload/v4/uploads/current")
    result["current_upload"] = {k: current.get(k) for k in
        ("upload_id","status","started_at","section_stats") if k in current}
    try:
        live = av.list_all_items()
        result["live_listing_count"] = len(live)
    except Exception as exc:
        result["live_api_error"] = str(exc)[:200]
        live = []
    source = FEED.read_bytes()
    wb = load_workbook(BytesIO(source))
    ws = wb[feed.SHEET_ADS]
    existing = row_map(ws)
    result["feed_before"] = {"rows": len(existing), "sha256": digest(source)}
    result["already_in_feed"] = [s for s in TARGETS if s in existing]
    result["missing_from_feed"] = [s for s in TARGETS if s not in existing]
    result["other_relevant_feed_skus"] = {s:(s in existing) for s in
                ("6t30","Dq500","EA888gen3-filter-kit","vint-kollektora-ea888")}
    media_ok = {}
    for sku in TARGETS:
        try:
            media_ok[sku] = validate_media(items[sku])
        except Exception as exc:
            media_ok[sku] = {"error":type(exc).__name__, "reason":str(exc)[:180]}
    result["approved_media_verification"] = media_ok
    suspect={}
    for sku in TARGETS:
        title = items[sku]["wb_title"]
        prefix = "Gen2" if sku.endswith("gen2") else "Gen1"
        matches=[]
        for x in live:
            actual=str(x.get("title") or "")
            t=actual.lower()
            if actual == title or ("6t30" in t and prefix.lower() in t and
                (("соленоид" in t and "блок" not in t) if "solenoids" in sku else "блок" in t)):
                matches.append({"id":x.get("id"),"title":actual,"status":x.get("status")})
        suspect[sku]=matches[:10]
    result["possible_live_duplicates"] = suspect
    require(mode != "apply" or result["autoload"]["enabled"] is True,
            "Avito auto-upload not confirmed enabled via paid profile")
    if mode != "apply":
        result["status"]="inspected_no_writes"
        return result
    require(not any("error" in media_ok[s] for s in TARGETS if s not in existing),
            "Approved photos unavailable or mismatched: refusing any publication")
    # Avoid mixing image aspect variants. Upload only exact approved originals
    # which passed the independent content SHA256 test; aspect is reported for review.
    require(not any(suspect[s] for s in TARGETS if s not in existing),
            "Possible existing live advertisement; manual mapping required to avoid duplicate")
    require(result["autoload"]["feed_link_correct"],
            "Autoload profile does not reference our stable feed URL; no overwrite")
    require(result["autoload"]["schedule_configured"],
            "Autoload schedule absent; no automatic publication")
    release, old_asset, published = get_avito_asset()
    result["public_feed_before_sha256"]=digest(published)
    require(source == published, "GitHub source feed differs from the live stable release; refuse clobber")
    if not result["missing_from_feed"]:
        result["status"]="already_in_feed_no_writes"
        return result
    # Idempotent new rows only; original cells of every old row stay exactly as loaded.
    old_all = {tab.title:list(tab.values) for tab in wb}
    old_prices = {sku:ws.cell(row,col=feed.COL_PRICE).value for sku,row in existing.items()}
    seller_address = str(ws.cell(5,feed.COL_ADDRESS).value or "").strip()
    require(seller_address, "Cannot infer Avito seller address")
    next_row=ws.max_row+1
    for sku in result["missing_from_feed"]:
        candidate_row(ws,next_row,items[sku],media_ok[sku]["url"],seller_address)
        next_row+=1
    after_old = {tab.title:list(tab.values)[:len(old_all[tab.title])] for tab in wb}
    require(after_old==old_all, "Existing feed rows/fields were unexpectedly edited")
    require(all(ws.cell(row,col=feed.COL_PRICE).value==price for sku,row in existing.items()
                for price in [old_prices[sku]]), "Existing seller price changed")
    buf=BytesIO()
    wb.save(buf)
    proposed=buf.getvalue()
    assert proposed != published
    suffix=digest(proposed)[:12]
    tempname="avito_feed.6t30_three_"+suffix+".xlsx"
    stage=gh("POST",release["upload_url"].split("{",1)[0],params={"name":tempname},
             data=proposed,headers={"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}).json()
    verified_download(stage["browser_download_url"],proposed)
    _,now_asset,now_bytes=get_avito_asset()
    require(now_asset["id"]==old_asset["id"] and now_bytes==published,
            "Concurrent Avito feed publication changed; no replacement")
    backup="avito_feed.before_paid_relaunch_"+str(old_asset["id"])+".xlsx"
    rename_asset(old_asset["id"],backup)
    try:
        rename_asset(stage["id"],"avito_feed.xlsx")
    except Exception:
        rename_asset(old_asset["id"],"avito_feed.xlsx")
        raise
    verified_download(STABLE_AVITO_URL,proposed)
    FEED.write_bytes(proposed)
    result["status"]="stable_feed_published_pending_avito_import"
    result["published_feed_sha256"]=digest(proposed)
    result["published_rows"]=len(row_map(load_workbook(BytesIO(proposed))[feed.SHEET_ADS]))
    result["newly_appended_skus"]=result["missing_from_feed"]
    result["backup_asset"]=backup
    result["avito_autoload_api_triggered"]=False
    result["note"]="The existing Avito scheduled importer must process the new stable feed; API 200 is not Avito moderation."
    return result


def main():
    report={"status":"fatal"}
    try:
        report=run()
        print("AVITO_PAID_RELAUNCH",json.dumps({"status":report["status"],
            "missing":report.get("missing_from_feed"),
            "autoupload":report.get("autoload")},ensure_ascii=False),flush=True)
    except Exception as exc:
        report["error_type"]=type(exc).__name__
        report["error"]=str(exc)[:600]
        print("AVITO_PAID_RELAUNCH_BLOCKED",report["error_type"],report["error"],flush=True)
        raise
    finally:
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")


if __name__=="__main__":
    main()
