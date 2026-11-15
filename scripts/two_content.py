"""Append approved media and add missing rich for exactly two SKUs. No commercial writes."""
import base64,contextlib,copy,datetime,io,json,os,sys,time,traceback
from pathlib import Path
import requests
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
SKUS=('0AM325025B','0am325025m');APPLY='--apply'in sys.argv
PLATFORMS=set(os.environ.get('CONTENT_TARGETS','ozon,wb,market').split(','))
ASSETS=json.loads((ROOT/'data/two_content_assets_20261115.json').read_text())
RESULT={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'apply':APPLY,'price_write_calls':0,'stock_write_calls':0}
LOG=io.StringIO()
def save():
 key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12)
 pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes())
 wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
 data=AESGCM(key).encrypt(nonce,json.dumps(RESULT,ensure_ascii=False).encode(),None)
 p=ROOT/'reports/two-content-private.enc';p.parent.mkdir(exist_ok=True)
 p.write_text(json.dumps({k:base64.b64encode(v).decode()for k,v in {'key':wrapped,'nonce':nonce,'data':data}.items()}))
def one(rows,key,sku):
 rows=[r for r in rows if r.get(key)==sku];assert len(rows)==1,(key,sku,len(rows));return rows[0]
def urls(sku):return [r['url']for r in ASSETS[sku]]
def same(b,a,keys):
 for k in keys:assert b.get(k)==a.get(k),('Unrelated change',k)
def attrs(d):return sorted([x for x in d.get('attributes',[])if x['id']!=11254],key=lambda x:(x.get('complex_id',0),x['id']))
def video(d):return sorted(d.get('complex_attributes',[]),key=lambda x:(x.get('complex_id',0),x['id']))
def rich(d):
 values=[v.get('value')for a in d.get('attributes',[])if a['id']==11254 for v in a.get('values',[])if v.get('value')]
 assert len(values)<=1;return json.loads(values[0])if values else None
def norm(x):
 if isinstance(x,str):return x.replace('https://ir-20.ozone.ru/','https://ir.ozone.ru/')
 if isinstance(x,list):return [norm(z)for z in x]
 if isinstance(x,dict):return {k:norm(v)for k,v in x.items()}
 return x
def ozon():
 import ozon_client as oz
 d=RESULT['ozon']={};before={r['offer_id']:r for r in oz.get_product_names(list(SKUS))};assert set(before)==set(SKUS)
 d['before']=before;d['prices_before']=oz.get_prices_and_stocks(list(SKUS));d['stocks_before']=oz.get_stocks(list(SKUS));save()
 for sku in SKUS:
  b=before[sku];p=d.setdefault(sku,{})
  if APPLY and 'ozon'in PLATFORMS:
   extra=list(b.get('images',[]));expected_base={'0AM325025B':1,'0am325025m':0}[sku]
   assert len(extra)in (expected_base,expected_base+2),('Unexpected Ozon gallery',sku,len(extra))
   if len(extra)==expected_base:
    payload={'items':[{'offer_id':sku,'primary_image':b['primary_image'],'images':extra+urls(sku),'color_image':b.get('color_image')or''}]}
    p['gallery_response']=oz._post('/v2/product/pictures/import',payload);save()
    for _ in range(36):
     time.sleep(5);a=one(oz.get_product_names([sku]),'offer_id',sku)
     if len(a.get('images',[]))==expected_base+2:break
    assert len(a.get('images',[]))==expected_base+2,'Ozon gallery pending'
   else:a=b
   assert norm(a.get('primary_image'))==norm(b.get('primary_image')),'Cover changed'
   assert norm(a.get('images',[])[:expected_base])==norm(b.get('images',[])[:expected_base]),'Existing photos changed'
   template=json.loads((ROOT/'data/ozon_rich'/f'{sku}.json').read_text())
   for block in template['content']:
    for row in block.get('blocks',[]):
     idx=urls(sku).index(row['img']['src']);u=a['images'][expected_base+idx]
     row['img']['src']=u;row['img']['srcMobile']=u
   p['expected_rich']=template;old=rich(a)
   assert old is None or norm(old)==norm(template),'Existing rich differs; preserved'
   if old is None:
    p['rich_response']=oz.update_attributes([{'offer_id':sku,'attributes':[{'id':11254,'complex_id':0,'values':[{'value':json.dumps(template,ensure_ascii=False)}]}]}]);save()
    for _ in range(36):
     time.sleep(5);a=one(oz.get_product_names([sku]),'offer_id',sku)
     if norm(rich(a))==norm(template):break
    assert norm(rich(a))==norm(template),'Rich still processing'
  a=one(oz.get_product_names([sku]),'offer_id',sku);p['after']=a
  same(b,a,['name','type_id','description_category_id','depth','width','height','weight'])
  assert attrs(b)==attrs(a),'Non-rich attributes changed';assert video(b)==video(a),'Video changed'
  p['cover_preserved']=norm(b['primary_image'])==norm(a['primary_image']);assert p['cover_preserved']
  p['photo_count']=1+len(a.get('images',[]));p['rich_blocks']=len((rich(a)or{}).get('content',[]));p['video_preserved']=True
 d['prices_after']=oz.get_prices_and_stocks(list(SKUS));d['stocks_after']=oz.get_stocks(list(SKUS))
 assert {r['offer_id']:r.get('price')for r in d['prices_before']}=={r['offer_id']:r.get('price')for r in d['prices_after']},'Prices changed'
 d['prices_preserved']=True;save()
