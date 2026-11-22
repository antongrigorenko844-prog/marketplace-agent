"""Four owner-approved DQ200 clip and sleeve prices only (no other price changes).
stage: canonical master, Ozon/WB, site/Avito source prices; publish: scoped live writes.
"""
from __future__ import annotations
import io, os, json, hashlib, sys, time
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
REQ=ROOT/"data/approved_vtulki_klipsy_20261122.json"
OUT=ROOT/"data/approved_vtulki_klipsy_20261122_result.json"
MP={"0am325025D":1880,"vtulki-shtokov-2sht":2880,"0am325025P":980,"klipsy-shtokov-2sht":1350}
DIRECT={"0am325025D":980,"vtulki-shtokov-2sht":1650,"0am325025P":450,"klipsy-shtokov-2sht":850}
OLD_MP={"0am325025D":1490,"vtulki-shtokov-2sht":4850,"0am325025P":990,"klipsy-shtokov-2sht":1350}
WB_POSSIBLE={"0am325025D":[1490,3350,3350.13],
             "vtulki-shtokov-2sht":[4850,4850.02],
             "0am325025P":[990,989.8],
             "klipsy-shtokov-2sht":[1350,1349.8]}
OLD_DIRECT={"0am325025D":950,"vtulki-shtokov-2sht":2850,"0am325025P":450,"klipsy-shtokov-2sht":950}
AVITO_IDS={"vtulki-shtokov-2sht":8349295933,"0am325025P":8274232784,
           "klipsy-shtokov-2sht":8403438057}
TITLE={"0am325025D":"втулк","vtulki-shtokov-2sht":"втулк",
       "0am325025P":"клипс","klipsy-shtokov-2sht":"клипс"}
def number(v):
    try:return float(str(v).replace(" ","").replace(",","."))
    except (ValueError,TypeError):return None
def close(a,b,tolerance=1.0):
    return a is not None and b is not None and abs(float(a)-float(b))<=tolerance
def verify_request():
    j=json.loads(REQ.read_text(encoding="utf-8"))
    if j.get("marketplace_prices")!=MP or j.get("site_avito_prices")!=DIRECT or j.get("other_prices_untouched") is not True:
        raise ValueError("Owner's exact four SKU approved prices mismatch")
