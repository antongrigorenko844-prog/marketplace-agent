import copy
import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from scripts.ozon_profit_snapshot import (
    READ_ALLOWLIST, ReadOnlyOzonClient, SnapshotError, catalogue_identity,
    collect_finance, collect_promotions, collect_protection, collect_snapshot,
    finance_mapping, flag, money, normalize_protection, price_schema_errors,
    read_master, read_pages, scoped_rows, main,
)


class Response:
    def __init__(self, status=200, payload=None, headers=None):
        self.status_code = status
        self.payload = payload if payload is not None else {}
        self.headers = headers or {}

    def json(self):
        return self.payload

    @property
    def text(self):
        raise AssertionError("Response bodies must not be read for error reporting")


class Session:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def request(self, method, url, **kwargs):
        self.calls.append((method, url, kwargs))
        return next(self.responses)


class FakeClient:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.calls = []

    def post(self, path, body):
        self.calls.append(("POST", path, copy.deepcopy(body)))
        value = next(self.responses)
        if isinstance(value, Exception):
            raise value
        return value

    def request(self, method, path):
        self.calls.append((method, path, None))
        return next(self.responses)


class ReadTransportTests(unittest.TestCase):
    def test_cli_fatal_exception_is_exit_one_and_redacted(self):
        output = io.StringIO()
        with patch("scripts.ozon_profit_snapshot.ReadOnlyOzonClient", side_effect=RuntimeError("secret-key")), \
                patch("sys.stdout", output):
            code = main([])
        self.assertEqual(code, 1)
        self.assertNotIn("secret-key", output.getvalue())
        self.assertEqual(json.loads(output.getvalue())["status"], "failed")

    def test_cli_completed_partial_is_exit_two(self):
        manifest = {"status": "partial", "catalog": {"active_count": 147},
                    "sections": {"protection": {"status": "partial"}}}
        with patch("scripts.ozon_profit_snapshot.ReadOnlyOzonClient"), \
                patch("scripts.ozon_profit_snapshot.collect_snapshot", return_value=manifest), \
                patch("sys.stdout", io.StringIO()):
            self.assertEqual(main([]), 2)

    def test_exact_allowlist_blocks_writes_and_other_hosts_before_request(self):
        session = Session([])
        client = ReadOnlyOzonClient("client", "secret", session=session)
        for method, path in [
            ("POST", "/v1/product/import/prices"),
            ("POST", "/v1/product/action/timer/update"),
            ("POST", "/v1/actions/products/activate"),
            ("GET", "/v3/product/list"),
            ("POST", "https://evil.example/v3/product/list"),
        ]:
            with self.subTest(path=path), self.assertRaises(SnapshotError):
                client.request(method, path, {})
        self.assertEqual(session.calls, [])
        self.assertEqual(len(READ_ALLOWLIST), 10)
        with self.assertRaises(SnapshotError):
            ReadOnlyOzonClient("client", "secret", base_url="https://api-seller.ozon.ru.evil.example")

    def test_redirects_are_disabled_and_errors_do_not_expose_body_or_credentials(self):
        session = Session([Response(302, {"message": "secret"}, {"Location": "https://elsewhere"})])
        client = ReadOnlyOzonClient("client", "secret", session=session)
        with self.assertRaises(SnapshotError) as caught:
            client.request("GET", "/v1/actions")
        self.assertFalse(session.calls[0][2]["allow_redirects"])
        self.assertEqual(caught.exception.safe(), {"code": "http_error", "endpoint": "/v1/actions", "http_status": 302})
        self.assertNotIn("secret", json.dumps(client.calls))

    def test_429_and_5xx_retry_at_most_three_attempts(self):
        session = Session([Response(429, headers={"Retry-After": "900"}), Response(503), Response(503)])
        waits = []
        client = ReadOnlyOzonClient("client", "secret", session=session, sleep=waits.append)
        with self.assertRaises(SnapshotError):
            client.post("/v3/product/list", {})
        self.assertEqual(len(session.calls), 3)
        self.assertEqual(waits, [30, 2.0])


