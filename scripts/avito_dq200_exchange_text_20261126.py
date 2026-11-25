"""Change only the description cell of the already registered new DQ200 feed.
Owner-approved exchange price 29500, without exchange 33500. No new listing,
no profile update, no price, stock, promotion or existing-primary-feed writes.
"""
from __future__ import annotations
import copy, io, json, os, re, sys, time, zipfile
from pathlib import Path
from xml.sax.saxutils import escape
from urllib.parse import urlsplit
import requests
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path.insert(0,str(ROOT))
from scripts import avito_dq200_submit_20261126 as s
from scripts.apply_dq200_prices_20261120 import rename_asset, validate_workbook_delta
from content_pipeline import checkpoint,current_main,stamp,write_json
from avito_feed import SHEET_ADS,FIRST_DATA_ROW,COL_ID,COL_AVITO_ID,COL_DESCRIPTION,COL_PRICE,COL_OEM,COL_IMAGE_URLS,COL_VIDEO_FILE_URL
OUT=ROOT/'data/avito_dq200_exchange_text_20261126_result.json'
NEW_AVITO_ID=8477970233
BEFORE_SHA='7c86867fc8913a8c540b241f994d0750f81ad1fd0341d0c68a9b20c07df91a44'
OLD='🔄 Обмен: пришлите свой старый мехатроник (без платы) — вернём 4 000 ₽. Доставку старого оплачиваем мы. Принимаем мехатроник, если он не ржавый, не был в воде и подлежит восстановлению. Напишите «обмен» в сообщения. Цена объявления указана без обмена.'
NEW='🔄 Цена при обмене вашего старого мехатроника (без электронной платы) — 29 500 ₽. Цена без обмена — 33 500 ₽.'
NOTE='На фото с отзывами — сокращённые отзывы покупателей с Ozon, а не рейтинг на Авито.'
J={}

def save():
    J['updated_utc']=stamp()
    write_json(OUT,J);checkpoint([OUT],live=True)
    print('AVITO EXCHANGE',J['status'],flush=True)

def check(ok,msg):s.require(ok,msg)

def workbook(raw):
    book=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
    try:
        ws=book[SHEET_ADS]
        hits=[(i,r) for i,r in enumerate(ws.iter_rows(min_row=FIRST_DATA_ROW,values_only=True),FIRST_DATA_ROW) if r[COL_ID-1]]
        check(len(hits)==1 and hits[0][0]==FIRST_DATA_ROW,'Exact isolated one-row feed required')
        i,row=hits[0]
        check(row[COL_ID-1]==s.NEW_ID and row[COL_AVITO_ID-1] in (None,NEW_AVITO_ID,str(NEW_AVITO_ID)),'New listing binding mismatch')
        check(row[COL_PRICE-1]==29500 and row[COL_OEM-1]==s.OEM,'Price or OEM mismatch')
        return row, {w.title:list(w.values) for w in book}
    finally:book.close()

