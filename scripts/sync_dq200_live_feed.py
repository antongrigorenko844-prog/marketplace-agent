"""Synchronize only the active DQ200 ad feed from verified Tilda prices and stock.
Never writes website/marketplace prices, stock, advertising copy, bidding or settings.
"""
from __future__ import annotations
import copy, hashlib, json, os, re, subprocess, sys, time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import xml.etree.ElementTree as ET
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path[:0]=[str(ROOT),str(ROOT/'.github/scripts')]
import direct_owner_approved_20261124 as a
import site_pricing
from scripts.leoparts_direct_operation_nov21 import site_records
FILE=ROOT/'feeds/dq200_candidate.yml'
REPORT=ROOT/'data/direct_recovery_20261124/automatic_feed_sync.json'
LOCKED_PATHS=['feeds/dq200_candidate.yml','data/avito_site_price_overrides.xlsx','data/site_price_only_overrides.json','data/site_prices.json','data/site_links.json','scripts/sync_dq200_live_feed.py','.github/workflows/dq200-ad-feed.yml']
UIDS={'0am325025h':('0am325025H','908157881413'),'0am325025x':('0am325025x','353312992873'),'0am325025hx':('0am325025HX','517217914653'),'0am325025hxe':('0am325025HXE','937513129983'),'0am325025hxf':('0am325025HXF','915950884033'),'0am325025hxg':('0am325025HXG','772831134323'),'0am325025hxk':('0am325025HXK','684991663463'),'0am927769d':('0AM927769D','787466152673'),'0am927769e':('0AM927769E','137877342943'),'0am927769f':('0AM927769F','801039414923'),'0am927769g':('0AM927769G','933254300083'),'0am927769k':('0AM927769K','234667588903')}
def now():return datetime.now(timezone.utc).isoformat()
def sha(b):return hashlib.sha256(b).hexdigest()
def git(*args):return subprocess.check_output(['git',*args],text=True).strip()
def require(ok,msg):
    if not ok:raise RuntimeError(msg)
def money(x):
    d=Decimal(str(x));require(d.is_finite() and d>0 and d==d.quantize(Decimal('.01')),'Invalid price')
    return d
def qty(x):
    d=Decimal(str(x));require(d.is_finite() and d>=0 and d==d.to_integral_value(),'Unverified quantity')
    return int(d)
def approved_price(sku,overrides,fallback):
    exact=UIDS[sku][0]
    if exact in overrides:return money(overrides[exact]),'protected_site_override'
    same=[(k,v) for k,v in overrides.items() if k.casefold()==sku]
    require(len(same)<=1,'Ambiguous protected price')
    if same:return money(same[0][1]),'protected_site_override_casefold'
    require(sku in fallback,'Approved price missing')
    return money(fallback[sku]),'site_price_snapshot'
def protected_xml(raw):
    root=ET.fromstring(raw)
    for o in root.findall('./shop/offers/offer'):
        o.attrib.pop('available',None)
        for k in ('price','count'):
            for el in o.findall(k):o.remove(el)
    return ET.canonicalize(xml_data=ET.tostring(root,encoding='unicode'),strip_text=True)
def patch(raw, records, overrides, fallback):
    offers=a.xml_offers(raw)
    text=raw.decode('utf-8');changes={};blocked={};checked={}
    for sku,o in offers.items():
        try:
            exact,uid=UIDS[sku]
            matches=[x for x in records.values() if str(x.get('sku','')).casefold()==sku]
            require(len(matches)==1 and str(matches[0].get('uid'))==uid and matches[0].get('sku')==exact,'Site SKU/UID ambiguity')
            r=matches[0]
            require(str(r.get('externalid',r.get('external_id','')))==exact,'External ID mismatch')
            require('/tproduct/'+uid+'-' in o.findtext('url') and urlsplit(o.findtext('url')).hostname=='leoparts-akpp.ru','Feed destination mismatch')
            live=money(r['price']);approved,source=approved_price(sku,overrides,fallback)
            require(live==approved,'Site price differs from protected source; do not propagate')
            ed=r.get('editions') or [];require(len(ed)<=1,'Multiple editions')
            n=qty(r['quantity'])
            if ed:
                require(str(ed[0].get('uid'))==uid and ed[0].get('sku')==exact and money(ed[0]['price'])==live and qty(ed[0]['quantity'])==n,'Edition inconsistency')
            p0=money(o.findtext('price'));n0=o.findtext('count');a0=o.get('available')
            checked[sku]={'uid':uid,'price':str(live),'stock':n,'approved_source':source}
            if (p0==live and n0 is not None and qty(n0)==n and a0==('true' if n else 'false')):continue
            pat=r'<offer id="'+re.escape(sku)+r'"[^>]*>.*?</offer>'
            m=re.search(pat,text,re.S);require(m is not None,'Offer block missing')
            before=m.group(0);after=before
            if p0!=live:
                after,c=re.subn(r'<price>[^<]+</price>','<price>'+format(live,'.2f')+'</price>',after);require(c==1,'Price node')
            after,c=re.subn(r'available="(?:true|false)"','available="'+('true' if n else 'false')+'"',after,count=1);require(c==1,'Availability node')
            if n0 is not None:
                after,c=re.subn(r'<count>[^<]+</count>','<count>'+str(n)+'</count>',after);require(c==1,'Stock node')
            else:after=after.replace('</offer>','  <count>'+str(n)+'</count>\n      </offer>')
            text=text[:m.start()]+after+text[m.end():]
            changes[sku]={'price_before':str(p0),'price_after':str(live),'stock_before':n0,'stock_after':n,'available_before':a0,'available_after':bool(n)}
        except Exception as e:blocked[sku]=a.d.scrub(e)
    new=text.encode('utf-8')
    require(protected_xml(raw)==protected_xml(new),'Unrelated XML field modified')
    aa=a.xml_offers(new)
    for sku in blocked:require(ET.tostring(offers[sku])==ET.tostring(aa[sku]),'Blocked SKU modified')
    return new,changes,blocked,checked
