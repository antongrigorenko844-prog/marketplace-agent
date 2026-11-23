"""Avito-only correction after owner confirms pictured listing has FOUR seals.

Exact Avito ID 8394615261. Original Excel owner price 990.
Clip Avito ID 8609616823 stays at 950, never 9.
Preserve Ozon/WB/YM title/description/price, Tilda effective price 950,
stock, all other SKUs, and every media cell. Use inspect/apply stages.
"""
from __future__ import annotations
from datetime import datetime,timezone
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import json,time,sys,subprocess
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
import site_pricing
from scripts.apply_dq200_prices_20261120 import get_avito_asset,gh,rename_asset,STABLE_AVITO_URL
from scripts.publish_avito_titles_20261121 import verified_download

SKU="0AM325413A"
ID=8394615261
CLIP_ID=8609616823
CLIP_SKU="klipsa-datchika-peredach-dq200"
BEFORE=950
AFTER=990
TITLE="Сальники толкателей вилок DSG7 DQ200, 4 шт"
DESCRIPTION=("Комплект сальников толкателей вилок мехатроника DSG7 DQ200 (0AM/0CW), "
             "артикул 0AM325413A. Цена указана за комплект из 4 сальников (4 шт.), "
             "как показано на фотографиях объявления. Предназначены для обслуживания "
             "уплотнений толкателей вилок. Перед заказом проверьте совместимость "
             "с вашим мехатроником.")
REQ=ROOT/"data/avito_four_seals_owner_request_20261124.json"
OUT=ROOT/"data/avito_four_seals_owner_result_20261124.json"
FEED=ROOT/"data/avito_feed.xlsx"
OVERRIDE=ROOT/"data/avito_site_price_overrides.xlsx"
CONTENT=ROOT/"data/content_manifest.json"

def require(x,m):
    if not x:raise RuntimeError("GUARD_BLOCKED: "+m)
def load(path):return json.loads(path.read_text(encoding="utf-8"))
def num(x):
    try:return round(float(x),2)
    except (TypeError,ValueError):return None
def digest(raw):return sha256(raw).hexdigest()
def save(result):
    result["checked_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("AVITO_FOUR_SEALS_OWNER",json.dumps({k:result.get(k)
           for k in ("status","mode","live_after","error")},ensure_ascii=False),flush=True)
def seller():
    rows=av.list_all_items(per_page=25,max_pages=45)
    found={int(x["id"]):x for x in rows if x.get("id") is not None}
    require(len(found)==len(rows) and len(found)>=140,
            "Incomplete Avito seller listing response")
    require(ID in found and CLIP_ID in found,"Seal or clip not in seller API")
    return found
def index(ws,col,start):
    d={}
    for i in range(start,ws.max_row+1):
        k=ws.cell(i,col).value
        if k is None:continue
        k=str(k).strip()
        require(k not in d,"Duplicate source SKU "+k)
        d[k]=i
    return d
def check_only(before,after,allowed):
    require(before.sheetnames==after.sheetnames,"Sheet names changed")
    for n in before.sheetnames:
        a,b=before[n],after[n]
        require((a.max_row,a.max_column)==(b.max_row,b.max_column),
                "Sheet dimensions changed "+n)
        for row in a:
            for c in row:
                if (n,c.coordinate) in allowed:continue
                e=b[c.coordinate]
                require(c.value==e.value and c.style_id==e.style_id
                        and c.number_format==e.number_format,
                        "Unapproved change "+n+":"+c.coordinate)
def patch(path,book,allowed):
    buf=BytesIO();book.save(buf);raw=buf.getvalue()
    require(len(raw)>10000,"Workbook serialized incompletely")
    check_only(load_workbook(BytesIO(path.read_bytes())),load_workbook(BytesIO(raw)),allowed)
    return raw

