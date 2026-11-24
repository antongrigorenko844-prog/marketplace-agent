"""Resumable, description-only orchestration over the established seller adapters.
Each SKU/channel has its own state. Writes are opt-in and only from main.
No price, stock, title, media, archive or product creation operation exists here.
"""
import argparse
from collections import Counter, defaultdict
import copy
import csv
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
import sys

import content_manifest as cmf
import content_master_sync as cms
import content_sync as api
import content_aux_sync as aux
import ozon_description_guard as ozguard
import time
from content_transport import guard

ROOT = Path(__file__).resolve().parent
CONFIG = ROOT / 'data/content_pipeline_request.json'
BASE = ROOT / 'reports/content-pipeline'
STATE = BASE / 'state.json'
CHANNELS = ('ozon', 'wb', 'market', 'avito', 'tilda')
MAX_ATTEMPTS = 3
MAX_POLLS = 6
RETIRED = {('wb', '0AM927769D'): 'Previously confirmed retired WB card collision; exact active identity requires separate investigation'}
TERMINAL = {'verified', 'blocked', 'not_applicable'}

def now():
    return datetime.now(timezone.utc)

def stamp():
    return now().isoformat()

def digest(value):
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()

def read_json(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default

def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    temp.replace(path)

def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()

def current_main():
    git('fetch', '--no-tags', 'origin', 'main')
    if git('rev-parse', 'HEAD') != git('rev-parse', 'FETCH_HEAD'):
        raise RuntimeError('main advanced concurrently; stop before next write, resume from saved checkpoint')

def checkpoint(paths, *, live):
    """Never merge a stale workbook or overwrite owner edits. A failed push leaves
    the previously persisted pre-write marker intact; next run reads back first."""
    if not live:
        return
    current_main()
    files = [str(Path(p).relative_to(ROOT)) for p in paths if Path(p).exists()]
    if not files:
        return
    git('add', '--', *files)
    if not git('diff', '--cached', '--name-only'):
        return
    git('config','user.name','marketplace-agent-bot')
    git('config','user.email','actions@users.noreply.github.com')
    git('commit','-m','chore: checkpoint description pipeline [skip ci]')
    git('push','origin','HEAD:main')

def row_status(entry, status, error=None, **fields):
    entry.update(status=status, updated_utc=stamp(), error=error, **fields)
    return entry

def reconcile(previous, sku, ch, identity, target):
    """Only retain proof for the same exact listing and target content."""
    target_hash = digest(target) if target else None
    if previous and previous.get('card_id') == identity and previous.get('target_hash') == target_hash:
        return copy.deepcopy(previous)
    if previous and previous.get('status') in ('submitted','processing','retry_pending') and previous.get('protected_hash'):
        return row_status(copy.deepcopy(previous), 'blocked', 'Identity or desired text changed while an earlier write was unresolved; reconcile before any new write', candidate_card_id=identity, candidate_target_hash=target_hash)
    return {'sku':sku,'channel':ch,'card_id':identity,'target_hash':target_hash,
            'status':'prepared' if target else 'pending','attempts':0,'polls':0,'read_failures':0,
            'updated_utc':stamp(),'error':None}

def due(entry):
    value = entry.get('next_attempt_utc')
    return not value or datetime.fromisoformat(value) <= now()

def defer(entry, status, error, minutes=15):
    return row_status(entry,status,error,next_attempt_utc=(now()+timedelta(minutes=minutes)).isoformat())

def read_failure(entry, error):
    n = entry.get('read_failures',0)+1
    entry['read_failures'] = n
    if entry.get('status') in ('submitted','processing'):
        status = 'processing' if n < MAX_POLLS else 'blocked'
    else:
        status = 'retry_pending' if n < MAX_POLLS else 'blocked'
    return defer(entry,status,error, min(120, 5*2**min(n,5)))

def add(mapping, sku, identity, name=None):
    if isinstance(sku,str) and sku and identity is not None and str(identity):
        mapping.setdefault(sku,[]).append({'id':str(identity),'name':name})

def inventory():
    """Fresh observed coverage; no inference that unseen legacy/public cards are absent."""
    ids = {}; errors = {}; extra = {}; snapshots = {}
    def read(ch, fn):
        try:
            ids[ch], snapshots[ch] = fn()
        except Exception as exc:
            ids[ch] = {}; errors[ch] = type(exc).__name__+': '+str(exc)[:300]
    def ozon():
        import ozon_client as oz
        rows = oz.list_products()
        if not rows:raise ValueError('Empty Ozon census; cannot establish absence')
        out = {}
        for r in rows:
            if not r.get('archived',False) and not r.get('is_archived',False):
                add(out,r.get('offer_id'),r.get('product_id'))
        return out, None
    def wb():
        import wb_client
        cards = wb_client.list_cards()
        if not cards:raise ValueError('Empty WB census; cannot establish absence')
        out = {}
        for r in cards: add(out,r.get('vendorCode'),r.get('nmID'),r.get('title'))
        return out, {'cards':cards}
    def market():
        import yandex_market_client as ym
        rows = ym.get_all_offers(archived=False); biz=ym.get_business_id()
        if not rows:raise ValueError('Empty Market census; cannot establish absence')
        out = {}
        for r in rows:
            if not r.get('archived'): add(out,r.get('sku'),r.get('market_sku') or r.get('sku'),r.get('name'))
        return out, {'business_id':biz}
    with guard([],allow_write=False,descriptions_only=True):
        for ch,fn in [('ozon',ozon),('wb',wb),('market',market)]: read(ch,fn)
    def avito():
        import avito_client as av
        live=av.list_all_items(statuses=['active'])
        report=aux.read_avito_autoload(av)['items']
        links=read_json(ROOT/'data/avito_links.json',{})
        active={str(x.get('id')):x for x in live if x.get('status')=='active'}
        out={}; used=set()
        for r in report:
            sku=r.get('ad_id'); aid=str(r.get('avito_id') or '')
            if not isinstance(sku,str) or aid not in active: continue
            # The known alias is only accepted when the existing link proves identity.
            if sku.endswith('-av') and str(links.get(sku[:-3],''))==aid: sku=sku[:-3]
            add(out,sku,aid,active[aid].get('title'));used.add(aid)
        extra['avito_unmapped_active']=[{'id':k,'title':v.get('title')} for k,v in active.items() if k not in used]
        return out,None
    def tilda():
        from scripts.leoparts_direct_operation_nov21 import site_records
        records=site_records();out={}
        for uid,r in records.items(): add(out,r.get('sku'),uid,r.get('title'))
        extra['tilda_public_only']=True
        return out,records
    read('avito',avito);read('tilda',tilda)
    return ids,errors,extra,snapshots

def isolate_book(raw,sheet,col,ch,rules,skus):
    """Keep row-specific failures out of the valid workbook edits. A malformed
    workbook/header fails the entire workbook without touching other books."""
    cms.read_rows(raw,sheet,col)
    def part(data, group):
        try:
            return cms.prepare_book(data,sheet,col,ch,rules,group)
        except Exception as exc:
            if len(group)==1:
                return data,{group[0]:{'status':'blocked_cell','error':str(exc)[:300]}}
            middle=len(group)//2
            first,a=part(data,group[:middle])
            second,b=part(first,group[middle:])
            return second,{**a,**b}
    if not skus: return raw,{}
    return part(raw,skus)

def master_rules_for_census(rules, ids, errors):
    """Resolve a stale master channel flag only with independent exact census
    evidence and an identical, explicitly approved text on two live channels.
    The returned rules are for the master workbook only, never a seller API."""
    output = dict(rules)
    if 'ozon' in errors or not ids.get('ozon'):
        return output
    for sku, rule in rules.items():
        text = rule.get('text', {})
        if sku in ids['ozon'] or rule.get('ids', {}).get('ozon') or text.get('ozon', {}).get('description'):
            continue
        approved = {ch: fields['description'] for ch, fields in text.items()
                    if fields.get('description')}
        if len(set(approved.values())) != 1:
            continue
        exact = [ch for ch in approved if ch not in errors and len(ids.get(ch, {}).get(sku, [])) == 1
                 and (not rule.get('ids', {}).get(ch) or
                      str(rule['ids'][ch]) == ids[ch][sku][0]['id'])]
        if len(exact) < 2:
            continue
        output[sku] = dict(rule, _master_description=next(iter(approved.values())))
    return output


def ozon_strict_coverage(rows):
    """Do not confuse text equality under old normalization with exact
    protected-field preservation. Preserve the original evidence and hold."""
    counts = Counter()
    for channels in rows.values():
        e = channels.get('ozon', {})
        if e.get('status') != 'verified':
            continue
        counts['text_verified'] += 1
        if any(e.get('observed_ozon_normalization', {}).values()):
            counts['protected_fields_normalized'] += 1
        elif e.get('protection_verified'):
            counts['exact_protection_verified'] += 1
        elif e.get('attempts', 0) == 0:
            counts['already_matching_no_write'] += 1
        else:
            counts['protection_unproven'] += 1
    return dict(counts)


def materialize(rules,skus,live,apply,master_rules=None):
    result={}; changes={}
    for filename,(sheet,col,ch) in cms.BOOKS.items():
        path=ROOT/filename
        try:
            raw=path.read_bytes()
            new,rows=isolate_book(raw,sheet,col,ch,master_rules if ch=='master' and master_rules is not None else rules,skus)
            if apply and new!=raw: changes[path]=(raw,new)
            result[filename]={'rows':rows,'before_sha256':sha256(raw).hexdigest(),'staged_sha256':sha256(new).hexdigest()}
        except Exception as exc:
            result[filename]={'error':str(exc)[:300]}
    if apply and changes:
        current_main()
        for path,(raw,new) in changes.items():
            if path.read_bytes()!=raw: raise RuntimeError('Workbook changed during staging')
        for path,(raw,new) in changes.items(): path.write_bytes(new)
        # The original adapter checks each edited cell via openpyxl before it
        # returns staged bytes. Verify once more against the bytes on disk.
        for filename,(sheet,col,ch) in cms.BOOKS.items():
            path=ROOT/filename
            if path in changes:
                _,rows=isolate_book(path.read_bytes(),sheet,col,ch,master_rules if ch=='master' and master_rules is not None else rules,skus)
                result[filename]['rows']=rows
                if any(r.get('status')=='pending' for r in rows.values()):
                    raise RuntimeError('Workbook readback failed')
        checkpoint(list(changes),live=live)
    return result

def master_ok(report,sku,ch):
    master=report.get('data/master_control.xlsx',{})
    if master.get('rows',{}).get(sku,{}).get('status')!='verified': return False
    relevant={'wb':['data/wb_catalog.xlsx','data/wb_new_products.xlsx'],
              'ozon':['data/ozon_catalog.xlsx','data/ozon_new_products.xlsx']}.get(ch,[])
    for book in relevant:
        r=report.get(book,{})
        if r.get('error'): return False
        if r.get('rows',{}).get(sku,{}).get('status') not in ('verified','not_present'): return False
    return True

def identity(ch, card, detail):
    if ch=='wb': return str(card['nmID'])
    if ch=='ozon': return str(card.get('id') or card.get('product_id') or '')
    return str((detail or {}).get('mapping',{}).get('marketSku') or card.get('offerId'))

def protected_hash(ch, card, detail):
    if ch=='wb':
        fields=['nmID','vendorCode','title','brand','subjectID','dimensions','sizes','video','kizMarked']
        obj={k:card.get(k) for k in fields}
        obj['characteristics']=sorted(card.get('characteristics',[]),key=lambda x:x['id'])
        obj['gallery']=[cmf.normalize_url(x) for x in api.wb_gallery(card)]
    elif ch=='market':
        fields=['offerId','name','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions']
        obj={k:card.get(k) for k in fields}
        obj['mapping']=(detail or {}).get('mapping')
        from market_content import grouped
        obj['typed_parameters']=grouped((detail or {}).get('parameterValues',[]))
        obj['media']=card.get('mediaFiles',{})
        obj['gallery']=[cmf.normalize_url(x) for x in card.get('pictures',[])]
    else:
        obj=ozguard.projection(card,detail)
    return digest(obj)

def card_detail(ch,snapshot,sku):
    card,detail=api.description_card(ch,snapshot,sku)
    if ch=='ozon':
        detail=api.one(snapshot.get('prices',[]),'offer_id',sku)
    return card,detail

def read_channel(ch,skus,context):
    data=api.read_description_channel(ch,skus,context or {})
    if ch=='ozon':
        import ozon_client as oz
        data['prices']=oz.get_prices_and_stocks(skus)
    return data

def verify_preservation(ch,sku,old,new):
    if ch!='ozon':return api.verify_description_preservation(ch,sku,old,new)
    b,bp=card_detail(ch,old,sku);a,ap=card_detail(ch,new,sku)
    return ozguard.verify(b,a,bp,ap)

def send_channel(ch,payload,context):
    if ch!='ozon':return api.send_description_batch(ch,payload,context or {})
    import ozon_client as oz
    # Same successful annotation endpoint as the test; explicit dictionary ID,
    # one network attempt, never send model or KIZ attributes.
    for row in payload:
        row['attributes'][0]['values'][0]['dictionary_value_id']=0
    reply=oz._post('/v1/product/attributes/update',{'items':payload},retries=1)
    if not reply or reply.get('error'):raise ValueError('Ozon description request not acknowledged')
    return reply.get('task_id') or (reply.get('result') or {}).get('task_id')

def observe(entry,ch,snapshot,sku,target):
    card,detail=card_detail(ch,snapshot,sku)
    actual_id=identity(ch,card,detail)
    if actual_id!=entry['card_id']:
        raise ValueError('Live card ID differs from census')
    actual=api.description_value(ch,card)
    target_hash=digest(target)
    fingerprint=protected_hash(ch,card,detail)
    if entry.get('protected_hash') and entry.get('status') in ('submitted','processing','retry_pending') and entry['protected_hash']!=fingerprint:
        raise RuntimeError('Protected fields changed since submitted baseline')
    entry.update(after_hash=digest(actual),last_checked_utc=stamp(),read_failures=0)
    if digest(actual)==target_hash:
        row_status(entry,'verified',None,next_attempt_utc=None,protection_verified=bool(entry.get('protected_hash')),verification_basis='postwrite_protected_readback' if entry.get('protected_hash') else 'already_matching_live_text_no_write')
    return actual,fingerprint

def confirmed_rejection(audit):
    codes=[r.get('http_status') for r in audit]
    return bool(codes and all(c in (400,422,429) for c in codes))

def read_api(ch,skus,context):
    if ch=='wb':return read_channel(ch,skus,context)
    merged={}
    for offset in range(0,len(skus),50):
        part=read_channel(ch,skus[offset:offset+50],context)
        for key,value in part.items():
            if isinstance(value,list):merged.setdefault(key,[]).extend(value)
            else:merged[key]=value
    return merged

def worker_api(ch,entries,rules,context,paused,master,save,report_dir,apply):
    if not entries:return
    # Read all due exact targets once. For WB this uses the existing paginated list.
    try:
        with guard([],allow_write=False,descriptions_only=True):
            snap=read_api(ch,list(entries),context)
    except Exception as exc:
        for e in entries.values():read_failure(e,'Read failed: '+str(exc)[:300])
        save();return
    candidates=[]
    for sku,e in entries.items():
        target=rules[sku]['text'][ch]['description']
        try:
            actual,fingerprint=observe(e,ch,snap,sku,target)
            if e['status']=='verified':continue
            if e['status'] in ('submitted','processing'):
                n=e.get('polls',0)+1;e['polls']=n
                defer(e,'processing' if n<MAX_POLLS else 'blocked','Submitted write not yet verified; no blind resend')
                continue
            if ch in paused or not apply:
                row_status(e,'blocked' if ch in paused and apply else 'validated',paused.get(ch),before_hash=digest(actual))
                continue
            if not master_ok(master,sku,ch):
                row_status(e,'blocked','Canonical workbook description not verified for this SKU/channel')
                continue
            if e.get('attempts',0)>=MAX_ATTEMPTS:
                row_status(e,'blocked','Retry budget exhausted');continue
            rule={'text':{ch:{'description':target}},'ids':{'wb':e['card_id']} if ch=='wb' else {}}
            payload=api.description_payload(ch,sku,rule,snap)
            if payload is None:raise ValueError('Inconsistent readback')
            e.update(before_hash=digest(actual),protected_hash=fingerprint)
            candidates.append((sku,payload))
        except RuntimeError as exc:
            row_status(e,'blocked',str(exc))
            paused[ch]='Protected-field drift: '+sku
        except Exception as exc:
            row_status(e,'blocked',str(exc)[:300])
    save()
    if ch in paused or not apply:return
    # Never send a new Ozon batch when an earlier accepted write is unresolved.
    if ch=='ozon' and any(e.get('status') in ('submitted','processing') for e in entries.values()):return
    # Every actual write has a persisted intent and an exact pre-write baseline.
    groups=[];batch=[]
    if ch=='ozon':
        # Start with a known inexpensive singleton; then ordinary safe batches.
        candidates.sort(key=lambda item:(item[0]!='01X301127C',item[0]))
        if candidates:groups.append([candidates.pop(0)])
    for sku,payload in candidates:
        if entries[sku].get('isolate'):
            if batch:groups.append(batch);batch=[]
            groups.append([(sku,payload)])
        else:
            batch.append((sku,payload))
            if len(batch)==10:groups.append(batch);batch=[]
    if batch:groups.append(batch)
    for seq,group in enumerate(groups):
        if ch in paused:break
        skus=[s for s,_ in group]
        audit=[];safe=[]
        try:
            with guard([],allow_write=False,descriptions_only=True):
                fresh=read_channel(ch,skus,context)
            for sku,_ in group:
                e=entries[sku]
                try:
                    verify_preservation(ch,sku,snap,fresh)
                    actual,fp=observe(e,ch,fresh,sku,rules[sku]['text'][ch]['description'])
                    if e['status']=='verified':continue
                    if fp!=e['protected_hash']:raise ValueError('Fresh protected baseline differs')
                    rule={'text':{ch:rules[sku]['text'][ch]},'ids':{'wb':e['card_id']} if ch=='wb' else {}}
                    payload=api.description_payload(ch,sku,rule,fresh)
                    if payload is not None:safe.append((sku,payload))
                except Exception as exc:
                    if isinstance(exc,RuntimeError) and 'Protected fields changed' in str(exc):paused[ch]='Protected-field drift: '+sku
                    row_status(e,'blocked','Prewrite drift: '+str(exc)[:300])
        except Exception as exc:
            for sku in skus:read_failure(entries[sku],'Prewrite read: '+str(exc)[:300])
        if not safe:save();continue
        for sku,_ in safe:
            e=entries[sku];row_status(e,'submitted',None,attempts=e['attempts']+1,polls=0,next_attempt_utc=None,evidence=str((report_dir/ch/str(seq)).relative_to(ROOT)))
        before=fresh;failure=None;after=None
        evidence={'before':before,'writes':audit,'products':{},'revision':git('rev-parse','HEAD')}
        old_out=api.OUT;api.OUT=report_dir/ch/str(seq)
        try:
            api.encrypt_report(evidence)
            # Persist the encrypted original baseline together with the intent.
            save([api.OUT/'evidence.enc']);current_main()
            if ch in paused:break
            task=None
            try:
                with guard(audit,allow_write=True,descriptions_only=True):
                    task=send_channel(ch,[p for _,p in safe],context)
            except Exception as exc:failure=str(exc)[:300]
            consecutive=0
            for attempt in range(12 if ch=='ozon' else 1):
                if ch=='ozon':time.sleep(5)
                try:
                    with guard([],allow_write=False,descriptions_only=True):
                        if ch=='ozon' and task:
                            import ozon_client as oz
                            evidence['task_result']=oz.get_import_status(int(task))
                        after=read_channel(ch,[s for s,_ in safe],context)
                except Exception as exc:
                    consecutive=0
                    failure=(failure or '')[:200]+' Readback: '+str(exc)[:300]
                    continue
                matched=True
                for sku,_ in safe:
                    e=entries[sku]
                    if e.get('status')=='blocked':matched=False;continue
                    try:
                        normalization=verify_preservation(ch,sku,before,after)
                        if ch=='ozon':
                            card,_=card_detail(ch,after,sku)
                            if api.description_value(ch,card)!=rules[sku]['text'][ch]['description']:
                                matched=False;continue
                            e['observed_ozon_normalization']=normalization
                        else:observe(e,ch,after,sku,rules[sku]['text'][ch]['description'])
                    except Exception as exc:
                        matched=False
                        if 'Protected' in str(exc) or 'Unrelated fields changed' in str(exc):
                            paused[ch]='Protected-field drift: '+sku
                            row_status(e,'blocked',str(exc)[:300])
                        else:
                            e['error']=str(exc)[:300]
                if ch!='ozon' or ch in paused:break
                consecutive=consecutive+1 if matched else 0
                if consecutive>=2:
                    for sku,_ in safe:observe(entries[sku],ch,after,sku,rules[sku]['text'][ch]['description'])
                    break
            for sku,_ in safe:
                e=entries[sku]
                if e['status'] in ('verified','blocked'):continue
                if confirmed_rejection(audit) and e['attempts']<MAX_ATTEMPTS:
                    e['isolate']=True
                    defer(e,'retry_pending',failure or 'Explicit HTTP rejection; retry isolated after fresh read',15*e['attempts'])
                elif confirmed_rejection(audit):
                    row_status(e,'blocked',failure or 'Explicit rejection and retry budget exhausted')
                else:
                    defer(e,'processing',failure or 'Awaiting exact live readback')
            evidence.update(after=after,products={sku:entries[sku] for sku,_ in safe})
            api.encrypt_report(evidence)
        finally:api.OUT=old_out
        save([report_dir/ch/str(seq)/'evidence.enc'])
        if ch=='ozon' and any(entries[s]['status']!='verified' for s,_ in safe):break

def worker_tilda(entries,rules,records,report_dir,save):
    rows,patch=aux.tilda_rows(rules,list(entries),records)
    for sku,e in entries.items():
        r=rows.get(sku,{})
        if r.get('description_verified') and str(r.get('uid'))==e['card_id']:
            row_status(e,'verified',None)
        elif r.get('error') or (r.get('uid') and str(r['uid'])!=e['card_id']):
            row_status(e,'blocked',r.get('error') or 'Tilda UID mismatch')
        else:row_status(e,'blocked','Verified existing-product description import requires UID-scoped CSV; not published')
    if patch:
        path=report_dir/'tilda_description_update.csv';path.parent.mkdir(parents=True,exist_ok=True)
        with path.open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=['Tilda UID','External ID','SKU','Title','Description','Text'],delimiter=';',quoting=csv.QUOTE_ALL)
            w.writeheader();w.writerows(patch)
        save([path])
    else:save()

