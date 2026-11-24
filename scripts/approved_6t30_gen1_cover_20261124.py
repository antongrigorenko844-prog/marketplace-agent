"""Exact owner-approved Gen1 cover. No description, price, stock or new card writes."""
import copy,csv,hashlib,io,json,os,sys,time
from pathlib import Path
from PIL import Image,ImageChops,ImageStat
import requests
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import content_pipeline as p
from scripts import approved_6t30_gen1_cover_20261123 as prev
from photo_host import upload_file
SKU='6t30'
REQ=ROOT/'data/6t30_cover_publish_20261124_request.json'
OUT=ROOT/'data/6t30_cover_publish_20261124_result.json'
SRC=ROOT/'assets/product-intake-v2/bridge/6t30-gen1-cover-20261124.png'
EXPECTED='6d5af9507dd8bb6eaabf26a3b2b9c2084d2f82b8cbd9c906503df951fddc296d'
ASSET='6t30-gen1-cover-20261124.png'

def sha(raw):return hashlib.sha256(raw).hexdigest()

def require(ok,message):
    if not ok:raise ValueError(message)

def exact_image(url,reference):
    r=requests.get(url,params={'audit':str(time.time_ns())},timeout=45)
    r.raise_for_status()
    live=Image.open(io.BytesIO(r.content)).convert('RGB')
    a=reference.copy();a.thumbnail((280,400))
    require(abs(live.width/live.height-reference.width/reference.height)<0.03,'Cover aspect ratio changed')
    live=live.resize(a.size,Image.Resampling.LANCZOS)
    return round(sum(ImageStat.Stat(ImageChops.difference(live,a)).mean)/3,3)

def verify_wb(reference,jpeg):
    import wb_client as wb
    card=p.read_wb_exact(SKU,'2254117475')
    photos=card.get('photos') or []
    require(photos and photos[0].get('big'),'Existing WB gallery required')
    first=photos[0]['big']
    try:
        if exact_image(first,reference)<10:
            return {'status':'verified','already_present':True,'cover':first}
    except Exception:pass
    p.current_main()
    r=requests.post('https://content-api.wildberries.ru/content/v3/media/file',headers={'Authorization':os.environ['WB_API_TOKEN'],'X-Nm-Id':'2254117475','X-Photo-Number':'1'},files={'uploadfile':('cover.jpg',jpeg,'image/jpeg')},timeout=110)
    r.raise_for_status()
    require(not r.json().get('error'),'WB rejected photo')
    def protected(c):
        return {k:(sorted(c[k],key=lambda a:str(a.get('id'))) if k=='characteristics' and isinstance(c[k],list) else c[k]) for k in ('title','description','brand','subjectID','dimensions','sizes','characteristics','video') if k in c}
    result={'status':'submitted_pending_readback'}
    for _ in range(10):
        time.sleep(7)
        after=p.read_wb_exact(SKU,'2254117475')
        require(protected(after)==protected(card),'WB nonmedia field changed')
        ap=after.get('photos')or[]
        require(len(ap)==len(photos),'WB gallery count changed')
        require(ap[1:]==photos[1:],'WB secondary images changed')
        try:
            mad=exact_image(ap[0]['big'],reference)
            result.update(cover=ap[0]['big'],mean_pixel_difference=mad,secondary_preserved=True)
            if mad<10:
                result['status']='verified';break
        except (requests.RequestException,OSError,ValueError):continue
    return result

def stage_tilda(url):
    from scripts.leoparts_direct_operation_nov21 import site_records
    rows=[v for v in site_records().values() if v.get('sku')==SKU]
    require(len(rows)==1 and str(rows[0].get('uid'))=='695009271403','Exact Tilda UID missing')
    r=rows[0]
    gallery=r.get('gallery')
    if isinstance(gallery,str):gallery=json.loads(gallery)
    require(isinstance(gallery,list) and gallery,'Tilda live gallery absent')
    urls=[]
    for el in gallery:
        if isinstance(el,str):u=el
        else:u=next((el.get(k) for k in ('img','url','original','src','large') if isinstance(el.get(k),str) and el[k].startswith('http')),None)
        require(isinstance(u,str) and u.startswith('https://'),'Unrecognized gallery entry')
        urls.append(u)
    file=ROOT/'data/6t30_cover_tilda_editor_patch_20261124.json'
    p.write_json(file,{'sku':SKU,'uid':'695009271403','external_id':r.get('externalid'),'before_gallery':urls,'replace_first_with':url,'desired_gallery':[url]+urls[1:],'mode':'existing product editor only; no CommerceML import; retain current other fields'})
    return {'status':'editor_required','file':str(file.relative_to(ROOT)),'secondary_count':len(urls)-1},file

