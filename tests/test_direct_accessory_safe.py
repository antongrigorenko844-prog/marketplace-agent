"""Offline invariants for scoped LEOPARTS Accessory category experiment."""
import importlib.util,json,pathlib,unittest
PATH=pathlib.Path(__file__).resolve().parents[1]/".github/scripts/direct_accessory_test_20261120.py"
spec=importlib.util.spec_from_file_location("direct_accessory_safe",PATH)
m=importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
BASE=pathlib.Path(__file__).resolve().parents[1]/"data/direct_optimization_20261120/structure_review.json"
class TestAccessorySafety(unittest.TestCase):
    def test_compiles(self):
        compile(PATH.read_text(encoding="utf-8"),str(PATH),"exec")
    def test_intended_ids(self):
        self.assertEqual(m.CID,716316525)
        self.assertEqual(m.TARGET,205826183946)
        self.assertEqual(m.GROUP,5826183946)
    def test_guard_rejects_unknown_updates_before_network(self):
        m.UPDATE_ALLOWED=None
        for method,service,params in [
            ("update","ads",{}),("update","keywords",{"Keywords":[{"Id":123}]}),
            ("delete","keywords",{}),("get","campaigns",{"SelectionCriteria":{"Ids":[999]}})
        ]:
            with self.subTest(service=service,method=method):
                with self.assertRaisesRegex(RuntimeError,"outside exact approved"):
                    m.api(service,method,params)
    def test_update_preserves_every_setting_except_accessory(self):
        data=json.loads(BASE.read_text(encoding="utf-8"))
        self.assertEqual(data["status"],"complete")
        self.assertEqual(len(data["autotargeting"]),6)
        t=next(x for x in data["autotargeting"] if x["Id"]==m.TARGET)
        old=json.loads(json.dumps(t["AutotargetingSettings"]))
        p,new=m.plan_update(t)
        self.assertEqual(len(p["Keywords"]),1)
        self.assertEqual(p["Keywords"][0]["Id"],m.TARGET)
        self.assertEqual(set(p["Keywords"][0]),{"Id","AutotargetingSettings"})
        self.assertEqual(new["Categories"]["Accessory"],"NO")
        self.assertEqual(old["Categories"]["Accessory"],"YES")
        for k,v in old["Categories"].items():
            if k!="Accessory":self.assertEqual(v,new["Categories"][k])
        self.assertEqual(old["BrandOptions"],new["BrandOptions"])
        self.assertEqual(old["Categories"]["Accessory"],"YES")
if __name__=="__main__":unittest.main()
