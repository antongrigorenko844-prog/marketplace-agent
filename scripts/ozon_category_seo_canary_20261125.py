"""Owner-approved existing-card category correction, one exact SKU only.
Do not invent customs codes, quantities, brand, dimensions, price or attributes.
Fail closed on unknown attribute loss, protected content changes, identity drift.
Uses the regular ozon_client and its canonical-description guard."""
import sys,json,copy,time,datetime,hashlib
from pathlib import Path
from decimal import Decimal
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client as oz
from content_manifest import check_ozon_description_write, normalize_url
SKU="EA888gen3"
ID=4463402130
OLD=(17028756,971100605)
NEW=(17028751,971071830)
NEW_TYPE="Запчасти автомобильного фильтра"
OUT=ROOT/"data/ozon_category_seo_canary_20261125.json"
def stamp():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(x):return hashlib.sha256(json.dumps(x,ensure_ascii=False,sort_keys=True,separators=(",",":"),default=str).encode()).hexdigest()
def write(d):
    d["updated"]=stamp()
    OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n")
def read():
    return json.loads(OUT.read_text())
def exact(xs,label):
    hits=[x for x in xs if x.get("offer_id")==SKU]
    if len(hits)!=1:raise ValueError(label+" exact identity missing/ambiguous")
    return hits[0]
def live():
    a=exact(oz.get_product_names(offer_ids=[SKU],limit=20),"attributes")
    info=exact(oz.get_product_info_list([SKU]),"info")
    if int(a.get("id") or 0)!=ID or int(info.get("id") or info.get("product_id") or 0)!=ID:
        raise ValueError("product_id drift")
    if not all(k in a.get("name","").lower() for k in ("ea888","06l115401s","06l115678ad","клапан","фильтр")):
        raise ValueError("title and exact physical identity mismatch")
    pr=exact(oz.get_prices_and_stocks([SKU]),"price")
    stock=exact(oz.get_stocks([SKU]),"stock")
    return a,info,pr,stock
def pair(a):return (int(a.get("description_category_id") or 0),int(a.get("type_id") or 0))
def attr_payload(a):
    return {"id":a["id"],"complex_id":a.get("complex_id",0),"values":[{k:v for k,v in val.items() if k in ("dictionary_value_id","value")} for val in a.get("values",[])]}
def gallery(a):
    p=a.get("primary_image")
    if isinstance(p,list):
        if len(p)!=1:raise ValueError("ambiguous primary image")
        p=p[0]
    if isinstance(p,dict):p=p.get("file_name") or p.get("url")
    imgs=[]
    for item in a.get("images") or []:
        u=item.get("file_name") or item.get("url") if isinstance(item,dict) else item
        if not isinstance(u,str) or not u.startswith("https://"):raise ValueError("unreadable image URL")
        if u!=p and u not in imgs:imgs.append(u)
    if not isinstance(p,str) or not p.startswith("https://"):raise ValueError("missing primary image")
    return p,imgs
def norm_gallery(a):
    p,imgs=gallery(a);return [normalize_url(x) for x in [p]+imgs]
def protected(a):
    return {"name":a.get("name"),"gallery":norm_gallery(a),"barcode":a.get("barcode"),"color_image":a.get("color_image"),"pdf_list":a.get("pdf_list") or [],"dimensions":{k:a.get(k) for k in ("depth","width","height","dimension_unit","weight","weight_unit")},"attrs":sorted((attr_payload(x) for x in a.get("attributes",[]) if x.get("id")!=8229),key=lambda x:(x["id"],x.get("complex_id",0))),"complex_attributes":a.get("complex_attributes") or []}
def prices(pr):
    p=pr.get("price") or {}
    out={}
    for k in ("price","old_price","vat"):
        if p.get(k) is None:raise ValueError("missing live "+k)
        out[k]=str(p[k])
    if Decimal(out["price"])<=0:raise ValueError("nonpositive live price")
    if Decimal(out["old_price"])<0:raise ValueError("negative old price")
    out["currency_code"]=p.get("currency_code") or pr.get("currency_code") or "RUB"
    if out["currency_code"]!="RUB":raise ValueError("currency mismatch")
    if Decimal(out["old_price"]) and Decimal(out["old_price"])<=Decimal(out["price"]):raise ValueError("inconsistent original old price")
    return out
