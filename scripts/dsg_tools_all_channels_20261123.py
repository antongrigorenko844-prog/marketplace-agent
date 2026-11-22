"""Owner-approved two DSG workshop tools, independent five-channel publication.

Only SKUs tool-dq200-acc-restore / tool-dsg-pressure-hp. Do not edit any other
offer; do not claim that an accepted async request means published. Keep Avito
feed DEFERRED until account autoload subscription and proper tool-category template.
Keep Ozon DEFERRED unless legally verified product-specific TN VED is supplied.
"""
import os, sys, json, time, hashlib, io, traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from PIL import Image
import photo_host
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym
import tilda_commerceml as tc
from scripts.leoparts_direct_operation_nov21 import site_records

SKUS=("tool-dq200-acc-restore","tool-dsg-pressure-hp")
MANIFEST=ROOT/"drafts/new-products/dsg-tools-20261120/product_cards.json"
REQUEST=ROOT/"data/dsg_tools_all_channels_20261123_request.json"
OUT=ROOT/"data/dsg_tools_all_channels_20261123_result.json"
PRICE_MP={SKUS[0]:38800,SKUS[1]:28800}
PRICE_DIRECT={SKUS[0]:24800,SKUS[1]:18800}
CAT_WB=6370
CAT_MARKET=82184337
CAT_OZON=83250454
TYPE_OZON=97469

def when():return datetime.now(timezone.utc).isoformat()
def emit(sku,channel,**result):
    try:d=json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError):d={"started_utc":when(),"mode":"scope_two_DSG_service_tools","channels":{}}
    d.setdefault("channels",{}).setdefault(sku,{})[channel]=dict(result,checked_at_utc=when())
    d["updated_at_utc"]=when()
    tmp=OUT.with_suffix(".tmp");tmp.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    tmp.replace(OUT)
    print(json.dumps({"sku":sku,"channel":channel,**result},ensure_ascii=False,default=str),flush=True)

def data():
    approved=json.loads(MANIFEST.read_text(encoding="utf-8"))
    req=json.loads(REQUEST.read_text(encoding="utf-8"))
    if set(req.get("skus") or [])!=set(SKUS) or req.get("owner_approved") is not True or req.get("mode") not in ("inspect","apply"):
        raise ValueError("Two SKU owner approval/scope mismatch")
    docs={p["seller_sku_candidate"]:p for p in approved["products"]}
    if set(docs)!=set(SKUS):raise ValueError("Product draft SKU mismatch")
    for sku,p in docs.items():
        prices=p["commercial_inputs"]["prices_rub"]
        if (prices["ozon"]!=PRICE_MP[sku] or prices["wildberries"]!=PRICE_MP[sku] or
            prices["yandex_market"]!=PRICE_MP[sku] or prices["avito"]!=PRICE_DIRECT[sku] or
            prices["site"]!=PRICE_DIRECT[sku] or p["commercial_inputs"]["available_sets"]!=3):
            raise ValueError("Owner price/stock scope changed")
        if p["media"]["approved_for_publication_by_user"] is not True:
            raise ValueError("Owner image approval absent")
        if any(not (ROOT/f["path"]).is_file() for f in p["media"]["repo_assets"]):
            raise ValueError("Approved repo image missing")
        for media in p["media"]["repo_assets"]:
            content=(ROOT/media["path"]).read_bytes()
            if hashlib.sha256(content).hexdigest()!=media["sha256"]:raise ValueError("Approved image hash changed")
            with Image.open(io.BytesIO(content)) as im:
                if im.format!="PNG":raise ValueError("Non-PNG photo")
                if min(im.size)<600:raise ValueError("Cover resolution too low")
    return req,docs

def title(p,channel):
    return p["content"]["customer_copy"]["channels"][channel]["title"]
def desc(p):
    # Do not claim a precise working pressure, material, couplings, or exact
    # compatibility until manufacturer technical sheet is available.
    return p["content"]["customer_copy"]["full_description"]