def patch(raw,desired):
    row,before=workbook(raw)
    previous=row[COL_DESCRIPTION-1]
    if previous==desired:return raw
    check(s.sha(raw)==BEFORE_SHA,'Unexpected isolated feed version; no overwrite')
    check(isinstance(previous,str) and previous.count(OLD)==1 and previous.replace(OLD,NEW)==desired,'Only owner approved description paragraph may change')
    # Discover the worksheet's exact package part via the workbook relationship.
    from xml.etree import ElementTree as ET
    from posixpath import normpath,join
    from openpyxl.utils import get_column_letter
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        root=ET.fromstring(z.read('xl/workbook.xml'))
        ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        matches=[x for x in root.findall('m:sheets/m:sheet',ns) if x.get('name')==SHEET_ADS]
        check(len(matches)==1,'Exact worksheet name missing')
        rid=matches[0].get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
        rels=ET.fromstring(z.read('xl/_rels/workbook.xml.rels'))
        hits=[r for r in rels if r.get('Id')==rid]
        check(len(hits)==1,'Exact worksheet relationship missing')
        target=hits[0].get('Target')
        check(isinstance(target,str),'Worksheet path missing')
        sheet=target.lstrip('/') if target.startswith('/') else normpath(join('xl',target))
        check(sheet.startswith('xl/worksheets/') and sheet in z.namelist(),'Unexpected worksheet part')
    cell=get_column_letter(COL_DESCRIPTION)+str(FIRST_DATA_ROW)
    result=io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(raw)) as src, zipfile.ZipFile(result,'w') as dst:
        check(sheet in src.namelist(),'Worksheet missing')
        for info in src.infolist():
            data=src.read(info.filename)
            if info.filename==sheet:
                t=data.decode('utf-8')
                pattern=r'<c\b[^>]*\br="'+cell+r'"[^>]*>.*?</c>'
                m=list(re.finditer(pattern,t,flags=re.S))
                check(len(m)==1,'Target description cell not unique')
                prior=m[0].group()
                check('inlineStr' in prior,'Unexpected target cell serialization')
                new,n=re.subn(r'(<t(?:\s[^>]*)?>).*?(</t>)',lambda x:x.group(1)+escape(desired)+x.group(2),prior,flags=re.S)
                check(n==1,'Target description text not unique')
                data=(t[:m[0].start()]+new+t[m[0].end():]).encode('utf-8')
            dst.writestr(info,data)
    newraw=result.getvalue()
    validate_workbook_delta(raw,newraw,[cell],sheet)
    afterrow,after=workbook(newraw)
    check(afterrow[COL_DESCRIPTION-1]==desired,'Description readback mismatch')
    check(before.keys()==after.keys(),'Workbook sheet change')
    for name in before:
        check(len(before[name])==len(after[name]),'Unexpected row change')
        for i,(a,b) in enumerate(zip(before[name],after[name]),1):
            if name==SHEET_ADS and i==FIRST_DATA_ROW:
                a=list(a);a[COL_DESCRIPTION-1]=desired;a=tuple(a)
            check(a==b,'Unexpected workbook value change')
    return newraw

def get_asset(name):
    release=s.gh('GET','/releases/tags/product-photos').json()
    hits=[a for a in release.get('assets',[]) if a.get('name')==name]
    check(len(hits)<=1,'Duplicate release asset '+name)
    return release,hits[0] if hits else None

def public(url,expected):
    for i in range(5):
        try:
            r=requests.get(url,params={'exchange_rev':s.sha(expected)[:12],'check':i},timeout=35)
            if r.ok and r.content==expected:return True
        except requests.RequestException:pass
        time.sleep(3)
    return False

def evidence(desired):
    live=s.av.list_all_items()
    old=[a for a in live if str(a.get('id'))==str(s.OLD_ID)]
    new=[a for a in live if str(a.get('id'))==str(NEW_AVITO_ID)]
    if len(old)==1:J['old_after']={k:old[0].get(k) for k in ('id','price','status','title')}
    check(len(old)==1 and int(old[0].get('price') or 0)==33500 and old[0].get('status')=='active','Protected old listing readback differs')
    if len(new)==1:
        item=new[0]
        J['new_listing']={k:item.get(k) for k in ('id','price','status','title','url')}
        desc=item.get('description')
        if not isinstance(desc,str):
            try:
                user_id=str(s.api('GET','/core/v1/accounts/self').get('id') or '')
                if user_id.isdigit():
                    detail=s.api('GET',f'/core/v1/accounts/{user_id}/items/{NEW_AVITO_ID}/')
                    if str(detail.get('id',NEW_AVITO_ID))==str(NEW_AVITO_ID):desc=detail.get('description')
            except Exception:pass
        J['live_description_verified']=isinstance(desc,str) and NEW in desc and OLD not in desc
    reports=[]
    for kind in ('current','last_successful'):
        try:
            report,rows=s.report_items(kind)
            hits=[x for x in rows if str(x.get('ad_id'))==s.NEW_ID]
            check(all(str(x.get('avito_id')) in ('None','',str(NEW_AVITO_ID)) for x in hits),'Conflicting Avito ID')
            record={'kind':kind,'upload_id':report.get('upload_id'),'status':report.get('status'),'matches':[{k:x.get(k) for k in ('ad_id','avito_id','avito_status','section','messages')} for x in hits]}
            links=[f.get('url') for f in report.get('feed_urls',[]) if f.get('name')==s.NAME]
            if len(links)==1:
                u=urlsplit(links[0])
                if u.scheme=='https' and u.hostname=='api.avito.ru' and u.path.startswith('/autoload/v2/feed/content/'):
                    rr=requests.get(links[0],headers={'Authorization':s.av._headers()['Authorization']},timeout=35,allow_redirects=False)
                    if rr.ok:
                        row,_=workbook(rr.content)
                        record['received_description_matches']=row[COL_DESCRIPTION-1]==desired
                        record['received_sha256']=s.sha(rr.content)
            reports.append(record)
        except Exception as e:reports.append({'kind':kind,'read_error':type(e).__name__+': '+str(e)[:180]})
    J['reports']=reports
    if J.get('live_description_verified'):J['status']='live_description_verified'
    elif any(x.get('received_description_matches') for x in reports):J['status']='avito_received_updated_feed_live_text_unverified'

