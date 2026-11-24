"""Final guarded work over existing description infrastructure.
Only owner-approved rich text values and existing Avito XML Description fields.
No seller product creation, price, stock, media, grouping, or status requests.
"""
import copy
import hashlib
import html
import json
import re
import time
from decimal import Decimal
from pathlib import Path
from xml.etree import ElementTree as ET

import requests
import content_pipeline as p
import ozon_description_guard as ozguard
from content_transport import guard

RICH=11254
RICH_STATE=p.BASE/'rich_state.json'
TOOLS_STATE=p.BASE/'avito_tools_description_state.json'
TOOLS_NAME='avito_tools_feed.xml'
TOOLS_URL='https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/'+TOOLS_NAME
H_FAMILY={'0am325025H','0am325025HX','0am325025HXE','0am325025HXF','0am325025HXG','0am325025HXK'}

def require(ok,message):
    if not ok:raise ValueError(message)

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def normalize(text):
    return re.sub(r'\s+',' ',str(text or '')).strip()

def exclude_uncreated(cfg,ids,extra):
    """Exclude only independently confirmed uncreated, non-public exact IDs."""
    import ozon_client as oz
    candidates=cfg.get('ozon_uncreated',{})
    try:
        with guard([],allow_write=False,descriptions_only=True):
            rows=oz.get_product_info_list(list(candidates))
        excluded={}
        for sku,pid in candidates.items():
            hits=[r for r in rows if r.get('offer_id')==sku and str(r.get('id') or r.get('product_id'))==str(pid)]
            if len(hits)!=1 or [r['id'] for r in ids['ozon'].get(sku,[])]!=[str(pid)]:continue
            row=hits[0]
            status=row.get('statuses') or {}
            model=row.get('model_info') or {}
            if (status.get('is_created') is False and row.get('sku')==0 and
                model.get('model_id')==0 and model.get('count')==0):
                excluded[sku]={'id':str(pid),'sku':0,'status':status,'model_info':model}
                ids['ozon'].pop(sku)
        extra['ozon_uncreated']=excluded
    except Exception as exc:
        extra['ozon_uncreated_read_error']=type(exc).__name__+': '+str(exc)[:160]

def rich_attr(card):
    attrs=[a for a in card.get('attributes',[]) if a.get('id')==RICH]
    require(len(attrs)==1 and len(attrs[0].get('values',[]))==1,'Unique existing rich attribute required')
    raw=attrs[0]['values'][0].get('value')
    require(isinstance(raw,str) and raw.strip(),'Existing rich document missing')
    doc=json.loads(raw)
    require(isinstance(doc,dict) and isinstance(doc.get('content'),list),'Unsupported rich document')
    return attrs,doc

def text_nodes(doc):
    """Only visible title/text content strings; never images, alt, links, or style."""
    out=[]
    def visit(w):
        require(isinstance(w,dict),'Unexpected rich block type')
        if 'blocks' in w:
            require(isinstance(w['blocks'],list),'Unexpected rich blocks')
            for b in w['blocks']:visit(b)
        if 'title' in w or 'text' in w:
            title=w.get('title')
            body=w.get('text')
            for obj in (title,body):
                if obj is None:continue
                require(isinstance(obj,dict) and isinstance(obj.get('content'),list)
                        and all(isinstance(s,str) for s in obj['content']),'Unsupported visible text')
                require(not any(re.search(r'https?://|href\s*=|<a\b|\{\{img:',s,re.I) for s in obj['content']),'Existing text contains a protected link')
            if body is not None and body['content'] and any(normalize(s) for s in body['content']):
                out.append((title,body))
            elif title is not None and any(normalize(s) for s in title['content']):
                raise ValueError('Title-only block needs separate review')
    for w in doc['content']:
        require(w.get('widgetName') in ('raShowcase','raTextBlock'),'Unsupported rich widget')
        visit(w)
    require(out,'No existing editable rich text blocks')
    return out

def structural(doc):
    value=copy.deepcopy(doc)
    for title,body in text_nodes(value):
        if title is not None:
            title['content']=['__TEXT__']*len(title['content'])
        body['content']=['__TEXT__']*len(body['content'])
    return value

