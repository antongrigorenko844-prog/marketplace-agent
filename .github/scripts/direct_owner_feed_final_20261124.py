"""Finalize the previously approved price/availability feed refresh.
Never repeat category or negative updates; preserve all campaign settings.
"""
import sys,copy,json,hashlib,time,subprocess,urllib.request
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import direct_owner_approved_20261124 as a
from direct_owner_approved_20261124 import *
from direct_owner_approved_feed_complete_20261124 import guard_ad
PRE=Path('data/direct_recovery_20261124/owner_approved_changes.json')
SYNC=Path('data/direct_recovery_20261124/owner_approved_feed_completion.json')
FINAL=Path('data/direct_recovery_20261124/owner_approved_feed_final.json')
def canonical(raw):return ET.canonicalize(xml_data=raw.decode('utf-8-sig'),strip_text=True)
def run3():
    require(not FINAL.exists(),'Existing final journal; no replay')
    p=json.loads(PRE.read_text());s=json.loads(SYNC.read_text())
    require(all(p['stages'].get(k,{}).get('status')=='verified' for k in ['category_filters','campaign_negatives','group_negatives']),'Prior writes not verified')
    a.OUT=FINAL;a.J={'status':'running','campaign':CID,'feed':FID,'started_utc':now(),'stages':{},'errors':[],'site_writes':0,'budget_writes':0,'ads_writes':0,'negative_writes':0}
    j=a.J;a.save()
    try:
        raw=FILE.read_bytes();sha=hashlib.sha256(raw).hexdigest()
        require(sha==s['stages']['feed_source']['after_sha256'],'Published source differs from verified plan')
        offers=xml_offers(raw)
        for sku,row in s['site_catalog_check'].items():
            require(row['price_source_agrees'] and row['site_stock'] is not None,'Unverified source '+sku)
            require(Decimal(offers[sku].findtext('price'))==Decimal(row['site_price']) and int(offers[sku].findtext('count'))==row['site_stock'] and offers[sku].get('available')==('true' if row['site_stock']>0 else 'false'),'Source discrepancy '+sku)
        c,g,ads,k=get_campaign(),get_groups(),get_ads(),get_keys();validate_campaign(c,g,ads,k)
        orig={int(i):v for i,v in p['before']['ads'].items()}
        require(all(guard_ad(orig[i],ads[i]) for i in orig) and all(ads[i]['ShoppingAd']['FeedFilterConditions']==desired(i) for i in ADGROUP),'Ad fields/filter drift')
        require(same_campaign(p['before']['campaign'],c) and set(ADD)<=set(items(c.get('NegativeKeywords'))) and all(not REMOVE.intersection(items(g[i].get('NegativeKeywords'))) for i in GROUPS),'Campaign/group drift')
        f=get_feed();old=f['UrlFeed']['Url'];require(old.startswith(FEED_URL),'Unknown live feed source')
        j['initial_feed']=f
        j['initial_ad_states']={str(i):[ads[i]['State'],ads[i]['Status'],ads[i]['ShoppingAd'].get('FeedProcessingStatus')] for i in ADGROUP}
        blob=subprocess.check_output(['git','rev-parse','HEAD:'+str(FILE)],text=True).strip()
        j['public_attempts']=[]
        # Use a fresh cache key for the already committed version, not an early cached URL.
        urls=[FEED_URL+'?copy_rev=20261124_'+blob,FEED_URL+'?rev='+sha,FEED_URL+'?v='+blob]
        selected=None
        for url in urls:
            try:
                req=urllib.request.Request(url,headers={'User-Agent':'LEOPARTS feed verification','Cache-Control':'no-cache','Accept':'application/xml,text/plain','Accept-Encoding':'identity'})
                with urllib.request.urlopen(req,timeout=20) as rr:
                    data=rr.read(1000000);status=rr.status
                exact=data==raw
                equivalent=exact or canonical(data)==canonical(raw)
                j['public_attempts'].append({'url':url,'http_status':status,'sha256':hashlib.sha256(data).hexdigest(),'bytes_exact':exact,'full_xml_equivalent':equivalent})
                if equivalent:
                    selected=url;break
            except Exception as e:j['public_attempts'].append({'url':url,'error':d.scrub(e)})
        a.checkpoint()
        require(selected is not None,'No fresh public feed version verified; no API write')
        a.NEW_URL=selected
        finish_stage('feed_source',True,all_12_preserved=True,source_sha256=sha,public_url=selected,changes=s['stages']['feed_source']['changes'])
        require(get_feed()['UrlFeed']['Url']==old,'Concurrent feed change')
        ad=get_ads();require(all(guard_ad(orig[i],ad[i]) for i in orig) and all(ad[i]['ShoppingAd']['FeedFilterConditions']==desired(i) for i in ADGROUP),'Ad drift')
        if old!=selected:api_update('feed_refresh','feeds',{'Feeds':[{'Id':FID,'UrlFeed':{'Url':selected}}]},[FID])
        j['stages'].setdefault('feed_refresh',{})
        ff=get_feed()
        for _ in range(10):
            if ff['UrlFeed']['Url']==selected and ff.get('Status')=='DONE' and ff.get('NumberOfItems')==12:break
            time.sleep(4);ff=get_feed()
        ok=ff['UrlFeed']['Url']==selected and ff.get('Status')=='DONE' and ff.get('NumberOfItems')==12
        j['stages']['feed_refresh'].update(status='verified' if ok else 'submitted_pending_processing' if ff['UrlFeed']['Url']==selected else 'requires_review',after=ff,verified_utc=now());a.checkpoint()
        def ready(x):return all(x[i].get('State')=='ON' and x[i]['ShoppingAd'].get('FeedProcessingStatus')=='PROCESSED' for i in ADGROUP)
        af=get_ads()
        for _ in range(12):
            if ready(af):break
            time.sleep(5);af=get_ads()
        j['final_ad_states']={str(i):{'state':af[i]['State'],'status':af[i]['Status'],'processing':af[i]['ShoppingAd'].get('FeedProcessingStatus'),'filter':af[i]['ShoppingAd']['FeedFilterConditions']} for i in ADGROUP}
        ca,ga,ka=get_campaign(),get_groups(),get_keys()
        j['protection']={'campaign_unchanged':stable(c)==stable(ca),'groups_unchanged':stable(g)==stable(ga),'keywords_unchanged':k==ka,'ad_fields_unchanged':all(guard_ad(orig[i],af[i]) for i in orig),'all_filters_correct':all(af[i]['ShoppingAd']['FeedFilterConditions']==desired(i) for i in ADGROUP),'budget_rub':ca['UnifiedCampaign']['BiddingStrategy']['Search']['HighestPosition']['WeeklySpendLimit']/1000000,'negative_count':len(items(ca.get('NegativeKeywords')))}
        j['ads_ready']=ready(af)
        j['status']='verified' if ok and ready(af) and all(j['protection'][x] for x in ['campaign_unchanged','groups_unchanged','keywords_unchanged','ad_fields_unchanged','all_filters_correct']) else 'partial_or_pending'
    except Exception as e:j['status']='blocked_or_unverified';j['errors'].append(d.scrub(e))
    finally:
        j['finished_utc']=now();a.save()
        print(json.dumps({'status':j['status'],'stages':{x:v['status'] for x,v in j['stages'].items()},'errors':j['errors']},ensure_ascii=False))
    return 0 if j['status']=='verified' else 1
if __name__=='__main__':sys.exit(run3())
