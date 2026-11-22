"""Exactly scoped LEOPARTS owner-approved rod price and DQ250/DQ500 cost update.
No other SKU, sale price, stock, media, descriptions, campaigns or discounts may change.
Run: python scripts/approved_rods_filters_20261122.py local|publish
"""
from __future__ import annotations
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
REQ = ROOT / "data/approved_rods_filters_20261122.json"
REPORT = ROOT / "data/approved_rods_filters_20261122_result.json"
MP = {
    "0am325091e": 1990, "0am325091f": 1990,
    "0am325025D": 1490, "0am325025u": 2690,
    "0am325025z": 2690, "0am325091e-set": 4990,
    "0am325091f-set": 4990,
}
DIRECT = {
    "0am325091e": 1240, "0am325091f": 1240,
    "0am325025D": 950, "0am325025u": 1880,
    "0am325025z": 1880, "0am325091e-set": 3450,
    "0am325091f-set": 3450,
}
COST = {
    "0am325091e": 300, "0am325091f": 300,
    "0am325025D": 200, "0am325025u": 500,
    "0am325025z": 500, "0am325091e-set": 1000,
    "0am325091f-set": 1000,
    "02e305045": 280, "02E305051C": 120,
    "02E 305 045": 400, "Dq500": 280,
    "filtr-dsg7-dq500": 120, "DQ500": 400,
}
OLD_MP = {
    "0am325091e": 3850, "0am325091f": 3350,
    "0am325025D": 3350, "0am325025u": 3490,
    "0am325025z": 3490, "0am325091e-set": 8850,
    "0am325091f-set": 8850,
}
OLD_AVITO = {
    "0am325091e": 2350, "0am325091f": 1850,
    "0am325025D": 1850, "0am325025u": 2350,
    "0am325025z": 2350, "0am325091e-set": 5800,
    "0am325091f-set": 5800,
}
OLD_SITE = dict(OLD_AVITO, **{"0am325091e-set":4850, "0am325091f-set":4850})
OLD_COST = {
    "0am325091e":400, "0am325091f":500, "0am325025D":400,
    "0am325025u":800, "0am325025z":900,
    "0am325091e-set":1000, "0am325091f-set":1000,
    "02e305045":350, "02E305051C":150, "02E 305 045":500,
    "Dq500":350, "filtr-dsg7-dq500":150, "DQ500":500,
}
SKU_TITLE_MARKERS = {
    "0am325091e":"101", "0am325091f":"99",
    "0am325025u":"101", "0am325025z":"99",
    "0am325091e-set":"101", "0am325091f-set":"99",
}

def now():
    return datetime.now(timezone.utc).isoformat()

def check_request():
    q = json.loads(REQ.read_text(encoding="utf-8"))
    if q.get("marketplace_prices") != MP or q.get("site_avito_prices") != DIRECT or q.get("purchase_costs") != COST:
        raise ValueError("Approval request does not match exact authorized 7 price and 13 purchase-cost targets")
    if q.get("change_other_prices") is not False:
        raise ValueError("Unrelated price changes are forbidden")

def load_book(path, sheet):
    import openpyxl
    w = openpyxl.load_workbook(ROOT / path)
    if sheet not in w:
        raise ValueError("Missing worksheet " + sheet)
    return w, w[sheet]

def index_skus(ws, col, target, allow_missing=False):
    rows = {}
    for i in range(2, ws.max_row + 1):
        value = ws.cell(i, col).value
        if isinstance(value, str) and value.strip() in target:
            key = value.strip()
            if key in rows:
                raise ValueError("Duplicated exact SKU: " + key)
            rows[key] = i
    missing = set(target) - set(rows)
    if missing and not allow_missing:
        raise ValueError("Missing exact SKUs: " + ", ".join(sorted(missing)))
    return rows

def money(value):
    if value is None or value == "":
        return None
    return round(float(value), 2)

