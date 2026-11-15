"""Change only the approved cover of 0am325025x; encrypt all private evidence."""
import base64,contextlib,copy,datetime,io,json,os,sys,time,traceback
from pathlib import Path
import requests
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
SKU='0am325025x'
URL='https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/0am325025x-cover-without-new-20261115.png'
APPLY='--apply' in sys.argv
TARGETS=set(os.environ.get('COVER_TARGETS','ozon,wb,market').split(','))
result={'sku':SKU,'apply':APPLY,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'stock_write_calls':0,'price_write_calls':0}
log=io.StringIO()
def one(rows,key):
 matches=[r for r in rows if r.get(key)==SKU];assert len(matches)==1,(key,len(matches));return matches[0]
def same(b,a,keys):
 for k in keys:assert b.get(k)==a.get(k),('Unrelated field changed',k)
def ozon():
 import ozon_client as oz
 before=one(oz.get_product_names([SKU]),'offer_id');info=one(oz.get_product_info_list([SKU]),'offer_id');prices=oz.get_prices_and_stocks([SKU]);stocks=oz.get_stocks([SKU])
 d=result['ozon']={'before':before,'info_before':info,'prices_before':prices,'stocks_before':stocks}
 primary=before.get('primary_image');assert isinstance(primary,str) and primary
 extra=[u for u in before.get('images',[]) if u!=primary and u!=URL];images=[URL]+extra
 if APPLY and 'ozon'in TARGETS:
  d['request']={'items':[{'offer_id':SKU,'primary_image':URL,'images':extra,'color_image':before.get('color_image')or''}]};d['response']=oz._post('/v2/product/pictures/import',d['request'])
  task=d['response'].get('task_id')or d['response'].get('result',{}).get('task_id')
  if task:
   for _ in range(24):
    time.sleep(5);d['import_status']=oz.get_import_status(task);items=d['import_status'].get('result',{}).get('items',[])
    if items and all(r.get('status')not in ['pending','processing','']for r in items):break
   assert not any(r.get('errors')for r in items),d['import_status']
  time.sleep(10)
 after=one(oz.get_product_names([SKU]),'offer_id');d['after']=after;d['info_after']=one(oz.get_product_info_list([SKU]),'offer_id');d['prices_after']=oz.get_prices_and_stocks([SKU]);d['stocks_after']=oz.get_stocks([SKU])
 same(before,after,['name','type_id','description_category_id','depth','width','height','weight'])
 # Ozon reorders attributes after a media import; compare every value by ID.
 assert sorted(before.get('attributes',[]),key=lambda x:(x['id'],x.get('complex_id',0)))==sorted(after.get('attributes',[]),key=lambda x:(x['id'],x.get('complex_id',0))),'Ozon attribute values changed'
 assert sorted(before.get('complex_attributes',[]),key=lambda x:(x.get('complex_id',0),x['id']))==sorted(after.get('complex_attributes',[]),key=lambda x:(x.get('complex_id',0),x['id'])),'Ozon video changed'
 assert before.get('images')==after.get('images'),'Ozon secondary photos changed'
 assert [r.get('price')for r in prices]==[r.get('price')for r in d['prices_after']],'Ozon prices changed'
 d['non_media_preserved']=True
def wb_run():
 import wb_client as wb
 before=one(wb.list_cards(),'vendorCode');nid=before['nmID'];prices=[r for r in wb.get_prices()if r['nmID']==nid]
 d=result['wb']={'before':before,'prices_before':prices};assert before.get('photos')
 if APPLY and 'wb'in TARGETS:
  image=requests.get(URL,timeout=60);image.raise_for_status();assert image.content.startswith(b'\x89PNG\r\n\x1a\n')
  d['request']={'nmId':nid,'photo_number':1,'source':URL}
  response=requests.post('https://content-api.wildberries.ru/content/v3/media/file',headers={'Authorization':os.environ['WB_API_TOKEN'],'X-Nm-Id':str(nid),'X-Photo-Number':'1'},files={'uploadfile':('cover.png',image.content,'image/png')},timeout=120)
  d['response']={'http':response.status_code,'body':response.json()};assert response.ok and not response.json().get('error');time.sleep(15)
 after=one(wb.list_cards(),'vendorCode');d['after']=after;d['prices_after']=[r for r in wb.get_prices()if r['nmID']==nid]
 same(before,after,['title','description','brand','subjectID','dimensions','sizes'])
 assert sorted(before.get('characteristics',[]),key=lambda x:x['id'])==sorted(after.get('characteristics',[]),key=lambda x:x['id'])
 assert prices==d['prices_after'],'WB prices changed'
 assert before.get('video')==after.get('video'),'WB video changed'
 assert len(before['photos'])==len(after['photos']),'WB photo count changed'
 assert before['photos'][1:]==after['photos'][1:],'WB secondary photo URLs changed'
 d['non_media_preserved']=True
def market():
 import yandex_market_client as ym
 from market_content import read_rows,preserve_typed_parameters,grouped
 b=ym.get_business_id();before=one([r['offer']for r in read_rows(b,'offer-mappings',[SKU])],'offerId');card=one(read_rows(b,'offer-cards',[SKU]),'offerId')
 d=result['market']={'before':before,'card_before':card};assert before.get('pictures')
 pics=[URL]+[u for u in before['pictures'][1:]if u!=URL]
 if APPLY and 'market'in TARGETS:
  payload=preserve_typed_parameters(b,[{'offer':{'offerId':SKU,'pictures':pics}}]);d['request']=payload
  st,res=ym._request('POST',f'/v2/businesses/{b}/offer-mappings/update',json_body={'offerMappings':payload});d['response']={'http':st,'body':res}
  assert st==200 and res.get('status')=='OK'
  assert not any(r.get('errors')for r in res.get('results',[]))
  for _ in range(15):
   after=one([r['offer']for r in read_rows(b,'offer-mappings',[SKU])],'offerId')
   if after.get('pictures')==pics:break
   time.sleep(5)
 else:after=before
 ca=one(read_rows(b,'offer-cards',[SKU]),'offerId');d['after']=after;d['card_after']=ca
 same(before,after,['name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions'])
 assert grouped(card.get('parameterValues',[]))==grouped(ca.get('parameterValues',[])),'Market characteristics changed'
 if APPLY and 'market'in TARGETS:assert after.get('pictures')==pics,'Market pictures not applied'
 assert before.get('mediaFiles',{}).get('videos')==after.get('mediaFiles',{}).get('videos'),'Market videos changed'
 d['non_media_preserved']=True
with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
 for name,fn in [('ozon',ozon),('wb',wb_run),('market',market)]:
  try:fn()
  except Exception:result.setdefault(name,{})['error']=traceback.format_exc()
result['log']=log.getvalue();result['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12);pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes())
wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None));data=AESGCM(key).encrypt(nonce,json.dumps(result,ensure_ascii=False).encode(),None)
out=ROOT/'reports/mech-x-cover-private.enc';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps({k:base64.b64encode(v).decode()for k,v in {'key':wrapped,'nonce':nonce,'data':data}.items()}))
print('Targeted cover operation finished; private evidence encrypted. Price and stock write calls: zero.')
sys.exit(1 if any(result.get(n,{}).get('error')for n in ['ozon','wb','market'])else 0)
