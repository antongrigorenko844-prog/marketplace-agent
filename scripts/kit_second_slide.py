"""Exact approved second slide only. Never write prices, stocks, attributes or documents."""
import base64,contextlib,datetime,hashlib,io,json,os,sys,time,traceback
from pathlib import Path
import requests
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
SKU='plita-filtr-pylniki-sal-orig';URL='https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/plita-filtr-pylniki-sal-orig-contents-20261116.png'
APPLY='--apply'in sys.argv;TARGETS=set(os.environ.get('SLIDE_TARGETS','ozon,wb,market').split(','))
R={'sku':SKU,'apply':APPLY,'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'price_write_calls':0,'stock_write_calls':0};LOG=io.StringIO()
def save():
 key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12);pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes())
 wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None));data=AESGCM(key).encrypt(nonce,json.dumps(R,ensure_ascii=False).encode(),None)
 p=ROOT/'reports/kit-second-slide-private.enc';p.parent.mkdir(exist_ok=True);p.write_text(json.dumps({k:base64.b64encode(v).decode()for k,v in {'key':wrapped,'nonce':nonce,'data':data}.items()}))
def one(rows,key):
 x=[r for r in rows if r.get(key)==SKU];assert len(x)==1,(key,len(x));return x[0]
def same(b,a,keys):
 for k in keys:assert b.get(k)==a.get(k),('Unrelated change',k)
def norm(x):
 if isinstance(x,str):return x.replace('https://ir-20.ozone.ru/','https://ir.ozone.ru/')
 if isinstance(x,list):return[norm(z)for z in x]
 if isinstance(x,dict):return{k:norm(v)for k,v in x.items()}
 return x
def ozon():
 import ozon_client as o
 b=one(o.get_product_names([SKU]),'offer_id');d=R['ozon']={'before':b,'info_before':one(o.get_product_info_list([SKU]),'offer_id'),'prices_before':o.get_prices_and_stocks([SKU]),'stocks_before':o.get_stocks([SKU])};save()
 if APPLY and 'ozon'in TARGETS:
  assert len(b.get('images',[]))==0,'Unexpected gallery; inspect before retrying'
  req={'items':[{'offer_id':SKU,'primary_image':b['primary_image'],'images':[URL],'color_image':b.get('color_image')or''}]};d['request']=req;d['response']=o._post('/v2/product/pictures/import',req);save()
  for _ in range(24):
   time.sleep(5);a=one(o.get_product_names([SKU]),'offer_id')
   if len(a.get('images',[]))==1:break
  assert len(a.get('images',[]))==1,'Gallery processing'
 else:a=b
 d['after']=a;d['info_after']=one(o.get_product_info_list([SKU]),'offer_id');d['prices_after']=o.get_prices_and_stocks([SKU]);d['stocks_after']=o.get_stocks([SKU])
 same(b,a,['name','type_id','description_category_id','depth','width','height','weight'])
 for key in ['attributes','complex_attributes']:
  assert sorted(b.get(key,[]),key=lambda x:(x.get('complex_id',0),x['id']))==sorted(a.get(key,[]),key=lambda x:(x.get('complex_id',0),x['id'])),key
 assert norm(b['primary_image'])==norm(a['primary_image']),'Cover changed'
 assert [r.get('price')for r in d['prices_before']]==[r.get('price')for r in d['prices_after']]
 d['cover_and_other_fields_preserved']=True;d['photo_count']=1+len(a.get('images',[]));save()
def wb():
 import wb_client as w
 b=one(w.list_cards(),'vendorCode');nid=b['nmID'];assert nid==1605380809
 d=R['wb']={'before':b,'prices_before':[r for r in w.get_prices()if r['nmID']==nid]};save()
 if APPLY and 'wb'in TARGETS:
  assert len(b.get('photos',[]))==1,'Unexpected gallery; inspect before retrying'
  asset=requests.get(URL,timeout=60);asset.raise_for_status();assert asset.content.startswith(b'\x89PNG\r\n\x1a\n')
  r=requests.post('https://content-api.wildberries.ru/content/v3/media/file',headers={'Authorization':os.environ['WB_API_TOKEN'],'X-Nm-Id':str(nid),'X-Photo-Number':'2'},files={'uploadfile':('contents.png',asset.content,'image/png')},timeout=120)
  d['response']={'http':r.status_code,'body':r.json()};save();assert r.ok and not r.json().get('error')
  for _ in range(24):
   time.sleep(5);a=one(w.list_cards(),'vendorCode')
   if len(a.get('photos',[]))==2:break
  assert len(a.get('photos',[]))==2,'Gallery processing'
 else:a=b
 d['after']=a;d['prices_after']=[r for r in w.get_prices()if r['nmID']==nid]
 same(b,a,['title','description','brand','subjectID','dimensions','sizes','video'])
 assert sorted(b.get('characteristics',[]),key=lambda x:x['id'])==sorted(a.get('characteristics',[]),key=lambda x:x['id'])
 assert b['photos'][0]==a['photos'][0];assert d['prices_before']==d['prices_after'];d['cover_and_other_fields_preserved']=True;d['photo_count']=len(a['photos']);save()
def market():
 import yandex_market_client as y
 from market_content import read_rows,preserve_typed_parameters,grouped
 biz=y.get_business_id();b=one([r['offer']for r in read_rows(biz,'offer-mappings',[SKU])],'offerId');c=one(read_rows(biz,'offer-cards',[SKU]),'offerId');d=R['market']={'before':b,'card_before':c};save()
 pics=b['pictures'][:1]+[URL]+[u for u in b['pictures'][1:]if u!=URL]
 if APPLY and 'market'in TARGETS and pics!=b['pictures']:
  req=preserve_typed_parameters(biz,[{'offer':{'offerId':SKU,'pictures':pics}}]);d['request']=req
  st,r=y._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':req});d['response']={'http':st,'body':r};save();assert st==200 and r.get('status')=='OK';assert not any(i.get('errors')for i in r.get('results',[]))
  for _ in range(24):
   time.sleep(5);a=one([r['offer']for r in read_rows(biz,'offer-mappings',[SKU])],'offerId')
   if a.get('pictures')==pics:break
  assert a.get('pictures')==pics
 else:a=b
 ca=one(read_rows(biz,'offer-cards',[SKU]),'offerId');d['after']=a;d['card_after']=ca
 same(b,a,['name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions'])
 assert grouped(c.get('parameterValues',[]))==grouped(ca.get('parameterValues',[]));assert b.get('mediaFiles',{}).get('videos')==a.get('mediaFiles',{}).get('videos')
 d['cover_and_other_fields_preserved']=True;d['photo_count']=len(a['pictures']);save()
with contextlib.redirect_stdout(LOG),contextlib.redirect_stderr(LOG):
 for name,fn in [('ozon',ozon),('wb',wb),('market',market)]:
  try:fn()
  except Exception:R.setdefault(name,{})['error']=traceback.format_exc();save()
R['log']=LOG.getvalue();R['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
print('Exact approved slide processed. No price or stock writes. Private verification encrypted.')
sys.exit(1 if any(R.get(n,{}).get('error')for n in ['ozon','wb','market'])else 0)
