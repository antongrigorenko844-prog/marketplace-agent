import csv
import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import tilda_cover_csv as tilda

GOOD = "https://example.com/cover.jpg"
OTHER = "https://example.com/detail.jpg"


class TildaPhotoOnlyTests(unittest.TestCase):
    def test_csv_contains_only_uid_sku_photo(self):
        result = tilda.build_csv("695009271403", "6t30", [GOOD, OTHER])
        rows = list(csv.reader(io.StringIO(result), delimiter=";"))
        self.assertEqual(rows, [["TildaUID", "SKU", "Photo"],
                                ["695009271403", "6t30", GOOD + "," + OTHER]])
        self.assertNotIn("Price", result)
        self.assertNotIn("Quantity", result)

    def test_rejects_invalid_uid_and_photo_list(self):
        for args in [
            ("", "6t30", [GOOD]),
            ("abc", "6t30", [GOOD]),
            ("123", "6t30", []),
            ("123", "6t30", [GOOD, GOOD]),
            ("123", "6t30", ["http://example.com/cover.jpg"]),
        ]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                tilda.build_csv(*args)

    def test_rejects_missing_and_duplicated_site_uid(self):
        approved = {"6t30": {"ids": {"tilda": "695009271403"},
                             "sources": {"tilda": [GOOD, OTHER]}}}
        with patch("tilda_cover_csv.load", return_value=approved):
            with self.assertRaises(ValueError):
                tilda.run("6t30", site_records_fn=lambda: {}, check_pixels=False)
            with self.assertRaises(ValueError):
                tilda.run("6t30", site_records_fn=lambda: {
                    "a": {"uid": "695009271403", "sku": "6t30"},
                    "b": {"uid": "695009271403", "sku": "6t30"},
                }, check_pixels=False)

    def test_photo_plan_has_no_commercial_writes(self):
        approved = {"6t30": {"ids": {"tilda": "695009271403"},
                             "sources": {"tilda": [GOOD, OTHER]}}}
        live = {"a": {"uid": "695009271403", "sku": "6t30",
                       "gallery": '["https://example.com/old.jpg"]',
                       "title": "Original title", "price": 10900}}
        with TemporaryDirectory() as td:
            with patch.object(tilda, "CSV", Path(td) / "cover.csv"), \
                 patch("tilda_cover_csv.load", return_value=approved):
                result = tilda.run("6t30", site_records_fn=lambda: live, check_pixels=False)
                self.assertEqual(result["status"], "needs_csv_import")
                self.assertTrue(result["csv_identity_and_media_columns_only"])
                self.assertEqual(result["stock_writes"], 0)
                self.assertEqual(result["price_writes"], 0)
                content = (Path(td) / "cover.csv").read_text(encoding="utf-8-sig")
                self.assertIn("TildaUID;SKU;Photo", content)
                self.assertNotIn("10900", content)

    def test_gallery_parse(self):
        self.assertEqual(tilda.gallery_entries(
            {"gallery": '[{"img":"https://example.com/a"},{"url":"https://example.com/b"}]'}),
            ["https://example.com/a", "https://example.com/b"])


if __name__ == "__main__":
    unittest.main()
