"""LEOPARTS — exact WB/Yandex Market price comparison for user review.
READ ONLY: GET/POST query endpoints ONLY. No seller writes, no prices updated.
Covers Nov 22 approved changes + Nov 23 user-approved Ozon changes.
"""
from __future__ import annotations
from pathlib import Path
from datetime import datetime, timezone
import json
import os
import sys
import time
import hashlib
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client as wb
import yandex_market_client as ym
import ozon_client as oz

OUT=ROOT/"data/marketplace_price_preflight_20261123.json"
TODAY={
 "122390AK-AF":950,
 "WHT001922":950,
 "0am325066AC":1490,
 "0am325025":13850,
 "0am325025z":2880,
 "0am325025u":2880,
 "0am325025r":1490,
 "0AM325443D":2190,
}
YESTERDAY={
 "0am325091e":1990, "0am325091f":1990,
 "0am325025D":1880,
 "0am325091e-set":4990,"0am325091f-set":4990,
 "0am325025P":980,"vtulki-shtokov-2sht":2880,"klipsy-shtokov-2sht":1350,
 "02e305045":1980,"02E305051C":1450,"02E 305 045":2880,
 "Dq500":1980,"filtr-dsg7-dq500":1450,"DQ500":2880,
}
PLAN=TODAY|YESTERDAY
assert len(TODAY)==8 and len(YESTERDAY)==14 and len(PLAN)==22
REPORT={
 "status":"running","scope":"strict_read_only","external_seller_writes":0,
 "start_utc":datetime.now(timezone.utc).isoformat(),
 "target_today":TODAY,"target_yesterday":YESTERDAY,
 "sku_exact_count":len(PLAN),"sku_ids_exact":list(PLAN),
 "source_snapshot":"GitHub manifest and live Seller API at execution time",
 "errors":{}, "channels":{},
 "notes": [
  "WB discountedPrice is seller price after discount and may differ from buyer price.",
  "WB 'price quarantine' can reject approved prices. Never claim update succeeded from request acknowledgement alone.",
  "Yandex may have shop prices and business prices; verify both plus campaign overrides.",
  "Exact SKU spelling/case is important: Dq500 is not DQ500.",
 ]
}
def write():
 OUT.parent.mkdir(parents=True,exist_ok=True)
 OUT.write_text(json.dumps(REPORT,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
def num(v):
 if v is None:return None
 try:return round(float(str(v).replace(" ","").replace(",",".")),4)
 except (TypeError,ValueError):return None
def near(a,b,eps=1.5):
 return a is not None and b is not None and abs(float(a)-float(b))<=eps
def run_channel(name,fn):
 try: REPORT["channels"][name]={"status":"ok","data":fn()}
 except Exception as e:
  REPORT["channels"][name]={"status":"error","error_type":type(e).__name__,"message":str(e)[:230]}
  REPORT["errors"][name]=str(e)[:230]
 write()
 print("PRICE_PREFLIGHT",name,REPORT["channels"][name]["status"],flush=True)

def ozon():
 allrows=oz.get_prices_and_stocks(list(PLAN))
 ret={}
 for sku,target in PLAN.items():
  m=[x for x in allrows if x.get("offer_id")==sku]
  if len(m)!=1:ret[sku]={"status":"missing_or_duplicate","count":len(m)};continue
  p=m[0].get("price") or {}
  ret[sku]={"status":"ok","current":num(p.get("price")),
   "min_price":num(p.get("min_price")),
   "seller_price":num(p.get("marketing_seller_price")),
   "old_price":num(p.get("old_price"))}
 return ret

def wb_read():
 allrows=wb.get_prices(limit=1000)
 ret={}
 for sku,target in PLAN.items():
  found=[x for x in allrows if x.get("vendorCode")==sku]
  if len(found)!=1:
   ret[sku]={"status":"not_found" if not found else "duplicate","matched":len(found)}
   continue
  row=found[0]
  sizes=row.get("sizes") or []
  ret[sku]={"status":"ok" if len(sizes)==1 else "multiple_or_no_sizes",
   "nm_id":row.get("nmID"),"size_count":len(sizes),
   "discount":num(row.get("discount")),
   "editable_size_price":row.get("editableSizePrice"),
   "current_prices":[num(x.get("discountedPrice")) for x in sizes],
   "base_prices":[num(x.get("price")) for x in sizes],
   "matches_target":len(sizes)==1 and near(num(sizes[0].get("discountedPrice")),target)}
 return ret

def ym_read():
 _,camp=ym._creds()
 biz=ym.get_business_id()
 ret={}
 def ok(method,path,params=None,body=None):
  status,result=ym._request(method,path,params=params,json_body=body)
  if status!=200 or not isinstance(result,dict) or result.get("status","OK")!="OK":
   raise RuntimeError("Market read failed HTTP "+str(status)+" endpoint "+path)
  return result
 shop=ok("POST",f"/v2/campaigns/{camp}/offers",body={"offerIds":list(PLAN)})
 business=ok("POST",f"/v2/businesses/{biz}/offer-prices",body={"offerIds":list(PLAN)})
 shops=(shop.get("result") or {}).get("offers") or []
 businesses=(business.get("result") or {}).get("offers") or []
 by_shop={x.get("offerId"):x for x in shops}
 by_business={x.get("offerId"):x for x in businesses}
 setting=ok("POST",f"/v2/businesses/{biz}/settings",body={})
 only_default=((setting.get("result") or {}).get("settings") or {}).get("onlyDefaultPrice")
 for sku,target in PLAN.items():
  s=by_shop.get(sku)
  b=by_business.get(sku)
  if s is None or b is None:
   ret[sku]={"status":"missing_shop_or_business","shop_found":bool(s),"business_found":bool(b)}
   continue
  sp=s.get("basicPrice") or {}
  cp=s.get("campaignPrice") or {}
  bp=b.get("price") or {}
  shprice=num(sp.get("value"));bizprice=num(bp.get("value"))
  ret[sku]={"status":"ok","shop_basic":shprice,"business_price":bizprice,
   "campaign_price":num(cp.get("value")),
   "shop_offer_status":s.get("status"),
   "shop_offer_unverified":s.get("unverified"),
   "only_default_price":only_default,
   "shop_currency":sp.get("currencyId"),
   "business_currency":bp.get("currencyId"),
   "shop_matches_target":near(shprice,target,.03),
   "business_matches_target":near(bizprice,target,.03)}
 return ret
def run():
 write()
 for n,fn in [("ozon",ozon),("wb",wb_read),("yandex_market",ym_read)]:
  run_channel(n,fn)
 REPORT["status"]="complete" if not REPORT["errors"] else "partial"
 REPORT["finished_utc"]=datetime.now(timezone.utc).isoformat()
 write()
 print("PRICE_PREFLIGHT_FINAL",REPORT["status"],REPORT["errors"],flush=True)
 return 0 if REPORT["status"]=="complete" else 1
if __name__=="__main__":sys.exit(run())
