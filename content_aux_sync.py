"""Auxiliary description adapter: update existing Avito feed cells only, prepare
Tilda's supported CSV update and verify the public product text. No seller-price,
stock, creation, archive or media operations. Tilda CSV is not a publication.
"""
import argparse
import csv
import datetime
import hashlib
import html
from io import BytesIO
import json
import os
from pathlib import Path
import re
import subprocess
import time
import xml.etree.ElementTree as ET

from openpyxl import load_workbook
from content_manifest import load
import avito_feed as af
import tilda_feed as tf
import exchange_items

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'reports/content-aux'
FEED=ROOT/'data/avito_feed.xlsx'


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def normalized(value):
    return re.sub(r'\s+',' ',html.unescape(tf._clean_description(str(value or '')))).strip()


def one(rows, label):
    if len(rows)!=1:
        raise ValueError(f'Expected one {label}, got {len(rows)}')
    return rows[0]


def desired_avito(sku,description,price):
    text=af._clean_description(description)
    note=exchange_items.avito_note(sku,price)
    if note and note not in text:
        text=note+'\n\n'+text
    if not text.strip() or len(text)>7500:
        raise ValueError('Avito description is empty or exceeds 7500 characters')
    return text


def read_avito_autoload(av):
    """Use Avito's current v4 reports. Never treat an old report as evidence
    that a later feed has been imported. Keep legacy lookup as a fallback."""
    evidence={}
    try:
        current=av._request('GET','/autoload/v4/uploads/current',retries=1)
        evidence['current']={k:current.get(k) for k in ('upload_id','status','started_at','finished_at')}
    except Exception as exc:
        evidence['current_error']=type(exc).__name__
    try:
        path='/autoload/v4/uploads/last_successful'
        last=av._request('GET',path,retries=1)
        if not last.get('upload_id') or not last.get('started_at'):
            raise ValueError('Missing v4 report identity')
        evidence['last_successful']={k:last.get(k) for k in ('upload_id','status','started_at','finished_at')}
        rows=[];seen=set()
        for page in range(1,41):
            data=av._request('GET',path+'/items',params={'page':page,'perPage':50},retries=1)
            batch=data.get('items')
            meta=data.get('meta') or {}
            if not isinstance(batch,list):
                raise ValueError('Unexpected v4 items response')
            if any(not isinstance(r,dict) for r in batch):
                raise ValueError('Unexpected v4 item')
            for row in batch:
                identity=(str(row.get('ad_id')),str(row.get('avito_id')))
                if identity in seen: raise ValueError('Duplicate v4 report item')
                seen.add(identity);rows.append(row)
            pages=meta.get('pages')
            if (isinstance(pages,int) and page>=pages) or len(batch)<50:
                break
        else: raise ValueError('v4 report pagination incomplete')
        again=av._request('GET',path,retries=1)
        if again.get('upload_id')!=last.get('upload_id'):
            raise ValueError('v4 report changed while reading items')
        if not rows or not any(r.get('ad_id') and r.get('avito_id') for r in rows):
            raise ValueError('No usable v4 mapping')
        return {'report':last,'items':rows,'version':'v4','evidence':evidence}
    except Exception as exc:
        evidence['v4_error']=type(exc).__name__+': '+str(exc)[:240]
        old=av.get_autoload_report_items()
        return {**old,'version':'v2_fallback','evidence':evidence}


def avito_import_evidence(report, rows, feed_sha, policy):
    """Distinguish matched source, processed report and exact live text.
    A later report is evidence of processing, not proof of its file hash."""
    record=policy.get('avito',{})
    timestamp=record.get('await_report_started_after_utc')
    def parse(value):
        try: return datetime.datetime.fromisoformat(value.replace('Z','+00:00'))
        except (TypeError,AttributeError,ValueError):return None
    start=parse(report.get('started_at'))
    threshold=parse(timestamp)
    fresh=bool(start and threshold and start>threshold and
               feed_sha==record.get('feed_sha256'))
    for item in rows.values():
        if item.get('error'):continue
        accepted=(item.get('autoload_section') in ('success_published','success_updated','success_skipped')
                  and item.get('autoload_status') in (None,'active')
                  and not any(m.get('type')=='error' for m in item.get('autoload_messages',[])))
        item['post_publication_report']=fresh
        item['autoload_processed']=bool(fresh and accepted and item.get('feed_verified'))
        item['status']='verified_live_text' if item.get('description_verified') else (
            'processed_text_not_exposed' if item['autoload_processed'] else 'pending_fresh_report_or_text')
    return fresh


