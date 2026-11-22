"""LEOPARTS: read-only cross-channel inventory/card existence audit for 8 supplier-list products.
Do not interpret supplier quantity as seller inventory; no writes to business APIs.
"""
from __future__ import annotations
import json, os, sys
from collections import defaultdict
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from openpyxl import load_workbook
OUT=ROOT/"data/supplier_batch_presence_20261123_result.json"
MANIFEST=ROOT/"data/supplier_batch_presence_20261123_request.json"
ROWS=[
 ("dq200_pushrod_seal","DQ200机电单元推杆油封",100,
  ["salnik-shtoka-orig","salniki-shtokov-2sht","0AM325091E / 0AM325091F","0am325066ad","0сц"]),
 ("dq200_pushrod","DQ200机电单元推杆",100,
  ["0am325091e","0am325091f"]),
 ("accumulator_original","蓄压器（原厂）",48,
  ["0AM325587E","gidroakkumulyator-orig"]),
 ("6t30_solenoids_gen1","6t30电磁阀一代",20,
  ["6t30"]),
 ("6t30_solenoids_gen2","6t30电磁阀二代",20,
  ["6t30-solenoids-gen2"]),
 ("6t30_tcm","6T30变速箱电子控制模块",6,
  ["6t30-tcm"]),
 ("0am_solenoids","0am 电磁阀",40,
  ["0AM325025B","0am325025n","0AM325473"]),
 ("dq200_fork_pushrod_added_seal","DQ200机电单元拨叉推杆附加油封",300,
  ["0AM325413A","salnik-tolkatelya-alum","salniki-tolkatelya-4sht","salnik-tolkatelya-additional-dq200"]),
]
def save(data):OUT.write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def collect(response, title, fields, candidates):
    arr=[]
    for row in response:
        if not isinstance(row,dict):continue
        key=str(row.get(fields[0]) or "").strip()
        if key in candidates:
            arr.append({k:row.get(k) for k in fields})
    return arr
def master_items():
    b=load_workbook(ROOT/"data/master_control.xlsx",read_only=True,data_only=True)
    ws=b["Товары"];r={}
    for line in ws.iter_rows(min_row=2,values_only=True):
        if not line or not line[0]:continue
        sku=str(line[0]).strip()
        if sku in ALL:
            r.setdefault(sku,[]).append({"sku":sku,"name":line[5],"price_ozon":line[7],
                "wb_price":line[10],"stock":line[9],"cost":line[26],
                "enabled_wb":line[17],"enabled_avito":line[18],
                "enabled_market":line[19],"enabled_site":line[20]})
    b.close();return r

def each_external():
    import ozon_client,wb_client,yandex_market_client as ym,avito_client
    from scripts.leoparts_direct_operation_nov21 import site_records
    def ozon():
        a=ozon_client.list_products(limit=100)
        return {"total":len(a),"matches":collect(a,"ozon",["offer_id","product_id","visibility"],ALL)}
    def wb():
        a=wb_client.list_cards(limit=100)
        return {"total":len(a),"matches":collect(a,"wb",["vendorCode","nmID","title","subjectID"],ALL)}
    def market():
        a=ym.get_all_offers(archived=False)
        return {"total":len(a),"matches":collect(a,"market",["sku","name","archived","market_sku"],ALL)}
    def avito():
        mapped=avito_client.get_autoload_report_items().get("items") or []
        live=avito_client.list_all_items()
        byid={str(x.get("id")):x for x in live}
        a=[]
        for x in mapped:
            sku=str(x.get("ad_id") or "").strip()
            if sku in ALL:
                st=byid.get(str(x.get("avito_id"))) or {}
                a.append({"ad_id":sku,"avito_id":x.get("avito_id"),
                    "autoload_status":x.get("avito_status"),
                    "live_status":st.get("status"),
                    "price":st.get("price")})
        return {"total":len(mapped),"matches":a}
    def site():
        allrows=site_records()
        a=[]
        for uid,item in allrows.items():
            sku=str(item.get("sku") or "").strip()
            if sku in ALL:
                a.append({"sku":sku,"uid":uid,"name":item.get("title"),"price":item.get("price")})
        return {"total":len(allrows),"matches":a}
    return [("ozon",ozon),("wb",wb),("yandex_market",market),("avito",avito),("site",site)]
def main():
    p=json.loads(MANIFEST.read_text(encoding="utf-8"))
    if p.get("mode")!="read_only" or p.get("change_prices_or_stock") is not False:raise ValueError("Read-only manifest required")
    status={"started_at_utc":datetime.now(timezone.utc).isoformat(),
            "supplier_quantities_are_not_LEOPARTS_stock":True,
            "read_only":True,"source":"customer supplied Chinese product list",
            "products":[{"id":i,"cn":name,"supplier_qty":qty,"candidate_skus":skus} for i,name,qty,skus in ROWS],
            "catalog":{"source":"master_control.xlsx","matches":master_items()},
            "channels":{},"errors":[]}
    save(status)
    for name,fn in each_external():
        try:
            response=fn()
            status["channels"][name]={"status":"ok",**response}
            print(name,"verified",response["total"],len(response["matches"]),flush=True)
        except Exception as e:
            status["channels"][name]={"status":"error","error_type":type(e).__name__}
            status["errors"].append(name)
            print(name,"source_unavailable",type(e).__name__,flush=True)
        save(status)
    status["finished_at_utc"]=datetime.now(timezone.utc).isoformat()
    status["status"]="complete" if not status["errors"] else "partial"
    save(status)
    print("SUPPLIER_PRESENCE_DONE",status["status"],flush=True)
ALL={k for _,_,_,v in ROWS for k in v}
if __name__=="__main__":main()
