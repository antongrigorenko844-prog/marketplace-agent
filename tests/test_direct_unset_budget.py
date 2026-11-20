"""Check the budget-unset request is exact and cannot alter non-budget settings."""
import importlib.util
import pathlib
import unittest

PATH=pathlib.Path(__file__).resolve().parents[1]/".github/scripts/direct_unset_budget_20261120.py"
spec=importlib.util.spec_from_file_location("unset_budget",PATH)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class BudgetOnlyTests(unittest.TestCase):
    def test_script_compiles(self):
        compile(PATH.read_text(encoding="utf-8"),str(PATH),"exec")
    def test_campaign_identity(self):
        self.assertEqual(mod.CID,716316525)
        self.assertEqual(mod.PREVIOUS_LIMIT,4000000000)
    def test_variants_never_raise_limit(self):
        for variant in ("explicit_null","empty_highest_position"):
            p=mod.payload(variant)
            self.assertEqual(list(p),["Campaigns"])
            self.assertEqual(len(p["Campaigns"]),1)
            item=p["Campaigns"][0]
            self.assertEqual(item["Id"],716316525)
            self.assertEqual(set(item),{"Id","UnifiedCampaign"})
            self.assertEqual(list(item["UnifiedCampaign"]),["BiddingStrategy"])
            b=item["UnifiedCampaign"]["BiddingStrategy"]
            self.assertEqual(b["Network"],{"BiddingStrategyType":"SERVING_OFF"})
            self.assertEqual(b["Search"]["BiddingStrategyType"],"HIGHEST_POSITION")
            hp=b["Search"]["HighestPosition"]
            self.assertEqual(hp,{"WeeklySpendLimit":None} if variant=="explicit_null" else {})
    def test_nonbudget_removes_only_limit(self):
        c={"Id":716316525,"Name":mod.NAME,"Status":"ACCEPTED",
           "NegativeKeywords":{"Items":["a","b"]},
           "UnifiedCampaign":{"BiddingStrategy":{
             "Search":{"BiddingStrategyType":"HIGHEST_POSITION",
                       "HighestPosition":{"WeeklySpendLimit":4000000000}},
             "Network":{"BiddingStrategyType":"SERVING_OFF"}}}}
        before=mod.nonbudget(c)
        self.assertEqual(before["NegativeKeywords"],{"Items":["a","b"]})
        self.assertEqual(before["UnifiedCampaign"]["BiddingStrategy"]["Search"]["HighestPosition"],{})
        self.assertEqual(c["UnifiedCampaign"]["BiddingStrategy"]["Search"]["HighestPosition"]["WeeklySpendLimit"],4000000000)

if __name__=="__main__":
    unittest.main()
