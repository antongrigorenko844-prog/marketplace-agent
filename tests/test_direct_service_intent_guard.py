"""Offline tests: campaign-wide repair service exclusions cannot block product demand."""
import importlib.util
from pathlib import Path
from unittest import TestCase

p=Path(__file__).resolve().parents[1]/".github/scripts/direct_service_intent_guard.py"
spec=importlib.util.spec_from_file_location("direct_service_guard",p)
g=importlib.util.module_from_spec(spec)
spec.loader.exec_module(g)

class DirectServiceIntentTests(TestCase):
    def test_exact_list_is_fixed_word_count(self):
        self.assertTrue(g.quoted_match('"ремонт дсг 7 фольксваген"',"ремонт дсг 7 фольксваген"))
        self.assertFalse(g.quoted_match('"ремонт дсг 7 фольксваген"',"запчасти для ремонта дсг 7 фольксваген"))
    def test_protection_of_product_queries(self):
        g.protect()
        self.assertNotIn("ремонт",g.ADDITIONS)
        self.assertTrue(all("запчасти" not in n for n in g.ADDITIONS))
        self.assertTrue(all(not any(g.quoted_match(n,query) for query in g.PROTECTED)
                            for n in g.ADDITIONS))
    def test_group_exclusions_reviewed_but_not_extended(self):
        groups=[
          {"Id":1,"Name":"без платы","NegativeKeywords":{"Items":["запчасти","автозапчасти"]}},
          {"Id":2,"Name":"платы","NegativeKeywords":{"Items":["запчасти"]}}
        ]
        r=g.risks(['"ремонт дсг 7"','сцепление'],groups,[])
        self.assertEqual(r["group_negative_total"],3)
        self.assertEqual(r["groups"][0]["possible_parts_buyer_blockers"],["запчасти","автозапчасти"])
        self.assertEqual(r["groups"][1]["negative_count"],1)
        self.assertEqual(r["full_campaign_negative_list"],['"ремонт дсг 7"','сцепление'])
    def test_manual_keyword_collision_not_mutated(self):
        kw=[{"Id":1,"Keyword":"ремонт дсг 7 фольксваген","AdGroupId":12}]
        r=g.risks([],[],kw)
        self.assertIn('"ремонт дсг 7 фольксваген"',r["possible_manual_keyword_collisions"])
    def test_write_not_possible_without_explicit_payload(self):
        g.ALLOWED_PAYLOAD=None
        with self.assertRaisesRegex(RuntimeError,"outside safe campaign-only scope"):
            g.api("campaigns","update",{"Campaigns":[]})
