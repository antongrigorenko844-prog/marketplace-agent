"""One approved existing 0am325025H gallery change, retaining the old cover.
The existing GitHub Release media host is reused. Never writes commercial fields,
other SKUs, Avito existing listings, or unverified Tilda import operations.
A journal is committed before external catalog writes; retry by reading live media.
"""
from __future__ import annotations
import copy, hashlib, io, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
import requests
from PIL import Image, ImageOps, ImageChops, ImageStat

ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path[:0]=[str(ROOT),str(ROOT/'.github/scripts')]
import content_pipeline as pipeline
import photo_host
import content_manifest as cm

SKU='0am325025H'
WB_ID=681503332
TILDA_UID='908157881413'
BIZ=194752944
FEED_ID=4061297
AVITO_OLD=8312205273
SOURCE=ROOT/'assets/product-intake-v2/bridge/0am325025H-cover-20261126.jpeg'
EXPECTED='1f0b5739a1b476268c9d6217970dbdc0a0c23dba728405ce67d65551eca4ed0c'
REQUEST=ROOT/'drafts/dq200_0am325025H_main_photo_correction_20261126.json'
AVITO_DRAFT=ROOT/'drafts/avito_0am325025H_new_listing_20261126.json'
OUT=ROOT/'data/dq200_cover_exact_20261126_result.json'
MANIFEST=ROOT/'data/content_manifest.json'
CACHE=ROOT/'data/photo_upload_cache.json'
J={}

def require(v,message):
    if not v: raise ValueError(message)

def sha(raw):return hashlib.sha256(raw).hexdigest()

def same_url(a,b):
    return cm.normalize_url(a)==cm.normalize_url(b)

def urls_only(rows):
    require(isinstance(rows,list) and rows,'No verified existing gallery')
    require(all(isinstance(x,str) and x.startswith('https://') for x in rows),'Invalid media URL')
    require(len({cm.normalize_url(x) for x in rows})==len(rows),'Ambiguous duplicate existing gallery')
    return rows

def prepend(old,new):
    old=urls_only(list(old))
    return [new]+[v for v in old if not same_url(v,new)]

def exact_bytes(path,expected,dimensions):
    raw=path.read_bytes()
    require(sha(raw)==expected,'Original SHA256 mismatch: '+path.name)
    with Image.open(io.BytesIO(raw)) as im:
        require(list(im.size)==list(dimensions),'Original dimensions mismatch: '+path.name)
        im.verify()
    return raw

def download(url):
    require(url.startswith('https://'),'HTTPS required')
    r=requests.get(url,timeout=35,headers={'User-Agent':'LEOPARTS-media-verification','Cache-Control':'no-cache'})
    r.raise_for_status()
    return r.content

def verified_host(path,expected,dimensions,asset):
    raw=exact_bytes(path,expected,dimensions)
    pipeline.current_main()
    # A unique content-addressed asset cannot overwrite the old shared cover.
    url=photo_host.upload_file(str(path),filename=asset)
    require(url.startswith('https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/'),'Unexpected media host')
    for _ in range(5):
        try:
            if sha(download(url))==expected:return {'url':url,'sha256':expected,'bytes':len(raw),'dimensions':dimensions}
        except (requests.RequestException,OSError):pass
        time.sleep(3)
    raise ValueError('Hosted image bytes not verified: '+asset)

def journal(stage,**fields):
    J['channels'][stage]=dict(J['channels'].get(stage,{}),**fields)
    J['updated_utc']=pipeline.stamp()
    pipeline.write_json(OUT,J)
    print(json.dumps({'stage':stage,**fields},ensure_ascii=False,default=str)[:8000],flush=True)

def save(*paths):
    pipeline.write_json(OUT,J)
    pipeline.checkpoint([OUT,*paths],live=True)

def guard(stage,fn):
    try:fn()
    except Exception as e:
        journal(stage,status='blocked_or_unverified',error=type(e).__name__+': '+str(e)[:450])
        save()

def one(rows,key,val):
    hits=[x for x in rows if str(x.get(key))==str(val)]
    require(len(hits)==1,'Exact live identity not unique: '+str(val))
    return hits[0]

def digest_image(raw):
    with Image.open(io.BytesIO(raw)) as im:
        return ImageOps.exif_transpose(im).convert('RGB')

