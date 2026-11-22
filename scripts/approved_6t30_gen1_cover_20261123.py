"""User-approved 6t30 Gen1 cover, exact SKU only; media-only live update.
Preserve listings, prices, inventory, other media; keep evidence for each channel.
Run only inside marketplace-agent GitHub Actions, from explicit approved request.
"""
from __future__ import annotations
import base64, hashlib, io, json, os, sys, time, traceback
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image
import requests
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
SKU='6t30'
R='antongrigorenko844-prog/marketplace-agent'
DATA=ROOT/'data'
OUT=DATA/'6t30_cover_publish_20261123_result.json'
NEW_MP='6t30-gen1-approved-cover-20261123.jpg'
NEW_AV='6t30-gen1-approved-avito-20261123.jpg'
EXPECTED_MP='20cea0c4a021ffd95450112dd4ad41776074519a3be22d37a7d74058d5e33c85'
EXPECTED_AV='c63abdb50761d6542dd15b81e283447446a403fb4277ba1382a3eb7bb9c349f3'
LOG={'sku':SKU,'scope':'only approved Gen1 primary image, no price/stock/text writes','started':datetime.now(timezone.utc).isoformat(),'channels':{}}

def write(stage,details):
    LOG['channels'][stage]=details
    LOG['updated']=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(LOG,ensure_ascii=False,indent=2,default=str)+'\n')
    print('6T30 COVER:',stage,json.dumps({k:v for k,v in details.items() if k not in {'before','after','response'}},ensure_ascii=False),flush=True)

def one(rows,key):
    hits=[x for x in rows if str(x.get(key))==SKU]
    if len(hits)!=1:raise RuntimeError(f'Expected one {key}={SKU}, got {len(hits)}')
    return hits[0]

def decode(path,out,sha,dimensions):
    raw=base64.b64decode((DATA/path).read_text().strip(),validate=True)
    if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('Approved file sha256 mismatch')
    with Image.open(io.BytesIO(raw)) as img:
        img.verify()
    with Image.open(io.BytesIO(raw)) as img:
        if img.size!=dimensions or img.format!='JPEG':raise ValueError('Unexpected approved cover dimensions/format')
    out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(raw)
    return len(raw)

def gallery_current_manifest(url_mp,url_av):
    p=DATA/'content_manifest.json'
    j=json.loads(p.read_text()); row=j['products'][SKU]
    expected=row.get('ids') or {}
    if expected.get('wb')!=2254117475 or expected.get('avito')!=8425207775 or str(expected.get('tilda'))!='695009271403':
        raise ValueError('Owner master mapping changed')
    old={k:list(v) for k,v in row.get('sources',{}).items()}
    for ch,new in [('ozon',url_mp),('wb',url_mp),('market',url_mp),('avito',url_av),('tilda',url_mp)]:
        current=list(row['sources'][ch]);
        if len(current)<1:raise ValueError('Missing original source photos '+ch)
        row['sources'][ch]=[new]+[x for x in current[1:] if x!=new]
    row['note']='Обложка комплекта 7 соленоидов первого поколения: одобренный владельцем макет 23.11.2026, без упаковки и слова «Новые», с отметкой проверки на стенде.'
    p.write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n')
    return old

def run_ozon(main_url):
    import ozon_client as oz
    before=one(oz.get_product_names([SKU]),'offer_id')
    old=before['primary_image']; extras=[x for x in before.get('images',[]) if x!=old and x!=main_url]
    if not old:raise ValueError('Ozon missing original cover')
    if old==main_url: return {'status':'already_present','url':main_url}
    response=oz._post('/v2/product/pictures/import',{'items':[{'offer_id':SKU,'primary_image':main_url,'images':extras,'color_image':before.get('color_image')or''}]})
    task=response.get('task_id') or (response.get('result') or {}).get('task_id')
    task_result=None
    if task:
        for _ in range(18):
            time.sleep(5)
            task_result=oz.get_import_status(task)
            values=(task_result.get('result')or{}).get('items',[])
            if values and all(v.get('status') not in ('pending','processing','') for v in values):break
    after=one(oz.get_product_names([SKU]),'offer_id')
    for k in ('name','description_category_id','type_id','height','depth','width','weight'):
        if before.get(k)!=after.get(k):raise ValueError('Unrelated Ozon field changed: '+k)
    # Ozon media may be queued for moderation; report submitted vs confirmed separately.
    verified=(after.get('primary_image')==main_url)
    return {'status':'verified' if verified else 'submitted_pending_readback','url':main_url,'old_url':old,'task_id':task,'primary_live':after.get('primary_image'),'extras_preserved_request':len(extras),'request_accepted':bool(response),'same_commercial_fields':True,'no_price_or_stock_write':True}