def update_cell(ws, r, col, desired, before_allowed, changed, label):
    c = ws.cell(r, col)
    prev = money(c.value) if isinstance(desired, (int, float)) else c.value
    allowed = set(before_allowed) | {desired}
    if prev not in allowed:
        raise ValueError("Unexpected current " + label + " at " + c.coordinate + ": " + str(prev))
    if prev != desired:
        changed.append({"cell": c.coordinate, "what": label, "old": prev, "new": desired})
        c.value = desired

def process_books():
    plans = [
        ("data/master_control.xlsx","Товары",1),
        ("data/ozon_catalog.xlsx","Ozon",1),
        ("data/wb_catalog.xlsx","WB",1),
        ("data/avito_site_price_overrides.xlsx","Цены Avito и сайт",1),
        ("data/avito_feed.xlsx","Объявления",4),
    ]
    opened, summary = [], {}
    for path, sheet, keycol in plans:
        wb, ws = load_book(path,sheet)
        targets = set(MP) | (set(COST) if "master_control" in path else set())
        if "overrides" in path: targets = set(DIRECT)
        if "avito_feed" in path: targets = set(DIRECT) - {"0am325025D"}
        found = index_skus(ws,keycol,targets,allow_missing=("avito_feed" in path))
        changes = []
        for sku, r in found.items():
            if sku in SKU_TITLE_MARKERS and "master_control" in path:
                title = str(ws.cell(r,6).value or "")
                if SKU_TITLE_MARKERS[sku] not in title:
                    raise ValueError("Wrong rod size for " + sku)
            if "master_control" in path:
                if sku in MP:
                    update_cell(ws,r,8,MP[sku],[OLD_MP[sku]],changes,sku+" master Ozon")
                    update_cell(ws,r,11,MP[sku],[OLD_MP[sku]],changes,sku+" master WB")
                if sku in COST:
                    update_cell(ws,r,27,COST[sku],[OLD_COST[sku]],changes,sku+" cost")
            elif "ozon_catalog" in path:
                update_cell(ws,r,4,MP[sku],[OLD_MP[sku]],changes,sku+" Ozon catalog")
            elif "wb_catalog" in path:
                # WB's current stored price can contain sub-ruble rounding from discounts
                update_cell(ws,r,6,MP[sku],[OLD_MP[sku], round(OLD_MP[sku]*.9999,2)],changes,sku+" WB catalog")
            elif "overrides" in path:
                update_cell(ws,r,2,DIRECT[sku],[OLD_AVITO[sku]],changes,sku+" Avito override")
                if ws.cell(r,3).value not in ("да","yes","1","фикс"):
                    ws.cell(r,3).value = "да"
                    changes.append({"cell":ws.cell(r,3).coordinate, "what":sku+" manual lock", "new":"да"})
                old_site = ws.cell(r,4).value
                old_site = money(old_site) if old_site not in (None,"") else OLD_AVITO[sku]
                if old_site not in (OLD_SITE[sku], DIRECT[sku]):
                    raise ValueError("Unexpected site override for " + sku)
                if ws.cell(r,4).value != DIRECT[sku]:
                    ws.cell(r,4).value = DIRECT[sku]
                    changes.append({"cell":ws.cell(r,4).coordinate, "what":sku+" site override","old":old_site,"new":DIRECT[sku]})
            else:
                update_cell(ws,r,34,DIRECT[sku],[OLD_AVITO[sku]],changes,sku+" Avito feed")
        opened.append((path,wb,changes))
        summary[path]={"matched":len(found),"changed":changes}
    # No writes before all workbook preflight checks have passed.
    for path,wb,changes in opened:
        if changes:
            wb.save(ROOT / path)
        wb.close()
    return summary

