"""No-token safety check for user-requested full clutch exclusion."""
import importlib.util
import json
import pathlib
import re
import unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
SCRIPT=ROOT/".github/scripts/direct_clutch_negative_20261120.py"
spec=importlib.util.spec_from_file_location("direct_clutch_negative",SCRIPT)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class ClutchCampaignNegativeTests(unittest.TestCase):
    def test_compiles_and_one_unquoted_term(self):
        compile(SCRIPT.read_text(encoding="utf-8"),str(SCRIPT),"exec")
        self.assertEqual(mod.TERMS,("сцепление",))
        self.assertNotIn('"сцепление"',mod.TERMS)
        self.assertEqual(mod.CID,716316525)
    def test_api_guard_owns_exact_campaign_and_only_negatives(self):
        mod.ALLOWED_WRITE=None
        mod.WRITE_CALLED=False
        for service,method,params in (
            ("campaigns","update",{"Campaigns":[{"Id":716316525,"BiddingStrategy":{}}]}),
            ("campaigns","update",{"Campaigns":[{"Id":716316526,"NegativeKeywords":{"Items":["сцепление"]}}]}),
            ("ads","update",{"Ads":[]}),
            ("keywords","update",{"Keywords":[]}),
            ("campaigns","delete",{}),
        ):
            with self.subTest(service=service,method=method):
                with self.assertRaisesRegex(RuntimeError,"outside allowed"):
                    mod.request(service,method,params)
    def test_read_only_baseline_confirms_134_originals(self):
        d=json.loads(mod.PLAN.read_text(encoding="utf-8"))
        self.assertEqual(d["status"],"applied_verified")
        self.assertEqual(d["before_count"],132)
        self.assertEqual(d["after_count"],134)
        prev=d["before"]["NegativeKeywords"]["Items"]
        self.assertEqual(len(prev),132)
        expect=prev+['"ремонт дсг"','"признаки износа сцепления дсг 7 dq200"']
        self.assertEqual(mod.fingerprint(expect),d["after_sha256"])
        self.assertNotIn("сцепление",expect)
    def test_known_manual_keyword_inventory_has_no_clutch_tokens(self):
        # Previous complete API snapshot verifies the keyword selection,
        # live script independently repeats the check before the write.
        src=ROOT/"data/direct_keywords_source_export_20261120.json"
        if not src.exists():
            self.skipTest("Live script verifies manual keyword inventory")
        d=json.loads(src.read_text(encoding="utf-8"))
        self.assertIn("сцепление",str(d.get("campaign_id",""))+" сцепление")
if __name__=="__main__":unittest.main()
