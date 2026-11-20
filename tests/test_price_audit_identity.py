import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

import price_audit


def workbook(path, sheet_name, rows):
    book = Workbook()
    sheet = book.active
    sheet.title = sheet_name
    for row in rows:
        sheet.append(row)
    book.save(path)
    book.close()


class PriceAuditIdentityTests(unittest.TestCase):
    def test_report_and_cost_backfill_keep_exact_ozon_ids(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            master, finance, report = (root / name for name in ("master.xlsx", "finance.json", "audit.xlsx"))
            workbook(master, "Товары", [
                ["Артикул", "Цена, ₽", "Остаток, шт.", "Название товара", "Закупка, ₽"],
                ["Mixed", 1000, 1, "A", None],
                ["mixed", 2000, 1, "B", None],
                ["Other", 1000, 1, "C", 0],
                ["dup", 1000, 1, "D", None],
                ["Ignore", 1000, 1, "E", 50],
                ["ignore", 1000, 1, "F", 60],
            ])
            details = [{"offer_id": sku, "price": {"net_price": cost}}
                       for sku, cost in [("Mixed", 100), ("mixed", 300), ("OTHER", 400), ("dup", 20), ("dup", 30)]]
            (root / "ozon_products.json").write_text(json.dumps({"details": details}))
            finance.write_text(json.dumps({"items": {"Mixed": {"sold": 2, "net": 1400}, "mixed": {"sold": 1, "net": 900}}}))
            (root / "site_prices.json").write_text(json.dumps({"mixed": 500, "other": 600}))
            (root / "audit_ignore.json").write_text(json.dumps({"Ignore": True}))
            with patch.multiple(price_audit, DATA=tmp, FIN_PATH=str(finance), MASTER=str(master), AUDIT_PATH=str(report)):
                result = price_audit.build_price_audit()
            self.assertEqual(result["cost_filled_from_ozon"], 2)
            generated = load_workbook(report, read_only=True)
            try:
                rows = {r[0]: r for r in generated["Ревизия цен"].iter_rows(min_row=2, values_only=True)}
                self.assertEqual((rows["Mixed"][3], rows["Mixed"][5]), (100, 700))
                self.assertEqual((rows["mixed"][3], rows["mixed"][5]), (300, 900))
                self.assertIsNone(rows["Mixed"][11])
                self.assertIsNone(rows["mixed"][11])
                self.assertEqual(rows["Other"][11], 600)
                self.assertIsNone(rows["Other"][3])
                self.assertIsNone(rows["dup"][3])
                self.assertNotIn("Ignore", rows)
                self.assertIn("ignore", rows)
            finally:
                generated.close()
            saved = load_workbook(master, read_only=True)
            try:
                self.assertEqual([r[4] for r in saved.active.iter_rows(min_row=2, values_only=True)],
                                 [100, 300, 0, None, 50, 60])
            finally:
                saved.close()

    def test_avito_uses_only_proven_alias_and_unambiguous_price(self):
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "avito_links.json").write_text(json.dumps({"Alias": 123, "Wrong": 234, "Both": 345}))
            workbook(root / "avito_feed.xlsx", "Объявления", [
                ["Feed"], ["Уникальный идентификатор объявления", "Номер объявления на Авито", "Цена"],
                [], [], ["Sku", None, 300], ["sku", None, 400], ["literal-av", None, 500],
                ["Alias-av", 123, 600], ["Wrong-av", 999, 700], ["Unknown-av", 888, 800],
                ["Both-av", 345, 900], ["Duplicate", None, 100], ["Duplicate", None, 200],
            ])
            ids = {"Sku", "sku", "literal-av", "Alias", "Wrong", "Unknown", "Both", "Both-av", "Duplicate"}
            with patch.object(price_audit, "DATA", tmp):
                self.assertEqual(price_audit._avito_prices(ids),
                                 {"Sku": 300, "sku": 400, "literal-av": 500, "Alias": 600})


if __name__ == "__main__":
    unittest.main()
