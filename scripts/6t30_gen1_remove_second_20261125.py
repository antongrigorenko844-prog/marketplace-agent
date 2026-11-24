"""Owner-approved 6t30 Gen1: remove ONLY gallery item #2.
Preserve the current primary image, all other media, all commercial fields and all other SKUs.
"""
import copy,hashlib,io,json,os,sys,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from openpyxl import load_workbook
from PIL import Image,ImageChops,ImageStat
import requests
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
import content_pipeline as p
import content_manifest as cm
import content_master_sync as ms
import content_sync as cs
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym
import avito_client as av
import avito_feed as af
from market_content import read_rows,grouped,preserve_typed_parameters
SKU='6t30';OZID='6287285958';WBID='2254117475';AVID='8425207775';UID='695009271403'
REPO='antongrigorenko844-prog/marketplace-agent'
COVER='https://github.com/'+REPO+'/releases/download/product-photos/6t30-gen1-six-20261124.png?v=083f4c63f0'
SLIDE='https://raw.githubusercontent.com/'+REPO+'/33e049bbab6a0c547501fe8ce3b06ccc00c74e78/photos/media-three-20261116/6t30-preview.png'
COVER_SHA='083f4c63f04f0f06ea9a071c14a75d4670aa212873c940eee5faf794af11b6ae'
SLIDE_SHA='f21b3fb17a43a6604a7ce0e6b0243b6024c539e9c739f6c15a994d0c6229ea8c'
OUT=ROOT/'data/6t30_gen1_remove_second_result_20261125.json'
MAN=ROOT/'data/content_manifest.json'
TILDA=ROOT/'data/6t30_gen1_remove_second_tilda_20261125.json'
report={'sku':SKU,'scope':'remove gallery item #2 only','owner_approved':True,'channels':{},'source_files':{},'price_writes':0,'stock_writes':0,'description_writes':0,'other_sku_writes':0,'started_utc':p.stamp()}
def req(ok,msg):
    if not ok:raise ValueError(msg)
def one(rows,label):
    req(len(rows)==1,'Nonunique '+label);return rows[0]
def sha(raw):return hashlib.sha256(raw).hexdigest()
def save():p.write_json(OUT,report)
def norm(url):return cm.normalize_url(url)
def gallery_after(urls):
    req(isinstance(urls,list) and urls and all(isinstance(x,str) and x.startswith('https://') for x in urls),'Missing or invalid gallery')
    req(len({norm(x) for x in urls})==len(urls),'Duplicate gallery entries; review')
    return urls[:1]+urls[2:] if len(urls)>1 else urls[:]
def download(url):
    r=requests.get(url,params={'audit':str(time.time_ns())},timeout=45)
    r.raise_for_status()
    req(0<len(r.content)<30_000_000,'Invalid image length')
    im=Image.open(io.BytesIO(r.content));im.load()
    return r.content,im.convert('RGB')
def pixels_same(a,b):
    ba,ia=a;bb,ib=b
    if sha(ba)==sha(bb):return True,0.0
    if abs(ia.width/ia.height-ib.width/ib.height)>0.025:return False,999.0
    i=ia.resize((256,256),Image.Resampling.LANCZOS);j=ib.resize((256,256),Image.Resampling.LANCZOS)
    mad=sum(ImageStat.Stat(ImageChops.difference(i,j)).mean)/3
    return mad<3.0,round(mad,3)
def compare_urls(expected,actual):
    req(len(expected)==len(actual),'Gallery count differs')
    return all(norm(x)==norm(y) or pixels_same(download(x),download(y))[0] for x,y in zip(expected,actual))