class PaginationAndIdentityTests(unittest.TestCase):
    def test_all_pages_preserve_rows(self):
        client = FakeClient([
            {"result": {"items": [{"product_id": 1}], "last_id": "next", "total": 2}},
            {"result": {"items": [{"product_id": 2}], "last_id": "", "total": 2}},
        ])
        rows, meta = read_pages(client, "/v3/product/list", {"limit": 1}, limit=1)
        self.assertEqual([row["product_id"] for row in rows], [1, 2])
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(client.calls[1][2]["last_id"], "next")

    def test_repeat_and_missing_cursor_do_not_claim_complete(self):
        cases = [
            ([{"items": [{"product_id": 1}], "last_id": "x"},
              {"items": [{"product_id": 2}], "last_id": "x"}], "pagination_repeated_cursor"),
            ([{"items": [{"product_id": 1}]}], "pagination_missing_cursor"),
        ]
        for responses, code in cases:
            with self.subTest(code=code):
                _, meta = read_pages(FakeClient(responses), "/v3/product/list", {}, limit=1)
                self.assertEqual(meta["status"], "partial")
                self.assertIn(code, [e["code"] for e in meta["errors"]])

    def test_missing_schema_is_not_empty_success(self):
        _, meta = read_pages(FakeClient([{"result": {}}]), "/v3/product/list", {})
        self.assertEqual(meta["status"], "error")
        self.assertEqual(meta["errors"][0]["code"], "rows_key_missing")

    def test_explicit_batch_can_finish_with_retained_final_cursor(self):
        client = FakeClient([{"items": [{"product_id": 1}], "last_id": "still-present"}])
        _, meta = read_pages(client, "/v4/product/info/stocks", {}, limit=1, expected_ids={"1"})
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(len(client.calls), 1)

    def test_duplicate_identity_and_missing_requested_id_are_reported(self):
        client = FakeClient([{"items": [{"product_id": 1}, {"product_id": 1}]}])
        _, meta = read_pages(client, "/v4/product/info/stocks", {}, expected_ids={"1", "2"})
        codes = [e["code"] for e in meta["errors"]]
        self.assertIn("duplicate_row_identity", codes)
        self.assertIn("missing_expected_products", codes)
        self.assertEqual(meta["coverage"]["missing_product_ids"], ["2"])

    def test_exact_case_and_unicode_identity_and_conflicts(self):
        valid, meta = catalogue_identity([
            {"offer_id": "Dq500", "product_id": 1},
            {"offer_id": "DQ500", "product_id": 2},
            {"offer_id": "ABC", "product_id": 3},
            {"offer_id": "ABС", "product_id": 4},  # Cyrillic final С
        ])
        self.assertEqual(len(valid), 4)
        self.assertEqual(meta["status"], "ok")
        valid, meta = catalogue_identity([
            {"offer_id": "A", "product_id": 1},
            {"offer_id": "B", "product_id": 1},
            {"offer_id": "C", "product_id": 3, "id": 4},
        ])
        self.assertEqual(valid, {})
        self.assertIn("duplicate_product_id", [e["code"] for e in meta["errors"]])
        self.assertIn("conflicting_product_ids", [e["code"] for e in meta["errors"]])

    def test_scoped_response_cannot_remap_same_pid_to_different_offer(self):
        client = FakeClient([{"items": [{"offer_id": "a", "id": 1}]}])
        _, meta = scoped_rows(client, "/v3/product/info/list", {"A": "1"}, kind="info")
        self.assertEqual(meta["status"], "partial")
        self.assertEqual(meta["coverage"]["missing_offer_ids"], ["A"])
        self.assertEqual(meta["coverage"]["conflicting_offer_ids"], ["a"])


