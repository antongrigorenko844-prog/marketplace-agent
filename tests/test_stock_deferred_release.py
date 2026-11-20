import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

from stock_sync import _ensure_db
from stock_transaction import StockSyncError, order_batch


class DeferredFboReleaseTest(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.db = Path(tmp.name) / "orders.db"
        self.catalog = Path(tmp.name) / "stock.xlsx"
        wb = Workbook()
        for sku, qty in [("sku", "qty"), ("KIT", 5), ("Part", 10)]:
            wb.active.append([sku, None, None, None, None, None, None, qty])
        wb.save(self.catalog)
        wb.close()
        bom = patch("kits.load_bom", return_value={"KIT": [("Part", 2)]})
        bom.start()
        self.addCleanup(bom.stop)
        self.run_line(cancelled=False)
        self.change("UPDATE processed_orders SET status='cancelled_pending_count'")
        self.change("""INSERT INTO stock_deferred_release
                    SELECT p.source,p.order_key,p.offer_id,p.decremented,r.components
                    FROM processed_orders p JOIN stock_order_recipe r USING(source,order_key)""")
        self.booked = self.catalog.read_bytes()

    def run_line(self, cancelled=True, sku="KIT", qty=1, key="example"):
        with order_batch(self.db, self.catalog, _ensure_db) as b:
            b.reconcile("ozon_fbo", key, sku, qty, cancelled)
        return b.summary["deltas"]

    def change(self, statement):
        with closing(sqlite3.connect(self.db)) as conn, conn:
            conn.execute(statement)

    def rows(self, table):
        with closing(sqlite3.connect(self.db)) as conn:
            return conn.execute("SELECT * FROM " + table).fetchall()

    def test_replay_keeps_original_ledger_recipe_and_entire_workbook(self):
        names = ("processed_orders", "stock_order_recipe", "stock_deferred_release")
        before = [self.rows(name) for name in names]
        self.assertEqual(self.run_line(), {})
        self.assertEqual(self.run_line(), {})
        self.assertEqual(before, [self.rows(name) for name in names])
        self.assertEqual(self.catalog.read_bytes(), self.booked)

    def test_reactivation_quantity_or_sku_change_roll_back_other_movements(self):
        for cancelled, sku, qty in ((False,"KIT",1), (True,"KIT",2), (True,"Part",1), (True,"KIT",True)):
            with self.subTest(cancelled=cancelled, sku=sku, qty=qty):
                with self.assertRaises(StockSyncError), order_batch(self.db, self.catalog, _ensure_db) as b:
                    b.reconcile("ozon_fbo", "other", "Part", 1, False)
                    b.reconcile("ozon_fbo", "example", sku, qty, cancelled, legacy_sku="KIT")
                self.assertEqual(len(self.rows("processed_orders")),1)
                self.assertEqual(self.catalog.read_bytes(), self.booked)

    def test_missing_receipt_cannot_release_or_accept_active_replay(self):
        self.change("DELETE FROM stock_deferred_release")
        for flag in (True,False):
            with self.assertRaises(StockSyncError):
                self.run_line(cancelled=flag)
        self.assertEqual(self.catalog.read_bytes(), self.booked)

    def test_orphan_receipt_cannot_recreate_or_release(self):
        self.change("UPDATE processed_orders SET status='active'")
        with self.assertRaises(StockSyncError):
            self.run_line()
        self.change("DELETE FROM processed_orders")
        with self.assertRaises(StockSyncError):
            self.run_line(cancelled=False)
        self.assertEqual(self.catalog.read_bytes(), self.booked)

    def test_receipt_recipe_quantity_sku_must_match(self):
        for update in ("quantity=2", "offer_id='Part'", "components='{}'"):
            original = self.rows("stock_deferred_release")[0]
            self.change("UPDATE stock_deferred_release SET " + update)
            with self.assertRaises(StockSyncError):
                self.run_line()
            with closing(sqlite3.connect(self.db)) as conn, conn:
                conn.execute("INSERT OR REPLACE INTO stock_deferred_release VALUES(?,?,?,?,?)",original)
        self.change("DELETE FROM stock_order_recipe")
        with self.assertRaises(StockSyncError):
            self.run_line()
        self.assertEqual(self.catalog.read_bytes(), self.booked)

    def test_matching_corrupt_recipes_are_rejected(self):
        for malformed in ("null", "[]", "{}", "invalid", '{"Part":true}', '{"Part":0}'):
            with closing(sqlite3.connect(self.db)) as conn, conn:
                for table in ("stock_order_recipe","stock_deferred_release"):
                    conn.execute("UPDATE "+table+" SET components=?",(malformed,))
            with self.assertRaises(StockSyncError):
                self.run_line()
            self.assertEqual(self.catalog.read_bytes(), self.booked)

    def test_old_db_recreates_empty_table_and_keeps_legacy_rules(self):
        self.change("DROP TABLE stock_deferred_release")
        self.change("UPDATE processed_orders SET status='active'")
        self.change("DELETE FROM stock_order_recipe")
        with self.assertRaises(StockSyncError):
            self.run_line()
        self.assertEqual(self.rows("stock_deferred_release"),[])
        self.assertEqual(self.run_line(False, "Part", 2, "new"), {"Part": -2})
        self.assertEqual(self.run_line(True, "Part", 2, "new"), {"Part": 2})