def main():
    global J
    check(os.environ.get('GITHUB_REF')=='refs/heads/main','Main only')
    current_main()
    d=json.loads(s.DRAFT.read_text())
    check(d['price_rub']==29500 and d['article']==s.ARTICLE and d['oem']==s.OEM and d['description'].count(NEW)==1 and OLD not in d['description'],'Owner draft mismatch')
    desired=d['description']+'\n\n'+NOTE
    prior=json.loads(s.OUT.read_text())
    check(prior['new_avito_id']==NEW_AVITO_ID and prior['new_feed_id']==s.NEW_ID and prior['profile_registered'] is True,'Prior exact Avito ID/registration not confirmed')
    J={'sku':s.SKU,'avito_id':NEW_AVITO_ID,'feed_id':s.NEW_ID,'price_rub':29500,'without_exchange_price_rub':33500,'description_paragraph':NEW,'status':'preflight','old_listing_id':s.OLD_ID,'seller_price_writes':0,'seller_profile_writes':0,'existing_listing_writes':0}
    try:
        pf=s.profile()
        check([x['feed_url'] for x in pf['feeds_data']]==s.EXISTING+[s.URL],'Isolated feed registration missing')
        J['old_before']=s.live_old()
        old_other=[s.sha(s.get(u)) for u in s.EXISTING]
        release,asset=get_asset(s.ASSET)
        check(asset is not None,'Isolated registered feed unavailable')
        raw=s.get(s.URL)
        newraw=patch(raw,desired)
        J['old_sha256']=s.sha(raw);J['new_sha256']=s.sha(newraw)
        J['one_cell_only_verified']=True
        J['status']='isolated_description_preflight_verified';save()
        if raw!=newraw:
            current_main()
            temp=s.ASSET.replace('.xlsx','.exchange_staged_'+s.sha(newraw)[:12]+'.xlsx')
            _,stage=get_asset(temp)
            if stage is None:
                stage=s.gh('POST',release['upload_url'].split('{',1)[0],params={'name':temp},data=newraw,headers={'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}).json()
            check(public(stage['browser_download_url'],newraw),'Staged description feed not verified')
            _,current=get_asset(s.ASSET)
            check(current and current['id']==asset['id'] and s.get(s.URL)==raw,'Concurrent isolated feed change')
            J['status']='isolated_asset_swap_intent';J['staged_asset_id']=stage['id'];J['old_asset_id']=asset['id'];save()
            current_main()
            backup=s.ASSET.replace('.xlsx','.before_exchange_'+str(asset['id'])+'.xlsx')
            rename_asset(asset['id'],backup)
            try:rename_asset(stage['id'],s.ASSET)
            except Exception:
                rename_asset(asset['id'],s.ASSET);raise
        check(public(s.URL,newraw),'Updated isolated public feed not verified')
        check([s.sha(s.get(u)) for u in s.EXISTING]==old_other,'Other feed contents differ')
        check([x['feed_url'] for x in s.profile()['feeds_data']]==s.EXISTING+[s.URL],'Registered feed links changed')
        J['other_feeds_unchanged']=True;J['public_feed_verified']=True
        J['status']='isolated_feed_updated_pending_avito_processing';save()
        evidence(desired);save()
        return 0
    except Exception as e:
        J['status']='blocked_or_unverified';J['error']=type(e).__name__+': '+str(e)[:300];save();return 1

if __name__=='__main__':sys.exit(main())
