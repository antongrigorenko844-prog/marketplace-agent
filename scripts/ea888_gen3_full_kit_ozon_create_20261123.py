"""One owner-approved full EA888 Gen3 oil-filter kit. Ozon SKU new; old unchanged.
Clone category of existing EA888gen3 housing+valve set, not product id.
"""
import os,sys,time,json,traceback
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client,ozon_new_products
SKU="EA888gen3-filter-kit"
SAMPLE="EA888gen3"
MANIFEST=ROOT/"data/ea888_gen3_filter_kit_approved_20261123.json"
OUT=ROOT/"data/ea888_gen3_full_kit_ozon_result_20261123.json"
PHOTO0="https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/EA888gen3_1.jpg"
def note(stage,x):
    try:o=json.loads(OUT.read_text())
    except (OSError,ValueError):o={}
    o[stage]=x;o["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(o,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"stage":stage,"data":x},ensure_ascii=False,default=str),flush=True)
def safe_sku(s):
    return str(s or "").strip() == SKU
def run():
    a=json.loads(MANIFEST.read_text())
    if a.get("sku")!=SKU or a["source_sku"]!=SAMPLE or a["price_rub"]["ozon"]!=7850 or a["saleable_stock"]!=40:
        raise ValueError("Owner approval changed")
    current=ozon_client.get_product_names(offer_ids=[SAMPLE,SKU],limit=100)
    samples=[x for x in current if x.get("offer_id")==SAMPLE]
    match=[x for x in current if x.get("offer_id")==SKU]
    if len(samples)!=1 or len(match)>1:raise ValueError("Wrong sample identity")
    if match:note("creation",{"status":"already_created","product_id":match[0].get("id")})
    else:
        # Includes three-component description; temporarily sample image while user filter image is imported.
        edit={
            "sample_offer_id":SAMPLE,
            "name":a["title_by_channel"]["ozon"],
            "description":a["text"]["ozon"],
            "price":a["price_rub"]["ozon"],
            "old_price":None,"barcode":None,"tnved":"8421230000",
            "weight_g":800,"length_mm":180,"width_mm":120,"height_mm":100,
            "images":[PHOTO0],"video_url":"","quantity_to_sell":40,
            "hashtags":"#EA888 #EA888Gen3 #масляный_фильтр #обратный_клапан #корпус_фильтра"
        }
        items=ozon_new_products.build_new_import_items({"names":samples},{SKU:edit})
        if len(items)!=1 or items[0].get("offer_id")!=SKU:raise ValueError("Ozon item not correctly scoped")
        if items[0].get("description_category_id")!=samples[0].get("description_category_id"):
            raise ValueError("Category differs unexpectedly")
        response=ozon_client.import_products(items)
        task=(response.get("result") or {}).get("task_id")
        if not task:raise ValueError("Ozon did not accept import")
        note("submitted",{"task_id":task,"sku":SKU,"price_rub":7850,"photo":"current kit source only; filter photo to be added"})
        status=None
        for i in range(20):
            time.sleep(3)
            info=ozon_client.get_import_status(task)
            rows=(info.get("result") or {}).get("items") or []
            if rows:
                status=[x for x in rows if x.get("offer_id")==SKU]
                if status:break
        if not status:raise RuntimeError("Ozon task accepted but status not confirmed")
        issue=status[0]
        err=issue.get("errors") or []
        if any(str(e.get("level") or "").upper() in ("ERROR","CRITICAL") for e in err if isinstance(e,dict)):
            note("creation",{"status":"rejected","status_code":issue.get("status"),"errors":err})
            raise RuntimeError("Ozon import validation rejected product")
        note("creation",{"status":"task_processed","status_code":issue.get("status"),"errors":err,"product_id":issue.get("product_id")})
    for t in (3,6,12):
        time.sleep(t)
        p=ozon_client.get_product_names(offer_ids=[SKU],limit=100)
        match=[x for x in p if x.get("offer_id")==SKU]
        if match:break
    if not match:raise RuntimeError("New Ozon SKU not observed after import")
    note("readback",{"status":"created","sku":SKU,"product_id":match[0].get("id"),"name":match[0].get("name"),
        "primary_image":match[0].get("primary_image")})
    price=ozon_client.get_prices_and_stocks([SKU])
    pr=price[0].get("price",{}) if price else {}
    noted_price=pr.get("price")
    note("price",{"reported":noted_price,"verified":noted_price is not None and abs(float(noted_price)-7850)<.01})
    warehouse=os.environ.get("OZON_WAREHOUSE_ID")
    if warehouse and warehouse.isdigit():
        try:
            r=ozon_client.update_stocks([{"offer_id":SKU,"stock":40,"warehouse_id":int(warehouse)}])
            note("stock",{"status":"submitted","units":40,"result":r.get("result") if isinstance(r,dict) else None})
        except Exception as e:note("stock",{"status":"api_error","error_type":type(e).__name__})
    else:note("stock",{"status":"missing_warehouse_secret"})
if __name__=="__main__":
    try:run()
    except Exception as e:
        t=traceback.extract_tb(e.__traceback__)
        last=t[-1] if t else None
        note("fatal",{"type":type(e).__name__,"file":Path(last.filename).name if last else None,"line":last.lineno if last else None})
        sys.exit(1)
