"""One-time exact closure probes. No seller or feed mutations."""
import base64
import hashlib
import io
import json
import re
from urllib.parse import urlsplit
from pathlib import Path
from PIL import Image, ImageOps, ImageChops, ImageStat
from openpyxl import load_workbook
import requests
import content_pipeline as p
from content_transport import guard

def one(rows,reason):
    if len(rows)!=1:raise ValueError(reason)
    return rows[0]

def run_market(rules):
    import yandex_market_client as ym
    import market_content as mc
    sku='0am325025Y'
    with guard([],allow_write=False,descriptions_only=True):
        biz=ym.get_business_id()
        card=one(mc.read_rows(biz,'offer-cards',[sku]),'Expected exact Market card')
        offer=one(mc.read_rows(biz,'offer-mappings',[sku]),'Expected exact Market offer')['offer']
        if str(card['mapping']['marketSku'])!='4384120991' or offer['offerId']!=sku:
            raise ValueError('Market identity mismatch')
        cat=card['mapping']['marketCategoryId']
        status,data=ym._request('POST',f'/v2/category/{cat}/parameters',params={'businessId':biz},retries=1)
        if status!=200 or data.get('status')!='OK':
            raise ValueError('Category definitions unavailable: HTTP '+str(status))
        defs=data['result']['parameters']
    return {'sku':sku,'market_sku':str(card['mapping']['marketSku']),'category_id':cat,
            'seller_text_matches':offer.get('description')==rules[sku]['text']['market']['description'],
            'current_protected_hash':p.protected_hash('market',offer,card),
            'original_protected_hash':p.read_json(p.STATE,{})['rows'][sku]['market'].get('protected_hash'),
            'legacy_annotation':[x for x in offer.get('params',[]) if 'аннотац' in str(x.get('name','')).lower()],
            'typed_description_parameters':[{k:v.get(k) for k in ('id','name','type','required','distinctive','allowCustomValues','description')} for v in defs if re.search(r'аннотац|описание',str(v.get('name','')),re.I)],
            'typed_count':len(defs),'no_write':True}

def thumb(img):
    img=ImageOps.exif_transpose(img).convert('RGB')
    img.thumbnail((768,768))
    out=io.BytesIO()
    img.save(out,format='JPEG',quality=86)
    return base64.b64encode(out.getvalue()).decode('ascii')

def run_tilda():
    from scripts.leoparts_direct_operation_nov21 import site_records
    row=site_records().get('602430815843')
    if not row or row.get('sku')!='Ea888gen3':raise ValueError('Tilda UID/SKU mismatch')
    gal=row.get('gallery')
    if isinstance(gal,str):gal=json.loads(gal)
    if not isinstance(gal,list) or not gal:raise ValueError('No exact gallery')
    url=gal[0].get('img')
    u=urlsplit(url)
    if u.scheme!='https' or u.hostname not in ('static.tildacdn.com','static.tildacdn.pub'):
        raise ValueError('Unexpected public image host')
    res=requests.get(url,timeout=20,allow_redirects=False)
    res.raise_for_status()
    if not res.headers.get('Content-Type','').startswith('image/') or len(res.content)>12_000_000:
        raise ValueError('Unexpected image response')
    im=Image.open(io.BytesIO(res.content));im.load()
    files=[p.ROOT/'photos/Ea888gen3-sep/Ea888gen3_1.png',p.ROOT/'photos/EA888gen3/EA888gen3_1.jpg']
    pics=[]
    for f in files:
        if f.is_file():
            src=Image.open(f);src.load()
            a=ImageOps.exif_transpose(im).convert('RGB').resize((128,128))
            b=ImageOps.exif_transpose(src).convert('RGB').resize((128,128))
            pics.append({'path':str(f.relative_to(p.ROOT)),'pixel_mean_difference':sum(ImageStat.Stat(ImageChops.difference(a,b)).mean)/3,'jpeg_base64':thumb(src)})
    return {'uid':str(row['uid']),'sku':row['sku'],'title':row['title'],'text':row.get('text'),'descr':row.get('descr'),
            'public_image_url':url,'public_image_sha256':hashlib.sha256(res.content).hexdigest(),
            'public_image_jpeg_base64':thumb(im),'comparison_photos':pics}

def run_avito(rules):
    import avito_client as av
    import avito_feed as af
    from scripts.apply_dq200_prices_20261120 import get_avito_asset
    from scripts.apply_avito_owner_excel_20261124 import data_rows
    with guard([],allow_write=False,descriptions_only=True):
        release,asset,raw=get_avito_asset()
        live=av.list_all_items(statuses=['active'])
        report=p.aux.read_avito_autoload(av)
    if raw!=(p.ROOT/'data/avito_feed.xlsx').read_bytes():raise ValueError('Current release and repo Excel differ')
    state=p.read_json(p.STATE,{})
    links=p.read_json(p.ROOT/'data/avito_links.json',{})
    active_ids={str(x.get('id')) for x in live if x.get('status')=='active'}
    targets=[sku for sku,row in state['rows'].items() if row.get('avito',{}).get('card_id') in active_ids and sku not in ('tool-dq200-acc-restore','tool-dsg-pressure-hp') and rules.get(sku,{}).get('text',{}).get('avito',{}).get('description')]
    scoped={s:json.loads(json.dumps(rules[s])) for s in targets}
    for sku in targets:scoped[sku].setdefault('ids',{})['avito']=state['rows'][sku]['avito']['card_id']
    staged,checks=p.aux.patch_avito_feed(raw,scoped,targets,report['items'],live,links)
    wb=load_workbook(io.BytesIO(raw));ws,rows=data_rows(wb,'Объявления',af.COL_ID,af.FIRST_DATA_ROW)
    classified=[]
    for sku,i in rows.items():
        canon=sku
        if sku.endswith('-av') and sku[:-3] in rules and str(links.get(sku[:-3],''))==str(ws.cell(i,af.COL_AVITO_ID).value or ''):canon=sku[:-3]
        desc=rules.get(canon,{}).get('text',{}).get('avito',{}).get('description')
        if not desc:continue
        actual=ws.cell(i,af.COL_DESCRIPTION).value
        target=p.aux.desired_avito(canon,desc,ws.cell(i,af.COL_PRICE).value)
        if actual!=desc:
            classified.append({'feed_id':sku,'canonical_sku':canon,'exact_adapter_match':actual==target,
                              'normalized_adapter_match':p.aux.normalized(actual)==p.aux.normalized(target),
                              'active_mapped':canon in targets,'raw_manifest_equal':False})
    return {'release_sha256':hashlib.sha256(raw).hexdigest(),'active_exact_rows':len(targets),
            'canonical_feed_matches':sum(v.get('feed_verified') is True for v in checks.values()),
            'staged_bytes_unchanged':staged==raw,'mismatches_or_errors':{s:v for s,v in checks.items() if v.get('error') or v.get('needs_feed_change')},
            'last_report':{k:report['report'].get(k) for k in ('report_id','upload_id','status','started_at','finished_at')},
            'raw_manifest_differences':classified,'public_text_verified':0,'seller_writes':0}

def probe(rules):
    result={'checked_utc':p.stamp(),'seller_writes':0}
    for name,fn in [('market',lambda:run_market(rules)),('tilda',run_tilda),('avito',lambda:run_avito(rules))]:
        try:result[name]=fn()
        except Exception as exc:result[name]={'error_type':type(exc).__name__,'error':str(exc)[:260]}
    return result