def avito_live_details(av, live, report, skus, rules, links):
    """Best-effort documented detail reads; absence of a description is not success."""
    by_id={str(x.get('id')):x for x in live}
    targets=set()
    for sku in skus:
        expected=rules[sku].get('ids',{}).get('avito') or links.get(sku)
        aliases={sku}
        if expected and links.get(sku) and str(expected)==str(links[sku]):
            aliases.add(sku+'-av')
        hits=[r for r in report if r.get('ad_id') in aliases and r.get('avito_id')]
        if len(hits)==1 and (not expected or str(hits[0]['avito_id'])==str(expected)):
            aid=str(hits[0]['avito_id'])
            if aid.isdigit() and aid in by_id and not isinstance(by_id[aid].get('description'),str):
                targets.add(aid)
    if not targets:
        return
    try:
        account=av._request('GET','/core/v1/accounts/self')
        user_id=str(account.get('id') or '')
        if not user_id.isdigit(): return
    except Exception:
        return
    for aid in sorted(targets):
        try:
            detail=av._request('GET',f'/core/v1/accounts/{user_id}/items/{aid}/')
            if isinstance(detail.get('description'),str) and str(detail.get('id',aid))==aid:
                by_id[aid]['description']=detail['description']
        except Exception:
            continue


def patch_avito_feed(raw,rules,skus,report,live,links=None):
    """Return staged bytes; all other workbook cells must match exactly."""
    from scripts.apply_avito_owner_excel_20261124 import data_rows,unchanged_except
    links=links or {}
    wb=load_workbook(BytesIO(raw))
    ws,rows=data_rows(wb,'Объявления',af.COL_ID,af.FIRST_DATA_ROW)
    allowed=set();results={}
    for sku in skus:
        rule=rules[sku]
        desired=rule.get('text',{}).get('avito',{}).get('description')
        if not desired:
            continue
        try:
            expected=rule.get('ids',{}).get('avito') or links.get(sku)
            aliases={sku}
            if expected and links.get(sku) and str(links[sku])==str(expected):
                aliases.add(sku+'-av')
            feed_id=one([k for k in aliases if k in rows],f'Avito feed row for {sku}')
            record=one([x for x in report if str(x.get('ad_id'))==feed_id and x.get('avito_id')],
                       f'latest Avito report mapping for {sku}')
            aid=str(record['avito_id'])
            if expected and aid!=str(expected):
                raise ValueError('Avito ID differs from approved mapping')
            active=one([x for x in live if str(x.get('id'))==aid],f'live Avito ID {aid}')
            if active.get('status')!='active':
                raise ValueError('Avito listing is not active')
            i=rows[feed_id]
            bound=ws.cell(i,af.COL_AVITO_ID).value
            if bound and str(bound)!=aid:
                raise ValueError('Feed Avito ID differs from latest report')
            cell=ws.cell(i,af.COL_DESCRIPTION)
            want=desired_avito(sku,desired,ws.cell(i,af.COL_PRICE).value)
            changed=cell.value!=want
            actual=active.get('description')
            results[sku]={'avito_id':aid,'feed_id':feed_id,'needs_feed_change':changed,
                          'description_verified':isinstance(actual,str) and normalized(actual)==normalized(want),
                          'live_description_available':isinstance(actual,str),
                          'feed_verified':not changed,
                          'autoload_section':(record.get('section') or {}).get('slug'),
                          'autoload_status':record.get('avito_status'),
                          'autoload_messages':[{k:m.get(k) for k in ('code','type','title')} for m in record.get('messages',[])]}
            if changed:
                cell.value=want;allowed.add((ws.title,cell.coordinate))
        except Exception as exc:
            results[sku]={'error':str(exc)[:1000],'description_verified':False}
    if not allowed:
        return raw,results
    buf=BytesIO();wb.save(buf);staged=buf.getvalue()
    unchanged_except(load_workbook(BytesIO(raw)),load_workbook(BytesIO(staged)),allowed)
    return staged,results


