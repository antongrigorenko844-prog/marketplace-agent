import concurrent.futures
from io import BytesIO
import json
import unittest
from urllib.error import HTTPError
from urllib.parse import parse_qs,urlsplit
from unittest.mock import patch

import avito_stock_client as stock


SKU = "0am325066ae"
ID = stock.LISTINGS[SKU]
OTHER = "plita-filtr-orig"


class FakeAPI:
    def __init__(self):
        self.calls=[]
        self.links=[{"ad_id":s,"avito_id":i} for s,i in stock.LISTINGS.items()]
        self.detail={"status":"active","autoload_item_id":SKU}
        self.info={"stocks":[{"item_id":ID,"quantity":8,"is_multiple":True,
                              "is_unlimited":False,"is_out_of_stock":False}]}
        self.ack={"stocks":[{"item_id":ID,"success":True,"errors":[]}]}

    def open(self,req,timeout):
        parts=urlsplit(req.full_url)
        assert (parts.scheme,parts.netloc,timeout)==("https","api.avito.ru",20)
        method,path=req.get_method(),parts.path
        body=None
        if req.data:
            body=parse_qs(req.data.decode()) if path=="/token" else json.loads(req.data)
        self.calls.append((method,path,body))
        if path=="/token":
            assert method=="POST" and req.get_header("Authorization") is None
            result={"access_token":"offline-token"}
        else:
            assert req.get_header("Authorization")=="Bearer offline-token"
            if (method,path)==("GET","/core/v1/accounts/self"):
                result={"id":123}
            elif (method,path)==("GET","/autoload/v2/items/avito_ids"):
                assert set(parse_qs(parts.query)["query"][0].split(","))=={
                    k for s in stock.LISTINGS for k in (s,s+"-av")}
                result={"items":self.links}
            elif method=="GET" and path==f"/core/v1/accounts/123/items/{ID}/":
                result=self.detail
            elif (method,path)==("POST","/stock-management/1/info"):
                assert body=={"item_ids":[ID],"strong_consistency":True}
                result=self.info
            elif (method,path)==("PUT","/stock-management/1/stocks"):
                result=self.ack
            else:
                raise AssertionError("unexpected request")
        if isinstance(result,Exception):
            raise result
        fp=BytesIO(json.dumps(result).encode())
        fp.status=200
        return fp

    def writes(self):
        return [c for c in self.calls if c[0]=="PUT"]


