"""Continue only the already-approved advertising feed operation after filter regeneration.
The completed filter/negative writes are not repeated.
"""
import copy,hashlib,json,sys,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import direct_owner_approved_20261124 as a
from direct_owner_approved_20261124 import *
PRE=Path('data/direct_recovery_20261124/owner_approved_changes.json')
OUT2=Path('data/direct_recovery_20261124/owner_approved_feed_completion.json')
def guard_ad(old,new):
    old,new=copy.deepcopy(old),copy.deepcopy(new)
    if old['Id'] in ADGROUP:
        require(new.get('State') in {'OFF','ON'} and new.get('Status') in {'ACCEPTED','PREACCEPTED','MODERATION'},'Unexpected target ad state '+str(new['Id']))
        old.pop('State',None);new.pop('State',None)
    return same_ad(old,new)
def run2():
    require(not OUT2.exists(),'Existing continuation journal; no blind replay')
    prev=json.loads(PRE.read_text())
    require(all(prev['stages'].get(x,{}).get('status')=='verified' for x in ('category_filters','campaign_negatives','group_negatives')),'Completed changes not verified')
    a.OUT=OUT2
    a.J={'status':'running','campaign':CID,'feed':FID,'started_utc':now(),'stages':{},'errors':[],'site_writes':0,'metrika_writes':0,'ads_writes':0,'negative_writes':0,'budget_writes':0}
    j=a.J;a.save()
    try:
        old=prev['before']; c,g,ads,k=get_campaign(),get_groups(),get_ads(),get_keys()
        validate_campaign(c,g,ads,k)
        orig={int(i):v for i,v in old['ads'].items()}
        require(all(guard_ad(orig[i],ads[i]) for i in orig),'Ad settings other than regeneration changed')
        require(all(ads[i]['ShoppingAd']['FeedFilterConditions']==desired(i) for i in ADGROUP),'Category selection missing')
        require(set(ADD)<=set(items(c.get('NegativeKeywords'))),'Campaign negative changes missing')
        require(all(not(REMOVE&set(items(g[i].get('NegativeKeywords')))) for i in GROUPS),'Group negative changes missing')
        require(same_campaign(old['campaign'],c),'Unexpected campaign setting change')
        j['initial_ad_states']={str(i):{'state':ads[i]['State'],'status':ads[i]['Status'],'processing':ads[i]['ShoppingAd'].get('FeedProcessingStatus'),'clarification':ads[i].get('StatusClarification')} for i in ADGROUP}
        f=get_feed();url=f['UrlFeed']['Url']
        require(url==old['feed']['UrlFeed']['Url'] or url==NEW_URL,'Unexpected feed URL')
        raw=FILE.read_bytes()
        require(raw==get_public(url),'Public file differs from repository')
        a.checkpoint()
        site=site_read(raw)
        j['site_catalog_check']=site;a.save()
        new,changes,blocked=patch_xml(raw,site)
        j['stages']['feed_source']={'status':'preflight','changes':changes,'blocked':blocked,'before_sha256':hashlib.sha256(raw).hexdigest(),'after_sha256':hashlib.sha256(new).hexdigest()}
        require(FILE.read_bytes()==raw,'Feed changed during operation')
        if new!=raw:
            FILE.write_bytes(new);a.checkpoint([FILE])
        remote=None
        for _ in range(12):
            try:
                remote=get_public(NEW_URL)
                if remote==new:break
            except Exception:pass
            time.sleep(4)
        require(remote==new,'Public updated feed bytes not confirmed')
        finish_stage('feed_source',not blocked,changes=changes,blocked=blocked,public_sha256=hashlib.sha256(new).hexdigest(),all_12_preserved=True)
        # Re-read settings before the only outstanding approved external update.
        aa=get_ads();ff=get_feed()
        require(all(guard_ad(orig[i],aa[i]) for i in orig),'Unexpected ad field change')
        require(all(aa[i]['ShoppingAd']['FeedFilterConditions']==desired(i) for i in ADGROUP),'Category guard')
        require(ff['UrlFeed']['Url']==url,'Concurrent feed edit')
        if url!=NEW_URL:
            api_update('feed_refresh','feeds',{'Feeds':[{'Id':FID,'UrlFeed':{'Url':NEW_URL}}]},[FID])
        j['stages'].setdefault('feed_refresh',{})
        af=get_feed()
        for _ in range(12):
            if af.get('Status')=='DONE' and af.get('NumberOfItems')==12 and af['UrlFeed']['Url']==NEW_URL:break
            time.sleep(5);af=get_feed()
        accepted=af['UrlFeed']['Url']==NEW_URL
        j['stages']['feed_refresh'].update(status='verified' if accepted and af.get('Status')=='DONE' and af.get('NumberOfItems')==12 else 'submitted_pending_processing' if accepted else 'requires_review',after=af,verified_utc=now());a.checkpoint()
        # Readback only: OFF/UNPROCESSED during regeneration is not a manual suspension.
        ad=get_ads()
        def ready(x):
            return all(x[i].get('State')=='ON' and x[i]['ShoppingAd'].get('FeedProcessingStatus')=='PROCESSED' for i in ADGROUP)
        for _ in range(12):
            if ready(ad):break
            time.sleep(5);ad=get_ads()
        j['final_ad_states']={str(i):{'state':ad[i]['State'],'status':ad[i]['Status'],'processing':ad[i]['ShoppingAd'].get('FeedProcessingStatus'),'clarification':ad[i].get('StatusClarification')} for i in ADGROUP}
        ca,ga,ka=get_campaign(),get_groups(),get_keys()
        j['protection']={'campaign_unchanged':stable(c)==stable(ca),'groups_unchanged':stable(g)==stable(ga),'keywords_unchanged':k==ka,'all_copy_and_settings_unchanged':all(guard_ad(orig[i],ad[i]) for i in orig),'all_filters_correct':all(ad[i]['ShoppingAd']['FeedFilterConditions']==desired(i) for i in ADGROUP),'budget_rub':ca['UnifiedCampaign']['BiddingStrategy']['Search']['HighestPosition']['WeeklySpendLimit']/1000000,'negative_count':len(items(ca.get('NegativeKeywords')))}
        j['ads_ready']=ready(ad)
        j['status']='verified' if ready(ad) and all(j['protection'][x] for x in ['campaign_unchanged','groups_unchanged','keywords_unchanged','all_copy_and_settings_unchanged','all_filters_correct']) and all(j['stages'][x]['status']=='verified' for x in ['feed_source','feed_refresh']) else 'partial_or_pending'
    except Exception as e:j['status']='blocked_or_unverified';j['errors'].append(d.scrub(e))
    finally:
        j['finished_utc']=now();a.save()
        print(json.dumps({'status':j['status'],'stages':{k:v.get('status') for k,v in j['stages'].items()},'errors':j['errors'],'ads_ready':j.get('ads_ready')},ensure_ascii=False),flush=True)
    return 0 if j['status']=='verified' else 1
if __name__=='__main__':sys.exit(run2())
