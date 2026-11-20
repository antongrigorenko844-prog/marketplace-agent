"""Offline regression tests for the campaign-scoped Direct Telegram summary."""
import unittest
from datetime import date
from unittest.mock import patch

import direct_tools


class DirectReportScopeTests(unittest.TestCase):
    def test_perf_queries_only_leoparts_campaign(self):
        with patch.object(direct_tools, "_report", return_value=[{
            "CampaignId": "716316525", "Impressions": "100",
            "Clicks": "10", "Cost": "280.50", "Conversions": "2",
        }]) as fetch:
            result = direct_tools._perf("stub", date(2026, 11, 19),
                                       date(2026, 11, 19), "test")
        self.assertEqual(result["Cost"], 280.5)
        self.assertEqual(result["Conversions"], 2)
        p = fetch.call_args.args[1]["params"]
        self.assertEqual(p["ReportType"], "CAMPAIGN_PERFORMANCE_REPORT")
        self.assertEqual(p["SelectionCriteria"]["Filter"], [{
            "Field": "CampaignId", "Operator": "IN", "Values": ["716316525"]
        }])
        self.assertIn("CampaignId", p["FieldNames"])

    def test_missing_conversion_metrics_not_fabricated_as_zero(self):
        with patch.object(direct_tools, "_report", return_value=[{
            "CampaignId": "716316525", "Impressions": "100",
            "Clicks": "5", "Cost": "99", "Conversions": "--"
        }]):
            result = direct_tools._perf("stub", date(2026, 11, 19),
                                       date(2026, 11, 19), "test")
        self.assertIsNone(result["Conversions"])
        msg = direct_tools._fmt(result)
        self.assertIn("данные недоступны", msg)
        self.assertNotIn("заявка", msg)

    def test_report_rejects_another_campaign_even_with_valid_metrics(self):
        with patch.object(direct_tools, "_report", return_value=[{
            "CampaignId": "999", "Impressions": "100",
            "Clicks": "5", "Cost": "99", "Conversions": "2",
        }]):
            with self.assertRaises(RuntimeError):
                direct_tools._perf("stub", date(2026, 11, 19),
                                   date(2026, 11, 19), "test")

    def test_daily_report_query_scope_and_no_false_lead_claim(self):
        seen = []
        def fake_fetch(token, payload):
            p = payload["params"]
            seen.append(p)
            if p["ReportType"] == "CAMPAIGN_PERFORMANCE_REPORT":
                return [{"CampaignId": "716316525", "Impressions": "100",
                         "Clicks": "5", "Cost": "150", "Conversions": "1"}]
            self.assertEqual(p["ReportType"], "SEARCH_QUERY_PERFORMANCE_REPORT")
            return [{"CampaignId": "716316525", "Query": "мехатроник dq200",
                     "Clicks": "2", "Cost": "65"}]
        with patch.dict(direct_tools.os.environ, {"YANDEX_DIRECT_TOKEN": "dummy"}), \
             patch.object(direct_tools, "_report", side_effect=fake_fetch), \
             patch.object(direct_tools, "_tg_owner") as send:
            status = direct_tools.cmd_direct_report()
        self.assertEqual(status, 0)
        self.assertEqual(len(seen), 3)
        for p in seen:
            self.assertEqual(p["SelectionCriteria"]["Filter"][0]["Values"],
                             ["716316525"])
        message = send.call_args.args[0]
        self.assertIn("конверсии (все цели)", message)
        self.assertIn("не равны подтверждённым заявкам", message)
        self.assertNotIn("(заявка ", message)
        self.assertIn("мехатроник dq200", message)


if __name__ == "__main__":
    unittest.main()