def read_ads():
    c,g,ads,k=a.get_campaign(),a.get_groups(),a.get_ads(),a.get_keys()
    require(c['Id']==a.CID and c['Type']=='UNIFIED_CAMPAIGN','Wrong campaign')
    for aid,gid in a.ADGROUP.items():
        x=ads[aid];require(x['AdGroupId']==gid and x['ShoppingAd']['FeedId']==a.FID and x['ShoppingAd']['FeedFilterConditions']==a.desired(aid),'Category mapping changed')
    return c,g,ads,k
def source_url(f):
    u=f['UrlFeed']['Url'];require(u==a.FEED_URL or u.startswith(a.FEED_URL+'?'),'Unknown feed source');return u
def current_remote():
    git('fetch','-q','origin','main')
    return git('rev-parse','FETCH_HEAD')
def verify_paths():
    remote=current_remote()
    require(git('diff','--name-only','HEAD',remote,'--',*LOCKED_PATHS)=='','Concurrent protected source edit; retry next run')
    return remote
def commit_feed(new):
    verify_paths()
    FILE.write_bytes(new)
    git('config','user.name','marketplace-agent-bot');git('config','user.email','actions@users.noreply.github.com')
    git('add',str(FILE.relative_to(ROOT)))
    git('commit','-m','Sync verified DQ200 advertising prices and stock [skip ci]')
    try:git('push','origin','HEAD:main')
    except subprocess.CalledProcessError:
        # A non-fast-forward push never overwrites the other writer.
        remote=current_remote()
        require(git('diff','--name-only','HEAD^',remote,'--',*LOCKED_PATHS)=='','Concurrent source edit; defer')
        git('rebase',remote);git('push','origin','HEAD:main')
    return git('rev-parse','HEAD:'+str(FILE.relative_to(ROOT)))
def public_verified(url,raw):
    import urllib.request
    for _ in range(6):
        try:
            req=urllib.request.Request(url,headers={'Cache-Control':'no-cache','Accept-Encoding':'identity','User-Agent':'LEOPARTS-feed-sync'})
            with urllib.request.urlopen(req,timeout=15) as resp:data=resp.read(1000000)
            if data==raw:return True
        except Exception:pass
        time.sleep(5)
    return False
def save(j):
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    tmp=REPORT.with_suffix('.tmp');tmp.write_text(json.dumps(j,ensure_ascii=False,indent=2)+'\n');tmp.replace(REPORT)
def feed_update(url):
    import urllib.request
    h={'Authorization':'Bearer '+os.environ['YANDEX_DIRECT_TOKEN'],'Content-Type':'application/json; charset=utf-8','Accept-Language':'en'}
    if os.getenv('YANDEX_DIRECT_LOGIN'):h['Client-Login']=os.environ['YANDEX_DIRECT_LOGIN']
    params={'method':'update','params':{'Feeds':[{'Id':a.FID,'UrlFeed':{'Url':url}}]}}
    req=urllib.request.Request('https://api.direct.yandex.com/json/v501/feeds',headers=h,data=json.dumps(params).encode(),method='POST')
    with urllib.request.urlopen(req,timeout=30) as r:result=json.load(r)
    rows=result.get('result',{}).get('UpdateResults',[])
    return len(rows)==1 and rows[0].get('Id')==a.FID and not rows[0].get('Errors')
