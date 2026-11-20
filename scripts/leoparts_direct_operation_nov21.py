"""Approved price-only updates, one channel per invocation. Run from repo root."""
import argparse
import hashlib
import json
import os
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import requests

sys.path.insert(0, str(Path.cwd()))


@dataclass(frozen=True)
class Target:
    uid: str
    avito_id: int
    old: int
    new: int
    title_terms: tuple


TARGETS = {
    "02E 305 045": Target("878265016983", 8386056623, 2350, 1450, ("корпус", "+", "фильтр")),
    "02e305045": Target("372031303103", 8416684544, 1850, 950, ("корпус", "масляного", "фильтра")),
    "0am325025u": Target("646625764263", 8276593810, 2850, 2350, ("шток",)),
    "0am325025ae": Target("594252601863", 8231885696, 2850, 2350, ("крышк", "прокладк")),
    "0AM325219C": Target("753647197813", 8374903791, 2350, 1850, ("крышк",)),
    "0am325025P": Target("483010641863", 8274232784, 950, 450, ("клипс", "шток")),
    "filtr-setka-0b5-orig": Target("676463915103", 8395219250, 1350, 950, ("фильтр", "сетк")),
}


# Two ribbed housings have distinct catalog cards. Some historical Avito ads have
# no live feed key: protect their numeric IDs without guessing their color or SKU.
RIBBED_SITE = {
    "548717003313": "0BH325183", "261596318053": "0BH325183 B",
    "852450678823": "DQ500", "256312245563": "Dq500",
    "678325991893": "korpus-filtra-0bh325159", "333082224463": "0BH325",
}
RIBBED_AVITO = {"8387624443", "8374830282", "8387244563", "8344886348", "8386890410",
                "8416432130", "4348814868", "4285324155"}
PLAIN_HOUSINGS = {"02E 305 045", "02e305045"}


def ribbed_title(title):
    lower = str(title).lower().replace("ё", "е")
    return any(term in lower for term in ("ребрист", "ребрами", "оребрен"))


def ribbed_site_baseline(rows):
    require(not ({v.uid for v in TARGETS.values()} & set(RIBBED_SITE)), "Forbidden ribbed Tilda identity")
    snapshots = {}
    for uid, sku in RIBBED_SITE.items():
        row = rows.get(uid) or {}
        require(row.get("sku") == sku, "Missing protected Tilda identity: " + sku)
        snapshots[uid] = {"sku": sku, "price": str(row["price"]),
                          "editions": [str(ed["price"]) for ed in editions_of(row)]}
    return snapshots


def ribbed_avito_baseline(rows):
    protected = RIBBED_AVITO | {i for i, row in rows.items() if ribbed_title(row.get("title"))}
    require(not ({str(v.avito_id) for v in TARGETS.values()} & protected), "Forbidden ribbed Avito identity")
    return {i: {"price": str(rows[i]["price"]), "status": rows[i].get("status")}
            if i in rows else {"status": "absent_from_seller_list"} for i in sorted(protected)}


class CheckError(Exception):
    pass


def require(condition, message):
    if not condition:
        raise CheckError(message)


def amount(value):
    return Decimal(str(value))


def check_price(sku, value):
    expected = TARGETS[sku]
    require(amount(value) in (expected.old, expected.new), f"Unexpected current price for {sku}")


def check_title(sku, title):
    if sku in PLAIN_HOUSINGS:
        require(not ribbed_title(title), f"Ribbed housing excluded: {sku}")
        if sku == "02e305045":
            require("+" not in str(title), f"Unexpected filter kit: {sku}")
    require(all(word in str(title).lower() for word in TARGETS[sku].title_terms), f"Product identity changed: {sku}")


def site_records():
    import site_links
    headers = {"User-Agent": "Mozilla/5.0 LEOPARTS approved price verification"}
    parts, recs = set(), set()
    for path in ("/catalog", "/"):
        response = requests.get("https://leoparts-akpp.ru" + path, headers=headers, timeout=35)
        if response.ok:
            a, b = site_links._store_parts(response.text)
            parts.update(a)
            recs.update(b)
    require(parts, "No public Tilda store blocks")
    records = {}
    for part in sorted(parts):
        page = 1
        while True:
            params = {"storepartuid": part, "recid": next(iter(sorted(recs)), ""), "c": str(time.time_ns()),
                      "getparts": "true", "getoptions": "true", "slice": page, "size": 100}
            response = requests.get("https://store.tildaapi.com/api/getproductslist/", params=params, headers=headers, timeout=35)
            response.raise_for_status()
            payload = response.json()
            for product in payload["products"]:
                if product.get("uid"):
                    records[str(product["uid"])] = product
            next_page = payload.get("nextslice")
            if not next_page or not payload["products"]:
                break
            require(page < int(next_page) <= 100, "Tilda pagination was not monotonic")
            page = int(next_page)
    return records


def editions_of(row):
    editions = row.get("editions") or []
    require(isinstance(editions, list), "Unexpected Tilda edition format")
    return editions


