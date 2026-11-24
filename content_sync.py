"""Publish approved content only; verify live state, including a no-write retry."""
import argparse
import base64
import copy
import datetime
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import time
from functools import lru_cache

import requests
from PIL import Image, ImageChops, ImageStat
from content_manifest import load, normalize_url, insert_second, unchanged_fields, market_offer_payload, market_parameters_for
from content_transport import guard

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'reports/content-sync'
REQUEST = ROOT / 'data/content_sync_request.json'
PICTURE_ALIASES = {}


def bind_market_images(offer):
    # Market's upload URLs are not public image URLs. Use the public CDN URL
    # returned by the same authenticated offer response for visual comparison.
    for item in offer.get('mediaFiles', {}).get('pictures', []):
        source, public = item.get('url'), item.get('marketUrl')
        if source and public and public.startswith('https://'):
            PICTURE_ALIASES[source] = public


def one(rows, key, sku):
    matched = [r for r in rows if str(r.get(key,'')) == str(sku)]
    if len(matched) != 1:
        raise ValueError(f'Expected one existing {key}={sku}, found {len(matched)}')
    return matched[0]


@lru_cache(maxsize=256)
def picture(url):
    url = PICTURE_ALIASES.get(url, url)
    r=requests.get(url,timeout=60);r.raise_for_status()
    if len(r.content)>30_000_000:raise ValueError('Image too large')
    with Image.open(io.BytesIO(r.content)) as im:
        im.load()
        return im.convert('RGB').resize((128,128),Image.Resampling.LANCZOS), im.width/im.height


def same_picture(a,b):
    if normalize_url(a)==normalize_url(b):return True
    x,ax=picture(a);y,ay=picture(b)
    if abs(ax-ay)>0.025:return False
    # CDN recompression is allowed; a different slide must not be treated as the same asset.
    return sum(ImageStat.Stat(ImageChops.difference(x,y)).mean)/3 < 3.0


def equal_gallery(a,b):
    return len(a)==len(b) and all(same_picture(x,y) for x,y in zip(a,b))


def ozon_gallery(row):
    return [row['primary_image']]+[x for x in row.get('images',[]) if normalize_url(x)!=normalize_url(row['primary_image'])]


def wb_gallery(row):
    return [p.get('big') or p.get('c516x688') or p.get('square') for p in row.get('photos',[])]


def capture(skus, include_aux=True, channels=None):
    import ozon_client as oz, wb_client as wb, yandex_market_client as ym
    from market_content import read_rows
    result={}
    def call(channel,fn):
        if channels is not None and channel not in channels: return
        try:result[channel]=fn()
        except Exception as exc:result[channel]={'error':str(exc)[:1000]}
    def ozon():
        info=oz.get_product_info_list(skus)
        ids=[int(r['sku'])for r in info if r.get('sku')]
        result={'cards':oz.get_product_names(skus),'info':info,'prices':oz.get_prices_and_stocks(skus),'stocks':oz.get_stocks(skus)}
        result['definitions']={}
        for card in result['cards']:
            cid,tid=card['description_category_id'],card['type_id']
            key=f'{cid}:{tid}'
            if key not in result['definitions']:
                result['definitions'][key]=oz.get_category_attributes(cid,tid)
        if ids:
            try:result['ratings']=oz._post('/v1/product/rating-by-sku',{'skus':ids})
            except Exception as exc:result['ratings_error']=str(exc)[:500]
        return result
    call('ozon',ozon)
    call('wb',lambda:{'cards':[c for c in wb.list_cards() if c.get('vendorCode') in skus],'prices':wb.get_prices()})
    def market():
        biz=ym.get_business_id()
        cards=read_rows(biz,'offer-cards',skus)
        definitions={}
        for cid in {71380208} | {r['mapping']['marketCategoryId'] for r in cards}:
            status,data=ym._request('POST',f'/v2/category/{cid}/parameters',params={'businessId':biz},json_body={})
            if status!=200 or data.get('status')!='OK':raise ValueError('Cannot read Market category dictionary')
            definitions[str(cid)]=data['result']
        return {'business_id':biz,'offers':read_rows(biz,'offer-mappings',skus),'cards':cards,'definitions':definitions}
    call('market',market)
    if include_aux:
        def tilda():
            rows=[];token=None;seen=set()
            for _ in range(50):
                params={'storepartuid':'419144347374','recid':'1928468643','size':100,'c':time.time()}
                if token:params['slice']=token
                r=requests.get('https://store.tildaapi.com/api/getproductslist/',params=params,timeout=60);r.raise_for_status();data=r.json()
                rows += [p for p in data.get('products',[]) if p.get('sku') in skus]
                token=data.get('nextslice')
                if not token:return rows
                if token in seen:raise ValueError('Repeated Tilda cursor')
                seen.add(token)
            raise ValueError('Tilda pagination incomplete')
        call('tilda',tilda)
        def avito():
            import avito_client as av
            ids={int(row.get('ids',{}).get('avito',0)) for sku,row in load().items() if sku in skus}
            data={'items':[r for r in av.list_all_items(statuses=['active','removed','old','blocked','rejected']) if int(r.get('id',0)) in ids]}
            for name,path in [('autoload_profile','/autoload/v2/profile'),('autoload_report','/autoload/v3/reports/last_completed_report')]:
                try:data[name]=av._request('GET',path)
                except Exception as exc:data[name]={'error':str(exc)[:600]}
            return data
        call('avito',avito)
    return result


