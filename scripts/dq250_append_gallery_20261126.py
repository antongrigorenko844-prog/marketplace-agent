"""Append three exact owner-selected photos to existing 02E927770AL only.
No new cards, no cover replacement, no commercial-field or ad-setting changes.
Journal before each seller write; resume by readback, never blind replay.
"""
from __future__ import annotations
import copy, io, json, os, re, sys, tempfile, time, zipfile
from pathlib import Path
from posixpath import normpath,join
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from urllib.parse import urlsplit
import requests
from PIL import Image, ImageOps, ImageChops, ImageStat
from openpyxl import load_workbook
from openpyxl.utils import get_column_letter
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path[:0]=[str(ROOT),str(ROOT/'.github/scripts')]
import content_pipeline as p
import content_manifest as cm
from scripts import dq200_cover_exact_20261126 as media
from scripts import avito_dq200_submit_20261126 as avbase
from scripts.apply_dq200_prices_20261120 import gh, get_avito_asset,rename_asset,validate_workbook_delta
SKU='02E927770AL'; WB=1949822305; AV=8276437673; BIZ=194752944
REQ=ROOT/'drafts/dq250_02E927770AL_append_gallery_20261126.json'
OUT=ROOT/'data/dq250_append_gallery_20261126_result.json'
MAN=ROOT/'data/content_manifest.json'; CACHE=ROOT/'data/photo_upload_cache.json'
AF=ROOT/'data/avito_feed.xlsx'
EXPECTED=['10f8316558ada720f6514af53380ee28d426b06004999e67dd8de98187c0b54b','90b1302a8623bcc97fd98551375cb91b2ce7cfbf6fe08f3e292364c2e9082924','48642f97e5cf463becea20a6f8137b25324910d1161d18cb451c794599ea9493']
J={}; NEW=[]; IMAGES=[]; PATHS=[]
def require(ok,msg):
    if not ok:raise ValueError(msg)
def sha(b):return media.sha(b)
def download(u):return media.download(u)
def save(*paths):
    J['updated_utc']=p.stamp();p.write_json(OUT,J);p.checkpoint([OUT,*paths],live=True)
def state(ch,status,**kw):
    J['channels'][ch]=dict(J['channels'].get(ch,{}),status=status,**kw)
    print(ch,status,json.dumps({k:v for k,v in kw.items() if k not in ('protected','protected_card','old_raw')},ensure_ascii=False,default=str)[:1800],flush=True)
def journal(ch,status,**kw):state(ch,status,**kw);save()
def entry(ch):return J['channels'].get(ch,{})
def guard(ch,fn):
    try:fn()
    except Exception as e:
        journal(ch,'blocked_or_unverified',error=type(e).__name__+': '+str(e)[:450])
def exact_one(rows,key,value):
    hits=[r for r in rows if str(r.get(key))==str(value)]
    require(len(hits)==1,'Exact identity not unique: '+str(value));return hits[0]
def valid_urls(values):
    require(isinstance(values,list) and values and all(isinstance(x,str) and x.startswith('https://') for x in values),'Existing gallery missing or invalid')
    require(len(set(map(cm.normalize_url,values)))==len(values),'Existing duplicate photo URLs; preserve manual gallery')
    return values
def image(raw):return media.digest_image(raw)
def similarity(a,b):
    # Allow platform resize/white letterboxing; verify pixels rather than CDN slot URLs.
    def compare(x,y):
        x=x.copy();x.thumbnail((240,320));return sum(ImageStat.Stat(ImageChops.difference(x,y.resize(x.size,Image.Resampling.LANCZOS))).mean)/3
    if abs(a.width/a.height-b.width/b.height)<.055:return compare(a,b)
    scores=[]
    for x,y in ((a,b),(b,a)):
        size=x.size
        for color in ('white',(245,245,245),(238,238,238)):
            scores.append(compare(x,ImageOps.pad(y,size,method=Image.Resampling.LANCZOS,color=color)))
        scores.append(compare(x,ImageOps.fit(y,size,method=Image.Resampling.LANCZOS)))
    return min(scores)
