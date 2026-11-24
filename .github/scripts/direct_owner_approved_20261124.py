"""Owner-approved Nov24 DQ200 category filters, precise negatives and feed sync.
No keyword, bid, budget, copy, site price, site stock or analytics writes.
Only the three approved advertising operations, with readbacks and journal.
"""
import copy, json, os, re, sys, time, hashlib, subprocess
from decimal import Decimal
from pathlib import Path
from datetime import datetime, timezone
import urllib.request, urllib.error
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path.cwd()))
import direct_preflight_20261120 as d
from direct_mass_negatives_apply_20261120 import CAMPAIGN_GET,GROUP_GET,ADS_GET,atomic_save
from direct_shopping_copy_finish import OFFER_GROUPS,FEED_URL
from direct_neutral_ad_copy_20261120 import FEED_GET
import direct_metrika_read_20261120 as mt

CID=716316525
FID=4061297
GROUPS={5826171512,5826183946,5826320196}
ADGROUP={1927186476439690942:5826171512,1927189770678081700:5826183946,1927190920657966611:5826320196}
CATS={1927186476439690942:'1',1927189770678081700:'2',1927190920657966611:'3'}
OLD={1927186476439690942:'33500-38500',1927189770678081700:'60000-75000',1927190920657966611:'39000-45000'}
ADD=['ноутбук','"мехатроник мокрый купить"','"автозапчасти"']
REMOVE={'запчасти','автозапчасти'}
FILE=Path('feeds/dq200_candidate.yml')
OUT=Path('data/direct_recovery_20261124/owner_approved_changes.json')
REV='?copy_rev=20261124_owner_approved_prices'
NEW_URL=FEED_URL+REV
ROOT=Path.cwd()
d.ALLOWED=tuple(set(d.ALLOWED)|{'ads','feeds','dictionaries'})
USED=set()
J={}
def now():return datetime.now(timezone.utc).isoformat()
def require(ok,msg):
    if not ok:raise RuntimeError(msg)
def save():
    atomic_save(OUT,J)
def checkpoint(paths=()):
    save()
    subprocess.run(['bash','.github/scripts/save_data.sh',str(OUT),*map(str,paths)],check=True,timeout=120)
def items(o):return list((o or {}).get('Items') or [])
def maprows(rows):
    m={r['Id']:r for r in rows}
    require(len(m)==len(rows),'Duplicate ID')
    return m
def get_campaign():
    rows=d.get('campaigns',CAMPAIGN_GET,'Campaigns')
    require(len(rows)==1 and rows[0]['Id']==CID,'Campaign identity')
    return rows[0]
def get_groups():return maprows(d.get('adgroups',GROUP_GET,'AdGroups'))
def get_ads():return maprows(d.get('ads',ADS_GET,'Ads'))
def get_keys():return maprows(d.keywords())
def get_feed():
    rows=d.get('feeds',FEED_GET,'Feeds')
    require(len(rows)==1 and rows[0]['Id']==FID and rows[0].get('SourceType')=='URL','Feed identity')
    return rows[0]
def desired(aid):return {'Items':[{'Operand':'categoryId','Operator':'EQUALS_ANY','Arguments':[CATS[aid]]}]}
def old_filter(aid):return {'Items':[{'Operand':'price','Operator':'IN_RANGE','Arguments':[OLD[aid]]}]}
def stable(x):
    if isinstance(x,dict):
        return {k:stable(v) for k,v in x.items() if k not in {'Status','StatusClarification','SitelinksModeration','FeedProcessingStatus'}}
    if isinstance(x,list):return [stable(v) for v in x]
    return x
def without(o,*fields):
    return {k:v for k,v in o.items() if k not in fields}
def same_campaign(a,b):return stable(without(a,'NegativeKeywords'))==stable(without(b,'NegativeKeywords'))
def same_group(a,b):return stable(without(a,'NegativeKeywords'))==stable(without(b,'NegativeKeywords'))
def same_ad(a,b):
    a,b=copy.deepcopy(a),copy.deepcopy(b)
    for x in (a,b):
        if 'ShoppingAd' in x:x['ShoppingAd'].pop('FeedFilterConditions',None)
    return stable(a)==stable(b)
