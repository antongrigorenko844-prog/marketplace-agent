"""WB advanced optimization: exact attributes + independent category/media/finance evidence."""
import base64, collections, concurrent.futures, copy, csv, datetime as dt, hashlib, io, json, math, os, re, subprocess, sys, time
from pathlib import Path
from urllib.parse import urlparse, urlencode
import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
import wb_client as wb
import content_manifest as cm
from config import config
from wb_identity_policy import is_held
ROOT=Path("data"); OUT=ROOT/"wb_advanced"; OUT.mkdir(exist_ok=True)
PLAN=json.loads((ROOT/"wb_advanced_request_20261126.json").read_text())
BASE=json.loads((ROOT/"wb_monitor/2026-11-26-45684111111.json").read_text())
PRIOR=json.loads((ROOT/"wb_audit_priorities_20261126.json").read_text())
MAN=cm.load()
JPATH=OUT/"journal.json"
J=json.loads(JPATH.read_text()) if JPATH.exists() else {"version":1,"operations":{},"writes":0}
def now():return dt.datetime.now(dt.timezone.utc).isoformat()
def dump(p,v):p.write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+"\n")
def checkpoint():
    dump(JPATH,J)
    subprocess.run(["bash",".github/scripts/save_data.sh",str(OUT)],check=True)
def vmap(c):return {int(v["id"]):v["value"] for v in c.get("characteristics",[])}
def protect(c,ids):
    result={k:copy.deepcopy(v) for k,v in c.items() if k not in {"updatedAt","characteristics"}}
    result["characteristics"]=sorted([v for v in c.get("characteristics",[]) if v["id"] not in ids],key=lambda v:v["id"])
    return result
def digest(x):return hashlib.sha256(json.dumps(x,sort_keys=True,ensure_ascii=False,default=str).encode()).hexdigest()
def excluded(c):return c.get("vendorCode") in PLAN["excluded"] or is_held(c.get("vendorCode"),c.get("nmID"))
original=wb._request
allowed=None
def guard(method,base,path,json_body=None,retries=1):
    m=method.upper();p=path.split("?")[0]
    if m=="POST" and p=="/content/v2/cards/update":
        assert base==config.wb_content_base and allowed is not None and json_body==allowed
        assert all(not is_held(x.get("vendorCode"),x.get("nmID")) for x in json_body)
    else:
        assert (m=="GET" and (p in {"/content/v2/object/parent/all","/content/v2/object/all","/api/v2/list/goods/filter","/api/v3/warehouses"} or p.startswith("/content/v2/object/charcs/"))) or (m=="POST" and (p in {"/content/v2/get/cards/list","/content/v2/get/cards/trash","/content/v2/cards/error/list"} or (base==config.wb_marketplace_base and re.fullmatch(r"/api/v3/stocks/\d+",p)))),(m,p)
    return original(m,base,path,json_body,retries=1)
wb._request=guard
def cards():
    a=wb.list_cards()
    assert len(a)==len({v["nmID"] for v in a})==len({v["vendorCode"] for v in a}) and len(a)>=140
    return {v["vendorCode"]:v for v in a}
def price():return {v["nmID"]:v for v in wb.get_prices()}
def stocks(cc):
    ids=sorted({s["chrtID"] for c in cc.values() for s in c.get("sizes",[]) if s.get("chrtID")});out={}
    for w in wb.get_warehouses():
        k=str(w["id"]);v=wb._request("POST",config.wb_marketplace_base,"/api/v3/stocks/"+k,{"chrtIds":ids})
        assert isinstance(v.get("stocks"),list)
        out[k]={int(s["chrtId"]):s["amount"] for s in v["stocks"]}
    return out
def quantity(c,ss):
    ids=[v["chrtID"] for v in c.get("sizes",[]) if v.get("chrtID")]
    vals=[w.get(i) for w in ss.values() for i in ids]
    return sum(vals) if vals and all(isinstance(v,int) for v in vals) else None
def exact(plan):
    j=wb._request("POST",config.wb_content_base,"/content/v2/get/cards/list",{"settings":{"cursor":{"limit":100},"filter":{"withPhoto":-1,"textSearch":str(plan["nmID"])}}})
    rows=[v for v in j.get("cards",[]) if v.get("nmID")==plan["nmID"] and v.get("vendorCode")==plan["sku"]]
    assert len(rows)==1,"exact identity not unique"
    return rows[0]