def same(a,b):return similarity(a,b)<9
def read_image(u):return image(download(u))
def matches(actual,desired):
    if len(actual)!=len(desired):return False
    try:return all(cm.normalize_url(a)==cm.normalize_url(b) or same(read_image(a),read_image(b)) for a,b in zip(actual,desired))
    except Exception:return False
def missing(old):
    refs=[read_image(u) for u in old]
    return [u for u,im in zip(NEW,IMAGES) if not any(same(im,a) for a in refs)]
def settle(read,check,attempts=6):
    v=read()
    for _ in range(attempts-1):
        if check(v):break
        time.sleep(5);v=read()
    return v
def persist(ch,urls,appended=None):
    """Pin exact complete verified gallery to prevent legacy refresh undoing it."""
    data=json.loads(MAN.read_text());old=copy.deepcopy(data)
    require(SKU in data['products'],'Manifest exact SKU absent')
    data['products'][SKU].setdefault('sources',{})[ch]=list(urls)
    old['products'][SKU].setdefault('sources',{})[ch]=list(urls)
    require(data==old,'Unrelated manifest change')
    p.current_main();p.write_json(MAN,data);cm.load()
    paths=[MAN]
    if ch in ('ozon','wb'):
        filename,sheet,col={'ozon':('ozon_catalog.xlsx','Ozon',6),'wb':('wb_catalog.xlsx','WB',4)}[ch]
        path=ROOT/'data'/filename;book=load_workbook(path);ws=book[sheet]
        hits=[r for r in range(2,ws.max_row+1) if str(ws.cell(r,1).value or '').strip()==SKU]
        require(len(hits)==1,'Exact catalog row absent')
        oldvalue=ws.cell(hits[0],col).value
        require(isinstance(oldvalue,str) and oldvalue.startswith('https://'),'Old catalog photos missing')
        values=[u.strip() for u in oldvalue.split('|') if u.strip()]
        for u in appended if appended is not None else NEW:
            if not any(cm.normalize_url(u)==cm.normalize_url(v) for v in values):values.append(u)
        ws.cell(hits[0],col).value='|'.join(values);book.save(path);book.close();paths.append(path)
    save(*paths)
def host():
    global NEW,IMAGES,PATHS
    req=json.loads(REQ.read_text())
    require(req['owner_approved'] is True and req['sku']==SKU and req['operation']=='append_three_original_images_only','Request identity mismatch')
    require([x['sha256'] for x in req['media']]==EXPECTED,'Original digest mismatch')
    require([x['position'] for x in req['media']]==[1,2,3],'Original order mismatch')
    proofs=[]
    # Verify all originals before hosting any.
    for x in req['media']:
        path=ROOT/x['repository_path']
        raw=media.exact_bytes(path,x['sha256'],x['dimensions'])
        require(len(raw)==x['bytes'],'Original size mismatch')
        PATHS.append(path);IMAGES.append(image(raw))
    for x,path in zip(req['media'],PATHS):
        proofs.append(media.verified_host(path,x['sha256'],x['dimensions'],x['asset_name']))
    NEW=[x['url'] for x in proofs]
    state('source','verified',originals=proofs);save(CACHE)
