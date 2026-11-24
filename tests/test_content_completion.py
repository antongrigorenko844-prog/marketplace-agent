import copy
import json
import unittest
import content_completion as c

class CompletionTests(unittest.TestCase):
    def test_rich_only_text_and_exact_source(self):
        doc={'version':0.3,'content':[{'widgetName':'raShowcase','type':'roll','blocks':[{'imgLink':'','img':{'src':'https://example.com/a.jpg','alt':'original','widthMobile':1200},'title':{'content':['old'],'color':'color1'},'text':{'content':['old','old2'],'align':'left'}}]},{'widgetName':'raTextBlock','title':{'content':['old']},'text':{'content':['old']}}]}
        target='Новая проверенная деталь. Комплектация: одна деталь. Перед заказом проверьте номер и исполнение.'
        result=c.plan_rich('A',doc,target)
        self.assertEqual(c.structural(doc),c.structural(result))
        self.assertEqual(c.normalize(' '.join(' '.join(b['content']) for _,b in c.text_nodes(result))),target)
        self.assertEqual(doc['content'][0]['blocks'][0]['img'],result['content'][0]['blocks'][0]['img'])
    def test_protected_link_rejected(self):
        doc={'content':[{'widgetName':'raTextBlock','text':{'content':['https://example.com/old']}}]}
        with self.assertRaises(ValueError):c.plan_rich('A',doc,'new')
    def test_xml_only_existing_description(self):
        raw=b'<Ads><Ad><Id>A</Id><Title>Tool</Title><Price>100</Price><Description>old</Description><Images><Image url="https://example.com/1"/></Images></Ad></Ads>'
        target={'A':'1'}
        rules={'A':{'text':{'avito':{'description':'new & safe'}}}}
        live=[{'id':1,'title':'Tool','price':100,'status':'active'}]
        report=[{'ad_id':'A','avito_id':1}]
        after,rows=c.patch_tools(raw,target,rules,live,report)
        self.assertEqual(after,raw.replace(b'old',b'new &amp; safe'))
        live[0]['price']=99
        with self.assertRaises(ValueError):c.patch_tools(raw,target,rules,live,report)
    def test_partition_exact(self):
        text='Один товар. Второе предложение. Подбор по номеру детали.'
        self.assertEqual(c.normalize(' '.join(c.partition(text,4))),text)
