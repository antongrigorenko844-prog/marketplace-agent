"""Public Tilda two DSG kits: verify exact image order/content and approved prices, read only."""
import sys,json,io,ast,re
from pathlib import Path
from datetime import datetime,timezone
import requests
from PIL import Image,ImageChops,ImageStat
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.leoparts_direct_operation_nov21 import site_records
M=ROOT/"drafts/new-products/dsg-tools-20261120/media"
OUT=ROOT/"data/leoparts_dsg_tilda_visual_verify_20261123_result.json"
LIST={
 "tool-dq200-acc-restore":{"price":24800,"files":["tool-dq200-acc-restore_cover_20261120.png"]},
 "tool-dsg-pressure-hp":{"price":18800,"files":["tool-dsg-pressure-hp_cover_20261120.png","tool-dsg-pressure-hp_application_20261120.png"]}
}
def get_urls(obj):
    if isinstance(obj,list):return [u for item in obj for u in get_urls(item)]
    if isinstance(obj,dict):return [u for key,v in obj.items() for u in get_urls(v) if key in ("url","src","link","original","gallery","image","images","photos")]
    if isinstance(obj,str):
        if obj.startswith("http"):return [obj]
        for decoder in (json.loads,ast.literal_eval):
            try:parsed=decoder(obj)
            except Exception:continue
            if parsed!=obj:return get_urls(parsed)
        return re.findall(r'https?://[^\s,;"\'<>]+',obj)
    return []
def main():
    catalog=site_records()
    result={"read_only":True,"checked_utc":datetime.now(timezone.utc).isoformat(),"items":{}}
    for sku,spec in LIST.items():
        matches=[p for p in catalog.values() if p.get("sku")==sku]
        row={"catalog_matches":len(matches),"target_price":spec["price"]}
        if len(matches)==1:
            p=matches[0]
            row["price_actual"]=float(p.get("price") or 0)
            row["price_verified"]=row["price_actual"]==spec["price"]
            row["stock_raw"]=str(p.get("quantity") or p.get("inv") or "")[:120]
            row["gallery_type"]=type(p.get("gallery")).__name__
            row["gallery_raw_sample"]=str(p.get("gallery"))[:650]
            urls=get_urls(p.get("gallery") or [])
            row["public_gallery_count"]=len(urls)
            row["public_gallery_first_urls"]=urls[:4]
            row["verified_images"]=[]
            for i,fn in enumerate(spec["files"]):
                if i>=len(urls):
                    row["verified_images"].append({"filename":fn,"status":"missing"});continue
                try:
                    response=requests.get(urls[i],timeout=25)
                    response.raise_for_status()
                    with Image.open(io.BytesIO(response.content)) as image:
                        actual=image.convert("RGB").resize((256,256),Image.Resampling.LANCZOS)
                    with Image.open(M/fn) as approved:
                        wanted=approved.convert("RGB").resize((256,256),Image.Resampling.LANCZOS)
                    diff=ImageStat.Stat(ImageChops.difference(actual,wanted)).mean
                    mean=sum(diff)/3
                    row["verified_images"].append({"filename":fn,"status":"visually_matches" if mean<28 else "visual_mismatch_or_reorder",
                                                    "pixel_abs_mean":round(mean,2)})
                except Exception as e:
                    row["verified_images"].append({"filename":fn,"status":"image_fetch_error","error_type":type(e).__name__,"message":str(e)[:180]})
        result["items"][sku]=row
    OUT.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(result,ensure_ascii=False),flush=True)
if __name__=="__main__":main()