def ozon():
    import ozon_client as oz
    def read():return exact_one(oz.get_product_names([SKU]),'offer_id',SKU)
    def gallery(row):
        cover=row.get('primary_image');others=row.get('images') or []
        require(isinstance(cover,str) and cover and isinstance(others,list),'Ozon gallery missing')
        return valid_urls([cover]+[u for u in others if cm.normalize_url(u)!=cm.normalize_url(cover)])
    before=read();old=gallery(before);e=entry('ozon')
    if e.get('desired_gallery'):
        desired=e['desired_gallery'];protected=e['protected'];prior=e['before_gallery'];added=e['appended']
    else:
        prior=old;added=missing(old);desired=prior+added;protected=media.ozon_protection(before)
        require(len(desired)<=15,'Ozon photo limit guard')
        if added:
            journal('ozon','intent',product_id=before.get('id') or before.get('product_id'),before_gallery=prior,desired_gallery=desired,appended=added,protected=protected)
            p.current_main()
            res=oz._post('/v2/product/pictures/import',{'items':[{'offer_id':SKU,'primary_image':prior[0],'images':desired[1:],'color_image':before.get('color_image') or ''}]},retries=1)
            task=res.get('task_id') or (res.get('result') or {}).get('task_id')
            journal('ozon','submitted',task_id=task)
    after=settle(read,lambda r:matches(gallery(r),desired))
    actual=gallery(after);ok=matches(actual,desired);diff=media.ozon_protection_diff(protected,media.ozon_protection(after))
    journal('ozon','verified' if ok and not diff else 'gallery_verified_protection_diff' if ok else 'submitted_pending_readback',product_id=after.get('id') or after.get('product_id'),before_gallery=prior,desired_gallery=desired,appended=added,actual_gallery=actual,protected_unchanged=not diff,protection_differences=diff,main_photo_preserved=bool(ok),photos_before=len(prior),photos_after=len(actual),error=None)
    if ok and not diff:persist('ozon',desired,added)
def market():
    import yandex_market_client as ym
    from market_content import read_rows,grouped
    biz=ym.get_business_id();require(int(biz)==BIZ,'Market business mismatch')
    def read():
        r=[x for x in read_rows(biz,'offer-mappings',[SKU]) if (x.get('offer') or {}).get('offerId')==SKU]
        require(len(r)==1,'Market exact offer missing');return r[0]['offer'],exact_one(read_rows(biz,'offer-cards',[SKU]),'offerId',SKU)
    before,card=read();old=valid_urls(list(before.get('pictures') or []));e=entry('market')
    fields=('name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions','marketCategoryId')
    if e.get('desired_gallery'):
        desired=e['desired_gallery'];prior=e['before_gallery'];added=e['appended'];protected=e['protected'];attrs=e['protected_card']
    else:
        prior=old;added=missing(old);desired=prior+added;protected={k:before.get(k) for k in fields};attrs=card.get('parameterValues',[])
        require(len(desired)<=20,'Market gallery limit guard')
        if added:
            payload=cm.market_offer_payload(before,card,pictures=desired)
            journal('market','intent',market_sku=card.get('mapping',{}).get('marketSku'),before_gallery=prior,desired_gallery=desired,appended=added,protected=protected,protected_card=attrs)
            p.current_main()
            code,res=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':[payload]})
            require(code==200 and res.get('status')=='OK' and not any(x.get('errors') for x in res.get('results',[])),'Market media request rejected: '+str(code))
            journal('market','submitted')
    after,acard=settle(read,lambda pair:pair[0].get('pictures')==desired)
    diffs=[k for k,v in protected.items() if after.get(k)!=v]
    if grouped(attrs)!=grouped(acard.get('parameterValues',[])):diffs.append('parameterValues')
    actual=after.get('pictures');ok=actual==desired
    journal('market','verified' if ok and not diffs else 'gallery_verified_protection_diff' if ok else 'submitted_pending_readback',market_sku=acard.get('mapping',{}).get('marketSku'),before_gallery=prior,desired_gallery=desired,appended=added,actual_gallery=actual,protected_unchanged=not diffs,protection_differences=diffs,main_photo_preserved=ok,photos_before=len(prior),photos_after=len(actual or []),error=None)
    if ok and not diffs:persist('market',desired,added)
