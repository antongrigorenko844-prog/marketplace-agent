"""No-network verification of the two exact service/diagnostic exclusions."""
import importlib.util,json,pathlib,unittest
PATH=pathlib.Path(__file__).resolve().parents[1]/".github/scripts/direct_info_service_negatives_20261120.py"
spec=importlib.util.spec_from_file_location("info_negatives",PATH)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
SRC=pathlib.Path(__file__).resolve().parents[1]/"data/direct_optimization_20261120/live_traffic_review.json"
class TestDirectNewExactNegatives(unittest.TestCase):
    def test_compiles(self):compile(PATH.read_text(encoding="utf-8"),str(PATH),"exec")
    def test_scope(self):
        self.assertEqual(m.CID,716316525)
        self.assertEqual(m.TERMS,('"ремонт дсг"','"признаки износа сцепления дсг 7 dq200"'))
        self.assertTrue(all(x.startswith('"') and x.endswith('"') and len(x.split())<=7 for x in m.TERMS))
        for broad in ('"ремонт"','"плата"','"замена"','"диагностика"'):
            self.assertNotIn(broad,m.TERMS)
    def test_guards_reject_unapproved_writes(self):
        m.ALLOWED_WRITE=None
        for service,method,params in [
            ("campaigns","update",{"Campaigns":[{"Id":716316525}]}),
            ("keywords","update",{"Keywords":[]}),
            ("ads","update",{"Ads":[]}),
            ("campaigns","delete",{})]:
            with self.subTest(service=service,method=method):
                with self.assertRaisesRegex(RuntimeError,"outside allowed"):
                    m.request(service,method,params)
    def test_paid_query_evidence_and_exact_current_baseline(self):
        v=json.loads(SRC.read_text(encoding="utf-8"))
        self.assertEqual(v["status"],"complete")
        self.assertEqual(v["campaign_id"],m.CID)
        self.assertEqual(v["negatives_count"],132)
        self.assertIsNone(v["weekly_limit_raw"])
        self.assertEqual(v["manual_keywords"],266)
        self.assertEqual(v["autotargeting_conditions"],6)
        for item in m.TERMS:
            name=item[1:-1]
            hits=[r for r in v["query_paid"] if r["Query"]==name]
            self.assertGreater(len(hits),0)
            self.assertTrue(all(float(r["Cost"])>0 and float(r["Conversions"])==0 for r in hits))
if __name__=="__main__":unittest.main()