def wb():
 import wb_client as w
 cards=w.list_cards();prices=w.get_prices();d=RESULT['wb']={}
 for sku in SKUS:
  b=one(cards,'vendorCode',sku);nid=b['nmID'];p=d[sku]={'before':b,'prices_before':[r for r in prices if r['nmID']==nid]};save()
  base={'0AM325025B':2,'0am325025m':3}[sku]
  if APPLY and 'wb'in PLATFORMS:
   assert base<=len(b.get('photos',[]))<=base+2,('Unexpected WB gallery',sku)
   for index,asset in enumerate(ASSETS[sku]):
    slot=base+index+1
    if len(b['photos'])>=slot:continue
    image=requests.get(asset['url'],timeout=60);image.raise_for_status()
    import hashlib
    assert hashlib.sha256(image.content).hexdigest()==asset['sha256']
    response=requests.post('https://content-api.wildberries.ru/content/v3/media/file',headers={'Authorization':os.environ['WB_API_TOKEN'],'X-Nm-Id':str(nid),'X-Photo-Number':str(slot)},files={'uploadfile':(asset['name'],image.content,'image/png')},timeout=120)
    p.setdefault('responses',[]).append({'slot':slot,'http':response.status_code,'body':response.json()});save()
    assert response.ok and not response.json().get('error');time.sleep(3)
   for _ in range(15):
    time.sleep(5);a=one(w.list_cards(),'vendorCode',sku)
    if len(a.get('photos',[]))==base+2:break
   assert len(a.get('photos',[]))==base+2,'WB photo count not updated'
  else:a=b
  p['after']=a;same(b,a,['title','description','brand','subjectID','dimensions','sizes','video'])
  assert sorted(b.get('characteristics',[]),key=lambda x:x['id'])==sorted(a.get('characteristics',[]),key=lambda x:x['id'])
  assert b['photos'][:base]==a['photos'][:base],'Existing WB photos changed';p['non_media_preserved']=True
  p['photo_count']=len(a['photos']);p['prices_after']=[r for r in w.get_prices()if r['nmID']==nid];assert p['prices_after']==p['prices_before'];save()
def market():
 import yandex_market_client as y
 from market_content import read_rows,preserve_typed_parameters,grouped
 biz=y.get_business_id();d=RESULT['market']={}
 for sku in SKUS:
  b=one([r['offer']for r in read_rows(biz,'offer-mappings',[sku])],'offerId',sku);c=one(read_rows(biz,'offer-cards',[sku]),'offerId',sku)
  p=d[sku]={'before':b,'card_before':c};save();pics=b.get('pictures',[])+[u for u in urls(sku)if u not in b.get('pictures',[])]
  if APPLY and 'market'in PLATFORMS and pics!=b.get('pictures'):
   payload=preserve_typed_parameters(biz,[{'offer':{'offerId':sku,'pictures':pics}}]);st,res=y._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':payload})
   p['response']={'http':st,'body':res};save();assert st==200 and res.get('status')=='OK';assert not any(r.get('errors')for r in res.get('results',[]))
   for _ in range(24):
    time.sleep(5);a=one([r['offer']for r in read_rows(biz,'offer-mappings',[sku])],'offerId',sku)
    if a.get('pictures')==pics:break
   assert a.get('pictures')==pics,'Market gallery not updated'
  else:a=b
  ca=one(read_rows(biz,'offer-cards',[sku]),'offerId',sku);p['after']=a;p['card_after']=ca
  same(b,a,['name','description','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions'])
  assert grouped(c.get('parameterValues',[]))==grouped(ca.get('parameterValues',[]));assert b.get('mediaFiles',{}).get('videos')==a.get('mediaFiles',{}).get('videos')
  p['non_media_preserved']=True;p['photo_count']=len(a.get('pictures',[]));save()
with contextlib.redirect_stdout(LOG),contextlib.redirect_stderr(LOG):
 for name,fn in [('ozon',ozon),('wb',wb),('market',market)]:
  try:fn()
  except Exception:RESULT.setdefault(name,{})['error']=traceback.format_exc();save()
RESULT['log']=LOG.getvalue();RESULT['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
print('Two-product content operation completed. No prices or stocks written. Private verification encrypted.')
sys.exit(1 if any(RESULT.get(p,{}).get('error')for p in ['ozon','wb','market'])else 0)