def desired_gallery(current, rule):
    if not rule.get('second_slide'):return list(current)
    return insert_second(current, rule['second_slide']['url'], same_picture)


def apply_ozon(sku,rule,before):
    import ozon_client as oz
    b=one(before['cards'],'offer_id',sku)
    current=ozon_gallery(b);desired=desired_gallery(current,rule)
    if len(desired)>30:raise ValueError('Gallery exceeds limit')
    media_changed=not equal_gallery(current,desired)
    fields=rule.get('text',{}).get('ozon',{})
    desired_attrs={aid:fields[name] for name,aid in [('description',4191),('composition',4384)] if name in fields}
    def text_value(row,aid):
        return next((a.get('values',[{}])[0].get('value','') for a in row.get('attributes',[]) if a['id']==aid and a.get('values')), '')
    attrs=[{'id':aid,'complex_id':0,'values':[{'value':value}]} for aid,value in desired_attrs.items() if text_value(b,aid)!=value]
    changed=media_changed or bool(attrs)
    if media_changed:
        payload={'items':[{'offer_id':sku,'primary_image':current[0],'images':desired[1:],'color_image':b.get('color_image') or ''}]}
        response=oz._post('/v2/product/pictures/import',payload)
        if not response:raise ValueError('Ozon did not acknowledge media request')
    if attrs:
        response=oz.update_attributes([{'offer_id':sku,'attributes':attrs}])
        if not response:raise ValueError('Ozon did not acknowledge text request')
    after=b
    for attempt in range(18 if changed else 1):
        if changed:time.sleep(5)
        after=one(oz.get_product_names([sku]),'offer_id',sku)
        if equal_gallery(ozon_gallery(after),desired) and all(text_value(after,k)==v for k,v in desired_attrs.items()):break
    if not equal_gallery(ozon_gallery(after),desired) or not all(text_value(after,k)==v for k,v in desired_attrs.items()):raise ValueError('Ozon content still not verified')
    unchanged_fields(b,after,['name','description_category_id','type_id','height','depth','width','weight'])
    for key in ['attributes','complex_attributes']:
        keep=lambda values:[a for a in values if key!='attributes' or a['id'] not in desired_attrs]
        if sorted(keep(b.get(key,[])),key=lambda x:(x.get('complex_id',0),x['id'])) != sorted(keep(after.get(key,[])),key=lambda x:(x.get('complex_id',0),x['id'])):
            raise ValueError('Ozon unrelated attributes changed: '+key+'; before/after IDs '+str(sorted((x.get('complex_id',0),x['id']) for x in keep(b.get(key,[]))))+' / '+str(sorted((x.get('complex_id',0),x['id']) for x in keep(after.get(key,[])))))
    return {'changed':changed,'verified':True,'photos':len(desired),'cover_preserved':(not current or same_picture(current[0],ozon_gallery(after)[0])),'after':after}


