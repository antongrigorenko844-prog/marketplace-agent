"""Publish the confirmed physical count to one existing Tilda stock offer."""
import os
import time
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import catalog_editor
import requests
import tilda_commerceml as tc
from sales_availability import quantity_for_sale

SKU = "0AM325587E"
UID = "920284757613"
TARGET = 55


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def source_count():
    row = catalog_editor.load_catalog_edits(str(Path("data/ozon_catalog.xlsx"))).get(SKU)
    require(row is not None, "Missing working-catalog SKU")
    quantity = quantity_for_sale(SKU, row.get("quantity_to_sell"))
    require(Decimal(str(quantity)) == TARGET, "Current saleable working-catalog count changed")


def current():
    page = 1
    matches = {}
    while True:
        res = requests.get(
            "https://store.tildaapi.com/api/getproductslist/",
            params={"storepartuid":"419144347374","recid":"5199578401","size":100,
                    "slice":page,"c":str(time.time_ns()),"getparts":"true","getoptions":"true"},
            headers={"User-Agent":"Mozilla/5.0","Referer":"https://leoparts-akpp.ru/"}, timeout=25)
        res.raise_for_status()
        data = res.json()
        products = data["products"]
        for product in products:
            rows = [product, *(product.get("editions") or [])]
            if any(str(r.get(k) or "").strip().casefold() == SKU.casefold()
                   for r in rows for k in ("sku", "externalid", "external_id")):
                matches[str(product.get("uid"))] = product
        nxt = data.get("nextslice")
        if not nxt or not products:
            break
        require(page < int(nxt) <= 100, "Unexpected public pagination")
        page = int(nxt)
    require(set(matches) == {UID}, "Ambiguous Tilda SKU or edition mapping")
    prod = matches[UID]
    require(prod.get("sku") == SKU and (prod.get("externalid") or prod.get("external_id")) == SKU,
            "Tilda exchange ID mismatch")
    url = urlsplit(str(prod.get("url") or ""))
    require(url.hostname == "leoparts-akpp.ru" and "/tproduct/"+UID+"-" in url.path,
            "Tilda public UID mismatch")
    editions = prod.get("editions") or []
    require(len(editions) == 1 and str(editions[0].get("uid")) == UID and editions[0].get("sku") == SKU,
            "Tilda SKU gained independent editions")
    require("гидроаккумулятор" in prod["title"].casefold(), "Unexpected Tilda product")
    rows = [prod, editions[0]]
    quantities = tuple(Decimal(str(r["quantity"])) for r in rows)
    fixed = tuple(tuple(str(r.get(k, "")) for k in ("uid", "sku", "externalid", "title", "price", "priceold"))
                  for r in rows)
    return prod["title"], fixed, quantities


def send(title):
    offer = ET.fromstring(tc.build_offers_xml([{"id":SKU, "title":title, "price":None, "qty":TARGET}]))
    offer.find("ПакетПредложений").set("СодержитТолькоИзменения", "true")
    records = offer.findall(".//Предложение")
    require(len(records) == 1 and records[0].findtext("Ид") == SKU and records[0].findtext("Артикул") == SKU
            and records[0].findtext("Количество") == str(TARGET) and offer.find(".//Цена") is None,
            "Unexpected scoped Tilda payload")
    imp = ET.Element("КоммерческаяИнформация", offer.attrib)
    cat = ET.SubElement(imp, "Каталог", {"СодержитТолькоИзменения":"true"})
    ET.SubElement(cat, "Ид").text = tc.CATALOG_ID
    ET.SubElement(cat, "ИдКлассификатора").text = tc.CLASSIFIER_ID
    item = ET.SubElement(ET.SubElement(cat, "Товары"), "Товар")
    for key, value in (("Ид", SKU), ("Артикул", SKU), ("Наименование", title)):
        ET.SubElement(item, key).text = value
    def xml(doc):
        return ET.tostring(doc, encoding="utf-8", xml_declaration=True).decode()
    progress = tc.push(os.environ["TILDA_COMMERCEML_URL"], os.environ["TILDA_COMMERCEML_USER"],
                       os.environ["TILDA_COMMERCEML_PASSWORD"], xml(imp), xml(offer), timeout=70)
    require(any(s.startswith("import offers0_1.xml: success") for s in progress), "No Tilda offer acknowledgement")


def run(target):
    require(target == TARGET, "Unapproved target")
    source_count()
    title, fixed, before = current()
    expected = (Decimal(TARGET),) * 2
    require(before in ((Decimal(4), Decimal(4)), expected), "Unexpected Tilda stock before publishing")
    if before != expected:
        source_count()
        send(title)
    for attempt in range(12):
        _, after_fixed, quantities = current()
        require(after_fixed == fixed, "Tilda price or product identity changed")
        if quantities == expected:
            return {"sku":SKU, "uid":UID, "before":int(before[0]), "public_quantity":TARGET,
                    "edition_quantity":TARGET, "unchanged_price":True}
        if attempt < 11:
            time.sleep(8)
    raise RuntimeError("Tilda storefront quantity awaiting confirmation")
