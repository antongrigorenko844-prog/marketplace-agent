"""Independent LEOPARTS audit. Only Direct.get and Reports; no mutation code."""
import csv
import hashlib
import io
import json
import os
from pathlib import Path
import re
import time
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import urllib.request
import urllib.error
from urllib.parse import urlparse
import xml.etree.ElementTree as ET

CID = 716316525
BASE = 'https://api.direct.yandex.com/json/v501/'
OUT = Path('data/direct_independent_audit_20261120.json')
PLAN = Path('data/direct_all234_keywords_plan_20261120.json')
# Immutable approved request schema, not the workbook SHA.
PLAN_SHA = '077f0b4c500c9f035f9e5c6feecf231fe7e0f4685090a876a8bab267353db3e8'
MAX_BYTES = 8 * 1024 * 1024
REPORT_TYPES = {'SEARCH_QUERY_PERFORMANCE_REPORT','CRITERIA_PERFORMANCE_REPORT','ADGROUP_PERFORMANCE_REPORT'}

def now():
    return datetime.now(timezone.utc).isoformat()

def clean(value):
    if isinstance(value, dict):
        return {str(k):clean(v) for k,v in value.items() if str(k).lower() not in {'authorization','token','password','client-login','login'}}
    if isinstance(value,list): return [clean(v) for v in value]
    if isinstance(value,str):
        for name in ['YANDEX_DIRECT_TOKEN','YANDEX_DIRECT_LOGIN']:
            secret=os.environ.get(name,'')
            if secret: value=value.replace(secret,'[redacted]')
    return value

def save(out):
    OUT.parent.mkdir(parents=True,exist_ok=True)
    OUT.write_text(json.dumps(clean(out),ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl): return None

def request(service, params, report=False):
    allowed={'campaigns','adgroups','keywords','ads','sitelinks','feeds','negativekeywordsharedsets'}
    if report:
        if service!='reports' or params.get('ReportType') not in REPORT_TYPES: raise RuntimeError('Read report allowlist failed')
        if params['SelectionCriteria']['Filter'] != [{'Field':'CampaignId','Operator':'IN','Values':[str(CID)]}]: raise RuntimeError('Report scope failed')
        body={'params':params}
    else:
        if service not in allowed: raise RuntimeError('Only read services are allowed')
        body={'method':'get','params':params}
    token=os.environ.get('YANDEX_DIRECT_TOKEN','')
    if not token: raise RuntimeError('Direct credential unavailable')
    headers={'Authorization':'Bearer '+token,'Content-Type':'application/json; charset=utf-8','Accept-Language':'en'}
    if os.environ.get('YANDEX_DIRECT_LOGIN'): headers['Client-Login']=os.environ['YANDEX_DIRECT_LOGIN']
    if report: headers.update(processingMode='auto',returnMoneyInMicros='false',skipReportHeader='true',skipReportSummary='true',skipColumnHeader='false')
    req=urllib.request.Request(BASE+service,data=json.dumps(body,ensure_ascii=False).encode(),headers=headers,method='POST')
    for attempt in range(7):
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=40) as res:
                status=res.status; raw=res.read(MAX_BYTES+1)
        except urllib.error.HTTPError as exc:
            code=exc.code; exc.close(); raise RuntimeError(service+' read HTTP '+str(code)) from None
        except Exception:
            raise RuntimeError(service+' read transport failed') from None
        if len(raw)>MAX_BYTES: raise RuntimeError('Response too large')
        if status in (201,202) and report:
            time.sleep(8); continue
        if status != 200: raise RuntimeError('Unexpected read status '+str(status))
        if report: return raw.decode('utf-8-sig')
        response=json.loads(raw)
        if response.get('error'): raise RuntimeError(service+' read API '+str(response['error'].get('error_code'))+' '+str(response['error'].get('error_string','')))
        result=response['result']
        if result.get('LimitedBy'): raise RuntimeError(service+' returned partial page')
        return result
    raise RuntimeError('Report was not ready within bounded retries')

