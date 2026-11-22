"""Verify all five approved LEOPARTS WB FBS quantities via actual size IDs.

Stock API may require chrtId rather than legacy barcode 'sku'. Only attempt
a stock repair when the exact card + size ID can be confirmed unambiguously.
No other SKU, price, image, or card is modified.
"""
import os,sys,json,time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import wb_client as wb
import config
OUT=ROOT/"data/leoparts_five_wb_stock_verify_20261123_result.json"
TARGET={"6t30-solenoids-gen2":20,"6t30-tcm-gen1":3,"6t30-tcm-gen2":3,
        "tool-dq200-acc-restore":3,"tool-dsg-pressure-hp":3}
def note(key,value):
    try:d=json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError,ValueError):d={}
    d[key]=value;d["updated_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({key:value},ensure_ascii=False,default=str),flush=True)
def report():
    warehouse=os.environ.get("WB_WAREHOUSE_ID")
    if not warehouse:raise RuntimeError("Warehouse secret missing")
    cards=wb.list_cards()
    if len(cards)<110:raise RuntimeError("Incomplete card list")
    sizes={}
    for sku,target in TARGET.items():
        found=[c for c in cards if c.get("vendorCode")==sku]
        if len(found)!=1:
            note(sku,{"status":"missing_or_duplicate","matches":len(found)})
            continue
        c=found[0];parts=c.get("sizes") or []
        if len(parts)!=1:note(sku,{"status":"unexpected_variants","count":len(parts)});continue
        x=parts[0];cid=x.get("chrtID") or x.get("chrtId")
        bar=x.get("skus") or []
        if len(bar)!=1 or not str(bar[0]).isdigit():
            note(sku,{"status":"unexpected_barcode","nmID":c.get("nmID")});continue
        if not cid:
            note(sku,{"status":"missing_size_id","nmID":c.get("nmID")});continue
        sizes[sku]={"nmID":c.get("nmID"),"chrtId":int(cid),"barcode":str(bar[0]),
                    "price_target":None,"photo_count":len(c.get("photos") or []),
                    "stock_target":target}
    note("cards",{sku:{k:v for k,v in x.items() if k!="barcode"} for sku,x in sizes.items()})
    prices=wb.get_prices(limit=1000)
    pby={p.get("nmID"):p for p in prices}
    note("prices",{sku:{"observed":((pby.get(x["nmID"]) or {}).get("sizes") or [{}])[0].get("discountedPrice"),
                       "photo_count":x["photo_count"]} for sku,x in sizes.items()})
    all_ids=[x["chrtId"] for x in sizes.values()]
    if not all_ids:return
    path=f"/api/v3/stocks/{warehouse}"
    try:
        raw=wb._request("POST",config.config.wb_marketplace_base,path,{"chrtIds":all_ids})
        method="chrtIds"
    except Exception as e:
        note("new_schema_error",{"type":type(e).__name__,"text":str(e)[:200]})
        raw=wb._request("POST",config.config.wb_marketplace_base,path,
                        {"skus":[x["barcode"] for x in sizes.values()]})
        method="skus"
    data=raw.get("stocks") or (raw.get("data") or {}).get("stocks") or []
    byid={int(x.get("chrtId") or x.get("chrtID") or 0):x for x in data}
    bybar={str(x.get("sku") or ""):x for x in data}
    out={}
    for sku,s in sizes.items():
        item=byid.get(s["chrtId"]) or bybar.get(s["barcode"])
        observed=int(item.get("amount")) if item and item.get("amount") is not None else None
        out[sku]={"status":"verified" if observed==s["stock_target"] else "not_verified",
                  "quantity":observed,"target":s["stock_target"],"read_method":method}
    note("stock_readback",out)
    if method=="chrtIds":
        for sku,s in sizes.items():
            if out[sku]["status"]=="verified":continue
            try:
                payload={"stocks":[{"chrtId":s["chrtId"],"amount":s["stock_target"]}]}
                ack=wb._request("PUT",config.config.wb_marketplace_base,path,payload)
                note("repair_"+sku,{"status":"submitted","target":s["stock_target"],"response":str(ack)[:140]})
            except Exception as e:
                note("repair_"+sku,{"status":"error","type":type(e).__name__,"error":str(e)[:200]})
        time.sleep(5)
        new=wb._request("POST",config.config.wb_marketplace_base,path,{"chrtIds":all_ids})
        after=new.get("stocks") or (new.get("data") or {}).get("stocks") or []
        rows={int(v.get("chrtId") or v.get("chrtID") or 0):v for v in after}
        note("stock_final",{sku:{"observed":(rows.get(s["chrtId"]) or {}).get("amount"),
                                 "target":s["stock_target"],
                                 "verified":(rows.get(s["chrtId"]) or {}).get("amount")==s["stock_target"]}
                            for sku,s in sizes.items()})
if __name__=="__main__":
    try:report()
    except Exception as e:note("fatal",{"type":type(e).__name__,"error":str(e)[:240]});raise
