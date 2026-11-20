"""Tests for exact two-phrase Direct campaign update safety. No credentials needed."""
import importlib.util
import pathlib
import unittest

SCRIPT=pathlib.Path(__file__).resolve().parents[1] / ".github/scripts/direct_two_exact_negatives_20261120.py"
spec=importlib.util.spec_from_file_location("two_exact",SCRIPT)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class ExactNegativeSafetyTests(unittest.TestCase):
    def test_script_compiles(self):
        compile(SCRIPT.read_text(encoding="utf-8"),str(SCRIPT),"exec")

    def test_exact_scope_only(self):
        self.assertEqual(module.CID,716316525)
        self.assertEqual(module.TERMS,('"02e мехатроник в сборе"','"ремонт дсг вологда"'))
        self.assertTrue(all(x.startswith('"') and x.endswith('"') for x in module.TERMS))
        self.assertNotIn('"ремонт"',module.TERMS)
        self.assertNotIn('"плата"',module.TERMS)

    def test_reject_out_of_scope_operations_before_network(self):
        module.ALLOWED_WRITE=None
        module.WRITE_CALLED=False
        for service,method,params in (
            ("campaigns","update",{"Campaigns":[{"Id":716316525}]}),
            ("keywords","update",{"Keywords":[]}),
            ("campaigns","add",{}),
            ("accounts","get",{}),
        ):
            with self.subTest(service=service,method=method):
                with self.assertRaisesRegex(RuntimeError,"outside allowed"):
                    module.request(service,method,params)

    def test_preflight_has_exact_two_new_candidates(self):
        p=pathlib.Path(__file__).resolve().parents[1] / "data/direct_optimization_20261120/preflight.json"
        import json
        d=json.loads(p.read_text(encoding="utf-8"))
        self.assertEqual(d["campaign_id"],module.CID)
        self.assertEqual(d["negative_count"],130)
        self.assertEqual(d["candidates"],list(module.TERMS))
        self.assertEqual(d["status"],"preflight_verified_safe_to_propose")

    def test_fingerprint_is_order_sensitive_and_deterministic(self):
        self.assertEqual(module.fingerprint(["a","b"]),module.fingerprint(["a","b"]))
        self.assertNotEqual(module.fingerprint(["a","b"]),module.fingerprint(["b","a"]))

if __name__=="__main__":
    unittest.main()
