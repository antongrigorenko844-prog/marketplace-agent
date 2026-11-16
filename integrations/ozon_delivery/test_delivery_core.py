import unittest
from decimal import Decimal
from .delivery_core import DemoProvider, OzonLiveProvider, IntegrationNotReady, validate_order_reference

class DeliveryTests(unittest.TestCase):
    def setUp(self): self.provider=DemoProvider()
    def test_city_filter(self):
        self.assertEqual(len(self.provider.list_points("Москва")),2)
        self.assertEqual(len(self.provider.list_points("Пермь")),0)
    def test_demo_quote_is_labeled(self):
        q=self.provider.quote("demo-msk-1",500)
        self.assertEqual(q.amount_rub,Decimal("199.00"))
        self.assertEqual(q.source,"synthetic_demo")
    def test_invalid_point_rejected(self):
        with self.assertRaises(ValueError): self.provider.quote("unknown",500)
    def test_invalid_weight_rejected(self):
        for w in (0,-1,30001,True,"500"):
            with self.subTest(w=w),self.assertRaises(ValueError):
                self.provider.quote("demo-msk-1",w)
    def test_shipment_disabled(self):
        with self.assertRaises(IntegrationNotReady):
            self.provider.create_shipment("order1","demo-msk-1")
    def test_live_fails_closed(self):
        p=OzonLiveProvider()
        with self.assertRaises(IntegrationNotReady): p.list_points("Москва")
        with self.assertRaises(IntegrationNotReady): p.quote("a",500)
        with self.assertRaises(IntegrationNotReady): p.create_shipment("o","p")
    def test_order_ref(self):
        self.assertEqual(validate_order_reference("LP-123"),"LP-123")
        with self.assertRaises(ValueError): validate_order_reference("../bad")
if __name__=="__main__":unittest.main()