def partition(text,n):
    """Keep original number of text values; split at sentence/word boundaries,
    retain every source word, no newly asserted product facts."""
    words=normalize(text).split()
    require(n>=1 and len(words)>=n,'Insufficient approved text for existing slots')
    if n==1:return [' '.join(words)]
    total=sum(len(w)+1 for w in words)
    out=[];start=0;used=0
    for i in range(1,n):
        left=n-i
        hi=len(words)-left
        target=total*i/n
        pos=start; running=used
        options=[]
        for end in range(start+1,hi+1):
            running+=len(words[end-1])+1
            # Prefer natural sentence/clause ends if reasonably balanced.
            boundary=words[end-1][-1:]
            penalty=0 if boundary in '.!?;' else (12 if boundary in ',:' else 36)
            options.append((abs(running-target)+penalty,end,running))
            if running>target+max(100,total/n):break
        _,pos,used=min(options)
        out.append(' '.join(words[start:pos]));start=pos
    out.append(' '.join(words[start:]))
    require(normalize(' '.join(out))==normalize(text),'Approved text lost during split')
    return out

def heading(text,first=False):
    low=text.lower()
    if first:
        first_line=text.split('\n')[0].split(' — ')[0].split('. ')[0].strip()
        return first_line if 8<=len(first_line)<=95 else 'О товаре'
    if 'комплектация' in low[:120] or 'в поставке' in low[:120] or 'в комплект' in low[:120]:return 'Комплектация'
    if 'подбор' in low[:120] or 'перед заказом' in low[:120] or 'сверьте' in low[:100]:return 'Подбор детали'
    if 'совместимость' in low[:100] or 'применяемость' in low[:100]:return 'Совместимость'
    if 'установ' in low[:100] or 'адаптац' in low[:100]:return 'Установка и адаптация'
    if 'провер' in low[:100] or 'диагност' in low[:100]:return 'Проверка и диагностика'
    if 'предназначен' in low[:100] or 'применяется' in low[:100]:return 'Назначение'
    return 'Сведения о товаре'

def plan_rich(sku,doc,description):
    out=copy.deepcopy(doc)
    groups=text_nodes(out)
    # For the flagship family retain the association of the existing images
    # with the corresponding approved technical sections.
    paras=description.split('\n\n')
    mapping=[]
    if sku in H_FAMILY and len(paras)==13 and len(groups) in (7,8):
        has_pick=any(t and 'как подобрать' in ' '.join(t['content']).lower() for t,b in groups)
        used=[]
        for idx,(t,b) in enumerate(groups):
            old=' '.join(t['content']).lower() if t else ''
            if idx==0:indices=[0];name=heading(paras[0],True)
            elif 'как подобрать' in old:indices=[9];name='Подбор электронной платы'
            elif 'что внутри' in old:indices=[1,2,7];name='Оригинальные компоненты и комплектация'
            elif 'проверка' in old:indices=[4,5,6];name='Стендовые и дорожные испытания'
            elif 'штоки' in old:indices=[3];name='Усовершенствованные комплектующие'
            elif 'установка' in old:indices=[11];name='Установка и адаптация'
            elif 'когда' in old:indices=[10];name='Назначение и диагностика'
            elif 'применяемость' in old:indices=[8,12] if has_pick else [8,9,12];name='Совместимость и подбор'
            else:break
            used.extend(indices)
            mapping.append((name,'\n\n'.join(paras[j] for j in indices)))
        if sorted(used)!=list(range(len(paras))) or len(mapping)!=len(groups):
            mapping=[]
    if not mapping:
        # Existing widget order is preserved. Every body word is copied from
        # the approved SKU-specific description, never from legacy claims.
        chunks=partition(description,len(groups))
        mapping=[(heading(v,i==0),v) for i,v in enumerate(chunks)]
    for (title,body),(name,chunk) in zip(groups,mapping):
        if title is not None and title['content']:
            require(len(title['content'])==1,'Multiple heading values need review')
            title['content']=[name]
        body['content']=partition(chunk,len(body['content']))
    require(structural(doc)==structural(out),'Rich structure, images, links, formatting changed')
    if sku not in H_FAMILY or not (len(paras)==13 and len(groups) in (7,8)):
        require(normalize(' '.join(' '.join(b['content']) for _,b in text_nodes(out)))==normalize(description),
                'Full approved description not preserved')
    return out

