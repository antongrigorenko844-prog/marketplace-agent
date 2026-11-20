import contextlib
import io
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

import openpyxl

import catalog_editor
import main
import ozon_client
import wb_client
import yandex_market_client as market


class ScopedPricesTests(unittest.TestCase):
    def setUp(self):
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
        self.stack.enter_context(patch("socket.socket.connect", side_effect=AssertionError("offline test")))

    def mock(self, module, name, **kwargs):
        return self.stack.enter_context(patch.object(module, name, **kwargs))

    def cli(self, command, article=""):
        with patch.object(sys, "argv", ["main.py", "--" + command, "--article", article]):
            return main.main()

    def wb_inputs(self):
        cards = [{"vendorCode": sku, "nmID": nm} for nm, sku in enumerate(["Part-A", "part-a"], 1)]
        prices = [{"nmID": nm, "discount": 10, "sizes": [{"price": 400, "discountedPrice": 360}]} for nm in (1, 2)]
        edits = {c["vendorCode"]: {"price": 270} for c in cards}
        self.mock(main, "_load_wb_push_inputs", return_value=({"cards": cards, "prices": prices}, edits))
        self.mock(main.time, "sleep")
        return edits

    def test_wb_cli_scopes_preview_and_upload_with_delayed_history(self):
        self.wb_inputs()
        write = self.mock(wb_client, "update_prices", return_value={"data": {"id": 71}})
        history = self.mock(wb_client, "get_price_upload_result", side_effect=[
            {"task": {}, "goods": []},
            {"task": {"uploadID": 71, "status": 3, "successGoodsNumber": 1, "overAllGoodsNumber": 1},
             "goods": [{"nmID": 2, "price": 300, "discount": 10, "errorText": ""}]},
        ])
        self.assertEqual(self.cli("push-wb-price-dryrun", "part-a"), 0)
        write.assert_not_called()
        self.assertEqual(self.cli("push-wb-price", "part-a"), 0)
        write.assert_called_once_with([{"nmID": 2, "price": 300, "discount": 10}])
        self.assertEqual(history.call_count, 2)

    def test_wb_noop_invalid_scope_or_invalid_target(self):
        edits = self.wb_inputs()
        write = self.mock(wb_client, "update_prices")
        for article in ("PART-A", "Part-A,unknown", " , ", "Part-A,"):
            with self.subTest(article=article):
                self.assertEqual(self.cli("push-wb-price", article), 1)
        edits["Part-A"]["price"] = 360
        self.assertEqual(self.cli("push-wb-price", "Part-A"), 0)
        for value in ("wrong", 0, None):
            edits["Part-A"]["price"] = value
            self.assertEqual(self.cli("push-wb-price", "Part-A"), 1)
        write.assert_not_called()

    def test_wb_partial_quarantined_and_unknown_outcomes_fail_without_resubmit(self):
        self.wb_inputs()
        write = self.mock(wb_client, "update_prices", return_value={"data": {"id": 71}})
        history = self.mock(wb_client, "get_price_upload_result")
        for status, rows in ((1, []), (4, []), (5, [{"nmID": 1, "errorText": "quarantine"}]),
                             (3, [{"nmID": 1}]), (3, [{"nmID": 1}, {"nmID": 9}]),
                             (3, [{"nmID": 1, "price": 20, "discount": 10},
                                  {"nmID": 2, "price": 30, "discount": 10}])):
            with self.subTest(status=status, rows=rows):
                history.return_value = {"task": {"uploadID": 71, "status": status, "successGoodsNumber": 2,
                                                 "overAllGoodsNumber": 2}, "goods": rows}
                write.reset_mock()
                self.assertEqual(self.cli("push-wb-price"), 1)
                self.assertEqual([r["nmID"] for r in write.call_args.args[0]], [1, 2])
                self.assertEqual(write.call_count, 1)
        write.return_value = {}
        history.reset_mock()
        self.assertEqual(self.cli("push-wb-price"), 1)
        history.assert_not_called()

    def market_inputs(self):
        directory = self.stack.enter_context(tempfile.TemporaryDirectory())
        path = Path(directory, "catalog.xlsx")
        workbook = openpyxl.Workbook()
        for row in (("SKU", "", "", "Цена", "Старая"), ("Part-A", "", "", 240, 300),
                    ("part-a", "", "", 360, None), ("Bad", "", "", -1, None)):
            workbook.active.append(row)
        workbook.save(path)
        self.mock(main, "_data_path", return_value=str(path))
        self.mock(market, "is_configured", return_value=True)
        self.mock(market, "_creds", return_value=("test", 1))
        return self.mock(market, "_request", return_value=(200, {"status": "OK"}))

    def test_market_exact_scope_invalid_input_and_bulk(self):
        send = self.market_inputs()
        self.assertEqual(self.cli("push-yandex-price-dryrun", "part-a"), 0)
        for sku in ("PART-A", "Part-A,Bad", " ", ","):
            self.assertEqual(self.cli("push-yandex-price", sku), 1)
        send.assert_not_called()
        self.assertEqual(self.cli("push-yandex-price", "part-a"), 0)
        self.assertEqual(send.call_args.kwargs["json_body"]["offers"], [
            {"offerId": "part-a", "price": {"value": 360, "currencyId": "RUR"}},
        ])
        self.assertEqual(self.cli("push-yandex-price"), 0)
        self.assertEqual([v["offerId"] for v in send.call_args.kwargs["json_body"]["offers"]], ["Part-A", "part-a"])

    def test_market_ambiguous_ack_and_partial_success(self):
        send = self.market_inputs()
        for body in ({}, {"status": "ERROR"}, {"status": "OK", "errors": [{"code": "ERR"}]}):
            with self.subTest(body=body):
                send.return_value = (200, body)
                send.reset_mock()
                self.assertEqual(self.cli("push-yandex-price", "Part-A"), 1)
                self.assertEqual(send.call_count, 1)
        send.side_effect = [(400, {}), (200, {"status": "OK"}), (400, {})]
        self.assertEqual(self.cli("push-yandex-price"), 1)

    def ozon_inputs(self):
        self.mock(main, "_data_path", return_value="mock-catalog")
        self.mock(catalog_editor, "load_catalog_edits", return_value={
            "part-a": {"price": 250}, "Part-A": {"price": 450},
        })
        self.mock(catalog_editor, "_load_overrides", return_value={})
        read = self.mock(ozon_client, "get_prices_and_stocks", side_effect=lambda ids: [
            {"offer_id": sku, "price": {"price": "180", "old_price": "0"}} for sku in ids])
        return read, self.mock(ozon_client, "update_prices")

    def test_ozon_requires_explicit_ack_for_each_exact_sku(self):
        read, write = self.ozon_inputs()
        ok = {"offer_id": "part-a", "updated": True, "errors": []}
        for response in ({}, {"result": []}, {"result": [ok, ok]}, {"result": [dict(ok, offer_id="Part-A")]},
                         {"result": [{"offer_id": "part-a"}]}, {"result": [dict(ok, errors=["rejected"])]}):
            with self.subTest(response=response):
                write.return_value = response
                self.assertEqual(self.cli("sync-ozon-prices", "part-a"), 1)
        read.assert_called_with(["part-a"])
        write.return_value = {"result": [ok]}
        self.assertEqual(self.cli("sync-ozon-prices", "part-a"), 0)
        write.reset_mock()
        self.assertEqual(self.cli("sync-ozon-prices-dryrun", "Part-A"), 0)
        for article in (",", "not-found", "Part-A,not-found"):
            self.assertEqual(self.cli("sync-ozon-prices", article), 1)
        read.side_effect = lambda ids: []
        self.assertEqual(self.cli("sync-ozon-prices", "Part-A"), 1)
        read.side_effect = lambda ids: [{"offer_id": "Part-A", "price": {}}]
        self.assertEqual(self.cli("sync-ozon-prices", "Part-A"), 1)
        write.assert_not_called()
