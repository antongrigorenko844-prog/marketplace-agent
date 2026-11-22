"""Check why original DQ200 accumulator 0AM325587E does not have an active Avito listing.
Read-only: local catalog xlsx, stable public autoload feed, Avito seller API / autoload.
"""
from __future__ import annotations
import io,json,sys,traceback,requests
from pathlib import Path
from datetime import datetime,timezone
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
SKU="0AM325587E"
OUT=ROOT/"data/supplier_avito_accumulator_gap_20261123_result.json"
def rowdata(blob):
    b=load_workbook(io.BytesIO(blob),read_only=True,data_only=True)
    try:
        ws=b["Объявления"]
        rows=[r for r in ws.values if len(r)>33 and r[3]==SKU]
        return [{"sku":r[3],"title":r[24],"price":r[33],
                 "photo_count":len(str(r[18] or "").split("|")) if r[18] else 0,
                 "row_fields":len(r),"category":r[15]} for r in rows]
    finally:b.close()
def run():
    out={"sku":SKU,"read_only":True,"at":datetime.now(timezone.utc).isoformat(),"errors":[]}
    p=ROOT/"data/avito_feed.xlsx"
    try:out["local_feed"]=rowdata(p.read_bytes())
    except Exception as e:out["errors"].append({"source":"local","type":type(e).__name__})
    url="https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/avito_feed.xlsx"
    try:
        r=requests.get(url,params={"fresh":str(datetime.now().timestamp())},timeout=50)
        if r.ok:out["stable_published_feed"]=rowdata(r.content)
        else:out["errors"].append({"source":"stable","http_status":r.status_code})
    except Exception as e:out["errors"].append({"source":"stable","type":type(e).__name__})
    import avito_client
    try:
        rows=avito_client.get_autoload_report_items().get("items") or []
        out["autoload"]=[{"sku":z.get("ad_id"),"item_id":z.get("avito_id"),"status":z.get("avito_status"),
                          "reason_count":len(z.get("messages") or [])} for z in rows if z.get("ad_id")==SKU]
    except Exception as e:out["errors"].append({"source":"autoload","type":type(e).__name__})
    try:
        rows=avito_client.list_all_items()
        out["active_identified"]=[{"id":z.get("id"),"status":z.get("status"),"title":z.get("title"),"price":z.get("price")} for z in rows if z.get("status")=="active" and SKU.lower() in str(z.get("title") or "").lower()]
    except Exception as e:out["errors"].append({"source":"avito","type":type(e).__name__})
    try:
        p=avito_client._request("GET","/autoload/v2/profile")
        out["autoload_profile"]={k:p.get(k) for k in ("autoload_enabled","schedule") if k in p}
    except Exception as e:out["errors"].append({"source":"autoload_profile","type":type(e).__name__})
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False),flush=True)
if __name__=="__main__":run()