def pictures(sku,p,upload):
    entries=p["media"]["repo_assets"]
    if not upload:return []
    urls=[]
    for i,entry in enumerate(entries,1):
        path=ROOT/entry["path"]
        photo=photo_host.upload_file(str(path),sku+"_"+str(i)+".png")
        urls.append(photo)
    return urls

def ozon(sku,p,mode):
    existing=[x for x in oz.list_products() if str(x.get("offer_id") or "")==sku]
    if len(existing)>1:raise ValueError("Ozon duplicate")
    if existing:
        emit(sku,"ozon",status="already_created",product_id=existing[0].get("product_id"),note="No modifications")
        return
    # Required customs classification cannot be inferred just from the title
    # or a generated illustration. Do not invent 902620 or 8205 codes.
    emit(sku,"ozon",status="blocked_for_required_product_tnved",
         category_id=CAT_OZON,type_id=TYPE_OZON,target_price=PRICE_MP[sku],
         missing="Product-specific confirmed TN VED / brand / exact kit specification",
         photos_ready=True,created=False)

def wb_products(docs,urls,mode):
    all_=wb.list_cards()
    if len(all_)<110:raise ValueError("WB incomplete pagination")
    found={}
    for sku in SKUS:
        matches=[c for c in all_ if str(c.get("vendorCode") or "")==sku]
        if len(matches)>1:raise ValueError("WB duplicate "+sku)
        if matches:found[sku]=matches[0]
    fields=wb.get_subject_characteristics(CAT_WB)
    mandatory=[x for x in fields if x.get("required") or x.get("isRequired") or x.get("is_required")]
    # Stop rather than guessing mandatory values of unfamiliar type/category.
    known={"Бренд","Комплектация","Назначение","Модель","Артикул производителя"}
    surprising=[str(x.get("name")) for x in mandatory if str(x.get("name")) not in known]
    for sku in SKUS:
        p=docs[sku]
        card=found.get(sku)
        if card:
            id_=int(card["nmID"])
            if urls.get(sku) and mode=="apply" and not card.get("photos"):
                r=wb.update_media(id_,urls[sku])
                emit(sku,"wb",status="media_submitted_waiting_moderation",nmID=id_,count=len(urls[sku]),error=r.get("error"))
            else:emit(sku,"wb",status="already_created",nmID=id_,photo_count=len(card.get("photos") or []))
            continue
        if mode=="inspect":
            emit(sku,"wb",status="not_created",subject_id=CAT_WB)
            continue
        if surprising:
            emit(sku,"wb",status="blocked_unknown_mandatory_fields",fields=surprising)
            continue
        barcode=wb.generate_barcodes(1)
        if len(barcode)!=1:raise ValueError("WB barcode missing")
        c=p["commercial_inputs"];attrs=[]
        # Only choose known subject mandatory values if required.
        for item in mandatory:
            name=item.get("name");key=item.get("charcID") or item.get("id")
            if name=="Комплектация": value=["Специализированный набор инструментов — 1 комплект"]
            elif name=="Назначение": value=["Ремонт и диагностика трансмиссий"]
            elif name=="Модель": value=["DSG"]
            elif name=="Бренд": value=["Нет бренда"]
            elif name=="Артикул производителя":value=[sku]
            else:continue
            if key:attrs.append({"id":key,"value":value})
        variant={"vendorCode":sku,"title":title(p,"wildberries")[:60],
            "description":desc(p),"brand":"Нет бренда",
            "dimensions":{"length":int(c["package_length_mm"]/10),"width":int(c["package_width_mm"]/10),
                          "height":int(c["package_height_mm"]/10),"weightBrutto":c["packed_weight_g"]/1000},
            "sizes":[{"techSize":"0","wbSize":"","price":PRICE_MP[sku],"skus":[str(barcode[0])]}]}
        if attrs:variant["characteristics"]=attrs
        ack=wb.create_cards(CAT_WB,[variant])
        if ack.get("error"):raise RuntimeError("WB upload rejected")
        emit(sku,"wb",status="creation_submitted_pending_indexing",subject_id=CAT_WB,
             image_count=0,sku=sku,barcode_last4=str(barcode[0])[-4:])
    if mode!="apply":return
    for wait in (4,8,12,15):
        if all(s in found for s in SKUS):break
        time.sleep(wait)
        fresh=wb.list_cards()
        found={c.get("vendorCode"):c for c in fresh if c.get("vendorCode") in SKUS}
    for sku in SKUS:
        card=found.get(sku)
        if card:
            id_=int(card["nmID"])
            if urls.get(sku) and not card.get("photos"):
                try:
                    ack=wb.update_media(id_,urls[sku])
                    emit(sku,"wb",status="created_media_submitted",nmID=id_,photo_count=len(urls[sku]),media_api_error=ack.get("error"))
                except Exception as e:emit(sku,"wb",status="created_media_error",nmID=id_,error_type=type(e).__name__)
            else:
                emit(sku,"wb",status="created",nmID=id_,photo_count=len(card.get("photos") or []))
        else:
            emit(sku,"wb",status="creation_unverified",note="Do not retry blindly")