def field_snapshot(c,ids):return {str(i):vmap(c).get(i) for i in ids}
def attr_apply():
    global allowed
    b=cards();pb=price();sb=stocks(b)
    assert set(c["nmID"] for c in b.values())<=set(pb)
    group=collections.Counter(c.get("imtID") for c in b.values())
    cache={}
    for plan in PLAN["cards"]:
        key=str(plan["nmID"])+":"+digest(plan["fields"])[:12]
        fields=plan["fields"];ids={x["id"] for x in fields};target={str(x["id"]):x["target"] for x in fields}
        rec=J["operations"].get(key)
        try:
            c=exact(plan)
            assert not excluded(c)
            assert c["imtID"]==plan["imtID"] and c["subjectID"]==plan["subjectID"] and group[c["imtID"]]==1,"group or category changed"
            assert c.get("title")==plan["title"] and c.get("description")==plan["description"] and MAN.get(plan["sku"],{}).get("text",{}).get("wb",{}).get("description")==c.get("description"),"approved text differs"
            if rec:
                same=digest(protect(c,ids))==rec.get("protected_hash")
                match=field_snapshot(c,ids)==target
                rec["readback"]={"values":field_snapshot(c,ids),"protected_same":same,"checked":now()}
                if not same:rec["status"]="PROTECTED_DRIFT";checkpoint();break
                if match:rec["status"]="VERIFIED"
                checkpoint();continue
            if field_snapshot(c,ids)==target:
                J["operations"][key]={"sku":plan["sku"],"nmID":plan["nmID"],"fields":fields,"status":"NO_CHANGE_REQUIRED","protected_hash":digest(protect(c,ids))};continue
            assert field_snapshot(c,ids)=={str(x["id"]):x["previous"] for x in fields},"field precondition changed"
            if c["subjectID"] not in cache:cache[c["subjectID"]]={x["charcID"]:x for x in wb.get_subject_characteristics(c["subjectID"])}
            for f in fields:
                rule=cache[c["subjectID"]][f["id"]];v=f["target"]
                assert not rule.get("existNamedField") and rule["charcType"]==(4 if isinstance(v,int) else 1)
                if isinstance(v,list):assert len(v)<=rule["maxCount"] and all(isinstance(z,str) and z for z in v)
                assert not rule.get("isVariable") or f["id"] in {16532,90702}
            payload={k:copy.deepcopy(c[k]) for k in ("nmID","vendorCode","title","description","brand","dimensions","sizes","kizMarked") if k in c}
            assert len(payload)==8
            payload["characteristics"]=[{"id":x["id"],"value":x["value"]} for x in c.get("characteristics",[]) if x["id"] not in ids]+[{"id":x["id"],"value":x["target"]} for x in fields if x["target"] is not None]
            rec={"sku":plan["sku"],"nmID":plan["nmID"],"fields":fields,"status":"SUBMISSION_STARTED","protected_hash":digest(protect(c,ids)),"before":c,"started":now()}
            J["operations"][key]=rec;checkpoint()
            allowed=[payload];J["writes"]+=1
            try:
                ack=wb.update_cards(allowed);rec["ack"]=ack;rec["status"]="REJECTED" if ack.get("error") else "ACCEPTED"
            except Exception as ex:rec.update(status="UNCONFIRMED",error=str(ex)[:350])
            finally:allowed=None
            checkpoint()
            if rec["status"]!="ACCEPTED":continue
            for i in range(4):
                if i:time.sleep(5)
                a=exact(plan);same=digest(protect(a,ids))==rec["protected_hash"];match=field_snapshot(a,ids)==target
                rec["readback"]={"values":field_snapshot(a,ids),"protected_same":same,"checked":now()}
                if not same:rec["status"]="PROTECTED_DRIFT";rec["changed_protected"]=[k for k in protect(c,ids) if protect(c,ids).get(k)!=protect(a,ids).get(k)];break
                rec["status"]="VERIFIED" if match else "PENDING_MODERATION"
                if match:break
            checkpoint()
            if rec["status"]=="PROTECTED_DRIFT":break
        except Exception as ex:
            J["operations"].setdefault(key,{"sku":plan["sku"],"nmID":plan["nmID"],"fields":fields,"status":"REVIEW_REQUIRED","error":str(ex)[:350] or type(ex).__name__})
            checkpoint()
    a=cards();pa=price();sa=stocks(a)
    J["final_protection"]={"card_count_before":len(b),"card_count_after":len(a),"price_changes":[k for k in set(pb)|set(pa) if pb.get(k)!=pa.get(k)],"stock_changes":[[w,i,v,sa.get(w,{}).get(i)] for w,m in sb.items() for i,v in m.items() if sa.get(w,{}).get(i)!=v],"identity_media_differences":[k for k in set(a)|set(b) if any(a.get(k,{}).get(f)!=b.get(k,{}).get(f) for f in ("nmID","imtID","nmUUID","subjectID","vendorCode","title","description","sizes","brand","photos","video","dimensions","kizMarked"))]}
    J["finished"]=now();J["summary"]=dict(collections.Counter(x["status"] for x in J["operations"].values()));checkpoint()
    print("ATTRIBUTE_RESULT",json.dumps(J["summary"],ensure_ascii=False),flush=True)
    return a,pa,sa