def prepare():
    req=load(REQ)
    require(req.get("mode") in ("inspect","apply")
            and req.get("owner_approved") is True
            and req.get("avito_id")==ID
            and req.get("sku")==SKU
            and req.get("quantity_per_sale")==4
            and req.get("price_after")==AFTER
            and req.get("price_before")==BEFORE
            and req.get("sensor_clip_id")==CLIP_ID
            and req.get("sensor_clip_keep_price")==950
            and req.get("no_other_channels") is True
            and req.get("no_other_skus") is True
            and req.get("stock_unchanged") is True
            and req.get("photos_unchanged") is True,
            "Owner scope mismatch")
    policy=load(ROOT/"data/pricing_policy.json")
    require(policy.get("mode")=="manual_prices_only"
            and policy.get("automatic_avito_site_repricing") is False,
            "Automatic pricing not disabled")
    require(len(TITLE)<=50 and "4 шт" in TITLE,
            "Title must be <=50 chars and show 4 pieces")
    live=seller()
    target,clip=live[ID],live[CLIP_ID]
    require(target.get("status")=="active" and clip.get("status")=="active",
            "One listing not active")
    require(num(target.get("price")) in (BEFORE,AFTER)
            and num(clip.get("price"))==950,"Live price changed outside scope")
    require("сальник" in str(target.get("title") or "").lower()
            and "клипс" in str(clip.get("title") or "").lower(),
            "Seller listings do not match expected products")
    effective_site=site_pricing.load_price_overrides(channel="site")
    require(num(effective_site.get(SKU))==BEFORE
            and num(effective_site.get(CLIP_SKU))==950,
            "Effective site prices differ from baseline")
    avito_cfg=site_pricing.load_price_overrides(channel="avito")
    require(num(avito_cfg.get(SKU)) in (BEFORE,AFTER)
            and num(avito_cfg.get(CLIP_SKU))==950,
            "Avito price source unexpectedly changed")

    book=load_workbook(OVERRIDE)
    ws=book["Цены Avito и сайт"]
    bysku=index(ws,1,2)
    require(SKU in bysku and CLIP_SKU in bysku,"SKU absent in override")
    n=bysku[SKU]
    require(num(ws.cell(n,2).value) in (BEFORE,AFTER)
            and num(ws.cell(bysku[CLIP_SKU],2).value)==950,
            "Workbook price outside approved scope")
    ws.cell(n,2).value=AFTER
    ws.cell(n,4).value=BEFORE
    priced=patch(OVERRIDE,book,{
        ("Цены Avito и сайт","B"+str(n)),
        ("Цены Avito и сайт","D"+str(n))})

    src=load_workbook(FEED)
    fw=src["Объявления"]
    rows=index(fw,4,5)
    require(SKU in rows and CLIP_SKU in rows,"SKU missing from published Avito feed")
    rr=rows[SKU]
    require(num(fw.cell(rr,34).value) in (BEFORE,AFTER)
            and num(fw.cell(rows[CLIP_SKU],34).value)==950,
            "Seller feed price different than expected")
    title_before=fw.cell(rr,24).value
    require(title_before in ("Сальник толкателя вилки мехатроника DSG7 DQ200",TITLE),
            "Feed title changed outside original approval")
    fw.cell(rr,24).value=TITLE
    fw.cell(rr,17).value=DESCRIPTION
    fw.cell(rr,34).value=AFTER
    staged=patch(FEED,src,{
      ("Объявления","X"+str(rr)),("Объявления","Q"+str(rr)),
      ("Объявления","AH"+str(rr))})

    doc=load(CONTENT)
    require(doc.get("version")==1 and isinstance(doc.get("products"),dict),
            "Content source invalid")
    row=doc["products"].get(SKU,{})
    ozon_before=row.get("text",{}).get("ozon",{})
    require("один" in str(ozon_before.get("description","")).lower(),
            "Ozon original one-piece source unexpectedly changed")
    avito_old=row.get("text",{}).get("avito",{})
    require(avito_old.get("title") in (None,TITLE),
            "Pinned Avito title diverged")
    require(avito_old.get("description") is None or
            "один новый сальник" in avito_old["description"].lower() or
            avito_old.get("description")==DESCRIPTION,
            "Pinned Avito description externally modified")
    doc["products"].setdefault(SKU,{}).setdefault("text",{}).setdefault("avito",{}).update({
       "title":TITLE,"description":DESCRIPTION,
       "composition":"Комплект 4 шт. сальников толкателей вилок DQ200 (0AM/0CW)"
    })
    require(doc["products"][SKU].get("text",{}).get("ozon",{})==ozon_before,
            "Ozon description must not be modified")
    content=(json.dumps(doc,ensure_ascii=False,indent=2)+"\n").encode("utf-8")
    changes={OVERRIDE:priced,FEED:staged,CONTENT:content}
    # Idempotence after a partial earlier run.
    if (num(avito_cfg.get(SKU))==AFTER and
        title_before==TITLE and
        load(CONTENT)==doc):
        changes={p:p.read_bytes() for p in changes}
    return req,live,changes,{
      "old_title":target.get("title"),"current_price":num(target.get("price")),
      "site_keep":num(effective_site[SKU]),
      "clip_keep":num(clip["price"]),"description_characters":len(DESCRIPTION),
      "new_title":TITLE,"feed_line":rr,"override_line":n}

