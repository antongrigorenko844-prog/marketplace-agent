"""Read-only LEOPARTS Tilda catalog SKU, price and URL snapshot.

Fetches public Tilda product list with EXACT case-sensitive sku and uid.
Never mutates CommerceML, GitHub source price files, site or marketplaces.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime,timezone
from collections import Counter,defaultdict
import json,sys
import requests

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from site_links import SITE,UA,_store_parts
import site_pricing

OUT=ROOT/"data/site_price_review_live_20261124.json"
API="https://store.tildaapi.com/api/getproductslist/"
def price(v):
    try:
        a=float(str(v).replace(" ","").replace(",","."))
        return round(a,2) if a>0 else None
    except (TypeError,ValueError):return None

def get_store():
    pages=[]
    snippets=[]
    for target in ("/catalog","/"):
        try:
            res=requests.get(SITE+target,headers=UA,timeout=35)
            pages.append({"url":SITE+target,"http_status":res.status_code})
            if res.ok:snippets.append(res.text)
        except requests.RequestException as e:
            pages.append({"url":SITE+target,"error_type":type(e).__name__})
    parts,recids=_store_parts("\n".join(snippets))
    if not parts:raise RuntimeError("No public Tilda storepart ID found from catalog/home page")
    found=[]
    diagnostics=[]
    for part in sorted(parts):
        nextslice=1
        for step in range(1,40):
            query={"storepartuid":part,"recid":next(iter(recids),""),"c":"",
                   "getparts":"true","getoptions":"true",
                   "slice":nextslice,"size":100}
            resp=requests.get(API,params=query,headers=UA,timeout=40)
            resp.raise_for_status()
            obj=resp.json()
            allproducts=obj.get("products") or []
            if not isinstance(allproducts,list):raise ValueError("Invalid Tilda products structure")
            diagnostics.append({"storepart":part,"slice":nextslice,"count":len(allproducts),"keys":list(obj)[:10]})
            for p in allproducts:
                if not isinstance(p,dict):continue
                sku=str(p.get("sku") or "").strip()
                uid=str(p.get("uid") or p.get("id") or "").strip()
                url=str(p.get("url") or "").strip()
                title=str(p.get("title") or p.get("name") or "").strip()
                name=str(p.get("name") or "").strip()
                externalid=str(p.get("externalid") or p.get("external_id") or "").strip()
                editions=[]
                for ed in (p.get("editions") or []):
                    if not isinstance(ed,dict):continue
                    editions.append({"sku":str(ed.get("sku") or "").strip(),
                        "price":price(ed.get("price")),
                        "externalid":str(ed.get("externalid") or ed.get("external_id") or ""),
                        "uid":str(ed.get("uid") or ed.get("id") or "")})
                found.append({"sku":sku,"uid":uid,"title":title,"name":name,
                    "externalid":externalid,"url":url,"price":price(p.get("price")),
                    "priceold":price(p.get("priceold")),"editions":editions,
                    "storepart":part})
            nxt=obj.get("nextslice")
            if not nxt or not allproducts:break
            if int(nxt)<=nextslice:raise RuntimeError("Tilda nextslice not monotonic")
            nextslice=int(nxt)
        else:raise RuntimeError("Tilda pagination over 39 slices, stopping for safety")
    return pages,diagnostics,found

def run():
    result={"status":"started","mode":"READ_ONLY",
            "date_utc":datetime.now(timezone.utc).isoformat(),
            "source":"public Tilda Store getproductslist",
            "seller_price_changes":0,"site_price_changes":0,
            "avito_writes":0,"site_writes":0,"stock_writes":0}
    try:
        result["pages"],result["pages_api"],rows=get_store()
        result["rows"]=rows
        source_price=site_pricing.load_price_overrides(channel="site")
        result["source_override_prices"]=source_price
        result["site_only_prices"]=json.loads((ROOT/"data/site_price_only_overrides.json").read_text())
        audit=json.loads((ROOT/"data/avito_price_audit_20261124.json").read_text())
        master={p["sku"]:p for p in audit["products"] if p.get("sku") and p.get("name")}
        ids=Counter(x["sku"] for x in rows if x["sku"])
        double=[sku for sku,n in ids.items() if n>1]
        collision=defaultdict(list)
        for sku in ids:collision[sku.casefold()].append(sku)
        collision_groups=[sorted(v) for v in collision.values() if len(v)>1]
        compared=[]
        for live in rows:
            sku=live["sku"]
            p=master.get(sku)
            cfg=source_price.get(sku)
            price_type="site_override_xlsx_or_site_only_json" if cfg is not None else "unverified_catalog_fallback"
            if cfg is None and p:cfg=p.get("site_cfg")
            compared.append({
               "sku":sku,"uid":live["uid"],"title":p.get("name") if p else live["title"],
               "live_title":live["title"],"live_price":live["price"],
               "source_price":price(cfg),"source_type":price_type,
               "source_cost":p.get("cost_master") if p else None,
               "url":live["url"],"priceold":live["priceold"],
               "editions":live["editions"],"master_found":bool(p),
               "mismatch":live["price"] is not None and cfg is not None and
                          live["price"]!=price(cfg)})
        present={q["sku"] for q in rows}
        unmapped=[{"sku":sku,"name":p["name"],"source_price":source_price.get(sku,p.get("site_cfg"))}
                  for sku,p in master.items() if sku not in present]
        result["compared"]=compared
        result["missing_in_live"]=unmapped
        result["stats"]={
          "public_rows":len(rows),"exact_sku_distinct":len(ids),
          "duplicate_sku":double,
          "case_sensitive_collision_groups":collision_groups,
          "master_named":len(master),"master_missing_from_live":len(unmapped),
          "source_override_skus":len(source_price),
          "live_master_price_mismatch_count":sum(x["mismatch"] for x in compared if x["master_found"]),
          "live_missing_price_count":sum(x["live_price"] is None for x in compared),
          "has_no_master":sum(not x["master_found"] for x in compared)
        }
        result["status"]="success"
    except Exception as ex:
        result["status"]="error"
        result["error"]={"type":type(ex).__name__,"message":str(ex)[:400]}
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("TILDA_PRICE_REVIEW",json.dumps({"status":result["status"],
        "stats":result.get("stats"),"error":result.get("error")},
        ensure_ascii=False),flush=True)
    if result["status"]!="success":raise RuntimeError(str(result.get("error")))
if __name__=="__main__":run()
