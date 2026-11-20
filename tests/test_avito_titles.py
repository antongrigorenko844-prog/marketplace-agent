import io
from pathlib import Path
import unittest

import openpyxl

import avito_feed


class AvitoTitleTests(unittest.TestCase):
    def test_word_ending_at_limit_is_kept(self):
        prefix = "Мехатроник DSG6 DQ250 в сборе с электронной платой"
        self.assertEqual(len(prefix), 50)
        self.assertEqual(avito_feed._truncate_title(prefix + " 02E927770"), prefix)
        self.assertEqual(avito_feed._truncate_title(prefix), prefix)

    def test_partial_word_is_removed(self):
        self.assertEqual(avito_feed._truncate_title("Ремкомплект DQ200", 14), "Ремкомплект")

    def test_generated_feed_preserves_identifiers_and_kit_contents(self):
        boards = [f"0AM927769{rev}" for rev in "DEFGK"]
        assemblies = {"0am325025HX": "0AM927769D", "0am325025HXF": "0AM927769F"}
        parts = {
            "0cw325025h": ("Прокладка", "0AM927769N"),
            "0am325025G": ("2 пыльника", "2 сальника", "2 крышки"),
            "0am325025hfe": ("2 пыльника", "2 сальника", "2 крышки"),
            "0am325025j": ("сепараторная", "соленоидов", "VAG"),
            "separatornaya-plastiny-solenoid-dq200": ("сепараторная", "2 пластины", "соленоидов"),
            "plita-filtr-pylniki-orig": ("фильтр", "2 пыльника"),
            "plita-filtr-salniki-orig": ("фильтр", "2 сальника"),
            "plita-pylniki-salniki-orig": ("2 пыльника", "2 сальника"),
            "plita-pylniki-salniki-orig-v2": ("2 пыльника", "2 сальника"),
        }
        catalog = openpyxl.Workbook()
        sheet = catalog.active
        sheet.append(["SKU", "Название", "Описание", "Цена", "", "Фото", "Видео", "Остаток"])
        skus = [*boards, *assemblies, *parts]
        for sku in skus:
            # A shared source title ensures distinctions come from the Avito exporter.
            sheet.append([sku, "Мехатроник DSG7 DQ200", "", 100, "", "", "", 3])
        source = io.BytesIO()
        catalog.save(source)
        output = io.BytesIO()
        template = Path(avito_feed.__file__).parent / "data" / "avito_template.xlsx"
        avito_feed.build_avito_feed(source, template, output, seller_address="Тестовый адрес",
                                    master_control_path="", avito_photos_map_path="")
        result = openpyxl.load_workbook(output, read_only=True)
        try:
            rows = result[avito_feed.SHEET_ADS].iter_rows(min_row=avito_feed.FIRST_DATA_ROW, values_only=True)
            titles = {
                str(row[avito_feed.COL_ID - 1]).removesuffix(avito_feed.AVITO_LINK_SUFFIX):
                row[avito_feed.COL_TITLE - 1] for row in rows
            }
        finally:
            result.close()
        self.assertEqual(set(titles), set(skus))
        for sku, title in titles.items():
            self.assertLessEqual(len(title), 50, sku)
            self.assertIn("DQ200", title, sku)
        for sku in boards:
            self.assertIn(sku, titles[sku])
        for sku, revision in assemblies.items():
            self.assertIn(revision, titles[sku])
        for sku, components in parts.items():
            for component in components:
                self.assertIn(component, titles[sku], sku)
        self.assertNotIn("сальника", titles["plita-filtr-pylniki-orig"])
        self.assertNotIn("пыльника", titles["plita-filtr-salniki-orig"])