def tilda_rows(rules,skus,records):
    results={};patch=[]
    for sku in skus:
        rule=rules[sku]
        desired=rule.get('text',{}).get('tilda',{}).get('description')
        if not desired:
            continue
        try:
            row=one([v for v in records.values() if v.get('sku')==sku],f'exact Tilda SKU {sku}')
            uid=str(row.get('uid') or '')
            expected=rule.get('ids',{}).get('tilda')
            if not uid.isdigit() or (expected is not None and str(expected)!=uid):
                raise ValueError('Tilda UID mismatch')
            if not row.get('externalid') or not row.get('title'):
                raise ValueError('Tilda external ID or title missing')
            text=tf._clean_description(desired)
            prefix=exchange_items.site_prefix(sku)
            if prefix and normalized(prefix) not in normalized(text):
                text=prefix+'<br/><br/>'+text
            short=tf._short_description(tf._clean_description(text))
            full_match=normalized(row.get('text'))==normalized(text)
            short_match=normalized(row.get('descr'))==normalized(short)
            matched=full_match and short_match
            results[sku]={'uid':uid,'description_verified':matched,'full_text_verified':full_match,
                          'short_description_verified':short_match,
                          'status':'verified' if matched else 'csv_import_required'}
            if not matched:
                patch.append({'Tilda UID':uid,'External ID':row['externalid'],
                              'SKU':sku,'Title':row['title'],'Description':short,
                              'Text':text})
        except Exception as exc:
            results[sku]={'error':str(exc)[:1000],'description_verified':False}
    return results,patch


def tilda_protected_state(records,target_uid):
    """Compare entire catalog, including text of all non-target products."""
    from scripts.tilda_price_pipeline_v1 import STABLE_FIELDS
    return {uid:{k:row.get(k) for k in STABLE_FIELDS
                 if uid!=target_uid or k not in ('text','descr')}
                | {'price':row.get('price'),'editions':row.get('editions')}
            for uid,row in records.items()}