def market(docs,urls,mode):
    business=ym.get_business_id()
    rows=ym.get_all_offers(archived=False)
    for sku in SKUS:
        present=[x for x in rows if x.get("sku")==sku]
        if len(present)>1:raise ValueError("Market duplicate "+sku)
        if present:
            emit(sku,"market",status="already_in_business",market_sku=present[0].get("market_sku"))
            continue
        if mode=="inspect":
            emit(sku,"market",status="not_created",category_id=CAT_MARKET)
            continue
        p=docs[sku];c=p["commercial_inputs"]
        if not urls.get(sku):raise ValueError("Published photos unavailable "+sku)
        offer={"offerId":sku,"name":title(p,"yandex_market"),
               "description":desc(p),"pictures":urls[sku],"vendor":"Нет бренда",
               "vendorCode":sku,"marketCategoryId":CAT_MARKET,
               "weightDimensions":{"length":c["package_length_mm"]/10,"width":c["package_width_mm"]/10,
                                   "height":c["package_height_mm"]/10,"weight":c["packed_weight_g"]/1000}}
        code,response=ym._request("POST",f"/v2/businesses/{business}/offer-mappings/update",
            json_body={"offerMappings":[{"offer":offer}]})
        if code!=200 or response.get("status")!="OK" or response.get("errors"):
            emit(sku,"market",status="api_rejected",http_status=code,errors=str(response.get("errors"))[:500])
            continue
        emit(sku,"market",status="creation_submitted_pending_readback",category_id=CAT_MARKET)
    if mode!="apply":return
    for t in (4,7,9):
        time.sleep(t)
        rows=ym.get_all_offers(archived=False)
        if all(any(r.get("sku")==sku for r in rows) for sku in SKUS):break
    for sku in SKUS:
        match=[r for r in rows if r.get("sku")==sku]
        if len(match)==1:
            emit(sku,"market",status="business_catalog_created",market_sku=match[0].get("market_sku"))
            try:
                ok,fail=ym.update_prices({sku:(PRICE_MP[sku],None)})
                emit(sku,"market_price",status="submitted" if ok==1 and not fail else "error",failure=fail)
            except Exception as e:emit(sku,"market_price",status="error",error_type=type(e).__name__)
            try:
                ok,fail=ym.update_stocks({sku:3})
                emit(sku,"market_stock",status="submitted" if ok==1 and not fail else "error",failure=fail)
            except Exception as e:emit(sku,"market_stock",status="error",error_type=type(e).__name__)
        else:emit(sku,"market",status="not_indexed_yet")

