"""Exact-five LEOPARTS publication recovery, independently checked by channel.

Only the two approved DSG service kits may be CREATED on Wildberries, because
the three 6T30 products already exist there, and their approved Library image
bytes are not in this GitHub repository yet. Never substitute another generation.
Only approved DSG kit prices may be updated on Market using *business default*
pricing, as the seller's campaign-level pricing returns 423.
"""
import os, sys, json, time, traceback
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client as wb
import yandex_market_client as ym
from scripts.leoparts_direct_operation_nov21 import site_records

OUT=ROOT/"data/leoparts_five_recovery_20261123_result.json"
REQ=ROOT/"data/leoparts_five_recovery_20261123_request.json"
DSG=ROOT/"data/dsg_tools_all_channels_20261123_result.json"
SKUS6=("6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2")
KIT1="tool-dq200-acc-restore"; KIT2="tool-dsg-pressure-hp"
KITS=(KIT1,KIT2)
PRICES={KIT1:38800,KIT2:28800,SKUS6[0]:19880,SKUS6[1]:58850,SKUS6[2]:58850}
STOCK={KIT1:3,KIT2:3,SKUS6[0]:20,SKUS6[1]:3,SKUS6[2]:3}
WB_SUBJECT=6370

def now(): return datetime.now(timezone.utc).isoformat()
def write(channel,sku,value):
    try: result=json.loads(OUT.read_text(encoding="utf-8"))
    except (OSError,ValueError): result={"source":"owner-approved-five-SKU-scoped-recovery","channels":{}}
    result.setdefault("channels",{}).setdefault(channel,{})[sku]=value
    result["updated_utc"]=now()
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"channel":channel,"sku":sku,"result":value},ensure_ascii=False,default=str),flush=True)
def failure(channel,sku,e):
    write(channel,sku,{"status":"error","type":type(e).__name__,"reason":str(e)[:220]})
def valid():
    request=json.loads(REQ.read_text(encoding="utf-8"))
    assert request["skus"]==list(SKUS6)+list(KITS)
    assert request["owner_approved"] is True and request["mode"]=="apply"
    assert request["prices_marketplaces"]==[19880,58850,58850,38800,28800]
    assert request["stock"]==[20,3,3,3,3]
    r=json.loads(DSG.read_text(encoding="utf-8"))
    links={}
    for sku in KITS:
        urls=r["channels"][sku]["images"]["urls"]
        if r["channels"][sku]["images"]["status"]!="published_to_public_release" or len(urls)!=(1 if sku==KIT1 else 2):
            raise ValueError("Approved DSG kit image assets missing")
        if not all(u.startswith("https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/") for u in urls):
            raise ValueError("Unexpected DSG kit media host")
        links[sku]=urls
    return links

