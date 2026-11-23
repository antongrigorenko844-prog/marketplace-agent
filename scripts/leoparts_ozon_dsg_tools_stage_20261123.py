"""Ozon-only exact-media staging for two approved DSG service tools.
Never invent a customs code. Does not submit unclassified products to Seller API.
"""
from __future__ import annotations
import hashlib, json, os, sys
from pathlib import Path
from datetime import datetime, timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import ozon_client as oz
import photo_host
MANIFEST=ROOT/"data/product_intake_v2/requests/leoparts-five-20261123.json"
OUT=ROOT/"data/leoparts_ozon_dsg_tools_stage_20261123_result.json"
SKUS=("tool-dq200-acc-restore","tool-dsg-pressure-hp")
EXPECTED={"tool-dq200-acc-restore":38800,"tool-dsg-pressure-hp":28800}
RESULT={"channel":"ozon","mode":"upload_owner_approved_media_and_verify_exact_ozon_tool_taxonomy",
        "seller_product_writes":0,"started_utc":datetime.now(timezone.utc).isoformat(),
        "products":{}}
def save():
    OUT.write_text(json.dumps(RESULT,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf8")
    print("OZON_TOOL_STAGING",json.dumps({k:v.get("stage") for k,v in RESULT["products"].items()},ensure_ascii=False),flush=True)
def require(ok,msg):
    if not ok: raise ValueError(msg)
def main():
    manifest=json.loads(MANIFEST.read_text(encoding="utf8"))
    require(manifest.get("approved") is True and manifest.get("policy",{}).get("never_infer_customs") is True,
            "Owner batch or customs guard not approved")
    items={r["sku"]:r for r in manifest["items"]}
    catalog={str(x.get("offer_id")):x for x in oz.list_products(limit=100)}
    # Ozon API category/type already verified from its category tree in this seller account.
    cat,typ=83250454,97469
    attrs=oz.get_category_attributes(cat,typ)
    require(attrs,"Ozon current special auto tool attributes could not be fetched")
    required=[{"id":a.get("id"),"name":a.get("name"),"is_required":a.get("is_required")}
              for a in attrs if a.get("is_required")]
    require(any(x.get("id")==22232 for x in required),"No verified customs attribute")
    RESULT["ozon_tool_category"]={"category":cat,"type_id":typ,
        "name":"Автотовары / Товары для гаражей и автосервисов / Специнструмент для авто",
        "required_attributes":required}
    for sku in SKUS:
        item=items[sku]
        v=RESULT["products"].setdefault(sku,{})
        require(item["prices"]["ozon"]==EXPECTED[sku] and item["stock"]==3,
                "Commercial inputs changed "+sku)
        v["price_rub"]=EXPECTED[sku]
        v["stock_intended"]=3
        v["existing_ozon_product_id"]=catalog.get(sku,{}).get("product_id")
        v["media"]=[]
        for n,m in enumerate(item["media"],start=1):
            require(m.get("approved") is True and m.get("source")=="repository","Unapproved tool media "+sku)
            p=(ROOT/m["path"]).resolve()
            require(p.is_relative_to(ROOT) and p.is_file(),"Missing tool media "+str(p))
            digest=hashlib.sha256(p.read_bytes()).hexdigest()
            require(digest==m["sha256"],"Tool media hash changed "+sku)
            url=photo_host.upload_file(str(p),sku+"_"+str(n)+p.suffix.lower())
            require(url.startswith("https://github.com/"),"Tool media public URL not hosted")
            v["media"].append({"original_path":m["path"],"sha256":digest,
                               "url":url,"role":"main" if n==1 else "additional"})
        require(len(v["media"])==len(item["media"]), "Incomplete uploaded media "+sku)
        v["title"]=item["title"]
        v["dimensions"]=item["dimensions"]
        v["stage"]="media_ready_customs_unverified" if not v["existing_ozon_product_id"] else "already_in_ozon"
        v["blocker"]="Mandatory customs classification for exact kit not owner-/officially-verified"
        save()
    for name, vtype in [("Набор инструментов для автомобиля", 971437067),
                        ("Специнструмент для авто", 97469),
                        ("Манометр автомобильный", 92176)]:
        try:
            rows=oz.get_category_attributes(cat,vtype)
            RESULT.setdefault("candidate_ozon_types_read_only",[]).append({
                "name":name, "type_id":vtype,
                "mandatory":[{"id":a.get("id"),"name":a.get("name")}
                             for a in rows if a.get("is_required")]})
        except Exception as e:
            RESULT.setdefault("candidate_ozon_types_read_only",[]).append({
                "name":name,"type_id":vtype,"error":str(e)[:135]})
    for needle in ("902620","820559","848180","848110","847989"):
        try:
            vals=oz.search_attribute_values(cat,typ,22232,needle,limit=15)
            RESULT.setdefault("customs_dictionary_candidates_read_only",{})[needle]=vals[:15]
        except Exception as e:
            RESULT.setdefault("customs_dictionary_candidates_read_only",{})[needle]={"error":str(e)[:180]}
    save()

if __name__=="__main__":
    try:main()
    except Exception as e:
        RESULT["stage"]="blocked_or_error"
        RESULT["error_type"]=type(e).__name__
        RESULT["error"]=str(e)[:250]
        save()
        raise
