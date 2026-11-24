"""One-time exact seller/public read-only audit; no seller write operations."""
import html
import json
import re
from collections import Counter
from html.parser import HTMLParser
from urllib.parse import urlsplit
import requests
import content_pipeline as p
from content_transport import guard

class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip=0
        self.parts=[]
    def handle_starttag(self, tag, attrs):
        if tag in ('head','script','style','noscript','template'):self.skip+=1
    def handle_endtag(self, tag):
        if tag in ('head','script','style','noscript','template') and self.skip:self.skip-=1
    def handle_data(self, data):
        if not self.skip:self.parts.append(data)

def normalize(value):
    return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]+>',' ',str(value or '')))).strip()

def extract_public(url, target, expected_id, allowed_hosts, stopped):
    host=urlsplit(url).hostname
    if host not in allowed_hosts:return {'status':'no_verified_public_url'}
    if host in stopped:return {'status':'public_access_unavailable'}
    try:
        response=requests.get(url,timeout=12,headers={'User-Agent':'LEOPARTS description verification'},allow_redirects=True)
        final=urlsplit(response.url)
        if response.status_code in (401,403,429,451) or 'captcha' in final.path.lower():
            stopped.add(host);return {'status':'public_access_unavailable','http_status':response.status_code}
        if not response.ok:return {'status':'public_read_failed','http_status':response.status_code}
        if final.hostname not in allowed_hosts or str(expected_id) not in final.path:
            return {'status':'public_identity_unconfirmed','http_status':response.status_code}
        parser=VisibleText();parser.feed(response.text)
        actual=normalize(' '.join(parser.parts));wanted=normalize(target)
        if len(actual)<100 or re.search(r'подтвердите, что вы не робот|captcha|access denied',actual,re.I):
            stopped.add(host);return {'status':'public_access_unavailable','http_status':response.status_code}
        # Exact plain HTML text is evidence, not an independent rendered UI or
        # seller attribution proof. Absence is not proof of stale content.
        return {'status':'public_html_text_matches' if wanted and wanted in actual else 'public_full_text_not_observed',
                'http_status':response.status_code,'rendered_storefront_verified':False}
    except requests.RequestException as exc:
        stopped.add(host);return {'status':'public_read_failed','error_type':type(exc).__name__}