def run_wb(urls):
    cards=wb.list_cards()
    if len(cards)<110: raise RuntimeError("WB pagination incomplete, no creation allowed")
    found={}
    for sku in list(SKUS6)+list(KITS):
        group=[x for x in cards if str(x.get("vendorCode") or "")==sku]
        if len(group)>1: raise RuntimeError("Duplicate WB SKU: "+sku)
        if group:found[sku]=group[0]
    for sku in SKUS6:
        card=found.get(sku)
        write("wb",sku,{"status":"existing" if card else "missing_unexpected",
                        "nmID":(card or {}).get("nmID"),"photo_count":len((card or {}).get("photos") or []),
                        "media":"approved image remains in ChatGPT Library, not yet transferred to public GitHub host"})
    for sku in KITS:
        card=found.get(sku)
        if card:
            write("wb",sku,{"status":"existing","nmID":card.get("nmID"),"photo_count":len(card.get("photos") or [])})
            continue
        try:
            barcodes=wb.generate_barcodes(1)
            if len(barcodes)!=1 or not str(barcodes[0]).isdigit(): raise ValueError("barcode generation")
            attrs=wb.get_subject_characteristics(WB_SUBJECT)
            required=[a for a in attrs if a.get("required") or a.get("isRequired") or a.get("is_required")]
            accepted={"Бренд","Комплектация","Назначение","Модель","Артикул производителя"}
            unexpected=[str(a.get("name")) for a in required if str(a.get("name")) not in accepted]
            if unexpected:raise ValueError("Unverified WB mandatory attributes: "+repr(unexpected))
            dimensions=({"length":40,"width":30,"height":15,"weightBrutto":3.0} if sku==KIT1
                        else {"length":35,"width":25,"height":15,"weightBrutto":2.0})
            title=("Набор восстановления гидроаккумулятора DSG7 DQ200" if sku==KIT1
                   else "Набор проверки давления DSG")
            desc=("Специализированный набор для обслуживания гидроаккумулятора DSG7 DQ200 и заправки азотом."
                  if sku==KIT1 else "Специализированный набор проверки давления при обслуживании трансмиссий DSG.")
            charcs=[]
            for a in required:
                label=a.get("name")
                key=a.get("charcID") or a.get("id")
                vals={"Бренд":["Нет бренда"],"Комплектация":["Специализированный набор инструментов — 1 комплект"],
                      "Назначение":["Ремонт и диагностика трансмиссий"],"Модель":["DSG"],
                      "Артикул производителя":[sku]}
                if key and label in vals:charcs.append({"id":key,"value":vals[label]})
            payload={"vendorCode":sku,"title":title[:60],"description":desc,"brand":"Нет бренда",
                     "dimensions":dimensions,
                     "sizes":[{"techSize":"0","wbSize":"","price":PRICES[sku],"skus":[str(barcodes[0])]}]}
            if charcs:payload["characteristics"]=charcs
            ack=wb.create_cards(WB_SUBJECT,[payload])
            if not isinstance(ack,dict) or ack.get("error"): raise RuntimeError("WB rejected creation: "+repr(ack)[:150])
            write("wb",sku,{"status":"creation_submitted","subject":WB_SUBJECT,"barcode_last4":str(barcodes[0])[-4:]})
        except Exception as e: failure("wb",sku,e)
    # Readback is mandatory after an asynchronous WB creation.
    for attempt in range(6):
        time.sleep(8 if attempt else 5)
        try:
            updated=wb.list_cards()
            for sku in KITS:
                matched=[x for x in updated if str(x.get("vendorCode") or "")==sku]
                if len(matched)==1:found[sku]=matched[0]
            if all(s in found for s in KITS):break
        except Exception as e:
            if attempt==5:failure("wb_readback","all",e)
    for sku in KITS:
        card=found.get(sku)
        if not card:
            write("wb_final",sku,{"status":"creation_unverified_no_retry","price_expected":PRICES[sku]})
            continue
        nm=int(card["nmID"])
        pictures=card.get("photos") or []
        if not pictures:
            try:
                ack=wb.update_media(nm,urls[sku])
                write("wb_media",sku,{"status":"submitted_pending_readback" if not ack.get("error") else "rejected",
                                     "nmID":nm,"source_photo_urls":urls[sku],"error":ack.get("error")})
            except Exception as e:failure("wb_media",sku,e)
        else:write("wb_media",sku,{"status":"already_has_photos","nmID":nm,"photo_count":len(pictures)})
        try:
            records=wb.get_prices(limit=1000)
            rec=[x for x in records if x.get("nmID")==nm]
            sizes=(rec[0].get("sizes") or []) if len(rec)==1 else []
            actual=float(sizes[0].get("discountedPrice") or 0) if len(sizes)==1 else None
            if actual is not None and abs(actual-PRICES[sku])<1:
                write("wb_price",sku,{"status":"verified","price":actual})
            else:
                ack=wb.update_prices([{"nmID":nm,"price":PRICES[sku],"discount":0}])
                write("wb_price",sku,{"status":"submitted_pending_readback","target":PRICES[sku],"previous":actual,
                                       "task_id":(ack.get("data") or {}).get("id")})
        except Exception as e:failure("wb_price",sku,e)
        try:
            bc=[str(s) for z in card.get("sizes") or [] for s in (z.get("skus") or [])]
            if len(bc)!=1 or not bc[0].isdigit():raise ValueError("SKU barcode missing")
            if not os.getenv("WB_WAREHOUSE_ID"):raise RuntimeError("WB warehouse secret missing")
            wb.update_stocks(os.environ["WB_WAREHOUSE_ID"],[{"sku":bc[0],"amount":STOCK[sku]}])
            write("wb_stock",sku,{"status":"submitted","target":STOCK[sku],"nmID":nm})
        except Exception as e:failure("wb_stock",sku,e)
    try:
        updated=wb.list_cards()
        for sku in KITS:
            found_final=[x for x in updated if x.get("vendorCode")==sku]
            write("wb_final",sku,{"status":"created" if len(found_final)==1 else "unverified",
                                  "nmID":found_final[0].get("nmID") if len(found_final)==1 else None,
                                  "photo_count":len(found_final[0].get("photos") or []) if len(found_final)==1 else None,
                                  "photo_status":"readback" if len(found_final)==1 else "not_confirmed"})
    except Exception as e: failure("wb_final","all",e)

