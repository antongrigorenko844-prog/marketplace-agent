"""Read-only cross-check of exact two DSG kits' already recorded customs codes
in user's own Yandex Market catalog. No Market mutations and no Avito calls.
"""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import yandex_market_client as market
OUT=ROOT/"data/leoparts_ozon_tools_market_codes_20261123.json"
SKUS=["tool-dq200-acc-restore","tool-dsg-pressure-hp"]
report={"checked_utc":datetime.now(timezone.utc).isoformat(),"mode":"readonly",
        "skus":SKUS,"results":{},"external_writes":0}
try:
    business=market.get_business_id()
    status,body=market._request("POST",f"/v2/businesses/{business}/offer-mappings",
                                json_body={"offerIds":SKUS},timeout=45)
    report["http_status"]=status
    report["response_status"]=body.get("status")
    rows=(body.get("result") or {}).get("offerMappings") or []
    for item in rows:
        offer=item.get("offer") or {}
        sku=offer.get("offerId")
        if sku not in SKUS:continue
        report["results"][sku]={
            "name":offer.get("name"),
            "tnved_codes":[x.get("code") for x in offer.get("commodityCodes") or []
                           if x.get("type")=="CUSTOMS_COMMODITY_CODE" and isinstance(x,dict)],
            "legacy_customs_code":offer.get("customsCommodityCode"),
            "market_category":offer.get("marketCategoryId"),
            "manufacturer_countries":offer.get("manufacturerCountries")
        }
    for sku in SKUS:report["results"].setdefault(sku,{"market_offer_found":False})
except Exception as e:
    report["error_type"]=type(e).__name__
    report["error"]=str(e)[:250]
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
print(json.dumps(report,ensure_ascii=False),flush=True)