def apply_wb(sku,rule,before):
    import wb_client as wb
    b=one(before['cards'],'vendorCode',sku)
    expected=rule.get('ids',{}).get('wb')
    if expected and b['nmID']!=expected:raise ValueError('WB identity mismatch')
    current=wb_gallery(b);desired=desired_gallery(current,rule);media_changed=not equal_gallery(current,desired)
    fields=rule.get('text',{}).get('wb',{})
    if set(fields)-{'title','description'}:raise ValueError('WB composition needs an explicit characteristic plan')
    characteristic_plan={p['id']:p['value']for p in rule.get('wb_characteristics',[])}
    characteristics=copy.deepcopy(b.get('characteristics',[]))
    for aid,value in characteristic_plan.items():
        existing=[p for p in characteristics if p['id']==aid]
        if len(existing)!=1:raise ValueError('Expected existing WB composition characteristic')
        existing[0]['value']=value
    char_changed=characteristics!=b.get('characteristics',[])
    text_changed=any(b.get(k)!=v for k,v in fields.items());changed=media_changed or text_changed or char_changed
    if len(desired)>30:raise ValueError('Gallery exceeds limit')
    if media_changed:
        media=desired+([b['video']] if b.get('video') else [])
        response=wb.update_media(b['nmID'],media)
        if response.get('error'):raise ValueError('WB rejected media: '+str(response))
    if text_changed or char_changed:
        row={k:copy.deepcopy(b[k]) for k in ['nmID','vendorCode','brand','title','description','dimensions','characteristics','sizes']}
        row['kizMarked']=b.get('kizMarked',False);row.update(fields);row['characteristics']=characteristics
        row['sizes']=[{k:v for k,v in s.items() if k in {'chrtID','techSize','wbSize','skus'}} for s in row['sizes']]
        response=wb.update_cards([row])
        if response.get('error'):raise ValueError('WB rejected text: '+str(response))
    after=b
    for attempt in range(12 if changed else 1):
        if changed:time.sleep(10)
        after=one(wb.list_cards(),'vendorCode',sku)
        if equal_gallery(wb_gallery(after),desired) and all(after.get(k)==v for k,v in fields.items()) and all(next((p['value']for p in after.get('characteristics',[])if p['id']==aid),None)==value for aid,value in characteristic_plan.items()):break
    if not equal_gallery(wb_gallery(after),desired) or not all(after.get(k)==v for k,v in fields.items()):raise ValueError('WB content still not verified')
    unchanged_fields(b,after,[k for k in ['title','description','brand','subjectID','dimensions','sizes','video'] if k not in fields])
    if sorted(characteristics,key=lambda x:x['id'])!=sorted(after.get('characteristics',[]),key=lambda x:x['id']):raise ValueError('WB characteristics not verified')
    return {'changed':changed,'verified':True,'photos':len(desired),'cover_preserved':(not current or same_picture(current[0],wb_gallery(after)[0])),'after':after}