def image_difference(a,b):
    a=a.copy();a.thumbnail((240,320))
    require(abs(a.width/a.height-b.width/b.height)<0.055,'Image aspect ratio differs')
    return round(sum(ImageStat.Stat(ImageChops.difference(a,b.resize(a.size,Image.Resampling.LANCZOS))).mean)/3,3)

def match(url,reference):
    try:return image_difference(reference,digest_image(download(url)))<10
    except (requests.RequestException,OSError,ValueError):return False

def persist_sources(channel,urls,video=None):
    """Use the approved existing manifest and exact one-row catalog fields.
    Do not re-create product rows or mutate commercial columns.
    """
    from openpyxl import load_workbook
    path=MANIFEST
    data=json.loads(path.read_text(encoding='utf-8'))
    before=copy.deepcopy(data)
    row=data['products'][SKU]
    row.setdefault('sources',{})[channel]=list(urls)
    before['products'][SKU].setdefault('sources',{})[channel]=list(urls)
    require(data==before,'Unrelated manifest change')
    paths=[path]
    # Existing catalog media columns are used by the legacy refresh pipeline.
    specs={'ozon':('ozon_catalog.xlsx','Ozon',6),'wb':('wb_catalog.xlsx','WB',4)}
    if channel in specs:
        filename,sheet,col=specs[channel]
        catalog=ROOT/'data'/filename
        book=load_workbook(catalog)
        ws=book[sheet]
        hits=[i for i in range(2,ws.max_row+1) if str(ws.cell(i,1).value or '').strip()==SKU]
        require(len(hits)==1,'Catalog exact row missing: '+filename)
        old=ws.cell(hits[0],col).value
        require(isinstance(old,str) and old.startswith('https://'),'Existing catalog media missing')
        values=list(urls)+([video] if video else [])
        ws.cell(hits[0],col).value='|'.join(values)
        book.save(catalog)
        paths.append(catalog)
        journal(channel,catalog_row=hits[0],catalog_media_count=len(values))
    path.write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    cm.load()
    save(*paths)

def host_cover():
    req=json.loads(REQUEST.read_text())
    require(req['sku']==SKU and req['owner_approved'] is True and req['source']['sha256']==EXPECTED,'Approval mismatch')
    require(str(SOURCE.relative_to(ROOT))==req['source']['required_repository_path'],'Source path mismatch')
    require(len(SOURCE.read_bytes())==req['source']['bytes'],'Source size mismatch')
    proof=verified_host(SOURCE,EXPECTED,[960,1280],'0am325025H-cover-20261126-'+EXPECTED[:10]+'.jpeg')
    journal('source',status='verified',**proof)
    save(CACHE)
    return proof['url']

def host_avito():
    draft=json.loads(AVITO_DRAFT.read_text(encoding='utf-8'))
    require(draft['sku']==SKU and draft['price_rub']==29500 and draft['publication']['owner_requested_create'] is True,'Avito draft approval mismatch')
    require(draft['existing_listing_protection']['avito_id']==AVITO_OLD,'Protected Avito ID mismatch')
    gallery=[draft['gallery']['cover_candidate'],*draft['gallery']['additional_photos']]
    require([x['position'] for x in gallery]==list(range(1,8)),'Avito ordered gallery mismatch')
    proofs=[]
    for row in gallery:
        pos=row['position'];src=ROOT/row['repository_path'];expected=row['sha256']
        require(src.parent==ROOT/'assets/product-intake-v2/bridge/avito-dq200-20261126','Unexpected Avito photo source')
        name=f'avito-dq200-20261126-{pos:02d}-{expected[:10]}{src.suffix.lower()}'
        info=verified_host(src,expected,row['dimensions'],name)
        proofs.append({'position':pos,'filename':row['filename'],**info})
        journal('avito_media',status='hosting',images=proofs)
        save(CACHE)
    journal('avito_media',status='verified',images=proofs,count=7,price_rub=29500)
    save(CACHE)

