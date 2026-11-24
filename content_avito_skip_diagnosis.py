"""Read-only historical Avito diagnosis; no upload, profile, listing or price writes."""
import hashlib
import io
import re
from urllib.parse import urlsplit
from collections import Counter
import requests
from openpyxl import load_workbook
import avito_client as av
import avito_feed as af
import content_pipeline as p
import content_aux_sync as aux

SKU='0am325025H'
AID='8312205273'
def read(path,params=None):
    return av._request('GET',path,params=params,retries=1)
def subset(row):
    return {k:row.get(k) for k in ('ad_id','avito_id','avito_status','section','status','status_detail','messages','errors','warnings','feed_name','avito_date_end') if k in row}
def hit(row):
    return str(row.get('avito_id'))==AID and row.get('ad_id')==SKU
def exact(rows):
    return [subset(x) for x in rows if hit(x)]
def summary(row):
    return {k:row.get(k) for k in ('upload_id','report_id','started_at','finished_at','status','stats','section_stats','events') if k in row}
def snapshot(upload,target):
    feeds=upload.get('feed_urls') or []
    found=[f for f in feeds if f.get('name')=='LEOPARTS запчасти']
    if len(found)!=1:return {'error':'No unique historical parts snapshot'}
    url=found[0].get('url')
    u=urlsplit(str(url or ''))
    if u.scheme!='https' or u.hostname!='api.avito.ru' or not u.path.startswith('/autoload/v2/feed/content/'):
        return {'error':'Unexpected official snapshot path'}
    r=requests.get(url,headers={'Authorization':av._headers()['Authorization']},timeout=20,allow_redirects=False)
    if r.status_code!=200:return {'http_status':r.status_code}
    if len(r.content)>8_000_000:return {'error':'Unexpected snapshot size'}
    try:
        wb=load_workbook(io.BytesIO(r.content),read_only=True,data_only=True)
        ws=wb[af.SHEET_ADS]
        rows=[v for v in ws.iter_rows(min_row=af.FIRST_DATA_ROW,values_only=True) if v[af.COL_ID-1]==SKU]
        if len(rows)!=1:raise ValueError('Exact control row not unique')
        row=rows[0];desc=str(row[af.COL_DESCRIPTION-1] or '')
        wanted=aux.desired_avito(SKU,target,row[af.COL_PRICE-1])
        out={'sha256':hashlib.sha256(r.content).hexdigest(),'exact_description_matches':desc==wanted,
             'normalized_description_matches':aux.normalized(desc)==aux.normalized(wanted),
             'description_sha256':p.digest(aux.normalized(desc)),'description_start':desc[:180],
             'source_id':str(row[af.COL_ID-1]),'bound_avito_id':str(row[af.COL_AVITO_ID-1] or ''),
             'description_length':len(desc)}
        wb.close()
        return out
    except Exception as exc:return {'error':type(exc).__name__+': '+str(exc)[:150]}
def run(rules):
    result={'checked_utc':p.stamp(),'seller_writes':0,'feed_writes':0,'control':{'sku':SKU,'avito_id':AID}}
    target=rules[SKU]['text']['avito']['description']
    profile=read('/autoload/v2/profile')
    result['profile']={'autoload_enabled':profile.get('autoload_enabled'),'uploadMode':profile.get('uploadMode'),
                       'feed_names':[x.get('feed_name') for x in profile.get('feeds_data',[])],
                       'schedule_windows':len(profile.get('schedule') or [])}
    live=[r for r in av.list_all_items(statuses=['active']) if str(r.get('id'))==AID]
    if len(live)==1:
        r=live[0];result['control']['live_status']=r.get('status')
        result['control']['live_title']=r.get('title')
        result['control']['live_description_exposed']=isinstance(r.get('description'),str)
        if isinstance(r.get('description'),str):
            result['control']['live_description_matches']=aux.normalized(r['description'])==aux.normalized(aux.desired_avito(SKU,target,r.get('price')))
    # Exact current and completed v4 lookups avoid the broad pagination issue.
    result['v4_exact']={}
    history=[]
    try:
        history=read('/autoload/v4/uploads',{'page':1,'perPage':20}).get('uploads',[])
    except Exception as exc:result['history_error']=str(exc)[:160]
    for kind in ('current','last_successful'):
        path='/autoload/v4/uploads/'+kind
        try:
            u=read(path)
            q=read(path+'/items',{'page':1,'perPage':50,'query':AID})
            rows=q.get('items',[])
            same=exact(rows)
            if not same:
                q=read(path+'/items',{'page':1,'perPage':50,'query':SKU})
                same=exact(q.get('items',[]))
            result['v4_exact'][kind]={'upload':summary(u),'matches':same,'query_total':q.get('meta',{}).get('total'),'snapshot':snapshot(u,target)}
        except Exception as exc:result['v4_exact'][kind]={'error':type(exc).__name__+': '+str(exc)[:180]}
    # The older per-report endpoint still exposes the exact historical section
    # for recent reports. Never assume a prior upload's item status from today.
    result['timeline']=[]
    for u in history[:16]:
        rid=u.get('upload_id')
        if not isinstance(rid,int):continue
        entry={'upload':summary(u)}
        try:
            d=read(f'/autoload/v2/reports/{rid}/items',{'page':0,'per_page':200})
            rows=d.get('items') or d.get('resources') or []
            entry['exact_items']=exact(rows)
            entry['returned_rows']=len(rows)
            if not entry['exact_items'] and len(rows)>=100:
                d=read(f'/autoload/v2/reports/{rid}/items',{'page':1,'per_page':200})
                entry['exact_items']=exact(d.get('items') or d.get('resources') or [])
            if entry['exact_items']:
                entry['snapshot']=snapshot(u,target)
        except Exception as exc:entry['historical_read_error']=type(exc).__name__+': '+str(exc)[:150]
        result['timeline'].append(entry)
    # Read all exact active entries from the last report; retain full messages
    # for status/reason analysis. No attempt to trigger another upload.
    report=av.get_autoload_report_items()
    active={str(x.get('id')) for x in av.list_all_items(statuses=['active'])}
    managed=[r for r in report['items'] if str(r.get('avito_id')) in active and r.get('ad_id')]
    result['last_report_active']={'report':summary(report['report']),'count':len(managed),
       'sections':dict(Counter((x.get('section') or {}).get('slug','unknown') for x in managed)),
       'messages':dict(Counter(str(m.get('code'))+': '+str(m.get('title') or m.get('description') or '')[:180] for x in managed for m in x.get('messages',[]))),
       'nonstandard_items':[subset(x) for x in managed if (x.get('section') or {}).get('slug') not in ('success_published','success_updated','success_skipped') or any(m.get('type')=='error' or 'редактирован' in str(m).lower() for m in x.get('messages',[]))]}
    return result
