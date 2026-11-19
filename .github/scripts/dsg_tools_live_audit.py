"""Read-only live audit of the two approved DSG tool products.

Run from the marketplace-agent repository root. This script changes no seller
data and writes only data/dsg_tools_live_audit_20261120.json. Product-list POSTs
are reads; no import, update, price, stock, media or publication method is used.
"""
from __future__ import annotations

import concurrent.futures
import datetime as dt
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path.cwd()))
logging.disable(logging.CRITICAL)

SKUS = ("tool-dq200-acc-restore", "tool-dsg-pressure-hp")
OUT = Path("data/dsg_tools_live_audit_20261120.json")
UA = {"User-Agent": "Mozilla/5.0"}
HTTP = []


def guard_http():
    """Fail closed if a reused client attempts anything except these reads."""
    original = requests.sessions.Session.request

    def readonly(self, method, url, **kwargs):
        parsed, method = urlsplit(url), method.upper()
        host, path = parsed.hostname, parsed.path
        allowed = False
        if host == "api-seller.ozon.ru":
            allowed = method == "POST" and path in {"/v4/product/info/attributes", "/v3/product/info/list", "/v2/product/info/list"}
        elif host == "content-api.wildberries.ru":
            allowed = method == "POST" and path == "/content/v2/get/cards/list"
        elif host == "discounts-prices-api.wildberries.ru":
            allowed = method == "GET" and path == "/api/v2/list/goods/filter"
        elif host == "api.partner.market.yandex.ru":
            allowed = (method == "GET" and bool(re.fullmatch(r"/v2/campaigns/\d+", path))) or (method == "POST" and (path == "/v2/categories/tree" or bool(re.fullmatch(r"/v2/businesses/\d+/(offer-mappings|offer-cards)", path))))
            if method == "POST" and "/businesses/" in path:
                body = kwargs.get("json") or {}
                allowed = allowed and set(body) == {"offerIds"} and set(body["offerIds"]) == set(SKUS)
        elif host == "api.avito.ru":
            allowed = (method == "POST" and path == "/token") or (method == "GET" and path == "/core/v1/items")
        elif host == "leoparts-akpp.ru":
            allowed = method == "GET" and path in {"/", "/catalog"}
        elif host == "store.tildaapi.com":
            allowed = method == "GET" and path == "/api/getproductslist/"
        if parsed.scheme != "https" or parsed.username or parsed.password or not allowed:
            raise RuntimeError("Request outside the read-only audit allowlist")
        kwargs["allow_redirects"] = False
        kwargs["timeout"] = 30
        event = {"method": method, "host": host, "path": re.sub(r"/(businesses|campaigns)/\d+", r"/\1/{id}", path)}
        HTTP.append(event)
        try:
            response = original(self, method, url, **kwargs)
            event["http_status"] = response.status_code
            return response
        except Exception as exc:
            event["error_type"] = type(exc).__name__
            raise

    requests.sessions.Session.request = readonly


def safe_error(exc):
    message = str(exc)
    for name, value in os.environ.items():
        if any(word in name for word in ("TOKEN", "SECRET", "PASSWORD", "API_KEY", "COMMERCEML_URL")) and len(value) >= 5:
            message = message.replace(value, "[redacted]")
    return {"type": type(exc).__name__, "message": message[:1600]}


def select(obj, keys):
    return {key: obj[key] for key in keys if key in obj}


def match_reason(sku, title):
    if str(sku or "").strip().casefold() in SKUS:
        return "exact_sku"
    text = str(title or "").casefold().replace("ё", "е")
    if "全套拆装充气" in text or "高压测压" in text:
        return "supplier_variant_title_candidate"
    tool = any(w in text for w in ("набор", "инструмент", "оснастк", "приспособ", "тестер", "манометр", "комплект", "test kit", "tool"))
    accumulator = "гидроаккум" in text or "accumulator" in text
    restoration = any(w in text for w in ("восстанов", "заправ", "азот", "заряд", "сняти", "установ", "разбор", "nitrogen", "charg"))
    pressure = any(w in text for w in ("давлен", "манометр", "pressure", "опрессов", "герметич"))
    dsg = any(w in text for w in ("dsg", "дсг", "dq200", "0am", "мехатрон", "mechatron"))
    if tool and accumulator and restoration:
        return "accumulator_tool_title_candidate"
    if tool and pressure and dsg:
        return "dsg_pressure_tool_title_candidate"
    return None


def wrap(call):
    try:
        data = call()
        incomplete = any(data.get(key) for key in ("read_errors", "detail_read_errors", "price_read_error", "category_read_error"))
        incomplete = incomplete or any(isinstance(data.get(key), dict) and data[key].get("read_error") for key in ("offer-mappings", "offer-cards"))
        return {"status": "partial" if incomplete else "ok", **data}
    except Exception as exc:
        return {"status": "error", "error": safe_error(exc)}