def collect():
    c=cards();p=price();s=stocks(c)
    out={"checked":now(),"external_writes":0,"rows":{},"api":{},"category_candidates":PRIOR["priorities"]["category_review"],"schemas":{}}
    subjects={}
    try:
        parents=wb.get_parent_categories();out["parents"]=parents
        for off in range(0,30000,1000):
            j=wb._request("GET",config.wb_content_base,"/content/v2/object/all?"+urlencode({"limit":1000,"offset":off,"locale":"ru"}))
            page=j.get("data")
            assert isinstance(page,list)
            for x in page:subjects[x["subjectID"]]=x
            if len(page)<1000:break
            time.sleep(.65)
        out["subject_count"]=len(subjects)
        current={v["subjectID"] for v in c.values()}
        parent_ids={subjects[i]["parentID"] for i in current if i in subjects}
        out["subjects"]={str(i):x for i,x in subjects.items() if x.get("parentID") in parent_ids or i in current or re.search(r"коробк|трансмис|мехатрон|гидроблок|соленоид|автомобильн.*(датчик|клапан)|датчик.*автомобильн|масля.*фильтр|запчаст|комплектующ.*авто|радиодетал|микросхем|шлейф|разъ[её]м",str(x.get("subjectName","")),re.I)}
        assert current<=set(subjects)
    except Exception as ex:out["category_error"]=str(ex)[:350]
    for k,x in c.items():
        if excluded(x):continue
        docs=x.get("documents") or {};desc=x.get("description","")
        out["rows"][k]={"sku":k,"nmID":x["nmID"],"imtID":x.get("imtID"),"title":x.get("title"),"subjectID":x["subjectID"],"subjectName":x.get("subjectName"),"stock":quantity(x,s),"price":(p.get(x["nmID"],{}).get("sizes") or [{}])[0].get("discountedPrice"),"description":desc,"characteristics":x.get("characteristics",[]),"documents":docs,"dimensions":x.get("dimensions"),"photos":[z.get("big") or z.get("hq") for z in x.get("photos",[])],"video":x.get("video"),"cost":next((r.get("cost") for r in BASE["rows"].values() if r["sku"]==k and r["nmID"]==x["nmID"]),None),"public_availability":"UNCONFIRMED"}
    # One current official inventory endpoint check. Prior scopes already returned 403.
    try:
        res=requests.post("https://seller-analytics-api.wildberries.ru/api/analytics/v1/stocks-report/wb-warehouses",json={"nmIDs":[x["nmID"] for x in c.values()],"limit":1000,"offset":0},headers={"Authorization":config.wb_api_token,"Content-Type":"application/json"},timeout=20)
        out["api"]["wb_warehouse_inventory"]={"http":res.status_code,"body":res.json() if res.ok else {k:res.json().get(k) for k in ("title","detail")}}
    except Exception as ex:out["api"]["wb_warehouse_inventory"]={"error":type(ex).__name__}
    # Public storefront only, standard website request; stop on protection, do not circumvent.
    for sku in ["0am325025H","0AM927769D-plata","0am325025u"]:
        if sku not in out["rows"]:continue
        url="https://www.wildberries.ru/catalog/"+str(out["rows"][sku]["nmID"])+"/detail.aspx"
        try:
            res=requests.get(url,timeout=15,allow_redirects=True,headers={"User-Agent":"Mozilla/5.0"})
            v={"url":url,"http":res.status_code,"region":"not established","verified":False}
            if res.ok:
                # Only structured data with explicit offer availability establishes a public observation.
                for m in re.findall(r'<script[^>]+type=["\x27]application/ld\+json["\x27][^>]*>(.*?)</script>',res.text,re.S|re.I):
                    try:
                        z=json.loads(m)
                        if isinstance(z,dict) and z.get("@type")=="Product" and z.get("offers"):
                            v["structured_offers"]=z["offers"];v["observed_title"]=z.get("name")
                    except Exception:pass
            out["api"]["public_"+sku]=v
            if res.status_code in (401,403,429,498):break
        except Exception as ex:out["api"]["public_"+sku]={"url":url,"error":type(ex).__name__};break
    out["summary"]={"scope":len(out["rows"]),"positive_fbs":sum((x["stock"] or 0)>0 for x in out["rows"].values()),"zero_fbs":sum(x["stock"]==0 for x in out["rows"].values()),"unknown_fbs":sum(x["stock"] is None for x in out["rows"].values()),"missing_documents":sum(x["documents"].get("overallVerdict",{}).get("reason")=="documents_missing" for x in out["rows"].values()),"with_cover":sum(bool(x["photos"]) for x in out["rows"].values()),"no_video":sum(not x["video"] for x in out["rows"].values())}
    dump(OUT/"live_inventory.json",out);checkpoint()
    print("READ_RESULT",out["summary"],flush=True)
    return out

