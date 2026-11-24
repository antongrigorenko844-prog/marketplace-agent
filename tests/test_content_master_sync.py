import unittest
from io import BytesIO
from zipfile import ZipFile
from openpyxl import Workbook,load_workbook
import content_master_sync as cm


class MasterDescriptionTests(unittest.TestCase):
    def raw(self):
        w=Workbook();s=w.active;s.title='Товары'
        s.append(['SKU','Статус Ozon','Описание','Цена'])
        s.append(['A','active','old',1234])
        s.append(['a','active','different','=1+2'])
        s['C2'].font=s['C2'].font.copy(bold=True)
        b=BytesIO();w.save(b);return b.getvalue()

    def test_zip_patch_preserves_every_other_member_and_cell(self):
        raw=self.raw()
        rules={'A':{'text':{'ozon':{'description':'new & <text>\nsecond line'}}}}
        changed,result=cm.prepare_book(raw,'Товары',3,'ozon',rules,['A'])
        self.assertEqual(result['A']['status'],'pending')
        a=load_workbook(BytesIO(raw));b=load_workbook(BytesIO(changed))
        self.assertEqual(b.active['C2'].value,'new & <text>\nsecond line')
        self.assertEqual(a.active['C2'].style_id,b.active['C2'].style_id)
        for coord in ['A2','B2','D2','A3','B3','C3','D3']:
            self.assertEqual(a.active[coord].value,b.active[coord].value)
        with ZipFile(BytesIO(raw)) as za,ZipFile(BytesIO(changed)) as zb:
            self.assertEqual([n for n in za.namelist() if za.read(n)!=zb.read(n)],['xl/worksheets/sheet1.xml'])
        second,r=cm.prepare_book(changed,'Товары',3,'ozon',rules,['A'])
        self.assertEqual(second,changed)
        self.assertEqual(r['A']['status'],'verified')

    def test_master_preserves_ozon_description_if_only_wb_is_approved(self):
        rule={'text':{'wb':{'description':'WB'}}}
        self.assertIsNone(cm.target_description(rule,{'ozon_status':'active','wb_status':'active'},'master'))
        self.assertEqual(cm.target_description(rule,{'wb_status':'active'},'master'),'WB')

    def test_formula_is_not_replaced(self):
        with self.assertRaises(ValueError):
            cm.patch_cells(self.raw(),'Товары',{'D3':'not a formula'})
