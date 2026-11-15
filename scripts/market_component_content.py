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
  plans=json.loads((ROOT/'data/market_component_content_20261116.json').read_text())['offers'];ids=[p['offerId']for p in plans]
  assert len(ids)==9 and len(set(ids))==9
  gateids=ids+['chip-02e927770','chip-0am927769d'];biz=y.get_business_id()
  R['publication_gate']=y._request('POST','/v2/campaigns/141377963/offers',json_body={'offerIds':gateids});save()
  gate={o['offerId']:o for o in R['publication_gate'][1]['result']['offers']}
  assert set(gate)==set(gateids) and all(o['status']=='PUBLISHED'for o in gate.values()),'Not all eleven offers have returned to sale; no writes performed.'
  R['before_mappings']=read_rows(biz,'offer-mappings',ids);R['before_cards']=read_rows(biz,'offer-cards',ids);save()
  cards={c['offerId']:c for c in R['before_cards']};assert set(cards)==set(ids)
  R['schema']={};payload=[]
  for plan in plans:
   sku=plan['offerId'];c=cards[sku];cat=c['mapping']['marketCategoryId'];category=c['mapping']['marketCategoryName']
   if sku=='vint-kollektora-ea888':assert 'метизы' in category.lower(),category
   else:assert cat==71380208,(sku,category)
   if str(cat) not in R['schema']:R['schema'][str(cat)]=y._request('POST',f'/v2/category/{cat}/parameters',params={'businessId':biz},json_body={})
   valid={p['id']for p in R['schema'][str(cat)][1]['result']['parameters']}
   groups={}
   for p in c.get('parameterValues',[]):
    if p['parameterId'] in valid:groups.setdefault(p['parameterId'],[]).append(copy.deepcopy(p))
   if plan.get('clear_wrong_oem'):
    existing=groups.get(60134505,[])
    assert not existing or all(p.get('value') in ['',plan['clear_wrong_oem']]for p in existing),(sku,existing)
    if existing:groups[60134505]=[{'parameterId':60134505,'value':''}]
   if plan.get('confirmed_material'):
    previous='\n'.join(p.get('value','')for p in groups.get(57046341,[]))
    lines=[ln for ln in previous.splitlines()if not ln.lower().startswith('материал:')]
    lines.append('Материал: '+plan['confirmed_material']);groups[57046341]=[{'parameterId':57046341,'value':'\n'.join(lines)}]
   assert 60<=len(plan['name'])<=256 and 500<=len(plan['description'])<=6000
   offer={'offerId':sku,'name':plan['name'],'description':plan['description'],'marketCategoryId':cat,'parameterValues':[v for vs in groups.values()for v in vs]}
   assert set(offer)=={'offerId','name','description','marketCategoryId','parameterValues'}
   payload.append({'offer':offer})
  R['payload']={'offerMappings':payload};save()
  R['update']=y._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body=R['payload']);save()
  assert R['update'][0]==200 and R['update'][1].get('status')=='OK',R['update']
  R['after_mappings']=read_rows(biz,'offer-mappings',ids);R['after_cards']=read_rows(biz,'offer-cards',ids)
  R['campaign']=y._request('POST','/v2/campaigns/141377963/offers',json_body={'offerIds':ids});save()
  before={x['offer']['offerId']:x['offer']for x in R['before_mappings']};after={x['offer']['offerId']:x['offer']for x in R['after_mappings']}
  protected=['basicPrice','purchasePrice','pictures','videos','mediaFiles','weightDimensions','vendor','barcodes','commodityCodes']
  R['preservation']={sku:{k:before[sku].get(k)==after[sku].get(k) for k in protected}for sku in ids}
  assert all(all(v.values())for v in R['preservation'].values()),'Protected fields changed; inspect encrypted evidence'
 except Exception:R['error']=traceback.format_exc()
R['log']=log.getvalue();save();print('Reviewed content operation finished; encrypted result available.')
if 'error' in R:raise SystemExit(1)
