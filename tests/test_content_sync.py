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

    def test_description_only_projection_excludes_media_titles_and_attributes(self):
        import content_sync as cs
        rules = {'A': {'ids': {'wb': 42}, 'second_slide': {'url': 'https://example.org/a'},
                       'market_parameters': {'category': 1}, 'wb_characteristics': [{'id': 378533}],
                       'text': {'wb': {'title': 'old', 'description': 'approved'},
                                'ozon': {'composition': 'other'}, 'avito': {'description': 'feed'}}}}
        original = copy.deepcopy(rules)
        self.assertEqual(cs.description_rules(rules, ['A']),
                         {'A': {'ids': {'wb': 42}, 'text': {'wb': {'description': 'approved'}}}})
        self.assertEqual(rules, original)
        for scope in (None, [], ['A', 'A'], ['a']):
            with self.assertRaises(ValueError):
                cs.description_rules(rules, scope)


    def test_description_transport_rejects_non_description_writes(self):
        oz='https://api-seller.ozon.ru/v1/product/attributes/update'
        ym='https://api.partner.market.yandex.ru/v2/businesses/123/offer-mappings/update'
        valid={'items':[{'offer_id':'A','attributes':[{'id':4191,'values':[{'value':'approved'}]}]}]}
        ct.enforce_description_only(oz,valid)
        invalid=copy.deepcopy(valid)
        invalid['items'][0]['attributes'][0]['id']=4384
        with self.assertRaises(ValueError):ct.enforce_description_only(oz,invalid)
        ct.enforce_description_only(ym,{'offerMappings':[{'offer':{'offerId':'A','description':'approved'}}]})
        with self.assertRaises(ValueError):
            ct.enforce_description_only(ym,{'offerMappings':[{'offer':{'offerId':'A','description':'approved','name':'new title'}}]})
        with self.assertRaises(ValueError):
            ct.enforce_description_only('https://content-api.wildberries.ru/content/v3/media/save',{})

    def test_description_batch_uses_shared_readbacks_and_noop_retry(self):
        import content_sync as cs
        skus=['A','B']
        def card(sku,desc):
            return {'offer_id':sku,'name':sku,'id':sku,'primary_image':'https://images.test/a',
                    'images':[],'attributes':[{'id':4191,'values':[{'value':desc}]}]}
        before={'cards':[card(s,'old') for s in skus]}
        after={'cards':[card(s,'approved') for s in skus]}
        rules={s:{'text':{'ozon':{'description':'approved'}}} for s in skus}
        result={'before':{'ozon':before,'wb':{},'market':{}},'products':{}}
        with patch.object(cs,'read_description_channel',side_effect=[before,after]) as read, \
             patch.object(cs,'send_description_batch') as send, patch.object(cs,'encrypt_report'):
            cs.apply_description_batches(result,rules,skus,'apply')
            self.assertEqual(send.call_count,1)
            self.assertEqual(len(send.call_args.args[1]),2)
            self.assertEqual(read.call_count,2)
        self.assertTrue(all(result['products'][s]['ozon']['verified'] for s in skus))
        retry={'before':{'ozon':after,'wb':{},'market':{}},'products':{}}
        with patch.object(cs,'read_description_channel',return_value=after), \
             patch.object(cs,'send_description_batch') as send, patch.object(cs,'encrypt_report'):
            cs.apply_description_batches(retry,rules,skus,'apply')
            send.assert_not_called()
        self.assertTrue(all(retry['products'][s]['ozon']['verified'] and not retry['products'][s]['ozon']['changed'] for s in skus))

    def test_description_batch_never_overwrites_prewrite_wb_drift(self):
        import content_sync as cs
        b={'nmID':1,'vendorCode':'A','brand':'X','title':'original','description':'old',
           'dimensions':{'width':5},'characteristics':[],'sizes':[],'photos':[]}
        a=copy.deepcopy(b);a['title']='concurrent edit'
        rules={'A':{'ids':{'wb':1},'text':{'wb':{'description':'approved'}}}}
        result={'before':{'ozon':{},'wb':{'cards':[b]},'market':{}},'products':{}}
        with patch.object(cs,'read_description_channel',return_value={'cards':[a]}), \
             patch.object(cs,'send_description_batch') as send, patch.object(cs,'encrypt_report'):
            cs.apply_description_batches(result,rules,['A'],'apply')
            send.assert_not_called()
        self.assertFalse(result['products']['A']['wb']['verified'])
        self.assertIn('Prewrite drift',result['products']['A']['wb']['error'])

    def test_wb_description_payload_preserves_live_identity_and_other_fields(self):
        import content_sync as cs
        b={'nmID':1,'vendorCode':'A','brand':'X','title':'original','description':'old',
           'dimensions':{'width':5},'characteristics':[{'id':1,'value':['original']}],
           'sizes':[{'chrtID':5,'skus':['123'],'techSize':'0'}]}
        rule={'ids':{'wb':1},'text':{'wb':{'description':'approved'}}}
        p=cs.description_payload('wb','A',rule,{'cards':[b]})
        self.assertEqual(p['description'],'approved')
        self.assertEqual({k:p[k] for k in b if k!='description'},{k:b[k] for k in b if k!='description'})
        self.assertEqual(b['description'],'old')
        rule['ids']['wb']=2
        with self.assertRaises(ValueError):cs.description_payload('wb','A',rule,{'cards':[b]})


    def test_canonical_write_guard_blocks_stale_legacy_payloads(self):
        rules={'A':{'text':{'ozon':{'description':'O'},'wb':{'description':'W'},'market':{'description':'M'}}}}
        oz={'items':[{'offer_id':'A','attributes':[{'id':4191,'values':[{'value':'O'}]}]}]}
        wb=[{'vendorCode':'A','description':'W'}]
        ym={'offerMappings':[{'offer':{'offerId':'A','description':'M'}}]}
        with patch.object(cm,'load',return_value=rules):
            cm.check_ozon_description_write('/v1/product/attributes/update',oz)
            cm.check_wb_description_write('/content/v2/cards/update',wb)
            cm.check_market_description_write('/v2/businesses/1/offer-mappings/update',ym)
            cm.check_ozon_description_write('/v1/product/attributes/update',{'items':[{'offer_id':'A','attributes':[{'id':4384,'values':[{'value':'other'}]}]}]})
            cm.check_market_description_write('/v2/businesses/1/offer-mappings/update',{'offerMappings':[{'offer':{'offerId':'A','name':'title'}}]})
            cm.check_wb_description_write('/content/v2/cards/update',[{'vendorCode':'a','description':'unmapped'}])
            for fn,args in [
                (cm.check_ozon_description_write,('/v1/product/attributes/update',{'items':[{'offer_id':'A','attributes':[{'id':4191,'values':[{'value':'stale'}]}]}]})),
                (cm.check_ozon_description_write,('/v3/product/import',{'items':[{'offer_id':'A','attributes':[]}]})),
                (cm.check_wb_description_write,('/content/v2/cards/update',[{'vendorCode':'A','description':'stale'}])),
                (cm.check_wb_description_write,('/content/v2/cards/update',[{'vendorCode':'A'}])),
                (cm.check_market_description_write,('/v2/businesses/1/offer-mappings/update',{'offerMappings':[{'offer':{'offerId':'A','description':'stale'}}]}))]:
                with self.assertRaises(ValueError):fn(*args)

    def test_paused_channel_does_not_send_description_write(self):
        import content_sync as cs
        card={'offer_id':'A','name':'A','primary_image':'https://images.test/a','images':[],
              'attributes':[{'id':4191,'values':[{'value':'old'}]}]}
        result={'before':{'ozon':{'cards':[card]}},'products':{}}
        rules={'A':{'text':{'ozon':{'description':'approved'}}}}
        with patch.object(cs,'send_description_batch') as send:
            cs.apply_description_batches(result,rules,['A'],'apply',{'ozon':'attribute drift'})
            send.assert_not_called()
        self.assertIn('Channel writes paused',result['products']['A']['ozon']['error'])

    def test_description_channel_selection_never_includes_unselected_ozon(self):
        import content_sync as cs
        rules={'A':{'text':{'ozon':{'description':'O'},'wb':{'description':'W'},
                           'market':{'description':'M'}}}}
        self.assertEqual(cs.description_rules(rules,['A'],['wb','market'])['A']['text'],
                         {'wb':{'description':'W'},'market':{'description':'M'}})
        with self.assertRaises(ValueError):cs.description_rules(rules,['A'],['avito'])

    def test_ozon_composition_override_is_platform_and_sku_specific(self):
        import content_updates as cu
        rules = {'PART': {'text': {'ozon': {'composition': 'Reviewed Ozon contents'}}}}
        previous = {'PART': {'composition': 'Common contents', 'wb_composition': 'WB contents'}}
        with patch.object(cm, 'load', return_value=rules), patch.object(cu, 'load', return_value=previous):
            self.assertEqual(cu.composition_for('PART', 'ozon'), 'Reviewed Ozon contents')
            self.assertEqual(cu.composition_for('PART', 'wb'), 'WB contents')
            self.assertEqual(cu.composition_for('PART'), 'Common contents')
            self.assertIsNone(cu.composition_for('part', 'ozon'))

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

    def test_content_response_warnings_are_preserved_in_private_evidence(self):
        response=requests.Response();response.status_code=200
        response._content=b'{"status":"OK","results":[{"warnings":["pending"]}]}'
        calls=[]
        with patch.object(requests.sessions.Session,'request',return_value=response):
            with ct.guard(calls,allow_write=True):
                requests.post('https://content-api.wildberries.ru/content/v3/media/save',json={'nmId':1,'data':['https://img/a']})
        self.assertEqual(calls[0]['http_status'],200)
        self.assertEqual(calls[0]['response']['results'][0]['warnings'],['pending'])

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
