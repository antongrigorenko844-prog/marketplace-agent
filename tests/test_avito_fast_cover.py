"""Pure regression tests for one-image Avito updates and safe publish gates."""
from __future__ import annotations

from hashlib import sha256
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
import os
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

import avito_fast_cover as fast


OLD = "https://example.com/old.jpg"
NEW = "https://example.com/new.jpg"
SECOND = "https://example.com/second.jpg"


def workbook_bytes(extra=()):
    book = Workbook()
    sheet = book.active
    sheet.title = fast.SHEET
    for n in range(1, 5):
        sheet.cell(n, 1).value = f"Avito template header {n}"
    entries = [
        ("6t30", OLD + "\n" + SECOND, 18800),
        ("other-kit", "https://example.com/other.jpg", 45900),
        *extra,
    ]
    for i, (sku, images, price) in enumerate(entries, 5):
        sheet.cell(i, fast.ID_COL).value = sku
        sheet.cell(i, fast.IMAGE_COL).value = images
        sheet.cell(i, 24).value = "Описание в заголовке " + sku
        sheet.cell(i, 17).value = "Описание товара " + sku
        sheet.cell(i, 34).value = price
    dictionary = book.create_sheet("Справочники")
    dictionary["A1"] = "Must not change"
    output = BytesIO()
    book.save(output)
    book.close()
    return output.getvalue()


class AvitoFastCoverTests(unittest.TestCase):
    def test_only_first_image_replaced(self):
        before = workbook_bytes()
        after, result = fast.patch_cover(before, "6t30", NEW)
        self.assertTrue(result["changed"])
        self.assertEqual(result["unchanged_secondary_count"], 1)
        self.assertNotEqual(sha256(before).digest(), sha256(after).digest())
        book = load_workbook(BytesIO(after), read_only=True)
        try:
            sheet = book[fast.SHEET]
            self.assertEqual(sheet.cell(5, fast.IMAGE_COL).value, NEW + "\n" + SECOND)
            self.assertEqual(sheet.cell(5, 34).value, 18800)
            self.assertEqual(sheet.cell(6, 34).value, 45900)
            self.assertEqual(sheet.cell(6, fast.IMAGE_COL).value,
                             "https://example.com/other.jpg")
            self.assertEqual(book["Справочники"]["A1"].value, "Must not change")
        finally:
            book.close()

    def test_no_change_means_identical_xlsx_bytes(self):
        data = workbook_bytes()
        once, _ = fast.patch_cover(data, "6t30", NEW)
        twice, result = fast.patch_cover(once, "6t30", NEW)
        self.assertFalse(result["changed"])
        self.assertEqual(once, twice)

    def test_reject_missing_and_duplicate(self):
        data = workbook_bytes()
        with self.assertRaises(fast.AvitoCoverError):
            fast.patch_cover(data, "not-present", NEW)
        dup = workbook_bytes(extra=[("6t30-av", "https://example.com/third.jpg", 100)])
        with self.assertRaises(fast.AvitoCoverError):
            fast.patch_cover(dup, "6t30", NEW)

    def test_valid_link_required_and_no_secondary_reordering(self):
        data = workbook_bytes()
        with self.assertRaises(fast.AvitoCoverError):
            fast.patch_cover(data, "6t30", "http://example.com/unsecured.jpg")
        with self.assertRaises(fast.AvitoCoverError):
            fast.patch_cover(data, "6t30", SECOND)

    def test_exact_linked_avito_identifier(self):
        wb = Workbook()
        ws = wb.active
        ws.title = fast.SHEET
        ws.cell(5, fast.ID_COL).value = "6t30-av"
        ws.cell(5, fast.IMAGE_COL).value = OLD
        data = BytesIO()
        wb.save(data)
        out, info = fast.patch_cover(data.getvalue(), "6t30", NEW)
        self.assertEqual(info["row"], 5)
        new = load_workbook(BytesIO(out), read_only=True)
        try:
            self.assertEqual(new[fast.SHEET].cell(5, fast.IMAGE_COL).value, NEW)
        finally:
            new.close()

    def test_ready_gate_is_explicit(self):
        for value in (None, "", "false", "0", "no"):
            self.assertFalse(fast._ready(value))
        for value in ("true", "1", "yes"):
            self.assertTrue(fast._ready(value))

    def test_digest_requires_authoritative_sha256(self):
        data = "a" * 64
        self.assertEqual(fast._digest({"digest": "sha256:" + data}), data)
        with self.assertRaises(fast.AvitoCoverError):
            fast._digest({"digest": None})

    def test_unpaid_or_unconfirmed_never_contacts_release(self):
        with TemporaryDirectory() as td:
            local = Path(td) / "avito_feed.xlsx"
            initial = workbook_bytes()
            local.write_bytes(initial)
            with patch.object(fast, "LOCAL_FEED", local), \
                 patch.object(fast, "_approved_image", return_value=NEW), \
                 patch.dict(os.environ, {"AVITO_AUTOLOAD_READY": "false"}), \
                 patch.object(fast, "_get_release") as network:
                result = fast.run("6t30", "apply", check_release=True)
                self.assertEqual(result["result"], "skipped_autoload_not_confirmed_ready")
                self.assertEqual(local.read_bytes(), initial)
                network.assert_not_called()

    def test_remote_digest_noop_skips_publishing(self):
        with TemporaryDirectory() as td:
            local = Path(td) / "avito_feed.xlsx"
            raw = workbook_bytes()
            local.write_bytes(raw)
            expected, _ = fast.patch_cover(raw, "6t30", NEW)
            expected_digest = sha256(expected).hexdigest()
            with patch.object(fast, "LOCAL_FEED", local), \
                 patch.object(fast, "_approved_image", return_value=NEW), \
                 patch.dict(os.environ, {"AVITO_AUTOLOAD_READY": "true", "GH_TOKEN": "fake"}), \
                 patch.object(fast, "_get_release", return_value=(
                     {}, {"digest": "sha256:" + expected_digest}
                 )), patch.object(fast, "_publish") as publisher:
                result = fast.run("6t30", "apply")
                self.assertEqual(result["result"], "already_published_no_upload")
                publisher.assert_not_called()

    def test_remote_drift_blocks_publish_and_keeps_source(self):
        with TemporaryDirectory() as td:
            local = Path(td) / "avito_feed.xlsx"
            original = workbook_bytes()
            local.write_bytes(original)
            with patch.object(fast, "LOCAL_FEED", local), \
                 patch.object(fast, "_approved_image", return_value=NEW), \
                 patch.dict(os.environ, {"AVITO_AUTOLOAD_READY": "true", "GH_TOKEN": "fake"}), \
                 patch.object(fast, "_get_release", return_value=(
                     {}, {"digest": "sha256:" + ("f" * 64)}
                 )), patch.object(fast, "_publish") as publisher:
                with self.assertRaises(fast.AvitoCoverError):
                    fast.run("6t30", "apply")
                self.assertEqual(local.read_bytes(), original)
                publisher.assert_not_called()


if __name__ == "__main__":
    unittest.main()