def apply_market(sku,rule,before):
    import yandex_market_client as ym
    from market_content import read_rows,grouped
    biz=before['business_id'];b=one([r['offer'] for r in before['offers']],'offerId',sku);card=one(before['cards'],'offerId',sku)
    bind_market_images(b)
    current=b.get('pictures',[]);desired=desired_gallery(current,rule)
    fields=rule.get('text',{}).get('market',{})
    expected={'name' if k=='title' else k:v for k,v in fields.items()}
    category,parameters=market_parameters_for(card,rule.get('market_parameters'),before.get('definitions',{}))
    params_changed=category!=card['mapping']['marketCategoryId'] or grouped(parameters)!=grouped(card.get('parameterValues',[]))
    media_changed=not equal_gallery(current,desired);changed=media_changed or params_changed or any(b.get(k)!=v for k,v in expected.items())
    if changed:
        row=market_offer_payload(b,card,pictures=desired if media_changed else None,text=fields)
        if params_changed:row['offer'].update(marketCategoryId=category,parameterValues=parameters)
        status,response=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':[row]})
        if status!=200 or response.get('status')!='OK' or any(x.get('errors') for x in response.get('results',[])):
            raise ValueError('Market rejected media: '+str(response))
    after=b
    after_card=card
    for attempt in range(12 if changed else 1):
        if changed:time.sleep(5)
        after=one([r['offer'] for r in read_rows(biz,'offer-mappings',[sku])],'offerId',sku)
        bind_market_images(after)
        after_card=one(read_rows(biz,'offer-cards',[sku]),'offerId',sku)
        if equal_gallery(after.get('pictures',[]),desired) and all(after.get(k)==v for k,v in expected.items()) and after_card['mapping']['marketCategoryId']==category and grouped(parameters)==grouped(after_card.get('parameterValues',[])):break
    if not equal_gallery(after.get('pictures',[]),desired) or not all(after.get(k)==v for k,v in expected.items()):raise ValueError('Market content still not verified')
    after_card=one(read_rows(biz,'offer-cards',[sku]),'offerId',sku)
    unchanged_fields(b,after,[k for k in ['name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions'] if k not in expected])
    if b.get('mediaFiles',{}).get('videos')!=after.get('mediaFiles',{}).get('videos'):raise ValueError('Market video changed')
    if grouped(parameters)!=grouped(after_card.get('parameterValues',[])):raise ValueError('Market typed characteristics not verified')
    if after_card['mapping']['marketCategoryId']!=category:raise ValueError('Market category not verified')
    return {'changed':changed,'verified':True,'photos':len(desired),'cover_preserved':(not current or same_picture(current[0],after['pictures'][0])),'after':after,'card_after':after_card,'rating':after_card.get('contentRating'),'rating_status':after_card.get('contentRatingStatus')}


def encrypt_report(result):
    from cryptography.hazmat.primitives import serialization,hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12)
    pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes())
    wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    enc=AESGCM(key).encrypt(nonce,json.dumps(result,ensure_ascii=False).encode(),None)
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'evidence.enc').write_text(json.dumps({k:base64.b64encode(v).decode() for k,v in {'key':wrapped,'nonce':nonce,'data':enc}.items()}))


def description_rules(rules, skus, channels=None):
    """Project approved rules onto description-only payloads; never copy media or attributes."""
    if not skus or len(set(skus)) != len(skus) or set(skus)-set(rules):
        raise ValueError('Explicit unique approved SKU list required')
    channels=('ozon','wb','market') if channels is None else channels
    if not channels or len(set(channels))!=len(channels) or set(channels)-{'ozon','wb','market'}:
        raise ValueError('Invalid API channel scope')
    selected = {}
    for sku in skus:
        original = rules[sku]
        text = {ch: {'description': fields['description']}
                for ch, fields in original.get('text', {}).items()
                if ch in channels and isinstance(fields.get('description'), str)
                and fields['description'].strip()}
        if not text:
            raise ValueError('No approved API description for '+sku)
        selected[sku] = {'text': text, 'ids': copy.deepcopy(original.get('ids', {}))}
    return selected



# Description batches reuse the established adapters and preserve the existing
# full-content path. Only the description is mutable in this mode.
DESCRIPTION_BATCH_SIZE = 10

def description_card(channel, snapshot, sku):
    if snapshot.get('error'):
        raise ValueError(snapshot['error'])
    if channel == 'ozon':
        return one(snapshot['cards'], 'offer_id', sku), None
    if channel == 'wb':
        return one(snapshot['cards'], 'vendorCode', sku), None
    return (one([r['offer'] for r in snapshot['offers']], 'offerId', sku),
            one(snapshot['cards'], 'offerId', sku))

def description_value(channel, card):
    if channel == 'ozon':
        return next((a['values'][0].get('value', '') for a in card.get('attributes', [])
                     if a['id'] == 4191 and a.get('values')), '')
    return card.get('description') or ''

def description_payload(channel, sku, rule, snapshot):
    card, detail = description_card(channel, snapshot, sku)
    desired = rule['text'][channel]['description']
    if channel == 'wb':
        expected = rule.get('ids', {}).get('wb')
        if expected is not None and str(card['nmID']) != str(expected):
            raise ValueError('WB identity mismatch')
    if description_value(channel, card) == desired:
        return None
    if channel == 'ozon':
        return {'offer_id': sku, 'attributes': [{'id': 4191, 'complex_id': 0,
                'values': [{'value': desired}]}]}
    if channel == 'wb':
        if not (0 < len(desired) <= 2000):
            raise ValueError('WB description length exceeds limit')
        payload = {k: copy.deepcopy(card[k]) for k in
                   ['nmID','vendorCode','brand','title','description','dimensions','characteristics','sizes']}
        payload['description'] = desired
        payload['kizMarked'] = card.get('kizMarked', False)
        payload['sizes'] = [{k:v for k,v in size.items() if k in
                             {'chrtID','techSize','wbSize','skus'}} for size in payload['sizes']]
        return payload
    return market_offer_payload(card, detail, text={'description': desired})

