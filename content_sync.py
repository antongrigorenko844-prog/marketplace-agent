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
from content_manifest import load, normalize_url, insert_second, unchanged_fields, market_offer_payload
from content_transport import guard

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'reports/content-sync'
REQUEST = ROOT / 'data/content_sync_request.json'


def one(rows, key, sku):
    matched = [r for r in rows if str(r.get(key,'')) == str(sku)]
    if len(matched) != 1:
        raise ValueError(f'Expected one existing {key}={sku}, found {len(matched)}')
    return matched[0]


@lru_cache(maxsize=256)
def picture(url):
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


def capture(skus, include_aux=True):
    import ozon_client as oz, wb_client as wb, yandex_market_client as ym
    from market_content import read_rows
    result={}
    def call(channel,fn):
        try:result[channel]=fn()
        except Exception as exc:result[channel]={'error':str(exc)[:1000]}
    call('ozon',lambda:{'cards':oz.get_product_names(skus),'info':oz.get_product_info_list(skus),'prices':oz.get_prices_and_stocks(skus),'stocks':oz.get_stocks(skus)})
    call('wb',lambda:{'cards':[c for c in wb.list_cards() if c.get('vendorCode') in skus],'prices':wb.get_prices()})
    def market():
        biz=ym.get_business_id()
        return {'business_id':biz,'offers':read_rows(biz,'offer-mappings',skus),'cards':read_rows(biz,'offer-cards',skus)}
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
            return {'items':[r for r in av.list_all_items(statuses=['active','removed','old','blocked','rejected']) if int(r.get('id',0)) in ids]}
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
            raise ValueError('Ozon unrelated attributes changed')
    return {'changed':changed,'verified':True,'photos':len(desired),'cover_preserved':same_picture(current[0],ozon_gallery(after)[0]),'after':after}


def apply_wb(sku,rule,before):
    import wb_client as wb
    b=one(before['cards'],'vendorCode',sku)
    expected=rule.get('ids',{}).get('wb')
    if expected and b['nmID']!=expected:raise ValueError('WB identity mismatch')
    current=wb_gallery(b);desired=desired_gallery(current,rule);media_changed=not equal_gallery(current,desired)
    fields=rule.get('text',{}).get('wb',{})
    if set(fields)-{'title','description'}:raise ValueError('WB composition needs an explicit characteristic plan')
    text_changed=any(b.get(k)!=v for k,v in fields.items());changed=media_changed or text_changed
    if len(desired)>30:raise ValueError('Gallery exceeds limit')
    if media_changed:
        media=desired+([b['video']] if b.get('video') else [])
        response=wb.update_media(b['nmID'],media)
        if response.get('error'):raise ValueError('WB rejected media: '+str(response))
    if text_changed:
        row={k:copy.deepcopy(b[k]) for k in ['nmID','vendorCode','brand','title','description','dimensions','characteristics','sizes']}
        row['kizMarked']=b.get('kizMarked',False);row.update(fields)
        row['sizes']=[{k:v for k,v in s.items() if k in {'chrtID','techSize','wbSize','skus'}} for s in row['sizes']]
        response=wb.update_cards([row])
        if response.get('error'):raise ValueError('WB rejected text: '+str(response))
    after=b
    for attempt in range(12 if changed else 1):
        if changed:time.sleep(10)
        after=one(wb.list_cards(),'vendorCode',sku)
        if equal_gallery(wb_gallery(after),desired) and all(after.get(k)==v for k,v in fields.items()):break
    if not equal_gallery(wb_gallery(after),desired) or not all(after.get(k)==v for k,v in fields.items()):raise ValueError('WB content still not verified')
    unchanged_fields(b,after,[k for k in ['title','description','brand','subjectID','dimensions','sizes','video'] if k not in fields])
    if sorted(b.get('characteristics',[]),key=lambda x:x['id'])!=sorted(after.get('characteristics',[]),key=lambda x:x['id']):raise ValueError('WB characteristics changed')
    return {'changed':changed,'verified':True,'photos':len(desired),'cover_preserved':same_picture(current[0],wb_gallery(after)[0]),'after':after}


