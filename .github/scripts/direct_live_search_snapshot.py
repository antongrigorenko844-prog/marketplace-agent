"""Read-only snapshot for one seller-owned Direct campaign; stdout contains sealed receipt only."""
import base64, csv, gzip, importlib.util, io, json, os, re, time, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

spec=importlib.util.spec_from_file_location('audit',Path('.github/scripts/direct_full_audit_20261120.py'))
d=importlib.util.module_from_spec(spec); spec.loader.exec_module(d)
CID=d.CID
def iso():return datetime.now(timezone.utc).isoformat()
end=datetime.now(ZoneInfo('Europe/Moscow')).date()
start=end-timedelta(days=13)
R={'at':iso(),'from':str(start),'to':str(end),'campaign':CID,'api_mode':'read_only','errors':{}}
def read(name,fn):
    try:R[name]=fn()
    except Exception as e:R['errors'][name]={'type':type(e).__name__,'message':str(e)[:160] if isinstance(e,RuntimeError) else ''}
def campaign():
    base=['Id','Name','Type','State','Status','Currency','NegativeKeywords','StatusPayment','TimeZone','DailyBudget','Funds']
    for names in (base,base[:6]):
        try:return d.api_get('campaigns',{'SelectionCriteria':{'Ids':[CID]},'FieldNames':names,'UnifiedCampaignFieldNames':['BiddingStrategy','AttributionModel','PriorityGoals','CounterIds']},'Campaigns')
        except RuntimeError:
            if names==base[:6]:raise
