"""Offline regression tests for oil-change service negative, no API credentials."""
import importlib.util
import json
from pathlib import Path
import unittest
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/".github/scripts/direct_oil_change_negative_20261120.py"
spec=importlib.util.spec_from_file_location("oil_neg",SRC)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class OilChangeGuardTests(unittest.TestCase):
    def test_script_compiles(self):
        compile(SRC.read_text(encoding="utf-8"),str(SRC),"exec")
    def test_only_exactly_two_words_as_one_minus_phrase(self):
        self.assertEqual(mod.CID,716316525)
        self.assertEqual(mod.TERMS,("замена масла",))
        self.assertNotIn("масло",mod.TERMS)
        self.assertNotIn("замена",mod.TERMS)
        self.assertNotIn('"замена масла"',mod.TERMS)
    def test_live_previous_proof_chain(self):
        p=json.loads(mod.PLAN.read_text(encoding="utf-8"))
        self.assertEqual(p["status"],"applied_verified")
        self.assertEqual(p["before_count"],134)
        self.assertEqual(p["after_count"],135)
        self.assertTrue(p["no_extra_negatives"])
        self.assertTrue(p["unchanged_original_134"])
        self.assertTrue(p["single_clutch_word_present"])
        existing=p["before"]["NegativeKeywords"]["Items"]
        self.assertEqual(len(existing),134)
        self.assertNotIn("сцепление",existing)
        self.assertNotIn("замена масла",existing)
        self.assertEqual(len(set(existing)),134)
    def test_guard_denies_unexpected_mutations(self):
        mod.ALLOWED_WRITE=None
        mod.WRITE_CALLED=False
        for svc,meth,p in [
            ("campaigns","update",{"Campaigns":[{"Id":716316525,"NegativeKeywords":{"Items":["замена масла"]}}]}),
            ("campaigns","update",{"Campaigns":[{"Id":999,"NegativeKeywords":{"Items":[]}}]}),
            ("ads","update",{"Ads":[]}),
            ("keywords","update",{"Keywords":[]}),
            ("campaigns","delete",{}),
        ]:
            with self.subTest(svc=svc,meth=meth):
                with self.assertRaisesRegex(RuntimeError,"outside allowed"):
                    mod.request(svc,meth,p)
if __name__=="__main__":
    unittest.main()
