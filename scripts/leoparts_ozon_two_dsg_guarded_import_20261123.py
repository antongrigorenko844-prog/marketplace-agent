"""Ozon-only publication of two approved service kits.
No submission while the exact-product TN VED decision remains unconfirmed.
No price changes to existing cards; no Avito actions.
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ozon_client as oz

BATCH=ROOT/"data/product_intake_v2/requests/leoparts-five-20261123.json"
STAGE=ROOT/"data/leoparts_ozon_dsg_tools_stage_20261123_result.json"
DECISIONS=ROOT/"data/leoparts_ozon_dsg_tools_tnved_decision_20261123.json"
OUT=ROOT/"data/leoparts_ozon_two_dsg_guarded_import_20261123_result.json"
SKUS=("tool-dq200-acc-restore","tool-dsg-pressure-hp")
PRICES={"tool-dq200-acc-restore":38800,"tool-dsg-pressure-hp":28800}
CATEGORY_ID=83250454
TYPE_ID=971437067  # Ozon: Набор инструментов для автомобиля
STATUS={"channel":"ozon","skus":SKUS,"run_utc":datetime.now(timezone.utc).isoformat(),
        "seller_writes":0,"items":{}}

def save():
    OUT.write_text(json.dumps(STATUS,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf8")
    print("TWO_DSG_OZON",json.dumps({k:v.get("state") for k,v in STATUS["items"].items()},
                                  ensure_ascii=False),flush=True)

def require(test,message):
    if not test:raise ValueError(message)

def attr(id,value,dict_id=0):
    return {"id":id,"complex_id":0,"values":[{"dictionary_value_id":dict_id,"value":str(value)}]}

def get_customs_dictionary(code):
    rows=oz.search_attribute_values(CATEGORY_ID,TYPE_ID,22232,str(code),limit=100)
    matched=[x for x in rows if str(x.get("value") or "").split(" - ")[0].strip()==str(code)]
    require(len(matched)==1,"Customs code not uniquely accepted by this Ozon product type: "+code)
    return matched[0]

def verify_media(sku,item,staged):
    declared=item.get("media") or []
    photos=staged.get("media") or []
    require(len(declared)==len(photos) and len(declared)>0,"Missing verified staged artwork for "+sku)
    urls=[]
    for i,(expected,published) in enumerate(zip(declared,photos),start=1):
        p=(ROOT/expected.get("path","")).resolve()
        require(expected.get("approved") is True and expected.get("source")=="repository",
                "Non-approved media in manifest "+sku)
        require(p.is_relative_to(ROOT) and p.is_file(),"Missing original image "+sku)
        sha=hashlib.sha256(p.read_bytes()).hexdigest()
        require(sha==expected.get("sha256")==published.get("sha256"),
                "Approved original image mismatch "+sku)
        require(published.get("url","").startswith(
                "https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/"+sku+"_"+str(i)+".png"),
                "Wrong public image source "+sku)
        urls.append(published["url"])
    return urls

def approved_category():
    rows=oz.get_category_attributes(CATEGORY_ID,TYPE_ID)
    require(rows,"Cannot verify current Ozon toolkit category attributes")
    required={int(x["id"]) for x in rows if x.get("is_required") and x.get("id")}
    supported={85,8229,22232,23536,7236}
    require(required.issubset(supported),"Unverified Ozon toolkit mandatory attributes: "+str(required-supported))
    require({85,8229,22232,23536}.issubset(required),
            "Current Ozon mandatory attributes changed unexpectedly")
    return sorted(required)

def build(sku,item,photos,customs):
    d=item["dimensions"]
    require(item["dimensions_are_estimates"] is True,
            "Owner logistics estimates changed without new approval")
    for name in ("weight_g","length_mm","width_mm","height_mm"):
        require(isinstance(d.get(name),int) and d[name]>0,
                "Missing approved package dimension "+sku+":"+name)
    require(item["prices"]["ozon"]==PRICES[sku] and item["stock"]==3,
            "Owner prices or stock changed "+sku)
    require(item.get("brand")=="Нет бренда" and item["title"].startswith("Набор "),
            "Owner-approved toolkit identity changed "+sku)
    attributes=[
        attr(85,"Нет бренда",126745801),
        attr(22232,customs["value"],int(customs["id"])),
        attr(23536,"false"),
        attr(4180,item["title"]),
        attr(4191,item["description"]),
        attr(7236,sku),
        attr(9024,sku),
        attr(8513,1),
        attr(4384,item["title"]+" — 1 набор"),
    ]
    return {"offer_id":sku,"name":item["title"],
            "description_category_id":CATEGORY_ID,"type_id":TYPE_ID,
            "new_description_category_id":0,"attributes":attributes,
            "complex_attributes":[],"barcode":"","color_image":"",
            "images":photos[1:],"primary_image":photos[0],"pdf_list":[],
            "currency_code":"RUB","price":str(PRICES[sku]),"old_price":"0",
            "vat":"0","depth":d["length_mm"],"width":d["width_mm"],
            "height":d["height_mm"],"dimension_unit":"mm",
            "weight":d["weight_g"],"weight_unit":"g"}

def inspect_info(sku):
    info=oz.get_product_info_list([sku])
    matches=[x for x in info if x.get("offer_id")==sku and not x.get("_error")]
    if len(matches)!=1:return None
    return matches[0]

def maybe_stock(sku,row):
    if not (row.get("statuses") or {}).get("is_created"):return
    previous=STATUS["items"][sku]
    if previous.get("stock_submitted"):return
    wid=os.getenv("OZON_WAREHOUSE_ID","").strip()
    require(wid.isdecimal() and int(wid)>0,"Verified OZON_WAREHOUSE_ID secret missing")
    ack=oz.update_stocks([{"offer_id":sku,"stock":3,"warehouse_id":int(wid)}])
    STATUS["seller_writes"]+=1
    previous["stock_submitted"]=ack

def run():
    manifest=json.loads(BATCH.read_text(encoding="utf8"))
    staged=json.loads(STAGE.read_text(encoding="utf8"))
    decisions=json.loads(DECISIONS.read_text(encoding="utf8"))
    require(manifest.get("approved") is True and manifest["policy"].get("never_infer_customs") is True,
            "Owner customs safety guard not confirmed")
    require(decisions.get("policy",{}).get("allow_inferred_code") is False,
            "Owner classification guard changed")
    items={x["sku"]:x for x in manifest["items"]}
    previous=json.loads(OUT.read_text(encoding="utf8")) if OUT.exists() else {"items":{}}
    listing={str(x.get("offer_id")):x for x in oz.list_products(limit=100)}
    must_send=[]
    for sku in SKUS:
        info=STATUS["items"].setdefault(sku,{"state":"preflight"})
        exist=listing.get(sku)
        if exist:
            info["state"]="already_in_ozon"
            info["product_id"]=exist.get("product_id")
            continue
        if previous.get("items",{}).get(sku,{}).get("task_id"):
            info["state"]="previous_import_task_pending_readback"
            info["task_id"]=previous["items"][sku]["task_id"]
            continue
        decision=decisions["items"][sku]
        code=decision.get("classification_code")
        if not decision.get("classification_confirmed") or not code or not decision.get("classification_evidence"):
            info["state"]="awaiting_verified_tnved"
            info["required_action"]="Exact-product classification code must be confirmed, not inferred from analogous articles."
            continue
        require(str(code).isdigit() and len(str(code))==10,"Unusable TN VED code "+sku)
        photos=verify_media(sku,items[sku],staged["products"][sku])
        info["media_verified"]=True
        info["price_rub"]=PRICES[sku]
        must_send.append((sku,code,photos))
    if not must_send:
        STATUS["summary"]="No seller changes. Missing classification or existing items."
        save();return
    STATUS["ozon_required_attribute_ids"]=approved_category()
    for sku,code,photos in must_send:
        info=STATUS["items"][sku]
        try:
            dictionary=get_customs_dictionary(code)
            payload=build(sku,items[sku],photos,dictionary)
            info["category_id"]=CATEGORY_ID
            info["type_id"]=TYPE_ID
            info["tnved_confirmed"]=code
            # Every import is isolated to prevent one rejection affecting another.
            ack=oz.import_products([payload])
            STATUS["seller_writes"]+=1
            task_id=(ack.get("result") or {}).get("task_id")
            info["task_id"]=task_id
            info["state"]="submitted_not_yet_verified"
            save()
            if not task_id:continue
            for n in range(8):
                time.sleep(10)
                try:
                    task=oz.get_import_status(int(task_id))
                    info["task_status"]=task
                except Exception as e:
                    info["task_status_error"]=str(e)[:150]
                current=inspect_info(sku)
                if current:
                    info["product_id"]=current.get("id") or current.get("product_id")
                    info["moderation"]=current.get("statuses")
                    info["errors"]=current.get("errors")
                    if (current.get("statuses") or {}).get("is_created"):
                        info["state"]="created"
                        try:
                            maybe_stock(sku,current)
                            info["state"]="stock_submitted"
                        except Exception as e:
                            info["stock_blocker"]=str(e)[:200]
                        break
                    if (current.get("statuses") or {}).get("moderate_status")=="declined":
                        info["state"]="moderation_declined"
                        break
                save()
            save()
        except Exception as e:
            info["state"]="blocked_or_api_error"
            info["error"]=str(e)[:250]
            save()
    save()
if __name__=="__main__":
    try:run()
    except Exception as e:
        STATUS["fatal"]=str(e)[:300]
        save()
        raise
