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
    offers = root.findall(".//Предложение")
    require(len(offers) == 1 and offers[0].findtext("Ид") == SKU and offers[0].findtext("Артикул") == SKU
            and offers[0].findtext("Количество") == "0" and root.find(".//Цена") is None,
            "Unexpected offer payload")
    imp = ET.Element("КоммерческаяИнформация", root.attrib)
    catalog = ET.SubElement(imp, "Каталог", {"СодержитТолькоИзменения": "true"})
    ET.SubElement(catalog, "Ид").text = tc.CATALOG_ID
    ET.SubElement(catalog, "ИдКлассификатора").text = tc.CLASSIFIER_ID
    entry = ET.SubElement(ET.SubElement(catalog, "Товары"), "Товар")
    for tag, value in (("Ид", SKU), ("Артикул", SKU), ("Наименование", title)):
        ET.SubElement(entry, tag).text = value
    # Tilda validates the product descriptor and offer file as one CommerceML session.
    # Supply the same existing title/identity as the proven price-only exchange.
    def xml(doc):
        return ET.tostring(doc, encoding="utf-8", xml_declaration=True).decode()
    progress = tc.push(os.environ["TILDA_COMMERCEML_URL"], os.environ["TILDA_COMMERCEML_USER"],
                       os.environ["TILDA_COMMERCEML_PASSWORD"], xml(imp), xml(root), timeout=70)
    require(any(x.startswith("import offers0_1.xml: success") for x in progress), "Missing offer import confirmation")
    print("Tilda confirmed import of the single availability offer.", flush=True)


def public_ui(product):
    from urllib.parse import urljoin
    from playwright.sync_api import sync_playwright

    canonical = product["url"]
    suffix = canonical.split("/tproduct/", 1)[1]
    base = "https://leoparts-akpp.ru"
    urls = {canonical, base + "/tproduct/" + suffix}
    headers = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36"}
    try:
        sm = requests.get(base + "/sitemap-store.xml", headers=headers, timeout=12)
        if sm.ok:
            for node in ET.fromstring(sm.content).iter():
                loc = node.text or ""
                if node.tag.endswith("loc") and urlsplit(loc).hostname == "leoparts-akpp.ru" and "/tproduct/" + UID + "-" in loc:
                    urls.add(loc)
    except (ET.ParseError, requests.RequestException):
        pass
    print("EXACT public product routes", sorted(urls), flush=True)

    js = """(uid) => {
        const vis=e=>e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden';
        const norm=s=>String(s||'').replace(/\\s+/g,' ').trim();
        const title=(document.title||'').toLowerCase();
        const own=[...document.querySelectorAll('[data-product-gen-uid],[data-product-uid]')].find(e=>[e.getAttribute('data-product-gen-uid'),e.getAttribute('data-product-uid')].includes(uid)&&vis(e));
        const scope=own || document.querySelector('.t-store__product-snippet, .js-store-product.t-store__product-popup, .t-store__product-popup') || document.body;
        const txt=norm(scope.innerText);
        const exact=Boolean(own) || (title.includes('прокладк')&&title.includes('плат')) || (txt.toLowerCase().includes('прокладка электронной платы'));
        const controls=[...scope.querySelectorAll('.js-store-prod-buy-btn,.t-store__prod-popup__btn,a[href="#order"],button')].filter(vis).map(e=>({txt:norm(e.innerText),dis:!!e.disabled||e.getAttribute('aria-disabled')==='true'||/disabled|soldout/i.test(String(e.className)),cls:String(e.className)}));
        const buttons=controls.filter(c=>/корзин|купить|заказать|оформить заказ/i.test(c.txt));
        const sold=/нет в наличии|нет на складе|закончился|закончились|распродан|сообщить о поступлении|временно отсутствует|нет в продаже|out of stock|sold out/i.test(txt);
        return {exact:exact,sold:sold,active:buttons.filter(x=>!x.dis).map(x=>x.txt).slice(0,4),disabled:controls.some(x=>x.dis),
                shownButtons:controls.map(x=>x.txt).filter(Boolean).slice(0,5),title:title.slice(0,100)};
    }"""
    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, executable_path=shutil.which("google-chrome") or shutil.which("chromium"),
                                     args=["--no-sandbox"])
        page = browser.new_page(locale="ru-RU", user_agent=headers["User-Agent"])
        page.route("**/*", lambda route: route.continue_() if route.request.method in ("GET", "HEAD") else route.abort())
        for url in sorted(urls):
            try:
                res = page.goto(url, wait_until="domcontentloaded", timeout=48000)
                status = res.status if res else None
                page.wait_for_timeout(3500)
                evidence = page.evaluate(js, UID)
                print("PUBLIC_ROUTE", urlsplit(url).path, "HTTP", status, evidence, flush=True)
                if status in (404, 410):
                    results.append("not_public")
                elif status == 200 and evidence["exact"] and not evidence["active"] and (evidence["sold"] or evidence["disabled"]):
                    results.append("sold_out")
                elif status == 200 and evidence["exact"] and evidence["active"]:
                    results.append("active")
                else:
                    results.append("inconclusive")
            except Exception as exc:
                print("PUBLIC_ROUTE", urlsplit(url).path, type(exc).__name__, flush=True)
                results.append("inconclusive")
        browser.close()
    if all(x == "not_public" for x in results):
        # A 404 on the neighboring live product would make a generic gateway catch-all inconclusive.
        guard_url = "https://leoparts-akpp.ru/tproduct/431429802703-uplotnenie-elektromotora-gidrobloka-meha"
        guard = requests.get(guard_url, headers=headers, timeout=25)
        print("PUBLIC_CONTROL", guard.status_code, flush=True)
        require(guard.ok, "Public gateway returned missing pages for the control as well")
    require(results and all(x in ("sold_out", "not_public") for x in results),
            "Public offer quantity is zero; some storefront routes could not confirm the purchase controls")
    print("STOREFRONT", results, flush=True)


def verify_only():
    product, neighbor = inspect(site_records())
    q = quantities(product)
    print("READ_ONLY", SKU, UID, q, "editions", len(product.get("editions", [])), "control", neighbor["uid"], flush=True)
    require(zero(q), "Public quantity is not zero")
    public_ui(product)

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
            public_ui(after)
            return
        if attempt < 11:
            time.sleep(8)
    raise RuntimeError("Tilda storefront did not confirm zero availability")


if __name__ == "__main__":
    import sys
    if sys.argv[1:] == ["--verify-only"]:
        verify_only()
    elif not sys.argv[1:]:
        main()
    else:
        raise SystemExit("Unsupported invocation")
