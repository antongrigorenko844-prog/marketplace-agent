import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import designer_video as video


COPY = {p: {"title": "Заголовок " + p, "description": "Абзац №1\nАбзац №2 · " + p,
            "hashtags": "#пример #" + p} for p in video.PLATFORMS}


class VideoDeliveryTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        (self.root / "data").mkdir()
        self.keys = ["photos/part" + str(i) + ".mp4" for i in range(6)]
        self.legacy = {k: i + 1 for i, k in enumerate(self.keys[:4])}
        self.state_path = self.root / video.STATE
        self.state_path.write_text(json.dumps(self.legacy))
        (self.root / video.QUEUE).write_text(json.dumps([{"file": k, "articles": ["DEMO"]} for k in self.keys]))
        (self.root / "photos").mkdir()
        for k in self.keys[4:]:
            (self.root / k).write_bytes(b"synthetic-video")
        self.patcher(video, "build_copy", return_value=COPY)
        self.patcher(video.bot, "_token", return_value="TEST")
        self.patcher(video.bot, "_chat_id", return_value=101)
        self.wire = self.patcher(video.bot.requests, "post", side_effect=self.respond)
        self.response = self.acknowledgement(["part4.mp4", video.COPY_NAME])
        self.text_response = {"message_id": 205, "chat": {"id": 101}}
        self.attachments = {}
        self.inline_text = None

    def patcher(self, obj, attr, **kwargs):
        p = patch.object(obj, attr, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def acknowledgement(self, names):
        return [{"message_id": 203 + i, "media_group_id": "mock-group", "chat": {"id": 101},
                 "document": {"file_name": name, "file_id": "mock-file-" + str(i)}} for i, name in enumerate(names)]

    def respond(self, url, data, files, timeout):
        self.assertEqual(data["chat_id"], 101)
        self.assertEqual(timeout, 120)
        if url.endswith("/sendMessage"):
            self.assertIsNone(files)
            self.inline_text = data["text"]
            self.assertTrue(data["text"].strip())
            self.assertLessEqual(len(data["text"]), 4096)
            if isinstance(self.text_response, Exception):
                raise self.text_response
            result = self.text_response
        else:
            self.assertTrue(url.endswith("/sendMediaGroup"))
            media = json.loads(data["media"])
            self.assertEqual([x["type"] for x in media], ["document", "document"])
            self.assertEqual([x["media"] for x in media], ["attach://video", "attach://copy"])
            self.assertLessEqual(len(media[0]["caption"]), 1024)
            self.attachments = {name: fh.read() for name, fh, _ in files.values()}
            self.text_caption = media[1].get("caption")
            if isinstance(self.response, Exception):
                raise self.response
            result = self.response
        class Response:
            def json(self):
                return {"ok": True, "result": result}
        return Response()

    def run_phase(self, phase, op="703:1"):
        return video.deliver(phase, op, self.root)

    def test_one_album_carries_complete_copy_and_skips_four_legacy_entries(self):
        self.assertEqual(self.run_phase("prepare"), "reserved")
        self.wire.assert_not_called()
        self.assertEqual(self.run_phase("send"), "sent")
        self.assertEqual(self.run_phase("send"), "already_sent")
        self.assertEqual(self.wire.call_count, 2)
        state = json.loads(self.state_path.read_bytes())
        self.assertEqual({k: state[k] for k in self.legacy}, self.legacy)
        self.assertEqual(state[self.keys[4]]["message_ids"], [203, 204])
        self.assertEqual(state[self.keys[4]]["text_message_id"], 205)
        self.assertNotIn(self.keys[5], state)
        self.assertEqual(self.attachments["part4.mp4"], b"synthetic-video")
        text = self.attachments["publication.txt"].decode("utf-8")
        self.assertEqual(self.text_caption, text.rstrip())
        self.assertEqual(self.inline_text, text.rstrip())
        for platform, content in COPY.items():
            self.assertIn(platform + "\n", text)
            for value in content.values():
                self.assertIn(value, text)

    def test_does_not_send_before_persisted_reservation_or_after_contents_change(self):
        with self.assertRaises(video.DeliveryError):
            self.run_phase("send")
        self.run_phase("prepare")
        (self.root / self.keys[4]).write_bytes(b"changed")
        with self.assertRaises(video.DeliveryError):
            self.run_phase("send")
        self.wire.assert_not_called()

    def test_rejects_incomplete_copy_before_creating_a_reservation(self):
        with patch.object(video, "build_copy", return_value={"YouTube": COPY["YouTube"]}):
            with self.assertRaises(video.DeliveryError):
                self.run_phase("prepare")
        self.assertEqual(json.loads(self.state_path.read_bytes()), self.legacy)
        self.wire.assert_not_called()

    def test_incomplete_receipt_or_transport_timeout_holds_queue_across_runs(self):
        for result in (self.response[:1], TimeoutError("potential remote acceptance")):
            with self.subTest():
                self.state_path.write_text(json.dumps(self.legacy))
                self.response = result
                self.wire.reset_mock()
                self.run_phase("prepare")
                with self.assertRaises(video.DeliveryError):
                    self.run_phase("send")
                self.assertEqual(json.loads(self.state_path.read_bytes())[self.keys[4]]["status"], "needs_review")
                for op in ("703:1", "703:2", "704:1"):
                    with self.assertRaises(video.DeliveryError):
                        self.run_phase("prepare", op)
                self.wire.assert_called_once()

    def test_receipt_must_confirm_two_distinct_messages_same_chat_album_and_files(self):
        for field, bad in (("message_id", 203), ("media_group_id", "another"), ("chat", {"id": 102}),
                           ("document", {"file_name": "wrong.txt", "file_id": "test"})):
            with self.subTest(field=field):
                docs=self.acknowledgement(["part4.mp4", video.COPY_NAME])
                docs[1][field]=bad
                with self.assertRaises(video.DeliveryError):
                    video.receipt_ids(docs, 101, (("part4.mp4", 15), (video.COPY_NAME, 900)))

    def test_missing_optional_filename_uses_known_size_of_each_document(self):
        docs = self.acknowledgement(["part4.mp4", video.COPY_NAME])
        uploads = (("part4.mp4", 15), (video.COPY_NAME, 900))
        for doc, (_, size) in zip(docs, uploads):
            del doc["document"]["file_name"]
            doc["document"]["file_size"] = size
        self.assertEqual(video.receipt_ids(docs, 101, uploads), [203, 204])
        del docs[1]["document"]["file_size"]
        with self.assertRaises(video.DeliveryError):
            video.receipt_ids(docs, 101, uploads)

    def test_long_text_remains_complete_in_file_when_too_long_for_telegram_caption(self):
        long_copy = {p: {**c, "description": "🎞" * 540} for p, c in COPY.items()}
        with patch.object(video, "build_copy", return_value=long_copy):
            self.run_phase("prepare")
            self.run_phase("send")
        self.assertIsNone(self.text_caption)
        self.assertEqual(self.attachments["publication.txt"].decode("utf-8").count("🎞"), 1620)

    def test_failure_to_save_receipt_cannot_resend_from_committed_reservation(self):
        self.run_phase("prepare")
        remote = self.state_path.read_bytes()
        save = video.bot._save
        def fail_at_receipt(path, state):
            if state[self.keys[4]]["status"] == "sent":
                raise OSError("simulated disk error")
            save(path, state)
        with patch.object(video.bot, "_save", side_effect=fail_at_receipt):
            with self.assertRaises(OSError):
                self.run_phase("send")
        self.state_path.write_bytes(remote)  # A new runner sees the pushed reservation only.
        for op in ("703:2", "704:1"):
            with self.assertRaises(video.DeliveryError):
                self.run_phase("prepare", op)
        self.wire.assert_called_once()

    def test_inline_text_failure_does_not_resend_already_accepted_video(self):
        self.run_phase("prepare")
        self.text_response = TimeoutError("uncertain inline text acceptance")
        with self.assertRaises(video.DeliveryError):
            self.run_phase("send")
        state = json.loads(self.state_path.read_bytes())
        self.assertEqual(state[self.keys[4]]["status"], "needs_review")
        for op in ("703:1", "703:2", "704:1"):
            with self.assertRaises(video.DeliveryError):
                self.run_phase("prepare", op)
        self.assertEqual(self.wire.call_count, 2)

    def test_next_run_selects_only_the_next_unsent_item(self):
        self.run_phase("prepare")
        self.run_phase("send")
        self.response=self.acknowledgement(["part5.mp4", video.COPY_NAME])
        self.run_phase("prepare", "704:1")
        self.run_phase("send", "704:1")
        self.assertEqual(self.run_phase("prepare", "705:1"), "queue_complete")
        self.assertEqual(self.run_phase("send", "705:1"), "queue_complete")
        self.assertEqual(self.wire.call_count, 4)

    def test_missing_or_corrupt_journal_never_restarts_the_queue(self):
        self.state_path.write_text("not JSON")
        with self.assertRaises(ValueError):
            self.run_phase("prepare")
        self.state_path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.run_phase("prepare")
        self.wire.assert_not_called()


if __name__ == "__main__":
    unittest.main()
