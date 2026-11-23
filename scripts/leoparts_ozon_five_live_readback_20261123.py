"""Ozon only: final independent readback for all five approved LEOPARTS products.
Never change seller data or touch Avito. Success requires created + approved,
exact prices and positive FBS balance for all five.
"""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client as oz
SKUS=["6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2",
       "tool-dq200-acc-restore","tool-dsg-pressure-hp"]
PRICES={"6t30-solenoids-gen2":19880,"6t30-tcm-gen1":58850,
        "6t30-tcm-gen2":58850,"tool-dq200-acc-restore":38800,"tool-dsg-pressure-hp":28800}
STOCKS={"6t30-solenoids-gen2":20,"6t30-tcm-gen1":3,
        "6t30-tcm-gen2":3,"tool-dq200-acc-restore":3,"tool-dsg-pressure-hp":3}
OUT=ROOT/"data/leoparts_ozon_five_live_readback_20261123.json"
report={"channel":"ozon","checked_utc":datetime.now(timezone.utc).isoformat(),
        "mode":"read_only","external_writes":0,"items":{}}
def store():
    report["summary"]={
        "total":len(SKUS),
        "created_approved":sum(1 for x in report["items"].values() if x.get("created") and x.get("moderation")=="approved"),
        "for_sale":sum(1 for x in report["items"].values() if x.get("status_name")=="Продаётся"),
        "prices_verified":sum(1 for x in report["items"].values() if x.get("price_verified")),
        "positive_fbs":sum(1 for x in report["items"].values() if x.get("fbs_present",0)>0)}
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf8")
    print("OZON_5",json.dumps(report["summary"],ensure_ascii=False),flush=True)
try:
    catalog=oz.list_products(limit=100)
    info=oz.get_product_info_list(SKUS)
    prices=oz.get_prices_and_stocks(SKUS)
    stocks=oz.get_stocks(SKUS)
    media=oz.get_product_names(offer_ids=SKUS,limit=100)
    names={x.get("offer_id"):x for x in media}
    info_by={x.get("offer_id"):x for x in info if not x.get("_error")}
    prices_by={x.get("offer_id"):x for x in prices}
    stocks_by={x.get("offer_id"):x for x in stocks}
    catalog_by={}
    for c in catalog:catalog_by.setdefault(c.get("offer_id"),[]).append(c)
    for sku in SKUS:
        x=report["items"].setdefault(sku,{})
        list_rows=catalog_by.get(sku,[])
        x["seller_catalog_matches"]=len(list_rows)
        row=info_by.get(sku) or {}
        x["product_id"]=row.get("id") or row.get("product_id")
        status=row.get("statuses") or {}
        x["created"]=bool(status.get("is_created"))
        x["moderation"]=status.get("moderate_status")
        x["status_name"]=status.get("status_name")
        x["errors"]=row.get("errors")
        p=(prices_by.get(sku) or {}).get("price") or {}
        val=p.get("price") if isinstance(p,dict) else p
        x["observed_price"]=val
        x["expected_price"]=PRICES[sku]
        try:x["price_verified"]=abs(float(val)-PRICES[sku])<0.01
        except (TypeError,ValueError):x["price_verified"]=False
        q=(stocks_by.get(sku) or {}).get("stocks") or []
        x["fbs_present"]=sum(int(z.get("present") or 0) for z in q if isinstance(z,dict) and z.get("type")=="fbs")
        x["expected_stock"]=STOCKS[sku]
        x["fbs_stock_matches_target"]=x["fbs_present"]==STOCKS[sku]
        m=names.get(sku) or {}
        x["name"]=m.get("name")
        x["primary_image"]=m.get("primary_image")
        x["secondary_image_count"]=len(m.get("images") or [])
        x["description_category_id"]=m.get("description_category_id")
        x["type_id"]=m.get("type_id")
except Exception as e:
    report["readback_error"]=str(e)[:250]
store()