def worker_avito(entries,rules,master,report_dir,save,apply):
    if not entries:return
    # Independent verification and publication groups: waiting listings never
    # prevent fresh eligible listings from being included in a feed.
    eligible=[];verify=[]
    for sku,e in entries.items():
        if apply and e['status'] not in ('submitted','processing') and master_ok(master,sku,'avito') and e['attempts']<MAX_ATTEMPTS:
            eligible.append(sku)
        else:verify.append(sku)
    groups=[('verify',verify[i:i+50]) for i in range(0,len(verify),50)]
    if eligible:groups.append(('apply',eligible[:50]))
    for offset,(mode,skus) in enumerate(groups):
        group={s:entries[s] for s in skus}
        if mode=='apply':
            for sku,e in group.items():row_status(e,'submitted',None,attempts=e['attempts']+1)
            save();current_main()
        old_out,old_load=aux.OUT,aux.load
        aux.OUT=report_dir/'avito'/str(offset)
        bound=copy.deepcopy(rules)
        for sku,e in group.items():
            bound[sku].setdefault('ids',{})['avito']=e['card_id']
        aux.load=lambda:bound
        try:
            aux.run(mode,skus,['avito'])
            report=read_json(aux.OUT/'summary.json',{})
        finally:aux.OUT,aux.load=old_out,old_load
        for sku,e in group.items():
            r=report.get('products',{}).get('avito',{}).get(sku,{})
            if r.get('avito_id') and str(r['avito_id'])!=e['card_id']:
                row_status(e,'blocked','Avito report identity differs from census')
            elif r.get('description_verified'):
                row_status(e,'verified',None)
            elif r.get('error'):
                row_status(e,'blocked',r['error'])
            elif report.get('avito_error'):
                read_failure(e,report['avito_error'])
            elif r.get('feed_verified') or e['status'] in ('submitted','processing'):
                n=e.get('polls',0)+1;e['polls']=n
                defer(e,'processing' if n<MAX_POLLS else 'blocked',
                      'Feed matched; live description not verified' if r.get('feed_verified') else 'Uncertain feed write; verify before any resend',30)
            elif not master_ok(master,sku,'avito') and apply:
                row_status(e,'blocked','Canonical workbook description not verified')
            else:
                row_status(e,'validated',None)
            e['evidence']=str((report_dir/'avito'/str(offset)/'summary.json').relative_to(ROOT))
        save([report_dir/'avito'/str(offset)/'summary.json'])