def run():
    j={'status':'running','started_utc':now(),'campaign':a.CID,'feed':a.FID,'site_writes':0,'marketplace_writes':0,'ad_setting_writes':0,'feed_refresh_attempted':False,'errors':[]};save(j)
    try:
        raw=FILE.read_bytes()
        c,g,ads,k=read_ads()
        f=a.get_feed();old=source_url(f)
        j['feed_before']=f
        if f.get('Status')=='UPDATING':
            j['status']='deferred_provider_processing';return 0
        require(f.get('Status')=='DONE' and f.get('NumberOfItems')==12,'Unexpected feed status or item count')
        overrides=site_pricing.load_price_overrides(channel='site')
        fallback=json.loads((ROOT/'data/site_prices.json').read_text())
        records=site_records()
        new,changes,blocked,checked=patch(raw,records,overrides,fallback)
        j.update(changes=changes,blocked=blocked,checked=checked,sku_count=12,source_before_sha256=sha(raw),source_after_sha256=sha(new))
        require(len(checked)+len(blocked)==12,'Incomplete site inspection')
        # Confirm each proposed change against a second independent public read.
        if changes:
            again=site_records();verify_paths()
            new2,changes2,blocked2,_=patch(raw,again,overrides,fallback)
            require(new2==new and changes2==changes and blocked2==blocked,'Live values changed during verification; defer')
        # No other advertising setting can be changed by this workflow.
        cc,gg,ad2,kk=read_ads();ff=a.get_feed()
        require(a.stable(c)==a.stable(cc) and a.stable(g)==a.stable(gg) and a.stable(ads)==a.stable(ad2) and k==kk and source_url(ff)==old and ff.get('Status')=='DONE','Advertising state changed during preflight')
        if new!=raw:
            blob=commit_feed(new)
            j['source_published']=True
        else:
            verify_paths()
            blob=git('rev-parse','HEAD:'+str(FILE.relative_to(ROOT)))
        # A previous run may have published but not refreshed. The URL is versioned by the committed blob.
        desired=a.FEED_URL+'?copy_rev=auto_'+blob
        matches=any(v.endswith(blob) for vals in parse_qs(urlsplit(old).query).values() for v in vals)
        if not matches:
            require(public_verified(desired,new),'New public feed not verified')
            verify_paths()
            require(source_url(a.get_feed())==old and a.get_feed().get('Status')=='DONE','Concurrent feed refresh')
            j['feed_refresh_attempted']=True;j['intended_url']=desired;save(j)
            try:j['api_accepted']=feed_update(desired)
            except Exception as e:j['api_error']=a.d.scrub(e)
            # Read back the URL, not a blind retry.
            af=a.get_feed()
            require(source_url(af)==desired,'Feed update not confirmed')
        else:
            require(public_verified(old,new),'Current public file differs')
            af=a.get_feed()
        for _ in range(8):
            if af.get('Status')=='DONE':break
            time.sleep(5);af=a.get_feed()
        j['feed_after']=af
        after_c,after_g,after_ads,after_k=read_ads()
        j['protected']={'campaign_unchanged':a.stable(c)==a.stable(after_c),'groups_unchanged':a.stable(g)==a.stable(after_g),'keywords_unchanged':k==after_k,'ad_fields_unchanged':all(a.same_ad(ads[i],after_ads[i]) or (i in a.ADGROUP and a.same_ad(dict(ads[i],State=after_ads[i]['State']),after_ads[i])) for i in ads)}
        require(all(j['protected'].values()),'Unrelated setting changed')
        j['ads']={str(i):{'state':after_ads[i]['State'],'processing':after_ads[i]['ShoppingAd'].get('FeedProcessingStatus')} for i in a.ADGROUP}
        ready=af.get('Status')=='DONE' and af.get('NumberOfItems')==12 and all(x['state']=='ON' and x['processing']=='PROCESSED' for x in j['ads'].values())
        j['status']='blocked_skus_review' if blocked else 'verified_updated' if ready and (changes or j['feed_refresh_attempted']) else 'verified_no_changes' if ready else 'submitted_pending_processing'
        return 1 if blocked else 0
    except Exception as e:
        j['status']='blocked_or_unverified';j['errors'].append(a.d.scrub(e));return 1
    finally:
        j['finished_utc']=now();save(j)
        print(json.dumps({'status':j['status'],'changes':len(j.get('changes',{})),'blocked':j.get('blocked',{}),'errors':j['errors'],'feed_refresh_attempted':j['feed_refresh_attempted']},ensure_ascii=False),flush=True)
if __name__=='__main__':sys.exit(run())
