"""Plan a Tilda cover-only CSV import for one existing item.

Tilda's CommerceML import may APPEND gallery images instead of replacing.
Use the officially documented CSV import with TildaUID and the "update only
existing products" option. This module never writes into the Tilda account.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

from PIL import Image, ImageChops, ImageStat
import requests

from content_manifest import load

ROOT = Path(__file__).resolve().parent
REPORT = ROOT / "data/tilda_cover_plan_report.json"
CSV = ROOT / "data/tilda_cover_patch.csv"


def build_csv(uid: str, sku: str, photos: list[str]) -> str:
    """Only identity + photo. No price/quantity/title/description columns."""
    if not uid.isascii() or not uid.isdigit() or not sku or sku.strip() != sku:
        raise ValueError("Tilda UID and exact SKU required")
    if not photos or len(photos) > 15 or len(set(photos)) != len(photos):
        raise ValueError("Missing or duplicate product photos")
    if any(not p.startswith("https://") for p in photos):
        raise ValueError("Tilda requires direct HTTPS photos")
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, delimiter=";", lineterminator="\n")
    writer.writerow(["TildaUID", "SKU", "Photo"])
    writer.writerow([uid, sku, ",".join(photos)])
    return buf.getvalue()


def gallery_entries(row):
    value = row.get("gallery") or []
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list):
        raise ValueError("Unsupported live Tilda gallery")
    result = []
    for photo in value:
        if isinstance(photo, str) and photo.startswith("https://"):
            result.append(photo)
        elif isinstance(photo, dict):
            url = next((photo.get(k) for k in ("img", "url", "original", "src", "large")
                        if isinstance(photo.get(k), str) and photo[k].startswith("https://")), None)
            if url:
                result.append(url)
    return result


def same_pixels(url1, url2):
    def open_image(url):
        r = requests.get(url, timeout=24, headers={"User-Agent": "LEOPARTS Tilda photo check"})
        r.raise_for_status()
        if len(r.content) > 13_000_000:
            raise ValueError("Too many image bytes")
        with Image.open(io.BytesIO(r.content)) as image:
            return image.convert("RGB").resize((240, 320), Image.Resampling.LANCZOS)
    a, b = open_image(url1), open_image(url2)
    return round(sum(ImageStat.Stat(ImageChops.difference(a, b)).mean)/3, 2)


def run(sku: str, site_records_fn=None, check_pixels=True):
    rules = load()
    row = rules.get(sku)
    if not row or not row.get("sources", {}).get("tilda"):
        raise ValueError("Exact SKU not approved for Tilda")
    uid = str((row.get("ids") or {}).get("tilda") or "")
    photos = list(row["sources"]["tilda"])
    contents = build_csv(uid, sku, photos)
    if site_records_fn is None:
        from scripts.leoparts_direct_operation_nov21 import site_records
        site_records_fn = site_records
    records = site_records_fn()
    found = [x for x in records.values() if str(x.get("sku") or "") == sku]
    if len(found) != 1 or str(found[0].get("uid")) != uid:
        raise ValueError("Tilda SKU/UID not unique or changed; no CSV generated")
    live = found[0]
    gallery = gallery_entries(live)
    if not gallery:
        raise ValueError("Existing Tilda gallery unexpectedly empty")
    difference = None
    if check_pixels:
        try:
            difference = same_pixels(gallery[0], photos[0])
        except Exception:
            difference = None
    status = "verified" if difference is not None and difference < 16 else "needs_csv_import"
    CSV.write_text(contents, encoding="utf-8-sig")
    result = {
        "sku": sku, "uid": uid, "checked_utc": datetime.now(timezone.utc).isoformat(),
        "status": status, "live_primary": gallery[0],
        "desired_primary": photos[0], "live_gallery_size": len(gallery),
        "approved_gallery_size": len(photos), "mean_pixel_difference": difference,
        "csv_path": str(CSV.relative_to(ROOT)),
        "csv_identity_and_media_columns_only": True,
        "tilda_writes": 0, "price_writes": 0, "stock_writes": 0,
        "next_step": (
            "none" if status == "verified" else
            "Tilda Catalog > Import (or update) CSV > map TildaUID, SKU, Photo; "
            "check 'Update only existing products and do not create new ones'; "
            "verify first image on public site afterwards"
        ),
    }
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sku", required=True)
    p.add_argument("--no-pixel-check", action="store_true")
    args = p.parse_args()
    try:
        report = run(args.sku, check_pixels=not args.no_pixel_check)
    except Exception as exc:
        report = {"sku": args.sku, "status": "blocked",
                  "reason": str(exc)[:320], "tilda_writes": 0,
                  "price_writes": 0, "stock_writes": 0}
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if report["status"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