def same_prices(a,b):
    return all(Decimal(str(a[k]))==Decimal(str(b[k])) for k in ("price","old_price","vat")) and a["currency_code"]==b["currency_code"]
def target_confirmed():
    def walk(nodes,c=None):
        for n in nodes:
            cc=n.get("description_category_id") or c
            if cc==NEW[0] and n.get("type_id")==NEW[1] and n.get("type_name")==NEW_TYPE and not n.get("disabled"):return True
            if walk(n.get("children") or n.get("types") or [],cc):return True
        return False
    if not walk(oz.get_category_tree("RU")):raise ValueError("approved destination no longer exists")
def prepare():
    d={"sku":SKU,"product_id":ID,"old":list(OLD),"new":list(NEW),"new_type":NEW_TYPE,"status":"preflight","external_writes":0,"started":stamp()};write(d)
    a,info,pr,stock=live()
    d["before"]={"attributes":a,"info":info,"price":prices(pr),"stock":stock,"protected_sha":digest(protected(a))};write(d)
    if pair(a)==NEW:
        d["status"]="already_correct";write(d);return
    if pair(a)!=OLD:raise ValueError("unexpected old category/type; do not import")
    if info.get("is_archived") or info.get("errors"):raise ValueError("archived/errored item; investigate separately")
    target_confirmed()
    specs=oz.get_category_attributes(*NEW)
    if not specs:raise ValueError("destination category specification unavailable")
    by={int(x["id"]):x for x in specs if x.get("id")}
    required={k for k,x in by.items() if x.get("is_required")}
    old_specs=oz.get_category_attributes(*OLD)
    original={int(x["id"]):x for x in a.get("attributes",[]) if x.get("id")}
    unknown=[{"id":k,"old_name":next((x.get("name") for x in old_specs if x.get("id")==k),None),"values":original[k].get("values")} for k in original if k not in by and k!=8229]
    d["schema"]={"required":[{"id":k,"name":by[k].get("name")} for k in sorted(required)],"unsupported_existing":unknown,"missing":[{"id":k,"name":by[k].get("name")} for k in sorted(required-set(original)-{8229})],"destination_specifications":[{"id":k,"name":x.get("name"),"dictionary_id":x.get("dictionary_id"),"required":x.get("is_required")} for k,x in by.items()]};write(d)
    if unknown:raise ValueError("destination would discard unreviewed existing attributes")
    if required-set(original)-{8229}:raise ValueError("required attributes missing; do not invent values")
    if any(not original[k].get("values") for k in required&set(original)-{8229}):raise ValueError("mandatory attribute has empty values")
    if not {85,22232,23536,4191}.issubset(original):raise ValueError("brand/customs/marking/description missing")
    codes=original[22232].get("values") or []
    if len(codes)!=1 or not str(codes[0].get("value","")).startswith("8421230000"):
        raise ValueError("existing code differs from verified oil-filtration code")
    code=oz.search_attribute_values(*NEW,22232,"8421230000",limit=50)
    if not any(v.get("id")==codes[0].get("dictionary_value_id") and str(v.get("value","")).startswith("8421230000") for v in code):
        raise ValueError("existing customs code not accepted for destination; do not guess")
    attrs=[attr_payload(v) for k,v in original.items() if k!=8229]
    if 8229 in required or 8229 in original:
        typ=oz.search_attribute_values(*NEW,8229,NEW_TYPE,limit=30)
        if not any(v.get("id")==NEW[1] and v.get("value")==NEW_TYPE for v in typ):
            raise ValueError("destination type dictionary not verified")
        attrs.append({"id":8229,"complex_id":0,"values":[{"dictionary_value_id":NEW[1],"value":NEW_TYPE}]})
    if a.get("complex_attributes"):
        raise ValueError("complex attributes require separate migration review")
    dims={k:a.get(k) for k in ("depth","width","height","dimension_unit","weight","weight_unit")}
    if any(not dims[k] for k in dims) or any(Decimal(str(dims[k]))<=0 for k in ("depth","width","height","weight")):
        raise ValueError("original dimensions incomplete; no guessed defaults")
    primary,imgs=gallery(a)
    if isinstance(a.get("barcode"),list):raise ValueError("multiple barcodes; preserve with separate reviewed import")
    payload={"offer_id":SKU,"name":a["name"],"description_category_id":OLD[0],"new_description_category_id":NEW[0],"type_id":NEW[1],"attributes":attrs,"complex_attributes":a.get("complex_attributes") or [],"primary_image":primary,"images":imgs,**dims,**prices(pr)}
    for k in ("barcode","color_image","pdf_list"):
        if k in a and a[k] is not None:payload[k]=a[k]
    check_ozon_description_write("/v3/product/import",{"items":[payload]})
    d["payload"]=payload;d["status"]="ready";write(d)