def protected(card,price,stock):
    """Capture exact field hashes, with previously tested Ozon service normalization.
    Rich is the only excluded mutable attribute; annotation is separately protected."""
    safe=copy.deepcopy(card)
    safe['attributes']=[a for a in safe.get('attributes',[]) if a.get('id')!=RICH]
    obj=ozguard.projection(safe,price)
    flat={('field:'+k):p.digest(v) for k,v in obj['fields'].items()}
    for key,val in obj.items():
        if key=='fields':continue
        if key=='attributes':
            by={}
            for a in val:by.setdefault(str(a.get('id')),[]).append(a)
            flat.update({'attribute:'+k:p.digest(v) for k,v in by.items()})
        else:flat[key]=p.digest(val)
    flat['annotation']=p.digest(p.api.description_value('ozon',card))
    require(stock is not None and 'stocks' in stock,'Exact stock snapshot unavailable')
    rows=stock.get('stocks')
    require(isinstance(rows,list),'Unsupported stock response')
    flat['stocks']=p.digest(sorted(rows,key=ozguard.canonical))
    return flat

def read_rich(skus):
    import ozon_client as oz
    with guard([],allow_write=False,descriptions_only=True):
        snap=p.read_api('ozon',skus,None)
        stocks=oz.get_stocks(skus)
    return snap,stocks

def rich_snapshot(snap,stocks,sku,pid):
    card,price=p.card_detail('ozon',snap,sku)
    require(p.identity('ozon',card,price)==str(pid),'Rich target product ID differs')
    ozguard.model_identity(card)
    stock=p.api.one(stocks,'offer_id',sku)
    attrs,doc=rich_attr(card)
    return attrs,doc,protected(card,price,stock)

def rich_write(entries):
    import ozon_client as oz
    payload=[]
    for sku,e in entries:
        before=e['original_rich'];after=e['target_rich']
        require(structural(before)==structural(after),'Prewrite rich nontext mismatch')
        payload.append({'offer_id':sku,'attributes':[{'id':RICH,'complex_id':0,
                        'values':[{'dictionary_value_id':0,'value':json.dumps(after,ensure_ascii=False)}]}]})
    require(payload and all(len(x['attributes'])==1 and x['attributes'][0]['id']==RICH for x in payload),
            'Only existing rich attribute allowed')
    # The transport independently rejects unknown and commercial endpoints.
    with guard([],allow_write=True,descriptions_only=False):
        reply=oz._post('/v1/product/attributes/update',{'items':payload},retries=1)
    return reply.get('task_id') or (reply.get('result') or {}).get('task_id')

def persist_verified_source(sku,entry,paths):
    source=p.ROOT/'data'/'ozon_rich'/(sku+'.json')
    source.parent.mkdir(parents=True,exist_ok=True)
    # Use exact live visual URLs, never substitute current gallery indices.
    source.write_text(json.dumps(entry['target_rich'],ensure_ascii=False,indent=1)+'\n',encoding='utf-8')
    cache_path=p.ROOT/'data'/'ozon_rich_cache.json'
    cache=p.read_json(cache_path,{})
    cache[sku]=sha(json.dumps(entry['target_rich'],ensure_ascii=False).encode())[:16]
    p.write_json(cache_path,cache)
    paths.extend([source,cache_path])

