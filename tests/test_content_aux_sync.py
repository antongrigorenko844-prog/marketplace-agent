import unittest
from unittest.mock import patch
from io import BytesIO
from openpyxl import Workbook,load_workbook
import content_aux_sync as aux


class AuxContentTests(unittest.TestCase):
    def feed(self):
        wb=Workbook();ws=wb.active;ws.title='Объявления'
        for i,sku,aid in [(5,'A',123),(6,'B',456)]:
            for col,val in [(4,sku),(9,aid),(17,'old '+sku),(19,'https://images.test/'+sku),
                            (24,'title '+sku),(34,1500),(41,'Новое')]:
                ws.cell(i,col,val)
        buf=BytesIO();wb.save(buf);return buf.getvalue()

    def test_exact_avito_feed_patch_only_description(self):
        raw=self.feed()
        rules={'A':{'ids':{'avito':123},'text':{'avito':{'description':'approved A'}}},
               'B':{'ids':{'avito':456},'text':{'avito':{'description':'approved B'}}}}
        report=[{'ad_id':'A','avito_id':123},{'ad_id':'B','avito_id':456}]
        live=[{'id':123,'status':'active'},{'id':456,'status':'active'}]
        with patch.object(aux.exchange_items,'avito_note',return_value=''):
            staged,rows=aux.patch_avito_feed(raw,rules,['A'],report,live)
        before=load_workbook(BytesIO(raw)).active
        after=load_workbook(BytesIO(staged)).active
        self.assertEqual(after['Q5'].value,'approved A')
        self.assertEqual(after['Q6'].value,'old B')
        for row in before:
            for cell in row:
                if cell.coordinate!='Q5':
                    self.assertEqual(cell.value,after[cell.coordinate].value)
        self.assertFalse(rows['A']['description_verified'])
        self.assertTrue(rows['A']['needs_feed_change'])
        with patch.object(aux.exchange_items,'avito_note',return_value=''):
            same,_=aux.patch_avito_feed(staged,rules,['A'],report,live)
        self.assertEqual(same,staged)

    def test_avito_mismatched_id_is_blocked(self):
        raw=self.feed()
        rules={'A':{'ids':{'avito':999},'text':{'avito':{'description':'approved'}}}}
        staged,rows=aux.patch_avito_feed(raw,rules,['A'],[{'ad_id':'A','avito_id':123}],[{'id':123,'status':'active'}])
        self.assertEqual(staged,raw)
        self.assertIn('error',rows['A'])

    def test_tilda_csv_preserves_exact_identity_and_excludes_commercial_fields(self):
        rules={'A':{'ids':{'tilda':'123'},'text':{'tilda':{'description':'approved'}}}}
        records={'123':{'uid':'123','sku':'A','externalid':'ext-A','title':'title','text':'old','price':123}}
        with patch.object(aux.exchange_items,'site_prefix',return_value=''):
            result,rows=aux.tilda_rows(rules,['A'],records)
        self.assertEqual(result['A']['status'],'csv_import_required')
        self.assertEqual(rows[0]['Tilda UID'],'123')
        self.assertEqual(rows[0]['Title'],'title')
        self.assertNotIn('Price',rows[0])
        self.assertNotIn('Quantity',rows[0])
        records['123']['text']='approved';records['123']['descr']='approved'
        with patch.object(aux.exchange_items,'site_prefix',return_value=''):
            result,rows=aux.tilda_rows(rules,['A'],records)
        self.assertTrue(result['A']['description_verified'])
        self.assertEqual(rows,[])

    def test_tilda_case_only_and_duplicate_do_not_match(self):
        rules={'A':{'text':{'tilda':{'description':'approved'}}}}
        records={'123':{'uid':'123','sku':'a','externalid':'ext','title':'title','text':'old'}}
        result,rows=aux.tilda_rows(rules,['A'],records)
        self.assertIn('error',result['A'])
        self.assertEqual(rows,[])


    def test_ozon_diagnostic_distinguishes_order_from_value_change(self):
        from content_ozon_diagnosis import attribute_diff
        a={'attributes':[{'id':1,'values':[{'value':'a'},{'value':'b'}]},
                         {'id':2,'values':[{'value':'x'}]},
                         {'id':4191,'values':[{'value':'old'}]}]}
        b={'attributes':[{'id':1,'values':[{'value':'b'},{'value':'a'}]},
                         {'id':2,'values':[{'value':'y'}]},
                         {'id':4191,'values':[{'value':'new'}]}]}
        self.assertEqual({v['id']:v['kind'] for v in attribute_diff(a,b)},
                         {1:'order_only',2:'value_or_metadata_changed'})


    def test_tilda_canary_import_contains_only_existing_identity_and_text(self):
        import xml.etree.ElementTree as ET
        xml=aux.tilda_single_description_xml({'sku':'A','externalid':'A','title':'keep'},'new')
        root=ET.fromstring(xml)
        self.assertEqual(root.find('Каталог').attrib,{'СодержитТолькоИзменения':'true'})
        product=root.find('.//Товар')
        self.assertEqual({x.tag:x.text for x in product},
                         {'Ид':'A','Артикул':'A','Наименование':'keep','Описание':'new'})

    def test_tilda_canary_protects_other_products_and_commercial_fields(self):
        a={'1':{'uid':'1','price':100,'text':'old'},'2':{'uid':'2','price':200,'text':'other'}}
        b={'1':{'uid':'1','price':100,'text':'new'},'2':{'uid':'2','price':200,'text':'other'}}
        self.assertEqual(aux.tilda_protected_state(a,'1'),aux.tilda_protected_state(b,'1'))
        b['2']['text']='changed'
        self.assertNotEqual(aux.tilda_protected_state(a,'1'),aux.tilda_protected_state(b,'1'))
        b['2']['text']='other';b['1']['price']=1
        self.assertNotEqual(aux.tilda_protected_state(a,'1'),aux.tilda_protected_state(b,'1'))


    def test_avito_v4_report_freshness_is_not_live_text_proof(self):
        row={'feed_verified':True,'description_verified':False,'autoload_section':'success_skipped',
             'autoload_status':'active','autoload_messages':[]}
        policy={'avito':{'feed_sha256':'abc','await_report_started_after_utc':'2026-11-24T01:15:52Z'}}
        self.assertFalse(aux.avito_import_evidence({'started_at':'2026-11-24T00:00:00Z'},{'A':row},'abc',policy))
        self.assertFalse(row['autoload_processed'])
        self.assertTrue(aux.avito_import_evidence({'started_at':'2026-11-24T02:00:00Z'},{'A':row},'abc',policy))
        self.assertTrue(row['autoload_processed'])
        self.assertFalse(row['description_verified'])
        self.assertFalse(aux.avito_import_evidence({'started_at':'2026-11-24T02:00:00Z'},{'A':row},'other',policy))

    def test_avito_v4_report_identity_checked_twice(self):
        from unittest.mock import Mock
        api=Mock()
        last={'upload_id':42,'started_at':'2026-11-24T02:00:00Z','status':'success'}
        api._request.side_effect=[{'upload_id':43},last,{'items':[{'ad_id':'A','avito_id':1}],'meta':{'pages':1}},last]
        r=aux.read_avito_autoload(api)
        self.assertEqual(r['version'],'v4')
        self.assertEqual(r['items'][0]['ad_id'],'A')
        api.get_autoload_report_items.assert_not_called()


    def test_receipt_only_applies_to_exact_current_feed(self):
        policy={'avito':{'feed_sha256':'old'}}
        receipt={'feed_sha256':'new','await_report_started_after_utc':'2026-11-24T02:00:00Z'}
        self.assertEqual(aux.current_avito_policy(policy,receipt,'new')['avito'],receipt)
        self.assertEqual(aux.current_avito_policy(policy,receipt,'old'),policy)
        with self.assertRaises(ValueError):
            aux.avito_publication_receipt({'status':'unknown','sha256':'new'},{})
        r=aux.avito_publication_receipt({'status':'published_sha_verified','sha256':'new'},
             {'A':{'needs_feed_change':True,'avito_id':'123','feed_id':'A'},
              'B':{'needs_feed_change':False}})
        self.assertEqual(r['feed_sha256'],'new')
        self.assertEqual(set(r['items']),{'A'})
        self.assertFalse(r['live_text_verified'])
