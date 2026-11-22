import json
import sqlite3
import unittest

import stock_preflight as sp


def posting(sku="6t30", qty=1, status="cancelled", number="id-1"):
    return {"posting_number": number, "status": status,
            "products": [{"offer_id": sku, "quantity": qty}]}


class StockPreflightTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.db.executescript("""
            CREATE TABLE processed_orders (
                source TEXT NOT NULL, order_key TEXT NOT NULL,
                offer_id TEXT NOT NULL, decremented INTEGER NOT NULL,
                status TEXT, updated_at TEXT, PRIMARY KEY(source,order_key));
            CREATE TABLE stock_order_recipe (
                source TEXT NOT NULL, order_key TEXT NOT NULL,
                components TEXT NOT NULL, PRIMARY KEY(source,order_key));
            CREATE TABLE stock_pending(id INTEGER PRIMARY KEY, before_hash TEXT, workbook BLOB);
        """)
        self.db.execute(
            "INSERT INTO processed_orders VALUES(?,?,?,?,?,?)",
            ("ozon_fbs", "id-1:6t30", "6t30", 1, "active", "old")
        )

    def test_old_cancelled_order_without_recipe_is_blocked_read_only(self):
        self.db.execute("PRAGMA query_only=ON")
        result = sp.compare_orders(self.db, "ozon_fbs", [posting()])
        self.assertEqual(result["counts"]["blocked_legacy"], 1)
        self.assertEqual(result["blocked_examples"][0]["reason"], "historical_recipe_missing")
        self.assertNotIn("id-1", json.dumps(result))
        self.assertEqual(self.db.execute("SELECT decremented FROM processed_orders").fetchone()[0], 1)

    def test_old_unchanged_order_is_noop_without_recipe(self):
        result = sp.compare_orders(self.db, "ozon_fbs", [posting(status="awaiting_packaging")])
        self.assertEqual(result["counts"]["unchanged"], 1)
        self.assertNotIn("blocked_legacy", result["counts"])

    def test_with_saved_original_recipe_can_be_reconciled(self):
        self.db.execute("INSERT INTO stock_order_recipe VALUES(?,?,?)", (
            "ozon_fbs", "id-1:6t30", json.dumps({"X": 2})))
        result = sp.compare_orders(self.db, "ozon_fbs", [posting()])
        self.assertEqual(result["counts"]["can_reconcile_from_saved_recipe"], 1)
        self.assertNotIn("blocked_legacy", result["counts"])

    def test_corrupted_recipe_is_blocked_not_guessed(self):
        self.db.execute("INSERT INTO stock_order_recipe VALUES(?,?,?)", (
            "ozon_fbs", "id-1:6t30", "{invalid"))
        result = sp.compare_orders(self.db, "ozon_fbs", [posting()])
        self.assertEqual(result["blocked_examples"][0]["reason"], "historical_recipe_invalid")

    def test_unknown_order_is_not_altered_or_inferred(self):
        result = sp.compare_orders(self.db, "ozon_fbs", [
            posting(number="id-new", status="awaiting_packaging")])
        self.assertEqual(result["counts"]["new_or_unrecorded"], 1)
        self.assertEqual(self.db.execute("SELECT COUNT(*) FROM processed_orders").fetchone()[0], 1)

    def test_changed_sku_is_blocked(self):
        self.db.execute(
            "UPDATE processed_orders SET offer_id='OTHER' WHERE source='ozon_fbs'")
        result = sp.compare_orders(self.db, "ozon_fbs", [posting()])
        self.assertEqual(result["blocked_examples"][0]["reason"], "sku_changed")

    def test_pending_projection_visible_in_report(self):
        self.db.execute("INSERT INTO stock_pending VALUES(1,'hash',X'00')")
        stats = sp.legacy_stats(self.db)
        self.assertEqual(stats["pending_projection"], 1)
        self.assertEqual(stats["by_source"][0]["active_missing_recipes"], 1)

    def test_duplicate_product_lines_are_aggregated(self):
        p = posting(status="awaiting_packaging")
        p["products"].append({"offer_id": "6t30", "quantity": 2})
        result = sp.compare_orders(self.db, "ozon_fbs", [p])
        self.assertEqual(result["blocked_examples"][0]["requested_units"], 3)

    def test_no_implicit_positive_quantity_or_failed_cancel_status(self):
        with self.assertRaises(ValueError):
            list(sp._rows({"posting_number": "id-1", "status": "cancelled",
                           "products": [{"offer_id": "6t30", "quantity": None}]}))
        with self.assertRaises(ValueError):
            list(sp._rows({"posting_number": "", "status": "cancelled",
                           "products": [{"offer_id": "6t30", "quantity": 1}]}))


if __name__ == "__main__":
    unittest.main()
