import unittest
from scripts.ozon_description_recovery_20261124 import normalized_candidate,protected_changes
class OzonRecoveryTests(unittest.TestCase):
    def row(self):
        return {'offer_id':'A','id':1,'model_info':{'model_id':3,'count':1},'attributes':[
            {'id':9048,'values':[{'value':'model_A'}]},{'id':23173,'values':[{'value':'1'}]},
            {'id':4191,'values':[{'value':'old'}]}]}
    def test_candidate_requires_existing_kiz_and_singleton(self):
        r=self.row();self.assertIsNone(normalized_candidate(r,{'model_A':1}))
        r['model_info']['count']=2;self.assertIsNotNone(normalized_candidate(r,{'model_A':1}))
        r['model_info']['count']=1;r['attributes'].pop(1);self.assertIsNotNone(normalized_candidate(r,{'model_A':1}))
    def test_protected_model_id_and_text_never_exempted(self):
        import copy
        a=self.row();b=copy.deepcopy(a)
        b['attributes'][-1]['values'][0]['value']='new'
        self.assertEqual(protected_changes(a,b,{},{}),[])
        b['attributes'][0]['values'][0]['value']='model_A_A'
        b['model_info']['model_id']=4
        self.assertIn('card:model_info',protected_changes(a,b,{},{}))
        self.assertIn('attribute:9048:value_or_metadata_changed',protected_changes(a,b,{},{}))
