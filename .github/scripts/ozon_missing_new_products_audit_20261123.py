"""READ-ONLY: locate six missing LEOPARTS Ozon listings and exact blockers.

Do not create/edit/relist anything. Also compare WB existence and photograph
count; local spreadsheet/draft data are not evidence of Ozon publication.
"""
import json,sys,os
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[2];sys.path.insert(0,str(ROOT))
import ozon_client as oz
import wb_client as wb
from openpyxl import load_workbook
SKUS={
 "6t30-solenoids-gen2":"Соленоиды 6T30 семейство Gen2",
 "6t30-tcm-gen1":"Блок с соленоидами Gen1",
 "6t30-tcm-gen2":"Блок с соленоидами Gen2",
 "tool-dq200-acc-restore":"Набор восстановления гидроаккумулятора DSG7",
 "tool-dsg-pressure-hp":"Набор проверки давления DSG",
 "vint-kollektora-ea888":"Винт коллектора EA888 (control)",
}
OUT=ROOT/"data/ozon_missing_new_products_audit_20261123.json"
r={"mode":"read_only","checked_utc":datetime.now(timezone.utc).isoformat(),"items":{s:{"name":n} for s,n in SKUS.items()},"errors":[]}
def record(what,fn):
    try:fn()
    except Exception as e:r["errors"].append({"operation":what,"type":type(e).__name__,"message":str(e)[:175]})
record("ozon all-list",lambda:None)
try:
    allitems=oz.list_products(limit=100)
    r["ozon_items_returned"]=len(allitems)
    for sku in SKUS:
        matches=[x for x in allitems if str(x.get("offer_id") or "").casefold()==sku.casefold()]
        r["items"][sku]["ozon_list_found"]=bool(matches)
        r["items"][sku]["ozon_list"]=[{k:v for k,v in x.items() if k in ("offer_id","product_id","visibility","has_fbo_stocks","has_fbs_stocks","is_archived")} for x in matches]
except Exception as e:r["errors"].append({"operation":"ozon all-list","type":type(e).__name__,"message":str(e)[:170]})
try:
    info=oz.get_product_info_list(list(SKUS))
    for sku in SKUS:
        m=[x for x in info if str(x.get("offer_id") or "").casefold()==sku.casefold()]
        r["items"][sku]["ozon_info_found"]=bool(m)
        r["items"][sku]["ozon_info"]=[{k:v for k,v in x.items() if k in ("offer_id","id","product_id","is_archived","archived","visibility","status","statuses","error","errors","validation_errors","has_fbs_stocks")} for x in m]
    if any("_error" in x for x in info):
        r["errors"].append({"operation":"ozon info-list","type":"fallback_error","message":str(next(x["_error"] for x in info if "_error" in x))[:180]})
except Exception as e:r["errors"].append({"operation":"ozon info-list","type":type(e).__name__,"message":str(e)[:170]})
try:
    cards=wb.list_cards(limit=100)
    r["wb_items_returned"]=len(cards)
    for sku in SKUS:
        matches=[x for x in cards if str(x.get("vendorCode") or "").casefold()==sku.casefold()]
        r["items"][sku]["wb"]=[{"vendorCode":x.get("vendorCode"),"nmID":x.get("nmID"),"title":x.get("title"),"photo_count":len(x.get("photos") or []),"subjectID":x.get("subjectID")} for x in matches]
except Exception as e:r["errors"].append({"operation":"wb cards","type":type(e).__name__,"message":str(e)[:170]})
for x in ["ozon_catalog.xlsx","ozon_new_products.xlsx","new_products.xlsx","master_control.xlsx"]:
    p=ROOT/"data"/x
    if not p.exists():continue
    try:
        book=load_workbook(p,read_only=True,data_only=True)
        try:
            rows=list(book.active.iter_rows(min_row=2,values_only=True))
            for sku in SKUS:
                m=[r for r in rows if r and r[0] and str(r[0]).casefold()==sku.casefold()]
                r["items"][sku].setdefault("local_spreadsheets",{})[x]={"count":len(m),"rows":len(rows)}
        finally:book.close()
    except Exception as e:r["errors"].append({"operation":"xlsx "+x,"type":type(e).__name__})
for sku in SKUS:
    r["items"][sku]["media_files"]=[str(p.relative_to(ROOT)) for base in (ROOT/"photos"/sku, ROOT/"photos_avito"/sku) if base.is_dir() for p in base.iterdir() if p.is_file()]
p=ROOT/"drafts/new-products/dsg-tools-20261120/product_cards.json"
if p.exists():
    d=json.loads(p.read_text())
    r["draft_status"]={"status":d.get("status"),"publication_ready":d.get("publication_ready"),"non_executable_draft":d.get("non_executable_draft")}
    r["drafts"]={x.get("seller_sku_candidate"):{"publication_blockers":x.get("publication_blockers"),"media_status":x.get("media",{}).get("status"),"cover_verified_as_product_photograph":x.get("media",{}).get("cover_verified_as_product_photograph"),"price_ozon":x.get("commercial_inputs",{}).get("prices_rub",{}).get("ozon")} for x in d.get("products",[])}

