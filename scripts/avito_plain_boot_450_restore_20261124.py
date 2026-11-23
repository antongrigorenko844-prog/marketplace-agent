"""Emergency corrective price-only rollback for non-original DQ200 single boot.

Owner explicitly objects to 950 Avito price; historical 450 on two old ads,
marketplace price remains untouched. Permanent fix updates Avito feed and
Avito override B column, preserves the site column D and every other SKU.
Two stages: inspect (read-only), apply (backup/publish + seller readback).
"""
from __future__ import annotations
import json, time, sys, subprocess
from io import BytesIO
from hashlib import sha256
from pathlib import Path
from datetime import datetime, timezone

from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
import site_pricing
from scripts.apply_dq200_prices_20261120 import get_avito_asset, gh, rename_asset, STABLE_AVITO_URL
from scripts.publish_avito_titles_20261121 import verified_download

SKU="122390AK-AF"  # ordinary SINGLE boot; 122390AKAF is the ORIGINAL, do not change
BEFORE=950
AFTER=450
SITE_KEEP=950
MAIN_ID=8461500993
LEGACY_TO_FIX=4125079566
OTHER_OLD_ALREADY_450=3965207177
REQ=ROOT/"data/avito_plain_boot_restore_20261124_request.json"
OUT=ROOT/"data/avito_plain_boot_restore_20261124_result.json"
OVERRIDES=ROOT/"data/avito_site_price_overrides.xlsx"
FEED=ROOT/"data/avito_feed.xlsx"
KEEP_ORIGINAL="122390AKAF"

def check(ok,reason):
    if not ok:raise RuntimeError("GUARD_BLOCKED: "+reason)
def cash(x):
    try:return round(float(x),2)
    except (TypeError,ValueError):return None
def utc():return datetime.now(timezone.utc).isoformat()
def sha(b):return sha256(b).hexdigest()
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def save(result):
    result["checked_at_utc"]=utc()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("PLAIN_DQ200_BOOT_AVITO_CORRECTION",json.dumps({
       k:result.get(k) for k in ("mode","status","seller_prices_after",
        "source_publish","error")},ensure_ascii=False),flush=True)

def row_index(ws,col,start):
    rows={}
    for i in range(start,ws.max_row+1):
        sku=ws.cell(i,col).value
        if sku is None:continue
        sku=str(sku).strip()
        check(sku not in rows,"Duplicate workbook SKU "+sku)
        rows[sku]=i
    return rows

def workbook_verify(original,new,allowed_cells):
    check(original.sheetnames==new.sheetnames,"Workbook sheets changed")
    for s in original.sheetnames:
        a,b=original[s],new[s]
        check((a.max_row,a.max_column)==(b.max_row,b.max_column),
              "Worksheet dimensions changed "+s)
        check(list(a.merged_cells.ranges)==list(b.merged_cells.ranges),
              "Merged cells changed "+s)
        for row in a:
            for cell in row:
                if (s,cell.coordinate) in allowed_cells:continue
                after=b[cell.coordinate]
                check(cell.value==after.value and cell.style_id==after.style_id and
                      cell.number_format==after.number_format,
                      "Non-target workbook cell changed "+s+":"+cell.coordinate)

