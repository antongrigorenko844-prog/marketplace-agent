"""Pause public sales of one identified Tilda product, preserving inventory at source.

Only the exchange offer quantity is sent. All product history, pricing and stock
in the inventory system stay in place. Safe to rerun against the same UID.
"""
import os
import re
import shutil
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

import requests

from leoparts_direct_operation_nov21 import site_records
import tilda_commerceml as tc

SKU = "0cw0am"
UID = "338007985583"
PROTECTED = "0cw0amdsgdq200"
PROTECTED_UID = "431429802703"


def require(value, message):
    if not value:
        raise RuntimeError(message)


def rows_for(product):
    return [product] + list(product.get("editions") or [])


def inspect(records):
    matches = []
    for uid, product in records.items():
        for row in rows_for(product):
            ids = [str(row.get(k) or "").strip() for k in ("sku", "externalid", "external_id")]
            if any(x.casefold() == SKU for x in ids):
                matches.append(str(uid))
                break
    require(set(matches) == {UID}, "Unexpected public SKU or edition matches: " + str(matches))
    product = records[UID]
    neighbor = records.get(PROTECTED_UID, {})
    require(neighbor.get("sku") == PROTECTED, "Neighbor identity mismatch")
    require(product.get("sku") == SKU and (product.get("externalid") or product.get("external_id")) == SKU,
            "Target exchange ID is not exactly the public SKU")
    require(all(part in product["title"].lower() for part in ("проклад", "плат")), "Unexpected target title")
    require(all(str(e.get("uid")) == UID and e.get("sku") == SKU for e in product.get("editions", [])),
            "Unexpected independent product edition")
    url = str(product["url"])
    require(urlsplit(url).hostname == "leoparts-akpp.ru" and "/tproduct/" + UID + "-" in url,
            "Unexpected product route")
    return product, neighbor


def quantities(product):
    return [str(row.get("quantity", "")).strip() for row in rows_for(product)]


def zero(values):
    try:
        return bool(values) and all(Decimal(v).is_finite() and Decimal(v) == 0 for v in values)
    except (InvalidOperation, ValueError):
        return False


def fixed(product):
    return [[str(row.get(k, "")) for k in ("uid", "sku", "externalid", "price", "priceold", "title")]
            for row in rows_for(product)]


def pause_offer(title):
    root = ET.fromstring(tc.build_offers_xml([{"id": SKU, "title": title, "price": None, "qty": 0}]))
    pack = root.find("ПакетПредложений")
    pack.set("СодержитТолькоИзменения", "true")
    # No price types are needed for an availability-only exchange.
    pack.remove(pack.find("ТипыЦен"))
    offers = root.findall(".//Предложение")
    require(len(offers) == 1 and offers[0].findtext("Ид") == SKU and offers[0].findtext("Артикул") == SKU
            and offers[0].findtext("Количество") == "0" and root.find(".//Цена") is None,
            "Unexpected offer payload")
    path = os.environ["TILDA_COMMERCEML_URL"]
    with requests.Session() as session:
        session.auth = (os.environ["TILDA_COMMERCEML_USER"], os.environ["TILDA_COMMERCEML_PASSWORD"])
        def get(mode, **kwargs):
            response = session.get(tc._url(path, type="catalog", mode=mode, **kwargs), timeout=60)
            return tc._check(response, "import offers0_1.xml" if mode == "import" else mode)
        lines = get("checkauth").strip().splitlines()
        require(lines and lines[0].strip().lower() == "success", "Exchange authorization failed")
        if len(lines) >= 3 and lines[1].strip() not in session.cookies:
            session.cookies.set(lines[1].strip(), lines[2].strip())
        get("init")
        name = "offers0_1.xml"
        response = session.post(tc._url(path, type="catalog", mode="file", filename=name),
                                data=ET.tostring(root, encoding="utf-8", xml_declaration=True),
                                headers={"Content-Type": "application/octet-stream"}, timeout=60)
        tc._check(response, "file")
        for attempt in range(36):
            state = get("import", filename=name).strip().splitlines()[0].lower()
            if state == "success":
                print("Tilda confirmed import of the single availability offer.", flush=True)
                return
            require(state == "progress", "Unexpected CommerceML import status")
            time.sleep(4)
        raise RuntimeError("Tilda import did not finish within the verification window")


def public_ui(url):
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which("google-chrome") or shutil.which("chromium"), args=["--no-sandbox"])
        page = browser.new_page(locale="ru-RU")
        page.route("**/*", lambda route: route.continue_() if route.request.method in ("GET", "HEAD") else route.abort())
        response = page.goto(url, wait_until="domcontentloaded", timeout=50000)
        require(response is not None and response.ok, "Public card failed to load")
        page.wait_for_timeout(4000)
        data = page.evaluate("""() => {
            const vis = e => e.getClientRects().length > 0 && getComputedStyle(e).visibility !== 'hidden';
            const scope = document.querySelector('.t-store__product-snippet, .js-store-product.t-store__product-popup, .t-store__product-popup') || document.body;
            const controls = Array.from(scope.querySelectorAll('.js-store-prod-buy-btn, .t-store__prod-popup__btn, a[href="#order"], button'))
              .filter(vis).map(e => ({text:e.innerText.trim(), cls:e.className, disabled:e.disabled || e.getAttribute('aria-disabled')==='true' || /disabled|soldout/.test(String(e.className))}));
            const body = scope.innerText.replace(/\\s+/g,' ');
            return {soldout:/нет в наличии|нет на складе|закончился|распродан|сообщить о поступлении|товар закончился/i.test(body), controls};
        }""")
        browser.close()
    active = [x["text"] for x in data["controls"] if not x["disabled"] and re.search(r"в корзину|добавить в корзину|купить|оформить заказ", x["text"], re.I)]
    print("PUBLIC_CARD", response.status, "sold_out_text=", data["soldout"], "active_buy_controls=", active, flush=True)
    require(not active and (data["soldout"] or any(x["disabled"] for x in data["controls"])),
            "Catalog zero confirmed, but public purchase controls were not conclusively disabled")


def main():
    start, guard = inspect(site_records())
    before_fixed = fixed(start)
    guard_before = (fixed(guard), quantities(guard))
    print("TARGET", SKU, UID, start["url"], "quantity before", quantities(start),
          "control", PROTECTED, PROTECTED_UID, flush=True)
    if not zero(quantities(start)):
        pause_offer(start["title"])
    for attempt in range(12):
        after, neighbor = inspect(site_records())
        require(fixed(after) == before_fixed and (fixed(neighbor), quantities(neighbor)) == guard_before,
                "Target price/identity or protected neighbor changed during readback")
        q = quantities(after)
        if zero(q):
            print("VERIFIED", SKU, UID, "public offer and editions quantity", q, "price and protected neighbor unchanged", flush=True)
            public_ui(after["url"])
            return
        if attempt < 11:
            time.sleep(8)
    raise RuntimeError("Tilda storefront did not confirm zero availability")


if __name__ == "__main__":
    main()
