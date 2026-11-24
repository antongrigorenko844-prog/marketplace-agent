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
REQ=ROOT/'data/6t30_gen1_six_publish_request_20261124.json'
OUT=ROOT/'data/6t30_gen1_six_publish_result_20261124.json'
SRC=ROOT/'assets/product-intake-v2/bridge/6t30-gen1-six-20261124.png'
EXPECTED='083f4c63f04f0f06ea9a071c14a75d4670aa212873c940eee5faf794af11b6ae'
ASSET='6t30-gen1-six-20261124.png'

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
    from PIL import ImageOps
    # WB normalizes aspect ratio, so compare the exact changed numeral region
    # against both old and new sources rather than a global difference score.
    oldref=Image.open(ROOT/'assets/product-intake-v2/bridge/6t30-gen1-cover-20261124.png').convert('RGB')
    card=p.read_wb_exact(SKU,'2254117475')
    photos=card.get('photos') or []
    require(photos and photos[0].get('big'),'Existing WB gallery required')
    def digit_check(url):
        r=requests.get(url,params={'audit':str(time.time_ns())},timeout=45)
        r.raise_for_status()
        live=Image.open(io.BytesIO(r.content)).convert('RGB')
        size=live.size
        # All comparison candidates preserve the changed text and identify
        # normalization by global error excluding the small digit region.
        models=[]
        for mode in ('resize','contain_white','contain_gray','fit'):
            def model(img):
                if mode=='resize':return img.resize(size,Image.Resampling.LANCZOS)
                if mode=='fit':return ImageOps.fit(img,size,Image.Resampling.LANCZOS)
                return ImageOps.pad(img,size,Image.Resampling.LANCZOS,color='white' if mode=='contain_white' else '#eeeeee')
            a,b=model(reference),model(oldref)
            box=ImageChops.difference(a,b).getbbox()
            if box is None:continue
            left,top,right,bottom=box
            left=max(0,left-3);top=max(0,top-3);right=min(size[0],right+3);bottom=min(size[1],bottom+3)
            box=(left,top,right,bottom)
            score=sum(ImageStat.Stat(ImageChops.difference(live.resize((180,240)),a.resize((180,240)))).mean)/3
            x=live.crop(box);newerr=sum(ImageStat.Stat(ImageChops.difference(x,a.crop(box))).mean)/3;olderr=sum(ImageStat.Stat(ImageChops.difference(x,b.crop(box))).mean)/3
            models.append((score,newerr,olderr,mode,box))
        score,n,o,mode,box=min(models)
        return {'cover':url,'global_difference':round(score,3),'digit_error_new':round(n,3),'digit_error_old':round(o,3),'normalization':mode,'live_dimensions':list(size),'digit_matches_six':score<25 and n+3<o and n<35}
    try:
        old_check=digit_check(photos[0]['big'])
        if old_check['digit_matches_six']:
            return {'status':'verified','already_present':True,**old_check}
    except Exception:pass
    p.current_main()
    # Upload a 3:4 canvas with the complete approved artwork, not a cropped cover.
    # WB's public CDN uses 900x1200; neutral side padding preserves all original pixels.
    canvas=ImageOps.pad(reference,(900,1200),Image.Resampling.LANCZOS,color='white')
    b=io.BytesIO();canvas.save(b,'JPEG',quality=97,subsampling=0)
    r=requests.post('https://content-api.wildberries.ru/content/v3/media/file',headers={'Authorization':os.environ['WB_API_TOKEN'],'X-Nm-Id':'2254117475','X-Photo-Number':'1'},files={'uploadfile':('cover.jpg',b.getvalue(),'image/jpeg')},timeout=110)
    r.raise_for_status()
    require(not r.json().get('error'),'WB rejected photo')
    def protected(c):
        return {k:(sorted(c[k],key=lambda a:str(a.get('id'))) if k=='characteristics' and isinstance(c[k],list) else c[k]) for k in ('title','description','brand','subjectID','dimensions','sizes','characteristics','video') if k in c}
    result={'status':'submitted_pending_readback'}
    for _ in range(6):
        time.sleep(7)
        after=p.read_wb_exact(SKU,'2254117475')
        require(protected(after)==protected(card),'WB nonmedia field changed')
        ap=after.get('photos')or[]
        require(len(ap)==len(photos),'WB gallery count changed')
        require(ap[1:]==photos[1:],'WB secondary images changed')
        try:
            result.update(**digit_check(ap[0]['big']),secondary_preserved=True)
            if result['digit_matches_six']:
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
    file=ROOT/'data/6t30_gen1_six_tilda_editor_patch_20261124.json'
    p.write_json(file,{'sku':SKU,'uid':'695009271403','external_id':r.get('externalid'),'before_gallery':urls,'replace_first_with':url,'desired_gallery':[url]+urls[1:],'mode':'existing product editor only; no CommerceML import; retain current other fields'})
    return {'status':'editor_required','file':str(file.relative_to(ROOT)),'secondary_count':len(urls)-1},file

