from contextlib import ExitStack
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import openpyxl

import avito_feed
import catalog_editor
import main
import sync_master_control as master
import tilda_commerceml


class StockPublicationTests(unittest.TestCase):
    def test_master_content_edit_and_targeted_removal(self):
        with TemporaryDirectory() as tmp, ExitStack() as patches:
            root = Path(tmp)
            catalog = root / "catalog.xlsx"
            wb = openpyxl.Workbook()
            wb.active.append([label for _, label in catalog_editor.COLUMNS])
            for sku, qty in [("A", 1), ("a", 3)]:
                wb.active.append([sku, "Old", None, 100, None, None, None, qty])
            wb.save(catalog)
            wb.close()
            rows = {sku: {"status_ozon": master.STATUS_EXISTING, "status_wb": "",
                          "name": "New", "quantity": qty} for sku, qty in [("a", 0), ("A", 20)]}
            for name, path in {"MASTER_PATH": catalog, "CATALOG_PATH": catalog,
                               "WB_CATALOG_PATH": root / "absent-wb.xlsx",
                               "NEW_PATH": root / "absent-new.xlsx", "WB_NEW_PATH": root / "absent-wb-new.xlsx"}.items():
                patches.enter_context(patch.object(master, name, str(path)))
            patches.enter_context(patch.object(master, "_ensure_tnved_column", return_value=0))
            patches.enter_context(patch.object(master, "_read_master", return_value=(rows, {})))
            self.assertEqual(master.run(), 0)
            self.assertEqual(catalog_editor.get_stock_levels(catalog), {"A": 1, "a": 3})
            self.assertEqual(catalog_editor.load_catalog_edits(catalog)["A"]["name"], "New")
            self.assertEqual(master.run(zero_stock_ids={"a"}), 0)
            self.assertEqual(catalog_editor.get_stock_levels(catalog), {"A": 1, "a": 0})

    def test_avito_uses_order_catalog_even_when_master_is_unavailable(self):
        wb = openpyxl.Workbook()
        wb.active.append(["SKU", "Title", "Description", "Price", "", "Photo", "Video", "Stock"])
        for sku, qty in [("A", 0), ("B", 3)]:
            wb.active.append([sku, "Мехатроник DSG7", "", 1000, "", "", "", qty])
        source = BytesIO()
        wb.save(source)
        wb.close()
        template = Path(avito_feed.__file__).parent / "data" / "avito_template.xlsx"
        result = avito_feed.build_avito_feed(source, template, BytesIO(), "Test",
                                             master_control_path="unused", avito_photos_map_path="")
        self.assertEqual(result["skipped_no_stock"], ["A"])
        self.assertEqual(result["written"], 1)

    def test_tilda_commerceml_requires_step_specific_acknowledgement(self):
        def check(text, step):
            return tilda_commerceml._check(SimpleNamespace(text=text, status_code=200), step)
        self.assertEqual(check("success", "file offers.xml"), "success")
        self.assertEqual(check("zip=no\nfile_limit=200000", "init"), "zip=no\nfile_limit=200000")
        self.assertEqual(check("progress", "import offers.xml"), "progress")
        for body, step in [("<html>OK</html>", "import offers.xml"), ("", "checkauth"), ("progress", "file photo")]:
            with self.subTest(body=body, step=step), self.assertRaises(RuntimeError):
                check(body, step)

    def test_webhook_without_stable_identity_cannot_debit(self):
        import os
        with patch.dict(os.environ, {"TILDA_ORDER_PAYLOAD": '{"items":[{"offer_id":"A","qty":1}]}'}), \
             patch("stock_sync.sync_tilda_order") as sync:
            self.assertEqual(main.cmd_sync_tilda_order(), 1)
            sync.assert_not_called()
