"""Ozon-only final verification of three newly imported approved 6T30 SKUs.
Only allowed seller mutation: first stock submission for the new solenoids Gen2
after approval (verified no prior submission); no re-import or other SKU writes.
"""
from __future__ import annotations
import json,os,sys,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ozon_client as oz
OUT=ROOT/"data/leoparts_ozon_three_final_verify_20261123.json"
TARGET={
 "6t30-solenoids-gen2":{"stock":20,"price":19880},
 "6t30-tcm-gen1":{"stock":3,"price":58850},
 "6t30-tcm-gen2":{"stock":3,"price":58850}
}
result={"channel":"ozon","run_utc":datetime.now(timezone.utc).isoformat(),
        "scope":list(TARGET),"seller_writes":0,"items":{}}
def save():
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf8")
    print("OZON_THREE_FINAL",json.dumps({s:{"created":x.get("is_created"),"moderation":x.get("moderation"),
          "stock_submit":x.get("stock_submit")} for s,x in result["items"].items()},ensure_ascii=False),flush=True)
def whid():
    v=os.getenv("OZON_WAREHOUSE_ID","")
    if v.isdigit() and int(v)>0:return int(v)
    rows=oz.get_warehouses()
    ids={int(x["warehouse_id"]) for x in rows if x.get("warehouse_id")}
    if len(ids)!=1:raise ValueError("No unique Ozon FBS warehouse")
    return next(iter(ids))
def readback():
    cards=oz.get_product_info_list(list(TARGET))
    by={x.get("offer_id"):x for x in cards if not x.get("_error")}
    for sku in TARGET:
        ent=result["items"].setdefault(sku,{})
        row=by.get(sku)
        if not row:
            ent["found"]=False
            continue
        ent["found"]=True
        ent["product_id"]=row.get("id") or row.get("product_id")
        ent["is_created"]=bool((row.get("statuses") or {}).get("is_created"))
        ent["moderation"]=(row.get("statuses") or {}).get("moderate_status")
        ent["status_name"]=(row.get("statuses") or {}).get("status_name")
        ent["errors"]=row.get("errors")
    try:
        px=oz.get_prices_and_stocks(list(TARGET))
        for row in px:
            sku=row.get("offer_id")
            if sku not in TARGET:continue
            data=(row.get("price") or {})
            if isinstance(data,dict):
                price=data.get("price") or data.get("marketing_seller_price")
            else:
                price=data
            ent=result["items"].setdefault(sku,{})
            ent["observed_price"]=price
            ent["expected_price"]=TARGET[sku]["price"]
            try:ent["price_verified"]=abs(float(price)-TARGET[sku]["price"])<0.01
            except (TypeError,ValueError):ent["price_verified"]=False
    except Exception as e:
        result["prices_readback_error"]=str(e)[:220]
    try:
        stocks=oz.get_stocks(list(TARGET))
        for row in stocks:
            sku=row.get("offer_id")
            if sku in TARGET:
                result["items"].setdefault(sku,{})["stock_readback"]=row.get("stocks")
    except Exception as e:
        result["stocks_readback_error"]=str(e)[:220]
def main():
    previous=json.loads((ROOT/"data/leoparts_ozon_6t30_gen2_publish_20261123.json").read_text(encoding="utf8"))
    assert previous.get("task_id")==6104222187 and previous.get("seller_writes")==1
    readback()
    sku="6t30-solenoids-gen2"
    if not result["items"].get(sku,{}).get("is_created"):
        result["gen2_stock"]="blocked_until_ozon_moderation"
    else:
        # This is the first and only stock submission for the newly approved SKU.
        prior=json.loads(OUT.read_text(encoding="utf8")) if OUT.exists() else {}
        if prior.get("items",{}).get(sku,{}).get("stock_submit"):
            result["items"][sku]["stock_submit"]="already_submitted_previous_run"
        else:
            current=result["items"].get(sku,{}).get("stock_readback")
            present=sum(int(row.get("present") or 0) for row in current or [] if isinstance(row,dict))
            if present>0:
                result["items"][sku]["stock_submit"]="skipped_already_positive_stock"
            else:
                w=whid()
                ack=oz.update_stocks([{"offer_id":sku,"warehouse_id":w,"stock":20}])
                result["seller_writes"]+=1
                result["items"][sku]["stock_submit"]={"ack":ack,"warehouse":w}
    save()
    for n in range(3):
        time.sleep(8)
        readback()
        save()
if __name__=="__main__":
    try:main()
    except Exception as e:
        result["error"]=str(e)[:280]
        save()
        raise
