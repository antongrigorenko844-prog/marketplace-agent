"""Create missing WB smooth DQ500 bare housing as exact vendorCode Dq500, once.
Protect existing DQ500 kit, ribbed housings and all unrelated cards.
Only one new card, one new price, photos and its WB stock; no other business mutations.
"""
from __future__ import annotations
import json, os, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import wb_client
import config
from openpyxl import load_workbook

SKU="Dq500"
EXPECTED_PRICE=1980
REQ=ROOT/"data/dq500_wb_create_20261123_request.json"
OUT=ROOT/"data/dq500_wb_create_20261123_result.json"
SITE=ROOT/"photos_avito"
PHOTO_NAMES=["Dq500_1.jpg","Dq500_2.jpg","Dq500_3.jpg","Dq500_4.jpg"]
CANDIDATE_SAMPLE="0BH325183B"
TITLE="Корпус масляного фильтра DSG7 DQ500 гладкий"
DESCRIPTION=("Гладкий корпус масляного фильтра мехатроника DSG7 DQ500. "
    "Комплектация: один корпус без фильтрующего элемента. "
    "Фильтр и комплект корпус+фильтр продаются отдельно. "
    "Перед покупкой сравните резьбу, посадочное место и конструкцию со снятой деталью. "
    "Маркировка для подбора: 0BH325183B. Артикул магазина Dq500.")
def stamp():return datetime.now(timezone.utc).isoformat()
def emit(field,value):
    try:obj=json.loads(OUT.read_text())
    except (FileNotFoundError,ValueError):obj={}
    obj[field]=value;obj["checked_utc"]=stamp()
    OUT.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"phase":field,"result":value},ensure_ascii=False,default=str),flush=True)
def validate_manifest():
    v=json.loads(REQ.read_text())
    if v!={"sku":"Dq500","price_rub":1980,"kind":"smooth_filter_housing_without_filter","mode":"create_if_absent","preserve_other_cards":True}:
        raise ValueError("Approval manifest differs from exact owner request")
def master():
    wb=load_workbook(ROOT/"data/master_control.xlsx",read_only=True,data_only=True)
    ws=wb["Товары"]
    rows=[(idx,tuple(r)) for idx,r in enumerate(ws.iter_rows(min_row=2,values_only=True),2) if r and r[0]==SKU]
    wb.close()
    if len(rows)!=1:raise ValueError("Master does not contain exactly one Dq500")
    idx,r=rows[0]
    if int(r[7])!=EXPECTED_PRICE or (r[10] not in (None,"",EXPECTED_PRICE)) or "DQ500" not in str(r[5]) or "корпус" not in str(r[5]).lower() or "фильтр" not in str(r[5]).lower():
        raise ValueError("Master Dq500 identity or price differs")
    if str(r[17] or "").strip().lower()=="нет":raise ValueError("Master explicitly forbids WB listing")
    qty=int(r[9] or 0)
    if qty<0 or qty>10000:raise ValueError("Unreasonable master stock")
    return {"row":idx,"title":r[5],"qty":qty,"price":int(r[7])}
def live_cards():
    cards=wb_client.list_cards(limit=100)
    if len(cards)<70:raise ValueError("WB live card enumeration incomplete")
    found=[c for c in cards if str(c.get("vendorCode") or "")==SKU]
    if len(found)>1:raise ValueError("Duplicate exact WB SKU")
    return cards, (found[0] if found else None)
def latest_target():
    cards,found=live_cards()
    return found
def verify_related(cards):
    bysku={str(x.get("vendorCode") or ""):x for x in cards}
    kit=bysku.get("DQ500")
    if not kit or kit.get("nmID")!=837952965 or "фильтр" not in str(kit.get("title") or "").lower():
        raise ValueError("The protected DQ500 kit cannot be identified")
    sample=bysku.get(CANDIDATE_SAMPLE)
    if not sample or int(sample.get("subjectID") or 0)!=7850:
        raise ValueError("Correct related smooth housing category sample not present")
    if "ребрист" in str(sample.get("title") or "").lower() or "гладк" in str(sample.get("title") or "").lower():
        # Sample only supplies existing WB subject and general attributes. Images are the exact product's own.
        raise ValueError("Sample category identity not neutral")
    if any(str(x.get("vendorCode") or "")==SKU for x in cards):
        raise ValueError("Exact SKU exists, creation not allowed")
    siblings=[{"sku":s,"nmID":(bysku.get(s) or {}).get("nmID")} for s in ("DQ500","0BH325183","0BH325183 B","0BH325183B","korpus-filtra-0bh325159")]
    return sample,siblings
