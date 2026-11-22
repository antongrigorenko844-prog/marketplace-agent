"""Safe media-only update regression tests. No live marketplace API calls."""
import os
import unittest
from unittest.mock import patch

import media_fast


URL = "https://example.com/approved.jpg"


class MediaFastTests(unittest.TestCase):
    def test_channels_must_be_unique_known_and_nonempty(self):
        self.assertEqual(media_fast.selected_channels("ozon,wb,market"), ("ozon", "wb", "market"))
        for bad in ("", "ozon,ozon", "ozon,sklad", "wb,,wb"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                media_fast.selected_channels(bad)

    def test_requires_exact_approved_cover(self):
        manifest = {"ABC": {"sources": {"ozon": [URL]}}}
        self.assertEqual(media_fast.desired_url("ABC", "ozon", manifest), URL)
        for sku in ("not-found", "../ABC", " ABC"):
            with self.subTest(sku=sku), self.assertRaises(ValueError):
                media_fast.desired_url(sku, "ozon", manifest)

    def test_original_secondary_media_kept(self):
        old = "https://example.com/old.jpg"
        extras = ["https://example.com/extra-1.jpg", "https://example.com/extra-2.jpg"]
        self.assertEqual(media_fast.other_images([old, *extras], old, URL), extras)
        self.assertEqual(media_fast.other_images([old, URL, *extras], old, URL), extras)

    def test_apply_needs_explicit_approval(self):
        with patch.dict(os.environ, {"MEDIA_APPLY_APPROVED": "false"}):
            with self.assertRaises(PermissionError):
                media_fast.run("ABC", "apply", ("ozon",))

    def test_ozon_inspect_never_writes(self):
        import ozon_client as oz
        cover = {"offer_id": "ABC", "primary_image": "https://example.com/old.jpg",
                 "images": ["https://example.com/extra.jpg"]}
        with patch.object(oz, "get_product_names", return_value=[cover]), \
                patch("media_fast._jpg_compare", return_value=60.0), \
                patch.object(oz, "_post") as send:
            result = media_fast._ozon("ABC", URL, "inspect")
            self.assertEqual(result["status"], "needs_update")
            self.assertEqual(result["preserved_secondary_count"], 1)
            send.assert_not_called()

    def test_ozon_cdn_same_pixels_is_verified_without_reupload(self):
        import ozon_client as oz
        cover = {"offer_id": "ABC", "primary_image": "https://ir.ozone.ru/cdn-new.jpg",
                 "images": ["https://example.com/detail.jpg"]}
        with patch.object(oz, "get_product_names", return_value=[cover]), \
                patch("media_fast._jpg_compare", return_value=2.1), \
                patch.object(oz, "_post") as send:
            result = media_fast._ozon("ABC", URL, "inspect")
            self.assertEqual(result["status"], "verified")
            self.assertLess(result["mean_pixel_difference"], 16)
            send.assert_not_called()

    def test_ozon_apply_only_pictures_exact_sku(self):
        import ozon_client as oz
        before = {"offer_id": "ABC", "primary_image": "https://example.com/old.jpg",
                  "images": ["https://example.com/old.jpg", "https://example.com/extra.jpg"],
                  "name": "Item", "type_id": 100}
        after = {**before, "primary_image": URL}
        with patch.object(oz, "get_product_names", side_effect=[[before], [after]]), \
                patch.object(oz, "_post", return_value={"task_id": 22}) as send:
            result = media_fast._ozon("ABC", URL, "apply")
        self.assertEqual(result["status"], "verified")
        self.assertFalse(result["nonmedia_drift"])
        self.assertEqual(send.call_args.args[0], "/v2/product/pictures/import")
        payload = send.call_args.args[1]["items"][0]
        self.assertEqual(set(payload), {"offer_id", "primary_image", "images", "color_image"})
        self.assertEqual(payload["offer_id"], "ABC")
        self.assertEqual(payload["images"], ["https://example.com/extra.jpg"])

    def test_wb_never_claims_verified_without_pixel_evidence(self):
        import wb_client as wb
        row = {"vendorCode": "ABC", "nmID": 100, "photos": [{"big": "https://example.com/cdn.jpg"}]}
        with patch.object(wb, "list_cards", return_value=[row]), \
                patch("media_fast.load_manifest", return_value={"ABC": {"ids": {"wb": 100}}}), \
                patch("media_fast._jpg_compare", return_value=2.5) as compare:
            value = media_fast._wb("ABC", URL, "inspect", check_pixels=True)
            self.assertEqual(value["status"], "verified")
            compare.assert_called_once()
            value = media_fast._wb("ABC", URL, "inspect", check_pixels=False)
            self.assertEqual(value["status"], "unverified")

    def test_mixed_success_keeps_independent_channel_statuses(self):
        with patch("media_fast.desired_url", return_value=URL), \
                patch("media_fast._ozon", return_value={"status": "verified"}), \
                patch("media_fast._wb", side_effect=ValueError("WB unavailable")):
            r = media_fast.run("ABC", "inspect", ("ozon", "wb"), check_pixels=False)
        self.assertEqual(r["overall"], "blocked")
        self.assertEqual(r["channels"]["ozon"]["status"], "verified")
        self.assertEqual(r["channels"]["wb"]["status"], "blocked")
        self.assertEqual(r["stock_write_calls"], 0)
        self.assertEqual(r["price_write_calls"], 0)

    def test_failed_channel_does_not_trigger_other_updates(self):
        with patch("media_fast.desired_url", return_value=URL), \
                patch("media_fast._ozon", side_effect=ConnectionError("API error")), \
                patch("media_fast._market", return_value={"status": "verified"}):
            r = media_fast.run("ABC", "inspect", ("ozon", "market"))
        self.assertEqual(r["channels"]["ozon"]["status"], "blocked")
        self.assertEqual(r["channels"]["market"]["status"], "verified")


if __name__ == "__main__":
    unittest.main()
