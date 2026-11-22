"""Owner-approved one-product creation on WB:
main master SKU Dq500 -> unique WB vendorCode dq500-korpus-gladkiy,
because WB rejects Dq500 (collides with existing DQ500 kit).
Does not change existing products or prices; exact one-product price 1980 RUB.
"""
from __future__ import annotations
import json, os, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client
from openpyxl import load_workbook
import build_master_control, sync_master_control

SKU="dq500-korpus-gladkiy"
MASTER_SKU="Dq500"
PRICE=1980
REQUEST=ROOT/"data/dq500_wb_create_unique_20261123_request.json"
REPORT=ROOT/"data/dq500_wb_create_unique_20261123_result.json"
PHOTO_URLS=[
    "https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/Dq500_"+str(i)+".jpg"
    for i in (1,2,3,4)
]
TITLE="Корпус масляного фильтра DSG7 DQ500 гладкий"
DESCRIPTION=(
    "Гладкий корпус масляного фильтра мехатроника DSG7 DQ500. "
    "Комплектация: один корпус без фильтрующего элемента. "
    "Фильтр и комплект корпус+фильтр продаются отдельно. "
    "Перед покупкой сравните резьбу, посадочное место и конструкцию со снятой деталью. "
    "Маркировка для подбора: 0BH325183B. Внутренний артикул LEOPARTS: Dq500."
)
def stamp():return datetime.now(timezone.utc).isoformat()
def emit(field,value):
    try:r=json.loads(REPORT.read_text())
    except (OSError,ValueError):r={}
    r[field]=value;r["checked_utc"]=stamp()
    REPORT.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"phase":field,"data":value},ensure_ascii=False,default=str),flush=True)

def preflight_approval():
    p=json.loads(REQUEST.read_text())
    target={"mode":"create_if_no_unique_wb_card","master_sku":"Dq500",
        "wb_vendor_code":"dq500-korpus-gladkiy","price_rub":1980,
        "kind":"smooth_housing_without_filter","preserve_other_cards":True}
    if p!=target:raise ValueError("Unexpected request")
    if build_master_control.WB_ALIAS.get(SKU)!=MASTER_SKU:raise ValueError("Master WB alias not configured")
    if sync_master_control._WB_ALIAS_OZON_TO_VENDOR.get(MASTER_SKU)!=SKU:raise ValueError("Stock WB alias not configured")

def master_row():
    wb=load_workbook(ROOT/"data/master_control.xlsx",read_only=True,data_only=True)
    ws=wb["Товары"]
    matches=[(n,r) for n,r in enumerate(ws.iter_rows(min_row=2,values_only=True),2) if r and r[0]==MASTER_SKU]
    wb.close()
    if len(matches)!=1:raise ValueError("Missing or ambiguous canonical SKU Dq500")
    n,r=matches[0]
    name=str(r[5] or "")
    if not ("DQ500" in name and "корпус" in name.lower() and "фильтр" in name.lower()):raise ValueError("Master title is not the smooth housing")
    if int(r[7])!=PRICE:raise ValueError("Master price mismatch")
    if r[10] not in (None,"",PRICE):raise ValueError("Master WB price conflicts")
    if str(r[17] or "").strip().lower()=="нет":raise ValueError("Master forbids WB")
    if int(r[26] or 0)!=280:raise ValueError("Master cost unexpectedly changed")
    qty=int(r[9] or 0)
    if not 0<=qty<=10000:raise ValueError("Master stock is not valid")
    return {"row":n,"qty":qty,"name":name,"cost":int(r[26]),"price":PRICE}

def cards():
    allcards=wb_client.list_cards(limit=100)
    if len(allcards)<110:raise ValueError("WB card pagination incomplete")
    items=[c for c in allcards if str(c.get("vendorCode") or "").casefold()==SKU.casefold()]
    if len(items)>1:raise ValueError("WB vendorCode duplicated")
    return allcards,items[0] if items else None

