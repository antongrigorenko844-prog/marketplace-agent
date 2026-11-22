"""Independently verify EA888 intake screw on Ozon after correcting category.
Read-only: no price/category/stock writes; capture full moderation statuses.
"""
import sys,json,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client
SKU="vint-kollektora-ea888"
OUT=ROOT/"data/repair_vint_ea888_ozon_verify_publication_20261123.json"
SAMPLES=[]
def update(stage,d):
    SAMPLES.append({"step":stage,"at":datetime.now(timezone.utc).isoformat(),**d})
    OUT.write_text(json.dumps({"sku":SKU,"read_only":True,"checks":SAMPLES},ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"step":stage,**d},ensure_ascii=False,default=str),flush=True)
def verify_one(stage):
    item=ozon_client.get_product_info_list([SKU])
    hits=[x for x in item if x.get("offer_id")==SKU]
    if len(hits)!=1:raise ValueError("Target product absent in info list")
    x=hits[0];statuses=x.get("statuses") or {}
    errors=x.get("errors") or []
    val={"product_id":x.get("id") or x.get("product_id"),
         "description_category_id":x.get("description_category_id"),"type_id":x.get("type_id"),
         "category_correct":x.get("description_category_id")==74606916 and x.get("type_id")==970858643,
         "moderation_errors":errors,"statuses":statuses}
    update(stage,val)
    return val
def main():
    req=json.loads((ROOT/"data/repair_vint_ea888_ozon_verify_publication_20261123_request.json").read_text())
    if req.get("sku")!=SKU or req.get("read_only") is not True:raise ValueError("Scope mismatch")
    for n,sec in enumerate((0,12,22,35)):
        if sec:time.sleep(sec)
        try:
            val=verify_one("readback_"+str(n))
            if val["category_correct"] and val["statuses"].get("is_created") is True and not val["moderation_errors"]:
                break
        except Exception as e:update("api_error_"+str(n),{"error_type":type(e).__name__})
    try:
        p=ozon_client.get_prices_and_stocks([SKU])
        item=next((x for x in p if x.get("offer_id")==SKU),{})
        block=item.get("price") or {}
        update("price",{"selling":block.get("price"),"price_is_950":str(block.get("price")) in ("950","950.0","950.00")})
    except Exception as e:update("price_error",{"error_type":type(e).__name__})
if __name__=="__main__":main()