def cell_patch(raw,sheet,key_col,photo_col,start,sep,check_id=False):
    w=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
    try:
        ws=w[sheet] if sheet else w.active
        r=one([r for r in ws.iter_rows(min_row=start) if str(r[key_col-1].value or '').strip()==SKU],'exact workbook row')
        if check_id:req(str(r[af.COL_AVITO_ID-1].value or '') in ('',AVID),'Avito workbook ID mismatch')
        c=r[photo_col-1];req(isinstance(c.value,str),'Photo cell missing')
        urls=[x.strip() for x in c.value.split(sep) if x.strip()]
        want=gallery_after(urls)
        # The current source cover must be the approved cover from the previous task.
        req(norm(urls[0])==norm(COVER),'Unexpected source cover; preserve existing workbook')
        if len(urls)>1:req(norm(urls[1])==norm(SLIDE),'Second source photo not the known slide')
        edit={c.coordinate:sep.join(want)} if want!=urls else {}
        name=ws.title
    finally:w.close()
    new=ms.patch_cells(raw,name,edit)
    return new,{'status':'verified','before_count':len(urls),'after_count':len(want),'main_unchanged':want[0]==urls[0],'changed_cells':list(edit),'removed':urls[1] if len(urls)>1 else None}
def source_changes():
    before=json.loads(MAN.read_text());after=copy.deepcopy(before);row=after['products'][SKU];old=before['products'][SKU]
    req(str(row['ids']['wb'])==WBID and str(row['ids']['avito'])==AVID and str(row['ids']['tilda'])==UID,'Manifest identity changed')
    for ch,urls in row['sources'].items():
        req(norm(urls[0])==norm(COVER),'Unexpected canonical cover')
        if len(urls)>1:req(norm(urls[1])==norm(SLIDE),'Unexpected canonical second slide')
        row['sources'][ch]=gallery_after(urls)
    slide=row.pop('second_slide',None)
    if slide:req(slide['sha256']==SLIDE_SHA and norm(slide['url'])==norm(SLIDE),'Unexpected slide identity')
    req(row['text']==old['text'] and row.get('market_parameters')==old.get('market_parameters') and row.get('wb_characteristics')==old.get('wb_characteristics'),'Nonmedia manifest changed')
    verify=copy.deepcopy(after);verify['products'][SKU]=old;req(verify==before,'Other SKU changed')
    main=ROOT/'photos/6t30/6t30_1.png';req(sha(main.read_bytes())==COVER_SHA,'Local approved main bytes changed')
    mainav=ROOT/'photos_avito/6t30/6t30_1.jpg';req(mainav.is_file(),'Avito primary source missing')
    avsha=sha(mainav.read_bytes())
    changes=[]
    if before!=after:
        MAN.write_text(json.dumps(after,ensure_ascii=False,indent=2)+'\n');changes.append(MAN)
    cm.load()
    for filename,sheet,keycol,photocol,start,sep in [('data/ozon_catalog.xlsx','Ozon',1,6,2,'|'),('data/wb_catalog.xlsx','WB',1,4,2,'|'),('data/avito_photos.xlsx',None,1,2,2,'|')]:
        path=ROOT/filename
        try:
            raw=path.read_bytes();new,e=cell_patch(raw,sheet,keycol,photocol,start,sep)
            if new!=raw:path.write_bytes(new);changes.append(path)
            report['source_files'][filename]=e
        except Exception as ex:report['source_files'][filename]={'status':'blocked','error':str(ex)[:250]}
    # Retire the active file, retaining an exact recovery copy outside photos/.
    active=ROOT/'photos/6t30/6t30_2.png'
    archive=ROOT/'assets/product-intake-v2/bridge/6t30-gen1-retired-second-20261125.png'
    if active.exists():
        req(sha(active.read_bytes())==SLIDE_SHA,'Unexpected active secondary bytes')
        req(not archive.exists() or sha(archive.read_bytes())==SLIDE_SHA,'Unexpected archive')
        if archive.exists():active.unlink()
        else:active.rename(archive)
        changes.extend([active,archive])
    req(sha(main.read_bytes())==COVER_SHA and sha(mainav.read_bytes())==avsha,'Primary source bytes changed')
    report['source_files']['manifest']={'status':'verified','only_target_gallery_changed':True,'other_skus_unchanged':True,'second_slide_rule_removed':True}
    report['source_files']['local_photo']={'status':'verified','primary_sha256':COVER_SHA,'primary_bytes_unchanged':True,'avito_primary_bytes_unchanged':True,'second_removed_from_active_folder':not active.exists(),'archived':archive.exists()}
    save()
    # Explicitly stage deletion as well as retained backup, not unrelated files.
    p.current_main()
    paths=[str(x.relative_to(ROOT)) for x in [OUT,*changes]]
    p.git('add','-A','--',*paths)
    if p.git('diff','--cached','--name-only'):
        p.git('config','user.name','marketplace-agent-bot');p.git('config','user.email','actions@users.noreply.github.com')
        p.git('commit','-m','chore: remove only Gen1 second slide [skip ci]');p.git('push','origin','HEAD:main')
