"""Independent readback of exact Gen1 second-photo removal; serial guarded Avito recovery."""
import importlib,json,io,os,sys,time
from pathlib import Path
from PIL import Image,ImageOps,ImageChops,ImageStat
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
s=importlib.import_module('scripts.6t30_gen1_remove_second_20261125')
OUT=ROOT/'data/6t30_gen1_remove_second_readback_20261125.json'
previous=json.loads(s.OUT.read_text())
result={'sku':s.SKU,'scope':'verify only second removed; retry exact Avito feed','channels':{},'other_sku_writes':0,'price_writes':0,'stock_writes':0,'description_writes':0}
def req(x,m):
 if not x:raise ValueError(m)
def cover_check(url,wb=False):
 raw,im=s.download(url)
 if wb:
  reference=Image.open(ROOT/'photos/6t30/6t30_1.png').convert('RGB')
  reference=ImageOps.pad(reference,(900,1200),Image.Resampling.LANCZOS,color='white')
  score=sum(ImageStat.Stat(ImageChops.difference(im.resize((180,240)),reference.resize((180,240)))).mean)/3
  return score<4,round(score,3)
 return s.pixels_same(s.download(s.COVER),(raw,im))
def ozon():
 c=s.one([x for x in s.oz.get_product_names([s.SKU]) if x.get('offer_id')==s.SKU],'Ozon exact')
 req(str(c.get('id') or c.get('product_id'))==s.OZID,'Ozon identity')
 g=s.cs.ozon_gallery(c);ok,mad=cover_check(g[0])
 attrs={x['id']:[str(y.get('value')) for y in x.get('values',[])] for x in c.get('attributes',[])}
 rule=s.cm.load()[s.SKU]['text']['ozon']
 z={'count':len(g),'main_verified':ok,'cover_difference':mad,'same_primary_url':g[0]==previous['channels']['ozon']['primary_before'],'gallery':g,'quantity_six':attrs.get(8513)==['6'],'description_verified':attrs.get(4191)==[rule['description']],'composition_verified':attrs.get(4384)==[rule['composition']]}
 if len(g)==1 and ok:z['status']='verified';return z
 try:
  status=s.oz.get_import_status(int(previous['channels']['ozon']['task_id']))
  z['prior_task']=status
 except Exception as e:z['task_read_error']=str(e)[:200]
 z['status']='not_verified';return z
def wb():
 c=s.p.read_wb_exact(s.SKU,s.WBID);g=s.cs.wb_gallery(c);ok,mad=cover_check(g[0],True)
 rule=s.cm.load()[s.SKU];chars={x['id']:x.get('value') for x in c.get('characteristics',[])}
 z={'count':len(g),'main_verified':ok,'cover_difference':mad,'same_primary_url':g[0]==previous['channels']['wb']['primary_before'],'gallery':g,'title_verified':c.get('title')==rule['text']['wb']['title'],'description_verified':c.get('description')==rule['text']['wb']['description'],'composition_verified':chars.get(378533)==rule['wb_characteristics'][0]['value'],'all_prior_characteristics_exact':None}
 # Compare latest saved public seller card only when it matches the approved
 # title and description; ordering of characteristic rows is immaterial.
 try:
  data=json.loads((ROOT/'data/wb_cards.json').read_text());rows=data.get('cards',[]) if isinstance(data,dict) else data
  b=s.one([x for x in rows if str(x.get('nmID'))==s.WBID and x.get('vendorCode')==s.SKU],'saved card')
  if b.get('title')==c.get('title') and b.get('description')==c.get('description'):
   z['all_prior_characteristics_exact']={x['id']:x.get('value') for x in b.get('characteristics',[])}==chars
 except Exception:pass
 z['status']='verified' if len(g)==1 and ok and all(z[k] for k in ('title_verified','description_verified','composition_verified')) else 'not_verified'
 return z
def run():
 for ch,f in [('ozon',ozon),('wb',wb),('avito',s.avito)]:
  try:result['channels'][ch]=f()
  except Exception as e:result['channels'][ch]={'status':'blocked_or_uncertain','error':str(e)[:450]}
  s.p.write_json(OUT,result)
 result['channels']['market']=previous['channels']['market'];result['channels']['tilda']=previous['channels']['tilda']
 result['finished_utc']=s.p.stamp();s.p.write_json(OUT,result)
 s.p.checkpoint([OUT,ROOT/'data/avito_feed.xlsx'],live=True)
if __name__=='__main__':run()
