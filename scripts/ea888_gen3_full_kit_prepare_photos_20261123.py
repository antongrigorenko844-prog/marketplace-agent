"""Create exact EA888 full-kit gallery using original existing-kit cover plus owner's
provided filter+O-ring photo. No AI re-rendering or altered product geometry.
Uploads JPGs to GitHub product-photos release and stores local photos for Tilda.
"""
from __future__ import annotations
import base64,hashlib,io,json,os,sys
from pathlib import Path
from PIL import Image,ImageOps,ImageDraw,ImageFont
import requests
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from photo_host import upload_file
SKU="EA888gen3-filter-kit"
OUTPUT=ROOT/"data/ea888_gen3_full_kit_photos_20261123_result.json"
MEDIA=ROOT/"photos"/SKU
SAMPLE_URL="https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/EA888gen3_1.jpg"
SOURCE=ROOT/"data/ea888_gen3_filter_kit_photo_20261123.b64"
def font(size,bold=False):
    for f in (("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"):
        if os.path.exists(f):return ImageFont.truetype(f,size)
    return ImageFont.load_default()
def fit_rect(im,w,h):
    im=im.convert("RGB");return ImageOps.contain(im,(w,h),Image.Resampling.LANCZOS)
def validate():
    a=json.loads((ROOT/"data/ea888_gen3_filter_kit_approved_20261123.json").read_text())
    if a.get("sku")!=SKU or a["saleable_stock"]!=40:raise ValueError("Owner manifest mismatch")
    if not SOURCE.exists():raise ValueError("Owner filter photo missing")
    raw=base64.b64decode(SOURCE.read_text().strip(),validate=True)
    filter_original=Image.open(io.BytesIO(raw));filter_original.verify()
    return raw
def prepare():
    raw=validate();MEDIA.mkdir(parents=True,exist_ok=True)
    filt=Image.open(io.BytesIO(raw)).convert("RGB")
    if min(filt.size)<200:raise ValueError("Unexpectedly small owner filter image")
    resp=requests.get(SAMPLE_URL,timeout=65)
    resp.raise_for_status()
    old=Image.open(io.BytesIO(resp.content)).convert("RGB")
    if min(old.size)<300:raise ValueError("Existing kit image invalid")
    # Bright, truthful collage: existing kit vs. supplied real filter,
    # without inventing OEM numbers for the cartridge.
    canvas=Image.new("RGB",(1400,1400),"#f8fafc")
    d=ImageDraw.Draw(canvas)
    d.rectangle((0,0,1400,164),fill="#ff7900")
    d.text((50,24),"LEOPARTS • EA888 Gen3",font=font(61,True),fill="white")
    d.text((55,188),"КОРПУС + КЛАПАН + ФИЛЬТР",font=font(57,True),fill="#171717")
    d.text((56,271),"Комплект с фильтрующим элементом и уплотнением",font=font(33),fill="#333333")
    d.rectangle((35,342,900,1238),fill="white",outline="#ededed",width=3)
    sample=fit_rect(old,824,842)
    canvas.paste(sample,(55+(824-sample.width)//2,360+(842-sample.height)//2))
    d.rectangle((921,342,1370,1238),fill="white",outline="#ff7900",width=5)
    real_filter=fit_rect(filt,430,700)
    canvas.paste(real_filter,(930+(430-real_filter.width)//2,410+(700-real_filter.height)//2))
    d.text((947,1148),"ФИЛЬТР + КОЛЬЦО",font=font(28,True),fill="#db6c00")
    d.rectangle((0,1265,1400,1400),fill="#141414")
    d.text((52,1303),"EA888 Gen3 • 1.8 / 2.0 TSI",font=font(50,True),fill="white")
    main=MEDIA/(SKU+"_1.jpg")
    canvas.save(main,quality=92,optimize=True)
    # Two standalone gallery images: exact owner filter and current two-part kit.
    f2=Image.new("RGB",(1200,1200),"white")
    imp=fit_rect(filt,1090,1090)
    f2.paste(imp,((1200-imp.width)//2,(1200-imp.height)//2))
    secondary=MEDIA/(SKU+"_2.jpg");f2.save(secondary,quality=90,optimize=True)
    third=MEDIA/(SKU+"_3.jpg");old.save(third,quality=90,optimize=True)
    gallery=[main,secondary,third]
    a={}
    for x in gallery:
        a[x.name]={"pixels":Image.open(x).size,"sha256":hashlib.sha256(x.read_bytes()).hexdigest()[:12]}
    urls={}
    for x in gallery:
        urls[x.name]=upload_file(str(x),filename=x.name)
    result={"status":"three_real_photo_gallery_assets_uploaded",
            "sku":SKU,"urls":urls,"meta":a,
            "source":"original existing EA888gen3 kit photo + owner's uploaded filter and O-ring",
            "avoid_misleading_claims":"No filter OEM asserted; composite covers all components"}
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=="__main__":prepare()
