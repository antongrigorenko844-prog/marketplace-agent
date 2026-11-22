"""Tilda-only DQ200 0AM927769 standalone board price contract tests."""
import unittest,xml.etree.ElementTree as ET
from unittest.mock import patch
import site_board_prices as bp

class BoardSitePriceTests(unittest.TestCase):
    def test_only_five_standalone_boards_in_scope(self):
        self.assertEqual(set(bp.TARGETS),{"0AM927769D","0AM927769E","0AM927769F","0AM927769G","0AM927769K"})
        self.assertEqual(bp.TARGETS["0AM927769D"],37500)
        self.assertEqual(set(bp.TARGETS.values()),{37500,39500})
        self.assertNotIn("0AM927769N",bp.TARGETS)
        self.assertFalse(any(x.startswith("chip") for x in bp.TARGETS))
    def test_xml_change_only_and_no_images_stock_or_description(self):
        rows={sku:{"title":f"Плата 0AM927769{sku[-1]} DQ200"} for sku in bp.TARGETS}
        imp,off=bp.xml(rows)
        first=ET.fromstring(imp)
        second=ET.fromstring(off)
        self.assertEqual(len(first.findall(".//Товар")),5)
        self.assertEqual(len(second.findall(".//Предложение")),5)
        self.assertIn('СодержитТолькоИзменения="true"',off)
        self.assertNotIn("<Количество>",off)
        self.assertNotIn("<Картинка>",imp)
        self.assertNotIn("<Описание>",imp)
        actual={x.findtext("Артикул"):int(float(x.findtext(".//ЦенаЗаЕдиницу"))) for x in second.findall(".//Предложение")}
        self.assertEqual(actual,bp.TARGETS)
    def test_identity_validator_rejects_kit_or_mismatched_uid(self):
        rows={}
        for idx,sku in enumerate(bp.TARGETS):
            uid=str(765400000000+idx)
            rows[uid]={"uid":uid,"sku":sku,"externalid":sku,"title":f"Электронная плата мехатроника DQ200 {sku}","price":bp.EXPECTED_OLD[sku],"editions":[]}
        self.assertEqual(set(bp.validate(rows,True)),set(bp.TARGETS))
        bad={key:dict(val) for key,val in rows.items()}
        key=next(iter(bad))
        bad[key]["title"]+=" комплект"
        with self.assertRaises(ValueError):
            bp.validate(bad,True)
if __name__=="__main__":unittest.main()