def report(name,value):
    try:o=json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError,ValueError):o={}
    o[name]=value;o["updated_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(o,indent=2,ensure_ascii=False,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"phase":name,"data":value},ensure_ascii=False,default=str),flush=True)
def index(ws,col,needed):
    found={}
    for i in range(2,ws.max_row+1):
        k=ws.cell(i,col).value
        if isinstance(k,str) and k.strip() in needed:
            k=k.strip()
            if k in found:raise ValueError("Duplicate exact SKU "+k)
            found[k]=i
    if set(found)!=set(needed):raise ValueError("Missing exact SKU(s) "+str(sorted(set(needed)-set(found)))+" in "+ws.title)
    return found
def edit(ws,r,col,desired,allow,changes,sku):
    c=ws.cell(r,col);old=number(c.value)
    if not any(close(old,a,1.5) for a in list(allow)+[desired]):
        raise ValueError("Unexpected source price for "+sku+" in "+ws.title+" "+c.coordinate+" "+str(old))
    if not close(old,desired,.001):
        c.value=desired;changes.append({"sku":sku,"cell":c.coordinate,"before":old,"after":desired})
def stage():
    from openpyxl import load_workbook
    verify_request()
    plans=[("data/master_control.xlsx","Товары",1),
           ("data/ozon_catalog.xlsx","Ozon",1),
           ("data/wb_catalog.xlsx","WB",1),
           ("data/avito_site_price_overrides.xlsx","Цены Avito и сайт",1),
           ("data/avito_feed.xlsx","Объявления",4)]
    prepared=[];result={}
    for path,sheet,keycol in plans:
        wb=load_workbook(ROOT/path);ws=wb[sheet]
        wanted=set(MP)-({"0am325025D"} if "avito_feed" in path else set())
        found=index(ws,keycol,wanted)
        changes=[]
        for sku,r in found.items():
            title=str(ws.cell(r,6 if "master_control" in path else 2).value or "").lower()
            if ("master_control" in path or "ozon_catalog" in path) and TITLE[sku] not in title:
                raise ValueError("Wrong title for exact SKU "+sku+" in "+path)
            if "master_control" in path:
                edit(ws,r,8,MP[sku],[OLD_MP[sku]],changes,sku)
                edit(ws,r,11,MP[sku],WB_POSSIBLE[sku],changes,sku)
            elif "ozon_catalog" in path:
                edit(ws,r,4,MP[sku],[OLD_MP[sku]],changes,sku)
            elif "wb_catalog" in path:
                edit(ws,r,6,MP[sku],WB_POSSIBLE[sku],changes,sku)
            elif "overrides" in path:
                edit(ws,r,2,DIRECT[sku],[OLD_DIRECT[sku]],changes,sku)
                site=ws.cell(r,4)
                raw=number(site.value) if site.value is not None else OLD_DIRECT[sku]
                if not any(close(raw,x,.001) for x in (OLD_DIRECT[sku],DIRECT[sku])):
                    raise ValueError("Unexpected site price for "+sku)
                if site.value is None or not close(number(site.value),DIRECT[sku],.001):
                    site.value=DIRECT[sku]
                    changes.append({"sku":sku,"cell":site.coordinate,"before":raw,"after":DIRECT[sku]})
                locked=ws.cell(r,3)
                if str(locked.value or "").strip().lower() not in ("да","yes","1","фикс"):
                    locked.value="да";changes.append({"sku":sku,"cell":locked.coordinate,"locked":True})
            else:
                edit(ws,r,34,DIRECT[sku],[OLD_DIRECT[sku]],changes,sku)
        prepared.append((path,wb,changes));result[path]={"matches":len(found),"changes":changes}
    for path,wb,changes in prepared:
        if changes:wb.save(ROOT/path)
        wb.close()
    report("source",result)

def ozon():
    import ozon_client
    items=ozon_client.get_prices_and_stocks(list(MP))
    before={};duplicates=set()
    for item in items:
        sku=item.get("offer_id")
        if sku in MP:
            if sku in before:duplicates.add(sku)
            before[sku]=item
    updates=[];out={}
    for sku,target in MP.items():
        x=before.get(sku)
        if not x or sku in duplicates:
            out[sku]={"status":"missing_or_duplicate"};continue
        p=x.get("price") or {};old=number(p.get("price"))
        if not any(close(old,t,.01) for t in (OLD_MP[sku],target)):
            out[sku]={"status":"unexpected_price","observed":old};continue
        if p.get("currency_code") not in (None,"RUB"):
            out[sku]={"status":"invalid_currency"};continue
        if close(old,target,.01):
            out[sku]={"status":"already_correct","verified":True,"observed":old};continue
        payload={"offer_id":sku,"price":str(target),"currency_code":"RUB"}
        crossed=number(p.get("old_price"))
        if crossed and crossed>target:payload["old_price"]=str(int(crossed))
        minprice=number(p.get("min_price"))
        if minprice and minprice>target:payload["min_price"]=str(target)
        updates.append(payload);out[sku]={"status":"ready","before":old}
    if updates:
        reply=ozon_client.update_prices(updates)
        for row in (reply.get("result") or []) if isinstance(reply,dict) else []:
            sku=row.get("offer_id")
            if sku in out:out[sku]["accepted"]=bool(row.get("updated") and not row.get("errors"))
    for delay in (4,7,12):
        time.sleep(delay)
        latest={x.get("offer_id"):x for x in ozon_client.get_prices_and_stocks(list(MP))}
        for sku,target in MP.items():
            val=number((latest.get(sku) or {}).get("price",{}).get("price"))
            out[sku].update({"observed":val,"verified":close(val,target,.01)})
        if all(out[s].get("verified") for s in MP):break
    return out

def wb():
    import wb_client
    rows=wb_client.get_prices(limit=1000)
    found={};duplicates=set()
    for row in rows:
        sku=row.get("vendorCode")
        if sku in MP:
            if sku in found:duplicates.add(sku)
            found[sku]=row
    batch=[];out={}
    for sku,target in MP.items():
        row=found.get(sku)
        if not row or sku in duplicates:
            out[sku]={"status":"missing_or_duplicate"};continue
        sizes=row.get("sizes") or []
        if len(sizes)!=1 or row.get("editableSizePrice"):
            out[sku]={"status":"size_specific"};continue
        old=number(sizes[0].get("discountedPrice"))
        if not any(close(old,t,1.5) for t in WB_POSSIBLE[sku]+[target]):
            out[sku]={"status":"unexpected_live_price","observed":old};continue
        if close(old,target,1.5):
            out[sku]={"status":"already_correct","observed":old,"verified":True};continue
        disc=int(row.get("discount") or 0)
        if not 0<=disc<100:
            out[sku]={"status":"unusable_discount"};continue
        base=int((Decimal(target)/(Decimal(100-disc)/100)).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        batch.append({"nmID":row["nmID"],"price":base,"discount":disc})
        out[sku]={"status":"ready","before":old,"nmID":row["nmID"],"upload_base":base}
    task=None
    if batch:
        answer=wb_client.update_prices(batch)
        task=(answer.get("data") or {}).get("id")
        out["upload"]={"acknowledged":bool(task),"id":task}
    for seconds in (10,15,20):
        time.sleep(seconds)
        fresh={x.get("vendorCode"):x for x in wb_client.get_prices(limit=1000) if x.get("vendorCode") in MP}
        for sku in MP:
            sizes=(fresh.get(sku) or {}).get("sizes") or []
            val=number(sizes[0].get("discountedPrice")) if len(sizes)==1 else None
            out[sku].update({"observed":val,"verified":close(val,MP[sku],1.5)})
        if all(out[s].get("verified") for s in MP):break
    if task and not all(out[s].get("verified") for s in MP):
        try:
            data=wb_client.get_price_upload_result(task)
            out["history"]={"task":data.get("task"),"errors":[{"nmID":x.get("nmID"),"status":x.get("status"),"errorText":str(x.get("errorText") or "")[:200]} for x in data.get("goods",[]) if x.get("status")!=2]}
        except Exception as e:out["history_error"]=type(e).__name__
    return out

def ym_req(ym,method,path,**kwargs):
    code,result=ym._request(method,path,**kwargs)
    if code!=200 or not isinstance(result,dict) or result.get("status","OK")!="OK" or result.get("errors"):
        raise ValueError("Unconfirmed Yandex response for "+path.rsplit("/",1)[-1])
    return result

def market():
    import yandex_market_client as ym
    _,campaign=ym._creds()
    business=ym.get_business_id()
    q=ym_req(ym,"POST",f"/v2/businesses/{business}/settings",json_body={})
    if (q.get("result") or {}).get("settings",{}).get("onlyDefaultPrice") is not True:
        return {"status":"unified_price_setting_unconfirmed"}
    sibling=ym_req(ym,"GET","/v2/campaigns").get("campaigns") or []
    for c in sibling:
        if str(c["id"])==str(campaign) or str((c.get("business") or {}).get("id"))!=str(business):continue
        rr=ym_req(ym,"POST",f"/v2/campaigns/{c['id']}/offers",json_body={"offerIds":list(MP)})
        active=[v.get("offerId") for v in (rr.get("result") or {}).get("offers",[]) if v.get("status") not in ("ARCHIVED","DISABLED_BY_PARTNER")]
        if active:return {"status":"other_live_market_store","skus":active}
    mapping={x["sku"]:x for x in ym.get_all_offers(archived=False) if x.get("sku") in MP}
    q=ym_req(ym,"POST",f"/v2/campaigns/{campaign}/offers",json_body={"offerIds":list(MP)})
    shop={x.get("offerId"):x for x in (q.get("result") or {}).get("offers",[])}
    q=ym_req(ym,"POST",f"/v2/businesses/{business}/offer-prices",json_body={"offerIds":list(MP)})
    office={x.get("offerId"):x for x in (q.get("result") or {}).get("offers",[])}
    updates=[];out={}
    for sku,target in MP.items():
        s=shop.get(sku);b=office.get(sku)
        if not s or not b or s.get("unverified") or sku not in mapping or mapping[sku].get("archived") or not mapping[sku].get("market_sku"):
            out[sku]={"status":"unverified_sku"};continue
        if (s.get("campaignPrice") or {}).get("value") is not None:
            out[sku]={"status":"campaign_override"};continue
        ps=s.get("basicPrice") or {};pb=b.get("price") or {}
        vs=number(ps.get("value"));vb=number(pb.get("value"))
        if vs is None or vb is None or not close(vs,vb,.01):
            out[sku]={"status":"shop_business_mismatch"};continue
        if ps.get("currencyId") not in ("RUR","RUB") or pb.get("currencyId") not in ("RUR","RUB"):
            out[sku]={"status":"invalid_currency"};continue
        if not any(close(vb,x,.01) for x in (OLD_MP[sku],target)):
            out[sku]={"status":"unexpected_price","before":vb};continue
        if close(vb,target,.01):
            out[sku]={"status":"already_correct","verified":True,"observed":vb};continue
        new={"value":target,"currencyId":"RUR"}
        for key in ("discountBase","minimumForBestseller","excludedFromBestsellers"):
            if pb.get(key) is not None:new[key]=pb[key]
        updates.append({"offerId":sku,"price":new})
        out[sku]={"status":"ready","before":vb}
    if updates:
        result=ym_req(ym,"POST",f"/v2/businesses/{business}/offer-prices/updates",json_body={"offers":updates})
        out["upload"]={"count":len(updates),"acknowledged":result.get("status")=="OK"}
    for delay in (4,8,12):
        time.sleep(delay)
        a=ym_req(ym,"POST",f"/v2/campaigns/{campaign}/offers",json_body={"offerIds":list(MP)})
        b=ym_req(ym,"POST",f"/v2/businesses/{business}/offer-prices",json_body={"offerIds":list(MP)})
        sh={x.get("offerId"):x for x in (a.get("result") or {}).get("offers",[])}
        of={x.get("offerId"):x for x in (b.get("result") or {}).get("offers",[])}
        for sku,target in MP.items():
            p=number(((sh.get(sku) or {}).get("basicPrice") or {}).get("value"))
            q=number(((of.get(sku) or {}).get("price") or {}).get("value"))
            out[sku].update({"shop":p,"business":q,"verified":close(p,target,.01) and close(q,target,.01)})
        if all(out[sku].get("verified") for sku in MP):break
    return out

def site():
    import xml.etree.ElementTree as ET
    import tilda_commerceml as tc
    from scripts.leoparts_direct_operation_nov21 import site_records
    before=site_records()
    todo={};out={}
    for sku,target in DIRECT.items():
        hits=[(uid,row) for uid,row in before.items() if row.get("sku")==sku]
        if len(hits)!=1:
            out[sku]={"status":"missing_or_duplicated"};continue
        uid,r=hits[0]
        if TITLE[sku] not in str(r.get("title") or "").lower():
            out[sku]={"status":"wrong_title"};continue
        if str(r.get("externalid",r.get("external_id")) or sku)!=sku:
            out[sku]={"status":"wrong_external_id"};continue
        editions=r.get("editions") or []
        if len(editions)>1 or any(str(e.get("sku"))!=sku for e in editions):
            out[sku]={"status":"variant_mismatch"};continue
        prev=number(r.get("price"))
        if not any(close(prev,x,.01) for x in (OLD_DIRECT[sku],target)):
            out[sku]={"status":"unexpected_live_price","before":prev};continue
        if any(not any(close(number(e.get("price")),x,.01) for x in (OLD_DIRECT[sku],target)) for e in editions):
            out[sku]={"status":"unexpected_edition_price"};continue
        out[sku]={"status":"already_correct" if close(prev,target,.01) else "ready","before":prev}
        if not close(prev,target,.01) or any(not close(number(e.get("price")),target,.01) for e in editions):todo[sku]=r
    if todo:
        attrs={"ВерсияСхемы":"2.05","ДатаФормирования":datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
        imp=ET.Element("КоммерческаяИнформация",attrs);cat=ET.SubElement(imp,"Каталог",{"СодержитТолькоИзменения":"true"})
        for k,v in (("Ид",tc.CATALOG_ID),("ИдКлассификатора",tc.CLASSIFIER_ID)):ET.SubElement(cat,k).text=v
        products=ET.SubElement(cat,"Товары")
        offers=ET.Element("КоммерческаяИнформация",attrs);pack=ET.SubElement(offers,"ПакетПредложений",{"СодержитТолькоИзменения":"true"})
        for k,v in (("Ид",tc.CATALOG_ID+"#offers"),("ИдКаталога",tc.CATALOG_ID),("ИдКлассификатора",tc.CLASSIFIER_ID)):ET.SubElement(pack,k).text=v
        pt=ET.SubElement(ET.SubElement(pack,"ТипыЦен"),"ТипЦены")
        ET.SubElement(pt,"Ид").text=tc.PRICE_TYPE_ID;ET.SubElement(pt,"Наименование").text="Розничная"
        entries=ET.SubElement(pack,"Предложения")
        for sku,r in todo.items():
            for section in (ET.SubElement(products,"Товар"),ET.SubElement(entries,"Предложение")):
                for k,v in (("Ид",sku),("Артикул",sku),("Наименование",r["title"])):ET.SubElement(section,k).text=str(v)
                if section.tag=="Предложение":
                    price=ET.SubElement(ET.SubElement(section,"Цены"),"Цена")
                    for k,v in (("ИдТипаЦены",tc.PRICE_TYPE_ID),("ЦенаЗаЕдиницу",DIRECT[sku]),("Валюта","RUB"),("Единица","шт"),("Коэффициент",1)):ET.SubElement(price,k).text=str(v)
        tc.push(os.environ["TILDA_COMMERCEML_URL"],os.environ["TILDA_COMMERCEML_USER"],
                os.environ["TILDA_COMMERCEML_PASSWORD"],
                ET.tostring(imp,encoding="utf-8",xml_declaration=True).decode(),
                ET.tostring(offers,encoding="utf-8",xml_declaration=True).decode(),timeout=60)
    time.sleep(6)
    after=site_records()
    for sku in DIRECT:
        xs=[r for r in after.values() if r.get("sku")==sku]
        val=number(xs[0].get("price")) if len(xs)==1 else None
        out[sku].update({"observed":val,"verified":close(val,DIRECT[sku],.01)})
    return out

def avito():
    import requests
    import avito_client
    rows=avito_client.get_autoload_report_items()["items"]
    mapping={}
    for r in rows:
        sku=r.get("ad_id")
        if sku in DIRECT and r.get("avito_id"):
            if sku in mapping and mapping[sku]!=int(r["avito_id"]):
                raise ValueError("Duplicated Avito SKU mapping")
            mapping[sku]=int(r["avito_id"])
    current={int(x["id"]):x for x in avito_client.list_all_items()}
    out={}
    for sku,target in DIRECT.items():
        uid=mapping.get(sku);row=current.get(uid) if uid else None
        if not row or row.get("status")!="active":
            out[sku]={"status":"no_active_unique_ad"};continue
        if TITLE[sku] not in str(row.get("title") or "").lower():
            out[sku]={"status":"title_mismatch"};continue
        before=number(row.get("price"))
        if not any(close(before,x,.01) for x in (OLD_DIRECT[sku],target)):
            out[sku]={"status":"unexpected_live_price","before":before};continue
        state="already_correct" if close(before,target,.01) else "ready"
        if state=="ready":
            response=requests.post(f"https://api.avito.ru/core/v1/items/{uid}/update_price",
                headers=avito_client._headers(),json={"price":target},timeout=40)
            state="accepted" if response.ok else "rejected_HTTP_"+str(response.status_code)
        out[sku]={"status":state,"id":uid,"before":before}
    time.sleep(5)
    after={int(x["id"]):x for x in avito_client.list_all_items()}
    for sku in DIRECT:
        uid=mapping.get(sku);r=after.get(uid) if uid else None
        val=number(r.get("price")) if r else None
        out[sku].update({"observed":val,"verified":close(val,DIRECT[sku],.01)})
    return out

def publish_stable_feed():
    """Update the public Avito autoload feed, protecting all unrelated price cells."""
    import requests
    from openpyxl import load_workbook
    from scripts.apply_dq200_prices_20261120 import gh, rename_asset, STABLE_AVITO_URL
    release=gh("GET","/releases/tags/product-photos").json()
    old=[a for a in release.get("assets",[]) if a.get("name")=="avito_feed.xlsx"]
    if len(old)!=1:raise ValueError("Stable feed asset not uniquely identified")
    asset=old[0]
    resp=requests.get(STABLE_AVITO_URL,params={"cache":time.time_ns()},timeout=70)
    resp.raise_for_status();blob=resp.content
    wb=load_workbook(io.BytesIO(blob))
    ws=wb["Объявления"]
    wanted={s:DIRECT[s] for s in DIRECT if s!="0am325025D"}
    found=index(ws,4,wanted)
    changes=[]
    for sku,row in found.items():edit(ws,row,34,DIRECT[sku],[OLD_DIRECT[sku]],changes,sku)
    if not changes:
        wb.close();return {"status":"already_published","verified_rows":len(found)}
    rebuilt=io.BytesIO();wb.save(rebuilt);wb.close()
    check=load_workbook(io.BytesIO(rebuilt.getvalue()),read_only=True)
    for sku,target in wanted.items():
        hits=[r[33] for r in check["Объявления"].values if len(r)>33 and r[3]==sku]
        if hits!=[target]:raise ValueError("Staged feed price failed verification "+sku)
    check.close()
    staged="avito_feed.vtulki_klipsy_"+hashlib.sha256(rebuilt.getvalue()).hexdigest()[:12]+".xlsx"
    uploaded=gh("POST",release["upload_url"].split("{",1)[0],params={"name":staged},
                data=rebuilt.getvalue(),
                headers={"Content-Type":"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}).json()
    validated=requests.get(uploaded["browser_download_url"],timeout=70);validated.raise_for_status()
    if validated.content!=rebuilt.getvalue():raise ValueError("Staged feed bytes mismatch")
    backup="avito_feed.before_vtulki_klipsy_"+str(asset["id"])+".xlsx"
    rename_asset(asset["id"],backup)
    try:rename_asset(uploaded["id"],"avito_feed.xlsx")
    except Exception:
        rename_asset(asset["id"],"avito_feed.xlsx")
        raise
    latest=requests.get(STABLE_AVITO_URL,params={"cache":time.time_ns()},timeout=70)
    return {"status":"promoted","price_changes":changes,
            "stable_bytes_verified":bool(latest.ok and latest.content==rebuilt.getvalue())}

def publish():
    verify_request()
    for name,fn in (("ozon",ozon),("wildberries",wb),("yandex_market",market),
                    ("tilda",site),("avito_stable_feed",publish_stable_feed),("avito",avito)):
        try:result=fn()
        except Exception as ex:
            import traceback
            t=traceback.extract_tb(ex.__traceback__)[-1]
            result={"status":"failed_or_unverified","error_type":type(ex).__name__,
                    "line":t.lineno,"file":str(t.filename).split("/")[-1]}
        report(name,result)

if __name__=="__main__":
    mode=sys.argv[1] if len(sys.argv)>1 else "stage"
    if mode=="stage":stage()
    elif mode=="publish":publish()
    else:raise SystemExit("Usage: stage|publish")