def read_public(url):
    host=urlparse(url).hostname
    if host not in {'leoparts-akpp.ru','www.leoparts-akpp.ru','raw.githubusercontent.com'}: raise RuntimeError('Public read host outside allowlist')
    class SameHost(urllib.request.HTTPRedirectHandler):
        def redirect_request(self,req,fp,code,msg,headers,newurl):
            if urlparse(newurl).scheme!='https' or urlparse(newurl).hostname!=host: raise RuntimeError('Redirect outside source host')
            return super().redirect_request(req,fp,code,msg,headers,newurl)
    with urllib.request.build_opener(SameHost()).open(urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 LEOPARTS-read-only-audit'}),timeout=40) as res:
        raw=res.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES: raise RuntimeError('Public read oversized')
        return raw, {'requested_url':url,'final_url':res.url,'status':res.status,'sha256':hashlib.sha256(raw).hexdigest(),'fetched_at_utc':now()}

def run():
    report={'audit_kind':'independent_read_only','started_at_utc':now(),'api_mutations':0,'errors':[],'snapshots':{},'reports':{},'public_pages':[],'feed_contents':[]}
    raw=PLAN.read_bytes()
    if hashlib.sha256(raw).hexdigest()!=PLAN_SHA: raise RuntimeError('Read schema plan changed')
    plan=json.loads(raw)
    for spec in plan['preflight_read_only_requests']:
        service=spec['endpoint'].removeprefix(BASE)
        try:
            params=spec['params']
            if service=='keywords' and 'ServingStatus' not in params['FieldNames']: params['FieldNames'].append('ServingStatus')
            report['snapshots'][service]={'at_utc':now(),'result':request(service,params)}
        except Exception as e: report['errors'].append({'section':service,'error':clean(str(e))})
        save(report)
    try:
        report['historical_campaign_states']=request('campaigns',{'SelectionCriteria':{'Ids':[715571070,715572923]},'FieldNames':['Id','Name','State','Status','Type'],'Page':{'Limit':1000}})
    except Exception as e: report['errors'].append({'section':'historical_campaigns','error':clean(str(e))})
    campaigns=report['snapshots'].get('campaigns',{}).get('result',{}).get('Campaigns',[])
    groups=report['snapshots'].get('adgroups',{}).get('result',{}).get('AdGroups',[])
    ads=report['snapshots'].get('ads',{}).get('result',{}).get('Ads',[])
    shared=set()
    for obj in [c.get('UnifiedCampaign',{}) for c in campaigns]+groups:
        shared.update((obj.get('NegativeKeywordSharedSetIds') or {}).get('Items',[]))
    report['associated_shared_set_ids']=sorted(shared)
    if shared:
        try: report['shared_sets']=request('negativekeywordsharedsets',{'SelectionCriteria':{'Ids':sorted(shared)},'FieldNames':['Id','Name','NegativeKeywords','Associated']})
        except Exception as e: report['errors'].append({'section':'shared_sets','error':clean(str(e))})
    feed_ids=sorted({a['ShoppingAd']['FeedId'] for a in ads if a.get('ShoppingAd',{}).get('FeedId')})
    if feed_ids:
        try:
            report['feeds']=request('feeds',{'SelectionCriteria':{'Ids':feed_ids},'FieldNames':['Id','Name','SourceType','UpdatedAt','NumberOfItems','Status','TitleAndTextSources'],'UrlFeedFieldNames':['Url','RemoveUtmTags'],'FileFeedFieldNames':['Filename'],'Page':{'Limit':1000}})
            for feed in report['feeds'].get('Feeds',[]):
                url=feed.get('UrlFeed',{}).get('Url')
                if url:
                    raw,meta=read_public(url); tree=ET.fromstring(raw)
                    offers=[]
                    for offer in tree.findall('.//offer'):
                        offers.append({'id':offer.get('id'),'available':offer.get('available'),**{key:offer.findtext(key) for key in ['name','url','price','oldprice','vendorCode','categoryId','description']}})
                    report['feed_contents'].append({'feed_id':feed['Id'],**meta,'offer_count':len(offers),'offers':offers})
        except Exception as e: report['errors'].append({'section':'feeds','error':clean(str(e))})
    landing_urls=sorted({a['ResponsiveAd']['Href'] for a in ads if a.get('ResponsiveAd',{}).get('Href')})
    for url in landing_urls:
        try:
            raw,meta=read_public(url); html=raw.decode('utf-8',errors='replace')
            title=re.search(r'<title[^>]*>(.*?)</title>',html,re.S|re.I)
            meta.update(title=title.group(1) if title else None,anchors={x:bool(re.search(r'(?:id|name)=[\"\x27]'+re.escape(x)+r'[\"\x27]',html)) for x in ['bez-platy','s-platoi','platy']},contains_site_name='leoparts' in html.lower())
            report['public_pages'].append(meta)
        except Exception as e: report['public_pages'].append({'requested_url':url,'error':clean(str(e))})
    date_to=datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()
    report['report_period']={'date_from':'2026-10-20','date_to':date_to,'timezone':'Europe/Moscow','last_day_partial':True,'vat_included':True,'discount_included':True,'attribution':'LC','full_import_completed_utc':'2026-11-19T19:57:59.212926+00:00','daily_granularity_cannot_split_import_day':True}
    base_fields=['Date','CampaignId','AdGroupId','AdGroupName']
    requests=[('queries','SEARCH_QUERY_PERFORMANCE_REPORT',base_fields+['AdId','Query','CriterionType','CriterionId','Criterion','MatchedKeyword','MatchType','Impressions','Clicks','Cost','Conversions']),('criteria','CRITERIA_PERFORMANCE_REPORT',base_fields+['AdNetworkType','CriterionType','CriterionId','Criterion','Impressions','Clicks','Cost','Conversions']),('groups','ADGROUP_PERFORMANCE_REPORT',base_fields+['AdNetworkType','Impressions','Clicks','Cost','Conversions'])]
    for key,kind,fields in requests:
        try:
            params={'SelectionCriteria':{'DateFrom':'2026-10-20','DateTo':date_to,'Filter':[{'Field':'CampaignId','Operator':'IN','Values':[str(CID)]}]},'FieldNames':fields,'ReportName':'LEOPARTS-independent-'+key+'-'+str(int(time.time())),'ReportType':kind,'DateRangeType':'CUSTOM_DATE','Format':'TSV','IncludeVAT':'YES','IncludeDiscount':'YES','AttributionModels':['LC']}
            raw=request('reports',params,report=True)
            path=Path('data/direct_independent_'+key+'_20261120.tsv'); path.write_text(raw,encoding='utf-8')
            report['reports'][key]={'path':str(path),'at_utc':now(),'fields':fields,'rows':list(csv.DictReader(io.StringIO(raw),delimiter='\t'))}
        except Exception as e: report['errors'].append({'section':'report_'+key,'error':clean(str(e))})
        save(report)
    report['finished_at_utc']=now(); report['status']='complete' if not report['errors'] else 'partial'; save(report)
    print(json.dumps({'status':report['status'],'api_mutations':0,'errors':report['errors'],'finished_at_utc':report['finished_at_utc']},ensure_ascii=False))

if __name__=='__main__': run()
