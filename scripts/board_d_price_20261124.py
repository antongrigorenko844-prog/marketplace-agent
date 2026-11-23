from __future__ import annotations
import json, os, subprocess, sys, time
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
ROOT=Path.cwd()
import wb_client as wb
import yandex_market_client as ym
from openpyxl import load_workbook
OUT=ROOT/"data/board_d_price_20261124_result.json"
SKU="0AM927769D"
TARGET=74850
ALIAS="unused"
def now():return datetime.now(timezone.utc).isoformat()
def require(test,msg):
    if not test:raise RuntimeError("GUARD_BLOCKED: "+str(msg))
def money(v):
    if v is None:return None
    try:return float(str(v).replace(",","."))
    except (TypeError,ValueError):return None
def close(a,b,tolerance=1.5):
    return a is not None and b is not None and abs(float(a)-float(b))<=tolerance
def save(j):
    j["updated_utc"]=now()
    OUT.write_text(json.dumps(j,indent=2,ensure_ascii=False,default=str)+"\n",encoding="utf-8")
def persist(*items):
    rel=[str(Path(x).relative_to(ROOT)) for x in items]
    subprocess.run(["bash",".github/scripts/save_data.sh",*rel],cwd=ROOT,check=True,timeout=180)
def wb_read(targets):
    rows=wb.get_prices(limit=1000)
    require(isinstance(rows,list) and len(rows)>100,"WB price list unexpectedly incomplete")
    out={}
    for sku,target in targets.items():
        vcode=ALIAS if sku=="Dq500" else sku
        matches=[x for x in rows if x.get("vendorCode")==vcode]
        if len(matches)!=1:
            out[sku]={"status":"missing_or_duplicate","matching_count":len(matches)}
            continue
        item=matches[0]
        sizes=item.get("sizes") or []
        if len(sizes)!=1 or item.get("editableSizePrice"):
            out[sku]={"status":"size_specific_or_missing","size_count":len(sizes)}
            continue
        disc=money(item.get("discount"))
        if disc is None or not (0<=disc<100) or int(disc)!=disc:
            out[sku]={"status":"invalid_discount"};continue
        price=money(sizes[0].get("discountedPrice"))
        out[sku]={"status":"ok","vendorCode":vcode,"nmID":item.get("nmID"),
                   "discount":int(disc),"current":price,
                   "base":money(sizes[0].get("price")),
                   "target":target,"matches_target":close(price,target)}
    return out
def market_read(targets):
    _,camp=ym._creds()
    biz=ym.get_business_id()
    def api(method,path,payload=None):
        status,response=ym._request(method,path,json_body=payload,retries=2)
        require(status==200 and isinstance(response,dict) and response.get("status","OK")=="OK",
                "Market read failed: "+path+" status "+str(status))
        return response
    bycamp=(api("POST",f"/v2/campaigns/{camp}/offers",{"offerIds":list(targets)}).get("result") or {}).get("offers") or []
    bybiz=(api("POST",f"/v2/businesses/{biz}/offer-prices",{"offerIds":list(targets)}).get("result") or {}).get("offers") or []
    settings=(api("POST",f"/v2/businesses/{biz}/settings",{}).get("result") or {}).get("settings") or {}
    require(settings.get("onlyDefaultPrice") is True,"Business has additional price levels")
    a={x.get("offerId"):x for x in bycamp}
    b={x.get("offerId"):x for x in bybiz}
    mapped={x["sku"]:x for x in ym.get_all_offers(archived=False) if x.get("sku") in targets}
    out={}
    for sku,target in targets.items():
        s=a.get(sku);v=b.get(sku)
        if s is None or v is None or sku not in mapped or mapped[sku].get("archived") or not mapped[sku].get("market_sku"):
            out[sku]={"status":"sku_missing_or_unmapped"};continue
        bp=s.get("basicPrice") or {}
        op=v.get("price") or {}
        if s.get("campaignPrice") or s.get("unverified") or bp.get("currencyId") not in ("RUR","RUB") or op.get("currencyId") not in ("RUR","RUB"):
            out[sku]={"status":"override_or_currency_or_unverified"};continue
        x=money(bp.get("value"));y=money(op.get("value"))
        if not close(x,y,.01):
            out[sku]={"status":"shop_business_price_conflict","shop":x,"business":y};continue
        out[sku]={"status":"ok","shop":x,"business":y,"target":target,
          "matches_target":close(x,target,.01),
          "keep_fields":{k:op[k] for k in ("discountBase","minimumForBestseller","excludedFromBestsellers") if op.get(k) is not None}}
    # Default business prices can affect other stores; protect them.
    shops=api("GET","/v2/campaigns").get("campaigns") or []
    others=[]
    for row in shops:
        if int((row.get("business") or {}).get("id") or 0)==int(biz) and str(row.get("id"))!=str(camp):
            checked=api("POST",f"/v2/campaigns/{row['id']}/offers",{"offerIds":list(targets)})
            active={x.get("offerId") for x in (checked.get("result") or {}).get("offers") or []
                    if x.get("status") not in ("ARCHIVED","DISABLED_BY_PARTNER")}
            if active:others.append({"other_campaign":row["id"],"active_skus":sorted(active)})
    return out,others,biz


