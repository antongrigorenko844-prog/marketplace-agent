"""Offline protection for one Metrica checkout-start goal. No OAuth requests."""
from pathlib import Path
from unittest.mock import patch
import importlib.util,json,unittest
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/".github/scripts/metrika_checkout_start_create_20261120.py"
spec=importlib.util.spec_from_file_location("metrika_checkout",SRC)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
class MetricaCheckoutSafetyTests(unittest.TestCase):
    def test_syntax_and_exact_scope(self):
        compile(SRC.read_text(encoding="utf-8"),str(SRC),"exec")
        self.assertEqual(mod.CID,113839860)
        self.assertEqual(mod.GOAL_NAME,"LEOPARTS | Начало оплаты (не покупка)")
        self.assertEqual(mod.GOAL_PATH,"/tilda/form5260805101/payment/")
        self.assertEqual(mod.PAYLOAD,{"goal":{"name":mod.GOAL_NAME,"type":"url",
            "conditions":[{"type":"contain","url":mod.GOAL_PATH}]}})
    def test_no_unapproved_write(self):
        mod.ALLOW_CREATE=False
        mod.POST_CALLED=False
        for method,path,payload in [
            ("PUT",mod.ENDPOINT+"/goal/675506966",{}),
            ("DELETE",mod.ENDPOINT+"/goal/675507028",None),
            ("POST",mod.ENDPOINT+"/goals",{"goal":{"name":"Purchase","type":"url"}}),
            ("POST",mod.ENDPOINT+"/goals",mod.PAYLOAD),
            ("GET",mod.ENDPOINT+"/goal/675506966",None),
        ]:
            with self.subTest(method=method,path=path):
                with self.assertRaisesRegex(RuntimeError,"outside|Unexpected"):
                    mod.request(method,path,payload)
    def test_existing_goal_definitions_preserved(self):
        originals=[{"id":675506966,"name":"Заявка по VIN","type":"url",
                    "conditions":[{"type":"contain","url":"/tilda/form5246286101/submitted/"}]},
                   {"id":675507028,"name":"Заказ с сайта","type":"url",
                    "conditions":[{"type":"contain","url":"/tilda/form5260805101/submitted/"}]}]
        goals=originals+[{"id":1000+i,"name":f"Existing {i}","type":"e_cart"}
                         for i in range(10)]
        self.assertIsNone(mod.verify_prior(goals))
        changed=json.loads(json.dumps(goals))
        changed[1]["conditions"][0]["url"]="/tilda/form5260805101/some-other-event/"
        with self.assertRaisesRegex(RuntimeError,"Primary goal"):
            mod.verify_prior(changed)
    def test_observed_event_and_preflight(self):
        pre=mod.preflight()
        self.assertEqual(pre["historical_event_count"],1)
        self.assertEqual(pre["source"],"Metrica virtual pageviews")
    def test_goal_is_not_purchase_event(self):
        self.assertEqual(mod.PAYLOAD["goal"]["type"],"url")
        self.assertNotIn("Ecommerce: покупка",mod.GOAL_NAME)
        self.assertTrue("не покупка" in mod.GOAL_NAME)
if __name__=="__main__":unittest.main()