def ozon(url):
    import ozon_client as oz
    def read():return one(oz.get_product_names([SKU]),'offer_id',SKU)
    def ordered(r):
        p=r.get('primary_image');require(isinstance(p,str) and p,'Ozon primary image absent')
        a=r.get('images') or []
        require(isinstance(a,list) and all(isinstance(x,str) for x in a),'Unknown Ozon gallery schema')
        return urls_only([p]+[x for x in a if not same_url(x,p)])
    def protection(r):
        return {k:r.get(k) for k in ('id','product_id','offer_id','name','description_category_id','type_id','height','width','depth','weight','attributes','complex_attributes','color_image')}
    before=read(); old=ordered(before); desired=prepend(old,url)
    entry=J['channels'].get('ozon',{})
    if entry.get('before_gallery'):
        old=list(entry['before_gallery']);desired=prepend(old,url)
        require(ordered(before)==old or ordered(before)==desired,'Ozon gallery changed since previous intent')
    if desired!=ordered(before):
        journal('ozon',status='intent',product_id=before.get('id') or before.get('product_id'),before_gallery=old,desired_gallery=desired,protected=protection(before))
        save()
        pipeline.current_main()
        body={'items':[{'offer_id':SKU,'primary_image':url,'images':desired[1:],'color_image':before.get('color_image') or ''}]}
        response=oz._post('/v2/product/pictures/import',body,retries=1)
        task=response.get('task_id') or (response.get('result') or {}).get('task_id')
        journal('ozon',status='submitted',task_id=task)
        save()
    after=read()
    for _ in range(12):
        if ordered(after)==desired:break
        time.sleep(5);after=read()
    require(protection(before)==protection(after),'Ozon protected catalog fields changed')
    actual=ordered(after)
    ok=actual==desired
    if not ok and len(actual)==len(desired):
        ok=all(match(a,digest_image(download(b))) for a,b in zip(actual,desired))
    journal('ozon',status='verified' if ok else 'submitted_pending_readback',product_id=after.get('id') or after.get('product_id'),before_gallery=old,desired_gallery=desired,actual_gallery=actual,protected_unchanged=True)
    save()
    if ok:persist_sources('ozon',desired)

def market(url):
    import yandex_market_client as ym
    from market_content import read_rows,grouped
    biz=ym.get_business_id();require(int(biz)==BIZ,'Market business mismatch')
    def read():
        row=one([r for r in read_rows(biz,'offer-mappings',[SKU]) if (r.get('offer') or {}).get('offerId')==SKU],'offer',next((r['offer'] for r in read_rows(biz,'offer-mappings',[SKU]) if r.get('offer',{}).get('offerId')==SKU),{})) if False else None
        rows=[r for r in read_rows(biz,'offer-mappings',[SKU]) if (r.get('offer') or {}).get('offerId')==SKU]
        require(len(rows)==1,'Market exact offer not unique')
        return rows[0]['offer'],one(read_rows(biz,'offer-cards',[SKU]),'offerId',SKU)
    before,card=read();old=urls_only(list(before.get('pictures') or []));desired=prepend(old,url)
    entry=J['channels'].get('market',{})
    if entry.get('before_gallery'):
        old=entry['before_gallery'];desired=prepend(old,url)
        require(before['pictures'] in (old,desired),'Market gallery drift since intent')
    if before['pictures']!=desired:
        payload=cm.market_offer_payload(before,card,pictures=desired)
        journal('market',status='intent',business_id=biz,market_sku=card.get('mapping',{}).get('marketSku'),before_gallery=old,desired_gallery=desired)
        save();pipeline.current_main()
        code,res=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':[payload]})
        require(code==200 and res.get('status')=='OK' and not any(r.get('errors') for r in res.get('results',[])),'Market media request rejected: '+str(code)+' '+str(res)[:250])
        journal('market',status='submitted');save()
    after,acard=read()
    for _ in range(12):
        if after.get('pictures')==desired:break
        time.sleep(5);after,acard=read()
    protected=('name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions','marketCategoryId')
    cm.unchanged_fields(before,after,protected)
    require(grouped(card.get('parameterValues',[]))==grouped(acard.get('parameterValues',[])),'Market search attributes changed')
    ok=after.get('pictures')==desired
    journal('market',status='verified' if ok else 'submitted_pending_readback',market_sku=acard.get('mapping',{}).get('marketSku'),before_gallery=old,desired_gallery=desired,actual_gallery=after.get('pictures'),protected_unchanged=True)
    save()
    if ok:persist_sources('market',desired)

