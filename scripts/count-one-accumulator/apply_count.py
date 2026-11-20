"""One owner-authorized recount of the common FBS stock; never edit orders or FBO."""
import json
import os
import re
import subprocess
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from openpyxl import load_workbook

ROOT = Path.cwd()
sys.path.insert(0, str(ROOT))
import main
from config import config
from stock_transaction import assert_stock_ready
from sales_availability import quantity_for_sale

SKU = '0AM325587E'
TARGET = 55
MESSAGE = 'Record owner count of 55 for 0AM325587E (2026-11-21) [skip ci]'
FILES = {'data/ozon_catalog.xlsx': ('Кол-во к продаже', 4), 'data/master_control.xlsx': ('Остаток, шт.', 12)}

def git(*args):
    return subprocess.check_output(['git', *args], text=True).strip()

def exact(rows):
    r = list(rows)
    if len(r) != 1: raise ValueError('Missing or ambiguous exact item')
    return r[0]

def locate(path, heading):
    book = load_workbook(path, read_only=True, data_only=True)
    sheet = book.worksheets[0]
    it = sheet.iter_rows()
    headers = next(it)
    col = exact(i for i,c in enumerate(headers) if str(c.value or '').startswith(heading))
    cell = exact(row[col] for row in it if row[0].value == SKU)
    out = (cell.coordinate, cell.value)
    book.close()
    return out

def patch(path, heading, old):
    addr, value = locate(path,heading)
    if value != old: raise ValueError('Expected stock has changed: '+str(value))
    p = Path(path)
    tmp = p.with_suffix('.staged.xlsx')
    with zipfile.ZipFile(p) as src, zipfile.ZipFile(tmp,'w') as dst:
        for entry in src.infolist():
            raw=src.read(entry)
            if entry.filename=='xl/worksheets/sheet1.xml':
                pattern = rb'(<c\b[^>]*\br="' + addr.encode() + rb'"[^>]*>(?:(?!</c>).)*?<v>)' + str(old).encode() + rb'(</v>)'
                raw,n=re.subn(pattern,lambda m:m[1]+str(TARGET).encode()+m[2],raw,flags=re.S)
                if n!=1:raise ValueError('Unexpected source XML')
            dst.writestr(entry,raw)
    if locate(tmp,heading)!=(addr,TARGET):raise ValueError('Workbook verification failed')
    tmp.replace(p)
    return value

def assert_source():
    # Re-read main's operational quantity, independent of content-only commits.
    git('fetch','-q','origin','main')
    remote_blob=git('rev-parse','origin/main:data/ozon_catalog.xlsx')
    if remote_blob!=git('hash-object','data/ozon_catalog.xlsx'):
        raise ValueError('Shared stock workbook advanced; re-read before another write')
    if locate('data/ozon_catalog.xlsx',FILES['data/ozon_catalog.xlsx'][0])[1]!=TARGET:raise ValueError('Inventory changed')
    assert_stock_ready(config.db_path)
    if quantity_for_sale(SKU,TARGET)!=TARGET:raise ValueError('Offer is on a sales hold')

def record():
    assert_stock_ready(config.db_path)
    if not Path(config.db_path).is_file():raise ValueError('Order ledger missing')
    if quantity_for_sale(SKU,TARGET)!=TARGET:raise ValueError('Offer on hold')
    import kits
    bom=kits.load_bom(strict=True)
    if SKU in bom or any(any(k==SKU for k,n in recipe) for recipe in bom.values()):raise ValueError('Assembly components changed; exact-only scope')
    if git('log','-1','--fixed-strings','--grep='+MESSAGE,'--format=%H','main'):
        assert_source()
        return {'already_recorded':True,'after':TARGET}
    source_before={p:patch(p,*conf) for p,conf in FILES.items()}
    git('config','user.name','github-actions[bot]');git('config','user.email','41898282+github-actions[bot]@users.noreply.github.com')
    git('add','--',*FILES)
    if set(git('diff','--cached','--name-only').splitlines())!=set(FILES):raise ValueError('Unexpected staged paths')
    git('commit','-qm',MESSAGE)
    git('push','origin','HEAD:main')
    assert_source()
    return {'before':source_before,'after':TARGET,'commit':git('rev-parse','HEAD')}

