"""Submit one owner-approved Avito listing via a separate official feed.

Do not overwrite, relink, archive, disguise or modify any existing listing.
The real OEM remains unchanged; a new neutral feed ID identifies only this
submission. Avito decides duplicate eligibility in its ordinary moderation.
Never retry under a different ID, category, geography or OEM.
"""
from __future__ import annotations
import copy, hashlib, io, json, os, sys, time
from pathlib import Path
import requests
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
import avito_client as av
import avito_feed as af
import content_pipeline as p
from scripts.apply_dq200_prices_20261120 import gh
from sales_availability import quantity_for_sale

SKU='0am325025H'
ARTICLE='0ам 325 025'
OEM='0AM325025H'
PRICE=29500
OLD_ID=8312205273
NEW_ID='leoparts-avito-dq200-cover-20261126-01'
NAME='LEOPARTS мехатроник DQ200'
ASSET='avito_dq200_new_20261126.xlsx'
BASE='https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/'
URL=BASE+ASSET
EXISTING=[BASE+'avito_feed.xlsx',BASE+'avito_tools_feed.xml']
DRAFT=ROOT/'drafts/avito_0am325025H_new_listing_20261126.json'
OUT=ROOT/'data/avito_dq200_new_20261126_result.json'
J={}

def require(ok,msg):
    if not ok:raise ValueError(msg)

def sha(raw):return hashlib.sha256(raw).hexdigest()

def get(url):
    r=requests.get(url,timeout=60)
    r.raise_for_status()
    return r.content

def api(method,path,**kwargs):
    return av._request(method,path,retries=1,**kwargs)

def save(*paths):
    J['updated_utc']=p.stamp()
    p.write_json(OUT,J)
    p.checkpoint([OUT,*paths],live=True)
    print('AVITO_DQ200',J.get('status'),flush=True)

def profile():
    x=api('GET','/autoload/v2/profile')
    require(x.get('autoload_enabled') is True,'Autoload is not enabled')
    require(x.get('allow_pay_over_limit') is False,'Existing paid-over-limit protection not confirmed')
    require(x.get('uploadMode') in ('auto',None),'Automatic upload not available')
    feeds=x.get('feeds_data') or []
    require([f.get('feed_url') for f in feeds] in (EXISTING,EXISTING+[URL]),'Unexpected registered feed URLs; preserve all existing feeds')
    require(all(f.get('feed_name') for f in feeds),'Existing feed label missing')
    require(bool(x.get('schedule')),'Existing schedule missing')
    require(isinstance(x.get('report_email'),str) and '@' in x['report_email'],'Existing notification email not available')
    return x

def normalized_schedule(value):
    return sorted((w.get('rate'),tuple(sorted(w.get('weekdays') or [])),tuple(sorted(w.get('time_slots') or []))) for w in value or [])

def verify_profile(before,after):
    require(after['feeds_data'][:2]==before['feeds_data'][:2],'Existing feed configuration differs')
    require(normalized_schedule(after['schedule'])==normalized_schedule(before['schedule']),'Existing schedule differs')
    require(after.get('allow_pay_over_limit') is False,'Paid-over-limit protection differs')
    require(after.get('report_email')==before.get('report_email'),'Report email differs')

def live_old():
    rows=av.list_all_items(statuses=['active'])
    found=[r for r in rows if int(r.get('id') or 0)==OLD_ID]
    require(len(found)==1 and int(found[0].get('price') or 0)==33500,'Existing protected listing is not active at expected price')
    return {k:found[0].get(k) for k in ('id','title','price','status','url')}

def report_items(kind):
    base='/autoload/v4/uploads/'+kind
    first=api('GET',base)
    result=[]
    for page in range(1,51):
        response=api('GET',base+'/items',params={'page':page,'perPage':50})
        rows=response.get('items')
        require(isinstance(rows,list),'Avito report item list unavailable')
        result.extend(rows)
        pages=(response.get('meta') or {}).get('pages')
        if len(rows)<50 or isinstance(pages,int) and page>=pages:break
    else:raise ValueError('Avito report pagination incomplete')
    require(api('GET',base).get('upload_id')==first.get('upload_id'),'Report advanced during read')
    return first,result

