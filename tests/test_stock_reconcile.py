import sqlite3
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import openpyxl

from stock_sync import _ensure_db, sync_all_orders, sync_tilda_order
from stock_transaction import StockSyncError, order_batch


class StockReconcileTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.xlsx = self.root / "catalog.xlsx"
        self.db = self.root / "orders.db"
        self.create_catalog({"A": 5, "B": 2, "KIT": 2})
        self.recipe = {"KIT": [("A", 2)]}
        bom = patch("kits.load_bom", return_value=self.recipe)
        bom.start()
        self.addCleanup(bom.stop)

    def create_catalog(self, stock):
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["SKU", None, None, None, None, None, None, "Stock"])
        for sku, level in stock.items():
            ws.append([sku, None, None, None, None, None, None, level])
        wb.save(self.xlsx)
        wb.close()

    def levels(self):
        with closing(openpyxl.load_workbook(self.xlsx, read_only=True)) as wb:
            return {row[0]: row[7] for row in wb.active.iter_rows(min_row=2, values_only=True)}

    def query(self, sql):
        with closing(sqlite3.connect(self.db)) as db:
            return db.execute(sql).fetchall()

    def tilda(self, items):
        return sync_tilda_order("demo", items, str(self.xlsx), str(self.db))

    def reconcile(self, qty, cancelled=False, sku="A"):
        with order_batch(self.db, self.xlsx, _ensure_db) as batch:
            batch.reconcile("ozon_fbs", "demo", sku, qty, cancelled)

    def test_tilda_aggregates_duplicates_retries_and_quantity_changes(self):
        rows = [{"offer_id": "A", "qty": 1}, {"offer_id": "A", "quantity": "2"}]
        self.assertEqual(self.tilda(rows)["deltas"], {"A": -3})
        self.assertEqual(self.tilda(rows)["deltas"], {})
        self.assertEqual(self.levels()["A"], 2)
        self.tilda([{"offer_id": "A", "qty": 2}])
        self.assertEqual(self.levels()["A"], 3)

    def test_invalid_tilda_quantities_do_not_consume_order(self):
        for qty in (0, -1, None, "", False, "bad", "1.9", 1.5):
            with self.subTest(qty=qty), self.assertRaises(StockSyncError):
                self.tilda([{"offer_id": "A", "qty": qty}])
        self.assertFalse(self.db.exists())
        self.assertEqual(self.levels()["A"], 5)

    def test_unknown_and_shortage_roll_back_whole_batch(self):
        for sku, qty in (("missing", 1), ("B", 3)):
            with self.subTest(sku=sku), self.assertRaises(StockSyncError):
                self.tilda([{"offer_id": "A", "qty": 1}, {"offer_id": sku, "qty": qty}])
            self.assertEqual(self.query("SELECT count(*) FROM processed_orders"), [(0,)])
            self.assertEqual(self.levels()["A"], 5)
        self.create_catalog({"A": 5, "B": 3, "KIT": 2, "missing": 1})
        self.tilda([{"offer_id": "A", "qty": 1}, {"offer_id": "missing", "qty": 1}])
        self.assertEqual(self.levels()["missing"], 0)

    def test_kit_cancellation_reverses_original_components(self):
        self.reconcile(1, sku="KIT")
        self.assertEqual(self.levels(), {"A": 3, "B": 2, "KIT": 1})
        self.recipe["KIT"] = [("B", 1)]
        self.reconcile(1, cancelled=True, sku="KIT")
        self.assertEqual(self.levels()["A"], 5)
        self.assertEqual(self.levels()["B"], 2)
        self.assertEqual(self.levels()["KIT"], 2)

    def test_preexisting_active_order_requires_evidence_for_release(self):
        with closing(sqlite3.connect(self.db)) as db:
            _ensure_db(db)
            db.execute("INSERT INTO processed_orders VALUES('ozon_fbs','demo','A',3,'active','old')")
            db.commit()
        self.reconcile(3)
        with self.assertRaisesRegex(StockSyncError, "сверка"):
            self.reconcile(3, cancelled=True)
        self.assertEqual(self.query("SELECT decremented FROM processed_orders"), [(3,)])
        self.assertEqual(self.levels()["A"], 5)

    def test_recovery_of_committed_journal_before_replace(self):
        order = [{"offer_id": "A", "qty": 2}]
        with patch("stock_transaction.os.replace", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.tilda(order)
        self.assertEqual(self.levels()["A"], 5)
        self.assertEqual(self.query("SELECT count(*) FROM stock_pending"), [(1,)])
        self.assertEqual(self.tilda(order)["deltas"], {})
        self.assertEqual(self.levels()["A"], 3)
        self.assertEqual(self.query("SELECT count(*) FROM stock_pending"), [(0,)])

    def test_conflicting_edit_blocks_pending_and_keeps_edit(self):
        order = [{"offer_id": "A", "qty": 2}]
        with patch("stock_transaction.os.replace", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.tilda(order)
        self.create_catalog({"A": 9, "B": 2, "KIT": 2})
        with self.assertRaisesRegex(StockSyncError, "сверка"):
            self.tilda(order)
        self.assertEqual(self.levels()["A"], 9)
        self.assertEqual(self.query("SELECT count(*) FROM stock_pending"), [(1,)])

    def test_recovery_after_replace_before_acknowledgement(self):
        import stock_transaction
        replace = stock_transaction._replace_workbook

        def fail_after_replace(path, data):
            replace(path, data)
            raise OSError("lost acknowledgement")

        order = [{"offer_id": "A", "qty": 2}]
        with patch("stock_transaction._replace_workbook", fail_after_replace), self.assertRaises(OSError):
            self.tilda(order)
        self.assertEqual(self.levels()["A"], 3)
        self.tilda(order)
        self.assertEqual(self.levels()["A"], 3)
        self.assertEqual(self.query("SELECT count(*) FROM stock_pending"), [(0,)])

    def test_failed_marketplace_rolls_back_other_sources(self):
        posting = {"posting_number": "demo", "status": "active", "products": [{"offer_id": "A", "quantity": 2}]}
        with patch("ozon_client.get_fbs_orders_since", return_value=[posting]), \
                patch("ozon_client.get_fbo_orders_since", side_effect=TimeoutError()), self.assertRaises(TimeoutError):
            sync_all_orders(catalog_path=str(self.xlsx), db_path=str(self.db))
        self.assertEqual(self.query("SELECT count(*) FROM processed_orders"), [(0,)])
        self.assertEqual(self.levels()["A"], 5)

    def test_standalone_push_guard_checks_pending_without_creating_files(self):
        from stock_transaction import assert_stock_ready

        assert_stock_ready(self.db)
        self.assertFalse(self.db.exists())
        with closing(sqlite3.connect(self.db)) as db:
            _ensure_db(db)
        assert_stock_ready(self.db)
        with patch("stock_transaction.os.replace", side_effect=OSError("disk")), self.assertRaises(OSError):
            self.tilda([{"offer_id": "A", "qty": 2}])
        with self.assertRaises(StockSyncError):
            assert_stock_ready(self.db)
        self.tilda([{"offer_id": "A", "qty": 2}])
        assert_stock_ready(self.db)

    def test_old_wb_vendor_alias_unchanged_is_a_noop(self):
        with closing(sqlite3.connect(self.db)) as db:
            _ensure_db(db)
            db.execute("INSERT INTO processed_orders VALUES('wb','demo','vendor',1,'active','old')")
            db.commit()
        with order_batch(self.db, self.xlsx, _ensure_db) as batch:
            batch.reconcile("wb", "demo", "A", 1, False, legacy_sku="vendor")
        self.assertEqual(self.levels()["A"], 5)
        with self.assertRaises(StockSyncError), order_batch(self.db, self.xlsx, _ensure_db) as batch:
            batch.reconcile("wb", "demo", "A", 1, True, legacy_sku="vendor")

    def test_retired_wb_vendor_cannot_debit_reused_sku(self):
        from stock_sync import _sync_wb

        self.create_catalog({"0AM927769D": 5})
        retired = {"supplierArticle": "0AM927769D", "srid": "old-sale", "isCancel": False}
        with patch("wb_client.get_orders_since", return_value=[retired]), self.assertRaises(StockSyncError):
            with order_batch(self.db, self.xlsx, _ensure_db) as batch:
                _sync_wb(batch, "2026-11-01")
        self.assertEqual(self.levels()["0AM927769D"], 5)
        with closing(sqlite3.connect(self.db)) as db:
            db.execute("INSERT INTO processed_orders VALUES('wb','old-sale','0AM927769D',1,'active','old')")
            db.commit()
        board = {"supplierArticle": "0AM927769D-plata", "srid": "new-sale", "isCancel": False}
        with patch("wb_client.get_orders_since", return_value=[retired, board]):
            with order_batch(self.db, self.xlsx, _ensure_db) as batch:
                _sync_wb(batch, "2026-11-01")
        self.assertEqual(self.levels()["0AM927769D"], 4)

    def test_concurrent_replay_debits_once(self):
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(self.tilda, [{"offer_id": "A", "qty": 2}]) for _ in range(2)]
            results = [future.result() for future in futures]
        self.assertEqual(sum(bool(result["deltas"]) for result in results), 1)
        self.assertEqual(self.levels()["A"], 3)
        self.assertEqual(self.query("SELECT count(*) FROM stock_pending"), [(0,)])


class KitSourceValidationTest(unittest.TestCase):
    def test_strict_mode_rejects_missing_file_and_invalid_component_multiplier(self):
        import kits

        with TemporaryDirectory() as tmp:
            path = Path(tmp) / "kits.xlsx"
            with self.assertRaises(FileNotFoundError):
                kits.load_bom(path, strict=True)
            wb = openpyxl.Workbook()
            wb.active.append(kits.KITS_HEADER)
            wb.active.append(["KIT", "", "A", 1.5])
            wb.save(path)
            wb.close()
            with self.assertRaises(ValueError):
                kits.load_bom(path, strict=True)
