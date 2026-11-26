import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import price_audit
from scripts.ozon_historical_finance import (
    ALLOWLIST, DATES, FOCUS, LOOKUP_SKU, ROOT, HistoricalReader, collect_history,
)
from scripts.ozon_profit_snapshot import ReadOnlyOzonClient, SnapshotError


class Response:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.headers = payload, status, {}

    def json(self):
        return copy.deepcopy(self.payload)

    @property
    def text(self):
        raise AssertionError("Raw error bodies must not be read")


class HistoricalSession:
    def __init__(self, lookup_available):
        self.lookup_available, self.calls = lookup_available, []
        self.products = [{"offer_id": oid, "product_id": int(pid), "archived": False}
                         for oid, pid in FOCUS.items()]
        self.info = [{"offer_id": oid, "id": int(pid), "sku": 1000 + n,
                      "sources": [{"sku": 1000 + n}]}
                     for n, (oid, pid) in enumerate(FOCUS.items())]

    @staticmethod
    def posting(sku, price, commission, delivery, quantity=1):
        return {"accrued_category": "POSTING", "total_amount": str(price + commission + delivery),
                "operation_id": "private-operation-must-not-appear",
                "posting": {"posting_number": "private-posting-must-not-appear",
                            "buyer": "private-buyer-must-not-appear",
                            "products": [{"sku": sku, "quantity": quantity,
                                          "commission": {"sale_amount": str(abs(price)),
                                                         "sale_commission": str(commission)},
                                          "delivery": {"total_accrued": str(delivery)}}]}}

    def request(self, method, url, **kwargs):
        path = url.removeprefix("https://api-seller.ozon.ru")
        body = kwargs["json"]
        self.calls.append((method, path, copy.deepcopy(body)))
        if (method, path) not in ALLOWLIST:
            raise AssertionError("Unexpected endpoint reached transport")
        if path == "/v3/product/list":
            if body.get("last_id"):
                return Response({"result": {"items": self.products[3:], "total": 5, "last_id": ""}})
            return Response({"result": {"items": self.products[:3], "total": 5, "last_id": "catalog-next"}})
        if path == "/v3/product/info/list":
            if body["sku"]:
                if body != {"offer_id": [], "product_id": [], "sku": [LOOKUP_SKU]}:
                    raise AssertionError("Unexpected direct lookup")
                if not self.lookup_available:
                    return Response({"message": "private-api-error-must-not-appear"}, 400)
                return Response({"items": [{"offer_id": "archived-exact", "id": 9001,
                                             "sources": [{"sku": int(LOOKUP_SKU)}]}]})
            return Response({"items": [row for row in self.info if row["offer_id"] in body["offer_id"]]})
        if path == "/v1/finance/accrual/types":
            return Response({"accrual_types": [
                {"id": 1, "description": "Продвижение бренда"},
                {"id": 2, "description": "Сбор отзывов"},
                {"id": 3, "description": "Эквайринг"},
                {"id": 4, "description": "Подписка"},
            ]})
        day = body["date"]
        if day == DATES[0]:
            if body.get("last_id"):
                return Response({"accruals": [
                    {"accrued_category": "ITEM", "total_amount": "-6",
                     "item_fees": {"fees": [{"sku": 1000, "quantity": 2, "fees": [
                         {"type_id": 1, "accrued": "-2"}, {"type_id": 2, "accrued": "-3"},
                         {"type_id": 3, "accrued": "-1"}]}]}},
                    self.posting(int(LOOKUP_SKU), 100, -20, -10),
                    {"accrued_category": "NON_ITEM", "total_amount": "-7", "non_item_fee": {"type_id": 4}},
                ], "last_id": "", "total": 8})
            return Response({"accruals": [self.posting(1000, 200, -40, -20, 2)]
                             + [self.posting(1000 + n, 1000, -200, -50) for n in range(1, 5)],
                             "last_id": "finance-next", "total": 8})
        if day == "2026-10-01":
            return Response({"accruals": [self.posting(1000, -100, 20, -5)], "last_id": ""})
        return Response({"accruals": [], "last_id": ""})


