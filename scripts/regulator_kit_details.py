import base64,contextlib,datetime,io,json,os,sys,traceback
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
R={'checked_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'price_writes':0,'stock_writes':0};log=io.StringIO()
def save():
 key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12);pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes());wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None));data=AESGCM(key).encrypt(nonce,json.dumps(R,ensure_ascii=False).encode(),None);p=ROOT/'reports/regulator-kit-details.enc';p.parent.mkdir(exist_ok=True);p.write_text(json.dumps({k:base64.b64encode(v).decode()for k,v in {'key':wrapped,'nonce':nonce,'data':data}.items()}))
def apply_market(y,biz):
 from market_content import read_rows,grouped,flatten
 ids=['0AM325025B','0am325025m'];before={r['offer']['offerId']:r['offer']for r in read_rows(biz,'offer-mappings',ids)};cards={r['offerId']:r for r in read_rows(biz,'offer-cards',ids)};R['market_before']=before;R['market_cards_before']=cards;save()
 payload=[]
 for sku,b in before.items():
  g=grouped(cards[sku].get('parameterValues',[]))
  for pid,value in {57046341:'Вид техники: Легковые автомобили\nКоробка передач: DSG7 DQ200 (0AM/0CW)\nСовместимость: Все мехатроники DQ200',7351754:('на один мехатроник устанавливаются два регулятора давления; цена указана за один соленоид'if sku=='0AM325025B'else 'комплект прокладок и уплотнений, сальники, пыльники, фильтр и сепараторная пластина; без гидроплиты; подходит на все мехатроники DQ200')}.items():g[pid]=[{'parameterId':pid,'value':value}]
  g[33663230]=[{'parameterId':33663230,'unitId':6145205,'value':'1'}]
  if sku=='0am325025m':g[21194330]=[{'parameterId':21194330,'valueId':34071233,'value':'ремкомплект'}]
  # These are internal store identifiers, not verified OEM part numbers.
  if sku=='0am325025m' and g.get(60134505)==[{'parameterId':60134505,'value':'0AM325025M'}]:g.pop(60134505)
  if sku=='0AM325025B' and g.get(60134505)==[{'parameterId':60134505,'value':'0am'}]:g.pop(60134505)
  offer={'offerId':sku,'marketCategoryId':71380208,'parameterValues':flatten(g)}
  if sku=='0am325025m':
   offer['videos']=['https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/0am325025m-original-video-20261115.mp4']
   offer['commodityCodes']=[{'code':'8708409909','type':'CUSTOMS_COMMODITY_CODE'}]
  payload.append({'offer':offer})
 R['market_payload']=payload;save();R['market_response']=y._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':payload});save();assert R['market_response'][0]==200 and R['market_response'][1].get('status')=='OK'
with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
 try:
  import wb_client as w
  cards=[x for x in w.list_cards()if x['vendorCode']in ['0AM325025B','6t30']];R['wb_cards']=cards;save()
  R['wb_subjects']={q:w.get_subjects(name=q)for q in ['регуляторы давления']};save()
  ids={7941};ids.update(x['subjectID']for v in R['wb_subjects'].values()for x in v if 'авто' in x.get('subjectName','').lower())
  R['wb_schema']={str(i):w.get_subject_characteristics(i)for i in ids};R['wb_prices']=[x for x in w.get_prices()if x['nmID']==670925505];save()
 except Exception:R['wb_error']=traceback.format_exc();save()
 try:
  import yandex_market_client as y
  from market_content import read_rows
  biz=y.get_business_id();
  if '--apply'in sys.argv:apply_market(y,biz)
  ids=['0AM325025B','0am325025m'];R['market_mappings']=read_rows(biz,'offer-mappings',ids);R['market_cards']=read_rows(biz,'offer-cards',ids);save()
  R['market_schema']=y._request('POST','/v2/category/71380208/parameters',params={'businessId':biz},json_body={});save()
  R['market_campaign']=y._request('POST','/v2/campaigns/141377963/offers',json_body={'offerIds':ids});save()
 except Exception:R['market_error']=traceback.format_exc();save()
R['log']=log.getvalue();save();print('Targeted two-product read finished; encrypted results. No price or stock writes.')
