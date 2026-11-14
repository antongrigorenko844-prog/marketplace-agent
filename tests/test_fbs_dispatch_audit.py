import unittest
from datetime import datetime, timezone
from fbs_dispatch_audit import report


class DispatchTests(unittest.TestCase):
    NOW = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)

    def test_deadline_is_moscow_and_recommended_slot_is_ignored(self):
        row = {"posting_number": "1", "status": "awaiting_deliver", "shipment_date_without_delay": "2026-11-14T09:29:00Z",
               "shipment_date": "2026-11-15T17:00:00Z"}
        data = report([row], self.NOW)
        self.assertEqual(data["rows"][0]["deadline_moscow"], "14.11.2026 12:29")
        self.assertEqual(data["rows"][0]["urgency"], "До срока не больше 3 часов")

    def test_shipped_and_cancelled_orders_are_not_todo(self):
        self.assertEqual(report([{"status": s} for s in ("delivered", "delivering", "cancelled")], self.NOW)["pending_count"], 0)

    def test_missing_deadline_is_not_invented(self):
        data = report([{"posting_number": "2", "status": "awaiting_packaging", "shipment_date": "2026-11-14T09:29:00Z"}], self.NOW)
        self.assertIsNone(data["rows"][0]["deadline_utc"])
        self.assertIn("Срок не получен", data["rows"][0]["urgency"])


if __name__ == "__main__":
    unittest.main()
