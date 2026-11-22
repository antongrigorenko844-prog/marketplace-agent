"""Owner approved new 6T30/6T40/6T45/6T50 products: read-only preflight.
Determine platform category examples, existing card collision and required fields.
Never mutate product/order/stock/price.
"""
from __future__ import annotations
import json,sys,traceback
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
OUT=ROOT/"data/6t_family_20261123_preflight.json"
DRAFT=ROOT/"data/supplier_batch_20261123_card_drafts.json"
SKUS=["6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2"]
def dump(o):OUT.write_text(json.dumps(o,ensure_ascii=False,indent=2,default=str)+"\n")
def safe(fn):
    try:return fn()
    except Exception as ex:
        import re
        status=re.search(r"(?:вернул|HTTP|status)\\s*(?:code)?\\s*([45]\\d\\d)",str(ex),re.I)
        return {"error":type(ex).__name__,"http_status":status.group(1) if status else None,"safe_note":str(ex).split(":")[0][:100]}
def main():
    manifest=json.loads(DRAFT.read_text())
    spec={x["sku"]:x for x in manifest["confirmed_missing_product_drafts"]}
    if sorted(spec)!=sorted(SKUS):raise ValueError("Incorrect owner-approved SKUs")
    r={"mode":"read_only","at":datetime.now(timezone.utc).isoformat(),"items":[{"sku":s,"title":spec[s]["name_ru"],"price":spec[s]["prices_rub"]["ozon"],"stock":spec[s]["stock_LEOPARTS"]} for s in SKUS]}
    import ozon_client
    def oz():
        # Keep the Ozon API query scoped to three target SKUs and two known samples.
        # A wide v3 product/list request can fail under temporary API limits.
        attrs=ozon_client.get_product_names(offer_ids=SKUS+["6t30","0am325025HX"],limit=100)
        collisions=[{"offer_id":x.get("offer_id")} for x in attrs if x.get("offer_id") in SKUS]
        selected=[]
        import re
        rx=re.compile(r"(6t30|6t40|6t45|6t50|блок.{0,20}управлен|электронн.{0,12}модул|гидроблок)",re.I)
        for x in attrs:
            if rx.search(str(x.get("name",""))+" "+str(x.get("offer_id",""))):
                d={key:x.get(key) for key in ("offer_id","name","description_category_id","type_id","weight","weight_unit","depth","width","height","dimension_unit","images")}
                d["attribute_ids"]=[z.get("id") for z in (x.get("attributes") or [])][:30]
                selected.append(d)
        return {"count":len(attrs),"collisions":collisions,"samples":selected[:35]}
    r["ozon"]=safe(oz);dump(r)
    import wb_client
    def wb():
        cards=wb_client.list_cards(limit=100)
        collision=[x.get("vendorCode") for x in cards if str(x.get("vendorCode") or "").casefold() in [s.casefold() for s in SKUS]]
        matches=[]
        import re
        rx=re.compile(r"(6t30|6t40|6t45|6t50|блок.{0,20}управлен|электронн.{0,12}модул|гидроблок)",re.I)
        for c in cards:
            if rx.search(str(c.get("title",""))+" "+str(c.get("vendorCode",""))):
                matches.append({k:c.get(k) for k in ("vendorCode","nmID","title","subjectID","subjectName","dimensions","brand")})
        return {"count":len(cards),"collisions":collision,"samples":matches[:40],
                "subjects":{k:safe(lambda k=k:wb_client.get_subjects(name=k)[:15]) for k in ("блок управления","блок АКПП","гидроблок","ЭБУ","электронный блок")}}
    r["wildberries"]=safe(wb);dump(r)
    import yandex_market_client as ym
    def market():
        offers=ym.get_all_offers(archived=False)
        return {"count":len(offers),"collisions":[x for x in offers if x.get("sku") in SKUS],
        "samples":[{"sku":x.get("sku"),"name":x.get("name"),"category":x.get("category")} for x in offers if any(z in str(x.get("name","")).lower() for z in ("6t30","6t40","блок управления","электронный модуль"))][:30]}
    r["market"]=safe(market);dump(r)
    return r
if __name__=="__main__":
    r=main()
    print(json.dumps({"finished":True,"sources":{k:{"error":v.get("error"),"collisions":len(v.get("collisions") or []),"examples":len(v.get("samples") or [])} for k,v in r.items() if isinstance(v,dict)}},ensure_ascii=False))
