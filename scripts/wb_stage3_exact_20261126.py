"""WB stage 3: exact first-slot repair and evidence-bound missing attributes.
Only WB writes: single photo slot for exact K board and selected empty attributes.
No price, stock, category, document, product-state or other-platform writes.
"""
import copy, hashlib, io, json, os, sys, time
from datetime import datetime, timezone
from pathlib import Path
import requests
from PIL import Image, ImageChops, ImageStat
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));os.chdir(ROOT)
import wb_client as wb
from config import config
from wb_identity_policy import is_held
import content_manifest as cm
OUT=ROOT/"data/wb_advanced/stage3_execution.json"
ART=Path("/tmp/wb-stage3");ART.mkdir(exist_ok=True)
SNAP=json.loads((ROOT/"data/wb_advanced/live_inventory.json").read_text())["rows"]
REV=json.loads((ROOT/"data/wb_advanced/category_document_review.json").read_text())
MAN=cm.load()
EXCLUDED={"TESTWB001","0AM927769D","solenoid-02e-0b5-bolshoy","0cw0am"}
LEGAL_EXCLUDED={r["sku"] for r in REV["document_review"]["rows"] if r["status"]=="POLICY_REVIEW_REQUIRED"}
K="0AM927769K"; KID=2253503880
OLD_HASH="000ee02f38301b08f1941e183f9c31b0fef657beb97222e049aa1ba7b402b43c"
SOURCE="https://ir-20.ozone.ru/s3/multimedia-1-2/21314548874.jpg"
SOURCE_HASH="3a0f95cd9484271e1ea3abfffa5783f0bfb13adb5c675b4124f4f163ee2e5d9d"
# Facts explicitly present in the previous seller description; do not infer quantities.
ATTR={
 "31705-1XF1A": {378533:["Гидроблок вариатора JF011E, старое поколение"]},
 "0AM325091E / 0AM325091F": {378533:["Оригинальный сальник штока мехатроника"]},
 "0cw0amdsgdq200": {378533:["Уплотнение электромотора гидроблока мехатроника"]},
 "tool-dq200-acc-restore": {378533:["Набор для восстановления гидроаккумулятора DSG7 DQ200"]},
 "tool-dsg-pressure-hp": {378533:["Набор проверки давления DSG, исполнение для высокого давления, с упаковкой"]},
}
def now():return datetime.now(timezone.utc).isoformat()
def save():
    R["updated"]=now()
    OUT.write_text(json.dumps(R,ensure_ascii=False,indent=2,default=str)+"\n")
def check(b,msg):
    if not b:raise ValueError(msg)
def sha(b):return hashlib.sha256(b).hexdigest()
def img(raw):
    im=Image.open(io.BytesIO(raw));im.load();return im.convert("RGB")
def download(url, fresh=False):
    r=requests.get(url,params={"audit":str(time.time_ns())} if fresh else None,timeout=25)
    r.raise_for_status();check(len(r.content)<20_000_000,"Image exceeds size limit")
    img(r.content);return r.content
def difference(a,b):
    a,b=img(a),img(b)
    if abs(a.width/a.height-b.width/b.height)>0.03:return 255
    a.thumbnail((256,384));b=b.resize(a.size,Image.Resampling.LANCZOS)
    return round(sum(ImageStat.Stat(ImageChops.difference(a,b)).mean)/3,3)
def cards():
    c=wb.list_cards()
    check(len(c)>=140 and len(c)==len({x["nmID"] for x in c})==len({x["vendorCode"] for x in c}),"Incomplete or ambiguous list")
    return {x["vendorCode"]:x for x in c}
def exact(sku,nm):
    res=wb._request("POST",config.wb_content_base,"/content/v2/get/cards/list",{"settings":{"cursor":{"limit":100},"filter":{"withPhoto":-1,"textSearch":str(nm)}}},retries=1)
    rows=[x for x in res.get("cards",[]) if x.get("vendorCode")==sku and x.get("nmID")==nm]
    check(len(rows)==1,"Exact readback missing")
    check(sku not in EXCLUDED and not is_held(sku,nm),"Held identity")
    return rows[0]
