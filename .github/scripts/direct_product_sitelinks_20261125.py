"""Owner-approved direct product sitelinks only for active DQ200 ads.
Preserve the three existing section links, put verified relevant product links first.
Read-only public storefront checks; never click, order, or send form submissions.
No campaign/group/keyword/bid/budget/feed/copy/site changes.
"""
import asyncio,copy,hashlib,json,os,re,sys,time,urllib.request,urllib.error
from pathlib import Path
from urllib.parse import urlsplit
from datetime import datetime,timezone
from decimal import Decimal
from playwright.async_api import async_playwright
ROOT=Path(__file__).resolve().parents[2];os.chdir(ROOT)
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(Path(__file__).resolve().parent))
import direct_owner_approved_20261124 as a
import direct_preflight_20261120 as d
from direct_mass_negatives_apply_20261120 import ADS_GET,CAMPAIGN_GET,GROUP_GET,NoRedirect,atomic_save
from scripts.leoparts_direct_operation_nov21 import site_records
CID=716316525;SID=1526814548
OUT=ROOT/'data/direct_recovery_20261124/product_sitelinks_20261125.json'
JOURNAL=ROOT/'data/direct_recovery_20261124/product_sitelinks_journal_20261125.json'
BASE='https://api.direct.yandex.com/json/v501/'
TARGETS={
 1927186476439690942:(5826171512,'ShoppingAd'),
 1927466195846975291:(5826171512,'ResponsiveAd'),
 1927189770678081700:(5826183946,'ShoppingAd'),
 1927466206584354174:(5826183946,'ResponsiveAd'),
 1927190920657966611:(5826320196,'ShoppingAd'),
 1927488453443618167:(5828476541,'ResponsiveAd'),
}
PREF={
 5826171512:['0am325025h','0am325025hx','0am927769d','0am927769f','0am927769e'],
 5826183946:['0am325025hx','0am325025hxe','0am325025hxf','0am325025hxg','0am325025hxk'],
 5826320196:['0am927769d','0am927769e','0am927769f','0am927769g','0am927769k'],
 5828476541:['0am325025h','0am325025hx','0am927769d','0am927769f','0am927769e'],
}
COPY={
 '0am325025h':('Мехатроник без платы 0AM','Мехатроник DSG7 DQ200 без электронной платы'),
 '0am325025hx':('Мехатроник с платой D','Мехатроник DQ200 в сборе с платой D'),
 **{'0am325025hx'+x.lower():('Мехатроник с платой '+x,'Мехатроник DQ200 в сборе с платой '+x) for x in 'EFGK'},
 **{'0am927769'+x.lower():('Плата 0AM927769'+x,'Электронная плата мехатроника DSG7 DQ200, версия '+x) for x in 'DEFGK'},
}
FALLBACK={'https://leoparts-akpp.ru/mehatronik-dq200#bez-platy','https://leoparts-akpp.ru/mehatronik-dq200#s-platoi','https://leoparts-akpp.ru/mehatronik-dq200#platy'}
R={'campaign_id':CID,'status':'preflight','started_utc':a.now(),'site_writes':0,'feed_writes':0,'keyword_writes':0,'budget_writes':0,'bid_writes':0,'copy_writes':0,'errors':[],'checks':{},'groups':{},'ad_results':{},'operations':[]}
def require(x,m):
 if not x:raise RuntimeError(m)