def prepare():
    policy=load(ROOT/"data/pricing_policy.json")
    check(policy.get("mode")=="manual_prices_only" and
          policy.get("automatic_avito_site_repricing") is False,
          "Manual-price policy changed")
    registry=load(ROOT/"data/avito_legacy_catalog_candidates_20261124.json")
    row=next((x for x in registry["entries"] if x["avito_id"]==LEGACY_TO_FIX),None)
    check(row is not None and
          row.get("owner_information_status")=="composition_verified_from_user_description" and
          row.get("owner_confirmed_related_master_skus")==[SKU] and
          "обычный пыльник" in json.dumps(row.get("owner_confirmed_composition"),ensure_ascii=False).lower(),
          "Owner verified single ordinary boot not recorded")
    link=load(ROOT/"data/avito_links.json")
    check(MAIN_ID not in [v for k,v in link.items() if not k.startswith("_")],
          "Unexpected canonical primary feed takeover mapping")
    check(LEGACY_TO_FIX not in [v for k,v in link.items() if not k.startswith("_")],
          "Old legacy listing became feed-managed unexpectedly")
    ovbook=load_workbook(OVERRIDES)
    check("Цены Avito и сайт" in ovbook.sheetnames,"Missing override worksheet")
    ovs=ovbook["Цены Avito и сайт"]
    i=row_index(ovs,1,2)
    check(SKU in i and KEEP_ORIGINAL in i,"Ordinary/original SKU missing override")
    oi=i[SKU]
    before_av=cash(ovs.cell(oi,2).value)
    site=cash(ovs.cell(oi,4).value)
    check(before_av in (BEFORE,AFTER),"Avito override changed outside 950/450")
    check(site==SITE_KEEP,"Website price changed; do not update website")
    original_sku_price=cash(ovs.cell(i[KEEP_ORIGINAL],2).value)
    check(original_sku_price==950,"Original boot Avito price changed")
    fbook=load_workbook(FEED)
    check("Объявления" in fbook.sheetnames,"Missing Avito source feed sheet")
    fs=fbook["Объявления"]
    fi=row_index(fs,4,5)
    check(SKU in fi and KEEP_ORIGINAL in fi,"Ordinary/original SKU missing from Avito feed")
    old_feed=cash(fs.cell(fi[SKU],34).value)
    check(old_feed in (BEFORE,AFTER),"Avito feed price changed outside 950/450")
    check(old_feed==before_av,"Avito override/feed source mismatch")
    check(cash(fs.cell(fi[KEEP_ORIGINAL],34).value)==950,"Original boot feed price changed")
    check("пыльник" in str(fs.cell(fi[SKU],24).value or "").lower(),
          "Feed SKU title no longer ordinary boot")
    # No metadata fields and no website B/D coupling are touched.
    ovs.cell(oi,2).value=AFTER
    fs.cell(fi[SKU],34).value=AFTER
    changed={}
    for path,book,changes in (
        (OVERRIDES,ovbook,{("Цены Avito и сайт","B"+str(oi))}),
        (FEED,fbook,{("Объявления","AH"+str(fi[SKU]))}),
    ):
        out=BytesIO();book.save(out);payload=out.getvalue()
        check(len(payload)>1000,"Incomplete XLSX output")
        old=load_workbook(BytesIO(path.read_bytes()))
        new=load_workbook(BytesIO(payload))
        workbook_verify(old,new,changes)
        changed[path]=payload
    check(cash(ovbook["Цены Avito и сайт"].cell(oi,4).value)==SITE_KEEP,
          "Website price changed in-memory")
    return changed,{"override_price_before":before_av,"feed_price_before":old_feed,
                    "site_price_unchanged":site,"original_boot_avito_price_kept":original_sku_price,
                    "override_row":oi,"feed_row":fi[SKU]}

def current_ads():
    rows=av.list_all_items(per_page=25,max_pages=45)
    out={int(x["id"]):x for x in rows if x.get("id") is not None}
    check(len(out)==len(rows) and len(out)>=110,"Seller API listing incomplete")
    return out

def inspect_live():
    rows=current_ads()
    check(all(i in rows for i in (MAIN_ID,LEGACY_TO_FIX,OTHER_OLD_ALREADY_450)),
          "Ordinary boot active IDs missing")
    primary=rows[MAIN_ID];old=rows[LEGACY_TO_FIX];already=rows[OTHER_OLD_ALREADY_450]
    check(all(x.get("status")=="active" for x in (primary,old,already)),
          "One boot listing is not active")
    check("пыльник" in str(primary.get("title") or "").lower() and
          "пыльник" in str(old.get("title") or "").lower(),
          "One ordinary boot title changed")
    check(cash(primary.get("price")) in (BEFORE,AFTER) and
          cash(old.get("price")) in (BEFORE,AFTER),
          "Price changed outside approval")
    check(cash(already.get("price"))==AFTER,
          "Unchanged previous old boot did not remain at 450")
    check(MAIN_ID != LEGACY_TO_FIX != OTHER_OLD_ALREADY_450,"Colliding seller IDs")
    return {str(i):{"price":cash(rows[i]["price"]),"status":rows[i]["status"],
                   "title":rows[i].get("title"),"url":rows[i].get("url")}
            for i in (MAIN_ID,LEGACY_TO_FIX,OTHER_OLD_ALREADY_450)}