def ozon():
    def read():
        c=one([x for x in oz.get_product_names([SKU]) if x.get('offer_id')==SKU],'Ozon SKU')
        req(str(c.get('id') or c.get('product_id'))==OZID,'Ozon product mismatch')
        price=one([x for x in oz.get_prices_and_stocks([SKU]) if x.get('offer_id')==SKU],'Ozon price')
        return c,price
    b,bp=read();old=cs.ozon_gallery(b);want=gallery_after(old)
    orig=download(old[0])
    # Preserve the exact live primary URL; no replacement with another design.
    if len(old)>1:
        p.current_main()
        payload={'offer_id':SKU,'primary_image':b['primary_image'],'images':want[1:],'color_image':b.get('color_image') or ''}
        reply=oz._post('/v2/product/pictures/import',{'items':[payload]},retries=1)
        req(reply and not reply.get('error'),'Ozon media request rejected')
        task=reply.get('task_id') or (reply.get('result') or {}).get('task_id')
    else:task=None
    def protected(c,price):
        import ozon_description_guard as og
        return ({k:c.get(k) for k in ('id','offer_id','sku','name','description_category_id','type_id','height','depth','width','weight','barcode','barcodes','color_image','images360','model_info','pdf_list')},og.attributes(c),c.get('complex_attributes'),og.price_projection(price))
    before=protected(b,bp)
    result={'status':'submitted_pending_readback','before_count':len(old),'expected_count':len(want),'primary_before':old[0],'task_id':task,'removed_url':old[1] if len(old)>1 else None}
    for _ in range(8 if len(old)>1 else 1):
        if len(old)>1:time.sleep(5)
        a,ap=read();actual=cs.ozon_gallery(a)
        if protected(a,ap)!=before:return {**result,'status':'protected_drift'}
        result['actual_count']=len(actual)
        if len(actual)!=len(want):continue
        same,mad=pixels_same(orig,download(actual[0]))
        result.update(primary_after=actual[0],primary_content_unchanged=same,primary_pixel_difference=mad,primary_url_preserved=actual[0]==old[0],other_media_unchanged=compare_urls(want,actual),protected_unchanged=True)
        if same and result['other_media_unchanged']:result['status']='verified';return result
    return result
def wildberries():
    b=p.read_wb_exact(SKU,WBID);old=cs.wb_gallery(b);want=gallery_after(old);orig=download(old[0])
    if len(old)>1:
        # The supported media endpoint accepts the retained gallery URLs. Reuse
        # the actual live first photo rather than uploading a different image.
        payload=want+([b['video']] if b.get('video') else [])
        p.current_main()
        reply=wb.update_media(int(WBID),payload,retries=1)
        req(isinstance(reply,dict) and not reply.get('error'),'WB rejected retained gallery')
    result={'status':'submitted_pending_readback','before_count':len(old),'expected_count':len(want),'primary_before':old[0],'removed_url':old[1] if len(old)>1 else None}
    for _ in range(10 if len(old)>1 else 1):
        if len(old)>1:time.sleep(5)
        a=p.read_wb_exact(SKU,WBID);actual=cs.wb_gallery(a)
        fields=('nmID','vendorCode','brand','title','description','subjectID','dimensions','sizes','characteristics','video','kizMarked')
        bad=[k for k in fields if a.get(k)!=b.get(k)]
        if bad:return {**result,'status':'protected_drift','protected_differences':bad}
        result['actual_count']=len(actual)
        if len(actual)!=len(want):continue
        same,mad=pixels_same(orig,download(actual[0]))
        result.update(primary_after=actual[0],primary_content_unchanged=same,primary_pixel_difference=mad,primary_url_preserved=actual[0]==old[0],other_media_unchanged=compare_urls(want,actual),protected_unchanged=True)
        if same and result['other_media_unchanged']:result['status']='verified';return result
    return result