def related(cards):
    by={str(c.get("vendorCode") or ""):c for c in cards}
    kit=by.get("DQ500")
    if not kit or int(kit.get("nmID") or 0)!=837952965:
        raise ValueError("Existing DQ500 kit not verified")
    if str(kit.get("vendorCode"))==SKU:raise ValueError("WB key collision")
    # Price API listing and known sibling in category Фильтры масляные (not fuel filters).
    sample=by.get("0BH325183BB")
    if not sample or int(sample.get("subjectID") or 0)!=4122:
        raise ValueError("WB oil-filter housing category sample unverified")
    protect={}
    for name in ("DQ500","0BH325183","0BH325183 B","0BH325183B","0BH325183BB","korpus-filtra-0bh325159"):
        c=by.get(name)
        protect[name]=int(c["nmID"]) if c else None
    return protect

def payload(barcode):
    return {
        "vendorCode":SKU,"title":TITLE,"description":DESCRIPTION,"brand":"Нет бренда",
        # Existing DQ500 housing+filter kit envelope used as conservative packaging dimensions; verify later physically.
        "dimensions":{"length":8,"width":6,"height":6,"weightBrutto":0.5},
        "sizes":[{"techSize":"0","wbSize":"","price":PRICE,"skus":[str(barcode)]}],
        "characteristics":[
            {"id":378533,"value":["Корпус масляного фильтра — 1 шт., фильтр не входит"]},
            {"id":5023,"value":["DQ500"]},
            {"id":4572490,"value":["0BH325183B"]},
            {"id":5522881,"value":[SKU]},
            {"id":15003115,"value":["легковые автомобили"]},
        ],
    }

