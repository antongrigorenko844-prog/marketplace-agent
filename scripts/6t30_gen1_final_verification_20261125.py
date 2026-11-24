"""Final exact-SKU gallery check. Only authorized possible seller write:
Ozon v2 pictures import of the existing primary as the complete one-image list.
No other listing, photo asset, title, price or inventory mutation."""
import importlib,io,json,os,sys,time
from pathlib import Path
from urllib.parse import urlsplit
from concurrent.futures import ThreadPoolExecutor
from PIL import Image,ImageOps,ImageChops,ImageStat
import requests
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
s=importlib.import_module('scripts.6t30_gen1_remove_second_20261125')
rb=importlib.import_module('scripts.6t30_gen1_remove_second_readback_20261125')
import ozon_description_guard as og
OUT=ROOT/'data/6t30_gen1_final_verification_20261125.json'
PRE=json.loads(s.OUT.read_text())['channels']
R={'sku':s.SKU,'checked_utc':s.p.stamp(),'channels':{},'other_sku_writes':0,'price_writes':0,'stock_writes':0,'description_writes':0}
def req(x,m):
 if not x:raise ValueError(m)
def save():s.p.write_json(OUT,R)
def ozon():
 def read():
  c=s.one([x for x in s.oz.get_product_names([s.SKU]) if x.get('offer_id')==s.SKU],'Ozon exact')
  req(str(c.get('id') or c.get('product_id'))==s.OZID,'Ozon identity')
  info=s.oz._post('/v2/product/pictures/info',{'product_id':[s.OZID]},retries=1)
  pi=s.one([x for x in info.get('items',[]) if str(x.get('product_id'))==s.OZID],'Ozon gallery')
  g=list(dict.fromkeys((pi.get('primary_photo') or [])+(pi.get('photo') or [])))
  req(g==s.cs.ozon_gallery(c),'Ozon gallery views disagree')
  return c,pi,g
 def fields(c):
  return ({k:c.get(k) for k in ('id','offer_id','sku','name','description_category_id','type_id','height','depth','width','weight','barcode','barcodes','color_image','model_info','pdf_list')},og.attributes(c),s.cs.description_value('ozon',c),c.get('complex_attributes'))
 b,pi,g=read();old=s.download(g[0]);ok,mad=rb.cover_check(g[0])
 z={'count':len(g),'main_verified':ok,'cover_difference':mad,'primary_before':g[0],'gallery':g,'status':'verified' if len(g)==1 and ok else 'not_verified','seller_writes':0}
 if len(g)==1:return z
 req(len(g)==2 and g[0]==PRE['ozon']['primary_before'] and g[1]==PRE['ozon']['removed_url'] and ok,'Unexpected gallery; no write')
 req(not pi.get('photo_360') and not pi.get('color_photo') and not b.get('color_image'),'Additional media; stop')
 # Reuse current primary, no new image upload or full card import.
 s.p.current_main()
 try:
  reply=s.oz._post('/v2/product/pictures/import',{'items':[{'offer_id':s.SKU,'images':[g[0]]}]},retries=1)
  req(reply and not reply.get('error'),'Ozon rejected request')
  z.update(seller_writes=1,task_id=reply.get('task_id') or (reply.get('result') or {}).get('task_id'),status='submitted_pending_readback')
 except Exception as e:
  z.update(error=str(e)[:280]);return z
 for _ in range(9):
  time.sleep(5)
  a,pa,ag=read()
  z.update(count=len(ag),gallery=ag,primary_after=ag[0],primary_url_preserved=ag[0]==g[0],protected_unchanged=fields(a)==fields(b))
  z['main_verified'],z['cover_difference']=s.pixels_same(old,s.download(ag[0]))
  if not z['protected_unchanged']:z['status']='protected_drift';break
  if len(ag)==1 and z['main_verified']:z['status']='verified';break
 if z.get('task_id'):
  try:
   task=s.oz.get_import_status(int(z['task_id']))
   z['task_status']=[{'status':x.get('status'),'warnings':[e.get('code') for e in x.get('errors',[])]} for x in (task.get('result') or {}).get('items',[])]
  except Exception:pass
 return z
def market():
 biz=s.ym.get_business_id()
 a=s.one([r['offer'] for r in s.read_rows(biz,'offer-mappings',[s.SKU]) if r['offer'].get('offerId')==s.SKU],'Market exact')
 c=s.one([r for r in s.read_rows(biz,'offer-cards',[s.SKU]) if r.get('offerId')==s.SKU],'Market card')
 g=a.get('pictures') or [];rule=s.cm.load()[s.SKU];ok=bool(g and s.norm(g[0])==s.norm(s.COVER))
 return {'status':'verified' if len(g)==1 and ok else 'not_verified','count':len(g),'main_verified':ok,'gallery':g,'title_verified':a.get('name')==rule['text']['market']['title'],'description_verified':a.get('description')==rule['text']['market']['description'],'quantity_six':any(x['parameterId']==14805583 and str(x.get('value'))=='6' for x in c.get('parameterValues',[]))}
