#!/usr/bin/env python3
"""Build a *draft* DQ200 YML from existing repository catalog data.

Prices and links come from data/site_prices.json and data/site_links.json.
Photos are checked in the repository. No live stock is assumed: all offers
are marked unavailable until a trustworthy stock source is integrated.
"""
import argparse
import datetime as dt
import json
import os
from pathlib import Path
import tempfile
import xml.etree.ElementTree as ET
from openpyxl import load_workbook
from urllib.parse import quote, urlparse

ROOT = Path(__file__).resolve().parents[1]
PRODUCTS = [
    ("0am325025h", "Мехатроник DSG7 DQ200 0AM325025H без электронной платы", "1", "photos/0am325025H/0am325025H-1.png"),
    ("0am325025x", "Мехатроник DSG7 DQ200 0AM325025X без электронной платы", "1", "photos/0am325025x/0am325025x_1.png"),
    ("0am325025hx", "Мехатроник DSG7 DQ200 в сборе с платой D", "2", "photos/0am325025HX/0am325025HX_1.png"),
    ("0am325025hxf", "Мехатроник DSG7 DQ200 в сборе с платой F", "2", "photos/0am325025HXF/0am325025HXF_1.png"),
    ("0am325025hxe", "Мехатроник DSG7 DQ200 в сборе с платой E", "2", "photos/0am325025HXE/0am325025HXE_1.png"),
    ("0am325025hxg", "Мехатроник DSG7 DQ200 в сборе с платой G", "2", "photos/0am325025HXG/0am325025HXG_1.png"),
    ("0am325025hxk", "Мехатроник DSG7 DQ200 в сборе с платой K", "2", "photos/0am325025HXK/0am325025HXK_1.png"),
    ("0am927769d", "Электронная плата мехатроника DSG7 DQ200 0AM927769D", "3", "photos/0AM927769D/0AM927769D_1.png"),
    ("0am927769f", "Электронная плата мехатроника DSG7 DQ200 0AM927769F", "3", "photos/0AM927769F/0AM927769F_1.png"),
    ("0am927769e", "Электронная плата мехатроника DSG7 DQ200 0AM927769E", "3", "photos/0AM927769E/0AM927769E_1.png"),
    ("0am927769g", "Электронная плата мехатроника DSG7 DQ200 0AM927769G", "3", "photos/0AM927769G/0AM927769G_1.png"),
    ("0am927769k", "Электронная плата мехатроника DSG7 DQ200 0AM927769K", "3", "photos/0AM927769K/0AM927769K_1.png"),
]
MECH_AD_TITLE = "Мехатроник DSG7 DQ200"
MECH_AD_TEXT = "Восстановленные мехатроники. Проверены на авто. Гарантия. Доставка по РФ и СНГ"
CATEGORIES = {"1": "Мехатроники DSG7 DQ200", "2": "Мехатроники с электронной платой", "3": "Электронные платы"}
OUTPUT = ROOT / "feeds" / "dq200.yml"


def catalog_stocks():
    """Read master stock column from the same workbook as stock_sync.py."""
    ws = load_workbook(ROOT / "data/ozon_catalog.xlsx", read_only=True, data_only=True).active
    stocks = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] is None:
            continue
        key = str(row[0]).strip().lower()
        # Only DQ200 advertising SKUs matter here; other catalog products may
        # legitimately have case-distinct identifiers (e.g. Dq500 / DQ500).
        if key not in {item[0] for item in PRODUCTS}:
            continue
        if key in stocks:
            raise ValueError(f"Duplicate target DQ200 SKU in stock catalog: {key}")
        qty = row[7] if len(row) > 7 else None
        if qty is None or str(qty).strip() == "":
            stocks[key] = None
        else:
            try:
                n = float(qty)
                if not n.is_integer() or n < 0:
                    raise ValueError()
                stocks[key] = int(n)
            except (TypeError, ValueError):
                raise ValueError(f"Invalid stock for {key}: {qty!r}")
    return stocks


def build():
    stocks = catalog_stocks()
    prices = json.loads((ROOT / "data/site_prices.json").read_text(encoding="utf-8"))
    links = json.loads((ROOT / "data/site_links.json").read_text(encoding="utf-8"))
    root = ET.Element("yml_catalog", date=dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"))
    shop = ET.SubElement(root, "shop")
    for key, val in (("name", "Leoparts"), ("company", "Leoparts"), ("url", "https://leoparts-akpp.ru")):
        ET.SubElement(shop, key).text = val
    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", id="RUR", rate="1")
    categories = ET.SubElement(shop, "categories")
    for cid, title in CATEGORIES.items():
        ET.SubElement(categories, "category", id=cid).text = title
    offers = ET.SubElement(shop, "offers")
    report = []
    for sku, title, category, picture in PRODUCTS:
        if sku not in prices or sku not in links:
            raise ValueError(f"Missing price or URL for {sku}")
        price = prices[sku]
        url = links[sku]
        if not isinstance(price, (int, float)) or price <= 0:
            raise ValueError(f"Invalid price for {sku}: {price!r}")
        if urlparse(url).scheme != "https" or urlparse(url).hostname != "leoparts-akpp.ru":
            raise ValueError(f"Invalid site URL for {sku}: {url}")
        if not (ROOT / picture).is_file():
            raise ValueError(f"Missing product photo: {picture}")
        photo_url = "https://raw.githubusercontent.com/antongrigorenko844-prog/marketplace-agent/main/" + "/".join(quote(x) for x in picture.split("/"))
        report.append({"sku": sku, "catalog_stock": stocks.get(sku), "price_rub": price, "site_url": url, "stock_source": "data/ozon_catalog.xlsx", "availability": "disabled_pending_live_verification"})
        offer = ET.SubElement(offers, "offer", id=sku, available="false")
        for key, val in (("url", url), ("price", str(price)), ("currencyId", "RUR"), ("categoryId", category), ("picture", photo_url), ("name", MECH_AD_TITLE if category in {"1", "2"} else title), ("vendorCode", sku.upper())):
            ET.SubElement(offer, key).text = val
            if key == "name" and category in {"1", "2"}:
                ET.SubElement(offer, "description").text = MECH_AD_TEXT
    if len(offers) != 12:
        raise ValueError("Expected exactly 12 offers")
    ET.indent(root, space="  ")
    candidate = ET.fromstring(ET.tostring(root, encoding="utf-8"))
    for offer in candidate.findall("./shop/offers/offer"):
        stock = stocks.get(offer.get("id"))
        if stock is not None and stock > 0:
            offer.set("available", "true")
            ET.SubElement(offer, "count").text = str(stock)
    ET.indent(candidate, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), report, ET.tostring(candidate, encoding="utf-8", xml_declaration=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload, report, candidate = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, prefix=".dq200-", delete=False) as f:
        f.write(payload)
        temp = f.name
    os.replace(temp, args.output)
    candidate_path = ROOT / "feeds/dq200_candidate.yml"
    candidate_path.write_bytes(candidate)
    print("Created review-only candidate (NOT verified live):", candidate_path)
    report_path = ROOT / "feeds/dq200_stock_audit.json"
    report_path.write_text(json.dumps({"source": "data/ozon_catalog.xlsx", "note": "Snapshot from repository, not verified live stock", "offers": report}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Saved stock snapshot:", report_path)
    for row in report:
        print("STOCK CHECK", row["sku"], row["catalog_stock"])
    print(f"Built 12-offer DRAFT feed: {args.output}. All offers available=false; live stock NOT verified.")


if __name__ == "__main__":
    main()
