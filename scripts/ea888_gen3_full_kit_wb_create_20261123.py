"""Create exact new oil filter kit WB product, preserving original EA888gen3.
Media is subsequently updated from original kit+user's filter; do not treat HTTP accepted as published.
"""
import json,os,sys,time,traceback
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client
SKU="EA888gen3-filter-kit"
OUT=ROOT/"data/ea888_gen3_full_kit_wb_result_20261123.json"
M=ROOT/"data/ea888_gen3_filter_kit_approved_20261123.json"
def note(stage,value):
    try:r=json.loads(OUT.read_text())
    except (FileNotFoundError,ValueError):r={}
    r[stage]=value;r["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"step":stage,"result":value},ensure_ascii=False,default=str),flush=True)
def check():
    a=json.loads(M.read_text(encoding="utf-8"))
    if (a["sku"]!=SKU or a["source_sku"]!="EA888gen3" or
        a["price_rub"]["wildberries"]!=7850 or a["saleable_stock"]!=40 or
        a["existing_kit_sku_protected"]!="EA888gen3" or not a.get("publish_authorized")):
        raise ValueError("Approved owner manifest invalid")
    return a
def cards():
    v=wb_client.list_cards(limit=100)
    if len(v)<125:raise ValueError("Incomplete card traversal")
    found=[c for c in v if str(c.get("vendorCode") or "").casefold()==SKU.casefold()]
    if len(found)>1:raise ValueError("Duplicate WB vendor code")
    return v,(found[0] if found else None)
def run():
    a=check()
    all_cards,existing=cards()
    if existing:note("creation",{"status":"exists","nmID":existing.get("nmID")})
    else:
        prior={}
        try:prior=json.loads(OUT.read_text())
        except (OSError,ValueError):pass
        if (prior.get("accepted") or {}).get("ok"):
            note("creation",{"status":"prior_request_accepted","note":"Avoid duplicate submission"})
        else:
            barcode=wb_client.generate_barcodes(1)
            if not isinstance(barcode,list) or len(barcode)!=1:raise ValueError("No unique barcode")
            variant={"vendorCode":SKU,
            "title":"EA888 Gen3 комплект масляного фильтра с корпусом и клапаном",
            "description":a["text"]["wildberries"],"brand":"Нет бренда",
            "dimensions":{"length":18,"width":12,"height":10,"weightBrutto":0.8},
            "sizes":[{"techSize":"0","wbSize":"","price":7850,"skus":[str(barcode[0])]}],
            "characteristics":[
              {"id":15003115,"value":["легковые автомобили"]},
              {"id":5023,"value":["EA888 Gen3"]},
              {"id":378533,"value":["Алюминиевый корпус масляного фильтра — 1 шт.; обратный клапан — 1 шт.; фильтрующий элемент и уплотнительное кольцо — 1 комплект."]},
              {"id":5522881,"value":[SKU]},
              {"id":4572490,"value":["06L115401S"]},
            ]}
            accepted=wb_client.create_cards(4122,[variant])
            if not isinstance(accepted,dict) or accepted.get("error") not in (None,False):
                raise ValueError("WB did not accept new kit creation")
            note("accepted",{"ok":True,"subject_id":4122,"barcode_last4":str(barcode[0])[-4:],"dimensions_estimated":True})
    existing=None
    for t in range(10):
        _,existing=cards()
        if existing:break
        if t<9:time.sleep(10)
    if not existing:
        errors=[]
        try:
            for e in wb_client.get_upload_errors(limit=100):
                if SKU in json.dumps(e,ensure_ascii=False):
                    errors.append(e.get("errors"))
        except Exception as x:errors.append({"read_failed":type(x).__name__})
        note("creation",{"status":"NOT_CREATED","errors":errors})
        raise RuntimeError("WB accepted new kit but no card materialized")
    nmid=int(existing["nmID"])
    note("creation",{"status":"created_verified","nmID":nmid,"sku":SKU,
                     "photo_count":len(existing.get("photos") or []),"category":existing.get("subjectID")})
    try:
        price=[x for x in wb_client.get_prices(limit=1000) if x.get("vendorCode")==SKU]
        if len(price)==1 and len(price[0].get("sizes") or [])==1:
            val=float(price[0]["sizes"][0].get("discountedPrice") or 0)
            if abs(val-7850)<=1:note("price",{"status":"verified","actual":val})
            else:note("price",{"status":"not_at_target","actual":val})
        else:
            note("price",{"status":"not_indexed_yet"})
    except Exception as e:note("price",{"status":"api_not_ready","error_type":type(e).__name__})
    codes=[str(s) for x in (existing.get("sizes") or []) for s in (x.get("skus") or []) if str(s).isdigit()]
    if len(codes)==1 and os.getenv("WB_WAREHOUSE_ID"):
        try:
            wb_client.update_stocks(os.environ["WB_WAREHOUSE_ID"],[{"sku":codes[0],"amount":40}])
            note("stock",{"status":"submitted","units":40})
        except Exception as e:note("stock",{"status":"api_error","type":type(e).__name__})
    else:note("stock",{"status":"missing_warehouse_or_unique_barcode"})
if __name__=="__main__":
    try:run()
    except Exception as e:
        trace=traceback.extract_tb(e.__traceback__);v=trace[-1] if trace else None
        note("fatal",{"type":type(e).__name__,"file":Path(v.filename).name if v else None,"line":v.lineno if v else None})
        sys.exit(1)
