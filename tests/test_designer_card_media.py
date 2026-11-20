from collections import Counter
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import designer_bot as bot
from designer_card_media import CardArchive, identity


class CardCollectionTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(TemporaryDirectory()))
        self.photos = self.root / "photos"
        self.social = self.root / "social"
        self.state = self.root / "data" / "designer_state.json"
        self.state.parent.mkdir()
        for sku in ("DEMO-1", "other", "К-85"):
            (self.photos / sku).mkdir(parents=True)
        self.gallery = self.photos / "DEMO-1" / "DEMO-1_1.png"
        self.gallery.write_bytes(b"existing approved image")
        self.rows = []
        self.sent = []
        self.downloads = Counter()
        self.fail = None
        self.mid = 6000
        self.uid = 0
        self.key = "test-key"
        self.fake_token = self.replace(bot, "_token", side_effect=lambda: self.key)
        for k, v in {"_ROOT": str(self.root), "PHOTOS_DIR": str(self.photos),
                     "AVITO_DIR": str(self.root / "photos_avito"), "STATE_PATH": str(self.state),
                     "OUTBOX_DIR": str(self.root / "outbox")}.items():
            self.stack.enter_context(patch.object(bot, k, v))
        for k, v in {"IMAGES_DIR": str(self.social), "TEXTS_DIR": str(self.root / "texts"),
                     "HOLD_PATH": str(self.root / "holds")}.items():
            self.stack.enter_context(patch.object(bot.sp, k, v))
        self.replace(bot, "persist_designer", return_value=None)
        self.replace(bot, "_chat_id", return_value=10)
        self.replace(bot, "_owner_id", return_value=20)
        self.replace(bot, "_plan_rows", return_value=[])
        self.replace(bot, "_notify_owner", return_value=None)
        self.replace(bot, "_notify_owner_text", return_value=None)
        self.replace(bot, "_download", side_effect=self.download)
        self.replace(bot, "_tg", side_effect=self.telegram)

    def replace(self, obj, key, **spec):
        return self.stack.enter_context(patch.object(obj, key, **spec))

    def download(self, fid):
        self.downloads[fid] += 1
        if self.fail == fid:
            raise OSError("synthetic secret error")
        return ("image payload " + fid).encode()

    def telegram(self, method, **kwargs):
        if method == "getUpdates":
            return list(self.rows)
        self.assertEqual(method, "sendMessage")
        self.mid += 1
        self.sent.append((kwargs["chat_id"], kwargs["text"], self.mid))
        return {"message_id": self.mid}

    def album(self, group, n, size, caption="", chat=10, reply=None):
        messages = []
        for k in range(size):
            m = {"chat": {"id": chat}, "message_id": n+k, "media_group_id": group,
                 "document": {"file_id": "fid-" + str(n+k), "file_name": "test.png", "mime_type": "image/png"}}
            if k == 0 and caption:
                m["caption"] = caption
            if k == 0 and reply is not None:
                m["reply_to_message"] = {"message_id": reply}
            messages.append(m)
        return messages

    def text(self, n, command, chat=10):
        return {"chat": {"id": chat}, "message_id": n, "text": command}

    def collect(self, messages):
        self.rows = []
        for message in messages:
            self.uid += 1
            self.rows.append({"update_id": self.uid, "message": message})
        with redirect_stdout(StringIO()):
            return bot.cmd_collect()

    def archive(self):
        return CardArchive(self.root, self.key)

    def files(self, sku="DEMO-1", channel="catalog"):
        return self.archive().files_for(sku, channel)

    def test_two_albums_same_poll_16_retained_and_existing_gallery_unchanged(self):
        a = self.album("a", 1, 10, "Карточка DEMO-1")
        b = self.album("b", 11, 6, "Карточка DEMO-1")
        self.collect(a+b)
        self.assertEqual(len(self.files()), 16)
        self.assertEqual([p.read_bytes() for p in self.files()], [self.download("fid-"+str(i)) for i in range(1, 17)])
        self.assertEqual(self.gallery.read_bytes(), b"existing approved image")
        self.assertEqual(list(self.gallery.parent.iterdir()), [self.gallery])

    def test_different_polls_replay_and_late_file(self):
        a = self.album("a", 1, 10, "Карточка DEMO-1")
        b = self.album("b", 11, 6, "Карточка DEMO-1")
        self.collect(a)
        self.collect(b)
        notices = len(self.sent)
        downloads = self.downloads.copy()
        self.collect(a+b)
        self.assertEqual(self.downloads, downloads)
        self.assertEqual(len(self.sent), notices)
        self.collect(self.album("b", 17, 1))
        self.assertEqual(len(self.files()), 17)
        new = self.album("c", 40, 1, "Карточка DEMO-1")
        new[0]["document"]["file_id"] = "fid-1"
        self.collect(new)
        self.assertEqual(len(self.files()), 18)
        self.assertEqual(len(set(self.files())), 17)

    def test_two_identical_slides_keep_two_positions_and_one_blob(self):
        batch = self.album("identical", 40, 3, "Карточка DEMO-1")
        batch[2]["document"]["file_id"] = batch[0]["document"]["file_id"]
        self.collect(batch)
        a, b, c = self.files()
        self.assertEqual(a, c)
        self.assertNotEqual(a, b)
        self.assertEqual(len(list(a.parent.glob("*.png"))), 2)
        self.collect(batch)
        self.assertEqual(self.files(), [a, b, c])
        self.assertTrue(any("В группе 3, всего по карточке 3" in text for _, text, _ in self.sent))

    def test_split_telegram_group_and_reply_to_previous_receipt(self):
        self.collect(self.album("one", 2, 3, "Карточка DEMO-1"))
        reply = next(mid for chat, _, mid in self.sent if chat == 10)
        self.collect(self.album("one", 5, 2))
        self.collect(self.album("two", 7, 4, reply=reply))
        self.collect(self.album("three", 11, 2, reply=3))
        self.assertEqual(len(self.files()), 11)

    def test_unidentified_album_is_encrypted_then_linked_without_resend(self):
        self.collect(self.album("labelled", 1, 10, "Карточка DEMO-1"))
        self.collect(self.album("loose", 11, 6))
        self.assertEqual(len(self.files()), 10)
        code = identity("group", 10, "loose")[:16]
        self.assertTrue(any(code in text for _, text, _ in self.sent))
        folder = self.root/"data/designer_card_media"
        self.assertEqual(len(list((folder/"unbound").glob("*.fernet"))), 6)
        self.assertFalse(any(b"image payload fid-11" in path.read_bytes() or b"fid-11" in path.read_bytes()
                             for path in folder.rglob("*") if path.is_file()))
        downloads = self.downloads.copy()
        self.collect([self.text(55, "Привязать "+code+" DEMO-1")])
        self.assertEqual(len(self.files()), 16)
        self.assertEqual(self.downloads, downloads)
        self.assertFalse(self.social.exists())

    def test_unknown_or_conflicting_card_captions_never_write_social_post(self):
        for n, cap in enumerate(("Карточка 123-А?", "Карточка 77 unknown", "произвольный номер 44"), 5):
            self.collect(self.album(str(n), n, 1, cap))
        a=self.album("conflict", 80, 2, "Карточка DEMO-1")
        a[1]["caption"]="Карточка other"
        self.collect(a)
        self.assertFalse(self.social.exists())
        self.assertEqual(self.files(), [])
        self.assertEqual(self.files("other"), [])
        self.collect(self.album("cyr", 91, 1, "Карточка К-85 Авито"))
        self.assertEqual(len(self.files("К-85", "avito")), 1)

    def test_unknown_first_slice_then_explicit_label_in_next_slice(self):
        self.collect(self.album("split", 1, 2))
        self.collect(self.album("split", 3, 1, "Карточка DEMO-1"))
        self.assertEqual(len(self.files()), 3)

    def test_contradictory_late_caption_quarantines_continuations(self):
        self.collect(self.album("split", 1, 2, "Карточка DEMO-1"))
        self.collect(self.album("split", 3, 2, "Карточка other"))
        self.collect(self.album("split", 5, 1))
        self.assertEqual(len(self.files()), 2)
        self.assertFalse(self.files("other"))
        code = identity("conflict", 10, "split")[:16]
        self.collect([self.text(11, "Привязать " + code + " other")])
        self.assertEqual(len(self.files("other")), 3)
        self.collect(self.album("split", 12, 1))
        self.assertEqual(len(self.files("other")), 3)
        self.assertEqual(len(self.files()), 2)

    def test_owner_can_resolve_quarantine_but_token_rotation_needs_resubmission(self):
        self.collect(self.album("u", 1, 1))
        code = identity("group", 10, "u")[:16]
        self.key = "rotated"
        self.collect([self.text(2, "Привязать " + code + " other", 20)])
        self.assertFalse(self.files("other"))
        self.assertTrue(any("повторно" in message for _, message, _ in self.sent))
        self.key = "test-key"
        self.collect([self.text(3, "Привязать " + code + " other", 20)])
        self.assertEqual(len(self.files("other")), 1)

    def test_stale_collector_cannot_overwrite_concurrent_registry(self):
        first, second = self.archive(), self.archive()
        first.accept(10, "first", [(1, "a", ".png")], None, self.download)
        with self.assertRaisesRegex(RuntimeError, "Журнал изменился"):
            second.accept(10, "next", [(2, "b", ".png")], None, self.download)
        self.assertEqual(len(self.archive().groups), 1)

    def test_chats_with_same_group_id_do_not_mix_channels_or_private_material(self):
        self.collect(self.album("same", 1, 2, "Карточка DEMO-1", chat=10)
                     +self.album("same", 7, 3, "Карточка other Авито", chat=20)
                     +self.album("same", 11, 1, "Карточка DEMO-1", chat=30))
        self.assertEqual(len(self.files()), 2)
        self.assertEqual(len(self.files("other","avito")), 3)
        self.assertNotIn("fid-11",self.downloads)
        self.collect(self.album("ownerunbound", 12, 1, chat=20))
        code=identity("group",20,"ownerunbound")[:16]
        self.collect([self.text(13,"Привязать "+code+" DEMO-1",10)])
        self.assertEqual(len(self.files()), 2)

    def test_partial_download_failure_does_not_checkpoint_or_duplicate(self):
        batch=self.album("a",1,2,"Карточка DEMO-1")+self.album("b",3,3,"Карточка DEMO-1")
        self.fail="fid-4"
        with self.assertRaisesRegex(RuntimeError, "Опрос будет повторён"):
            self.collect(batch)
        self.assertFalse(self.state.exists())
        self.assertEqual(len(self.files()), 2)
        self.fail=None
        self.collect(batch)
        self.assertEqual(len(self.files()), 5)
        self.assertEqual(self.downloads["fid-1"], 1)
        self.assertGreater(json.loads(self.state.read_text())["offset"],0)

    def test_social_numbers_are_scoped_to_sender_and_precede_images(self):
        self.collect([self.text(1,"101"),*self.album("p",2,1),self.text(3,"202"),
                      *self.album("p2",4,1,"пост 202 добавить"),*self.album("neutral",5,1,chat=20)])
        self.assertEqual((self.social/"101/1.png").read_bytes(), b"image payload fid-2")
        self.assertEqual((self.social/"202/1.png").read_bytes(), b"image payload fid-4")
        self.assertEqual(len(list(self.social.rglob("*.png"))),2)

    def test_explicit_social_post_10_plus_6_preserves_all_sixteen_files(self):
        a = self.album("p1", 1, 10, "пост 101")
        b = self.album("p2", 11, 6, "101")
        self.collect(a + b)
        paths = [self.social / "101" / f"{i}.png" for i in range(1, 17)]
        self.assertEqual([f.read_bytes() for f in paths], [f"image payload fid-{i}".encode() for i in range(1, 17)])
        paths[5].unlink()
        self.collect(self.album("p3", 17, 1, "101 добавить"))
        self.assertEqual((self.social / "101/16.png").read_bytes(), b"image payload fid-16")
        self.assertEqual((self.social / "101/17.png").read_bytes(), b"image payload fid-17")

    def test_same_social_update_replayed_after_flush_failure_appends_once(self):
        rows = self.album("one", 1, 1, "101 добавить")
        with patch.object(bot, "_flush_tasks", side_effect=RuntimeError("simulated checkpoint failure")):
            with self.assertRaises(RuntimeError):
                self.collect(rows)
        self.assertFalse(bot._load(self.state, {}).get("offset"))
        folder = self.social / "101"
        self.assertEqual([p.name for p in folder.glob("*.png")], ["1.png"])
        with redirect_stdout(StringIO()):
            self.assertEqual(bot.cmd_collect(), 0)  # identical update_id and message_id
        self.assertEqual([p.name for p in folder.glob("*.png")], ["1.png"])
        self.assertEqual(self.downloads["fid-1"], 1)
        self.assertEqual(bot._load(self.state, {})["offset"], self.rows[-1]["update_id"] + 1)

    def test_equal_bytes_in_two_social_messages_recover_two_ordered_positions(self):
        rows = self.album("same", 12, 2, "101 добавить")
        folder = self.social / "101"
        real_write = bot.atomic_write

        def fail_on_second(path, data):
            if path == folder / "2.png":
                raise OSError("simulated interrupted write")
            real_write(path, data)

        with patch.object(bot, "_download", return_value=b"duplicate bytes") as download:
            with patch.object(bot, "atomic_write", side_effect=fail_on_second):
                with self.assertRaises(OSError):
                    self.collect(rows)
            self.assertFalse(bot._load(self.state, {}).get("offset"))
            with redirect_stdout(StringIO()):
                self.assertEqual(bot.cmd_collect(), 0)
            self.assertEqual(download.call_count, 2)
        self.assertEqual(sorted(p.name for p in folder.glob("*.png")), ["1.png", "2.png"])
        for p in folder.glob("*.png"):
            self.assertEqual(p.read_bytes(), b"duplicate bytes")
        receipt = json.loads((folder / ".designer-receipts.json").read_bytes())
        self.assertEqual(len(set(receipt["done"])), 2)
        self.assertTrue(all(len(k) == 64 for k in receipt["done"]))
        self.assertNotIn("pending", receipt)
        self.assertFalse((folder / ".designer-pending").exists())

    def test_even_one_open_social_post_cannot_claim_an_untagged_image(self):
        m = self.album("single", 98, 1)[0]
        del m["media_group_id"]
        with patch.object(bot, "_single_open", return_value=421):
            self.collect([m])
        self.assertFalse(self.social.exists())
        self.assertEqual(len(self.archive().groups), 1)


if __name__ == "__main__":
    unittest.main()