def read_description_channel(channel, skus, before):
    if channel == 'ozon':
        import ozon_client as oz
        return {'cards': oz.get_product_names(skus)}
    if channel == 'wb':
        import wb_client as wb
        wanted = set(skus)
        return {'cards': [r for r in wb.list_cards() if r.get('vendorCode') in wanted]}
    from market_content import read_rows
    biz = before['business_id']
    return {'business_id': biz, 'offers': read_rows(biz,'offer-mappings',skus),
            'cards': read_rows(biz,'offer-cards',skus)}

def send_description_batch(channel, payload, before):
    if channel == 'ozon':
        import ozon_client as oz
        reply = oz.update_attributes(payload)
        if not reply or reply.get('error'):
            raise ValueError('Ozon description request not acknowledged')
    elif channel == 'wb':
        import wb_client as wb
        reply = wb.update_cards(payload)
        if not isinstance(reply, dict) or reply.get('error'):
            raise ValueError('WB description request not acknowledged')
    else:
        import yandex_market_client as ym
        status, reply = ym._request('POST',f"/v2/businesses/{before['business_id']}/offer-mappings/update",
                                    json_body={'offerMappings':payload})
        if status != 200 or reply.get('status') != 'OK' or any(r.get('errors') for r in reply.get('results', [])):
            raise ValueError('Market description request not acknowledged')

def verify_description_preservation(channel, sku, old, new):
    b, bc = description_card(channel, old, sku)
    a, ac = description_card(channel, new, sku)
    if channel == 'ozon':
        unchanged_fields(b,a,['id','product_id','offer_id','name','description_category_id','type_id',
                              'height','depth','width','weight','color_image'])
        for key in ['attributes','complex_attributes']:
            def protected(row):
                return sorted([x for x in row.get(key,[]) if key != 'attributes' or x['id'] != 4191],
                              key=lambda x:(x.get('complex_id',0),x['id']))
            if protected(b) != protected(a):
                old_values,new_values=protected(b),protected(a)
                def group(items):
                    output={}
                    for item in items:
                        output.setdefault((item.get('complex_id',0),item['id']),[]).append(item)
                    return output
                old_group,new_group=group(old_values),group(new_values)
                changed=sorted(k for k in old_group.keys()|new_group.keys()
                               if old_group.get(k)!=new_group.get(k))
                raise ValueError('Protected Ozon attributes changed: '+key+'; IDs '+str(changed))
        first, last = ozon_gallery(b), ozon_gallery(a)
    elif channel == 'wb':
        unchanged_fields(b,a,['nmID','vendorCode','title','brand','subjectID','dimensions','sizes','video','kizMarked'])
        if sorted(b.get('characteristics',[]),key=lambda x:x['id']) != sorted(a.get('characteristics',[]),key=lambda x:x['id']):
            raise ValueError('Protected WB characteristics changed')
        first, last = wb_gallery(b), wb_gallery(a)
    else:
        from market_content import grouped
        unchanged_fields(b,a,['offerId','name','basicPrice','purchasePrice','vendor','vendorCode','barcodes',
                              'params','videos','weightDimensions'])
        if bc['mapping'] != ac['mapping'] or grouped(bc.get('parameterValues',[])) != grouped(ac.get('parameterValues',[])):
            raise ValueError('Protected Market mapping or parameters changed')
        if b.get('mediaFiles',{}).get('videos') != a.get('mediaFiles',{}).get('videos'):
            raise ValueError('Protected Market video changed')
        bind_market_images(b);bind_market_images(a)
        first,last=b.get('pictures',[]),a.get('pictures',[])
    if not equal_gallery(first,last):
        raise ValueError('Protected gallery changed')
    return a

