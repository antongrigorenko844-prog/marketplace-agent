import json
from pathlib import Path
import shutil
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

import avito_stock as policy
from avito_stock_client import LISTINGS, StockError


PLATE = "0am325066ae"
ZERO = "0am325025x"


class FakeAvito:
    def __init__(self):
        self.remote = dict.fromkeys(LISTINGS,15)
        self.remote[ZERO] = 0
        self.issues = {}
        self.sent = []
        self.reads = []
        self.after_put = None

    def link_issues(self):
        return self.issues

    def verify_listing(self, sku):
        if sku in self.issues:
            raise StockError("reverse_link_conflicts")

    def read(self, sku):
        self.reads.append(sku)
        return self.remote[sku]

    def set_quantity(self, sku, qty):
        self.sent.append((sku,qty))
        self.remote[sku] = qty
        if self.after_put:
            self.after_put(sku,qty)


class StockPolicyTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.data = root/"data"
        self.data.mkdir()
        self.plan = root/"plan.json"
        sqlite3.connect(self.data/policy.DB).close()
        shutil.copyfile(Path(policy.__file__).parent/"data"/policy.STATE, self.data/policy.STATE)
        self.api = FakeAvito()
        self.qty = dict(self.api.remote)
        self.write_stock()
        w = Workbook()
        w.active.append(["Kit",None,"Part","Per kit"])
        for kit in sorted(policy.CONFIRMED_KITS):
            w.active.append([kit,None,"component",1])
        w.save(self.data/policy.BOM)
        w.close()

    def write_stock(self, extra=(), omitted=None):
        wb = Workbook()
        wb.active.append(["Артикул (offer_id)",None,None,None,None,None,None,"Кол-во к продаже"])
        for sku,qty in [*self.qty.items(),("component",15),*extra]:
            if sku != omitted:
                wb.active.append([sku,None,None,None,None,None,None,qty])
        wb.save(self.data/policy.BOOK)
        wb.close()

    def prepare(self, path=None):
        return policy.prepare(self.data,path or self.plan,self.api)

    def apply(self, path=None):
        return policy.apply(self.data,path or self.plan,self.api)

    def test_noop_sources_persist_byte_for_byte_and_no_put(self):
        orig = (self.data/policy.STATE).read_bytes()
        self.assertFalse(self.prepare()["state_changed"])
        self.assertTrue(self.apply()["all_exact"])
        self.assertEqual(self.api.sent,[])
        self.assertEqual((self.data/policy.STATE).read_bytes(), orig)

    def test_preserves_marketplace_drop_on_unchanged_or_decreasing_source(self):
        self.api.remote[PLATE] = 11
        self.qty[PLATE] = 14
        self.write_stock()
        self.prepare()
        self.assertEqual(self.apply()["items"][PLATE]["status"],"lower_available_preserved")
        self.assertEqual(self.api.sent,[])
        self.assertEqual(policy.load_state(self.data)["source_targets"][PLATE],14)

    def test_real_restock_can_increase_only_from_confirmed_previous_source(self):
        self.qty[PLATE] = 19
        self.write_stock()
        self.prepare()
        self.assertEqual(policy.load_state(self.data)["source_targets"][PLATE],19)
        self.assertEqual(self.apply()["items"][PLATE]["status"],"confirmed")
        self.assertEqual(self.api.sent,[(PLATE,19)])
        with self.assertRaisesRegex(policy.PolicyError,"consumed"):
            self.apply()

    def test_new_reservation_between_prepare_and_apply_or_unproven_baseline_loses_increase(self):
        self.qty[PLATE] = 20
        self.write_stock()
        self.prepare()
        self.api.remote[PLATE] = 14
        self.assertFalse(self.apply()["all_exact"])
        self.assertEqual(self.api.sent,[])
        self.api.remote[PLATE] = 15
        other = self.plan.with_name("next.json")
        self.prepare(other)
        self.assertEqual(self.apply(other)["items"][PLATE]["status"],"lower_available_preserved")
        self.assertEqual(self.api.sent,[])

    def test_observed_mismatch_at_prepare_cannot_become_authority_to_raise(self):
        self.api.remote[PLATE] = 14
        self.qty[PLATE] = 20
        self.write_stock()
        self.prepare()
        self.api.remote[PLATE] = 15
        self.assertEqual(self.apply()["items"][PLATE]["status"],"lower_available_preserved")
        self.assertEqual(self.api.sent,[])

    def test_zero_unlimited_and_excess_are_capped(self):
        self.api.remote[ZERO] = None
        self.api.remote[PLATE] = 32
        self.prepare()
        self.assertTrue(self.apply()["all_exact"])
        self.assertEqual(self.api.sent,[(ZERO,0),(PLATE,15)])

    def test_timeout_after_applied_write_reads_only_and_stops(self):
        self.qty[PLATE] = 18
        self.write_stock()
        self.prepare()
        def timeout(sku,qty):
            self.api.remote[sku] = qty-1  # reservation before readback
            raise StockError("transport_error")
        self.api.after_put = timeout
        r = self.apply()
        self.assertFalse(r["ok"])
        self.assertEqual(r["items"][PLATE]["status"],"write_unconfirmed")
        self.assertEqual(r["items"][PLATE]["readback"],17)
        self.assertEqual(self.api.reads.count(PLATE),3)
        self.assertEqual(self.api.sent,[(PLATE,18)])
        self.api.after_put = None
        other=self.plan.with_name("next")
        self.prepare(other)
        self.assertEqual(self.apply(other)["items"][PLATE]["status"],"lower_available_preserved")
        self.assertEqual(self.api.sent,[(PLATE,18)])

    def test_missing_receipt_is_unconfirmed_even_if_readback_exact(self):
        self.api.remote[ZERO] = None
        self.prepare()
        def reject(*_):
            raise StockError("receipt_identity_unconfirmed")
        self.api.after_put = reject
        r = self.apply()
        self.assertFalse(r["ok"])
        self.assertEqual(r["items"][ZERO]["readback"],0)
        self.assertEqual(len(self.api.sent),1)

    def test_new_commit_or_changed_state_after_prepare_blocks_all(self):
        self.api.remote[ZERO] = None
        self.prepare()
        self.qty[PLATE] -= 1
        self.write_stock()
        with self.assertRaisesRegex(policy.PolicyError,"changed"):
            self.apply()
        self.assertEqual(self.api.sent,[])

    def test_plan_cannot_forge_growth_if_watermark_is_unchanged(self):
        self.api.remote[PLATE] = 10
        self.prepare()
        plan=json.loads(self.plan.read_bytes())
        plan["body"]["previous"][PLATE]=10
        self.plan.write_text(json.dumps(plan))
        with self.assertRaisesRegex(policy.PolicyError,"growth_proof"):
            self.apply()
        self.assertEqual(self.api.sent,[])

    def test_stale_remapped_and_ambiguous_inputs_cannot_write(self):
        self.prepare()
        self.api.issues[PLATE] = "historical_link_changed"
        self.api.remote[PLATE] = None
        r=self.apply()
        self.assertFalse(r["ok"])
        self.assertEqual(r["items"][PLATE]["status"],"listing_check_failed")
        self.assertEqual(self.api.sent,[])
        self.assertFalse(self.prepare(self.plan.with_name("later"))["preflight_complete"])

    def test_plan_expiry_blocks_mutation(self):
        self.api.remote[ZERO] = None
        self.prepare()
        with patch("avito_stock.time.time",return_value=10**12):
            r=self.apply()
        self.assertFalse(r["ok"])
        self.assertEqual(self.api.sent,[])

    def test_operational_stock_strict_schema_and_kit_mismatch(self):
        for qty in (None,True,-1,1.5,1000000,"formula","=1+3"):
            self.qty[PLATE] = qty
            self.write_stock()
            with self.subTest(qty=qty), self.assertRaises((ValueError,RuntimeError,policy.PolicyError)):
                self.prepare()
        self.qty[PLATE] = 15
        self.write_stock(extra=[(PLATE,15)])
        with self.assertRaisesRegex(policy.PolicyError,"ambiguous"):
            self.prepare()
        self.write_stock(omitted=PLATE)
        with self.assertRaisesRegex(policy.PolicyError,"missing"):
            self.prepare()
        self.qty[next(iter(policy.CONFIRMED_KITS))] = 14
        self.write_stock()
        with self.assertRaisesRegex(policy.PolicyError,"kit_stock_mismatch"):
            self.prepare()
        self.assertEqual(self.api.sent,[])

    def test_pending_stock_transaction_blocks_prepare(self):
        with sqlite3.connect(self.data/policy.DB) as db:
            db.execute("CREATE TABLE stock_pending(id INTEGER PRIMARY KEY)")
            db.execute("INSERT INTO stock_pending VALUES(1)")
        with self.assertRaisesRegex(policy.PolicyError,"pending"):
            self.prepare()
        self.assertFalse(self.plan.exists())

    def test_preflight_isolates_one_inactive_listing_without_advancing_its_growth(self):
        self.api.issues[PLATE]="listing_not_active"
        self.qty[PLATE]=19
        self.api.remote[ZERO]=None
        self.write_stock()
        self.assertFalse(self.prepare()["preflight_complete"])
        self.assertEqual(policy.load_state(self.data)["source_targets"][PLATE],15)
        r=self.apply()
        self.assertFalse(r["ok"])
        self.assertFalse(r["items"][PLATE]["put_attempted"])
        self.assertEqual(r["items"][ZERO]["status"],"confirmed")
        self.assertEqual(self.api.sent,[(ZERO,0)])
        self.api.issues.clear()
        nxt=self.plan.with_name("next")
        self.prepare(nxt)
        self.assertTrue(self.apply(nxt)["ok"])
        self.assertEqual(self.api.sent,[(ZERO,0),(PLATE,19)])

    def test_apply_transient_before_any_set_can_issue_one_new_restock_proof(self):
        self.qty[PLATE]=19
        self.write_stock()
        self.prepare()
        self.api.issues[PLATE]="temporary independently of mapping"
        r=self.apply()
        self.assertTrue(r["state_changed"])
        self.assertFalse(r["items"][PLATE]["put_attempted"])
        self.assertEqual(policy.load_state(self.data)["unsent_growth"],{PLATE:15})
        self.api.issues.clear()
        nxt=self.plan.with_name("next")
        self.prepare(nxt)
        self.assertEqual(policy.load_state(self.data)["unsent_growth"],{})
        self.assertTrue(self.apply(nxt)["all_exact"])
        self.assertEqual(self.api.sent,[(PLATE,19)])

    def test_ambiguous_put_never_rearms_and_does_not_block_other_skus(self):
        self.qty[PLATE]=19
        self.api.remote[ZERO]=None
        self.write_stock()
        self.prepare()
        def one_failure(sku,qty):
            if sku==ZERO:
                raise StockError("transport_error")
        self.api.after_put=one_failure
        r=self.apply()
        self.assertFalse(r["ok"])
        self.assertTrue(r["items"][ZERO]["put_attempted"])
        self.assertEqual(r["items"][ZERO]["status"],"write_unconfirmed")
        self.assertEqual(r["items"][PLATE]["status"],"confirmed")
        self.assertFalse(r["state_changed"])
        self.assertEqual(self.api.sent,[(ZERO,0),(PLATE,19)])

    def test_readback_failure_keeps_attempt_outcome_and_reconciles_independent_listing(self):
        self.qty[PLATE]=19
        self.api.remote[ZERO]=None
        self.write_stock()
        self.prepare()
        read=self.api.read
        def flaky(sku):
            if sku==ZERO and self.api.sent:
                raise StockError("http_503")
            return read(sku)
        self.api.read=flaky
        r=self.apply()
        self.assertFalse(r["ok"])
        self.assertEqual(r["items"][ZERO]["readback_error"],"http_503")
        self.assertEqual(r["items"][ZERO]["status"],"write_unconfirmed")
        self.assertTrue(r["items"][ZERO]["put_attempted"])
        self.assertEqual(self.api.sent,[(ZERO,0),(PLATE,19)])

    def test_changed_workbook_mid_publication_halts_stale_plan(self):
        self.api.remote[ZERO]=None
        self.api.remote[PLATE]=None
        self.prepare()
        def change_source(sku,qty):
            self.qty[PLATE]=9
            self.write_stock()
        self.api.after_put=change_source
        r=self.apply()
        self.assertFalse(r["ok"])
        self.assertEqual(r["items"][PLATE]["status"],"source_changed")
        self.assertEqual(r["items"][PLATE]["put_attempted"],False)
        self.assertEqual(self.api.sent,[(ZERO,0)])