class HistoricalFinanceTests(unittest.TestCase):
    def test_fixed_window_pagination_exact_mapping_financial_reconciliation_and_isolation(self):
        with tempfile.TemporaryDirectory() as temp:
            normal = Path(temp, "normal")
            normal.mkdir()
            current_snapshot = normal / "ozon_finance.json"
            current_snapshot.write_text('{"normal_30_day_snapshot":"keep"}', encoding="utf-8")
            original = current_snapshot.read_bytes()
            with patch.object(price_audit, "DATA", str(normal)), \
                    patch.object(price_audit, "FIN_PATH", str(current_snapshot)), patch("time.sleep"):
                for lookup_available in (True, False):
                    with self.subTest(lookup_available=lookup_available):
                        session = HistoricalSession(lookup_available)
                        client = ReadOnlyOzonClient("fake-client", "fake-key", session=session)
                        output = Path(temp, f"history-{lookup_available}")
                        manifest = collect_history(client, output)
                        self.assertEqual(manifest["status"], "complete_read_only" if lookup_available else "partial")
                        self.assertEqual(manifest["seller_writes"], 0)
                        finance = json.loads((output / "finance.json").read_text())
                        details = json.loads((output / "financial_fields_by_day.json").read_text())
                        self.assertEqual(finance["window"]["from"], "2026-09-25")
                        self.assertEqual(finance["window"]["to"], "2026-10-23")
                        self.assertEqual(finance["coverage"]["collected_dates"], 29)
                        self.assertEqual(finance["daily_coverage"][0]["pages"], 2)
                        self.assertEqual([row["date"] for row in details["days"]], list(DATES))
                        self.assertEqual(finance["operations"], 9)
                        item = finance["items"][next(iter(FOCUS))]
                        self.assertEqual(item, {"sold": 2, "returned": 1, "revenue": 100.0,
                                                "commission": -20.0, "logistics": -25.0,
                                                "services": -6.0, "net": 49.0})
                        self.assertEqual(finance["fees_by_type"][next(iter(FOCUS))],
                                         {"Продвижение бренда": -2.0, "Сбор отзывов": -3.0, "Эквайринг": -1.0})
                        self.assertEqual(finance["payout_total"], 3112.0)
                        self.assertEqual(sum(row["net"] for row in finance["items"].values())
                                         + sum(finance["unassigned"].values()), finance["payout_total"])
                        first_sale = details["days"][0]["accruals"][0]["posting"]["products"][0]
                        returned = next(row for row in details["days"] if row["date"] == "2026-10-01")
                        # Gross sale base 200 and return base 100 remain separately reviewable.
                        self.assertEqual(first_sale["commission"]["sale_amount"], "200")
                        self.assertEqual(returned["accruals"][0]["posting"]["products"][0]
                                         ["commission"]["sale_amount"], "100")
                        self.assertTrue(all(row["status"] == "ready_for_reconciliation"
                                            and row["reconciliation_status"] == "pending_review"
                                            and not row["service_rates_verified"]
                                            for row in manifest["focus"].values()))
                        resolution = manifest["direct_lookup"]["resolved"]
                        if lookup_available:
                            self.assertEqual(resolution, {"marketplace_sku": LOOKUP_SKU,
                                                          "offer_id": "archived-exact", "product_id": "9001"})
                        else:
                            self.assertIsNone(resolution)
                            self.assertIn(f"sku:{LOOKUP_SKU}", finance["items"])
                        saved = "".join(path.read_text() for path in output.glob("*.json"))
                        self.assertNotIn("must-not-appear", saved)
                        self.assertNotIn("fake-key", saved)
                        self.assertEqual(current_snapshot.read_bytes(), original)
                        self.assertEqual(price_audit.DATA, str(normal))
                        self.assertEqual(price_audit.FIN_PATH, str(current_snapshot))
                        day_requests = [body["date"] for _, path, body in session.calls if path.endswith("/by-day")]
                        self.assertEqual(set(day_requests), set(DATES))
                        self.assertEqual(len(day_requests), 30)  # 29 dates plus the second page.
                        for method, path in [("POST", "/v1/product/import/prices"),
                                             ("POST", "/v5/product/info/prices"),
                                             ("GET", "/v3/product/list")]:
                            before = len(session.calls)
                            with self.assertRaises(SnapshotError):
                                HistoricalReader(client).request(method, path, {})
                            self.assertEqual(len(session.calls), before)
                        with self.assertRaises(SnapshotError):
                            HistoricalReader(client).post("/v1/finance/accrual/by-day", {"date": "2026-10-24"})
                        with self.assertRaises(SnapshotError):
                            collect_history(client, output)
                        with self.assertRaises(SnapshotError):
                            collect_history(client, ROOT / "data/should-not-exist-history")


if __name__ == "__main__":
    unittest.main()
