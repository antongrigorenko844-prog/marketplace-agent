"""Publish approved exact 6T30 media bundle after the owner uploads the prepared ZIP.

Never invoke until the exact user-approved ZIP is present. Verify all three
original image SHA256 hashes BEFORE any external write. No other SKUs touched.
Can publish to GitHub Release and existing WB cards; create Yandex/Tilda
offers when taxonomy and attributes can be confirmed. Not an Ozon/Avito hack.
"""
import os,io,sys,json,hashlib,time,zipfile,traceback
from pathlib import Path
from datetime import datetime,timezone
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import photo_host,wb_client as wb,yandex_market_client as ym,tilda_commerceml as tc
from scripts.leoparts_direct_operation_nov21 import site_records

ZIP=ROOT/"LEOPARTS_3_approved_6T30_images_exact.zip"
PROOF=ROOT/"data/leoparts_6t30_approved_images_sha256_20261123.json"
DRAFT=ROOT/"data/supplier_batch_20261123_card_drafts.json"
OUT=ROOT/"data/leoparts_6t30_photo_upload_and_publish_result.json"
SKUS=("6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2")
IDS={"6t30-solenoids-gen2":2338952761,"6t30-tcm-gen1":2338952880,"6t30-tcm-gen2":2338953074}
PRICE_MP={"6t30-solenoids-gen2":19880,"6t30-tcm-gen1":58850,"6t30-tcm-gen2":58850}
PRICE_DIRECT={"6t30-solenoids-gen2":11880,"6t30-tcm-gen1":35350,"6t30-tcm-gen2":35350}
STOCK={"6t30-solenoids-gen2":20,"6t30-tcm-gen1":3,"6t30-tcm-gen2":3}
def update(stage,sku,status,**extra):
    try:x=json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError,ValueError):x={"scope":"three_approved_6T30_only","channels":{}}
    x.setdefault("channels",{}).setdefault(stage,{})[sku]={"status":status,**extra,"time_utc":datetime.now(timezone.utc).isoformat()}
    x["updated_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"channel":stage,"sku":sku,"status":status,**extra},ensure_ascii=False,default=str),flush=True)
def safe(stage,sku,func):
    try:return func()
    except Exception as e:
        update(stage,sku,"error",error_type=type(e).__name__,error=str(e)[:220])
        return None
def preflight():
    if not ZIP.is_file():raise FileNotFoundError("Approved exact-three ZIP not yet uploaded")
    refs=json.loads(PROOF.read_text(encoding="utf-8"))
    specs={x["sku"]:x for x in json.loads(DRAFT.read_text(encoding="utf-8"))["confirmed_missing_product_drafts"]}
    mapped={a["sku"]:a for a in refs["assets"]}
    if set(mapped)!=set(SKUS) or set(specs)!=set(SKUS):
        raise RuntimeError("Missing or extra SKU, unsafe")
    paths={}
    import zipfile as zf
    with zf.ZipFile(ZIP) as archive:
        archive_names=set(archive.namelist())
        expected={f"photos/{sku}/{sku}_1.png" for sku in SKUS}|{"PHOTO_MAPPING_AND_PRICES.json"}
        if archive_names!=expected:raise ValueError("ZIP contains unexpected/duplicate files")
        note=json.loads(archive.read("PHOTO_MAPPING_AND_PRICES.json"))
        rows={x["sku"]:x for x in note["products"]}
        if set(rows)!=set(SKUS):raise ValueError("Owner ZIP mapping has unknown SKU")
        for sku in SKUS:
            row=rows[sku];item=mapped[sku];name=f"photos/{sku}/{sku}_1.png"
            raw=archive.read(name)
            if hashlib.sha256(raw).hexdigest()!=item["sha256"] or item["sha256"]!=row["sha256"]:
                raise ValueError("Approved original SHA256 mismatch "+sku)
            if (row["marketplace_price_rub"]!=PRICE_MP[sku] or row["avito_site_price_rub"]!=PRICE_DIRECT[sku] or
                row["stock"]!=STOCK[sku]):raise ValueError("Approved price or quantity mismatch "+sku)
            with Image.open(io.BytesIO(raw)) as picture:
                if picture.format!="PNG" or min(picture.size)<600:raise ValueError("Not full-res PNG "+sku)
            dst=ROOT/name;dst.parent.mkdir(parents=True,exist_ok=True);dst.write_bytes(raw)
            paths[sku]=str(dst)
            update("source",sku,"verified",sha256=item["sha256"],bytes=len(raw))
    return paths,specs

def publish_release(paths):
    urls={}
    for sku in SKUS:
        u=safe("media_host",sku,lambda sku=sku:photo_host.upload_file(paths[sku],sku+"_1.png"))
        if u and u.startswith("https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/"):
            update("media_host",sku,"verified",url=u);urls[sku]=u
    return urls

def run_wb(urls):
    cards=wb.list_cards()
    if len(cards)<110:raise ValueError("WB card paging incomplete")
    for sku in SKUS:
        rows=[c for c in cards if c.get("vendorCode")==sku]
        if len(rows)!=1 or int(rows[0].get("nmID") or 0)!=IDS[sku]:
            update("wb",sku,"blocked_unverified_exact_card",count=len(rows));continue
        if sku not in urls:update("wb",sku,"blocked_no_approved_image_url");continue
        def push():
            return wb.update_media(IDS[sku],[urls[sku]])
        a=safe("wb",sku,push)
        if a is not None:
            update("wb",sku,"media_submitted",nmID=IDS[sku],response_error=a.get("error"))
    for delay in (5,12,15):
        time.sleep(delay)
        fresh=wb.list_cards()
        found={c.get("vendorCode"):c for c in fresh if c.get("vendorCode") in SKUS}
        if all(len((found.get(sku) or {}).get("photos") or [])>0 for sku in SKUS):break
    for sku in SKUS:
        c=found.get(sku) or {}
        update("wb_final",sku,"image_visible" if c.get("photos") else "image_not_indexed",
               nmID=c.get("nmID"),photo_count=len(c.get("photos") or []))

def market_category(biz):
    """Read original Gen1 offer category ID from seller, never guess another taxonomy."""
    page=None
    for _ in range(8):
        params={"limit":100}
        if page:params["pageToken"]=page
        code,body=ym._request("POST",f"/v2/businesses/{biz}/offer-mappings",
                              params=params,json_body={"archived":False})
        if code!=200:raise RuntimeError("Market mapping read failed "+str(code))
        result=body.get("result") or {}
        for m in result.get("offerMappings") or []:
            offer=m.get("offer") or {};mapping=m.get("mapping") or {}
            if offer.get("offerId")=="6t30":
                return offer.get("marketCategoryId") or mapping.get("marketCategoryId")
        page=(result.get("paging") or {}).get("nextPageToken")
        if not page:break
    return None

def run_market(urls,specs):
    biz=ym.get_business_id()
    present=ym.get_all_offers(archived=False)
    if len(present)<100:raise ValueError("Market business catalog incomplete")
    category=market_category(biz)
    if not category:
        for sku in SKUS:update("market",sku,"blocked_category_unconfirmed")
        return
    update("market_category","6t30","verified_existing_gen1",category=category)
    for sku in SKUS:
        if sku not in urls:update("market",sku,"blocked_no_approved_image_url");continue
        matches=[x for x in present if x.get("sku")==sku]
        if len(matches)>1:update("market",sku,"blocked_duplicate");continue
        p=specs[sku]
        if not matches:
            def create():
                d=({"length":12,"width":9,"height":8,"weight":0.8} if sku==SKUS[0]
                   else {"length":30,"width":25,"height":12,"weight":3.5})
                offer={"offerId":sku,"name":p["name_ru"],"description":p["description"],
                       "pictures":[urls[sku]],"vendor":"Нет бренда","vendorCode":sku,
                       "marketCategoryId":int(category),"weightDimensions":d}
                code,body=ym._request("POST",f"/v2/businesses/{biz}/offer-mappings/update",
                                 json_body={"offerMappings":[{"offer":offer}]})
                if code!=200 or body.get("status")!="OK" or body.get("errors"):
                    raise RuntimeError("Market offer API rejection "+str(body.get("errors"))[:180])
                return True
            if not safe("market",sku,create):continue
            update("market",sku,"creation_submitted")
        else:update("market",sku,"already_in_catalog")
    time.sleep(9)
    current=ym.get_all_offers(archived=False)
    for sku in SKUS:
        if len([p for p in current if p.get("sku")==sku])!=1:
            update("market_final",sku,"not_indexed_yet");continue
        update("market_final",sku,"business_catalog_verified")
        def submit_price():
            code,body=ym._request("POST",f"/v2/businesses/{biz}/offer-prices/updates",
                json_body={"offers":[{"offerId":sku,"price":{"value":PRICE_MP[sku],"currencyId":"RUR"}}]})
            if code!=200 or body.get("status")!="OK" or body.get("errors"):
                raise RuntimeError("Market business price rejected "+str(body.get("errors"))[:200])
            return True
        if safe("market_price",sku,submit_price):
            update("market_price",sku,"submitted",target=PRICE_MP[sku])
        safe("market_stock",sku,lambda sku=sku:ym.update_stocks({sku:STOCK[sku]}))
    time.sleep(5)
    for sku in SKUS:
        def check_price():
            code,body=ym._request("POST",f"/v2/businesses/{biz}/offer-prices",json_body={"offerIds":[sku]})
            if code!=200 or body.get("status")!="OK":raise RuntimeError("Market price readback rejected")
            res=[x for x in ((body.get("result") or {}).get("offers") or []) if x.get("offerId")==sku]
            if len(res)!=1:raise ValueError("Market price not indexed")
            val=float((res[0].get("price") or {}).get("value") or 0)
            update("market_price_final",sku,"verified" if val==PRICE_MP[sku] else "price_mismatch",
                   actual=val,expected=PRICE_MP[sku])
        safe("market_price_final",sku,check_price)

def run_tilda(paths,specs):
    rows=site_records()
    for sku in SKUS:
        existing=[x for x in rows.values() if x.get("sku")==sku]
        if len(existing)>1:update("tilda",sku,"blocked_duplicate");continue
        if len(existing)==1:
            update("tilda",sku,"already_exists",uid=existing[0].get("uid"),price=existing[0].get("price"))
            continue
        p=specs[sku]
        def create():
            filename="import_files/"+sku+"_1.jpg"
            binary,_=tc._image_bytes(paths[sku],3_000_000)
            entry={"id":sku,"title":p["name_ru"],"description":p["description"],
                   "price":PRICE_DIRECT[sku],"qty":STOCK[sku],"photo":""}
            imp=tc.build_import_xml([entry],{sku:[filename]}).replace("<Каталог>",'<Каталог СодержитТолькоИзменения="true">')
            off=tc.build_offers_xml([entry]).replace('СодержитТолькоИзменения="false"','СодержитТолькоИзменения="true"')
            if 'СодержитТолькоИзменения="true"' not in imp or 'СодержитТолькоИзменения="true"' not in off:
                raise ValueError("Full Tilda import not allowed")
            creds=("TILDA_COMMERCEML_URL","TILDA_COMMERCEML_USER","TILDA_COMMERCEML_PASSWORD")
            if not all(os.environ.get(s) for s in creds):raise RuntimeError("Tilda credentials absent")
            return tc.push(*(os.environ[s] for s in creds),imp,off,timeout=95,image_files=[(filename,binary)])
        ack=safe("tilda",sku,create)
        if ack is not None:update("tilda",sku,"import_accepted",steps=str(ack)[:500])
    for attempt in range(4):
        time.sleep(6)
        current=site_records()
        all_found=True
        for sku in SKUS:
            matches=[x for x in current.values() if x.get("sku")==sku]
            if len(matches)!=1:all_found=False;continue
            p=matches[0]
            gallery=p.get("gallery") or "[]"
            try:count=len(json.loads(gallery)) if isinstance(gallery,str) else len(gallery)
            except Exception:count=0
            price=float(p.get("price") or 0)
            update("tilda_final",sku,"verified" if price==PRICE_DIRECT[sku] and count>=1 else "incomplete",
                   price=price,target_price=PRICE_DIRECT[sku],images=count,uid=p.get("uid"))
        if all_found:break

def main():
    paths,specs=preflight()
    urls=publish_release(paths)
    if len(urls)!=3:raise RuntimeError("Cannot publish any channel with incomplete approved image upload")
    for task in (lambda:run_wb(urls),lambda:run_market(urls,specs),lambda:run_tilda(paths,specs)):
        try:task()
        except Exception as e:update("channel_error",task.__code__.co_firstlineno,"error",kind=type(e).__name__,reason=str(e)[:220])
    for sku in SKUS:
        update("ozon",sku,"deferred_required_Ozon_verified_product_attributes")
        update("avito",sku,"deferred_category_and_autoload")
if __name__=="__main__":
    try:main()
    except Exception as e:
        update("fatal","_all","error",type=type(e).__name__,reason=str(e)[:260]);raise