def held_ozon_readback(rows,rules):
    """Ozon hold forbids writes, not verification. Re-read exact paused targets
    so that descriptions updated in the seller UI or safe isolated probes
    become verified without removing the write hold or replaying anything."""
    return {sku:chs['ozon'] for sku,chs in rows.items()
            if chs['ozon'].get('card_id') and chs['ozon'].get('status')=='blocked'
            and chs['ozon'].get('error','').startswith('Ozon description-only requests')
            and rules.get(sku,{}).get('text',{}).get('ozon',{}).get('description')}

def verify_held_ozon(entries,rules,context,save):
    if not entries:return
    try:
        with guard([],allow_write=False,descriptions_only=True):
            snapshot=read_api('ozon',list(entries),context)
        for sku,e in entries.items():
            try:
                # Never discard a protected-field failure or alter the hold
                # based on mere matching text.
                if e.get('protected_hash'):continue
                card,_=api.description_card('ozon',snapshot,sku)
                if identity('ozon',card,None)!=e['card_id']:continue
                e['last_checked_utc']=stamp()
                if api.description_value('ozon',card)==rules[sku]['text']['ozon']['description']:
                    row_status(e,'verified',None,verification_basis='already_matching_live_text_no_write',
                               after_hash=digest(rules[sku]['text']['ozon']['description']),protection_verified=False)
            except Exception:continue
        save()
    except subprocess.CalledProcessError:raise
    except RuntimeError:raise
    except Exception as exc:
        print('Paused Ozon verification failed: '+str(exc)[:200])

