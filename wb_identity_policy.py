"""Exact WB-only sales holds and identity collision quarantine.

Do not treat an old nmID as the present product simply because vendorCode
matches an Ozon SKU. Neither this policy nor its tests changes shared stock.
"""
import re

HELD = {
    "0AM927769D": {"nmID": 670860344, "chrtID": 901719290, "barcode": "2047604932052",
                  "reason": "Retired legacy gasket kit; never the active electronic board"},
    "solenoid-02e-0b5-bolshoy": {"nmID": 1722881122, "chrtID": 2822491271, "barcode": "2057528010828",
                                "reason": "Owner confirmed discontinued on WB 2026-11-26"},
}
ACTIVE_BOARD = {"vendorCode": "0AM927769D-plata", "nmID": 1922846685}
ACTIVE_GASKET = {"vendorCode": "0AM927769N", "nmID": 1949822182}


def is_held(vendor=None, nm_id=None):
    return vendor in HELD or str(nm_id) in {str(v["nmID"]) for v in HELD.values()}


def guard_write(method, path, body):
    """Reject accidental writes to exact retired identities; allow zero-stock holds."""
    method = method.upper()
    path = path.split("?")[0]
    if method == "PUT" and re.fullmatch(r"/api/v3/stocks/[^/]+", path):
        blocked = {str(v["chrtID"]) for v in HELD.values()} | {v["barcode"] for v in HELD.values()}
        for row in (body or {}).get("stocks", []):
            if any(str(row.get(k)) in blocked for k in ("sku", "chrtId", "chrtID")) and row.get("amount") != 0:
                raise ValueError("Positive WB stock forbidden for owner-held or retired identity")
    elif method == "POST" and path in ("/content/v2/cards/update", "/content/v2/cards/upload"):
        rows = body or []
        if path.endswith("/upload"):
            rows = [v for group in rows for v in group.get("variants", [])]
        if any(is_held(v.get("vendorCode"), v.get("nmID")) for v in rows):
            raise ValueError("WB content mutation forbidden for held/retired exact identity")
    elif method == "POST" and path == "/content/v3/media/save":
        if is_held(nm_id=(body or {}).get("nmId")):
            raise ValueError("WB media mutation forbidden for held/retired exact identity")
    elif method == "POST" and path == "/api/v2/upload/task":
        if any(is_held(nm_id=v.get("nmID")) for v in (body or {}).get("data", [])):
            raise ValueError("WB price mutation forbidden for held/retired exact identity")