def readback():
    evidence=[]
    for kind in ('current','last_successful'):
        try:
            report,items=report_items(kind)
            matches=[r for r in items if str(r.get('ad_id'))==NEW_ID]
            evidence.append({'kind':kind,'upload_id':report.get('upload_id'),'status':report.get('status'),'matches':[{k:r.get(k) for k in ('ad_id','avito_id','avito_status','status','section','messages','url','feed_name')} for r in matches]})
        except Exception as exc:evidence.append({'kind':kind,'read_error':str(exc)[:250]})
    J['reports']=evidence
    hits=[row for entry in evidence for row in entry.get('matches',[])]
    ids={int(row['avito_id']) for row in hits if str(row.get('avito_id') or '').isdigit()}
    require(OLD_ID not in ids,'New feed linked to protected old listing; stop')
    require(len(ids)<=1,'Multiple new IDs; stop')
    if ids:
        new_id=ids.pop()
        live=av.list_all_items()
        found=[x for x in live if int(x.get('id') or 0)==new_id]
        J['new_avito_id']=new_id
        if len(found)==1:
            ad=found[0]
            J['new_listing']={k:ad.get(k) for k in ('id','title','price','status','url')}
            if ad.get('status')=='active' and int(ad.get('price') or 0)==PRICE:
                J['status']='active_price_verified_media_readback_required'
                return
        J['status']='new_id_assigned_pending_or_not_active'
    elif hits:J['status']='avito_report_received_no_new_id'
    else:J['status']='registered_pending_avito_processing'

def read_draft():
    d=json.loads(DRAFT.read_text(encoding='utf-8'))
    require(d['sku']==SKU and d['article']==ARTICLE and d['oem']==OEM and d['price_rub']==PRICE,'Exact approved draft mismatch')
    require(d['publication']['owner_requested_create'] is True and d['publication']['owner_acknowledged_duplicate_risk'] is True,'Owner authorization not recorded')
    require(d['existing_listing_protection']['avito_id']==OLD_ID,'Old listing identity mismatch')
    require('Артикул: '+ARTICLE+'.' in d['description'],'Owner article not present')
    rows=[d['gallery']['cover_candidate'],*d['gallery']['additional_photos']]
    require([r['position'] for r in rows]==list(range(1,8)),'Expected seven photos in owner order')
    video=d['gallery']['video']
    require(video.get('hosted_verified') is True and video['filename']==SKU+'_video.mp4','Video identity missing')
    for row in rows:
        url=row['hosted_url']
        require(url.startswith(BASE) and sha(get(url))==row['sha256'],'Approved photo digest mismatch '+str(row['position']))
    require(video['url'].startswith(BASE) and sha(get(video['url']))==video['sha256'],'Approved video digest mismatch')
    J['media_verified']={'photos':7,'video':1}
    return d,rows,video

def read_stock():
    wb=load_workbook(ROOT/'data/ozon_catalog.xlsx',read_only=True,data_only=True)
    try:
        hits=[r for r in wb.active.iter_rows(min_row=2,values_only=True) if str(r[0] or '')==SKU]
        require(len(hits)==1,'Canonical stock row not unique')
        raw=quantity_for_sale(SKU,hits[0][7])
        n=float(raw)
        require(n.is_integer() and n>0,'No verified stock for new listing')
        return int(n)
    finally:wb.close()

