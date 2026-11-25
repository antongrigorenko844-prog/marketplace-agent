"""Offline checks: no network, no payment data beyond non-PII owner evidence."""
import copy
import json
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch
import direct_commercial_evidence as c
import direct_tools

START=date(2026,11,16)
END=date(2026,11,25)
def row(values=None, cid=c.CAMPAIGN_ID):
    return {"dimensions":[{"id":str(cid)}],"metrics":values or [237,1,40416.25,0,11]}

class CommercialEvidenceTests(unittest.TestCase):
    def test_historical_exact_attribution_and_known_unpaid(self):
        p=Path("data/direct_traffic_quality/2026-11-26-45705645639/targeted_supplement.json")
        s=json.loads(p.read_text(encoding="utf-8"))["sections"]["purchase_attribution"]["data"]["by_direct_campaign"]["data"]
        x=c.commercial_summary(s["rows"],s["api_total_rows"],s["sampled"],START,END)
        self.assertEqual(x["ecommerce_purchase_events"],1)
        self.assertEqual(x["ecommerce_event_amount_rub"],40416.25)
        self.assertEqual(x["purchase_goal_visits"],0)
        self.assertEqual(x["payment_evidence"]["known_unpaid_cases"],1)
        self.assertEqual(x["payment_evidence"]["known_unpaid_order_amount_rub"],40416.25)
        self.assertIn("передумал",x["payment_evidence"]["cases"][0]["reason"])
        for k in ("actual_paid_orders","actual_paid_revenue_rub","actual_ROAS","actual_CAC","actual_ROMI"):
            self.assertIsNone(x[k])

    def test_new_ecommerce_event_not_paid_evidence(self):
        x=c.commercial_summary([row([238,2,80832.50,1,12])],1,False,START,END)
        self.assertEqual(x["ecommerce_purchase_events"],2)
        self.assertEqual(x["payment_evidence"]["known_unpaid_cases"],1)
        self.assertIsNone(x["actual_paid_orders"])
        self.assertIsNone(x["actual_paid_revenue_rub"])

    def test_outside_visit_date_does_not_count_case(self):
        x=c.commercial_summary([row([45,1,40416.25,0,1])],1,False,date(2026,11,23),END)
        self.assertEqual(x["payment_evidence"]["known_unpaid_cases"],0)
        self.assertEqual(x["ecommerce_purchase_events"],1)
        self.assertIsNone(x["actual_ROAS"])

    def test_reject_other_campaign(self):
        with self.assertRaisesRegex(ValueError,"ID mismatch"):
            c.summarize_campaign_rows([row(cid=123)],1,False)

    def test_reject_truncated_rows(self):
        with self.assertRaises(ValueError):
            c.summarize_campaign_rows([row()],2,False)

    def test_empty_complete_rows_not_paid_proof(self):
        x=c.commercial_summary([],0,False,START,END)
        self.assertEqual(x["ecommerce_purchase_events"],0)
        self.assertIsNone(x["actual_paid_orders"])
        self.assertEqual(x["payment_evidence"]["known_unpaid_cases"],1)

    def test_missing_and_invalid_numeric_values(self):
        for v in (None, "--", True, -1, float("nan")):
            with self.subTest(v=v),self.assertRaises(ValueError):
                c.summarize_campaign_rows([row([237,1,v,0,11])],1,False)

    def test_duplicate_payment_evidence_rejected(self):
        r=json.loads(c.REGISTER.read_text(encoding="utf-8"))
        r["cases"].append(copy.deepcopy(r["cases"][0]))
        with self.assertRaisesRegex(ValueError,"duplicate"):
            c.owner_evidence(START,END,r)

    def test_summary_request_exact_id_and_no_redirect(self):
        response=Mock(status_code=200)
        response.json.return_value={"data":[row()],"total_rows":1,"sampled":False}
        with patch.object(direct_tools.requests,"get",return_value=response) as call:
            x=direct_tools._metrika_direct_campaign_summary("dummy",START,END)
        params=call.call_args.kwargs["params"]
        self.assertEqual(params["filters"],"ym:s:lastDirectClickOrder==716316525")
        self.assertEqual(params["dimensions"],c.DIMENSION)
        self.assertFalse(call.call_args.kwargs["allow_redirects"])
        self.assertEqual(x["ecommerce_purchase_events"],1)
        self.assertIsNone(x["actual_paid_revenue_rub"])

    def test_failed_request_does_not_leak_credentials(self):
        response=Mock(status_code=403)
        with patch.object(direct_tools.requests,"get",return_value=response):
            with self.assertRaisesRegex(RuntimeError,"HTTP 403") as ctx:
                direct_tools._metrika_direct_campaign_summary("private-token",START,END)
        self.assertNotIn("private-token",str(ctx.exception))

if __name__=="__main__":
    unittest.main()
