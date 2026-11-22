"""Read-only live reconciliation of Ozon order changes versus existing stock ledger.

Do not alter historic recipes, inventory, catalog, orders or marketplace stock.
Checks whether a cancellation/quantity correction is blocked by missing historical
order-component evidence. Hash order keys in reports to avoid public order IDs.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
import sys

ROOT = Path(__file__).resolve().parent
DB = ROOT / "data/orders_seen.db"
OUT = ROOT / "data/stock_preflight_report.json"
CANCELLED = {"cancelled", "not_accepted"}


def safe_key(source, order_key):
    return sha256(f"{source}|{order_key}".encode("utf-8")).hexdigest()[:14]


def _rows(posting):
    number = str(posting.get("posting_number") or "").strip()
    status = str(posting.get("status") or "").strip().lower()
    if not number or not status:
        raise ValueError("Ozon returned missing order number/status")
    quantities = Counter()
    for item in posting.get("products") or []:
        sku = str(item.get("offer_id") or "").strip()
        qty = item.get("quantity")
        if not sku or type(qty) is not int or qty < 1:
            raise ValueError("Ozon returned invalid SKU or quantity")
        quantities[sku] += qty
    if not quantities:
        raise ValueError("Ozon returned empty posting")
    for sku, qty in quantities.items():
        yield number + ":" + sku, sku, 0 if status in CANCELLED else qty, status


def compare_orders(conn, source, postings):
    """Pure reconciliation classifier over a read-only SQLite connection."""
    counts = Counter()
    blocked = []
    reviewed = set()
    for posting in postings:
        for key, sku, target, status in _rows(posting):
            marker = (source, key)
            if marker in reviewed:
                counts["duplicate_api_order_keys"] += 1
                continue
            reviewed.add(marker)
            old = conn.execute("""
                SELECT p.offer_id,p.decremented,p.status,r.components
                  FROM processed_orders p
                  LEFT JOIN stock_order_recipe r
                    ON r.source=p.source AND r.order_key=p.order_key
                 WHERE p.source=? AND p.order_key=?""", (source, key)).fetchone()
            counts["checked"] += 1
            if old is None:
                counts["new_or_unrecorded"] += 1
                continue
            prior_sku, previous, old_status, recipe_json = old
            if prior_sku != sku:
                reason = "sku_changed"
            elif previous == target:
                counts["unchanged"] += 1
                continue
            elif previous and not recipe_json:
                reason = "historical_recipe_missing"
            elif previous:
                try:
                    old_recipe = json.loads(recipe_json)
                    if not isinstance(old_recipe, dict) or not old_recipe or any(
                            not isinstance(component, str) or not component
                            or type(count) is not int or count < 1
                            for component, count in old_recipe.items()):
                        reason = "historical_recipe_invalid"
                    else:
                        counts["can_reconcile_from_saved_recipe"] += 1
                        continue
                except (TypeError, ValueError):
                    reason = "historical_recipe_invalid"
            else:
                counts["can_reconcile_no_previous_debit"] += 1
                continue
            counts["blocked_legacy"] += 1
            if len(blocked) < 40:
                blocked.append({
                    "order_ref_hash": safe_key(source, key),
                    "sku": sku,
                    "source": source,
                    "previous_units": previous,
                    "requested_units": target,
                    "reason": reason,
                    "prior_status": old_status,
                    "latest_status": status,
                })
    return {"counts": dict(counts), "blocked_examples": blocked}


def legacy_stats(conn):
    old = conn.execute("""
        SELECT p.source,COUNT(*) AS total,
               SUM(CASE WHEN p.decremented>0 AND r.components IS NULL THEN 1 ELSE 0 END)
                   AS active_without_recipe
          FROM processed_orders p
          LEFT JOIN stock_order_recipe r
            ON p.source=r.source AND p.order_key=r.order_key
         GROUP BY p.source ORDER BY p.source""").fetchall()
    pending = conn.execute("SELECT COUNT(*) FROM stock_pending").fetchone()[0]
    return {"by_source": [{"source": s, "orders": n, "active_missing_recipes": m} for s,n,m in old],
            "pending_projection": pending}


def run(live=False, days=30):
    report = {
        "read_only": True,
        "checked_utc": datetime.now(timezone.utc).isoformat(),
        "lookback_days": days,
        "inventory_writes": 0,
        "recipe_writes": 0,
        "status": "read_only_preflight",
        "live_orders_checked": False,
    }
    if not DB.exists():
        report["status"] = "unavailable_db"
        return report
    with sqlite3.connect(DB.resolve().as_uri()+"?mode=ro", uri=True, timeout=15) as conn:
        conn.execute("PRAGMA query_only=ON")
        try:
            report["ledger"] = legacy_stats(conn)
        except sqlite3.DatabaseError as exc:
            report["status"] = "unavailable_schema"
            report["reason"] = type(exc).__name__
            return report
        if not live:
            report["status"] = "historical_risk_not_live_verified"
            return report
        import ozon_client as oz
        now = datetime.now(timezone.utc)
        since = (now - timedelta(days=days)).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        to = now.isoformat(timespec="milliseconds").replace("+00:00", "Z")
        channels = {}
        for source, load in [
            ("ozon_fbs", oz.get_fbs_orders_since),
            ("ozon_fbo", oz.get_fbo_orders_since),
        ]:
            try:
                channels[source] = compare_orders(conn, source, load(since, to))
            except Exception as exc:
                channels[source] = {"error_type": type(exc).__name__, "status": "source_unavailable"}
        report["channels"] = channels
        report["live_orders_checked"] = True
        errors = any("error_type" in x for x in channels.values())
        pending = any(x.get("counts", {}).get("blocked_legacy") for x in channels.values())
        if errors:
            report["status"] = "incomplete_live_preflight"
        elif pending:
            report["status"] = "historical_corrections_need_evidence"
        else:
            report["status"] = "no_detected_live_conflicts_legacy_risk_remains"
        report["historical_recipes_auto_filled"] = 0
        report["ledger_modified"] = False
    return report


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--live", action="store_true")
    p.add_argument("--days", type=int, default=30)
    args = p.parse_args()
    if args.days not in range(1, 32):
        p.error("1–31 days only")
    report = run(args.live, args.days)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2)+"\n")
    print(json.dumps(report, ensure_ascii=False), flush=True)
    return 0 if report["status"] not in ("unavailable_db", "unavailable_schema") else 1


if __name__ == "__main__":
    sys.exit(main())
