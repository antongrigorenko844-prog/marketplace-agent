"""LEOPARTS Tilda-only board pricing: 0AM927769D/E/F/G/K.

Explicit owner-approved five-SKU mapping, never touches Ozon/WB/Market/Avito.
First inspect public site, and on apply send price-only CommerceML (no photo,
stock, descriptions); read back prices, verify non-price catalog integrity.
After success save a Tilda-ONLY override JSON to prevent future feed resets.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from datetime import datetime,timezone
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from scripts.leoparts_direct_operation_nov21 import site_records
import tilda_commerceml as tc

REQUEST=ROOT/"data/site_board_prices_request.json"
OVERRIDE=ROOT/"data/site_price_only_overrides.json"
SITE_PRICES=ROOT/"data/site_prices.json"
OUT=ROOT/"data/site_board_prices_result.json"
TARGETS={
    "0AM927769D":37500,
    "0AM927769E":39500,
    "0AM927769F":39500,
    "0AM927769G":39500,
    "0AM927769K":39500,
}
EXPECTED_OLD={
    "0AM927769D":39500,
    "0AM927769E":44910,
    "0AM927769F":39500,
    "0AM927769G":44910,
    "0AM927769K":44910,
}
COLUMNS=("uid","sku","externalid","external_id","title","descr","text","gallery",
         "quantity","inv","partuids","characteristics","json_options","unit",
         "priceold","brand","tabs","mark","url")
def utc():return datetime.now(timezone.utc).isoformat()
def clean_price(x):
    v=float(str(x).replace(" ","").replace(",","."))
    if v<=0 or int(v)!=v:raise ValueError("Invalid Tilda price")
    return int(v)
def canon(roots):
    """Verify every product except target price and target-variant price."""
    found={}
    for uid,row in roots.items():
        x={k:row.get(k) for k in COLUMNS}
        eds=row.get("editions") or []
        if not isinstance(eds,list):raise ValueError("Unsupported Tilda editions")
        if row.get("sku") in TARGETS:
            x["editions"]=[{k:v for k,v in ed.items() if k!="price"} for ed in eds]
        else:
            x["price"]=row.get("price")
            x["editions"]=eds
        found[str(uid)]=x
    return hashlib.sha256(json.dumps(found,ensure_ascii=False,sort_keys=True,default=str).encode()).hexdigest()
def validate(rows,require_old=False):
    found={}
    for sku,price in TARGETS.items():
        match=[r for r in rows.values() if r.get("sku")==sku]
        if len(match)!=1:raise ValueError("Tilda SKU missing or duplicated: "+sku)
        r=match[0]
        if not (str(r.get("uid") or "").isdigit() and str(r.get("externalid",r.get("external_id")))==sku):
            raise ValueError("Tilda external identity mismatch: "+sku)
        title=str(r.get("title") or "").casefold()
        if not (sku.casefold() in title or (("плат" in title or "электрон" in title) and "dq200" in title)):
            raise ValueError("Could not positively identify board title: "+sku)
        if "чип" in title or "комплект" in title or "мехатроник в сборе" in title:
            raise ValueError("Not standalone board: "+sku)
        editions=r.get("editions") or []
        if not isinstance(editions,list) or len(editions)>1:
            raise ValueError("Unverified edition count: "+sku)
        for ed in editions:
            if str(ed.get("uid"))!=str(r["uid"]) or ed.get("sku")!=sku:
                raise ValueError("Edition identity mismatch: "+sku)
        present=[clean_price(r["price"])]+[clean_price(ed["price"]) for ed in editions]
        if require_old and any(p not in (EXPECTED_OLD[sku],price) for p in present):
            raise ValueError("Live price unexpected for "+sku)
        found[sku]={"uid":str(r["uid"]),"title":str(r["title"])[:180],
                    "live_prices":present,"target_price":price,"edition_count":len(editions)}
    return found
def xml(parts):
    def sub(node,tag,value):
        ET.SubElement(node,tag).text=str(value)
    attrs={"ВерсияСхемы":"2.05","ДатаФормирования":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
    base=ET.Element("КоммерческаяИнформация",attrs)
    offers=ET.Element("КоммерческаяИнформация",attrs)
    cat=ET.SubElement(base,"Каталог",{"СодержитТолькоИзменения":"true"})
    sub(cat,"Ид",tc.CATALOG_ID);sub(cat,"ИдКлассификатора",tc.CLASSIFIER_ID)
    products=ET.SubElement(cat,"Товары")
    pack=ET.SubElement(offers,"ПакетПредложений",{"СодержитТолькоИзменения":"true"})
    sub(pack,"Ид",tc.CATALOG_ID+"#offers")
    sub(pack,"ИдКаталога",tc.CATALOG_ID);sub(pack,"ИдКлассификатора",tc.CLASSIFIER_ID)
    kind=ET.SubElement(ET.SubElement(pack,"ТипыЦен"),"ТипЦены")
    sub(kind,"Ид",tc.PRICE_TYPE_ID);sub(kind,"Наименование","Розничная")
    entries=ET.SubElement(pack,"Предложения")
    for sku in parts:
        prod=ET.SubElement(products,"Товар");offer=ET.SubElement(entries,"Предложение")
        for tag in ("Ид","Артикул","Наименование"):
            val=parts[sku]["title"] if tag=="Наименование" else sku
            sub(prod,tag,val);sub(offer,tag,val)
        price=ET.SubElement(ET.SubElement(offer,"Цены"),"Цена")
        for tag,val in [("ИдТипаЦены",tc.PRICE_TYPE_ID),("ЦенаЗаЕдиницу",TARGETS[sku]),
                        ("Валюта","RUB"),("Единица","шт"),("Коэффициент",1)]:
            sub(price,tag,val)
    return tuple(ET.tostring(root,encoding="utf-8",xml_declaration=True).decode() for root in (base,offers))
def save_sources():
    existing=json.loads(OVERRIDE.read_text()) if OVERRIDE.exists() else {}
    if not isinstance(existing,dict):raise ValueError("Corrupt site-only override")
    existing.update({k:int(v) for k,v in TARGETS.items()})
    OVERRIDE.write_text(json.dumps(existing,ensure_ascii=False,indent=2)+"\n")
    data=json.loads(SITE_PRICES.read_text())
    for sku,price in TARGETS.items():data[sku.casefold()]=price
    SITE_PRICES.write_text(json.dumps(data,ensure_ascii=False,indent=2)+"\n")
def main():
    req=json.loads(REQUEST.read_text())
    if (req.get("target_skus")!=TARGETS or req.get("site_only") is not True
            or req.get("mode") not in ("inspect","apply")):
        raise ValueError("Unapproved site-only price request")
    mode=req["mode"]
    result={"started_utc":utc(),"mode":mode,"channel":"tilda_only","targets":TARGETS,
            "avito_price_writes":0,"marketplace_price_writes":0,"stock_writes":0,
            "photos_sent":0,"status":"preflight","errors":[]}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    try:
        before=site_records()
        b=validate(before,True)
        result.update(before=b,nonprice_hash_before=canon(before))
        pending=[k for k,v in b.items() if any(p!=TARGETS[k] for p in v["live_prices"])]
        result["pending"]=pending
        if mode=="inspect":
            result["status"]="needs_apply" if pending else "already_verified"
        elif pending:
            if not all(os.getenv(n) for n in ("TILDA_COMMERCEML_URL","TILDA_COMMERCEML_USER","TILDA_COMMERCEML_PASSWORD")):
                raise RuntimeError("Tilda CommerceML credentials missing")
            # No images and NO quantity elements: this is a price-only update.
            data,offers=xml({k:b[k] for k in pending})
            if "<Картинка>" in data or "<Количество>" in offers or "<Описание>" in data:
                raise ValueError("Nonprice CommerceML field unexpectedly present")
            result["attempted"]=True
            result["site_written_skus"]=pending
            OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
            result["tilda_steps"]=tc.push(os.environ["TILDA_COMMERCEML_URL"],
                os.environ["TILDA_COMMERCEML_USER"],os.environ["TILDA_COMMERCEML_PASSWORD"],
                data,offers,timeout=85)
            for attempt in range(6):
                after=site_records()
                a=validate(after,False)
                result["after"]=a
                result["nonprice_hash_after"]=canon(after)
                match=all(all(p==TARGETS[sku] for p in a[sku]["live_prices"]) for sku in TARGETS)
                integrity=result["nonprice_hash_before"]==result["nonprice_hash_after"]
                if match and integrity:
                    result["status"]="verified"
                    save_sources()
                    result["site_override_persisted"]=True
                    break
                if match and not integrity:
                    raise RuntimeError("Tilda nonprice data or other product changed: manual audit required")
                if attempt<5:time.sleep(6)
            else:
                result["status"]="submitted_pending_verification"
        else:
            # No network write needed; still make new site-only prices stick for future sync.
            save_sources();result["site_override_persisted"]=True
            result["after"]=b
            result["status"]="verified"
    except Exception as exc:
        result["status"]="blocked_or_unverified"
        result["errors"].append(type(exc).__name__+": "+str(exc)[:280])
    result["finished_utc"]=utc()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"status":result["status"],"targets":TARGETS,
        "before":result.get("before"),"after":result.get("after"),
        "pending":result.get("pending"),"errors":result["errors"]},ensure_ascii=False),flush=True)
    return 0 if result["status"] in ("verified","already_verified","needs_apply") else 1
if __name__=="__main__":sys.exit(main())