def avito():
 from openpyxl import load_workbook
 z={'status':'not_verified','reports':{}}
 def extract(raw):
  w=load_workbook(io.BytesIO(raw),read_only=True,data_only=True)
  try:
   ws=w[s.af.SHEET_ADS];r=s.one([r for r in ws.iter_rows(min_row=s.af.FIRST_DATA_ROW) if str(r[s.af.COL_ID-1].value or '').strip()==s.SKU],'Avito target row')
   req(str(r[s.af.COL_AVITO_ID-1].value or '') in ('',s.AVID),'Avito ID mismatch')
   g=[x.strip() for x in str(r[s.af.COL_IMAGE_URLS-1].value or '').splitlines() if x.strip()]
   return {'count':len(g),'main_verified':bool(g and s.norm(g[0])==s.norm(s.COVER)),'title_six':'6 шт' in str(r[s.af.COL_TITLE-1].value or ''),'description_six':'6 соленоидов' in str(r[s.af.COL_DESCRIPTION-1].value or '')}
  finally:w.close()
 stable=requests.get('https://github.com/'+s.REPO+'/releases/download/product-photos/avito_feed.xlsx',params={'audit':time.time_ns()},timeout=45);stable.raise_for_status()
 z['feed']=extract(stable.content);z['feed']['sha256']=s.sha(stable.content)
 z['local']=extract((ROOT/'data/avito_feed.xlsx').read_bytes())
 active=s.one([x for x in s.av.list_all_items(statuses=['active']) if str(x.get('id'))==s.AVID],'Avito active')
 z['active']=active.get('status')=='active';z['live_title']=active.get('title');z['live_title_six']='6 шт' in str(active.get('title') or '')
 for kind in ('last_successful','current'):
  d={}
  try:
   endpoint='/autoload/v4/uploads/'+kind
   up=s.av._request('GET',endpoint,retries=1)
   d={k:up.get(k) for k in ('upload_id','status','started_at','finished_at')}
   matches=[];conflict=False;seen=set()
   for page in range(1,41):
    q=s.av._request('GET',endpoint+'/items',params={'page':page,'perPage':50},retries=1)
    batch=q.get('items');req(isinstance(batch,list),'Unexpected items')
    for x in batch:
     if str(x.get('ad_id'))==s.SKU:
      if str(x.get('avito_id'))!=s.AVID:conflict=True
      else:matches.append(x)
    sig=json.dumps([(x.get('ad_id'),x.get('avito_id')) for x in batch])
    if sig in seen:break
    seen.add(sig)
    pages=(q.get('meta') or {}).get('pages')
    if (isinstance(pages,int) and page>=pages) or len(batch)<50:break
   if matches and not conflict:
    x=matches[-1];d['target']={'section':(x.get('section') or {}).get('slug'),'status':x.get('status') or x.get('avito_status'),'messages':[{'code':m.get('code'),'type':m.get('type'),'title':m.get('title')} for m in x.get('messages',[])]}
   else:d['target_mapping_verified']=False
   links=[x.get('url') for x in up.get('feed_urls',[]) if x.get('name')=='LEOPARTS запчасти']
   req(len(links)==1,'Unique official received feed missing')
   u=urlsplit(links[0]);req(u.scheme=='https' and u.hostname=='api.avito.ru' and u.path.startswith('/autoload/v2/feed/content/'),'Unrecognized official feed snapshot URL')
   r=requests.get(links[0],headers={'Authorization':s.av._headers()['Authorization']},timeout=40,allow_redirects=False);r.raise_for_status()
   d['received']=extract(r.content)
   d['received']['sha256']=s.sha(r.content)
   req(s.av._request('GET',endpoint,retries=1).get('upload_id')==up.get('upload_id'),'Report advanced')
  except Exception as e:d['error']=str(e)[:180]
  z['reports'][kind]=d
 if z['feed']['count']==1 and z['feed']['main_verified']:
  z['status']='stable_feed_verified_not_listing'
 for d in z['reports'].values():
  f=d.get('received',{});t=d.get('target',{})
  if not d.get('error') and f.get('count')==1 and f.get('main_verified') and t.get('section')=='success_published' and d.get('status') in ('success','completed','finished'):
   z['status']='received_and_published_report_verified';break
 # actual marketplace API may not expose photos; never equate receipt with UI.
 z['public_gallery_verified']=False
 return z
def tilda():
 from scripts.leoparts_direct_operation_nov21 import site_records
 r=s.one([r for r in site_records().values() if r.get('sku')==s.SKU],'Tilda SKU')
 req(str(r.get('uid'))==s.UID and r.get('externalid')==s.SKU,'Tilda identity')
 g=r.get('gallery')
 if isinstance(g,str):g=json.loads(g)
 urls=[x if isinstance(x,str) else next((x.get(k) for k in ('img','url','original','src','large') if isinstance(x.get(k),str) and x[k].startswith('https://')),None) for x in g]
 old=PRE['tilda'];removed=old['removed_url'];oldcover=old['primary_before']
 second_gone=not any(s.norm(x)==s.norm(removed) for x in urls if x)
 return {'status':'verified' if second_gone and len(urls)==old['expected_count'] and urls[0]==oldcover else 'editor_required','count':len(urls),'expected_count':old['expected_count'],'second_removed':second_gone,'current_main_unchanged':urls[0]==oldcover,'main_is_approved_new':s.norm(urls[0])==s.norm(s.COVER),'title':r.get('title'),'gallery':urls}
def run():
 s.p.current_main()
 # Read/write guard is used only in Ozon. Other workers are read-only.
 with ThreadPoolExecutor(max_workers=5) as pool:
  fs={k:pool.submit(f) for k,f in [('ozon',ozon),('wb',rb.wb),('market',market),('avito',avito),('tilda',tilda)]}
  for k,f in fs.items():
   try:R['channels'][k]=f.result()
   except Exception as e:R['channels'][k]={'status':'blocked_or_uncertain','error':str(e)[:350]}
   save()
 R['finished_utc']=s.p.stamp();save();s.p.checkpoint([OUT],live=True)
if __name__=='__main__':run()