def commit_data():
    import subprocess
    filenames=["data/master_control.xlsx","data/ozon_catalog.xlsx","data/wb_catalog.xlsx",
        "data/avito_site_price_overrides.xlsx","data/avito_feed.xlsx",
        "data/approved_rods_filters_20261122_result.json"]
    env = dict(os.environ)
    cmd=["bash",".github/scripts/save_data.sh",*filenames]
    r=subprocess.run(cmd,cwd=ROOT,env=env,text=True,capture_output=True)
    if r.returncode:
        raise RuntimeError("GitHub source save failed: "+(r.stderr or r.stdout)[-250:])
    return "committed"

def stage():
    check_request()
    old_feed=(ROOT/"data/avito_feed.xlsx").read_bytes()
    summary=process_books()
    out={"status":"local_sources_updated","requested_at":now(),
        "marketplace_prices":MP,"site_avito_prices":DIRECT,"purchase_costs":COST,
        "old_avito_feed_sha256":hashlib.sha256(old_feed).hexdigest(),"files":summary,
        "external_writes":0}
    REPORT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps({"stage":"local","updated_files":{k:len(v["changed"]) for k,v in summary.items()},
        "purchase_costs":len(COST),"prices":len(MP)},ensure_ascii=False),flush=True)

def emit(category, data):
    report=json.loads(REPORT.read_text(encoding="utf-8"))
    report.setdefault("channels",{})[category]=data
    report["checked_at_utc"]=now()
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"channel":category,"result":data},ensure_ascii=False,default=str),flush=True)

def publish_ozon():
    import ozon_client
    before = {v["offer_id"]:v for v in ozon_client.get_prices_and_stocks(list(MP))}
    todo=[]
    details={}
    for sku,target in MP.items():
        item=before.get(sku)
        if not item or not isinstance(item.get("price"),dict):
            details[sku]="missing";continue
        p=item["price"]
        if money(p.get("price")) not in (target,OLD_MP[sku]):
            details[sku]="unexpected_live_price";continue
        if money(p.get("price"))==target:
            details[sku]="already_at_target";continue
        t={"offer_id":sku,"price":str(target),"currency_code":"RUB"}
        if money(p.get("old_price")) and money(p.get("old_price"))>target:
            t["old_price"]=str(int(money(p["old_price"])))
        curmin=money(p.get("min_price"))
        if curmin and curmin>target:
            t["min_price"]=str(target)
        todo.append(t)
    if todo:
        r=ozon_client.update_prices(todo)
        for x in r.get("result",[]) if isinstance(r,dict) else []:
            sku=x.get("offer_id")
            if sku in MP:
                details[sku]="accepted" if x.get("updated") and not x.get("errors") else "rejected"
    time.sleep(5)
    after={x["offer_id"]:x for x in ozon_client.get_prices_and_stocks(list(MP))}
    for sku in MP:
        actual=money((after.get(sku) or {}).get("price",{}).get("price"))
        details[sku]={"status":details.get(sku,"unknown"),"actual":actual,"verified":actual==MP[sku]}
    return details

