"""Prospective Avito Delivery reservations. CLI defaults to a private simulation.

Activation verifies the one approved historical order. Routine accounting is
part of the existing shared inventory transaction. API requests are read-only.
"""

import argparse
import calendar
from collections import Counter
from contextlib import closing, contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
from tempfile import TemporaryDirectory

from avito_order_preview import CUT, GROUP, Invalid, avito_pages, exact_map, read_snapshot, require, timestamp
from stock_sync import _ensure_db
from stock_transaction import StockSyncError, order_batch


SOURCE = "avito_delivery"
SHIPPING = ("ready_to_ship", "in_transit", "delivered")
BASELINE_END = datetime(2026, 11, 20, 18, 15, 26, tzinfo=timezone.utc)
APPROVED_SKU = "0am325091f-set"


def stamp(t):
    return t.astimezone(timezone.utc).isoformat(timespec="microseconds")


def clock():
    return datetime.now(timezone.utc)


def ledger_key():
    own = os.environ.get("AVITO_LEDGER_KEY")
    if own:
        require(len(own.encode()) >= 32, "ledger_key_required")
        return own.encode()
    oauth = os.environ.get("AVITO_CLIENT_SECRET")
    require(oauth, "ledger_key_required")
    return hmac.digest(oauth.encode(), b"LEOPARTS/avito-delivery/local-inventory/v1", "sha256")


def tag(secret, *parts):
    require(isinstance(secret, bytes) and len(secret) >= 32, "ledger_key_required")
    message = json.dumps(parts, ensure_ascii=False, separators=(",", ":")).encode()
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def setup(conn):
    _ensure_db(conn)
    conn.execute("""CREATE TABLE IF NOT EXISTS avito_cutover(
        id INTEGER PRIMARY KEY CHECK(id=1), cutoff TEXT NOT NULL, key_check TEXT NOT NULL, last_scan TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS avito_observed(
        ref TEXT PRIMARY KEY, created TEXT NOT NULL, updated TEXT NOT NULL, status TEXT NOT NULL,
        basket TEXT NOT NULL, binding TEXT, phase TEXT NOT NULL, last_seen TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS avito_receipt(
        receipt TEXT PRIMARY KEY, contents TEXT NOT NULL)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS avito_start_order(
        id INTEGER PRIMARY KEY CHECK(id=1), ref TEXT NOT NULL)""")


def checked_settings(conn, secret):
    row = conn.execute("SELECT cutoff,key_check,last_scan FROM avito_cutover WHERE id=1").fetchone()
    require(row is not None, "activation_required")
    require(row[1] == tag(secret, "key-check"), "ledger_key_mismatch")
    return row[0], row[2]


@contextmanager
def inventory(data, preview):
    db, catalog, kits = data / "orders_seen.db", data / "ozon_catalog.xlsx", data / "kits.xlsx"
    if not preview:
        with order_batch(db, catalog, setup, kits_path=kits) as batch:
            yield batch
        return
    # The ledger snapshot stays in RAM. Recovery, staging and projection use a
    # disposable copy of the product workbook; the original files are read-only.
    def copy_ledger(conn):
        if db.exists():
            with closing(sqlite3.connect(db.resolve().as_uri()+"?mode=ro", uri=True)) as original:
                original.backup(conn)
        setup(conn)

    with TemporaryDirectory() as temp:
        copy = Path(temp) / catalog.name
        shutil.copyfile(catalog, copy)
        with order_batch(":memory:", copy, copy_ledger, kits_path=kits) as batch:
            yield batch


def observe(batch, key, state, mapping, cutoff, end, secret, approved=None):
    created, updated, status, basket = state
    ref = tag(secret, "order", key)
    if stamp(created) < cutoff and ref != approved:
        return "review_cutover_gap" if approved and created >= BASELINE_END else "review_pre_cutover"
    cart = tag(secret, "basket", basket)
    rows = [(tag(secret, "line", key, item), mapping.get(item), qty) for item, qty in basket]
    binding = tag(secret, "bindings", rows) if all(sku in batch.cells for _, sku, _ in rows) else None
    # Unknown status text never enters persistent state or diagnostics.
    code = status if status in GROUP else tag(secret, "unknown-status", status)
    prior = batch.conn.execute("""SELECT created,updated,status,basket,binding,phase
                                 FROM avito_observed WHERE ref=?""", (ref,)).fetchone()
    phase = "waiting"
    reason = "waiting_confirmation"
    if prior:
        old_created, old_updated, old_status, old_cart, old_binding, phase = prior
        if stamp(updated) < old_updated:
            batch.conn.execute("UPDATE avito_observed SET last_seen=? WHERE ref=?", (end, ref))
            return "stale_observation"
        if (old_created != stamp(created) or old_cart != cart or old_binding != binding
                or (stamp(updated) == old_updated and old_status != code)
                or (old_status in SHIPPING and status in ("on_confirmation", *SHIPPING)
                    and (status == "on_confirmation" or SHIPPING.index(status) < SHIPPING.index(old_status)))):
            phase, reason = "hold", "review_changed_order"
        elif phase == "hold":
            reason = "review_held_order"
    if phase != "hold":
        if not binding:
            phase, reason = "hold", "review_unmapped_order"
        elif status in SHIPPING:
            if phase == "booked":
                reason = "already_booked"
            else:
                for line, sku, qty in rows:
                    batch.reconcile(SOURCE, line, sku, qty, False)
                phase, reason = "booked", "newly_booked"
        elif status != "on_confirmation":
            # Snapshot history cannot prove an unshipped cancellation or receipt.
            phase, reason = "hold", "review_" + (status if status in GROUP else "unknown_status")
    batch.conn.execute("INSERT OR REPLACE INTO avito_observed VALUES(?,?,?,?,?,?,?,?)",
                       (ref, stamp(created), stamp(updated), code, cart, binding, phase, end))
    return reason


