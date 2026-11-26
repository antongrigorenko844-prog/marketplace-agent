"""Regression coverage for anonymous Tilda page access monitoring."""
import unittest
from unittest.mock import patch
import requests
import direct_tools as d


def response(status, html, headers=None):
    r = requests.Response()
    r.status_code = status
    r._content = html.encode("utf-8")
    r.encoding = "utf-8"
    r.headers.update(headers or {})
    return r


class AccessTest(unittest.TestCase):
    def test_tilda_password_page_can_return_http_200(self):
        r = response(200, "<html><title>Authentication required</title></html>")
        with patch.object(d.requests, "get", return_value=r):
            self.assertEqual(d.check_page("https://leoparts-akpp.ru/catalog", []),
                             ["запрашивается авторизация/пароль"])

    def test_basic_auth_is_not_accessible(self):
        r = response(401, "Restricted", {"WWW-Authenticate": 'Basic realm="Test"'})
        self.assertTrue(d._page_auth_required(r))

    def test_public_store_login_field_is_not_site_lock(self):
        r = response(200, '<html><title>Каталог</title><div id="allrecords"><input type="password"></div></html>')
        self.assertFalse(d._page_auth_required(r))

    def test_password_gate_without_title(self):
        self.assertTrue(d._page_auth_required(response(200, '<html><input type="password"></html>')))

    def test_transient_402_retry(self):
        with patch.object(d.requests, "get", side_effect=[
            response(402, "402 Please renew your subscription."),
            response(200, '<html><title>Каталог</title><div id="allrecords"></div></html>')
        ]) as get, patch.object(d.time, "sleep"):
            self.assertEqual(d.check_page("https://leoparts-akpp.ru/catalog", []), [])
            self.assertEqual(get.call_count, 2)

    def test_mobile_checks_use_both_user_agents(self):
        page = response(200, '<html><title>Каталог</title><div id="allrecords"></div></html>')
        with patch.object(d.requests, "get", return_value=page) as get:
            results = d.check_core_mobile_pages()
        self.assertEqual(len(results), 6)
        self.assertTrue(all(not issues for issues in results.values()))
        self.assertEqual(sum("iPhone" in c.kwargs["headers"]["User-Agent"] for c in get.call_args_list), 3)
        self.assertEqual(sum("Android" in c.kwargs["headers"]["User-Agent"] for c in get.call_args_list), 3)


if __name__ == "__main__":
    unittest.main()
