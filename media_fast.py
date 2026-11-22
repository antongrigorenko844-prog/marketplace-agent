"""Fast, content-only cover replacement for one approved LEOPARTS SKU.

Uses only sources pinned in data/content_manifest.json. No catalog rebuild, no
price or inventory API calls. Each channel is independent; pending is NOT success.
"""
from __future__ import annotations

import argparse
import datetime
import io
import json
import os
from pathlib import Path
import sys
import time
from urllib.parse import urlsplit

from PIL import Image, ImageChops, ImageStat
import requests

from content_manifest import load as load_manifest

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "media_fast_report.json"
CHANNELS = ("ozon", "wb", "market")
READ_ONLY = {"inspect", "verify"}


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def selected_channels(value):
    parts = tuple(x.strip() for x in value.split(",") if x.strip())
    if not parts or len(parts) != len(set(parts)) or set(parts) - set(CHANNELS):
        raise ValueError("Choose unique channels: ozon,wb,market")
    return parts


def desired_url(sku, channel, manifest=None):
    if not sku or sku != sku.strip() or "/" in sku or "\\" in sku:
        raise ValueError("Exact SKU required")
    rules = load_manifest() if manifest is None else manifest
    row = rules.get(sku)
    if not isinstance(row, dict):
        raise ValueError("SKU missing in approved media manifest")
    source = row.get("sources", {}).get(channel) or []
    if not source:
        raise ValueError("No approved cover for this channel")
    url = source[0]
    p = urlsplit(url)
    if p.scheme != "https" or not p.hostname or p.username or p.password:
        raise ValueError("Approved image source must be HTTPS")
    return url


def other_images(before, primary, new_url):
    """Preserve existing second and later images; exclude the old cover."""
    return [u for u in before if u and u != primary and u != new_url]


def _one(rows, key, sku):
    found = [row for row in rows if str(row.get(key)) == sku]
    if len(found) != 1:
        raise ValueError(f"Expected one existing {key}={sku}; found {len(found)}")
    return found[0]


def _diff_keys(before, after, keys):
    return [k for k in keys if before.get(k) != after.get(k)]


def _jpg_compare(url_a, url_b, timeout=22):
    """Inspect actual WB CDN pixels, not a changing CDN filename."""
    def load(url):
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "LEOPARTS media verifier"})
        r.raise_for_status()
        if len(r.content) > 12_000_000:
            raise ValueError("Image unexpectedly large")
        with Image.open(io.BytesIO(r.content)) as im:
            return im.convert("RGB").resize((240, 320), Image.Resampling.LANCZOS)
    a, b = load(url_a), load(url_b)
    return round(sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3, 2)


def _photo_url(photo):
    return photo.get("big") or photo.get("c516x688") or photo.get("square") or ""


def _ozon(sku, url, mode):
    import ozon_client as oz
    before = _one(oz.get_product_names([sku]), "offer_id", sku)
    primary = before.get("primary_image") or ""
    if not isinstance(primary, str) or not primary:
        raise ValueError("Ozon live primary image unavailable")
    if primary == url:
        return {"status": "verified", "changed": False, "live_primary": primary}
    existing = before.get("images") or []
    extras = other_images(existing, primary, url)
    if mode in READ_ONLY:
        return {"status": "needs_update", "changed": False, "old_cover_present": True,
                "preserved_secondary_count": len(extras)}
    response = oz._post("/v2/product/pictures/import", {
        "items": [{"offer_id": sku, "primary_image": url, "images": extras,
                   "color_image": before.get("color_image") or ""}]
    })
    if not isinstance(response, dict) or response.get("error"):
        raise RuntimeError("Ozon did not accept image update")
    after = _one(oz.get_product_names([sku]), "offer_id", sku)
    unrelated = _diff_keys(before, after, [
        "name", "description_category_id", "type_id", "height", "depth", "width", "weight"])
    verified = after.get("primary_image") == url
    return {"status": "verified" if verified else "pending", "changed": True,
            "request_accepted": True,
            "task_id": response.get("task_id") or (response.get("result") or {}).get("task_id"),
            "preserved_secondary_count": len(extras),
            "nonmedia_drift": unrelated}


