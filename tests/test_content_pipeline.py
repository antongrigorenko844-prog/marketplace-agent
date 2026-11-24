import unittest
from io import BytesIO
from openpyxl import Workbook, load_workbook
import content_pipeline as p

class PipelineTests(unittest.TestCase):
    def test_reconcile_keeps_verified_exact_identity_and_hash(self):
        e=p.reconcile(None,'A','wb','123','text')
        p.row_status(e,'verified')
        self.assertEqual(p.reconcile(e,'A','wb','123','text')['status'],'verified')
        self.assertEqual(p.reconcile(e,'A','wb','123','changed')['status'],'prepared')
        self.assertEqual(p.reconcile(e,'A','wb','456','text')['status'],'prepared')

    def test_uncertain_write_blocks_changed_target(self):
        e=p.reconcile(None,'A','wb','123','text')
        p.row_status(e,'submitted',protected_hash='baseline')
        self.assertEqual(p.reconcile(e,'A','wb','123','different')['status'],'blocked')
        self.assertEqual(p.reconcile(e,'A','wb','456','text')['status'],'blocked')
    
    def test_retired_and_formula_rows_do_not_block_unrelated_valid_cells(self):
        w=Workbook();s=w.active
        s.append(['SKU','status','Описание','price'])
        s.append(['0AM927769D','active','retired',1])
        s.append(['A','active','old',123])
        s.append(['B','active','=1+2',456])
        f=BytesIO();w.save(f)
        rules={k:{'text':{'wb':{'description':'new '+k}}} for k in ['0AM927769D','A','B']}
        updated,rows=p.isolate_book(f.getvalue(),None,3,'wb',rules,list(rules))
        self.assertEqual(rows['0AM927769D']['status'],'blocked_retired_wb_collision')
        self.assertEqual(rows['B']['status'],'blocked_formula')
        b=load_workbook(BytesIO(updated)).active
        self.assertEqual(b['C2'].value,'retired')
        self.assertEqual(b['C3'].value,'new A')
        self.assertEqual(b['C4'].value,'=1+2')
        self.assertEqual(b['D3'].value,123)

    def test_retry_only_explicit_rejection(self):
        self.assertTrue(p.confirmed_rejection([{'http_status':429}]))
        self.assertTrue(p.confirmed_rejection([{'http_status':400},{'http_status':422}]))
        self.assertFalse(p.confirmed_rejection([{'http_status':500}]))
        self.assertFalse(p.confirmed_rejection([{'http_status':429},{'http_status':200}]))
        self.assertFalse(p.confirmed_rejection([{}]))

    def test_master_isolation_is_channel_specific(self):
        report={'data/master_control.xlsx':{'rows':{'A':{'status':'verified'}}},
                'data/wb_catalog.xlsx':{'rows':{'A':{'status':'blocked_retired_wb_collision'}}},
                'data/wb_new_products.xlsx':{'rows':{'A':{'status':'not_present'}}}}
        self.assertFalse(p.master_ok(report,'A','wb'))
        self.assertTrue(p.master_ok(report,'A','market'))

    def test_queue_routes_pure_description_only(self):
        from content_request_scope import queue_covers, MANIFEST
        old={'version':1,'products':{'A':{'text':{'wb':{'description':'old'}}}}}
        new={'version':1,'products':{'A':{'text':{'wb':{'description':'new'}}}}}
        cfg={'version':1,'enabled':True}
        self.assertTrue(queue_covers([MANIFEST],old,new,cfg))
        self.assertFalse(queue_covers([MANIFEST],old,new,{'enabled':False}))
        new['products']['A']['text']['wb']['title']='changed title'
        self.assertFalse(queue_covers([MANIFEST],old,new,cfg))

    def test_fast_followup_never_resends_or_selects_unaccepted_work(self):
        states=['prepared','submitted','processing','verified','retry_pending','blocked']
        rows={s:{'wb':{'status':s,'card_id':s}} for s in states}
        rules={s:{'text':{'wb':{'description':'text'}}} for s in states}
        self.assertEqual(set(p.followup_entries(rows,'wb',rules)),{'submitted','processing'})

    def test_held_ozon_readback_only_selects_paused_targets(self):
        rows={'A':{'ozon':{'status':'blocked','card_id':'1','error':'Ozon description-only requests changed protected attributes'}},
              'B':{'ozon':{'status':'blocked','card_id':'2','error':'identity mismatch'}},
              'C':{'ozon':{'status':'verified','card_id':'3'}}}
        rules={s:{'text':{'ozon':{'description':'text'}}} for s in rows}
        self.assertEqual(set(p.held_ozon_readback(rows,rules)),{'A'})

    def test_master_stale_ozon_flag_requires_two_exact_agreeing_channels(self):
        rules={'A':{'text':{'wb':{'description':'approved'},'tilda':{'description':'approved'}}}}
        ids={'ozon':{'B':[{'id':'9'}]},'wb':{'A':[{'id':'1'}]},'tilda':{'A':[{'id':'2'}]}}
        out=p.master_rules_for_census(rules,ids,{})
        self.assertEqual(p.cms.target_description(out['A'],{'ozon_status':'active'},'master'),'approved')
        self.assertIsNone(p.cms.target_description(out['A'],{},'ozon'))
        self.assertNotIn('_master_description',rules['A'])
        self.assertNotIn('_master_description',p.master_rules_for_census(rules,ids,{'ozon':'failed'})['A'])
        self.assertNotIn('_master_description',p.master_rules_for_census(rules,ids,{'tilda':'failed'})['A'])
        ids['ozon']['A']=[{'id':'3'}]
        self.assertNotIn('_master_description',p.master_rules_for_census(rules,ids,{})['A'])
        del ids['ozon']['A']
        rules['A']['text']['tilda']['description']='different'
        self.assertNotIn('_master_description',p.master_rules_for_census(rules,ids,{})['A'])

    def test_strict_ozon_coverage_excludes_old_normalized_proofs(self):
        rows={'A':{'ozon':{'status':'verified','attempts':1,'protection_verified':True,
                           'observed_ozon_normalization':{'kiz_materialized':True}}},
              'B':{'ozon':{'status':'verified','attempts':1,'protection_verified':True}},
              'C':{'ozon':{'status':'verified','attempts':0,'protection_verified':False}},
              'D':{'ozon':{'status':'blocked'}}}
        self.assertEqual(p.ozon_strict_coverage(rows),{'text_verified':3,'protected_fields_normalized':1,
                                                       'exact_protection_verified':1,'already_matching_no_write':1})