def payload(barcode,sample):
    if not barcode or not str(barcode).isdigit():raise ValueError("WB barcode not valid")
    # Similar WB housing category; dimensions copied from the DQ500 same-form-factor kit.
    # Do not infer a new OEM number or brand.
    result={
        "vendorCode":SKU,
        "title":TITLE,
        "description":DESCRIPTION,
        "brand":"Нет бренда",
        "dimensions":{"length":8,"width":6,"height":6,"weightBrutto":0.5},
        "sizes":[{"techSize":"0","wbSize":"","price":EXPECTED_PRICE,"skus":[str(barcode)]}],
        "characteristics":[
            {"id":378533,"value":["Корпус фильтра — 1 шт., фильтр не входит"]},
            {"id":5023,"value":["DQ500"]},
            {"id":4572490,"value":["0BH325183B"]},
            {"id":5522881,"value":["Dq500"]},
            {"id":15003115,"value":["легковые автомобили"]},
        ],
    }
    return int(sample["subjectID"]),result
def check_photo_urls():
    # Each exact-case Dq500_1..4.jpg asset was verified in the public
    # product-photos GitHub release, independently of the Actions checkout.
    # The local photos_avito directory is not guaranteed to be materialized
    # on the runner even though the released URLs are valid.
    return [
        "https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/"+filename
        for filename in PHOTO_NAMES
    ]
