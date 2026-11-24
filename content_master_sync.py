"""Materialize approved descriptions into existing Excel cells without
resaving the workbook, changing formulas, images, prices, or stock.

Canonical source remains content_manifest.json. No marketplace calls.
"""
import argparse
from collections import Counter
from datetime import datetime,timezone
from hashlib import sha256
from html import escape
from io import BytesIO
import json
import os
from pathlib import Path
import posixpath
import re
import subprocess
import xml.etree.ElementTree as ET
from zipfile import ZipFile

from openpyxl import load_workbook
from content_manifest import load

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'reports/content-master/summary.json'
BOOKS={
    'data/master_control.xlsx': ('Товары',12,'master'),
    'data/ozon_catalog.xlsx': (None,3,'ozon'),
    'data/ozon_new_products.xlsx': (None,4,'ozon'),
    'data/wb_catalog.xlsx': (None,3,'wb'),
    'data/wb_new_products.xlsx': (None,4,'wb'),
}
MAIN_NS='http://schemas.openxmlformats.org/spreadsheetml/2006/main'
DOC_REL='http://schemas.openxmlformats.org/officeDocument/2006/relationships'
XML_NS='http://www.w3.org/XML/1998/namespace'


def digest(data):
    return sha256(data).hexdigest()


def read_rows(raw,sheet,desc_col):
    book=load_workbook(BytesIO(raw),read_only=True,data_only=False)
    try:
        ws=book[sheet] if sheet else book.active
        header=ws.cell(1,desc_col)
        if str(header.value or '').strip()!='Описание':
            raise ValueError('Unexpected description column')
        column_letter=header.column_letter
        rows={};duplicates=set()
        for row in ws.iter_rows(min_row=2):
            key=row[0].value
            if key is None or key=='':continue
            sku=str(key).strip()
            if sku in rows:duplicates.add(sku)
            if len(row)<desc_col:raise ValueError('Description column missing')
            cell=row[desc_col-1]
            rows[sku]={'coordinate':f'{column_letter}{row[0].row}',
                       'description':cell.value,'type':cell.data_type,
                       'ozon_status':row[1].value,'wb_status':row[2].value}
        return ws.title,rows,duplicates
    finally:book.close()


def sheet_xml_path(zipfile,sheet_name):
    root=ET.fromstring(zipfile.read('xl/workbook.xml'))
    matches=[s for s in root.findall('.//{'+MAIN_NS+'}sheet') if s.get('name')==sheet_name]
    if len(matches)!=1:raise ValueError('Ambiguous worksheet')
    rid=matches[0].get('{'+DOC_REL+'}id')
    rels=ET.fromstring(zipfile.read('xl/_rels/workbook.xml.rels'))
    hits=[r for r in rels if r.get('Id')==rid]
    if len(hits)!=1:raise ValueError('Missing sheet relationship')
    target=hits[0].get('Target','')
    path=target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join('xl',target))
    if not path.startswith('xl/worksheets/') or path not in zipfile.namelist():
        raise ValueError('Unexpected worksheet path')
    return path


def patch_cells(raw,sheet_name,edits):
    """Replace only preexisting explicit cell elements in the original sheet XML."""
    if not edits:return raw
    with ZipFile(BytesIO(raw)) as source:
        if len(source.namelist())!=len(set(source.namelist())):
            raise ValueError('Duplicate ZIP entries')
        path=sheet_xml_path(source,sheet_name)
        original=source.read(path)
        changed=original
        for coord,value in edits.items():
            if not re.fullmatch(r'[A-Z]+[1-9][0-9]*',coord) or not isinstance(value,str) or not value.strip() or len(value)>32767:
                raise ValueError('Invalid cell or description length')
            if any(ord(c)<32 and c not in '\n\r\t' for c in value):
                raise ValueError('Illegal XML control character')
            # Repository workbooks use ordinary unprefixed OOXML cell tags.
            # Refuse unfamiliar layouts rather than rewrite the whole workbook.
            rx=rb'<c(?=[\s>])(?=[^>]*\br="'+coord.encode()+rb'")([^>]*?)(?:/>|>(.*?)</c>)'
            hits=list(re.finditer(rx,changed,re.DOTALL))
            if len(hits)!=1:raise ValueError('Missing/ambiguous existing cell '+coord)
            match=hits[0]
            element=ET.fromstring(match.group(0))
            if element.find('f') is not None or element.find('{'+MAIN_NS+'}f') is not None:
                raise ValueError('Formula cell forbidden '+coord)
            if any(c.tag not in ('v','is','{'+MAIN_NS+'}v','{'+MAIN_NS+'}is') for c in element):
                raise ValueError('Unsupported cell metadata '+coord)
            attrs=re.sub(rb'\s+t="[^"]*"',b'',match.group(1)).rstrip()
            safe=escape(value,quote=False).replace('\r','&#13;').encode('utf-8')
            new=b'<c'+attrs+b' t="inlineStr"><is><t xml:space="preserve">'+safe+b'</t></is></c>'
            changed=changed[:match.start()]+new+changed[match.end():]
        ET.fromstring(changed)
        output=BytesIO()
        with ZipFile(output,'w') as dest:
            dest.comment=source.comment
            for item in source.infolist():
                dest.writestr(item,changed if item.filename==path else source.read(item.filename))
    staged=output.getvalue()
    with ZipFile(BytesIO(raw)) as old,ZipFile(BytesIO(staged)) as new:
        if old.namelist()!=new.namelist():raise ValueError('Workbook members changed')
        if any(old.read(name)!=new.read(name) for name in old.namelist() if name!=path):
            raise ValueError('Non-sheet workbook member changed')
    # Validate every target cell through the standard workbook reader.
    wb=load_workbook(BytesIO(staged),read_only=True,data_only=False)
    try:
        for coord,value in edits.items():
            if wb[sheet_name][coord].value!=value:raise ValueError('Readback failed '+coord)
    finally:wb.close()
    return staged


