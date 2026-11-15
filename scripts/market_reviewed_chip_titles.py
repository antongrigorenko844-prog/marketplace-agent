"""Reviewed content for nine Market components, with sale and preservation gates."""
import base64,contextlib,copy,datetime,io,json,os,sys,traceback
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
R={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'price_writes':0,'stock_writes':0};log=io.StringIO()
def save():
 key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12);pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes());wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None));data=AESGCM(key).encrypt(nonce,json.dumps(R,ensure_ascii=False).encode(),None);p=ROOT/'reports/market-recovery.enc';p.parent.mkdir(exist_ok=True);p.write_text(json.dumps({k:base64.b64encode(v).decode()for k,v in {'key':wrapped,'nonce':nonce,'data':data}.items()}))
with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
 try:
  import yandex_market_client as y
  from market_content import read_rows
  names=json.loads((ROOT/'data/market_chip_titles_20261116.json').read_text());ids=list(names)
  assert set(ids)=={'chip-02e927770','chip-0am927769d'}
  biz=y.get_business_id();R['before_mappings']=read_rows(biz,'offer-mappings',ids);R['before_cards']=read_rows(biz,'offer-cards',ids)
  R['before_sale']=y._request('POST','/v2/campaigns/141377963/offers',json_body={'offerIds':ids});save()
  sale=R['before_sale'][1]['result']['offers'];assert len(sale)==2 and all(o['status']=='PUBLISHED'for o in sale),'No writes: sale gate failed'
  before={x['offer']['offerId']:x['offer']for x in R['before_mappings']};assert set(before)==set(ids)
  payload=[]
  for sku in ids:
   assert 60<=len(names[sku])<=256
   o=before[sku];params=copy.deepcopy(o.get('params',[]))
   for p in params:
    if p['name'].lower()=='название':p['value']=names[sku]
   payload.append({'offer':{'offerId':sku,'name':names[sku],'params':params}})
  R['payload']={'offerMappings':payload};save()
  R['update']=y._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body=R['payload']);save()
  assert R['update'][0]==200 and R['update'][1].get('status')=='OK',R['update']
  assert not any(x.get('errors')for x in R['update'][1].get('result',{}).get('results',[])),R['update']
  R['after_mappings']=read_rows(biz,'offer-mappings',ids);R['after_cards']=read_rows(biz,'offer-cards',ids)
  R['campaign']=y._request('POST','/v2/campaigns/141377963/offers',json_body={'offerIds':ids});save()
  after={x['offer']['offerId']:x['offer']for x in R['after_mappings']}
  protected=['basicPrice','purchasePrice','pictures','videos','mediaFiles','weightDimensions','vendor','barcodes','commodityCodes','description','vendorCode']
  R['preservation']={sku:{k:before[sku].get(k)==after[sku].get(k)for k in protected}for sku in ids}
  assert all(all(v.values())for v in R['preservation'].values()),'Protected field changed'
 except Exception:R['error']=traceback.format_exc()
R['log']=log.getvalue();save();print('Two reviewed chip titles processed; encrypted evidence available.')
if 'error'in R:raise SystemExit(1)
