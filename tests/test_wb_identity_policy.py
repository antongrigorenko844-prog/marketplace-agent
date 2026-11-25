import unittest
from wb_identity_policy import guard_write, is_held, HELD, ACTIVE_BOARD, ACTIVE_GASKET


class TestWbIdentityPolicy(unittest.TestCase):
    def test_exact_identities(self):
        for sku, row in HELD.items():
            self.assertTrue(is_held(sku))
            self.assertTrue(is_held(nm_id=row["nmID"]))
            for key, value in (("sku", row["barcode"]), ("chrtId", row["chrtID"])):
                guard_write("PUT", "/api/v3/stocks/1627258", {"stocks": [{key: value, "amount": 0}]})
                with self.assertRaises(ValueError):
                    guard_write("PUT", "/api/v3/stocks/1627258", {"stocks": [{key: value, "amount": 5}]})
            with self.assertRaises(ValueError):
                guard_write("POST", "/content/v2/cards/update", [{"vendorCode": sku, "nmID": row["nmID"]}])
            with self.assertRaises(ValueError):
                guard_write("POST", "/content/v3/media/save", {"nmId": row["nmID"], "data": []})
        for row in (ACTIVE_BOARD, ACTIVE_GASKET):
            self.assertFalse(is_held(row["vendorCode"], row["nmID"]))
            guard_write("POST", "/content/v2/cards/update", [row])

    def test_unrelated_stock_is_allowed(self):
        guard_write("PUT", "/api/v3/stocks/1627258", {"stocks": [{"chrtId": 3370182126, "amount": 7}]})
