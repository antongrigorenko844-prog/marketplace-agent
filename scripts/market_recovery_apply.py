"""Apply only reviewed metadata for 11 component cards; encrypted evidence."""
import base64,contextlib,datetime,io,json,os,sys,traceback
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
  plans=json.loads((ROOT/'data/market_recovery_plan_20261115.json').read_text())['products'];ids=[p['offerId']for p in plans]
  assert len(ids)==11 and len(set(ids))==11
  biz=y.get_business_id();R['before_mappings']=read_rows(biz,'offer-mappings',ids);R['before_cards']=read_rows(biz,'offer-cards',ids);save()
  by={c['offerId']:c for c in R['before_cards']};assert set(by)==set(ids)
  code,schema=y._request('POST','/v2/category/71380208/parameters',params={'businessId':biz},json_body={});assert code==200
  params={p['id']:p for p in schema['result']['parameters']};assert params[21194330]['allowCustomValues']
  payload=[]
  for plan in plans:
   sku=plan['offerId'];c=by[sku]
   # Do not carry OEMs of the whole parent assembly over as component OEMs.
   pv={p['parameterId']:dict(p) for p in c.get('parameterValues',[]) if p['parameterId'] in params and p['parameterId'] not in {60134505,53973426}}
   pv[200]={'parameterId':200,'value':sku}
   typ=plan['type'];dictionary=params[21194330].get('values',[]);match=next((v for v in dictionary if v.get('value')==typ or v.get('name')==typ),None)
   pv[21194330]={'parameterId':21194330,'value':typ}
   if match:pv[21194330]['valueId']=match.get('id',match.get('valueId'))
   pv[14805583]={'parameterId':14805583,'unitId':6145205,'value':str(plan['units'])}
   pv[33663230]={'parameterId':33663230,'value':'1'}
   pv[57046341]={'parameterId':57046341,'value':'Вид техники: Легковые автомобили\nСовместимость: '+plan['compatibility']}
   offer={'offerId':sku,'marketCategoryId':plan['categoryId'],'parameterValues':list(pv.values()),'commodityCodes':[{'type':'CUSTOMS_COMMODITY_CODE','code':plan['customsCode']}]}
   assert set(offer)=={'offerId','marketCategoryId','parameterValues','commodityCodes'}
   assert all(p['parameterId'] in params for p in offer['parameterValues'])
   payload.append({'offer':offer})
  R['payload']={'offerMappings':payload};save()
  R['update']=y._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body=R['payload']);save()
  assert R['update'][0]==200,R['update']
  R['after_mappings']=read_rows(biz,'offer-mappings',ids);R['after_cards']=read_rows(biz,'offer-cards',ids)
  R['campaign']=y._request('POST','/v2/campaigns/141377963/offers',json_body={'offerIds':ids});save()
  before={x['offer']['offerId']:x['offer']for x in R['before_mappings']};after={x['offer']['offerId']:x['offer']for x in R['after_mappings']}
  protected=['basicPrice','purchasePrice','pictures','videos','mediaFiles','weightDimensions','name','description','vendor','barcodes']
  R['preservation']={sku:{k:before[sku].get(k)==after[sku].get(k) for k in protected}for sku in ids}
  assert all(all(v.values()) for v in R['preservation'].values()),'Protected fields unexpectedly changed; inspect encrypted evidence'
 except Exception:R['error']=traceback.format_exc()
R['log']=log.getvalue();save();print('Reviewed metadata batch completed; inspect encrypted result.')
if 'error' in R:raise SystemExit(1)
