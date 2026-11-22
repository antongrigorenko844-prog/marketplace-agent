"""Fix one Ozon non-published EA888 intake screw.
Category: Automotive Fasteners (74606916); Type: automotive fastener (970858643).
Only SKU vint-kollektora-ea888. Wrong old oil-filter OEMs and gearbox TNVED removed.
For existing item, Ozon requires new_description_category_id to change category.
"""
from __future__ import annotations
import json,sys,time,re,traceback,os
from datetime import datetime,timezone
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from openpyxl import load_workbook
import ozon_client
SKU="vint-kollektora-ea888"
OLD_CAT=17028751
OLD_TYPE=970863591
NEW_CAT=74606916
NEW_TYPE=970858643
TYPE_NAME="Метиз автомобильный"
MANIFEST=ROOT/"data/repair_vint_ea888_ozon_apply_20261123_request.json"
OUT=ROOT/"data/repair_vint_ea888_ozon_apply_20261123_result.json"
def note(step,v):
    try:doc=json.loads(OUT.read_text())
    except (OSError,ValueError):doc={}
    doc[step]=v
    doc["updated_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(doc,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"stage":step,"result":v},ensure_ascii=False,default=str),flush=True)
def numeric(v):
    try:return float(str(v).replace(" ",""))
    except (ValueError,TypeError):return None
def as_attr(id,value=None,dictionary=None):
    v={"value":str(value)}
    if dictionary is not None:v["dictionary_value_id"]=int(dictionary)
    return {"id":int(id),"complex_id":0,"values":[v]}
def run():
    m=json.loads(MANIFEST.read_text(encoding="utf-8"))
    req={"sku":SKU,"old_category":OLD_CAT,"old_type":OLD_TYPE,
         "new_category":NEW_CAT,"new_type":NEW_TYPE,
         "new_type_name":TYPE_NAME,"owner_price_unchanged":True}
    for k,v in req.items():
        if m.get(k)!=v:raise ValueError("Unapproved correction scope: "+k)
    code=str(m.get("tnved_code") or "")
    code_id=int(m.get("tnved_dictionary_id") or 0)
    if not re.fullmatch(r"731815\d{4}",code) or code_id<1:
        raise ValueError("Verified fastener customs code/id not provided")
    names=ozon_client.get_product_names(offer_ids=[SKU],limit=25)
    if len(names)!=1 or names[0].get("offer_id")!=SKU:
        raise ValueError("Target product absent/duplicate in Ozon attributes")
    x=names[0]
    if int(x.get("id") or 0)!=6950936854:
        raise ValueError("Unexpected Ozon product ID; abort before mutation")
    before_cat=int(x.get("description_category_id") or 0)
    before_type=int(x.get("type_id") or 0)
    if (before_cat,before_type) not in [(OLD_CAT,OLD_TYPE),(NEW_CAT,NEW_TYPE)]:
        raise ValueError("Unverified current category/type")
    title=str(x.get("name") or "")
    if "впускного коллектора" not in title.lower() or "ea888" not in title.lower() or "винт" not in title.lower():
        raise ValueError("Title mismatch; wrong physical item")
    before=ozon_client.get_product_info_list([SKU])
    moderation=[y for y in before if y.get("offer_id")==SKU]
    if len(moderation)!=1:raise ValueError("Moderation status missing")
    old_errors=moderation[0].get("errors") or []
    if (before_cat,before_type)==(NEW_CAT,NEW_TYPE) and not old_errors:
        note("result",{"status":"already_correct","category":NEW_CAT,"type":NEW_TYPE})
        return
    if (before_cat,before_type)==(OLD_CAT,OLD_TYPE) and not any(
       (str((e.get("texts") or {}).get("attribute_name"))=="Тип" or e.get("attribute_id")==8229) for e in old_errors if isinstance(e,dict)):
        raise ValueError("Expected type validation error absent; re-audit before changing category")
    # Validate live mandatory attributes for the exact NEW category/type.
    specs=ozon_client.get_category_attributes(NEW_CAT,NEW_TYPE)
    allowed={int(a["id"]) for a in specs if a.get("id")}
    mandatory={int(a["id"]) for a in specs if a.get("is_required") and a.get("id")}
    if not {85,8229,22232,23536,7202}.issubset(mandatory):
        raise ValueError("Ozon category requirements changed")
    vals=ozon_client.search_attribute_values(NEW_CAT,NEW_TYPE,22232,code,limit=60)
    matches=[z for z in vals if str(z.get("value") or "").startswith(code+" ") and int(z.get("id") or 0)==code_id]
    if len(matches)!=1:raise ValueError("Verified code dictionary entry changed")
    tnved_value=matches[0]["value"]
    typ=ozon_client.search_attribute_values(NEW_CAT,NEW_TYPE,8229,TYPE_NAME,limit=30)
    if not any(z.get("id")==NEW_TYPE and z.get("value")==TYPE_NAME for z in typ):
        raise ValueError("Metiz automotive Ozon type not confirmed")
    count=ozon_client.search_attribute_values(NEW_CAT,NEW_TYPE,7202,"1",limit=20)
    count_match=[z for z in count if str(z.get("value")).strip()=="1"]
    if not count_match:
        # Do not invent a dictionary ID for mandatory quantity.
        raise ValueError("Ozon quantity of one has no verified dictionary entry")
    original={int(z.get("id")):z for z in (x.get("attributes") or []) if z.get("id")}
    if 85 not in original or 23536 not in original:
        raise ValueError("Brand or marking attribute missing in original")
    # Copy only attributes valid for the new category. Completely remove the
    # wrong oil-filter housing part numbers (7324, 11031) and gearbox tariff.
    blacklist={7324,11031,9048,8962,22232,8229,7202}
    attrs=[dict(z) for z in (x.get("attributes") or []) if z.get("id") in allowed and z.get("id") not in blacklist]
    attrs+=[
        as_attr(8229,TYPE_NAME,NEW_TYPE),
        as_attr(22232,tnved_value,code_id),
        as_attr(7202,"1",count_match[0]["id"]),
    ]
    if {int(z.get("id")) for z in attrs}.isdisjoint({85}):
        raise ValueError("Original brand omitted")
    if not mandatory.issubset({int(z.get("id")) for z in attrs}):
        raise ValueError("Not all required attributes provided")
    # Keep approved original product photo, name, price, VAT, stock unchanged.
    images=[]
    primary=x.get("primary_image")
    if isinstance(primary,list):primary=primary[0] if primary else ""
    if primary:images.append(primary)
    for it in (x.get("images") or []):
        u=it.get("file_name") if isinstance(it,dict) else it
        if u and u not in images:images.append(u)
    if not images:raise ValueError("Existing product has no image")
    prices=ozon_client.get_prices_and_stocks([SKU])
    rows=[p for p in prices if p.get("offer_id")==SKU]
    if len(rows)!=1:raise ValueError("Live Ozon price not uniquely confirmed")
    pb=rows[0].get("price") or {}
    selling=numeric(pb.get("price"))
    if not selling or selling<=0:raise ValueError("Price missing; prevent resetting price")
    old_price=numeric(pb.get("old_price"))
    # Keep existing approved price unchanged. On a declined SKU 950 is expected,
    # but do not replace a potentially newer owner-adjusted amount.
    vat=str(pb.get("vat") or "0")
    composition=original.get(4384)
    payload={
        "offer_id":SKU,
        "name":title,
        "description_category_id":before_cat,
        "new_description_category_id": NEW_CAT if before_cat!=NEW_CAT else 0,
        "type_id":NEW_TYPE,
        "barcode":x.get("barcode") or "",
        "attributes":attrs,
        "complex_attributes":x.get("complex_attributes") or [],
        "color_image":x.get("color_image") or "",
        "images":images[1:],
        "primary_image":images[0],
        "pdf_list":x.get("pdf_list") or [],
        "currency_code":"RUB",
        "price":str(round(selling,2)),
        "old_price":str(round(old_price,2)) if old_price and old_price>selling else "0",
        "vat":vat,
        "depth":x.get("depth") or 100,
        "width":x.get("width") or 100,
        "height":x.get("height") or 100,
        "dimension_unit":x.get("dimension_unit") or "mm",
        "weight":x.get("weight") or 500,
        "weight_unit":x.get("weight_unit") or "g",
    }
    note("preflight",{"sku":SKU,"product_id":x.get("id"),
           "before_category":before_cat,"before_type":before_type,
           "new_category":NEW_CAT,"new_type":NEW_TYPE,
           "old_oem_removed":[7324,11031],
           "old_gearbox_code_removed":"8708409909",
           "tnved":code,"price_preserved":selling,
           "photo_count":len(images),"required_attribute_ids":sorted(mandatory)})
    response=ozon_client.import_products([payload])
    task_id=(response.get("result") or {}).get("task_id")
    if not task_id:raise ValueError("Ozon did not confirm import task")
    note("submission",{"status":"accepted","task_id":task_id,"sku":SKU})
    last=None
    for interval in ([3]*12)+([6]*8):
        time.sleep(interval)
        result=ozon_client.get_import_status(task_id)
        rows=[y for y in (result.get("result") or {}).get("items",[]) if y.get("offer_id")==SKU]
        if len(rows)!=1:continue
        last=rows[0]
        state=str(last.get("status") or "").lower()
        errs=last.get("errors") or []
        if errs or state not in ("pending","processing",""):
            break
    if not last:
        note("import_result",{"status":"not_verified","task_id":task_id})
        return
    err=last.get("errors") or []
    note("import_result",{"status":last.get("status"),"errors":err,"product_id":last.get("product_id"),"task_id":task_id})
    if err or str(last.get("status") or "").lower()!="imported":
        note("overall",{"status":"not_yet_imported_or_errors","task_id":task_id})
        return
    # Persist new TNVED in source catalogs, protecting every other SKU.
    changes={}
    for path,sheet,col in [("data/master_control.xlsx","Товары",16),
                           ("data/ozon_catalog.xlsx","Ozon",10)]:
        book=load_workbook(ROOT/path)
        ws=book[sheet]
        idx=[n for n in range(2,ws.max_row+1) if ws.cell(n,1).value==SKU]
        if len(idx)!=1:raise ValueError("SKU not unique in source "+path)
        cell=ws.cell(idx[0],col)
        old=cell.value
        if old!=code:
            cell.value=code
            book.save(ROOT/path)
        book.close()
        changes[path]={"before":old,"after":code}
    note("catalog_source",changes)
    # Moderation/creation can be asynchronous after successful import.
    for delay in (3,8,15,25):
        time.sleep(delay)
        current=ozon_client.get_product_names(offer_ids=[SKU],limit=25)
        info=ozon_client.get_product_info_list([SKU])
        item=[a for a in current if a.get("offer_id")==SKU]
        details=[a for a in info if a.get("offer_id")==SKU]
        category=item[0].get("description_category_id") if len(item)==1 else None
        typid=item[0].get("type_id") if len(item)==1 else None
        flags=(details[0].get("statuses") or {}) if len(details)==1 else {}
        errs=(details[0].get("errors") or []) if len(details)==1 else []
        note("readback",{"category":category,"type":typid,
                         "correct_type":category==NEW_CAT and typid==NEW_TYPE,
                         "is_created":flags.get("is_created"),
                         "moderation":flags.get("moderate_status"),
                         "status_name":flags.get("status_name"),
                         "errors":errs})
        if category==NEW_CAT and typid==NEW_TYPE and flags.get("is_created") and not errs:break
    note("overall",{"status":"imported_and_verified" if (category==NEW_CAT and typid==NEW_TYPE and flags.get("is_created") and not errs) else "imported_moderation_or_category_change_pending"})
if __name__=="__main__":
    try:run()
    except Exception as e:
        t=traceback.extract_tb(e.__traceback__)
        last=t[-1] if t else None
        note("fatal",{"status":"failed","type":type(e).__name__,"file":Path(last.filename).name if last else None,"line":last.lineno if last else None})
        sys.exit(1)