def publish(new):
    release,old,current=get_avito_asset()
    require(current==FEED.read_bytes(),
            "Published stable feed out of sync with GitHub")
    if current==new:return {"status":"already_published","sha256":digest(new)}
    name="avito_feed.four_seals_4pc_"+digest(new)[:16]+".xlsx"
    candidates=[x for x in release["assets"] if x.get("name")==name]
    require(len(candidates)<=1,"Duplicate staged feed asset")
    staged=candidates[0] if candidates else gh(
      "POST",release["upload_url"].split("{",1)[0],
      params={"name":name},data=new,
      headers={"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}).json()
    verified_download(staged["browser_download_url"],new)
    _,current_asset,unchanged=get_avito_asset()
    require(current_asset["id"]==old["id"] and unchanged==current,
            "Stable feed changed during stage")
    backup="avito_feed.before_four_seals_"+str(old["id"])+".xlsx"
    rename_asset(old["id"],backup)
    try:rename_asset(staged["id"],"avito_feed.xlsx")
    except Exception:
        rename_asset(old["id"],"avito_feed.xlsx")
        raise
    verified_download(STABLE_AVITO_URL,new)
    return {"status":"published_sha_verified","sha256":digest(new),
            "backup_asset":backup}

def persist(changes):
    for path,raw in changes.items():path.write_bytes(raw)
    subprocess.run(["bash",".github/scripts/save_data.sh"]+
       [str(x.relative_to(ROOT)) for x in changes],
       cwd=ROOT,check=True,timeout=200)

def run():
    result={"status":"started","mode":None,"sku":SKU,"id":ID,
            "website_price_writes":0,"ozon_wb_ym_writes":0,
            "sensor_clip_price_writes":0,"other_sku_writes":0,
            "stock_writes":0,"photo_writes":0}
    try:
        req,live,changes,review=prepare()
        result.update({"status":"preflight_passed","mode":req["mode"],
            "owner_verified":review,"feed_before":digest(FEED.read_bytes()),
            "feed_after":digest(changes[FEED])})
        _,asset,public=get_avito_asset()
        require(public==FEED.read_bytes(),
                "Published feed differs from catalog")
        save(result)
        if req["mode"]=="inspect":
            result["status"]="inspect_ok_no_writes";save(result);return
        result["publish"]=publish(changes[FEED])
        persist(changes)
        result["source_saved"]=True
        require(num(site_pricing.load_price_overrides(channel="site").get(SKU))==BEFORE,
                "Website price changed")
        require(num(site_pricing.load_price_overrides(channel="avito").get(SKU))==AFTER,
                "Avito source price incorrect")
        if num(live[ID].get("price"))!=AFTER:
            resp=av._request("POST",f"/core/v1/items/{ID}/update_price",
               json={"price":AFTER},retries=2)
            require((resp.get("result") or {}).get("success") is True,
                    "Price API did not acknowledge update")
            result["price_api_accepted"]=True
        for i in range(7):
            if i:time.sleep(4)
            fresh=seller()
            seal=fresh[ID];clip=fresh[CLIP_ID]
            result["live_after"]={"price":num(seal.get("price")),
                "title":seal.get("title"),"status":seal.get("status"),
                "clip_price":num(clip.get("price")),
                "title_updated":seal.get("title")==TITLE}
            if (seal.get("status")=="active" and num(seal.get("price"))==AFTER
                and num(clip.get("price"))==950):
                result["status"]="verified_price_990_title_in_feed"
                if result["live_after"]["title_updated"]:
                    result["status"]="verified_price_and_live_title"
                save(result);return
        raise RuntimeError("Seller live price not verified after update")
    except Exception as exc:
        result["status"]="blocked_or_partial"
        result["error"]={"kind":type(exc).__name__,"message":str(exc)[:300]}
        save(result)
        raise
if __name__=="__main__":run()
