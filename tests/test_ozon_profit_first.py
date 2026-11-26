import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone

from ozon_profit_guard import unmanaged_ids, reject_legacy_write
from ozon_profit_plan import financial_floor, evaluate, assess_candidate


class ProfitFirst(unittest.TestCase):
    def test_both_floors(self):
        self.assertEqual(financial_floor(300, 100, ".4", 200, ".1"), 1000)
        self.assertEqual(financial_floor(300, 100, ".4", 50, ".3"), 1334)
        self.assertTrue(evaluate(1000,300,100,".4",200,".1")["meets_floor"])
        self.assertFalse(evaluate(999,300,100,".4",200,".1")["meets_floor"])

    def test_invalid_finance(self):
        for rate, pct in [(".9",".2"),("NaN",".1"),(".2",0)]:
            with self.assertRaises((ValueError, ArithmeticError)):
                financial_floor(300,100,rate,200,pct)

    def test_missing_finance_blocks(self):
        x = assess_candidate({"sku":"A"}, {})
        self.assertEqual(x["status"], "BLOCKED")
        self.assertIn("POLICY_NOT_APPROVED", x["blockers"])

    def test_fresh_confirmed_data_and_stale(self):
        now = datetime(2026,11,27,tzinfo=timezone.utc)
        row = {"sku":"A","band":"small","target_price":1100,"cost_rub":300,"fixed_rub":100,
               "variable_rate":".4","verified_action_cap":1200,"verified_protection_price":1000,
               "economics_verified":True,"stock_verified":True,"identity_verified":True,
               "competitors_checked":True,"protection_verified":True,"combined_promotions_verified":True,
               "financial_evidence":"ledger","competition_evidence":"snapshot","protection_evidence":"readback",
               "live_snapshot_at":now.isoformat()}
        p={"approved":True,"approval_reference":"owner","bands":{"small":{"min_rub":200,"min_pct":".1"}}}
        self.assertEqual(assess_candidate(row,p,now)["status"],"PREFLIGHT_PASS_NO_WRITE")
        row["verified_action_cap"]=900
        self.assertIn("ABOVE_VERIFIED_ACTION_CAP",assess_candidate(row,p,now)["blockers"])
        row["live_snapshot_at"]="2026-11-26T00:00:00+00:00"
        self.assertIn("STALE_OR_FUTURE_API_DATA",assess_candidate(row,p,now)["blockers"])

    def test_exact_legacy_isolation(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"policy.json"
            p.write_text(json.dumps({"schema_version":1,"managed_skus":["Dq500"],
                                     "approved":True,"approval_reference":"owner"}))
            self.assertEqual(unmanaged_ids(["Dq500","DQ500"],p),["DQ500"])
            with self.assertRaises(ValueError):
                reject_legacy_write([{"offer_id":"Dq500"}],p)
            with self.assertRaises(ValueError):
                reject_legacy_write([{"product_id":1}],p)
            reject_legacy_write([{"offer_id":"DQ500"}],p)
            p.write_text("{}")
            with self.assertRaises(ValueError):
                unmanaged_ids(["Dq500"],p)


if __name__ == "__main__":
    unittest.main()