def followup_entries(rows, ch, rules):
    """A read-only sweep of accepted writes after independent work has run.
    Never include a prepared, rejected, blocked, or already verified card."""
    return {sku: channels[ch] for sku,channels in rows.items()
            if channels[ch].get('status') in ('submitted','processing')
            and channels[ch].get('card_id')
            and rules.get(sku,{}).get('text',{}).get(ch,{}).get('description')}

def pending_evidence(state, ids, snapshots, master_rows, duplicates, inventory_errors):
    """Compact review queue for live identities without reviewed copy.
    Do not assign identities by normalized SKU or titles."""
    rows={}
    for sku, channels in state['rows'].items():
        need=[ch for ch,e in channels.items() if e.get('status')=='pending' and e.get('card_id')]
        if not need:continue
        record={'master_found':sku in master_rows,'master_duplicate':sku in duplicates,
                'master_description':str(master_rows.get(sku,{}).get('description') or '')[:2400],
                'channels':{}}
        for ch in need:
            e=channels[ch]
            matches=ids.get(ch,{}).get(sku,[])
            record['channels'][ch]={'id':e['card_id'],'matches':matches,'error':None}
        rows[sku]=record
    for ch in ('ozon','market','wb','tilda'):
        wanted=[s for s,r in rows.items() if ch in r['channels']]
        if not wanted or ch in inventory_errors:continue
        try:
            if ch=='tilda':
                cards=snapshots[ch]
                for s in wanted:
                    item=cards.get(rows[s]['channels'][ch]['id'],{})
                    rows[s]['channels'][ch].update(title=item.get('title'),description=str(item.get('text') or '')[:2400])
                continue
            if ch=='wb':
                snapshot=snapshots[ch]
            else:
                with guard([],allow_write=False,descriptions_only=True):
                    snapshot=read_api(ch,wanted,snapshots.get(ch))
            for sku in wanted:
                entry=rows[sku]['channels'][ch]
                try:
                    card,detail=api.description_card(ch,snapshot,sku)
                    if identity(ch,card,detail)!=entry['id']:
                        raise ValueError('Evidence identity differs from census')
                    entry.update(title=card.get('title') or card.get('name'),
                                 description=str(api.description_value(ch,card))[:2400])
                except Exception as exc:entry['error']=str(exc)[:300]
        except Exception as exc:
            for sku in wanted:rows[sku]['channels'][ch]['error']=str(exc)[:300]
    return {'version':1,'checked_utc':stamp(),'rows':rows}