def report(key,kind,cols,goals=None,fallback=None):
    def issue(fields,name):
        params={'SelectionCriteria':{'DateFrom':str(start),'DateTo':str(end),'Filter':[{'Field':'CampaignId','Operator':'IN','Values':[str(CID)]}]},'FieldNames':fields,'ReportType':kind,'ReportName':'leoparts-current-'+name+'-'+datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f'),'DateRangeType':'CUSTOM_DATE','Format':'TSV','IncludeVAT':'YES','IncludeDiscount':'NO'}
        if goals:params.update(Goals=list(map(str,goals)),AttributionModels=['LC'])
        for _ in range(12):
            code,raw,hdr=d.request(d.BASE+'reports',{'params':params},reports=True)
            if code==200:
                reader=csv.DictReader(io.StringIO(raw),delimiter='\t');rows=list(reader)
                if not reader.fieldnames or not {'Date','CampaignId'}<=set(reader.fieldnames):raise RuntimeError('incomplete returned headers')
                if any(row['CampaignId']!=str(CID) or not str(start)<=row['Date']<=str(end) or None in row for row in rows):raise RuntimeError('unexpected scope')
                for row in rows:
                    if 'Query' in row and row['Query']:
                        row['Query']=re.sub(r'\b[A-HJ-NPR-Z0-9]{17}\b|[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}|(?<!\w)\+?\d[\d ()-]{10,}\d(?!\w)','[удалено]',row['Query'],flags=re.I)
                return {'fields':reader.fieldnames,'rows':rows,'captured':iso(),'request_id':next((v for k,v in hdr.items() if k.lower()=='requestid'),None)}
            if code not in (201,202):raise RuntimeError('HTTP '+str(code))
            time.sleep(min(10,max(1,int(next((str(v) for k,v in hdr.items() if k.lower()=='retryin'),'4')))))
        raise RuntimeError('Report processing')
    try:return issue(cols,key)
    except RuntimeError as e:
        if not fallback or 'HTTP 400' not in str(e):raise
        return issue(fallback,key+'-supported')
def funds():
    token=os.environ['YANDEX_DIRECT_TOKEN'];login=os.environ.get('YANDEX_DIRECT_LOGIN')
    sel={'Logins':[login]} if login else {}
    req=urllib.request.Request('https://api.direct.yandex.ru/live/v4/json/',data=json.dumps({'method':'AccountManagement','token':token,'locale':'ru','param':{'Action':'Get','SelectionCriteria':sel}}).encode(),headers={'Content-Type':'application/json; charset=utf-8'})
    with urllib.request.urlopen(req,timeout=22) as r:obj=json.load(r)
    if obj.get('error_code'):return {'error_code':obj['error_code']}
    accounts=(obj.get('data') or {}).get('Accounts',[])
    if len(accounts)!=1:return {'eligible_accounts':len(accounts),'ambiguous':True}
    return {k:v for k,v in accounts[0].items() if k in ('Amount','AmountAvailableForTransfer','Currency','Discount','AmountOverdraft','Sum','Balance')}
read('settings',campaign)
read('adgroups',lambda:d.api_get('adgroups',{'SelectionCriteria':{'CampaignIds':[CID]},'FieldNames':['Id','Name','CampaignId','Status','ServingStatus','NegativeKeywords']},'AdGroups'))
read('keywords',lambda:[{k:v for k,v in row.items() if k in ('AdGroupId','Keyword','State','Status')} for row in d.api_get('keywords',{'SelectionCriteria':{'CampaignIds':[CID]},'FieldNames':['Id','CampaignId','AdGroupId','Keyword','State','Status']},'Keywords')])
read('funds',funds)
read('daily',lambda:report('daily','CAMPAIGN_PERFORMANCE_REPORT',['Date','CampaignId','Impressions','Clicks','Cost','Conversions']))
read('search',lambda:report('query','SEARCH_QUERY_PERFORMANCE_REPORT',['Date','CampaignId','AdGroupId','Query','MatchedKeyword','CriterionType','Impressions','Clicks','Cost','Conversions'],fallback=['Date','CampaignId','AdGroupId','Query','Impressions','Clicks','Cost','Conversions']))
read('goals',lambda:report('goals','CUSTOM_REPORT',['Date','CampaignId','Conversions','Revenue'],goals=[682257540,675487735,675506966,675507028,685988392,686020680],fallback=['Date','CampaignId','Conversions']))
R['finished_at']=iso()
raw=gzip.compress(json.dumps(R,ensure_ascii=False).encode())
key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12)
pub=serialization.load_pem_public_key(('''-----BEGIN PUBLIC KEY-----
MIIBojANBgkqhkiG9w0BAQEFAAOCAY8AMIIBigKCAYEArtjmDGaOaHRYhyxTbBtP
NQqq8sa7u1ifIgTu8hDvhNnNKq/ZK+ZHCWNS9FKTcts9lPTn6CTrZSYyuh5gtwJ3
md4w3ELwuouZfe55yg2Ni2TrHnn0iVIkfSJwqXN34eDhoHSFb7lBhnErZNqX8zDM
LktTw5qwKKvUweUa3GWFGdxZ4zt5nRyu4ZjpEsKQcrL+F9HcE/l6cGhvKFZ/h+O1
ftX8VGIvehxC1lPQ0sj452YqQAWAIBjGscXtdeOzKBUd6vZI4qYtC/ALaLH0NG5b
nyBXfpl0BaFpyjMb0LgSPEary6uBEd+W6mMug1Tc5IsYqAm+XMVYIgFKXxXyO1yU
H0DN+vLsPEsne2reXME+oPP7OORFtvOG1lZCc1zpqsRlspiIR3DjHzrjPviDId2f
BkJRmIOBoWayBrPh3JnkvvMbpZ/mDFXBhKLddN0H5wJwiS0KJLxtO9/6xjFzs8fe
b8KDDhyV8OmmB+lOhjS5SXP8/2wjXQOE+aNOOFhQvKRBAgMBAAE=
-----END PUBLIC KEY-----
''').encode())
sealed=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
encode=lambda v:base64.b64encode(v).decode()
parts=encode(AESGCM(key).encrypt(nonce,raw,None))
print('DIRECT_KEY '+encode(sealed));print('DIRECT_NONCE '+encode(nonce))
for i in range(0,len(parts),4000):print('DIRECT_PART '+parts[i:i+4000])
