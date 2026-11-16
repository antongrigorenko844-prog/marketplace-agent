import unittest
from dataclasses import replace
from decimal import Decimal
from .delivery_core import DemoProvider
from .checkout_contract import (make_demo_selection,validate_server_quote,
    production_checkout_payload,create_live_order,CheckoutError)

class CheckoutContractTests(unittest.TestCase):
    def setUp(self):
        self.p=DemoProvider()
        self.point=self.p.list_points("Москва")[0]
        self.quote=self.p.quote(self.point.id,500)
    def test_demo_selection_never_enables_checkout(self):
        selection=make_demo_selection(self.point,self.quote)
        self.assertEqual(selection.carrier,"ozon")
        self.assertFalse(selection.checkout_enabled)
        self.assertEqual(selection.quote_source,"synthetic_demo")
    def test_quote_mismatch_rejected(self):
        with self.assertRaises(CheckoutError):
            make_demo_selection(self.point,replace(self.quote,point_id="other"))
    def test_negative_price_rejected(self):
        with self.assertRaises(CheckoutError):
            validate_server_quote(self.point.id,replace(self.quote,amount_rub=Decimal("-1")))
    def test_bad_eta_rejected(self):
        with self.assertRaises(CheckoutError):
            validate_server_quote(self.point.id,replace(self.quote,eta_min_days=8,eta_max_days=3))
    def test_production_disabled(self):
        with self.assertRaises(RuntimeError): production_checkout_payload()
        with self.assertRaises(RuntimeError): create_live_order()
if __name__=="__main__": unittest.main()
