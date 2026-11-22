"""READ-ONLY: locate six missing LEOPARTS Ozon listings and exact blockers.

Do not create/edit/relist anything. Also compare WB existence and photograph
count; local spreadsheet/draft data are not evidence of Ozon publication.
"""
import json,sys,os
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import ozon_client as oz
import wb_client as wb
from openpyxl import load_workbook
SKUS={
 "6t30-solenoids-gen2":"Соленоиды 6T30 семейство Gen2",
 "6t30-tcm-gen1":"Блок с соленоидами Gen1",
 "6t30-tcm-gen2":"Блок с соленоидами Gen2",
 "tool-dq200-acc-restore":"Набор восстановления гидроаккумулятора DSG7",
 "tool-dsg-pressure-hp":"Набор проверки давления DSG",
 "vint-kollektora-ea888":"Винт коллектора EA888 (control)",
}
OUT=ROOT/"data/ozon_missing_new_products_audit_20261123.json"
r={"mode":"read_only","checked_utc":datetime.now(timezone.utc).isoformat(),"items":{s:{"name":n} for s,n in SKUS.items()},"errors":[]}
def record(what,fn):
    try:fn()
    except Exception as e:r["errors"].append({"operation":what,"type":type(e).__name__,"message":str(e)[:175]})
record("ozon all-list",lambda:None)
try:
    allitems=oz.list_products(limit=100)
    r["ozon_items_returned"]=len(allitems)
    for sku in SKUS:
        matches=[x for x in allitems if str(x.get("offer_id") or "").casefold()==sku.casefold()]
        r["items"][sku]["ozon_list_found"]=bool(matches)
        r["items"][sku]["ozon_list"]=[{k:v for k,v in x.items() if k in ("offer_id","product_id","visibility","has_fbo_stocks","has_fbs_stocks","is_archived")} for x in matches]
except Exception as e:r["errors"].append({"operation":"ozon all-list","type":type(e).__name__,"message":str(e)[:170]})
try:
    info=oz.get_product_info_list(list(SKUS))
    for sku in SKUS:
        m=[x for x in info if str(x.get("offer_id") or "").casefold()==sku.casefold()]
        r["items"][sku]["ozon_info_found"]=bool(m)
        r["items"][sku]["ozon_info"]=[{k:v for k,v in x.items() if k in ("offer_id","id","product_id","is_archived","archived","visibility","status","statuses","error","errors","validation_errors","has_fbs_stocks")} for x in m]
    if any("_error" in x for x in info):
        r["errors"].append({"operation":"ozon info-list","type":"fallback_error","message":str(next(x["_error"] for x in info if "_error" in x))[:180]})
except Exception as e:r["errors"].append({"operation":"ozon info-list","type":type(e).__name__,"message":str(e)[:170]})
try:
    cards=wb.list_cards(limit=100)
    r["wb_items_returned"]=len(cards)
    for sku in SKUS:
        matches=[x for x in cards if str(x.get("vendorCode") or "").casefold()==sku.casefold()]
        r["items"][sku]["wb"]=[{"vendorCode":x.get("vendorCode"),"nmID":x.get("nmID"),"title":x.get("title"),"photo_count":len(x.get("photos") or []),"subjectID":x.get("subjectID")} for x in matches]
except Exception as e:r["errors"].append({"operation":"wb cards","type":type(e).__name__,"message":str(e)[:170]})
for x in ["ozon_catalog.xlsx","ozon_new_products.xlsx","new_products.xlsx","master_control.xlsx"]:
    p=ROOT/"data"/x
    if not p.exists():continue
    try:
        book=load_workbook(p,read_only=True,data_only=True)
        try:
            rows=list(book.active.iter_rows(min_row=2,values_only=True))
            for sku in SKUS:
                m=[r for r in rows if r and r[0] and str(r[0]).casefold()==sku.casefold()]
                r["items"][sku].setdefault("local_spreadsheets",{})[x]={"count":len(m),"rows":len(rows)}
        finally:book.close()
    except Exception as e:r["errors"].append({"operation":"xlsx "+x,"type":type(e).__name__})
for sku in SKUS:
    r["items"][sku]["media_files"]=[str(p.relative_to(ROOT)) for base in (ROOT/"photos"/sku, ROOT/"photos_avito"/sku) if base.is_dir() for p in base.iterdir() if p.is_file()]
p=ROOT/"drafts/new-products/dsg-tools-20261120/product_cards.json"
if p.exists():
    d=json.loads(p.read_text())
    r["draft_status"]={"status":d.get("status"),"publication_ready":d.get("publication_ready"),"non_executable_draft":d.get("non_executable_draft")}
    r["drafts"]={x.get("seller_sku_candidate"):{"publication_blockers":x.get("publication_blockers"),"media_status":x.get("media",{}).get("status"),"cover_verified_as_product_photograph":x.get("media",{}).get("cover_verified_as_product_photograph"),"price_ozon":x.get("commercial_inputs",{}).get("prices_rub",{}).get("ozon")} for x in d.get("products",[])}
OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n")
print(json.dumps({"items":r["items"],"drafts":r.get("drafts"),"errors":r["errors"]},ensure_ascii=False),flush=True)
