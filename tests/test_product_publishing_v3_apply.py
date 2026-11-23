"""Offline acceptance tests for the entire guarded v3 publishing lifecycle.

Every seller client is mocked; approval and journal failure test the safety
contract. No network, seller account or inventory files are modified.
"""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import product_intake_v2 as intake
from scripts import product_publishing_v3_apply as writer
from scripts import product_publishing_v3_adapters as adapters


class MockNewSeller:
    def __init__(self, name, *, existing=False, fail_submit=False, duplicate=False,
                 reject=False, missing_after_submit=False):
        self.name=name
        self.exists=existing
        self.fail_submit=fail_submit
        self.duplicate=duplicate
        self.reject=reject
        self.missing_after_submit=missing_after_submit
        self.calls={k:0 for k in ("scan", "existing", "prepare", "submit",
                                  "observe", "set_stock", "set_price", "set_media")}
        self.submissions=[]

    def count(self, action):
        self.calls[action]+=1

    def scan(self):
        self.count("scan")

    def existing(self, sku):
        self.count("existing")
        if self.duplicate:return [{"sku":sku},{"sku":sku}]
        if self.exists:return [{"sku":sku}]
        return []

    def prepare(self,item,urls,paths,*,stock):
        self.count("prepare")
        assert item["sku"] and urls and stock==2
        return {"offer_id":item["sku"], "price":item["prices"][self.name]}

    def submit(self,item,payload):
        self.count("submit")
        self.submissions.append(payload)
        if self.fail_submit:
            raise TimeoutError("HTTP request outcome unknown")
        self.exists = True
        return {"task_id":987,"submitted":True}

    def observe(self,item,submission):
        self.count("observe")
        if self.reject:return {"state":"rejected","errors":[{"code":"BAD_TYPE"}]}
        if self.missing_after_submit:return {"state":"not_created"}
        return {"state":"created" if self.exists else "not_created", "product_id":4354}

    def set_stock(self,item,submission,amount):
        self.count("set_stock")
        assert amount==2
        return {"stock":amount}

    def set_price(self,item,submission):
        self.count("set_price")
        return {"price":item["prices"][self.name]}

    def set_media(self,item,submission,urls):
        self.count("set_media")
        return {"images":len(urls)}


class GuardedPublishingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original=intake.read_json(
            ROOT/"data/product_intake_v2/requests/leoparts-five-20261123.json")

    def item_and_batch(self):
        batch=deepcopy(self.original)
        batch["batch_id"]="new-test-batch-101"
        item=deepcopy(batch["items"][3])
        item["sku"]="brand-new-test-tool-101"
        item["title"]="Набор инструментов для авто, проверенная комплектация"
        item["stock"]=2
        item["channels"]=["ozon","wildberries","yandex_market","tilda"]
        item["categories"]["ozon"]={
            "verified": True,"description_category_id":83250454,
            "type_id":971437067,"tnved_code":"8479899707",
            "customs_evidence":"Owner-approved technical classification document"}
        item["categories"]["wildberries"]={
            "verified":True,"subject_id":6370,"characteristics":[]}
        item["categories"]["yandex_market"]={
            "verified":True,"category_id":82184337}
        batch["items"]=[item]
        return batch

    def config(self,batch, *, mode="apply", channels=None):
        return {"schema_version":1,"mode":mode,"batch_id":batch["batch_id"],
                "channels":channels or ["ozon","wildberries","yandex_market","tilda","avito"],
                "apply_authorized":True,"allow_existing_changes":False,
                "approved_manifest_sha256":writer.fingerprint(batch)}

    def build(self,batch,config,adapters_map,folder,mode="apply"):
        return writer.Publisher(
            batch,config,mode=mode,adapter_map=adapters_map,
            state_path=Path(folder)/"state.json",git_checkpoint=False)

    def test_requires_signed_batch_and_existing_changes_banned(self):
        batch=self.item_and_batch()
        data=self.config(batch)
        for key,value in [("apply_authorized",False),
                          ("allow_existing_changes",True),
                          ("approved_manifest_sha256","a"*64),
                          ("mode","pilot")]:
            altered={**data,key:value}
            with tempfile.TemporaryDirectory() as d, self.assertRaises(writer.PublicationGuard):
                self.build(batch,altered,{"ozon":MockNewSeller("ozon")},d)

    def test_unapproved_modified_price_is_rejected_before_any_api_call(self):
        batch=self.item_and_batch()
        cfg=self.config(batch)
        batch["items"][0]["prices"]["ozon"]+=1
        seller=MockNewSeller("ozon")
        with tempfile.TemporaryDirectory() as d, self.assertRaises(writer.PublicationGuard):
            self.build(batch,cfg,{"ozon":seller},d)
        self.assertEqual(seller.calls["scan"],0)

    def test_new_skus_complete_once_with_verified_journal(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon","wildberries"])
        oz=MockNewSeller("ozon")
        wb=MockNewSeller("wildberries")
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch.object(writer,"verified_media",return_value=(["https://good.test/x.png"],[Path("image.png")])),\
                 patch.object(writer.Publisher,"_final_readback",return_value={"verified":True,"scope":"selling"}):
                p=self.build(batch,cfg,{"ozon":oz,"wildberries":wb},d)
                result=p.run()
                self.assertEqual(result["summary"]["done"],2)
                self.assertEqual(oz.calls["submit"],1)
                self.assertEqual(wb.calls["submit"],1)
                self.assertEqual(oz.calls["set_stock"],1)
                self.assertEqual(wb.calls["set_stock"],1)
                self.assertEqual(wb.calls["set_media"],1)
                self.assertEqual(wb.calls["set_price"],1)
                self.assertFalse(any(x["channel"]=="avito" for x in result["events"]))
                state=json.loads((Path(d)/"state.json").read_text(encoding="utf8"))
                self.assertTrue(all(x["state"]=="done" for x in state["tasks"].values()))
                p2=self.build(batch,cfg,{"ozon":oz,"wildberries":wb},d)
                self.assertEqual(p2.run()["summary"]["done"],2)
                self.assertEqual(oz.calls["submit"],1)
                self.assertEqual(wb.calls["set_stock"],1)

    def test_existing_stock_price_media_never_modified(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon",existing=True)
        with tempfile.TemporaryDirectory() as d:
            p=self.build(batch,cfg,{"ozon":seller},d)
            result=p.run()
        self.assertEqual(result["summary"]["existing_untouched"],1)
        self.assertEqual(seller.calls["submit"],0)
        for name in ("prepare","set_price","set_media","set_stock"):
            self.assertEqual(seller.calls[name],0)

    def test_timeout_creates_intent_and_never_resubmits_unknown_post(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon",fail_submit=True)
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch.object(writer,"verified_media",return_value=(["https://good.test/x.png"],[Path("image.png")])):
                first=self.build(batch,cfg,{"ozon":seller},d).run()
                self.assertEqual(seller.calls["submit"],1)
                self.assertEqual(first["events"][0]["state"],"create_outcome_unknown")
                row=json.loads((Path(d)/"state.json").read_text())["tasks"]
                self.assertTrue(next(iter(row.values()))["create_intent"])
                seller.fail_submit=False
                resumed=self.build(batch,cfg,{"ozon":seller},d,mode="resume").run()
                self.assertEqual(seller.calls["submit"],1)
                self.assertEqual(resumed["events"][0]["state"],"awaiting_platform")

    def test_confirmed_moderation_decline_never_resubmits(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon",reject=True)
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch.object(writer,"verified_media",return_value=(["https://good.test/x.png"],[Path("image.png")])):
                first=self.build(batch,cfg,{"ozon":seller},d).run()
                self.assertEqual(first["events"][0]["state"],"blocked_moderation")
                again=self.build(batch,cfg,{"ozon":seller},d,mode="resume").run()
                self.assertEqual(seller.calls["submit"],1)
                self.assertEqual(again["events"][0]["state"],"blocked_moderation")

    def test_duplicate_seller_listing_fails_closed(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon",duplicate=True)
        with tempfile.TemporaryDirectory() as d:
            result=self.build(batch,cfg,{"ozon":seller},d).run()
        self.assertEqual(result["events"][0]["state"],"read_error")
        self.assertEqual(seller.calls["submit"],0)

    def test_incorrect_stock_source_blocks_before_write(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon")
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",side_effect=ValueError("stock mismatch")):
                r=self.build(batch,cfg,{"ozon":seller},d).run()
        self.assertEqual(r["events"][0]["state"],"blocked_preflight")
        self.assertEqual(seller.calls["submit"],0)

    def test_one_channel_error_does_not_block_others(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon","wildberries"])
        bad=MockNewSeller("ozon")
        bad.scan=lambda: (_ for _ in ()).throw(RuntimeError("HTTP 503"))
        good=MockNewSeller("wildberries",existing=True)
        with tempfile.TemporaryDirectory() as d:
            result=self.build(batch,cfg,{"ozon":bad,"wildberries":good},d).run()
        self.assertEqual({x["state"] for x in result["events"]},
                         {"api_scan_error","existing_untouched"})
        self.assertEqual(good.calls["submit"],0)

    def test_pilot_existing_five_starts_no_price_stock_media_writes(self):
        cfg={"schema_version":1,"mode":"live","batch_id":self.original["batch_id"],
             "channels":["ozon","wildberries","yandex_market","tilda","avito"]}
        fake={name:MockNewSeller(name,existing=True) for name in writer.ACTIVE}
        with tempfile.TemporaryDirectory() as d:
            p=writer.Publisher(self.original,cfg,mode="pilot",adapter_map=fake,
                               state_path=Path(d)/"state.json")
            report=p.run()
            self.assertEqual(report["summary"]["existing_untouched"],20)
            self.assertEqual(report["summary"]["writes_enabled"],False)
            self.assertFalse((Path(d)/"state.json").exists())
        self.assertTrue(all(x.calls["submit"]==0 for x in fake.values()))

    def test_media_not_approved_never_submits(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon")
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch.object(writer,"verified_media",side_effect=ValueError("SHA mismatch")):
                res=self.build(batch,cfg,{"ozon":seller},d).run()
        self.assertEqual(res["events"][0]["state"],"blocked_preflight")
        self.assertEqual(seller.calls["submit"],0)

    def test_pilot_for_new_sku_never_uploads_media_or_changes_catalog(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,mode="live",channels=["ozon"])
        seller=MockNewSeller("ozon")
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch("photo_host.upload_file",side_effect=AssertionError("Pilot uploaded Release media")):
                p=self.build(batch,cfg,{"ozon":seller},d,mode="pilot")
                report=p.run()
                self.assertEqual(report["events"][0]["state"],"ready_to_create")
                self.assertFalse((Path(d)/"state.json").exists())
                self.assertEqual(seller.calls["submit"],0)
                self.assertEqual(seller.calls["set_stock"],0)

    def test_adapter_ozon_requires_customs_without_api_call(self):
        batch=self.item_and_batch(); item=batch["items"][0]
        oz=adapters.OzonAdapter.__new__(adapters.OzonAdapter)
        oz.api=None; oz.required={}; oz.customs={}
        for code,evidence in [(None,"approved"),("notvalid","proof"),("8479899707",None)]:
            cfg=item["categories"]["ozon"]
            cfg["tnved_code"]=code;cfg["customs_evidence"]=evidence
            with self.assertRaises(adapters.AdapterGuard):
                oz.prepare(item,["https://example.test/approved.png"],[],stock=2)

    def test_tilda_requires_credentials_before_submit(self):
        batch=self.item_and_batch()
        item=batch["items"][0]
        for key in ("TILDA_COMMERCEML_URL","TILDA_COMMERCEML_USER","TILDA_COMMERCEML_PASSWORD"):
            os.environ.pop(key,None)
        til=adapters.TildaAdapter.__new__(adapters.TildaAdapter)
        with self.assertRaises(adapters.AdapterGuard):
            til.prepare(item,["https://a.test/img.png"],[Path("a.png")],stock=2)


    def test_git_checkpoint_failure_blocks_seller_create(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon")
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch.object(writer,"verified_media",return_value=(["https://good.test/img.png"],[Path("image.png")])),\
                 patch.dict(os.environ,{"GITHUB_ACTIONS":"true"}),\
                 patch.object(writer.subprocess,"run",side_effect=RuntimeError("git push failed")):
                p=writer.Publisher(batch,cfg,mode="apply",adapter_map={"ozon":seller},
                                   state_path=Path(d)/"state.json",git_checkpoint=True)
                response=p.run()
                self.assertEqual(response["events"][0]["state"],"blocked_error")
                self.assertEqual(seller.calls["submit"],0)
        self.assertEqual(seller.calls["set_stock"],0)

    def test_unknown_wb_price_side_effect_not_replayed(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["wildberries"])
        seller=MockNewSeller("wildberries")
        failures=[0]
        def uncertain(*args):
            failures[0]+=1
            raise TimeoutError("WB price API timeout")
        seller.set_price=uncertain
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch.object(writer,"verified_media",return_value=(["https://good.test/img.png"],[Path("image.png")])),\
                 patch.object(writer.Publisher,"_final_readback",return_value={"verified":False,"scope":"not_verified"}):
                first=self.build(batch,cfg,{"wildberries":seller},d).run()
                self.assertEqual(first["events"][0]["state"],"uncertain_price")
                self.assertEqual(failures[0],1)
                again=self.build(batch,cfg,{"wildberries":seller},d,mode="resume").run()
                self.assertEqual(again["events"][0]["state"],"needs_review")
                self.assertEqual(failures[0],1)
                self.assertEqual(seller.calls["submit"],1)
                self.assertEqual(seller.calls["set_stock"],0)

    def test_state_fingerprint_prevents_manifest_revision_reuse(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon")
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch.object(writer,"verified_media",return_value=(["https://good.test/img.png"],[Path("image.png")])),\
                 patch.object(writer.Publisher,"_final_readback",return_value={"verified":True,"scope":"selling"}):
                self.build(batch,cfg,{"ozon":seller},d).run()
                changed=deepcopy(batch)
                changed["items"][0]["prices"]["ozon"]+=100
                newcfg=self.config(changed,channels=["ozon"])
                with self.assertRaises(writer.PublicationGuard):
                    self.build(changed,newcfg,{"ozon":seller},d)
                self.assertEqual(seller.calls["submit"],1)

    def test_restore_unknown_create_after_card_appears_never_recreates(self):
        batch=self.item_and_batch()
        cfg=self.config(batch,channels=["ozon"])
        seller=MockNewSeller("ozon",fail_submit=True)
        with tempfile.TemporaryDirectory() as d:
            with patch.object(writer,"catalog_stock",return_value=2),\
                 patch.object(writer,"verified_media",return_value=(["https://good.test/img.png"],[Path("image.png")])):
                self.build(batch,cfg,{"ozon":seller},d).run()
                seller.fail_submit=False
                seller.exists=True  # external platform eventually applied timed-out POST
                second=self.build(batch,cfg,{"ozon":seller},d,mode="resume").run()
                self.assertEqual(seller.calls["submit"],1)
                # Unconfirmed task identity means the engine will never change
                # price, photo or stock from this ambiguous initial write.
                self.assertEqual(second["events"][0]["state"],"uncertain_created_identity")
                self.assertEqual(seller.calls["set_stock"],0)

if __name__=="__main__":
    unittest.main()