def validate_campaign(c,g,a,k):
    require(c['Id']==CID and c.get('State')=='ON' and c.get('Type')=='UNIFIED_CAMPAIGN','Campaign state')
    st=c['UnifiedCampaign']['BiddingStrategy']
    require(st['Search']['BiddingStrategyType']=='HIGHEST_POSITION' and st['Search']['HighestPosition']['WeeklySpendLimit']==4000000000 and st['Network']['BiddingStrategyType']=='SERVING_OFF','Budget or strategy drift')
    require(len(g)==6 and len(a)==8 and len(k)==272,'Inventory drift')
    require(all(x['CampaignId']==CID for x in list(g.values())+list(a.values())+list(k.values())),'Cross campaign')
    require(set(ADGROUP)<=set(a) and GROUPS<=set(g),'Missing target')
    for aid,gid in ADGROUP.items():
        x=a[aid]
        require(x['AdGroupId']==gid and x['Type']=='SHOPPING_AD' and x['ShoppingAd']['FeedId']==FID,'Ad identity')
        require(x['ShoppingAd']['FeedFilterConditions'] in (old_filter(aid),desired(aid)),'Unexpected original filter')
    require('"запчасти"' in items(c.get('NegativeKeywords')),'Exact generic exclusion absent')
def api_update(label,service,params,expected):
    require(label not in USED,'No blind retries')
    allowed=False
    if service=='campaigns':
        rows=params.get('Campaigns',[])
        allowed=len(rows)==1 and rows[0]['Id']==CID and set(rows[0])=={'Id','NegativeKeywords'}
    elif service=='adgroups':
        rows=params.get('AdGroups',[])
        allowed=bool(rows) and all(x['Id'] in GROUPS and set(x)=={'Id','NegativeKeywords'} for x in rows)
    elif service=='ads':
        rows=params.get('Ads',[])
        allowed=bool(rows) and all(x['Id'] in ADGROUP and x=={'Id':x['Id'],'ShoppingAd':{'FeedFilterConditions':desired(x['Id'])}} for x in rows)
    elif service=='feeds':
        allowed=params=={'Feeds':[{'Id':FID,'UrlFeed':{'Url':NEW_URL}}]}
    require(allowed,'Write outside authorization')
    USED.add(label)
    J['stages'][label]={'status':'intent_persisted','request':params,'at_utc':now()}
    checkpoint()
    h={'Authorization':'Bearer '+os.environ['YANDEX_DIRECT_TOKEN'],'Content-Type':'application/json; charset=utf-8','Accept-Language':'en'}
    if os.getenv('YANDEX_DIRECT_LOGIN'):h['Client-Login']=os.environ['YANDEX_DIRECT_LOGIN']
    request=urllib.request.Request('https://api.direct.yandex.com/json/v501/'+service,data=json.dumps({'method':'update','params':params},ensure_ascii=False).encode(),headers=h,method='POST')
    try:
        with urllib.request.urlopen(request,timeout=45) as resp:
            result=json.load(resp)
        if result.get('error'):J['stages'][label]['api_error']=d.scrub(result['error'])
        else:
            rows=result.get('result',{}).get('UpdateResults',[])
            J['stages'][label]['api_results']=rows
            J['stages'][label]['accepted']=set(x.get('Id') for x in rows)==set(expected) and all(not x.get('Errors') for x in rows)
    except Exception as exc:J['stages'][label]['api_error']=d.scrub(exc)
    save()
def finish_stage(label,ok,**fields):
    J['stages'].setdefault(label,{})
    J['stages'][label].update(status='verified' if ok else 'requires_review',verified_utc=now(),**fields)
    checkpoint()
def get_public(url):
    require(url==FEED_URL or url.startswith(FEED_URL+'?'),'Unexpected public feed URL')
    with urllib.request.urlopen(urllib.request.Request(url,headers={'Cache-Control':'no-cache'}),timeout=35) as r:
        return r.read(1000000)
def xml_offers(raw):
    root=ET.fromstring(raw)
    rows=root.findall('./shop/offers/offer')
    m={x.get('id'):x for x in rows}
    require(len(rows)==len(m)==12 and set(m)==set().union(*OFFER_GROUPS.values()),'Offer inventory')
    for aid,ids in OFFER_GROUPS.items():
        require({k for k,v in m.items() if v.findtext('categoryId')==CATS[aid]}==ids,'Category membership drift')
    return m
def no_other_xml_changes(old,new):
    aa,bb=xml_offers(old),xml_offers(new)
    for m in (aa,bb):
        for x in m.values():
            x.attrib.pop('available',None)
            for field in ('price','count'):
                e=x.find(field)
                if e is not None:x.remove(e)
    return all(ET.tostring(aa[k])==ET.tostring(bb[k]) for k in aa)