def apply_market(sku,rule,before):
    import yandex_market_client as ym
    from market_content import read_rows,grouped
    biz=before['business_id'];b=one([r['offer'] for r in before['offers']],'offerId',sku);card=one(before['cards'],'offerId',sku)
    current=b.get('pictures',[]);desired=desired_gallery(current,rule)
    fields=rule.get('text',{}).get('market',{})
    expected={'name' if k=='title' else k:v for k,v in fields.items()}
    media_changed=not equal_gallery(current,desired);changed=media_changed or any(b.get(k)!=v for k,v in expected.items())
    if changed:
        row=market_offer_payload(b,card,pictures=desired if media_changed else None,text=fields)
        status,response=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':[row]})
        if status!=200 or response.get('status')!='OK' or any(x.get('errors') for x in response.get('results',[])):
            raise ValueError('Market rejected media: '+str(response))
    after=b
    for attempt in range(12 if changed else 1):
        if changed:time.sleep(5)
        after=one([r['offer'] for r in read_rows(biz,'offer-mappings',[sku])],'offerId',sku)
        if equal_gallery(after.get('pictures',[]),desired) and all(after.get(k)==v for k,v in expected.items()):break
    if not equal_gallery(after.get('pictures',[]),desired) or not all(after.get(k)==v for k,v in expected.items()):raise ValueError('Market content still not verified')
    after_card=one(read_rows(biz,'offer-cards',[sku]),'offerId',sku)
    unchanged_fields(b,after,[k for k in ['name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions'] if k not in expected])
    if b.get('mediaFiles',{}).get('videos')!=after.get('mediaFiles',{}).get('videos'):raise ValueError('Market video changed')
    if grouped(card.get('parameterValues',[]))!=grouped(after_card.get('parameterValues',[])):raise ValueError('Market typed characteristics changed')
    return {'changed':changed,'verified':True,'photos':len(desired),'cover_preserved':same_picture(current[0],after['pictures'][0]),'after':after,'card_after':after_card}


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


def run(mode='inspect',only=None):
    rules=load();skus=only or list(rules)
    if not skus or set(skus)-set(rules):raise ValueError('Only explicitly approved exact SKUs')
    result={'mode':mode,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'revision':os.environ.get('GITHUB_SHA'),'writes':[],'products':{}}
    with guard(result['writes'],allow_write=(mode=='apply')):
        result['before']=capture(skus)
        encrypt_report(result)
        if mode!='inspect':
            for sku in skus:
                result['products'][sku]={}
                for channel,fn in [('ozon',apply_ozon),('wb',apply_wb),('market',apply_market)]:
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
        result['after']=capture(skus)
        # An unchanged stock value is not required: legitimate new orders can arrive.
        # Safety is enforced at transport level; raw before/after values are kept for review.
        result['finished_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
        encrypt_report(result)
    summary={'mode':mode,'revision':result['revision'],'checked_at':result['finished_at'],'content_write_calls':len(result['writes']),'price_write_calls':0,'stock_write_calls':0,'products':{sku:{ch:{k:v for k,v in row.items() if k in {'verified','changed','photos','cover_preserved','error'}} for ch,row in channels.items()} for sku,channels in result['products'].items()},'read_errors':{phase+':'+ch:v['error'] for phase in ['before','after'] for ch,v in result[phase].items() if isinstance(v,dict) and v.get('error')}}
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
    print(json.dumps(summary,ensure_ascii=False))
    return 1 if summary['read_errors'] or any(not r.get('verified') for c in summary['products'].values() for r in c.values()) else 0


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--mode',choices=['inspect','apply','verify']);parser.add_argument('--article',default='');args=parser.parse_args()
    request=json.loads(REQUEST.read_text()) if REQUEST.exists() else {}
    mode=args.mode or request.get('mode','inspect')
    if mode not in {'inspect','apply','verify'}:raise ValueError('Unknown mode')
    only=[x.strip() for x in args.article.split(',') if x.strip()] or request.get('skus')
    return run(mode,only)


if __name__=='__main__':sys.exit(main())
