"""LEOPARTS Product Publishing v3 — single, read-only multi-channel coordinator.

This is the first guarded migration step, NOT a seller-account writer.
plan: no HTTP and no credentials; live: batch reads only. A successful create
acknowledgement never means that a marketplace listing is sellable.

No Avito API calls while auto-upload is unpaid. No data/ or prices are written;
the report is an optional caller-chosen artifact, not a Git auto-commit.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import product_intake_v2 as intake

SUPPORTED = ("ozon", "wildberries", "yandex_market", "tilda", "avito")
ACTIVE = ("ozon", "wildberries", "yandex_market", "tilda")


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_batch(batch_id: str) -> dict:
    if not intake.BATCH_PATTERN.fullmatch(batch_id):
        raise intake.GuardError("Unsafe batch ID")
    path = ROOT / "data" / "product_intake_v2" / "requests" / (batch_id + ".json")
    if not path.is_file():
        raise intake.GuardError("Approved batch not found: " + batch_id)
    return intake.validate(intake.read_json(path))


def exact(rows: list[dict], sku: str, field: str) -> list[dict]:
    return [row for row in rows if str(row.get(field) or "").casefold() == sku.casefold()]


def money_matches(actual: Any, expected: int) -> bool:
    try:
        return actual is not None and abs(float(actual) - expected) < .01
    except (TypeError, ValueError):
        return False


def media_report(item: dict, memo: dict[tuple[str, str], bool]) -> dict:
    result = {"approved_count": len(item["media"]), "verified_local": 0,
              "approved_external_unverified": 0, "issues": []}
    for n, media in enumerate(item["media"], start=1):
        source = media["source"]
        if source == "repository":
            signature = media["sha256"]
            cache_key = (media["path"], signature)
            if cache_key not in memo:
                memo[cache_key] = intake.verified_file(
                    intake.safe_local_path(media["path"]), signature)
            if memo[cache_key]:
                result["verified_local"] += 1
            else:
                result["issues"].append(f"image {n}: missing file or SHA256 mismatch")
        elif source == "public_url":
            # The owner approved this URL, but without a fresh hash read it
            # cannot be treated as a verified binary input for a new upload.
            result["approved_external_unverified"] += 1
        else:
            result["issues"].append(f"image {n}: ChatGPT Library bridge is required")
    if result["issues"]:
        result["state"] = "blocked"
    elif result["approved_external_unverified"]:
        result["state"] = "verification_needed_before_upload"
    else:
        result["state"] = "verified"
    return result


def prepared_state(item: dict, channel: str, media: dict) -> dict:
    if channel == "avito":
        return {"state": "disabled", "reason": "Avito auto-upload subscription not paid; no API calls"}
    if media["issues"]:
        return {"state": "needs_media", "issues": media["issues"]}
    if media["approved_external_unverified"]:
        return {"state": "needs_media_verification",
                "reason": "Approved external image needs SHA256 verification before NEW upload"}
    cfg = (item.get("categories") or {}).get(channel) or {}
    if channel == "ozon":
        if not cfg.get("verified") or not cfg.get("description_category_id") or not cfg.get("type_id"):
            return {"state": "needs_category", "reason": "Exact Ozon description_category_id/type_id not approved"}
        if not cfg.get("tnved_code") or not cfg.get("customs_evidence"):
            return {"state": "needs_customs_evidence", "reason": "Exact item TN VED not approved"}
    if channel == "wildberries" and (
            not cfg.get("verified") or not cfg.get("subject_id") or "characteristics" not in cfg):
        return {"state": "needs_category"}
    if channel == "yandex_market" and (not cfg.get("verified") or not cfg.get("category_id")):
        return {"state": "needs_category"}
    return {"state": "ready_for_safe_submission", "mode": "plan_only_no_seller_writes"}


def build_plan(manifest: dict, requested: tuple[str, ...]) -> dict:
    intake.validate(manifest)
    result = {"schema_version": 3, "batch_id": manifest["batch_id"],
              "mode": "plan", "checked_utc": now_utc(), "seller_writes": 0,
              "read_phases": {}, "items": {}}
    memo: dict[str, bool] = {}
    for item in manifest["items"]:
        media = media_report(item, memo)
        selected = [c for c in item.get("channels", list(SUPPORTED)) if c in requested]
        result["items"][item["sku"]] = {
            "name": item["title"], "approved_prices": {c: item["prices"][c] for c in selected},
            "approved_stock": item["stock"], "media": media,
            "channels": {c: prepared_state(item, c, media) for c in selected}}
    return summarize(result)


def verify_ozon(items: list[dict]) -> tuple[dict, int]:
    import ozon_client as oz
    skus = [item["sku"] for item in items]
    # Five API operations for the entire batch, NOT five per SKU.
    catalog = oz.list_products(limit=100)
    infos = oz.get_product_info_list(skus)
    prices = oz.get_prices_and_stocks(skus)
    stocks = oz.get_stocks(skus)
    by_info = {x.get("offer_id"): x for x in infos if not x.get("_error")}
    by_price = {x.get("offer_id"): x for x in prices}
    by_stocks = {x.get("offer_id"): x for x in stocks}
    result: dict[str, dict] = {}
    for item in items:
        sku = item["sku"]
        matches = exact(catalog, sku, "offer_id")
        if len(matches) > 1:
            result[sku] = {"state": "blocked_duplicate", "matches": len(matches)}
            continue
        if not matches:
            result[sku] = {"state": "not_created"}
            continue
        meta = by_info.get(sku) or {}
        statuses = meta.get("statuses") or {}
        p = (by_price.get(sku) or {}).get("price") or {}
        observed_price = p.get("price") if isinstance(p, dict) else p
        stock_rows = (by_stocks.get(sku) or {}).get("stocks") or []
        fbs = sum(int(x.get("present") or 0) for x in stock_rows
                  if isinstance(x, dict) and x.get("type") == "fbs")
        approved = statuses.get("moderate_status") == "approved"
        created = statuses.get("is_created") is True
        matched = money_matches(observed_price, item["prices"]["ozon"])
        selling = (created and approved and matched and fbs > 0
                   and statuses.get("status_name") == "Продаётся")
        if selling:
            state = "selling"
        elif meta.get("errors") or statuses.get("moderate_status") == "declined":
            state = "moderation_rejected"
        elif not matched and observed_price is not None:
            state = "price_mismatch_review"
        elif not created:
            state = "moderating"
        elif fbs <= 0:
            state = "needs_fbs_stock"
        else:
            state = "not_yet_selling"
        result[sku] = {
            "state": state, "product_id": meta.get("id") or meta.get("product_id") or matches[0].get("product_id"),
            "created": created, "moderation": statuses.get("moderate_status"),
            "status_name": statuses.get("status_name"), "price": observed_price,
            "expected_price": item["prices"]["ozon"], "price_verified": matched,
            "fbs_available": fbs, "issues": meta.get("errors") or []}
    return result, 4


def verify_wildberries(items: list[dict]) -> tuple[dict, int]:
    import wb_client as wb
    cards = wb.list_cards(limit=100)
    prices = {int(x["nmID"]): x for x in wb.get_prices(limit=1000) if x.get("nmID") is not None}
    result: dict[str, dict] = {}
    for item in items:
        matches = exact(cards, item["sku"], "vendorCode")
        if len(matches) != 1:
            result[item["sku"]] = {"state": "not_created" if not matches else "blocked_duplicate"}
            continue
        card = matches[0]
        nm_id = int(card["nmID"])
        variants = (prices.get(nm_id) or {}).get("sizes") or []
        price = variants[0].get("discountedPrice") if len(variants) == 1 else None
        actual_photos = len(card.get("photos") or [])
        matches_price = money_matches(price, item["prices"]["wildberries"])
        result[item["sku"]] = {
            "state": "catalog_verified" if matches_price and actual_photos >= len(item["media"]) else "needs_review",
            "nmID": nm_id, "price": price, "expected_price": item["prices"]["wildberries"],
            "price_verified": matches_price, "photo_count": actual_photos,
            "expected_photos": len(item["media"]),
            "note": "Content and price verified; no live WB sale/stock moderation claim"}
    return result, 2


def verify_yandex_market(items: list[dict]) -> tuple[dict, int]:
    import yandex_market_client as ym
    business_id = ym.get_business_id()
    offers = ym.get_all_offers(archived=False)
    wanted = [x["sku"] for x in items]
    http_status, answer = ym._request(
        "POST", f"/v2/businesses/{business_id}/offer-prices",
        json_body={"offerIds": wanted})
    if http_status != 200:
        raise ValueError("Yandex Market offer-prices read failed, HTTP " + str(http_status))
    by_price = {x.get("offerId"): x for x in (answer.get("result") or {}).get("offers") or []}
    result: dict[str, dict] = {}
    for item in items:
        sku = item["sku"]
        matches = exact(offers, sku, "sku")
        if len(matches) != 1:
            result[sku] = {"state": "not_created" if not matches else "blocked_duplicate"}
            continue
        p = (by_price.get(sku) or {}).get("price") or {}
        actual = p.get("value") if isinstance(p, dict) else None
        ok = money_matches(actual, item["prices"]["yandex_market"])
        result[sku] = {"state": "business_catalog_verified" if ok else "needs_review",
                       "price": actual, "expected_price": item["prices"]["yandex_market"],
                       "price_verified": ok,
                       "note": "Business catalog is not proof of an active storefront offer"}
    return result, 3


def verify_tilda(items: list[dict]) -> tuple[dict, int]:
    from scripts.leoparts_direct_operation_nov21 import site_records
    rows = list(site_records().values())
    result: dict[str, dict] = {}
    for item in items:
        matches = exact(rows, item["sku"], "sku")
        if len(matches) != 1:
            result[item["sku"]] = {"state": "not_created" if not matches else "blocked_duplicate"}
            continue
        found = matches[0]
        price = found.get("price")
        verified_price = money_matches(price, item["prices"]["tilda"])
        gallery = found.get("gallery") or []
        try:
            images = len(json.loads(gallery)) if isinstance(gallery, str) else len(gallery)
        except (TypeError, ValueError):
            images = 0
        result[item["sku"]] = {
            "state": "public_catalog_verified" if verified_price and images >= len(item["media"]) else "needs_review",
            "uid": found.get("uid"), "price": price, "price_verified": verified_price,
            "photo_count": images,
            "note": "Public catalog readback; pixel-perfect photo identity not verified"}
    return result, 1


PROBES: dict[str, Callable] = {
    "ozon": verify_ozon,
    "wildberries": verify_wildberries,
    "yandex_market": verify_yandex_market,
    "tilda": verify_tilda,
}


def summarize(report: dict) -> dict:
    states = [s["state"] for row in report["items"].values()
              for s in row["channels"].values()]
    report["summary"] = {
        "sku_count": len(report["items"]), "channel_tasks": len(states),
        "selling_verified": states.count("selling"),
        "catalog_present_other_channels": sum(s in ("catalog_verified", "business_catalog_verified",
                                                     "public_catalog_verified") for s in states),
        "blocked_or_review": sum(s.startswith("needs_") or s.startswith("blocked") or
                                 s in ("price_mismatch_review", "moderation_rejected", "not_yet_selling")
                                 for s in states),
        "disabled": states.count("disabled"),
        "api_errors": states.count("api_error"),
    }
    return report


def run_live(manifest: dict, requested: tuple[str, ...],
             probes: dict[str, Callable] | None = None) -> dict:
    report = build_plan(manifest, requested)
    report["mode"] = "live_read_only"
    eligible = {c: [item for item in manifest["items"] if c in report["items"][item["sku"]]["channels"]]
                for c in ACTIVE if c in requested}
    operations = PROBES if probes is None else probes
    with ThreadPoolExecutor(max_workers=min(4, max(1, len(eligible)))) as executor:
        futures = {executor.submit(operations[c], items): (c, items)
                   for c, items in eligible.items() if items}
        for future in as_completed(futures):
            channel, items = futures[future]
            try:
                observations, queries = future.result()
                report["read_phases"][channel] = queries
                for item in items:
                    sku = item["sku"]
                    report["items"][sku]["channels"][channel] = observations.get(sku, {"state": "not_found"})
            except Exception as exc:
                report["channel_read_counts"][channel] = None
                for item in items:
                    report["items"][item["sku"]]["channels"][channel] = {
                        "state": "api_error", "error_type": type(exc).__name__,
                        "reason": str(exc)[:180]}
    return summarize(report)


def parse_channels(raw: str) -> tuple[str, ...]:
    channels = tuple(x.strip() for x in raw.split(",") if x.strip())
    if not channels or len(channels) != len(set(channels)) or any(c not in SUPPORTED for c in channels):
        raise intake.GuardError("Invalid channel list")
    return channels


def atomic_report(path: Path, report: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(path)
    finally:
        temp.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="LEOPARTS v3: batch plan/live inspection, no seller writes")
    parser.add_argument("--batch", required=True)
    parser.add_argument("--mode", choices=("plan", "live"), default="plan")
    parser.add_argument("--channels", default=",".join(SUPPORTED))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    manifest = load_batch(args.batch)
    requested = parse_channels(args.channels)
    report = (build_plan(manifest, requested) if args.mode == "plan"
              else run_live(manifest, requested))
    if args.output:
        atomic_report(args.output, report)
    else:
        print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
