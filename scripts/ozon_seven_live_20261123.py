"""LEOPARTS: seven at-risk Ozon SKUs — live READ-ONLY exact seller price/status/stock.
No seller writes, no buyer data, no changes to master catalog or pricing.
"""
from datetime import datetime, timezone
from pathlib import Path
import json
import traceback
import ozon_client as oz

SKUS = ["122390AK-AF","WHT001922","0am325066AC","0am325025",
        "0am325025z","0am325025r","0AM325443D"]
PATH = Path("data/ozon_seven_live_20261123.json")

def run():
    report = {
        "mode":"read_only",
        "captured_at_utc":datetime.now(timezone.utc).isoformat(),
        "seller_writes":0,
        "sku_list":SKUS,
        "errors":{},
        "source":"Ozon Seller API live at run time",
        "sku":{},
    }
    data={}
    operations={
        "prices":lambda:oz.get_prices_and_stocks(SKUS),
        "info":lambda:oz.get_product_info_list(SKUS),
        "stocks":lambda:oz.get_stocks(SKUS),
        "list":lambda:oz.list_products(limit=100),
    }
    for k,fn in operations.items():
        try:
            data[k]=fn()
            if not isinstance(data[k],list):
                raise TypeError("Expected list from Ozon client")
        except Exception as e:
            report["errors"][k]=type(e).__name__+": "+str(e)[:200]
            data[k]=[]
        print("OZON7",k,"items",len(data[k]),"error",bool(report["errors"].get(k)),flush=True)
    for sku in SKUS:
        prices=[x for x in data["prices"] if str(x.get("offer_id") or "").casefold()==sku.casefold()]
        info=[x for x in data["info"] if str(x.get("offer_id") or "").casefold()==sku.casefold()]
        stocks=[x for x in data["stocks"] if str(x.get("offer_id") or "").casefold()==sku.casefold()]
        found=[x for x in data["list"] if str(x.get("offer_id") or "").casefold()==sku.casefold()]
        p=prices[0] if len(prices)==1 else {}
        i=info[0] if len(info)==1 else {}
        s=stocks[0] if len(stocks)==1 else {}
        pr=p.get("price") or {}
        sr=(s.get("stocks") or []) if isinstance(s.get("stocks"),list) else []
        report["sku"][sku]={
            "prices_count":len(prices),"info_count":len(info),
            "stock_count":len(stocks),"seller_exact_matches":len(found),
            "price":pr if isinstance(pr,(dict,int,float,str)) else None,
            "marketing_price":p.get("marketing_price"),"old_price":p.get("old_price"),
            "price_index":p.get("price_index"),"commissions":p.get("commissions"),
            "statuses":i.get("statuses"),"moderation_errors":i.get("errors"),
            "status":i.get("status"),"visibility":i.get("visibility"),
            "stocks":[{"type":x.get("type"),"present":x.get("present"),"reserved":x.get("reserved")} for x in sr if isinstance(x,dict)],
            "product_id":i.get("id") or i.get("product_id") or (found[0].get("product_id") if len(found)==1 else None),
            "price_row_keys":sorted(p.keys()) if p else [],
        }
    report["status"]="complete" if not report["errors"] else "partial"
    report["finished_utc"]=datetime.now(timezone.utc).isoformat()
    PATH.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print("OZON7",report["status"], report["errors"],flush=True)
if __name__=="__main__":
    run()
