"""LEOPARTS Tilda price-only pipeline, case-sensitive UID-aware, with rollback guards.

Run ONLY for an explicitly approved price-change manifest derived from user's
annotated Tilda 150-row workbook. This script does not delete catalog items.
The duplicate Copy: transport plug, UID 351198279983, is NEVER eligible for
automated price sync; it must be manually deleted from the Tilda editor.
Commercial writes are limited to targeted SKU prices via CommerceML.
No Ozon/WB/YM, Avito, media, descriptions, stock or product creation.
"""
from __future__ import annotations

import json, hashlib, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import xml.etree.ElementTree as ET
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import tilda_commerceml as tc
from scripts.leoparts_direct_operation_nov21 import site_records

REQUEST = ROOT / "data/tilda_price_pipeline_request.json"
OUT = ROOT / "data/tilda_price_pipeline_result.json"
SITE_ONLY = ROOT / "data/site_price_only_overrides.json"
SNAPSHOT = ROOT / "data/site_price_review_live_20261124.json"
BLOCKED_DUPLICATE_UID = "351198279983"
BLOCKED_DUPLICATE_EXTERNAL_ID = "vvhXIAeziJxJ7VCAPLJK"

class SitePriceGuardError(ValueError):
    pass

def require(ok: bool, msg: str) -> None:
    if not ok:
        raise SitePriceGuardError(msg)

def price(value: Any) -> int:
    if type(value) not in (int,float):
        raise SitePriceGuardError(f"Price is not numeric: {value!r}")
    v=float(value)
    if v!=int(v) or v<1 or v>500000:
        raise SitePriceGuardError(f"Invalid whole-ruble price: {value!r}")
    return int(v)

def index_by_uid(records) -> dict[str,dict]:
    """Protect exact case, UID and CommerceML identity against collisions."""
    if isinstance(records,dict):
        values=list(records.values())
    elif isinstance(records,list):
        values=records
    else:
        raise SitePriceGuardError("Tilda records must be list or dict")
    by={}
    for row in values:
        require(isinstance(row,dict),"Unsupported public Tilda item")
        uid=str(row.get("uid") or "")
        require(uid.isdigit() and uid not in by,"Duplicate/missing Tilda UID")
        by[uid]=row
    require(len(by)>=100,"Tilda returned incomplete product list")
    return by

def validate_manifest(manifest,live):
    require(manifest.get("schema_version")==1 and
            manifest.get("mode") in ("inspect","apply") and
            manifest.get("owner_approved") is True and
            manifest.get("channel")=="site_only" and
            manifest.get("preserve_avito") is True and
            manifest.get("preserve_marketplaces") is True and
            manifest.get("preserve_stock") is True and
            manifest.get("preserve_media") is True and
            manifest.get("preserve_titles") is True and
            manifest.get("preserve_catalog_products") is True,
            "Missing explicit owner-approved Tilda-only scope")
    edits=manifest.get("targets")
    require(isinstance(edits,list) and 1<=len(edits)<=150,
            "Empty or unusually large Tilda price update")
    seen_skus=set()
    seen_uids=set()
    prepared={}
    for item in edits:
        require(isinstance(item,dict) and
                set(item)=={"uid","sku","externalid","expected_price","approved_price"},
                "Unknown or missing target properties")
        uid=str(item["uid"])
        sku=item["sku"]
        external=str(item["externalid"])
        require(uid != BLOCKED_DUPLICATE_UID and external != BLOCKED_DUPLICATE_EXTERNAL_ID,
                "Owner-requested duplicate awaiting deletion: price must never change")
        require(uid not in seen_uids and sku not in seen_skus,
                "Duplicate SKU/UID in one Tilda update")
        seen_uids.add(uid);seen_skus.add(sku)
        require(uid in live,f"UID {uid} missing from live Tilda")
        row=live[uid]
        require(isinstance(sku,str) and sku==row.get("sku") and
                sku == external and sku==row.get("externalid",row.get("external_id")),
                f"{uid}: SKU/externalid identity mismatch, refuse deletion of copy")
        actual=price(row.get("price"))
        old=price(item["expected_price"])
        new=price(item["approved_price"])
        require(actual in (old,new),
                f"{uid}: current site price changed outside approved {old}/{new}")
        versions=row.get("editions") or []
        require(isinstance(versions,list) and len(versions)<=1,
                f"{uid}: ambiguous multi-variant SKU")
        require(all(str(ed.get("sku"))==sku and
                    str(ed.get("uid"))==uid and
                    price(ed.get("price")) in (old,new) for ed in versions),
                f"{uid}: edition UID/price drift")
        prepared[sku]={"uid":uid,"sku":sku,"externalid":external,
                       "title":str(row.get("title") or sku),
                       "old_price":actual,"new_price":new,
                       "edition_count":len(versions)}
    return prepared