def build(d,rows,video,primary):
    source=load_workbook(io.BytesIO(primary),read_only=True,data_only=True)
    try:
        s=source[af.SHEET_ADS]
        matches=[r for r in s.iter_rows(min_row=af.FIRST_DATA_ROW,values_only=True) if str(r[af.COL_ID-1] or '') in (SKU,SKU+'-av') or str(r[af.COL_AVITO_ID-1] or '')==str(OLD_ID)]
        require(len(matches)==1,'Primary feed exact protected row not unique')
        old=matches[0]
        require(str(old[af.COL_AVITO_ID-1] or '') in ('',str(OLD_ID)),'Conflicting protected listing binding')
        require(int(old[af.COL_PRICE-1])==33500,'Primary feed protected price mismatch')
        require(old[af.COL_CATEGORY-1]==d['category'] and old[af.COL_PART_KIND-1]==d['part_kind'] and old[af.COL_TRANSMISSION_PART_TYPE-1]==d['transmission_part_type'],'Primary category does not match approved draft')
        address=old[af.COL_ADDRESS-1]
        require(bool(address),'Protected seller address missing')
    finally:source.close()
    book=load_workbook(ROOT/'data/avito_template.xlsx')
    ws=book[af.SHEET_ADS]
    if ws.max_row>=af.FIRST_DATA_ROW:ws.delete_rows(af.FIRST_DATA_ROW,ws.max_row-af.FIRST_DATA_ROW+1)
    require(len(d['title'])<=50,'Title exceeds known category limit')
    # Exact owner media remains unchanged. Make the cross-platform origin of
    # editorially abbreviated real reviews explicit, not Avito ratings.
    note='На фото с отзывами — сокращённые отзывы покупателей с Ozon, а не рейтинг на Авито.'
    description=d['description']+'\n\n'+note
    require(len(description)<7500,'Description exceeds existing feed limit')
    # Restored, not new: use the official used condition. Never change the OEM
    # to Cyrillic or invent a different model to defeat duplicate detection.
    condition='Б/У'
    allowed={str(c.value).strip().casefold():str(c.value).strip() for sh in book.worksheets if sh.title!=af.SHEET_ADS for row in sh.iter_rows() for c in row if c.value is not None}
    require(condition.casefold() in allowed,'Official template used-condition value not found')
    condition=allowed[condition.casefold()]
    values={af.COL_ADDRESS:address,af.COL_ID:NEW_ID,af.COL_CATEGORY:d['category'],af.COL_DESCRIPTION:description,af.COL_IMAGE_URLS:'\n'.join(r['hosted_url'] for r in rows),af.COL_TITLE:d['title'],af.COL_PRICE:PRICE,af.COL_VIDEO_FILE_URL:video['url'],af.COL_GOODS_TYPE:d['goods_type'],af.COL_AD_TYPE:d['ad_type'],af.COL_PART_FOR:d['part_for'],af.COL_PART_KIND:d['part_kind'],af.COL_TRANSMISSION_PART_TYPE:d['transmission_part_type'],af.COL_CONDITION:condition,af.COL_BRAND:d['brand'],af.COL_OEM:OEM}
    # Where the template exposes a fee or promotion option, explicitly avoid
    # extra paid placement. The profile must separately prohibit over-limit.
    for col in range(1,ws.max_column+1):
        headers=[str(ws.cell(r,col).value or '').strip().casefold() for r in range(1,af.FIRST_DATA_ROW)]
        if any(x=='listingfee' or x=='способ оплаты размещения' for x in headers):values[col]='Package'
        if any(x=='adstatus' for x in headers):values[col]='Free'
    for col,value in values.items():ws.cell(af.FIRST_DATA_ROW,col,value)
    require(ws.cell(af.FIRST_DATA_ROW,af.COL_AVITO_ID).value is None,'Never link the new row to an existing Avito ID')
    buf=io.BytesIO();book.save(buf);book.close()
    raw=buf.getvalue()
    verify=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
    try:
        rows2=[r for r in verify[af.SHEET_ADS].iter_rows(min_row=af.FIRST_DATA_ROW,values_only=True) if r[af.COL_ID-1]]
        require(len(rows2)==1 and rows2[0][af.COL_ID-1]==NEW_ID,'New feed must contain exactly one new ID')
        require(rows2[0][af.COL_PRICE-1]==PRICE and rows2[0][af.COL_AVITO_ID-1] is None,'New feed price/binding mismatch')
        require(rows2[0][af.COL_OEM-1]==OEM and rows2[0][af.COL_VIDEO_FILE_URL-1]==video['url'],'OEM/video readback mismatch')
        require(rows2[0][af.COL_IMAGE_URLS-1].splitlines()==[r['hosted_url'] for r in rows],'Seven-image order changed')
    finally:verify.close()
    return raw,condition

