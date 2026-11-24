import sys,unittest
from pathlib import Path
from decimal import Decimal
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import sync_dq200_live_feed as m
class TestLiveFeed(unittest.TestCase):
    def setUp(self):
        self.raw=m.FILE.read_bytes()
        self.records={};self.prices={}
        for sku,o in m.a.xml_offers(self.raw).items():
            name,uid=m.UIDS[sku];price=o.findtext('price');q=o.findtext('count')
            self.prices[name]=price
            self.records[uid]={'uid':uid,'sku':name,'externalid':name,'price':price,'quantity':q,'editions':[{'uid':uid,'sku':name,'price':price,'quantity':q}]}
    def test_noop(self):
        new,changes,blocked,checked=m.patch(self.raw,self.records,self.prices,{})
        self.assertEqual(new,self.raw);self.assertFalse(changes);self.assertFalse(blocked);self.assertEqual(len(checked),12)
    def test_authorized_price_and_zero_stock(self):
        sku='0am927769d';name,uid=m.UIDS[sku];row=self.records[uid]
        p=str(Decimal(row['price'])-100);row.update(price=p,quantity='0');row['editions'][0].update(price=p,quantity='0');self.prices[name]=p
        new,changes,blocked,_=m.patch(self.raw,self.records,self.prices,{})
        self.assertEqual(set(changes),{sku});self.assertFalse(blocked)
        o=m.a.xml_offers(new)[sku]
        self.assertEqual(Decimal(o.findtext('price')),Decimal(p));self.assertEqual(o.get('available'),'false');self.assertEqual(o.findtext('count'),'0')
        self.assertEqual(m.protected_xml(new),m.protected_xml(self.raw))
    def test_unapproved_price_is_not_propagated(self):
        name,uid=m.UIDS['0am927769d'];self.records[uid]['price']='1'
        new,changes,blocked,_=m.patch(self.raw,self.records,self.prices,{})
        self.assertEqual(new,self.raw);self.assertFalse(changes);self.assertIn('0am927769d',blocked)
    def test_duplicate_or_bad_stock_is_blocked(self):
        name,uid=m.UIDS['0am927769d'];self.records['duplicate']=dict(self.records[uid],uid='123')
        self.records[m.UIDS['0am927769f'][1]]['quantity']=''
        new,changes,blocked,_=m.patch(self.raw,self.records,self.prices,{})
        self.assertEqual(new,self.raw);self.assertFalse(changes);self.assertEqual(set(blocked),{'0am927769d','0am927769f'})
if __name__=='__main__':unittest.main()
