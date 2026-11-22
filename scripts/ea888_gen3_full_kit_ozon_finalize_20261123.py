"""Safely reconcile one previously submitted Ozon new 3-component EA888 Gen3 kit.
Task 6102966354 may be pending. Never submit duplicate SKU if state unknown.
Apply real gallery only once new offer_id exists. 40 FBS units as owner confirmed.
"""
import json,os,sys,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client,ozon_new_products
SKU="EA888gen3-filter-kit";SRC="EA888gen3";TASK=6102966354
OUT=ROOT/"data/ea888_gen3_full_kit_ozon_finalize_20261123_result.json"
M=ROOT/"data/ea888_gen3_filter_kit_approved_20261123.json"
PH=ROOT/"data/ea888_gen3_full_kit_photos_20261123_result.json"
def record(name,val):
    try:a=json.loads(OUT.read_text())
    except (OSError,ValueError):a={}
    a[name]=val;a["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(a,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"stage":name,"result":val},ensure_ascii=False,default=str),flush=True)
def names(ids):
    try:return ozon_client.get_product_names(offer_ids=ids,limit=100)
    except Exception as e:
        record("name_error",{"error":type(e).__name__})
        return None
def run():
    m=json.loads(M.read_text())
    if m["sku"]!=SKU or m["source_sku"]!=SRC or m["price_rub"]["ozon"]!=7850 or m["saleable_stock"]!=40:
        raise ValueError("Approval mismatch")
    urls=json.loads(PH.read_text())["urls"]
    images=[urls[SKU+f"_{i}.jpg"] for i in (1,2,3)]
    try:
        status=ozon_client.get_import_status(TASK).get("result") or {}
        rows=[v for v in status.get("items",[]) if v.get("offer_id")==SKU]
        record("original_task",{"task_id":TASK,"items":rows})
    except Exception as e:
        record("original_task",{"status":"API_ERROR","error_type":type(e).__name__})
        rows=[]
    current=names([SKU,SRC])
    if current is None:
        record("overall",{"status":"Ozon read unavailable - no duplicate submission"})
        return
    existing=[x for x in current if x.get("offer_id")==SKU]
    if len(existing)>1:raise ValueError("Duplicate SKU")
    if not existing:
        # Pending is ambiguous. Don't resubmit unless the import API explicitly
        # reports a terminal rejection for this exact SKU.
        record("overall",{"status":"not_yet_in_catalog_no_resubmission","original_task":TASK,"known_status":rows[0].get("status") if rows else "unknown"})
        return
    item=existing[0]
    record("catalog",{"status":"found","product_id":item.get("id"),"title":item.get("name"),"category":item.get("description_category_id")})
    primary=item.get("primary_image")
    current_images=item.get("images") or []
    has_new=any(SKU+"_1.jpg" in str(v) for v in [primary]+current_images)
    if not has_new:
        edit={"sample_offer_id":SKU,"name":m["title_by_channel"]["ozon"],
              "description":m["text"]["ozon"],"price":7850,"old_price":None,
              "barcode":item.get("barcode") or "",
              "tnved":"8421230000","weight_g":800,
              "length_mm":180,"width_mm":120,"height_mm":100,
              "images":images,"video_url":"","quantity_to_sell":40,
              "hashtags":"#EA888 #EA888Gen3 #масляный_фильтр #корпус_фильтра #обратный_клапан"}
        params=ozon_new_products.build_new_import_items({"names":[item]},{SKU:edit})
        if len(params)!=1 or params[0].get("offer_id")!=SKU:raise ValueError("Unexpected gallery update scope")
        res=ozon_client.import_products(params)
        tid=(res.get("result") or {}).get("task_id")
        record("gallery_submission",{"status":"accepted" if tid else "unverified","task_id":tid,"image_count":len(images)})
        if tid:
            for t in range(10):
                time.sleep(3)
                try:
                    a=ozon_client.get_import_status(tid)
                    match=[r for r in (a.get("result") or {}).get("items",[]) if r.get("offer_id")==SKU]
                    if match and match[0].get("status") not in (None,"pending"):
                        record("gallery_task",{"status":match[0].get("status"),"errors":match[0].get("errors")});break
                except Exception as e:record("gallery_task",{"status":"api_error","type":type(e).__name__});break
    else:record("gallery_submission",{"status":"already_matches_new_kit_cover"})
    try:
        pr=ozon_client.get_prices_and_stocks([SKU])
        actual=float(pr[0]["price"]["price"]) if pr else None
        if actual is not None and actual!=7850:
            # Correct price only if previously approved 7850 is the intended master source.
            r=ozon_client.update_prices([{"offer_id":SKU,"price":"7850","currency_code":"RUB"}])
            record("price",{"status":"updated_or_accepted","before":actual})
        else:record("price",{"status":"verified","actual":actual})
    except Exception as e:record("price",{"status":"read_error","type":type(e).__name__})
    wh=os.environ.get("OZON_WAREHOUSE_ID","")
    if wh.isdigit():
        try:
            r=ozon_client.update_stocks([{"offer_id":SKU,"stock":40,"warehouse_id":int(wh)}])
            record("stock",{"status":"submitted","amount":40,"response":r.get("result") if isinstance(r,dict) else None})
        except Exception as e:record("stock",{"status":"api_error","type":type(e).__name__})
    else:record("stock",{"status":"warehouse_not_configured"})
    try:
        p=names([SKU]) or []
        r=ozon_client.get_prices_and_stocks([SKU])
        price=float(r[0]["price"]["price"]) if r else None
        record("readback",{"found":bool(p),"price":price,"price_verified":price==7850,
                          "name":p[0].get("name") if p else None})
    except Exception as e:record("readback",{"error":type(e).__name__})
if __name__=="__main__":run()
