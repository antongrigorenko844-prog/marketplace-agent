"""Pure fixed-pilot and transport-permit checks; no client or network imports."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import ozon_black_friday_pilot as pilot
import ozon_control_guard as guard
import ozon_promo_batch as legacy

NOW = "2026-11-27T05:00:00Z"


def pilot_fixture():
    items, rows = [], {}
    costs = {"0am325025G": 200, "porshni-usil-pylniki-kryshki-v2": 400,
             "0AM325025B": 1350, "6t30": 3500}
    for sku, fixed in pilot.FIXED_SCOPE.items():
        action = {"action_id": pilot.ACTION_ID, "date_start": "2026-11-26T23:00:00Z",
                  "date_end": "2026-12-02T20:59:59Z", "cap_rub": fixed["target_price"],
                  "action_type": "STOCK_DISCOUNT", "eligible_for_price_comparison": True}
        item = {"decision_id": pilot.DECISION_ID, "sku": sku, **fixed, "strategy_code": "C",
                "price_before_rub": fixed["before_price"], "target_price_rub": fixed["target_price"],
                "experiment_floor_rub": fixed["target_price"],
                "allowed_action_ids": [pilot.ACTION_ID], "approved_actions": [deepcopy(action)],
                "initial_verified": {"decision_id": pilot.DECISION_ID, "observed_at": NOW,
                    "source": "fixture direct read", "verified": True,
                    "fields": {"price": fixed["before_price"], "min_price": fixed["before_price"],
                               "old_price": fixed["before_price"] * 2, "currency_code": "RUB",
                               "auto_action_enabled": False, "auto_add_to_ozon_actions_list_enabled": False,
                               "min_price_for_auto_actions_enabled": True}},
                "last_verified": None, "requests": []}
        row = {"sku": sku, "product_id": fixed["product_id"], "current_price": fixed["before_price"],
               "actions": [deepcopy(action)],
               "stock": {"status": "OBSERVED", "rows": [{"offer_id": sku, "product_id": fixed["product_id"],
                                                           "stocks": [{"present": 10}]}]},
               "economics": {"procurement": {"scenario_cost_rub": costs[sku]},
                   "current": {"tax_rate_scenario": .06, "services_estimated": True,
                       "service_model": {"available": True, "percentage_rate_pct": 3.8 if sku == "0AM325025B" else 2.8,
                                         "fixed_per_sold_unit_rub": 0},
                       "by_scheme": {"fbs": {"commission_rate_pct": 47, "acquiring_rate_pct": 1,
                           "deductions": {"logistics_snapshot_max_rub": 317 if sku == "6t30" else 237}}}}}}
        item["approved_economics"] = pilot.approval_economics(item, row)
        items.append(item)
        rows[sku] = row
    plan = {"schema_version": 1, "decision_id": pilot.DECISION_ID, "approved": True, "enabled": True,
            "approvalReference": "Explicit owner approval of the exact four targets and individual risk",
            "approval_scope": "Four fixed targets/minimums and action 4532787 only",
            "source": {"kind": "test fixture"}, "items": items}
    plan["scope_sha256"] = pilot.scope_digest(plan)
    return plan, rows


def legacy_fixture():
    items = []
    fixed_rows = list(pilot.FIXED_SCOPE.items())
    for i in range(96):
        group = "A" if i < 9 else "B" if i < 38 else "C"
        sku, pid, before, target = "Legacy-" + str(i), 900000 + i, 1000, 1100 if group == "A" else 1000 if group == "B" else 900
        if 38 <= i < 42:
            sku, fixed = fixed_rows[i - 38]
            pid, target = fixed["product_id"], fixed["before_price"]
            before = target + 100
        items.append({"decision_id": legacy.DECISION_ID, "sku": sku, "product_id": pid,
                      "strategy_code": group, "before_price": before, "target_price": target,
                      "experiment_floor_rub": target, "allowed_action_ids": [1977747],
                      "approved_actions": [{"action_id": 1977747}],
                      "initial_verified": {"decision_id": legacy.DECISION_ID, "observed_at": NOW,
                          "source": "legacy fixture", "verified": True,
                          "fields": {"price": target, "min_price": target, "old_price": before * 2,
                                     "currency_code": "RUB", "auto_action_enabled": False,
                                     "auto_add_to_ozon_actions_list_enabled": False}},
                      "last_verified": None, "requests": []})
    plan = {"schema_version": 1, "decision_id": legacy.DECISION_ID, "approved": True, "enabled": True,
            "approvalReference": "Original exact 96-item approval", "approval_scope": "Original frozen package",
            "source": {"kind": "legacy fixture"}, "items": items}
    plan["scope_sha256"] = legacy.scope_digest(plan)
    legacy.validate_approved_plan(plan)
    return plan


def price_request(plan, sku):
    item = next(i for i in plan["items"] if i["sku"] == sku)
    fields = (item.get("last_verified") or item["initial_verified"])["fields"]
    return {"prices": [{"offer_id": sku, "product_id": item["product_id"],
                        "price": str(item["target_price"]), "min_price": str(item["experiment_floor_rub"]),
                        "old_price": str(fields["old_price"]), "currency_code": "RUB"}]}


def runtime_request(plan, sku, payload=None, *, action_id=None):
    runtime = deepcopy(plan)
    operation = "price" if action_id is None else "activate"
    path = "/v1/product/import/prices" if operation == "price" else "/v1/actions/products/update"
    payload = payload if payload is not None else price_request(plan, sku)
    runtime["requests"] = [{"request_id": "fixture-intent", "sku": sku, "operation": operation,
                            "action_id": action_id, "path": path, "payload": payload,
                            "payload_sha256": guard.payload_digest(payload)}]
    return runtime, path, payload


class PilotPolicyTests(unittest.TestCase):
    def setUp(self):
        self.plan, self.rows = pilot_fixture()
        self.item = self.plan["items"][0]

    def test_fixed_approval_and_legacy_digest_compatibility(self):
        self.assertTrue(pilot.validate_approved_plan(self.plan))
        self.assertEqual(pilot.scope_digest(self.plan), legacy.scope_digest(self.plan))
        self.plan["items"][0]["last_verified"] = {"verified": False}
        self.assertTrue(pilot.validate_approved_plan(self.plan))
        with self.assertRaises(ValueError):
            legacy.validate_approved_plan(self.plan)

    def test_wrong_sku_product_price_floor_or_action_cannot_be_rehashed_into_approval(self):
        mutations = [
            lambda p: p["items"][0].update(sku="0AM325025G"),
            lambda p: p["items"][0].update(product_id=1790610717),
            lambda p: p["items"][0].update(target_price=3194.9999),
            lambda p: p["items"][0].update(before_price=3725),
            lambda p: p["items"][0].update(experiment_floor_rub=3194),
            lambda p: p["items"][0].update(allowed_action_ids=[4535879], approved_actions=[{"action_id": 4535879}]),
            lambda p: p["items"][0].update(strategy_code="B"),
            lambda p: p["items"].pop(),
            lambda p: p.update(decision_id=legacy.DECISION_ID),
        ]
        for mutate in mutations:
            changed = deepcopy(self.plan)
            mutate(changed)
            changed["scope_sha256"] = pilot.scope_digest(changed)
            with self.subTest(change=changed["items"][0].get("sku")), self.assertRaises(ValueError):
                pilot.validate_approved_plan(changed)

    def test_approved_financial_numbers_must_reconcile_even_with_new_digest(self):
        changed = deepcopy(self.plan)
        changed["items"][0]["approved_economics"]["by_scheme"]["fbs"]["target_partial_contribution_rub"] += 100
        changed["scope_sha256"] = pilot.scope_digest(changed)
        with self.assertRaises(ValueError):
            pilot.validate_approved_plan(changed)

    def test_individually_approved_risk_does_not_change_the_legacy_ten_percent_gate(self):
        for item in self.plan["items"]:
            row = self.rows[item["sku"]]
            fresh = pilot.evaluate_item(item, row, now=NOW)
            self.assertGreater(fresh["financial_check"]["required_unit_growth_pct"], 10)
            self.assertEqual(fresh["status"], "READY")
            self.assertEqual(fresh["ready_action_ids"], [pilot.ACTION_ID])
            old = legacy.evaluate_item(item, row, now=NOW)
            self.assertIn("UPDATED_UNIT_GROWTH_EXCEEDS_APPROVED_PACKAGE_SCOPE", old["blockers"])

    def test_missing_services_and_invalid_financial_evidence_hold(self):
        row = deepcopy(self.rows[self.item["sku"]])
        row["economics"]["current"]["services_estimated"] = False
        row["economics"]["current"]["service_model"]["available"] = False
        result = pilot.evaluate_item(self.item, row, now=NOW)
        self.assertEqual(result["status"], "HOLD")
        self.assertFalse(result["price_write_allowed"])
        self.assertIn("SERVICE_OR_FINANCIAL_EVIDENCE_REQUIRED", result["blockers"])

    def test_each_worsened_cost_or_rate_holds_but_rounding_and_improvement_do_not(self):
        changes = [
            lambda e: e["procurement"].update(scenario_cost_rub=201),
            lambda e: e["current"].update(tax_rate_scenario=.061),
            lambda e: e["current"]["service_model"].update(percentage_rate_pct=2.9),
            lambda e: e["current"]["service_model"].update(fixed_per_sold_unit_rub=1),
            lambda e: e["current"]["by_scheme"]["fbs"].update(commission_rate_pct=47.1),
            lambda e: e["current"]["by_scheme"]["fbs"].update(acquiring_rate_pct=1.1),
            lambda e: e["current"]["by_scheme"]["fbs"]["deductions"].update(logistics_snapshot_max_rub=238),
        ]
        for change in changes:
            row = deepcopy(self.rows[self.item["sku"]])
            change(row["economics"])
            result = pilot.evaluate_item(self.item, row, now=NOW)
            self.assertEqual(result["status"], "HOLD")
            self.assertTrue(any("WORSENED" in b for b in result["blockers"]))
        row = deepcopy(self.rows[self.item["sku"]])
        row["economics"]["current"]["service_model"]["percentage_rate_pct"] += .00001
        self.assertEqual(pilot.evaluate_item(self.item, row, now=NOW)["status"], "READY")
        row["economics"]["procurement"]["scenario_cost_rub"] -= 1
        self.assertEqual(pilot.evaluate_item(self.item, row, now=NOW)["status"], "READY")

    def test_stock_price_action_cap_and_campaign_window_remain_required(self):
        for change in (
            lambda r: r.update(current_price=3000),
            lambda r: r["stock"]["rows"][0]["stocks"][0].update(present=0),
            lambda r: r["actions"][0].update(cap_rub=3194),
            lambda r: r["actions"][0].update(quarantined_or_failed=True),
            lambda r: r["actions"].append(deepcopy(r["actions"][0])),
        ):
            row = deepcopy(self.rows[self.item["sku"]])
            change(row)
            self.assertEqual(pilot.evaluate_item(self.item, row, now=NOW)["status"], "HOLD")
        self.assertEqual(pilot.evaluate_item(self.item, self.rows[self.item["sku"]], now="2026-12-03T00:00:00Z")["status"], "HOLD")

    def test_legacy_96_count_and_group_policy_are_not_weakened(self):
        old = legacy_fixture()
        self.assertTrue(legacy.validate_approved_plan(old))
        for mutate in (lambda p: p["items"].pop(), lambda p: p["items"][0].update(strategy_code="C", target_price=900, experiment_floor_rub=900)):
            changed = deepcopy(old)
            mutate(changed)
            changed["scope_sha256"] = legacy.scope_digest(changed)
            with self.assertRaises(ValueError):
                legacy.validate_approved_plan(changed)


class PilotGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.old_path = Path(self.temp.name) / "legacy" / "approved_plan.json"
        self.pilot_path = Path(self.temp.name) / "pilot" / "approved_plan.json"
        self.old_path.parent.mkdir()
        self.pilot_path.parent.mkdir()
        self.old, (self.plan, self.rows) = legacy_fixture(), pilot_fixture()
        self.old_path.write_text(json.dumps(self.old))
        self.pilot_path.write_text(json.dumps(self.plan))
        self.paths = patch.multiple(guard, PLAN_PATH=self.old_path, BF4_PATH=self.pilot_path)
        self.paths.start()
        self.sku = self.plan["items"][0]["sku"]

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def save_pilot(self):
        self.pilot_path.write_text(json.dumps(self.plan))

    def test_exact_new_price_and_lower_minimum_use_one_permit(self):
        runtime, path, payload = runtime_request(self.plan, self.sku)
        with guard.approved_write(pilot.DECISION_ID, self.sku, "price", plan=runtime):
            self.assertTrue(guard.prepare_request(path, payload).permitted)
            with self.assertRaisesRegex(guard.OzonControlError, "already_consumed"):
                guard.prepare_request(path, payload)

    def test_wrong_payload_identity_price_minimum_and_action_are_rejected(self):
        for field, bad in (("offer_id", "0AM325025G"), ("product_id", 1790610717),
                           ("price", "3194"), ("min_price", "3194")):
            payload = price_request(self.plan, self.sku)
            payload["prices"][0][field] = bad
            runtime, path, payload = runtime_request(self.plan, self.sku, payload)
            with self.subTest(field=field), self.assertRaises(guard.OzonControlError):
                with guard.approved_write(pilot.DECISION_ID, self.sku, "price", plan=runtime):
                    guard.prepare_request(path, payload)
        for aid in (4535879, pilot.ACTION_ID):
            payload = {"action_id": aid, "products": [{"product_id": self.plan["items"][0]["product_id"],
                       "action_price": {"amount": "3195" if aid != pilot.ACTION_ID else "3194", "currency": "RUB"}}]}
            runtime, path, payload = runtime_request(self.plan, self.sku, payload, action_id=aid)
            with self.assertRaises(guard.OzonControlError):
                with guard.approved_write(pilot.DECISION_ID, self.sku, "activate", aid, plan=runtime):
                    guard.prepare_request(path, payload)

    def test_exact_new_action_permit_works(self):
        payload = {"action_id": pilot.ACTION_ID, "products": [{"product_id": self.plan["items"][0]["product_id"],
                   "action_price": {"amount": "3195", "currency": "RUB"}}]}
        runtime, path, payload = runtime_request(self.plan, self.sku, payload, action_id=pilot.ACTION_ID)
        with guard.approved_write(pilot.DECISION_ID, self.sku, "activate", pilot.ACTION_ID, plan=runtime):
            self.assertTrue(guard.prepare_request(path, payload).permitted)

    def test_old_four_are_superseded_but_other_92_keep_the_original_permits(self):
        for sku in pilot.FIXED_SCOPE:
            self.assertEqual(guard.superseding_decision(legacy.DECISION_ID, sku), pilot.DECISION_ID)
            runtime, path, payload = runtime_request(self.old, sku)
            with self.assertRaisesRegex(guard.OzonControlError, "superseded"):
                with guard.approved_write(legacy.DECISION_ID, sku, "price", plan=runtime):
                    guard.prepare_request(path, payload)
        self.assertIsNone(guard.superseding_decision(pilot.DECISION_ID, self.sku))
        runtime, path, payload = runtime_request(self.old, "Legacy-42")
        with guard.approved_write(legacy.DECISION_ID, "Legacy-42", "price", plan=runtime):
            self.assertTrue(guard.prepare_request(path, payload).permitted)

    def test_supersession_is_rechecked_when_old_permit_reaches_transport(self):
        self.plan["enabled"] = False
        self.save_pilot()
        runtime, path, payload = runtime_request(self.old, self.sku)
        with guard.approved_write(legacy.DECISION_ID, self.sku, "price", plan=runtime):
            self.plan["enabled"] = True
            self.save_pilot()
            with self.assertRaisesRegex(guard.OzonControlError, "superseded"):
                guard.prepare_request(path, payload)

    def test_pilot_revocation_and_unknown_decisions_fail_closed(self):
        runtime, path, payload = runtime_request(self.plan, self.sku)
        with guard.approved_write(pilot.DECISION_ID, self.sku, "price", plan=runtime):
            self.plan["enabled"] = False
            self.save_pilot()
            with self.assertRaises(guard.OzonControlError):
                guard.prepare_request(path, payload)
        with self.assertRaisesRegex(guard.OzonControlError, "unregistered"):
            with guard.approved_write("arbitrary-new-decision", self.sku, "price", plan=runtime):
                pass

    def test_new_decision_requires_the_valid_unchanged_legacy_protected_scope(self):
        self.old["items"].pop()
        self.old["scope_sha256"] = legacy.scope_digest(self.old)
        self.old_path.write_text(json.dumps(self.old))
        runtime, path, payload = runtime_request(self.plan, self.sku)
        with self.assertRaises(guard.OzonControlError):
            with guard.approved_write(pilot.DECISION_ID, self.sku, "price", plan=runtime):
                guard.prepare_request(path, payload)

    def test_legacy_content_import_uses_pilot_pending_state_without_changing_old_plan(self):
        original = self.old_path.read_bytes()
        self.plan["items"][0]["last_verified"] = {"decision_id": pilot.DECISION_ID, "verified": False}
        self.save_pilot()
        payload = {"items": [{"offer_id": self.sku, "price": "3195", "name": "Updated title"}]}
        reader = Mock()
        decision = guard.prepare_request("/v3/product/import", payload, read_prices=reader)
        self.assertIsNone(decision.payload)
        reader.assert_not_called()
        self.assertEqual(self.old_path.read_bytes(), original)
        content = {"items": [{"offer_id": self.sku, "name": "Content only"}]}
        self.assertEqual(guard.prepare_request("/v3/product/import", content).payload, content)

    def test_fresh_content_matches_new_price_but_cannot_restore_legacy_price(self):
        item = self.plan["items"][0]
        item["last_verified"] = deepcopy(item["initial_verified"])
        item["last_verified"]["fields"].update(price=3195, min_price=3195)
        self.save_pilot()
        def reader(skus):
            return [{"offer_id": self.sku, "product_id": item["product_id"],
                     "price": deepcopy(item["last_verified"]["fields"])}]
        for price, allowed in (("3195", True), ("3724", False)):
            body = {"items": [{"offer_id": self.sku, "price": price, "currency_code": "RUB"}]}
            result = guard.prepare_request("/v3/product/import", body, read_prices=reader)
            self.assertEqual(result.payload is not None, allowed)

    def test_redirected_legacy_store_never_reads_production_pilot(self):
        with patch.object(guard, "BF4_PATH", guard._DEFAULT_BF4_PATH):
            self.assertNotEqual(guard._registered_path(pilot.DECISION_ID), guard._DEFAULT_BF4_PATH)
            self.assertIsNone(guard.superseding_decision(legacy.DECISION_ID, self.sku))
            runtime, path, payload = runtime_request(self.old, self.sku)
            with guard.approved_write(legacy.DECISION_ID, self.sku, "price", plan=runtime):
                self.assertTrue(guard.prepare_request(path, payload).permitted)


if __name__ == "__main__":
    unittest.main()