def publish_wb():
    from decimal import Decimal, ROUND_HALF_UP
    import wb_client, config
    rows={r["vendorCode"]:r for r in wb_client.get_prices(limit=1000) if r.get("vendorCode") in MP}
    batch=[];d={}
    for sku, target in MP.items():
        x=rows.get(sku)
        if not x or not x.get("sizes"):
            d[sku]="missing";continue
        size=x["sizes"][0]
        disc=int(x["discount"])
        if disc>=100 or x.get("editableSizePrice"):
            d[sku]="special_sizes_or_discount";continue
        current=money(size["discountedPrice"])
        if abs(current-target)<1:
            d[sku]="already_at_target";continue
        if abs(current-OLD_MP[sku])>1 and sku!="0am325025u":
            d[sku]="unexpected_live_price";continue
        factor=Decimal(100-disc)/100
        base=int((Decimal(target)/factor).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        batch.append({"nmID":x["nmID"],"price":base,"discount":disc})
        d[sku]="submitted"
    if batch:
        result=wb_client._request("POST",config.wb_prices_base,"/api/v2/upload/task",{"data":batch})
        d["task_acknowledged"]=bool((result.get("data") or {}).get("id"))
    time.sleep(8)
    final={x["vendorCode"]:x for x in wb_client.get_prices(limit=1000) if x.get("vendorCode") in MP}
    for sku in MP:
        row=final.get(sku)
        final_price=money(row["sizes"][0]["discountedPrice"]) if row and row.get("sizes") else None
        d[sku]={"status":d.get(sku,"unknown"),"actual":final_price,"verified":final_price is not None and abs(final_price-MP[sku])<1}
    return d

def publish_market():
    import yandex_market_client as ym
    mapping={x["sku"]:x for x in ym.get_all_offers(archived=False)}
    existing={sku for sku in MP if sku in mapping and not mapping[sku]["archived"]}
    if not existing:
        return {"status":"no_verified_catalog_skus"}
    _,campaign=ym._creds()
    s,body=ym._request("POST",f"/v2/campaigns/{campaign}/offers",json_body={"offerIds":sorted(existing)})
    if s!=200 or body.get("status")!="OK":
        return {"status":"preflight_failed"}
    rows={x.get("offerId"):x for x in body.get("result",{}).get("offers",[])}
    updates={};d={}
    for sku in sorted(existing):
        row=rows.get(sku)
        if not row or row.get("unverified"):
            d[sku]="not_verified";continue
        base=(row.get("basicPrice") or {}).get("value")
        if base is None or money(base) not in (MP[sku],OLD_MP[sku]):
            d[sku]="unexpected_live_price";continue
        if money(base)!=MP[sku]:
            updates[sku]=(MP[sku],None)
        d[sku]="ready"
    if updates:
        ok,failed=ym.update_prices(updates)
        d["api_result"]={"accepted":ok,"failed_skus":list(failed)}
    time.sleep(6)
    s,body=ym._request("POST",f"/v2/campaigns/{campaign}/offers",json_body={"offerIds":sorted(existing)})
    if s==200 and body.get("status")=="OK":
        rows={x.get("offerId"):x for x in body.get("result",{}).get("offers",[])}
        for sku in existing:
            row=rows.get(sku) or {}
            actual=money((row.get("basicPrice") or {}).get("value"))
            d[sku]={"status":d.get(sku),"actual":actual,"verified":actual==MP[sku]}
    return d

def publish_tilda():
    import xml.etree.ElementTree as ET
    from scripts.leoparts_direct_operation_nov21 import site_records
    import tilda_commerceml as tc
    rows=site_records()
    todo={};d={}
    for sku,p in DIRECT.items():
        hits=[(uid,r) for uid,r in rows.items() if r.get("sku")==sku]
        if len(hits)!=1:
            d[sku]="missing_or_ambiguous";continue
        uid,row=hits[0]
        editions=row.get("editions") or []
        if len(editions)>1 or any(str(e.get("sku"))!=sku for e in editions):
            d[sku]="variant_conflict";continue
        actual=money(row.get("price"))
        if actual not in (p,OLD_SITE[sku]):
            d[sku]="unexpected_live_price";continue
        if actual!=p:
            todo[sku]=row
        d[sku]="ready"
    if todo:
        ns={"ВерсияСхемы":"2.05","ДатаФормирования":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
        imp=ET.Element("КоммерческаяИнформация",ns)
        cat=ET.SubElement(imp,"Каталог",{"СодержитТолькоИзменения":"true"})
        for tag,val in (("Ид",tc.CATALOG_ID),("ИдКлассификатора",tc.CLASSIFIER_ID)):
            ET.SubElement(cat,tag).text=val
        prods=ET.SubElement(cat,"Товары")
        offers=ET.Element("КоммерческаяИнформация",ns)
        pack=ET.SubElement(offers,"ПакетПредложений",{"СодержитТолькоИзменения":"true"})
        for tag,val in (("Ид",tc.CATALOG_ID+"#offers"),("ИдКаталога",tc.CATALOG_ID),("ИдКлассификатора",tc.CLASSIFIER_ID)):
            ET.SubElement(pack,tag).text=val
        kind=ET.SubElement(ET.SubElement(pack,"ТипыЦен"),"ТипЦены")
        ET.SubElement(kind,"Ид").text=tc.PRICE_TYPE_ID
        ET.SubElement(kind,"Наименование").text="Розничная"
        entries=ET.SubElement(pack,"Предложения")
        for sku,old in todo.items():
            prod=ET.SubElement(prods,"Товар")
            offer=ET.SubElement(entries,"Предложение")
            for el in (prod,offer):
                for tag,val in (("Ид",sku),("Артикул",sku),("Наименование",old["title"])):
                    ET.SubElement(el,tag).text=str(val)
            cp=ET.SubElement(ET.SubElement(offer,"Цены"),"Цена")
            for tag,val in (("ИдТипаЦены",tc.PRICE_TYPE_ID),("ЦенаЗаЕдиницу",DIRECT[sku]),("Валюта","RUB"),("Единица","шт"),("Коэффициент",1)):
                ET.SubElement(cp,tag).text=str(val)
        tc.push(os.environ["TILDA_COMMERCEML_URL"],os.environ["TILDA_COMMERCEML_USER"],
                os.environ["TILDA_COMMERCEML_PASSWORD"],
                ET.tostring(imp,encoding="utf-8",xml_declaration=True).decode(),
                ET.tostring(offers,encoding="utf-8",xml_declaration=True).decode(),
                timeout=60)
    time.sleep(6)
    after=site_records()
    for sku in DIRECT:
        hits=[r for r in after.values() if r.get("sku")==sku]
        actual=money(hits[0].get("price")) if len(hits)==1 else None
        d[sku]={"status":d.get(sku),"actual":actual,"verified":actual==DIRECT[sku]}
    return d

def publish_avito():
    import requests
    import avito_client
    mappings=avito_client.get_autoload_report_items()["items"]
    mapping={}
    for x in mappings:
        sku=x.get("ad_id")
        if sku in DIRECT and x.get("avito_id"):
            if sku in mapping and mapping[sku]!=int(x["avito_id"]):
                raise ValueError("Ambiguous Avito SKU " + sku)
            mapping[sku]=int(x["avito_id"])
    live={int(x["id"]):x for x in avito_client.list_all_items()}
    report={}
    for sku, target in DIRECT.items():
        item_id=mapping.get(sku)
        row=live.get(item_id) if item_id else None
        if not row or row.get("status")!="active":
            report[sku]="no_active_identified_listing";continue
        current=money(row.get("price"))
        if current not in (target,OLD_AVITO[sku]):
            report[sku]="unexpected_live_price";continue
        if current!=target:
            result=requests.post(f"https://api.avito.ru/core/v1/items/{item_id}/update_price",
                headers=avito_client._headers(),json={"price":target},timeout=40)
            report[sku]="accepted" if result.ok else "api_rejected"
        else:
            report[sku]="already_at_target"
    time.sleep(5)
    latest={int(x["id"]):x for x in avito_client.list_all_items()}
    for sku in DIRECT:
        key=mapping.get(sku)
        row=latest.get(key) if key else None
        actual=money(row.get("price")) if row else None
        report[sku]={"status":report.get(sku),"actual":actual,"verified":actual==DIRECT[sku]}
    return report

def publish():
    check_request()
    for channel,fn in (("ozon",publish_ozon),("wildberries",publish_wb),("yandex_market",publish_market),
                       ("tilda",publish_tilda),("avito",publish_avito)):
        try:
            data=fn()
        except Exception as e:
            data={"status":"failed_or_unverified","error_type":type(e).__name__}
        emit(channel,data)

if __name__=="__main__":
    check_request()
    mode=sys.argv[1] if len(sys.argv)>1 else "local"
    if mode=="local":
        stage()
    elif mode=="publish":
        publish()
    else:
        raise SystemExit("Expected local|publish")