def run(live=False):
    cfg=read_json(CONFIG,{})
    if not cfg.get('enabled'):
        print('Pipeline disabled; no seller access');return 0
    if cfg.get('version')!=1:raise ValueError('Unknown request version')
    if cfg.get('mode') not in ('verify','apply'):raise ValueError('Explicit verify/apply mode required')
    apply=cfg['mode']=='apply'
    if apply and (not live or os.getenv('GITHUB_REF')!='refs/heads/main'):
        raise ValueError('Apply requires main with persistent checkpoints')
    if live:current_main()
    rules=cmf.load()
    old=read_json(STATE,{'version':1,'rows':{},'paused_channels':{}})
    if old.get('version')!=1:raise ValueError('Unknown state version')
    ids,errors,extra,snaps=inventory()
    _,master_rows,duplicates=cms.read_rows((ROOT/'data/master_control.xlsx').read_bytes(),'Товары',12)
    universe=sorted(set(master_rows)|set(rules)|set().union(*(set(x) for x in ids.values())))
    run_id=os.getenv('GITHUB_RUN_ID','local')+'-'+os.getenv('GITHUB_RUN_ATTEMPT','1')
    report_dir=BASE/'runs'/run_id
    policy=read_json(ROOT/'data/content_description_policy.json',{})
    paused={**old.get('paused_channels',{}),**policy.get('paused_writes',{})}
    legacy_hold=policy.get('ozon',{}).get('legacy_hold_reason')
    resume=policy.get('ozon',{}).get('guarded_resume') is True and bool(legacy_hold)
    if resume and old.get('paused_channels',{}).get('ozon') not in (None,legacy_hold):
        paused['ozon']=old['paused_channels']['ozon'] # preserve any NEW safety stop
    elif resume and paused.get('ozon')==legacy_hold:
        paused.pop('ozon',None)
    state={'version':1,'rows':{},'paused_channels':paused,'updated_utc':stamp(),'run_id':run_id,'inventory_errors':errors,'inventory_extra':extra,'master_count':len(master_rows),'inventory_counts':{ch:sum(len(v) for v in ids[ch].values()) for ch in CHANNELS}}
    for sku in universe:
        state['rows'][sku]={}
        for ch in CHANNELS:
            target=rules.get(sku,{}).get('text',{}).get(ch,{}).get('description')
            matches=ids[ch].get(sku,[])
            prev=old.get('rows',{}).get(sku,{}).get(ch)
            if ch in errors:
                e=copy.deepcopy(prev) if prev else reconcile(None,sku,ch,None,target)
                if e['status']!='verified':read_failure(e,errors[ch])
            else:
                identity_value=matches[0]['id'] if len(matches)==1 else None
                e=reconcile(prev,sku,ch,identity_value,target)
                expected=rules.get(sku,{}).get('ids',{}).get(ch)
                if len(matches)>1 or (expected is not None and identity_value is not None and str(expected)!=identity_value):
                    row_status(e,'blocked','Ambiguous or mismatched live identity')
                elif not matches:
                    if ch in ('avito','tilda') or expected or (prev and prev.get('card_id')):
                        row_status(e,'blocked','No exact active mapping in observed inventory; absence not confirmed')
                    else:row_status(e,'not_applicable','No exact active SKU in seller census')
                elif (ch,sku) in RETIRED:
                    row_status(e,'blocked',RETIRED[ch,sku])
                elif not target:
                    row_status(e,'pending','No reviewed target description')
                elif sku not in master_rows or sku in duplicates:
                    row_status(e,'blocked','Missing or ambiguous master SKU; no automatic creation')
                elif ch=='ozon' and resume and ch not in paused and e['status']=='blocked' and e.get('error')==legacy_hold and not e.get('attempts'):
                    row_status(e,'prepared',None,next_attempt_utc=None)
                elif e['status']=='not_applicable' or (e['status']=='blocked' and e.get('error') in ('No exact active mapping in observed inventory; absence not confirmed','Missing or ambiguous master SKU; no automatic creation','Canonical workbook description not verified for this SKU/channel','Canonical workbook description not verified')):
                    row_status(e,'prepared',None)
            state['rows'][sku][ch]=e
    def save(more=()):
        state['updated_utc']=stamp()
        state['counts']={ch:dict(Counter(v[ch]['status'] for v in state['rows'].values())) for ch in CHANNELS}
        state['ozon_strict_coverage']=ozon_strict_coverage(state['rows'])
        write_json(STATE,state);checkpoint([STATE,*more],live=live)
    selected=[s for s in universe if s in master_rows and s not in duplicates and any(rules.get(s,{}).get('text',{}).get(c,{}).get('description') for c in CHANNELS)]
    master=materialize(rules,selected,live,apply,master_rules_for_census(rules,ids,errors))
    mp=report_dir/'master.json';write_json(mp,master)
    save([mp])
    for ch in CHANNELS:
        if ch in errors:continue
        if ch=='ozon' and ch in paused:
            verify_held_ozon(held_ozon_readback(state['rows'],rules),rules,snaps[ch],save)
        entries={s:v[ch] for s,v in state['rows'].items() if v[ch].get('card_id') and rules.get(s,{}).get('text',{}).get(ch,{}).get('description') and due(v[ch]) and (v[ch]['status'] not in TERMINAL or (ch=='tilda' and v[ch].get('error','') and 'UID-scoped CSV' in v[ch]['error']))}
        try:
            if ch in ('ozon','wb','market'):
                worker_api(ch,entries,rules,snaps[ch],paused,master,save,report_dir,apply)
            elif ch=='tilda':worker_tilda(entries,rules,snaps[ch],report_dir,save)
            else:worker_avito(entries,rules,master,report_dir,save,apply)
        except subprocess.CalledProcessError:
            # Lost git lease or unpersisted state: never continue seller writes.
            raise
        except RuntimeError as exc:
            if 'main advanced' in str(exc) or 'Workbook changed' in str(exc):raise
            for e in entries.values():
                if e['status'] not in TERMINAL:read_failure(e,str(exc)[:300])
            save()
        except Exception as exc:
            for e in entries.values():
                if e['status'] not in TERMINAL:read_failure(e,type(exc).__name__+': '+str(exc)[:300])
            save()
    # Give accepted WB/Market writes a second readback after the other queues
    # have had their turn. This is one read per channel, not per product or a
    # replay of the write batch. Later scheduled runs keep the bounded polling.
    for ch in ('ozon','wb','market'):
        if ch in errors:continue
        entries=followup_entries(state['rows'],ch,rules)
        if entries:
            worker_api(ch,entries,rules,snaps[ch],paused,master,save,report_dir,False)
    evidence=pending_evidence(state,ids,snaps,master_rows,duplicates,errors)
    pending_path=BASE/'pending_review.json'
    write_json(pending_path,evidence)
    save([pending_path])
    print(json.dumps({'master_count':state['master_count'],'inventory_counts':state['inventory_counts'],'counts':state['counts'],'paused_channels':paused},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true')
    sys.exit(run(p.parse_args().live))