def run_rich(cfg,state,rules,ids,errors,save,apply):
    q=cfg.get('rich_text_update') or {}
    if q.get('version')!=1 or q.get('owner_authorized') is not True or 'ozon' in errors:return
    journal=p.read_json(RICH_STATE,{'version':1,'rows':{},'pause':None})
    rows=journal['rows']
    def persist(paths=()):
        journal['updated_utc']=p.stamp()
        journal['counts']={}
        for s in q['targets']:
            k=rows.get(s,{}).get('status','pending')
            journal['counts'][k]=journal['counts'].get(k,0)+1
        p.write_json(RICH_STATE,journal)
        save([RICH_STATE,*paths])
    todo=[s for s in q['targets'] if rows.get(s,{}).get('status') not in ('verified','blocked')]
    if not todo:return
    # Read submitted work first, never send it again without proof of rejection.
    try:snap,stocks=read_rich(todo)
    except Exception as exc:
        journal['read_error']=str(exc)[:260];persist();return
    paths=[]
    for sku in todo:
        t=q['targets'][sku]
        e=rows.setdefault(sku,{'status':'pending','product_id':str(t['product_id']),'attempts':0})
        try:
            require([v['id'] for v in ids['ozon'].get(sku,[])]==[str(t['product_id'])],'Exact live SKU not confirmed')
            attrs,doc,fp=rich_snapshot(snap,stocks,sku,t['product_id'])
            if e.get('status') in ('submitted','processing'):
                changed=sorted(k for k in fp.keys()|e['protected'].keys() if fp.get(k)!=e['protected'].get(k))
                if changed:
                    e.update(status='blocked',error='Protected field drift',changed_fields=changed)
                    journal['pause']='Protected drift: '+sku
                elif doc==e['target_rich']:
                    e['matching_reads']=e.get('matching_reads',0)+1
                    if e['matching_reads']>=2:
                        e.update(status='verified',error=None,verified_utc=p.stamp(),public_verified=False)
                        persist_verified_source(sku,e,paths)
                else:
                    e.update(status='processing',matching_reads=0,error='Awaiting exact rich readback')
                continue
            if t.get('append_compatibility'):
                require(sku=='0am325025H' and t.get('prior_verified_rich_hash') and p.digest(doc)==t['prior_verified_rich_hash'], 'Exact previously verified rich document changed; do not overwrite')
            else:
                require(p.digest(attrs)==t['source_hash'],'Live rich changed after approved audit; do not overwrite')
            target=rules[sku]['text']['ozon']['description']
            require(p.api.description_value('ozon',p.card_detail('ozon',snap,sku)[0])==target,
                    'Annotation differs from canonical text; complete ordinary queue first')
            if t.get('append_compatibility'):
                sentence=t['append_compatibility']
                require(sku=='0am325025H' and target.count(sentence)==1,'Exact approved compatibility sentence required')
                desired=copy.deepcopy(doc)
                slots=[(body,i) for _,body in text_nodes(desired) for i,v in enumerate(body['content']) if v.startswith('Совместимость Семиступенчатая')]
                require(len(slots)==1,'Unique existing compatibility text slot required')
                body,i=slots[0]
                body['content'][i]=body['content'][i].replace('Совместимость Семиступенчатая','Совместимость '+sentence+' Семиступенчатая',1)
                require(structural(doc)==structural(desired),'Rich structure changed')
            else:
                desired=plan_rich(sku,doc,target)
            e.update(status='prepared',error=None,original_rich=doc,target_rich=desired,protected=fp,
                     original_hash=p.digest(doc),target_hash=p.digest(desired),canonical_description_hash=p.digest(target))
            if doc==desired:
                e.update(status='verified',verified_utc=p.stamp(),public_verified=False,matching_reads=2)
                persist_verified_source(sku,e,paths)
        except Exception as exc:
            e.update(status='blocked',error=str(exc)[:260])
    persist(paths)
    if not apply or journal.get('pause') or state.get('paused_channels',{}).get('ozon'):return
    if any(e.get('status') in ('submitted','processing') for e in rows.values()):return
    canary=q['canary']
    # The first exact guarded publication must be verified before batching.
    pending=[s for s in todo if rows[s]['status']=='prepared']
    if rows.get(canary,{}).get('status')!='verified':
        pending=[canary] if canary in pending else []
    while pending:
        group=pending[:1] if rows.get(canary,{}).get('status')!='verified' else pending[:8]
        pending=pending[len(group):]
        before,st=read_rich(group)
        safe=[]
        for sku in group:
            e=rows[sku]
            try:
                _,doc,fp=rich_snapshot(before,st,sku,e['product_id'])
                require(doc==e['original_rich'] and fp==e['protected'],'Fresh prewrite baseline changed')
                safe.append((sku,e))
            except Exception as exc:e.update(status='blocked',error=str(exc)[:260])
        if not safe:persist();continue
        for sku,e in safe:e.update(status='submitted',attempts=e['attempts']+1,submitted_utc=p.stamp(),matching_reads=0)
        persist();p.current_main()
        try:
            task=rich_write(safe)
            for _,e in safe:e['task_id']=task
        except Exception as exc:
            for _,e in safe:e.update(status='processing',error='Uncertain write: '+str(exc)[:220])
            persist();return
        persist()
        import ozon_client as oz
        for _ in range(12):
            time.sleep(5)
            try:
                with guard([],allow_write=False,descriptions_only=True):
                    status=oz.get_import_status(int(task)) if task else {}
                after,stock_after=read_rich([s for s,_ in safe])
            except Exception:continue
            paths=[]
            for sku,e in safe:
                if e['status'] in ('verified','blocked'):continue
                try:
                    _,doc,fp=rich_snapshot(after,stock_after,sku,e['product_id'])
                    changed=sorted(k for k in fp.keys()|e['protected'].keys() if fp.get(k)!=e['protected'].get(k))
                    if changed:
                        e.update(status='blocked',error='Protected field drift',changed_fields=changed)
                        journal['pause']='Protected drift: '+sku
                        continue
                    if doc==e['target_rich']:
                        e['matching_reads']=e.get('matching_reads',0)+1
                        if e['matching_reads']>=2:
                            e.update(status='verified',error=None,verified_utc=p.stamp(),public_verified=False)
                            persist_verified_source(sku,e,paths)
                    else:e.update(status='processing',matching_reads=0,error='Awaiting exact rich readback')
                    items=(status.get('result') or {}).get('items') or status.get('items') or []
                    for r in items:
                        if r.get('offer_id')==sku and (r.get('errors') or r.get('status') in ('failed','error')):
                            e.update(status='blocked',error='Ozon rejected rich',task_status=r.get('status'),task_errors=r.get('errors',[]))
                except Exception as exc:e.update(status='processing',error=str(exc)[:260])
            persist(paths)
            if all(e['status'] in ('verified','blocked') for _,e in safe) or journal.get('pause'):break
        if journal.get('pause') or any(e['status']!='verified' for _,e in safe):return
        if canary in group:
            # Continue immediately with other already-prepared exact targets.
            pending=[s for s in todo if rows[s]['status']=='prepared']