def wb():
    import wb_client
    def read():return p.read_wb_exact(SKU,str(WB))
    def gallery(c):return valid_urls([x['big'] for x in c.get('photos') or []])
    def protect(c):return {k:c.get(k) for k in ('vendorCode','nmID','title','description','brand','subjectID','dimensions','sizes','characteristics','video')}
    before=read();old=gallery(before);e=entry('wb')
    if e.get('desired_gallery'):
        desired=e['desired_gallery'];prior=e['before_gallery'];added=e['appended'];protected=e['protected'];video=e.get('video_source')
    else:
        prior=old;added=missing(old);protected=protect(before)
        require(not before.get('video') or not added,'Existing WB video source needs exact independent verification; no media replacement')
        video=None
        if added:
            # WB CDN slots can be overwritten. Pin each old image's bytes first.
            stable=[]
            cache=json.loads(CACHE.read_text())
            candidates=[v['url'] for k,v in cache.items() if k.startswith(SKU+'_') and k.lower().endswith(('.jpg','.jpeg','.png','.webp')) and v.get('url')]
            for i,u in enumerate(old,1):
                raw=download(u);ref=image(raw);chosen=None
                for candidate in candidates:
                    try:
                        if same(ref,read_image(candidate)):chosen=candidate;break
                    except Exception:pass
                if not chosen:
                    with tempfile.TemporaryDirectory() as tmp:
                        with Image.open(io.BytesIO(raw)) as im:fmt=im.format
                        ext={'JPEG':'.jpg','PNG':'.png','WEBP':'.webp'}.get(fmt)
                        require(ext,'Unexpected existing WB image type')
                        f=Path(tmp)/('prior'+ext);f.write_bytes(raw);h=sha(raw)
                        with Image.open(f) as im:dims=list(im.size)
                        chosen=media.verified_host(f,h,dims,f'{SKU}-wb-prior-{i}-{h[:12]}{ext}')['url']
                stable.append(chosen)
            require(len(set(map(cm.normalize_url,stable)))==len(old),'WB old source mapping ambiguous')
            desired=stable+added;require(len(desired)<=30,'WB photo limit')
            state('wb','intent',nmID=WB,before_gallery=prior,desired_gallery=desired,appended=added,protected=protected,video_source=video);save(CACHE)
            p.current_main()
            res=wb_client.update_media(WB,desired,retries=1)
            require(not res.get('error'),'WB media save rejected')
            journal('wb','submitted')
        else:desired=prior
    after=settle(read,lambda r:matches(gallery(r),desired),attempts=9)
    actual=gallery(after);ok=matches(actual,desired);pa=protect(after)
    diffs=[k for k,v in protected.items() if media.canon_attrs(v)!=media.canon_attrs(pa.get(k))]
    journal('wb','verified' if ok and not diffs else 'gallery_verified_protection_diff' if ok else 'submitted_pending_readback',nmID=WB,before_gallery=prior,desired_gallery=desired,appended=added,actual_gallery=actual,main_photo_preserved=ok,protected_unchanged=not diffs,protection_differences=diffs,video_present=bool(after.get('video')),photos_before=len(prior),photos_after=len(actual),error=None)
    if ok and not diffs:persist('wb',desired,added)
def avito_row(raw):
    import avito_feed as af
    book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
    try:
        ws=book[af.SHEET_ADS]
        hits=[(i,r) for i,r in enumerate(ws.iter_rows(min_row=af.FIRST_DATA_ROW,values_only=True),af.FIRST_DATA_ROW) if str(r[af.COL_ID-1] or '')==SKU or str(r[af.COL_AVITO_ID-1] or '')==str(AV)]
        require(len(hits)==1 and str(hits[0][1][af.COL_ID-1])==SKU,'Avito exact row absent')
        require(str(hits[0][1][af.COL_AVITO_ID-1] or '') in ('',str(AV)),'Avito ID conflict')
        return hits[0]
    finally:book.close()