def site_read(raw):
    from scripts.leoparts_direct_operation_nov21 import site_records
    records=site_records()
    prices=json.loads(Path('data/site_prices.json').read_text())
    links=json.loads(Path('data/site_links.json').read_text())
    offers=xml_offers(raw)
    out={}
    for sku,offer in offers.items():
        matches=[(uid,row) for uid,row in records.items() if str(row.get('sku','')).casefold()==sku]
        require(len(matches)==1,'Missing or ambiguous Tilda SKU '+sku)
        uid,row=matches[0]
        require(str(row.get('externalid',row.get('external_id',''))).casefold()==sku,'Wrong external ID '+sku)
        require('/tproduct/'+uid+'-' in offer.findtext('url') and '/tproduct/'+uid+'-' in links[sku],'Tilda UID mismatch '+sku)
        live=Decimal(str(row['price']))
        require(live.is_finite() and live>0,'Bad site price '+sku)
        approved=Decimal(str(prices[sku]))
        ed=row.get('editions') or []
        require(len(ed)<=1,'Multiple variants '+sku)
        if ed:
            require(str(ed[0]['uid'])==uid and str(ed[0].get('sku','')).casefold()==sku and Decimal(str(ed[0]['price']))==live,'Variant mismatch '+sku)
        qty=row.get('quantity')
        if qty in (None,''):stock=None
        else:
            q=Decimal(str(qty));require(q.is_finite() and q>=0 and q==q.to_integral_value(),'Bad site stock '+sku);stock=int(q)
        if ed and ed[0].get('quantity') not in (None,''):
            eq=Decimal(str(ed[0]['quantity']))
            if stock is None or eq!=stock:stock=None
        out[sku]={'uid':uid,'site_price':str(live),'source_price':str(approved),'price_source_agrees':live==approved,'old_feed_price':offer.findtext('price'),'site_stock':stock,'old_feed_stock':offer.findtext('count'),'old_available':offer.get('available')}
    return out
def patch_xml(raw,site):
    text=raw.decode('utf-8')
    changes={}; blocked={}
    for sku,row in site.items():
        if not row['price_source_agrees']:
            blocked[sku]='live and protected source prices differ'
            continue
        pat=r'<offer id="'+re.escape(sku)+r'"[^>]*>.*?</offer>'
        match=re.search(pat,text,re.S);require(match is not None,'Offer block absent')
        old=match.group(0);new=old
        v=Decimal(row['site_price'])
        if Decimal(row['old_feed_price'])!=v:
            new,n=re.subn(r'<price>[^<]+</price>','<price>'+str(v.quantize(Decimal('0.01')))+'</price>',new)
            require(n==1,'Price node')
        stock=row['site_stock']
        if stock is None:
            blocked[sku]='unverified site quantity; preserved availability'
        else:
            new,n=re.subn(r'available="(?:true|false)"','available="'+('true' if stock>0 else 'false')+'"',new,count=1)
            require(n==1,'Availability node')
            if re.search(r'<count>[^<]+</count>',new):
                new,n=re.subn(r'<count>[^<]+</count>','<count>'+str(stock)+'</count>',new)
                require(n==1,'Count node')
            else:
                new=new.replace('</offer>','  <count>'+str(stock)+'</count>\n      </offer>')
        if old!=new:
            text=text[:match.start()]+new+text[match.end():]
            changes[sku]={'price_before':row['old_feed_price'],'price_after':str(v),'stock_before':row['old_feed_stock'],'stock_after':stock}
    new=text.encode('utf-8')
    require(no_other_xml_changes(raw,new),'Non-price/non-stock XML field changed')
    return new,changes,blocked
def check_site_readonly():
    result={}
    try:
        from urllib.request import urlopen,Request
        with urlopen(Request('https://leoparts-akpp.ru/mehatronik-dq200',headers={'User-Agent':'Mozilla/5.0'}),timeout=35) as r:
            html=r.read(3000000).decode('utf-8','replace')
            result['http_status']=r.status
        result['markers']={x:x in html for x in ['bez-platy','s-platoi','platy','form5246286101','form5260805101','113839860','tinkoff','ozon','viewport']}
        result['h1_count']=len(re.findall(r'<h1\b',html,re.I));result['h2_count']=len(re.findall(r'<h2\b',html,re.I))
        result['note']='HTML only; no browser checkout, form submission or payment'
    except Exception as exc:result['site_error']=d.scrub(exc)
    try:
        goals=mt.call('/management/v1/counter/113839860/goals')
        result['goals']=[{k:x.get(k) for k in ('id','name','type','status')} for x in goals.get('goals',[])]
        result['goal_checks']='Definition read only; actual paid orders not proven'
    except Exception as exc:result['metrika_error']=d.scrub(exc)
    return result