def stock(c):
    ids=sorted({s["chrtID"] for x in c.values() for s in x.get("sizes",[]) if s.get("chrtID")})
    d={}
    for w in wb.get_warehouses():
        k=str(w["id"]);r=wb._request("POST",config.wb_marketplace_base,"/api/v3/stocks/"+k,{"chrtIds":ids},retries=1)
        check(isinstance(r.get("stocks"),list),"Stock read failed")
        d[k]={int(z["chrtId"]):z["amount"] for z in r["stocks"]}
    return d
def quantity(c,s):
    ids=[z["chrtID"] for z in c.get("sizes",[]) if z.get("chrtID")]
    v=[w.get(i) for w in s.values() for i in ids]
    return sum(v) if v and all(isinstance(i,int) for i in v) else None
def normalized(c,omit=(),attrs=()):
    d={k:copy.deepcopy(v) for k,v in c.items() if k not in {"updatedAt",*omit}}
    if "characteristics" in d:
        d["characteristics"]=sorted([v for v in d["characteristics"] if v["id"] not in attrs],key=lambda x:x["id"])
    return d
def fields(c):return {x["id"]:x["value"] for x in c.get("characteristics",[])}
def write_snapshot(name,obj):
    (ART/(name+".json")).write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str))
def repair_k():
    z=R["media"][K]={"nmID":KID,"status":"PENDING","writes":0};save()
    b=exact(K,KID);check("0AM927769K" in b.get("title",""),"Title mismatch")
    check(len(b.get("photos",[]))==3 and b.get("video"),"Gallery/video changed")
    source=download(SOURCE);check(sha(source)==SOURCE_HASH,"Reviewed source bytes changed")
    old=download(b["photos"][0]["big"],True)
    z.update(old_sha256=sha(old),source_sha256=sha(source),before_difference=difference(old,source),source_url=SOURCE)
    if z["before_difference"]<8:
        z["status"]="ALREADY_CORRECT";save();return
    check(sha(old)==OLD_HASH,"Current cover differs from reviewed damaged image")
    (ART/"K-cover-before.webp").write_bytes(old);(ART/"K-cover-source.jpg").write_bytes(source)
    write_snapshot("K-before",b)
    # Official existing single-slot endpoint previously used in approved_6t30_gen1_cover_20261124.py.
    buf=io.BytesIO();img(source).save(buf,"JPEG",quality=96,subsampling=0)
    z["status"]="SUBMISSION_STARTED";z["writes"]=1;save()
    r=requests.post(config.wb_content_base.rstrip("/")+"/content/v3/media/file",
        headers={"Authorization":os.environ["WB_API_TOKEN"],"X-Nm-Id":str(KID),"X-Photo-Number":"1"},
        files={"uploadfile":("cover.jpg",buf.getvalue(),"image/jpeg")},timeout=75)
    z["http"]=r.status_code;r.raise_for_status()
    check(not r.json().get("error"),"WB rejected cover upload")
    z["status"]="ACCEPTED_PENDING_READBACK";save()
    for _ in range(10):
        time.sleep(5)
        a=exact(K,KID);write_snapshot("K-after",a)
        z["protected_same"]=normalized(b,omit=("photos",))==normalized(a,omit=("photos",))
        z["secondary_same"]=b["photos"][1:]==a.get("photos",[])[1:]
        z["video_same"]=b["video"]==a.get("video")
        z["count_same"]=len(a.get("photos",[]))==len(b["photos"])
        check(all(z[k] for k in ("protected_same","secondary_same","video_same","count_same")),"Unexpected non-target drift")
        try:
            new=download(a["photos"][0]["big"],True)
            z["after_sha256"]=sha(new);z["after_difference"]=difference(new,source)
            (ART/"K-cover-after.webp").write_bytes(new)
            if z["after_difference"]<8:
                z["status"]="VERIFIED";break
        except (OSError,requests.RequestException):pass
        save()
    save()
