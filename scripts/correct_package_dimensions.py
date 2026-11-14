"""Narrow, manual correction of two owner-confirmed package dimensions.

No price/stock update endpoints; preserve all existing product content and weight.
"""
import copy
import json
import os
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym
import catalog_editor as ce
import master_dims

IDS = ["0am325025H", "0am325025x"]
APPLY = os.environ.get("APPLY_DIMENSIONS") == "true"
OUT = Path("/tmp/package-dimensions-result")
OUT.mkdir(exist_ok=True)

def save(name, data):
    (OUT / (name + ".json")).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def log(*args):
    print(*args, flush=True)

def by(rows, key):
    return {r[key]: r for r in rows if r.get(key) in IDS}

def ypost(path, body):
    code, data = ym._request("POST", path, json_body=body)
    assert code == 200 and data.get("status") == "OK", (code, data)
    return data

def dims(row):
    return [row.get(k) for k in ("depth", "width", "height")]

def import_complex_attributes(attrs):
    # The read API returns flat attributes; import expects grouped objects.
    groups = {}
    nested = []
    for attr in attrs or []:
        if "attributes" in attr:
            nested.append(copy.deepcopy(attr))
        else:
            groups.setdefault(attr.get("complex_id", 0), []).append(copy.deepcopy(attr))
    return nested + [{"attributes": group} for group in groups.values()]

def semantic_attrs(attrs):
    return sorted(json.dumps(a, sort_keys=True, ensure_ascii=False) for a in attrs or [])

def price_signature(rows):
    return {s: {k: v.get("price", {}).get(k) for k in ("price", "old_price", "min_price", "currency_code", "vat")} for s, v in rows.items()}

def ozon():
    before = by(oz.get_product_names(IDS), "offer_id")
    prices = by(oz.get_prices_and_stocks(IDS), "offer_id")
    stocks = oz.get_stocks(IDS)
    save("ozon-before", {"cards": before, "prices": prices, "stocks": stocks})
    assert set(before) == set(IDS) and set(prices) == set(IDS)
    items = []
    for sku in IDS:
        card = before[sku]
        log("OZON BEFORE", sku, dims(card), card.get("weight"), card.get("weight_unit"))
        confirmed = master_dims.for_article(sku)
        assert confirmed["dims_mm"] == [400, 300, 300]
        if dims(card) == [400, 300, 300] and card.get("dimension_unit") == "mm":
            continue
        item = ce._build_one_item(copy.deepcopy(card), prices[sku], {})
        # Do not apply text, attribute, weight or brand overrides from other work.
        for key in ("name", "attributes", "complex_attributes", "weight", "weight_unit", "color_image", "pdf_list"):
            if key in card:
                item[key] = copy.deepcopy(card[key])
        item.update(depth=400, width=300, height=300, dimension_unit="mm")
        item["complex_attributes"] = import_complex_attributes(card.get("complex_attributes"))
        assert float(item["price"]) == float(prices[sku]["price"]["price"])
        assert float(item["old_price"]) == float(prices[sku]["price"]["old_price"])
        assert item["attributes"] == card["attributes"]
        assert item["weight"] == card["weight"]
        items.append(item)
    save("ozon-request", items)
    if APPLY and items:
        res = oz.import_products(items)
        save("ozon-import", res)
        task = res["result"]["task_id"]
        log("OZON TASK", task)
        for attempt in range(24):
            status = oz.get_import_status(task)
            save("ozon-import-status", status)
            log("OZON IMPORT", status)
            rows = status.get("result", {}).get("items", [])
            if rows and all(r.get("status") in ("imported", "failed") for r in rows):
                assert all(r.get("status") == "imported" for r in rows), status
                break
            time.sleep(15)
    after = before
    for attempt in range(24 if APPLY else 1):
        after = by(oz.get_product_names(IDS), "offer_id")
        if all(dims(after[s]) == [400, 300, 300] for s in IDS) or not APPLY:
            break
        time.sleep(15)
    final_prices = by(oz.get_prices_and_stocks(IDS), "offer_id")
    save("ozon-after", {"cards": after, "prices": final_prices, "stocks": oz.get_stocks(IDS), "info": oz.get_product_info_list(IDS)})
    assert price_signature(prices) == price_signature(final_prices), "Ozon prices changed during run; investigate"
    for sku in IDS:
        log("OZON VERIFY", sku, dims(after[sku]), "price unchanged")
        if APPLY:
            assert dims(after[sku]) == [400, 300, 300], (sku, dims(after[sku]))
            for key in ("name", "weight", "description_category_id", "type_id"):
                assert before[sku].get(key) == after[sku].get(key), (sku, key)
            for key in ("attributes", "complex_attributes"):
                assert semantic_attrs(before[sku].get(key)) == semantic_attrs(after[sku].get(key)), (sku, key, "Content changed")
            for key in ("images", "primary_image"):
                assert before[sku].get(key) == after[sku].get(key), (sku, key, "Images changed")

