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
        out = {}
        for r in rows:
            if not r.get('archived',False) and not r.get('is_archived',False):
                add(out,r.get('offer_id'),r.get('product_id'))
        return out, None
    def wb():
        import wb_client
        cards = wb_client.list_cards()
        out = {}
        for r in cards: add(out,r.get('vendorCode'),r.get('nmID'),r.get('title'))
        return out, {'cards':cards}
    def market():
        import yandex_market_client as ym
        rows = ym.get_all_offers(archived=False); biz=ym.get_business_id()
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

def materialize(rules,skus,live,apply):
    result={}; changes={}
    for filename,(sheet,col,ch) in cms.BOOKS.items():
        path=ROOT/filename
        try:
            raw=path.read_bytes()
            new,rows=isolate_book(raw,sheet,col,ch,rules,skus)
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
                _,rows=isolate_book(path.read_bytes(),sheet,col,ch,rules,skus)
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
        obj={k:card.get(k) for k in ['id','product_id','offer_id','name','description_category_id','type_id','height','depth','width','weight','color_image']}
        obj['attributes']=sorted([x for x in card.get('attributes',[]) if x['id']!=4191],key=lambda x:(x.get('complex_id',0),x['id']))
        obj['complex_attributes']=card.get('complex_attributes',[])
        obj['gallery']=[cmf.normalize_url(x) for x in api.ozon_gallery(card)]
    return digest(obj)

def observe(entry,ch,snapshot,sku,target):
    card,detail=api.description_card(ch,snapshot,sku)
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
        row_status(entry,'verified',None,next_attempt_utc=None,protection_verified=True)
    return actual,fingerprint

def confirmed_rejection(audit):
    codes=[r.get('http_status') for r in audit]
    return bool(codes and all(c in (400,422,429) for c in codes))

def worker_api(ch,entries,rules,context,paused,master,save,report_dir,apply):
    if not entries:return
    # Read all due exact targets once. For WB this uses the existing paginated list.
    try:
        with guard([],allow_write=False,descriptions_only=True):
            snap=api.read_description_channel(ch,list(entries),context or {})
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
                row_status(e,'blocked' if ch in paused else 'validated',paused.get(ch),before_hash=digest(actual))
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
    # Every actual write has a persisted intent and an exact pre-write baseline.
    groups=[];batch=[]
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
                fresh=api.read_description_channel(ch,skus,context or {})
            for sku,_ in group:
                e=entries[sku]
                try:
                    api.verify_description_preservation(ch,sku,snap,fresh)
                    actual,fp=observe(e,ch,fresh,sku,rules[sku]['text'][ch]['description'])
                    if e['status']=='verified':continue
                    if fp!=e['protected_hash']:raise ValueError('Fresh protected baseline differs')
                    rule={'text':{ch:rules[sku]['text'][ch]},'ids':{'wb':e['card_id']} if ch=='wb' else {}}
                    payload=api.description_payload(ch,sku,rule,fresh)
                    if payload is not None:safe.append((sku,payload))
                except Exception as exc:row_status(e,'blocked','Prewrite drift: '+str(exc)[:300])
        except Exception as exc:
            for sku in skus:read_failure(entries[sku],'Prewrite read: '+str(exc)[:300])
        if not safe:save();continue
        for sku,_ in safe:
            e=entries[sku];row_status(e,'submitted',None,attempts=e['attempts']+1,polls=0,next_attempt_utc=None,evidence=str((report_dir/ch/str(seq)).relative_to(ROOT)))
        # The checkpoint itself is a lease on the current main; do not send if it fails.
        save();current_main()
        before=fresh;failure=None;after=None
        evidence={'before':before,'writes':audit,'products':{},'revision':git('rev-parse','HEAD')}
        old_out=api.OUT;api.OUT=report_dir/ch/str(seq)
        try:
            api.encrypt_report(evidence)
            try:
                with guard(audit,allow_write=True,descriptions_only=True):
                    api.send_description_batch(ch,[p for _,p in safe],context or {})
            except Exception as exc:failure=str(exc)[:300]
            try:
                with guard([],allow_write=False,descriptions_only=True):
                    after=api.read_description_channel(ch,[s for s,_ in safe],context or {})
            except Exception as exc:failure=(failure or '')+' Readback: '+str(exc)[:300]
            if after is not None:
                for sku,_ in safe:
                    e=entries[sku]
                    try:
                        api.verify_description_preservation(ch,sku,before,after)
                        observe(e,ch,after,sku,rules[sku]['text'][ch]['description'])
                    except Exception as exc:
                        if 'Protected' in str(exc) or 'Unrelated fields changed' in str(exc):
                            paused[ch]='Protected-field drift: '+sku
                            row_status(e,'blocked',str(exc)[:300])
                        else:
                            # Incomplete readback is not a successful write.
                            e['error']=str(exc)[:300]
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
    # At most one feed publication per run. All other operations are readback.
    for offset in range(0,len(entries),50):
        group=dict(list(entries.items())[offset:offset+50])
        eligible=[s for s,e in group.items() if apply and offset==0 and e['status'] not in ('submitted','processing') and master_ok(master,s,'avito') and e['attempts']<MAX_ATTEMPTS]
        mode='apply' if eligible and len(eligible)==len(group) else 'verify'
        if mode=='apply':
            for sku in eligible:
                e=group[sku];row_status(e,'submitted',None,attempts=e['attempts']+1)
            save();current_main()
        old=aux.OUT;aux.OUT=report_dir/'avito'/str(offset)
        try:
            aux.run(mode,list(group),['avito'])
            report=read_json(aux.OUT/'summary.json',{})
        finally:aux.OUT=old
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
            elif r.get('feed_verified'):
                if r.get('live_description_available'):
                    defer(e,'processing','Feed matched; live text not yet matched')
                else:
                    defer(e,'processing','Feed matched; public description not exposed; wait for fresh report')
            elif not master_ok(master,sku,'avito') and apply:
                row_status(e,'blocked','Canonical workbook description not verified')
            else:
                row_status(e,'validated',None)
            e['evidence']=str((report_dir/'avito'/str(offset)/'summary.json').relative_to(ROOT))
        save([report_dir/'avito'/str(offset)/'summary.json'])

def run(live=False):
    cfg=read_json(CONFIG,{})
    if not cfg.get('enabled'):
        print('Pipeline disabled; no seller access');return 0
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
                elif e['status']=='not_applicable':
                    row_status(e,'prepared',None)
            state['rows'][sku][ch]=e
    def save(more=()):
        state['updated_utc']=stamp()
        state['counts']={ch:dict(Counter(v[ch]['status'] for v in state['rows'].values())) for ch in CHANNELS}
        write_json(STATE,state);checkpoint([STATE,*more],live=live)
    selected=[s for s in universe if s in master_rows and s not in duplicates and any(rules.get(s,{}).get('text',{}).get(c,{}).get('description') for c in CHANNELS)]
    master=materialize(rules,selected,live,apply)
    mp=report_dir/'master.json';write_json(mp,master)
    save([mp])
    for ch in CHANNELS:
        if ch in errors:continue
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
    save()
    print(json.dumps({'master_count':state['master_count'],'inventory_counts':state['inventory_counts'],'counts':state['counts'],'paused_channels':paused},ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--live',action='store_true')
    sys.exit(run(p.parse_args().live))