def tilda(docs,mode):
    rows=site_records()
    for sku in SKUS:
        matches=[x for x in rows.values() if x.get("sku")==sku]
        if len(matches)>1:raise ValueError("Tilda duplicate "+sku)
        if matches:
            emit(sku,"tilda",status="already_exists",uid=matches[0].get("uid"),price=matches[0].get("price"))
            continue
        if mode=="inspect":
            emit(sku,"tilda",status="not_created")
            continue
        p=docs[sku];img=[]
        for i,item in enumerate(p["media"]["repo_assets"],1):
            name=f"import_files/{sku}_{i}.jpg"
            data,_=tc._image_bytes(str(ROOT/item["path"]),3_000_000)
            img.append((name,data))
        files=[name for name,_ in img]
        entry={"id":sku,"title":p["content"]["customer_copy"]["title"],
               "description":desc(p),"price":PRICE_DIRECT[sku],"qty":3,"photo":""}
        main=tc.build_import_xml([entry],{sku:files}).replace("<Каталог>","<Каталог СодержитТолькоИзменения=\"true\">")
        offers=tc.build_offers_xml([entry]).replace('СодержитТолькоИзменения="false"','СодержитТолькоИзменения="true"')
        if "СодержитТолькоИзменения=\"true\"" not in main or "СодержитТолькоИзменения=\"true\"" not in offers:raise ValueError("Full catalog import prohibited")
        if not all(os.environ.get(k) for k in ("TILDA_COMMERCEML_URL","TILDA_COMMERCEML_USER","TILDA_COMMERCEML_PASSWORD")):raise RuntimeError("Tilda credentials unavailable")
        response=tc.push(os.environ["TILDA_COMMERCEML_URL"],os.environ["TILDA_COMMERCEML_USER"],os.environ["TILDA_COMMERCEML_PASSWORD"],
                         main,offers,timeout=95,image_files=img)
        emit(sku,"tilda",status="import_accepted_pending_readback",photos_sent=len(img),steps=response)
        found=[]
        for seconds in (3,8,10):
            time.sleep(seconds)
            found=[x for x in site_records().values() if x.get("sku")==sku]
            if found:break
        if len(found)==1:
            price=float(found[0].get("price") or 0)
            emit(sku,"tilda",status="created_verified" if price==PRICE_DIRECT[sku] else "created_price_unverified",
                 uid=found[0].get("uid"),price=price,approved_price=PRICE_DIRECT[sku],
                 photo_count=len(found[0].get("gallery") or []))
        else:emit(sku,"tilda",status="not_indexed_yet")

def run():
    req,docs=data()
    mode=req["mode"]
    for sku in SKUS:emit(sku,"preflight",status="owner_approved",price_marketplaces=PRICE_MP[sku],
                        price_avito_site=PRICE_DIRECT[sku],photos=len(docs[sku]["media"]["repo_assets"]),stock=3)
    image_urls={}
    if mode=="apply":
        for sku in SKUS:
            try:
                image_urls[sku]=pictures(sku,docs[sku],upload=True)
                emit(sku,"images",status="published_to_public_release",urls=image_urls[sku])
            except Exception as e:
                emit(sku,"images",status="error",reason=str(e)[:220],error_type=type(e).__name__)
    else:
        for sku in SKUS:emit(sku,"images",status="repo_only_preflight")
    for sku in SKUS:
        try:ozon(sku,docs[sku],mode)
        except Exception as e:emit(sku,"ozon",status="read_error",error_type=type(e).__name__,reason=str(e)[:200])
    try:wb_products(docs,image_urls,mode)
    except Exception as e:
        for sku in SKUS:emit(sku,"wb",status="error",error_type=type(e).__name__,reason=str(e)[:220])
    try:market(docs,image_urls,mode)
    except Exception as e:
        for sku in SKUS:emit(sku,"market",status="error",error_type=type(e).__name__,reason=str(e)[:220])
    try:tilda(docs,mode)
    except Exception as e:
        for sku in SKUS:emit(sku,"tilda",status="error",error_type=type(e).__name__,reason=str(e)[:220])
    for sku in SKUS:
        emit(sku,"avito",status="deferred_autoload_and_tool_template",
             reason="Avito tariff/autoload unconfirmed. Existing feed classifies transmission parts, cannot safely publish workshop tools in wrong category.",
             price=PRICE_DIRECT[sku],images=image_urls.get(sku,[]))
    print(json.dumps({"run_finished_at_utc":when(),"skus":SKUS,"mode":mode},ensure_ascii=False),flush=True)

if __name__=="__main__":
    try:run()
    except Exception as e:
        print("FATAL "+type(e).__name__+" "+str(e)[:350],flush=True)
        sys.exit(1)
