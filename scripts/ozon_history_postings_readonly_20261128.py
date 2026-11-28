"""Read-only posting aggregates, using existing Ozon client; no customer data saved."""
from collections import Counter, defaultdict
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from pathlib import Path
import json
import time
import ozon_client

START = "2026-10-19"
END = "2026-11-27"
SINCE = "2026-10-18T21:00:00Z"
TO = "2026-11-27T21:00:00Z"
MSK = ZoneInfo("Europe/Moscow")
OUT = Path("data/ozon_history_20261128/postings_daily_evidence.json")
doc = {"generated_at_utc": datetime.now(timezone.utc).isoformat(),
       "mode": "read_only", "marketplace_mutations": 0,
       "date_from": START, "date_to": END, "time_zone": "Europe/Moscow",
       "date_basis": "in_process_at, else created_at; units in all returned statuses",
       "notes": ["Postings are shipments, not unique orders.",
                 "Units include cancellations and uncompleted deliveries, item quantities are counted.",
                 "This is NOT a card visits report and does not replace analytics revenue.",
                 "No customer data or posting identifiers are retained in the report."],
       "schemes": {}}
OUT.parent.mkdir(parents=True, exist_ok=True)

def save():
    OUT.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")

def collect(kind, path):
    state = {"endpoint": path, "complete": False, "pages": 0, "received_postings": 0,
             "outside_date_window": 0, "missing_dates": 0, "duplicate_postings": 0,
             "by_day": {}, "sku_daily_units": {}}
    doc["schemes"][kind] = state
    cursor = ""
    offset = 0
    seen_cursors = set()
    seen_postings = set()
    days = defaultdict(lambda: {"postings": 0, "units": 0, "cancelled_postings": 0,
                               "cancelled_units": 0, "statuses": Counter()})
    skus = defaultdict(lambda: defaultdict(int))
    for page in range(30):
        fields = {"analytics_data": False, "financial_data": False}
        if kind == "FBO":
            fields["legal_info"] = False
        payload = {"filter": {"since": SINCE, "to": TO}, "limit": 100, "with": fields}
        if cursor:
            payload["cursor"] = cursor
        elif offset:
            payload["offset"] = offset
        try:
            result = ozon_client._post(path, payload, retries=2)
            result = result.get("result", result)
            rows = result if isinstance(result, list) else result.get("postings", [])
            has_next = len(rows) >= 100 if isinstance(result, list) else bool(result.get("has_next"))
            next_cursor = "" if isinstance(result, list) else result.get("cursor", "")
            if not isinstance(rows, list):
                raise ValueError("Postings response is not an array")
            state["pages"] += 1
            state["received_postings"] += len(rows)
            for item in rows:
                ident = str(item.get("posting_number") or "")
                if ident and ident in seen_postings:
                    state["duplicate_postings"] += 1
                    continue
                if ident:
                    seen_postings.add(ident)
                raw_date = item.get("in_process_at") or item.get("created_at")
                if not raw_date:
                    state["missing_dates"] += 1
                    continue
                date = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                if date.tzinfo is None:
                    date = date.replace(tzinfo=timezone.utc)
                day = date.astimezone(MSK).date().isoformat()
                if not START <= day <= END:
                    state["outside_date_window"] += 1
                    continue
                products = item.get("products", [])
                units = sum(max(0, int(p.get("quantity") or 0)) for p in products)
                status = str(item.get("status") or "unknown")
                days[day]["postings"] += 1
                days[day]["units"] += units
                days[day]["statuses"][status] += 1
                if status.lower() in ("cancelled", "canceled"):
                    days[day]["cancelled_postings"] += 1
                    days[day]["cancelled_units"] += units
                for product in products:
                    sku = str(product.get("offer_id") or "?")
                    skus[sku][day] += max(0, int(product.get("quantity") or 0))
            if not has_next:
                state["complete"] = True
                break
            if not rows or (next_cursor and next_cursor in seen_cursors):
                raise ValueError("Pagination cannot continue despite has_next")
            if next_cursor:
                seen_cursors.add(next_cursor)
                cursor = next_cursor
            else:
                offset += len(rows)
            time.sleep(0.4)
        except Exception as exc:
            state["error"] = str(exc)[:900]
            break
    if not state["complete"] and "error" not in state:
        state["error"] = "Pagination safety limit reached"
    state["by_day"] = {day: dict(values, statuses=dict(values["statuses"]))
                       for day, values in sorted(days.items())}
    state["sku_daily_units"] = {sku: dict(sorted(dd.items())) for sku, dd in sorted(skus.items())}
    state["checked_at_utc"] = datetime.now(timezone.utc).isoformat()
    save()
    print(kind + ": complete=" + str(state["complete"]) +
          ", postings=" + str(state["received_postings"]), flush=True)

save()
collect("FBS", "/v4/posting/fbs/list")
collect("FBO", "/v3/posting/fbo/list")
doc["completed_at_utc"] = datetime.now(timezone.utc).isoformat()
save()
