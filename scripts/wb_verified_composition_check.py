"""Read live WB composition and corroborate against exact project product keys."""
import json
import logging
import re
import sys
from pathlib import Path
from openpyxl import load_workbook
sys.path.insert(0,str(Path.cwd()))
import wb_client
logging.disable(logging.CRITICAL)
catalog=wb_client.list_cards()
nm={c['nmID'] for c in catalog}
assert len(nm)==len(catalog) and len(nm)>=130
rules=json.loads(Path("data/content_updates.json").read_text())
def filled(c):
    return any(x.get('name')=='Комплектация' and bool(x.get('value')) for x in c.get('characteristics',[]))
empty=[c for c in catalog if not filled(c)]
print('COVERAGE',len(catalog),len(empty),flush=True)
eligible=[c for c in empty if c['vendorCode']!='0cw0am' and 'TEST' not in c['vendorCode'].upper()]
skus={c['vendorCode'] for c in eligible}
defs={}
for c in eligible:
    sid=c['subjectID']
    if sid not in defs:
        items=wb_client.get_subject_characteristics(sid)
        defs[sid]=[(r.get('charcID'),r.get('charcType')) for r in items if r.get('name')=='Комплектация']
    source=rules.get(c['vendorCode'],{})
    comp=source.get('wb_composition',source.get('composition'))
    print('EMPTY',json.dumps([c['vendorCode'],c['nmID'],sid,defs[sid],comp],ensure_ascii=False),flush=True)
for path in ('data/master_control.xlsx','data/ozon_catalog.xlsx','data/kits.xlsx'):
    book=load_workbook(path,read_only=True,data_only=True)
    for ws in book.worksheets[:2]:
        rows=iter(ws.values)
        h=next(rows,())
        h=[str(v or '') for v in h]
        print('COLS',path,ws.title,h[:8],flush=True)
        for vals in rows:
            if not vals or not isinstance(vals[0],str) or vals[0] not in skus:continue
            fields={name:str(v or '')[:350] for name,v in zip(h,vals) if 'состав' in name.lower() or 'комплект' in name.lower() or 'назван' in name.lower() or ('артикул' in name.lower() and 'детал' in name.lower())}
            if path.endswith('kits.xlsx'):
                fields={n:str(v or '')[:150] for n,v in zip(h,vals) if n and not re.search('цен|сумм|закуп|себест|руб|₽',n,re.I)}
            print('EXACT',path,ws.title,vals[0],json.dumps(fields,ensure_ascii=False),flush=True)
    book.close()
