"""Owner authorized 3 NEW 6T30-6T50 Gen2 solenoids and Gen1/Gen2 TEHCM WB cards.
Do not touch existing 6t30 Gen1 solenoids or any other cards.
New photo uploads from the owner's supplied photos still require materialization.
"""
from __future__ import annotations
import json,sys,os,time,traceback
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client
MANIFEST=ROOT/"data/6t_family_20261123_publish_wb_request.json"
DRAFT=ROOT/"data/supplier_batch_20261123_card_drafts.json"
OUT=ROOT/"data/6t_family_20261123_publish_wb_result.json"
SKUS=["6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2"]
SUBJECT={"6t30-solenoids-gen2":7807,"6t30-tcm-gen1":7845,"6t30-tcm-gen2":7845}
def emit(name,v):
    try:o=json.loads(OUT.read_text())
    except (FileNotFoundError,ValueError):o={}
    o[name]=v;o["updated_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(o,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"stage":name,"data":v},ensure_ascii=False,default=str),flush=True)
def confirmed():
    req=json.loads(MANIFEST.read_text())
    want={"skus":SKUS,"channels":["wildberries"],"create_if_absent_only":True,
          "protect_existing_gen1":"6t30","price_rub":[19880,58850,58850],"inventory":[20,3,3]}
    if any(req.get(k)!=v for k,v in want.items()):raise ValueError("owner scope manifest mismatch")
    spec=json.loads(DRAFT.read_text())["confirmed_missing_product_drafts"]
    by={p["sku"]:p for p in spec}
    if set(by)!=set(SKUS):raise ValueError("missing draft or duplicate SKU")
    for i,sku in enumerate(SKUS):
        p=by[sku]
        if p["prices_rub"]["wildberries"]!=want["price_rub"][i] or p["stock_LEOPARTS"]!=want["inventory"][i]:
            raise ValueError("owner price or stock changed")
        if not all(s in p["name_ru"] for s in ["6T30","6T40","6T45","6T50"]):raise ValueError("missing family code")
        if not p["description"].endswith("Соленоиды проверены на стенде."):raise ValueError("bench statement missing")
    return by
def card_payload(sku,p,barcode):
    if sku=="6t30-solenoids-gen2":
        title="Соленоиды АКПП 6T30 6T40 6T45 6T50 Gen2, 7 шт"
        completeness="Комплект соленоидов АКПП второго поколения — 7 шт."
        dim={"length":12,"width":9,"height":8,"weightBrutto":0.8} # approximate, pending packing measurement
        subject=7807
    else:
        title=f"Блок АКПП 6T30 6T40 6T45 6T50 с соленоидами {sku[-4:].title()}"
        completeness="Блок управления АКПП с соленоидами — 1 шт."
        dim={"length":30,"width":25,"height":12,"weightBrutto":3.5} # approximate, pending packaging measurement
        subject=7845
    if len(title)>60:raise ValueError("WB title length exceeds 60")
    return subject,{
        "vendorCode":sku,"title":title,"description":p["description"],
        "brand":"Нет бренда","dimensions":dim,
        "sizes":[{"techSize":"0","wbSize":"","price":int(p["prices_rub"]["wildberries"]),"skus":[str(barcode)]}],
        "characteristics":[
            {"id":15003115,"value":["легковые автомобили"]},
            {"id":5023,"value":["6T30","6T40","6T45","6T50"]},
            {"id":378533,"value":[completeness]}
        ]
    }
def get_all():
    rows=wb_client.list_cards(limit=100)
    if len(rows)<110:raise ValueError("WB content paging appeared incomplete")
    return rows
def run():
    by=confirmed()
    orig=get_all()
    seen={}
    for r in orig:
        k=str(r.get("vendorCode") or "").casefold()
        if k in [x.casefold() for x in SKUS]:
            if k in seen:raise ValueError("duplicate existing WB SKU")
            seen[k]=r
    out={}
    # Do not create a duplicate on retry after an asynchronously accepted submission.
    try:prior=json.loads(OUT.read_text())
    except (FileNotFoundError,ValueError):prior={}
    for sku in SKUS:
        x=seen.get(sku.casefold())
        if x:
            out[sku]={"status":"existing_verified","nmID":x.get("nmID")}
            continue
        if (prior.get("create_submission") or {}).get(sku,{}).get("accepted"):
            out[sku]={"status":"previously_submitted_skip"};continue
        barcodes=wb_client.generate_barcodes(1)
        if not isinstance(barcodes,list) or len(barcodes)!=1 or not str(barcodes[0]).isdigit():
            out[sku]={"status":"failed_to_generate_barcode"};continue
        subject,payload=card_payload(sku,by[sku],barcodes[0])
        try:
            result=wb_client.create_cards(subject,[payload])
            if not isinstance(result,dict) or result.get("error") not in (None,False):
                raise ValueError("WB unacknowledged create response")
            out[sku]={"accepted":True,"subject":subject,"barcode_last4":str(barcodes[0])[-4:],
                "catalog_photo_count":0,"dimensions_are_estimates":True}
        except Exception as e:
            out[sku]={"accepted":False,"error_type":type(e).__name__}
        emit("create_submission",out)
    for n in range(12):
        rows=get_all()
        found={str(x.get("vendorCode")):x for x in rows if x.get("vendorCode") in SKUS}
        if all(s in found for s in SKUS):break
        if n<11:time.sleep(15)
    result={}
    for sku in SKUS:
        x=found.get(sku)
        if x:
            result[sku]={"status":"card_created","nmID":x.get("nmID"),"title":x.get("title"),
                         "photos_count":len(x.get("photos") or []),"category_id":x.get("subjectID"),
                         "price_target":by[sku]["prices_rub"]["wildberries"],
                         "stock_target":by[sku]["stock_LEOPARTS"]}
        else:result[sku]={"status":"not_visible_yet","need_creation_errors":True}
    emit("creation",result)
    # Set only observed new card prices, and only if WB indexes them.
    price_rows={str(r.get("vendorCode")):r for r in wb_client.get_prices(limit=1000) if r.get("vendorCode") in SKUS}
    prices={}
    for sku in SKUS:
        x=found.get(sku);pr=price_rows.get(sku)
        if not x:continue
        target=by[sku]["prices_rub"]["wildberries"]
        if pr and len(pr.get("sizes") or [])==1:
            before=float(pr["sizes"][0].get("discountedPrice") or 0)
            if abs(before-target)<=1:
                prices[sku]={"observed":before,"verified":True,"status":"already_correct"}
                continue
            if before not in (0,target):
                prices[sku]={"status":"unexpected_price","observed":before};continue
        # Fresh WB card price is usually 0/absent; sending only the newly created nmID.
        try:
            ack=wb_client.update_prices([{"nmID":int(x["nmID"]),"price":target,"discount":0}])
            prices[sku]={"status":"submitted","task_id":(ack.get("data") or {}).get("id")}
        except Exception as e:prices[sku]={"status":"api_error","error_type":type(e).__name__}
    emit("prices",prices)
    # Stock only for exact created IDs/barcodes. If WB cards lack photo, the listing may remain under moderation.
    stocks={}
    if os.getenv("WB_WAREHOUSE_ID"):
        for sku in SKUS:
            card=found.get(sku)
            if not card:continue
            barcode=[s for z in (card.get("sizes") or []) for s in (z.get("skus") or []) if str(s).isdigit()]
            if len(barcode)!=1:
                stocks[sku]={"status":"missing_barcode"};continue
            qty=int(by[sku]["stock_LEOPARTS"])
            try:
                wb_client.update_stocks(os.environ["WB_WAREHOUSE_ID"],[{"sku":str(barcode[0]),"amount":qty}])
                stocks[sku]={"status":"submitted","quantity":qty}
            except Exception as e:stocks[sku]={"status":"api_error","error_type":type(e).__name__}
    else:stocks={"status":"warehouse_secret_missing"}
    emit("stocks",stocks)
    fresh=get_all()
    final={str(x.get("vendorCode")):x for x in fresh if x.get("vendorCode") in SKUS}
    listed={str(r.get("vendorCode")):r for r in wb_client.get_prices(limit=1000) if r.get("vendorCode") in SKUS}
    readback={}
    for sku in SKUS:
        card=final.get(sku);price=listed.get(sku);sizes=(price or {}).get("sizes") or []
        observed=float(sizes[0].get("discountedPrice")) if len(sizes)==1 else None
        expected=by[sku]["prices_rub"]["wildberries"]
        readback[sku]={"card_created":bool(card),"nmID":(card or {}).get("nmID"),
                       "photo_count":len((card or {}).get("photos") or []),"price":observed,
                       "price_verified":observed is not None and abs(observed-expected)<1,
                       "stock_submission":stocks.get(sku)}
    emit("final_readback",readback)
    # Wildberries import is asynchronous. HTTP accepted != published:
    # a failed card must make this job fail, never green "Success".
    incomplete=[sku for sku,row in readback.items() if not row["card_created"]]
    if incomplete:
        failures={}
        try:
            for row in wb_client.get_upload_errors(limit=100):
                raw=json.dumps(row,ensure_ascii=False)
                for sku in incomplete:
                    if sku in raw:
                        failures.setdefault(sku,[]).append(str(row.get("errors") or "")[:1300])
        except Exception as e:
            failures["_read_error"]=type(e).__name__
        emit("creation_failures",{"status":"NOT_CREATED","skus":incomplete,"wildberries_errors":failures})
        raise RuntimeError("WB accepted upload request but did not create product cards")
    return readback
if __name__=="__main__":
    try:run()
    except Exception as e:
        trace=traceback.extract_tb(e.__traceback__)
        last=trace[-1] if trace else None
        emit("fatal",{"type":type(e).__name__,"file":Path(last.filename).name if last else None,"line":last.lineno if last else None})
        sys.exit(1)
