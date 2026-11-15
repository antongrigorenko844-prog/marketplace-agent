"""Apply the owner's precise content correction to one regulator; no commercial writes."""
import base64,contextlib,copy,datetime,io,json,os,sys,time,traceback
from pathlib import Path
from cryptography.hazmat.primitives import hashes,serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
SKU='0AM325025B';APPLY='--apply'in sys.argv
TARGETS=set(os.environ.get('CONTENT_TARGETS','ozon,wb,market').split(','))
URL='https://github.com/antongrigorenko844-prog/marketplace-agent/releases/download/product-photos/0AM325025B-compatibility-20261115-v2.png'
DESC='Новый соленоид Bosch — регулятор давления мехатроника DSG7 DQ200 (0AM/0CW). Подходит на все мехатроники DQ200. На один мехатроник — 2 регулятора давления. Цена указана за 1 шт.'
PREV=json.loads((ROOT/'data/regulator_previous_20261115.json').read_text())
RESULT={'started_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'sku':SKU,'apply':APPLY,'price_write_calls':0,'stock_write_calls':0}
LOG=io.StringIO()
def save():
 key=AESGCM.generate_key(bit_length=256);nonce=os.urandom(12)
 pub=serialization.load_pem_public_key((ROOT/'scripts/black_filter_result_public.pem').read_bytes())
 wrapped=pub.encrypt(key,padding.OAEP(mgf=padding.MGF1(hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
 encrypted=AESGCM(key).encrypt(nonce,json.dumps(RESULT,ensure_ascii=False).encode(),None)
 p=ROOT/'reports/regulator-simple-private.enc';p.parent.mkdir(exist_ok=True)
 p.write_text(json.dumps({k:base64.b64encode(v).decode()for k,v in {'key':wrapped,'nonce':nonce,'data':encrypted}.items()}))
def one(rows,key):
 rows=[r for r in rows if r.get(key)==SKU];assert len(rows)==1,(key,len(rows));return rows[0]
def same(b,a,keys):
 for k in keys:assert b.get(k)==a.get(k),('Unrelated change',k)
def norm(x):
 if isinstance(x,str):return x.replace('https://ir-20.ozone.ru/','https://ir.ozone.ru/')
 if isinstance(x,list):return [norm(z)for z in x]
 if isinstance(x,dict):return {k:norm(v)for k,v in x.items()}
 return x
def rich(d):
 v=[v['value']for a in d.get('attributes',[])if a['id']==11254 for v in a.get('values',[])if v.get('value')];assert len(v)<=1;return json.loads(v[0])if v else None
def newrich(url):
 return {'version':0.3,'content':[{'widgetName':'raTextBlock','title':{'content':['Регулятор давления DSG7 DQ200'],'size':'size4','align':'left','color':'color1'},'theme':'default','padding':'type2','gapSize':'m','text':{'content':['Подходит на все мехатроники DQ200.','На один мехатроник — 2 регулятора давления.','Цена указана за 1 шт.'],'size':'size2','align':'left','color':'color1'}},{'widgetName':'raShowcase','type':'roll','blocks':[{'imgLink':'','img':{'src':url,'srcMobile':url,'alt':'Подходит на все мехатроники DQ200. На один мехатроник — 2 регулятора давления. Цена за 1 шт.','position':'width_full','positionMobile':'width_full','widthMobile':1086,'heightMobile':1448,'isParandjaMobile':False}}]}]}
def ozon():
 import ozon_client as oz
 b=one(oz.get_product_names([SKU]),'offer_id');p=RESULT['ozon']={'before':b,'prices_before':oz.get_prices_and_stocks([SKU])};save()
 assert norm(b['primary_image'])==norm(PREV['ozon']['primary_image'])
 assert norm(b['images'][:1])==norm(PREV['ozon']['images'][:1])
 assert len(b['images'])in(2,3)
 if APPLY and 'ozon'in TARGETS:
  if len(b['images'])==3:
   assert norm(b['images'])==norm(PREV['ozon']['images'])
   p['media_response']=oz._post('/v2/product/pictures/import',{'items':[{'offer_id':SKU,'primary_image':b['primary_image'],'images':b['images'][:1]+[URL],'color_image':b.get('color_image')or''}]});save()
   for _ in range(30):
    time.sleep(5);a=one(oz.get_product_names([SKU]),'offer_id')
    if len(a['images'])==2:break
   assert len(a['images'])==2,'Ozon gallery pending'
  else:a=b
  desired=newrich(a['images'][-1]);old=rich(a)
  assert norm(old)in [norm(json.loads(PREV['ozon']['rich'])),norm(desired)],'Unexpected rich edit'
  p['content_response']=oz.update_attributes([{'offer_id':SKU,'attributes':[{'id':4191,'complex_id':0,'values':[{'value':DESC}]},{'id':11254,'complex_id':0,'values':[{'value':json.dumps(desired,ensure_ascii=False)}]}]}]);save()
  for _ in range(30):
   time.sleep(5);a=one(oz.get_product_names([SKU]),'offer_id')
   desc=next(v['value']for x in a['attributes']if x['id']==4191 for v in x['values'])
   if desc==DESC and norm(rich(a))==norm(desired):break
  assert desc==DESC and norm(rich(a))==norm(desired),'Ozon content pending'
 a=one(oz.get_product_names([SKU]),'offer_id');p['after']=a
 same(b,a,['name','type_id','description_category_id','depth','width','height','weight','complex_attributes'])
 filt=lambda d:sorted([x for x in d['attributes']if x['id']not in(4191,11254)],key=lambda x:(x.get('complex_id',0),x['id']))
 assert filt(b)==filt(a);assert norm(b['primary_image'])==norm(a['primary_image']);assert norm(b['images'][:1])==norm(a['images'][:1])
 p['prices_after']=oz.get_prices_and_stocks([SKU]);assert [x.get('price')for x in p['prices_before']]==[x.get('price')for x in p['prices_after']]
 p['old_media_and_commercial_preserved']=True;p['photo_count']=1+len(a['images']);save()
def wb():
 import wb_client as w
 b=one(w.list_cards(),'vendorCode');nid=b['nmID'];assert nid==670925505
 p=RESULT['wb']={'before':b,'prices_before':[r for r in w.get_prices()if r['nmID']==nid]};save()
 assert len(b['photos'])in(3,4);assert b['photos'][:2]==PREV['wb']['photos'][:2]
 if APPLY and 'wb'in TARGETS:
  q={k:copy.deepcopy(b[k])for k in ['nmID','vendorCode','brand','title','dimensions','characteristics','sizes']};q['kizMarked']=b.get('kizMarked',False);q['description']=DESC
  if b.get('description')!=DESC:
   p['content_response']=w.update_cards([q]);save();assert not p['content_response'].get('error')
  if len(b['photos'])==4:
   assert b['photos']==PREV['wb']['photos'];assert not b.get('video'),'Unexpected video: preserve via separate flow'
   p['media_response']=w.update_media(nid,[x['big']for x in b['photos'][:2]]+[URL]);save();assert not p['media_response'].get('error')
  for _ in range(30):
   time.sleep(5);a=one(w.list_cards(),'vendorCode')
   if len(a['photos'])==3 and a.get('description')==DESC:break
  assert len(a['photos'])==3 and a.get('description')==DESC,'WB content pending'
 else:a=b
 p['after']=a;same(b,a,['title','brand','subjectID','dimensions','sizes','video','kizMarked'])
 assert sorted(b['characteristics'],key=lambda x:x['id'])==sorted(a['characteristics'],key=lambda x:x['id']);assert b['photos'][:2]==a['photos'][:2]
 p['prices_after']=[r for r in w.get_prices()if r['nmID']==nid];assert p['prices_after']==p['prices_before'];p['old_media_and_commercial_preserved']=True;p['photo_count']=len(a['photos']);save()
def market():
 import yandex_market_client as y
 from market_content import read_rows,preserve_typed_parameters,grouped
 biz=y.get_business_id();read=lambda:one([r['offer']for r in read_rows(biz,'offer-mappings',[SKU])],'offerId')
 b=read();c=one(read_rows(biz,'offer-cards',[SKU]),'offerId');p=RESULT['market']={'before':b,'card_before':c};save()
 target=PREV['market']['pictures'][:2]+[URL];assert b['pictures']in [PREV['market']['pictures'],target]
 if APPLY and 'market'in TARGETS:
  payload=preserve_typed_parameters(biz,[{'offer':{'offerId':SKU,'pictures':target,'description':DESC}}])
  status,res=y._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':payload});p['response']={'http':status,'body':res};save();assert status==200 and res.get('status')=='OK';assert not any(x.get('errors')for x in res.get('results',[]))
  for _ in range(24):
   time.sleep(5);a=read()
   if a.get('pictures')==target and a.get('description')==DESC:break
  assert a.get('pictures')==target and a.get('description')==DESC,'Market content pending'
 else:a=b
 ca=one(read_rows(biz,'offer-cards',[SKU]),'offerId');p['after']=a;p['card_after']=ca
 same(b,a,['name','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions']);assert grouped(c.get('parameterValues',[]))==grouped(ca.get('parameterValues',[]));assert b.get('mediaFiles',{}).get('videos')==a.get('mediaFiles',{}).get('videos')
 p['old_media_and_commercial_preserved']=True;p['photo_count']=len(a['pictures']);save()
with contextlib.redirect_stdout(LOG),contextlib.redirect_stderr(LOG):
 for name,fn in [('ozon',ozon),('wb',wb),('market',market)]:
  try:fn()
  except Exception:RESULT.setdefault(name,{})['error']=traceback.format_exc();save()
RESULT['log']=LOG.getvalue();RESULT['checked_at']=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
print('Regulator content correction checked. No price or stock write calls. Evidence encrypted.')
sys.exit(1 if any(RESULT.get(p,{}).get('error')for p in ['ozon','wb','market'])else 0)
