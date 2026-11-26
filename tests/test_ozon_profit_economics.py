"""Meaningful financial regression cases; no network or credentials."""
import copy
import unittest

from ozon_profit_economics import (
    current_projection, exact_sku_index, historical_contribution, procurement_context,
)


def tariffs():
    return {
        "price": 1000, "acquiring": 10,
        "has_fbs_stocks": True, "has_fbo_stocks": True,
        "commissions": {
            "sales_percent_fbs": 47, "sales_percent_fbo": 47,
            "fbs_direct_flow_trans_max_amount": 80, "fbs_deliv_to_customer_amount": 10,
            "fbs_first_mile_max_amount": 10,
            "fbo_direct_flow_trans_max_amount": 70, "fbo_deliv_to_customer_amount": 10,
        },
    }


def clean_history():
    return {
        "sold": 10, "returned": 0, "revenue": 10000, "commission": -4700,
        "logistics": -1000, "services": -500, "net": 3800,
    }


def fees():
    return {"Эквайринг": -100, "Звёздные товары": -150,
            "Подписка Управление отзывами": -130, "Упаковка товара партнёрами": -120}


class EconomicsTests(unittest.TestCase):
    def test_actual_mechatronic_accruals_keep_two_percentage_bases(self):
        examples = [
            (30500, -949.37, -2291.31, 62126.82, 24495.82, 80.3142, 20.6107),
            (31000, -375, -4608.4, 60384.1, 22253.1, 71.7842, 18.7237),
        ]
        for cost, logistics, services, net, contribution, on_cost, margin in examples:
            with self.subTest(cost=cost):
                f = {"sold": 1, "returned": 0, "revenue": 118850, "commission": -53482.5,
                     "logistics": logistics, "services": services, "net": net}
                result = historical_contribution(f, cost)
                self.assertEqual(result["contribution_rub"], contribution)
                self.assertEqual(result["profit_on_cost_pct"], on_cost)
                self.assertEqual(result["margin_on_revenue_pct"], margin)
                self.assertIn("LOW_OBSERVATION_COUNT", result["flags"])
                self.assertFalse(result["verified"])
                self.assertFalse(result["write_allowed"])

    def test_acquiring_once_and_percent_services_scale_with_candidate_price(self):
        base = current_projection(1000, 100, tariffs(), clean_history(), fees())
        candidate = current_projection(2000, 100, tariffs(), clean_history(), fees())
        historical = historical_contribution(clean_history(), 100)
        self.assertEqual(base["contribution_rub"], historical["contribution_rub"])
        self.assertEqual(base["contribution_rub"], 220)
        self.assertEqual(candidate["contribution_rub"], 652)
        deductions = candidate["by_scheme"]["fbs"]["deductions"]
        self.assertEqual(deductions["acquiring_rub"], 20)
        self.assertEqual(deductions["percentage_services_rub"], 56)
        self.assertEqual(deductions["fixed_services_proxy_rub"], 12)
        self.assertEqual(base["reference_scheme"], "fbs")
        self.assertEqual(base["by_scheme"]["fbo"]["contribution_rub"], 240)

    def test_returns_never_credit_inventory_or_create_unit_margin_automatically(self):
        f = {"sold": 2, "returned": 1, "revenue": 1000, "commission": -470,
             "logistics": -100, "services": -30, "net": 400}
        historical = historical_contribution(f, 100)
        self.assertEqual(historical["period_contribution_rub"], 140)
        self.assertEqual(historical["procurement_debit_rub"], 200)
        self.assertIsNone(historical["return_inventory_credit_rub"])
        self.assertIsNone(historical["contribution_rub"])
        self.assertIsNone(historical["profit_on_cost_pct"])
        projected = current_projection(1000, 100, tariffs(), f, {"Эквайринг": -30})
        self.assertFalse(projected["services_estimated"])
        self.assertIn("current_marketplace_service_costs", projected["unknown_costs"])

    def test_no_history_exposes_only_partial_subtotal_and_unknown_service_cost(self):
        result = current_projection(1000, 100, tariffs())
        self.assertEqual(result["contribution_rub"], 260)
        self.assertFalse(result["services_estimated"])
        self.assertIsNone(result["by_scheme"]["fbs"]["deductions"]["percentage_services_rub"])
        self.assertIn("current_marketplace_service_costs", result["unknown_costs"])
        self.assertIn("seller_packaging", result["unknown_costs"])
        self.assertFalse(result["verified"])

    def test_missing_cost_and_all_zero_tariffs_are_not_free_trading(self):
        snapshot = tariffs()
        snapshot.update({"acquiring": 0, "has_fbs_stocks": False, "has_fbo_stocks": False})
        snapshot["commissions"] = {key: 0 for key in snapshot["commissions"]}
        result = current_projection(1000, 100, snapshot)
        self.assertIsNone(result["contribution_rub"])
        self.assertIn("SUSPECT_ALL_ZERO_TARIFFS_UNVERIFIED", result["flags"])
        missing = current_projection(1000, None, tariffs())
        self.assertIsNone(missing["contribution_rub"])
        self.assertIn("MISSING_PROCUREMENT_COST", missing["flags"])

    def test_unrelated_scheme_tariff_does_not_validate_free_fbs_or_fbo(self):
        snapshot = tariffs()
        snapshot.update({"acquiring": 0, "has_fbs_stocks": False, "has_fbo_stocks": False})
        snapshot["commissions"] = {key: 0 for key in snapshot["commissions"]}
        snapshot["commissions"]["sales_percent_rfbs"] = 47
        result = current_projection(1000, 100, snapshot)
        self.assertIsNone(result["contribution_rub"])
        self.assertIn("SUSPECT_ZERO_TARIFF_FBS_UNVERIFIED", result["flags"])

    def test_positive_acquiring_credit_is_not_repeated_in_current_projection(self):
        history = clean_history()
        history.update({"services": -300, "net": 4000})
        service_details = fees()
        service_details["Эквайринг"] = 100
        result = current_projection(1000, 100, tariffs(), history, service_details)
        self.assertEqual(result["contribution_rub"], 220)
        self.assertIn("HISTORICAL_ACQUIRING_CREDIT_NOT_PROJECTED", result["flags"])

    def test_unreconciled_fee_details_do_not_turn_into_a_complete_service_model(self):
        result = current_projection(1000, 100, tariffs(), clean_history(), {"Эквайринг": -100})
        self.assertFalse(result["services_estimated"])
        self.assertIn("SERVICE_DETAILS_UNRECONCILED", result["flags"])
        self.assertIn("current_marketplace_service_costs", result["unknown_costs"])

    def test_procurement_conflicts_preserve_original_values(self):
        result = procurement_context(100, 170, 170)
        self.assertEqual(result["master_cost_rub"], 100)
        self.assertEqual(result["scenario_cost_rub"], 170)
        self.assertIn("CONSERVATIVE_REFERENCE_ABOVE_MASTER", result["flags"])
        self.assertFalse(result["fully_landed_cost_verified"])
        self.assertIsNone(procurement_context(None, 170)["scenario_cost_rub"])

    def test_case_and_unicode_sku_identities_stay_distinct(self):
        rows = [{"sku": "DQ500"}, {"sku": "Dq500"}, {"sku": "EA888gen3"},
                {"sku": "Ea888gen3"}, {"sku": "0am325025С"}, {"sku": "0am325025C"}]
        self.assertEqual(len(exact_sku_index(rows)), 6)
        with self.assertRaises(ValueError):
            exact_sku_index(rows + [{"sku": "DQ500"}])

    def test_calculators_do_not_mutate_supplied_evidence(self):
        supplied = [tariffs(), clean_history(), fees(), {"sku": "Dq500", "values": [280, 280]}]
        original = copy.deepcopy(supplied)
        current_projection(2000, 100, supplied[0], supplied[1], supplied[2], cost_source=supplied[3])
        historical_contribution(supplied[1], 100, cost_source=supplied[3])
        self.assertEqual(supplied, original)

    def test_nonfinite_and_boolean_amounts_are_rejected(self):
        for invalid in ("NaN", "Infinity", True):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                current_projection(invalid, 100, tariffs())


if __name__ == "__main__":
    unittest.main()