STABLE_FIELDS=(
  "uid","sku","externalid","external_id","title","descr",
  "text","gallery","quantity","inv","partuids","characteristics",
  "json_options","unit","priceold","brand","tabs","mark","url"
)

def fingerprint(records,targets):
    """All other site metadata, other prices and variant metadata must not drift."""
    canon={}
    for uid,row in records.items():
        val={key:row.get(key) for key in STABLE_FIELDS}
        versions=row.get("editions") or []
        if uid in targets:
            val["editions"]=[{k:v for k,v in ed.items() if k!="price"} for ed in versions]
        else:
            val["price"]=row.get("price")
            val["editions"]=versions
        canon[uid]=val
    return hashlib.sha256(json.dumps(canon,ensure_ascii=False,
                            sort_keys=True,default=str).encode()).hexdigest()

def create_price_only_xml(targets: Mapping[str,dict]):
    """CommerceML partial update: prices only, no images, qty or descriptions."""
    attrs={"ВерсияСхемы":"2.05",
           "ДатаФормирования":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
    root=ET.Element("КоммерческаяИнформация",attrs)
    offers=ET.Element("КоммерческаяИнформация",attrs)
    cat=ET.SubElement(root,"Каталог",{"СодержитТолькоИзменения":"true"})
    def tag(parent,name,val):
        ET.SubElement(parent,name).text=str(val)
    tag(cat,"Ид",tc.CATALOG_ID)
    tag(cat,"ИдКлассификатора",tc.CLASSIFIER_ID)
    products=ET.SubElement(cat,"Товары")
    pack=ET.SubElement(offers,"ПакетПредложений",{"СодержитТолькоИзменения":"true"})
    tag(pack,"Ид",tc.CATALOG_ID+"#offers")
    tag(pack,"ИдКаталога",tc.CATALOG_ID)
    tag(pack,"ИдКлассификатора",tc.CLASSIFIER_ID)
    kind=ET.SubElement(ET.SubElement(pack,"ТипыЦен"),"ТипЦены")
    tag(kind,"Ид",tc.PRICE_TYPE_ID)
    tag(kind,"Наименование","Розничная")
    entries=ET.SubElement(pack,"Предложения")
    for sku,entry in targets.items():
        product=ET.SubElement(products,"Товар")
        offer=ET.SubElement(entries,"Предложение")
        for dest in (product,offer):
            tag(dest,"Ид",entry["externalid"])
            tag(dest,"Артикул",sku)
            tag(dest,"Наименование",entry["title"])
        price_xml=ET.SubElement(ET.SubElement(offer,"Цены"),"Цена")
        for key,val in (("ИдТипаЦены",tc.PRICE_TYPE_ID),
                        ("ЦенаЗаЕдиницу",entry["new_price"]),
                        ("Валюта","RUB"),("Единица","шт"),("Коэффициент",1)):
            tag(price_xml,key,val)
    import_xml=ET.tostring(root,encoding="utf-8",xml_declaration=True).decode()
    offers_xml=ET.tostring(offers,encoding="utf-8",xml_declaration=True).decode()
    for fragment in ("Картинка","Количество","Описание","ПометкаУдаления","Остатки"):
        require("<"+fragment+">" not in import_xml and
                "<"+fragment+">" not in offers_xml,
                "Commercial XML unexpectedly contains non-price fields: "+fragment)
    return import_xml,offers_xml

def verified_site_only_source(before,targets):
    require(isinstance(before,dict),"Site-only price mapping invalid")
    merged=dict(before)
    for sku,entry in targets.items():
        merged[sku]=entry["new_price"]
    require(len(merged)==len(before)+sum(sku not in before for sku in targets),
            "Site-only source count unexpectedly changed")
    return merged

def save(report):
    report["checked_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("TILDA_PRICE_PIPELINE",json.dumps({key:report.get(key) for key
          in ("status","mode","counts","error")},ensure_ascii=False),flush=True)

