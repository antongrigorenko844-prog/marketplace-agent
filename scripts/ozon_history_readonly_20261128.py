"""Read-only Ozon historical analytics for the owner's traffic investigation."""
import datetime as dt
import json
import pathlib
import time
import ozon_client

OUT = pathlib.Path("data/ozon_history_20261128/api_daily_evidence.json")
OUT.parent.mkdir(parents=True, exist_ok=True)
report = {
    "created_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    "marketplace_mutations": 0,
    "endpoint": "/v1/analytics/data",
    "notes": [
        "ordered_units counts ordered goods, not paid or delivered orders.",
        "revenue is the analytics metric, not net profit or cash received.",
        "All date bounds are recorded as sent to Ozon. Current date Nov 28 is excluded.",
        "Traffic is never inferred from order counts. API errors are retained."
    ],
    "requests": []
}
last_call = None

def save():
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

def query(label, metrics, start, end, dimensions):
    global last_call
    entry = {"label": label, "date_from": start, "date_to": end,
             "metrics": metrics, "dimensions": dimensions, "pages": [], "ok": False}
    report["requests"].append(entry)
    save()
    for page in range(3):
        if last_call is not None:
            while time.monotonic() - last_call < 63:
                time.sleep(min(20, 63 - (time.monotonic() - last_call)))
        payload = {"date_from": start, "date_to": end, "metrics": metrics,
                   "dimension": dimensions, "filters": [], "sort": [],
                   "limit": 1000, "offset": page * 1000}
        last_call = time.monotonic()
        try:
            result = ozon_client._post("/v1/analytics/data", payload, retries=1)
            rows = (result.get("result") or {}).get("data")
            if not isinstance(rows, list):
                raise ValueError("Missing result.data array")
            entry["pages"].append({"offset": page * 1000, "rows": rows,
                                   "totals": (result.get("result") or {}).get("totals")})
            print(label + ": " + str(len(rows)) + " rows", flush=True)
            if len(rows) < 1000:
                entry["ok"] = True
                break
        except Exception as exc:
            entry["error"] = str(exc)[:900]
            print(label + ": " + entry["error"], flush=True)
            break
        finally:
            entry["checked_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
            save()
    if not entry["ok"] and "error" not in entry:
        entry["error"] = "Pagination safety limit reached; evidence incomplete."
        save()
    return entry

query("daily_orders_20261019_20261127", ["ordered_units", "revenue"],
      "2026-10-19", "2026-11-27", ["day"])
query("weekly_sku_orders_20261019_20261127", ["ordered_units", "revenue"],
      "2026-10-19", "2026-11-27", ["sku", "week"])
traffic = query("daily_card_visits_20261019_20261127", ["hits_view_pdp"],
                "2026-10-19", "2026-11-27", ["day"])
if traffic["ok"]:
    query("daily_card_visits_20260817_20260920", ["hits_view_pdp"],
          "2026-08-17", "2026-09-20", ["day"])
report["completed_at_utc"] = dt.datetime.now(dt.timezone.utc).isoformat()
save()
