"""Remove only the known Gen1 second photo using the existing one-photo gallery.
The first attempt accepted empty extras but retained them. Use the documented
pictures import with one image, the exact existing main URL, only on this card."""
import importlib,json,os,sys,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
s=importlib.import_module('scripts.6t30_gen1_remove_second_20261125')
OUT=ROOT/'data/6t30_gen1_remove_second_ozon_final_20261125.json'
PREVIOUS=json.loads(s.OUT.read_text())['channels']['ozon']
def req(x,m):
 if not x:raise ValueError(m)
def read():
 c=s.one([x for x in s.oz.get_product_names([s.SKU]) if x.get('offer_id')==s.SKU],'exact Ozon card')
 req(str(c.get('id') or c.get('product_id'))==s.OZID,'wrong product ID')
 return c
def photos():
 d=s.oz._post('/v2/product/pictures/info',{'product_id':[s.OZID]},retries=1)
 r=s.one([x for x in d.get('items',[]) if str(x.get('product_id'))==s.OZID],'picture readback')
 return r
def protected(c):
 import ozon_description_guard as og
 return ({k:c.get(k) for k in ('id','offer_id','sku','name','description_category_id','type_id','height','depth','width','weight','barcode','barcodes','color_image','model_info','pdf_list')},og.attributes(c),c.get('complex_attributes'))
def unique(urls):
 return list(dict.fromkeys(x for x in urls if x))
def gallery(p):
 return unique((p.get('primary_photo') or [])+(p.get('photo') or []))
def main():
 s.p.current_main();b=read();g=s.cs.ozon_gallery(b)
 req(g and s.pixels_same(s.download(g[0]),s.download(s.COVER))[0],'main not approved; stop')
 req(g[0]==PREVIOUS['primary_before'],'main URL unexpectedly changed; stop')
 pi=photos();actual=gallery(pi)
 old=s.download(g[0]);r={'sku':s.SKU,'product_id':s.OZID,'before_gallery':g,'pictures_before':actual,'price_writes':0,'stock_writes':0,'description_writes':0,'other_sku_writes':0,'status':'readback','started_utc':s.p.stamp()}
 s.p.write_json(OUT,r)
 if len(actual)==1 and len(g)==1:
  r.update(status='verified',count=1,primary_url_preserved=True,main_verified=True)
 else:
  req(len(g)==2 and g[1]==PREVIOUS['removed_url'],'unexpected live gallery; stop')
  req(len(actual)==2 and set(actual)==set(g),'picture readback disagrees; stop')
  req(not pi.get('photo_360'),'360 media present; stop')
  colors=pi.get('color_photo') or []
  req(len(colors)<=1,'unexpected color photos; stop')
  req(not colors or colors[0]==b.get('color_image'),'color picture mismatch; stop')
  before=protected(b);s.p.current_main()
  # Deprecated but documented picture-only method; explicit complete array of
  # exactly the existing main image, never a new asset or other card.
  payload={'product_id':int(s.OZID),'images':[g[0]],'color_image':b.get('color_image') or ''}
  try:
   reply=s.oz._post('/v1/product/pictures/import',payload,retries=1)
   r['method']='v1_picture_only_single_existing_url'
  except Exception as e:
   message=str(e)
   # A fallback is permitted only on explicit method unavailability.
   if not any(x in message.lower() for x in ('404','405','410','unimplemented')):raise
   s.p.current_main()
   reply=s.oz._post('/v2/product/pictures/import',{'items':[{'offer_id':s.SKU,'images':[g[0]],'color_image':b.get('color_image') or ''}]},retries=1)
   r['method']='v2_picture_only_single_existing_url'
  r['reply']=reply;r['status']='submitted_pending_readback';s.p.write_json(OUT,r)
  for _ in range(12):
   time.sleep(5);a=read();ap=photos();ag=s.cs.ozon_gallery(a);pg=gallery(ap)
   if protected(a)!=before:
    r.update(status='protected_drift');break
   r.update(after_gallery=ag,pictures_after=pg,count=len(ag),pictures_count=len(pg),primary_url_preserved=bool(ag and ag[0]==g[0]),protected_unchanged=True)
   if ag:
    ok,mad=s.pixels_same(old,s.download(ag[0]));r.update(main_verified=ok,main_difference=mad)
   if len(ag)==1 and len(pg)==1 and r.get('main_verified') and s.pixels_same(old,s.download(pg[0]))[0]:
    r['status']='verified';break
  r['finished_utc']=s.p.stamp()
 s.p.write_json(OUT,r);s.p.checkpoint([OUT],live=True)
if __name__=='__main__':
 try:main()
 except Exception as e:
  r=json.loads(OUT.read_text()) if OUT.exists() else {'sku':s.SKU}
  r.update(status='blocked_or_uncertain',error=str(e)[:500]);s.p.write_json(OUT,r);raise
