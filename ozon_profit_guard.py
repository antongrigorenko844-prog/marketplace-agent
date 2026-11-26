"""Exact-SKU isolation from legacy Ozon price writers. No marketplace writes here."""
import json
from pathlib import Path

POLICY = Path(__file__).resolve().parent / "data" / "ozon_profit_policy.json"


def managed_ids(path=None):
    # Invalid config stops the legacy mutation rather than releasing protected SKUs.
    data = json.loads(Path(path or POLICY).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or data.get("schema_version") != 1:
        raise ValueError("Invalid Ozon profit policy")
    ids = data.get("managed_skus")
    if (not isinstance(ids, list)
            or any(not isinstance(x, str) or not x or x != x.strip() for x in ids)
            or len(ids) != len(set(ids))):
        raise ValueError("Invalid exact managed SKU list")
    if ids and (data.get("approved") is not True or not data.get("approval_reference")):
        raise ValueError("Managed SKUs require documented owner approval")
    return frozenset(ids)


def unmanaged_ids(ids, path=None):
    managed = managed_ids(path)
    return [sku for sku in ids if sku not in managed]


def reject_legacy_write(items, path=None):
    managed = managed_ids(path)
    overlap = {x.get("offer_id") for x in items if x.get("offer_id") in managed}
    # product_id-only requests cannot establish exact identity when managed SKUs exist.
    if managed and any(not isinstance(x.get("offer_id"), str) or not x["offer_id"] for x in items):
        raise ValueError("Exact offer_id required for Ozon price update")
    if overlap:
        raise ValueError("Legacy Ozon price writer blocked: " + ", ".join(sorted(overlap)))
