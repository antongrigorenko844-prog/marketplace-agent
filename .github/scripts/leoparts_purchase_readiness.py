"""LEOPARTS paid-purchase readiness audit. Read-only; aggregate metadata only.

Never treats a Tilda form webhook or Metrica ecommerce purchase as evidence
of payment settlement. Never submits a form, makes a payment, or changes ads.
"""
from __future__ import annotations

import json
import os
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

COUNTER = 113839860
GOALS = [682257540, 675507028, 685988392, 675487735, 675506966, 686020680]
ROOT = Path("data/direct_optimization_20261120")
OUT = ROOT / "purchase_readiness_audit.json"
DB = Path("data/orders_seen.db")
MOSCOW = ZoneInfo("Europe/Moscow")


def now_utc():
    return datetime.now(timezone.utc).isoformat()


def persist(report):
    ROOT.mkdir(parents=True, exist_ok=True)
    temp = OUT.with_suffix(".json.tmp")
    temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temp.replace(OUT)


def metrika_get(path, params=None):
    if path not in (
        f"/management/v1/counter/{COUNTER}",
        f"/management/v1/counter/{COUNTER}/goals",
        "/stat/v1/data",
    ):
        raise RuntimeError("Metrica endpoint not allowed")
    token = os.getenv("YANDEX_METRIKA_TOKEN")
    if not token:
        raise RuntimeError("No read-only Metrica token")
    query = urllib.parse.urlencode(params or {}, safe=",")
    url = "https://api-metrika.yandex.net" + path + (("?" + query) if query else "")
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"Authorization": "OAuth " + token, "User-Agent": "LEOPARTS-purchase-readonly/1.0"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read(2000001)
    except urllib.error.HTTPError as e:
        status = e.code
        e.close()
        raise RuntimeError("Metrica HTTP " + str(status)) from None
    except Exception:
        raise RuntimeError("Metrica transport error") from None
    if len(raw) > 2000000:
        raise RuntimeError("Metrica response too large")
    return json.loads(raw)


def counter_options():
    payload = metrika_get(f"/management/v1/counter/{COUNTER}")
    c = payload.get("counter")
    if not isinstance(c, dict):
        raise RuntimeError("Counter missing")
    opts = c.get("code_options") or {}
    raw_enabled = opts.get("ecommerce")
    if raw_enabled not in (True, False, 0, 1):
        raise RuntimeError("Unexpected ecommerce config value")
    return {
        "counter_id_confirmed": c.get("id") == COUNTER,
        "status": str(c.get("status", ""))[:30],
        "ecommerce_enabled": bool(raw_enabled),
        "ecommerce_flag_api_type": type(raw_enabled).__name__,
        "note": "Server-side counter configuration only; a working storefront event requires separate validation.",
    }


def goal_definitions():
    goals = metrika_get(f"/management/v1/counter/{COUNTER}/goals").get("goals")
    if not isinstance(goals, list):
        raise RuntimeError("Goals not provided")
    found = []
    for x in goals:
        if x.get("id") in GOALS:
            found.append({
                "id": x.get("id"),
                "name": str(x.get("name", ""))[:100],
                "type": str(x.get("type", ""))[:60],
                "status": str(x.get("status", ""))[:40],
            })
    return {"found": found, "missing": sorted(set(GOALS) - {x["id"] for x in found})}


def goal_visits():
    end = datetime.now(MOSCOW).date()
    start = end - timedelta(days=29)
    ids = [str(x) for x in GOALS]
    params = {
        "ids": str(COUNTER),
        "date1": start.isoformat(),
        "date2": end.isoformat(),
        "metrics": ",".join(["ym:s:visits"] + ["ym:s:goal" + x + "visits" for x in ids]),
        "dimensions": "ym:s:date",
        "accuracy": "full",
        "limit": "40",
        "lang": "ru",
    }
    payload = metrika_get("/stat/v1/data", params)
    rows = payload.get("data")
    totals = payload.get("totals")
    if not isinstance(rows, list) or len(rows) > 40:
        raise RuntimeError("Unbounded visits report")
    if not isinstance(totals, list) or len(totals) != 1 + len(GOALS):
        raise RuntimeError("Invalid visits totals")
    result = {}
    for row in rows:
        dimensions = row.get("dimensions") or []
        metrics = row.get("metrics") or []
        if len(dimensions) != 1 or len(metrics) != len(totals):
            raise RuntimeError("Unexpected visits columns")
        day = str(dimensions[0].get("name", ""))
        if not (start.isoformat() <= day <= end.isoformat()):
            raise RuntimeError("Invalid report date")
        result[day] = {"visits": metrics[0], "goals": dict(zip(ids, metrics[1:]))}
    return {
        "from": start.isoformat(),
        "to": end.isoformat(),
        "totals": {"visits": totals[0], "goals": dict(zip(ids, totals[1:]))},
        "daily": result,
        "sampled": payload.get("sampled"),
        "note": "Metrica goal hits are not verified paid orders.",
    }


def tilda_form_dispatch_counts():
    # This database stores inventory-sync events, NOT processor payment statuses.
    if not DB.is_file():
        return {"status": "db_unavailable", "paid_orders_verified": False}
    conn = sqlite3.connect("file:" + str(DB.resolve()) + "?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT order_key, updated_at, status FROM processed_orders WHERE source = ?",
            ("tilda",),
        ).fetchall()
    finally:
        conn.close()
    keys = set()
    dates = []
    for key, updated, _ in rows:
        k = str(key or "")
        if k:
            keys.add(k.rsplit(":", 1)[0])
        if updated:
            dates.append(str(updated))
    return {
        "status": "ok",
        "tilda_inventory_sync_lines": len(rows),
        "distinct_form_order_keys": len(keys),
        "latest_form_order_event_utc": max(dates) if dates else None,
        "paid_orders_verified": False,
        "note": "Tilda webhook forms change inventory only; they do not certify that money was paid.",
    }


def main():
    report = {
        "status": "running",
        "mode": "strict_read_only",
        "started_utc": now_utc(),
        "counter_id": COUNTER,
        "sections": {},
        "errors": [],
        "metrika_writes": 0,
        "direct_writes": 0,
        "tilda_submissions": 0,
        "payments_attempted": 0,
        "paid_transaction_provider_verified": False,
        "interpretation": "Only the payment provider or Tilda paid-status record can prove a paid order.",
    }
    persist(report)
    operations = (
        ("metrika_counter_ecommerce_setting", counter_options),
        ("metrika_goal_definitions", goal_definitions),
        ("metrika_30d_goals", goal_visits),
        ("tilda_form_webhook_register", tilda_form_dispatch_counts),
    )
    for name, fn in operations:
        try:
            report["sections"][name] = {"status": "ok", "data": fn()}
        except Exception as exc:
            report["sections"][name] = {"status": "error", "message": str(exc)[:160]}
            report["errors"].append(name)
        persist(report)
    report["status"] = "complete" if not report["errors"] else "partial"
    report["finished_utc"] = now_utc()
    persist(report)
    print("PURCHASE_READINESS", report["status"], "errors:", report["errors"])
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