def submit():
    d=read()
    if d.get("status")!="ready":
        print("No approved ready plan; no mutation");return
    if d.get("sku")!=SKU or d.get("external_writes")!=0 or d.get("new")!=list(NEW) or d.get("old")!=list(OLD):
        raise ValueError("scope or journal mismatch")
    a,info,pr,stock=live()
    if pair(a)!=OLD or digest(protected(a))!=d["before"]["protected_sha"] or not same_prices(prices(pr),d["before"]["price"]):
        raise ValueError("protected product content or price changed since preflight")
    if info.get("is_archived") or info.get("errors"):raise ValueError("product state changed")
    p=d["payload"]
    if p.get("offer_id")!=SKU or (p.get("new_description_category_id"),p.get("type_id"))!=NEW:
        raise ValueError("payload scope changed")
    check_ozon_description_write("/v3/product/import",{"items":[p]})
    d["status"]="submission_started";d["external_writes"]=1;write(d)
    res=oz.import_products([p],retries=1)
    task=(res.get("result") or {}).get("task_id")
    d["import_response"]=res;d["task_id"]=task;d["status"]="submitted" if task else "submission_unconfirmed";write(d)
def verify():
    d=read()
    if d.get("status") not in ("submitted","submission_started","submission_unconfirmed","awaiting_moderation"):return
    task=d.get("task_id")
    if not task:raise ValueError("uncertain import response; manual readback before any retry")
    for i in range(8):
        result=oz.get_import_status(int(task));d["import_status"]=result;write(d)
        a,info,pr,stock=live()
        d["after"]={"attributes":a,"info":info,"price":prices(pr),"stock":stock}
        d["after_checks"]={"same_product_id":int(a["id"])==ID,"category_verified":pair(a)==NEW,"protected_unchanged":digest(protected(a))==d["before"]["protected_sha"],"price_unchanged":same_prices(prices(pr),d["before"]["price"]),"stock_snapshot_unchanged":stock==d["before"]["stock"],"errors":info.get("errors") or []}
        checks=d["after_checks"]
        if checks["category_verified"]:
            if not checks["protected_unchanged"] or not checks["price_unchanged"]:d["status"]="protected_drift_review"
            elif checks["errors"]:d["status"]="moderation_review"
            else:d["status"]="category_verified"
            write(d);return
        d["status"]="awaiting_moderation";write(d)
        if i<7:time.sleep(5)
def main():
    try:
        {"prepare":prepare,"submit":submit,"verify":verify}[sys.argv[1]]()
    except Exception as e:
        d=read() if OUT.exists() else {"sku":SKU,"external_writes":0}
        d["error"]={"stage":sys.argv[1],"type":type(e).__name__,"message":str(e)[:500]}
        if not d.get("external_writes"):d["status"]="blocked_preflight"
        else:d["status"]="uncertain_review"
        write(d);print("Blocked:",d["error"]);return
    print("Stage:",sys.argv[1],"status:",read().get("status"))
if __name__=="__main__":main()
