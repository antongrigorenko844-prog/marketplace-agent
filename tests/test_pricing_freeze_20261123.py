"""Offline regression checks for LEOPARTS price freeze (no seller API calls)."""
from __future__ import annotations
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import pricing_policy
import site_pricing


class PricingFreezeTests(unittest.TestCase):
    def test_policy_freezes_both_automatic_price_models(self):
        policy = pricing_policy.load_policy()
        self.assertEqual(policy["mode"], "manual_prices_only")
        self.assertFalse(policy["automatic_avito_site_repricing"])
        self.assertTrue(pricing_policy.auto_repricing_frozen())

    def test_invalid_or_missing_policy_fails_closed(self):
        with tempfile.TemporaryDirectory() as t:
            self.assertRaises(pricing_policy.PricingFrozenError, pricing_policy.load_policy, Path(t) / "none.json")
            p = Path(t) / "policy.json"
            p.write_text('{"schema_version":1,"mode":"legacy_automatic","preserve_existing_prices":true}')
            self.assertFalse(pricing_policy.auto_repricing_enabled(p))
            p.write_text("not-json")
            self.assertRaises(pricing_policy.PricingFrozenError, pricing_policy.load_policy, p)

    def test_auto_recalc_does_not_even_open_missing_workbook(self):
        # A successful no-op even without a valid master proves no workbook write
        # or latent 70/60%/equal-profit conversion is attempted.
        with patch.object(site_pricing, "save_price_overrides", side_effect=AssertionError("Unexpected write")):
            self.assertEqual(
                site_pricing.recalc_from_master("/path/that/does/not/exist.xlsx", dry_run=False),
                [],
            )

    def test_auto_set_price_fails_before_writing(self):
        with patch.object(site_pricing, "save_price_overrides", side_effect=AssertionError("Unexpected write")):
            with self.assertRaises(pricing_policy.PricingFrozenError):
                site_pricing.set_price_override("not-approved-test", 1000)

    def test_preview_is_still_available_without_publishing(self):
        with tempfile.TemporaryDirectory() as t:
            master = Path(t) / "master.xlsx"
            override = Path(t) / "overrides.xlsx"  # intentionally does not exist
            wb = Workbook()
            ws = wb.active
            ws.title = "Товары"
            ws.append(["Артикул (offer_id)", "Цена, ₽"])
            ws.append(["safe-example", 10000])
            wb.save(master)
            before = hashlib.sha256(master.read_bytes()).hexdigest()
            with patch.object(site_pricing, "save_price_overrides", side_effect=AssertionError("Unexpected write")):
                planned = site_pricing.recalc_from_master(str(master), str(override), dry_run=True)
            self.assertEqual(planned[0][0], "safe-example")
            self.assertTrue(planned[0][2] > 0)
            self.assertFalse(override.exists())
            self.assertEqual(hashlib.sha256(master.read_bytes()).hexdigest(), before)

    def test_fixed_direct_prices_are_required_for_legacy_creation(self):
        skus = ["new-sku-1", "new-sku-2"]
        av = {"new-sku-1":11880, "new-sku-2":35350}
        site = {"new-sku-1":11880, "new-sku-2":35350}
        fixed = set(skus)
        self.assertTrue(pricing_policy.require_legacy_new_product_prices(skus, av, site, fixed))
        with self.assertRaises(pricing_policy.PricingFrozenError):
            pricing_policy.require_legacy_new_product_prices(skus, av, {"new-sku-1":11880}, fixed)
        with self.assertRaises(pricing_policy.PricingFrozenError):
            pricing_policy.require_legacy_new_product_prices(skus, av, site, {"new-sku-1"})

    def test_actual_price_files_preserved_during_frozen_recalc(self):
        master = ROOT / "data/master_control.xlsx"
        override = ROOT / "data/avito_site_price_overrides.xlsx"
        if not master.exists() or not override.exists():
            self.skipTest("Repository test fixtures not available")
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in (master,override)}
        self.assertEqual(site_pricing.recalc_from_master(str(master), str(override), dry_run=False), [])
        self.assertEqual(before, {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in before})

    def test_legacy_entrypoints_guarded_before_publish(self):
        main=(ROOT/"main.py").read_text(encoding="utf-8")
        launch=main.split("def cmd_push_new_product_all() -> int:",1)[1].split("def cmd_finish_new_product()",1)[0]
        self.assertLess(launch.index("pricing_policy.require_legacy_new_product_prices("),
                        launch.index("ozon_client.import_products(ozon_items)"))
        self.assertNotIn("site_pricing.set_price_override(offer_id, half)",launch)
        self.assertIn("pricing_policy.auto_repricing_frozen()",main)
        self.assertIn("FREEZE: автосоздание новых WB/Ozon карточек",main)

if __name__=="__main__":
    unittest.main(verbosity=2)
