"""LEOPARTS Product Intake v2.

One approved batch is the source of truth for a NEW product launch. Per-channel
operations are independent and idempotent: no duplicate create on retries,
no guessed Ozon TN VED / Avito taxonomy, no photos from the wrong generation,
and never a silent recomputation of owner-approved channel prices.

Default CLI is offline inspect: no seller/API mutations.
"""
from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import sys
import tempfile
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
CHANNELS = ("ozon", "wildberries", "yandex_market", "avito", "tilda")
SKU_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{1,100}$")
BATCH_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,80}$")
SHA_PATTERN = re.compile(r"^[0-9a-f]{64}$")
ALLOWED_MEDIA_HOSTS = {
    "github.com", "raw.githubusercontent.com", "static.tildacdn.com",
    "user-images.githubusercontent.com", "objects.githubusercontent.com",
}
STATUS_DIR = ROOT / "data" / "product_intake_v2" / "status"


class GuardError(ValueError):
    pass


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_json(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise GuardError("Expected JSON object")
    return data


def validate(manifest: dict) -> dict:
    if manifest.get("schema_version") != 2:
        raise GuardError("schema_version must equal 2")
    batch = manifest.get("batch_id")
    if not isinstance(batch, str) or not BATCH_PATTERN.fullmatch(batch):
        raise GuardError("Unsafe or missing batch_id")
    if manifest.get("approved") is not True:
        raise GuardError("Explicit owner approval required")
    items = manifest.get("items")
    if not isinstance(items, list) or not items or len(items) > 30:
        raise GuardError("Batch must contain 1..30 products")
    seen = set()
    for item in items:
        if not isinstance(item, dict):
            raise GuardError("Product must be object")
        sku = item.get("sku")
        if not isinstance(sku, str) or not SKU_PATTERN.fullmatch(sku):
            raise GuardError("Invalid SKU")
        if sku.casefold() in seen:
            raise GuardError("Duplicate SKU in batch: " + sku)
        seen.add(sku.casefold())
        if not item.get("title") or not item.get("description"):
            raise GuardError("Missing title/description: " + sku)
        if type(item.get("stock")) is not int or item["stock"] < 0:
            raise GuardError("Stock must be nonnegative integer: " + sku)
        prices = item.get("prices")
        if not isinstance(prices, dict) or set(prices) != set(CHANNELS):
            raise GuardError("Explicit prices for all five channels required: " + sku)
        for channel, price in prices.items():
            if type(price) is not int or price <= 0:
                raise GuardError("Price must be approved positive integer: " + sku + ":" + channel)
        item_channels = item.get("channels", list(CHANNELS))
        if not isinstance(item_channels, list) or len(set(item_channels)) != len(item_channels):
            raise GuardError("Invalid channel selection: " + sku)
        if not item_channels or any(c not in CHANNELS for c in item_channels):
            raise GuardError("Unknown channel: " + sku)
        media = item.get("media")
        if not isinstance(media, list) or not media or len(media) > 12:
            raise GuardError("At least one owner-approved media file required: " + sku)
        for entry in media:
            if not isinstance(entry, dict) or entry.get("approved") is not True:
                raise GuardError("Unapproved image: " + sku)
            if not SHA_PATTERN.fullmatch(str(entry.get("sha256") or "")):
                raise GuardError("Missing image SHA256: " + sku)
            kind = entry.get("source")
            if kind not in ("repository", "public_url", "chatgpt_library"):
                raise GuardError("Unknown media source: " + sku)
            if kind == "repository":
                p = entry.get("path")
                if not isinstance(p, str) or not p or Path(p).is_absolute() or ".." in Path(p).parts:
                    raise GuardError("Unsafe repo media path: " + sku)
            if kind == "public_url":
                u = urllib.parse.urlparse(str(entry.get("url") or ""))
                if u.scheme != "https" or u.hostname not in ALLOWED_MEDIA_HOSTS:
                    raise GuardError("Media host not approved: " + sku)
            if kind == "chatgpt_library" and not entry.get("filename"):
                raise GuardError("Library filename missing: " + sku)
        dimensions = item.get("dimensions")
        if not isinstance(dimensions, dict) or any(
            type(dimensions.get(k)) not in (int, float) or dimensions[k] <= 0
            for k in ("weight_g", "length_mm", "width_mm", "height_mm")
        ):
            raise GuardError("Missing positive packaged dimensions: " + sku)
        if not isinstance(item.get("categories", {}), dict):
            raise GuardError("Invalid channel category config: " + sku)
    return manifest


def safe_local_path(p: str) -> Path:
    resolved = (ROOT / p).resolve()
    if not resolved.is_relative_to(ROOT.resolve()):
        raise GuardError("Media file escapes repository")
    return resolved


def verified_file(path: Path, digest: str) -> bool:
    if not path.is_file():
        return False
    hasher = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            hasher.update(chunk)
    return hasher.hexdigest() == digest


def media_preflight(item: dict) -> tuple[list[Path], list[str]]:
    paths, blockers = [], []
    for index, m in enumerate(item["media"], 1):
        kind = m["source"]
        if kind == "chatgpt_library":
            blockers.append(
                f"image {index}: ChatGPT Library original exists, but GitHub runner has no binary-file bridge; "
                "do not substitute another image"
            )
            continue
        if kind == "repository":
            path = safe_local_path(m["path"])
            if not verified_file(path, m["sha256"]):
                blockers.append(f"image {index}: repository file missing or SHA256 mismatch")
            else:
                paths.append(path)
        else:
            # Only download public approved hosts; hash-check before publication.
            url = m["url"]
            try:
                with urllib.request.urlopen(
                    urllib.request.Request(url, headers={"User-Agent": "LEOPARTS-intake/2.0"}),
                    timeout=24,
                ) as resp:
                    data = resp.read(12_000_001)
                if len(data) > 12_000_000 or hashlib.sha256(data).hexdigest() != m["sha256"]:
                    raise GuardError("Size limit or SHA256 mismatch")
                folder = ROOT / "data" / "product_intake_v2" / "media_cache"
                folder.mkdir(parents=True, exist_ok=True)
                suffix = Path(urllib.parse.urlparse(url).path).suffix.lower()
                if suffix not in (".png", ".jpg", ".jpeg", ".webp"):
                    raise GuardError("Unsupported media suffix")
                local = folder / (m["sha256"] + suffix)
                local.write_bytes(data)
                paths.append(local)
            except Exception as exc:
                blockers.append(f"image {index}: public media fetch failed ({type(exc).__name__})")
    if len(paths) != len(item["media"]):
        return [], blockers
    return paths, []


def status(label: str, **fields: Any) -> dict:
    return {"state": label, **fields}


def full_price(item: dict, channel: str) -> int:
    # Deliberately no site_pricing.half_price or automatic 70%/60% share.
    return item["prices"][channel]


def different(actual: Any, expected: int) -> bool:
    try:
        return round(float(actual)) != expected
    except (TypeError, ValueError):
        return True


def build_local_plan(item: dict, paths: list[Path], blockers: list[str]) -> dict:
    return {
        "sku": item["sku"],
        "approved_prices": dict(item["prices"]),
        "stock": item["stock"],
        "approved_media_count": len(item["media"]),
        "media_ready": not blockers,
        "media_blockers": blockers,
        "channels": {c: status("ready_for_live_readback" if not blockers else "media_blocked")
                     for c in item.get("channels", list(CHANNELS))},
    }


def unique_matches(cards: list[dict], sku: str, key: str) -> list[dict]:
    return [c for c in cards if str(c.get(key) or "").casefold() == sku.casefold()]


def previous_pending(prev: dict, sku: str, channel: str) -> bool:
    old = (prev.get("items") or {}).get(sku, {}).get("channels", {}).get(channel, {})
    if old.get("state") not in ("creation_submitted", "creation_unverified"):
        return False
    submitted = old.get("time")
    if not isinstance(submitted, str):
        return False
    try:
        t = datetime.fromisoformat(submitted)
        return (datetime.now(timezone.utc) - t).total_seconds() < 3600
    except ValueError:
        return False


def run_wb(items: list[dict], plans: dict, media: dict, old: dict, apply: bool) -> None:
    try:
        import wb_client as wb
        import config
        cards = wb.list_cards(limit=100)
        if len(cards) < 110:
            raise GuardError("WB catalog paging unexpectedly short")
        prices = {int(x.get("nmID")): x for x in wb.get_prices(limit=1000)
                  if x.get("nmID") is not None}
        warehouse = os.environ.get("WB_WAREHOUSE_ID")
    except Exception as exc:
        for item in items:
            plans[item["sku"]]["channels"]["wildberries"] = status(
                "read_error", error_type=type(exc).__name__, reason=str(exc)[:160])
        return
    for item in items:
        sku, expected = item["sku"], full_price(item, "wildberries")
        row = plans[sku]["channels"]
        found = unique_matches(cards, sku, "vendorCode")
        if len(found) > 1:
            row["wildberries"] = status("blocked_duplicate", matches=len(found))
            continue
        if not found:
            cfg = (item.get("categories") or {}).get("wildberries") or {}
            if not media.get(sku):
                row["wildberries"] = status("blocked_missing_approved_images")
            elif previous_pending(old, sku, "wildberries"):
                row["wildberries"] = status("creation_unverified", reason="Cooldown after prior submit")
            elif not cfg.get("verified") or not cfg.get("subject_id") or "characteristics" not in cfg:
                row["wildberries"] = status("blocked_unverified_category")
            elif not apply:
                row["wildberries"] = status("ready_to_create", price=expected, stock=item["stock"])
            else:
                try:
                    barcodes = wb.generate_barcodes(1)
                    if len(barcodes) != 1:
                        raise GuardError("WB barcode unavailable")
                    dims = item["dimensions"]
                    body = {
                        "vendorCode": sku,
                        "title": item.get("wb_title", item["title"])[:60],
                        "description": item["description"],
                        "brand": item.get("brand", "Нет бренда"),
                        "dimensions": {
                            "length": round(dims["length_mm"]/10), "width": round(dims["width_mm"]/10),
                            "height": round(dims["height_mm"]/10),
                            "weightBrutto": round(dims["weight_g"]/1000, 3),
                        },
                        "sizes": [{"techSize": "0", "wbSize": "", "price": expected,
                                   "skus": [str(barcodes[0])]}],
                        "characteristics": cfg["characteristics"],
                    }
                    ack = wb.create_cards(int(cfg["subject_id"]), [body])
                    if ack.get("error"):
                        raise GuardError("WB async creation API rejected")
                    row["wildberries"] = status("creation_submitted", time=utcnow(),
                                                 reason="Await WB nmID and moderation")
                except Exception as exc:
                    row["wildberries"] = status("create_error", reason=str(exc)[:180])
            continue

        card = found[0]
        nm = int(card["nmID"])
        actual_images = len(card.get("photos") or [])
        current_price = prices.get(nm, {})
        variants = current_price.get("sizes") or []
        observed_price = variants[0].get("discountedPrice") if len(variants) == 1 else None
        problems = []
        if different(observed_price, expected):
            # Existing cards may have legitimate owner edits since manifest approval.
            # Never overwrite an existing listing's price from a stale intake batch.
            problems.append("existing_price_mismatch_manual_review")
        if actual_images < len(item["media"]):
            if media.get(sku) and apply:
                try:
                    wb.update_media(nm, media[sku])
                    problems.append("media_submitted_pending_readback")
                except Exception as exc:
                    problems.append("media_error:" + type(exc).__name__)
            else:
                problems.append("approved_media_missing")
        sizes = card.get("sizes") or []
        chrt = (sizes[0].get("chrtID") or sizes[0].get("chrtId")) if len(sizes)==1 else None
        observed_stock = None
        if warehouse and chrt:
            try:
                r = wb._request("POST", config.config.wb_marketplace_base,
                    f"/api/v3/stocks/{warehouse}", {"chrtIds":[int(chrt)]})
                data = r.get("stocks") or (r.get("data") or {}).get("stocks") or []
                sample = next((x for x in data if int(x.get("chrtId") or x.get("chrtID") or 0)==int(chrt)),None)
                observed_stock = sample.get("amount") if sample else None
                if observed_stock != item["stock"]:
                    # Operational stock is owned by the 3-hour shared-stock sync,
                    # not a catalog manifest that may already be stale.
                    problems.append("existing_stock_mismatch_do_not_overwrite")
            except Exception as exc:
                problems.append("stock_error:" + type(exc).__name__)
        else:
            problems.append("stock_readback_unavailable")
        row["wildberries"] = status("verified" if not problems else "partial",
             nmID=nm, photo_count=actual_images, expected_photo_count=len(item["media"]),
             price=observed_price, expected_price=expected, stock=observed_stock,
             expected_stock=item["stock"], issues=problems)


def run_market(items: list[dict], plans: dict, media: dict, old: dict, apply: bool) -> None:
    try:
        import yandex_market_client as ym
        business = ym.get_business_id()
        existing = ym.get_all_offers(archived=False)
        if len(existing) < 100:
            raise GuardError("Market catalog unexpectedly short")
    except Exception as exc:
        for item in items:
            plans[item["sku"]]["channels"]["yandex_market"] = status(
                "read_error", error_type=type(exc).__name__, reason=str(exc)[:160])
        return
    for item in items:
        sku, expected = item["sku"], full_price(item, "yandex_market")
        found = unique_matches(existing, sku, "sku")
        if len(found) > 1:
            plans[sku]["channels"]["yandex_market"] = status("blocked_duplicate")
            continue
        if not found:
            cfg = (item.get("categories") or {}).get("yandex_market") or {}
            if not media.get(sku):
                state = status("blocked_missing_approved_images")
            elif previous_pending(old, sku, "yandex_market"):
                state = status("creation_unverified", reason="Cooldown after prior submit")
            elif not cfg.get("verified") or not cfg.get("category_id"):
                state = status("blocked_unverified_category")
            elif not apply:
                state = status("ready_to_create", price=expected)
            else:
                dims = item["dimensions"]
                offer = {
                    "offerId":sku, "name":item["title"], "description":item["description"],
                    "pictures":media[sku], "vendor":item.get("brand","Нет бренда"),
                    "vendorCode":sku, "marketCategoryId":int(cfg["category_id"]),
                    "weightDimensions":{
                        "length":dims["length_mm"]/10, "width":dims["width_mm"]/10,
                        "height":dims["height_mm"]/10, "weight":dims["weight_g"]/1000,
                    },
                }
                try:
                    code, response = ym._request("POST", f"/v2/businesses/{business}/offer-mappings/update",
                        json_body={"offerMappings":[{"offer":offer}]})
                    if code != 200 or response.get("status") != "OK" or response.get("errors"):
                        raise GuardError("Market rejected create: " + str(response.get("errors"))[:100])
                    state = status("creation_submitted", time=utcnow())
                except Exception as exc:
                    state = status("create_error", reason=str(exc)[:180])
            plans[sku]["channels"]["yandex_market"] = state
            continue
        try:
            code, response = ym._request("POST", f"/v2/businesses/{business}/offer-prices",
                                        json_body={"offerIds":[sku]})
            price_rows = ((response.get("result") or {}).get("offers") or []) if code==200 else []
            price_entry = next((r for r in price_rows if r.get("offerId")==sku), None)
            raw_price = (price_entry or {}).get("price") or {}
            actual = raw_price.get("value")
            problems = []
            if different(actual, expected):
                # Existing Market prices are read-only in catalog onboarding.
                # Explicit owner price revisions must use a separate reviewed job.
                problems.append("existing_price_mismatch_manual_review")
            plans[sku]["channels"]["yandex_market"] = status(
                "verified" if not problems else "partial", expected_price=expected,
                observed_price=actual, issues=problems, business_catalog=True)
        except Exception as exc:
            plans[sku]["channels"]["yandex_market"] = status("read_error", reason=str(exc)[:180])


def run_tilda(items: list[dict], plans: dict, media_files: dict, apply: bool) -> None:
    try:
        from scripts.leoparts_direct_operation_nov21 import site_records
        rows = site_records()
    except Exception as exc:
        for item in items:
            plans[item["sku"]]["channels"]["tilda"] = status("read_error",reason=str(exc)[:160])
        return
    for item in items:
        sku, expected = item["sku"], full_price(item, "tilda")
        matches = unique_matches(list(rows.values()), sku, "sku")
        if len(matches) > 1:
            plans[sku]["channels"]["tilda"] = status("blocked_duplicate")
            continue
        found = matches[0] if matches else None
        if found and different(found.get("price"), expected):
            plans[sku]["channels"]["tilda"] = status(
                "existing_price_mismatch_manual_review",
                observed_price=found.get("price"), expected_price=expected)
            continue
        if found and not different(found.get("price"), expected):
            gal = found.get("gallery") or "[]"
            try:
                count = len(json.loads(gal)) if isinstance(gal,str) else len(gal)
            except (ValueError,TypeError):
                count=0
            if count >= len(item["media"]):
                plans[sku]["channels"]["tilda"] = status(
                    "verified", uid=found.get("uid"), price=expected, image_count=count)
                continue
        if found:
            # No destructive product+stock reimport for existing Tilda cards.
            # A media-only update requires a separate guarded step.
            plans[sku]["channels"]["tilda"]=status(
                "existing_media_incomplete_requires_safe_media_only_update", uid=found.get("uid"))
            continue
        if not media_files.get(sku):
            plans[sku]["channels"]["tilda"] = status("blocked_missing_approved_images",
                existing=False)
            continue
        if not apply:
            plans[sku]["channels"]["tilda"] = status("ready_to_update" if found else "ready_to_create")
            continue
        try:
            import tilda_commerceml as tc
            creds=("TILDA_COMMERCEML_URL","TILDA_COMMERCEML_USER","TILDA_COMMERCEML_PASSWORD")
            if not all(os.getenv(k) for k in creds):
                raise GuardError("Tilda CommerceML credentials unavailable")
            img, fn = [], []
            for n,p in enumerate(media_files[sku], 1):
                blob, suffix = tc._image_bytes(str(p), 3_000_000)
                ext = suffix or p.suffix
                filename=f"import_files/{sku}_{n}{ext}"
                img.append((filename,blob))
                fn.append(filename)
            entry={"id":sku,"title":item["title"],"description":item["description"],
                   "price":expected,"qty":item["stock"],"photo":""}
            imp=tc.build_import_xml([entry],{sku:fn}).replace(
                "<Каталог>",'<Каталог СодержитТолькоИзменения="true">')
            offers=tc.build_offers_xml([entry]).replace(
                'СодержитТолькоИзменения="false"','СодержитТолькоИзменения="true"')
            if 'СодержитТолькоИзменения="true"' not in imp or 'СодержитТолькоИзменения="true"' not in offers:
                raise GuardError("Change-only CommerceML required")
            tc.push(*(os.environ[k] for k in creds),imp,offers,timeout=110,image_files=img)
            plans[sku]["channels"]["tilda"]=status("import_submitted_pending_readback")
        except Exception as exc:
            plans[sku]["channels"]["tilda"]=status("import_error",reason=str(exc)[:180])


def read_ozon(items: list[dict], plans: dict) -> None:
    try:
        import ozon_client as oz
        existing = oz.list_products(limit=100)
    except Exception as exc:
        for item in items:
            plans[item["sku"]]["channels"]["ozon"]=status(
                "read_error", reason=str(exc)[:150])
        return
    for item in items:
        sku=item["sku"]
        matches=unique_matches(existing,sku,"offer_id")
        if len(matches)>1:
            state=status("blocked_duplicate")
        elif matches:
            state=status("existing_requires_moderation_readback",
                         product_id=matches[0].get("product_id"))
        else:
            state=status("blocked_verified_ozon_attributes_required",
                         reason="Missing confirmed category-specific payload including TN VED")
        plans[sku]["channels"]["ozon"]=state


def run(manifest: dict, *, mode: str = "inspect", offline: bool = True) -> dict:
    batch=validate(manifest)["batch_id"]
    if mode not in ("inspect","apply"):
        raise GuardError("Unknown run mode")
    if mode=="apply" and offline:
        raise GuardError("Apply requires explicitly enabled online mode")
    prev_path=STATUS_DIR/(batch+".json")
    prev=read_json(prev_path) if prev_path.exists() else {}
    plans, files, blockers = {}, {}, {}
    for item in manifest["items"]:
        paths, reasons=media_preflight(item) if not offline else (
            [safe_local_path(m["path"]) for m in item["media"]
             if m["source"]=="repository" and verified_file(safe_local_path(m["path"]),m["sha256"])],
            ["offline media source cannot be fetched"] if any(m["source"]!="repository" for m in item["media"]) else []
        )
        if len(paths)!=len(item["media"]) and not reasons:
            reasons=["repository file missing or SHA256 mismatch"]
        sku=item["sku"]
        files[sku]=paths if not reasons else []
        blockers[sku]=reasons
        plans[sku]=build_local_plan(item,files[sku],reasons)
    if not offline:
        public={}
        for item in manifest["items"]:
            sku=item["sku"]
            if files[sku] and mode=="apply":
                try:
                    import photo_host
                    public[sku]=[photo_host.upload_file(str(p),f"{sku}_{i}{p.suffix}")
                                 for i,p in enumerate(files[sku],1)]
                except Exception as exc:
                    public[sku]=[]
                    blockers[sku].append("Media release upload failed: "+type(exc).__name__)
            else:
                public[sku]=[]
        # Read existing cards even when media is blocked so statuses do not lie.
        chosen=lambda c:[it for it in manifest["items"] if c in it.get("channels",list(CHANNELS))]
        run_wb(chosen("wildberries"),plans,public,prev,mode=="apply")
        run_market(chosen("yandex_market"),plans,public,prev,mode=="apply")
        run_tilda(chosen("tilda"),plans,files,mode=="apply")
        read_ozon(chosen("ozon"),plans)
        for item in chosen("avito"):
            plans[item["sku"]]["channels"]["avito"]=status("blocked_category_specific_feed",
              reason="Avito tools require verified tool/part template and autoload readback; no wrong category")
    report={
        "schema_version":2,"batch_id":batch,
        "mode":mode,"offline":offline,"generated_at_utc":utcnow(),
        "items":{sku:{"media":status("verified" if not blockers[sku] else "blocked",
                                   count=len(files[sku]),issues=blockers[sku]),
                      "channels":row["channels"]} for sku,row in plans.items()},
    }
    report["summary"]={
        "total":sum(len(row["channels"]) for row in plans.values()),
        "verified":sum(c["state"]=="verified" for row in plans.values() for c in row["channels"].values()),
        "blocked":sum(c["state"].startswith("blocked") for row in plans.values() for c in row["channels"].values()),
        "pending":sum("submitted" in c["state"] or c["state"]=="creation_unverified"
                       for row in plans.values() for c in row["channels"].values()),
    }
    if not offline and mode=="apply":
        STATUS_DIR.mkdir(parents=True,exist_ok=True)
        prev.pop("generated_at_utc",None)
        compare=dict(report);compare.pop("generated_at_utc",None)
        if prev!=compare:
            prev_path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    return report


def cli() -> None:
    parser=argparse.ArgumentParser(description="LEOPARTS single-batch product intake, fail-safe")
    parser.add_argument("--request",required=True)
    parser.add_argument("--mode",choices=("inspect","apply"),default="inspect")
    parser.add_argument("--online",action="store_true")
    args=parser.parse_args()
    request=safe_local_path(args.request)
    if request.suffix!=".json" or "data/product_intake_v2/requests/" not in str(request):
        raise GuardError("Requests must be under data/product_intake_v2/requests/")
    report=run(read_json(request),mode=args.mode,offline=not args.online)
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=="__main__":
    cli()