def main():
    require(os.environ.get("GITHUB_ACTIONS")=="true","runner only")
    require(not OUT.exists(),"journal exists; no automatic replay")
    targets={SKU:TARGET}
    w=wb_read(targets)[SKU]
    y,others,biz=market_read(targets)
    y=y[SKU]
    require(w["status"]=="ok" and y["status"]=="ok","missing or ambiguous card")
    require(y["matches_target"] or not others,"other shops affected")
    # Guard canonical Ozon price and unique WB source rows before any write.
    staged=[]
    for path,sheet,col in [(ROOT/"data/master_control.xlsx","Товары",11),(ROOT/"data/wb_catalog.xlsx","WB",6)]:
        book=load_workbook(path)
        ws=book[sheet]
        rows=[r for r in range(2,ws.max_row+1) if ws.cell(r,1).value==SKU]
        require(len(rows)==1,"ambiguous workbook SKU")
        r=rows[0]
        if sheet=="Товары":
            require(close(money(ws.cell(r,8).value),TARGET,.01),"Ozon canonical price changed")
        old=money(ws.cell(r,col).value)
        require(close(old,TARGET) or close(old,78800.15,2) or close(old,w["current"],2),"unexpected WB source price")
        staged.append((path,book,ws,r,col))
    j={"sku":SKU,"target":TARGET,"before":{"wb":w,"ym":y},"state":"intent","other_channels_untouched":True}
    save(j);persist(OUT)
    if not w["matches_target"]:
        base=int((Decimal(TARGET)*100/Decimal(100-w["discount"])).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        j["wb_ack"]=wb.update_prices([{"nmID":int(w["nmID"]),"price":base,"discount":w["discount"]}],retries=1)
        save(j);persist(OUT)
    if not y["matches_target"]:
        price={"value":TARGET,"currencyId":"RUR",**y["keep_fields"]}
        status,body=ym._request("POST",f"/v2/businesses/{biz}/offer-prices/updates",json_body={"offers":[{"offerId":SKU,"price":price}]},retries=1)
        j["ym_ack"]={"status":status,"body":body}
        save(j);persist(OUT)
    for pause in (5,10,15):
        time.sleep(pause)
        wa=wb_read(targets)[SKU]
        ya=market_read(targets)[0][SKU]
        j["after"]={"wb":wa,"ym":ya}
        if wa.get("matches_target") and ya.get("matches_target"):break
    if j["after"]["wb"].get("matches_target"):
        changed=[]
        for path,book,ws,r,col in staged:
            if not close(money(ws.cell(r,col).value),TARGET,.01):
                ws.cell(r,col).value=TARGET
                book.save(path);changed.append(path)
            book.close()
        if changed:persist(*changed)
    j["state"]="verified" if all(x.get("matches_target") for x in j["after"].values()) else "partial"
    save(j);persist(OUT)
    require(j["state"]=="verified","readback mismatch")
if __name__=="__main__":main()
