"""Attach gallery to exact NEW EA888 Gen3 full kit WB card and reconcile price/stock.
Never touch original EA888gen3 SKU.
"""
import json,os,sys,time,re
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client
SKU="EA888gen3-filter-kit"
IDS=ROOT/"data/ea888_gen3_full_kit_wb_result_20261123.json"
PHOTO=ROOT/"data/ea888_gen3_full_kit_photos_20261123_result.json"
MANIFEST=ROOT/"data/ea888_gen3_filter_kit_approved_20261123.json"
OUT=ROOT/"data/ea888_gen3_full_kit_wb_finish_20261123_result.json"
def emit(name,val):
    try:o=json.loads(OUT.read_text())
    except (OSError,ValueError):o={}
    o[name]=val;o["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(o,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"stage":name,"val":val},ensure_ascii=False,default=str),flush=True)
def products():
    rows=[x for x in wb_client.list_cards(limit=100) if x.get("vendorCode")==SKU]
    if len(rows)!=1:raise ValueError("WB product absent or duplicate")
    return rows[0]
def price_row():
    a=[x for x in wb_client.get_prices(limit=1000) if x.get("vendorCode")==SKU]
    if len(a)>1:raise ValueError("Duplicate price row")
    return a[0] if a else None
def run():
    manifest=json.loads(MANIFEST.read_text())
    if manifest["sku"]!=SKU or manifest["price_rub"]["wildberries"]!=7850 or manifest["saleable_stock"]!=40:
        raise ValueError("Owner approval mismatch")
    x=products()
    nmid=int(x["nmID"])
    ids=json.loads(IDS.read_text())
    if nmid!=int(ids["creation"]["nmID"]):raise ValueError("Unexpected WB nmID")
    image_map=json.loads(PHOTO.read_text())["urls"]
    requested=[image_map[SKU+f"_{i}.jpg"] for i in (1,2,3)]
    if not all(s.startswith("https://github.com/") for s in requested):raise ValueError("Image source untrusted")
    oldphotos=x.get("photos") or []
    if len(oldphotos)<3:
        try:
            ack=wb_client.update_media(nmid,requested)
            emit("media",{"status":"submitted","images":len(requested),"api_ack":bool(not ack.get("error"))})
        except Exception as e:emit("media",{"status":"api_error","type":type(e).__name__})
    else:emit("media",{"status":"already_uploaded","observed":len(oldphotos)})
    try:
        p=price_row()
        sizes=(p or {}).get("sizes") or []
        value=float(sizes[0].get("discountedPrice")) if len(sizes)==1 else None
        if value is not None and abs(value-7850)<=1:
            emit("prices",{"status":"verified","actual":value})
        else:
            if value not in (None,0) and abs(value-7850)>1:
                emit("prices",{"status":"unexpected","actual":value})
            else:
                try:
                    task=wb_client.update_prices([{"nmID":nmid,"price":7850,"discount":0}])
                    emit("prices",{"status":"upload_acknowledged","task_id":(task.get("data") or {}).get("id")})
                except Exception as e:emit("prices",{"status":"api_error","type":type(e).__name__})
    except Exception as e:emit("prices",{"status":"read_error","type":type(e).__name__})
    codes=[str(s) for z in (x.get("sizes") or []) for s in (z.get("skus") or []) if str(s).isdigit()]
    wh=os.environ.get("WB_WAREHOUSE_ID","")
    if len(codes)==1 and wh:
        try:
            ack=wb_client.update_stocks(wh,[{"sku":codes[0],"amount":40}])
            emit("stock",{"status":"submitted","amount":40,"ack":bool(isinstance(ack,dict))})
        except Exception as e:emit("stock",{"status":"api_error","type":type(e).__name__})
    else:emit("stock",{"status":"unique_barcode_or_warehouse_missing"})
    time.sleep(10)
    x=products()
    photo=len(x.get("photos") or [])
    try:
        p=price_row();z=(p or {}).get("sizes") or []
        value=float(z[0].get("discountedPrice")) if len(z)==1 else None
    except Exception:value=None
    emit("verification",{"sku":SKU,"nmID":nmid,"photos_visible":photo,"price_visible":value,
        "price_verified":value is not None and abs(value-7850)<=1,
        "gallery_confirmed":photo>=3,"stock_update_requested":40})
if __name__=="__main__":run()
