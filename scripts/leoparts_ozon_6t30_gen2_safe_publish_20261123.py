"""Publish only approved 6T30 Gen2 solenoid kit to Ozon using seller's exact
existing 6T30 Gen1 listing as the product-class reference.
Never change existing products, prices, media, stock, or other channels.
A failed preflight makes no seller writes.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ozon_client as oz

SKU = "6t30-solenoids-gen2"
REFERENCE_SKU = "6t30"
MANIFEST = ROOT / "data/product_intake_v2/requests/leoparts-five-20261123.json"
OUTPUT = ROOT / "data/leoparts_ozon_6t30_gen2_publish_20261123.json"
STATE = {"scope": SKU, "reference_seller_offer": REFERENCE_SKU,
         "channels_touched": ["ozon"], "started_utc": datetime.now(timezone.utc).isoformat(),
         "stage": "preflight", "seller_writes": 0, "checkpoints": {}}


def save(**kv):
    STATE.update(kv)
    OUTPUT.write_text(json.dumps(STATE, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf8")
    print("OZON_GEN2_STATUS", json.dumps({"stage": STATE["stage"], "seller_writes": STATE["seller_writes"]}, ensure_ascii=False), flush=True)


def require(cond, message):
    if not cond:
        raise ValueError(message)


def attr(offer, attr_id):
    result = [x for x in offer.get("attributes", []) if x.get("id") == attr_id]
    require(len(result) == 1 and len(result[0].get("values") or []) == 1,
            "Reference seller SKU is missing exact attribute " + str(attr_id))
    value = result[0]["values"][0]
    return {"id": attr_id, "complex_id": 0, "values": [{"value": value.get("value", ""),
             "dictionary_value_id": value.get("dictionary_value_id") or 0}]}


def text_attr(id, val):
    return {"id": id, "complex_id": 0,
            "values": [{"value": str(val), "dictionary_value_id": 0}]}


def find_existing():
    rows = oz.list_products(limit=100)
    return [x for x in rows if str(x.get("offer_id") or "").casefold() == SKU.casefold()]


def verify_photo(item):
    media = item["media"]
    require(len(media) == 1 and media[0].get("approved") is True, "Gen2 image not approved")
    m = media[0]
    url = m.get("url") or ""
    require(m.get("source") == "public_url" and url.startswith("https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/6t30-solenoids-gen2_1.png"),
            "Unexpected Gen2 photo host or filename")
    request = urllib.request.Request(url, headers={"User-Agent": "LEOPARTS-Ozon-ApprovedMedia-Check"})
    with urllib.request.urlopen(request, timeout=35) as result:
        data = result.read(6500000)
    digest = hashlib.sha256(data).hexdigest()
    require(digest == m.get("sha256") == "4b7c231bfb871186aabfb8042c73dd44780345e0f6312324c2b30f09cd5bdaeb",
            "Original approved Gen2 photo SHA256 mismatch")
    STATE["checkpoints"]["photo"] = {"verified_sha256": digest, "bytes": len(data)}
    return url


def build(item, sample, url):
    require(sample.get("offer_id") == REFERENCE_SKU, "Reference SKU mismatch")
    require("6T30" in str(sample.get("name") or ""), "Existing seller sample is not 6T30")
    require(sample.get("description_category_id") == 17028756 and sample.get("type_id") == 970984897,
            "Existing seller solenoid taxonomy differs from approved catalog reference")
    brand, customs, marked = (attr(sample, n) for n in (85, 22232, 23536))
    require(brand["values"][0]["value"] == "Нет бренда", "Reference brand not no-brand")
    require(str(customs["values"][0]["value"]).startswith("8708409909") and
            customs["values"][0]["dictionary_value_id"] == 971400283,
            "Existing exact 6T30 solenoid class TN VED no longer matches seller baseline")
    require(str(marked["values"][0]["value"]).lower() == "false", "Marking status differs")
    required = oz.get_category_attributes(17028756, 970984897)
    require(len(required) > 0, "Cannot read live Ozon category requirements")
    required_ids = {int(x["id"]) for x in required if x.get("is_required") and x.get("id")}
    supported = {85, 8229, 22232, 23536, 7236}
    require(required_ids.issubset(supported),
            "New Ozon required attributes not independently verified: " + repr(sorted(required_ids-supported)))
    require({85, 22232}.issubset(required_ids),
            "Unexpected current mandatory attribute definition for exact solenoid family")

    price = item["prices"]["ozon"]
    require(price == 19880 and item["stock"] == 20 and item["dimensions_are_estimates"] is True,
            "Owner-approved commercial inputs changed")
    require(item.get("brand") == "Нет бренда" and "Gen2" in item["title"],
            "Owner-approved Gen2 identity changed")
    require(item.get("channels", []).count("ozon") == 1, "Unexpected channel configuration")

    attributes = [
        brand, customs, marked,
        text_attr(4180, item["title"]),
        text_attr(4191, item["description"]),
        text_attr(7236, SKU),
        text_attr(9024, SKU),
        text_attr(8513, 7),
        text_attr(4384, "Соленоиды АКПП 6T30/6T40/6T45/6T50 второго поколения (Gen2) — 7 шт."),
        text_attr(9048, "соленоиды АКПП 6T30 6T40 6T45 6T50 второе поколение Gen2"),
    ]
    dim=item["dimensions"]
    for key in ("weight_g","length_mm","width_mm","height_mm"):
        require(isinstance(dim[key], int) and dim[key]>0, "Missing approved dimensions: "+key)
    STATE["checkpoints"]["ozon_category"] = {"id":17028756,"type":970984897,
        "reference_offer":REFERENCE_SKU,"reference_customs_code":customs["values"][0]["value"],
        "required_ids":sorted(required_ids)}
    return {"offer_id":SKU,"name":item["title"],"description_category_id":17028756,
            "type_id":970984897,"new_description_category_id":0,"attributes":attributes,
            "complex_attributes":[],"barcode":"","color_image":"","images":[],
            "primary_image":url,"pdf_list":[],"currency_code":"RUB","price":str(price),
            "old_price":"0","vat":"0","depth":dim["length_mm"],"width":dim["width_mm"],
            "height":dim["height_mm"],"dimension_unit":"mm","weight":dim["weight_g"],
            "weight_unit":"g"}


def run():
    manifest = json.loads(MANIFEST.read_text(encoding="utf8"))
    require(manifest.get("approved") is True and
            manifest.get("policy",{}).get("never_recalculate_approved_prices") is True,
            "Batch no longer approved")
    matched = [x for x in manifest["items"] if x["sku"] == SKU]
    require(len(matched) == 1, "Approved Gen2 SKU missing")
    item = matched[0]
    previous = json.loads(OUTPUT.read_text(encoding="utf8")) if OUTPUT.exists() else {}
    existing=find_existing()
    require(len(existing)<=1, "Duplicate Ozon seller SKU")
    if existing:
        save(stage="already_exists", existing_product_id=existing[0].get("product_id"))
        return
    require(not previous.get("task_id"),
            "Previous Gen2 import was submitted: check task " + str(previous.get("task_id")))
    image_url=verify_photo(item)
    reference = oz.get_product_names(offer_ids=[REFERENCE_SKU],limit=1)
    require(len(reference)==1, "Seller 6T30 first-gen class cannot be verified live")
    payload=build(item, reference[0], image_url)
    save(stage="preflight_verified", target_price=19880, target_stock=20,
         approved_photo=image_url)
    # Once and only once after all preflights; report stores task for idempotence.
    response = oz.import_products([payload])
    STATE["seller_writes"] += 1
    task_id = (response.get("result") or {}).get("task_id")
    save(stage="import_submitted",task_id=task_id,import_ack=response)
    if not task_id:
        return
    for n in range(6):
        time.sleep(9)
        try:
            status = oz.get_import_status(int(task_id))
            STATE["checkpoints"]["import_status"] = status
        except Exception as e:
            STATE["checkpoints"]["poll_error"] = str(e)[:130]
        try:
            info = oz.get_product_info_list([SKU])
            found=[x for x in info if x.get("offer_id")==SKU and not x.get("_error")]
            if found:
                detail=found[0]
                STATE["checkpoints"]["catalog_readback"] = {
                    "product_id":detail.get("id") or detail.get("product_id"),
                    "statuses":detail.get("statuses"),"errors":detail.get("errors")}
                if (detail.get("statuses") or {}).get("is_created"):
                    save(stage="created_verified",product_id=detail.get("id") or detail.get("product_id"))
                    # Only stock THIS new SKU, never other catalog products.
                    try:
                        wid = os.getenv("OZON_WAREHOUSE_ID", "").strip()
                        if not wid.isdigit() or int(wid) <= 0:
                            warehouses = oz.get_warehouses()
                            ids = {int(w["warehouse_id"]) for w in warehouses if w.get("warehouse_id")}
                            require(len(ids) == 1, "No single verified FBS warehouse for this new SKU")
                            wid = str(next(iter(ids)))
                        reply = oz.update_stocks([{"offer_id": SKU, "stock": 20, "warehouse_id": int(wid)}])
                        STATE["seller_writes"] += 1
                        STATE["checkpoints"]["stock_submit"]={"warehouse_id":int(wid),"response":reply}
                        save(stage="created_stock_submitted")
                    except Exception as e:
                        STATE["checkpoints"]["stock_blocker"]=str(e)[:220]
                        save(stage="created_stock_not_verified")
                    return
        except Exception as e:
            STATE["checkpoints"]["readback_error"]=str(e)[:130]
        save(stage="import_pending_readback")
    save(stage="import_submitted_not_yet_verified")


if __name__=="__main__":
    try:
        run()
    except Exception as exc:
        save(stage="blocked_or_error",error_type=type(exc).__name__,error=str(exc)[:350])
        raise
