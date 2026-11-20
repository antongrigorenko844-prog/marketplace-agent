from concurrent.futures import ThreadPoolExecutor
import html
from pathlib import Path
import re
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock

from designer_card_tasks import CardTaskError, CardTasks, REGISTRY, expected_submission, list_tasks, submission_ref


class CardTasksTests(unittest.TestCase):
    def setUp(self):
        self.temp=TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        (self.root/"photos/Part-01").mkdir(parents=True)
        (self.root/"photos/0am325025").mkdir()
        self.store=CardTasks(self.root)
        self.calls=[]
        self.tg=self.telegram

    def telegram(self,method,**kwargs):
        if method=="sendDocument":
            file=kwargs["files"]["document"][1]
            self.calls.append((method,kwargs["chat_id"],file.read()))
        else:
            self.calls.append((method,kwargs["chat_id"],kwargs["text"]))
        return {"message_id":len(self.calls)}

    def flush(self,**kw):
        return self.store.flush(self.tg,designer=22,owner=11,**kw)

    def create(self,text="Правка Part-01 слайд 2 Ozon\nУвеличить текст\nТекст: Две детали",event=10):
        self.assertTrue(self.store.handle_text("owner",text,event))
        return list_tasks(self.root)[0]

    def upload(self,t,event=100,payload=b"\x89PNG\r\n\x1a\nfixture"):
        p=expected_submission(t["id"],t["version"],t["slide"],".png")
        (self.root/p).parent.mkdir(parents=True,exist_ok=True)
        (self.root/p).write_bytes(payload)
        return self.store.submit("designer",t["id"],t["version"],event,p)

    def test_empty_read_only_help_and_both_roles(self):
        self.assertEqual(list_tasks(self.root),[])
        self.assertFalse((self.root/REGISTRY).exists())
        self.assertFalse(self.store.handle_text("visitor","Правки",1))
        self.assertFalse(self.store.handle_text("owner","План",1))
        self.store.handle_text("owner","Правки",1)
        self.store.handle_text("designer","Помощь по карточкам",2)
        self.flush()
        self.assertTrue(any(cid==11 and "Правка ТОЧНЫЙ" in msg for _,cid,msg in self.calls))
        self.assertTrue(any(cid==22 and "Сдать ID" in msg for _,cid,msg in self.calls))

    def test_exact_case_platform_validation_no_partial_changes(self):
        for i,text in enumerate(("Правка part-01 слайд 2 Ozon\nТЗ", "Правка ../Part-01 слайд 1\nТЗ",
                                 "Правка Part-01 слайд 0\nТЗ","Правка Part-01 слайд 1 чужая\nТЗ",
                                 "Правка Part-01 слайд 2\nСрок: 29.02.2026\nТЗ")):
            self.assertTrue(self.store.handle_text("owner",text,i))
        self.assertEqual(list_tasks(self.root),[])
        self.flush()
        self.assertEqual(len(self.calls),5)
        self.assertEqual({x[1] for x in self.calls},{11})

    def test_full_escaped_copy_and_checkpoint_per_block(self):
        value=("Деталь <&>🙂\n"*900)+" хвост  \n"
        created=self.create("Правка Part-01 слайд 5 Яндекс Маркет\nТЗ: Изменить подпись."
                            "\nСрок: 2027-03-02\nТекст:\n"+value)
        self.assertEqual(created["copy_text"],value)
        self.assertEqual(created["deadline"],"2027-03-02")
        checkpoint=Mock()
        self.flush(checkpoint=checkpoint)
        blocks=[msg for _,cid,msg in self.calls if cid==22 and "<pre>" in msg]
        self.assertGreater(len(blocks),1)
        self.assertTrue(all(len(m.encode("utf-16-le"))//2<4096 for m in blocks))
        self.assertEqual("".join(html.unescape(re.search(r"<pre>(.*)</pre>",m,re.S)[1]) for m in blocks),value)
        self.assertTrue(self.store.get(created["id"])["sent_at"])
        self.assertEqual(checkpoint.call_count,2*len(self.calls))
        self.assertEqual(self.flush()["sent"],0)

    def test_command_replay_and_changed_payload_same_event(self):
        t=self.create()
        self.create()
        self.assertEqual(len(list_tasks(self.root)),1)
        with self.assertRaisesRegex(CardTaskError,"Повтор события"):
            self.store.handle_text("owner","Мои правки",10)
        self.flush()
        self.assertTrue(self.store.handle_text("owner","Правка Part-01 слайд 2 Ozon\nУвеличить текст\nТекст: Две детали",10))
        self.assertEqual(self.flush()["sent"],0)
        self.assertEqual(self.store.get(t["id"])["version"],1)

    def test_edit_then_stale_notification_suppression_and_role(self):
        t=self.create()
        self.store.handle_text("designer","Изменить "+t["id"]+" v1\nНельзя",11)
        self.store.handle_text("owner","Изменить "+t["id"]+" v1\nДругая правка\nТекст: новый вариант",12)
        self.assertEqual(self.store.get(t["id"])["version"],2)
        self.store.handle_text("owner","Изменить "+t["id"]+" v1\nСтарая",13)
        self.flush()
        self.assertFalse(any("Увеличить текст" in m for _,who,m in self.calls if who==22))
        self.assertTrue(any("Устаревшая" in m for _,who,m in self.calls if who==11))
        self.assertTrue(any("может владелец" in m for _,who,m in self.calls if who==22))

    def test_submission_path_integrity_decisions_and_version_replay(self):
        t=self.create()
        key=t["id"]
        self.assertEqual(submission_ref(f"Сдать {key} v1"),(key,1))
        self.assertIsNone(submission_ref(f"Сдать {key}"))
        with self.assertRaises(CardTaskError):
            self.store.submit("owner",key,1,22,"anything")
        for path in ("photos/Part-01/Part-01_2.png",expected_submission(key,2,2,"png"),
                     expected_submission(key,1,1,"jpg")):
            with self.assertRaises(CardTaskError):
                self.store.submit("designer",key,1,23,path)
        self.flush()
        submitted=self.upload(t)
        self.store.submit("designer",key,1,100,submitted["artifact"])
        self.assertEqual(self.store.get(key)["status"],"submitted")
        self.flush()
        self.assertEqual(sum(m=="sendDocument" and c==11 for m,c,_ in self.calls),1)
        self.store.handle_text("designer","Принять "+key+" v1",201)
        self.assertEqual(self.store.get(key)["status"],"submitted")
        self.store.handle_text("owner","Вернуть "+key+" v1\nИсправить отступ",202)
        returned=self.store.get(key)
        self.assertEqual((returned["status"],returned["version"]),("returned",2))
        with self.assertRaisesRegex(CardTaskError,"Устаревшая"):
            self.store.submit("designer",key,1,203,submitted["artifact"])
        v2=self.upload(returned,event=205)
        self.store.handle_text("owner","Принять "+key+" v1",206)
        self.assertEqual(self.store.get(key)["status"],"submitted")
        self.store.handle_text("owner","Принять "+key+" v2",207)
        self.store.handle_text("owner","Принять "+key+" v2",207)
        self.assertEqual(self.store.get(key)["status"],"accepted")
        self.assertTrue(self.store.get(key)["accepted_at"])
        self.assertEqual(self.store.get(key)["artifact"],v2["artifact"])
        self.assertFalse((self.root/"photos/Part-01/Part-01_2.png").exists())

    def test_rejects_wrong_file_or_tampering_after_submit(self):
        t=self.create()
        with self.assertRaisesRegex(CardTaskError,"Содержимое"):
            self.upload(t,payload=b"not an image")
        s=self.upload(t)
        (self.root/s["artifact"]).write_bytes(b"\x89PNG\r\n\x1a\ntampered")
        self.store.handle_text("owner","Принять "+t["id"]+" v1",515)
        self.assertEqual(self.store.get(t["id"])["status"],"submitted")

    def test_ambiguous_send_no_duplicate_manual_reopen_and_unrelated_work(self):
        first=self.create()
        second=self.store.ingest_outbox("independent.json",{"sku":"Part-01","slide":1,"brief":"Другая работа"})
        failed=[]
        def send(method,**kw):
            if kw["chat_id"]==22 and first["id"] in kw.get("text","") and not failed:
                failed.append(True)
                raise TimeoutError("fixture")
            return self.telegram(method,**kw)
        self.tg=send
        self.assertEqual(self.flush()["uncertain"],1)
        self.assertTrue(self.store.get(second["id"])["sent_at"])
        self.assertEqual(self.flush()["sent"],0)
        self.store.handle_text("designer","Задача "+first["id"],990)
        self.flush()
        self.assertTrue(any(cid==22 and "Две детали" in msg for _,cid,msg in self.calls))

    def test_pre_send_checkpoint_failure_sends_nothing(self):
        self.create()
        with self.assertRaises(OSError):
            self.flush(checkpoint=Mock(side_effect=OSError("test")))
        self.assertEqual(self.calls,[])
        self.assertGreater(self.flush()["sent"],0)

    def test_outbox_and_legacy_no_implicit_migration(self):
        fields={"sku":"Part-01","slide":3,"platform":"Avito","brief":"Изменить размер"}
        a=self.store.ingest_outbox("one.json",fields)
        self.assertEqual(a["id"],self.store.ingest_outbox("one.json",fields)["id"])
        self.flush()
        self.assertEqual(len(list_tasks(self.root)),1)
        legacy=self.store.adopt_legacy_ozon()
        self.assertEqual(legacy["id"],self.store.adopt_legacy_ozon()["id"])
        self.assertEqual((legacy["sku"],legacy["slide"],legacy["platform"]),("0am325025",2,"Ozon"))
        self.assertEqual(len(legacy["copy_text"].splitlines()),3)
        self.assertEqual(self.flush()["sent"],0)
        serialized=(self.root/REGISTRY).read_text()
        self.assertNotIn("chat_id",serialized)
        self.assertNotIn("message_id",serialized)

    def test_exact_sources_and_reviewed_file_links(self):
        sku="Деталь с пробелом"
        for folder in ("photos","photos_avito"):
            (self.root/folder/sku).mkdir(parents=True)
        t=self.store.ingest_outbox("link.json",{"sku":sku,"slide":2,"platform":"Авито","brief":"Сверить фото"})
        self.assertEqual(t["source_folder"],"photos_avito/"+sku)
        self.flush()
        self.assertTrue(any("/tree/main/photos_avito/%D0" in msg and "%20" in msg for _,_,msg in self.calls))
        s=self.upload(t)
        self.store.handle_text("owner","Задача "+t["id"]+" v1",110)
        self.flush()
        self.assertTrue(any(kind=="sendMessage" and "/blob/main/"+s["artifact"] in msg
                            for kind,cid,msg in self.calls if cid==11))

    def test_concurrent_same_target_returns_existing_task_and_skips_superseded_version(self):
        def assign(n):
            store=CardTasks(self.root)
            return store.ingest_outbox(str(n)+".json",{"sku":"Part-01","slide":2,"platform":"Ozon",
                                                       "brief":"Первоначальный текст"})["id"]
        with ThreadPoolExecutor(max_workers=2) as pool:
            keys=list(pool.map(assign,[1,2]))
        self.assertEqual(keys[0],keys[1])
        self.assertEqual(len(list_tasks(self.root)),1)
        self.store.handle_text("owner","Изменить "+keys[0]+" v1\nАктуальная редакция",55)
        self.flush()
        self.assertFalse(any(" v1" in text for _,_,text in self.calls))
        designer=[text for _,cid,text in self.calls if cid==22]
        self.assertFalse(any(" v1" in text for text in designer))
        self.assertTrue(any("Актуальная редакция" in text for text in designer))
        assign(3)
        self.flush()
        self.assertTrue(any("Изменить "+keys[0]+" v2" in text for _,cid,text in self.calls if cid==11))

    def test_explicit_platform_change_and_wrong_outbox_identity(self):
        first=self.create()
        key=first["id"]
        with self.assertRaisesRegex(CardTaskError,"SKU или слайд"):
            self.store.ingest_outbox("wrong.json",{"action":"edit","id":key,"version":1,"slide":99,"brief":"X"})
        self.store.handle_text("owner","Изменить "+key+" v1\nПлощадка: Avito\nНовый вариант",56)
        self.assertEqual(self.store.get(key)["platform"],"Avito")
        self.assertEqual(self.store.get(key)["version"],2)
        self.assertEqual(self.store.get(key)["copy_text"],"Две детали")
        self.store.ingest_outbox("other.json",{"sku":"Part-01","slide":2,"platform":"Ozon","brief":"Другой канал"})
        self.store.handle_text("owner","Изменить "+key+" v2\nПлощадка: Ozon\nТекст задания",57)
        self.assertEqual(self.store.get(key)["version"],2)

    def test_rejected_outbox_receipts_are_distinct_from_actual_work(self):
        self.store.outbox_problem("private/path/a.json","hash1","invalid_card_task")
        self.store.outbox_problem("a.json","hash1","invalid_card_task")
        self.store.outbox_problem("a.json","hash2","missing_attachment")
        self.store.outbox_problem("a.json","hash3","sensitive untrusted response")
        self.flush()
        self.assertEqual(len(self.calls),3)
        self.assertTrue(all(cid==11 and "a.json" in msg and "private/path" not in msg
                            and "sensitive" not in msg for _,cid,msg in self.calls))
        self.assertTrue(any("точный SKU" in m for _,_,m in self.calls))
        self.store.outbox_problem("a.json","hash1","invalid_card_task")
        self.assertEqual(self.flush()["sent"],0)
        self.assertEqual(list_tasks(self.root),[])
        created=self.store.ingest_outbox("a.json",{"sku":"Part-01","slide":2,"brief":"Проверить слайд"})
        self.assertEqual(created["status"],"assigned")
        self.assertGreater(self.flush()["sent"],0)

    def test_source_from_canonical_then_returned_then_latest_approved_file(self):
        canonical=self.root/"photos/Part-01/Part-01_2.png"
        canonical.write_bytes(b"canonical")
        t=self.create()
        self.assertEqual(t["source_slide"],{"kind":"canonical","path":"photos/Part-01/Part-01_2.png"})
        self.flush()
        self.assertTrue(any(cid==22 and "/blob/main/photos/Part-01/Part-01_2.png" in msg
                            for _,cid,msg in self.calls))
        first=self.upload(t)
        self.store.handle_text("owner","Вернуть "+t["id"]+" v1\nСделать крупнее",701)
        returned=self.store.get(t["id"])
        self.assertEqual((returned["version"],returned["source_slide"]["path"]),(2,first["artifact"]))
        self.flush()
        self.assertTrue(any(kind=="sendMessage" and cid==22 and "/blob/main/"+first["artifact"] in msg
                            for kind,cid,msg in self.calls))
        second=self.upload(returned,event=702,payload=b"\x89PNG\r\n\x1a\nversion-two")
        self.store.handle_text("owner","Принять "+t["id"]+" v2",703)
        next_task=self.store.ingest_outbox("next.json",{"sku":"Part-01","slide":2,"platform":"Ozon","brief":"Новый вариант"})
        self.assertNotEqual(t["id"],next_task["id"])
        self.assertEqual(next_task["source_slide"]["kind"],"accepted")
        self.assertEqual(next_task["source_slide"]["path"],second["artifact"])
        # Another accepted task for the same slot must supersede the older one.
        new_file=self.upload(next_task,event=704,payload=b"\x89PNG\r\n\x1a\nthird")
        self.store.handle_text("owner","Принять "+next_task["id"]+" v1",705)
        last=self.store.ingest_outbox("last.json",{"sku":"Part-01","slide":2,"platform":"Ozon","brief":"Последняя"})
        self.assertEqual(last["source_slide"]["path"],new_file["artifact"])
        other=self.store.ingest_outbox("otherChannel.json",{"sku":"Part-01","slide":2,"platform":"WB","brief":"Для WB"})
        self.assertEqual(other["source_slide"]["kind"],"canonical")

    def test_ambiguous_and_similar_numbered_images_are_not_selected(self):
        folder=self.root/"photos/Part-01"
        for name in ("Part-01_2-edit.png","Part-01_02.png","Part-01_2_final.png","part-01_2.jpg"):
            (folder/name).write_bytes(b"other")
        t=self.create()
        self.assertNotIn("path",t["source_slide"])
        for suffix in (".jpg",".webp"):
            (folder/("Part-01_3"+suffix)).write_bytes(b"duplicate")
        other=self.store.ingest_outbox("dup.json",{"sku":"Part-01","slide":3,"brief":"Проверить"})
        self.assertNotIn("path",other["source_slide"])
        self.assertIn("несколько",other["source_slide"]["note"])


if __name__=="__main__":
    unittest.main()