def run_wb(local_jpg):
    import wb_client as wb
    before=one(wb.list_cards(),'vendorCode')
    if before.get('nmID')!=2254117475:raise ValueError('WB wrong catalog identity')
    photos=before.get('photos') or []
    if not photos:raise ValueError('WB no original gallery')
    with open(local_jpg,'rb') as f:raw=f.read()
    resp=requests.post('https://content-api.wildberries.ru/content/v3/media/file',headers={'Authorization':os.environ['WB_API_TOKEN'],'X-Nm-Id':str(before['nmID']),'X-Photo-Number':'1'},files={'uploadfile':('cover.jpg',raw,'image/jpeg')},timeout=110)
    try:body=resp.json()
    except Exception:body={'result':resp.text[:300]}
    if not resp.ok or body.get('error'):
        raise RuntimeError('WB rejected photo upload: HTTP '+str(resp.status_code)+' '+str(body)[:320])
    time.sleep(18)
    after=one(wb.list_cards(),'vendorCode')
    for k in ['title','description','brand','subjectID','dimensions','sizes','characteristics','video']:
        if before.get(k)!=after.get(k):raise ValueError('WB unrelated field changed: '+k)
    if len(after.get('photos')or[])!=len(photos):raise ValueError('WB gallery photo count changed')
    first_b=photos[0].get('big');first_a=after['photos'][0].get('big')
    return {'status':'verified' if first_a and first_a!=first_b else 'submitted_pending_readback','nmID':before['nmID'],'photo_count_before':len(photos),'photo_count_after':len(after['photos']),'old_primary':first_b,'observed_primary':first_a,'same_nonmedia_fields':True,'no_price_or_stock_write':True}

def run_market(main_url):
    import yandex_market_client as ym
    from market_content import read_rows,preserve_typed_parameters,grouped
    business=ym.get_business_id()
    before=one([r['offer'] for r in read_rows(business,'offer-mappings',[SKU])],'offerId')
    bcard=one(read_rows(business,'offer-cards',[SKU]),'offerId')
    pics=list(before.get('pictures') or [])
    if not pics:raise ValueError('Market original gallery absent')
    desired=[main_url]+[u for u in pics[1:] if u!=main_url]
    if pics!=desired:
        payload=preserve_typed_parameters(business,[{'offer':{'offerId':SKU,'pictures':desired}}])
        status,response=ym._request('POST',f'/v2/businesses/{business}/offer-mappings/update',json_body={'offerMappings':payload})
        if status!=200 or response.get('status')!='OK' or any(x.get('errors') for x in response.get('results',[])):
            raise RuntimeError('Market rejected photo change '+str(status)+' '+str(response)[:450])
    after=before
    for _ in range(16 if pics!=desired else 1):
        if pics!=desired:time.sleep(5)
        after=one([r['offer'] for r in read_rows(business,'offer-mappings',[SKU])],'offerId')
        if after.get('pictures')==desired:break
    acard=one(read_rows(business,'offer-cards',[SKU]),'offerId')
    for key in ('name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions'):
        if before.get(key)!=after.get(key):raise ValueError('Market unrelated field changed: '+key)
    if grouped(bcard.get('parameterValues',[]))!=grouped(acard.get('parameterValues',[])):
        raise ValueError('Market characteristics changed')
    return {'status':'verified' if after.get('pictures')==desired else 'submitted_pending_readback','first_url':main_url,'old_url':pics[0],'photo_count':len(desired),'same_nonmedia_fields':True,'no_price_or_stock_write':True}

def replace_cover_in_xlsx(path,sheet,key_col,photo_col,url):
    from openpyxl import load_workbook
    p=DATA/path
    w=load_workbook(p);s=w[sheet]
    hits=[i for i in range(2,s.max_row+1) if str(s.cell(i,key_col).value or '').strip()==SKU]
    if len(hits)!=1:raise ValueError('Expected one SKU in '+path+', got '+str(len(hits)))
    r=hits[0];old=str(s.cell(r,photo_col).value or '')
    others=[x for x in old.split('|') if x and x!=url]
    # Replace first ONLY; keep all old secondary photos, not old cover.
    original=old.split('|')[0]
    new='|'.join([url]+[x for x in others if x!=original])
    s.cell(r,photo_col).value=new
    w.save(p)
    return {'row':r,'old_first_url':original,'new_first_url':url,'retained_secondary':max(0,len(new.split('|'))-1)}