def media(out):
    rows=out["rows"]; old=json.loads((ROOT/"ozon_products.json").read_text())
    oz=old.get("names",{}); oz={v["offer_id"]:v for v in oz.values()} if isinstance(oz,dict) else {v["offer_id"]:v for v in oz}
    try:
        with open(ROOT/"tilda_export.csv",encoding="utf-8-sig") as f:tilda={r["SKU"]:r for r in csv.DictReader(f,delimiter=";") if r.get("SKU")}
    except Exception:tilda={}
    reviewed=json.loads((ROOT/"wb_media_additions_20261115.json").read_text())
    market=json.loads((ROOT/"market_recovery_media/inventory.json").read_text())
    video=json.loads((ROOT/"market_video_sources.json").read_text())
    # Explicit known removal: do not source any secondary Gen1 solenoid image. The old D card is excluded.
    no_media={"6t30","0cw0am"}
    def fetch_image(url):
        try:
            assert url and urlparse(url).scheme=="https"
            rr=requests.get(url,timeout=14);rr.raise_for_status()
            assert len(rr.content)<=20_000_000
            im=Image.open(io.BytesIO(rr.content));im.load();im=im.convert("RGB")
            a=im.convert("L").resize((9,8));px=list(a.getdata())
            dh=sum(int(px[y*9+x]>px[y*9+x+1])<<(y*8+x) for y in range(8) for x in range(8))
            return im,hashlib.sha256(rr.content).hexdigest(),dh
        except Exception:return None
    urls=set(v for x in rows.values() for v in x["photos"] if v)
    source={}
    for sku,x in rows.items():
        source[sku]=[]
        if len(x["photos"])>=4 or sku in no_media:continue
        r=oz.get(sku,{})
        if r.get("offer_id")==sku:
            source[sku]+=[(u,"Own saved exact Ozon gallery") for u in ([r.get("primary_image")]+(r.get("images") or [])) if u]
        t=tilda.get(sku)
        if t:
            source[sku]+=[(u.strip(),"Own saved exact Tilda export") for u in re.split(r"[\s,;]+",t.get("Photo","")) if u.startswith("https://")]
        for ch,items in MAN.get(sku,{}).get("sources",{}).items():
            if ch in ("ozon","wb","market","tilda","avito"):
                source[sku]+=[(u,"Owner-approved exact manifest "+ch) for u in items if isinstance(u,str)]
        source[sku]+=[(v["url"],"Previously visually reviewed WB addition") for v in reviewed.get(sku,[]) if v.get("url")]
        source[sku]+=[(v["url"],v["source"]) for v in market.get("candidates",[]) if v.get("sku")==sku and "Wildberries" not in v.get("source","")]
        source[sku]=list(dict(source[sku]).items())[:12]
        urls.update(u for u,_ in source[sku])
    print("MEDIA_URLS",len(urls),flush=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as ex:images=dict(zip(sorted(urls),ex.map(fetch_image,sorted(urls))))
    cover=[];candidates=[];unavailable=[]
    def near(a,b):return a[1]==b[1] or (a[2]^b[2]).bit_count()<=5
    for sku,x in sorted(rows.items(),key=lambda kv:(-(kv[1]["stock"] or 0>0),-(kv[1]["price"] or 0),kv[0])):
        cur=[images.get(v) for v in x["photos"]];known=[i for i in cur if i]
        if x["photos"] and images.get(x["photos"][0]):
            z=images[x["photos"][0]];cover.append({"sku":sku,"nmID":x["nmID"],"url":x["photos"][0],"sha256":z[1],"size":list(z[0].size),"stock":x["stock"],"photo_count":len(x["photos"])})
        else:unavailable.append({"sku":sku,"nmID":x["nmID"],"reason":"No photo" if not x["photos"] else "CDN image unavailable"})
        # Only visual proposals; old media may have been removed, so approval requires separate explicit provenance.
        if len(known)!=len(cur):continue
        checked=[]
        for u,where in source.get(sku,[]):
            z=images.get(u)
            if not z or any(near(z,v) for v in known+checked) or u in x["photos"]:continue
            if sku in no_media:continue
            checked.append(z)
            candidates.append({"sku":sku,"nmID":x["nmID"],"url":u,"source":where,"sha256":z[1],"size":list(z[0].size),"cover":x["photos"][0] if x["photos"] else None,"existing":len(x["photos"]),"status":"REVIEW_REQUIRED","note":"Saved sources may contain removed photos; require visual approval and history check before appending."})
            if len(checked)>=2:break
    def sheets(name,items,paired=False):
        fs=[]
        try:font=ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",15)
        except Exception:font=ImageFont.load_default()
        n=8 if paired else 12; cols=2 if paired else 4
        cw=500 if paired else 250; ch=338
        for idx in range(0,len(items),n):
            chunk=items[idx:idx+n];h=math.ceil(len(chunk)/cols)*ch
            sheet=Image.new("RGB",(cw*cols,h),"white");d=ImageDraw.Draw(sheet)
            for k,r in enumerate(chunk):
                x=(k%cols)*cw;y=(k//cols)*ch
                label=f"{idx+k+1:03d} {r['sku']}"
                d.text((x+4,y+3),label[:52 if paired else 27],font=font,fill="black")
                d.text((x+4,y+21),str(r["nmID"]),font=font,fill="black")
                us=[r["cover"],r["url"]] if paired else [r["url"]]
                for z,u in enumerate(us):
                    rec=images.get(u)
                    if not rec:continue
                    im=ImageOps.contain(rec[0],(238,285));ox=x+z*250+(250-im.width)//2;oy=y+44+(285-im.height)//2;sheet.paste(im,(ox,oy))
            buf=io.BytesIO();sheet.save(buf,format="JPEG",quality=83)
            path=OUT/f"{name}_{idx//n+1:02d}.b64";path.write_text(base64.b64encode(buf.getvalue()).decode());fs.append(str(path))
        return fs
    for i,x in enumerate(cover,1):x["id"]=i
    for i,x in enumerate(candidates,1):x["id"]=i
    # Videos are source candidates only, never attached without matching frames/actual product.
    vrows=[]
    for sku,x in rows.items():
        if x["video"]:continue
        raw=video.get("overrides",{}).get(sku,[])
        if sku in video.get("excluded",[]) or sku in no_media:raw=[]
        vrows.append({"sku":sku,"nmID":x["nmID"],"candidate_urls":raw,"status":"REVIEW_REQUIRED" if raw else "DESIGN_REQUIRED","note":"No publication without verification of the exact displayed component and absence of removed materials."})
    report={"checked":now(),"source_scope":{"ozon_snapshot_exact":sum(k in oz for k in rows),"tilda_export_exact":sum(k in tilda for k in rows),"owner_manifest":sum(bool(MAN.get(k,{}).get("sources")) for k in rows),"market_reviewed_candidates":len(market.get("candidates",[])),"avito":"Only owner-approved manifest sources, no live full galleries","note":"Ozon/Tilda snapshots not fresh; not a complete live gallery audit."},"urls":len(urls),"downloaded":sum(v is not None for v in images.values()),"covers":cover,"cover_missing":unavailable,"candidate_images":candidates,"video_queue":vrows,"external_writes":0}
    report["cover_sheets"]=sheets("covers",cover)
    report["candidate_sheets"]=sheets("candidates",candidates,True)
    dump(OUT/"media_inventory.json",report);checkpoint()
    print("MEDIA_RESULT",len(cover),len(candidates),len(vrows),flush=True)

def main():
    # Preserve independent tasks; media and category results checkpoint before any writes.
    live=collect()
    try:media(live)
    except Exception as ex:dump(OUT/"media_error.json",{"error":str(ex)[:350],"checked":now()});checkpoint()
    if PLAN.get("mode")=="apply_verified_attributes_and_audit":attr_apply()
if __name__=="__main__":main()
