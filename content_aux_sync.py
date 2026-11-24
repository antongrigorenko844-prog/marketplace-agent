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
            matched=normalized(row.get('text'))==normalized(text)
            results[sku]={'uid':uid,'description_verified':matched,
                          'status':'verified' if matched else 'csv_import_required'}
            if not matched:
                patch.append({'Tilda UID':uid,'External ID':row['externalid'],
                              'SKU':sku,'Title':row['title'],'Description':tf._short_description(tf._clean_description(text)),
                              'Text':text})
        except Exception as exc:
            results[sku]={'error':str(exc)[:1000],'description_verified':False}
    return results,patch


def write_report(report):
    OUT.mkdir(parents=True,exist_ok=True)
    report['checked_utc']=datetime.datetime.now(datetime.timezone.utc).isoformat()
    (OUT/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def run(mode,skus,channels):
    rules=load()
    if mode not in {'inspect','apply','verify'} or not skus or len(skus)>50 or len(set(skus))!=len(skus) or set(skus)-set(rules):
        raise ValueError('Explicit unique approved SKU scope of 1–50 required')
    if not channels or set(channels)-{'avito','tilda'}:
        raise ValueError('Explicit auxiliary channels required')
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
            autoload=av.get_autoload_report_items()
            report_rows=autoload['items']
            report['avito_autoload_report']={k:autoload['report'].get(k) for k in ('report_id','started_at','finished_at','status')}
            live=av.list_all_items(statuses=['active'])
            links=json.loads((ROOT/'data/avito_links.json').read_text(encoding='utf-8'))
            avito_live_details(av,live,report_rows,skus,rules,links)
            staged,results=patch_avito_feed(raw,rules,skus,report_rows,live,links)
            report['products']['avito']=results
            report['avito_feed_sha_before']=digest(raw)
            report['avito_feed_sha_staged']=digest(staged)
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