def main():
    require(os.environ.get('GITHUB_REF')=='refs/heads/main','main only')
    p.current_main()
    require(not OUT.exists(),'Previous attempt exists; read back before retrying')
    q=json.loads(REQ.read_text())
    require(q['sku']==SKU and q['approved_by_owner'] is True and q['sha256']==EXPECTED and q['scope']=='replace_only_first_photo_existing_cards','Request mismatch')
    raw=SRC.read_bytes()
    require(sha(raw)==EXPECTED,'Exact source image bytes required')
    ref=Image.open(io.BytesIO(raw)).convert('RGB')
    require(ref.size==(1052,1494),'Dimensions mismatch')
    manifest=ROOT/'data/content_manifest.json'
    data=json.loads(manifest.read_text()); row=data['products'][SKU]
    require(str(row['ids']['wb'])=='2254117475' and str(row['ids']['avito'])=='8425207775' and str(row['ids']['tilda'])=='695009271403','Identity mismatch')
    before=copy.deepcopy(data)
    source=upload_file(str(SRC),filename=ASSET)
    for _ in range(5):
        try:
            rr=requests.get(source,timeout=45)
            if rr.ok and sha(rr.content)==EXPECTED:break
        except requests.RequestException:pass
        time.sleep(6)
    else:raise RuntimeError('Exact asset download not verified')
    result={'sku':SKU,'source_sha256':EXPECTED,'source_url':source,'channels':{},'price_writes':0,'stock_writes':0,'description_writes':0}
    def save(paths=()):
        p.write_json(OUT,result)
        p.checkpoint([OUT,*paths],live=True)
    for ch,old in row['sources'].items():
        require(old,'Missing existing gallery')
        row['sources'][ch]=[source]+old[1:]
    for ch in row['sources']:
        require(row['sources'][ch][1:]==before['products'][SKU]['sources'][ch][1:],'Other source photos changed')
        before['products'][SKU]['sources'][ch][0]=source
    require(before==data,'Unrelated manifest change')
    manifest.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    # Canonical image paths used by existing refresh tooling.
    mp=ROOT/'photos/6t30/6t30_1.png';mp.write_bytes(raw)
    buf=io.BytesIO();ref.save(buf,'JPEG',quality=96,subsampling=0)
    av=ROOT/'photos_avito/6t30/6t30_1.jpg';av.write_bytes(buf.getvalue())
    save([manifest,mp,av,ROOT/'data/photo_upload_cache.json'])
    # Update exact catalog source pins; all commercial fields remain untouched.
    for name,fn,path in [('ozon_catalog',lambda:prev.replace_cover_in_xlsx('ozon_catalog.xlsx','Ozon',1,6,source),'data/ozon_catalog.xlsx'),('wb_catalog',lambda:prev.replace_cover_in_xlsx('wb_catalog.xlsx','WB',1,4,source),'data/wb_catalog.xlsx'),('avito_photo_map',lambda:prev.update_avito_local(source),'data/avito_photos.xlsx')]:
        p.current_main()
        try:result['channels'][name]={'status':'updated',**fn()};save([ROOT/path])
        except Exception as exc:result['channels'][name]={'status':'blocked','error':str(exc)[:500]};save()
    # Use the known exact existing-card photo adapters. Each action is independent.
    for ch,fn in [('ozon',lambda:prev.run_ozon(source)),('wb',lambda:verify_wb(ref,buf.getvalue())),('market',lambda:prev.run_market(source)),('avito',lambda:prev.avito_feed_update(source))]:
        p.current_main()
        result['channels'][ch]={'status':'submitted_intent'}
        save()
        try:
            result['channels'][ch]=fn()
            save([ROOT/'data/avito_feed.xlsx'] if ch=='avito' else [])
        except Exception as exc:
            result['channels'][ch]={'status':'uncertain_or_blocked','error':str(exc)[:500]}
            save()
    try:
        result['channels']['tilda'],path=stage_tilda(source)
        save([path])
    except Exception as exc:
        result['channels']['tilda']={'status':'blocked','error':str(exc)[:500]};save()
    result['finished_utc']=p.stamp()
    save()
    print(json.dumps(result,ensure_ascii=False))
    return 0

if __name__=='__main__':sys.exit(main())