def frame(path,second):
    data=subprocess.check_output(['ffmpeg','-v','error','-ss',str(second),'-i',str(path),'-frames:v','1','-f','image2pipe','-vcodec','png','-'],timeout=40)
    require(bool(data),'Video frame unavailable')
    return digest_image(data)

def video_source(live):
    if not live:return None
    require(isinstance(live,str) and live.startswith('https://'),'Unexpected WB video schema')
    path=ROOT/'photos/0am325025H/0am325025H_video.mp4'
    cache=json.loads(CACHE.read_text())
    original=cache.get(path.name,{}).get('url')
    require(original and sha(download(original))==sha(path.read_bytes()),'Original WB video source not verified')
    samples=[]
    for sec in (1,3,5):
        samples.append(image_difference(frame(path,sec),frame(live,sec)))
    require(max(samples)<12,'Original video differs from existing WB video')
    journal('wb_video',status='verified',source=original,existing_video=live,sample_differences=samples)
    save()
    return original

def wb(url):
    import wb_client
    def read():return pipeline.read_wb_exact(SKU,str(WB_ID))
    def gallery(card):return urls_only([x['big'] for x in card.get('photos') or []])
    def protection(card):
        return {k:card.get(k) for k in ('vendorCode','nmID','title','description','brand','subjectID','dimensions','sizes','characteristics','video')}
    before=read();old=gallery(before);reference=digest_image(SOURCE.read_bytes())
    entry=J['channels'].get('wb',{})
    previous=entry.get('desired_gallery')
    if previous:
        desired=previous
        refs=[digest_image(download(u)) for u in desired]
        if len(old)==len(desired) and all(match(u,im) for u,im in zip(old,refs)):
            require(bool(before.get('video'))==bool(entry.get('video_source')),'WB video missing on retry')
            journal('wb',status='verified',actual_gallery=old,video_present=bool(before.get('video')),nmID=WB_ID)
            save();persist_sources('wb',desired,entry.get('video_source'));return
        require(old==entry.get('before_gallery'),'WB previous submission not resolved; do not repeat')
        raise ValueError('Previous WB intent exists; readback not resolved; no blind resubmission')
    require(len(old)<30,'WB photo limit would be exceeded')
    require(not any(match(u,reference) for u in old[1:]),'New photo already present in secondary gallery; review')
    if match(old[0],reference):
        raise ValueError('New cover already visible without recorded prior gallery; cannot prove old cover retained')
    # The old WB CDN slot URLs are mutable. Snapshot their actual images at
    # content-addressed release URLs before replacing anything; use original
    # source files where a complete exact match is available.
    old_refs=[];stable=[]
    with tempfile.TemporaryDirectory() as tmp:
        for i,u in enumerate(old,1):
            im=digest_image(download(u));old_refs.append(im)
            fn=Path(tmp)/f'wb-prior-{i:02d}.png';im.save(fn,'PNG')
            h=sha(fn.read_bytes())
            proof=verified_host(fn,h,list(im.size),f'0am325025H-wb-prior-{i:02d}-{h[:12]}.png')
            stable.append(proof['url'])
    video=video_source(before.get('video'))
    desired=[url]+stable
    refs=[reference]+old_refs
    journal('wb',status='intent',nmID=WB_ID,before_gallery=old,desired_gallery=desired,video_source=video,protected=protection(before))
    save(CACHE);pipeline.current_main()
    response=wb_client.update_media(WB_ID,desired+([video] if video else []),retries=1)
    require(not response.get('error'),'WB media save rejected: '+str(response)[:250])
    journal('wb',status='submitted');save()
    after=read()
    ok=False
    for _ in range(15):
        actual=gallery(after)
        if len(actual)==len(refs) and all(match(u,im) for u,im in zip(actual,refs)):
            ok=True;break
        time.sleep(5);after=read()
    bp,ap=protection(before),protection(after)
    # A video may receive a new HLS URL on re-ingest: prove content, not URL.
    bv,av=bp.pop('video',None),ap.pop('video',None)
    require(bp==ap,'WB unrelated card fields changed')
    require(bool(bv)==bool(av),'WB video presence changed')
    if bv and av!=bv:video_source(av)
    journal('wb',status='verified' if ok else 'submitted_pending_readback',nmID=WB_ID,actual_gallery=gallery(after),protected_unchanged=True,video_present=bool(av),video_source=video)
    save()
    if ok:persist_sources('wb',desired,video)

