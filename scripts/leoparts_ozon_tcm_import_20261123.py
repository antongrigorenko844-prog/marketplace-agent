"""Ozon-only controlled import of the two previously approved 6T30 Gen1/Gen2
TCM-and-solenoids assemblies. Never change other SKU, Avito or prices master.
"""
from __future__ import annotations
import hashlib, json, os, sys, time, urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ozon_client as oz

MANIFEST=ROOT/"data/product_intake_v2/requests/leoparts-five-20261123.json"
OUT=ROOT/"data/leoparts_ozon_tcm_import_20261123_result.json"
SKUS=("6t30-tcm-gen1","6t30-tcm-gen2")
SAMPLE="02E927770AL"
EXPECTED={
 "6t30-tcm-gen1":"f9e24dcc3158c0329c9c2b2ba5c87efcdc309ece035db4d17af89bfcd796f764",
 "6t30-tcm-gen2":"f43124ed7ef78159c432021291e5b768db797c25e882b6548af2ed772a3d3c6e"
}
report={"scope":"Ozon only 2 approved TCM 6T30 SKUs","started_utc":datetime.now(timezone.utc).isoformat(),
        "seller_writes":0,"items":{},"stage":"preflight"}

def persist():
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf8")
    print("OZON_TCM",json.dumps({"stage":report["stage"],"seller_writes":report["seller_writes"],
        "items":{k:{"stage":v.get("stage"),"product_id":v.get("product_id")} for k,v in report["items"].items()}},
        ensure_ascii=False),flush=True)

def require(ok,msg):
    if not ok: raise ValueError(msg)

def from_attr(row,id):
    a=[x for x in row.get("attributes",[]) if x.get("id")==id]
    require(len(a)==1 and len(a[0].get("values") or [])==1,"Reference missing attribute "+str(id))
    v=a[0]["values"][0]
    return {"id":id,"complex_id":0,
            "values":[{"value":v.get("value"),"dictionary_value_id":v.get("dictionary_value_id") or 0}]}

def text_attr(id,value):
    return {"id":id,"complex_id":0,"values":[{"value":str(value),"dictionary_value_id":0}]}

def verify_original(item):
    sku=item["sku"]
    require(len(item.get("media") or [])==1 and item["media"][0].get("approved") is True,
            "Approved original media missing "+sku)
    m=item["media"][0]
    url=m.get("url") or ""
    require(url.startswith("https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/"+sku+"_1.png"),
            "Wrong original image source "+sku)
    req=urllib.request.Request(url,headers={"User-Agent":"LEOPARTS/6T30-Ozon-safe-publication"})
    with urllib.request.urlopen(req,timeout=35) as response:
        binary=response.read(6500000)
    digest=hashlib.sha256(binary).hexdigest()
    require(digest==m.get("sha256")==EXPECTED[sku],"Approved photo SHA256 does not match "+sku)
    report["items"].setdefault(sku,{})["media_sha256"]=digest
    return url

def preflight_reference():
    found=oz.get_product_names(offer_ids=[SAMPLE],limit=1)
    require(len(found)==1 and found[0].get("offer_id")==SAMPLE,
            "Could not fetch exact existing DSG6 assembly from seller account")
    ref=found[0]
    require(ref.get("description_category_id")==17028756 and ref.get("type_id")==971100605,
            "Live seller assembly category changed")
    require("Мехатроник" in ref.get("name","") and "в сборе" in ref.get("name",""),
            "Seller reference is not a transmission control assembly")
    brand,customs,marked=(from_attr(ref,id) for id in (85,22232,23536))
    require(brand["values"][0]["value"]=="Нет бренда","Seller reference brand mismatch")
    require(customs["values"][0]["dictionary_value_id"]==971400283 and
            str(customs["values"][0]["value"]).startswith("8708409909"),
            "Seller transmission control assembly customs class changed")
    require(str(marked["values"][0]["value"]).lower()=="false",
            "Seller reference marking changed")
    attrs=oz.get_category_attributes(17028756,971100605)
    require(attrs,"Ozon live required attributes unavailable")
    mandatory={int(a["id"]) for a in attrs if a.get("is_required") and a.get("id")}
    supported={85,7236,8229,22232,23536}
    require(mandatory.issubset(supported),"Unverified Ozon mandatory attributes: "+str(sorted(mandatory-supported)))
    report["reference"]={"sku":SAMPLE,"category":17028756,"type":971100605,
       "customs_from_existing_seller_assembly":customs["values"][0],"mandatory_ids":sorted(mandatory)}
    return brand,customs,marked

