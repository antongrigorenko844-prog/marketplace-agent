"""Safety and performance-contract tests for generic Product Publishing v3.

Runs without marketplace API secrets; uses the real approved five-SKU batch
and mocked external clients. Absolutely no seller writes.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import product_publishing_v3 as v3
from scripts import product_intake_v2 as v2

REQUEST = ROOT / "data/product_intake_v2/requests/leoparts-five-20261123.json"
OZON_EVIDENCE = ROOT / "data/leoparts_ozon_five_live_readback_20261123.json"


class SafeProductPublishingV3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.batch = v2.read_json(REQUEST)

    def test_real_five_sku_plan_with_avito_disabled(self):
        report = v3.build_plan(self.batch, v3.SUPPORTED)
        self.assertEqual(report["schema_version"], 3)
        self.assertEqual(report["summary"]["sku_count"], 5)
        self.assertEqual(report["summary"]["channel_tasks"], 25)
        self.assertEqual(report["summary"]["disabled"], 5)
        self.assertEqual(report["seller_writes"], 0)
        self.assertEqual(report["read_phases"], {})
        for sku, row in report["items"].items():
            self.assertEqual(row["channels"]["avito"]["state"], "disabled")
            self.assertEqual(row["approved_prices"]["ozon"],
                             next(x for x in self.batch["items"] if x["sku"] == sku)["prices"]["ozon"])
        self.assertEqual(report["items"]["6t30-solenoids-gen2"]["media"]["state"],
                         "verification_needed_before_upload")
        for sku in ("tool-dq200-acc-restore", "tool-dsg-pressure-hp"):
            self.assertEqual(report["items"][sku]["media"]["state"], "verified")
            self.assertEqual(report["items"][sku]["channels"]["ozon"]["state"], "needs_category")

    def test_explicit_apply_mode_does_not_exist(self):
        # CLI only exposes plan or live; no accidental seller writes.
        self.assertEqual(set(v3.PROBES), set(v3.ACTIVE))
        self.assertNotIn("avito", v3.PROBES)
        self.assertNotIn("apply", ("plan", "live"))

    def test_reject_unsafe_batch_and_duplicate_channels(self):
        with self.assertRaises(v2.GuardError):
            v3.load_batch("../../secrets")
        for bad in ("ozon,ozon", "ozon,random", ",", ""):
            with self.assertRaises(v2.GuardError, msg=bad):
                v3.parse_channels(bad)

    def test_plan_is_offline_and_cannot_trigger_live_probes(self):
        with patch.dict(v3.PROBES, {"ozon": lambda *_: self.fail("called Ozon")}, clear=False):
            result = v3.build_plan(self.batch, ("ozon",))
        self.assertEqual(result["summary"]["channel_tasks"], 5)
        self.assertEqual(result["read_phases"], {})
        self.assertNotIn("avito", result["items"]["tool-dsg-pressure-hp"]["channels"])

    def test_local_media_hash_cache_is_path_specific(self):
        row = copy.deepcopy(self.batch["items"][3])
        good = row["media"][0]
        bad = {**good, "path": "drafts/new-products/dsg-tools-20261120/media/missing.png"}
        row["media"] = [good, bad]
        state = v3.media_report(row, {})
        self.assertEqual(state["verified_local"], 1)
        self.assertEqual(state["state"], "blocked")
        self.assertEqual(len(state["issues"]), 1)

    def test_live_errors_isolated_between_channels_and_skus(self):
        calls = {}
        def ozon(items):
            calls["ozon"] = [i["sku"] for i in items]
            return {i["sku"]: {"state": "selling"} for i in items}, 4
        def market(items):
            raise RuntimeError("HTTP 429: temporary")
        report = v3.run_live(self.batch, ("ozon", "yandex_market"),
                             probes={"ozon": ozon, "yandex_market": market})
        self.assertEqual(report["summary"]["selling_verified"], 5)
        self.assertEqual(report["summary"]["api_errors"], 5)
        self.assertEqual(report["read_phases"]["ozon"], 4)
        self.assertIsNone(report["read_phases"]["yandex_market"])
        self.assertEqual(len(calls["ozon"]), 5)
        self.assertEqual(report["seller_writes"], 0)

    def test_ozon_batch_fetch_only_once_per_method(self):
        item = self.batch["items"][0]
        calls = []
        def record(name, rows):
            def result(*args, **kwargs):
                calls.append(name)
                return copy.deepcopy(rows)
            return result
        mock = types.SimpleNamespace(
            list_products=record("list", [{"offer_id": item["sku"], "product_id": 123}]),
            get_product_info_list=record("info", [{
                "offer_id": item["sku"], "id": 123, "statuses": {
                    "is_created": True, "moderate_status": "approved", "status_name": "Продаётся"
                }, "errors": []}]),
            get_prices_and_stocks=record("prices", [{
                "offer_id": item["sku"], "price": {"price": str(item["prices"]["ozon"])}}]),
            get_stocks=record("stocks", [{
                "offer_id": item["sku"], "stocks": [{"type": "fbs", "present": 20}]}]))
        with patch.dict(sys.modules, {"ozon_client": mock}):
            out, reads = v3.verify_ozon([item] * 5)
        self.assertEqual(reads, 4)
        self.assertEqual(calls, ["list", "info", "prices", "stocks"])
        self.assertEqual(out[item["sku"]]["state"], "selling")

    def test_ozon_wrong_price_is_not_selling(self):
        item = self.batch["items"][3]
        mock = types.SimpleNamespace(
            list_products=lambda **_: [{"offer_id": item["sku"], "product_id": 4}],
            get_product_info_list=lambda _: [{
                "offer_id": item["sku"], "statuses": {"is_created": True,
                "moderate_status": "approved", "status_name": "Продаётся"}}],
            get_prices_and_stocks=lambda _: [{"offer_id": item["sku"], "price": {"price": "9"}}],
            get_stocks=lambda _: [{"offer_id": item["sku"], "stocks": [{"type": "fbs", "present": 3}]}])
        with patch.dict(sys.modules, {"ozon_client": mock}):
            result, _ = v3.verify_ozon([item])
        self.assertEqual(result[item["sku"]]["state"], "price_mismatch_review")

    def test_yandex_prices_read_is_one_batch_not_one_request_per_sku(self):
        calls = []
        def request(method, path, *, json_body=None):
            calls.append(json_body)
            return 200, {"result": {"offers": [
                {"offerId": item["sku"], "price": {"value": item["prices"]["yandex_market"]}}
                for item in self.batch["items"]]}}
        mock = types.SimpleNamespace(
            get_business_id=lambda: 77,
            get_all_offers=lambda archived=False: [
                {"sku": item["sku"], "name": item["title"]} for item in self.batch["items"]],
            _request=request)
        with patch.dict(sys.modules, {"yandex_market_client": mock}):
            observations, phases = v3.verify_yandex_market(self.batch["items"])
        self.assertEqual(phases, 3)
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(calls[0]["offerIds"]), 5)
        self.assertEqual(set(s["state"] for s in observations.values()), {"business_catalog_verified"})

    def test_actual_ozon_five_readback_snapshot_is_proven_selling(self):
        snapshot = json.loads(OZON_EVIDENCE.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["summary"]["for_sale"], 5)
        self.assertEqual(snapshot["summary"]["positive_fbs"], 5)
        self.assertEqual(snapshot["summary"]["prices_verified"], 5)
        self.assertTrue(all(x["moderation"] == "approved" for x in snapshot["items"].values()))
        self.assertTrue(all(x["fbs_present"] > 0 for x in snapshot["items"].values()))

    def test_fixed_dispatch_has_no_apply_or_file_escape(self):
        expected = ROOT / "data" / "product_intake_v3" / "dispatch.json"
        loaded = v3.load_dispatch(expected)
        self.assertEqual(loaded["batch_id"], "leoparts-five-20261123")
        self.assertEqual(loaded["mode"], "plan")
        self.assertEqual(loaded["channels"], v3.SUPPORTED)
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder) / "dispatch.json"
            outside.write_text(expected.read_text(encoding="utf-8"), encoding="utf-8")
            with self.assertRaises(v2.GuardError):
                v3.load_dispatch(outside)

    def test_atomic_report_writes_no_product_files(self):
        report = v3.build_plan(self.batch, ("ozon",))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "batch.json"
            v3.atomic_report(path, report)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["seller_writes"], 0)
            self.assertFalse((Path(folder) / "batch.json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
