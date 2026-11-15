import base64,contextlib,datetime,io,json,os,sys,traceback
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
IDS=['chip-02e927770','chip-0am927769d','datchik-peredach-dq200','datchik-scepleniya-dq200','derzhatel-datchikov-dq200','fpc-shleif-dq200','klipsa-datchika-peredach-dq200','kolodka-solenoidov-dq200','kryshka-platy-dq200','osnovanie-platy-dq200','shleif-platy-dq200','vint-kollektora-ea888','0AM325025B','0am325025m']
R={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'price_writes':0,'stock_writes':0};log=io.StringIO()
def save():
 key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12);pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes());wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None));data=AESGCM(key).encrypt(nonce,json.dumps(R,ensure_ascii=False).encode(),None);p=ROOT/'reports/market-recovery.enc';p.parent.mkdir(exist_ok=True);p.write_text(json.dumps({k:base64.b64encode(v).decode()for k,v in {'key':wrapped,'nonce':nonce,'data':data}.items()}))
with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
 try:
  import yandex_market_client as y
  from market_content import read_rows
  biz=y.get_business_id();R['mappings']=read_rows(biz,'offer-mappings',IDS);R['cards']=read_rows(biz,'offer-cards',IDS);save()
  R['campaign']=y._request('POST','/v2/campaigns/141377963/offers',json_body={'offerIds':IDS});save()
  if os.environ.get('MARKET_AUDIT_SCHEMA','0')=='1':
   cats={x['mapping']['marketCategoryId']for x in R['cards']};R['schema']={}
   for cat in cats:R['schema'][str(cat)]=y._request('POST',f'/v2/category/{cat}/parameters',params={'businessId':biz},json_body={});save()
 except Exception:R['error']=traceback.format_exc()
R['log']=log.getvalue();save();print('Read-only recovery audit completed; encrypted result.')