def audit_ozon():
    import ozon_client as oz
    all_named, cursor, seen = [], "", set()
    for _ in range(20):
        page = oz._post("/v4/product/info/attributes", {"filter": {"visibility": "ALL"}, "last_id": cursor, "limit": 1000, "sort_dir": "ASC"})
        rows = page.get("result") or []
        all_named.extend(rows)
        next_cursor = page.get("last_id") or ""
        if len(rows) < 1000 or not next_cursor:
            break
        if next_cursor in seen:
            raise RuntimeError("Ozon pagination did not advance")
        seen.add(next_cursor)
        cursor = next_cursor
    else:
        raise RuntimeError("Ozon catalog exceeds bounded pagination")
    named = []
    for row in all_named:
        reason = match_reason(row.get("offer_id"), row.get("name") or row.get("title"))
        if reason:
            item = select(row, ("offer_id", "id", "product_id", "name", "title", "description_category_id", "type_id", "category_id", "height", "width", "depth", "dimension_unit", "weight", "weight_unit", "images", "primary_image"))
            item["match_reason"] = reason
            named.append(item)
    identifiers = list(dict.fromkeys(list(SKUS) + [str(p["offer_id"]) for p in named if p.get("offer_id")]))
    info = oz.get_product_info_list(identifiers)
    details, errors = [], []
    for row in info:
        if row.get("_error"):
            errors.append(safe_error(RuntimeError(row["_error"])))
        elif str(row.get("offer_id")) in identifiers:
            details.append(select(row, ("id", "offer_id", "sku", "price", "old_price", "min_price", "currency_code", "statuses", "status", "errors", "stocks", "images", "primary_image", "description_category_id", "type_id", "is_archived", "is_autoarchived")))
    found = {str(p.get("offer_id")) for p in named + details}
    return {"catalog_titles_checked": len(all_named), "exact_skus_found": [s for s in SKUS if s in found], "title_candidates": named, "product_details": details, "detail_read_errors": errors}


def audit_wildberries():
    import wb_client as wb
    all_cards = wb.list_cards()
    cards = []
    for row in all_cards:
        reason = match_reason(row.get("vendorCode"), row.get("title"))
        if reason:
            item = select(row, ("nmID", "imtID", "nmUUID", "vendorCode", "title", "brand", "subjectID", "subjectName", "dimensions", "photos", "createdAt", "updatedAt"))
            item["match_reason"] = reason
            cards.append(item)
    ids = {str(p.get("nmID")) for p in cards}
    prices = []
    price_error = None
    if ids:
        try:
            for row in wb.get_prices():
                if str(row.get("nmID")) in ids:
                    prices.append(select(row, ("nmID", "vendorCode", "discount", "clubDiscount", "currencyIsoCode4217", "sizes")))
        except Exception as exc:
            price_error = safe_error(exc)
    found = {str(p.get("vendorCode")) for p in cards}
    return {"catalog_titles_checked": len(all_cards), "exact_skus_found": [s for s in SKUS if s in found], "title_candidates": cards, "prices": prices, "price_read_error": price_error, "scope_note": "Content catalog scan; deleted-card bin is not queried."}


def market_ok(response):
    status, body = response
    if status != 200 or body.get("status") not in (None, "OK"):
        raise RuntimeError("Market read HTTP %s: %s" % (status, json.dumps(body, ensure_ascii=False)[:1200]))
    return body


def category_candidates(node, parents=()):
    if isinstance(node, list):
        return [item for child in node for item in category_candidates(child, parents)]
    if not isinstance(node, dict):
        return []
    name = str(node.get("name") or "")
    path = parents + ((name,) if name else ())
    children = node.get("children") or node.get("categories") or []
    if children:
        return [item for child in children for item in category_candidates(child, path)]
    text = " / ".join(path).casefold()
    relevant = any(w in text for w in ("манометр", "пневмотест", "опрессов", "тестер давления")) or (any(w in text for w in ("авто", "транспорт")) and any(w in text for w in ("диагност", "специнструмент", "пневмоинструмент", "набор инструмент")))
    if relevant:
        return [{"id": node.get("id"), "name": name, "path": " / ".join(path)}]
    return []


