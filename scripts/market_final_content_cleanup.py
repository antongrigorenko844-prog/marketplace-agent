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
  plans=json.loads((ROOT/'data/market_final_content_cleanup_20261116.json').read_text())['offers'];ids=[p['offerId']for p in plans]
  assert ids==['osnovanie-platy-dq200','shleif-platy-dq200','vint-kollektora-ea888','0am325025m']
  biz=y.get_business_id();R['before_mappings']=read_rows(biz,'offer-mappings',ids);R['before_cards']=read_rows(biz,'offer-cards',ids);save()
  mappings={m['offer']['offerId']:m for m in R['before_mappings']};cards={c['offerId']:c for c in R['before_cards']};assert set(mappings)==set(cards)==set(ids)
  payload=[]
  for plan in plans:
   sku=plan['offerId'];o=mappings[sku]['offer'];c=cards[sku];wrong=plan['wrong_oem']
   existing=[p for p in c.get('parameterValues',[])if p['parameterId']==60134505]
   assert not existing or all(p.get('value')in ['',wrong]for p in existing),(sku,existing)
   name=plan.get('name',o['name']);description=plan.get('description',o['description'])
   params=[];removed=[]
   for p in o.get('params',[]):
    q=copy.deepcopy(p);key=q['name'].lower().replace('-','').replace(' ','')
    if key=='oemномер':
     assert q.get('value')in ['',wrong],(sku,q);removed.append(q);continue
    if plan.get('material') and key=='материал':q['value']=plan['material']
    if key=='название':q['value']=name
    if key=='аннотация':q['value']=description
    params.append(q)
   R.setdefault('legacy_oem_removed',{})[sku]=removed
   offer={'offerId':sku,'name':name,'description':description,'marketCategoryId':c['mapping']['marketCategoryId'],'params':params,'parameterValues':[{'parameterId':60134505,'value':''}]}
   assert set(offer)=={'offerId','name','description','marketCategoryId','params','parameterValues'}
   payload.append({'offer':offer})
  R['payload']={'offerMappings':payload};save();R['update']=y._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body=R['payload']);save()
  assert R['update'][0]==200 and R['update'][1].get('status')=='OK',R['update']
  assert not any(x.get('errors')for x in R['update'][1].get('result',{}).get('results',[])),R['update']
  R['after_mappings']=read_rows(biz,'offer-mappings',ids);R['after_cards']=read_rows(biz,'offer-cards',ids)
  R['campaign']=y._request('POST','/v2/campaigns/141377963/offers',json_body={'offerIds':ids});save()
  before={x['offer']['offerId']:x['offer']for x in R['before_mappings']};after={x['offer']['offerId']:x['offer']for x in R['after_mappings']}
  protected=['basicPrice','purchasePrice','pictures','videos','mediaFiles','weightDimensions','vendor','barcodes','commodityCodes']
  R['preservation']={sku:{k:before[sku].get(k)==after[sku].get(k)for k in protected}for sku in ids}
  assert all(all(v.values())for v in R['preservation'].values()),'Protected fields changed; inspect encrypted evidence'
 except Exception:R['error']=traceback.format_exc()
R['log']=log.getvalue();save();print('Targeted content cleanup finished; encrypted result available.')
if 'error'in R:raise SystemExit(1)
