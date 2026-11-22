"""Fast, single-SKU Avito cover change; never regenerate catalog or touch prices.

plan: read-only for seller feeds; checks local workbook + optional GitHub Release SHA.
apply: explicit AVITO_AUTOLOAD_READY=1 required; GitHub asset SHA acts as an
optimistic-lock guard; only one images cell may change. Avito ingestion is async.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import sys
from time import monotonic
from urllib.parse import urlsplit

import openpyxl
import requests

ROOT = Path(__file__).resolve().parent
LOCAL_FEED = ROOT / "data" / "avito_feed.xlsx"
REPORT = ROOT / "data" / "avito_fast_cover_report.json"
REPO = "antongrigorenko844-prog/marketplace-agent"
RELEASE = "product-photos"
ASSET = "avito_feed.xlsx"
SHEET = "Объявления"
FIRST_DATA_ROW = 5
ID_COL = 4
IMAGE_COL = 19


class AvitoCoverError(RuntimeError):
    pass


def _hash(data: bytes) -> str:
    return sha256(data).hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _approved_image(sku: str) -> str:
    import content_manifest

    if not sku or sku.strip() != sku or "/" in sku or chr(92) in sku:
        raise AvitoCoverError("Invalid exact SKU")
    rule = content_manifest.load().get(sku) or {}
    images = (rule.get("sources") or {}).get("avito") or []
    if not images:
        raise AvitoCoverError("No approved Avito image for exact SKU")
    url = images[0]
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise AvitoCoverError("Approved media URL must be public HTTPS")
    return url


def _target_row(ws, sku: str) -> int:
    matches = [
        row for row in range(FIRST_DATA_ROW, ws.max_row + 1)
        if str(ws.cell(row, ID_COL).value or "").strip() in (sku, sku + "-av")
    ]
    if len(matches) != 1:
        raise AvitoCoverError(
            f"Expected one existing Avito feed item for {sku}, found {len(matches)}"
        )
    return matches[0]


def _image_urls(text: object) -> list[str]:
    # Avito XLSX template puts images on separate lines, not in a JSON field.
    urls = [s.strip() for s in str(text or "").splitlines() if s.strip()]
    if not urls:
        raise AvitoCoverError("Existing Avito image gallery is empty")
    return urls


def _assert_single_cell_change(before: bytes, after: bytes, row: int):
    old = openpyxl.load_workbook(BytesIO(before), read_only=True, data_only=False)
    new = openpyxl.load_workbook(BytesIO(after), read_only=True, data_only=False)
    try:
        if old.sheetnames != new.sheetnames:
            raise AvitoCoverError("Workbook sheets changed")
        for name in old.sheetnames:
            a, b = old[name], new[name]
            if (a.max_row, a.max_column) != (b.max_row, b.max_column):
                raise AvitoCoverError("Workbook dimensions changed")
            for r, (ra, rb) in enumerate(zip(
                    a.iter_rows(values_only=True), b.iter_rows(values_only=True)), 1):
                for c, (va, vb) in enumerate(zip(ra, rb), 1):
                    if name == SHEET and r == row and c == IMAGE_COL:
                        continue
                    if va != vb:
                        raise AvitoCoverError(
                            f"Unrelated cell changed {name} row {r} col {c}"
                        )
    finally:
        old.close()
        new.close()


def patch_cover(original: bytes, sku: str, url: str) -> tuple[bytes, dict]:
    """Return a changed workbook or EXACT original bytes for a no-op."""
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise AvitoCoverError("Photo must have a public HTTPS URL")
    if not original:
        raise AvitoCoverError("Empty Avito feed")
    book = openpyxl.load_workbook(BytesIO(original))
    try:
        if SHEET not in book:
            raise AvitoCoverError("Missing official Avito sheet")
        ws = book[SHEET]
        row = _target_row(ws, sku)
        original_urls = _image_urls(ws.cell(row, IMAGE_COL).value)
        info = {
            "sku": sku,
            "row": row,
            "image_count": len(original_urls),
            "previous_primary": original_urls[0],
            "approved_primary": url,
            "unchanged_secondary_count": len(original_urls) - 1,
            "price_stock_title_description_untouched": True,
        }
        if original_urls[0] == url:
            return original, {**info, "changed": False}
        if url in original_urls[1:]:
            raise AvitoCoverError("Approved image is already secondary; explicit reorder required")
        ws.cell(row, IMAGE_COL).value = "\n".join([url] + original_urls[1:])
        buffer = BytesIO()
        book.save(buffer)
        revised = buffer.getvalue()
    finally:
        book.close()
    _assert_single_cell_change(original, revised, row)
    check = openpyxl.load_workbook(BytesIO(revised), read_only=True)
    try:
        actual = _image_urls(check[SHEET].cell(row, IMAGE_COL).value)
        if actual != [url] + original_urls[1:]:
            raise AvitoCoverError("Changed photo gallery did not survive XLSX reserialization")
    finally:
        check.close()
    return revised, {**info, "changed": True}


def _ready(value: object) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def _api(token: str, method: str, path: str, **kwargs):
    url = f"https://api.github.com/repos/{REPO}{path}"
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = "Bearer " + token
    r = requests.request(method, url, headers=headers, timeout=18, **kwargs)
    r.raise_for_status()
    return r.json()


def _get_release(token: str):
    release = _api(token, "GET", f"/releases/tags/{RELEASE}")
    existing = [x for x in release.get("assets", []) if x.get("name") == ASSET]
    if len(existing) != 1:
        raise AvitoCoverError("Stable Avito release asset missing or duplicated")
    return release, existing[0]


def _digest(asset: dict) -> str:
    value = asset.get("digest") or ""
    if not isinstance(value, str) or not value.startswith("sha256:") or len(value) != 71:
        raise AvitoCoverError("GitHub asset SHA-256 unavailable; refuse unsafe overwrite")
    return value[7:]


def _post_asset(token: str, upload_url: str, payload: bytes):
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": "Bearer " + token,
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    r = requests.post(
        upload_url.split("{")[0],
        params={"name": ASSET},
        headers=headers,
        data=payload,
        timeout=35,
    )
    r.raise_for_status()
    return r.json()


def _publish(token: str, release: dict, old_asset: dict, revised: bytes) -> dict:
    """Promote with rollback and verify authoritative GitHub asset SHA; no CDN polling."""
    if not token:
        raise AvitoCoverError("GitHub write token missing")
    old_id = int(old_asset["id"])
    previous_name = f"avito_feed.backup_fast_{old_id}.xlsx"
    new_id = None
    changed_old = False
    try:
        _api(token, "PATCH", f"/releases/assets/{old_id}",
             json={"name": previous_name})
        changed_old = True
        new_asset = _post_asset(token, release["upload_url"], revised)
        new_id = int(new_asset["id"])
        got = new_asset.get("digest")
        if got is None:
            got = _api(token, "GET", f"/releases/assets/{new_id}").get("digest")
        if got != "sha256:" + _hash(revised):
            raise AvitoCoverError("GitHub asset digest mismatch; rolling back")
        final = _api(token, "GET", f"/releases/assets/{new_id}")
        if final.get("name") != ASSET or _digest(final) != _hash(revised):
            raise AvitoCoverError("Stable asset not verified after upload")
        return {
            "status": "published_feed_waiting_avito_processing",
            "backup_asset": previous_name,
            "new_asset_id": new_id,
            "feed_sha256": _hash(revised),
            "public_cdn_readback": "not_blocking",
            "avito_listing_status": "not_checked",
        }
    except Exception as error:
        # Keep a revertible stable file even if upload or checks fail.
        rollback_errors = []
        if new_id is not None:
            try:
                _api(token, "DELETE", f"/releases/assets/{new_id}")
            except Exception as exc:
                rollback_errors.append(type(exc).__name__)
        if changed_old:
            try:
                _api(token, "PATCH", f"/releases/assets/{old_id}",
                     json={"name": ASSET})
            except Exception as exc:
                rollback_errors.append(type(exc).__name__)
        raise AvitoCoverError(
            "Promotion failed; rollback errors: " + ",".join(rollback_errors or ["none"])
        ) from error


def run(sku: str, mode: str, check_release: bool = False) -> dict:
    start = monotonic()
    url = _approved_image(sku)
    original = LOCAL_FEED.read_bytes()
    revised, info = patch_cover(original, sku, url)
    report = {
        "sku": sku,
        "mode": mode,
        "checked_utc": _utc(),
        "local_feed_sha256": _hash(original),
        "proposed_feed_sha256": _hash(revised),
        "patch": info,
        "price_and_stock_writes": 0,
    }
    if mode == "apply" and not _ready(os.environ.get("AVITO_AUTOLOAD_READY", "")):
        report["result"] = "skipped_autoload_not_confirmed_ready"
        report["seconds"] = round(monotonic() - start, 2)
        return report

    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN") or ""
    if mode != "apply" and not check_release:
        report["result"] = "already_staged" if not info["changed"] else "ready_for_review"
        report["seconds"] = round(monotonic() - start, 2)
        return report

    release, asset = _get_release(token)
    live_sha = _digest(asset)
    report["published_feed_sha256"] = live_sha
    report["repo_matches_published_feed"] = live_sha == _hash(original)

    if live_sha == _hash(revised):
        report["result"] = "already_published_no_upload"
    elif mode == "plan":
        report["result"] = (
            "ready_for_apply" if live_sha == _hash(original)
            else "blocked_remote_drift"
        )
    else:
        if live_sha != _hash(original):
            raise AvitoCoverError("Published and repository feeds differ; refuse overwrite")
        outcome = _publish(token, release, asset, revised)
        LOCAL_FEED.write_bytes(revised)
        report.update(outcome)
        report["result"] = outcome["status"]
    report["seconds"] = round(monotonic() - start, 2)
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sku", required=True)
    p.add_argument("--mode", choices=("plan", "apply"), default="plan")
    p.add_argument("--check-release", action="store_true")
    args = p.parse_args()
    try:
        report = run(args.sku, args.mode, args.check_release)
        code = 0
    except Exception as exc:
        report = {
            "sku": args.sku,
            "mode": args.mode,
            "result": "error",
            "error_type": type(exc).__name__,
            "error": str(exc)[:240],
            "checked_utc": _utc(),
            "price_and_stock_writes": 0,
        }
        code = 1
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