def confirmed_snapshot(fetch, now):
    start = (now - timedelta(days=180) + timedelta(minutes=5)).replace(microsecond=0)
    states, _, reasons, _, _, exhausted = read_snapshot(fetch, start, now)
    require(exhausted and not reasons, "incomplete_or_unstable_pages")
    # Page numbers have no snapshot token. A second independent traversal also
    # catches holes caused by insertion/deletion between two otherwise valid pages.
    again, _, reasons, _, _, exhausted = read_snapshot(fetch, start, now)
    require(exhausted and not reasons and again == states, "incomplete_or_unstable_pages")
    return states


def reconcile_snapshot(batch, states, mapping, secret, now):
    counts = Counter()
    quantities = Counter()
    cutoff, last_scan = checked_settings(batch.conn, secret)
    require(stamp(now) >= last_scan, "stale_scan")
    previous = timestamp(last_scan)
    months = (now.year - previous.year) * 12 + now.month - previous.month
    anniversary = previous.replace(year=now.year, month=now.month,
                                   day=min(previous.day, calendar.monthrange(now.year, now.month)[1]))
    require(months < 3 or (months == 3 and now < anniversary), "retention_gap_requires_review")
    pin = batch.conn.execute("SELECT ref FROM avito_start_order WHERE id=1").fetchone()
    for key, state in states.items():
        reason = observe(batch, key, state, mapping, cutoff, stamp(now), secret, pin[0] if pin else None)
        counts[reason] += 1
        quantities[reason] += sum(qty for _, qty in state[3])
    counts["review_missing_open"] = batch.conn.execute(
        """SELECT count(*) FROM avito_observed WHERE phase IN ('waiting','booked')
           AND status NOT IN ('delivered','closed') AND last_seen<>?""", (stamp(now),)).fetchone()[0]
    batch.conn.execute("UPDATE avito_cutover SET last_scan=? WHERE id=1", (stamp(now),))
    return {"cutoff_utc": cutoff, "orders": dict(counts), "observed_units_by_decision": dict(quantities)}


def synchronize(data, fetch, secret, now, *, preview=True):
    states = confirmed_snapshot(fetch, now)
    mapping = exact_map(data)
    with inventory(data, preview) as batch:
        if preview and not batch.conn.execute("SELECT 1 FROM avito_cutover").fetchone():
            batch.conn.execute("INSERT INTO avito_cutover VALUES(1,?,?,?)",
                               (stamp(now), tag(secret, "key-check"), stamp(now)))
        result = reconcile_snapshot(batch, states, mapping, secret, now)
    return dict(result, mode="preview" if preview else "apply")


def activate_one(data, *, preview=True, fetch=None):
    secret = ledger_key()
    with inventory(data, preview) as batch:
        if batch.conn.execute("SELECT 1 FROM avito_cutover WHERE id=1").fetchone():
            cutoff, _ = checked_settings(batch.conn, secret)
            pin = batch.conn.execute("""SELECT 1 FROM avito_start_order a JOIN avito_observed o
                                       ON a.ref=o.ref WHERE a.id=1""").fetchone()
            require(pin, "activation_state_requires_review")
            return {"activation": "already_recorded", "cutoff_utc": cutoff}
        require(not batch.conn.execute("SELECT 1 FROM processed_orders WHERE source=? LIMIT 1", (SOURCE,)).fetchone(),
                "existing_avito_ledger_requires_review")
        cutoff = clock()
        require(cutoff > BASELINE_END, "invalid_activation_time")
        # A closed order from the baseline cohort could disappear after three months.
        require(cutoff < CUT.replace(year=2027, month=2), "approved_baseline_no_longer_verifiable")
        states = confirmed_snapshot(fetch if fetch is not None else avito_pages(), cutoff)
        mapping = exact_map(data)
        candidates = [(key, state) for key, state in states.items() if CUT <= state[0] < BASELINE_END]
        require(len(candidates) == 1, "approved_window_not_unique")
        key, (_, _, status, basket) = candidates[0]
        require(status == "ready_to_ship" and len(basket) == 1 and basket[0][1] == 1
                and mapping.get(basket[0][0]) == APPROVED_SKU, "approved_order_does_not_match")
        bridge = [state for state in states.values() if state[0] >= BASELINE_END]
        if bridge:
            units = sum(n for state in bridge for _, n in state[3])
            raise Invalid(f"cutover_gap_requires_review_orders_{len(bridge)}_units_{units}")
        require(APPROVED_SKU in batch.cells, "approved_item_missing_in_inventory")
        ref = tag(secret, "order", key)
        batch.conn.execute("INSERT INTO avito_start_order VALUES(1,?)", (ref,))
        batch.conn.execute("INSERT INTO avito_cutover VALUES(1,?,?,?)",
                           (stamp(cutoff), tag(secret, "key-check"), stamp(cutoff)))
        result = reconcile_snapshot(batch, states, mapping, secret, cutoff)
        require(result["observed_units_by_decision"].get("newly_booked") == 1, "approved_unit_not_booked")
    return dict(result, activation="would_activate" if preview else "activated")