def run_market():
    business=ym.get_business_id()
    existing=ym.get_all_offers(archived=False)
    for sku in SKUS6:
        group=[x for x in existing if x.get("sku")==sku]
        write("market",sku,{"status":"existing" if len(group)==1 else "blocked_missing_public_approved_image" if not group else "duplicate_error",
                            "count":len(group)})
    for sku in KITS:
        group=[x for x in existing if x.get("sku")==sku]
        if len(group)!=1:
            write("market",sku,{"status":"not_exactly_one_business_offer","count":len(group)})
            continue
        write("market",sku,{"status":"business_catalog_confirmed"})
        endpoint=f"/v2/businesses/{business}/offer-prices"
        try:
            code,body=ym._request("POST",endpoint,json_body={"offerIds":[sku]})
            if code!=200 or body.get("status")!="OK":raise ValueError("Market readback HTTP "+str(code))
            hits=[x for x in (body.get("result") or {}).get("offers",[]) if x.get("offerId")==sku]
            actual=float((hits[0].get("price") or {}).get("value") or 0) if len(hits)==1 else None
            if actual is not None and abs(actual-PRICES[sku])<1:
                write("market_price",sku,{"status":"verified","price":actual,"source":"business_default"})
                continue
            code,resp=ym._request("POST",f"/v2/businesses/{business}/offer-prices/updates",
                json_body={"offers":[{"offerId":sku,"price":{"value":PRICES[sku],"currencyId":"RUR"}}]})
            if code!=200 or resp.get("status")!="OK" or resp.get("errors"):
                write("market_price",sku,{"status":"write_rejected","http":code,"errors":resp.get("errors"),"previous_price":actual})
            else:write("market_price",sku,{"status":"submitted_pending_readback","target":PRICES[sku],"previous_price":actual})
        except Exception as e:failure("market_price",sku,e)
    time.sleep(6)
    for sku in KITS:
        try:
            code,body=ym._request("POST",f"/v2/businesses/{business}/offer-prices",json_body={"offerIds":[sku]})
            matches=[x for x in (body.get("result") or {}).get("offers",[]) if x.get("offerId")==sku] if code==200 else []
            actual=float((matches[0].get("price") or {}).get("value") or 0) if len(matches)==1 else None
            write("market_price_final",sku,{"status":"verified" if actual is not None and abs(actual-PRICES[sku])<1 else "pending_or_not_verified","observed":actual,"target":PRICES[sku]})
        except Exception as e:failure("market_price_final",sku,e)

def run_site():
    rows=site_records()
    for sku in list(SKUS6)+list(KITS):
        matches=[x for x in rows.values() if x.get("sku")==sku]
        if len(matches)==1:
            expected=STOCK[sku]
            direct_price={KIT1:24800,KIT2:18800,SKUS6[0]:11880,SKUS6[1]:35350,SKUS6[2]:35350}[sku]
            observed=float(matches[0].get("price") or 0)
            write("tilda",sku,{"status":"price_verified" if observed==direct_price else "price_mismatch",
                               "uid":matches[0].get("uid"),"observed_price":observed,"expected_price":direct_price})
        else:write("tilda",sku,{"status":"missing_approved_public_images_or_duplicate" if len(matches)!=1 else "existing","matches":len(matches)})

def main():
    urls=valid()
    # Run each channel independently; failure in one never marks another as success.
    for label,task in [("wb",lambda:run_wb(urls)),("market",run_market),("tilda",run_site)]:
        try:task()
        except Exception as e:failure(label,"_channel",e)
    for sku in list(SKUS6)+list(KITS):
        write("ozon",sku,{"status":"not_published_by_this_recovery","reason":"Need verified exact technical/customs attributes; 6T30 covers also need public image URLs"})
        write("avito",sku,{"status":"not_published_by_this_recovery","reason":"Autoload/category processing not safely confirmed"})
if __name__=="__main__":main()
