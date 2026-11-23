"""LEOPARTS: owner-approved two Avito kit prices; no site, stock or other SKU writes.

Verified owner instruction 24 November 2026: DQ250 body+filter and DQ500
body+filter should be 1390 RUB on Avito. Site stays at prior 1480.
Two-stage inspect/apply protocol with exact original-price, BOM, seller-ID,
published-feed equality, source-of-truth and idempotence guards.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path
from hashlib import sha256
import json, sys, time, os, subprocess
from copy import deepcopy
from urllib.parse import urlencode

from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
import site_pricing
from scripts.apply_dq200_prices_20261120 import get_avito_asset, gh, rename_asset, STABLE_AVITO_URL
from scripts.publish_avito_titles_20261121 import verified_download

REQ=ROOT/"data/avito_two_kits_1390_approved.json"
OUT=ROOT/"data/avito_two_kits_1390_result.json"
FEED=ROOT/"data/avito_feed.xlsx"
OVERRIDE=ROOT/"data/avito_site_price_overrides.xlsx"
MASTER=ROOT/"data/master_control.xlsx"
KITS=ROOT/"data/kits.xlsx"
TARGETS={
    "02E 305 045": {"id":8386056623, "before":1480, "after":1390, "site_keep":1480,
                     "components":{"02e305045":1,"02E305051C":1}, "cost":400},
    "DQ500": {"id":8387244563, "before":1480, "after":1390, "site_keep":1480,
               "components":{"Dq500":1,"filtr-dsg7-dq500":1}, "cost":400},
}
DONE_PRICES={sku:v["after"] for sku,v in TARGETS.items()}

def utc(): return datetime.now(timezone.utc).isoformat()
def check(expr,reason):
    if not expr:raise RuntimeError("GUARD_BLOCKED: "+reason)
def digest(blob):return sha256(blob).hexdigest()
def num(v):
    try:return float(v)
    except (ValueError,TypeError):return None
def ok_price(got,want):
    v=num(got)
    return v is not None and abs(v-want)<0.01
def save(status,**data):
    result={"at_utc":utc(),"mode":json.loads(REQ.read_text(encoding="utf8")).get("mode"),
            "status":status,"targets":TARGETS,
            "seller_price_only":True,"site_price_changes":0,"stock_changes":0,
            "other_sku_changes":0,**data}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("AVITO_TWO_KITS_RESULT",json.dumps({k:result.get(k) for k in
           ("status","mode","live_prices_before","live_prices_after","source_changed",
            "release_status","error")},ensure_ascii=False),flush=True)
    return result

def cells_book(path,sheet,sku_col):
    book=load_workbook(path)
    check(sheet in book.sheetnames, "worksheet not found "+sheet)
    ws=book[sheet]; entries={}
    for row in range(2 if sheet!="Объявления" else 5,ws.max_row+1):
        key=ws.cell(row,sku_col).value
        if key is not None:
            key=str(key).strip()
            check(key not in entries,"duplicate SKU in "+sheet+": "+key)
            entries[key]=row
    return book,ws,entries

def checksum_other_cells(original,patched,allowed):
    check(original.sheetnames==patched.sheetnames,"Workbook sheets changed")
    for name in original.sheetnames:
        a,b=original[name],patched[name]
        check(a.max_row==b.max_row and a.max_column==b.max_column,
              "Workbook dimensions changed "+name)
        for row in a:
            for c in row:
                if (name,c.coordinate) in allowed:continue
                d=b[c.coordinate]
                check(c.value==d.value and c.style_id==d.style_id and c.number_format==d.number_format,
                      "Unexpected workbook difference "+name+":"+c.coordinate)
        check(len(a.merged_cells.ranges)==len(b.merged_cells.ranges),"Merged cells changed "+name)
        check(len(a.data_validations.dataValidation)==len(b.data_validations.dataValidation),
              "Data validations changed "+name)

def verify_manifest():
    req=json.loads(REQ.read_text(encoding="utf8"))
    check(req.get("mode") in ("inspect","apply"),"Expected inspect/apply")
    check(req.get("owner_approved") is True and req.get("target_channel")=="avito" and
          req.get("site_prices_unchanged") is True and
          req.get("never_auto_reprice") is True and
          req.get("approved_skus") == list(TARGETS) and
          req.get("new_price_rub")==1390,
          "Exact owner approval or channel changed")
    policy=json.loads((ROOT/"data/pricing_policy.json").read_text(encoding="utf8"))
    check(policy.get("automatic_avito_site_repricing") is False and
          policy.get("mode")=="manual_prices_only",
          "Automatic pricing policy changed")
    return req

def prep_workbooks():
    control,ws,override_index=cells_book(OVERRIDE,"Цены Avito и сайт",1)
    feedbook,feedws,feed_index=cells_book(FEED,"Объявления",4)
    kitbook=load_workbook(KITS)
    check("Комплекты" in kitbook.sheetnames,"Missing kit BOM sheet")
    kitws=kitbook["Комплекты"]  # kit SKU intentionally repeats per BOM component
    masterbook,masterws,master_index=cells_book(MASTER,"Товары",1)
    bom={}
    for row in kitws.iter_rows(min_row=2):
        k=row[0].value
        c=row[2].value if len(row)>2 else None
        q=row[3].value if len(row)>3 else None
        if k in TARGETS and c:
            bom.setdefault(k,{})[str(c)]=int(q)
    statuses={}
    for sku,expected in TARGETS.items():
        check(sku in override_index and sku in feed_index and sku in master_index,
              "Target missing from source workbook "+sku)
        check(bom.get(sku)==expected["components"],"Target kit BOM changed "+sku)
        check(ok_price(masterws.cell(master_index[sku],27).value,expected["cost"]),
              "Target master cost changed "+sku)
        oi,fi=override_index[sku],feed_index[sku]
        ov=ws.cell(oi,2).value
        fv=feedws.cell(fi,34).value
        sv=ws.cell(oi,4).value
        check(ok_price(ov,expected["before"]) or ok_price(ov,expected["after"]),
              "Unapproved Avito source price "+sku)
        check(ok_price(fv,expected["before"]) or ok_price(fv,expected["after"]),
              "Unapproved feed price "+sku)
        check(sv is None or ok_price(sv,expected["site_keep"]),
              "Site already has a different explicit price "+sku)
        check(str(ws.cell(oi,3).value).strip().lower()=="да","Override not frozen "+sku)
        check("корпус" in str(feedws.cell(fi,24).value or "").lower() and
              "фильтр" in str(feedws.cell(fi,24).value or "").lower(),
              "Kit title changed or kit identity unclear "+sku)
        for component,qty in expected["components"].items():
            check(component in override_index,"BOM component missing override "+component)
        part_sum=sum(float(ws.cell(override_index[c],2).value)*q for c,q in expected["components"].items())
        check(ok_price(part_sum,1475),"Underlying component pricing changed "+sku)
        check(expected["after"]<part_sum,"Kit does not undercut purchase of components "+sku)
        statuses[sku]={"configured_before":ov,"feed_before":fv,"site_before":sv if sv is not None else ov,
                       "bom_component_sum":part_sum,"source_row_override":oi,"source_row_feed":fi}
    sites_before=site_pricing.load_price_overrides(channel="site")
    check(all(ok_price(sites_before.get(sku),t["site_keep"]) for sku,t in TARGETS.items()),
          "Effective site price differs from approved baseline")
    # Convert to exact target data in-memory; no save unless all readbacks pass.
    for sku,t in TARGETS.items():
        ws.cell(override_index[sku],2).value=t["after"]
        ws.cell(override_index[sku],4).value=t["site_keep"] # site explicitly preserved
        feedws.cell(feed_index[sku],34).value=t["after"]
    paths={}
    for path,b in ((OVERRIDE,control),(FEED,feedbook)):
        out=BytesIO();b.save(out);new=out.getvalue()
        check(len(new)>1000,"Incomplete workbook serialization")
        paths[path]=new
        oldbook=load_workbook(BytesIO(path.read_bytes()))
        fresh=load_workbook(BytesIO(new))
        allowed={(n,c.coordinate) for n,seq in
                 [(("Цены Avito и сайт" if path==OVERRIDE else "Объявления"),[]) for _ in []]
                 for c in seq} # overridden below; explicit exact cell coordinates
        allowed=set()
        if path==OVERRIDE:
            allowed={("Цены Avito и сайт","B"+str(override_index[s])) for s in TARGETS}
            allowed|={("Цены Avito и сайт","D"+str(override_index[s])) for s in TARGETS}
        else:
            allowed={("Объявления","AH"+str(feed_index[s])) for s in TARGETS}
        checksum_other_cells(oldbook,fresh,allowed)
    effective_site_after=load_workbook(BytesIO(paths[OVERRIDE]),data_only=True).worksheets[0]
    for sku,t in TARGETS.items():
        ri=override_index[sku]
        check(ok_price(effective_site_after.cell(ri,4).value,t["site_keep"]),
              "Site freeze could not be applied "+sku)
    return statuses,paths

def read_live():
    live=av.list_all_items(statuses=["active"],per_page=100,max_pages=8)
    byid={int(x["id"]):x for x in live if x.get("id") is not None}
    binding=av._request("GET","/autoload/v2/items/avito_ids",
                         params={"query":",".join(TARGETS)},retries=1)
    binds={str(x.get("ad_id")):int(x.get("avito_id")) for x in binding.get("items") or []
           if x.get("ad_id") in TARGETS and x.get("avito_id")}
    output={}
    for sku,t in TARGETS.items():
        check(sku in binds and binds[sku]==t["id"],"Avito SKU binding ID changed "+sku)
        check(t["id"] in byid,"Target not active on Avito: "+sku)
        x=byid[t["id"]]
        check("фильтр" in str(x.get("title") or "").lower() and
              "корпус" in str(x.get("title") or "").lower(),
              "Avito live title not the intended kit: "+sku)
        price=num(x.get("price"))
        check(price in (t["before"],t["after"]),"Avito live price unexpectedly changed "+sku)
        output[sku]={"avito_id":t["id"],"price":price,"status":x.get("status"),
                     "url":x.get("url")}
    return output

def push_sources(data):
    for path,raw in data.items():
        path.write_bytes(raw)
    # Serialized inventory/order worker and full source safeguard are supplied
    # by the global marketplace-agent Actions concurrency lock.
    files=[str(p.relative_to(ROOT)) for p in data]
    subprocess.run(["bash",".github/scripts/save_data.sh",*files],cwd=ROOT,check=True,timeout=160)

def publish_feed(new):
    release,old_asset,current=get_avito_asset()
    check(current==FEED.read_bytes(),"Stable published Avito feed differs from source; refuse overwrite")
    if current==new:
        return {"status":"already_published","sha256":digest(current)}
    release_assets=release["assets"]
    stage_name="avito_feed.two_kits_1390_"+digest(new)[:16]+".xlsx"
    matches=[x for x in release_assets if x.get("name")==stage_name]
    check(len(matches)<=1,"Ambiguous staged asset")
    if matches:staged=matches[0]
    else:
        staged=gh("POST",release["upload_url"].split("{",1)[0],
             params={"name":stage_name},data=new,
             headers={"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}).json()
    verified_download(staged["browser_download_url"],new)
    _,last,latest=get_avito_asset()
    check(last["id"]==old_asset["id"] and latest==current,
          "Stable Avito publication changed concurrently")
    backup="avito_feed.before_two_kits_1390_"+str(old_asset["id"])+".xlsx"
    rename_asset(old_asset["id"],backup)
    try:
        rename_asset(staged["id"],"avito_feed.xlsx")
    except Exception:
        rename_asset(old_asset["id"],"avito_feed.xlsx")
        raise
    verified_download(STABLE_AVITO_URL,new)
    return {"status":"published_sha_verified","sha256":digest(new),"backup_asset":backup}

def change_live_prices(before):
    result={}
    for sku,t in TARGETS.items():
        p=before[sku]["price"]
        if ok_price(p,t["after"]):
            result[sku]={"status":"already_correct","before":p}
            continue
        try:
            res=av._request("POST",f"/core/v1/items/{t['id']}/update_price",
                             json={"price":t["after"]},retries=1)
            result[sku]={"status":"request_sent","before":p,
                         "accepted":bool((res.get("result") or {}).get("success") is True)}
        except Exception as ex:
            result[sku]={"status":"unconfirmed","error_type":type(ex).__name__}
    return result

def run():
    req=verify_manifest()
    before,staged=prep_workbooks()
    live_before=read_live()
    old_feed=digest(FEED.read_bytes())
    old_override=digest(OVERRIDE.read_bytes())
    _,asset,current=get_avito_asset()
    check(current==FEED.read_bytes(),"Stable published feed out-of-sync with GitHub")
    data={"workbook_before":before,"live_prices_before":live_before,
          "original_feed_sha256":old_feed,"original_override_sha256":old_override,
          "new_feed_sha256":digest(staged[FEED]),
          "new_override_sha256":digest(staged[OVERRIDE]),
          "seller_feed_asset_id":asset["id"]}
    if req["mode"]=="inspect":
        return save("preflight_passed_no_writes",**data)
    if all(ok_price(before[s]["feed_before"],t["after"]) and
           ok_price(before[s]["configured_before"],t["after"])
           for s,t in TARGETS.items()):
        return save("already_in_canonical_sources",**data)
    check(all(ok_price(before[s]["feed_before"],t["before"]) and
              ok_price(before[s]["configured_before"],t["before"])
              for s,t in TARGETS.items()),
          "Partial prior source change detected, refuse bulk overwrite")
    release=publish_feed(staged[FEED])
    # Important: either source is saved or safe rollback is needed. No further
    # seller writes before both source files have been persisted.
    push_sources(staged)
    check(site_pricing.load_price_overrides(channel="avito").get("DQ500")==1390 and
          all(ok_price(site_pricing.load_price_overrides(channel="site").get(s),1480)
              for s in TARGETS),"Avito/site price isolation failed")
    receipts=change_live_prices(live_before)
    time.sleep(3)
    live_after=read_live()
    verified=all(ok_price(live_after[s]["price"],1390) for s in TARGETS)
    return save("verified_2_of_2" if verified else "canonical_sources_updated_live_pending",
                **data,source_changed=True,release_status=release,
                avito_update_receipts=receipts,live_prices_after=live_after)
def main():
    try:
        run()
    except Exception as ex:
        save("blocked_or_partial_verify_before_retry",error_type=type(ex).__name__,
             error=str(ex)[:350])
        raise
if __name__=="__main__":main()