class PriceAndProtectionTests(unittest.TestCase):
    def test_money_objects_flags_and_raw_promotion_fields_are_preserved(self):
        row = {"id": 1, "price": {"amount": "1200", "currency": "RUB"},
               "action_price": {"amount": "1100", "currency": "RUB"},
               "current_boost": 42.7, "min_boost": 15, "current": 9, "min": 3,
               "price_min_elastic": {"amount": "1300", "currency": "RUB"},
               "price_max_elastic": {"amount": "900", "currency": "RUB"},
               "new_unknown_field": {"preserve": True}, "stock": 0}
        original = copy.deepcopy(row)
        client = FakeClient([{"result": [{"id": 123, "title": "Test"}]},
                             {"result": {"products": [row], "total": 1}},
                             {"result": {"products": [], "total": 0}}])
        actions, meta = collect_promotions(client, {"A": "1"})
        saved = actions[0]["sections"]["participants"]["rows"][0]
        self.assertEqual({k: v for k, v in saved.items() if k != "mapped_offer_id"}, original)
        self.assertEqual(saved["mapped_offer_id"], "A")
        self.assertEqual(meta["status"], "ok")
        self.assertEqual(str(money({"amount": "1 200,50", "currency": "RUB"})), "1200.50")
        self.assertIs(flag("DISABLED"), False)
        self.assertIsNone(flag("false"))

    def test_invalid_money_or_missing_minimum_is_not_verified(self):
        for value in [None, True, "NaN", "Infinity", {"value": "oops"}, {"amount": "1", "currency": "USD"}]:
            with self.subTest(value=value), self.assertRaises(SnapshotError):
                money(value)
        self.assertEqual(price_schema_errors([{"offer_id": "A", "price": {
            "price": "1200", "min_price": {"amount": "1000", "currency": "RUB"},
            "auto_action_enabled": "DISABLED"}}]), [])
        errors = price_schema_errors([{"offer_id": "A", "price": {"price": "1200",
                                      "auto_action_enabled": "UNKNOWN"}}])
        self.assertIn("price_field_missing", [x["code"] for x in errors])
        self.assertIn("price_flag_schema_unknown", [x["code"] for x in errors])

    def test_protection_requires_explicit_flag_and_expiry(self):
        now = datetime(2026, 11, 27, tzinfo=timezone.utc)
        future = {"product_id": 1, "min_price_for_auto_actions_enabled": True,
                  "expired_at": "2026-12-01T00:00:00Z"}
        self.assertIs(normalize_protection(future, {"1": "A"}, now)["active"], True)
        self.assertIs(normalize_protection({**future, "expired_at": "2026-11-01T00:00:00Z"}, {}, now)["active"], False)
        self.assertIsNone(normalize_protection({"product_id": 1, "auto_action_enabled": True}, {}, now)["active"])
        # An auto-add flag is not minimum-price protection.
        self.assertIsNone(normalize_protection({"product_id": 1, "auto_add_to_ozon_actions_list_enabled": True,
                                                "expired_at": future["expired_at"]}, {}, now)["active"])

    def test_timer_probe_unknown_schema_and_missing_id_are_partial(self):
        now = datetime(2026, 11, 27, tzinfo=timezone.utc)
        client = FakeClient([{"new_schema": []}])
        data, meta = collect_protection(client, {"A": "1"}, now=now)
        self.assertEqual(meta["status"], "error")
        self.assertEqual(data["batches"][0]["response"], {"new_schema": []})
        self.assertEqual(client.calls[0][2], {"product_ids": [1]})
        client = FakeClient([{"statuses": [{"product_id": 1, "min_price_for_auto_actions_enabled": False}]}])
        _, meta = collect_protection(client, {"A": "1", "B": "2"}, now=now)
        self.assertEqual(meta["status"], "partial")
        self.assertEqual(meta["coverage"]["missing_product_ids"], ["2"])


