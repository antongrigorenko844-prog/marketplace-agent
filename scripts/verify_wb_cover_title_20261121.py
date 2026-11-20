"""Apply the single approved title, verify seller fields, and record the public cover samples."""
import copy
import hashlib
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import wb_client
from content_sync import apply_wb
from content_transport import guard

SKU = "0am325025ae"
NM = 670925563
OLD = "усиленная прокладка мехатроника DSG7 DQ200"
NEW = "Крышка мехатроника с усиленной прокладкой DSG7 DQ200"
KIT = ["Крышка мехатроника — 1 шт.", "усиленная прокладка крышки — 1 шт."]
COVERS = {NM, 670925512, 672031719, 672031724, 837952965, 670860803}
OUT = Path("/tmp/wb-kit-title")
OUT.mkdir(exist_ok=True)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def card(cards):
    found = [c for c in cards if c.get("nmID") == NM or c.get("vendorCode") == SKU]
    if len(found) != 1 or (found[0]["vendorCode"], found[0]["nmID"]) != (SKU, NM):
        raise ValueError("WB identity changed")
    return found[0]


def summarize(c):
    stable = {k: copy.deepcopy(v) for k, v in c.items() if k not in {"title", "updatedAt"}}
    stable["characteristics"] = sorted(stable["characteristics"], key=lambda a: a["id"])
    return {"title": c["title"], "updated_at": c["updatedAt"], "content_except_title_sha256": digest(stable)}


def save(report):
    (OUT / "result.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))


def public_covers(cards):
    results = []
    for c in cards:
        if c["nmID"] not in COVERS or not c.get("photos"):
            continue
        link = c["photos"][0].get("c516x688") or c["photos"][0].get("big")
        row = {"nmID": c["nmID"], "sku": c["vendorCode"], "url": link}
        results.append(row)
        if not link or urlparse(link).scheme != "https" or not (urlparse(link).hostname or "").endswith(".wbbasket.ru"):
            continue
        try:
            resp = requests.get(link, timeout=15)
            row["http"] = resp.status_code
            if resp.ok and resp.headers.get("Content-Type", "").startswith("image/") and len(resp.content) < 8000000:
                (OUT / (str(c["nmID"]) + ".webp")).write_bytes(resp.content)
                row["sha256"] = hashlib.sha256(resp.content).hexdigest()
        except requests.RequestException:
            row["status"] = "unavailable"
    return results


def run():
    report = {"sku": SKU, "nmID": NM, "old_title": OLD, "approved_title": NEW, "started_utc": stamp(),
              "run_id": os.getenv("GITHUB_RUN_ID"), "status": "preflight"}
    try:
        before_cards = wb_client.list_cards()
        previous = card(before_cards)
        report["before"] = summarize(previous)
        save(report)
        if previous["title"] not in (OLD, NEW):
            raise ValueError("Current title differs from both approved values")
        composition = [a["value"] for a in previous["characteristics"] if a["id"] == 378533]
        if composition != [KIT] or any(code not in previous["description"] for code in ("0AM325219C", "0AM325443D")):
            raise ValueError("Product composition changed")
        writes = []
        with guard(writes, allow_write=True):
            result = apply_wb(SKU, {"ids": {"wb": NM}, "text": {"wb": {"title": NEW}}}, {"cards": [previous]})
        report["after"] = summarize(result["after"])
        if report["after"]["content_except_title_sha256"] != report["before"]["content_except_title_sha256"]:
            raise ValueError("Other seller fields differ on readback")
        report["accepted_content_requests"] = len(writes)
        report["prices_stock_media_category_writes"] = 0
        report["status"] = "seller_api_verified" if result["changed"] else "seller_api_already_correct"
        save(report)
        try:
            errors = wb_client.get_upload_errors()
            report["recent_upload_errors_for_sku"] = sum(SKU in (entry.get("errors") or {}) for entry in errors)
        except Exception:
            report["recent_upload_errors_for_sku"] = None
        report["moderation"] = "Отдельный статус модерации в ответе Content API не передан."
        report["covers"] = public_covers(before_cards)
        return 0
    except Exception as exc:
        report["status"] = "unconfirmed"
        report["reason"] = type(exc).__name__ + ": " + str(exc)[:240] if isinstance(exc, ValueError) else type(exc).__name__
        return 1
    finally:
        report["finished_utc"] = stamp()
        save(report)
        print(json.dumps({k: report.get(k) for k in ("sku", "nmID", "status", "accepted_content_requests", "reason")}, ensure_ascii=False))


if __name__ == "__main__":
    sys.exit(run())
