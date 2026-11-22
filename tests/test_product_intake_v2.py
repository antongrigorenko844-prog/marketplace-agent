"""Product Intake v2: offline acceptance tests for the five existing LEOPARTS products.

No seller secrets, HTTP calls, price files writes or card creation.
"""
import copy
import hashlib
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import product_intake_v2 as intake

REQUEST = ROOT / "data/product_intake_v2/requests/leoparts-five-20261123.json"


class ProductIntakeV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.batch = intake.read_json(REQUEST)

    def test_five_exact_skus_and_approved_prices(self):
        items = intake.validate(self.batch)["items"]
        self.assertEqual(
            [x["sku"] for x in items],
            ["6t30-solenoids-gen2", "6t30-tcm-gen1", "6t30-tcm-gen2",
             "tool-dq200-acc-restore", "tool-dsg-pressure-hp"],
        )
        self.assertEqual([x["prices"]["ozon"] for x in items],
                         [19880,58850,58850,38800,28800])
        self.assertEqual([x["prices"]["avito"] for x in items],
                         [11880,35350,35350,24800,18800])
        self.assertEqual([x["prices"]["tilda"] for x in items],
                         [11880,35350,35350,24800,18800])
        self.assertEqual([x["stock"] for x in items], [20,3,3,3,3])

    def test_requires_owner_approval(self):
        change=copy.deepcopy(self.batch)
        change["approved"]=False
        with self.assertRaises(intake.GuardError):
            intake.validate(change)

    def test_rejects_duplicates_and_invalid_price(self):
        change=copy.deepcopy(self.batch)
        change["items"].append(copy.deepcopy(change["items"][0]))
        with self.assertRaises(intake.GuardError):
            intake.validate(change)
        change=copy.deepcopy(self.batch)
        change["items"][0]["prices"]["avito"]=-1
        with self.assertRaises(intake.GuardError):
            intake.validate(change)
        change=copy.deepcopy(self.batch)
        change["items"][0]["stock"]=20.0
        with self.assertRaises(intake.GuardError):
            intake.validate(change)

    def test_rejects_wrong_generation_image_or_path_escape(self):
        change=copy.deepcopy(self.batch)
        change["items"][0]["media"][0]["approved"]=False
        with self.assertRaises(intake.GuardError):
            intake.validate(change)
        change=copy.deepcopy(self.batch)
        change["items"][3]["media"][0]["path"]="../../secret.png"
        with self.assertRaises(intake.GuardError):
            intake.validate(change)

    def test_rejects_untrusted_url_host(self):
        change=copy.deepcopy(self.batch)
        change["items"][0]["media"][0]={"source":"public_url","url":"https://invalid.example/cover.png",
                                         "sha256":"a"*64,"approved":True}
        with self.assertRaises(intake.GuardError):
            intake.validate(change)

    def test_fails_closed_on_unknown_media_and_unknown_market_category(self):
        for item in self.batch["items"][:3]:
            paths,reasons=intake.media_preflight(item)
            self.assertEqual(paths,[])
            self.assertTrue(reasons and "ChatGPT Library" in reasons[0])

    def test_exact_two_dsg_image_hashes_are_present(self):
        for item in self.batch["items"][3:]:
            paths,reasons=intake.media_preflight(item)
            self.assertFalse(reasons)
            self.assertEqual(len(paths),len(item["media"]))
            for img,path in zip(item["media"], paths):
                self.assertTrue(intake.verified_file(path,img["sha256"]))

    def test_offline_inspect_no_price_or_status_writes(self):
        files=[ROOT/"data/master_control.xlsx", ROOT/"data/avito_site_price_overrides.xlsx",
               ROOT/"data/ozon_catalog.xlsx"]
        baseline={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()}
        target=intake.STATUS_DIR/(self.batch["batch_id"]+".json")
        existed=target.exists()
        # Mock network modules: offline inspect must never import or call them.
        with patch.object(intake, "run_wb", side_effect=AssertionError("Live WB called")), \
             patch.object(intake, "run_market", side_effect=AssertionError("Live Market called")), \
             patch.object(intake, "run_tilda", side_effect=AssertionError("Live Tilda called")), \
             patch.object(intake, "read_ozon", side_effect=AssertionError("Live Ozon called")):
            result=intake.run(self.batch,mode="inspect",offline=True)
        self.assertEqual(result["summary"]["total"],25)
        self.assertEqual(result["summary"]["blocked"],15)
        self.assertEqual(len(result["items"]),5)
        self.assertEqual(result["items"]["6t30-solenoids-gen2"]["media"]["state"],"blocked")
        self.assertEqual(result["items"]["tool-dq200-acc-restore"]["media"]["state"],"verified")
        self.assertEqual(existed,target.exists())
        self.assertEqual(baseline,
            {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()})

    def test_offline_review_ignores_incomplete_previous_report(self):
        # Reproduce the first CI failure: shell output redirection opens an
        # existing report as an empty file before Python starts.
        import tempfile
        with tempfile.TemporaryDirectory() as t:
            folder = Path(t)
            stale = folder / (self.batch["batch_id"] + ".json")
            stale.write_text("", encoding="utf-8")
            with patch.object(intake, "STATUS_DIR", folder):
                report = intake.run(self.batch, mode="inspect", offline=True)
            self.assertEqual(report["summary"]["total"], 25)
            self.assertEqual(stale.read_text(encoding="utf-8"), "")

    def test_rejects_offline_apply(self):
        with self.assertRaises(intake.GuardError):
            intake.run(self.batch,mode="apply",offline=True)

    def test_unique_matches_is_strict_and_casefold(self):
        cards=[{"vendorCode":"test-1"},{"vendorCode":"TEST-1"},{"vendorCode":"test-2"}]
        self.assertEqual(len(intake.unique_matches(cards,"test-1","vendorCode")),2)

    def test_manual_price_protection_for_existing_cards(self):
        import inspect
        wb_source=inspect.getsource(intake.run_wb)
        market_source=inspect.getsource(intake.run_market)
        self.assertNotIn("wb.update_prices(",wb_source)
        self.assertNotIn('wb._request("PUT"',wb_source)
        # No business-price API updates of existing offers from stale manifest.
        self.assertNotIn('"/v2/businesses/{business}/offer-prices/updates"',market_source)


if __name__=="__main__":
    unittest.main(verbosity=2)
