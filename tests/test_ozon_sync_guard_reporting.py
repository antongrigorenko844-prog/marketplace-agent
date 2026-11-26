"""Legacy sync must distinguish a local protection skip from a failed write."""
import ast
import copy
import io
from pathlib import Path
import types
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch


class SyncGuardReportingTests(unittest.TestCase):
    def run_sync(self, response):
        # Isolate the CLI function without booting unrelated marketplace clients.
        source = ast.parse((Path(__file__).resolve().parents[1] / "main.py").read_text())
        node = next(n for n in source.body if isinstance(n, ast.FunctionDef)
                    and n.name == "cmd_sync_ozon_prices")
        module = ast.Module(body=[node], type_ignores=[])
        env = {"_data_path": lambda x: x, "_scoped_prices": lambda edits, article: edits}
        exec(compile(module, "main.py", "exec"), env)
        edits = {"protected": {"price": 100, "old_price": 200},
                 "ordinary": {"price": 100, "old_price": 200}}
        ozon = types.SimpleNamespace(
            get_prices_and_stocks=lambda ids: [
                {"offer_id": sku, "price": {"price": 110, "old_price": 200}} for sku in ids],
            update_prices=lambda rows: copy.deepcopy(response))
        catalog = types.SimpleNamespace(load_catalog_edits=lambda path: edits,
                                        _load_overrides=lambda: {})
        with patch.dict("sys.modules", {"ozon_client": ozon, "catalog_editor": catalog}), redirect_stdout(io.StringIO()):
            return env["cmd_sync_ozon_prices"](False)

    @staticmethod
    def guarded():
        return {"result": [
            {"offer_id": "protected", "updated": False, "guard_skipped": True,
             "errors": [{"code": "OZON_CONTROL_GUARD_SKIP"}]},
            {"offer_id": "ordinary", "updated": True, "errors": []}],
            "ozon_control_guard": {"marketplace_writes_for_skipped": 0,
                                   "skipped": [{"sku": "protected"}]}}

    def test_protected_skip_and_confirmed_unmanaged_write_succeed(self):
        self.assertEqual(self.run_sync(self.guarded()), 0)

    def test_real_error_on_other_product_still_fails(self):
        response = self.guarded()
        response["result"][1]["updated"] = False
        response["result"][1]["errors"] = [{"code": "PRICE_INVALID"}]
        self.assertEqual(self.run_sync(response), 1)

    def test_skip_flag_without_verified_local_metadata_still_fails(self):
        response = self.guarded()
        response.pop("ozon_control_guard")
        self.assertEqual(self.run_sync(response), 1)

    def test_guard_flag_does_not_hide_extra_error(self):
        response = self.guarded()
        response["result"][0]["errors"].append({"code": "PRICE_INVALID"})
        self.assertEqual(self.run_sync(response), 1)

    def test_duplicate_confirmation_still_fails(self):
        response = self.guarded()
        response["result"].append(response["result"][0].copy())
        self.assertEqual(self.run_sync(response), 1)


if __name__ == "__main__":
    unittest.main()