def save():atomic_save(OUT,R)
def digest(v):return hashlib.sha256(json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
def snapshot():
 return {'campaign':a.get_campaign(),'groups':a.get_groups(),'ads':a.get_ads(),'keywords':a.get_keys(),'feed':a.get_feed()}
def sets():
 d.ALLOWED=tuple(set(d.ALLOWED)|{'sitelinks'})
 rows=d.get('sitelinks',{'FieldNames':['Id','Sitelinks'],'Page':{'Limit':1000}},'SitelinksSets')
 require(len(rows)==len({x['Id'] for x in rows}),'Duplicate set IDs')
 return {x['Id']:x['Sitelinks'] for x in rows}
def links_normalize(items):
 return [{k:x.get(k) for k in ('Title','Href','Description') if x.get(k) is not None} for x in items]
def protected(s):
 out=copy.deepcopy(s)
 for ad in out['ads'].values():
  for key in ('ShoppingAd','ResponsiveAd'):
   if isinstance(ad.get(key),dict):
    ad[key].pop('SitelinkSetId',None)
  if ad['Id'] in TARGETS:ad.pop('State',None)
 # feed timestamp, transient moderation and keyword status are not settings.
 out['feed'].pop('UpdatedAt',None)
 return a.stable(out)
def checkpoint():
 save()
 # Report contains only public URLs and aggregate guards, no credentials or raw account data.
 import subprocess
 subprocess.run(['bash','.github/scripts/save_data.sh',str(OUT.relative_to(ROOT)),str(JOURNAL.relative_to(ROOT))],check=True,timeout=120)
def current_main():
 import subprocess
 g=lambda *args:subprocess.check_output(['git',*args],text=True).strip()
 g('fetch','--no-tags','origin','main')
 require(g('rev-parse','HEAD')==g('rev-parse','FETCH_HEAD'),'Main changed; no stale write')
def guard_url(url,uid):
 u=urlsplit(url)
 return u.scheme=='https' and u.hostname=='leoparts-akpp.ru' and not u.username and not u.password and u.port in (None,443) and not u.query and not u.fragment and ('/tproduct/'+uid+'-') in u.path
def candidate_rows():
 raw=(ROOT/'feeds/dq200_candidate.yml').read_bytes();offers=a.xml_offers(raw)
 links=json.loads((ROOT/'data/site_links.json').read_text())
 records=site_records();candidates={}
 for sku in COPY:
  r=[x for x in records.values() if str(x.get('sku','')).casefold()==sku]
  if len(r)!=1:
   R['checks'][sku]={'eligible':False,'reason':'SKU missing or ambiguous'};continue
  row=r[0];uid=str(row.get('uid',''));stock=row.get('quantity');price=row.get('price')
  u=offers[sku].findtext('url') or ''
  try:
   require(str(row.get('externalid',row.get('external_id',''))).casefold()==sku and uid.isdigit(),'SKU or external identity mismatch')
   require(offers[sku].get('available')=='true' and Decimal(str(stock))>0 and Decimal(str(price))>0,'No confirmed stock')
   require(guard_url(u,uid) and guard_url(links[sku],uid),'URL / UID mismatch')
   # If live site provides a second known direct path, try it only as an alternative.
   urls=[u]
   for x in (row.get('url'),links.get(sku)):
    if isinstance(x,str) and guard_url(x,uid) and x not in urls:urls.append(x)
   title=str(row.get('title') or '')
   require(len(title)>4,'No actual product title')
   candidates[sku]={'uid':uid,'title':title,'price':str(Decimal(str(price)).normalize()),'urls':urls}
  except Exception as e:R['checks'][sku]={'eligible':False,'reason':d.scrub(e)}
 return candidates
DOM=r"""(q) => {
 const norm=s=>String(s||'').toLowerCase().replace(/ё/g,'е').replace(/\s+/g,' ').trim();
 const vis=e=>!!e && e.getClientRects().length>0 && getComputedStyle(e).visibility!=='hidden' && getComputedStyle(e).display!=='none';
 const body=norm(document.body?.innerText);
 const titles=[document.title,...Array.from(document.querySelectorAll('h1,.js-store-prod-name,.t-store__prod-popup__name,.t-store__prod-snippet__title')).filter(vis).map(e=>e.innerText)];
 const sku=norm(q.sku);
 const title=norm(q.title);
 const titleok=titles.some(x=>norm(x).includes(title)||title.includes(norm(x))&&norm(x).length>=20&&norm(x).includes('dq200'))||body.includes(title);
 const skuVisible=body.includes(sku)||titles.some(x=>norm(x).includes(sku));
 const cta=Array.from(document.querySelectorAll('button,a,input[type=button],input[type=submit],.t-btn,[role=button]')).filter(vis).some(e=>{let t=norm(e.innerText||e.value||e.getAttribute('aria-label'));return /^(купить|в корзину|добавить в корзину|заказать)(\b|\s|$)/u.test(t)||['купить','в корзину','добавить в корзину','заказать'].includes(t)});
 const password=/authentication required|access denied|403 forbidden|введите пароль|доступ ограничен/.test(norm(document.title))||Array.from(document.querySelectorAll('input[type=password]')).some(vis);
 const price=String(q.price).replace(/\D/g,'');
 const p=Number(q.price);const formatted=Number.isInteger(p)?String(p):String(p).replace('.',',');
 const spaced=formatted.replace(/\B(?=(\d{3})+(?!\d))/g,' ');
 const priceok=body.includes(norm(formatted))||body.includes(norm(spaced))||Array.from(document.querySelectorAll('.js-store-prod-price-val,.t-store__prod-popup__price,.t-store__prod-snippet__price')).filter(vis).some(e=>e.innerText.replace(/\D/g,'')===price);
 return {title:document.title.slice(0,180),title_match:titleok,sku_visible:skuVisible,price_visible:priceok,buy_button_visible:cta,password_or_block:password};
}"""
async def browser_checks(candidates):
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=['--no-sandbox'])
  contexts={
   'desktop':await browser.new_context(viewport={'width':1365,'height':900}),
   'mobile':await browser.new_context(**p.devices['iPhone 13']),
  }
  async def block(route):
   u=urlsplit(route.request.url)
   if any(t in (u.hostname or '') for t in ('mc.yandex','metrika.yandex','google-analytics','googletagmanager','doubleclick')):
    await route.abort()
   else:await route.continue_()
  for ctx in contexts.values():await ctx.route('**/*',block)
  sem=asyncio.Semaphore(6)
  async def check(sku,row,url,device):
   async with sem:
    page=await contexts[device].new_page()
    ans={'device':device,'url':url,'passed':False}
    try:
     response=await page.goto(url,wait_until='domcontentloaded',timeout=26000)
     ans['http']=response.status if response else None
     if ans['http']!=200:return ans
     # Wait for real product content and buying control, not only a HTTP 200.
     q={'sku':sku,'title':row['title'],'price':row['price']}
     for i in range(5):
      await page.wait_for_timeout(1200)
      dom=await page.evaluate(DOM,q)
      ans.update(dom)
      ans['final_url']=page.url.split('?')[0].split('#')[0]
      ans['identity_verified']=guard_url(ans['final_url'],row['uid'])
      if ans['identity_verified'] and dom['title_match'] and dom['price_visible'] and dom['buy_button_visible'] and not dom['password_or_block']:
       ans['passed']=True;break
    except Exception as e:ans['error']=str(e).split('\n')[0][:200]
    finally:await page.close()
    return ans
  jobs=[(sku,row,url,device) for sku,row in candidates.items() for url in row['urls'] for device in contexts]
  results=await asyncio.gather(*(check(*x) for x in jobs))
  await browser.close()
  good={}
  for sku,row in candidates.items():
   tests=[x for x,j in zip(results,jobs) if j[0]==sku]
   passed=[u for u in row['urls'] if all(any(x['url']==u and x['device']==dev and x['passed'] for x in tests) for dev in contexts)]
   R['checks'][sku]={'eligible':bool(passed),'tests':tests,'selected_url':passed[0] if passed else None}
   if passed:good[sku]=passed[0]
  return good