def tilda(url):
    from scripts.leoparts_direct_operation_nov21 import site_records
    rows=[r for r in site_records().values() if r.get('sku')==SKU]
    require(len(rows)==1 and str(rows[0].get('uid'))==TILDA_UID,'Tilda exact product identity missing')
    r=rows[0];old=r.get('gallery')
    if isinstance(old,str):old=json.loads(old)
    require(isinstance(old,list) and old,'Tilda live gallery absent')
    actual=[]
    for item in old:
        u=item if isinstance(item,str) else next((item.get(k) for k in ('img','url','original','src','large') if isinstance(item.get(k),str) and item[k].startswith('https://')),None)
        actual.append(u)
    actual=urls_only(actual);desired=prepend(actual,url)
    # Prior CommerceML evidence shows appending, not reordering. Do not run a
    # destructive/append-only import. Read existing gallery and persist the exact
    # required editor operation as a separate blocker, not a success.
    journal('tilda',status='verified' if actual==desired else 'blocked_no_supported_gallery_reorder',uid=TILDA_UID,before_gallery=actual,desired_gallery=desired,actual_gallery=actual,site_writes=0)
    save()

def patch_feed(raw,url):
    import xml.etree.ElementTree as ET
    text=raw.decode('utf-8')
    m=list(re.finditer(r'<offer id="0am325025h"[^>]*>.*?</offer>',text,re.S))
    require(len(m)==1,'Exact Direct offer block missing')
    old=m[0].group()
    new,n=re.subn(r'<picture>[^<]+</picture>',lambda _: '<picture>'+url+'</picture>',old)
    require(n==1,'Exact single Direct picture required')
    out=(text[:m[0].start()]+new+text[m[0].end():]).encode()
    a,b=ET.fromstring(raw),ET.fromstring(out)
    for root in (a,b):
        offers=root.findall('./shop/offers/offer')
        require(len(offers)==12 and len({o.get('id') for o in offers})==12,'Feed inventory changed')
        target=[o for o in offers if o.get('id')=='0am325025h'][0]
        target.find('picture').text='PRESERVED'
    require(ET.tostring(a)==ET.tostring(b),'Unrelated feed field changed')
    return out

def direct(url):
    from scripts import sync_dq200_live_feed as s
    a=s.a
    require(a.FID==FEED_ID and a.CID==716316525,'Direct identity mismatch')
    c,g,ads,k=s.read_ads()
    f=a.get_feed();old=s.source_url(f)
    require(f.get('Status')=='DONE' and f.get('NumberOfItems')==12,'Direct currently processing; defer')
    path=ROOT/'feeds/dq200_candidate.yml'
    raw=path.read_bytes();new=patch_feed(raw,url)
    require(s.public_verified(old,raw) or (J['channels'].get('direct',{}).get('desired_sha256')==sha(raw)),'Current published feed differs from source; preserve')
    journal('direct',status='intent',feed_id=FEED_ID,source_before_sha256=sha(raw),desired_sha256=sha(new),previous_url=old)
    save();pipeline.current_main()
    builder=ROOT/'scripts/build_dq200_feed.py'
    source=builder.read_text()
    original='photos/0am325025H/0am325025H-1.png'
    bridge=str(SOURCE.relative_to(ROOT))
    require(source.count(original)==1 or source.count(bridge)==1,'Feed builder exact source mismatch')
    if original in source:source=source.replace(original,bridge)
    anchor='        report.append({"sku": sku, "catalog_stock":'
    require(source.count(anchor)==1,'Feed builder insertion point mismatch')
    if '# exact approved DQ200 cover 20261126' not in source:
        source=source.replace(anchor,'        if sku == "0am325025h":\n            photo_url = '+repr(url)+'  # exact approved DQ200 cover 20261126\n'+anchor)
    compile(source,str(builder),'exec')
    builder.write_text(source)
    path.write_bytes(new)
    save(builder,path)
    blob=pipeline.git('rev-parse','HEAD:feeds/dq200_candidate.yml')
    desired=a.FEED_URL+'?copy_rev=auto_'+blob
    require(s.public_verified(desired,new),'Exact public feed not verified')
    pipeline.current_main()
    now=a.get_feed();require(s.source_url(now)==old and now.get('Status')=='DONE','Concurrent feed change')
    if old!=desired:
        journal('direct',status='feed_refresh_intent',url=desired,public_sha256=sha(new))
        save();pipeline.current_main()
        require(s.feed_update(desired),'Direct rejected exact feed URL')
    af=a.get_feed()
    for _ in range(12):
        if s.source_url(af)==desired and af.get('Status')=='DONE':break
        time.sleep(5);af=a.get_feed()
    cc,gg,aa,kk=s.read_ads()
    require(a.stable(c)==a.stable(cc) and a.stable(g)==a.stable(gg) and k==kk,'Campaign, groups or keywords changed')
    require(all(a.same_ad(ads[i],aa[i]) or (i in a.ADGROUP and a.same_ad(dict(ads[i],State=aa[i]['State']),aa[i])) for i in ads),'Ad settings changed')
    ready=s.source_url(af)==desired and af.get('Status')=='DONE' and af.get('NumberOfItems')==12
    journal('direct',status='verified' if ready else 'submitted_pending_processing',feed_id=FEED_ID,url=desired,public_sha256=sha(new),feed_after=af,protected_unchanged=True,public_picture=url)
    save()

