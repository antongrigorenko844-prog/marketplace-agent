"""Create exactly new EA888 Gen3 oil filter set on Yandex Market,
using proper Maslyanye filtry category, not existing EA888gen3 miscategorized as magnets.
"""
import json,sys,time,traceback
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import yandex_market_client as ym
SKU="EA888gen3-filter-kit"
SOURCE="EA888gen3"
REF="Ea888gen3"
OUT=ROOT/"data/ea888_gen3_full_kit_market_result_20261123.json"
def note(k,v):
    try:x=json.loads(OUT.read_text())
    except (OSError,ValueError):x={}
    x[k]=v;x["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(x,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"stage":k,"result":v},ensure_ascii=False,default=str),flush=True)
def require(method,path,**kw):
    st,x=ym._request(method,path,**kw)
    if st!=200 or not isinstance(x,dict) or x.get("status") not in ("OK",None) or x.get("errors"):
        raise RuntimeError("Market unverified response for "+path.split("/")[-1]+" HTTP "+str(st))
    return x
def run():
    m=json.loads((ROOT/"data/ea888_gen3_filter_kit_approved_20261123.json").read_text())
    if m["sku"]!=SKU or m["source_sku"]!=SOURCE or m["price_rub"]["yandex_market"]!=7850 or m["saleable_stock"]!=40:
        raise ValueError("Owner approval mismatch")
    ids=json.loads((ROOT/"data/ea888_gen3_full_kit_photos_20261123_result.json").read_text())["urls"]
    links=[ids[SKU+f"_{i}.jpg"] for i in (1,2,3)]
    existing=ym.get_all_offers(archived=False)
    have=[z for z in existing if z.get("sku")==SKU]
    if len(have)>1:raise ValueError("Duplicate Market product")
    business=ym.get_business_id()
    category_response=require("POST",f"/v2/businesses/{business}/offer-mappings",json_body={"offerIds":[SOURCE,REF,SKU]})
    maps=(category_response.get("result") or {}).get("offerMappings") or []
    categories={}
    for r in maps:
        offer=r.get("offer") or {}
        code=offer.get("offerId")
        categories[code]={"marketCategoryId":offer.get("marketCategoryId"),"category":offer.get("category"),
                          "name":offer.get("name")}
    note("existing_category",categories)
    selected=categories.get(REF) or {}
    cat=selected.get("marketCategoryId")
    if not isinstance(cat,int) or cat<1:
        # Existing EA888 offers are legacy and have no marketCategoryId.
        # Use the official leaf category tree; never clone their incorrect
        # old categories ("Крепления магнитные"/"Ремкомплект КПП").
        tree=require("POST","/v2/categories/tree",json_body={"language":"RU"})
        root=tree.get("result") or {}
        pending=[(root,[])]
        candidates=[]
        while pending:
            node,parents=pending.pop()
            if not isinstance(node,dict):continue
            name=str(node.get("name") or "")
            children=[x for x in (node.get("children") or []) if isinstance(x,dict)]
            path=parents+[name]
            if not children and ("маслян" in name.lower() and "фильтр" in name.lower()):
                candidates.append({"id":node.get("id"),"name":name,"path":path})
            pending.extend((x,path) for x in children)
        note("category_candidates",candidates[:20])
        def score(c):
            t=" ".join(c["path"]).lower()
            return ((4 if "авто" in t else 0)+(3 if "запчаст" in t else 0)
                    +(2 if "масляные фильтры" in c["name"].lower() else 0))
        if candidates:
            candidates.sort(key=score,reverse=True)
            if len(candidates)>1 and score(candidates[0])==score(candidates[1]) and candidates[0]["id"]!=candidates[1]["id"]:
                note("blocker",{"status":"AMBIGUOUS_OIL_FILTER_CATEGORY","candidates":candidates[:5]})
                return
            cat=candidates[0]["id"]
        if not isinstance(cat,int) or cat<1:
            note("blocker",{"status":"NO_OIL_FILTER_LEAF_CATEGORY_CONFIRMED"})
            return
    if have:
        note("creation",{"status":"already_exists","sku":SKU,"market_sku":have[0].get("market_sku")})
    else:
        offer={"offerId":SKU,
            "name":m["title_by_channel"]["yandex_market"],
            "marketCategoryId":cat,
            "pictures":links,
            "vendor":"Нет бренда",
            "description":m["text"]["yandex_market"],
            "weightDimensions":{"length":18,"width":12,"height":10,"weight":0.8},
            "vendorCode":SKU}
        r=require("POST",f"/v2/businesses/{business}/offer-mappings/update",
                  json_body={"offerMappings":[{"offer":offer}]})
        note("creation",{"status":"api_accepted","category_id":cat,"response_status":r.get("status"),
                         "warnings":r.get("result",{}).get("warnings")})
    for t in (4,7,11):
        time.sleep(t)
        items=ym.get_all_offers(archived=False)
        present=[x for x in items if x.get("sku")==SKU]
        if present:break
    if not present:
        note("verification",{"status":"not_visible_in_business_yet"})
        return
    note("verification",{"status":"catalog_found","sku":SKU,"market_sku":present[0].get("market_sku"),
                         "name":present[0].get("name"),"category":present[0].get("category")})
    try:
        ok,failed=ym.update_prices({SKU:(7850,None)})
        note("price_submission",{"accepted":ok,"failed":failed})
    except Exception as e:note("price_submission",{"error_type":type(e).__name__})
    try:
        ok,failed=ym.update_stocks({SKU:40})
        note("stock_submission",{"accepted":ok,"failed":failed})
    except Exception as e:note("stock_submission",{"error_type":type(e).__name__})
    try:
        _,campaign=ym._creds()
        response=require("POST",f"/v2/campaigns/{campaign}/offers",json_body={"offerIds":[SKU]})
        matching=[x for x in ((response.get("result") or {}).get("offers") or []) if x.get("offerId")==SKU]
        p=matching[0].get("basicPrice",{}) if matching else {}
        note("final_readback",{"observed_price":p.get("value"),"verified_price":p.get("value")==7850,
             "offer_in_campaign":len(matching)==1,"offer_status":matching[0].get("status") if matching else None})
    except Exception as e:note("final_readback",{"error_type":type(e).__name__})
if __name__=="__main__":
    try:run()
    except Exception as e:
        t=traceback.extract_tb(e.__traceback__);last=t[-1] if t else None
        note("fatal",{"error_type":type(e).__name__,"file":Path(last.filename).name if last else None,
                      "line":last.lineno if last else None})
        sys.exit(1)
