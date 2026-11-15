"""Change only the approved cover of 0AM325025B; encrypt all private evidence."""
import base64,contextlib,copy,datetime,io,json,os,sys,time,traceback
from pathlib import Path
import requests
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
SKU='0AM325025B'
URL='https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/0AM325025B-price-per-unit-20261115.png'
APPLY='--apply' in sys.argv
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
 if APPLY:
  d['request']={'product_id':info['id'],'images':images};d['response']=oz._post('/v1/product/pictures/import',d['request'])
  time.sleep(15)
 after=one(oz.get_product_names([SKU]),'offer_id');d['after']=after;d['info_after']=one(oz.get_product_info_list([SKU]),'offer_id');d['prices_after']=oz.get_prices_and_stocks([SKU]);d['stocks_after']=oz.get_stocks([SKU])
 same(before,after,['name','attributes','complex_attributes','type_id','description_category_id','depth','width','height','weight'])
 assert [r.get('price')for r in prices]==[r.get('price')for r in d['prices_after']],'Ozon prices changed'
 d['non_media_preserved']=True
def wb_run():
 import wb_client as wb
 from video_sources import wb_video_for
 before=one(wb.list_cards(),'vendorCode');nid=before['nmID'];prices=[r for r in wb.get_prices()if r['nmID']==nid]
 d=result['wb']={'before':before,'prices_before':prices};assert before.get('photos')
 video=wb_video_for(SKU,before.get('video',''))
 assert not before.get('video') or video.endswith('.mp4'),'Cannot preserve video'
 urls=[URL]+[p['big']for p in before['photos'][1:]]+([video]if video else[])
 if APPLY:
  d['request']={'nmId':nid,'data':urls};d['response']=wb.update_media(nid,urls);assert not d['response'].get('error');time.sleep(10)
 after=one(wb.list_cards(),'vendorCode');d['after']=after;d['prices_after']=[r for r in wb.get_prices()if r['nmID']==nid]
 same(before,after,['title','description','brand','subjectID','dimensions','sizes'])
 assert sorted(before.get('characteristics',[]),key=lambda x:x['id'])==sorted(after.get('characteristics',[]),key=lambda x:x['id'])
 assert prices==d['prices_after'],'WB prices changed'
 assert bool(before.get('video'))==bool(after.get('video')),'WB video changed'
 assert len(before['photos'])==len(after['photos']),'WB photo count changed'
 d['non_media_preserved']=True
def market():
 import yandex_market_client as ym
 from market_content import read_rows,preserve_typed_parameters,grouped
 b=ym.get_business_id();before=one([r['offer']for r in read_rows(b,'offer-mappings',[SKU])],'offerId');card=one(read_rows(b,'offer-cards',[SKU]),'offerId')
 d=result['market']={'before':before,'card_before':card};assert before.get('pictures')
 pics=[URL]+[u for u in before['pictures'][1:]if u!=URL]
 if APPLY:
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
 if APPLY:assert after.get('pictures')==pics,'Market pictures not applied'
 d['non_media_preserved']=True
with contextlib.redirect_stdout(log),contextlib.redirect_stderr(log):
 for name,fn in [('ozon',ozon),('wb',wb_run),('market',market)]:
  try:fn()
  except Exception:result.setdefault(name,{})['error']=traceback.format_exc()
result['log']=log.getvalue();result['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat()
key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12);pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes())
wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None));data=AESGCM(key).encrypt(nonce,json.dumps(result,ensure_ascii=False).encode(),None)
out=ROOT/'reports/solenoid-cover-private.enc';out.parent.mkdir(exist_ok=True);out.write_text(json.dumps({k:base64.b64encode(v).decode()for k,v in {'key':wrapped,'nonce':nonce,'data':data}.items()}))
print('Targeted cover operation finished; private evidence encrypted. Price and stock write calls: zero.')
sys.exit(1 if any(result.get(n,{}).get('error')for n in ['ozon','wb','market'])else 0)
