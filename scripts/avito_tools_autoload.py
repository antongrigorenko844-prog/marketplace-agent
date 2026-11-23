"""Second Avito autoload feed for two approved DSG workshop toolkits. Guarded.
Never change the existing 127-row parts feed, prices, status, stock or media.
"""
from __future__ import annotations
import hashlib, json, re, sys, time
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree as ET
import requests
from openpyxl import load_workbook
from PIL import Image
from io import BytesIO

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import avito_client as av
import avito_feed as parts
from scripts.apply_dq200_prices_20261120 import gh, get_avito_asset, rename_asset, STABLE_AVITO_URL
from scripts.publish_avito_titles_20261121 import verified_download

BATCH=ROOT/"data/product_intake_v2/requests/leoparts-five-20261123.json"
REQUEST=ROOT/"data/avito_tools_autoload_request.json"
REPORT=ROOT/"data/avito_tools_autoload_result.json"
PARTS=ROOT/"data/avito_feed.xlsx"
STABLE_TOOLS="avito_tools_feed.xml"
TOOLS_URL="https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/"+STABLE_TOOLS
CAT={
 "tool-dq200-acc-restore":{"leaf":"drugoe_2304154","type":"Другое",
    "title":"Набор для восстановления гидроаккумулятора DQ200",
    "media":[("tool-dq200-acc-restore_1.png","0ba3a8ae0243d57f58f18ef2fdc12c942fc976ca150978e0b2f386835a3841c9")]},
 "tool-dsg-pressure-hp":{"leaf":"manometry","type":"Манометры",
    "title":"Набор для проверки давления DSG",
    "media":[("tool-dsg-pressure-hp_1.png","ed59977ea06c70e9b27d80c19e4868b962e8c57f8c22c7d88e9edb4af0713d45"),
             ("tool-dsg-pressure-hp_2.png","0e8d01b2ef73bb4d842599d922ee6f4ad4e21caadff5083f01a0d9abaf9094af")]},
}
FIELDS={
 "Category":"Запчасти и аксессуары","GoodsType":"Инструменты",
 "AdType":"Товар приобретен на продажу",
 "GoodsSubType":"Диагностический и измерительный инструмент",
 "Condition":"Новое","ListingFee":"Package"
}
def require(x,why):
    if not x:raise ValueError(why)
def sha(x):return hashlib.sha256(x).hexdigest()
def api(method,path,**kw):return av._request(method,path,retries=1,**kw)
def short(x):return str(x)[:160]
def avito_profile():
    p=api("GET","/autoload/v2/profile")
    require(p.get("autoload_enabled") is True,"Paid autoload profile not enabled")
    feeds=p.get("feeds_data")
    require(isinstance(feeds,list) and 1<=len(feeds)<=3,"Unexpected autoload feeds layout")
    require(any(f.get("feed_url")==STABLE_AVITO_URL for f in feeds),"Primary parts feed not found; preserving")
    require(p.get("schedule"),"Autoload schedule missing")
    return p
def verify_category_schema(slug,category_type):
    obj=api("GET",f"/autoload/v1/user-docs/node/{slug}/fields")
    fields={x["tag"]:(x.get("content") or [{}])[0] for x in obj.get("fields") or []}
    for name,value in {**FIELDS,"ToolType":category_type}.items():
        if name not in fields: 
            if name=="ListingFee":continue
            raise ValueError("Missing official mandatory category field "+name)
        vals=fields[name].get("values") or []
        if vals and value not in [v.get("value") for v in vals]:
            raise ValueError("Category field value invalid "+name+":"+value)
    configured={"Id","Address","Category","Description","Title","Price","GoodsType",
                "AdType","GoodsSubType","ToolType","Condition"}
    required={k for k,v in fields.items() if v.get("required") is True}
    require(required<=configured,"Other required fields unresolved: "+str(sorted(required-configured)))
    return sorted(required)
