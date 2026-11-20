from contextlib import ExitStack, redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from PIL import Image

import designer_bot as bot
from designer_card_media import CardArchive
from designer_card_tasks import list_tasks
from designer_card_digest import render_list


class DesignerFlowTests(unittest.TestCase):
    def setUp(self):
        self.stack=ExitStack()
        self.addCleanup(self.stack.close)
        self.root=Path(self.stack.enter_context(TemporaryDirectory()))
        self.sku="0am325025"
        photos=self.root/"photos"
        (photos/self.sku).mkdir(parents=True)
        self.outbox=self.root/"data/outbox"
        self.outbox.mkdir(parents=True)
        for key,value in {"_ROOT":str(self.root),"PHOTOS_DIR":str(photos),
                          "AVITO_DIR":str(self.root/"photos_avito"),"STATE_PATH":str(self.root/"data/designer_state.json"),
                          "OUTBOX_DIR":str(self.outbox),"OUTBOX_DONE":str(self.root/"data/outbox_done.json")}.items():
            self.stack.enter_context(patch.object(bot,key,value))
        self.stack.enter_context(patch.object(bot,"_chat_id",return_value=10))
        self.stack.enter_context(patch.object(bot,"_owner_id",return_value=20))
        self.stack.enter_context(patch.object(bot,"_token",return_value="fake"))
        self.stack.enter_context(patch.object(bot,"persist_designer"))
        self.stack.enter_context(patch("socket.socket.connect",side_effect=AssertionError("network disabled")))
        self.sent=[]
        self.messages=[]
        self.next_id=10
        self.bytes=self.png(12,24)
        self.stack.enter_context(patch.object(bot,"_tg",side_effect=self.telegram))
        self.stack.enter_context(patch.object(bot,"_download",side_effect=lambda _:self.bytes))

    @staticmethod
    def png(w,h):
        out=BytesIO()
        Image.new("RGB",(w,h),(21,30,90)).save(out,format="PNG")
        return out.getvalue()

    def telegram(self,method,**data):
        if method=="getUpdates":
            return self.messages
        if method=="sendDocument":
            self.assertTrue(data["files"]["document"][1].read())
        self.sent.append((method,data))
        return {"message_id":1000+len(self.sent),"chat":{"id":data.get("chat_id")}}

    def collect(self,*rows):
        self.messages=[]
        for row in rows:
            self.next_id+=1
            self.messages.append({"update_id":self.next_id,"message":{"message_id":self.next_id,**row}})
        with redirect_stdout(StringIO()):
            self.assertEqual(bot.cmd_collect(),0)

    def command(self,who,text):
        self.collect({"chat":{"id":who},"text":text})

    def slide(self,caption,document=True,**kwargs):
        attach={"document":{"file_id":"f","mime_type":"image/png","file_name":"slide.png"}} if document else {"photo":[{"file_id":"f"}]}
        self.collect({"chat":{"id":10},"caption":caption,**attach,**kwargs})

    def test_issue_return_submit_accept_and_copy(self):
        self.command(20,"Правка 0am325025 слайд 2 Ozon\nУвеличить заголовок.\nТекст: A < B & C")
        task=list_tasks(self.root)[0]
        key=task["id"]
        self.assertTrue(any("A &lt; B &amp; C" in p.get("text","") and p["chat_id"]==10 for _,p in self.sent))
        self.assertIn(key," ".join(render_list(self.root)))
        self.slide(f"Сдать {key} v1")
        self.assertEqual(list_tasks(self.root)[0]["status"],"submitted")
        self.assertTrue(any(m=="sendDocument" and p["chat_id"]==20 for m,p in self.sent))
        self.command(20,f"Вернуть {key} v1\nПоднять надпись выше.")
        self.assertEqual(list_tasks(self.root)[0]["version"],2)
        self.slide(f"Сдать {key} v1")
        self.assertEqual(list_tasks(self.root)[0]["status"],"returned")
        self.slide(f"Сдать {key} v2",document=False)
        self.assertEqual(list_tasks(self.root)[0]["status"],"returned")
        self.slide("",reply_to_message={"from":{"is_bot":True},"text":f"Файл с подписью Сдать {key} v2."})
        self.command(10,f"Принять {key} v2")
        self.assertEqual(list_tasks(self.root)[0]["status"],"submitted")
        self.command(20,f"Принять {key} v2")
        t=list_tasks(self.root)[0]
        self.assertEqual(t["status"],"accepted")
        self.assertTrue((self.root/t["artifact"]).is_file())
        self.assertEqual(list((self.root/"photos"/self.sku).iterdir()),[])
        self.assertNotIn(key," ".join(render_list(self.root)))

    def test_legacy_format_survives_return_via_modern_command(self):
        self.bytes=self.png(900,1200)
        self.slide("Ozon 0am325025 слайд 2")
        t=list_tasks(self.root)[0]
        self.command(20,f"Вернуть {t['id']} v1\nДобавить отступ.")
        self.bytes=self.png(2,2)
        self.slide(f"Сдать {t['id']} v2")
        self.assertEqual(list_tasks(self.root)[0]["status"],"returned")
        self.assertIn("3:4",self.sent[-1][1]["text"])

    def test_outbox_correction_in_place_after_validation(self):
        file=self.outbox/"instruction.json"
        file.write_text(json.dumps({"card_task":{"sku":self.sku,"slide":2}}))
        self.collect()
        self.assertEqual(list_tasks(self.root),[])
        self.assertTrue(any(p.get("chat_id")==20 for _,p in self.sent))
        n=len(self.sent)
        self.collect()
        self.assertEqual(len(self.sent),n)
        file.write_text(json.dumps({"card_task":{"sku":self.sku,"slide":2,"platform":"Ozon","brief":"Заменить букву","copy_text":"Точное слово"}}))
        self.collect()
        key=list_tasks(self.root)[0]["id"]
        file.write_text(json.dumps({"card_task":{"action":"edit","id":key,"version":1,"brief":"Сместить вправо"}}))
        self.collect()
        t=list_tasks(self.root)[0]
        self.assertEqual((t["version"],t["copy_text"]),(2,"Точное слово"))
        n=len(self.sent)
        self.collect()
        self.assertEqual(n,len(self.sent))

    def test_social_delivery_exit_status(self):
        with patch.object(bot,"cmd_collect",return_value=0),patch.object(bot,"_plan_rows",return_value=[{"№":1}]),patch.object(bot,"_send_targets",return_value=1):
            self.assertEqual(bot.cmd_send(False,"1"),0)

    def test_task_reply_with_two_albums_preserves_every_position(self):
        self.command(20,"Правка 0am325025 слайд 2 Ozon\nИзменить обложку.")
        task=list_tasks(self.root)[0]
        raw=[]
        for i in range(16):
            row={"chat":{"id":10},"photo":[{"file_id":str(i)}],"media_group_id":"first" if i<10 else "second"}
            if i in (0,10):
                row["caption"]=f"Сдать {task['id']} v1"
            raw.append(row)
        self.collect(*raw)
        archive=CardArchive(self.root,"fake")
        self.assertEqual(sum(len(row["messages"]) for row in archive.groups.values()),16)
        self.assertTrue(all(row["target"]=={"sku":self.sku,"channel":"catalog"} for row in archive.groups.values()))
        self.assertEqual(list_tasks(self.root)[0]["status"],"assigned")

    def test_plain_note_and_long_text_are_delivered_complete(self):
        text="Обычная заметка " + "абв "*1500
        (self.outbox/"note.json").write_text(json.dumps({"to":"designer","text":text}))
        self.collect()
        sent=[data["text"] for _,data in self.sent if data.get("chat_id")==10]
        self.assertEqual("".join(sent),text)

    def test_link_cannot_switch_to_an_unconfirmed_chat(self):
        self.messages=[{"message":{"chat":{"id":444,"type":"private"}}}]
        with patch.dict("os.environ",{"DESIGNER_LINK_TARGET":""}),redirect_stdout(StringIO()):
            self.assertEqual(bot.cmd_link(),1)
        self.assertFalse(self.sent)

    def test_unknown_chats_do_not_send_card_commands(self):
        self.command(30,"Правка 0am325025 слайд 2\nИзменить заголовок.")
        self.assertFalse(self.sent)
        self.assertFalse(list_tasks(self.root))
