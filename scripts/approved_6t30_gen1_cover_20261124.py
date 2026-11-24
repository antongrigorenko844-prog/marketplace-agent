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


def resume_readonly():
    """Recover readback only. Exact report identity is required even if AvitoId is blank in an already-linked feed."""
    import base64
    from openpyxl import load_workbook
    from PIL import ImageOps
    import avito_client as av
    import avito_feed as af
    import content_aux_sync as aux
    from urllib.parse import urlsplit
    p.current_main()
    result=json.loads(OUT.read_text())
    raw=SRC.read_bytes()
    require(sha(raw)==EXPECTED and result['source_sha256']==EXPECTED,'Source changed')
    ref=Image.open(io.BytesIO(raw)).convert('RGB')
    url=result['source_url']
    manifest=json.loads((ROOT/'data/content_manifest.json').read_text())['products']
    links=json.loads((ROOT/'data/avito_links.json').read_text())
    require(str(manifest[SKU]['ids']['avito'])=='8425207775','Manifest identity changed')
    evidence={'checked_utc':p.stamp(),'seller_writes':0,'feed_writes':0,'previous_run':'45377478506','method':'v4 exact mapping for existing linked rows; WB dimension-aware evidence'}
    paths=[OUT]
    def inspect_book(raw):
        book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
        sheets={ws.title:list(ws.values) for ws in book}
        book.close()
        return sheets
    def report_rows(kind):
        endpoint='/autoload/v4/uploads/'+kind
        upload=av._request('GET',endpoint,retries=1)
        require(upload.get('upload_id') and upload.get('started_at'),'Missing upload identity')
        rows=[]; seen=set()
        for page in range(1,41):
            d=av._request('GET',endpoint+'/items',params={'page':page,'perPage':50},retries=1)
            batch=d.get('items')
            require(isinstance(batch,list),'Invalid v4 page')
            for x in batch:
                key=(str(x.get('ad_id')),str(x.get('avito_id')))
                require(key not in seen,'Duplicate v4 mapping')
                seen.add(key);rows.append(x)
            pages=(d.get('meta') or {}).get('pages')
            if (isinstance(pages,int) and page>=pages) or len(batch)<50:break
        else:raise ValueError('Incomplete report pagination')
        again=av._request('GET',endpoint,retries=1)
        require(again.get('upload_id')==upload.get('upload_id'),'Report advanced during read')
        return upload,rows
    def target_row(sheets,sku,aid,mappings):
        aliases={sku}
        if str(links.get(sku,''))==aid:aliases.add(sku+'-av')
        rows=[(i,r) for i,r in enumerate(sheets[af.SHEET_ADS]) if i>=af.FIRST_DATA_ROW-1 and str(r[af.COL_ID-1] or '') in aliases]
        require(len(rows)==1,'Nonunique exact SKU '+sku)
        i,r=rows[0];feed_id=str(r[af.COL_ID-1])
        bound=str(r[af.COL_AVITO_ID-1] or '')
        require(not bound or bound==aid,'Conflicting explicit AvitoId '+sku)
        hit=[x for x in mappings if str(x.get('ad_id'))==feed_id and str(x.get('avito_id'))==aid]
        require(len(hit)==1,'No unique official v4 mapping '+sku)
        require(len([x for x in mappings if str(x.get('ad_id'))==feed_id])==1,'Ambiguous official mapping '+sku)
        return i,r,hit[0],bound
    def image_thumb(im):
        a=im.copy();a.thumbnail((256,360))
        b=io.BytesIO();a.save(b,'JPEG',quality=80)
        return base64.b64encode(b.getvalue()).decode('ascii')
    try:
        card=p.read_wb_exact(SKU,'2254117475')
        photos=card.get('photos') or []
        require(photos and photos[0].get('big'),'WB gallery absent')
        u=photos[0]['big']
        r=requests.get(u,params={'audit':str(time.time_ns())},timeout=45);r.raise_for_status()
        live=ImageOps.exif_transpose(Image.open(io.BytesIO(r.content))).convert('RGB')
        a=live.copy();a.thumbnail((280,400))
        def mad(candidate):
            return round(sum(ImageStat.Stat(ImageChops.difference(a,candidate)).mean)/3,3)
        ratio_delta=abs(live.width/live.height-ref.width/ref.height)
        stretched=mad(ref.resize(a.size,Image.Resampling.LANCZOS))
        fitted=mad(ImageOps.fit(ref,a.size,method=Image.Resampling.LANCZOS))
        strict=ratio_delta<0.03 and stretched<10
        evidence['wb']={'status':'cover_matches' if strict else 'aspect_or_content_mismatch_requires_review','cover':u,'photo_count':len(photos),'secondary':photos[1:],'live_dimensions':list(live.size),'source_dimensions':list(ref.size),'aspect_ratio_delta':round(ratio_delta,5),'stretched_pixel_difference':stretched,'center_crop_pixel_difference':fitted,'live_sha256':sha(r.content),'live_thumbnail_jpeg_base64':image_thumb(live),'approved_thumbnail_jpeg_base64':image_thumb(ref),'protected_before_after':'not recoverable from pre-write marker; no new upload'}
        if strict:result['channels']['wb']['status']='cover_readback_matches_protection_unresolved'
    except Exception as exc:evidence['wb']={'status':'read_error','error':str(exc)[:300]}
    reports={}
    for kind in ('last_successful','current'):
        try:reports[kind]=report_rows(kind)
        except Exception as exc:reports[kind]=str(exc)[:300]
    try:
        upload,mappings=reports['last_successful']
        stable='https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/'
        a=requests.get(stable+'avito_feed.xlsx',params={'audit':str(time.time_ns())},timeout=60);a.raise_for_status()
        b=requests.get(stable+'avito_feed.backup_6t30_20261123_786459916.xlsx',timeout=60);b.raise_for_status()
        require(sha(a.content)=='264444c623fb7e34a9cff0982344b2fccbdd6368853d00b90766328750e016b9','Current release bytes differ from recorded promotion')
        require(sha(b.content)=='1d045a88012b57790ba8da79438823477e3fb395d840926d1b668974b138d5d3','Backup bytes differ from recorded pre-write')
        old,new=inspect_book(b.content),inspect_book(a.content)
        i,row,hit,bound=target_row(new,SKU,'8425207775',mappings)
        j,oldrow,oldhit,oldbound=target_row(old,SKU,'8425207775',mappings)
        require(i==j and old.keys()==new.keys(),'Workbook structure changed')
        require(all(len(old[k])==len(new[k]) for k in old),'Workbook row count changed')
        diffs=[]
        for k in old:
            for n,(r1,r2) in enumerate(zip(old[k],new[k])):
                require(len(r1)==len(r2),'Workbook columns changed')
                for m,(v1,v2) in enumerate(zip(r1,r2)):
                    if v1!=v2:diffs.append((k,n,m))
        require(diffs==[(af.SHEET_ADS,i,af.COL_IMAGE_URLS-1)],'Unexpected workbook cell differences')
        images=[x.strip() for x in str(row[af.COL_IMAGE_URLS-1]).splitlines() if x.strip()]
        prior=[x.strip() for x in str(oldrow[af.COL_IMAGE_URLS-1]).splitlines() if x.strip()]
        require(images==[url]+prior[1:],'Gallery mismatch')
        evidence['avito_stable']={'status':'exact_single_cell_change_verified','sha256':sha(a.content),'previous_sha256':sha(b.content),'row':i+1,'avito_id':'8425207775','mapping_upload_id':upload['upload_id'],'feed_id':hit['ad_id'],'explicit_avito_id_cell':bound,'secondary_preserved':True,'all_other_cells_unchanged':True}
        local=ROOT/'data/avito_feed.xlsx'
        if local.exists():
            if sha(local.read_bytes())==sha(b.content):
                local.write_bytes(a.content);paths.append(local)
                evidence['avito_stable']['repository_copy']='recovered from verified release'
            elif sha(local.read_bytes())==sha(a.content):
                evidence['avito_stable']['repository_copy']='already exact'
            else:evidence['avito_stable']['repository_copy']='different bytes; not overwritten'
        result['channels']['avito']={'status':'stable_feed_verified_not_listing','new_feed_sha':sha(a.content),'non_image_cells_unchanged':True}
    except Exception as exc:evidence['avito_stable']={'status':'read_error_or_mismatch','error':str(exc)[:300]}
    evidence['avito_received']={}
    for kind in ('last_successful','current'):
        try:
            require(isinstance(reports[kind],tuple),'Report read failed: '+str(reports[kind])[:250])
            upload,mappings=reports[kind]
            matches=[f for f in upload.get('feed_urls',[]) if f.get('name')=='LEOPARTS запчасти']
            require(len(matches)==1,'Unique received feed missing')
            link=matches[0].get('url');u=urlsplit(link)
            require(u.scheme=='https' and u.hostname=='api.avito.ru' and u.path.startswith('/autoload/v2/feed/content/'),'Unexpected official snapshot path')
            resp=requests.get(link,headers={'Authorization':av._headers()['Authorization']},timeout=40,allow_redirects=False);resp.raise_for_status()
            book=inspect_book(resp.content)
            i,row,hit,bound=target_row(book,SKU,'8425207775',mappings)
            h_i,h_row,h_hit,h_bound=target_row(book,'0am325025H','8312205273',mappings)
            rule=manifest['0am325025H']['text']['avito']['description']
            desired=aux.desired_avito('0am325025H',rule,h_row[af.COL_PRICE-1])
            again=av._request('GET','/autoload/v4/uploads/'+kind,retries=1)
            require(again.get('upload_id')==upload.get('upload_id'),'Upload changed during snapshot read')
            evidence['avito_received'][kind]={'upload_id':upload.get('upload_id'),'status':upload.get('status'),'started_at':upload.get('started_at'),'sha256':sha(resp.content),'cover_exact':str(row[af.COL_IMAGE_URLS-1]).splitlines()[0]==url,'H_description_exact':str(h_row[af.COL_DESCRIPTION-1] or '')==desired,'item':{k:hit.get(k) for k in ('ad_id','avito_id','status','section','messages')},'public_verified':False}
        except Exception as exc:evidence['avito_received'][kind]={'error':str(exc)[:300]}
    try:
        active=av.list_all_items(statuses=['active'])
        evidence['avito_seller_current']={}
        for sku,aid in ((SKU,'8425207775'),('0am325025H','8312205273')):
            hits=[x for x in active if str(x.get('id'))==aid and x.get('status')=='active']
            require(len(hits)==1,'Current active identity unavailable '+sku)
            x=hits[0]
            evidence['avito_seller_current'][sku]={'id':aid,'status':x.get('status'),'description_available':isinstance(x.get('description'),str),'images_available':bool(x.get('images') or x.get('photos')),'public_verified':False}
            if sku=='0am325025H' and isinstance(x.get('description'),str):
                evidence['avito_seller_current'][sku]['approved_compatibility_sentence_present']='Если на вашем автомобиле установлена именно DSG7 DQ200 (0AM или 0CW), этот мехатроник без электронной платы подходит на 100% при переносе исправной штатной платы. После установки необходима адаптация.' in x['description']
    except Exception as exc:evidence['avito_seller_current_error']=str(exc)[:300]
    evidence['tilda']={'status':'existing_editor_required','public_cover_not_verified':True,'previous_patch_preserved':True}
    result['recovery_readback']=evidence
    p.write_json(OUT,result)
    p.checkpoint(paths,live=True)
    print(json.dumps({**evidence,'wb':{k:v for k,v in evidence['wb'].items() if not k.endswith('base64')}},ensure_ascii=False))
    return 0

if __name__=='__main__':sys.exit(resume_readonly() if '--resume-readonly' in sys.argv else main())
