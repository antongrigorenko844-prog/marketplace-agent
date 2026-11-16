import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import requests
import openpyxl

import content_manifest as cm
import content_transport as ct


class ContentBoundaryTests(unittest.TestCase):
    def test_market_category_migration_rejects_unreviewed_parameter_loss(self):
        card={'mapping':{'marketCategoryId':10},'parameterValues':[{'parameterId':1,'value':'keep'},{'parameterId':2,'value':'filter-only'}]}
        defs={'20':{'parameters':[{'id':1,'type':'TEXT','allowCustomValues':True},{'id':3,'type':'NUMERIC','allowCustomValues':True,'unit':{'units':[{'id':9}]},'constraints':{'minValue':1,'maxValue':100}}]}}
        plan={'category':20,'allowed_previous_categories':[10],'set':[{'parameterId':3,'unitId':9,'value':'7'}]}
        with self.assertRaises(ValueError):cm.market_parameters_for(card,plan,defs)
        plan['remove']=[2]
        cat,values=cm.market_parameters_for(card,plan,defs)
        self.assertEqual(cat,20);self.assertEqual(values[0],card['parameterValues'][0])
        current={'mapping':{'marketCategoryId':20},'parameterValues':values}
        self.assertEqual(cm.market_parameters_for(current,plan,defs),(cat,values))
        plan['set'][0]['unitId']=99
        with self.assertRaises(ValueError):cm.market_parameters_for(card,plan,defs)

    def test_content_sources_override_stale_text_without_changing_other_skus(self):
        import content_updates as cu
        rules={'PART':{'text':{'avito':{'title':'Reviewed','description':'Reviewed text'}}}}
        with patch.object(cm,'load',return_value=rules):
            self.assertEqual(cu.description_for('PART','stale','avito'),'Reviewed text')
            self.assertEqual(cu.title_for('PART','stale','avito'),'Reviewed')
            self.assertEqual(cu.title_for('OTHER','untouched','avito'),'untouched')

    def test_commerceml_import_is_not_misclassified_as_safe_get(self):
        with self.assertRaises(ValueError):ct.classify('GET','https://store.tilda.ru/connector?mode=import&filename=offers.xml')

    def test_rebuilding_both_catalogs_from_stale_api_preserves_approved_slides(self):
        import catalog_editor as oz,wb_catalog_editor as wb
        urls=['https://images.test/cover.png','https://images.test/approved.png']
        rules={'PART':{'sources':{'ozon':urls,'wb':urls}}}
        with tempfile.TemporaryDirectory() as tmp,patch.object(cm,'load',return_value=rules),patch.object(oz,'_get_tnved_attr_id',return_value=None),patch.object(oz,'_get_hashtags_attr_id',return_value=None):
            op=Path(tmp)/'oz.xlsx';wp=Path(tmp)/'wb.xlsx'
            oz.build_ozon_catalog({'names':[{'offer_id':'PART','name':'Existing','primary_image':urls[0],'images':[],'attributes':[]}],'details':[]},str(op))
            wb.build_wb_catalog({'cards':[{'vendorCode':'PART','nmID':1,'title':'Existing','description':'Text','photos':[{'big':urls[0]}]}],'prices':[]},str(wp))
            for path,column in [(op,6),(wp,4)]:
                book=openpyxl.load_workbook(path,read_only=True)
                self.assertEqual(book.active.cell(2,column).value,'|'.join(urls))
                book.close()

    def test_market_comparison_uses_returned_public_cdn_without_rewriting_offer(self):
        import content_sync as cs
        offer={'pictures':['https://datacamp.market.yandex.ru/pictures/abc'], 'mediaFiles':{'pictures':[{'url':'https://datacamp.market.yandex.ru/pictures/abc','marketUrl':'https://avatars.mds.yandex.net/photo/orig'}]}}
        original=copy.deepcopy(offer)
        with patch.dict(cs.PICTURE_ALIASES, {}, clear=True):
            cs.bind_market_images(offer)
            self.assertEqual(cs.PICTURE_ALIASES[offer['pictures'][0]],'https://avatars.mds.yandex.net/photo/orig')
        self.assertEqual(offer,original)

    def test_commercial_endpoints_rejected_even_with_innocent_name(self):
        endpoints=[
            ('POST','https://api-seller.ozon.ru/v1/product/import/prices'),
            ('POST','https://api-seller.ozon.ru/v2/products/stocks'),
            ('POST','https://api-seller.ozon.ru/v3/product/import'),
            ('PUT','https://marketplace-api.wildberries.ru/api/v3/stocks/1'),
            ('POST','https://discounts-prices-api.wildberries.ru/api/v2/upload/task'),
            ('POST','https://api.partner.market.yandex.ru/v2/campaigns/1/offers/stocks'),
            ('POST','https://api.partner.market.yandex.ru/v2/businesses/1/offer-prices/updates'),
            ('POST','https://example.test/unknown'),
        ]
        for method,url in endpoints:
            with self.subTest(url=url),self.assertRaises(ValueError):ct.classify(method,url,{})

    def test_commercial_field_cannot_be_hidden_in_media_payload(self):
        for key in ['price','quantity','old_price','discount','stocks','basicPrice']:
            with self.subTest(key=key),self.assertRaises(ValueError):
                ct.classify('POST','https://api.partner.market.yandex.ru/v2/businesses/1/offer-mappings/update',{'offerMappings':[{'offer':{'offerId':'A',key:123}}]})
        with self.assertRaises(ValueError):
            ct.classify('POST','https://content-api.wildberries.ru/content/v2/cards/update',[{'nmID':1,'sizes':[{'price':100}]}])

    def test_guard_blocks_write_before_network_in_inspection(self):
        with patch.object(requests.sessions.Session,'request') as network:
            with ct.guard([],allow_write=False),self.assertRaises(ValueError):
                requests.post('https://content-api.wildberries.ru/content/v3/media/save',json={'nmId':1,'data':['https://img/a']})
            network.assert_not_called()

    def test_content_write_is_audited(self):
        calls=[]
        with patch.object(requests.sessions.Session,'request',return_value='OK') as network:
            with ct.guard(calls,allow_write=True):
                r=requests.post('https://content-api.wildberries.ru/content/v3/media/save',json={'nmId':1,'data':['https://img/a']})
            self.assertEqual(r,'OK');self.assertEqual(len(calls),1);self.assertEqual(network.call_count,1)

    def test_photo_retry_preserves_cover_and_tail_without_duplicate(self):
        original=['https://img/cover','https://img/details','https://img/other']
        first=cm.insert_second(original,'https://img/approved?v=1')
        self.assertEqual(first,[original[0],'https://img/approved?v=1',*original[1:]])
        self.assertEqual(cm.insert_second(first,'https://img/approved?v=2'),first)
        self.assertEqual(original,['https://img/cover','https://img/details','https://img/other'])

    def test_same_slide_at_end_is_moved_without_losing_tail(self):
        self.assertEqual(cm.insert_second(['https://i/cover','https://i/detail','https://i/slide'],'https://i/slide'),['https://i/cover','https://i/slide','https://i/detail'])
        with self.assertRaises(ValueError):cm.insert_second(['https://i/slide'],'https://i/slide')

    def test_exact_sku_and_explicit_sources(self):
        rules={'SKU':{'sources':{'ozon':['https://i/cover','https://i/slide']}}}
        self.assertEqual(cm.gallery_for('SKU',['https://i/old'],'ozon',rules),rules['SKU']['sources']['ozon'])
        self.assertEqual(cm.gallery_for('sku',['https://i/old'],'ozon',rules),['https://i/old'])

    def test_market_update_preserves_typed_parameters_without_prices(self):
        offer={'offerId':'A','basicPrice':{'value':100},'pictures':['https://i/cover']}
        card={'parameterValues':[{'parameterId':1,'value':'x','unitId':2}], 'mapping':{'marketCategoryId':10}}
        payload=cm.market_offer_payload(offer,card,pictures=['https://i/cover','https://i/slide'])
        self.assertEqual(payload['offer']['parameterValues'],card['parameterValues'])
        self.assertIsNot(payload['offer']['parameterValues'],card['parameterValues'])
        self.assertNotIn('basicPrice',payload['offer'])
        self.assertEqual(ct.classify('POST','https://api.partner.market.yandex.ru/v2/businesses/1/offer-mappings/update',{'offerMappings':[payload]}),'content')

    def test_old_photo_and_card_entrypoints_use_only_safe_pipeline(self):
        import main,content_sync
        with patch.object(content_sync,'run',return_value=0) as safe,patch.object(main,'_cmd_update_cards_impl',side_effect=AssertionError('broad workflow reached')):
            for fn in [main.cmd_update_cards,main.cmd_push_photos,main.cmd_update_photos]:self.assertEqual(fn(),0)
        self.assertEqual(safe.call_count,3)

    def test_compatibility_commands_honor_exact_article_scope(self):
        import main,content_sync
        with patch.object(content_sync,'run',return_value=0) as safe:
            main.cmd_update_cards('6t30, stalnaya-vstavka-plity')
        safe.assert_called_once_with(mode='apply',only=['6t30','stalnaya-vstavka-plity'])

    def test_upload_failure_does_not_save_partial_gallery(self):
        import catalog_editor as ce,photo_host
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'catalog.xlsx';wb=openpyxl.Workbook();ws=wb.active
            ws.append([label for _,label in ce.COLUMNS]);row=[None]*len(ce.COLUMNS);row[0]='TEST';row[5]='https://i/cover|https://i/details';ws.append(row);wb.save(path)
            original=path.read_bytes()
            with patch.object(cm,'load',return_value={}),patch.object(ce,'own_media_files',side_effect=[['TEST_1.png','TEST_2.png']]),patch.object(photo_host,'upload_file',side_effect=['https://i/new',RuntimeError('network failure')]):
                with self.assertRaises(RuntimeError):ce.attach_local_photos(str(path),tmp)
            self.assertEqual(path.read_bytes(),original)

    def test_manifest_rejects_commercial_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/'manifest.json';p.write_text(json.dumps({'version':1,'products':{'A':{'price':10}}}))
            with self.assertRaises(ValueError):cm.load(p)

    def test_wb_video_is_preserved_when_reordering_gallery(self):
        import content_sync as cs,wb_client as wb
        before={'vendorCode':'A','nmID':1,'photos':[{'big':'https://i/cover'},{'big':'https://i/old'}],'video':'https://i/video.mp4','title':'t','description':'d','brand':'b','subjectID':2,'dimensions':{},'sizes':[],'characteristics':[]}
        state=copy.deepcopy(before);writes=[]
        def update(nmid,urls):
            writes.append(urls);state['photos']=[{'big':u} for u in urls if not u.endswith('.mp4')];return {'error':False}
        with patch.object(cs,'same_picture',side_effect=lambda a,b:a==b),patch.object(wb,'update_media',side_effect=update),patch.object(wb,'list_cards',side_effect=lambda:[copy.deepcopy(state)]),patch.object(cs.time,'sleep'):
            rule={'second_slide':{'url':'https://i/slide'},'ids':{'wb':1}}
            result=cs.apply_wb('A',rule,{'cards':[before]})
            retry=cs.apply_wb('A',rule,{'cards':[copy.deepcopy(state)]})
        self.assertTrue(result['verified']);self.assertFalse(retry['changed']);self.assertEqual(len(writes),1)
        self.assertEqual(writes[0],['https://i/cover','https://i/slide','https://i/old','https://i/video.mp4'])


if __name__=='__main__':unittest.main()