def publish(new):
    release,asset,published=get_avito_asset()
    check(published==FEED.read_bytes(),
          "Published stable Avito feed and GitHub source differ; no publish")
    if published==new:return {"status":"already_published","sha256":sha(published)}
    filename="avito_feed.plain_boot_450_"+sha(new)[:16]+".xlsx"
    candidates=[x for x in release["assets"] if x.get("name")==filename]
    check(len(candidates)<=1,"Duplicate feed stage")
    stage=(candidates[0] if candidates else
           gh("POST",release["upload_url"].split("{",1)[0],
              params={"name":filename},data=new,
              headers={"Content-Type":
                       "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}).json())
    verified_download(stage["browser_download_url"],new)
    _,now_asset,now_published=get_avito_asset()
    check(now_asset["id"]==asset["id"] and now_published==published,
          "Stable Avito asset changed concurrently")
    backup="avito_feed.before_plain_boot_450_"+str(asset["id"])+".xlsx"
    rename_asset(asset["id"],backup)
    try:
        rename_asset(stage["id"],"avito_feed.xlsx")
    except Exception:
        rename_asset(asset["id"],"avito_feed.xlsx")
        raise
    verified_download(STABLE_AVITO_URL,new)
    return {"status":"published_sha_verified","sha256":sha(new),"backup_asset":backup}

def persist_sources(data):
    for path,blob in data.items():path.write_bytes(blob)
    files=[str(p.relative_to(ROOT)) for p in data]
    subprocess.run(["bash",".github/scripts/save_data.sh",*files],
        cwd=ROOT,check=True,timeout=180)

def seller_prices(live):
    receipts={}
    for key in (MAIN_ID,LEGACY_TO_FIX):
        if live[str(key)]["price"]==AFTER:
            receipts[str(key)]={"status":"already_450"}
            continue
        response=av._request("POST",f"/core/v1/items/{key}/update_price",
                             json={"price":AFTER},retries=1)
        good=(response.get("result") or {}).get("success") is True
        receipts[str(key)]={"status":"accepted" if good else "not_confirmed"}
        check(good,"Seller price update not accepted "+str(key))
    return receipts

def verify_live():
    for attempt in range(7):
        if attempt:time.sleep(4)
        now=inspect_live()
        if all(now[str(i)]["price"]==AFTER for i in
               (MAIN_ID,LEGACY_TO_FIX,OTHER_OLD_ALREADY_450)):
            return now
    return now

def main():
    result={"mode":None,"status":"starting","sku":SKU,
            "new_price":AFTER,"site_price_changes":0,
            "original_boot_changes":0,"other_sku_price_changes":0,
            "stock_changes":0,"photos_changes":0}
    try:
        req=load(REQ)
        check(req.get("mode") in ("inspect","apply") and
              req.get("owner_approved") is True and
              req.get("reason")=="correct_avito_plain_boot_price_not_marketplace" and
              req.get("sku")==SKU and req.get("main_id")==MAIN_ID and
              req.get("legacy_id")==LEGACY_TO_FIX and
              req.get("from")==BEFORE and req.get("to")==AFTER and
              req.get("site_keep")==SITE_KEEP and
              req.get("no_ozon_wb_ym_changes") is True and
              req.get("preserve_orig_boot_price") is True,
              "Price correction owner scope changed")
        result["mode"]=req["mode"]
        changed,before=prepare()
        live=inspect_live()
        result.update({"source_before":before,"live_before":live,
           "feed_sha_before":sha(FEED.read_bytes()),
           "source_sha_before":sha(OVERRIDES.read_bytes()),
           "feed_sha_after":sha(changed[FEED]),
           "source_sha_after":sha(changed[OVERRIDES])})
        _,asset,published=get_avito_asset()
        check(published==FEED.read_bytes(),"Stable feed no longer matches main")
        result["seller_feed_asset_id"]=asset["id"]
        if req["mode"]=="inspect":
            result["status"]="preflight_passed_no_writes";save(result);return
        if before["override_price_before"]==BEFORE:
            result["source_publish"]=publish(changed[FEED])
            persist_sources(changed)
        else:
            check(before["override_price_before"]==AFTER,
                  "Partial source change cannot be auto-fixed")
            result["source_publish"]={"status":"already_source_450"}
        check(cash(site_pricing.load_price_overrides(channel="avito").get(SKU))==AFTER,
              "Source Avito price after update is not 450")
        check(cash(site_pricing.load_price_overrides(channel="site").get(SKU))==SITE_KEEP,
              "Source site price was not preserved")
        result["api_update_receipts"]=seller_prices(live)
        result["seller_prices_after"]=verify_live()
        good=all(result["seller_prices_after"][str(i)]["price"]==AFTER for i in
             (MAIN_ID,LEGACY_TO_FIX,OTHER_OLD_ALREADY_450))
        result["status"]="verified_3_of_3" if good else "source_corrected_seller_pending"
        save(result)
        check(good,"Seller live prices not verified 450")
    except Exception as ex:
        result["status"]="blocked_or_partial_manual_review"
        result["error"]={"type":type(ex).__name__,"message":str(ex)[:330]}
        save(result)
        raise
if __name__=="__main__":main()
