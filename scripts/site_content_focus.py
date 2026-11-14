"""Read current site descriptions and the corresponding Avito ads."""
import json, os, sys, time, xml.etree.ElementTree as ET
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import requests
import content_updates, exchange_items, tilda_commerceml as tc
import avito_client as av

OUT=Path('/tmp/site-content-focus');OUT.mkdir(exist_ok=True)
IDS=['0am325025','0am325025x']
def save(name,value):
    (OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2))

def products():
    r=requests.get('https://store.tildaapi.com/api/getproductslist/',params={'storepartuid':'419144347374','recid':'1928468643','size':100,'c':int(time.time()*1000)},timeout=60)
    r.raise_for_status()
    rows=[p for p in r.json()['products'] if p.get('sku') in IDS]
    assert {p['sku'] for p in rows}==set(IDS)
    return rows

def site():
    # The descriptions were verified after saving in the Tilda UI. Keep this
    # workflow read-only: an accepted CommerceML import did not update text.
    after=products();save('tilda-current',after)
    rules=content_updates.load()
    return {p['sku']:rules[p['sku']]['description'] in p.get('text','') for p in after}

def avito():
    report=av.get_autoload_report_items();save('avito-report',report)
    rows=av.list_all_items()
    rows=[r for r in rows if r.get('id') in [8252041186,4220978299] or ('ремкомплект' in r.get('title','').lower() and 'мехатроник' in r.get('title','').lower())]
    save('avito-candidates',rows)
    return {'candidates':len(rows)}

result={}
for name,fn in [('tilda',site),('avito',avito)]:
    try:result[name]=fn()
    except Exception as e:result[name]={'error':str(e)[:500]}
    save('result',result)
print(json.dumps(result,ensure_ascii=False))

if any(isinstance(v,dict) and (v.get("error") or any(x is False for x in v.values())) for v in result.values()):
    raise SystemExit(1)