def sync_if_enabled(batch, data):
    exists = batch.conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='avito_cutover'").fetchone()
    if not exists or not batch.conn.execute("SELECT 1 FROM avito_cutover WHERE id=1").fetchone():
        return None
    secret = ledger_key()
    checked_settings(batch.conn, secret)
    require(batch.conn.execute("""SELECT 1 FROM avito_start_order a JOIN avito_observed o
                                ON a.ref=o.ref WHERE a.id=1""").fetchone(), "activation_state_requires_review")
    now = clock()
    try:
        states = confirmed_snapshot(avito_pages(), now)
        return reconcile_snapshot(batch, states, exact_map(data), secret, now)
    except (Invalid, StockSyncError):
        raise
    except Exception:
        raise Invalid("avito_read_or_storage_error") from None


def receive(data, secret, *, order_id, receipt_id, received, physically_received_resellable, preview=True):
    """One confirmed warehouse receipt; received maps listing IDs to piece counts."""
    require(physically_received_resellable is True and isinstance(order_id, str) and order_id.strip() == order_id and order_id
            and isinstance(receipt_id, str) and receipt_id.strip() == receipt_id and receipt_id and isinstance(received, dict)
            and received and all(isinstance(k, str) and k.isascii() and k.isdecimal()
                                 and type(q) is int and q > 0 for k,q in received.items()), "receipt_evidence_required")
    lines = sorted((tag(secret, "line", order_id, i), n) for i, n in received.items())
    receipt, contents = tag(secret, "receipt", receipt_id), tag(secret, "receipt-contents", order_id, lines)
    with inventory(data, preview) as batch:
        checked_settings(batch.conn, secret)
        prior = batch.conn.execute("SELECT contents FROM avito_receipt WHERE receipt=?", (receipt,)).fetchone()
        if prior:
            require(prior[0] == contents, "receipt_changed")
            return {"receipt": "already_recorded"}
        state = batch.conn.execute("SELECT status FROM avito_observed WHERE ref=?",
                                   (tag(secret, "order", order_id),)).fetchone()
        require(state and state[0] in ("on_return", "delivered", "in_dispute", "canceled", "closed"),
                "receipt_order_state_requires_review")
        for line, n in lines:
            booked = batch.conn.execute("SELECT offer_id,decremented FROM processed_orders WHERE source=? AND order_key=?",
                                        (SOURCE, line)).fetchone()
            require(booked and n <= booked[1], "receipt_exceeds_booked")
            left = booked[1] - n
            batch.reconcile(SOURCE, line, booked[0], left or n, left == 0)
        batch.conn.execute("INSERT INTO avito_receipt VALUES(?,?)", (receipt, contents))
    return {"receipt": "would_restore" if preview else "restored", "units": sum(received.values())}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mode", choices=["preview", "preview-activation", "activate-one"], default="preview")
    p.add_argument("--data-dir", required=True, type=Path)
    p.add_argument("--read-avito", action="store_true", help="Allow read-only Avito API calls")
    args = p.parse_args()
    now = clock()
    require(args.read_avito, "explicit_read_avito_required")
    if args.mode in ("preview-activation", "activate-one"):
        sys.exit(activation_command(args.data_dir, preview=args.mode == "preview-activation"))
    result = synchronize(args.data_dir, avito_pages(), ledger_key(), now)
    print(json.dumps(result, sort_keys=True))


def activation_command(data, *, preview):
    try:
        result = activate_one(data, preview=preview)
    except Invalid as exc:
        print(json.dumps({"error": str(exc)}))
        return 2
    except Exception:
        print('{"error":"stock_or_storage_error"}')
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        main()
    except Invalid as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(2)
    except Exception:
        print('{"error":"stock_or_storage_error"}')
        sys.exit(2)
