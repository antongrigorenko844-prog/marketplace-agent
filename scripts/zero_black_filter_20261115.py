"""Zero the explicitly requested black filter housing; never modify prices."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from openpyxl import load_workbook
from config import config
import remove_product
import wb_client as wb
import yandex_market_client as ym

SKU = "0BH325183B"
OUT = Path("reports/zero-black-filter-20261115")
OUT.mkdir(parents=True, exist_ok=True)


def save(name, value):
    (OUT / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2))
    print(name, json.dumps(value, ensure_ascii=False)[:1800], flush=True)


def master_cells():
    book = load_workbook("data/master_control.xlsx")
    return {(s.title, c.coordinate): c.value for s in book for row in s for c in row if c.value is not None}


def wb_stock(warehouse, barcodes):
    return wb._request("POST", config.wb_marketplace_base, f"/api/v3/stocks/{warehouse}", {"skus": barcodes})


def market_stock():
    _, campaign = ym._creds()
    code, data = ym._request("POST", f"/v2/campaigns/{campaign}/offers/stocks", json_body={"offerIds": [SKU]})
    assert code == 200, (code, data)
    return data


def market_offer():
    code, data = ym._request("POST", f"/v2/businesses/{ym.get_business_id()}/offer-mappings", json_body={"offerIds": [SKU]})
    assert code == 200, (code, data)
    rows = data.get("result", {}).get("offerMappings", [])
    assert len(rows) == 1 and rows[0]["offer"]["offerId"] == SKU
    return rows


def main():
    cards = wb.list_cards()
    target = [c for c in cards if c["vendorCode"] == SKU]
    assert len(target) == 1 and target[0]["nmID"] == 670925512, "Unexpected WB product"
    barcodes = [b for s in target[0]["sizes"] for b in s["skus"]]
    assert barcodes
    warehouses = wb.get_warehouses()
    warehouses = [w for w in warehouses if not w.get("isDeleting")]
    save("wb-before", [{"warehouse": w["id"], "stocks": wb_stock(w["id"], barcodes)} for w in warehouses])
    save("market-before", market_stock())
    prices_before = [p for p in wb.get_prices() if p.get("nmID") == 670925512]
    save("wb-price-before", prices_before)
    offers_before = market_offer()
    save("market-offers-before", offers_before)

    # Persist only the requested cell using the repository's existing stock command.
    before = master_cells()
    found, missing = remove_product.zero_stock([SKU], dry_run=False)
    assert len(found) == 1 and not missing, (found, missing)
    after = master_cells()
    changed = [k for k in set(before) | set(after) if before.get(k) != after.get(k)]
    assert all(k[0] == "Товары" and k[1] == "J29" and after[k] == 0 for k in changed), changed
    save("master-change", {"sku": SKU, "previous": found[0][1], "stock": 0, "cells": changed})
    subprocess.run(["git", "config", "user.name", "github-actions[bot]"], check=True)
    subprocess.run(["git", "config", "user.email", "41898282+github-actions[bot]@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", "data/master_control.xlsx"], check=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode:
        subprocess.run(["git", "commit", "-m", "Set black filter housing 0BH325183B stock to zero [skip ci]"], check=True)
        subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)

    for w in warehouses:
        result = wb.update_stocks(str(w["id"]), [{"sku": b, "amount": 0} for b in barcodes])
        save("wb-update-" + str(w["id"]), result)
    count, errors = ym.update_stocks({SKU: 0})
    save("market-update", {"count": count, "errors": errors})
    assert count == 1 and not errors
    for attempt in range(12):
        values = [{"warehouse": w["id"], "stocks": wb_stock(w["id"], barcodes)} for w in warehouses]
        save("wb-after", values)
        if all("stocks" in v["stocks"] and not any(r.get("amount", 0) for r in v["stocks"]["stocks"]) for v in values):
            break
        time.sleep(5)
    else:
        raise RuntimeError("WB zero stock not yet confirmed")
    for attempt in range(12):
        current = market_stock()
        save("market-after", current)
        rows = [o for w in current.get("result", {}).get("warehouses", []) for o in w.get("offers", []) if o.get("offerId") == SKU]
        if rows and all(not s.get("count", 0) for o in rows for s in o.get("stocks", []) if s.get("type") in ("FIT", "AVAILABLE")):
            break
        time.sleep(5)
    else:
        raise RuntimeError("Market zero stock not yet confirmed")
    prices_after = [p for p in wb.get_prices() if p.get("nmID") == 670925512]
    assert prices_before == prices_after, "WB price changed concurrently"
    save("wb-price-unchanged", True)
    offers_after = market_offer()
    save("market-offers-after", offers_after)
    assert {o["offer"]["offerId"]: o["offer"].get("basicPrice") for o in offers_before} == {o["offer"]["offerId"]: o["offer"].get("basicPrice") for o in offers_after}, "Market price changed concurrently"
    save("market-price-unchanged", True)

    save("complete", {"sku": SKU, "stock": 0})


if __name__ == "__main__":
    main()