def ozon():
    import ozon_client as oz
    warehouse=main._ozon_warehouse_id_int()
    expected=exact(r for r in main._load_stock_items(warehouse_id=warehouse,skip_ozon_off=True) if r['offer_id']==SKU)
    if not warehouse or expected['stock']!=TARGET:raise ValueError('Ozon source mismatch')
    def read():
        x=oz._post('/v2/product/info/stocks-by-warehouse/fbs',{'offer_id':[SKU],'limit':100,'cursor':''})
        rows=[v for v in x.get('products',[]) if v.get('offer_id')==SKU and str(v.get('warehouse_id'))==str(warehouse)]
        if len(rows)==1 and type(rows[0].get('free_stock'))==int:
            return {'available':rows[0]['free_stock'],'reserved':rows[0].get('reserved_stock')}
        raise ValueError('No exact FBS seller warehouse readback')
    def apply():
        z=oz.update_stocks([expected])
        row=exact(x for x in z.get('result',[]) if x.get('offer_id')==SKU)
        if row.get('updated')!=True or row.get('errors'):raise ValueError('Ozon stock rejected')
    return update(read,apply)

def wildberries():
    import wb_client as wb
    wh=str(config.wb_warehouse_id)
    c=exact(c for c in json.loads(Path('data/wb_cards.json').read_text())['cards'] if c.get('vendorCode')==SKU)
    if c['nmID']!=670911962:raise ValueError('WB exact listing changed')
    size=exact(c['sizes']); sid=int(size['chrtID']); barcode=exact(size['skus'])
    bound=exact(x for x in main._wb_stock_rows()[0] if x['offer_id']==SKU)
    if bound['vendor_code']!=SKU or bound['sku']!=barcode or bound['amount']!=TARGET:raise ValueError('WB source mismatch')
    route='/api/v3/stocks/'+wh
    def read():
        data=wb._request('POST',config.wb_marketplace_base,route,{'chrtIds':[sid]})
        row=exact(v for v in data['stocks'] if str(v.get('chrtId'))==str(sid) or v.get('sku')==barcode)
        return {'available':int(row['amount'])}
    def apply():wb.update_stocks(wh,[{'sku':barcode,'amount':TARGET}])
    return update(read,apply)

def market():
    import yandex_market_client as ym
    campaign=os.environ['YANDEX_MARKET_CAMPAIGN_ID']
    if not ym.is_configured():raise ValueError('Market config')
    def read():
        st,body=ym._request('POST',f'/v2/campaigns/{campaign}/offers/stocks',json_body={'offerIds':[SKU]})
        if st!=200 or body.get('status')!='OK' or body['result'].get('paging',{}).get('nextPageToken'):raise ValueError('Market stock lookup')
        rows=[(w,o) for w in body['result']['warehouses'] for o in w.get('offers',[]) if o.get('offerId')==SKU]
        w,o=exact(rows)
        stocks={x['type']:x['count'] for x in o.get('stocks',[])}
        return {'available':stocks['AVAILABLE'],'reserved':stocks.get('FREEZE'),'warehouse':w['warehouseId']}
    def apply():
        ok,fail=ym.update_stocks({SKU:TARGET})
        if ok!=1 or fail:raise ValueError('Market did not accept stock')
    return update(read,apply)

def update(read,apply):
    before=read()
    if before['available']!=TARGET:
        assert_source()
        apply()
    for attempt in range(12):
        after=read()
        if after['available']==TARGET:
            return {'before':before,'after':after}
        if attempt<11:time.sleep(4)
    return {'before':before,'after':after,'pending':True}

def run():
    print('COUNT_SOURCE',json.dumps(record()),flush=True)
    import tilda_stock
    for ch,action in [('Ozon',ozon),('WB',wildberries),('Market',market),('Tilda',lambda:tilda_stock.run(TARGET))]:
        try:
            assert_source()
            result = action()
            print('COUNT_RESULT',ch,json.dumps(result,ensure_ascii=False),flush=True)
        except Exception as exc:
            # Public receipt contains status and error class, never request headers or secrets.
            safe = str(exc) if isinstance(exc,(ValueError,KeyError,AssertionError)) else type(exc).__name__
            print('COUNT_ERROR',ch, type(exc).__name__,safe[:140],flush=True)

if __name__=='__main__':run()