def avito_preflight():
    import avito_client as av
    draft=json.loads(AVITO_DRAFT.read_text())
    require(draft['price_rub']==29500,'New Avito price mismatch')
    active=av.list_all_items(statuses=['active'])
    matches=[x for x in active if str(x.get('id'))==str(AVITO_OLD)]
    require(len(matches)==1,'Existing protected Avito listing not confirmed')
    old=matches[0]
    profile=av._request('GET','/autoload/v2/profile',retries=1)
    require(profile.get('autoload_enabled') is True,'Avito autoload not enabled')
    # Owner approves a separate card but explicitly forbids bypassing duplicate
    # controls. A different price/photos alone do not establish a distinct item.
    # The supplied screenshots corroborate review substance, but the infographic
    # is editorially shortened and the original product identity is not visible.
    journal('avito',status='blocked_duplicate_eligibility_unverified',existing_avito_id=AVITO_OLD,existing_status=old.get('status'),existing_price=old.get('price'),requested_price=29500,autoload_enabled=True,feed_count=len(profile.get('feeds_data') or []),new_avito_id=None,review_note='Six review excerpts and 5/5 are supported in substance by the supplied screenshots, not exact verbatim quotations; same SKU/source attribution and allowed duplicate exception not independently established.',seller_writes=0)
    save()

def main():
    global J
    require(os.environ.get('GITHUB_REF')=='refs/heads/main','Main only')
    pipeline.current_main()
    J=json.loads(OUT.read_text()) if OUT.exists() else {'sku':SKU,'started_utc':pipeline.stamp(),'channels':{},'price_writes':0,'stock_writes':0,'description_writes':0,'existing_avito_writes':0}
    require(J['sku']==SKU,'Journal scope mismatch')
    url=host_cover()
    for name,fn in [('ozon',lambda:ozon(url)),('market',lambda:market(url)),('wb',lambda:wb(url)),('direct',lambda:direct(url)),('tilda',lambda:tilda(url)),('avito_media',host_avito),('avito',avito_preflight)]:
        guard(name,fn)
    J['finished_utc']=pipeline.stamp();save()
    return 0

if __name__=='__main__':
    if '--self-test' in sys.argv:
        assert prepend(['https://x/old','https://x/second'],'https://x/new')==['https://x/new','https://x/old','https://x/second']
        assert prepend(['https://x/new','https://x/old'],'https://x/new')==['https://x/new','https://x/old']
        test=ROOT/'feeds/dq200_candidate.yml'
        raw=test.read_bytes();assert patch_feed(raw,'https://x/new')!=raw
        assert patch_feed(patch_feed(raw,'https://x/new'),'https://x/new')==patch_feed(raw,'https://x/new')
        print('PASS: old cover becomes second; retry idempotent; feed other fields unchanged')
    else:sys.exit(main())
