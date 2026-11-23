"""Read-only live reconciliation for old Avito ads and canonical SKU offers.

User authorized catalog unification, not automatic price/stock/media changes.
Never writes to seller. Inspect relative engagement before any AvitoId takeover.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta, datetime, timezone
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import avito_client as av

REGISTRY = ROOT / "data/avito_legacy_catalog_candidates_20261124.json"
AUDIT = ROOT / "data/avito_price_audit_20261124.json"
OUT = ROOT / "data/avito_legacy_selection_preflight_20261124.json"


def account_id():
    raw = av._request("GET", "/core/v1/accounts/self", retries=1)
    uid = raw.get("id") or (raw.get("result") or {}).get("id")
    if not uid:
        raise RuntimeError("No seller ID available: halt, no actions performed")
    return int(uid)


def stats_by_id(user_id, ids):
    # Stats may be unavailable to some Avito subscriptions: never assume zero
    # when the API returned an error, or a particular ad was omitted.
    result = {"source": "POST /stats/v1/accounts/{uid}/items", "error": None, "by_id": {}}
    if not ids:
        return result
    end = date.today()
    start = end - timedelta(days=269)
    for i in range(0, len(ids), 100):
        batch = ids[i:i + 100]
        body = {"dateFrom": start.isoformat(), "dateTo": end.isoformat(),
                "itemIds": batch,
                "fields": ["uniqViews", "uniqContacts", "uniqFavorites"],
                "periodGrouping": "month"}
        try:
            raw = av._request("POST", f"/stats/v1/accounts/{user_id}/items",
                              json=body, retries=1)
            items = (raw.get("result") or {}).get("items") or []
            for x in items:
                item_id = x.get("itemId")
                if item_id is None:
                    continue
                metrics = {"views": 0, "contacts": 0, "favorites": 0}
                for t in (x.get("stats") or []):
                    metrics["views"] += int(t.get("uniqViews") or 0)
                    metrics["contacts"] += int(t.get("uniqContacts") or 0)
                    metrics["favorites"] += int(t.get("uniqFavorites") or 0)
                result["by_id"][str(item_id)] = metrics
        except Exception as ex:
            result["error"] = type(ex).__name__ + ": " + str(ex)[:160]
            break
    result["period"] = {"from": start.isoformat(), "through": end.isoformat()}
    return result


def detail_capabilities(user_id, sample_ids):
    # Probe just a few known listings; the v1 detail API previously showed
    # status only. Do not persist images, URLs, contacts or free-text descriptions.
    out = []
    for aid in sample_ids:
        attempts = []
        for endpoint in (
            f"/core/v2/accounts/{user_id}/items/{aid}",
            f"/core/v1/accounts/{user_id}/items/{aid}/",
        ):
            try:
                raw = av._request("GET", endpoint, retries=1)
                item = raw.get("result") if isinstance(raw.get("result"), dict) else raw
                fields = [k for k in ("title", "description", "text", "price", "images",
                                      "photos", "status", "autoload_item_id") if k in item]
                attempts.append({"api": "v2" if "/v2/" in endpoint else "v1",
                                 "ok": True, "available_fields": fields,
                                 "has_composition_text": bool(item.get("description") or item.get("text"))})
            except Exception as ex:
                attempts.append({"api": "v2" if "/v2/" in endpoint else "v1",
                                 "ok": False, "error_type": type(ex).__name__})
        out.append({"id": aid, "attempts": attempts})
    return out


def main():
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    baseline = json.loads(AUDIT.read_text(encoding="utf-8"))
    assert registry["safety"]["apply_to_avito_links"] is False
    assert len(registry["entries"]) == 36
    by_sku = {p["sku"]: p for p in baseline["products"]}
    live = {str(a["id"]): a for a in av.list_all_items(per_page=100, max_pages=12)
            if a.get("id") is not None}
    entries = []
    needed = set()
    for old in registry["entries"]:
        ident = str(old["avito_id"])
        known = live.get(ident)
        candidates = []
        for raw in old.get("candidate_master_skus", []):
            sku = raw["sku"]
            canon = by_sku.get(sku, {})
            current_id = canon.get("mapped", {}).get("id")
            current = live.get(str(current_id)) if current_id else None
            candidates.append({
                "sku": sku, "master_product_exists": bool(canon.get("in_master")),
                "old_price": known.get("price") if known else None,
                "configured_price": canon.get("avito_cfg"),
                "main_ad_id": current_id,
                "main_ad_price_live": current.get("price") if current else None,
                "main_ad_status": current.get("status") if current else None,
                "feed_includes_sku": bool(canon.get("in_feed")),
                "price_matches_main": (
                    (known.get("price") == current.get("price"))
                    if known and current else None
                )
            })
            if current_id:
                needed.add(int(current_id))
        needed.add(int(old["avito_id"]))
        entries.append({
            "old_avito_id": old["avito_id"],
            "old_live_price": known.get("price") if known else None,
            "old_live_status": known.get("status") if known else "missing",
            "title": str(known.get("title") or old["live_title"])[:140] if known else old["live_title"],
            "registry_match_confidence": old["match_confidence"],
            "candidate_skus": candidates,
            "canonical_takeover_safe_without_change": False,
            "decision": "compare_stats_then_inspect_composition",
        })
    uid = account_id()
    st = stats_by_id(uid, sorted(needed))
    by_id = st["by_id"]
    for x in entries:
        x["old_269d_metrics"] = by_id.get(str(x["old_avito_id"]))
        for c in x["candidate_skus"]:
            c["main_269d_metrics"] = by_id.get(str(c["main_ad_id"])) if c["main_ad_id"] else None
        if (x["registry_match_confidence"] == "strong_title_or_part_number"
            and len(x["candidate_skus"]) == 1
            and x["old_live_status"] == "active"):
            c = x["candidate_skus"][0]
            if c["main_ad_status"] == "active":
                x["decision"] = "already_active_canonical; do_not_takeover_without_selection"
            else:
                x["decision"] = "possible_takeover; require_composition_price_and_feed_review"
        elif x["registry_match_confidence"] == "composition_unknown_owner_note":
            x["decision"] = "unknown_composition; do_not_merge_990_and_5890"
        else:
            x["decision"] = "variant_or_bom_ambiguous; not_safe_for_automatic_takeover"
    # Always no seller write. v2 availability is optional and cannot confirm
    # same physical parts if only title/price is exposed.
    details = detail_capabilities(uid, [4285108577, 4220729305, 4348814868])
    report = {
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "mode": "read_only_preflight",
        "seller_writes": 0, "feed_writes": 0, "catalog_price_writes": 0,
        "no_ad_was_taken_over": True,
        "counts": {
            "legacy_active": sum(x["old_live_status"] == "active" for x in entries),
            "legacy_total": len(entries),
            "strong_title_or_oem": sum(x["registry_match_confidence"] == "strong_title_or_part_number" for x in entries),
            "strong_with_existing_primary": sum(
                x["registry_match_confidence"] == "strong_title_or_part_number"
                and any(c["main_ad_status"] == "active" for c in x["candidate_skus"])
                for x in entries),
            "stats_old_available": sum(x["old_269d_metrics"] is not None for x in entries)
        },
        "stats_source": {"name": st["source"], "period": st.get("period"), "error": st["error"]},
        "detail_capabilities": details,
        "entries": entries
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("AVITO_LEGACY_SELECTION_PREFLIGHT",
          json.dumps({"counts": report["counts"], "stats_error": st["error"],
                      "detail_capabilities": details,
                      "decision_counts": dict(Counter(e["decision"] for e in entries))},
                     ensure_ascii=False), flush=True)


if __name__ == "__main__":
    main()