def target_description(rule,row,channel):
    text=rule.get('text',{})
    if channel=='master':
        # The existing master has ONE description cell. Prefer its own Ozon
        # representation where that card exists; never substitute WB copy on
        # an Ozon row merely because a WB-only update was approved.
        if row.get('ozon_status'):
            channel='ozon'
        elif row.get('wb_status'):
            channel='wb'
        else:
            return next((text.get(ch,{}).get('description') for ch in ('ozon','tilda','market','avito','wb')
                         if text.get(ch,{}).get('description')),None)
    return text.get(channel,{}).get('description')


def prepare_book(raw,sheet,desc_col,channel,rules,skus):
    name,rows,duplicates=read_rows(raw,sheet,desc_col)
    results={};edits={}
    for sku in skus:
        if sku not in rows:
            results[sku]={'status':'not_present'}
            continue
        row=rows[sku]
        if sku in duplicates:
            results[sku]={'status':'blocked_duplicate'}
            continue
        if channel=='wb' and sku=='0AM927769D':
            results[sku]={'status':'blocked_retired_wb_collision'}
            continue
        value=target_description(rules[sku],row,channel)
        if not value:
            results[sku]={'status':'no_channel_description'}
            continue
        if row['type']=='f':
            results[sku]={'status':'blocked_formula'}
            continue
        results[sku]={'status':'verified' if row['description']==value else 'pending',
                      'cell':row['coordinate'],'description_sha256':digest(value.encode())}
        if row['description']!=value:edits[row['coordinate']]=value
    staged=patch_cells(raw,name,edits)
    return staged,results


def save(report):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    report['checked_utc']=datetime.now(timezone.utc).isoformat()
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def run(mode,skus):
    rules=load()
    if mode not in ('inspect','verify','apply') or not skus or len(skus)>200 or len(set(skus))!=len(skus) or set(skus)-set(rules):
        raise ValueError('Explicit exact SKU scope required')
    report={'mode':mode,'revision':os.getenv('GITHUB_SHA'),'seller_writes':0,
            'books':{},'skus':skus}
    staged={};baseline={}
    for filename,(sheet,col,channel) in BOOKS.items():
        path=ROOT/filename
        if not path.exists():
            report['books'][filename]={'status':'missing'}
            continue
        raw=path.read_bytes();baseline[filename]=raw
        try:
            new,rows=prepare_book(raw,sheet,col,channel,rules,skus)
            report['books'][filename]={'rows':rows,'before_sha256':digest(raw),'staged_sha256':digest(new)}
            if new!=raw:staged[filename]=new
        except Exception as exc:
            report['books'][filename]={'error':str(exc)[:1000]}
    errors=any(b.get('error') or any(v.get('status','').startswith('blocked') for v in b.get('rows',{}).values()) for b in report['books'].values())
    if mode=='apply' and staged and not errors:
        if os.getenv('GITHUB_REF')!='refs/heads/main':raise ValueError('Master apply only on main')
        # Caller holds shared write lock and checks current canonical revision.
        for filename,raw in baseline.items():
            if (ROOT/filename).read_bytes()!=raw:raise ValueError('Workbook changed during staging')
        for filename,raw in staged.items():(ROOT/filename).write_bytes(raw)
        # Never auto-merge a modified XLSX from an older snapshot. The workflow
        # commits only these staged files and rejects a non-fast-forward push.
        report['staged_files']=list(staged)
        for filename,(sheet,col,channel) in BOOKS.items():
            if filename not in baseline:continue
            _,rows=prepare_book((ROOT/filename).read_bytes(),sheet,col,channel,rules,skus)
            report['books'][filename]['rows']=rows
        report['status']='staged_and_verified'
    else:
        report['status']='blocked' if errors else 'read_only_or_already_current'
    save(report)
    return int(errors or (mode=='verify' and bool(staged)))


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--mode',required=True,choices=['inspect','verify','apply'])
    p.add_argument('--article',required=True)
    a=p.parse_args()
    raise SystemExit(run(a.mode,a.article.split(',')))
