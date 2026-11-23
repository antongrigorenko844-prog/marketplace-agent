"""Apply price & 2 safe title edits from user's verified 155-row Avito XLSX.

Owner workbook modified 86 H prices + 13 G prices + 3 D titles.
Trust ONLY C (unchanged Avito IDs) for identity. E/F/N/O were altered by
mistake and are not part of owner approval. Never modify site, stock,
Ozon/WB/Yandex, title if it creates a false 4-piece kit, or 9-RUB outlier.

91? The actual count is derived exclusively from owner manifest.
inspect -> apply; publish full stable Avito feed with backup, update main
price override with explicit site preservation; then seller price API.
"""
from __future__ import annotations

from pathlib import Path
from datetime import datetime,timezone
from io import BytesIO
from hashlib import sha256
import json,time,sys,subprocess
from openpyxl import load_workbook

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
from scripts.apply_dq200_prices_20261120 import get_avito_asset,gh,rename_asset,STABLE_AVITO_URL
from scripts.publish_avito_titles_20261121 import verified_download
import site_pricing

REQ=ROOT/"data/avito_excel_owner_changes_20261124_request.json"
OUT=ROOT/"data/avito_excel_owner_changes_20261124_result.json"
AUD=ROOT/"data/avito_price_audit_20261124.json"
FEED=ROOT/"data/avito_feed.xlsx"
OVERRIDE=ROOT/"data/avito_site_price_overrides.xlsx"
CONTENT=ROOT/"data/content_manifest.json"
BLOCKED_PRICE_IDS={8609616823,8394615261}
BLOCKED_TITLE_IDS={8394615261}
NOW=datetime.now(timezone.utc)

def guard(ok,reason):
    if not ok:raise RuntimeError("GUARD_BLOCKED: "+reason)
def load(path):return json.loads(path.read_text(encoding="utf8"))
def cash(v):
    try:return round(float(v),2)
    except(ValueError,TypeError):return None
def digest(v):return sha256(v).hexdigest()
def save(result):
    result["checked_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("AVITO_OWNER_XLSX",json.dumps({k:result.get(k) for k in
          ("mode","status","counts","error")},ensure_ascii=False),flush=True)

def catalog():
    data=load(AUD)
    guard(data.get("live_api_error") is None,"Previous seller audit had an API error")
    primary={}
    for item in data["products"]:
        bound=item.get("mapped") or {}
        if bound.get("id") and bound.get("status")=="active":
            av_id=int(bound["id"])
            guard(av_id not in primary,"Duplicate owner main Avito ID")
            primary[av_id]=item
    guard(len(primary)>=110,"Main Avito ID-to-SKU mapping incomplete")
    return primary

def all_ads():
    raw=av.list_all_items(per_page=25,max_pages=45)
    items={int(x["id"]):x for x in raw if x.get("id") is not None}
    guard(len(items)==len(raw) and len(items)>=140,"Seller Avito API incomplete")
    return items

def data_rows(wb,sheet,col,start):
    guard(sheet in wb.sheetnames,"Expected sheet missing "+sheet)
    ws=wb[sheet]
    result={}
    for row in range(start,ws.max_row+1):
        val=ws.cell(row,col).value
        if val is None:continue
        key=str(val).strip()
        guard(key not in result,"Duplicate SKU in XLSX "+key)
        result[key]=row
    return ws,result

def unchanged_except(raw,changed,allowed):
    guard(raw.sheetnames==changed.sheetnames,"Worksheet names changed")
    for name in raw.sheetnames:
        a,b=raw[name],changed[name]
        guard((a.max_row,a.max_column)==(b.max_row,b.max_column),
              "XLSX dimensions changed "+name)
        guard(len(a.merged_cells.ranges)==len(b.merged_cells.ranges),
              "Merged cells changed "+name)
        for row in a:
            for c in row:
                if (name,c.coordinate) in allowed:continue
                d=b[c.coordinate]
                guard(c.value==d.value and c.style_id==d.style_id
                      and c.number_format==d.number_format,
                      "Unapproved workbook modification "+name+":"+c.coordinate)

