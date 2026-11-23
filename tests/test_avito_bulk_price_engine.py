"""Pure, no-credentials tests for reusable Avito mass-price engine."""
from __future__ import annotations
import threading
import time
import unittest
from unittest.mock import Mock

from scripts.avito_bulk_price_engine import (
    AvitoPriceGuardError,
    PriceEdit,
    RateGate,
    preflight,
    assert_site_prices_unchanged,
    apply,
)


def listing(id, price, title=None):
    return {"id": id, "price": price, "status": "active",
            "title": title or f"Product {id}"}


class EngineTests(unittest.TestCase):
    def test_preflight_requires_exact_live_id_and_price(self):
        ads = {101: listing(101, 950)}
        edits = [PriceEdit(101, 950, 450, "Product 101")]
        self.assertEqual(preflight(edits, ads)[101]["status"], "pending")
        with self.assertRaises(AvitoPriceGuardError):
            preflight([PriceEdit(101, 1500, 450)], ads)
        with self.assertRaises(AvitoPriceGuardError):
            preflight([PriceEdit(102, 950, 450)], ads)
        with self.assertRaises(AvitoPriceGuardError):
            preflight([PriceEdit(101, 950, 450, "Different product")], ads)

    def test_guards_duplicate_id_and_invalid_prices(self):
        ads = {101: listing(101, 950)}
        with self.assertRaises(AvitoPriceGuardError):
            preflight([PriceEdit(101, 950, 450),
                       PriceEdit(101, 950, 450)], ads)
        for price in (0, -10, 9.5, True, 500001):
            with self.subTest(price=price), self.assertRaises(AvitoPriceGuardError):
                preflight([PriceEdit(101, 950, price)], ads)
        with self.assertRaises(AvitoPriceGuardError):
            preflight([], ads)

    def test_readonly_inspect_never_contacts_seller(self):
        ads = {101: listing(101, 950)}
        update = Mock(side_effect=AssertionError("Should not write"))
        readback = Mock(side_effect=AssertionError("Should not read back"))
        info = apply([PriceEdit(101, 950, 450)], ads,
                     owner_approved=False, mode="inspect",
                     update_fn=update, read_live_fn=readback)
        self.assertEqual(info["status"], "inspect_passed_no_writes")
        update.assert_not_called()
        readback.assert_not_called()

    def test_apply_requires_explicit_approval_and_limits_workers(self):
        ads = {101: listing(101, 950)}
        fn = Mock()
        read = Mock()
        with self.assertRaises(AvitoPriceGuardError):
            apply([PriceEdit(101, 950, 450)], ads,
                  owner_approved=False, mode="apply",
                  update_fn=fn, read_live_fn=read)
        with self.assertRaises(AvitoPriceGuardError):
            apply([PriceEdit(101, 950, 450)], ads,
                  owner_approved=True, mode="apply",
                  update_fn=fn, read_live_fn=read, workers=12)
        fn.assert_not_called()

    def test_throttled_concurrency_and_single_readback(self):
        ids = list(range(101, 113))
        prices = {x: listing(x, 950) for x in ids}
        state_lock = threading.Lock()
        activity = {"now": 0, "max": 0}
        recorded = []
        class NoWait:
            def acquire(self):
                return None
        def updater(aid, price):
            with state_lock:
                activity["now"] += 1
                activity["max"] = max(activity["max"], activity["now"])
            time.sleep(0.004)
            with state_lock:
                recorded.append((aid, price))
                prices[aid]["price"] = price
                activity["now"] -= 1
            return {"result":{"success": True}}
        read = Mock(side_effect=lambda: dict(prices))
        checkpoints = []
        outcome = apply([PriceEdit(x, 950, 450) for x in ids], dict(prices),
                        owner_approved=True, mode="apply",
                        update_fn=updater, read_live_fn=read,
                        workers=4, rate_gate=NoWait(),
                        checkpoint=lambda x: checkpoints.append(x["sent"]))
        self.assertEqual(outcome["status"], "verified_all_prices")
        self.assertEqual(outcome["verified"], len(ids))
        self.assertEqual(outcome["accepted"], len(ids))
        self.assertCountEqual(recorded, [(x, 450) for x in ids])
        self.assertLessEqual(activity["max"], 4)
        self.assertGreaterEqual(activity["max"], 2)
        self.assertEqual(read.call_count, 1)
        self.assertGreaterEqual(len(checkpoints), len(ids))

    def test_failure_after_server_accepted_is_resolved_by_readback(self):
        ads = {101: listing(101, 950), 102: listing(102, 950)}
        def request(aid, new):
            if aid == 101:
                ads[101]["price"] = new
                raise TimeoutError("response lost after write")
            return {"result":{"success":False}}
        class NoWait:
            def acquire(self): pass
        result=apply([PriceEdit(101, 950, 450),PriceEdit(102, 950, 450)],
                     dict(ads),owner_approved=True,mode="apply",
                     update_fn=request, read_live_fn=lambda:dict(ads),
                     workers=2,rate_gate=NoWait())
        self.assertEqual(result["status"], "partial_requires_review")
        self.assertTrue(result["details"]["101"]["verified"])
        self.assertEqual(result["details"]["101"]["error_type"], "TimeoutError")
        self.assertFalse(result["details"]["102"]["verified"])

    def test_idempotent_does_not_write_existing_target(self):
        ads={101:listing(101,450),102:listing(102,950)}
        calls=[]
        def send(aid,price):
            calls.append(aid)
            ads[aid]["price"]=price
            return {"result":{"success":True}}
        class NoWait:
            def acquire(self): pass
        r=apply([PriceEdit(101,950,450),PriceEdit(102,950,450)],dict(ads),
                owner_approved=True,mode="apply",update_fn=send,
                read_live_fn=lambda:dict(ads),rate_gate=NoWait())
        self.assertEqual(calls,[102])
        self.assertEqual(r["verified"],2)
        self.assertEqual(r["already_correct"],1)

    def test_site_isolation_includes_site_only_overrides(self):
        # Effective website prices: 37500 on Tilda even when source B Avito 39500.
        before={"0AM927769D":37500,"122390AK-AF":950}
        after={"0AM927769D":37500,"122390AK-AF":950}
        assert_site_prices_unchanged(before,after,list(before))
        with self.assertRaises(AvitoPriceGuardError):
            assert_site_prices_unchanged(
                before,{"0AM927769D":36500,"122390AK-AF":950},list(before))
        with self.assertRaises(AvitoPriceGuardError):
            assert_site_prices_unchanged(before,{},list(before))

    def test_rate_limiter_rejects_unsafe_rate(self):
        for rate in (0,121,-1):
            with self.assertRaises(ValueError):
                RateGate(rate)


if __name__ == "__main__":
    unittest.main()