MEDIA_PROVENANCE=[]
def verify_media(name,expected,mode):
    uri="https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/"+name
    r=requests.get(uri,timeout=80)
    require(r.ok,"Tool media unavailable "+name)
    require(sha(r.content)==expected,"Approved original tool photo digest differs "+name)
    with Image.open(BytesIO(r.content)) as im:
        w,h=im.size
        require(min(w,h)>=400,"Tool image too small "+name)
        if abs(w-h)/max(w,h)<=0.07:
            MEDIA_PROVENANCE.append({"original":name,"original_size":[w,h],"operation":"unchanged"})
            return uri
        # Convert only aspect ratio, not artwork. No cropping, stretching, text
        # replacement, product edits or swaps; preserve every original pixel.
        rgb=im.convert("RGB")
        side=max(w,h)
        padded=Image.new("RGB",(side,side),"white")
        padded.paste(rgb,((side-w)//2,(side-h)//2))
        out=BytesIO()
        padded.save(out,"PNG")
        blob=out.getvalue()
    variant=name.rsplit(".",1)[0]+"_avito_square_"+sha(blob)[:12]+".png"
    variant_url="https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/"+variant
    status="square_padding_preview"
    if mode=="apply":
        release=gh("GET","/releases/tags/product-photos").json()
        found=[x for x in release.get("assets",[]) if x.get("name")==variant]
        require(len(found)<=1,"Duplicate square cover release asset")
        if not found:
            asset=gh("POST",release["upload_url"].split("{",1)[0],
                     params={"name":variant},data=blob,headers={"Content-Type":"image/png"}).json()
            url=asset["browser_download_url"]
        else:
            url=found[0]["browser_download_url"]
        verified_download(url,blob)
        status="padded_square_published_sha_verified"
    MEDIA_PROVENANCE.append({"original":name,"original_size":[w,h],"derived":variant,
        "new_size":[side,side],"source_sha256":expected,"derived_sha256":sha(blob),
        "operation":status,"cropped":False,"artwork_changed":False})
    return variant_url
def build(items,address,mode):
    root=ET.Element("Ads",{"formatVersion":"3","target":"Avito.ru"})
    seen={}
    schema={}
    for sku,config in CAT.items():
        item=items[sku]
        require(item["sku"]==sku and "avito" in item["channels"],"Approved batch channel mismatch")
        require(0<int(item["stock"]),"Stock missing or zero "+sku)
        require(item["prices"]["avito"] in (24800,18800),"Approved price changed "+sku)
        require(len(config["title"])<=50,"Truncated title needed")
        schema[sku]=verify_category_schema(config["leaf"],config["type"])
        ad=ET.SubElement(root,"Ad")
        vals={"Id":sku,"Address":address,"Title":config["title"],"Description":item["description"],
              "Price":str(item["prices"]["avito"]),**FIELDS,"ToolType":config["type"]}
        for k,v in vals.items():ET.SubElement(ad,k).text=v
        imgs=ET.SubElement(ad,"Images")
        for name,checksum in config["media"]:
            uri=verify_media(name,checksum,mode)
            ET.SubElement(imgs,"Image",{"url":uri})
            seen[name]=checksum
    raw=b'<?xml version="1.0" encoding="utf-8"?>\n'+ET.tostring(root,encoding="utf-8",xml_declaration=False)
    require(b"<ListingFee>Package</ListingFee>" in raw and len(root)==2,"Unsafe tool feed")
    return raw,seen,schema
def publish_release(raw):
    release,asset,current_parts=get_avito_asset()
    require(current_parts==PARTS.read_bytes(),"Existing 127-item Avito feed changed, stop")
    candidates=[a for a in release["assets"] if a.get("name")==STABLE_TOOLS]
    require(len(candidates)<=1,"Duplicate published tool feed asset")
    if candidates:
        old=candidates[0]
        response=requests.get(old["browser_download_url"],timeout=80)
        require(response.ok,"Cannot inspect existing tools feed")
        if response.content==raw:
            return "already_published_verified",None
        raise ValueError("Existing tools feed differs, requires human review")
    stage_name="avito_tools_feed.staged_"+sha(raw)[:12]+".xml"
    # Publication is append-only and leaves primary 127-row parts asset untouched.
    staged=gh("POST",release["upload_url"].split("{",1)[0],
        params={"name":stage_name},data=raw,
        headers={"Content-Type":"application/xml"}).json()
    verified_download(staged["browser_download_url"],raw)
    rename_asset(staged["id"],STABLE_TOOLS)
    verified_download(TOOLS_URL,raw)
    return "new_verified",staged["id"]
def profile_payload(profile):
    incoming=profile.get("feeds_data") or []
    require(not any(f.get("feed_url")==TOOLS_URL for f in incoming),"Already registered tool URL")
    require(len(incoming)<3,"Avito profile already has three feeds")
    # A legacy v2 GET may contain an empty feed_name; the current v2 WRITE
    # requires a usable nonempty string. Only normalize this display label,
    # never change the existing feed URL or schedule.
    feeds=[{"feed_name":f.get("feed_name") or "LEOPARTS запчасти","feed_url":f["feed_url"]} for f in incoming]
    feeds.append({"feed_name":"LEOPARTS инструменты DSG","feed_url":TOOLS_URL})
    payload={"autoload_enabled":True,"feeds_data":feeds,
             "schedule":profile["schedule"]}
    # Unlike GET /profile, the v2 WRITE schema requires a STRING report_email.
    # Reuse the existing email, otherwise derive ONLY from the same Avito owner
    # account; never invent the address or commit it in GitHub audit artifacts.
    address=profile.get("report_email")
    origin="existing_avito_profile"
    if not isinstance(address,str) or "@" not in address:
        self_info=api("GET","/core/v1/accounts/self")
        address=self_info.get("email")
        if not address and isinstance(self_info.get("result"),dict):
            address=self_info["result"].get("email")
        origin="own_avito_account"
    require(isinstance(address,str) and address.count("@")==1 and "." in address.rsplit("@",1)[-1] and " " not in address and len(address)<255,
            "Avito write requires valid report_email; existing profile and own account provide none")
    payload["report_email"]=address
    return payload,origin
def live_duplicate_guard():
    live=av.list_all_items(statuses=["active","old","blocked","rejected"],per_page=100,max_pages=8)
    for item in live:
        t=str(item.get("title") or "").lower()
        if "набор" in t and (("давлени" in t and "dsg" in t) or
            ("гидроаккумулятор" in t and ("азот" in t or "восстановлен" in t))):
            raise ValueError("Potential existing Avito toolkit; refuse duplicate "+str(item.get("id")))
    return len(live)
def run():
    req=json.loads(REQUEST.read_text(encoding="utf8"))
    mode=req.get("mode")
    require(mode in ("inspect","apply"),"Invalid tools mode")
    require(req.get("owner_approved") is True,"Missing prior user approval")
    d=json.loads(BATCH.read_text(encoding="utf8"))
    require(d.get("approved") is True and d.get("policy",{}).get("no_duplicate_create") is True,"Unapproved tools")
    items={x["sku"]:x for x in d["items"]}
    profile=avito_profile()
    require(len([f for f in profile["feeds_data"] if f.get("feed_url")==TOOLS_URL])<=1,"Duplicate feed links")
    snap=load_workbook(PARTS,read_only=True)
    ws=snap[parts.SHEET_ADS]
    addr=str(ws.cell(5,parts.COL_ADDRESS).value or "").strip()
    require(addr,"Seller address not available")
    require(sum(1 for row in ws.iter_rows(min_row=5) if row[parts.COL_ID-1].value)==127,
            "Expected immutable 127-row parts feed not found")
    snap.close()
    source_sha=sha(PARTS.read_bytes())
    raw,media,schemas=build(items,addr,mode)
    report={"checked_at_utc":datetime.now(timezone.utc).isoformat(),"mode":mode,
          "tool_skus":list(CAT),"prices_rub":{s:items[s]["prices"]["avito"] for s in CAT},
          "media_sha256":media,"media_derivatives":MEDIA_PROVENANCE,"official_category_required":schemas,
          "payload_sha256":sha(raw),"parts_sha256":source_sha,
          "primary_feed_unchanged":True,"current_feed_count":len(profile["feeds_data"]),
          "new_listing_fee":"Package","new_wallet_billing_allowed":False,"seller_writes":0}
    if any(f["feed_url"]==TOOLS_URL for f in profile["feeds_data"]):
        # Once a POST succeeded, verify the readback rather than repeat writes.
        # The Avito API may reorder weekdays/time slots; treat order as irrelevant.
        require(len(profile["feeds_data"])==2,"Unexpected number of registered feeds")
        require(profile["feeds_data"][0]["feed_url"]==STABLE_AVITO_URL,
                "Primary feed URL changed after tools registration")
        require(profile["feeds_data"][1]["feed_url"]==TOOLS_URL,
                "Tools feed URL missing after registration")
        windows=profile["schedule"]
        require(isinstance(windows,list) and windows,"Registered schedule missing")
        for window in windows:
            require(set(window.get("weekdays") or [])==set(range(7)) and
                    set(window.get("time_slots") or [])==set(range(24)),
                    "Registered schedule no longer covers all original daily windows")
        release=gh("GET","/releases/tags/product-photos").json()
        found=[a for a in release.get("assets",[]) if a.get("name")==STABLE_TOOLS]
        require(len(found)==1,"Public tools release asset not unique")
        downloaded=requests.get(found[0]["browser_download_url"],timeout=80)
        require(downloaded.ok and downloaded.content==raw,
                "Public tools XML differs from approved media prices and taxonomy")
        report["registered_feed_links"]=[f["feed_url"] for f in profile["feeds_data"]]
        report["registered_schedule_windows"]=len(windows)
        report["registered_report_email_present"]=bool(profile.get("report_email"))
        report["status"]="two_registered_feeds_and_exact_xml_verified_no_writes"
        return report
    report["possible_duplicates_scanned"]=live_duplicate_guard()
    payload,email_source=profile_payload(profile)
    report["profile_email_source"]=email_source
    require([f["feed_url"] for f in payload["feeds_data"][:-1]]==
            [f["feed_url"] for f in profile["feeds_data"]],"Primary source URL would change")
    if mode=="inspect":
        report["status"]="approved_tools_preflight_passed_no_writes"
        return report
    require(req.get("exact_two_skus")==list(CAT),"Owner's two approved SKUs changed")
    status,asset=publish_release(raw)
    report["published_tools_feed"]=status
    # Mutating autoload settings only to append tool feed, with exact primary
    # feed and schedule readback. Never fire POST /autoload/v1/upload.
    try:
        api("POST","/autoload/v2/profile",json=payload)
    except Exception as exc:
        report["status"]="feed_asset_published_profile_update_rejected"
        report["error"]=short(exc)
        return report
    after=avito_profile()
    require(after["feeds_data"]==payload["feeds_data"],"Profile feeds readback differs")
    # Avito returns weekday/hour lists in arbitrary order; compare their sets.
    def normalized_schedule(value):
        return sorted((w.get("rate"),tuple(sorted(w.get("weekdays") or [])),
                       tuple(sorted(w.get("time_slots") or []))) for w in value or [])
    require(normalized_schedule(after["schedule"])==normalized_schedule(profile["schedule"]),
            "Profile schedule content changed")
    require(sha(PARTS.read_bytes())==source_sha,"Parts feed altered during tools operation")
    report["status"]="second_feed_added_pending_avito_import"
    report["current_feed_count"]=len(after["feeds_data"])
    report["seller_writes"]=1
    report["avito_oneoff_import_triggered"]=False
    return report
def main():
    report={"status":"fatal"}
    try:
        report=run()
        print("AVITO_TOOLS",json.dumps({k:report.get(k) for k in
            ("status","tool_skus","prices_rub","current_feed_count","new_listing_fee")},
            ensure_ascii=False),flush=True)
    except Exception as ex:
        report["error_type"]=type(ex).__name__
        report["error"]=short(ex)
        print("AVITO_TOOLS_BLOCKED",report["error_type"],report["error"],flush=True)
        raise
    finally:
        REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf8")
if __name__=="__main__":main()
