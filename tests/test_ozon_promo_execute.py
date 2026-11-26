import copy
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.ozon_promo_execute import (
    ACTION_PATH, PRICE_PATH, TIMER_PATH, ExecutionError, Executor, Journal,
    OfficialContract, commercial_state, execution_gate, preservation_errors,
    response_summary, rollback_eligibility, timer_effect,
)

ROOT = Path(__file__).resolve().parents[1]


def contract():
    return OfficialContract(json.loads((ROOT / "data/ozon_promo_execution_20261127/official_contract.json").read_text()))


def item(sku="Part", pid=1, strategy="C", before=100, target=90):
    return {"sku": sku, "product_id": pid, "strategy_code": strategy,
            "before_price": before, "target_price": target, "experiment_floor_rub": target,
            "allowed_action_ids": [101], "approved_actions": [], "initial_verified": {},
            "constraints": {"full_profit_verified": False}}


def observation(product, *, active=False, future=False, protected=False):
    now = datetime.now(timezone.utc)
    action = {"id": 101, "title": "Exact approved promotion", "action_type": "ELASTIC_BOOSTING",
              "is_voucher_action": False, "discount_type": "CURRENCY", "discount_value": 0,
              "date_start": (now + timedelta(days=1) if future else now - timedelta(days=1)).isoformat(),
              "date_end": (now + timedelta(days=30)).isoformat(), "auto_add_dates": []}
    raw = {"id": product["product_id"], "max_action_price": {"amount": "120", "currency": "RUB"},
           "price_max_elastic": {"amount": "120", "currency": "RUB"},
           "current_boost": 55 if active else 0, "max_boost": 55,
           "website_prices": {"price": {"amount": "80", "currency": "RUB"}}, "is_quarantined": False}
    return {"sku": product["sku"], "product_id": product["product_id"], "observed_at": now.isoformat(),
            "price": {"offer_id": product["sku"], "product_id": product["product_id"],
                      "acquiring": product["before_price"] / 100, "commissions": {"sales_percent_fbs": 47},
                      "price": {"price": product["before_price"], "min_price": product["before_price"],
                                "old_price": 200, "currency_code": "RUB", "auto_action_enabled": False,
                                "auto_add_to_ozon_actions_list_enabled": False}},
            "stock": {"offer_id": product["sku"], "product_id": product["product_id"],
                      "stocks": [{"present": 5, "reserved": 1, "type": "fbs"}]},
            "protection": {"enabled": protected, "active": protected,
                           "expired_at": (now + timedelta(days=30)).isoformat() if protected else None},
            "actions": {"101": {"action": action, "participant": raw if active else None,
                                "candidate": None if active else raw}}}


def bundle(product, observed):
    evidence = observed["actions"]["101"]
    return {"manifest": {"completed_at": observed["observed_at"]},
            "prices": [copy.deepcopy(observed["price"])], "stocks": [copy.deepcopy(observed["stock"])],
            "protection": {"rows": [{**copy.deepcopy(observed["protection"]),
                                     "product_id": product["product_id"], "mapped_offer_id": product["sku"]}]},
            "promotions": [{"action": copy.deepcopy(evidence["action"]), "sections": {
                "participants": {"rows": [copy.deepcopy(evidence["participant"])] if evidence["participant"] else []},
                "candidates": {"rows": [copy.deepcopy(evidence["candidate"])] if evidence["candidate"] else []}}}],
            "catalogue": {product["sku"]: {"sku": product["sku"], "product_id": product["product_id"],
                                           "current_price": product["before_price"]}}}


def ready(product, row, evidence=None):
    return {"status": "READY", "price_write_allowed": row["current_price"] != product["target_price"],
            "ready_action_ids": [101], "deferred_action_ids": [], "blockers": [],
            "financial_check": {"full_profit_verified": False}}


