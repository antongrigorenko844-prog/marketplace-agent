"""Apply only 22 approved LEOPARTS WB/Yandex prices, guarded and journaled.
Preflight and owner-approved manifest strictly matched. No Ozon, Avito, Tilda,
product-content, inventory or formula edits. WB async upload may quarantine prices.
New Dq500 is an already existing unique WB vendorCode alias; never create duplicate.
"""
from __future__ import annotations
import hashlib, json, os, subprocess, sys, time, traceback
from pathlib import Path
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client as wb
import yandex_market_client as ym
from openpyxl import load_workbook

APPROVED=ROOT/"data/approved_wb_ym_prices_20261123.json"
OUT=ROOT/"data/approved_wb_ym_prices_20261123_result.json"
MASTER=ROOT/"data/master_control.xlsx"
WBCAT=ROOT/"data/wb_catalog.xlsx"
SNAP=ROOT/"data/marketplace_price_preflight_20261123.json"
ALIAS="dq500-korpus-gladkiy"
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
def load():
    m=json.loads(APPROVED.read_text(encoding="utf-8"))
    s=json.loads(SNAP.read_text(encoding="utf-8"))
    require(m.get("approved_by_owner") is True and m.get("scope")==["wildberries","yandex_market"],"owner/scope approval absent")
    require(m.get("other_channels_unchanged") is True and m.get("no_automatic_formula") is True,"unexpected other-channel changes")
    require(m.get("today")==s.get("target_today") and m.get("yesterday")==s.get("target_yesterday"),"approval does not match read-only plan")
    require(s.get("status")=="complete" and s.get("external_seller_writes")==0,"preflight not complete")
    require(len(m["today"])==8 and len(m["yesterday"])==14,"wrong SKU counts")
    require(m["yesterday"]["Dq500"]==1980 and m["today"]["0am325025"]==13850,"exact protective prices absent")
    require(m["wb_new_sku_alias"]=={"master_sku":"Dq500","wb_vendor_code":ALIAS,"expected_nm_id":2338475459,"price":1980},"WB alias changed")
    targets=m["today"]|m["yesterday"]
    require(len(targets)==22 and len(set(targets))==22,"unexpected SKU overlap")
    h=hashlib.sha256(json.dumps(m,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return m,s,targets,h

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
def check_alias():
    cards=wb.list_cards(limit=100)
    require(len(cards)>120,"WB cards enumeration incomplete")
    matches=[x for x in cards if x.get("vendorCode")==ALIAS]
    kit=[x for x in cards if x.get("vendorCode")=="DQ500"]
    require(len(matches)==1 and len(kit)==1,"New Dq500 alias or protected DQ500 kit missing")
    a=matches[0];k=kit[0]
    require(int(a.get("nmID") or 0)==2338475459,"Unexpected Dq500 WB nmID")
    require(int(k.get("nmID") or 0)==837952965,"Original DQ500 kit changed")
    require("корпус" in (a.get("title") or "").lower() and "гладк" in (a.get("title") or "").lower(),"Not the smooth housing")
    require("фильтр" in (k.get("title") or "").lower(),"Not protected DQ500 kit")
    return {"master_sku":"Dq500","wb_vendorCode":ALIAS,"nmID":a["nmID"],
            "title":a.get("title"),"photos_count":len(a.get("photos") or []),
            "kit_nmID":k["nmID"]}
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

def stage_wb_catalog(targets,wb_before):
    # Only approved WB K column and WB catalog F column; never price H (Ozon).
    from collections import Counter
    allowed=set(targets)
    plans=[(MASTER,"Товары",11),(WBCAT,"WB",6)]
    staged=[];summary={}
    for path,sheet,col in plans:
        book=load_workbook(path)
        require(sheet in book.sheetnames,"Missing sheet "+sheet)
        ws=book[sheet]
        indexes={}
        for ri in range(2,ws.max_row+1):
            k=ws.cell(ri,1).value
            if isinstance(k,str) and k in allowed:
                require(k not in indexes,"Duplicate SKU "+k+" in "+sheet)
                indexes[k]=ri
        # Dq500 is an aliased WB SKU, absent from WB catalog in prior state;
        # it was created and its master WB price separately synced already.
        expected=allowed-({"Dq500"} if path==WBCAT else set())
        require(set(indexes)==expected,"Required WB workbook SKUs changed: "+str(sorted(expected-set(indexes))))
        changes=[]
        for sku,row in indexes.items():
            target=targets[sku]
            v=money(ws.cell(row,col).value)
            old=(wb_before.get(sku) or {}).get("current")
            # Current cell may already equal prior owner-approved price even if WB
            # rejected it, or may be old live price. Never overwrite arbitrary edits.
            require(v is not None and (close(v,target) or close(v,old,2.0)),
                  "Unexpected canonical price "+sku+" in "+sheet+": "+str(v))
            if not close(v,target,.01):
                ws.cell(row,col).value=int(target)
                changes.append({"sku":sku,"cell":ws.cell(row,col).coordinate,"before":v,"after":target})
        staged.append((path,book,changes))
        summary[str(path.relative_to(ROOT))]=changes
    for path,book,changes in staged:
        if changes:book.save(path)
        book.close()
    return summary

def run():
    require(os.environ.get("GITHUB_ACTIONS")=="true","Only GitHub Actions runner allowed")
    m,preflight,targets,h=load()
    if OUT.exists():
        doc=json.loads(OUT.read_text(encoding="utf-8"))
        require(doc.get("fingerprint")==h,"Existing journal not for this plan")
        # If a request has already been attempted, NEVER re-submit automatically.
        if doc.get("state") not in ("created","staged"):
            print("PRICE_SYNC_EXISTING_JOURNAL_NO_REPLAY",doc.get("state"),flush=True)
            return 0
    else:
        doc={"state":"created","fingerprint":h,"created_utc":now(),"approved":True,
             "seller_writes":{"wildberries":0,"yandex_market":0},"errors":[],
             "targets":targets,"results":{}}
        save(doc)
    # Live fresh preflight and materialization check.
    w0=wb_read(targets)
    alias=check_alias()
    require(len(alias.get("title") or "") and alias["photos_count"]>0,"Dq500 missing usable image")
    require(w0.get("Dq500",{}).get("matches_target") is True,"Dq500 must already be at 1980 WB")
    y0,other_shops,biz=market_read(targets)
    doc["before"]={"wb":w0,"ym":y0,"alias":alias,"other_market_shops":other_shops}
    save(doc)
    # For workbook staging, all SKU rows and prior prices must be guarded.
    if doc.get("state")=="created":
        doc["sources_staged"]=stage_wb_catalog(targets,w0)
        doc["state"]="staged"
        save(doc)
        persist(MASTER,WBCAT,OUT)
        print("PRICE_SYNC_MASTER_STAGED",sum(map(len,doc["sources_staged"].values())),flush=True)
    # WB: only rows needing price modification and matching prior read-only snapshot.
    wb_changes=[]; wb_skip={}
    s0=preflight["channels"]["wb"]["data"]
    for sku,target in targets.items():
        v=w0.get(sku,{})
        if sku=="Dq500":continue
        if v.get("status")!="ok":
            wb_skip[sku]="WB product missing/size ambiguous";continue
        if v.get("matches_target"):continue
        old=(s0.get(sku) or {}).get("current_prices") or []
        if len(old)!=1 or not close(v.get("current"),old[0],2):
            wb_skip[sku]="WB live price changed since approved snapshot";continue
        disc=v["discount"]
        base=int((Decimal(int(target))*Decimal(100)/Decimal(100-disc)).quantize(Decimal("1"),rounding=ROUND_HALF_UP))
        require(0<base<2000000,"WB calculated base-price invalid")
        wb_changes.append({"nmID":int(v["nmID"]),"price":base,"discount":disc})
    require(len(set(x["nmID"] for x in wb_changes))==len(wb_changes),"Duplicate WB nmID")
    doc["results"]["wb"]={"requested_skus":[sku for sku,target in targets.items() if sku!="Dq500" and w0.get(sku,{}).get("status")=="ok" and not w0[sku]["matches_target"]],
                          "submitted_nm_ids":[x["nmID"] for x in wb_changes],
                          "blocked_before_request":wb_skip,"upload_id":None}
    if wb_changes and not doc["results"]["wb"].get("sent_at"):
        doc["state"]="wb_intent_persisted";save(doc);persist(OUT)
        print("PRICE_SYNC_WB_POST_BEGIN",len(wb_changes),flush=True)
        try:
            result=wb.update_prices(wb_changes,retries=1)
            upload=(result.get("data") or {}).get("id") if isinstance(result,dict) else None
            doc["results"]["wb"].update({"sent_at":now(),"api_ack":bool(upload),"upload_id":upload})
            doc["seller_writes"]["wildberries"]=1
        except Exception as e:
            doc["results"]["wb"].update({"status":"unknown_request_outcome","error":type(e).__name__+": "+str(e)[:250]})
            doc["errors"].append("wb_unknown_outcome")
        save(doc);persist(OUT)
    else:
        doc["results"]["wb"]["status"]="nothing_to_submit"
    # YM: only prices not matching approved target and no other active stores.
    ym_changes=[];ym_skip={}
    s1=preflight["channels"]["yandex_market"]["data"]
    for sku,target in targets.items():
        v=y0.get(sku,{})
        if v.get("status")!="ok":
            ym_skip[sku]="YM product missing/overridden";continue
        if v.get("matches_target"):continue
        old=s1.get(sku,{})
        if not close(v.get("shop"),old.get("shop_basic"),.01) or not close(v.get("business"),old.get("business_price"),.01):
            ym_skip[sku]="YM price changed since approved snapshot";continue
        value={"value":int(target),"currencyId":"RUR"}
        value.update(v.get("keep_fields") or {})
        ym_changes.append({"offerId":sku,"price":value})
    doc["results"]["ym"]={"requested_skus":[v["offerId"] for v in ym_changes],"blocked_before_request":ym_skip}
    if other_shops:
        doc["results"]["ym"]["status"]="shared_business_other_active_shops";doc["results"]["ym"]["blocked_shops"]=other_shops
        doc["errors"].append("YM other shops affected; refused write")
    elif ym_changes:
        doc["state"]="ym_intent_persisted";save(doc);persist(OUT)
        print("PRICE_SYNC_YM_POST_BEGIN",len(ym_changes),flush=True)
        try:
            status,body=ym._request("POST",f"/v2/businesses/{biz}/offer-prices/updates",json_body={"offers":ym_changes},retries=1)
            doc["results"]["ym"]["sent_at"]=now()
            doc["results"]["ym"]["http_status"]=status
            doc["results"]["ym"]["api_status"]=body.get("status") if isinstance(body,dict) else None
            doc["seller_writes"]["yandex_market"]=1
            if status!=200 or not isinstance(body,dict) or body.get("status","OK")!="OK":
                doc["errors"].append("YM API returned non-success")
        except Exception as e:
            doc["results"]["ym"]["status"]="unknown_request_outcome"
            doc["results"]["ym"]["error"]=type(e).__name__+": "+str(e)[:240]
            doc["errors"].append("ym_unknown_outcome")
        save(doc);persist(OUT)
    else:
        doc["results"]["ym"]["status"]="nothing_to_submit"
    # Independent read-only after-settlement verification of all 22, plus WB upload history.
    for pause in (8,12):
        time.sleep(pause)
        try:
            wb_after=wb_read(targets)
            ym_after,_,_=market_read(targets)
            doc["results"]["wb"]["after"]=wb_after
            doc["results"]["ym"]["after"]=ym_after
            if all(v.get("matches_target") for v in wb_after.values()) and all(v.get("matches_target") for v in ym_after.values()):
                break
        except Exception as e:
            doc["results"]["readback_error"]=type(e).__name__+": "+str(e)[:230]
    uid=doc["results"]["wb"].get("upload_id")
    if uid:
        try:
            history=wb.get_price_upload_result(uid)
            doc["results"]["wb"]["upload_task"]=history.get("task")
            doc["results"]["wb"]["upload_goods"]=[
                {"nmID":x.get("nmID"),"status":x.get("status"),"errorText":str(x.get("errorText") or "")[:280]}
                for x in (history.get("goods") or [])]
        except Exception as e:
            doc["results"]["wb"]["history_error"]=type(e).__name__+": "+str(e)[:190]
    finalwb=doc["results"]["wb"].get("after") or {}
    finalym=doc["results"]["ym"].get("after") or {}
    wb_verified=[sku for sku in targets if (finalwb.get(sku) or {}).get("matches_target")]
    ym_verified=[sku for sku in targets if (finalym.get(sku) or {}).get("matches_target")]
    doc["results"]["wb"]["verified_skus"]=wb_verified
    doc["results"]["ym"]["verified_skus"]=ym_verified
    doc["results"]["wb"]["unverified_skus"]=[sku for sku in targets if sku not in wb_verified]
    doc["results"]["ym"]["unverified_skus"]=[sku for sku in targets if sku not in ym_verified]
    doc["state"]="verified_all" if len(wb_verified)==len(targets) and len(ym_verified)==len(targets) else "partial_verified"
    doc["finished_utc"]=now()
    save(doc);persist(OUT)
    print("PRICE_SYNC_FINAL",doc["state"],
          "WB",len(wb_verified),"/22 YM",len(ym_verified),"/22",
          "seller_writes",doc["seller_writes"],flush=True)
    return 0
if __name__=="__main__":
    try:sys.exit(run())
    except Exception as e:
        print("PRICE_SYNC_FAILURE",type(e).__name__,str(e)[:240],flush=True)
        if OUT.exists():
            v=json.loads(OUT.read_text(encoding="utf-8"))
            v["fatal"]={"type":type(e).__name__,"message":str(e)[:280]}
            save(v)
        raise