def update_avito_local(av_url):
    from openpyxl import load_workbook
    p=DATA/'avito_photos.xlsx';w=load_workbook(p);s=w.active
    ids=[]
    for i in range(2,s.max_row+1):
        if str(s.cell(i,1).value or '').strip()==SKU:ids.append(i)
    if len(ids)!=1:raise ValueError('Avito local photo map missing/duplicate SKU')
    r=ids[0]
    old=str(s.cell(r,2).value or '')
    sep='|' if '|' in old else '\n' if '\n' in old else '|'
    parts=[x.strip() for x in old.split(sep) if x.strip()]
    s.cell(r,2).value=sep.join([av_url]+parts[1:]);w.save(p)
    return {'row':r,'old_first_url':parts[0] if parts else None,'new_first_url':av_url,'photo_count':max(1,len(parts))}

def avito_feed_update(url):
    """Change only one existing feed cell, never rebuild full feed from stale prices."""
    from openpyxl import load_workbook
    import requests
    from hashlib import sha256
    stable=f'https://github.com/{R}/releases/download/product-photos/avito_feed.xlsx'
    resp=requests.get(stable,params={'check':str(time.time_ns())},timeout=70)
    if not resp.ok:raise RuntimeError('Cannot read current stable Avito feed (HTTP '+str(resp.status_code)+')')
    orig=resp.content
    w=load_workbook(io.BytesIO(orig));s=w['Объявления']
    # Autoload linked item can have a -av suffix; exact identity guard.
    rows=[r for r in range(5,s.max_row+1) if str(s.cell(r,4).value or '').strip() in (SKU,SKU+'-av')]
    if len(rows)!=1:raise ValueError('Avito stable feed does not contain exactly one 6t30 row: '+str(rows))
    row=rows[0]
    old=s.cell(row,19).value or ''
    parts=[x.strip() for x in str(old).splitlines() if x.strip()]
    if not parts:raise ValueError('Avito current images empty')
    if parts[0]==url:return {'status':'already_present','feed_url':stable}
    s.cell(row,19).value='\n'.join([url]+parts[1:])
    out=io.BytesIO();w.save(out);updated=out.getvalue()
    # Safe value-level verification: all cells except one must remain the same.
    old_book=load_workbook(io.BytesIO(orig),read_only=True,data_only=False)
    new_book=load_workbook(io.BytesIO(updated),read_only=True,data_only=False)
    for name in old_book.sheetnames:
        a,b=old_book[name],new_book[name]
        if (a.max_row,a.max_column)!=(b.max_row,b.max_column):raise ValueError('Avito workbook changed shape')
        for rr in range(1,a.max_row+1):
            for cc in range(1,a.max_column+1):
                if name=='Объявления' and rr==row and cc==19:continue
                if a.cell(rr,cc).value != b.cell(rr,cc).value:raise ValueError('Unexpected Avito field changed '+name+' '+str(rr)+':'+str(cc))
    (DATA/'avito_feed.xlsx').write_bytes(updated)
    gh_token=os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if not gh_token:raise ValueError('GH token for Avito release promotion not set')
    headers={'Authorization':'Bearer '+gh_token,'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
    api=f'https://api.github.com/repos/{R}'
    r=requests.get(api+'/releases/tags/product-photos',headers=headers,timeout=40)
    r.raise_for_status();release=r.json();a=[x for x in release['assets'] if x['name']=='avito_feed.xlsx']
    if len(a)!=1:raise ValueError('Expected unique live Avito feed asset')
    existing=a[0]
    backup=f'avito_feed.backup_6t30_20261123_{existing["id"]}.xlsx'
    rename=requests.patch(api+'/releases/assets/'+str(existing['id']),headers=headers,json={'name':backup},timeout=40)
    rename.raise_for_status()
    upload_base=release['upload_url'].split('{')[0]
    try:
        created=requests.post(upload_base,headers={**headers,'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},params={'name':'avito_feed.xlsx'},data=updated,timeout=90)
        created.raise_for_status()
        for delay in [0,4,8,15,22]:
            if delay:time.sleep(delay)
            rr=requests.get(stable,params={'v':sha256(updated).hexdigest()[:12]},timeout=70)
            if rr.ok and rr.content==updated:break
        else:raise RuntimeError('New Avito feed accepted but download readback differs')
    except Exception:
        # restore stable filename on failed upload or validation
        current=requests.get(api+'/releases/tags/product-photos',headers=headers,timeout=30).json()
        for x in current.get('assets',[]):
            if x.get('name')=='avito_feed.xlsx':
                requests.delete(api+'/releases/assets/'+str(x['id']),headers=headers,timeout=40)
        recover=requests.patch(api+'/releases/assets/'+str(existing['id']),headers=headers,json={'name':'avito_feed.xlsx'},timeout=40)
        if not recover.ok:print('WARNING Avito rollback needs manual attention HTTP',recover.status_code,flush=True)
        raise
    return {'status':'stable_feed_updated_verified','feed_url':stable,'feed_row':row,'avito_id':s.cell(row,9).value,'retained_other_images':len(parts)-1,'backup_asset':backup,'old_feed_sha':sha256(orig).hexdigest(),'new_feed_sha':sha256(updated).hexdigest(),'non_image_cells_unchanged':True,'autoupload_processing':'depends_on_Avito_subscription_schedule'}

def tilda_update(local_jpg):
    """Use only exact public live SKU; do not send price/stock offers."""
    import tilda_commerceml as tc
    from scripts.leoparts_direct_operation_nov21 import site_records
    records=site_records()
    matches=[x for x in records.values() if str(x.get('sku')or'')==SKU]
    if len(matches)!=1 or str(matches[0].get('uid'))!='695009271403':raise ValueError('Tilda exact SKU/UID mismatch')
    before=matches[0];gallery=before.get('gallery') or []
    if isinstance(gallery,str):
        try:gallery=json.loads(gallery)
        except Exception:raise ValueError('Tilda gallery format unrecognized; keep existing photos')
    if not isinstance(gallery,list) or not gallery:raise ValueError('Tilda gallery missing or unsupported; safe skip')
    links=[]
    for element in gallery:
        if isinstance(element,str):links.append(element)
        elif isinstance(element,dict):
            u=next((element.get(k) for k in ('img','url','original','src','large') if isinstance(element.get(k),str) and element[k].startswith('http')),None)
            if not u:raise ValueError('Tilda gallery entry not recognized')
            links.append(u)
        else:raise ValueError('Tilda gallery entry unexpected')
    images=[('import_files/6t30_gen1_cover_20261123.jpg',Path(local_jpg).read_bytes())]
    for index,u in enumerate(links[1:],2):
        q=requests.get(u,timeout=40);q.raise_for_status()
        if len(q.content)>4_000_000:raise ValueError('Tilda secondary image too large')
        # Re-send existing image bytes to keep secondary images in gallery.
        images.append((f'import_files/6t30_kept_{index}.jpg',q.content))
    # Existing live title/description preserved. Dummy unchanged price used only to include item in import.xml.
    price=float(str(before.get('price')).replace(' ','').replace(',','.'))
    items=[{'id':SKU,'title':before.get('title')or'',
            'description':before.get('descr') or before.get('text') or '',
            'price':price,'qty':None}]
    xml=tc.build_import_xml(items,{SKU:[n for n,_ in images]})
    xml=xml.replace('<Каталог>','<Каталог СодержитТолькоИзменения="true">')
    offers=tc.build_offers_xml([]).replace('СодержитТолькоИзменения="false"','СодержитТолькоИзменения="true"')
    if 'СодержитТолькоИзменения="true"' not in xml or 'СодержитТолькоИзменения="true"' not in offers:raise ValueError('Changes-only flag missing')
    required=['TILDA_COMMERCEML_URL','TILDA_COMMERCEML_USER','TILDA_COMMERCEML_PASSWORD']
    if any(not os.getenv(k) for k in required):raise RuntimeError('Tilda CommerceML not connected')
    steps=tc.push(os.environ['TILDA_COMMERCEML_URL'],os.environ['TILDA_COMMERCEML_USER'],os.environ['TILDA_COMMERCEML_PASSWORD'],xml,offers,image_files=images,timeout=100)
    time.sleep(9)
    refreshed=site_records()
    after=[x for x in refreshed.values() if str(x.get('sku')or'')==SKU]
    if len(after)!=1:raise RuntimeError('Tilda card missing after import')
    v=after[0]
    for k in ('price','quantity','title','descr','text'):
        if before.get(k)!=v.get(k):raise ValueError('Tilda unrelated field changed '+k)
    return {'status':'import_accepted_live_checked','tilda_uid':str(v.get('uid')),'gallery_count_before':len(links),'gallery_count_after':len(v.get('gallery')or[]),'same_price_stock_title_description':True,'file_count':len(images),'steps':steps,'no_price_stock_offers':True}

def main():
    req=json.loads((DATA/'6t30_cover_publish_20261123_request.json').read_text())
    if req!={'sku':SKU,'approved_by_owner':True,'scope':'replace_cover_all_channels','version':1}:
        raise ValueError('Unapproved scope manifest')
    from photo_host import upload_file
    mp=ROOT/'reports'/'6t30_approved_media'/'approved_mp.jpg'
    av=ROOT/'reports'/'6t30_approved_media'/'approved_avito.jpg'
    decode('6t30_cover_approved_marketplaces_20261123.b64',mp,EXPECTED_MP,(1200,1600))
    decode('6t30_cover_approved_avito_20261123.b64',av,EXPECTED_AV,(1400,1400))
    write('asset_validation',{'status':'verified','mp_sha':EXPECTED_MP,'av_sha':EXPECTED_AV})
    mp_url=upload_file(str(mp),filename=NEW_MP)
    av_url=upload_file(str(av),filename=NEW_AV)
    for label,url,sha in [('marketplaces',mp_url,EXPECTED_MP),('avito',av_url,EXPECTED_AV)]:
        for wait in [0,5,10,20]:
            if wait:time.sleep(wait)
            try:
                rr=requests.get(url,timeout=50)
                if rr.ok and hashlib.sha256(rr.content).hexdigest()==sha:break
            except requests.RequestException:pass
        else:raise RuntimeError('Approved GitHub release image not publicly reachable: '+label)
    write('release_assets',{'status':'published_verified','marketplaces_url':mp_url,'avito_url':av_url})
    # Upload is verified; persist the exact cover locally with canonical filenames.
    (ROOT/'photos'/SKU/'6t30_1.png').write_bytes(_png_bytes(mp))
    (ROOT/'photos_avito'/SKU/'6t30_1.jpg').write_bytes(av.read_bytes())
    # Primary source pins survive future catalog refreshes. Other slides are untouched.
    old=gallery_current_manifest(mp_url,av_url)
    (DATA/'6t30_cover_20261123_previous_sources.json').write_text(json.dumps(old,ensure_ascii=False,indent=2)+'\n')
    write('source_manifest',{'status':'updated','sku':SKU,'channels':list(old),'old_primary':{k:x[0] for k,x in old.items()}})
    # Prepare catalog bookkeeping, never write any price/stock column.
    for name,fn in [('ozon_catalog',lambda:replace_cover_in_xlsx('ozon_catalog.xlsx','Ozon',1,6,mp_url)),
                    ('wb_catalog',lambda:replace_cover_in_xlsx('wb_catalog.xlsx','WB',1,4,mp_url)),
                    ('avito_photo_map',lambda:update_avito_local(av_url))]:
        try:write(name,{'status':'updated',**fn()})
        except Exception as e:write(name,{'status':'failed','reason':type(e).__name__+': '+str(e)[:400]})
    for name,fn in [('ozon',lambda:run_ozon(mp_url)),('wb',lambda:run_wb(mp_url_to_path())),
                    ('market',lambda:run_market(mp_url)),('avito',lambda:avito_feed_update(av_url)),
                    ('tilda',lambda:tilda_update(mp_url_to_path()))]:
        try:write(name,fn())
        except Exception as e:
            write(name,{'status':'failed','reason':type(e).__name__+': '+str(e)[:550]})
    LOG['finished']=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(LOG,ensure_ascii=False,indent=2)+'\n')
    bad=[n for n in ('ozon','wb','market','avito','tilda') if LOG['channels'][n]['status'] not in ('verified','already_present','stable_feed_updated_verified','import_accepted_live_checked')]
    print('FINAL_6T30_COVER_STATUS',json.dumps({'issues':bad,'channels':{k:v['status'] for k,v in LOG['channels'].items()}},ensure_ascii=False),flush=True)
    return 1 if bad else 0

def mp_url_to_path():
    # The canonical source stays a PNG for existing catalog tooling.
    return ROOT/'reports'/'6t30_approved_media'/'approved_mp.jpg'

def _png_bytes(p):
    buf=io.BytesIO()
    Image.open(p).convert('RGB').save(buf,'PNG',optimize=True)
    return buf.getvalue()

if __name__=='__main__':sys.exit(main())