def get_tool_asset():
    from scripts.apply_dq200_prices_20261120 import gh
    release=gh('GET','/releases/tags/product-photos').json()
    hits=[a for a in release.get('assets',[]) if a.get('name')==TOOLS_NAME]
    require(len(hits)==1,'Published tool XML is not unique')
    asset=hits[0]
    r=requests.get(asset['browser_download_url'],params={'description_check':str(time.time_ns())},timeout=45)
    r.raise_for_status()
    if asset.get('digest'):require(asset['digest']=='sha256:'+sha(r.content),'Tool release digest mismatch')
    return release,asset,r.content

AD_PATTERN=re.compile(rb'<Ad(?:\s[^>]*)?>.*?</Ad>',re.S)
DESC_PATTERN=re.compile(rb'<Description(?:\s[^>]*)?>.*?</Description>',re.S)

def patch_tools(raw,targets,rules,live,report):
    root=ET.fromstring(raw)
    require(root.tag=='Ads','Unexpected tool XML root')
    seen=set();changes={}
    def edit(match):
        blob=match.group(0)
        ad=ET.fromstring(blob)
        sku=ad.findtext('Id')
        if sku not in targets:return blob
        require(sku not in seen,'Duplicate exact tool ID')
        seen.add(sku)
        aid=str(targets[sku])
        hits=[r for r in report if r.get('ad_id')==sku and str(r.get('avito_id'))==aid]
        require(len(hits)==1,'Exact tool mapping missing or ambiguous')
        cards=[r for r in live if str(r.get('id'))==aid and r.get('status')=='active']
        require(len(cards)==1,'Exact tool listing not active')
        card=cards[0]
        require(str(card.get('title'))==ad.findtext('Title'),'Live tool title differs from source')
        require(Decimal(str(card.get('price')))==Decimal(ad.findtext('Price')),'Live tool price differs from source')
        desired=rules[sku]['text']['avito']['description']
        require(isinstance(desired,str) and 0<len(desired)<=7500,'Invalid tool description')
        require(len(DESC_PATTERN.findall(blob))==1,'Unique existing Description required')
        old=ad.findtext('Description') or ''
        if old==desired:
            changes[sku]={'avito_id':aid,'source_matches':True,'changed':False,'live_text_matches':isinstance(card.get('description'),str) and p.aux.normalized(card['description'])==p.aux.normalized(desired)}
            return blob
        value=('<Description>'+html.escape(desired,quote=False)+'</Description>').encode()
        new=DESC_PATTERN.sub(lambda _:value,blob,count=1)
        require(ET.fromstring(new).findtext('Description')==desired,'XML text roundtrip failed')
        require(DESC_PATTERN.sub(b'<Description/>',new)==DESC_PATTERN.sub(b'<Description/>',blob),'Other tool fields changed')
        changes[sku]={'avito_id':aid,'source_matches':True,'changed':True,'live_text_matches':False}
        return new
    staged=AD_PATTERN.sub(edit,raw)
    require(seen==set(targets),'Existing XML does not contain both approved tool IDs')
    require(DESC_PATTERN.sub(b'<Description/>',raw)==DESC_PATTERN.sub(b'<Description/>',staged),'Non-description XML bytes changed')
    require(len(ET.fromstring(staged))==len(root),'XML listing count changed')
    return staged,changes