def schema(sid):
    if sid not in SCHEMAS:SCHEMAS[sid]={int(x["charcID"]):x for x in wb.get_subject_characteristics(sid)}
    return SCHEMAS[sid]
def attr(sku,target):
    prev=SNAP[sku];nm=prev["nmID"];z=R["attributes"][sku]={"nmID":nm,"status":"PENDING","writes":0,"targets":target}
    b=exact(sku,nm);f=fields(b)
    check(b["subjectID"]==prev["subjectID"] and b["title"]==prev["title"] and b["description"]==prev["description"],"Source facts drift")
    check(sku not in LEGAL_EXCLUDED,"Excluded by owner")
    check(len([c for c in B.values() if c.get("imtID")==b.get("imtID")])==1,"Grouped card")
    canonical=MAN.get(sku,{}).get("text",{}).get("wb",{}).get("description")
    check(canonical is None or canonical==b["description"],"Canonical description differs")
    if all(f.get(k)==v for k,v in target.items()):z["status"]="ALREADY_CORRECT";return
    check(all(f.get(k) in (None,[], "") for k in target),"Non-empty field; preserve")
    rules=schema(b["subjectID"])
    for k,v in target.items():
        rule=rules.get(k)
        check(rule and rule.get("charcType")==1 and not rule.get("existNamedField") and not rule.get("isVariable") and len(v)<=rule.get("maxCount",0),"Field not safe/available for subject")
    ids=set(target)
    payload={k:copy.deepcopy(b[k]) for k in ("nmID","vendorCode","title","description","brand","dimensions","sizes","kizMarked") if k in b}
    check(len(payload)==8,"Incomplete card update payload")
    payload["characteristics"]=[{"id":x["id"],"value":x["value"]} for x in b.get("characteristics",[]) if x["id"] not in ids]+[{"id":k,"value":v} for k,v in target.items()]
    write_snapshot("attribute-"+str(nm)+"-before",b)
    z["status"]="SUBMISSION_STARTED";z["writes"]=1;save()
    ack=wb._request("POST",config.wb_content_base,"/content/v2/cards/update",[payload],retries=1)
    check(not ack.get("error"),"Update rejected")
    z["status"]="ACCEPTED_PENDING_READBACK";save()
    for _ in range(5):
        time.sleep(5)
        a=exact(sku,nm)
        z["protected_same"]=normalized(b,attrs=ids)==normalized(a,attrs=ids)
        check(z["protected_same"],"Unexpected non-target drift")
        z["readback"]={k:fields(a).get(k) for k in target}
        if z["readback"]==target:z["status"]="VERIFIED";break
    save()
def categories():
    for r in REV["category_review"]["rows"]:
        k=r["sku"];target=r.get("target");c=A.get(k)
        z={"sku":k,"nmID":r["nmID"],"prior_status":r["status"],"target":target,"status":r["status"],"writes":0}
        R["categories"].append(z)
        if not c or not isinstance(target,dict):continue
        if c["nmID"]!=r["nmID"]:z["status"]="IDENTITY_DRIFT";continue
        if c["subjectID"]==target["subjectID"]:z["status"]="ALREADY_TARGET";continue
        try:
            defs=schema(target["subjectID"])
            z["required_fields"]=[{"id":i,"name":v.get("name"),"present":bool(fields(c).get(i))} for i,v in defs.items() if v.get("required")]
            z["existing_fields_not_in_target"]=[{"id":i,"name":x.get("name")} for x in c.get("characteristics",[]) for i in [x["id"]] if i not in defs]
            z["status"]="BLOCKED_CABINET_REQUIRED"
        except Exception as e:z.update(status="BLOCKED_SCHEMA",error=type(e).__name__)
        save()