def apply_description_batches(result, rules, skus, mode, paused_writes=None):
    paused_writes=paused_writes or {}
    for sku in skus:
        result['products'][sku] = {}
    for channel in ('ozon','wb','market'):
        targets = [s for s in skus if channel in rules[s]['text']]
        if not targets: continue
        before = result['before'][channel]
        stopped = False
        for offset in range(0,len(targets),DESCRIPTION_BATCH_SIZE):
            group = targets[offset:offset+DESCRIPTION_BATCH_SIZE]
            prepared, payload = {}, []
            for sku in group:
                try:
                    item = description_payload(channel,sku,rules[sku],before)
                    if item is not None and mode=='apply' and channel in paused_writes:
                        raise ValueError('Channel writes paused: '+paused_writes[channel])
                    prepared[sku] = item is not None
                    if item is not None: payload.append(item)
                except Exception as exc:
                    result['products'][sku][channel]={'verified':False,'error':str(exc)[:1500]}
            if not prepared:
                continue
            if mode == 'inspect':
                for sku,changed in prepared.items():
                    result['products'][sku][channel]={'changed':False,'needs_change':changed,'verified':not changed}
                continue
            if stopped and mode == 'apply':
                for sku in prepared:
                    result['products'][sku][channel]={'verified':False,'error':'Channel halted after protected-field change'}
                continue
            # Read a fresh group before sending full WB cards / retained Market
            # characteristics; do not overwrite a concurrent non-content edit.
            if mode == 'apply' and payload:
                try:
                    fresh=read_description_channel(channel,list(prepared),before)
                except Exception as exc:
                    for sku in prepared:
                        result['products'][sku][channel]={'verified':False,'error':'Prewrite read failed: '+str(exc)[:1000]}
                    continue
                payload=[]
                for sku in list(prepared):
                    try:
                        verify_description_preservation(channel,sku,before,fresh)
                        item=description_payload(channel,sku,rules[sku],fresh)
                        prepared[sku]=item is not None
                        if item is not None: payload.append(item)
                    except Exception as exc:
                        result['products'][sku][channel]={'verified':False,'error':'Prewrite drift: '+str(exc)[:1000]}
                        del prepared[sku]
                if not prepared: continue
            send_error = None
            if mode == 'apply' and payload:
                try: send_description_batch(channel,payload,before)
                except Exception as exc: send_error=str(exc)[:1000]
            # One shared readback for the entire group per attempt. Never resend
            # an uncertain write; accept only observed desired text and protection.
            pending = set(prepared)
            failures = {}
            for attempt in range(12 if mode == 'apply' and payload else 1):
                if attempt: time.sleep(10 if channel=='wb' else 5)
                try: after=read_description_channel(channel,list(prepared),before)
                except Exception as exc:
                    for sku in pending: failures[sku]=str(exc)[:1000]
                    continue
                for sku in list(pending):
                    try:
                        card=verify_description_preservation(channel,sku,before,after)
                    except Exception as exc:
                        failures[sku]=str(exc)[:1000]
                        # An incomplete read is not proof of collateral change.
                        # A specific preservation failure, however, stops the channel.
                        if 'Protected' in str(exc) or 'Unrelated fields changed' in str(exc):
                            stopped=True
                            result['products'][sku][channel]={'verified':False,'error':failures[sku]}
                            pending.remove(sku)
                        continue
                    if description_value(channel,card) == rules[sku]['text'][channel]['description']:
                        result['products'][sku][channel]={'verified':True,
                            'changed':bool(mode=='apply' and prepared[sku]),'cover_preserved':True,'after':card}
                        pending.remove(sku)
                    else: failures[sku]='Description not verified'
                if not pending: break
            for sku in pending:
                error=failures.get(sku,'No readback')
                if send_error: error+='; write acknowledgement: '+send_error
                result['products'][sku][channel]={'verified':False,'error':error[:1500]}
            encrypt_report(result)