class StockClientTests(unittest.TestCase):
    def setUp(self):
        self.api=FakeAPI()
        self.addCleanup(patch.stopall)
        patch.object(stock,"_SENT_IDS",set()).start()
        patch.object(stock,"build_opener",return_value=self.api).start()
        patch.dict(stock.os.environ,{"AVITO_CLIENT_ID":"demo","AVITO_CLIENT_SECRET":"demo"}).start()
        self.client=stock.StockClient()

    def test_allowlist_has_six_distinct_historical_items(self):
        self.assertEqual((len(stock.LISTINGS),len(set(stock.LISTINGS.values()))),(6,6))
        self.assertEqual(stock.LISTINGS["0am325025x"],8252041186)
        self.assertEqual(self.client.link_issues(),{})

    def test_alias_duplicate_collision_and_missing(self):
        self.api.links=[r for r in self.api.links if r["ad_id"]!=SKU]
        self.assertEqual(self.client.link_issues()[SKU],"historical_link_changed")
        self.api.links.extend([{"ad_id":SKU,"avito_id":ID}]*2)
        self.assertEqual(self.client.link_issues()[SKU],"duplicate_autoload_row")
        self.api.links.append({"ad_id":OTHER+"-av","avito_id":ID})
        issues=self.client.link_issues()
        self.assertEqual(set(issues),{SKU,OTHER})
        self.assertEqual(issues[SKU],"listing_linked_to_another_sku")
        with self.assertRaises(stock.StockError):
            self.client.set_quantity(SKU,20)
        self.assertFalse(self.api.writes())

    def test_requires_active_and_noncontradictory_reverse(self):
        for detail in ({"status":"active"}, {"status":"active","autoload_item_id":None},
                       {"status":"active","autoload_item_id":""}):
            self.api.detail=detail
            self.assertIsNone(self.client.verify_listing(SKU))
        for detail in ({"status":"old"}, {"status":"active","autoload_item_id":OTHER}):
            self.api.detail=detail
            with self.assertRaises(stock.StockError):
                self.client.set_quantity(SKU,3)
        self.assertFalse(self.api.writes())

    def test_read_free_unlimited_and_inconsistent(self):
        row=self.api.info["stocks"][0]
        self.assertEqual(self.client.read(SKU),8)
        row.update(is_multiple=False,quantity=0,is_out_of_stock=True)
        self.assertEqual(self.client.read(SKU),0)
        row.update(is_unlimited=True,quantity=-1)
        self.assertIsNone(self.client.read(SKU))
        row.update(is_unlimited=False,quantity=1)
        with self.assertRaisesRegex(stock.StockError,"inconsistent_stock"):
            self.client.read(SKU)
        row.update(is_out_of_stock=False,is_unlimited=1)
        with self.assertRaisesRegex(stock.StockError,"stock_flags_unconfirmed"):
            self.client.read(SKU)

    def test_unknown_listings_invalid_quantities_never_sent(self):
        n=len(self.api.calls)
        for value in (-1,1000000,True,2.0,"22",None):
            with self.assertRaisesRegex(stock.StockError,"invalid_quantity"):
                self.client.set_quantity(SKU,value)
        for func in (self.client.verify_listing,self.client.read,lambda k:self.client.set_quantity(k,4)):
            with self.assertRaisesRegex(stock.StockError,"unknown_listing"):
                func("new-listing")
        self.assertEqual(len(self.api.calls),n)

    def test_dynamic_quantity_and_sanitized_result(self):
        self.api.ack["stocks"][0]["external_id"]="private"
        self.assertEqual(self.client.set_quantity(SKU,37),{"item_id":ID,"success":True})
        self.assertEqual(self.api.writes(),[("PUT","/stock-management/1/stocks",
                                             {"stocks":[{"item_id":ID,"quantity":37}]})])
        with self.assertRaisesRegex(stock.StockError,"duplicate_write_prevented"):
            stock.StockClient().set_quantity(SKU,99)
        self.assertEqual(len(self.api.writes()),1)

    def test_partial_or_wrong_receipt_does_not_leak_response(self):
        for reply in ({"stocks":[]},{"stocks":[{"item_id":ID,"success":False,"errors":["sensitive"]}]},
                      {"stocks":[{"item_id":ID,"success":True,"errors":["sensitive"]}]},
                      {"stocks":[{"item_id":ID+1,"success":True}]},
                      {"stocks":[{"item_id":ID,"success":True}]*2}):
            with patch.object(stock,"_SENT_IDS",set()):
                self.api.ack=reply
                with self.assertRaises(stock.StockError) as got:
                    self.client.set_quantity(SKU,4)
                self.assertIn(str(got.exception),("item_rejected","receipt_identity_unconfirmed"))

    def test_transport_uncertainty_never_retries_and_can_read(self):
        self.api.ack=TimeoutError("sensitive response")
        with self.assertRaisesRegex(stock.StockError,"^transport_error$"):
            self.client.set_quantity(SKU,4)
        self.assertEqual(self.client.read(SKU),8)
        with self.assertRaisesRegex(stock.StockError,"duplicate_write_prevented"):
            self.client.set_quantity(SKU,4)
        self.assertEqual(len(self.api.writes()),1)

    def test_threaded_clients_share_the_process_write_guard(self):
        def send(_):
            try:
                stock.StockClient().set_quantity(SKU,0)
                return "ok"
            except stock.StockError as exc:
                return exc.code
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            result=list(pool.map(send,range(4)))
        self.assertEqual(result.count("ok"),1)
        self.assertEqual(result.count("duplicate_write_prevented"),3)
        self.assertEqual(len(self.api.writes()),1)

    def test_no_redirect_and_no_raw_errors(self):
        self.assertIsNone(stock._NoRedirect().redirect_request(None,None,307,"","",None))
        self.api.ack=HTTPError("https://api.avito.ru",403,"sensitive",{},BytesIO(b"sensitive"))
        with self.assertRaisesRegex(stock.StockError,"^http_403$"):
            self.client.set_quantity(SKU,1)
        self.assertEqual(str(stock.StockError("sensitive")),"stock_error")


if __name__=="__main__":
    unittest.main()
