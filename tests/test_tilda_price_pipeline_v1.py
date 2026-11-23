"""Offline Tilda price-only pipeline safety tests. No API or real store writes."""
import unittest
from scripts import tilda_price_pipeline_v1 as pipe

def sample_rows():
    rows=[]
    for n in range(105):
        sku=f"TestSKU{n:03d}"
        uid=str(100000000000+n)
        rows.append({"uid":uid,"sku":sku,"externalid":sku,
                    "title":"Товар № "+str(n),"price":"950",
                    "editions":[{"sku":sku,"uid":uid,"price":"950"}],
                    "gallery":["keep_images"],"quantity":"23"})
    rows[10].update({"sku":"DQ500","externalid":"DQ500"})
    rows[10]["editions"][0]["sku"]="DQ500"
    rows[11].update({"sku":"Dq500","externalid":"Dq500"})
    rows[11]["editions"][0]["sku"]="Dq500"
    rows.append({"uid":"351198279983","sku":"0AM325120A",
                 "externalid":"vvhXIAeziJxJ7VCAPLJK",
                 "title":"Copy: Транспортная заглушка", "price":"500",
                 "editions":[{"uid":"351198279983","sku":"0AM325120A","price":"500"}]})
    return pipe.index_by_uid(rows)

def approval(uid="100000000010",sku="DQ500",old=950,new=980,external=None):
    return {"schema_version":1,"mode":"inspect","owner_approved":True,
            "channel":"site_only","preserve_avito":True,
            "preserve_marketplaces":True,"preserve_stock":True,
            "preserve_media":True,"preserve_titles":True,
            "preserve_catalog_products":True,
            "targets":[{"uid":uid,"sku":sku,
                        "externalid":external or sku,
                        "expected_price":old,"approved_price":new}]}

class TildaPricePipelineTests(unittest.TestCase):
    def test_owner_approved_exact_uid(self):
        live=sample_rows()
        plan=pipe.validate_manifest(approval(),live)
        self.assertEqual(plan["DQ500"]["uid"],"100000000010")
        self.assertEqual(plan["DQ500"]["new_price"],980)

    def test_case_sensitive_similar_skus_are_different(self):
        live=sample_rows()
        bad=approval(uid="100000000011",sku="DQ500")
        with self.assertRaises(pipe.SitePriceGuardError):
            pipe.validate_manifest(bad,live)
        good=approval(uid="100000000011",sku="Dq500")
        self.assertEqual(pipe.validate_manifest(good,live)["Dq500"]["uid"],"100000000011")

    def test_copy_cannot_be_price_synced_or_removed(self):
        live=sample_rows()
        m=approval(uid="351198279983",sku="0AM325120A",
                   old=500,new=950,external="vvhXIAeziJxJ7VCAPLJK")
        with self.assertRaisesRegex(pipe.SitePriceGuardError,"awaiting deletion"):
            pipe.validate_manifest(m,live)

    def test_external_id_guards_identity(self):
        live=sample_rows()
        with self.assertRaises(pipe.SitePriceGuardError):
            pipe.validate_manifest(approval(external="other"),live)

    def test_live_price_drift_refuses_any_apply(self):
        live=sample_rows()
        live["100000000010"]["price"]="1550"
        with self.assertRaises(pipe.SitePriceGuardError):
            pipe.validate_manifest(approval(),live)

    def test_multiple_variants_block_implicit_price_change(self):
        live=sample_rows()
        live["100000000010"]["editions"].append({"sku":"DQ500",
               "uid":"123","price":"950"})
        with self.assertRaises(pipe.SitePriceGuardError):
            pipe.validate_manifest(approval(),live)

    def test_fingerprint_ignores_only_changed_target_prices(self):
        first=sample_rows()
        new={k:dict(v) for k,v in first.items()}
        new["100000000010"]=dict(new["100000000010"])
        new["100000000010"]["price"]="980"
        new["100000000010"]["editions"]=[{"uid":"100000000010","sku":"DQ500","price":"980"}]
        target={"100000000010"}
        self.assertEqual(pipe.fingerprint(first,target),pipe.fingerprint(new,target))
        new["100000000011"]=dict(new["100000000011"])
        new["100000000011"]["price"]="200"
        self.assertNotEqual(pipe.fingerprint(first,target),pipe.fingerprint(new,target))

    def test_xml_does_not_contain_quantity_images_delete_other_channels(self):
        obj=pipe.validate_manifest(approval(),sample_rows())
        a,b=pipe.create_price_only_xml(obj)
        self.assertIn("<ЦенаЗаЕдиницу>980</ЦенаЗаЕдиницу>",b)
        self.assertIn("<Ид>DQ500</Ид>",a)
        for word in ("ПометкаУдаления","Количество","Картинка","Описание","9800"):
            self.assertNotIn("<"+word+">",a+b)

    def test_site_only_prices_keep_marketplace_and_avito_unmodified(self):
        obj=pipe.validate_manifest(approval(),sample_rows())
        result=pipe.verified_site_only_source({"0AM927769D":37500},obj)
        self.assertEqual(result["0AM927769D"],37500)
        self.assertEqual(result["DQ500"],980)
        self.assertEqual(len(result),2)

    def test_reject_unsafe_manifest_or_duplicate_sku(self):
        live=sample_rows()
        for flag in ("preserve_avito","preserve_stock","preserve_media",
                     "preserve_catalog_products","preserve_titles"):
            m=approval()
            m[flag]=False
            with self.subTest(flag=flag), self.assertRaises(pipe.SitePriceGuardError):
                pipe.validate_manifest(m,live)
        with self.assertRaises(pipe.SitePriceGuardError):
            pipe.validate_manifest(dict(approval(),targets=approval()["targets"]*2),live)

if __name__=="__main__":
    unittest.main()
