import copy
import unittest
from unittest.mock import patch
import yandex_market_client as ym

class MarketNamesPreserveContent(unittest.TestCase):
    def test_existing_multi_value_characteristics_survive_name_update(self):
        params=[{'parameterId':1,'valueId':10},{'parameterId':1,'valueId':20},{'parameterId':2,'unitId':3,'value':'4'}]
        writes=[]
        def request(method,path,params=None,json_body=None):
            if path.endswith('/offer-cards'):
                return 200,{'status':'OK','result':{'offerCards':[{'offerId':'part','mapping':{'marketCategoryId':123},'parameterValues':copy.deepcopy(attributes)}]}}
            self.assertTrue(path.endswith('/offer-mappings/update'))
            writes.append(copy.deepcopy(json_body));return 200,{'status':'OK'}
        attributes=copy.deepcopy(params)
        with patch.object(ym,'get_business_id',return_value=99),patch.object(ym,'_request',side_effect=request):
            self.assertEqual(ym.update_names({'part':'Новое название'}),(1,{}))
        offer=writes[0]['offerMappings'][0]['offer']
        self.assertEqual(offer,{'offerId':'part','name':'Новое название','parameterValues':params,'marketCategoryId':123})
        self.assertEqual(attributes,params)

    def test_no_update_if_existing_card_cannot_be_read(self):
        calls=[]
        def request(method,path,**kwargs):
            calls.append(path);return 503,{'status':'ERROR'}
        with patch.object(ym,'get_business_id',return_value=99),patch.object(ym,'_request',side_effect=request):
            with self.assertRaises(AssertionError):ym.update_names({'part':'Новое название'})
        self.assertFalse(any(p.endswith('/update')for p in calls))

if __name__=='__main__':unittest.main()