class MasterAndFinanceTests(unittest.TestCase):
    def test_master_read_only_keeps_owner_scope_and_rejects_duplicate_sku(self):
        from openpyxl import Workbook
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "master.xlsx"
            book = Workbook()
            sheet = book.active
            sheet.title = "Товары"
            sheet.append(["Артикул", "Название товара", "Цена, ₽", "Закупка, ₽", "Ozon (да/нет)", "Заметки"])
            sheet.append(["A", "part", 1200, 500, "нет", "Пока не продаём"])
            sheet.append(["B", "dup", 100, 50, "да", None])
            sheet.append(["B", "dup2", 100, 50, "да", None])
            book.save(path)
            before = path.read_bytes()
            data, meta = read_master(path)
            self.assertEqual(data["A"], {"title": "part", "price": 1200, "cost": 500,
                                         "ozon_enabled": "нет", "notes": "Пока не продаём"})
            self.assertNotIn("B", data)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(meta["status"], "partial")

    def test_marketplace_sku_collision_is_never_assigned_arbitrarily(self):
        mapping, coverage = finance_mapping([
            {"id": 1, "offer_id": "A", "sources": [{"sku": 100}]},
            {"id": 2, "offer_id": "B", "sources": [{"sku": 100}, {"sku": 200}]},
        ], {"A": "1", "B": "2"})
        self.assertEqual(mapping["list"], [{"sku": "200", "offer_id": "B"}])
        self.assertEqual(coverage["conflicting_marketplace_skus"], ["100"])

    def test_finance_reuses_parser_for_30_complete_days_and_removes_private_samples(self):
        import price_audit
        import ozon_client
        fixture = {"total_amount": {"amount": "100", "currency": "RUB"},
                   "accrued_category": "POSTING", "posting": {"posting_number": "PRIVATE_POSTING",
                   "products": [{"sku": 500, "quantity": 1,
                   "commission": {"seller_price": "200", "sale_commission": "-80"},
                   "delivery": {"total_accrued": "-20"}}]}}

        class FinanceClient:
            def __init__(self):
                self.dates = []

            def post(self, path, body):
                if path.endswith("/types"):
                    return {"accrual_types": []}
                self.dates.append(body["date"])
                return {"accruals": [fixture] if len(self.dates) == 1 else [], "last_id": ""}

        client = FinanceClient()
        originals = (ozon_client._post, price_audit.DATA, price_audit.FIN_PATH, price_audit.datetime,
                     price_audit._accruals_by_day)
        original_fetch = price_audit.fetch_ozon_finance
        private_paths = []

        def capture_fetch(days):
            private_paths.append(Path(price_audit.FIN_PATH))
            return original_fetch(days=days)

        with patch("time.sleep"), patch.object(price_audit, "fetch_ozon_finance", side_effect=capture_fetch):
            result, meta = collect_finance(client, {"list": [{"sku": "500", "offer_id": "A"}], "details": []},
                {"conflicting_marketplace_skus": []}, now=datetime(2026, 11, 27, 3, tzinfo=timezone.utc))
        self.assertEqual((client.dates[0], client.dates[-1], len(client.dates)), ("2026-10-28", "2026-11-26", 30))
        self.assertFalse(result["window"]["includes_partial_current_day"])
        self.assertEqual(result["items"]["A"]["sold"], 1)
        self.assertEqual(result["items"]["A"]["net"], 100)
        self.assertEqual(meta["status"], "ok")
        self.assertNotIn("PRIVATE_POSTING", json.dumps(result))
        self.assertNotIn("samples", result)
        self.assertNotIn("debug_records", result)
        self.assertTrue(all(not path.exists() for path in private_paths))
        self.assertEqual((ozon_client._post, price_audit.DATA, price_audit.FIN_PATH, price_audit.datetime,
                          price_audit._accruals_by_day), originals)

    def test_invalid_finance_day_excluded_and_type_failure_remains_visible(self):
        class FinanceClient:
            def post(self, path, body):
                if path.endswith("/types"):
                    raise SnapshotError("http_error", path=path, http_status=500)
                return {"accruals": [{"total_amount": "NaN", "accrued_category": "NON_ITEM"}]}
        with patch("time.sleep"):
            data, meta = collect_finance(FinanceClient(), {"list": [], "details": []}, {}, days=1,
                                        now=datetime(2026, 11, 27, tzinfo=timezone.utc))
        self.assertFalse(data["collection_complete"])
        self.assertEqual(data["coverage"]["failed_dates"], ["2026-11-26"])
        self.assertNotEqual(meta["status"], "ok")
        self.assertEqual(data["operations"], 0)
        self.assertIn("finance_types", [e.get("section") for e in data["errors"]])

    def test_empty_or_changed_catalogue_cannot_be_reported_as_147_verified(self):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp) / "isolated"
            fake_finance = ({"items": {}, "collection_complete": True}, {"status": "ok", "count": 0,
                             "coverage": {}, "errors": []})
            with patch("scripts.ozon_profit_snapshot.collect_finance", return_value=fake_finance):
                manifest = collect_snapshot(FakeClient([
                    {"result": {"items": [], "total": 0}}, {"result": []}
                ]), output, Path(temp) / "missing-master.xlsx")
            self.assertTrue(manifest["catalog"]["changed"])
            self.assertEqual(manifest["catalog"]["active_count"], 0)
            self.assertEqual(manifest["status"], "partial")
            self.assertEqual(manifest["sections"]["prices"]["status"], "blocked")
            self.assertEqual(manifest["seller_writes"], 0)
            for filename in ["prices.json", "products.json", "product_info.json", "stocks.json", "master.json",
                             "protection.json", "promotions.json", "finance.json", "manifest.json"]:
                self.assertTrue((output / filename).is_file())


if __name__ == "__main__":
    unittest.main()