def write_api(service,method,params):
 # No general-purpose write access; exact safe fields only.
 require((service,method) in {('sitelinks','add'),('ads','update'),('ads','moderate')},'Forbidden write')
 if service=='sitelinks':
  require(params=={'SitelinksSets':[{'Sitelinks':v} for v in planned_new]},'Unexpected sitelink creation')
 elif method=='update':
  require(params=={'Ads':assignment},'Unexpected ad mutation')
 else:
  require(params=={'SelectionCriteria':{'Ids':moderation_ids}},'Unexpected moderation')
 token=os.environ['YANDEX_DIRECT_TOKEN']
 h={'Authorization':'Bearer '+token,'Content-Type':'application/json; charset=utf-8','Accept-Language':'en'}
 if os.getenv('YANDEX_DIRECT_LOGIN'):h['Client-Login']=os.environ['YANDEX_DIRECT_LOGIN']
 req=urllib.request.Request(BASE+service,data=json.dumps({'method':method,'params':params},ensure_ascii=False).encode(),headers=h,method='POST')
 with urllib.request.build_opener(NoRedirect()).open(req,timeout=35) as resp:
  result=json.load(resp)
 require(not result.get('error') and isinstance(result.get('result'),dict),'Direct API rejected: '+d.scrub(result.get('error')))
 return result['result']