def tilda_single_description_xml(row,text):
    """Existing CommerceML ID, minimal changes-only import, never offers."""
    import tilda_commerceml as tc
    root=ET.Element('КоммерческаяИнформация',{'ВерсияСхемы':'2.05',
        'ДатаФормирования':datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S')})
    cat=ET.SubElement(root,'Каталог',{'СодержитТолькоИзменения':'true'})
    ET.SubElement(cat,'Ид').text=tc.CATALOG_ID
    ET.SubElement(cat,'ИдКлассификатора').text=tc.CLASSIFIER_ID
    product=ET.SubElement(ET.SubElement(cat,'Товары'),'Товар')
    for key,val in [('Ид',row['externalid']),('Артикул',row['sku']),
                    ('Наименование',row['title']),('Описание',text)]:
        ET.SubElement(product,key).text=val
    if {x.tag for x in product}!={'Ид','Артикул','Наименование','Описание'}:
        raise ValueError('Unexpected Tilda import fields')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)

def tilda_import_only(xml):
    """Use the existing authenticated connector; never upload offers or images."""
    import requests
    from tilda_commerceml import _url,_check
    url,user,password=(os.environ[k] for k in ('TILDA_COMMERCEML_URL','TILDA_COMMERCEML_USER','TILDA_COMMERCEML_PASSWORD'))
    if not all((url,user,password)): raise ValueError('Tilda connection not configured')
    s=requests.Session();s.auth=(user,password)
    response=s.get(_url(url,type='catalog',mode='checkauth'),timeout=45)
    _check(response,'checkauth')
    lines=response.text.strip().splitlines()
    if len(lines)>=3 and lines[1].strip() and lines[2].strip() and lines[1].strip() not in s.cookies:
        s.cookies.set(lines[1].strip(),lines[2].strip())
    response=s.get(_url(url,type='catalog',mode='init'),timeout=45)
    _check(response,'init')
    filename='import0_1.xml'
    response=s.post(_url(url,type='catalog',mode='file',filename=filename),
                    data=xml,headers={'Content-Type':'application/octet-stream'},timeout=90)
    _check(response,'file '+filename)
    for attempt in range(12):
        response=s.get(_url(url,type='catalog',mode='import',filename=filename),timeout=90)
        outcome=_check(response,'import '+filename).splitlines()[0].lower()
        if outcome=='success': return
        time.sleep(5)
    raise RuntimeError('Tilda import acknowledgement timeout')

def tilda_canary(rules,sku,report):
    """A single proven existing identity; no auto retry or bulk promotion."""
    from scripts.leoparts_direct_operation_nov21 import site_records
    from scripts.tilda_price_pipeline_v1 import index_by_uid
    if os.getenv('GITHUB_REF')!='refs/heads/main':
        raise ValueError('Tilda canary requires main')
    rule=rules[sku]
    expected=str(rule.get('ids',{}).get('tilda') or '')
    previous=json.loads((ROOT/'data/tilda_price_pipeline_result.json').read_text())
    identity=previous.get('targets',{}).get(sku,{})
    if previous.get('status')!='verified_all_tilda_prices' or not expected or str(identity.get('uid'))!=expected or identity.get('externalid')!=sku:
        raise ValueError('No previously verified CommerceML identity for this exact UID and SKU')
    before=index_by_uid(site_records())
    row=one([r for r in before.values() if r.get('sku')==sku],f'exact Tilda SKU {sku}')
    if str(row.get('uid'))!=expected or row.get('externalid')!=sku or not row.get('title'):
        raise ValueError('Tilda UID/external ID mismatch')
    if len([r for r in before.values() if r.get('externalid')==sku])!=1:
        raise ValueError('Duplicate CommerceML identity')
    editions=row.get('editions') or []
    if len(editions)>1 or any(str(ed.get('uid'))!=expected or ed.get('sku')!=sku for ed in editions):
        raise ValueError('Variant ambiguity')
    results,patch=tilda_rows(rules,[sku],before)
    report['products']['tilda']=results
    if results[sku].get('error'): raise ValueError(results[sku]['error'])
    if not patch:
        results[sku]['status']='already_verified_no_write';return
    target=patch[0]
    xml=tilda_single_description_xml(row,target['Text'])
    baseline=tilda_protected_state(before,expected)
    report['tilda_canary']={'sku':sku,'uid':expected,'catalog_count_before':len(before),
                           'offers_sent':False,'photos_sent':False,'xml_sha256':digest(xml),
                           'status':'prepared'}
    write_report(report)
    send_error=None
    try:
        tilda_import_only(xml)
        report['tilda_canary']['status']='import_acknowledged'
    except Exception as exc:
        send_error=type(exc).__name__
        report['tilda_canary']['status']='unknown_import_outcome'
        report['tilda_canary']['send_error_type']=send_error
    write_report(report)
    # Acknowledgement alone never implies public text publication.
    for attempt in range(5):
        if attempt: time.sleep(8)
        after=index_by_uid(site_records())
        current=tilda_protected_state(after,expected)
        if baseline!=current:
            changed=sorted(uid for uid in baseline.keys()|current.keys() if baseline.get(uid)!=current.get(uid))
            report['tilda_canary'].update(status='protected_catalog_drift',changed_uids=changed)
            results[sku].update(status='protected_catalog_drift',description_verified=False)
            return
        live=after[expected]
        results[sku]['description_verified']=normalized(live.get('text'))==normalized(target['Text'])
        results[sku]['short_description_verified']=normalized(live.get('descr'))==normalized(target['Description'])
        if results[sku]['description_verified']:
            results[sku]['status']='verified'
            report['tilda_canary'].update(status='verified_full_text',protected_catalog_unchanged=True)
            return
    results[sku]['status']='csv_import_required'
    report['tilda_canary'].update(status='full_text_not_updated',protected_catalog_unchanged=True)


def write_report(report):
    OUT.mkdir(parents=True,exist_ok=True)
    report['checked_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def run(mode,skus,channels):
    rules=load()
    if mode not in {'inspect','apply','verify'} or not skus or len(skus)>50 or len(set(skus))!=len(skus) or set(skus)-set(rules):
        raise ValueError('Explicit unique approved SKU scope of 1–50 required')
    if not channels or set(channels)-{'avito','tilda','tilda_canary'}:
        raise ValueError('Explicit auxiliary channels required')
    if 'tilda_canary' in channels and (mode!='apply' or len(skus)!=1 or channels!=['tilda_canary']):
        raise ValueError('Explicit one-SKU Tilda canary apply only')
    report={'mode':mode,'revision':os.getenv('GITHUB_SHA'),'seller_price_writes':0,'stock_writes':0,'products':{}}
    if 'avito' in channels:
        try:
            import avito_client as av
            from scripts.apply_dq200_prices_20261120 import get_avito_asset
            from scripts.apply_avito_owner_excel_20261124 import publish_feed
            raw=FEED.read_bytes()
            _,_,stable=get_avito_asset()
            if stable!=raw:
                raise ValueError('Stable published Avito feed differs from repository; refuse update')
            autoload=read_avito_autoload(av)
            report_rows=autoload['items']
            report['avito_autoload_report']={k:autoload['report'].get(k) for k in ('report_id','upload_id','started_at','finished_at','status')}
            report['avito_autoload_api']=autoload['version']
            report['avito_autoload_evidence']=autoload['evidence']
            live=av.list_all_items(statuses=['active'])
            links=json.loads((ROOT/'data/avito_links.json').read_text(encoding='utf-8'))
            avito_live_details(av,live,report_rows,skus,rules,links)
            staged,results=patch_avito_feed(raw,rules,skus,report_rows,live,links)
            report['products']['avito']=results
            report['avito_feed_sha_before']=digest(raw)
            report['avito_feed_sha_staged']=digest(staged)
            policy=json.loads((ROOT/'data/content_description_policy.json').read_text())
            report['avito_report_after_known_publication']=avito_import_evidence(autoload['report'],results,digest(raw),policy)
            write_report(report)
            if mode=='apply' and staged!=raw:
                if os.getenv('GITHUB_REF')!='refs/heads/main':
                    raise ValueError('Avito publication only allowed from main')
                report['avito_feed_publication']=publish_feed(staged)
                FEED.write_bytes(staged)
                subprocess.run(['bash','.github/scripts/save_data.sh','data/avito_feed.xlsx'],
                               cwd=ROOT,check=True,timeout=180)
                for item in results.values():
                    if item.get('needs_feed_change'):
                        item.update(feed_verified=True,description_verified=False,
                                    status='feed_published_pending_live_description')
                report['avito_feed_persisted']=True
            elif mode=='apply':
                report['avito_feed_publication']={'status':'unchanged'}
        except Exception as exc:
            report['avito_error']=str(exc)[:1000]
        write_report(report)
    if 'tilda' in channels:
        try:
            from scripts.leoparts_direct_operation_nov21 import site_records
            records=site_records()
            results,patch=tilda_rows(rules,skus,records)
            # This is an actual public snapshot, not a stale export. Preserve
            # protected metadata fingerprints for post-import comparison.
            from scripts.tilda_price_pipeline_v1 import STABLE_FIELDS
            protected={uid:{k:row.get(k) for k in STABLE_FIELDS if k not in ('text','descr')} | {'price':row.get('price'),'editions':row.get('editions')} for uid,row in records.items()}
            report['tilda_protected_snapshot_sha256']=digest(json.dumps(protected,sort_keys=True,ensure_ascii=False,default=str).encode())
            report['products']['tilda']=results
            if patch:
                OUT.mkdir(parents=True,exist_ok=True)
                path=OUT/'tilda_description_update.csv'
                with path.open('w',encoding='utf-8-sig',newline='') as handle:
                    writer=csv.DictWriter(handle,fieldnames=['Tilda UID','External ID','SKU','Title','Description','Text'],
                                          delimiter=';',quoting=csv.QUOTE_ALL)
                    writer.writeheader();writer.writerows(patch)
                report['tilda_csv_rows']=len(patch)
                report['tilda_csv_sha256']=digest(path.read_bytes())
                report['tilda_csv_imported']=False
        except Exception as exc:
            report['tilda_error']=str(exc)[:1000]
    if 'tilda_canary' in channels:
        try:
            tilda_canary(rules,skus[0],report)
        except Exception as exc:
            report['tilda_canary_error']=type(exc).__name__+': '+str(exc)[:300]
    write_report(report)
    return 1 if any(report.get(ch+'_error') for ch in channels) or any(
        entry.get('error') or (mode!='inspect' and not entry.get('description_verified'))
        for rows in report['products'].values() for entry in rows.values()) else 0


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--mode',choices=['inspect','apply','verify'],required=True)
    p.add_argument('--article',required=True)
    p.add_argument('--channels',required=True)
    args=p.parse_args()
    raise SystemExit(run(args.mode,args.article.split(','),args.channels.split(',')))