def run(mode='inspect',only=None, descriptions_only=False, channels=None):
    if mode not in {'inspect','apply','verify'}:raise ValueError('Unknown mode')
    rules=load();skus=only or list(rules)
    if descriptions_only:
        rules=description_rules(rules, only, channels)
        channels=sorted({ch for row in rules.values() for ch in row['text']})
    if not skus or set(skus)-set(rules):raise ValueError('Only explicitly approved exact SKUs')
    result={'mode':mode,'descriptions_only':descriptions_only,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'revision':os.environ.get('GITHUB_SHA'),'writes':[],'products':{}}
    with guard(result['writes'],allow_write=(mode=='apply'),descriptions_only=descriptions_only):
        result['before']=capture(skus, include_aux=False, channels=channels) if descriptions_only else capture(skus)
        encrypt_report(result)
        if descriptions_only:
            policy_path=ROOT/'data/content_description_policy.json'
            paused=json.loads(policy_path.read_text()).get('paused_writes',{}) if policy_path.exists() else {}
            if not isinstance(paused,dict) or any(ch not in {'ozon','wb','market'} or not isinstance(reason,str) or not reason for ch,reason in paused.items()):
                raise ValueError('Invalid description write policy')
            apply_description_batches(result,rules,skus,mode,paused_writes=paused)
        elif mode!='inspect':
            for sku in skus:
                result['products'][sku]={}
                for channel,fn in [('ozon',apply_ozon),('wb',apply_wb),('market',apply_market)]:
                    if descriptions_only and channel not in rules[sku]['text']:
                        continue
                    try:
                        before=result['before'][channel]
                        if before.get('error'):raise ValueError(before['error'])
                        if mode=='verify':
                            # guard forbids any attempted mutation if the live state has drifted.
                            with guard([],allow_write=False):check=fn(sku,rules[sku],before)
                        else:check=fn(sku,rules[sku],before)
                        result['products'][sku][channel]=check
                    except Exception as exc:result['products'][sku][channel]={'verified':False,'error':str(exc)[:1500]}
                    encrypt_report(result)
        result['after']=capture(skus, include_aux=False, channels=channels) if descriptions_only else capture(skus)
        if descriptions_only and mode != 'inspect':
            # Final shared snapshot: do not count a transient intermediate match.
            for sku,channels in result['products'].items():
                for ch,entry in channels.items():
                    if not entry.get('verified'): continue
                    try:
                        card=verify_description_preservation(ch,sku,result['before'][ch],result['after'][ch])
                        if description_value(ch,card)!=rules[sku]['text'][ch]['description']:
                            raise ValueError('Description changed after initial readback')
                    except Exception as exc:
                        entry.update(verified=False,error='Final readback: '+str(exc)[:1000])
        # An unchanged stock value is not required: legitimate new orders can arrive.
        # Safety is enforced at transport level; raw before/after values are kept for review.
        result['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        encrypt_report(result)
    summary={'mode':mode,'descriptions_only':descriptions_only,'revision':result['revision'],'checked_at':result['finished_at'],'content_write_calls':len(result['writes']),'price_write_calls':0,'stock_write_calls':0,'products':{sku:{ch:{k:v for k,v in row.items() if k in {'verified','changed','needs_change','photos','cover_preserved','error','rating','rating_status'}} for ch,row in channels.items()} for sku,channels in result['products'].items()},'read_errors':{phase+':'+ch:v['error'] for phase in ['before','after'] for ch,v in result[phase].items() if isinstance(v,dict) and v.get('error')}}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False))
    return 1 if summary['read_errors'] or any(r.get('error') or (mode!='inspect' and not r.get('verified')) for c in summary['products'].values() for r in c.values()) else 0


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['inspect','apply','verify']);parser.add_argument('--article',default='');parser.add_argument('--descriptions-only',action='store_true');parser.add_argument('--channels',default='');args=parser.parse_args()
    request=json.loads(REQUEST.read_text()) if REQUEST.exists() else {}
    mode=args.mode or request.get('mode','inspect')
    if mode not in {'inspect','apply','verify'}:raise ValueError('Unknown mode')
    only=[x.strip() for x in args.article.split(',') if x.strip()] or request.get('skus')
    if args.descriptions_only and not args.article:
        raise ValueError('--descriptions-only requires an explicit --article list')
    if args.channels and not args.descriptions_only:
        raise ValueError('--channels is only for descriptions-only mode')
    return run(mode,only,descriptions_only=args.descriptions_only,
               channels=args.channels.split(',') if args.channels else None)


if __name__=='__main__':sys.exit(main())