def prepare():
    req=load(REQ)
    guard(req.get("schema_version")==1 and req.get("owner_approved") is True
          and req.get("mode") in ("inspect","apply")
          and req.get("other_channels_untouched") is True
          and req.get("site_price_preserve") is True
          and req.get("stock_untouched") is True
          and req.get("image_untouched") is True
          and req.get("sku_untouched") is True
          and req.get("master_cost_untouched") is True
          and req.get("automatic_pricing_disabled") is True,
          "Exact owner XLSX approval missing")
    prices=req.get("price_changes") or []
    titles=req.get("title_changes") or []
    guard(len(prices)==99 and len(titles)==3,"Owner XLSX change count incorrect")
    guard({int(x["avito_id"]) for x in prices}.__len__()==99,
          "Duplicate requested seller IDs")
    guard({int(x["avito_id"]) for x in titles}.__len__()==3,
          "Duplicate requested title IDs")
    guard(req.get("blocked",{}).get("8609616823") is not None and
          req.get("blocked",{}).get("8394615261") is not None,
          "Sensitive SKU/price gates removed")
    guard(load(ROOT/"data/pricing_policy.json").get("mode")=="manual_prices_only"
          and load(ROOT/"data/pricing_policy.json").get("automatic_avito_site_repricing") is False,
          "Pricing automation must remain disabled")

    primary=catalog()
    fresh=all_ads()
    all_requested={}
    blocked=[]
    noops=[]
    for x in prices:
        aid=int(x["avito_id"])
        old=cash(x["original_sheet_price_rub"])
        desired=cash(x["requested_price_rub"])
        col=x["edited_column"]
        guard(col in ("G","H") and desired is not None
              and old is not None and old>0,
              "Invalid G/H price change")
        if aid in BLOCKED_PRICE_IDS:
            blocked.append({"id":aid,"old":old,"proposed":desired,
                 "reason":req["blocked"].get(str(aid))})
            continue
        guard(desired>=100 and desired<=500000,
              "Unexpected extreme or invalid seller price "+str(aid))
        guard(aid in fresh and fresh[aid].get("status")=="active",
              "Edited seller Avito ID inactive/missing "+str(aid))
        current=cash(fresh[aid].get("price"))
        guard(current in (old,desired),
              "Live seller price deviates from uploaded baseline "+str(aid)+
              " observed "+str(current)+" expected "+str(old)+"/"+str(desired))
        if aid in primary:
            guard(cash(primary[aid]["avito_cfg"]) in (old,desired),
                  "Main catalog Avito price changed beyond owner scope "+str(aid))
        if old==desired:
            noops.append({"id":aid,"price":desired})
            continue
        all_requested[aid]={"id":aid,"sku":primary[aid]["sku"] if aid in primary else None,
            "expected_before":old,"requested":desired,"current":current,
            "edited_col":col,"type":"main" if aid in primary else "legacy",
            "live_title":fresh[aid].get("title"),"url":fresh[aid].get("url")}
    guard(len(all_requested)==95 and len(blocked)==2 and len(noops)==2,
          "Owner’s 95 safe price edits do not match expected strict count")

    approved_titles={}
    for x in titles:
        aid=int(x["avito_id"])
        if aid in BLOCKED_TITLE_IDS:
            blocked.append({"id":aid,"proposed_title":x["requested_title"],
                            "reason":"4pcs contradicts master unit count 1"})
            continue
        final=x.get("effective_title")
        guard(x.get("apply_title") is True and isinstance(final,str)
              and 10<len(final)<=50,"Avito title shorter than 50 characters required")
        guard(aid in fresh and fresh[aid].get("status")=="active" and
              fresh[aid].get("title")==x["original_title"],
              "Original Avito title has changed externally "+str(aid))
        guard(aid in primary and primary[aid]["sku"]==x["sku"],
              "Title ID does not match canonical SKU "+str(aid))
        approved_titles[aid]={"sku":x["sku"],"title_before":x["original_title"],
                               "requested_full":x["requested_title"],
                               "title_apply":final}
    guard(len(approved_titles)==2,"Expected 2 safe owner title changes")

    # Only the 71 *primary* changed prices alter central canonical sources.
    main_changes={aid:p for aid,p in all_requested.items() if aid in primary}
    legacy_changes={aid:p for aid,p in all_requested.items() if aid not in primary}
    guard(len(main_changes)==71 and len(legacy_changes)==24,
          "Unexpected main/legacy split; no bulk apply")

    book=load_workbook(OVERRIDE)
    ows,oi=data_rows(book,"Цены Avito и сайт",1,2)
    fbook=load_workbook(FEED)
    fws,fi=data_rows(fbook,"Объявления",4,5)
    protected=set()
    allowed_ov=set()
    allowed_feed=set()
    site_preserved={}
    for aid,t in main_changes.items():
        prod=primary[aid]
        sku=prod["sku"]
        alias=prod.get("source_feed_id") or sku
        guard(sku in oi and alias in fi,
              "Canonical feed/override SKU missing "+sku+"/"+alias)
        i=oi[sku];j=fi[alias]
        previous_ov=cash(ows.cell(i,2).value)
        previous_f=cash(fws.cell(j,34).value)
        guard(previous_ov in (t["expected_before"],t["requested"]) and
              previous_f in (t["expected_before"],t["requested"]),
              "Canonical Avito source changed "+sku)
        effective_site=cash(ows.cell(i,4).value)
        if effective_site is None:
            effective_site=previous_ov
        guard(effective_site is not None and effective_site>0,
              "Website effective price unknown "+sku)
        site_preserved[sku]=effective_site
        ows.cell(i,2).value=int(t["requested"])
        ows.cell(i,4).value=int(effective_site)
        fws.cell(j,34).value=int(t["requested"])
        allowed_ov.update({("Цены Avito и сайт","B"+str(i)),
                           ("Цены Avito и сайт","D"+str(i))})
        allowed_feed.add(("Объявления","AH"+str(j)))
        protected.add(alias)

    manifest=load(CONTENT)
    guard(manifest.get("version")==1 and isinstance(manifest.get("products"),dict),
          "Content manifest schema invalid")
    for aid,t in approved_titles.items():
        sku=t["sku"]
        prod=primary[aid]
        alias=prod.get("source_feed_id") or sku
        guard(alias in fi,"Title SKU not found in feed "+sku)
        j=fi[alias]
        original_title=str(fws.cell(j,24).value or "")
        guard(original_title==t["title_before"] or original_title==t["title_apply"],
              "Canonical Avito title source changed for "+sku)
        fws.cell(j,24).value=t["title_apply"]
        allowed_feed.add(("Объявления","X"+str(j)))
        row=manifest["products"].setdefault(sku,{})
        text=row.setdefault("text",{}).setdefault("avito",{})
        previous=text.get("title")
        guard(previous is None or previous in (t["title_before"],t["title_apply"]),
              "Persistent text manifest title changed for "+sku)
        text["title"]=t["title_apply"]
    raw=load(CONTENT)
    for sku,updated in manifest["products"].items():
        earlier=raw["products"].get(sku,{})
        if sku not in [t["sku"] for t in approved_titles.values()]:
            guard(earlier==updated,"Unrelated content changed "+sku)
    # Binary output guard: every non-target XLSX cell and style unchanged.
    changes={}
    for path,book_obj,allowed in ((OVERRIDE,book,allowed_ov),(FEED,fbook,allowed_feed)):
        f=BytesIO();book_obj.save(f)
        new=f.getvalue()
        guard(len(new)>5000,"Incomplete XLSX serialization")
        unchanged_except(load_workbook(BytesIO(path.read_bytes())),
                         load_workbook(BytesIO(new)),allowed)
        changes[path]=new
    changes[CONTENT]=(json.dumps(manifest,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
    return req,all_requested,blocked,noops,approved_titles,site_preserved,changes,fresh

def publish_feed(new):
    release,old_asset,current=get_avito_asset()
    guard(current==FEED.read_bytes(),"Published Avito feed differs from repository before update")
    if current==new:return {"status":"already_published","sha256":digest(new)}
    asset_name="avito_feed.owner_excel_20261124_"+digest(new)[:18]+".xlsx"
    hits=[x for x in release["assets"] if x.get("name")==asset_name]
    guard(len(hits)<=1,"Duplicate staged feed")
    staged=(hits[0] if hits else
       gh("POST",release["upload_url"].split("{",1)[0],
          params={"name":asset_name},data=new,
          headers={"Content-Type":
                   "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}).json())
    verified_download(staged["browser_download_url"],new)
    _,last,latest=get_avito_asset()
    guard(last["id"]==old_asset["id"] and latest==current,
          "Avito stable feed changed concurrently")
    backup="avito_feed.before_owner_excel_"+str(old_asset["id"])+".xlsx"
    rename_asset(old_asset["id"],backup)
    try:rename_asset(staged["id"],"avito_feed.xlsx")
    except Exception:
        rename_asset(old_asset["id"],"avito_feed.xlsx")
        raise
    verified_download(STABLE_AVITO_URL,new)
    return {"status":"published_sha_verified","sha256":digest(new),
            "old_feed_backup":backup}

def persist(changes):
    for path,raw in changes.items():path.write_bytes(raw)
    files=[str(p.relative_to(ROOT)) for p in changes]
    subprocess.run(["bash",".github/scripts/save_data.sh",*files],
                   cwd=ROOT,check=True,timeout=200)

def update_prices(targets,result):
    for idx,(aid,t) in enumerate(targets.items(),start=1):
        if t["current"]==t["requested"]:
            result["receipts"][str(aid)]={"status":"already_at_price"}
            continue
        try:
            response=av._request("POST",f"/core/v1/items/{aid}/update_price",
                                  json={"price":int(t["requested"])},retries=2)
            success=(response.get("result") or {}).get("success") is True
            result["receipts"][str(aid)]={"status":"accepted" if success
                                         else "not_confirmed"}
            if success:result["price_requests_accepted"]+=1
        except Exception as exc:
            result["receipts"][str(aid)]={"status":"error",
                "error_type":type(exc).__name__,"message":str(exc)[:120]}
        if idx%10==0:
            result["counts"]["attempted"]=idx
            save(result)
        time.sleep(0.5)
    result["counts"]["attempted"]=len(targets)
    save(result)

def verify_live(targets,titles):
    latest=all_ads()
    prices={}
    for aid,t in targets.items():
        v=latest.get(aid,{})
        prices[str(aid)]={"price":cash(v.get("price")),"target":t["requested"],
                          "status":v.get("status"),"verified":v.get("status")=="active"
                                     and cash(v.get("price"))==t["requested"]}
    names={}
    for aid,t in titles.items():
        v=latest.get(aid,{})
        names[str(aid)]={"title":v.get("title"),
             "requested_title":t["requested_full"],"feed_title":t["title_apply"],
             "live_verified":v.get("title")==t["title_apply"],
             "feed_published":True}
    return prices,names

def main():
    result={"mode":None,"status":"starting","seller_price_writes":0,
        "price_requests_accepted":0,"site_price_changes":0,
        "other_channel_changes":0,"stock_changes":0,"photo_changes":0,
        "cost_changes":0,"sku_changes":0,"new_ads":0,"receipts":{}}
    try:
        (req,targets,blocked,noops,titles,sites,changes,live)=prepare()
        result.update({"mode":req["mode"],"status":"preflight_passed",
             "targets":targets,"blocked":blocked,"noops":noops,
             "titles":titles,"site_prices_frozen":sites,
             "counts":{"input_price_cells":len(req["price_changes"]),
                 "safe_price_actions":len(targets),
                 "main_price_actions":len([x for x in targets.values() if x["type"]=="main"]),
                 "legacy_price_actions":len([x for x in targets.values() if x["type"]=="legacy"]),
                 "price_noops":len(noops),"blocked_price_cells":len(BLOCKED_PRICE_IDS),
                 "approved_titles":len(titles),"title_blocked":1},
             "feed_sha_before":digest(FEED.read_bytes()),
             "feed_sha_after":digest(changes[FEED]),
             "price_source_sha_before":digest(OVERRIDE.read_bytes()),
             "price_source_sha_after":digest(changes[OVERRIDE]),
             "content_manifest_sha_before":digest(CONTENT.read_bytes()),
             "content_manifest_sha_after":digest(changes[CONTENT])})
        _,asset,published=get_avito_asset()
        guard(published==FEED.read_bytes(),
              "Published Avito feed is stale vs repository")
        result["stable_feed_asset_before"]=asset["id"]
        save(result)
        if req["mode"]=="inspect":
            result["status"]="inspect_passed_no_writes";save(result);return
        result["feed_publish"]=publish_feed(changes[FEED])
        persist(changes)
        result["source_changes_committed"]=True
        result["status"]="sources_published_prices_in_progress"
        save(result)
        # Compare effective site after writing with before; this must be identical.
        for sku,old_site in sites.items():
            site=site_pricing.load_price_overrides(channel="site").get(sku)
            guard(cash(site)==old_site,"Website price changed "+sku)
        update_prices(targets,result)
        for i in range(7):
            if i:time.sleep(4)
            prices,names=verify_live(targets,titles)
            result["price_verify"]=prices
            result["title_verify"]=names
            result["counts"]["verified_prices"]=sum(z["verified"] for z in prices.values())
            result["counts"]["unverified_prices"]=len(targets)-result["counts"]["verified_prices"]
            result["counts"]["live_verified_titles"]=sum(z["live_verified"] for z in names.values())
            result["status"]="verified_95_prices" if all(x["verified"] for x in prices.values()) else "partially_verified_prices"
            save(result)
            if result["status"]=="verified_95_prices":return
        raise RuntimeError("At least one owner-approved price change still unverified in live Avito API")
    except Exception as exc:
        result["status"]="blocked_or_partial_review_needed"
        result["error"]={"type":type(exc).__name__,"message":str(exc)[:400]}
        save(result)
        raise
if __name__=="__main__":main()