def market():
    biz=ym.get_business_id()
    def read():
        b=one([x['offer'] for x in read_rows(biz,'offer-mappings',[SKU]) if x['offer'].get('offerId')==SKU],'Market exact offer')
        c=one([x for x in read_rows(biz,'offer-cards',[SKU]) if x.get('offerId')==SKU],'Market exact card')
        req(c['mapping']['marketCategoryId']==71380208,'Market category mismatch')
        return b,c
    b,c=read();old=list(b.get('pictures') or []);want=gallery_after(old)
    if len(old)>1:
        p.current_main()
        payload=preserve_typed_parameters(biz,[{'offer':{'offerId':SKU,'pictures':want}}])
        code,reply=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':payload},retries=1)
        req(code==200 and reply.get('status')=='OK' and not any(x.get('errors') for x in reply.get('results',[])),'Market media rejected')
    result={'status':'submitted_pending_readback','before_count':len(old),'expected_count':len(want),'primary_before':old[0],'removed_url':old[1] if len(old)>1 else None}
    for _ in range(8 if len(old)>1 else 1):
        if len(old)>1:time.sleep(5)
        a,z=read();actual=a.get('pictures') or []
        bad=[k for k in ('offerId','name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions') if b.get(k)!=a.get(k)]
        if b.get('mediaFiles',{}).get('videos')!=a.get('mediaFiles',{}).get('videos'):bad.append('video')
        if c.get('mapping')!=z.get('mapping') or grouped(c.get('parameterValues',[]))!=grouped(z.get('parameterValues',[])):bad.append('typed_characteristics')
        if bad:return {**result,'status':'protected_drift','protected_differences':bad}
        result.update(actual_count=len(actual),primary_after=actual[0] if actual else None,primary_url_preserved=bool(actual and actual[0]==old[0]),protected_unchanged=True)
        if actual==want:result.update(status='verified',primary_content_unchanged=True,other_media_unchanged=True);return result
    return result