def run():
    result={"status":"starting","seller_price_writes":0,
            "avito_writes":0,"marketplace_writes":0,"stock_writes":0,
            "media_writes":0,"title_writes":0,"site_product_deletes":0}
    try:
        req=json.loads(REQUEST.read_text(encoding="utf-8"))
        before=index_by_uid(site_records())
        targets=validate_manifest(req,before)
        result["mode"]=req["mode"]
        result["status"]="preflight_ok"
        result["targets"]=targets
        result["counts"]={"listed":len(before),"requested":len(targets),
                          "pending":sum(row["old_price"]!=row["new_price"] for row in targets.values())}
        result["fingerprint_before"]=fingerprint(before,{row["uid"] for row in targets.values()})
        original=json.loads(SITE_ONLY.read_text(encoding="utf-8")) if SITE_ONLY.exists() else {}
        new_source=verified_site_only_source(original,targets)
        result["site_override_changes"]={
          sku:{"before":original.get(sku),"after":row["new_price"]}
          for sku,row in targets.items()
          if original.get(sku)!=row["new_price"]
        }
        partial={k:v for k,v in targets.items() if v["old_price"]!=v["new_price"]}
        xml_a,xml_b=create_price_only_xml(partial) if partial else ("","")
        result["xml_bytes"]={"import":len(xml_a),"offers":len(xml_b)}
        save(result)
        if req["mode"]=="inspect":
            result["status"]="inspect_passed_no_writes";save(result);return
        if partial:
            require(all(os.getenv(k) for k in ("TILDA_COMMERCEML_URL",
                "TILDA_COMMERCEML_USER","TILDA_COMMERCEML_PASSWORD")),
                "Tilda CommerceML connection unavailable")
            result["steps"]=tc.push(os.environ["TILDA_COMMERCEML_URL"],
                os.environ["TILDA_COMMERCEML_USER"],
                os.environ["TILDA_COMMERCEML_PASSWORD"],xml_a,xml_b,timeout=80)
        for attempt in range(6):
            after=index_by_uid(site_records())
            actual_prices={}
            for sku,row in targets.items():
                uid=row["uid"]
                live=after.get(uid)
                require(live is not None,"UID missing after update "+uid)
                editions=live.get("editions") or []
                actual_prices[sku]=[price(live["price"])]+[price(ed["price"]) for ed in editions]
            complete=all(all(v==entry["new_price"] for v in actual_prices[sku])
                         for sku,entry in targets.items())
            integrity=fingerprint(after,{row["uid"] for row in targets.values()})
            result["fingerprint_after"]=integrity
            result["live_prices_after"]=actual_prices
            require(integrity==result["fingerprint_before"],
                "OTHER SITE PRODUCT/PRICE/IMAGE/TITLE CHANGED during update; review required")
            if complete:
                # Persist new source ONLY after actual Tilda API readback.
                if original!=new_source:
                    SITE_ONLY.write_text(json.dumps(new_source,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
                    result["source_price_persisted"]=True
                result["status"]="verified_all_tilda_prices"
                result["counts"]["verified"]=len(targets)
                save(result)
                return
            if attempt<5:time.sleep(6)
        raise RuntimeError("Some Tilda prices did not match target")
    except Exception as ex:
        result["status"]="blocked_or_partial_review_required"
        result["error"]={"type":type(ex).__name__,"message":str(ex)[:280]}
        save(result)
        raise

if __name__=="__main__":run()
