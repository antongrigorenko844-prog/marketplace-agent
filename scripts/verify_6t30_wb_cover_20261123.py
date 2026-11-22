"""Read-only verification of current WB Gen1 primary cover versus owner-approved image."""
from pathlib import Path
from datetime import datetime,timezone
import base64,io,json,sys,time
from PIL import Image,ImageChops,ImageStat
import requests
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import wb_client as wb
SKU='6t30'; NM=2254117475
OUT=ROOT/'data/6t30_wb_cover_20261123_result.json'
PREVIEW=ROOT/'data/6t30_wb_cover_20261123_preview.b64'
report={'sku':SKU,'nmID':NM,'time_utc':datetime.now(timezone.utc).isoformat()}
try:
    cards=[c for c in wb.list_cards() if c.get('vendorCode')==SKU]
    if len(cards)!=1 or int(cards[0]['nmID'])!=NM:raise RuntimeError('SKU/NmId mismatch')
    card=cards[0];photos=card.get('photos') or []
    if not photos:raise RuntimeError('WB card has no photos')
    url=photos[0].get('big') or photos[0].get('c516x688')
    report.update(photo_count=len(photos),photo_url=url,live_title=card.get('title'))
    approved=Image.open(ROOT/'photos/6t30/6t30_1.png').convert('RGB')
    approved.thumbnail((300,400),Image.Resampling.LANCZOS)
    for attempt in range(1,5):
        try:
            r=requests.get(url,params={'cover_audit':str(time.time_ns())},headers={'Cache-Control':'no-cache','User-Agent':'Mozilla/5.0'},timeout=40)
            r.raise_for_status()
            live=Image.open(io.BytesIO(r.content)).convert('RGB')
            report['live_dimensions']=live.size
            live=live.resize(approved.size,Image.Resampling.LANCZOS)
            mad=sum(ImageStat.Stat(ImageChops.difference(live,approved)).mean)/3
            report['mean_pixel_difference']=round(mad,2)
            report['approved_cover_matches']=mad<16
            report['image_download_status']='ok'
            out=io.BytesIO();live.save(out,format='JPEG',quality=55,optimize=True)
            PREVIEW.write_text(base64.b64encode(out.getvalue()).decode('ascii')+'\n')
            if mad<16:break
        except Exception as e:
            report['image_download_error']=type(e).__name__+': '+str(e)[:250]
        if attempt!=4:time.sleep(12)
    try:
        j=json.loads((ROOT/'data/wb_cards.json').read_text())
        rows=j if isinstance(j,list) else (j.get('cards')or j.get('data')or [])
        baseline=next((x for x in rows if x.get('vendorCode')==SKU),None)
        if baseline:
            b={x['id']:x.get('value') for x in baseline.get('characteristics',[])}
            a={x['id']:x.get('value') for x in card.get('characteristics',[])}
            report['different_characteristics_vs_cached_snapshot']=[i for i in sorted(set(a)|set(b)) if a.get(i)!=b.get(i)]
            report['live_characteristic_count']=len(a)
    except Exception as e:report['comparison_error_type']=type(e).__name__
except Exception as e:report['fatal']=type(e).__name__+': '+str(e)[:500]
OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False),flush=True)
sys.exit(0 if report.get('approved_cover_matches') else 1)