def audit(state,ids,errors,snaps,rules):
    out={'checked_utc':p.stamp(),'seller_writes':0,'rows':{},'inventory_errors':errors,'ozon_model_diagnostics':{},'ozon_rich':{}}
    stopped=set()
    for ch in ('ozon','wb','market'):
        if ch in errors:continue
        targets=[s for s,v in ids[ch].items() if len(v)==1 and s not in p.TEST_SKUS and rules.get(s,{}).get('text',{}).get(ch,{}).get('description')]
        # WB inventory already contains the seller's current full cards.
        try:
            with guard([],allow_write=False,descriptions_only=True):
                snapshot=snaps[ch] if ch=='wb' else p.read_api(ch,targets,snaps[ch])
        except Exception as exc:
            out['inventory_errors'][ch+'_audit']=type(exc).__name__+': '+str(exc)[:200];continue
        for sku in targets:
            expected=ids[ch][sku][0]['id'];target=rules[sku]['text'][ch]['description']
            result={'card_id':expected,'seller_text_matches':False,'storefront_verified':False}
            out['rows'].setdefault(sku,{})[ch]=result
            try:
                card,detail=p.card_detail(ch,snapshot,sku)
                if p.identity(ch,card,detail)!=expected:raise ValueError('exact card identity mismatch')
                actual=p.api.description_value(ch,card)
                result.update(seller_text_matches=normalize(actual)==normalize(target),actual_hash=p.digest(actual),target_hash=p.digest(target))
                if sku=='0am325025H':result['actual_description']=actual
                prior=state['rows'][sku][ch]
                if prior.get('protected_hash'):
                    try:result['protected_hash_matches_prior']=p.protected_hash(ch,card,detail)==prior['protected_hash']
                    except Exception as exc:result['protection_read_error']=str(exc)[:200]
                if ch=='ozon':
                    rich=[x for x in card.get('attributes',[]) if x.get('id')==11254 and x.get('values')]
                    if rich:
                        result['rich_content_present']=True
                        out['ozon_rich'][sku]={'card_id':expected,'rich_hash':p.digest(rich),'annotation_matches':result['seller_text_matches']}
                        if sku=='0am325025H':out['ozon_rich'][sku]['actual_rich']=rich
                    if not isinstance(card.get('model_info'),dict) or not card['model_info'].get('model_id') or not card['model_info'].get('count'):
                        out['ozon_model_diagnostics'][sku]={'card_id':expected,'sku':card.get('sku'),'model_info':card.get('model_info'),'model_attribute':[a for a in card.get('attributes',[]) if a.get('id')==9048]}
                    public_id=str(card.get('sku') or '')
                    url='https://www.ozon.ru/product/'+public_id+'/' if public_id.isdigit() else ''
                    hosts={'www.ozon.ru','ozon.ru'}
                elif ch=='wb':
                    public_id=expected;url='https://www.wildberries.ru/catalog/'+expected+'/detail.aspx';hosts={'www.wildberries.ru','wildberries.ru'}
                else:
                    public_id=expected;url='https://market.yandex.ru/product--/'+expected;hosts={'market.yandex.ru'}
                if url:result['public_probe']=extract_public(url,target,public_id,hosts,stopped)
            except Exception as exc:result['error']=str(exc)[:240]
    if out['ozon_model_diagnostics']:
        try:
            import ozon_client as oz
            with guard([],allow_write=False,descriptions_only=True):
                info=oz.get_product_info_list(list(out['ozon_model_diagnostics']))
            for row in info:
                s=row.get('offer_id')
                if s in out['ozon_model_diagnostics']:
                    out['ozon_model_diagnostics'][s]['independent_info']={k:row.get(k) for k in ('id','offer_id','sku','model_info','is_archived','is_autoarchived','statuses')}
        except Exception as exc:out['ozon_model_read_error']=type(exc).__name__
    if 'tilda' not in errors:
        targets=[s for s,v in ids['tilda'].items() if len(v)==1 and s not in p.TEST_SKUS and rules.get(s,{}).get('text',{}).get('tilda',{}).get('description')]
        results,_=p.aux.tilda_rows(rules,targets,snaps['tilda'])
        for sku,r in results.items():
            out['rows'].setdefault(sku,{})['tilda']={'card_id':r.get('uid'),'public_catalog_text_matches':r.get('description_verified',False),'full_text_matches':r.get('full_text_verified',False),'short_text_matches':r.get('short_description_verified',False),'error':r.get('error')}
    if 'avito' not in errors:
        try:
            import avito_client as av
            live=av.list_all_items(statuses=['active'])
            report=p.aux.read_avito_autoload(av)
            links=p.read_json(p.ROOT/'data/avito_links.json',{})
            targets=[s for s,v in ids['avito'].items() if len(v)==1 and s not in p.TEST_SKUS and rules.get(s,{}).get('text',{}).get('avito',{}).get('description')]
            p.aux.avito_live_details(av,live,report['items'],targets,rules,links)
            _,checks=p.aux.patch_avito_feed((p.ROOT/'data/avito_feed.xlsx').read_bytes(),rules,targets,report['items'],live,links)
            out['avito_report']={k:report['report'].get(k) for k in ('report_id','upload_id','started_at','finished_at','status')}
            live_by_id={str(x.get('id')):x for x in live}
            for sku,r in checks.items():
                aid=str(r.get('avito_id') or '')
                out['rows'].setdefault(sku,{})['avito']={**r,'storefront_verified':False}
                row=live_by_id.get(aid,{})
                url=row.get('url')
                if isinstance(url,str) and aid:
                    # A feed note may be added by the adapter; exact API text
                    # match above includes that note. This probe cannot replace it.
                    out['rows'][sku]['avito']['public_probe']=extract_public(url,rules[sku]['text']['avito']['description'],aid,{'www.avito.ru','avito.ru'},stopped)
            out['avito_unmapped_active']=state.get('inventory_extra',{}).get('avito_unmapped_active',[])
        except Exception as exc:out['inventory_errors']['avito_audit']=type(exc).__name__+': '+str(exc)[:200]
    out['summary']={}
    for ch in p.CHANNELS:
        rows=[v[ch] for v in out['rows'].values() if ch in v]
        out['summary'][ch]={'checked':len(rows),'seller_text_matches':sum(r.get('seller_text_matches') is True or r.get('description_verified') is True for r in rows),'public_catalog_text_matches':sum(r.get('public_catalog_text_matches') is True for r in rows),'public_probe_statuses':dict(Counter(r.get('public_probe',{}).get('status','not_checked') for r in rows))}
    return out
