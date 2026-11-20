import base64,datetime as dt,json,os,urllib.parse,urllib.request,urllib.error
from zoneinfo import ZoneInfo
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
GOALS=(675487735,675506966,675507028,682257540,685988392,686020680)
CID=113839860
URL='https://api-metrika.yandex.net'
def get(path,params):
 req=urllib.request.Request(URL+path+'?'+urllib.parse.urlencode(params),headers={'Authorization':'OAuth '+os.environ['YANDEX_METRIKA_TOKEN']})
 with urllib.request.urlopen(req,timeout=35) as res: return json.load(res)
now=dt.datetime.now(dt.timezone.utc)
out={'utc':now.isoformat(),'counter':CID,'campaign':716316525}
try:
 c=get('/management/v1/counter/'+str(CID),{})['counter']
 out['timezone']=c.get('time_zone_name') or c.get('timezone') or 'Europe/Moscow'
 out['ecommerce_enabled']=c.get('ecommerce')
except Exception as exc:out['timezone']='Europe/Moscow'
today=now.astimezone(ZoneInfo(out['timezone'])).date()
metrics=['ym:s:visits']+['ym:s:goal'+str(g)+'visits' for g in GOALS]
ecom=['ym:s:visits','ym:s:ecommercePurchases','ym:s:ecommerceRUBConvertedRevenue']
def report(dim,ms,flt):
 params={'ids':CID,'date1':str(today-dt.timedelta(days=4)),'date2':str(today),'dimensions':dim,'metrics':','.join(ms),'accuracy':'full','lang':'ru','limit':'150','filters':flt}
 try:
  x=get('/stat/v1/data',params)
  return {'from':params['date1'],'to':params['date2'],'sampled':x.get('sampled'),'rows':[{'d':[v.get('name') for v in (r.get('dimensions')or[])],'m':r.get('metrics')} for r in x.get('data',[])],'totals':x.get('totals'),'metrics':ms,'total_rows':x.get('total_rows')}
 except urllib.error.HTTPError as exc:
  return {'http':exc.code,'code':str(exc.read()[:340],errors='replace').replace(os.environ['YANDEX_METRIKA_TOKEN'],'[redacted]')}
except_none=''
direct="ym:s:lastDirectClickOrder=='716316525'"
utm="ym:s:lastUTMCampaign=='dq200_feed_search'"
for k,flt in [('exact_direct',direct),('campaign_utm',utm)]:
 out[k]=report('ym:s:date',metrics,flt)
 out[k+'_ecommerce']=report('ym:s:date',ecom,flt)
out['overall_today_context']=report('ym:s:date',metrics,'')
out['finished']=dt.datetime.now(dt.timezone.utc).isoformat()
pub=serialization.load_pem_public_key(b'''-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA2OO3u+FQlZDYf2wPEs47
Hv2rNzKZpat0vvvS4tgGPXIDndnMo2FwmdJR2c5mNM86RD90Jm10F5gsmI0Rg2N0
fNIsi/bpQBMz7gHrBBKS8wVXMLYbIwhOo7cZhAKgEASbCTP4fPaMvD/D9tB1VQIW
se1lk8FUl65eSfGx0h2OPEiosJpArTeJYRi//ch0/uYBZnPRjtcWxRxYrs1IwLhk
FNOQ12mK37Bx87iv9R9hzuLmuteIeTOYaniVNPDFY3OgfpRRjV7YOf5+LDXMncDD
pSDy1hXiQPQ7HEYbjQUU3IR+jFIUidlHOFTRJkU3a8BtZYLBvFMcCTka+YfSRubS
pwIDAQAB
-----END PUBLIC KEY-----''')
key=AESGCM.generate_key(bit_length=256); nonce=os.urandom(12)
encrypted={'k':base64.b64encode(pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))).decode(),'n':base64.b64encode(nonce).decode(),'c':base64.b64encode(AESGCM(key).encrypt(nonce,json.dumps(out,ensure_ascii=False).encode(),None)).decode()}
print('METRIKA_ENCRYPTED '+json.dumps(encrypted))
