"""Commercial guard invariants, legacy continuity and one-attempt transport."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import ozon_client
import ozon_control_guard as guard
from ozon_promo_batch import DECISION_ID, scope_digest, validate_approved_plan


def approved_plan():
    items = []
    for i in range(96):
        sku = ("DQ500", "Dq500")[i] if i < 2 else "ExactSKU-" + str(i)
        group = "A" if i < 9 else "B" if i < 38 else "C"
        target = 1100 if group == "A" else 1000 if group == "B" else 900
        items.append({
            "sku": sku, "product_id": 10000+i, "strategy_code": group,
            "before_price": 1000, "target_price": target,
            "price_before_rub": 1000, "target_price_rub": target,
            "experiment_floor_rub": target,
            "allowed_action_ids": [12345], "approved_actions": [{"action_id": 12345}],
            "existing_actions": [{"action_id": 111}], "candidate_actions": [{"action_id": 12345}],
            "economics": {"cost_evidence": "test-evidence", "verified_full_profit": False},
            "initial_verified": {"decision_id": DECISION_ID, "observed_at": "2026-11-26T18:30:00Z",
                 "source": {"snapshot": "preflight-fixture", "response_sha256": "abc"},
                 "fields": {"price": 1000, "min_price": 500, "old_price": 2000, "currency_code": "RUB",
                            "auto_add_to_ozon_actions_list_enabled": True,
                            "min_price_for_auto_actions_enabled": False}},
            "last_verified": None, "requests": [],
        })
    plan = {"schema_version": 1, "decision_id": DECISION_ID, "approved": True, "enabled": True,
            "approvalReference": "Owner approved exact 96-item package", "approval_scope": "Exact targets",
            "source": {"commit": "pinned-source"}, "items": items}
    plan["scope_sha256"] = scope_digest(plan)
    validate_approved_plan(plan)
    return plan


def recorded_price(plan, sku="DQ500", payload=None):
    runtime = deepcopy(plan)
    item = next(x for x in runtime["items"] if x["sku"] == sku)
    payload = payload or {"prices": [{"offer_id": sku, "price": str(item["target_price_rub"]),
        "min_price": str(item["experiment_floor_rub"]), "old_price": "2000", "currency_code": "RUB",
        "auto_add_to_ozon_actions_list_enabled": "DISABLED", "min_price_for_auto_actions_enabled": True}]}
    record = {"request_id": "price-" + sku, "sku": sku, "operation": "price",
              "path": "/v1/product/import/prices", "payload": payload,
              "payload_sha256": guard.payload_digest(payload)}
    runtime["requests"] = [record]
    return runtime, record


class GuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "approved_plan.json"
        self.policy_patch = patch.object(guard, "PLAN_PATH", self.path)
        self.policy_patch.start()
        self.plan = approved_plan()
        self.save(self.plan)

    def tearDown(self):
        self.policy_patch.stop()
        self.temp.cleanup()

    def save(self, plan):
        self.path.write_text(json.dumps(plan), encoding="utf-8")

    def live_prices(self, skus):
        current = json.loads(self.path.read_text())
        return [{"offer_id": row["sku"], "product_id": row["product_id"],
                 "price": deepcopy((row.get("last_verified") or row["initial_verified"])["fields"])}
                for row in current["items"] if row["sku"] in skus]

    def test_missing_empty_and_disabled_draft_leave_legacy_behaviour_unchanged(self):
        payload = {"prices": [{"offer_id": "DQ500", "price": "777"}]}
        variants = (None, "", "{}", "[]", json.dumps({**self.plan, "enabled": False}))
        for value in variants:
            with self.subTest(value=value):
                if value is None:
                    self.path.unlink(missing_ok=True)
                else:
                    self.path.write_text(value)
                result = guard.prepare_request("/v1/product/import/prices", payload)
                self.assertIs(result.payload, payload)
                self.assertFalse(result.skipped)

    def test_read_stock_and_attribute_endpoints_do_not_depend_on_pricing_policy_health(self):
        self.path.write_text("{not valid JSON")
        for path in ("/v5/product/info/prices", "/v2/actions/products", "/v1/actions/products",
                     "/v2/actions/auto-add/products/list", "/v2/actions/auto-add/products/candidates",
                     "/v1/product/action/timer/status", "/v2/products/stocks",
                     "/v1/product/attributes/update", "/v1/product/import/info"):
            body = {"offer_id": "DQ500"}
            self.assertIs(guard.prepare_request(path, body).payload, body)

    def test_mixed_legacy_prices_keep_unmanaged_and_exact_case_identities(self):
        body = {"prices": [{"offer_id": "DQ500", "price": "1200"},
                           {"offer_id": "dq500", "price": "123"},
                           {"product_id": 10001, "price": "1300"},
                           {"offer_id": "OTHER", "price": "500"}]}
        original = deepcopy(body)
        decision = guard.prepare_request("/v1/product/import/prices", body)
        self.assertEqual([x["offer_id"] for x in decision.payload["prices"]], ["dq500", "OTHER"])
        self.assertEqual([x["sku"] for x in decision.skipped], ["DQ500", "Dq500"])
        self.assertEqual(body, original)
        response = decision.finish({"result": [{"offer_id": "OTHER", "updated": True, "errors": []}]})
        skipped = [r for r in response["result"] if r.get("guard_skipped")]
        self.assertEqual(len(skipped), 2)
        self.assertTrue(all(r["updated"] is False and r["errors"][0]["code"] == "OZON_CONTROL_GUARD_SKIP" for r in skipped))

    def test_managed_only_legacy_call_makes_no_network_request(self):
        with patch("content_manifest.check_ozon_description_write") as description, patch.object(ozon_client.requests, "post") as post:
            result = ozon_client._post("/v1/product/import/prices", {"prices": [{"offer_id": "DQ500", "price": "777"}]})
        post.assert_not_called()
        description.assert_called_once()
        self.assertTrue(result["result"][0]["guard_skipped"])
        self.assertFalse(result["result"][0]["updated"])

    def test_fresh_content_import_survives_and_only_stale_commercial_row_is_skipped(self):
        body = {"items": [
            {"offer_id": "DQ500", "price": "1000.00", "old_price": "2000", "currency_code": "RUB", "name": "New SEO title"},
            {"offer_id": "Dq500", "price": "999", "name": "Stale commercial import"},
            {"offer_id": "OTHER", "price": "555", "name": "Other content"},
        ]}
        decision = guard.prepare_request("/v3/product/import", body, read_prices=self.live_prices)
        self.assertEqual([r["offer_id"] for r in decision.payload["items"]], ["DQ500", "OTHER"])
        self.assertEqual(decision.payload["items"][0]["name"], "New SEO title")
        self.assertEqual([r["sku"] for r in decision.skipped], ["Dq500"])

    def test_content_import_uses_last_verified_after_price_apply(self):
        row = self.plan["items"][0]
        row["last_verified"] = deepcopy(row["initial_verified"])
        row["last_verified"]["fields"].update(price=1100, min_price=1100,
                 auto_add_to_ozon_actions_list_enabled=False, min_price_for_auto_actions_enabled=True)
        self.save(self.plan)
        matching = {"offer_id": "DQ500", "price": "1100", "min_price": "1100", "currency_code": "RUB",
                    "auto_add_to_ozon_actions_list_enabled": "DISABLED", "min_price_for_auto_actions_enabled": True}
        self.assertIsNotNone(guard.prepare_request("/v3/product/import", {"items": [matching]}, read_prices=self.live_prices).payload)
        for key, value in (("price", "1000"), ("min_price", "900"), ("currency_code", "USD"),
                           ("auto_add_to_ozon_actions_list_enabled", "ENABLED"),
                           ("min_price_for_auto_actions_enabled", False)):
            invalid = {**matching, key: value}
            self.assertIsNone(guard.prepare_request("/v3/product/import", {"items": [invalid]}, read_prices=self.live_prices).payload)

    def test_full_import_live_check_preserves_owner_change_and_missing_flags_are_unknown(self):
        def owner_changed(skus):
            self.assertEqual(skus, ["DQ500"])
            return [{"offer_id": "DQ500", "product_id": 10000,
                     "price": {"price": "1050", "old_price": "2000", "min_price": "1050", "currency_code": "RUB"}}]
        body = {"items": [{"offer_id": "DQ500", "price": "1000", "currency_code": "RUB"},
                          {"offer_id": "OTHER", "price": "550"}]}
        decision = guard.prepare_request("/v3/product/import", body, read_prices=owner_changed)
        self.assertEqual(decision.payload["items"], [body["items"][1]])
        body["items"][0]["price"] = "1050"
        self.assertEqual(len(guard.prepare_request("/v3/product/import", body, read_prices=owner_changed).payload["items"]), 2)
        body["items"][0]["min_price_for_auto_actions_enabled"] = True
        self.assertEqual(len(guard.prepare_request("/v3/product/import", body, read_prices=owner_changed).payload["items"]), 1)
        failed_read = Mock(side_effect=RuntimeError("PRIVATE_READ_ERROR"))
        self.assertEqual(len(guard.prepare_request("/v3/product/import", body, read_prices=failed_read).payload["items"]), 1)

    def test_full_import_wrong_pid_or_pending_state_never_gets_live_exception(self):
        body = {"items": [{"offer_id": "DQ500", "price": "1000", "currency_code": "RUB"}]}
        wrong = lambda skus: [{"offer_id": "DQ500", "product_id": 10001,
                              "price": {"price": 1000, "currency_code": "RUB"}}]
        self.assertIsNone(guard.prepare_request("/v3/product/import", body, read_prices=wrong).payload)
        self.plan["items"][0]["last_verified"] = {**deepcopy(self.plan["items"][0]["initial_verified"]), "verified": False}
        self.save(self.plan)
        reader = Mock(side_effect=self.live_prices)
        self.assertIsNone(guard.prepare_request("/v3/product/import", body, read_prices=reader).payload)
        reader.assert_not_called()

    def test_guarded_writer_pending_marker_preserves_private_fresh_request_evidence(self):
        from scripts.ozon_promo_execute import GuardedWriter
        item = self.plan["items"][0]
        observation = {"sku": item["sku"], "product_id": item["product_id"], "observed_at": "2026-11-26T19:00:00Z",
                       "price": {"offer_id": item["sku"], "product_id": item["product_id"],
                                 "price": {"price": "1000", "min_price": "500", "old_price": "2500",
                                           "currency_code": "RUB", "auto_action_enabled": False,
                                           "auto_add_to_ozon_actions_list_enabled": False}},
                       "protection": {"enabled": True, "active": True, "expired_at": "2026-12-26T19:00:00Z"}}
        writer = GuardedWriter(deepcopy(self.plan), self.path)
        writer.mark_pending(item, observation)
        self.assertFalse(json.loads(self.path.read_text())["items"][0]["last_verified"]["verified"])
        self.assertTrue(writer.plan["items"][0]["last_verified"]["verified"])
        body = {"prices": [{"offer_id": item["sku"], "product_id": item["product_id"],
                            "price": "1100", "min_price": "1100", "old_price": "2500", "currency_code": "RUB"}]}
        cfg = SimpleNamespace(ozon_api_base="https://api-seller.ozon.ru", ozon_client_id="test-id",
                              ozon_api_key="test-key", request_timeout_seconds=30)
        response = Mock(status_code=200, ok=True)
        response.json.return_value = {"result": [{"offer_id": item["sku"], "updated": True, "errors": []}]}
        with patch.object(ozon_client, "config", cfg), patch("content_manifest.check_ozon_description_write"), \
             patch.object(ozon_client.requests, "post", return_value=response) as post:
            result = writer.write(item, "price", "/v1/product/import/prices", body)
        self.assertTrue(result["result"][0]["updated"])
        post.assert_called_once()

    def test_content_only_import_is_allowed_but_unverified_commercial_fields_are_skipped(self):
        self.plan["items"][0]["initial_verified"] = None
        self.save(self.plan)
        body = {"items": [{"offer_id": "DQ500", "attributes": [{"id": 1, "value": "updated"}]}]}
        self.assertEqual(guard.prepare_request("/v3/product/import", body).payload, body)
        for fields in ({"price": "1000"}, {"unknown_price_override": "1000"}):
            body = {"items": [{"offer_id": "DQ500", **fields}]}
            self.assertIsNone(guard.prepare_request("/v3/product/import", body).payload)

    def test_promotion_and_timer_direct_posts_filter_exact_managed_product_ids(self):
        for path, body in (
            ("/v1/actions/products/update", {"action_id": 12345, "product_ids": [10000, 99999]}),
            ("/v2/actions/products/deactivate", {"action_id": 12345, "product_ids": [10001, 99999]}),
            ("/v1/product/action/timer/update", {"product_ids": [10000, 99999]}),
        ):
            result = guard.prepare_request(path, body)
            self.assertEqual(result.payload["product_ids"], [99999])
            self.assertEqual(len(result.skipped), 1)

    def test_malformed_or_ambiguous_scope_fails_closed_before_any_mutation(self):
        variants = [
            ("/v1/product/import/prices", {"prices": [{"offer_id": "DQ500", "product_id": 99999}]}),
            ("/v1/product/import/prices", {"prices": [{"offer_id": "OTHER", "product_id": 10000}]}),
            ("/v1/product/import/prices", {"prices": [{"offer_id": "DQ500", "id": 10000, "product_id": 10001}]}),
            ("/v1/product/import/prices", {"prices": [{"price": "500"}]}),
            ("/v1/product/import/prices", {"prices": [{"offer_id": " DQ500"}]}),
            ("/v1/product/import/prices", {"prices": [{"offer_id": "OTHER"}, {"offer_id": "OTHER"}]}),
            ("/v1/actions/products/update", {"action_id": 12345, "product_ids": []}),
            ("/v1/actions/products/update", {"action_id": 12345, "all": True}),
            ("/v3/actions/change-price", {"offer_id": "DQ500"}),
        ]
        for path, body in variants:
            with self.subTest(path=path, body=body), self.assertRaises(guard.OzonControlError):
                guard.prepare_request(path, body)

    def test_approved_price_request_is_exact_and_single_use(self):
        runtime, record = recorded_price(self.plan)
        with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
            result = guard.prepare_request(record["path"], record["payload"])
            self.assertTrue(result.permitted)
            self.assertEqual(result.payload, record["payload"])
            with self.assertRaises(guard.OzonControlError):
                guard.prepare_request(record["path"], record["payload"])
        self.assertIsNone(guard.prepare_request(record["path"], record["payload"]).payload)

    def test_permit_checks_immutable_targets_even_if_runtime_request_digest_is_valid(self):
        invalid_payloads = []
        for fields in ({"price": "1099"}, {"min_price": "900"}, {"currency_code": "USD"},
                       {"auto_add_to_ozon_actions_list_enabled": "ENABLED"},
                       {"min_price_for_auto_actions_enabled": False}, {"old_price": "9999"},
                       {"offer_id": "OTHER"}):
            runtime, rec = recorded_price(self.plan)
            rec["payload"]["prices"][0].update(fields)
            rec["payload_sha256"] = guard.payload_digest(rec["payload"])
            invalid_payloads.append(runtime)
        for runtime in invalid_payloads:
            with self.subTest(runtime=runtime["requests"][0]["payload"]), self.assertRaises(guard.OzonControlError):
                with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
                    pass

    def test_runtime_cannot_change_approved_financial_evidence_or_scope_even_with_new_hash(self):
        runtime, _ = recorded_price(self.plan)
        runtime["items"][0]["economics"]["cost_evidence"] = "changed after approval"
        runtime["scope_sha256"] = scope_digest(runtime)
        with self.assertRaises(guard.OzonControlError):
            with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
                pass

    def test_runtime_request_cannot_change_after_context_creation(self):
        runtime, record = recorded_price(self.plan)
        with guard.approved_request(DECISION_ID, "DQ500", record["request_id"], plan=runtime):
            record["payload"]["prices"][0]["price"] = "100"
            with self.assertRaises(guard.OzonControlError):
                guard.prepare_request(record["path"], record["payload"])

    def test_action_permit_requires_exact_approved_action_id_and_price(self):
        for aid, price, valid in ((12345, 1100, True), (12346, 1100, False), (12345, 1099, False)):
            runtime = deepcopy(self.plan)
            payload = {"action_id": aid, "products": [{"product_id": 10000, "action_price": str(price)}]}
            record = {"request_id": "action", "sku": "DQ500", "operation": "activate", "action_id": aid,
                      "path": "/v1/actions/products/update", "payload": payload}
            runtime["requests"] = [record]
            if valid:
                with guard.approved_write(DECISION_ID, "DQ500", "activate", aid, plan=runtime):
                    self.assertTrue(guard.prepare_request(record["path"], payload).permitted)
            else:
                with self.assertRaises(guard.OzonControlError):
                    with guard.approved_write(DECISION_ID, "DQ500", "activate", aid, plan=runtime):
                        pass

    def test_documented_action_money_and_timer_string_ids_are_supported(self):
        for operation, path, payload, aid in (
            ("activate", "/v1/actions/products/update", {
                "action_id": 12345, "products": [{"product_id": "10000",
                  "action_price": {"amount": "1100.00", "currency": "RUB"}}]}, 12345),
            ("timer", "/v1/product/action/timer/update", {"product_ids": ["10000"]}, None),
        ):
            runtime = deepcopy(self.plan)
            runtime["requests"] = [{"sku": "DQ500", "operation": operation, "path": path,
                                    "payload": payload, "action_id": aid}]
            with guard.approved_write(DECISION_ID, "DQ500", operation, aid, plan=runtime):
                self.assertTrue(guard.prepare_request(path, payload).permitted)
        runtime["requests"][0]["payload"] = {"product_ids": ["10001"]}
        with self.assertRaises(guard.OzonControlError):
            with guard.approved_write(DECISION_ID, "DQ500", "timer", plan=runtime):
                pass

    def test_non_rub_action_money_is_rejected_and_canonical_price_alias_is_supported(self):
        plan = deepcopy(self.plan)
        for item in plan["items"]:
            del item["target_price_rub"]
        plan["scope_sha256"] = scope_digest(plan)
        self.save(plan)
        body = {"prices": [{"offer_id": "DQ500", "price": "1100", "min_price": "1100", "currency_code": "RUB"}]}
        runtime = deepcopy(plan)
        runtime["requests"] = [{"sku": "DQ500", "operation": "price", "path": "/v1/product/import/prices", "payload": body}]
        with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
            self.assertTrue(guard.prepare_request("/v1/product/import/prices", body).permitted)
        runtime["requests"] = [{"sku": "DQ500", "operation": "activate", "action_id": 12345,
            "path": "/v1/actions/products/update", "payload": {"action_id": 12345,
                "products": [{"product_id": 10000, "action_price": {"amount": "1100", "currency": "USD"}}]}}]
        with self.assertRaises(guard.OzonControlError):
            with guard.approved_write(DECISION_ID, "DQ500", "activate", 12345, plan=runtime):
                pass

    def test_disabled_or_changed_persisted_approval_cannot_be_bypassed_by_runtime_plan(self):
        runtime, record = recorded_price(self.plan)
        self.save({**self.plan, "enabled": False})
        with self.assertRaises(guard.OzonControlError):
            with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
                pass
        self.save(self.plan)
        with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
            self.path.unlink()
            with self.assertRaises(guard.OzonControlError):
                guard.prepare_request(record["path"], record["payload"])

    def test_nested_or_ambiguous_permits_are_rejected_and_context_is_restored(self):
        runtime, record = recorded_price(self.plan)
        with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
            with self.assertRaises(guard.OzonControlError):
                with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
                    pass
        runtime["requests"].append(deepcopy(record))
        with self.assertRaises(guard.OzonControlError):
            with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime):
                pass
        self.assertIsNone(guard.prepare_request(record["path"], record["payload"]).payload)

    def test_controlled_transport_has_no_redirect_retry_or_secret_error_body(self):
        runtime, record = recorded_price(self.plan)
        cfg = SimpleNamespace(ozon_api_base="https://api-seller.ozon.ru", ozon_client_id="test-id",
                              ozon_api_key="secret-key", request_timeout_seconds=30)
        outcomes = [requests.RequestException("PRIVATE_TRANSPORT secret-key"),
                    Mock(status_code=429, ok=False, text="PRIVATE_BODY secret-key"),
                    Mock(status_code=500, ok=False, text="PRIVATE_BODY secret-key"),
                    Mock(status_code=302, ok=True, text="PRIVATE_BODY secret-key")]
        for outcome in outcomes:
            post = Mock(side_effect=outcome) if isinstance(outcome, Exception) else Mock(return_value=outcome)
            with self.subTest(outcome=type(outcome).__name__), patch.object(ozon_client, "config", cfg), \
                 patch("content_manifest.check_ozon_description_write") as description, \
                 patch.object(ozon_client.requests, "post", post), patch.object(ozon_client.time, "sleep") as sleep:
                with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime), self.assertRaises(ozon_client.OzonApiError) as raised:
                    ozon_client._post(record["path"], record["payload"], retries=9)
                self.assertEqual(post.call_count, 1)
                self.assertFalse(post.call_args.kwargs["allow_redirects"])
                self.assertNotIn("PRIVATE", str(raised.exception))
                self.assertNotIn("secret-key", str(raised.exception))
                self.assertIn("readback required", str(raised.exception))
                sleep.assert_not_called()
                description.assert_called_once_with(record["path"], record["payload"])

    def test_description_guard_still_runs_and_prevents_network_before_commercial_permit(self):
        runtime, record = recorded_price(self.plan)
        with guard.approved_write(DECISION_ID, "DQ500", "price", plan=runtime), \
             patch("content_manifest.check_ozon_description_write", side_effect=ValueError("description policy")) as description, \
             patch.object(ozon_client.requests, "post") as post:
            with self.assertRaisesRegex(ValueError, "description policy"):
                ozon_client._post(record["path"], record["payload"])
        post.assert_not_called()
        description.assert_called_once()


if __name__ == "__main__":
    unittest.main()