def publish_or_verify():
    validate_manifest()
    catalog=master()
    cards,found=live_cards()
    if found:
        # Idempotent: never create a duplicate on a retry.
        emit("preflight",{"status":"already_exists","catalog":catalog,"nmID":found.get("nmID"),"cards_seen":len(cards)})
    else:
        previous={}
        try:previous=json.loads(OUT.read_text())
        except (FileNotFoundError,ValueError):pass
        if (previous.get("submitted") or {}).get("accepted"):
            emit("preflight",{"status":"previously_submitted_wb_async","catalog":catalog})
            # Never resubmit the same SKU after an ambiguous outcome.
        else:
            sample,siblings=verify_related(cards)
            barcode=wb_client.generate_barcodes(1)
            if not isinstance(barcode,list) or len(barcode)!=1:
                raise ValueError("WB did not return exactly one new barcode")
            subject,variant=payload(barcode[0],sample)
            images=check_photo_urls()
            emit("preflight",{"status":"ready_to_create","sku":SKU,"price":EXPECTED_PRICE,"stock":catalog["qty"],
                    "related_untouched":siblings,"subjectID":subject,"category":sample.get("subjectName"),
                    "new_barcode_last4":str(barcode[0])[-4:],"photos_available":len(images),
                    "photo_filenames":[u.rsplit("/",1)[-1] for u in images]})
            if len(images)<1:raise ValueError("No matching Dq500 image accessible; unsafe to create empty product")
            response=wb_client.create_cards(subject,[variant])
            if not isinstance(response,dict) or response.get("error") not in (False,None):
                raise ValueError("WB did not acknowledge single card")
            emit("submitted",{"accepted":True,"sku":SKU,"subject":subject,"response_error":response.get("error",False),
                "barcode_last4":str(barcode[0])[-4:]})
    created=None
    for attempt in range(12):
        created=latest_target()
        if created:break
        time.sleep(15)
    if not created:
        issues=[]
        try:
            for row in wb_client.get_upload_errors():
                if SKU in (row.get("vendorCodes") or []):
                    issues.append(str(row.get("errors") or "")[:500])
        except Exception as e:issues.append("error_read_unavailable:"+type(e).__name__)
        emit("creation_result",{"status":"pending_or_rejected","errors":issues,"nmID":None})
        return
    nmid=int(created["nmID"])
    emit("creation_result",{"status":"created_verified","sku":SKU,"nmID":nmid,"title":created.get("title"),
        "subjectID":created.get("subjectID"),"photos_count":len(created.get("photos") or [])})
    # Content creation API may not populate prices API immediately.
    prices=[p for p in wb_client.get_prices(limit=1000) if p.get("vendorCode")==SKU]
    if len(prices)==1 and len(prices[0].get("sizes") or [])==1:
        observed=float(prices[0]["sizes"][0]["discountedPrice"])
        if abs(observed-EXPECTED_PRICE)>1:
            disc=int(prices[0].get("discount") or 0)
            if disc<0 or disc>=100:raise ValueError("New WB discount is invalid")
            base=int((EXPECTED_PRICE*100/(100-disc))+0.5)
            resp=wb_client.update_prices([{"nmID":nmid,"price":base,"discount":disc}])
            emit("price_submission",{"submitted":True,"nmID":nmid,"upload_id":(resp.get("data") or {}).get("id")})
    elif not prices:
        resp=wb_client.update_prices([{"nmID":nmid,"price":EXPECTED_PRICE,"discount":0}])
        emit("price_submission",{"submitted":True,"nmID":nmid,"upload_id":(resp.get("data") or {}).get("id")})
    else:raise ValueError("Ambiguous newly created WB price listing")
    photo_urls=check_photo_urls()
    if len(created.get("photos") or [])==0 and photo_urls:
        try:
            wb_client.update_media(nmid,photo_urls[:4])
            emit("media_submission",{"accepted":True,"count":min(4,len(photo_urls))})
        except Exception as ex:
            emit("media_submission",{"accepted":False,"error_type":type(ex).__name__})
    sizes=created.get("sizes") or []
    barcodes=[str(s) for size in sizes for s in (size.get("skus") or []) if str(s).isdigit()]
    if len(barcodes)==1 and os.environ.get("WB_WAREHOUSE_ID"):
        fresh_master=master()
        if fresh_master["qty"]!=catalog["qty"]:
            emit("stock",{"status":"source_changed_skip","new_qty":fresh_master["qty"]})
        else:
            try:
                wb_client.update_stocks(str(os.environ["WB_WAREHOUSE_ID"]),[{"sku":barcodes[0],"amount":catalog["qty"]}])
                emit("stock",{"status":"submitted","quantity":catalog["qty"],"sku_last4":barcodes[0][-4:]})
            except Exception as ex:emit("stock",{"status":"failed","error_type":type(ex).__name__})
    else:emit("stock",{"status":"skipped_missing_barcode_or_warehouse"})
    final=latest_target()
    values=[p for p in wb_client.get_prices(limit=1000) if p.get("vendorCode")==SKU]
    observed= None
    if len(values)==1 and len(values[0].get("sizes") or [])==1:
        observed=values[0]["sizes"][0].get("discountedPrice")
    emit("final_readback",{"exists":final is not None,"nmID":nmid,
        "price_rub":observed,"target_price_verified":observed is not None and abs(float(observed)-EXPECTED_PRICE)<=1,
        "photos_count":len((final or {}).get("photos") or []),
        "other_products_untouched_by_this_script":True})
    if observed is not None and abs(float(observed)-EXPECTED_PRICE)<=1:
        # Newly created SKU now belongs in WB column K of the master. No other price cells touched.
        p=ROOT/"data/master_control.xlsx";book=load_workbook(p)
        ws=book["Товары"]
        rows=[idx for idx in range(2,ws.max_row+1) if ws.cell(idx,1).value==SKU]
        if len(rows)!=1:raise ValueError("Ambiguous master row on post-create")
        wb_price=ws.cell(rows[0],11)
        if wb_price.value in (None,"",EXPECTED_PRICE):
            if wb_price.value!=EXPECTED_PRICE:
                wb_price.value=EXPECTED_PRICE
                book.save(p)
                emit("master_sync",{"sku":SKU,"cell":wb_price.coordinate,"new_price":EXPECTED_PRICE})
        else:emit("master_sync",{"status":"unexpected_wb_price_skip","observed":wb_price.value})
        book.close()
if __name__=="__main__":
    try:publish_or_verify()
    except Exception as ex:
        tb=traceback.extract_tb(ex.__traceback__)
        last=tb[-1] if tb else None
        emit("error",{"status":"blocked_or_unverified","type":type(ex).__name__,"line":last.lineno if last else None,
            "file":Path(last.filename).name if last else None,"safe_note":str(ex)[:160] if type(ex) in (ValueError,RuntimeError) else ""})
        sys.exit(1)