def validate_site(records):
    for sku, t in TARGETS.items():
        # Exact IDs are authoritative even if lower-case spellings exist on other channels.
        matched = [uid for uid, row in records.items() if row.get("sku") == sku]
        require(matched == [t.uid], f"Unexpected SKU/UID pair for {sku}")
        external = [u for u, r in records.items() if r.get("externalid", r.get("external_id")) == sku]
        require(external == [t.uid], f"Ambiguous Tilda exchange ID for {sku}")
        row = records[t.uid]
        require(row.get("externalid", row.get("external_id")) == sku, f"Wrong Tilda external ID for {sku}")
        check_title(sku, row["title"])
        check_price(sku, row["price"])
        editions = editions_of(row)
        require(len(editions) <= 1, f"Distinct product variants need separate pricing: {sku}")
        for ed in editions:
            require(str(ed.get("uid")) == t.uid and ed.get("sku") == sku, f"Distinct edition identity for {sku}")
            check_price(sku, ed["price"])


def site_target_prices(records):
    return {sku: [str(records[t.uid]["price"])] + [str(ed["price"]) for ed in editions_of(records[t.uid])]
            for sku, t in TARGETS.items()}


def site_projection(records):
    # The public endpoint may refresh request metadata. Compare catalog fields, stock, public
    # URLs, edition identities and all prices except the exact authorized parent/default edition.
    fields = ("uid", "sku", "externalid", "external_id", "title", "descr", "text", "gallery", "quantity", "inv",
              "partuids", "characteristics", "json_options", "pack_label", "pack_m", "pack_x", "pack_y", "pack_z",
              "unit", "url", "priceold", "brand", "tabs", "mark")
    out = {}
    target_uids = {t.uid for t in TARGETS.values()}
    for uid, row in records.items():
        stable = {f: row.get(f) for f in fields}
        stable["editions"] = [dict(ed) for ed in editions_of(row)]
        if uid in target_uids:
            for edition in stable["editions"]:
                edition.pop("price", None)
        else:
            stable["price"] = row.get("price")
        out[uid] = hashlib.sha256(json.dumps(stable, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    return out


def xml_payloads(skus, records):
    import tilda_commerceml as tc
    args = {"ВерсияСхемы": "2.05", "ДатаФормирования": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
    def child(parent, tag, txt):
        ET.SubElement(parent, tag).text = str(txt)
    imp, offers = ET.Element("КоммерческаяИнформация", args), ET.Element("КоммерческаяИнформация", args)
    catalog = ET.SubElement(imp, "Каталог", {"СодержитТолькоИзменения": "true"})
    child(catalog, "Ид", tc.CATALOG_ID)
    child(catalog, "ИдКлассификатора", tc.CLASSIFIER_ID)
    products = ET.SubElement(catalog, "Товары")
    pack = ET.SubElement(offers, "ПакетПредложений", {"СодержитТолькоИзменения": "true"})
    child(pack, "Ид", tc.CATALOG_ID + "#offers")
    child(pack, "ИдКаталога", tc.CATALOG_ID)
    child(pack, "ИдКлассификатора", tc.CLASSIFIER_ID)
    kind = ET.SubElement(ET.SubElement(pack, "ТипыЦен"), "ТипЦены")
    child(kind, "Ид", tc.PRICE_TYPE_ID)
    child(kind, "Наименование", "Розничная")
    entries = ET.SubElement(pack, "Предложения")
    for sku in skus:
        t = TARGETS[sku]
        product = ET.SubElement(products, "Товар")
        entry = ET.SubElement(entries, "Предложение")
        for node in (product, entry):
            child(node, "Ид", sku)
            child(node, "Артикул", sku)
            child(node, "Наименование", records[t.uid]["title"])
        price = ET.SubElement(ET.SubElement(entry, "Цены"), "Цена")
        for tag, value in (("ИдТипаЦены", tc.PRICE_TYPE_ID), ("ЦенаЗаЕдиницу", t.new), ("Валюта", "RUB"), ("Единица", "шт"), ("Коэффициент", 1)):
            child(price, tag, value)
    return tuple(ET.tostring(root, encoding="utf-8", xml_declaration=True).decode() for root in (imp, offers))


def tilda(mode, report):
    import tilda_commerceml as tc
    before = site_records()
    report["protected_before"] = ribbed_site_baseline(before)
    validate_site(before)
    baseline = site_projection(before)
    report["before"] = site_target_prices(before)
    pending = [s for s, prices in report["before"].items() if any(amount(p) != TARGETS[s].new for p in prices)]
    report["pending"] = pending
    if mode != "apply" or not pending:
        return mode == "inspect" or not pending
    imp, offers = xml_payloads(pending, before)
    report["sent_skus"] = pending
    try:
        # Both XML documents are change-only, include no quantity fields, and contain only pending items.
        tc.push(os.environ["TILDA_COMMERCEML_URL"], os.environ["TILDA_COMMERCEML_USER"],
                os.environ["TILDA_COMMERCEML_PASSWORD"], imp, offers, timeout=60)
    except Exception as exc:
        report["exchange_error_type"] = type(exc).__name__
    for attempt in range(8):
        after = site_records()
        report["protected_after"] = ribbed_site_baseline(after)
        validate_site(after)
        report["after"] = site_target_prices(after)
        projected = site_projection(after)
        changed = sorted(uid for uid in set(baseline) | set(projected) if baseline.get(uid) != projected.get(uid))
        report["other_catalog_changes"] = changed
        done = all(all(amount(v) == TARGETS[s].new for v in vals) for s, vals in report["after"].items())
        if done:
            return not changed and report["protected_before"] == report["protected_after"]
        if attempt < 7:
            time.sleep(6)
    return False


def avito_rows():
    import avito_client
    return {str(r["id"]): r for r in avito_client.list_all_items()}


def validate_avito(rows, sku):
    t = TARGETS[sku]
    row = rows.get(str(t.avito_id), {})
    require(str(row.get("id")) == str(t.avito_id) and row.get("status") == "active", f"Avito listing missing/inactive: {sku}")
    check_title(sku, row.get("title", ""))
    check_price(sku, row["price"])
    return amount(row["price"])


def avito(mode, report):
    import avito_client
    # Immutable ad ID, exact vendor's feed ID and actual live title/status must all agree.
    mappings = avito_client.get_autoload_report_items()["items"]
    initial = avito_rows()
    report["protected_before"] = ribbed_avito_baseline(initial)
    report["items"] = {}
    report["all_other_price_changes"] = []
    success = True
    for sku, t in TARGETS.items():
        result = report["items"][sku] = {"id": t.avito_id, "target": t.new}
        try:
            matched = {str(v.get("avito_id")) for v in mappings if v.get("ad_id") == sku}
            require(matched == {str(t.avito_id)}, f"Avito feed/ad identity mismatch: {sku}")
            reverse = {v.get("ad_id") for v in mappings if str(v.get("avito_id")) == str(t.avito_id)}
            require(reverse == {sku}, f"Avito listing has conflicting feed IDs: {sku}")
            previous = validate_avito(initial, sku)
            result["before"] = str(previous)
            if previous == t.new:
                result["status"] = "already_verified"
                continue
            if mode != "apply":
                result["status"] = "pending"
                success = success and mode == "inspect"
                continue
            # Re-read immediately before mutation, e.g. if feed processing completed mid-run.
            current = validate_avito(avito_rows(), sku)
            if current != t.new:
                try:
                    response = requests.post(f"https://api.avito.ru/core/v1/items/{t.avito_id}/update_price",
                                             headers=avito_client._headers(), json={"price": t.new}, timeout=40)
                    result["http_status"] = response.status_code
                    if response.status_code == 429:
                        result["retry_after"] = response.headers.get("Retry-After")
                except requests.RequestException as exc:
                    result["request_error_type"] = type(exc).__name__
            result["status"] = "verification_pending"
            for attempt in range(5):
                actual = validate_avito(avito_rows(), sku)
                result["after"] = str(actual)
                if actual == t.new:
                    result["status"] = "verified"
                    break
                if attempt < 4:
                    time.sleep(5)
            success = success and result["status"] == "verified"
            if result.get("http_status") == 429 and result["status"] != "verified":
                return False  # Caller resumes after Retry-After; re-read prevents duplicate writes.
        except Exception as exc:
            result["error"] = str(exc) if isinstance(exc, CheckError) else type(exc).__name__
            success = False
    if mode == "apply":
        final = avito_rows()
        report["protected_after"] = ribbed_avito_baseline(final)
        target_ids = {str(t.avito_id) for t in TARGETS.values()}
        report["all_other_price_changes"] = sorted(i for i in (set(initial) | set(final)) - target_ids
                                                  if (initial.get(i, {}).get("price"), initial.get(i, {}).get("status"))
                                                  != (final.get(i, {}).get("price"), final.get(i, {}).get("status")))
        report["final"] = {s: str(validate_avito(final, s)) for s in TARGETS}
        identity_ok = all("error" not in r for r in report["items"].values())
        success = identity_ok and report["protected_before"] == report["protected_after"] and all(amount(report["final"][s]) == t.new for s, t in TARGETS.items()) and not report["all_other_price_changes"]
    return success


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("avito", "tilda"), required=True)
    parser.add_argument("--mode", choices=("inspect", "apply", "verify"), default="inspect")
    args = parser.parse_args()
    report = {"channel": args.channel, "mode": args.mode, "checked_at": datetime.now(timezone.utc).isoformat()}
    try:
        ok = (avito if args.channel == "avito" else tilda)(args.mode, report)
    except Exception as exc:
        # requests exceptions and helper exceptions can contain URLs; keep them out of the job artifact.
        report["error"] = str(exc) if isinstance(exc, CheckError) else type(exc).__name__
        ok = False
    report["ok"] = ok
    path = Path("reports/approved-direct-prices") / (args.channel + "-" + args.mode + ".json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