def host(raw):
    rel=gh('GET','/releases/tags/product-photos').json()
    found=[a for a in rel.get('assets',[]) if a.get('name')==ASSET]
    require(len(found)<=1,'Isolated feed asset not unique')
    if found:
        require(get(URL)==raw,'Existing isolated feed differs; no overwrite')
    else:
        gh('POST',rel['upload_url'].split('{',1)[0],params={'name':ASSET},data=raw,headers={'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'})
    require(get(URL)==raw,'Exact public isolated feed not verified')

def main():
    global J
    require(os.environ.get('GITHUB_REF')=='refs/heads/main','Main only')
    p.current_main()
    J=json.loads(OUT.read_text()) if OUT.exists() else {'sku':SKU,'new_feed_id':NEW_ID,'price_rub':PRICE,'article':ARTICLE,'oem':OEM,'protected_avito_id':OLD_ID,'seller_writes':0,'started_utc':p.stamp(),'status':'preflight','new_avito_id':None}
    require(J['new_feed_id']==NEW_ID,'Journal identity mismatch')
    try:
        before=profile()
        J['old_before']=live_old()
        if [x['feed_url'] for x in before['feeds_data']]==EXISTING+[URL]:
            J['status']='registered_pending_avito_processing'
            J['profile_registered']=True
            readback()
            J['old_after']=live_old()
            require(J['old_before']==J['old_after'],'Protected old listing changed')
            save();return 0
        require(not J.get('profile_write_intent'),'Previous profile write uncertain: no blind retry')
        require('--readback-only' not in sys.argv,'Readback only: new feed not registered')
        d,rows,video=read_draft()
        J['stock_snapshot']=read_stock()
        primary,tools=get(EXISTING[0]),get(EXISTING[1])
        J['existing_feed_sha256']=[sha(primary),sha(tools)]
        raw,condition=build(d,rows,video,primary)
        J['condition']=condition
        J['status']='single_new_feed_validated'
        J['feed_sha256']=sha(raw)
        save()
        p.current_main()
        host(raw)
        J['feed_url']=URL
        J['status']='exact_new_feed_hosted_not_registered'
        save()
        again=profile()
        require(again['feeds_data']==before['feeds_data'] and normalized_schedule(again['schedule'])==normalized_schedule(before['schedule']),'Profile changed during preflight')
        require(get(EXISTING[0])==primary and get(EXISTING[1])==tools,'Existing feeds changed during preflight')
        payload={'autoload_enabled':True,'feeds_data':copy.deepcopy(before['feeds_data'])+[{'feed_name':NAME,'feed_url':URL}],'schedule':before['schedule'],'report_email':before['report_email']}
        # Existing seller account had allow_pay_over_limit=false; do not
        # change billing or trigger a global one-off import. Ordinary Avito
        # scheduled processing and duplicate moderation decide the outcome.
        J['profile_write_intent']=True
        J['status']='registration_intent'
        save()
        p.current_main()
        try:
            api('POST','/autoload/v2/profile',json=payload)
            J['seller_writes']=1
        except Exception as exc:
            J['registration_error']=type(exc).__name__+': '+str(exc)[:300]
        after=profile()
        verify_profile(before,after)
        require(after['feeds_data']==payload['feeds_data'],'New feed registration not confirmed')
        require(get(EXISTING[0])==primary and get(EXISTING[1])==tools,'Existing hosted feeds changed')
        J['profile_registered']=True
        J['status']='registered_pending_avito_processing'
        J['old_after']=live_old()
        require(J['old_before']==J['old_after'],'Protected old listing changed')
        save()
        readback();save()
        return 0
    except Exception as exc:
        J['status']='blocked_or_unverified'
        J['error']=type(exc).__name__+': '+str(exc)[:350]
        save()
        return 1

if __name__=='__main__':sys.exit(main())
