"""Commercial-scenario and evidence-integrity regressions; no API calls."""
from copy import deepcopy
import unittest

from ozon_profit_catalog import build_catalogue, normalize_prices, unit_growth, validate_live_core

NOW = "2026-11-26T18:00:00+00:00"


def price(sku="A", pid=1, value=1000):
    return {
        "sku": sku, "product_id": pid, "price": value, "min_price": value,
        "old_price": value * 1.2, "auto": False, "acquiring": value * .01,
        "has_fbs_stocks": True, "has_fbo_stocks": True,
        "commissions": {"sales_percent_fbs": 47, "sales_percent_fbo": 47,
            "fbs_direct_flow_trans_max_amount": 80, "fbs_deliv_to_customer_amount": 10,
            "fbs_first_mile_max_amount": 10, "fbo_direct_flow_trans_max_amount": 70,
            "fbo_deliv_to_customer_amount": 10},
    }


def action(aid=100, cap=1200, *, sku="A", pid=1, group="participants", typ="ELASTIC_BOOSTING",
           boost=55, max_boost=55, max_price=1200, start="2026-11-01T00:00:00Z"):
    entry = {
        "id": pid, "mapped_offer_id": sku, "max_action_price": {"amount": str(cap), "currency": "RUB"},
        "price_max_elastic": {"amount": str(max_price), "currency": "RUB"},
        "price_min_elastic": {"amount": str(cap), "currency": "RUB"},
        "current_boost": boost, "min_boost": 15, "max_boost": max_boost,
    }
    return {"action": {"id": aid, "title": "Action " + str(aid), "action_type": typ,
                       "discount_type": "CURRENCY", "discount_value": 0,
                       "date_start": start, "date_end": "2026-12-31T23:59:59Z"},
            "sections": {name: {"status": "ok", "rows": [entry] if name == group else []}
                         for name in ("participants", "candidates")}}


def build(**changes):
    supplied = {"prices": [price()], "metadata": [{"sku": "A", "title": "Товар", "master_cost": 100}],
                "promotions": [action()], "as_of": NOW}
    supplied.update(changes)
    return build_catalogue(**supplied)


def live_inputs():
    p = price()
    p["offer_id"] = p.pop("sku")
    p["price"] = {"price": "1000", "min_price": "1000", "old_price": "1200", "currency_code": "RUB",
                  "auto_add_to_ozon_actions_list_enabled": "DISABLED"}
    products = [{"offer_id": "A", "product_id": 1, "archived": False,
                 "has_fbs_stocks": True, "has_fbo_stocks": True}]
    manifest = {"mode": "READ_ONLY", "status": "partial", "completed_at": NOW,
                "catalog": {"active_count": 1, "exact_offer_ids": ["A"]},
                "sections": {name: {"status": "ok", "count": 1, "errors": [], "coverage": {}}
                             for name in ("products", "prices", "promotions", "master")}}
    manifest["sections"]["finance"] = {"status": "partial", "errors": [{"code": "day_missing"}]}
    manifest["sections"]["protection"] = {"status": "partial", "errors": [{"code": "unknown_schema"}]}
    return {"prices": [p], "products": products, "manifest": manifest, "source_mode": "LIVE_READ_ONLY",
            "master": {"A": {"title": "Live title", "cost": 100, "ozon_enabled": "да"}}}


