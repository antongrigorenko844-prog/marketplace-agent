from contextlib import ExitStack
import csv
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import Workbook

import avito_feed
import catalog_editor
import main
import sales_availability
import tilda_commerceml
import tilda_feed


class PausedSalesTests(unittest.TestCase):
    def test_one_paused_offer_across_stock_exports(self):
        paused, neighbor = "0cw0am", "0cw0amdsgdq200"
        with TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            source = root / "ozon_catalog.xlsx"
            book = Workbook()
            book.active.append([label for _, label in catalog_editor.COLUMNS])
            for sku in (paused, neighbor):
                book.active.append([sku, "Прокладка мехатроника DSG7 DQ200", "", 1200, "", "", "", 37])
            book.save(source)
            book.close()
            stack.enter_context(patch.object(main, "_data_path", side_effect=lambda name: str(root / name)))
            stack.enter_context(patch("stock_transaction.assert_stock_ready"))
            result = main._load_stock_items()
            self.assertEqual({r["offer_id"]: r["stock"] for r in result}, {paused: 0, neighbor: 37})
            self.assertEqual(sales_availability.quantity_for_sale(paused.upper(), 37), 37)

            items, _ = tilda_commerceml.load_items(str(source), "", "")
            self.assertEqual({r["id"]: r["qty"] for r in items}, {paused: 0, neighbor: 37})
            tilda_feed.build_tilda_catalog(str(source), str(root / "tilda.csv"), "", "")
            with (root / "tilda.csv").open(encoding="utf-8") as f:
                self.assertEqual({r["SKU"]: r["Quantity"] for r in csv.DictReader(f, delimiter=";")},
                                 {paused: "0", neighbor: ""})
            avito = avito_feed.build_avito_feed(str(source),
                str(Path(avito_feed.__file__).parent / "data" / "avito_template.xlsx"),
                str(root / "avito.xlsx"), "Test", avito_photos_map_path="")
            self.assertEqual(avito["skipped_no_stock"], [paused])
            self.assertEqual(avito["written"], 1)
            self.assertEqual(catalog_editor.get_stock_levels(source), {paused: 37, neighbor: 37})
