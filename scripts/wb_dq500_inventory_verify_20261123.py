"""Read-only confirmation of WB Dq500 smooth housing availability and alias.
No writes to seller, no product identifiers other than seller codes kept.
"""
from __future__ import annotations
import json,os,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import wb_client as wb
import config
OUT=ROOT/"data/wb_dq500_inventory_verify_20261123.json"
SKU="dq500-korpus-gladkiy"
def main():
 d={"mode":"read_only","seller_writes":0,"at_utc":datetime.now(timezone.utc).isoformat(),
    "master_sku":"Dq500","wb_vendor_code":SKU,"checks":{}}
 cards=wb.list_cards(limit=100)
 found=[x for x in cards if x.get("vendorCode")==SKU]
 protected=[x for x in cards if x.get("vendorCode")=="DQ500"]
 if len(found)!=1 or len(protected)!=1:
  d["status"]="card_not_unique";d["checks"]["alias_count"]=len(found)
 else:
  c=found[0];sizes=c.get("sizes") or []
  codes=[str(v) for row in sizes for v in row.get("skus") or [] if str(v).isdigit()]
  ids=[int(row.get("chrtID")) for row in sizes if str(row.get("chrtID") or "").isdigit()]
  d["checks"]["card"]={"nmID":c.get("nmID"),"title":c.get("title"),
    "photos_count":len(c.get("photos") or []),
    "subject_id":c.get("subjectID"),
    "protected_DQ500_nmID":protected[0].get("nmID"),
    "single_barcode":len(codes)==1,"single_chrtID":len(ids)==1}
  prices=[x for x in wb.get_prices(limit=1000) if x.get("vendorCode")==SKU]
  d["checks"]["price"]={"matches_count":len(prices),
    "amount_rub":float(prices[0]["sizes"][0]["discountedPrice"]) if len(prices)==1 and len(prices[0].get("sizes") or [])==1 else None}
  warehouse=os.getenv("WB_WAREHOUSE_ID")
  if warehouse and len(codes)==1:
   # This is the read-only stock query. WB may now use chrtIds rather than skus.
   try:
    response=wb._request("POST",config.config.wb_marketplace_base,
                         f"/api/v3/stocks/{warehouse}",{"skus":codes},retries=1)
    d["checks"]["stock_api"]={"status":"returned","kind":"barcode",
                               "stocks":(response.get("stocks") or response.get("data") or [])[:8]
                                  if isinstance(response,dict) else []}
   except Exception as e:
    d["checks"]["stock_api"]={"status":"barcode_query_failed",
                              "type":type(e).__name__}
    if len(ids)==1:
     try:
      response=wb._request("POST",config.config.wb_marketplace_base,
                           f"/api/v3/stocks/{warehouse}",{"chrtIds":ids},retries=1)
      d["checks"]["stock_api"]={"status":"returned","kind":"chrtID",
                                 "stocks":(response.get("stocks") or response.get("data") or [])[:8]
                                    if isinstance(response,dict) else []}
     except Exception as e:
      d["checks"]["stock_api"]["chrt_query_error_type"]=type(e).__name__
  else:
   d["checks"]["stock_api"]={"status":"not_queried","unique_barcode":len(codes)==1,
                            "warehouse_present":bool(warehouse)}
  d["status"]="complete"
 OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf8")
 print("WB_DQ500_VERIFIED",d["status"],d["checks"],flush=True)
if __name__=="__main__":main()