def update_avito(url):
    """Exact feed-cell replacement with stable asset preflight and rollback."""
    from openpyxl import load_workbook
    import avito_client as av
    import avito_feed as af
    repo=prev.R
    stable=f'https://github.com/{repo}/releases/download/product-photos/avito_feed.xlsx'
    p.current_main()
    rsp=requests.get(stable,params={'audit':time.time_ns()},timeout=45);rsp.raise_for_status()
    raw=rsp.content
    def rows(v):
        w=load_workbook(io.BytesIO(v),read_only=True,data_only=False)
        try:return {s.title:list(s.values) for s in w}
        finally:w.close()
    old=rows(raw);sheet=old[af.SHEET_ADS]
    hits=[(i,r) for i,r in enumerate(sheet) if i>=af.FIRST_DATA_ROW-1 and str(r[af.COL_ID-1] or '')=='6t30']
    require(len(hits)==1,'Exact single Avito feed row required')
    i,row=hits[0];require(str(row[af.COL_AVITO_ID-1] or '') in ('','8425207775'),'Avito ID conflict')
    # Check current listing identity without touching other ads.
    items=av.list_all_items(statuses=['active'])
    require(len([x for x in items if str(x.get('id'))=='8425207775' and x.get('status')=='active'])==1,'Exact active Avito ad missing')
    pics=[x.strip() for x in str(row[af.COL_IMAGE_URLS-1] or '').splitlines() if x.strip()]
    require(pics,'Empty existing gallery')
    if pics[0]==url:return {'status':'stable_feed_already_present','public_verified':False}
    w=load_workbook(io.BytesIO(raw));w[af.SHEET_ADS].cell(i+1,af.COL_IMAGE_URLS).value='\n'.join([url]+pics[1:])
    b=io.BytesIO();w.save(b);w.close();new=b.getvalue();nr=rows(new)
    require(list(old)==list(nr),'Changed sheets')
    diffs=[]
    for k in old:
        require(len(old[k])==len(nr[k]),'Changed rows')
        for a,(x,y) in enumerate(zip(old[k],nr[k])):
            require(len(x)==len(y),'Changed columns')
            diffs.extend((k,a,b) for b,(v,z) in enumerate(zip(x,y)) if type(v)!=type(z) or v!=z)
    require(diffs==[(af.SHEET_ADS,i,af.COL_IMAGE_URLS-1)],'Unrelated cell changes')
    h={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    api=f'https://api.github.com/repos/{repo}'
    rel=requests.get(api+'/releases/tags/product-photos',headers=h,timeout=35);rel.raise_for_status();rel=rel.json()
    live=[x for x in rel['assets'] if x['name']=='avito_feed.xlsx'];require(len(live)==1,'Stable asset missing')
    live=live[0];backup=f'avito_feed.backup_gen1six_{live["id"]}.xlsx';stage=f'avito_feed.stage_gen1six_{sha(new)[:10]}.xlsx'
    verify=requests.get(stable,params={'audit':time.time_ns()},timeout=45);verify.raise_for_status();require(verify.content==raw,'Stable feed changed concurrently')
    p.current_main()
    upload=requests.post(rel['upload_url'].split('{')[0],headers={**h,'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},params={'name':stage},data=new,timeout=90);upload.raise_for_status();staged=upload.json()
    def rename(a,n):
        r=requests.patch(api+'/releases/assets/'+str(a),headers=h,json={'name':n},timeout=40);r.raise_for_status()
    old_renamed=False;new_renamed=False
    try:
        rename(live['id'],backup);old_renamed=True
        rename(staged['id'],'avito_feed.xlsx');new_renamed=True
        for delay in (0,3,6,10):
            if delay:time.sleep(delay)
            r=requests.get(stable,params={'audit':time.time_ns()},timeout=45)
            if r.ok and r.content==new:break
        else:raise RuntimeError('Stable download failed readback')
    except Exception:
        if new_renamed:rename(staged['id'],stage+'.failed')
        if old_renamed:rename(live['id'],'avito_feed.xlsx')
        raise
    local=ROOT/'data/avito_feed.xlsx'
    local_status='different_from_live_before_preserved'
    if local.exists() and local.read_bytes()==raw:
        local.write_bytes(new);local_status='updated_exact_release_bytes'
    elif local.exists() and local.read_bytes()==new:local_status='already_current'
    else:
        # Apply the same one-cell edit to the newer local workbook, preserving
        # its other pending edits instead of replacing it with an older feed.
        lr=local.read_bytes();ls=rows(lr);matches=[(j,v) for j,v in enumerate(ls[af.SHEET_ADS]) if j>=af.FIRST_DATA_ROW-1 and str(v[af.COL_ID-1] or '')=='6t30']
        require(len(matches)==1,'Local exact feed mapping missing')
        j,v=matches[0];require(str(v[af.COL_AVITO_ID-1] or '') in ('','8425207775'),'Local Avito ID conflict')
        op=[x.strip() for x in str(v[af.COL_IMAGE_URLS-1] or '').splitlines() if x.strip()]
        require(op,'Local photo gallery absent')
        if op[0]!=url:
            book=load_workbook(io.BytesIO(lr));book[af.SHEET_ADS].cell(j+1,af.COL_IMAGE_URLS).value='\n'.join([url]+op[1:]);o=io.BytesIO();book.save(o);book.close()
            nl=rows(o.getvalue());diffs=[]
            for k in ls:
                require(len(ls[k])==len(nl[k]),'Local rows changed')
                for a,(x,y) in enumerate(zip(ls[k],nl[k])):
                    require(len(x)==len(y),'Local columns changed')
                    diffs.extend((k,a,b) for b,(v,z) in enumerate(zip(x,y)) if type(v)!=type(z) or v!=z)
            require(diffs==[(af.SHEET_ADS,j,af.COL_IMAGE_URLS-1)],'Local unrelated change')
            local.write_bytes(o.getvalue())
        local_status='local_one_cell_verified'
    return {'status':'stable_feed_verified_not_listing','public_verified':False,'feed_row':i+1,'avito_id':'8425207775','non_image_cells_unchanged':True,'old_feed_sha':sha(raw),'new_feed_sha':sha(new),'backup_asset':backup,'secondary_preserved':True,'repository_copy':local_status}

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
    for ch,fn in [('ozon',lambda:prev.run_ozon(source)),('wb',lambda:verify_wb(ref,buf.getvalue())),('market',lambda:prev.run_market(source)),('avito',lambda:update_avito(source))]:
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
