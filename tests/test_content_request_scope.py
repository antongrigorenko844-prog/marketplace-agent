import unittest
import copy
from content_request_scope import skip_legacy,MANIFEST,REQUEST

class RequestScopeTests(unittest.TestCase):
    def setUp(self):
        self.before={'version':1,'products':{'A':{'text':{'wb':{'title':'keep','description':'old'}}}}}
        self.after=copy.deepcopy(self.before)
        self.after['products']['A']['text']['wb']['description']='new'
        self.after['products']['B']={'text':{'market':{'description':'approved'}}}
        self.request={'mode':'apply','channels':['wb','market'],'skus':['A','B']}
        self.paths=[MANIFEST,REQUEST,'reports/content-batches/revision-13/api-summary.json']
    def test_pure_scoped_text_does_not_duplicate_legacy(self):
        self.assertTrue(skip_legacy(self.paths,self.before,self.after,self.request))
    def test_title_or_media_or_commercial_changes_keep_legacy(self):
        for path in ('photos/A/x.jpg','data/master_control.xlsx','data/content_updates.json'):
            self.assertFalse(skip_legacy(self.paths+[path],self.before,self.after,self.request))
        modified=copy.deepcopy(self.after)
        modified['products']['A']['text']['wb']['title']='different'
        self.assertFalse(skip_legacy(self.paths,self.before,modified,self.request))
    def test_partial_scope_or_description_deletion_keeps_legacy(self):
        self.assertFalse(skip_legacy(self.paths,self.before,self.after,{**self.request,'skus':['A']}))
        modified=copy.deepcopy(self.after)
        del modified['products']['A']['text']['wb']['description']
        self.assertFalse(skip_legacy(self.paths,self.before,modified,self.request))