def wildberries():
    before = by(wb.list_cards(), "vendorCode")
    assert set(before) == set(IDS), list(before)
    nmids = {r["nmID"] for r in before.values()}
    prices = [r for r in wb.get_prices() if r.get("nmID") in nmids]
    save("wb-before", {"cards": before, "prices": prices})
    items = []
    for sku, card in before.items():
        d = {k: v for k, v in card.get("dimensions", {}).items() if k != "isValid"}
        log("WB BEFORE", sku, d)
        if [d.get(k) for k in ("length", "width", "height")] == [40, 30, 30]:
            continue
        d.update(length=40, width=30, height=30)
        item = {k: copy.deepcopy(card[k]) for k in ("nmID", "vendorCode", "brand", "title", "description", "characteristics", "sizes", "kizMarked") if k in card}
        item["dimensions"] = d
        items.append(item)
    save("wb-request", items)
    if APPLY and items:
        result = wb.update_cards(items)
        save("wb-update", result)
        log("WB UPDATE", result)
        assert not result.get("error"), result
    after = before
    for attempt in range(20 if APPLY else 1):
        after = by(wb.list_cards(), "vendorCode")
        if all([after[s].get("dimensions", {}).get(k) for k in ("length", "width", "height")] == [40, 30, 30] for s in IDS) or not APPLY:
            break
        time.sleep(20)
    final_prices = [r for r in wb.get_prices() if r.get("nmID") in nmids]
    save("wb-after", {"cards": after, "prices": final_prices})
    assert sorted(prices, key=lambda x: x["nmID"]) == sorted(final_prices, key=lambda x: x["nmID"]), "WB prices changed during run"
    for sku in IDS:
        log("WB VERIFY", sku, after[sku].get("dimensions"), "price unchanged")
        if APPLY:
            assert [after[sku]["dimensions"].get(k) for k in ("length", "width", "height")] == [40, 30, 30]
            assert before[sku]["dimensions"].get("weightBrutto") == after[sku]["dimensions"].get("weightBrutto")
            for k in ("title", "description", "characteristics", "sizes", "subjectID", "kizMarked"):
                assert before[sku].get(k) == after[sku].get(k), (sku, k)

def market():
    biz = ym.get_business_id()
    _, campaign = ym._creds()
    def mappings():
        data = ypost(f"/v2/businesses/{biz}/offer-mappings", {"offerIds": IDS})
        return {r["offer"]["offerId"]: r for r in data["result"]["offerMappings"]}
    def statuses():
        return by(ypost(f"/v2/campaigns/{campaign}/offers", {"offerIds": IDS})["result"]["offers"], "offerId")
    before, old = mappings(), statuses()
    save("market-before", {"mappings": before, "statuses": old})
    assert set(before) == set(IDS)
    items = []
    for sku in IDS:
        d = before[sku]["offer"]["weightDimensions"]
        log("MARKET BEFORE", sku, d)
        if [d.get(k) for k in ("length", "width", "height")] != [40, 30, 30]:
            items.append({"offer": {"offerId": sku, "weightDimensions": {**d, "length": 40, "width": 30, "height": 30}}})
    save("market-request", items)
    if APPLY and items:
        result = ypost(f"/v2/businesses/{biz}/offer-mappings/update", {"offerMappings": items})
        save("market-update", result)
        assert not any(r.get("errors") for r in result.get("results", [])), result
    after = before
    for attempt in range(12 if APPLY else 1):
        after = mappings()
        if all([after[s]["offer"]["weightDimensions"].get(k) for k in ("length", "width", "height")] == [40, 30, 30] for s in IDS) or not APPLY:
            break
        time.sleep(15)
    final = statuses()
    save("market-after", {"mappings": after, "statuses": final})
    for sku in IDS:
        log("MARKET VERIFY", sku, after[sku]["offer"]["weightDimensions"], final[sku].get("status"))
        assert old[sku].get("basicPrice") == final[sku].get("basicPrice")
        assert before[sku]["offer"]["weightDimensions"].get("weight") == after[sku]["offer"]["weightDimensions"].get("weight")
        if APPLY:
            assert [after[sku]["offer"]["weightDimensions"].get(k) for k in ("length", "width", "height")] == [40, 30, 30]

errors = {}
for name, function in (("ozon", ozon), ("wb", wildberries), ("market", market)):
    try:
        function()
    except Exception as exc:
        import traceback
        traceback.print_exc()
        errors[name] = str(exc)
save("summary", {"apply": APPLY, "errors": errors, "targets": IDS})
if errors:
    raise RuntimeError(errors)
log("COMPLETE", "APPLIED" if APPLY else "AUDIT ONLY")