def _wb(sku, url, mode, check_pixels):
    import wb_client as wb
    before = _one(wb.list_cards(), "vendorCode", sku)
    photos = before.get("photos") or []
    if not photos:
        raise ValueError("WB card has no existing images")
    primary = _photo_url(photos[0])
    if not primary:
        raise ValueError("WB primary image URL missing")
    expected = (load_manifest()[sku].get("ids") or {}).get("wb")
    if expected and int(before.get("nmID")) != int(expected):
        raise ValueError("WB nmID differs from approved catalog ID")
    difference = _jpg_compare(primary, url) if check_pixels else None
    verified = difference is not None and difference < 16
    if verified:
        return {"status": "verified", "changed": False, "mean_pixel_difference": difference,
                "live_photo_count": len(photos)}
    if mode in READ_ONLY:
        return {"status": "needs_update" if difference is not None else "unverified",
                "changed": False, "mean_pixel_difference": difference,
                "live_photo_count": len(photos)}
    response = requests.get(url, timeout=25)
    response.raise_for_status()
    if len(response.content) > 10_000_000:
        raise ValueError("Approved image exceeds 10 MB")
    image_bytes = response.content
    with Image.open(io.BytesIO(image_bytes)) as im:
        fmt = im.format
    if fmt not in {"PNG", "JPEG"}:
        raise ValueError("WB cover must be PNG/JPEG")
    ct = "image/png" if fmt == "PNG" else "image/jpeg"
    ext = ".png" if fmt == "PNG" else ".jpg"
    response = requests.post(
        "https://content-api.wildberries.ru/content/v3/media/file",
        headers={"Authorization": os.environ["WB_API_TOKEN"],
                 "X-Nm-Id": str(before["nmID"]), "X-Photo-Number": "1"},
        files={"uploadfile": ("cover" + ext, image_bytes, ct)}, timeout=55,
    )
    response.raise_for_status()
    body = response.json()
    if body.get("error"):
        raise RuntimeError("WB rejected photo upload")
    # Asynchronous moderation may not have finished. One bounded read, no long polling.
    after = _one(wb.list_cards(), "vendorCode", sku)
    live_after = _photo_url((after.get("photos") or [{}])[0])
    nonmedia = _diff_keys(before, after, [
        "title", "description", "brand", "subjectID", "dimensions", "sizes", "video"])
    char_before = {r.get("id"): r.get("value") for r in before.get("characteristics", [])}
    char_after = {r.get("id"): r.get("value") for r in after.get("characteristics", [])}
    if char_before != char_after:
        nonmedia.append("characteristics")
    if len(after.get("photos") or []) != len(photos):
        nonmedia.append("photo_count")
    return {"status": "pending", "changed": True,
            "request_accepted": True, "post_upload_live_url": live_after,
            "verification": "check CDN pixels in a subsequent read-only run",
            "nonmedia_drift": nonmedia, "photos_before": len(photos)}


def _market(sku, url, mode):
    import yandex_market_client as ym
    from market_content import read_rows, preserve_typed_parameters, grouped
    business = ym.get_business_id()
    old = _one([r["offer"] for r in read_rows(business, "offer-mappings", [sku])], "offerId", sku)
    card = _one(read_rows(business, "offer-cards", [sku]), "offerId", sku)
    photos = list(old.get("pictures") or [])
    if not photos:
        raise ValueError("Market primary image missing")
    new = [url] + other_images(photos, photos[0], url)
    if photos == new:
        return {"status": "verified", "changed": False, "photo_count": len(new)}
    if mode in READ_ONLY:
        return {"status": "needs_update", "changed": False,
                "preserved_secondary_count": len(new) - 1}
    payload = preserve_typed_parameters(business, [{"offer": {"offerId": sku, "pictures": new}}])
    status, res = ym._request("POST", f"/v2/businesses/{business}/offer-mappings/update",
                              json_body={"offerMappings": payload})
    if status != 200 or res.get("status") != "OK" or any(r.get("errors") for r in res.get("results", [])):
        raise RuntimeError("Market refused photo update")
    after = _one([r["offer"] for r in read_rows(business, "offer-mappings", [sku])], "offerId", sku)
    after_card = _one(read_rows(business, "offer-cards", [sku]), "offerId", sku)
    changed_fields = _diff_keys(old, after, [
        "name", "description", "basicPrice", "purchasePrice", "vendor", "vendorCode",
        "barcodes", "params", "videos", "weightDimensions"])
    if grouped(card.get("parameterValues", [])) != grouped(after_card.get("parameterValues", [])):
        changed_fields.append("parameterValues")
    return {"status": "verified" if after.get("pictures") == new else "pending",
            "changed": True, "request_accepted": True, "nonmedia_drift": changed_fields,
            "preserved_secondary_count": len(new) - 1}


def run(sku, mode="inspect", channels=("ozon", "wb", "market"), check_pixels=True):
    if mode not in {"inspect", "verify", "apply"}:
        raise ValueError("Unsupported mode")
    if mode == "apply" and os.getenv("MEDIA_APPLY_APPROVED") != "true":
        raise PermissionError("Explicit media apply approval is required")
    if not channels or len(set(channels)) != len(channels) or set(channels) - set(CHANNELS):
        raise ValueError("Invalid channel selection")
    report = {"sku": sku, "mode": mode, "created_utc": now(), "channels": {},
              "price_write_calls": 0, "stock_write_calls": 0,
              "requested_channels": list(channels)}
    for channel in channels:
        try:
            url = desired_url(sku, channel)
            report["channels"][channel] = {"approved_url": url, **{
                "ozon": lambda: _ozon(sku, url, mode),
                "wb": lambda: _wb(sku, url, mode, check_pixels),
                "market": lambda: _market(sku, url, mode),
            }[channel]()}
        except Exception as exc:
            report["channels"][channel] = {
                "status": "blocked", "error_type": type(exc).__name__,
                "reason": str(exc)[:280]}
    status = [r["status"] for r in report["channels"].values()]
    report["overall"] = ("blocked" if "blocked" in status else
                         "verified" if all(s == "verified" for s in status) else
                         "pending" if "pending" in status else "needs_update")
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sku", required=True)
    p.add_argument("--mode", default="inspect", choices=("inspect", "verify", "apply"))
    p.add_argument("--channels", default="ozon,wb,market")
    p.add_argument("--skip-wb-pixels", action="store_true",
                   help="Only for dry-run diagnostics; never assert WB verified without pixels")
    args = p.parse_args()
    try:
        report = run(args.sku, args.mode, selected_channels(args.channels),
                     check_pixels=not args.skip_wb_pixels)
    except Exception as exc:
        report = {"sku": args.sku, "mode": args.mode, "overall": "blocked",
                  "reason": str(exc)[:280], "price_write_calls": 0, "stock_write_calls": 0}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 1 if report["overall"] == "blocked" else 0


if __name__ == "__main__":
    sys.exit(main())