def patch_avito(raw,n,desired):
    import avito_feed as af
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        root=ET.fromstring(z.read('xl/workbook.xml'));ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        sheets=[x for x in root.findall('m:sheets/m:sheet',ns) if x.get('name')==af.SHEET_ADS];require(len(sheets)==1,'Exact worksheet missing')
        rid=sheets[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'));hits=[r for r in rels if r.get('Id')==rid];require(len(hits)==1,'Worksheet relationship missing')
        target=hits[0].get('Target');sheet=target.lstrip('/') if target.startswith('/') else normpath(join('xl',target));require(sheet.startswith('xl/worksheets/') and sheet in z.namelist(),'Worksheet path invalid')
    cell=get_column_letter(af.COL_IMAGE_URLS)+str(n);buf=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src,zipfile.ZipFile(buf,'w') as dst:
        for item in src.infolist():
            b=src.read(item.filename)
            if item.filename==sheet:
                txt=b.decode();hits=list(re.finditer(r'<c\b[^>]*\br="'+cell+r'"[^>]*>.*?</c>',txt,flags=re.S))
                require(len(hits)==1,'Target media cell not unique')
                m=hits[0];prior=m.group();require('inlineStr' in prior,'Unexpected media cell format')
                updated,k=re.subn(r'(<t(?:\s[^>]*)?>).*?(</t>)',lambda x:x.group(1)+escape(desired)+x.group(2),prior,flags=re.S);require(k==1,'Media text not unique')
                b=(txt[:m.start()]+updated+txt[m.end():]).encode()
            dst.writestr(item,b)
    new=buf.getvalue();validate_workbook_delta(raw,new,[cell],sheet)
    i,a=avito_row(raw);j,b=avito_row(new);require(i==j==n and b[af.COL_IMAGE_URLS-1]==desired,'Avito media cell readback mismatch')
    a=list(a);a[af.COL_IMAGE_URLS-1]=desired;require(tuple(a)==b,'Unrelated Avito row changed')
    return new
def avito():
    import avito_feed as af
    import avito_client as av
    from scripts.avito_dq200_exchange_text_20261126 import public
    def ad():return exact_one(av.list_all_items(statuses=['active']),'id',AV)
    before=ad();e=entry('avito')
    profile=avbase.profile()
    require(avbase.EXISTING[0] in [x['feed_url'] for x in profile['feeds_data']],'Registered primary Avito source missing')
    binding=av._request('GET','/autoload/v2/items/avito_ids',params={'query':SKU},retries=1)
    mapped=[x for x in binding.get('items',[]) if x.get('ad_id')==SKU and str(x.get('avito_id'))==str(AV)]
    require(len(mapped)==1,'Avito exact feed binding not verified')
    release,asset,raw=get_avito_asset()
    n,row=avito_row(raw);require(float(row[af.COL_PRICE-1])==float(before['price']),'Feed/live price differs; no media publication')
    old=valid_urls([u.strip() for u in str(row[af.COL_IMAGE_URLS-1]).splitlines() if u.strip()])
    if e.get('desired_gallery'):
        desired=e['desired_gallery'];prior=e['before_gallery'];added=e['appended']
        require(old==desired,'Prior Avito write not confirmed; do not replay blindly')
        require(AF.read_bytes()==raw,'Repo feed and stable feed differ')
    else:
        require(AF.read_bytes()==raw,'Published primary Avito feed differs from repository; refuse overwrite')
        prior=old;added=missing(old);desired=prior+added
        require(len(desired)<=af.AVITO_MAX_PHOTOS,'Avito photo limit exceeded')
        if added:
            new=patch_avito(raw,n,'\n'.join(desired));h=sha(new);name='avito_feed.dq250_gallery_'+h[:12]+'.xlsx'
            stage=[a for a in release['assets'] if a['name']==name]
            require(len(stage)<=1,'Duplicate staging asset')
            stage=stage[0] if stage else gh('POST',release['upload_url'].split('{',1)[0],params={'name':name},data=new,headers={'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}).json()
            require(public(stage['browser_download_url'],new),'Staged feed bytes not verified')
            journal('avito','intent',avito_id=AV,before_gallery=prior,desired_gallery=desired,appended=added,old_sha256=sha(raw),new_sha256=h,old_asset_id=asset['id'],staged_asset_id=stage['id'],only_media_cell_changed=True)
            p.current_main();_,current,currentraw=get_avito_asset();require(current['id']==asset['id'] and currentraw==raw,'Concurrent main feed change')
            rename_asset(asset['id'],'avito_feed.before_dq250_gallery_'+str(asset['id'])+'.xlsx')
            try:rename_asset(stage['id'],'avito_feed.xlsx')
            except Exception:
                rename_asset(asset['id'],'avito_feed.xlsx');raise
            require(public(avbase.EXISTING[0],new),'Stable updated Avito feed not verified')
            AF.write_bytes(new)
            state('avito','feed_updated_pending_readback',public_feed_verified=True);save(AF)
    after=ad();require({k:before.get(k) for k in ('id','price','title','status')}=={k:after.get(k) for k in ('id','price','title','status')},'Avito protected listing fields changed')
    # Exact public feed/gallery persisted. Ordinary scheduled autoload consumes it;
    # no profile rewrite, global manual import or new ad.
    persist('avito',desired,added)
    reports=[];received=False;live_ok=False
    try:
        account=av._request('GET','/core/v1/accounts/self',retries=1)
        aid=str(account.get('id') or '')
        detail=av._request('GET',f'/core/v1/accounts/{aid}/items/{AV}/',retries=1) if aid.isdigit() else {}
        urls=detail.get('images') or detail.get('photos')
        if isinstance(urls,list):
            v=[x if isinstance(x,str) else x.get('url') or x.get('large') or x.get('big') for x in urls]
            if all(isinstance(x,str) and x.startswith('https://') for x in v):live_ok=matches(v,desired)
    except Exception:pass
    for kind in ('current','last_successful'):
        try:
            report=av._request('GET','/autoload/v4/uploads/'+kind,retries=1)
            record={'kind':kind,'upload_id':report.get('upload_id'),'status':report.get('status')}
            for f in report.get('feed_urls',[]):
                url=f.get('url','');u=urlsplit(url)
                if u.scheme!='https' or u.hostname!='api.avito.ru' or not u.path.startswith('/autoload/v2/feed/content/'):continue
                rr=requests.get(url,headers={'Authorization':av._headers()['Authorization']},timeout=20,allow_redirects=False)
                if not rr.ok:continue
                try:_,r=avito_row(rr.content)
                except Exception:continue
                record['received_gallery_matches']=str(r[af.COL_IMAGE_URLS-1]).splitlines()==desired
                received=received or record['received_gallery_matches']
            reports.append(record)
        except Exception as exc:reports.append({'kind':kind,'read_error':str(exc)[:150]})
    journal('avito','verified' if live_ok else 'avito_received_feed_live_gallery_unverified' if received else 'feed_updated_pending_avito_processing',avito_id=AV,url=after.get('url'),before_gallery=prior,desired_gallery=desired,appended=added,photos_before=len(prior),photos_in_feed=len(desired),main_photo_preserved_in_feed=True,protected_unchanged=True,public_feed_verified=True,live_gallery_verified=live_ok,reports=reports,error=None)
def tilda():
    import tilda_commerceml as tc
    from scripts.leoparts_direct_operation_nov21 import site_records
    from scripts.tilda_price_pipeline_v1 import create_price_only_xml
    def read():
        row=exact_one(list(site_records().values()),'sku',SKU)
        require(str(row.get('externalid') or row.get('external_id'))==SKU,'Tilda external identity differs; no create/update')
        return row
    def g(row):
        v=row.get('gallery');v=json.loads(v) if isinstance(v,str) else v
        require(isinstance(v,list) and v,'Tilda gallery missing');return v
    def urls(v):
        out=[]
        for x in v:
            u=x if isinstance(x,str) else next((x[k] for k in ('img','url','original','src','large') if isinstance(x.get(k),str) and x[k].startswith('https://')),None)
            require(isinstance(u,str) and u.startswith('https://'),'Tilda unknown gallery entry');out.append(u)
        return out
    fields=('uid','sku','externalid','external_id','title','descr','text','price','priceold','quantity','editions','options','parts','brand')
    before=read();oldraw=g(before);old=urls(oldraw);e=entry('tilda')
    if e.get('desired_gallery'):
        prior=e['before_gallery'];oldraw=e['old_raw'];desired=e['desired_gallery'];added=e['appended'];protected=e['protected']
    else:
        prior=old;added=missing(old);desired=prior+added;protected={k:before.get(k) for k in fields}
        require(len(desired)<=15,'Tilda gallery limit')
        if added:
            # Use proven changes-only CommerceML descriptors, remove ALL commercial
            # fields from the offers XML. Keep exact existing title and external ID.
            imp,off=create_price_only_xml({SKU:{'externalid':SKU,'title':before['title'],'new_price':0}})
            a,b=ET.fromstring(imp),ET.fromstring(off)
            prod=a.find('./Каталог/Товары/Товар');offer=b.find('./ПакетПредложений/Предложения/Предложение')
            require(prod is not None and offer is not None,'Tilda descriptor missing')
            prices=offer.find('Цены');require(prices is not None,'Expected price scaffold missing');offer.remove(prices)
            files=[]
            for u in added:
                i=NEW.index(u);fn=f'import_files/dq250_02e927770al_{EXPECTED[i][:16]}.jpeg';ET.SubElement(prod,'Картинка').text=fn;files.append((fn,PATHS[i].read_bytes()))
            xml=lambda x:ET.tostring(x,encoding='utf-8',xml_declaration=True).decode()
            imp,off=xml(a),xml(b)
            require('СодержитТолькоИзменения="true"' in imp and 'СодержитТолькоИзменения="true"' in off,'Full import prohibited')
            require(all('<'+k+'>' not in imp+off for k in ('Цены','ЦенаЗаЕдиницу','Количество','Описание','ПометкаУдаления','Остатки')),'Commercial payload prohibited')
            require(all(os.getenv(k) for k in ('TILDA_COMMERCEML_URL','TILDA_COMMERCEML_USER','TILDA_COMMERCEML_PASSWORD')),'Tilda connection unavailable')
            journal('tilda','intent',uid=before['uid'],before_gallery=prior,old_raw=oldraw,desired_gallery=desired,appended=added,protected=protected,price_stock_fields_sent=False)
            p.current_main()
            steps=tc.push(os.environ['TILDA_COMMERCEML_URL'],os.environ['TILDA_COMMERCEML_USER'],os.environ['TILDA_COMMERCEML_PASSWORD'],imp,off,timeout=80,image_files=files)
            journal('tilda','submitted',step_count=len(steps))
    after=settle(read,lambda r:matches(urls(g(r)),desired),attempts=6)
    actualraw=g(after);actual=urls(actualraw);ok=matches(actual,desired)
    old_meta_preserved=actualraw[:len(oldraw)]==oldraw
    diffs=[k for k,v in protected.items() if after.get(k)!=v]
    journal('tilda','verified' if ok and old_meta_preserved and not diffs else 'gallery_verified_protection_diff' if ok else 'submitted_pending_readback',uid=after.get('uid'),before_gallery=prior,desired_gallery=desired,appended=added,actual_gallery=actual,main_photo_preserved=ok,existing_gallery_metadata_preserved=old_meta_preserved,protected_unchanged=not diffs,protection_differences=diffs,photos_before=len(prior),photos_after=len(actual),error=None)
    if ok and not diffs and old_meta_preserved:persist('tilda',desired,added)
def main():
    global J
    require(os.environ.get('GITHUB_REF')=='refs/heads/main','Main only')
    p.current_main()
    J=json.loads(OUT.read_text()) if OUT.exists() else {'sku':SKU,'started_utc':p.stamp(),'channels':{},'price_writes':0,'stock_writes':0,'description_writes':0,'new_cards':0}
    require(J['sku']==SKU,'Journal scope differs')
    host()
    # All three source images verified before any catalog write.
    for ch,fn in [('ozon',ozon),('market',market),('wb',wb),('avito',avito),('tilda',tilda)]:
        if entry(ch).get('status')=='verified':continue
        guard(ch,fn)
    state('direct','not_applicable',reason='Exact 02E927770AL is not in the DQ200-only advertising feed')
    J['completion_status']='verified' if all(entry(c).get('status')=='verified' for c in ('ozon','market','wb','avito','tilda')) else 'partial_or_pending'
    J['finished_utc']=p.stamp();save()
if __name__=='__main__':main()
