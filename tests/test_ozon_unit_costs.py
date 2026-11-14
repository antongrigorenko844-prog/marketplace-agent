import math
import tempfile
import unittest
from unittest.mock import patch

import ozon_unit


class CostSafetyTests(unittest.TestCase):
    def calculate(self, master, items):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ozon_unit, "DATA", tmp), patch.object(ozon_unit, "_master", return_value=master):
            return ozon_unit.build({"items": items}, {})

    def test_case_distinct_skus_keep_their_own_costs(self):
        rows = self.calculate({"EA888gen3": {"cost": 100}, "Ea888gen3": {"cost": 500}},
                              {sku: {"sold": 1, "revenue": 1000, "net": 800} for sku in ("EA888gen3", "Ea888gen3")})
        self.assertEqual({r["offer_id"]: r["profit"] for r in rows}, {"EA888gen3": 640, "Ea888gen3": 240})

    def test_ambiguous_case_fallback_has_no_cost(self):
        rows = self.calculate({"EA888gen3": {"cost": 100}, "Ea888gen3": {"cost": 500}},
                              {"ea888GEN3": {"sold": 1, "net": 800}})
        self.assertIsNone(rows[0]["profit"])

    def test_missing_zero_negative_and_nonfinite_costs_are_not_profit(self):
        for cost in (None, 0, -1, math.nan, math.inf):
            with self.subTest(cost=cost):
                rows = self.calculate({"x": {"cost": cost}}, {"x": {"sold": 2, "revenue": 1000, "net": 800}})
                self.assertIsNone(rows[0]["profit"])
                self.assertEqual(rows[0]["before_purchase"], 740)
                self.assertIn("Прибыль не рассчитана", ozon_unit.message(rows, "тест"))
                self.assertNotIn("Убыточных товаров за месяц нет", ozon_unit.message(rows, "тест"))

    def test_positive_cost_and_returns_keep_calculation(self):
        rows = self.calculate({"x": {"cost": 250}}, {"x": {"sold": 3, "returned": 1, "revenue": 2000, "net": 1500}})
        self.assertEqual(rows[0]["profit"], 880)

    def test_unique_case_fallback_is_allowed(self):
        rows = self.calculate({"x": {"cost": 250}}, {"X": {"sold": 1, "net": 1500}})
        self.assertEqual(rows[0]["profit"], 1250)


if __name__ == "__main__":
    unittest.main()
