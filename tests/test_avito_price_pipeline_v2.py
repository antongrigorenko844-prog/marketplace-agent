"""No-credential, in-memory-source safety tests for price pipeline v2."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import json, unittest

from openpyxl import Workbook,load_workbook
from scripts import avito_price_pipeline_v2 as pipeline
from scripts.avito_bulk_price_engine import AvitoPriceGuardError

def fixture_manifest(**kw):
    m={"schema_version":2,"mode":"inspect","owner_approved":True,
       "channel":"avito","preserve_other_channels":True,
       "preserve_stock":True,"preserve_photos":True,
       "preserve_costs":True,"freeze_effective_site_prices":True,
       "targets":[{
          "avito_id":123,"kind":"main","sku":"test-sku",
          "expected_price":950,"approved_price":450,
          "expected_site_price":950,
          "expected_title":"Original title",
          "approved_title":"Updated tested title"
       }]}
    m.update(kw)
    return m

class PipelineSafetyTests(unittest.TestCase):
    def test_manifest_guards_unsafe_changes(self):
        valid=fixture_manifest()
        self.assertEqual(len(pipeline.validate_manifest(valid)),1)
        for changes in [
            {"channel":"ozon"},
            {"owner_approved":False},
            {"preserve_stock":False},
            {"freeze_effective_site_prices":False},
            {"targets":[]},
            {"targets":valid["targets"]*2},
        ]:
            with self.subTest(changes=changes),self.assertRaises(AvitoPriceGuardError):
                pipeline.validate_manifest(fixture_manifest(**changes))

    def test_stage_only_avito_B_and_feed_price_title_without_site_price_change(self):
        with TemporaryDirectory() as root:
            base=Path(root)
            ov=base/"overrides.xlsx"
            feed=base/"avito_feed.xlsx"
            cm=base/"content.json"
            audit=base/"audit.json"

            b=Workbook();ws=b.active;ws.title="Цены Avito и сайт"
            ws.append(["offer_id","avito","fixed","site"])
            ws.append(["test-sku",950,"да",None])
            ws.append(["original-sku",950,"да",950])
            b.save(ov)

            b=Workbook();ws=b.active;ws.title="Объявления"
            for n in range(1,5):ws.cell(n,1).value=f"Header {n}"
            ws.cell(5,4).value="test-sku"
            ws.cell(5,24).value="Original title"
            ws.cell(5,34).value=950
            ws.cell(5,17).value="Original description"
            ws.cell(5,19).value="photo_url_remains_unmodified"
            ws.cell(6,4).value="original-sku"
            ws.cell(6,24).value="Original elsewhere"
            ws.cell(6,34).value=950
            b.save(feed)

            cm.write_text(json.dumps({"version":1,"products":{}}))
            audit.write_text(json.dumps({
                "products":[{"sku":"test-sku","source_feed_id":"test-sku",
                              "mapped":{"id":123,"status":"active"}}]}))
            def load_prices(channel, path=None):
                f=Path(path) if path else ov
                wb=load_workbook(f)
                out={}
                for row in wb["Цены Avito и сайт"].iter_rows(min_row=2,values_only=True):
                    if not row or not row[0]:continue
                    out[row[0]]=float(row[3] if channel=="site" and row[3] is not None
                                       else row[1])
                if channel=="site" and "site-json-override" in out:
                    out["test-sku"]=37500
                return out
            live={123:{"id":123,"title":"Original title",
                       "price":950,"status":"active"}}
            with patch.object(pipeline,"OVERRIDE",ov),patch.object(pipeline,"FEED",feed),\
                 patch.object(pipeline,"CONTENT",cm),patch.object(pipeline,"AUD",audit),\
                 patch.object(pipeline.site_pricing,"load_price_overrides",side_effect=load_prices):
                result=pipeline.patch_catalog(fixture_manifest(),live)
                self.assertEqual(result["main_count"],1)
                self.assertEqual(result["legacy_count"],0)
                self.assertEqual(len(result["source_modified_files"]),3)
                # Source on disk remains untouched in inspect/preflight.
                self.assertEqual(load_workbook(ov).active["B2"].value,950)
                self.assertEqual(load_workbook(ov).active["D2"].value,None)
                new=load_workbook(BytesIO(result["changes"][ov]))
                self.assertEqual(new.active["B2"].value,450)
                self.assertEqual(new.active["D2"].value,950)
                staged=load_workbook(BytesIO(result["changes"][feed]))
                self.assertEqual(staged.active.cell(5,34).value,450)
                self.assertEqual(staged.active.cell(5,24).value,"Updated tested title")
                self.assertEqual(staged.active.cell(5,19).value,"photo_url_remains_unmodified")
                self.assertEqual(staged.active.cell(6,34).value,950)
                content=json.loads(result["changes"][cm])
                self.assertEqual(content["products"]["test-sku"]["text"]["avito"]["title"],
                                 "Updated tested title")

    def test_site_drift_blocks_entire_plan(self):
        with self.assertRaises(AvitoPriceGuardError):
            pipeline.validate_manifest(
              fixture_manifest(targets=[{
                "avito_id":123,"kind":"main","sku":"test",
                "expected_title":"X","expected_price":950,
                "approved_price":450,
                "expected_site_price":None
              }])
            )

    def test_legacy_price_can_be_separate_from_master_without_feed_rebinding(self):
        row={"avito_id":456,"kind":"legacy","sku":None,
             "expected_price":450,"approved_price":490,
             "expected_title":"Old manual ad"}
        self.assertEqual(pipeline.validate_manifest(
            fixture_manifest(targets=[row]))[0]["kind"],"legacy")
        with self.assertRaises(AvitoPriceGuardError):
            pipeline.validate_manifest(fixture_manifest(
                targets=[dict(row,approved_description="do not silently edit legacy text")]))

if __name__=="__main__":
    unittest.main()
