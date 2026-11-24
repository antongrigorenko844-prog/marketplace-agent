import copy
import unittest
import ozon_description_guard as g
import content_pipeline as p

class OzonGuardTests(unittest.TestCase):
    def setUp(self):
        self.a={'offer_id':'A','id':1,'sku':2,'model_info':{'model_id':3,'count':2},
                'name':'part','images':['image'],'attributes_with_defaults':[23173,99],
                'attributes':[{'id':9048,'complex_id':0,'values':[{'value':'model_A_A','dictionary_value_id':0}]},
                              {'id':4191,'values':[{'value':'old'}]},{'id':22,'values':[{'value':'protected'}]}]}
        self.price={'offer_id':'A','price':{'price':'100','old_price':'200','min_price':'50'}}
        self.b=copy.deepcopy(self.a)
        self.b['attributes'][1]['values'][0]['value']='new'
        self.b['attributes'].append({'id':23173,'complex_id':0,'values':[{'value':'1','dictionary_value_id':0}]})
        self.b['attributes_with_defaults']=[]
    def test_observed_default_materialization_and_redundant_suffix(self):
        self.b['attributes'][0]['values'][0]['value']='model_A_A_A'
        self.assertEqual(p.protected_hash('ozon',self.a,self.price),p.protected_hash('ozon',self.b,self.price))
        r=g.verify(self.a,self.b,self.price,self.price)
        self.assertTrue(r['kiz_materialized'])
        self.assertTrue(r['redundant_model_suffix_changed'])
    def test_actual_group_changes_and_arbitrary_model_text_are_forbidden(self):
        for field,val in [('model_id',4),('count',1)]:
            z=copy.deepcopy(self.b);z['model_info'][field]=val
            with self.assertRaises(ValueError):g.verify(self.a,z,self.price,self.price)
        z=copy.deepcopy(self.b);z['attributes'][0]['values'][0]['value']='different'
        with self.assertRaises(ValueError):g.verify(self.a,z,self.price,self.price)
    def test_first_suffix_and_unreviewed_kiz_are_forbidden(self):
        z=copy.deepcopy(self.a);z['attributes'][0]['values'][0]['value']='model'
        with self.assertRaises(ValueError):g.verify(z,self.b,self.price,self.price)
        z=copy.deepcopy(self.b);z['attributes'][-1]['values'][0]['value']='2'
        with self.assertRaises(ValueError):g.verify(self.a,z,self.price,self.price)
        z=copy.deepcopy(self.a);z['attributes_with_defaults']=[]
        with self.assertRaises(ValueError):g.verify(z,self.b,self.price,self.price)
    def test_price_image_id_and_actual_attribute_changes_forbidden(self):
        for field,val in [('sku',55),('name','changed'),('images',['new'])]:
            z=copy.deepcopy(self.b);z[field]=val
            with self.assertRaises(ValueError):g.verify(self.a,z,self.price,self.price)
        price=copy.deepcopy(self.price);price['price']['price']='200'
        with self.assertRaises(ValueError):g.verify(self.a,self.b,self.price,price)
        z=copy.deepcopy(self.b);z['attributes'][2]['values'][0]['value']='changed'
        with self.assertRaises(ValueError):g.verify(self.a,z,self.price,self.price)