def build(item,url,base):
    sku=item["sku"]
    require(item["prices"]["ozon"]==58850 and item["stock"]==3,"Owner commercial data changed "+sku)
    require(item["dimensions_are_estimates"] is True and item["brand"]=="Нет бренда",
            "Owner-defined product inputs changed "+sku)
    require(item["title"].endswith("Gen1") or item["title"].endswith("Gen2"),
            "Wrong generation "+sku)
    require(sku.split("-")[-1] in item["title"].lower(),
            "Gen1/Gen2 label ambiguous "+sku)
    require("в сборе" in item["description"],"Assembly description missing")
    dims=item["dimensions"]
    for k in ("weight_g","length_mm","width_mm","height_mm"):
        require(isinstance(dims[k],int) and dims[k]>0,"Package dimensions missing "+sku)
    brand,tn,marked=base
    attributes=[brand,tn,marked,text_attr(4180,item["title"]),text_attr(4191,item["description"]),
        text_attr(7236,sku),text_attr(9024,sku),
        text_attr(8513,1),text_attr(4384,"Блок управления АКПП 6T30/6T40/6T45/6T50 с соленоидами, "+sku.split("-")[-1].upper()+", в сборе — 1 шт."),
        text_attr(9048,"блок управления АКПП 6T30 6T40 6T45 6T50 "+sku.split("-")[-1])]
    return {"offer_id":sku,"name":item["title"],"description_category_id":17028756,
      "type_id":971100605,"new_description_category_id":0,"attributes":attributes,
      "complex_attributes":[],"barcode":"","color_image":"","images":[],
      "primary_image":url,"pdf_list":[],"currency_code":"RUB","price":"58850",
      "old_price":"0","vat":"0","depth":dims["length_mm"],"width":dims["width_mm"],
      "height":dims["height_mm"],"dimension_unit":"mm","weight":dims["weight_g"],"weight_unit":"g"}

def try_stock(sku):
    if report["items"][sku].get("stock_submission"): return
    warehouse=os.getenv("OZON_WAREHOUSE_ID","").strip()
    if not warehouse.isdigit() or int(warehouse)<=0:
        matches={int(w["warehouse_id"]) for w in oz.get_warehouses() if w.get("warehouse_id")}
        require(len(matches)==1,"No unique approved Ozon FBS warehouse")
        warehouse=str(next(iter(matches)))
    result=oz.update_stocks([{"offer_id":sku,"stock":3,"warehouse_id":int(warehouse)}])
    report["seller_writes"]+=1
    report["items"][sku]["stock_submission"]={"warehouse_id":int(warehouse),"response":result}

def run():
    manifest=json.loads(MANIFEST.read_text(encoding="utf8"))
    require(manifest.get("approved") is True and manifest.get("policy",{}).get("never_infer_customs") is True,
            "Manifest safety policy changed")
    items={x["sku"]:x for x in manifest["items"]}
    require(all(s in items for s in SKUS),"Missing one approved SKU")
    previous=json.loads(OUT.read_text(encoding="utf8")) if OUT.exists() else {}
    existing={x.get("offer_id"):x for x in oz.list_products(limit=100)}
    to_create=[]
    for sku in SKUS:
        if sku in existing:
            report["items"][sku]={"stage":"already_in_ozon","product_id":existing[sku].get("product_id")}
            continue
        # Never repeat an import with a recorded previous task id.
        require(not previous.get("task_id") or
             not any(x.get("offer_id")==sku for x in previous.get("submitted_items") or []),
             "Earlier Ozon import task recorded, inspect instead of importing again")
        url=verify_original(items[sku])
        report["items"][sku].update({"stage":"original_media_verified","price":58850,"stock_intended":3})
        to_create.append((items[sku],url))
    if to_create:
        base=preflight_reference()
        data=[build(item,url,base) for item,url in to_create]
        report["stage"]="all_preflights_verified"
        persist()
        # Single bounded submission; no existing SKU ever included.
        ack=oz.import_products(data)
        report["seller_writes"]+=1
        report["task_id"]=(ack.get("result") or {}).get("task_id")
        report["submitted_items"]=[{"offer_id":x["offer_id"]} for x in data]
        report["import_ack"]=ack
        report["stage"]="import_submitted"
        for sku in [x["offer_id"] for x in data]:
            report["items"][sku]["stage"]="import_submitted"
        persist()
    for attempt in range(8):
        if attempt: time.sleep(12)
        if report.get("task_id"):
            try:
                report["import_task_status"]=oz.get_import_status(report["task_id"])
            except Exception as e:
                report["import_status_error"]=str(e)[:160]
        info=oz.get_product_info_list(list(SKUS))
        for entry in info:
            sku=entry.get("offer_id")
            if sku not in SKUS: continue
            row=report["items"][sku]
            row["product_id"]=entry.get("id") or entry.get("product_id")
            row["moderation"]=entry.get("statuses")
            row["errors"]=entry.get("errors")
            row["stage"]="created_verified" if (entry.get("statuses") or {}).get("is_created") else "moderation_or_processing"
            if row["stage"]=="created_verified" and not row.get("stock_submission"):
                try:
                    try_stock(sku)
                    row["stage"]="created_stock_submitted"
                except Exception as e:
                    row["stock_blocker"]=str(e)[:200]
        report["stage"]="readback_completed"
        persist()
        if all(report["items"].get(s,{}).get("stage") in ("created_stock_submitted",) for s in SKUS): break

if __name__=="__main__":
    try: run()
    except Exception as e:
        report["stage"]="failed_guard_or_api"
        report["error_type"]=type(e).__name__
        report["error"]=str(e)[:350]
        persist()
        raise
