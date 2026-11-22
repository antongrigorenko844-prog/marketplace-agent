"""Apply owner-approved DQ250/DQ500 site+Avito retail prices ONLY.
Exact-case SKU scope; no changes to Ozon, WB, Market, or inventory.
Run: python scripts/approved_filters_direct_20261122.py stage|publish
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
from datetime import datetime, timezone
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REQ = ROOT/"data/approved_filters_direct_20261122.json"
OUT = ROOT/"data/approved_filters_direct_20261122_result.json"
PRICE = {
    "02e305045":980,
    "02E305051C":495,
    "02E 305 045":1480,
    "Dq500":980,
    "filtr-dsg7-dq500":495,
    "DQ500":1480,
}
BEFORE_AVITO = {
    "02e305045":950,
    "02E305051C":950,
    "02E 305 045":1450,
    "Dq500":1850,
    "filtr-dsg7-dq500":450,
    "DQ500":1450,
}
BEFORE_SITE = dict(BEFORE_AVITO, **{"Dq500":950})
# Other previously approved rods: protect their new feed prices on stable autoload publication.
PREVIOUS_RODS = {
    "0am325091e":1240, "0am325091f":1240,
    "0am325025u":1880, "0am325025z":1880,
    "0am325091e-set":3450, "0am325091f-set":3450,
}
OLD_RODS = {
    "0am325091e":2350,"0am325091f":1850,
    "0am325025u":2350,"0am325025z":2350,
    "0am325091e-set":5800,"0am325091f-set":5800,
}
# Exact identifiers in public Tilda and Avito; check fresh metadata to avoid changing wrong items.
TILDA_UID = {
    "02e305045":"372031303103", "02E305051C":None, "02E 305 045":"878265016983",
    "Dq500":"256312245563", "filtr-dsg7-dq500":None, "DQ500":"852450678823",
}
AVITO_ID = {
    "02e305045":8416684544,"02E305051C":8341096851,"02E 305 045":8386056623,
    "Dq500":8344886348,"filtr-dsg7-dq500":8346375263,"DQ500":8387244563,
}
REPO = "antongrigorenko844-prog/marketplace-agent"

def now(): return datetime.now(timezone.utc).isoformat()

def as_int(x):
    try: return int(round(float(str(x))))
    except (TypeError, ValueError): return None

def assert_manifest():
    q=json.loads(REQ.read_text(encoding="utf-8"))
    if q.get("site_avito_prices")!=PRICE or q.get("other_retail_prices_unchanged") is not True:
        raise ValueError("manifest outside owner-approved scope")

def report(section, payload):
    try: obj=json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError, ValueError): obj={}
    obj[section]=payload;obj["checked_at_utc"]=now()
    OUT.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"phase":section,"data":payload},ensure_ascii=False,default=str),flush=True)

def match_rows(ws, col, wanted, allow_missing=False, start=2):
    found={}
    for i in range(start, ws.max_row+1):
        s=ws.cell(i,col).value
        if isinstance(s,str) and s.strip() in wanted:
            s=s.strip()
            if s in found: raise ValueError("duplicate SKU: "+s)
            found[s]=i
    if not allow_missing and set(found)!=set(wanted):
        raise ValueError("missing SKU(s): "+str(sorted(set(wanted)-set(found))))
    return found

def edit(ws, row, col, allowed, target, changes, key):
    c=ws.cell(row,col); v=as_int(c.value)
    if v not in set(allowed)|{target}:raise ValueError("unexpected price "+key+" at "+c.coordinate+"="+str(v))
    if v!=target:
        changes.append({"sku":key,"cell":c.coordinate,"before":v,"after":target})
        c.value=target

def stage():
    from openpyxl import load_workbook
    assert_manifest()
    plan=[
        ("data/avito_site_price_overrides.xlsx","Цены Avito и сайт",1),
        ("data/avito_feed.xlsx","Объявления",4),
    ]
    results={};opened=[]
    for p,sheet,keycol in plan:
        wb=load_workbook(ROOT/p);ws=wb[sheet];found=match_rows(ws,keycol,PRICE)
        change=[]
        for sku,r in found.items():
            if "overrides" in p:
                edit(ws,r,2,[BEFORE_AVITO[sku]],PRICE[sku],change,sku+" avito override")
                oldsite=as_int(ws.cell(r,4).value) if ws.cell(r,4).value is not None else BEFORE_AVITO[sku]
                if oldsite not in (BEFORE_SITE[sku],PRICE[sku]):
                    raise ValueError("unexpected site override for "+sku)
                if as_int(ws.cell(r,4).value)!=PRICE[sku]:
                    ws.cell(r,4).value=PRICE[sku]
                    change.append({"sku":sku,"cell":ws.cell(r,4).coordinate,"before":oldsite,"after":PRICE[sku]})
                if str(ws.cell(r,3).value or "").strip().lower() not in ("да","yes","1","фикс"):
                    ws.cell(r,3).value="да"
                    change.append({"sku":sku,"cell":ws.cell(r,3).coordinate,"after":"manual_lock"})
            else:
                edit(ws,r,34,[BEFORE_AVITO[sku]],PRICE[sku],change,sku+" avito feed")
        opened.append((p,wb,change));results[p]={"rows":len(found),"changed":change}
    # Don't modify any workbook before all preflight checks have passed.
    for p,w,changed in opened:
        if changed:w.save(ROOT/p)
        w.close()
    report("source_files",results)

def live_site():
    from scripts.leoparts_direct_operation_nov21 import site_records
    return site_records()

def site():
    import tilda_commerceml as tc
    records=live_site();subset={};states={}
    for sku,target in PRICE.items():
        matches=[(uid,row) for uid,row in records.items() if row.get("sku")==sku]
        if len(matches)!=1:
            states[sku]={"status":"missing_or_duplicate"};continue
        uid,row=matches[0]
        if TILDA_UID[sku] is not None and uid!=TILDA_UID[sku]:
            states[sku]={"status":"identity_changed"};continue
        if str(row.get("externalid",row.get("external_id")) or sku)!=sku:
            states[sku]={"status":"external_id_mismatch"};continue
        editions=row.get("editions") or []
        if len(editions)>1 or any(str(ed.get("sku"))!=sku for ed in editions):
            states[sku]={"status":"unexpected_variants"};continue
        old=as_int(row.get("price"))
        if old not in (BEFORE_SITE[sku],target):
            states[sku]={"status":"unexpected_live_price","observed":old};continue
        if any(as_int(ed.get("price")) not in (BEFORE_SITE[sku],target) for ed in editions):
            states[sku]={"status":"unexpected_edition_price"};continue
        states[sku]={"status":"already_at_target" if old==target else "ready","before":old}
        if old!=target or any(as_int(ed.get("price"))!=target for ed in editions):subset[sku]=row
    if subset:
        if not all(os.environ.get(k) for k in ["TILDA_COMMERCEML_URL","TILDA_COMMERCEML_USER","TILDA_COMMERCEML_PASSWORD"]):
            return {"status":"missing_Tilda_credentials","items":states}
        attrs={"ВерсияСхемы":"2.05","ДатаФормирования":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
        imp=ET.Element("КоммерческаяИнформация",attrs); cat=ET.SubElement(imp,"Каталог",{"СодержитТолькоИзменения":"true"})
        for tag,val in [("Ид",tc.CATALOG_ID),("ИдКлассификатора",tc.CLASSIFIER_ID)]:
            ET.SubElement(cat,tag).text=val
        productlist=ET.SubElement(cat,"Товары")
        offers=ET.Element("КоммерческаяИнформация",attrs); pack=ET.SubElement(offers,"ПакетПредложений",{"СодержитТолькоИзменения":"true"})
        for tag,val in [("Ид",tc.CATALOG_ID+"#offers"),("ИдКаталога",tc.CATALOG_ID),("ИдКлассификатора",tc.CLASSIFIER_ID)]:
            ET.SubElement(pack,tag).text=val
        kind=ET.SubElement(ET.SubElement(pack,"ТипыЦен"),"ТипЦены")
        ET.SubElement(kind,"Ид").text=tc.PRICE_TYPE_ID
        ET.SubElement(kind,"Наименование").text="Розничная"
        offerlist=ET.SubElement(pack,"Предложения")
        for sku,row in subset.items():
            for par in (ET.SubElement(productlist,"Товар"),ET.SubElement(offerlist,"Предложение")):
                for tag,val in [("Ид",sku),("Артикул",sku),("Наименование",row.get("title"))]:
                    ET.SubElement(par,tag).text=str(val)
                if par.tag=="Предложение":
                    pr=ET.SubElement(ET.SubElement(par,"Цены"),"Цена")
                    for tag,val in [("ИдТипаЦены",tc.PRICE_TYPE_ID),("ЦенаЗаЕдиницу",PRICE[sku]),("Валюта","RUB"),("Единица","шт"),("Коэффициент",1)]:
                        ET.SubElement(pr,tag).text=str(val)
        try:
            tc.push(os.environ["TILDA_COMMERCEML_URL"],os.environ["TILDA_COMMERCEML_USER"],
                os.environ["TILDA_COMMERCEML_PASSWORD"],
                ET.tostring(imp,encoding="utf-8",xml_declaration=True).decode(),
                ET.tostring(offers,encoding="utf-8",xml_declaration=True).decode(),timeout=60)
        except Exception as e:states["publication_error"]={"type":type(e).__name__}
    time.sleep(5)
    after=live_site()
    for sku in PRICE:
        xs=[r for r in after.values() if r.get("sku")==sku]
        actual=as_int(xs[0].get("price")) if len(xs)==1 else None
        states[sku].update({"actual":actual,"verified":actual==PRICE[sku]})
    return states

def release_feed():
    """Modify only six requested feed price cells and previously approved rod price cells.
    Preserve every other cell value and every other GitHub release asset."""
    from openpyxl import load_workbook
    import requests
    from scripts.apply_dq200_prices_20261120 import gh, rename_asset, STABLE_AVITO_URL
    release=gh("GET","/releases/tags/product-photos").json()
    stable=[x for x in release.get("assets",[]) if x.get("name")=="avito_feed.xlsx"]
    if len(stable)!=1:raise ValueError("Avito stable feed absent or ambiguous")
    asset=stable[0]
    r=requests.get(STABLE_AVITO_URL,timeout=75,params={"ref":str(time.time_ns())})
    r.raise_for_status()
    raw=r.content
    wb=load_workbook(io.BytesIO(raw))
    ws=wb["Объявления"]
    expected=dict(PRICE,**PREVIOUS_RODS)
    found=match_rows(ws,4,expected)
    changed=[]
    for sku,r in found.items():
        before = BEFORE_AVITO[sku] if sku in PRICE else OLD_RODS[sku]
        edit(ws,r,34,[before],expected[sku],changed,sku+" stable feed")
    if not changed:
        wb.close();return {"status":"stable_already_at_target","count":len(found)}
    out=io.BytesIO();wb.save(out);wb.close()
    # Independently compare all sheet cell values except target price fields before publication.
    oldwb=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
    newwb=load_workbook(io.BytesIO(out.getvalue()),read_only=True,data_only=False)
    try:
        for sname in oldwb.sheetnames:
            oldsh,newsh=oldwb[sname],newwb[sname]
            if oldsh.max_row!=newsh.max_row or oldsh.max_column!=newsh.max_column:raise ValueError("feed structure changed")
            for oldrow,newrow in zip(oldsh.iter_rows(),newsh.iter_rows()):
                for oc,nc in zip(oldrow,newrow):
                    if oc.value==nc.value:continue
                    if sname!="Объявления" or oc.column!=34 or str(oldsh.cell(oc.row,4).value) not in expected:
                        raise ValueError("unrelated Avito feed change")
    finally:oldwb.close();newwb.close()
    staged="avito_feed.approved_filters_"+hashlib.sha256(out.getvalue()).hexdigest()[:12]+".xlsx"
    upload_url=release["upload_url"].split("{",1)[0]
    uploaded=gh("POST",upload_url,params={"name":staged},data=out.getvalue(),
        headers={"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}).json()
    check=requests.get(uploaded["browser_download_url"],timeout=75);check.raise_for_status()
    if hashlib.sha256(check.content).digest()!=hashlib.sha256(out.getvalue()).digest():raise ValueError("upload mismatch")
    backup="avito_feed.before_filter_20261122_"+str(asset["id"])+".xlsx"
    rename_asset(asset["id"],backup)
    try: rename_asset(uploaded["id"],"avito_feed.xlsx")
    except Exception:
        rename_asset(asset["id"],"avito_feed.xlsx")
        raise
    final=requests.get(STABLE_AVITO_URL,timeout=75,params={"ref":str(time.time_ns())})
    return {"status":"asset_promoted","changed_skus":[c["sku"] for c in changed],
        "live_byte_verified":bool(final.ok and hashlib.sha256(final.content).digest()==hashlib.sha256(out.getvalue()).digest())}

def avito():
    import requests
    import avito_client
    mappings=avito_client.get_autoload_report_items()["items"]
    pairs={}
    for x in mappings:
        sku=x.get("ad_id")
        if sku not in PRICE:continue
        aid=int(x.get("avito_id") or 0)
        if aid and (sku in pairs and pairs[sku]!=aid):raise ValueError("ambiguous Avito SKU")
        if aid:pairs[sku]=aid
    live={int(x["id"]):x for x in avito_client.list_all_items()}
    result={}
    for sku,target in PRICE.items():
        itemid=pairs.get(sku);row=live.get(itemid) if itemid else None
        if itemid!=AVITO_ID[sku] or not row or row.get("status")!="active":
            result[sku]={"status":"mapping_not_active"};continue
        old=as_int(row.get("price"))
        if old not in (BEFORE_AVITO[sku],target):
            result[sku]={"status":"unexpected_price","observed":old};continue
        status="already_at_target" if old==target else "pending"
        if old!=target:
            resp=requests.post("https://api.avito.ru/core/v1/items/"+str(itemid)+"/update_price",
                headers=avito_client._headers(),json={"price":target},timeout=40)
            status="accepted" if resp.ok else "rejected_HTTP_"+str(resp.status_code)
        result[sku]={"status":status,"before":old}
    time.sleep(5)
    after={int(x["id"]):x for x in avito_client.list_all_items()}
    for sku,item in result.items():
        row=after.get(AVITO_ID[sku])
        actual=as_int(row.get("price")) if row else None
        item.update({"actual":actual,"verified":actual==PRICE[sku]})
    return result

def publish():
    assert_manifest()
    for step,func in [("tilda",site),("stable_avito_feed",release_feed),("avito",avito)]:
        try:out=func()
        except Exception as e:out={"status":"failed_or_unverified","error_type":type(e).__name__}
        report(step,out)

if __name__=="__main__":
    m=sys.argv[1] if len(sys.argv)>1 else "stage"
    if m=="stage":stage()
    elif m=="publish":publish()
    else:raise ValueError("mode stage|publish")