planned_new=[];assignment=[];moderation_ids=[]
def main():
 global planned_new,assignment,moderation_ids
 require(os.getenv('GITHUB_REF')=='refs/heads/main','main only')
 require(not JOURNAL.exists(),'Prior operation journal exists; do not repeat writes')
 current_main()
 d.ALLOWED=tuple(set(d.ALLOWED)|{'ads','feeds','sitelinks'})
 # All checks are fresh; no older snapshot used for actual settings.
 before=snapshot();oldsets=sets()
 require(before['campaign']['Id']==CID and before['campaign'].get('State')=='ON','Campaign not active')
 budget=before['campaign']['UnifiedCampaign']['BiddingStrategy']['Search']['HighestPosition']['WeeklySpendLimit']
 R['budget_rub']=budget/1e6;R['keyword_count']=len(before['keywords'])
 targets={}
 for aid,(gid,kind) in TARGETS.items():
  ad=before['ads'].get(aid)
  if not ad or ad.get('State')!='ON' or ad.get('Status')!='ACCEPTED':continue
  require(ad['CampaignId']==CID and ad['AdGroupId']==gid and ad['Type']==('SHOPPING_AD' if kind=='ShoppingAd' else 'RESPONSIVE_AD'),'Unexpected target identity')
  if kind=='ShoppingAd':require(ad[kind]['FeedId']==a.FID and ad[kind]['FeedFilterConditions']==a.desired(aid),'Shopping filter drift')
  sid=ad[kind].get('SitelinkSetId')
  require(sid in oldsets,'No existing set for active ad')
  old=links_normalize(oldsets[sid])
  require(len(old)==3 and {v['Href'] for v in old}==FALLBACK,'Existing links changed; no overwrite')
  targets[aid]=(gid,kind,sid,old)
 R['active_target_count']=len(targets);R['untouched_ads_count']=len(before['ads'])-len(targets)
 require(targets,'No active exact target')
 save()
 candidates=candidate_rows()
 good=asyncio.run(browser_checks(candidates));save()
 plans={}
 for aid,(gid,kind,sid,old) in targets.items():
  product=[]
  for sku in PREF[gid]:
   if sku not in good:continue
   title,desc=COPY[sku]
   require(len(title)<=30 and len(desc)<=60,'Sitelink text limit')
   product.append({'Title':title,'Href':good[sku],'Description':desc})
  if not product:
   R['groups'][str(gid)]={'status':'no_verified_product_links','preserved_original_set':True}
   continue
  links=product+old
  require(len(links)<=8 and len({x['Href'] for x in links})==len(links),'Sitelink count/uniqueness')
  R['groups'][str(gid)]={'status':'planned','direct_count':len(product),'links':links,'preserved_section_links':3}
  plans[aid]=links
 if not plans:
  R['status']='blocked_no_verified_product_links';save();return
 # Recheck protected state before any write.
 current_main();fresh=snapshot()
 require(protected(fresh)==protected(before),'Campaign, keywords or ad settings changed during site checks')
 require(all(fresh['ads'][k][kind].get('SitelinkSetId')==sid for k,(_,kind,sid,_) in targets.items()),'Existing assignment changed')
 journal={'campaign_id':CID,'scope':'sitelink sets and exact active ad assignments only','started_utc':a.now(),'operations':[]}
 def intent(label,payload):
  journal['operations'].append({'operation':label,'request_sha256':digest(payload),'at_utc':a.now()})
  atomic_save(JOURNAL,journal);R['operations'].append(label);checkpoint()
 # Reuse equivalent existing set if present. Deduplicate plan groups.
 unique=[]
 for links in plans.values():
  if links not in unique:unique.append(links)
 def matches(links,inventory):return sorted(k for k,v in inventory.items() if links_normalize(v)==links)
 planned_new=[x for x in unique if not matches(x,oldsets)]
 if planned_new:
  payload={'SitelinksSets':[{'Sitelinks':v} for v in planned_new]}
  intent('create_product_link_sets',payload)
  result=write_api('sitelinks','add',payload)
  require(len(result.get('AddResults',[]))==len(planned_new) and all(not x.get('Errors') and x.get('Id') for x in result['AddResults']),'Set creation not fully accepted')
 current=sets()
 for v in unique:require(matches(v,current),'Created link set not readable')
 assigned={aid:matches(links,current)[0] for aid,links in plans.items()}
 R['new_set_ids']={str(k):v for k,v in assigned.items()}
 require(all(links_normalize(current[v])==plans[k] for k,v in assigned.items()),'Sitelinks readback mismatch')
 # Preserve original sets, do not delete any.
 require(all(current.get(sid)==oldsets[sid] for _,_,sid,_ in targets.values()),'Original set changed')
 current_main()
 snap=snapshot();require(protected(snap)==protected(before),'Protected fields changed before assignment')
 require(all(snap['ads'][k][kind].get('SitelinkSetId')==sid for k,(_,kind,sid,_) in targets.items()),'Old assignments changed')
 assignment=[{'Id':k,targets[k][1]:{'SitelinkSetId':v}} for k,v in sorted(assigned.items())]
 payload={'Ads':assignment};intent('assign_product_links',payload)
 result=write_api('ads','update',payload)
 entries=result.get('UpdateResults',[])
 R['assignment_response']=[{'id':str(x.get('Id')),'errors':[e.get('Code') for e in x.get('Errors',[])]} for x in entries]
 require({x.get('Id') for x in entries}==set(assigned) and not any(x.get('Errors') for x in entries),'Assignment not fully accepted; readback required')
 final=snapshot()
 moderation_ids=sorted(k for k in assigned if final['ads'][k].get('Status')=='DRAFT' and final['ads'][k][targets[k][1]].get('SitelinkSetId')==assigned[k])
 if moderation_ids:
  payload={'SelectionCriteria':{'Ids':moderation_ids}};intent('moderate_own_updated_drafts',payload)
  write_api('ads','moderate',payload)
 final=snapshot();verified_sets=sets()
 R['protected_settings_unchanged']=protected(final)==protected(before)
 require(R['protected_settings_unchanged'],'Protected campaign/ad/keyword settings drift')
 for k,ad in final['ads'].items():
  kind=targets[k][1] if k in targets else ('ShoppingAd' if ad.get('ShoppingAd') else 'ResponsiveAd')
  if k not in assigned:require(ad.get(kind,{}).get('SitelinkSetId')==before['ads'][k].get(kind,{}).get('SitelinkSetId'),'Non-target ad changed')
 for k,v in assigned.items():
  ad=final['ads'][k];part=ad[targets[k][1]]
  ok=part.get('SitelinkSetId')==v and links_normalize(verified_sets.get(v,[]))==plans[k]
  R['ad_results'][str(k)]={'group':targets[k][0],'set_id':v,'saved':ok,'state':ad.get('State'),'status':ad.get('Status'),'sitelinks_moderation':(part.get('SitelinksModeration') or {}).get('Status')}
 for gid in R['groups']:
  g=R['groups'][gid]
  if g['status']=='planned':
   rows=[x for x in R['ad_results'].values() if x['group']==int(gid)]
   g['status']='verified' if rows and all(x['saved'] for x in rows) else 'unconfirmed'
 require(all(x['saved'] for x in R['ad_results'].values()),'Assignment readback incomplete')
 R['status']='verified' if all(x['state']=='ON' and x['status']=='ACCEPTED' and x['sitelinks_moderation']=='ACCEPTED' for x in R['ad_results'].values()) else 'saved_pending_moderation'
 R['original_sets_preserved']=True
if __name__=='__main__':
 try:main()
 except Exception as e:
  R['errors'].append(d.scrub(e));R['status']='requires_review' if R['operations'] else 'blocked_no_write'
 finally:
  R['finished_utc']=a.now();save()
  print('Direct product sitelinks:',R['status'],flush=True)