class Reader:
    def __init__(self, state):
        self.state = copy.deepcopy(state)
        self.reads = 0

    def observe(self, _item, **_kwargs):
        self.reads += 1
        return copy.deepcopy(self.state)

    def commercial(self, _item, **_kwargs):
        self.reads += 1
        return copy.deepcopy(self.state)

    def membership(self, _item, action_id):
        self.reads += 1
        return copy.deepcopy(self.state["actions"][str(action_id)]["participant"])


class Writer:
    def __init__(self, reader, journal, *, lose_response=False, apply=True):
        self.reader, self.journal = reader, journal
        self.lose_response, self.apply = lose_response, apply
        self.calls = []
        self.published = []

    def note_verified(self, product, observed, *, verified=True):
        self.published.append({"fields": commercial_state(observed), "verified": verified})

    def mark_pending(self, product, observed):
        self.note_verified(product, observed, verified=False)

    def write(self, product, operation, path, payload, action_id=None):
        saved = json.loads(self.journal.path.read_text())
        assert saved["operations"][-1]["state"] == "INTENT"
        self.calls.append((operation, path, copy.deepcopy(payload)))
        state = self.reader.state
        if self.apply:
            if operation == "timer":
                state["protection"].update(enabled=True, active=True,
                    expired_at=(datetime.now(timezone.utc) + timedelta(days=30)).isoformat())
            elif operation == "price":
                state["price"]["price"].update(price=product["target_price"], min_price=product["target_price"])
                state["price"]["acquiring"] = product["target_price"] / 100
            else:
                action = state["actions"][str(action_id)]
                action["participant"] = copy.deepcopy(action["candidate"])
                action["participant"]["current_boost"] = 15
        if self.lose_response:
            raise TimeoutError("private secret must never enter journal")
        return {"result": {"active_product_ids": [product["product_id"]]}}


