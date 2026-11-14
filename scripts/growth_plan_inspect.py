"""Read-only evidence for the approved 12-step sales improvement plan."""
import datetime as dt
import json
from pathlib import Path
import sys
import traceback
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym
from openpyxl import load_workbook

OUT=Path('/tmp/growth-plan-evidence'); OUT.mkdir(exist_ok=True)
def save(name,data):
 (OUT/(name+'.json')).write_text(json.dumps(data,ensure_ascii=False,indent=2,default=str))
 print(name,'saved',flush=True)
def safe(name,fn):
 try:save(name,fn())
 except Exception as e:save(name,{'error':str(e)});traceback.print_exc()
IDS=['0am325025H','0am325025x','0am325025','0AM325587E','0am325025m','0am325091e-set','0am325091f-set','0am325025u','0am325025z','0am325025hfe','0AM325025B','02E927770AL','0am325066AC','02e305045','prokladka-vtulki-dq200','01X301127C','vint-kollektora-ea888','solenoid-02e-0b5-bolshoy','0ambset','0am325025l','0BH325183']+['0AM927769'+s for s in 'DFGKE']+['0am325025HX'+s for s in ('','F','G','K','E')]
safe('ozon-attributes',lambda:oz.get_product_names(IDS))
safe('ozon-info',lambda:oz.get_product_info_list(IDS))
safe('ozon-prices',lambda:oz.get_prices_and_stocks(IDS))
safe('ozon-stocks',lambda:oz.get_stocks(IDS))
safe('wb-cards',wb.list_cards)
safe('wb-prices',wb.get_prices)
def mapping():
 biz=ym.get_business_id();_,camp=ym._creds()
 out={}
 for name,path in [('mappings',f'/v2/businesses/{biz}/offer-mappings'),('offers',f'/v2/campaigns/{camp}/offers')]:
  status,body=ym._request('POST',path,json_body={'offerIds':IDS})
  assert status==200,(status,body)
  out[name]=body
 return out
safe('market',mapping)
def postings():
 now=dt.datetime.now(dt.timezone.utc)
 rows=oz.get_fbs_orders_since('2026-10-31T00:00:00.000Z',now.isoformat().replace('+00:00','Z'))
 # Do not export customer contact/address details.
 return [{k:v for k,v in row.items() if k not in ('customer','addressee','analytics_data','financial_data','barcodes')} for row in rows]
safe('fbs-postings',postings)
def workbooks():
 out={}
 for name in ('master_control','kits','ozon_catalog','wb_catalog','avito_feed','tilda_feed'):
  path=Path('data')/(name+'.xlsx')
  if path.exists():
   workbook=load_workbook(path,read_only=True,data_only=False)
   out[name]={s.title:list(s.values) for s in workbook.worksheets}
 return out
safe('workbooks',workbooks)
for name in ('avito_links','site_links','store_links','market_video_cache','ozon_overrides','audit_ignore','removed_products'):
 p=Path('data')/(name+'.json')
 if p.exists():safe(name,lambda p=p:json.loads(p.read_text()))
save('meta',{'created_at':dt.datetime.now(dt.timezone.utc).isoformat(),'mode':'read only','offer_ids':IDS})
