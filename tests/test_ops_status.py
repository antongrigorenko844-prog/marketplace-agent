import unittest
import ops_status as op

class StatusTests(unittest.TestCase):
    def test_submitted_is_not_verified(self):
        s=op.normalize_media({"sku":"6t30","channels":{
            "ozon":{"status":"verified"},
            "wb":{"status":"pending"},
            "market":{"status":"blocked","reason":"photo not accepted"}}})
        self.assertEqual(s["ozon"]["status"],"verified")
        self.assertEqual(s["wb"]["status"],"pending_platform")
        self.assertEqual(s["market"]["status"],"failed")

    def test_published_avito_feed_is_not_a_confirmed_live_listing(self):
        x=op.normalize_avito({"sku":"6t30","result":"already_published_no_upload",
                              "repo_matches_published_feed":True})
        self.assertEqual(x["status"],"feed_published_listing_unverified")
        self.assertFalse(x["live_listing_photo_confirmed"])

    def test_tilda_import_needed_is_not_success(self):
        x=op.normalize_tilda({"status":"needs_csv_import","sku":"6t30"})
        self.assertEqual(x["status"],"manual_csv_import_needed")
        self.assertFalse(x["source_verified"])

    def test_stock_missing_historical_recipes_visible(self):
        x=op.normalize_stock({"status":"historical_corrections_need_evidence",
                              "ledger":{"pending_projection":0,
                                        "by_source":[{"active_missing_recipes":806},
                                                     {"active_missing_recipes":201}]},
                              "channels":{"ozon_fbs":{"counts":{"blocked_legacy":7}}}})
        self.assertEqual(x["old_debits_without_recipes"],1007)
        self.assertEqual(x["live_order_conflicts"],7)

    def test_rehearsal_success_is_not_live_stock_update(self):
        data=op.normalize_stock_dryrun({"status":"dryrun_success",
            "production_hashes_unchanged":True,"deferred_count":3})
        self.assertEqual(data["status"],"simulated_success")
        self.assertTrue(data["read_only"])
        self.assertTrue(data["production_inputs_unchanged"])
        self.assertEqual(data["deferred_legacy_cancellations"],3)

    def test_report_no_fake_channels(self):
        r=op.build({"media":{"sku":"x","channels":{"ozon":{"status":"verified"}}},
                    "avito":{},"tilda":{},"stock":{}})
        self.assertEqual(set(r["checks"]),{"ozon"})
        self.assertTrue(r["sources"]["media"])
        self.assertFalse(r["sources"]["avito"])
        self.assertIn("Ozon",op.render(r))

if __name__=="__main__":
    unittest.main()
