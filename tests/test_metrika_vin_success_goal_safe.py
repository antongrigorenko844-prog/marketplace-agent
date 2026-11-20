"""Offline guards for LEOPARTS single JS form-success goal, no OAuth network."""
import importlib.util, pathlib, unittest
ROOT=pathlib.Path(__file__).resolve().parents[1]
SRC=ROOT/".github/scripts/metrika_vin_success_goal_create_20261120.py"
spec=importlib.util.spec_from_file_location("vin_goal",SRC)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
class VinJsGoalGuardTests(unittest.TestCase):
    def test_valid_script_and_identifier(self):
        compile(SRC.read_text(encoding="utf-8"),str(SRC),"exec")
        self.assertEqual(m.COUNTER,113839860)
        self.assertEqual(m.IDENTIFIER,"LEOPARTS_VIN_SUCCESS_5246286101")
        self.assertEqual(m.PAYLOAD["goal"]["type"],"action")
        self.assertEqual(m.PAYLOAD["goal"]["conditions"],
                         [{"type":"exact","url":"LEOPARTS_VIN_SUCCESS_5246286101"}])
        self.assertNotIn("payment",m.IDENTIFIER.lower())
        self.assertNotIn("order",m.IDENTIFIER.lower())
    def test_preflight_requires_rendered_real_vin_form(self):
        d=m.preflight()
        self.assertEqual(d["rendered_vin_form_id"],"form5246286101")
        self.assertTrue(d["tilda_success_event_hook_present_in_snippet"])
        self.assertTrue(d["real_html_submit_button"])
    def test_api_rejects_all_nonapproved_mutations(self):
        m.ALLOWED_POST=None
        m.POST_CALLED=False
        for verb,url,data in [
            ("POST",m.URL+"/goals",m.PAYLOAD),
            ("PUT",m.URL+"/goal/675506966",m.PAYLOAD),
            ("DELETE",m.URL+"/goal/675507028",None),
            ("GET",m.URL+"/goal/675506966",None),
            ("POST","https://api.direct.yandex.com/json/v501/campaigns",{}),
        ]:
            with self.subTest(verb=verb):
                with self.assertRaisesRegex(RuntimeError,"Outside exact"):
                    m.request(verb,url,data)
    def test_goal_unique_specific_success_only(self):
        self.assertEqual(m.NAME,"LEOPARTS | VIN — успешная отправка")
        doc=m.HTML.read_text(encoding="utf-8")
        self.assertIn('tildaform:aftersuccess',doc)
        self.assertIn('document.getElementById(FORM_ID)',doc)
        self.assertIn('window.ym(COUNTER_ID, "reachGoal", GOAL_IDENTIFIER)',doc)
        self.assertNotIn('onclick=',doc)
        self.assertNotIn('onsubmit=',doc)
if __name__=="__main__":
    unittest.main()
