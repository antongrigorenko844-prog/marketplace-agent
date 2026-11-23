"""Fix exactly one declined Ozon Gen2 solenoid product: align its Ozon
product title to the owner's already approved WB title of the matching type.
No other SKU, catalog prices, stock or Avito touched.
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ozon_client as oz
from scripts import leoparts_ozon_6t30_gen2_safe_publish_20261123 as old
SKU="6t30-solenoids-gen2"
OUT=ROOT/"data/leoparts_ozon_gen2_moderation_fix_20261123_result.json"
REPORT={"channel":"ozon","sku":SKU,"started_utc":datetime.now(timezone.utc).isoformat(),
        "seller_writes":0,"step":"preflight"}
def save():
    OUT.write_text(json.dumps(REPORT,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf8")
    print("OZON_GEN2_MODERATION_FIX",json.dumps({"step":REPORT["step"],"seller_writes":REPORT["seller_writes"],
        "moderation":REPORT.get("readback",{}).get("moderation")},ensure_ascii=False),flush=True)
def require(ok,msg):
    if not ok:raise ValueError(msg)
def read_status():
    info=oz.get_product_info_list([SKU])
    found=[x for x in info if x.get("offer_id")==SKU and not x.get("_error")]
    require(len(found)==1,"Expected one existing Ozon SKU, not duplicate/uncreated")
    row=found[0]
    REPORT["readback"]={"product_id":row.get("id") or row.get("product_id"),
            "is_created":(row.get("statuses") or {}).get("is_created"),
            "moderation":(row.get("statuses") or {}).get("moderate_status"),
            "status_name":(row.get("statuses") or {}).get("status_name"),
            "errors":row.get("errors")}
    return row
def main():
    manifest=json.loads(old.MANIFEST.read_text(encoding="utf8"))
    require(manifest.get("approved") is True,"Owner batch unapproved")
    item=next(x for x in manifest["items"] if x["sku"]==SKU)
    target=item["wb_title"]
    require(target=="Соленоиды АКПП 6T30 6T40 6T45 6T50 Gen2, 7 шт",
            "Approved concise title changed")
    before=read_status()
    status=before.get("statuses") or {}
    if status.get("is_created"):
        REPORT["step"]="already_approved"
        save();return
    require(status.get("moderate_status")=="declined","Moderation not declined, no edit permitted")
    errors=before.get("errors") or []
    require(len(errors)>0 and all(x.get("attribute_id")==8229 for x in errors),
        "Unexpected non-title moderation errors")
    previous=json.loads(OUT.read_text(encoding="utf8")) if OUT.exists() else {}
    require(not previous.get("task_id"),"Earlier resubmission task already recorded")
    reference=oz.get_product_names(offer_ids=[old.REFERENCE_SKU],limit=1)
    require(len(reference)==1,"Existing Gen1 classification unavailable")
    url=old.verify_photo(item)
    payload=old.build(item,reference[0],url)
    require(payload["offer_id"]==SKU and payload["price"]=="19880",
            "Resubmit would change SKU or price")
    payload["name"]=target
    replaced=False
    for a in payload["attributes"]:
        if a["id"]==4180:
            a["values"]=[{"value":target,"dictionary_value_id":0}]
            replaced=True
    require(replaced,"Ozon title attribute missing")
    REPORT["approved_title"]=target
    REPORT["original_photo_sha256"]=item["media"][0]["sha256"]
    REPORT["category_type"]=payload["type_id"]
    REPORT["step"]="title_aligned_to_existing_category"
    save()
    ack=oz.import_products([payload])
    REPORT["seller_writes"]=1
    REPORT["task_id"]=(ack.get("result") or {}).get("task_id")
    REPORT["import_ack"]=ack
    REPORT["step"]="resubmitted_same_product_id"
    save()
    for i in range(8):
        time.sleep(10)
        if REPORT.get("task_id"):
            try:REPORT["task_status"]=oz.get_import_status(REPORT["task_id"])
            except Exception as e:REPORT["task_status_error"]=str(e)[:150]
        current=read_status()
        moderation=(current.get("statuses") or {}).get("moderate_status")
        REPORT["step"]="approved" if (current.get("statuses") or {}).get("is_created") else (
            "rejected_again" if moderation=="declined" and i>=2 else "in_moderation")
        save()
        if REPORT["step"] in ("approved","rejected_again"):
            break
if __name__=="__main__":
    try:main()
    except Exception as e:
        REPORT["step"]="blocked_or_error"
        REPORT["error_type"]=type(e).__name__
        REPORT["error"]=str(e)[:290]
        save()
        raise
