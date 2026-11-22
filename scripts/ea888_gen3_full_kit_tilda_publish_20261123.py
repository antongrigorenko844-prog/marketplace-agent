"""Safely create ONE EA888 Gen3 full kit on Tilda via CommerceML "changes only".
Old EA888gen3 housing+valve SKU is untouched.
"""
import json,sys,os,time,traceback
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import tilda_commerceml as tc
from scripts.leoparts_direct_operation_nov21 import site_records
SKU="EA888gen3-filter-kit"
OUT=ROOT/"data/ea888_gen3_full_kit_tilda_result_20261123.json"
def note(stage,x):
    try:v=json.loads(OUT.read_text())
    except (OSError,ValueError):v={}
    v[stage]=x;v["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"stage":stage,"result":x},ensure_ascii=False,default=str),flush=True)
def run():
    a=json.loads((ROOT/"data/ea888_gen3_filter_kit_approved_20261123.json").read_text())
    if a.get("sku")!=SKU or a["price_rub"]["tilda"]!=4350 or a["saleable_stock"]!=40:
        raise ValueError("Owner manifest changed")
    images=[ROOT/"photos"/SKU/(SKU+f"_{i}.jpg") for i in (1,2,3)]
    if not all(p.exists() for p in images):raise ValueError("User gallery assets not committed")
    allrows=site_records()
    existing=[r for r in allrows.values() if r.get("sku")==SKU]
    if len(existing)>1:raise ValueError("Duplicate Tilda SKU")
    if existing:
        note("existing",{"status":"already_on_site","price":existing[0].get("price"),"title":existing[0].get("title")})
        if int(float(existing[0].get("price")))!=4350:raise ValueError("Unexpected preexisting site price")
        return
    uid=SKU
    files=[]
    names=[]
    for i,p in enumerate(images,1):
        name="import_files/ea888gen3_filter_kit_"+str(i)+".jpg"
        content,_=tc._image_bytes(str(p),3000000)
        files.append((name,content));names.append(name)
    items=[{"id":SKU,"title":a["title_by_channel"]["tilda"],
        "description":a["text"]["tilda"],"price":4350,"qty":40,"photo":str(images[0])}]
    xml=tc.build_import_xml(items,{SKU:names})
    offers=tc.build_offers_xml(items)
    # Single SKU update; never authorize full-catalog replacement.
    xml=xml.replace("<Каталог>","<Каталог СодержитТолькоИзменения=\"true\">")
    offers=offers.replace('СодержитТолькоИзменения="false"','СодержитТолькоИзменения="true"')
    if 'СодержитТолькоИзменения="true"' not in xml or 'СодержитТолькоИзменения="true"' not in offers:
        raise ValueError("Could not guarantee changes-only mode")
    log=tc.push(os.environ["TILDA_COMMERCEML_URL"],os.environ["TILDA_COMMERCEML_USER"],
        os.environ["TILDA_COMMERCEML_PASSWORD"],xml,offers,timeout=100,image_files=files)
    note("import",{"status":"accepted","step_count":len(log),"photo_files":len(files)})
    for delay in (5,9,15):
        time.sleep(delay)
        after=site_records()
        found=[x for x in after.values() if x.get("sku")==SKU]
        if found:break
    if not found:
        note("verification",{"status":"not_public_yet"})
        return
    row=found[0]
    note("verification",{"status":"public_catalog_visible","uid":row.get("uid"),
        "title":row.get("title"),"price":row.get("price"),
        "price_confirmed":abs(float(row.get("price"))-4350)<1,
        "quantity":row.get("quantity"),"stock_target":40})
if __name__=="__main__":
    try:run()
    except Exception as e:
        t=traceback.extract_tb(e.__traceback__);l=t[-1] if t else None
        note("fatal",{"type":type(e).__name__,"file":Path(l.filename).name if l else None,"line":l.lineno if l else None})
        sys.exit(1)