def run_tools(cfg,rules,save,apply):
    q=cfg.get('avito_tools_description_update') or {}
    if q.get('version')!=1:return
    previous=p.read_json(TOOLS_STATE,{})
    if previous.get('status')=='published_pending_live_verification':
        # Never republish an identical source just because public reads are blocked.
        return
    if previous.get('status')=='verified':return
    import avito_client as av
    from scripts.apply_dq200_prices_20261120 import get_avito_asset,gh,rename_asset
    from scripts.publish_avito_titles_20261121 import verified_download
    result={'checked_utc':p.stamp(),'seller_price_writes':0,'stock_writes':0}
    def persist():
        p.write_json(TOOLS_STATE,result);save([TOOLS_STATE])
    try:
        targets=q['targets']
        profile=av._request('GET','/autoload/v2/profile',retries=1)
        feeds=[f.get('feed_url') for f in profile.get('feeds_data',[])]
        require(profile.get('autoload_enabled') is True and feeds.count(TOOLS_URL)==1,'Exact enabled tools feed not registered')
        main_release,main_asset,main_raw=get_avito_asset()
        require(main_raw==(p.ROOT/'data/avito_feed.xlsx').read_bytes(),'Main feed differs from canonical source')
        release,asset,raw=get_tool_asset()
        live=av.list_all_items(statuses=['active'])
        report=p.aux.read_avito_autoload(av)['items']
        staged,changes=patch_tools(raw,targets,rules,live,report)
        result.update(items=changes,before_sha256=sha(raw),target_sha256=sha(staged),primary_feed_sha256=sha(main_raw),
                      existing_asset_id=asset['id'],status='prepared')
        if raw==staged:
            result['status']='verified' if all(x['live_text_matches'] for x in changes.values()) else 'published_pending_live_verification'
            persist();return
        persist()
        if not apply:return
        p.current_main()
        stage_name='avito_tools_feed.description_'+sha(staged)[:18]+'.xml'
        hits=[a for a in release.get('assets',[]) if a.get('name')==stage_name]
        require(len(hits)<=1,'Duplicate staged XML')
        stage=hits[0] if hits else gh('POST',release['upload_url'].split('{',1)[0],params={'name':stage_name},
                    data=staged,headers={'Content-Type':'application/xml'}).json()
        verified_download(stage['browser_download_url'],staged)
        _,old,latest=get_tool_asset()
        require(old['id']==asset['id'] and latest==raw,'Tools XML changed concurrently')
        _,main_check,main_after=get_avito_asset()
        require(main_check['id']==main_asset['id'] and main_after==main_raw,'Main feed changed concurrently')
        p.current_main()
        backup='avito_tools_feed.before_description_'+str(old['id'])+'.xml'
        result.update(status='publication_intent',backup=backup,stage_asset=stage['id'])
        persist()
        p.current_main()
        rename_asset(old['id'],backup)
        try:rename_asset(stage['id'],TOOLS_NAME)
        except Exception:
            rename_asset(old['id'],TOOLS_NAME)
            raise
        verified_download(TOOLS_URL,staged)
        _,_,main_after=get_avito_asset()
        require(main_after==main_raw,'Main feed bytes changed concurrently')
        result.update(status='published_pending_live_verification',published_utc=p.stamp(),published_sha256=sha(staged),
                      primary_feed_unchanged=True)
        persist()
    except Exception as exc:
        result.update(status='blocked',error=type(exc).__name__+': '+str(exc)[:280])
        persist()

def finish(cfg,state,rules,ids,errors,save,apply):
    # The two queues are independent. Lost branch lease must propagate.
    try:run_rich(cfg,state,rules,ids,errors,save,apply)
    except (ValueError,requests.RequestException) as exc:
        path=p.BASE/'rich_completion_error.json'
        p.write_json(path,{'checked_utc':p.stamp(),'error':str(exc)[:260]});save([path])
    try:run_tools(cfg,rules,save,apply)
    except (ValueError,requests.RequestException) as exc:
        path=p.BASE/'avito_tools_completion_error.json'
        p.write_json(path,{'checked_utc':p.stamp(),'error':str(exc)[:260]});save([path])
