"""Finish exact owner approved Yandex Market price for newly created EA888 Gen3 full kit.
Business uses unified default prices; campaign price writes return HTTP 423 LOCKED.
This sets ONLY one business-level price, preserving existing store catalog entries.
"""
import json,sys,time,traceback
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import yandex_market_client as ym
SKU="EA888gen3-filter-kit"
PRICE=7850
OUT=ROOT/"data/ea888_gen3_full_kit_market_price_result_20261123.json"
def note(name,v):
    try:o=json.loads(OUT.read_text())
    except (OSError,ValueError):o={}
    o[name]=v;o["checked_at_utc"]=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(o,ensure_ascii=False,indent=2,default=str)+"\n")
    print(json.dumps({"stage":name,"data":v},ensure_ascii=False,default=str),flush=True)
def call(method,path,**kwargs):
    s,b=ym._request(method,path,**kwargs)
    if s!=200 or not isinstance(b,dict) or b.get("status","OK")!="OK" or b.get("errors"):
        raise RuntimeError("Yandex HTTP "+str(s)+": "+path.split("/")[-1])
    return b
def val(x):
    try:return float(x)
    except (TypeError,ValueError):return None
def read(biz,campaign):
    offerIds=[SKU]
    campaign_data=call("POST",f"/v2/campaigns/{campaign}/offers",json_body={"offerIds":offerIds})
    business_data=call("POST",f"/v2/businesses/{biz}/offer-prices",json_body={"offerIds":offerIds})
    shop=[x for x in ((campaign_data.get("result") or {}).get("offers") or []) if x.get("offerId")==SKU]
    office=[x for x in ((business_data.get("result") or {}).get("offers") or []) if x.get("offerId")==SKU]
    return shop[0] if len(shop)==1 else None,office[0] if len(office)==1 else None
def run():
    a=json.loads((ROOT/"data/ea888_gen3_filter_kit_approved_20261123.json").read_text())
    if a.get("sku")!=SKU or a["price_rub"]["yandex_market"]!=PRICE or a["saleable_stock"]!=40:
        raise ValueError("Owner approval mismatch")
    _,camp=ym._creds();biz=ym.get_business_id()
    settings=call("POST",f"/v2/businesses/{biz}/settings",json_body={})
    unified=(settings.get("result") or {}).get("settings",{}).get("onlyDefaultPrice")
    if unified is not True:
        note("blocker",{"type":"not_a_single_default_price_business","onlyDefaultPrice":unified});return
    rows=ym.get_all_offers(archived=False)
    matched=[x for x in rows if x.get("sku")==SKU]
    if len(matched)!=1 or matched[0].get("archived"):
        note("blocker",{"type":"offer_not_confirmed_or_duplicate","match_count":len(matched)});return
    # A unified business may have another store, so ensure no other store has
    # this very new SKU active (same guard as prior successful pricing runs).
    shops=call("GET","/v2/campaigns").get("campaigns") or []
    for sibling in shops:
        if str(sibling.get("id"))==str(camp) or str((sibling.get("business") or {}).get("id"))!=str(biz):
            continue
        data=call("POST",f"/v2/campaigns/{sibling['id']}/offers",json_body={"offerIds":[SKU]})
        active=[x for x in ((data.get("result") or {}).get("offers") or [])
                if x.get("offerId")==SKU and x.get("status") not in ("ARCHIVED","DISABLED_BY_PARTNER")]
        if active:
            note("blocker",{"type":"additional_store_uses_same_sku","campaign_id":sibling.get("id")});return
    shop,office=read(biz,camp)
    if not shop or not office:
        note("blocker",{"type":"offer_not_ready_for_price","shop":bool(shop),"business":bool(office)});return
    if (shop.get("campaignPrice") or {}).get("value") is not None:
        note("blocker",{"type":"campaign_price_override"});return
    sp=shop.get("basicPrice") or {};bp=office.get("price") or {}
    before=val(bp.get("value"));shop_before=val(sp.get("value"))
    if before not in (None,0,float(PRICE)) or shop_before not in (None,0,float(PRICE)):
        note("blocker",{"type":"unexpected_existing_price","business":before,"shop":shop_before});return
    if before==PRICE and shop_before==PRICE:
        note("price_submission",{"status":"already_correct"});return
    offer={"offerId":SKU,"price":{"value":PRICE,"currencyId":"RUR"}}
    for keep in ("discountBase","minimumForBestseller","excludedFromBestsellers"):
        if bp.get(keep) is not None:
            offer["price"][keep]=bp[keep]
    reply=call("POST",f"/v2/businesses/{biz}/offer-prices/updates",json_body={"offers":[offer]})
    note("price_submission",{"status":"accepted","requested":PRICE,"ack_status":reply.get("status")})
    for seconds in (4,8,13):
        time.sleep(seconds)
        shop,office=read(biz,camp)
        observed=val((office or {}).get("price",{}).get("value"))
        basic=val((shop or {}).get("basicPrice",{}).get("value"))
        note("verification",{"market_basic_price":basic,"business_default_price":observed,
                            "price_verified":basic==PRICE and observed==PRICE,
                            "market_offer_status":(shop or {}).get("status")})
        if basic==PRICE and observed==PRICE:break
if __name__=="__main__":
    try:run()
    except Exception as e:
        t=traceback.extract_tb(e.__traceback__);last=t[-1] if t else None
        note("fatal",{"error_type":type(e).__name__,"line":last.lineno if last else None,
                      "file":Path(last.filename).name if last else None})
        sys.exit(1)
