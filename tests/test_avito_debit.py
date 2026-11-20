import json
import os
import sys
from types import SimpleNamespace
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from avito_debit import APPROVED_SKU, BASELINE_END, activate_one, ledger_key, receive, setup, stamp, synchronize, tag
from stock_sync import sync_all_orders
from avito_order_preview import Invalid, exact_map
from stock_transaction import StockSyncError


class AvitoDebitTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.d = Path(folder.name)
        self.db = self.d / "orders_seen.db"
        self.book = self.d / "ozon_catalog.xlsx"
        self.now = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
        self.cut = self.now - timedelta(minutes=10)
        self.secret = b"synthetic-secret-never-used-on-platform"
        self.skus = ["Part", "PART", "KIT"]
        self.xlsx(self.book, "Товары", [["sku", None, None, None, None, None, None, "qty"]]
                  + [[s, None, None, None, None, None, None, n] for s, n in zip(self.skus, (8, 4, 4))])
        self.xlsx(self.d / "master_control.xlsx", "Товары", [["Артикул (offer_id)"]]+[[s] for s in self.skus])
        self.xlsx(self.d / "kits.xlsx", "Kits", [["kit", "", "part", "n"], ["KIT", "", "Part", 2]])
        self.links = self.d / "avito_links.json"
        self.links.write_text('{"KIT":303}')
        self.autoload = self.d / "avito_autoload.json"
        self.map([("Part", 101), ("PART", 202), ("KIT-av", 303)])

    def xlsx(self, path, name, rows):
        w = Workbook()
        w.active.title = name
        for r in rows:
            w.active.append(r)
        w.save(path)
        w.close()

    def map(self, pairs):
        self.autoload.write_text(json.dumps({"items":[{"ad_id":s,"avito_id":n} for s,n in pairs]}))

    def start(self):
        # An isolated active ledger for the transition tests; the actual
        # owner-approved activation path is exercised separately below.
        with closing(sqlite3.connect(self.db)) as conn, conn:
            setup(conn)
            conn.execute("INSERT INTO avito_cutover VALUES(1,?,?,?)",
                         (stamp(self.cut),tag(self.secret,"key-check"),stamp(self.cut)))

    def order(self, status="ready_to_ship", lines=((101, 1),), *, age=3, update=1, key="private-order-123456"):
        return {"id": key, "status": status, "createdAt": (self.now-timedelta(minutes=age)).isoformat(),
                "updatedAt": (self.now-timedelta(minutes=update)).isoformat(), "buyer":{"name":"private-buyer"},
                "items":[{"avitoId":str(n),"count":qty,"title":"private-title"} for n,qty in lines]}

    def sync(self, *rows, preview=False, key=None, now=None):
        return synchronize(self.d, lambda *_:{"orders":list(rows),"hasMore":False},
                           key or self.secret, now or self.now, preview=preview)

    def levels(self):
        with closing(load_workbook(self.book, read_only=True)) as w:
            return [r[7] for r in w.active.iter_rows(min_row=2, values_only=True)]

    def receipt(self, n, *, receipt="private-warehouse-receipt", preview=False, verified=True, item="303"):
        return receive(self.d, self.secret, order_id="private-order-123456", receipt_id=receipt,
                       received={item:n}, physically_received_resellable=verified, preview=preview)

    def test_default_preview_no_activation_or_disk_writes(self):
        before = self.book.read_bytes()
        report = synchronize(self.d, lambda *_: {"orders":[self.order()],"hasMore":False},
                             self.secret,self.now)
        self.assertEqual(report["orders"]["review_pre_cutover"],1)
        self.assertFalse(self.db.exists())
        self.assertEqual(self.book.read_bytes(),before)

    def test_key_change_rejects_accounting_before_debit(self):
        self.start()
        with self.assertRaisesRegex(Invalid, "ledger_key_mismatch"):
            self.sync(self.order(),key=b"different"*8)
        self.assertEqual(self.levels(),[8,4,4])

    def test_no_past_orders_even_if_updated_after_cutover(self):
        self.start()
        for status in ("ready_to_ship","in_transit","delivered","closed","on_return"):
            r=self.sync(self.order(status,age=11))
            self.assertEqual(r["orders"]["review_pre_cutover"],1)
        self.assertEqual(self.levels(),[8,4,4])

    def test_preview_and_repeats_preserve_identifiers_and_case(self):
        self.start()
        row = self.order(lines=((101,1),(101,2),(202,1)),age=10)
        before = (self.book.read_bytes(),self.db.read_bytes())
        self.assertEqual(self.sync(row,preview=True)["orders"]["newly_booked"],1)
        self.assertEqual(before,(self.book.read_bytes(),self.db.read_bytes()))
        self.sync(row)
        self.assertEqual(self.sync(row)["orders"]["already_booked"],1)
        self.assertEqual(self.levels(),[5,3,2])
        with closing(sqlite3.connect(self.db)) as conn:
            dump="\n".join(conn.iterdump())
            self.assertNotIn("private-",dump)
            self.assertEqual(conn.execute("SELECT count(*) FROM processed_orders").fetchone()[0],2)

    def test_confirmation_waits_and_shipping_states_debit_once(self):
        self.start()
        self.sync(self.order("on_confirmation",age=5,update=4))
        self.assertEqual(self.levels(),[8,4,4])
        for status,update in [("ready_to_ship",3),("in_transit",2),("delivered",1)]:
            self.sync(self.order(status,age=5,update=update))
        self.assertEqual(self.levels(),[7,4,3])
        self.assertEqual(self.sync(self.order("in_transit",age=5,update=2))["orders"]["stale_observation"],1)

    def test_incomplete_pages_and_conflict_cannot_move_any_stock(self):
        self.start()
        for page in (lambda n,_: {"orders":[self.order()], "hasMore": n==1},
                     lambda n,_: {"orders":[self.order("closed" if n==2 else "in_transit")],"hasMore":n==1},
                     lambda n,_: {"orders":[self.order()],"hasMore":True} if n==1 else {},
                     lambda n,_: {"orders":[self.order(key=str(n))],"hasMore":n<50},
                     lambda *_: {"orders":[self.order(update=0)],"hasMore":False},
                     lambda *_: {"orders":[self.order(lines=((101,False),))],"hasMore":False}):
            with self.assertRaisesRegex(Invalid,"incomplete"):
                synchronize(self.d,page,self.secret,self.now,preview=False)
        self.assertEqual(self.levels(),[8,4,4])

    def test_unknown_item_between_mapped_lines_quarantines_whole_order(self):
        self.start()
        rows=((101,1),(707,2))
        self.assertEqual(self.sync(self.order(lines=rows))["orders"]["review_unmapped_order"],1)
        self.map([("Part",101),("PART",707)])
        self.sync(self.order(lines=rows))
        self.assertEqual(self.levels(),[8,4,4])

    def test_distinct_valid_page_sets_block_entire_transaction(self):
        self.start()
        replies = iter([{"orders":[self.order()],"hasMore":False},{"orders":[],"hasMore":False}])
        with self.assertRaisesRegex(Invalid,"incomplete"):
            synchronize(self.d,lambda *_:next(replies),self.secret,self.now,preview=False)
        self.assertEqual(self.levels(),[8,4,4])

    def test_concurrent_replay_single_debit(self):
        self.start()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.sync,self.order()) for _ in range(2)]
            results = [f.result()["orders"] for f in futures]
        self.assertEqual(sum(r.get("newly_booked",0) for r in results),1)
        self.assertEqual(self.levels(),[7,4,3])

    def test_map_conflicts_suffix_and_case(self):
        self.map([("part",101),("Part",202),("PART",202),("KIT-av",909),("KIT-av",303)])
        self.assertEqual(exact_map(self.d),{"303":"KIT"})

    def test_change_of_cart_binding_and_equal_timestamp_status_holds(self):
        self.start()
        self.sync(self.order())
        self.assertEqual(self.sync(self.order("delivered"))["orders"]["review_changed_order"],1)
        self.sync(self.order(lines=((101,3),),update=0.5))
        self.map([("PART",101)])
        self.sync(self.order(update=0.25))
        self.assertEqual(self.levels(),[7,4,3])

    def test_first_seen_closed_or_return_or_unknown_requires_reconciliation(self):
        self.start()
        for s in ("closed","canceled","on_return","in_dispute","unknown-private-status"):
            result = self.sync(self.order(s,key=s))
            self.assertNotIn("unknown-private",str(result))
            self.assertNotIn("newly_booked",result["orders"])
        self.assertEqual(self.levels(),[8,4,4])

    def test_physical_receipt_uses_original_recipe_is_capped_and_idempotent(self):
        self.start()
        self.sync(self.order(lines=((303,2),),age=4,update=2))
        self.assertEqual(self.levels(),[4,4,2])
        with self.assertRaisesRegex(Invalid,"order_state"):
            self.receipt(1)
        self.sync(self.order("canceled",lines=((303,2),),age=4,update=1))
        self.assertEqual(self.levels(),[4,4,2])
        with self.assertRaises(Invalid):
            self.receipt(1,verified=False)
        self.xlsx(self.d / "kits.xlsx","Kits",[["kit","","part","n"],["KIT","","PART",1]])
        self.assertEqual(self.receipt(1,preview=True)["receipt"],"would_restore")
        self.assertEqual(self.levels(),[4,4,2])
        self.receipt(1)
        self.assertEqual(self.levels(),[6,4,4])
        self.assertEqual(self.receipt(1)["receipt"],"already_recorded")
        with self.assertRaisesRegex(Invalid,"receipt_changed"):
            self.receipt(2)
        with self.assertRaisesRegex(Invalid,"exceeds"):
            self.receipt(2,receipt="another")
        self.receipt(1,receipt="last")
        self.sync(self.order("canceled",lines=((303,2),),age=4,update=1))
        self.assertEqual(self.levels(),[8,4,4])

    def test_shortage_rolls_back_all_orders_and_crash_recovers(self):
        self.start()
        with self.assertRaises(StockSyncError):
            self.sync(self.order(key="one"),self.order(key="second",lines=((202,999),)))
        self.assertEqual(self.levels(),[8,4,4])
        with patch("stock_transaction.os.replace",side_effect=OSError), self.assertRaises(OSError):
            self.sync(self.order())
        self.assertEqual(self.levels(),[8,4,4])
        self.assertEqual(self.sync(self.order())["orders"]["already_booked"],1)
        self.assertEqual(self.levels(),[7,4,3])

    def test_missing_open_and_retention_gap_never_restore(self):
        self.start()
        self.sync(self.order("in_transit"))
        self.assertEqual(self.sync(now=self.now+timedelta(days=1))["orders"]["review_missing_open"],1)
        with self.assertRaisesRegex(Invalid,"retention_gap"):
            self.sync(now=self.now+timedelta(days=120))
        self.assertEqual(self.levels(),[7,4,3])

    def activation_fixture(self):
        self.now = BASELINE_END + timedelta(minutes=15)
        self.xlsx(self.book, "Товары", [["sku", None, None, None, None, None, None, "qty"]] +
                  [[s, None, None, None, None, None, None, 4] for s in (APPROVED_SKU, "Part")])
        self.xlsx(self.d/"master_control.xlsx", "Товары", [["Артикул (offer_id)"],[APPROVED_SKU],["Part"]])
        self.xlsx(self.d/"kits.xlsx", "Kits", [["kit", "", "part", "n"]])
        self.links.write_text("{}")
        self.map([(APPROVED_SKU,101),("Part",202)])
        self.candidate = self.order(age=20,update=2)
        env = patch.dict(os.environ, {"AVITO_CLIENT_SECRET":"synthetic-OAuth", "AVITO_LEDGER_KEY":""})
        env.start()
        self.addCleanup(env.stop)

    def activation(self, *rows, preview=False):
        with patch("avito_debit.clock", return_value=self.now):
            return activate_one(self.d,preview=preview,fetch=lambda *_: {"orders":list(rows),"hasMore":False})

    def scheduled(self, response, postings=()):
        ozon = SimpleNamespace(get_fbs_orders_since=lambda *_:postings, get_fbo_orders_since=lambda *_:[])
        market = SimpleNamespace(is_configured=lambda:False)
        with patch.dict(sys.modules, {"ozon_client":ozon,"yandex_market_client":market}), \
                patch("stock_sync._sync_wb", return_value=0), patch("kits.load_bom",return_value={}), \
                patch("avito_debit.clock",return_value=self.now), patch("avito_debit.avito_pages") as api:
            api.return_value = lambda *_:response
            summary = sync_all_orders(catalog_path=str(self.book),db_path=str(self.db))
            return summary, api.called

    def test_single_approved_order_is_activated_and_never_double_debited(self):
        self.activation_fixture()
        old = self.order(key="private-old",age=120,update=1)
        self.assertEqual(self.activation(old,self.candidate,preview=True)["activation"],"would_activate")
        self.assertFalse(self.db.exists())
        self.assertEqual(self.levels(),[4,4])
        self.assertEqual(self.activation(old,self.candidate)["activation"],"activated")
        self.assertEqual(self.activation()["activation"],"already_recorded")
        self.assertEqual(self.levels(),[3,4])
        self.now += timedelta(minutes=15)
        new = self.order(key="private-new",age=3,lines=((202,1),))
        late = self.order(key="private-gap",age=25,lines=((202,2),))
        result, called = self.scheduled({"orders":[old,self.candidate,new,late],"hasMore":False})
        self.assertTrue(called)
        counts = result["avito_delivery"]["orders"]
        for name in ("already_booked","review_pre_cutover","review_cutover_gap","newly_booked"):
            self.assertEqual(counts[name],1)
        self.assertEqual(self.levels(),[3,3])
        self.scheduled({"orders":[old,self.candidate,new,late],"hasMore":False})
        self.assertEqual(self.levels(),[3,3])
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertNotIn("private-", "\n".join(conn.iterdump()))

    def test_approval_stops_on_status_sku_quantity_or_any_other_in_baseline(self):
        self.activation_fixture()
        other = self.order("closed",key="private-other",age=22,lines=((202,1),))
        for rows in ([],[self.candidate,other],[{**self.candidate,"status":"in_transit"}],
                     [self.order(age=20,lines=((202,1),))],[self.order(age=20,lines=((101,2),))]):
            with self.assertRaises(Invalid):
                self.activation(*rows)
            self.assertEqual(self.levels(),[4,4])
        with self.assertRaisesRegex(Invalid,"gap_requires_review_orders_1_units_2"):
            self.activation(self.candidate,self.order(key="private-gap",age=3,lines=((202,2),)))
        with closing(sqlite3.connect(self.db)) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM avito_cutover").fetchone(),(0,))

    def test_schedule_does_not_contact_avito_before_activation_and_rolls_back_all_on_error(self):
        self.activation_fixture()
        posting = {"posting_number":"local-demo","status":"active","products":[{"offer_id":"Part","quantity":1}]}
        summary, called = self.scheduled(None)
        self.assertFalse(called)
        self.assertNotIn("avito_delivery",summary)
        self.activation(self.candidate)
        self.now += timedelta(seconds=2)
        with self.assertRaisesRegex(Invalid,"unstable_pages"):
            self.scheduled({},[posting])
        self.assertEqual(self.levels(),[3,4])
        result,_ = self.scheduled({"orders":[self.candidate],"hasMore":False},[posting])
        self.assertEqual(result["deltas"],{"Part":-1})
        self.assertEqual(self.levels(),[3,3])

    def test_credential_fallback_domain_separation_and_pinned_rotation(self):
        self.activation_fixture()
        key = ledger_key()
        self.assertEqual(len(key),32)
        self.assertNotEqual(key,os.environ["AVITO_CLIENT_SECRET"].encode())
        self.activation(self.candidate)
        with patch.dict(os.environ,{"AVITO_CLIENT_SECRET":"rotated"}):
            with self.assertRaisesRegex(Invalid,"ledger_key_mismatch"):
                self.scheduled({"orders":[],"hasMore":False})
        with patch.dict(os.environ,{"AVITO_LEDGER_KEY":"k"*32}):
            self.assertEqual(ledger_key(),b"k"*32)
        self.assertEqual(self.levels(),[3,4])
