"""Read-only inspection of actual unbound Avito item descriptions, media and titles.

Compares same-title but composition-unverified 990/5890 pair plus likely old-vs-new
same-product listings with differing seller prices. NEVER make seller writes.
"""
from __future__ import annotations
import json, sys, re
from pathlib import Path
from datetime import datetime, timezone
from itertools import combinations
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
OUT=ROOT/"data/avito_manual_duplicates_review_20261124.json"
# Same-title rod kits have different prices; owner suspects a 3-part kit at 990. Contents NOT confirmed.
PAIRS={
    "rod_kit_composition_unverified":[4285108577,4220729305],
    "DQ500_housing_old_new":[4348814868,4285324155,8344886348],
    "DQ500_oil_filter_old_new":[4444712249,4316875109,8346375263],
    "DQ250_oil_filter_old_new":[4444617509,8341096851],
    "DQ250_housing_old_new":[4349172359,4284955885,8416684544],
    "accumulator_ring_old_new":[4285296851,8449731691],
    "support_ring_old_new":[4284973404,8252362999],
    "solenoid_regulator_old_new":[4284950339,3965336925,4124730141],
    "GM_ring_same_title":[7260747200,8461145133],
    "hydraulic_plate_similar":[4349334275,4220734432,8385926244],
    "gasket_old_new":[3965344207,8421710248],
}
def safe_extract(x):
    if not isinstance(x,dict): return {}
    product=x.get("result") if isinstance(x.get("result"),dict) else x
    y={}
    for k in ("id","title","status","price","autoload_item_id","avito_id","category","sub_category","goods_type","item_type","condition","is_active"):
        v=product.get(k)
        if isinstance(v,(str,int,float,bool)):
            y[k]=v if not isinstance(v,str) else v[:180]
    for key in ("description","description_full","text"):
        v=product.get(key)
        if isinstance(v,str):
            # Vendor-provided product description only, no contact fields.
            y["description_excerpt"]=re.sub(r"(?is)<[^>]*>"," ",v).strip()[:1000]
            break
    for key in ("images","photos"):
        im=product.get(key)
        if isinstance(im,list):
            y["image_count"]=len(im)
            y["first_image"]=str(im[0].get("url") if isinstance(im[0],dict) else im[0])[:300] if im else ""
            break
    y["safe_product_field_keys"]=[k for k in product if k in ("id","title","status","price","autoload_item_id","category","description","photos","images","goods_type","productType")]
    return y
def main():
    account=av._request("GET","/core/v1/accounts/self",retries=1)
    account_id=account.get("id")
    if not account_id and isinstance(account.get("result"),dict):account_id=account["result"].get("id")
    if not account_id:raise RuntimeError("Self account ID not available; no alternate user lookup")
    allids=sorted({i for v in PAIRS.values() for i in v})
    live={int(x["id"]):x for x in av.list_all_items(per_page=100,max_pages=8)
          if x.get("id") is not None}
    details={}
    for aid in allids:
        result={"id":aid,"live_title":str(live.get(aid,{}).get("title") or "")[:150],
                "live_price":live.get(aid,{}).get("price"),
                "status":live.get(aid,{}).get("status")}
        try:
            answer=av._request("GET",f"/core/v1/accounts/{account_id}/items/{aid}/",retries=1)
            result["readback"]=safe_extract(answer)
            result["readback_status"]="ok"
        except Exception as exc:
            result["readback_status"]="error"
            result["error_type"]=type(exc).__name__
            result["error_message"]=str(exc)[:140]
        details[str(aid)]=result
    report={"checked_utc":datetime.now(timezone.utc).isoformat(),
            "mode":"read_only","seller_writes":0,"evaluated_ids":allids,"groups":[]}
    for category,ids in PAIRS.items():
        records=[details[str(i)] for i in ids]
        prices=[float(x["live_price"]) for x in records if isinstance(x.get("live_price"),(int,float))]
        item={"group":category,
              "user_verified_same_contents":False,
              "needs_composition_verification":True,
              "records":records,
              "price_ratio":round(max(prices)/min(prices),3) if len(prices)>1 and min(prices)>0 else None}
        report["groups"].append(item)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
    print("AVITO_MANUAL_DUPLICATES_READ_ONLY",
          json.dumps({"total_ids":len(allids),"groups":len(PAIRS),"details_read_ok":
           sum(x["readback_status"]=="ok" for x in details.values()),
           "user_pair":report["groups"][0]["records"]},ensure_ascii=False)[:2500],flush=True)
if __name__=="__main__":main()