def audit_yandex_market():
    import yandex_market_client as ym
    business_id = ym.get_business_id()
    result = {}
    for kind in ("offer-mappings", "offer-cards"):
        try:
            body = market_ok(ym._request("POST", f"/v2/businesses/{business_id}/{kind}", json_body={"offerIds": list(SKUS)}))
            result[kind] = body.get("result", {})
        except Exception as exc:
            result[kind] = {"read_error": safe_error(exc)}
    try:
        tree = market_ok(ym._request("POST", "/v2/categories/tree", json_body={})).get("result", {})
        candidates = category_candidates(tree)
        result["category_candidates"] = candidates[:150]
        result["category_candidates_total"] = len(candidates)
        result["category_candidates_omitted"] = max(0, len(candidates) - 150)
    except Exception as exc:
        result["category_read_error"] = safe_error(exc)
    return result


def audit_avito():
    import avito_client as av
    all_items = av.list_all_items()
    candidates = []
    for row in all_items:
        reason = match_reason(row.get("offer_id") or row.get("external_id") or row.get("id"), row.get("title"))
        if reason:
            item = select(row, ("id", "title", "price", "status", "url", "category"))
            item["match_reason"] = reason
            candidates.append(item)
    return {"catalog_titles_checked": len(all_items), "title_candidates": candidates, "scope_note": "Seller item listing, no customer/order data or mutations. A title candidate is not a confirmed identity."}


def audit_tilda():
    import site_links
    parts, recids = set(), set()
    for path in ("/catalog", "/"):
        response = requests.get("https://leoparts-akpp.ru" + path, headers=UA, timeout=35)
        if response.ok:
            p, r = site_links._store_parts(response.text)
            parts |= p
            recids |= r
    if not parts:
        # Existing observed public catalog IDs from repository site workflows.
        parts, recids = {"419144347374"}, {"1928468643"}
    products, errors = {}, []
    for part in sorted(parts):
        slice_no = 1
        for _ in range(50):
            try:
                response = requests.get("https://store.tildaapi.com/api/getproductslist/", headers=UA, timeout=35, params={"storepartuid": part, "recid": next(iter(sorted(recids)), ""), "c": str(int(time.time() * 1000)), "getparts": "true", "getoptions": "true", "slice": slice_no, "size": 100})
                response.raise_for_status()
                data = response.json()
                rows = data.get("products") or []
                for row in rows:
                    products[str(row.get("uid") or row.get("sku"))] = row
                if not rows or not data.get("nextslice"):
                    break
                next_slice = int(data["nextslice"])
                if next_slice <= slice_no:
                    raise RuntimeError("Tilda pagination did not advance")
                slice_no = next_slice
            except Exception as exc:
                errors.append({"storepartuid": part, "error": safe_error(exc)})
                break
    candidates = []
    for row in products.values():
        reason = match_reason(row.get("sku"), row.get("title"))
        if reason:
            item = select(row, ("uid", "sku", "externalid", "external_id", "title", "price", "priceold", "quantity", "inv", "url", "gallery", "partuids", "pack_m", "pack_x", "pack_y", "pack_z"))
            item["match_reason"] = reason
            candidates.append(item)
    found = {str(p.get("sku")) for p in candidates}
    return {"public_blocks_checked": len(parts), "catalog_titles_checked": len(products), "exact_skus_found": [s for s in SKUS if s in found], "title_candidates": candidates, "read_errors": errors, "scope_note": "Public catalog blocks only; absence does not prove absence from the private Tilda product catalog."}


def main():
    guard_http()
    jobs = {"ozon": audit_ozon, "wildberries": audit_wildberries, "yandex_market": audit_yandex_market, "avito": audit_avito, "tilda": audit_tilda}
    report = {"observed_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "scope_skus": list(SKUS), "external_mutations": 0, "expected_prices_rub": {SKUS[0]: {"marketplaces": 38800, "site_avito": 24800}, SKUS[1]: {"marketplaces": 28800, "site_avito": 18800}}, "channels": {}}
    if "--retry-failed" in sys.argv and OUT.exists():
        previous = json.loads(OUT.read_text())
        if previous.get("scope_skus") != list(SKUS):
            raise RuntimeError("Prior report scope differs")
        for name, result in previous.get("channels", {}).items():
            if result.get("status") == "ok":
                report["channels"][name] = result
                result["observed_at_utc"] = result.get("observed_at_utc") or previous["observed_at_utc"]
                jobs.pop(name, None)
        report["retry_of_observed_at_utc"] = previous["observed_at_utc"]
    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
        pending = {pool.submit(wrap, fn): name for name, fn in jobs.items()}
        for future in concurrent.futures.as_completed(pending):
            name = pending[future]
            report["channels"][name] = future.result()
            report["channels"][name]["observed_at_utc"] = report["observed_at_utc"]
            print(name + ": " + report["channels"][name]["status"], flush=True)
    report["read_requests"] = HTTP
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(str(OUT), flush=True)
    return 0 if all(v["status"] == "ok" for v in report["channels"].values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())

