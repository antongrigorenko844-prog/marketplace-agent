"""Update only two site descriptions and inspect the corresponding Avito ads."""
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
    before=products();save('tilda-before',before)
    rules=content_updates.load()
    expected={sku:exchange_items.site_prefix(sku)+('<br/><br/>' if exchange_items.site_prefix(sku) else '')+rules[sku]['description'] for sku in IDS}
    root=ET.fromstring(tc.build_import_xml([]))
    root.remove(root.find('Классификатор'))
    cat=root.find('Каталог');cat.set('СодержитТолькоИзменения','true')
    for p in before:
        sku=p['sku'];assert p['externalid']==sku
        if p.get('text')==expected[sku]:continue
        item=ET.SubElement(cat.find('Товары'),'Товар')
        ET.SubElement(item,'Ид').text=sku
        ET.SubElement(item,'Описание').text=expected[sku]
        assert [c.tag for c in item]==['Ид','Описание']
    if len(cat.find('Товары')):
        body=ET.tostring(root,encoding='unicode')
        (OUT/'import.xml').write_text(body)
        base=os.environ['TILDA_COMMERCEML_URL']
        s=requests.Session();s.auth=(os.environ['TILDA_COMMERCEML_USER'],os.environ['TILDA_COMMERCEML_PASSWORD'])
        r=s.get(tc._url(base,type='catalog',mode='checkauth'),timeout=60);tc._check(r,'checkauth')
        lines=r.text.strip().splitlines()
        if len(lines)>=3 and lines[1].strip() not in s.cookies:s.cookies.set(lines[1].strip(),lines[2].strip())
        tc._check(s.get(tc._url(base,type='catalog',mode='init'),timeout=60),'init')
        name='import0_1.xml'
        tc._check(s.post(tc._url(base,type='catalog',mode='file',filename=name),data=body.encode(),headers={'Content-Type':'application/octet-stream'},timeout=60),'file')
        for _ in range(30):
            msg=tc._check(s.get(tc._url(base,type='catalog',mode='import',filename=name),timeout=60),'import')
            if msg.splitlines()[0].strip().lower()!='progress':break
            time.sleep(5)
        else:raise RuntimeError('Tilda import still in progress')
        save('tilda-response',{'message':msg})
    for _ in range(12):
        after=products();save('tilda-after',after)
        for old in before:
            new=next(p for p in after if p['sku']==old['sku'])
            for key in ['price','priceold','quantity','title','gallery','uid','externalid','partuids']:
                assert old.get(key)==new.get(key),(old['sku'],key,'unexpected change')
        if all(rules[p['sku']]['description'] in p.get('text','') for p in after):break
        time.sleep(10)
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