def run():
    global J
    require(not OUT.exists(),'Existing execution journal; read back, do not replay')
    J={'status':'running','campaign':CID,'feed':FID,'started_utc':now(),'stages':{},'errors':[],'site_price_writes':0,'site_stock_writes':0,'metrika_writes':0,'keyword_writes':0,'bid_writes':0,'budget_writes':0,'copy_writes':0}
    save()
    try:
        c,g,a,k=get_campaign(),get_groups(),get_ads(),get_keys()
        validate_campaign(c,g,a,k)
        f=get_feed()
        require(f.get('UrlFeed',{}).get('Url','').startswith(FEED_URL),'Live feed source drift')
        raw=FILE.read_bytes()
        current_public=get_public(f['UrlFeed']['Url'])
        require(xml_offers(raw).keys()==xml_offers(current_public).keys(),'Live source inventory mismatch')
        require(raw==current_public,'Live feed bytes differ from repository; do not overwrite')
        J['before']={'campaign':c,'groups':g,'ads':a,'feed':f,'keyword_count':len(k)-6,'source_sha256':hashlib.sha256(raw).hexdigest()}
        checkpoint()
        # First protect category membership, before any price is lowered.
        try:
            pending=[{'Id':aid,'ShoppingAd':{'FeedFilterConditions':desired(aid)}} for aid in ADGROUP if a[aid]['ShoppingAd']['FeedFilterConditions']!=desired(aid)]
            if pending:api_update('category_filters','ads',{'Ads':pending},[p['Id'] for p in pending])
            aa=get_ads()
            ok=all(aa[aid]['ShoppingAd']['FeedFilterConditions']==desired(aid) for aid in ADGROUP)
            preserved=all(same_ad(a[x],aa[x]) for x in a) and same_campaign(c,get_campaign()) and get_keys()==k
            finish_stage('category_filters',ok and preserved,filters={str(aid):aa[aid]['ShoppingAd']['FeedFilterConditions'] for aid in ADGROUP},other_ad_fields_preserved=preserved,counts={CATS[x]:len(OFFER_GROUPS[x]) for x in ADGROUP})
        except Exception as exc:
            J['errors'].append('category_filters: '+d.scrub(exc));save()
        # Add exact campaign protections before lifting broad group exclusions.
        try:
            cc=get_campaign();require(same_campaign(c,cc),'Campaign changed')
            before=items(cc.get('NegativeKeywords'));require(len(set(before))==len(before),'Duplicate negatives')
            target=before+[x for x in ADD if x not in before]
            require(len(target)<=700 and sum(len(re.sub(r'[\s\-!+"\[\]]','',p)) for p in target)<19000,'Negatives cap')
            if target!=before:api_update('campaign_negatives','campaigns',{'Campaigns':[{'Id':CID,'NegativeKeywords':{'Items':target}}]},[CID])
            ac=get_campaign();ok=set(items(ac.get('NegativeKeywords')))==set(target) and same_campaign(cc,ac) and get_keys()==k
            finish_stage('campaign_negatives',ok,before_count=len(before),after_count=len(items(ac.get('NegativeKeywords'))),added=[x for x in ADD if x not in before])
            require(ok,'Campaign negative readback failed; do not lift group protection')
            gg=get_groups();require(all(stable(gg[x])==stable(g[x]) for x in g),'Group drift')
            changes=[{'Id':gid,'NegativeKeywords':{'Items':[p for p in items(gg[gid].get('NegativeKeywords')) if p not in REMOVE]}} for gid in sorted(GROUPS) if any(p in REMOVE for p in items(gg[gid].get('NegativeKeywords')))]
            if changes:api_update('group_negatives','adgroups',{'AdGroups':changes},[p['Id'] for p in changes])
            ag=get_groups()
            desired_groups={gid:[p for p in items(gg[gid].get('NegativeKeywords')) if p not in REMOVE] if gid in GROUPS else items(gg[gid].get('NegativeKeywords')) for gid in gg}
            ok=all(set(items(ag[gid].get('NegativeKeywords')))==set(desired_groups[gid]) and same_group(gg[gid],ag[gid]) for gid in gg) and same_campaign(c,get_campaign()) and get_keys()==k
            finish_stage('group_negatives',ok,groups={str(gid):{'before':items(gg[gid].get('NegativeKeywords')),'after':items(ag[gid].get('NegativeKeywords'))} for gid in sorted(GROUPS)})
        except Exception as exc:J['errors'].append('negatives: '+d.scrub(exc));save()
        # Feed stage is independent of negative changes, but never before categories are verified.
        try:
            aa=get_ads()
            require(all(aa[aid]['ShoppingAd']['FeedFilterConditions']==desired(aid) for aid in ADGROUP),'Category filters not verified; leave feed unchanged')
            require(all(same_ad(a[x],aa[x]) for x in a),'Other ad fields changed')
            ff=get_feed()
            require(ff.get('UrlFeed',{}).get('Url')==f['UrlFeed']['Url'],'Concurrent feed change')
            site=site_read(raw)
            J['site_catalog_check']=site;save()
            new,changes,blocked=patch_xml(raw,site)
            J['stages']['feed_source']={'status':'preflight','changes':changes,'blocked':blocked,'before_sha256':hashlib.sha256(raw).hexdigest(),'after_sha256':hashlib.sha256(new).hexdigest()}
            # Only publish an exact price/availability delta for known existing offers.
            require(FILE.read_bytes()==raw,'Feed file changed during operation')
            if new!=raw:
                FILE.write_bytes(new)
                checkpoint([FILE])
            remote=None
            for _ in range(8):
                try:
                    remote=get_public(NEW_URL)
                    if remote==new:break
                except Exception:pass
                time.sleep(4)
            require(remote==new,'Published file bytes not confirmed')
            finish_stage('feed_source',not blocked,changes=changes,blocked=blocked,public_sha256=hashlib.sha256(new).hexdigest(),all_12_preserved=True)
            if new!=raw or f['UrlFeed']['Url']!=NEW_URL:
                api_update('feed_refresh','feeds',{'Feeds':[{'Id':FID,'UrlFeed':{'Url':NEW_URL}}]},[FID])
            af=get_feed()
            for _ in range(8):
                if af.get('Status')=='DONE' and af.get('NumberOfItems')==12:break
                time.sleep(5);af=get_feed()
            accepted=af.get('UrlFeed',{}).get('Url')==NEW_URL
            J['stages']['feed_refresh'].update(status='verified' if accepted and af.get('Status')=='DONE' and af.get('NumberOfItems')==12 else 'submitted_pending_processing' if accepted else 'requires_review',after=af,verified_utc=now());checkpoint()
        except Exception as exc:J['errors'].append('feed: '+d.scrub(exc));save()
        J['site_and_metrika_readonly']=check_site_readonly()
        try:
            ca,ga,ad,kw=get_campaign(),get_groups(),get_ads(),get_keys()
            J['final_protection']={'campaign_except_negatives_unchanged':same_campaign(c,ca),'groups_except_negatives_unchanged':all(same_group(g[x],ga[x]) for x in g),'ads_except_filters_unchanged':all(same_ad(a[x],ad[x]) for x in a),'keywords_unchanged':k==kw,'budget_rub':ca['UnifiedCampaign']['BiddingStrategy']['Search']['HighestPosition']['WeeklySpendLimit']/1000000,'negative_count':len(items(ca.get('NegativeKeywords'))),'ad_states':{str(i):{'state':x.get('State'),'status':x.get('Status'),'feed_processing':(x.get('ShoppingAd') or {}).get('FeedProcessingStatus')} for i,x in ad.items()}}
        except Exception as exc:J['errors'].append('final: '+d.scrub(exc))
        required=['category_filters','campaign_negatives','group_negatives','feed_source','feed_refresh']
        J['status']='verified' if not J['errors'] and all(J['stages'].get(x,{}).get('status')=='verified' for x in required) and all(J.get('final_protection',{}).get(x) for x in ['campaign_except_negatives_unchanged','groups_except_negatives_unchanged','ads_except_filters_unchanged','keywords_unchanged']) else 'partial_or_pending'
    except Exception as exc:J['status']='blocked_or_unverified';J['errors'].append(d.scrub(exc))
    finally:
        J['finished_utc']=now();save()
        print(json.dumps({'status':J['status'],'stages':{k:v.get('status') for k,v in J['stages'].items()},'errors':J['errors']},ensure_ascii=False),flush=True)
    return 0 if J['status']=='verified' else 1
if __name__=='__main__':sys.exit(run())