def run():
    preflight_approval()
    info=master_row()
    allcards,existing=cards()
    protect=related(allcards)
    prior={}
    try:prior=json.loads(REPORT.read_text())
    except (OSError,ValueError):pass
    if existing:
        emit("preflight",{"status":"already_exists","nmID":existing.get("nmID"),"wb_vendor_code":SKU})
    elif (prior.get("submitted") or {}).get("accepted"):
        emit("preflight",{"status":"previous_request_accepted_do_not_resubmit","vendorCode":SKU})
    else:
        barcode=wb_client.generate_barcodes(1)
        if not isinstance(barcode,list) or len(barcode)!=1 or not str(barcode[0]).isdigit():
            raise ValueError("WB barcode could not be generated")
        emit("preflight",{"status":"ready_to_create","wb_vendor_code":SKU,"master_sku":MASTER_SKU,
            "price_rub":PRICE,"source_stock":info["qty"],"category_id":4122,"protected":protect,
            "image_count":len(PHOTO_URLS),"barcode_last4":str(barcode[0])[-4:]})
        response=wb_client.create_cards(4122,[payload(barcode[0])])
        if not isinstance(response,dict) or response.get("error") not in (None,False):
            raise ValueError("WB single-card creation not accepted")
        emit("submitted",{"accepted":True,"vendorCode":SKU,"price_rub":PRICE,
            "barcode_last4":str(barcode[0])[-4:]})
    found=None
    for n in range(13):
        _,found=cards()
        if found:break
        if n<12:time.sleep(15)
    if not found:
        problems=[]
        try:
            errors=wb_client.get_upload_errors(limit=100)
            for err in errors:
                errors_dict=err.get("errors") or {}
                if SKU in str(err.get("vendorCodes") or "") or SKU in str(errors_dict):
                    problems.append(str(errors_dict)[:320])
        except Exception as e:problems.append("error_read_unavailable:"+type(e).__name__)
        emit("creation_result",{"status":"not_verified_or_rejected","sku":SKU,"reason":problems,"nmID":None})
        return
    nmid=int(found["nmID"])
    emit("creation_result",{"status":"created_verified","sku":SKU,"nmID":nmid,
        "title":found.get("title"),"subject_id":found.get("subjectID"),
        "photos_count":len(found.get("photos") or [])})
    if len(found.get("photos") or [])==0:
        try:
            media=wb_client.update_media(nmid,PHOTO_URLS)
            emit("media",{"submitted":True,"urls_count":len(PHOTO_URLS),
                "api_error":media.get("error") if isinstance(media,dict) else None})
        except Exception as e:emit("media",{"submitted":False,"error_type":type(e).__name__})
    price_rows=[p for p in wb_client.get_prices(limit=1000) if p.get("vendorCode")==SKU]
    if len(price_rows)>1:raise ValueError("WB new card has duplicate price rows")
    if price_rows:
        row=price_rows[0]
        sizes=row.get("sizes") or []
        if len(sizes)!=1:raise ValueError("WB card has multiple prices")
        cur=float(sizes[0].get("discountedPrice") or 0)
        if abs(cur-PRICE)>1:
            pct=int(row.get("discount") or 0)
            if pct<0 or pct>=100:raise ValueError("WB new card discount invalid")
            base=int(PRICE*100/(100-pct)+0.5)
            answer=wb_client.update_prices([{"nmID":nmid,"price":base,"discount":pct}])
            emit("price_submission",{"submitted":True,"task_id":(answer.get("data") or {}).get("id")})
    else:
        answer=wb_client.update_prices([{"nmID":nmid,"price":PRICE,"discount":0}])
        emit("price_submission",{"submitted":True,"task_id":(answer.get("data") or {}).get("id")})
    codes=[str(v) for size in (found.get("sizes") or []) for v in (size.get("skus") or []) if str(v).isdigit()]
    if len(codes)==1 and os.environ.get("WB_WAREHOUSE_ID"):
        qty_now=master_row()["qty"]
        if qty_now!=info["qty"]:
            emit("stock",{"status":"source_changed_skip","was":info["qty"],"now":qty_now})
        else:
            try:
                wb_client.update_stocks(os.environ["WB_WAREHOUSE_ID"],[{"sku":codes[0],"amount":qty_now}])
                emit("stock",{"status":"submitted","stock_units":qty_now})
            except Exception as e:emit("stock",{"status":"not_verified","error_type":type(e).__name__})
    else:emit("stock",{"status":"not_verified_no_unique_barcode_or_warehouse"})
    time.sleep(10)
    _,last=cards()
    priced=[p for p in wb_client.get_prices(limit=1000) if p.get("vendorCode")==SKU]
    live=None
    if len(priced)==1 and len(priced[0].get("sizes") or [])==1:
        live=float(priced[0]["sizes"][0].get("discountedPrice"))
    ok=live is not None and abs(live-PRICE)<=1
    emit("final_readback",{"exists":last is not None,"nmID":last.get("nmID") if last else None,
        "price_rub":live,"target_price_verified":ok,
        "photos_count":len((last or {}).get("photos") or []),
        "master_sku":MASTER_SKU,"wb_vendor_code":SKU})
    if ok:
        wb=load_workbook(ROOT/"data/master_control.xlsx")
        ws=wb["Товары"]
        rows=[i for i in range(2,ws.max_row+1) if ws.cell(i,1).value==MASTER_SKU]
        if len(rows)!=1:raise ValueError("Master row changed during WB creation")
        cell=ws.cell(rows[0],11)
        if cell.value not in (None,"",PRICE):raise ValueError("Master WB price changed unexpectedly")
        if cell.value!=PRICE:
            cell.value=PRICE;wb.save(ROOT/"data/master_control.xlsx")
        wb.close()
        emit("master_sync",{"status":"price_recorded","sku":MASTER_SKU,
            "wb_vendor_code":SKU,"price":PRICE})
if __name__=="__main__":
    try:run()
    except Exception as e:
        trace=traceback.extract_tb(e.__traceback__)
        last=trace[-1] if trace else None
        emit("error",{"status":"blocked_or_unverified","type":type(e).__name__,
            "file":Path(last.filename).name if last else None,
            "line":last.lineno if last else None})
        sys.exit(1)