def avito():
    stable=f'https://github.com/{REPO}/releases/download/product-photos/avito_feed.xlsx'
    one([x for x in av.list_all_items(statuses=['active']) if str(x.get('id'))==AVID and x.get('status')=='active'],'active Avito listing')
    def patch(raw):return cell_patch(raw,af.SHEET_ADS,af.COL_ID,af.COL_IMAGE_URLS,af.FIRST_DATA_ROW,'\n',True)
    p.current_main();r=requests.get(stable,params={'audit':time.time_ns()},timeout=45);r.raise_for_status();raw=r.content;new,e=patch(raw)
    local=ROOT/'data/avito_feed.xlsx';local_before=local.read_bytes();local_new,le=patch(local_before)
    result={**e,'status':'stable_feed_already_correct','avito_id':AVID,'public_verified':False,'non_image_cells_unchanged':True}
    if raw!=new:
        h={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
        api=f'https://api.github.com/repos/{REPO}'
        rel=requests.get(api+'/releases/tags/product-photos',headers=h,timeout=35);rel.raise_for_status();rel=rel.json()
        asset=one([x for x in rel['assets'] if x['name']=='avito_feed.xlsx'],'stable feed asset')
        backup=f'avito_feed.backup_gen1remove2_{asset["id"]}.xlsx';stage=f'avito_feed.stage_gen1remove2_{sha(new)[:10]}.xlsx'
        check=requests.get(stable,params={'audit':time.time_ns()},timeout=45);check.raise_for_status();req(check.content==raw,'Concurrent stable feed edit')
        p.current_main()
        upload=requests.post(rel['upload_url'].split('{')[0],headers={**h,'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},params={'name':stage},data=new,timeout=90);upload.raise_for_status();staged=upload.json()
        def rename(id,name):
            z=requests.patch(api+'/releases/assets/'+str(id),headers=h,json={'name':name},timeout=40);z.raise_for_status()
        old_renamed=False;new_renamed=False
        try:
            rename(asset['id'],backup);old_renamed=True;rename(staged['id'],'avito_feed.xlsx');new_renamed=True
            for delay in (0,3,6,10):
                if delay:time.sleep(delay)
                z=requests.get(stable,params={'audit':time.time_ns()},timeout=45)
                if z.ok and z.content==new:break
            else:raise RuntimeError('Stable feed readback failed')
        except Exception:
            if new_renamed:rename(staged['id'],stage+'.failed')
            if old_renamed:rename(asset['id'],'avito_feed.xlsx')
            raise
        result.update(status='stable_feed_verified_not_listing',old_feed_sha=sha(raw),new_feed_sha=sha(new),backup=backup)
    req(local.read_bytes()==local_before,'Local feed changed concurrently')
    if local_new!=local_before:local.write_bytes(local_new)
    result.update(repository_copy_correct=True,local_cells=le['changed_cells'])
    return result
def tilda():
    from scripts.leoparts_direct_operation_nov21 import site_records
    rows=[x for x in site_records().values() if x.get('sku')==SKU]
    r=one(rows,'Tilda exact SKU')
    req(str(r.get('uid'))==UID and r.get('externalid')==SKU,'Tilda UID mismatch')
    gallery=r.get('gallery')
    if isinstance(gallery,str):gallery=json.loads(gallery)
    urls=[]
    for item in gallery:
        u=item if isinstance(item,str) else next((item.get(k) for k in ('img','url','original','src','large') if isinstance(item.get(k),str) and item[k].startswith('https://')),None)
        req(u,'Unrecognized Tilda photo');urls.append(u)
    want=gallery_after(urls)
    d={'sku':SKU,'uid':UID,'external_id':SKU,'before_gallery':urls,'desired_gallery':want,'remove_index':2,'current_primary_unchanged':urls[0]==want[0],'mode':'existing product editor, remove only second photo; do not reimport or replace cover','supersedes':'earlier pending photo editor patch for second-photo scope'}
    p.write_json(TILDA,d)
    return {'status':'verified_already_one' if urls==want else 'editor_required','before_count':len(urls),'expected_count':len(want),'primary_unchanged_in_plan':True,'primary_before':urls[0],'removed_url':urls[1] if len(urls)>1 else None,'patch':str(TILDA.relative_to(ROOT)),'reason':'No supported existing-product gallery delete through the connected Tilda API; CommerceML photo import appends instead of deleting.'}
def main():
    req(os.getenv('GITHUB_REF')=='refs/heads/main','main only')
    req(not OUT.exists(),'Previous run exists; independent readback before retry')
    p.current_main();source_changes()
    tasks={'ozon':ozon,'wb':wildberries,'market':market,'avito':avito,'tilda':tilda}
    with ThreadPoolExecutor(max_workers=5) as pool:
        fs={pool.submit(f):k for k,f in tasks.items()}
        for fut in as_completed(fs):
            k=fs[fut]
            try:report['channels'][k]=fut.result()
            except Exception as e:report['channels'][k]={'status':'blocked_or_uncertain','error':str(e)[:450]}
            save();print(k,report['channels'][k]['status'],flush=True)
    report['finished_utc']=p.stamp();save()
    p.checkpoint([OUT,TILDA,ROOT/'data/avito_feed.xlsx'],live=True)
if __name__=='__main__':main()
