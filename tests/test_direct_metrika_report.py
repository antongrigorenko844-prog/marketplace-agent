"""Offline regression checks for connected Metrica in daily Direct Telegram bot."""
from datetime import date
from unittest.mock import Mock, patch
import unittest
import direct_tools

CID=direct_tools.DIRECT_DQ200_CAMPAIGN_ID
GOALS=direct_tools.METRIKA_GOAL_IDS
class MetricaSummarySafetyTests(unittest.TestCase):
    def test_source_only_ads_and_correct_goal_names(self):
        response=Mock()
        response.status_code=200
        response.json.return_value={
            "data":[
                {"dimensions":[{"id":"direct","name":"Прямые заходы"}],
                 "metrics":[11,4,7,1,0,0,0,0]},
                {"dimensions":[{"id":"ad","name":"Переходы по рекламе"}],
                 "metrics":[22,1,8,0,0,0,2,1]},
            ],"sampled":False}
        with patch.object(direct_tools.requests,"get",return_value=response) as get:
            r=direct_tools._metrika_paid_summary("placeholder-secret",date(2026,11,19),
                                                   date(2026,11,19))
        self.assertEqual(r["visits"],22)
        self.assertEqual(r["goal_visits"][675272284],1)
        self.assertEqual(r["goal_visits"][675487735],8)
        self.assertEqual(r["goal_visits"][682257540],0)
        self.assertEqual(r["goal_visits"][685988392],2)
        self.assertEqual(r["goal_visits"][686020680],1)
        self.assertEqual(r["goal_visits"][675506966],0)
        self.assertEqual(r["attribution"],"all_ads_sitewide")
        self.assertEqual(get.call_args.kwargs["headers"]["Authorization"],
                         "OAuth placeholder-secret")
        self.assertEqual(get.call_args.kwargs["params"]["ids"],113839860)
        self.assertEqual(len(get.call_args.kwargs["params"]["metrics"].split(",")),
                         1+len(GOALS))
    def test_empty_paid_visits_is_valid_zero(self):
        response=Mock(status_code=200)
        response.json.return_value={"data":[{"dimensions":[{"id":"organic"}],
                                               "metrics":[5,0,0,0,0,0,0,0]}]}
        with patch.object(direct_tools.requests,"get",return_value=response):
            result=direct_tools._metrika_paid_summary("token",date(2026,11,19),
                                                      date(2026,11,19))
        self.assertEqual(result["visits"],0)
        self.assertEqual(result["goal_visits"][675272284],0)
    def test_bad_http_does_not_print_bearer(self):
        response=Mock(status_code=403)
        with patch.object(direct_tools.requests,"get",return_value=response):
            with self.assertRaisesRegex(RuntimeError,"HTTP 403") as ctx:
                direct_tools._metrika_paid_summary("top-secret",
                                                   date(2026,11,19),date(2026,11,19))
        self.assertNotIn("top-secret",str(ctx.exception))
    def test_daily_bot_adds_metrica_with_separate_semantics(self):
        def fake_direct(token,payload):
            p=payload["params"]
            if p["ReportType"]=="CAMPAIGN_PERFORMANCE_REPORT":
                return [{"CampaignId":str(CID),"Impressions":"100",
                         "Clicks":"10","Cost":"200","Conversions":"2"}]
            return [{"CampaignId":str(CID),"Query":"мехатроник dq200",
                     "Clicks":"2","Cost":"40"}]
        metrika={"visits":22,"goal_visits":{675272284:1,675487735:7,
                682257540:0,675506966:0,675507028:0,685988392:2,686020680:1},
                 "attribution":"all_ads_sitewide","sampled":False}
        with patch.dict(direct_tools.os.environ,{"YANDEX_DIRECT_TOKEN":"d",
                                                 "YANDEX_METRIKA_TOKEN":"m"}), \
             patch.object(direct_tools,"_report",side_effect=fake_direct), \
             patch.object(direct_tools,"_metrika_paid_summary",return_value=metrika) as read, \
             patch.object(direct_tools,"_metrika_utm_campaign_summary",return_value=metrika) as read_utm, \
             patch.object(direct_tools,"_tg_owner") as send:
            ok=direct_tools.cmd_direct_report()
        self.assertEqual(ok,0)
        self.assertEqual(read.call_count,2)
        self.assertEqual(read_utm.call_count,2)
        m=send.call_args.args[0]
        self.assertIn("Метрика",m)
        self.assertIn("все рекламные визиты сайта",m)
        self.assertIn("только визиты с UTM dq200_feed_search",m)
        self.assertIn("приоритетных URL-целей VIN и заказа",m)
        self.assertIn("Форма VIN (общая) 1",m)
        self.assertIn("VIN успешно (новая цель) 1",m)
        self.assertIn("Ecommerce-покупка 0",m)
        self.assertIn("Заказ-URL 0",m)
        self.assertIn("Начало оплаты (не покупка) 2",m)
        self.assertIn("промежуточное событие",m)
        self.assertIn("не подтверждают оплату",m)
        self.assertNotIn("(заявка ",m)
    def test_utm_campaign_aggregates_only_expected_yandex_traffic(self):
        response = Mock(status_code=200)
        response.json.return_value = {"data":[
            {"dimensions":[{"name":"yandex"},{"name":"dq200_feed_search"}],
             "metrics":[50,1,2,0,0,0,2,1]},
            {"dimensions":[{"name":"ya"},{"name":"dq200_feed_search"}],
             "metrics":[3,0,1,0,0,0,0,0]},
            {"dimensions":[{"name":"yandex"},{"name":"unrelated_campaign"}],
             "metrics":[45,30,20,10,10,10,10,10]},
            {"dimensions":[{"name":"google"},{"name":"dq200_feed_search"}],
             "metrics":[90,50,60,80,10,10,10,10]},
        ],"sampled":False,"total_rows":4}
        with patch.object(direct_tools.requests,"get",return_value=response) as call:
            out=direct_tools._metrika_utm_campaign_summary(
                "dummy-secret",date(2026,11,19),date(2026,11,19))
        self.assertEqual(out["visits"],53)
        self.assertEqual(out["goal_visits"][675272284],1)
        self.assertEqual(out["goal_visits"][675487735],3)
        self.assertEqual(out["goal_visits"][682257540],0)
        self.assertEqual(out["goal_visits"][685988392],2)
        self.assertEqual(out["goal_visits"][686020680],1)
        self.assertEqual(out["rows_matched"],2)
        self.assertEqual(out["attribution"],"campaign_utm_only")
        self.assertEqual(call.call_args.kwargs["params"]["dimensions"],
                         "ym:s:lastUTMSource,ym:s:lastUTMCampaign")

    def test_utm_campaign_rejects_truncated_data(self):
        response=Mock(status_code=200)
        response.json.return_value={"data":[],"total_rows":1001}
        with patch.object(direct_tools.requests,"get",return_value=response):
            with self.assertRaisesRegex(RuntimeError,"неполный"):
                direct_tools._metrika_utm_campaign_summary(
                    "token",date(2026,11,19),date(2026,11,19))

    def test_daily_bot_handles_metrika_failure_without_token_leak(self):
        def fake_report(token,payload):
            p=payload["params"]
            if p["ReportType"]=="CAMPAIGN_PERFORMANCE_REPORT":
                return [{"CampaignId":str(CID),"Impressions":"100",
                         "Clicks":"10","Cost":"200","Conversions":"2"}]
            return []
        with patch.dict(direct_tools.os.environ,{"YANDEX_DIRECT_TOKEN":"d",
                                                 "YANDEX_METRIKA_TOKEN":"sensitive"}), \
             patch.object(direct_tools,"_report",side_effect=fake_report), \
             patch.object(direct_tools,"_metrika_paid_summary",
                          side_effect=RuntimeError("sensitive")) as read, \
             patch.object(direct_tools,"_tg_owner") as send:
            self.assertEqual(direct_tools.cmd_direct_report(),0)
        msg=send.call_args.args[0]
        self.assertIn("Метрика: агрегированные цели пока недоступны",msg)
        self.assertNotIn("sensitive",msg)
if __name__=="__main__":unittest.main()