def availability():
    try:errors=wb.get_upload_errors();R["upload_error_rows"]=len(errors)
    except Exception as e:errors=[];R["upload_error_read"]=type(e).__name__
    for k,c in A.items():
        if k in EXCLUDED or is_held(k,c.get("nmID")):continue
        s=quantity(c,SA);doc=c.get("documents") or {};verdict=doc.get("overallVerdict") or {}
        z={"sku":k,"nmID":c["nmID"],"FBS":s,"seller_card_present":True,"photo_count":len(c.get("photos",[])),"video":bool(c.get("video")),"buyer_availability":"UNCONFIRMED","stock_signal":"POSITIVE_FBS" if s is not None and s>0 else "ZERO_FBS" if s==0 else "UNKNOWN"}
        if k in LEGAL_EXCLUDED:z["document_status"]="EXCLUDED_BY_OWNER"
        else:z["document_status"]=verdict.get("reason") or "NO_EXPLICIT_MISSING_VERDICT"
        z["upload_errors_match"]=sum(k in (e.get("errors") or {}) for e in errors)
        R["availability"].append(z)
    # Official public storefront sample only. No evasion of provider block.
    for z in sorted(R["availability"],key=lambda x:-(x["FBS"] or 0)):
        try:
            url="https://www.wildberries.ru/catalog/"+str(z["nmID"])+"/detail.aspx"
            q=requests.get(url,headers={"User-Agent":"Mozilla/5.0"},timeout=15)
            R["public_sample"]={"nmID":z["nmID"],"http":q.status_code,"buyer_availability":"UNCONFIRMED"}
        except Exception as e:R["public_sample"]={"error":type(e).__name__}
        break
    save()
R={"run_id":os.getenv("GITHUB_RUN_ID"),"started":now(),"legal_policy":"EXCLUDED_BY_OWNER","media":{},"attributes":{},"categories":[],"availability":[],"price_writes":0,"stock_writes":0,"document_writes":0,"category_writes":0,"other_platform_writes":0}
SCHEMAS={}
if __name__=="__main__":
    check(os.getenv("GITHUB_REF")=="refs/heads/main","main only")
    # Do not repeat a submitted or ambiguous previous mutation automatically.
    if OUT.exists():
        previous=json.loads(OUT.read_text())
        check(not any(x.get("writes") for x in [*previous.get("media",{}).values(),*previous.get("attributes",{}).values()]),"Previous write exists: read back before any retry")
    save();B=cards();PB={x["nmID"]:x for x in wb.get_prices()};SB=stock(B)
    write_snapshot("before",B);write_snapshot("prices-before",PB);write_snapshot("stocks-before",SB)
    try:repair_k()
    except Exception as e:R["media"][K].update(error=str(e)[:180] if isinstance(e,ValueError) else type(e).__name__,status="BLOCKED_OR_UNCONFIRMED");save()
    if R["media"][K].get("protected_same") is False:
        R["stop_reason"]="Non-target drift after media operation";save();sys.exit(1)
    for k,target in ATTR.items():
        try:attr(k,target)
        except Exception as e:R["attributes"].setdefault(k,{}).update(error=str(e)[:180] if isinstance(e,ValueError) else type(e).__name__,status="BLOCKED_OR_UNCONFIRMED");save()
        if R["attributes"][k].get("protected_same") is False:break
    A=cards();PA={x["nmID"]:x for x in wb.get_prices()};SA=stock(A)
    write_snapshot("after",A);write_snapshot("prices-after",PA);write_snapshot("stocks-after",SA)
    R["protection"]={"card_ids_same":set(B)==set(A) and all(B[k]["nmID"]==A[k]["nmID"] and B[k]["imtID"]==A[k]["imtID"] for k in B),"price_snapshot_same":PB==PA,"stock_snapshot_same":SB==SA,"non_target_differences":[k for k in set(B)&set(A) if normalized(B[k],omit=("photos",) if k==K else (),attrs=set(ATTR.get(k,{})))!=normalized(A[k],omit=("photos",) if k==K else (),attrs=set(ATTR.get(k,{})))]}
    save();categories();availability()
    R["finished"]=now();save()
    print(json.dumps({"media":R["media"],"attributes":R["attributes"],"protection":R["protection"],"availability_rows":len(R["availability"]),"category_rows":len(R["categories"])},ensure_ascii=False),flush=True)