# Discover Ozon category and type suggestions, not automatic assignment.
try:
    tree=oz.get_category_tree()
    queue=[(item, [], None) for item in tree]
    found=[]
    seen=0
    needles=("инструмент","манометр","давлен","диагност","оснаст","ремонт","автосервис","оборудован","тестер")
    while queue and seen<35000:
        item,parents,category=queue.pop(0);seen+=1
        if not isinstance(item,dict):continue
        label=str(item.get("type_name") or item.get("category_name") or item.get("name") or item.get("title") or "")
        context=parents+[label]
        cat=item.get("description_category_id") or category
        if label and any(k in label.casefold() for k in needles):
            score=sum(10 for k in ("манометр","автосервис","инструмент","оснаст","диагност","давлен") if k in label.casefold())
            score+=sum(2 for k in ("авто","набор","комплект") if k in label.casefold())
            if item.get("type_id"):score+=3
            found.append({"score":score,"name":label,"category_path":" / ".join(context[-5:]),
                         "description_category_id":cat,"type_id":item.get("type_id"),
                         "disabled":item.get("disabled")})
        for more in (item.get("children") or [],item.get("categories") or [],item.get("types") or []):
            if isinstance(more,list):
                for child in more:queue.append((child,context,cat))
    found.sort(key=lambda x:(-x["score"],not bool(x["type_id"]),x["category_path"]))
    r["ozon_category_probe"]={"status":"ok","nodes_scanned":seen,
       "candidate_count":len(found),"candidates":found[:120],
       "note":"Category labels and type IDs are suggestions, not publication approvals. Check mandatory attributes for exact toolkit."}
except Exception as e:
    r["ozon_category_probe"]={"status":"unavailable","error_type":type(e).__name__,
                              "error":str(e)[:170]}


# Mandatory field candidates for toolkit types (never publish based on a guess).
r["tool_category_attributes"]=[]
for title,description_category_id,type_id in [
    ("Специнструмент для авто",83250454,97469),
    ("Набор инструментов для автомобиля",83250454,971437067),
    ("Манометр автомобильный",83250454,92176),
]:
    try:
        attrs=oz.get_category_attributes(description_category_id,type_id)
        short=[]
        for a in attrs:
            if a.get("is_required") or a.get("is_aspect") or a.get("is_collection"):
                short.append({k:a.get(k) for k in ("id","name","description","is_required","is_collection","type","dictionary_id","max_value_count") if k in a})
        r["tool_category_attributes"].append({"type_name":title,"description_category_id":description_category_id,
           "type_id":type_id,"attribute_count":len(attrs),"mandatory_or_special":short[:45]})
    except Exception as e:
        r["tool_category_attributes"].append({"type_name":title,"error_type":type(e).__name__})


# Ozon-only: inspect actual seller category/type/required attribute values from
# live listings, after the fixed plural /v4/products/info/attributes route.
# Never infer a new item's customs code from an unrelated seller listing.
try:
    attr_cards=oz.get_product_names(limit=200)
    samples=[]
    import re
    target=re.compile(r"6t30|6t40|6t45|6t50|соленоид|манометр|специнструмент|давлен|гидроакк|набор.{0,20}инструмент|блок.{0,10}управлен",re.I)
    for card in attr_cards:
        sku=str(card.get("offer_id") or "")
        name=str(card.get("name") or "")
        if not target.search(sku+" "+name):continue
        select_attrs=[]
        for a in card.get("attributes") or []:
            if a.get("id") in (85,8229,22232,23536,4389,7236,9024) or "ТН ВЭД" in str(a.get("name")):
                values=a.get("values") or []
                select_attrs.append({"id":a.get("id"),"name":a.get("name"),
                    "values":[{"value":v.get("value"),"dictionary_value_id":v.get("dictionary_value_id")}
                              for v in values[:4] if isinstance(v,dict)]})
        samples.append({"offer_id":sku,"name":name[:150],
            "description_category_id":card.get("description_category_id"),
            "type_id":card.get("type_id"),"attributes":select_attrs})
    r["verified_catalog_candidates"]={"status":"ok","rows_scanned":len(attr_cards),
        "matches":len(samples),"examples":samples[:90]}
except Exception as e:
    r["verified_catalog_candidates"]={"status":"error","error_type":type(e).__name__,
        "error":str(e)[:190]}


# The documented Ozon attributes endpoint currently returns 404 on the
# broad catalog filter. Check exact existing product IDs with safe read-only
# payloads before calling this a permission, route or data error.
r["ozon_endpoint_probes"]=[]
try:
    known=next((x for x in oz.list_products(limit=100)
                if str(x.get("offer_id") or "")=="vint-kollektora-ea888"),None)
    if known:
        pid=known.get("product_id")
        for label,endpoint,body in [
            ("singular_product_id","/v4/product/info/attributes",{"filter":{"product_id":[pid]},"limit":1}),
            ("singular_offer_id","/v4/product/info/attributes",{"filter":{"offer_id":["vint-kollektora-ea888"]},"limit":1}),
            ("singular_all","/v4/product/info/attributes",{"filter":{"visibility":"ALL"},"limit":1}),
            ("plural_product_id","/v4/products/info/attributes",{"filter":{"product_id":[pid]},"limit":1}),
            ("product_description","/v1/product/info/description",{"offer_id":"vint-kollektora-ea888"}),
        ]:
            try:
                reply=oz._post(endpoint,body,retries=1)
                result=reply.get("result")
                item_count=len(result) if isinstance(result,list) else len(result.get("items") or []) if isinstance(result,dict) else 0
                r["ozon_endpoint_probes"].append({"label":label,"status":"ok","items":item_count,
                    "result_type":type(result).__name__,"keys":list(reply)[:10]})
            except Exception as e:
                r["ozon_endpoint_probes"].append({"label":label,"status":"error",
                    "type":type(e).__name__,"message":str(e)[:135]})
    else:
        r["ozon_endpoint_probes"].append({"status":"no_known_existing_product"})
except Exception as e:
    r["ozon_endpoint_probes"].append({"status":"error","message":str(e)[:145]})

OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n")
print(json.dumps({"items":r["items"],"drafts":r.get("drafts"),"errors":r["errors"]},ensure_ascii=False),flush=True)
