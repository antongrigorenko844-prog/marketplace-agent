"""Regression: legacy cancellations cannot release unknown historical kit parts."""
import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook
from stock_sync import _ensure_db, _sync_ozon
from stock_transaction import StockSyncError, order_batch


class LegacyCancellationQuarantineTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.db = self.root / "orders.db"
        self.xlsx = self.root / "catalog.xlsx"
        book = Workbook()
        sheet = book.active
        sheet.append(["SKU", None, None, None, None, None, None, "Stock"])
        for sku, n in [("A", 5), ("B", 5), ("KIT", 2)]:
            sheet.append([sku, None, None, None, None, None, None, n])
        book.save(self.xlsx)
        book.close()
        self.recipe = {"KIT": [("A", 2)]}
        p = patch("kits.load_bom", return_value=self.recipe)
        p.start()
        self.addCleanup(p.stop)

    def seed_legacy(self, source="ozon_fbs"):
        with closing(sqlite3.connect(self.db)) as con:
            _ensure_db(con)
            con.execute("INSERT INTO processed_orders VALUES(?,?,?,?,?,?)",
                        (source, "legacy:KIT", "KIT", 1, "active", "old"))
            con.commit()

    def levels(self):
        book = load_workbook(self.xlsx, read_only=True)
        try:
            return {row[0]:row[7] for row in book.active.iter_rows(min_row=2,values_only=True)}
        finally:
            book.close()

    def test_missing_historical_kit_recipe_never_releases_parts(self):
        self.seed_legacy()
        postings = [
            {"posting_number":"legacy","status":"cancelled",
             "products":[{"offer_id":"KIT","quantity":1}]},
            {"posting_number":"new","status":"awaiting_packaging",
             "products":[{"offer_id":"B","quantity":2}]},
        ]
        with order_batch(self.db,self.xlsx,_ensure_db) as batch:
            count = _sync_ozon(batch,postings,"ozon_fbs")
            summary = dict(batch.summary)
        self.assertEqual(count,2)
        self.assertEqual(self.levels(),{"A":5,"B":3,"KIT":2})
        deferred = summary["deferred_legacy_cancellations"]
        self.assertEqual(len(deferred),1)
        self.assertEqual(deferred[0]["sku"],"KIT")
        self.assertFalse(deferred[0]["stock_release_performed"])
        with closing(sqlite3.connect(self.db)) as con:
            row=con.execute("SELECT decremented,status FROM processed_orders WHERE source='ozon_fbs' AND order_key='legacy:KIT'").fetchone()
            self.assertEqual(row,(1,"active"))
        # Safe replay cannot debit the unrelated order again.
        with order_batch(self.db,self.xlsx,_ensure_db) as batch:
            _sync_ozon(batch,postings,"ozon_fbs")
        self.assertEqual(self.levels(),{"A":5,"B":3,"KIT":2})

    def test_active_quantity_increase_without_recipe_still_rolls_back(self):
        self.seed_legacy()
        postings = [
            {"posting_number":"legacy","status":"awaiting_packaging",
             "products":[{"offer_id":"KIT","quantity":2}]},
            {"posting_number":"new","status":"awaiting_packaging",
             "products":[{"offer_id":"B","quantity":1}]},
        ]
        with self.assertRaisesRegex(StockSyncError,"сверка"):
            with order_batch(self.db,self.xlsx,_ensure_db) as batch:
                _sync_ozon(batch,postings,"ozon_fbs")
        self.assertEqual(self.levels(),{"A":5,"B":5,"KIT":2})

    def test_fbo_legacy_cancellations_not_bypassed(self):
        self.seed_legacy("ozon_fbo")
        postings = [{"posting_number":"legacy","status":"cancelled",
                     "products":[{"offer_id":"KIT","quantity":1}]}]
        with self.assertRaisesRegex(StockSyncError,"сверка"):
            with order_batch(self.db,self.xlsx,_ensure_db) as batch:
                _sync_ozon(batch,postings,"ozon_fbo")
        self.assertEqual(self.levels(),{"A":5,"B":5,"KIT":2})

    def test_deferred_only_for_matching_active_sku(self):
        self.seed_legacy()
        # An incoming order line can match an old composite key but have a
        # conflicting stored SKU. This must fail closed, never be deferred.
        with closing(sqlite3.connect(self.db)) as con:
            con.execute("UPDATE processed_orders SET order_key='legacy:A' WHERE order_key='legacy:KIT'")
            con.commit()
        postings = [{"posting_number":"legacy","status":"cancelled",
                     "products":[{"offer_id":"A","quantity":1}]}]
        with self.assertRaisesRegex(StockSyncError,"сверка"):
            with order_batch(self.db,self.xlsx,_ensure_db) as batch:
                _sync_ozon(batch,postings,"ozon_fbs")
        self.assertEqual(self.levels(),{"A":5,"B":5,"KIT":2})


if __name__=="__main__":
    unittest.main()
