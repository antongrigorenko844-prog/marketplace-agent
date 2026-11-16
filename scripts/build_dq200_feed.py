#!/usr/bin/env python3
"""Build a fail-closed DQ200 YML feed from three public Tilda category feeds.

No Tilda, Direct or marketplace writes. Publish only after full validation.
"""
import argparse
import copy
import datetime as dt
import os
import tempfile
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from urllib.parse import urlparse

SOURCES = (
    ("МЕХАТРОНИК БЕЗ ПЛАТЫ", "617915778403", "https://leoparts-akpp.ru/tstore/yml/36e8d13511edcfc87f8041855f3f84e8.yml"),
    ("МЕХАТРОНИК В СБОРЕ С ПЛАТОЙ", "470330856843", "https://leoparts-akpp.ru/tstore/yml/9c2c5fda7ae397a57ed44647ff7e006e.yml"),
    ("ЭЛЕКТРОННЫЕ ПЛАТЫ", "976890247783", "https://leoparts-akpp.ru/tstore/yml/3f059b4a56564a1dfe6a86788c518dbe.yml"),
)
OUT = Path("feeds/dq200.yml")


def download(url):
    req = urllib.request.Request(url, headers={"User-Agent": "LeopartsFeedBot/1.0", "Accept": "application/xml,text/xml,*/*"})
    with urllib.request.urlopen(req, timeout=30) as response:
        raw = response.read(5_000_001)
    if not raw or len(raw) > 5_000_000:
        raise ValueError("Empty or oversized source: " + url)
    return ET.fromstring(raw)


def required(offer, tag):
    value = offer.findtext(tag, default="").strip()
    if not value:
        raise ValueError(f"Offer {offer.get('id')} missing {tag}")
    return value


def build():
    root = ET.Element("yml_catalog", {"date": dt.datetime.now(dt.timezone(dt.timedelta(hours=3))).strftime("%Y-%m-%dT%H:%M:%S%z")})
    shop = ET.SubElement(root, "shop")
    for tag, value in (("name", "Leoparts"), ("company", "leoparts"), ("url", "https://leoparts-akpp.ru"), ("platform", "Tilda Publishing")):
        ET.SubElement(shop, tag).text = value
    currencies = ET.SubElement(shop, "currencies")
    ET.SubElement(currencies, "currency", {"id": "RUR", "rate": "1"})
    categories = ET.SubElement(shop, "categories")
    offers_out = ET.SubElement(shop, "offers")
    seen = set()
    counts = []
    for category_name, _category_id, url in SOURCES:
        source = download(url)
        if source.tag != "yml_catalog":
            raise ValueError("Not a YML catalog: " + url)
        source_shop = source.find("shop")
        if source_shop is None:
            raise ValueError("Missing shop: " + url)
        category = source_shop.find("./categories/category")
        if category is None:
            raise ValueError(f"Missing category in {url}")
        category_id = category.get("id")
        category_name = (category.text or category_name).strip()
        items = source_shop.findall("./offers/offer")
        if not items:
            raise ValueError("Empty category feed: " + url)
        ET.SubElement(categories, "category", {"id": category_id}).text = category_name
        counts.append(len(items))
        for offer in items:
            oid = offer.get("id")
            if not oid or oid in seen:
                raise ValueError("Duplicate or unexpected offer id: " + str(oid))
            seen.add(oid)
            if required(offer, "categoryId") != category_id:
                raise ValueError(f"Wrong categoryId in {oid}")
            for tag in ("name", "picture", "url", "price", "currencyId"):
                required(offer, tag)
            if float(required(offer, "price")) <= 0 or (offer.findtext("count") is not None and int(offer.findtext("count")) < 0):
                raise ValueError(f"Invalid price or stock in {oid}")
            if required(offer, "currencyId") != "RUR":
                raise ValueError(f"Invalid currency in {oid}")
            for tag in ("picture", "url"):
                target = urlparse(required(offer, tag))
                if target.scheme != "https" or not target.netloc:
                    raise ValueError(f"Invalid HTTPS {tag} in {oid}")
            if urlparse(required(offer, "url")).hostname != "leoparts-akpp.ru":
                raise ValueError(f"Unexpected landing domain in {oid}")
            offers_out.append(copy.deepcopy(offer))
    if counts != [2, 5, 5] or len(seen) != 12:
        raise ValueError(f"Product set changed: counts={counts}, ids={sorted(seen)}. Review before updating ad feed.")
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), counts


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUT)
    args = parser.parse_args()
    content, counts = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=args.output.parent, prefix=".dq200-", suffix=".tmp", delete=False) as f:
        f.write(content)
        temp = f.name
    os.replace(temp, args.output)
    print(f"Validated DQ200 feed: {sum(counts)} offers, category counts {counts}; wrote {args.output}")


if __name__ == "__main__":
    main()
