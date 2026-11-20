"""Read-only Ozon FBO reconciliation. Run from the project checkout with python -B."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing, redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import json
import logging
import os
from pathlib import Path
import sqlite3
import sys
from unittest.mock import patch


SOURCE = "ozon_fbo"
SKU = "0am325025"
CANCELLED = {"cancelled", "not_accepted"}
COMPONENTS = {SKU, "0am325066ae", "0am325025m"}


def digest(key):
    return sha256(key.encode("utf-8")).hexdigest()


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else None


def window(values, cutoff):
    dates = [timestamp(v) for v in values if v]
    if not dates or any(d is None for d in dates):
        return "unknown"
    positions = {"at_or_before" if d <= cutoff else "after" for d in dates}
    return positions.pop() if len(positions) == 1 else "unknown"


def old_rows(path):
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    with closing(sqlite3.connect(uri, uri=True)) as db:
        return {digest(key): qty for key, qty in db.execute(
            "SELECT order_key, decremented FROM processed_orders "
            "WHERE source=? AND offer_id=? AND status='active' AND decremented>0",
            (SOURCE, SKU))}


def fetch(now):
    import ozon_client
    if ozon_client.config.ozon_api_base.rstrip("/") != "https://api-seller.ozon.ru":
        raise ValueError("unexpected endpoint")
    original = ozon_client._post
    complete = False
    def checked(path, payload, retries=3):
        nonlocal complete
        if path != "/v3/posting/fbo/list":
            raise ValueError("unexpected method")
        response = original(path, payload, retries=retries)
        result = response.get("result", response) if isinstance(response, dict) else response
        if isinstance(result, list):
            complete = len(result) < 100
        elif isinstance(result, dict) and isinstance(result.get("postings"), list):
            more = result.get("has_next")
            if not isinstance(more, bool):
                raise ValueError("pagination unavailable")
            complete = not more
        else:
            raise ValueError("invalid posting list")
        return response
    iso = lambda d: d.isoformat(timespec="milliseconds").replace("+00:00", "Z")
    with patch.object(ozon_client, "_post", checked):
        postings = ozon_client.get_fbo_orders_since(iso(now - timedelta(days=30)), iso(now))
    if not complete:
        raise ValueError("incomplete posting list")
    return postings


def changes(postings, history, cutoff):
    seen = {}
    result = []
    for posting in postings:
        products = posting.get("products")
        if not isinstance(products, list):
            raise ValueError("invalid products")
        items = [i for i in products if str(i.get("offer_id") or "").strip() == SKU]
        if not items:
            continue
        number = str(posting.get("posting_number") or "").strip()
        status = str(posting.get("status") or "").strip().lower()
        if not number or not status:
            raise ValueError("missing required field")
        key = digest(number + ":" + SKU)
        if key not in history:
            continue
        quantities = [i.get("quantity") for i in items]
        if not all(type(n) is int and n>0 or isinstance(n,str) and n.isascii() and n.isdecimal() and int(n)>0 for n in quantities):
            raise ValueError("invalid quantity")
        qty = sum(map(int, quantities))
        cancelled = status in CANCELLED
        current = 0 if cancelled else qty
        if key in seen:
            if seen[key] != (status, qty):
                raise ValueError("inconsistent duplicates")
            continue
        seen[key] = (status, qty)
        old = history[key]
        if current == old:
            continue
        change = "cancellation" if cancelled else ("quantity_increase" if current>old else "quantity_decrease")
        if cancelled:
            cancellation = posting.get("cancellation") or {}
            explicit_times = [obj.get(field) for obj in (posting, cancellation)
                              for field in ("cancelled_at","canceled_at")]
            fallback = [posting.get("status_changed_at")]
            evidence = "cancellation_time" if any(explicit_times) else "status_change_time"
            event = window(explicit_times if any(explicit_times) else fallback, cutoff)
        else:
            # Product-level quantities may change without an order-status transition.
            explicit_times = [i.get("quantity_changed_at") for i in items]
            event = window(explicit_times, cutoff) if all(explicit_times) else "unknown"
            evidence = "quantity_change_time"
        created = window([posting.get("created_at")], cutoff)
        # A status or quantity revision cannot predate this posting's creation.
        if event == "unknown" and created == "after":
            event, evidence = "after", "creation_lower_bound"
        result.append({"key_sha256":key,"source":SOURCE,"offer_id":SKU,"type":change,
                       "status":status if cancelled else "active","old_qty":old,
                       "reported_qty":qty,"target_qty":current,"created_window":created,
                       "event_window":event,"evidence":evidence if event!="unknown" else "unavailable"})
    return result, len(seen)


def summarize(events, mapping=None):
    groups = defaultdict(lambda: {"count":0, "nominal_offer_units":0})
    for event in events:
        key = (event["type"],event["event_window"],event["evidence"],event["created_window"])
        group = groups[key]
        group["count"] += 1
        delta = event["old_qty"] - event["target_qty"]
        group["nominal_offer_units"] += delta
        if mapping is None:
            continue
        receipt = mapping[event["key_sha256"]]
        if receipt["units"] != event["old_qty"] or receipt["units"] != 1:
            raise ValueError("different baseline")
        if set(receipt["receipt"]) - COMPONENTS or set(receipt["nominal_recipe"]) - COMPONENTS:
            raise ValueError("unexpected component")
        for field in ("receipt","nominal_recipe"):
            group.setdefault("historic_"+field, Counter()).update(receipt[field])
        if event["target_qty"] == 0:
            group.setdefault("actual_original_stock_reversible", Counter()).update(receipt["receipt"])
        else:
            # A historical receipt proves only the old debit. Additional units were never applied.
            group["extra_units_without_applied_receipt"] = group.get("extra_units_without_applied_receipt",0) - delta
    return [{**dict(zip(("type","event_window","evidence","created_window"),key)),**group}
            for key,group in sorted(groups.items())]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo",type=Path,default=Path.cwd())
    p.add_argument("--db",type=Path)
    p.add_argument("--cutoff",help="UTC timestamp of the technical count checkpoint")
    p.add_argument("--private-output",type=Path,help="Exclusive mode-0600 file for subsequent encryption")
    p.add_argument("--history-map",type=Path,help="Local-only verified receipts")
    p.add_argument("--match-private",type=Path,help="Offline matching; performs no API call")
    args=p.parse_args()
    history_map = None
    if args.history_map:
        document=json.loads(args.history_map.read_text())
        if (document["source"],document["offer_id"]) != (SOURCE,SKU):
            raise ValueError("invalid map")
        history_map=document["rows"]
    if args.match_private:
        if history_map is None or args.private_output:
            raise ValueError("offline matching requires a map")
        private=json.loads(args.match_private.read_text())
        events=private["changes"]
        scope=private["scope"]
        if (scope["source"],scope["offer_id"])!=(SOURCE,SKU):
            raise ValueError("different cohort")
    else:
        cutoff=timestamp(args.cutoff)
        if cutoff is None:
            raise ValueError("cutoff missing timezone")
        sys.path.insert(0,str(args.repo.resolve()))
        history=old_rows(args.db or args.repo/"data/orders_seen.db")
        if len(history)!=20 or set(history.values())!={1}:
            raise ValueError("unexpected historical cohort")
        if history_map is not None and history_map.keys()!=history.keys():
            raise ValueError("history not aligned")
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            events, matched=changes(fetch(datetime.now(timezone.utc)),history,cutoff)
        scope={"source":SOURCE,"offer_id":SKU,"historical_active_rows":len(history),
               "observed_in_30d":matched,"not_observed_in_30d":len(history)-matched}
        if args.private_output:
            with os.fdopen(os.open(args.private_output, os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),"w") as dest:
                json.dump({"scope":scope,"changes":events},dest)
    print(json.dumps({"scope":scope,"changed":len(events),"groups":summarize(events,history_map)},sort_keys=True))


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    try:
        main()
    except Exception as exc:
        # Supplier response bodies and internal keys must never reach the job log.
        print(json.dumps({"result":"probe_failed","error_class":type(exc).__name__}))
        sys.exit(1)