class CatalogueTests(unittest.TestCase):
    def test_exact_identity_and_scope_do_not_fold_or_include_metadata_only_skus(self):
        result = build(prices=[price("DQ500", 1), price("Dq500", 2)],
                       metadata=[{"sku": "DQ500", "master_cost": 100},
                                 {"sku": "Dq500", "master_cost": 200},
                                 {"sku": "OTHER", "master_cost": 50}], promotions=[])
        self.assertEqual([(r["sku"], r["cost_rub"]) for r in result["rows"]], [("DQ500", 100), ("Dq500", 200)])
        self.assertEqual(result["scope_count"], 2)
        self.assertEqual(result["summary"]["metadata_skus_outside_price_scope"], ["OTHER"])
        with self.assertRaises(ValueError):
            build(prices=[price(), price()])

    def test_raise_requires_current_elastic_strength_and_every_existing_cap(self):
        recovery = {"A": {"Цена пилота НЕ УТВЕРЖДЕНА": "1200"}}
        row = build(recovery=recovery)["rows"][0]
        self.assertEqual(row["strategy_code"], "A")
        self.assertEqual(row["review_target_price"], 1200)
        self.assertGreater(row["review_target_contribution_rub"], row["current_contribution_rub"])
        self.assertGreater(row["tolerable_unit_decline_pct"], 0)
        for extra in (action(boost=24.3), action(cap=1100),
                      action(aid=101, cap=1050, typ="STOCK_DISCOUNT", start="2026-12-01T00:00:00Z")):
            with self.subTest(extra=extra):
                row = build(recovery=recovery, promotions=[extra])["rows"][0]
                self.assertFalse(row["scenarios"]["preservation"]["is_raise"])
        row = build(recovery=recovery, promotions=[action(), action(aid=101, cap=1100, typ="STOCK_DISCOUNT")])["rows"][0]
        self.assertEqual(row["scenarios"]["preservation"]["target_price"], 1000)

    def test_partial_promotion_coverage_never_certifies_preserving_all_actions(self):
        promo = action()
        promo["sections"]["candidates"]["status"] = "partial"
        row = build(promotions=[promo], recovery={"A": {"Прежняя наблюдавшаяся цена": 1200}})["rows"][0]
        self.assertFalse(row["scenarios"]["preservation"]["is_raise"])
        self.assertFalse(row["scenarios"]["preservation"]["preserves_observed_membership_and_elastic_strength"])
        self.assertIn("PROMOTION_COVERAGE_INCOMPLETE", row["flags"])

    def test_future_same_price_action_is_scheduled_opportunity_not_active_participation(self):
        row = build(promotions=[action(group="candidates", cap=1000, typ="STOCK_DISCOUNT", start="2026-12-01T00:00:00Z")])["rows"][0]
        self.assertEqual(row["strategy_code"], "B")
        self.assertEqual(row["current_actions_count"], 0)
        self.assertEqual(row["future_same_price_candidates_count"], 1)
        self.assertEqual(row["active_same_price_candidates_count"], 0)
        self.assertIn("будущая", row["strategy_reason"])

    def test_closest_discount_uses_actual_cap_and_matches_like_for_like_projection(self):
        row = build(promotions=[action(aid=100, group="candidates", cap=900, max_price=700),
                                action(aid=101, group="candidates", cap=800, typ="STOCK_DISCOUNT")])["rows"][0]
        self.assertEqual(row["strategy_code"], "C")
        close = row["scenarios"]["closest_discount"]
        self.assertEqual(close["target_price"], 900)
        self.assertEqual(close["contribution_rub"], 214)
        self.assertEqual(row["current_contribution_rub"], 260)
        self.assertAlmostEqual(close["required_unit_growth_pct"], 21.4953)
        self.assertEqual(row["scenarios"]["max_boost"]["target_price"], 700)
        self.assertTrue(all("at_action_cap" in a for a in row["actions"]))

    def test_unprofitable_max_boost_retains_a_less_aggressive_alternative(self):
        row = build(promotions=[action(group="candidates", cap=900, max_price=300)])["rows"][0]
        self.assertEqual(row["strategy_code"], "D")
        self.assertEqual(row["review_target_price"], 900)
        self.assertLess(row["scenarios"]["max_boost"]["contribution_rub"], 0)
        self.assertIsNone(row["scenarios"]["max_boost"]["required_unit_growth_pct"])

    def test_no_history_is_partial_and_does_not_erase_technical_opportunity(self):
        row = build(promotions=[action(group="candidates", cap=1000)])["rows"][0]
        self.assertEqual(row["strategy_code"], "B")
        self.assertEqual(row["financial_model"], "partial_tariff_subtotal")
        self.assertFalse(row["economics"]["current"]["services_estimated"])
        self.assertIn("current_marketplace_service_costs", row["unknown_costs"])
        self.assertFalse(row["write_allowed"])
        self.assertIsNone(row["approved_floor_rub"])

    def test_missing_cost_and_zero_tariff_keep_opportunities_without_profit_or_floor(self):
        p = price()
        p["acquiring"] = 0
        p["commissions"] = {key: 0 for key in p["commissions"]}
        for kwargs in ({"metadata": [{"sku": "A", "master_cost": None}]}, {"prices": [p]}):
            with self.subTest(kwargs=kwargs):
                row = build(promotions=[action(group="candidates", cap=1000)], **kwargs)["rows"][0]
                self.assertEqual(row["strategy_code"], "G")
                self.assertIsNone(row["current_contribution_rub"])
                self.assertEqual(row["same_price_candidates_count"], 1)
                self.assertIsNone(row["approved_floor_rub"])

    def test_owner_exclusion_precedes_missing_economics_and_old_note_is_only_a_conflict(self):
        held = build(master={"A": {"cost": None, "ozon_enabled": "нет"}})["rows"][0]
        self.assertEqual(held["strategy_code"], "F")
        self.assertTrue(held["owner_hold"])
        self.assertIn("MISSING_PROCUREMENT_COST", held["flags"])
        row = build(master={"A": {"cost": 100, "ozon_enabled": "да", "notes": "2025: Ozon:нет / SPU"}},
                    promotions=[action(group="candidates")])["rows"][0]
        self.assertEqual(row["strategy_code"], "B")
        self.assertFalse(row["owner_hold"])
        self.assertIn("OLDER_NOTE_CONFLICTS_WITH_CURRENT_CHANNEL_FLAG", row["flags"])

    def test_known_cost_nonpositive_partial_gets_e_without_claim_of_net_loss(self):
        row = build(metadata=[{"sku": "A", "master_cost": 500}])["rows"][0]
        self.assertEqual(row["strategy_code"], "E")
        self.assertLess(row["current_contribution_rub"], 0)
        self.assertIn("NONPOSITIVE_PARTIAL_CONTRIBUTION", row["flags"])
        self.assertEqual(row["metric_kind"], "partial_contribution_not_net_profit")
        self.assertIsNone(row["required_unit_growth_pct"])

    def test_vouchers_additional_discounts_and_unknown_types_are_not_safe_same_price_actions(self):
        for changes in ({"discount_type": "PERCENT", "discount_value": 10},
                        {"is_voucher_action": True}, {"action_type": "UNKNOWN_NEW_PROGRAM"}):
            promo = action(group="candidates", cap=1200)
            promo["action"].update(changes)
            row = build(promotions=[promo])["rows"][0]
            self.assertEqual(row["strategy_code"], "F")
            self.assertEqual(row["same_price_candidates_count"], 0)
            self.assertIn("SEPARATE_DISCOUNT_MODEL_REQUIRED", row["actions"][0]["at_action_cap"]["flags"])

    def test_expired_actions_are_visible_but_do_not_become_new_opportunities(self):
        promo = action(group="candidates")
        promo["action"]["date_end"] = "2026-11-20T00:00:00Z"
        row = build(promotions=[promo])["rows"][0]
        self.assertEqual(row["same_price_candidates_count"], 0)
        self.assertEqual(row["actions"][0]["lifecycle"], "expired")
        self.assertIsNone(row["scenarios"]["max_boost"]["target_price"])

    def test_identity_conflicts_cannot_confirm_stock_or_promotions(self):
        row = build(stocks=[{"offer_id": "A", "product_id": 999, "stocks": [{"present": 5}]}])["rows"][0]
        self.assertEqual(row["stock"]["status"], "IDENTITY_CONFLICT")
        self.assertEqual(row["stock"]["rows"], [])
        promo = action(sku="Dq500", pid=1)
        row = build(promotions=[promo], recovery={"A": {"Прежняя наблюдавшаяся цена": 1200}})["rows"][0]
        self.assertEqual(row["actions"], [])
        self.assertIn("PROMOTION_IDENTITY_MISMATCH", row["flags"])
        self.assertFalse(row["scenarios"]["preservation"]["is_raise"])

    def test_minimum_equal_to_price_does_not_prove_active_protection(self):
        row = build()["rows"][0]
        self.assertEqual(row["observed_min_price"], row["current_price"])
        self.assertEqual(row["protection"]["status"], "NOT_VERIFIED")
        self.assertFalse(row["protection"]["verified"])
        self.assertIn("PROTECTION_ACTIVE_STATE_NOT_CONFIRMED", row["flags"])

    def test_unit_growth_formula_is_only_defined_for_positive_comparable_contributions(self):
        self.assertEqual(unit_growth(100, 50), 100)
        self.assertEqual(unit_growth(100, 200), -50)
        for pair in ((None, 50), (100, None), (0, 50), (-1, 50), (100, 0), (100, -1)):
            self.assertIsNone(unit_growth(*pair))

    def test_live_partial_finance_and_unknown_timer_do_not_invalidate_complete_catalogue(self):
        kwargs = live_inputs()
        result = build(**kwargs, finance={"collection_complete": False, "items": {},
                                         "coverage": {"expected_dates": 30, "collected_dates": 29, "failed_dates": ["2026-11-03"]}})
        row = result["rows"][0]
        self.assertEqual(result["source_mode"], "LIVE_READ_ONLY")
        self.assertIn("PARTIAL_FINANCE_PERIOD", row["flags"])
        self.assertIsNone(row["historical_contribution_rub"])
        self.assertFalse(result["summary"]["finance_period"]["collection_complete"])
        self.assertEqual(result["summary"]["finance_period"]["coverage"]["collected_dates"], 29)
        self.assertEqual(row["title"], "Live title")

    def test_live_missing_master_or_finance_never_silently_reuses_saved_values(self):
        kwargs = live_inputs()
        kwargs["master"] = None
        metadata = [{"sku": "A", "master_cost": 100,
                     "finance": {"sold": 10, "returned": 0, "revenue": 10000,
                                 "commission": -4700, "logistics": -1000, "services": -100, "net": 4200}}]
        row = build(**kwargs, metadata=metadata, finance={})["rows"][0]
        self.assertIsNone(row["cost_rub"])
        self.assertIsNone(row["historical_contribution_rub"])
        self.assertEqual(row["strategy_code"], "G")

    def test_live_core_partial_status_missing_price_duplicates_or_id_mismatch_are_fatal(self):
        variants = []
        for name in ("products", "prices"):
            kwargs = live_inputs()
            kwargs["manifest"]["sections"][name]["status"] = "partial"
            variants.append(kwargs)
        kwargs = live_inputs(); kwargs["prices"] = []; variants.append(kwargs)
        kwargs = live_inputs(); kwargs["prices"].append(deepcopy(kwargs["prices"][0])); variants.append(kwargs)
        kwargs = live_inputs(); kwargs["prices"][0]["product_id"] = 2; variants.append(kwargs)
        kwargs = live_inputs(); kwargs["products"][0]["archived"] = None; variants.append(kwargs)
        kwargs = live_inputs(); kwargs["manifest"]["sections"]["prices"]["coverage"]["missing_offer_ids"] = ["A"]; variants.append(kwargs)
        kwargs = live_inputs(); kwargs["prices"][0]["price"]["currency_code"] = "USD"; variants.append(kwargs)
        for kwargs in variants:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                build(**kwargs)

    def test_all_scenarios_are_read_only_and_evidence_is_not_mutated(self):
        supplied = {"prices": [price()], "metadata": [{"sku": "A", "master_cost": 100}],
                    "promotions": [action(), action(aid=101, group="candidates", cap=900)],
                    "recovery": {"A": {"Прежняя наблюдавшаяся цена": 1200}}, "as_of": NOW}
        before = deepcopy(supplied)
        result = build_catalogue(**supplied)
        self.assertEqual(supplied, before)
        self.assertFalse(result["write_allowed"])
        self.assertEqual(result["marketplace_writes"], 0)
        row = result["rows"][0]
        for scenario in list(row["scenarios"].values()) + [a["at_action_cap"] for a in row["actions"]]:
            self.assertFalse(scenario["write_allowed"])
            self.assertIsNone(scenario["approved_floor_rub"])
            self.assertIsNone(scenario["volume_forecast"])


if __name__ == "__main__":
    unittest.main()