class ContractAndGateTests(unittest.TestCase):
    def test_price_uses_exact_absolute_approved_target_and_preserves_flags(self):
        product = item()
        path, payload = contract().payload("price", product, observation(product))
        self.assertEqual(path, PRICE_PATH)
        row = payload["prices"][0]
        self.assertEqual(row["price"], "90")
        self.assertEqual(row["min_price"], "90")
        self.assertEqual(row["old_price"], "200")
        self.assertNotIn("min_price_for_auto_actions_enabled", row)
        self.assertNotIn("auto_action_enabled", row)
        self.assertNotIn("auto_add_to_ozon_actions_list_enabled", row)

    def test_money_action_and_official_string_timer_ids(self):
        product = item()
        path, payload = contract().payload("activate", product, observation(product), 101)
        self.assertEqual(path, ACTION_PATH)
        self.assertEqual(payload, {"action_id": 101, "products": [{"product_id": 1,
            "action_price": {"amount": "90", "currency": "RUB"}}]})
        self.assertEqual(contract().payload("timer", product, observation(product)),
                         (TIMER_PATH, {"product_ids": ["1"]}))
        with self.assertRaises(ExecutionError):
            contract().payload("future", product, observation(product), 101, "2026-12-01T00:00:00Z")

    def test_contract_cannot_add_an_undocumented_write_field(self):
        schema = contract().request_schema(TIMER_PATH)
        with self.assertRaises(ExecutionError):
            contract().validate({"product_ids": ["1"], "disable": True}, schema)

    def test_changed_cap_price_identity_tariff_and_stock_block(self):
        product = item()
        approved = observation(product)
        cases = [
            (lambda x: x["price"]["price"].update(price=95), "CURRENT_PRICE_OUTSIDE_APPROVED_BEFORE_OR_TARGET"),
            (lambda x: x["price"].update(offer_id="part"), "SKU_PID_MISMATCH"),
            (lambda x: x["stock"]["stocks"][0].update(present=0), "NO_AVAILABLE_STOCK"),
            (lambda x: x["price"]["commissions"].update(sales_percent_fbs=55), "CURRENT_TARIFFS_CHANGED"),
            (lambda x: x["actions"]["101"]["candidate"].update(max_action_price={"amount": "85", "currency": "RUB"}), "APPROVED_TARGET_ABOVE_CURRENT_ACTION_CAP"),
        ]
        for mutate, code in cases:
            with self.subTest(code=code):
                current = copy.deepcopy(approved)
                mutate(current)
                gate = execution_gate(product, current, approved, ready(product, {"current_price": 100}))
                self.assertIn(code, gate["blockers"] + [x["reason"] for x in gate["skipped_actions"]])

    def test_vouchers_and_unknown_fee_mechanics_are_not_enrolled(self):
        product = item()
        approved = observation(product)
        for key, value in [("is_voucher_action", True), ("discount_type", "PERCENT"),
                           ("action_type", "ADDITIONAL_DISCOUNT")]:
            current = copy.deepcopy(approved)
            current["actions"]["101"]["action"][key] = value
            gate = execution_gate(product, current, approved, ready(product, {"current_price": 100}))
            self.assertIn("ADDITIONAL_DISCOUNT_VOUCHER_OR_UNKNOWN_ACTION", [x["reason"] for x in gate["skipped_actions"]])
            self.assertIn("NO_CURRENTLY_ELIGIBLE_APPROVED_ACTION", gate["blockers"])

    def test_one_expired_action_does_not_block_another_approved_action(self):
        product = item()
        product["allowed_action_ids"].append(102)
        current = observation(product)
        current["actions"]["102"] = copy.deepcopy(current["actions"]["101"])
        current["actions"]["102"]["action"]["id"] = 102
        current["actions"]["101"]["action"]["date_end"] = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
        economics = ready(product, {"current_price": 100})
        economics["ready_action_ids"] = [102]
        gate = execution_gate(product, current, current, economics)
        self.assertEqual(gate["blockers"], [])
        self.assertEqual(gate["ready_action_ids"], [102])
        self.assertEqual(gate["skipped_actions"], [{"action_id": 101, "reason": "APPROVED_ACTION_EXPIRED"}])

    def test_future_is_deferred_and_never_reported_active(self):
        product = item()
        current = observation(product, future=True)
        economics = {"status": "DEFERRED", "price_write_allowed": False, "ready_action_ids": [],
                     "deferred_action_ids": [101], "blockers": [], "financial_check": {}}
        gate = execution_gate(product, current, current, economics)
        self.assertEqual(gate["status"], "DEFERRED")
        self.assertEqual(gate["ready_action_ids"], [])
        self.assertEqual(gate["deferred_action_ids"], [101])

    def test_timer_readback_is_required_before_price_changes(self):
        product = item()
        current = observation(product, protected=True)
        self.assertTrue(timer_effect(product, current))  # before price can still be 100
        current["protection"]["active"] = False
        self.assertFalse(timer_effect(product, current))

    def test_existing_active_protection_does_not_renew_on_every_future_cron(self):
        product = item()
        current = observation(product, protected=True)
        now = datetime.now(timezone.utc)
        current["protection"]["expired_at"] = (now + timedelta(days=20)).isoformat()
        self.assertTrue(timer_effect(product, current, now=now))
        self.assertFalse(timer_effect(product, current, now=now, renewal=True))


class ExecutionAndJournalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.product = item()
        self.plan = {"schema_version": 1, "decision_id": "test", "approved": True,
                     "enabled": True, "approvalReference": "owner", "items": [self.product]}
        self.initial = observation(self.product)
        self.journal = Journal(Path(self.temp.name) / "journal.json", self.plan)
        self.reader = Reader(self.initial)

    def tearDown(self):
        self.journal.close()
        self.temp.cleanup()

    def executor(self, writer, evaluator=ready):
        return Executor(self.plan, bundle(self.product, self.initial), contract(), self.journal,
                        self.reader, writer, evaluator=evaluator, sleep=lambda _: None)

    def test_timer_price_action_order_and_actual_readback(self):
        writer = Writer(self.reader, self.journal)
        report = self.executor(writer).run(execute=True)
        self.assertEqual([call[0] for call in writer.calls], ["timer", "price", "activate"])
        self.assertEqual(report["items"][0]["status"], "VERIFIED")
        self.assertEqual(report["marketplace_write_attempts"], 3)
        self.assertFalse(report["full_profit_verified"])
        self.assertTrue(all(op["state"] == "VERIFIED_BY_READBACK" for op in self.journal.data["operations"]))
        self.assertIn("website_prices", report["items"][0]["after"]["actions"]["101"]["participant"])

    def test_lost_response_is_reconciled_by_readback_without_write_retry(self):
        writer = Writer(self.reader, self.journal, lose_response=True)
        report = self.executor(writer).run(execute=True)
        self.assertEqual(report["items"][0]["status"], "VERIFIED")
        self.assertEqual(len(writer.calls), 3)
        self.assertNotIn("private secret", self.journal.path.read_text())

    def test_no_price_write_when_timer_activation_does_not_read_back(self):
        writer = Writer(self.reader, self.journal, apply=False)
        report = self.executor(writer).run(execute=True)
        self.assertEqual(report["items"][0]["status"], "HOLD_PROTECTION_READBACK")
        self.assertEqual([call[0] for call in writer.calls], ["timer"])
        self.assertEqual(self.reader.state["price"]["price"]["price"], 100)

    def test_unresolved_intent_is_read_back_and_never_blindly_resent(self):
        writer = Writer(self.reader, self.journal, apply=False, lose_response=True)
        executor = self.executor(writer)
        executor.run(execute=True)
        reads_before = self.reader.reads
        executor.run(execute=True)
        self.assertEqual(len(writer.calls), 1)
        self.assertGreater(self.reader.reads, reads_before)
        self.assertEqual(self.journal.data["items"][self.product["sku"]]["status"], "HOLD_PENDING_OPERATION")
        self.assertFalse(writer.published[-1]["verified"])

    def test_changed_payload_does_not_create_new_write_over_unresolved_intent(self):
        writer = Writer(self.reader, self.journal, apply=False)
        executor = self.executor(writer)
        executor.run(execute=True)
        self.reader.state["price"]["price"]["old_price"] = 210
        report = executor.run(execute=True)
        self.assertEqual(len(writer.calls), 1)
        self.assertEqual(len(self.journal.data["operations"]), 1)
        self.assertEqual(report["items"][0]["status"], "HOLD_PENDING_OPERATION")

    def test_partial_effect_publishes_actual_fields_but_blocks_legacy_exception(self):
        class PartialWriter(Writer):
            def write(self, product, operation, path, payload, action_id=None):
                result = super().write(product, operation, path, payload, action_id)
                if operation == "price":
                    self.reader.state["price"]["price"]["min_price"] = 80
                return result
        writer = PartialWriter(self.reader, self.journal)
        report = self.executor(writer).run(execute=True)
        self.assertEqual(report["items"][0]["status"], "HOLD_PRICE_READBACK")
        self.assertEqual(writer.published[-1]["fields"]["price"], "90")
        self.assertEqual(writer.published[-1]["fields"]["min_price"], "80")
        self.assertFalse(writer.published[-1]["verified"])

    def test_completed_rerun_does_not_write_again(self):
        writer = Writer(self.reader, self.journal)
        executor = self.executor(writer)
        executor.run(execute=True)
        report = executor.run(execute=True)
        self.assertEqual(len(writer.calls), 3)
        self.assertEqual(report["marketplace_write_attempts"], 0)
        self.assertEqual(report["items"][0]["status"], "VERIFIED")

    def test_owner_return_to_exact_before_is_not_overwritten(self):
        writer = Writer(self.reader, self.journal)
        executor = self.executor(writer)
        executor.run(execute=True)
        self.reader.state["price"]["price"]["price"] = 100
        report = executor.run(execute=True)
        self.assertEqual(len(writer.calls), 3)
        self.assertEqual(report["items"][0]["status"], "HOLD_OWNER_DRIFT")

    def test_disabled_batch_and_unapproved_sku_are_blocked(self):
        writer = Writer(self.reader, self.journal)
        executor = self.executor(writer)
        executor.plan["enabled"] = False
        with self.assertRaises(ExecutionError):
            executor.run(execute=True)
        executor.plan["enabled"] = True
        with self.assertRaises(ExecutionError):
            executor.run(execute=True, only_skus=["Different"])
        self.assertEqual(writer.calls, [])

    def test_dry_run_prepares_requests_without_writing(self):
        writer = Writer(self.reader, self.journal)
        report = self.executor(writer).run(execute=False)
        self.assertEqual(writer.calls, [])
        self.assertEqual(report["marketplace_write_attempts"], 0)
        self.assertEqual([r["operation"] for r in report["items"][0]["prepared_requests"]], ["timer", "price", "activate"])

    def test_future_only_never_changes_even_approved_lower_price_early(self):
        self.initial = observation(self.product, future=True)
        self.reader = Reader(self.initial)
        writer = Writer(self.reader, self.journal)
        evaluator = lambda *args: {"status": "DEFERRED", "price_write_allowed": False,
            "ready_action_ids": [], "deferred_action_ids": [101], "blockers": [], "financial_check": {}}
        report = self.executor(writer, evaluator).run(execute=True)
        self.assertEqual(writer.calls, [])
        self.assertEqual(report["items"][0]["status"], "DEFERRED_FUTURE_ONLY")

    def test_hold_does_not_initiate_cross_sku_rollback(self):
        writer = Writer(self.reader, self.journal)
        evaluator = lambda *args: {"status": "HOLD", "price_write_allowed": False,
            "ready_action_ids": [], "deferred_action_ids": [], "blockers": ["MISSING_FEES"], "financial_check": {}}
        report = self.executor(writer, evaluator).run(execute=True)
        self.assertEqual(writer.calls, [])
        self.assertEqual(report["items"][0]["status"], "HOLD")

    def test_rollback_requires_authorized_before_and_exact_our_after(self):
        current = copy.deepcopy(self.initial)
        operation = {"before": commercial_state(current), "after": commercial_state(current)}
        self.assertFalse(rollback_eligibility(self.product, operation, current)["allowed"])
        self.product["rollback"] = {"approved": True, "before": operation["before"]}
        self.assertTrue(rollback_eligibility(self.product, operation, current)["allowed"])
        current["price"]["price"]["price"] = 101
        self.assertFalse(rollback_eligibility(self.product, operation, current)["allowed"])


class ReadbackIntegrityTests(unittest.TestCase):
    def test_existing_a_membership_and_boost_must_survive(self):
        product = item(strategy="A", before=100, target=110)
        before = observation(product, active=True, protected=True)
        after = copy.deepcopy(before)
        after["price"]["price"].update(price=110, min_price=110)
        self.assertEqual(preservation_errors(product, before, after, [101]), [])
        after["actions"]["101"]["participant"]["current_boost"] = 54
        self.assertIn("EXISTING_BOOST_LOST_OR_UNVERIFIED", preservation_errors(product, before, after, [101]))
        after["actions"]["101"]["participant"] = None
        self.assertIn("EXISTING_ACTION_LOST", preservation_errors(product, before, after, [101]))

    def test_ack_payload_does_not_leak_server_messages(self):
        summary = response_summary({"result": {"rejected": [{"product_id": 1, "message": "secret"}],
                                              "active_product_ids": [2], "warnings": []}})
        self.assertEqual(summary["rejected_count"], 1)
        self.assertNotIn("secret", json.dumps(summary))


if __name__ == "__main__":
    unittest.main()
