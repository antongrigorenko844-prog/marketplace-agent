"""LEOPARTS read-only economic scenarios and public SEO/mobile audit.
No form submission, payment, site or seller changes; browser test only local cart.
"""
import asyncio,collections,datetime,json,sys,re,xml.etree.ElementTree as ET,urllib.parse,urllib.request
from pathlib import Path
from decimal import Decimal
from openpyxl import load_workbook
from playwright.async_api import async_playwright
sys.path.insert(0,str(Path(__file__).resolve().parent))
import tilda_cart_datalayer_readonly as cart
import direct_preflight_20261120 as safe
ROOT=Path("data/direct_performance_20261125");ROOT.mkdir(parents=True,exist_ok=True)
OUT=ROOT/"site_economics.json"
SITE="https://leoparts-akpp.ru"
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def save(d):OUT.write_text(json.dumps(d,ensure_ascii=False,indent=2,default=str)+"\n")
def num(x):
 try:
  n=Decimal(str(x).replace(" ","").replace(",","."))
  return n if n.is_finite() and n>0 else None
 except Exception:return None
def readbook(path):
 ws=load_workbook(path,read_only=True,data_only=True).active
 rows=iter(ws.iter_rows(values_only=True));head=next(rows);d={}
 for row in rows:
  if row[0]:
   k=str(row[0]);d.setdefault(k,[]).append(dict(zip(head,row)))
 return d
def xml_offers():
 root=ET.parse("feeds/dq200_candidate.yml")
 return [{"sku_feed":x.attrib["id"],"available":x.attrib.get("available"),"category":x.findtext("categoryId"),"title":x.findtext("name"),"vendor_code":x.findtext("vendorCode"),"price":x.findtext("price"),"count":x.findtext("count"),"url":x.findtext("url"),"image":x.findtext("picture")} for x in root.findall(".//offer")]
def econ(offers):
 master=readbook("data/master_control.xlsx")
 # exact master IDs are case-sensitive; the feed has deliberately lowercased
 # IDs, resolve only if the exact seller vendor code or unique casefold exists.
 by_lower=collections.defaultdict(list)
 for k in master:by_lower[k.casefold()].append(k)
 proof=json.loads(Path("data/direct_recovery_20261124/automatic_feed_sync.json").read_text())
 live_prices=json.loads(Path("data/site_price_review_live_20261124.json").read_text())
 pr=collections.defaultdict(list)
 for row in live_prices["rows"]:pr[row["sku"]].append(row)
 # Extra candidates use the last public price review; distinguish this from
 # live feed verification. No marketplace fee or marketplace seller price.
 extra=["0am325066ae","0AM325587E","gidroakkumulyator-orig","0am325025","0am325025D","0am325091e-set","0am325091f-set","0am325025z","0am325025u"]
 records=[]
 for item in offers+[{"sku_feed":k,"vendor_code":k,"category":"candidate","price":None} for k in extra]:
  k=item["sku_feed"];choices=by_lower[k.casefold()]
  v=item.get("vendor_code");match=v if v in master and v.casefold()==k.casefold() else k if k in master else choices[0] if len(choices)==1 else None
  out={"sku_feed":k,"sku":match,"category":item["category"],"title":item.get("title"),"price":None,"procurement":None,"available":item.get("available"),"source":None}
  if not match or len(master[match])!=1:out["blocker"]="Master exact identity ambiguous";records.append(out);continue
  row=master[match][0];cost=num(row.get("Закупка, ₽"));out["procurement"]=str(cost) if cost else None
  out["name"]=row.get("Название товара")
  if item["category"]!="candidate":
   x=proof.get("checked",{}).get(k,{})
   price=num(item["price"])
   if price and num(x.get("price"))==price:
    out["price"]=str(price);out["source"]="feed matched latest verified protected site price";out["price_checked_at"]=proof.get("finished_utc");out["stock"]=x.get("stock")
   else:out["blocker"]="Unverified feed/site price";records.append(out);continue
  else:
   rr=pr.get(match,[])
   if len(rr)!=1:out["blocker"]="Missing/ambiguous public site price";records.append(out);continue
   price=num(rr[0].get("price"));out["price"]=str(price) if price else None;out["source"]="public site snapshot";out["price_checked_at"]=live_prices.get("date_utc")
  if not price or not cost:out["blocker"]="Missing positive purchase cost or site price";records.append(out);continue
  out["tax_assumption"]="USN 6% on seller revenue; confirm accounting treatment"
  out["gross_after_cost_tax"]=round(float(price*Decimal(".94")-cost),2)
  # explicitly illustrative, not known payment/delivery/return fees.
  out["scenarios"]=[]
  for reserve in (Decimal(".05"),Decimal(".10"),Decimal(".15")):
   contribution=price*(Decimal(".94")-reserve)-cost
   out["scenarios"].append({"other_variable_reserve_pct":float(reserve*100),"contribution_before_ads":round(float(contribution),2),"break_even_CAC":round(float(contribution),2),"break_even_CPC":{str(float(cv*100))+"%":round(float(contribution*cv),2) for cv in (Decimal(".002"),Decimal(".005"),Decimal(".01"),Decimal(".02"),Decimal(".03"))},"required_purchase_rate_pct_at_cpc_30":round(float(Decimal(30)/contribution*100),3) if contribution>0 else None,"break_even_ROAS":round(float(price/contribution),2) if contribution>0 else None})
  records.append(out)
 return {"rows":records,"source_master":"data/master_control.xlsx","price_proof":"data/direct_recovery_20261124/automatic_feed_sync.json","note":"No actual paid transaction verified. CAC, CPC and ROAS are scenarios only; no estimate of observed profit. Delivery, exchange/core returns, payment fees and returns require actual accounting."}
def get_public(url):
 req=urllib.request.Request(url,headers={"User-Agent":"LEOPARTS-SEO-audit/1.0"})
 with urllib.request.urlopen(req,timeout=25) as res:
  return res.status,res.geturl(),res.read(2_000_001)
def sitemap():
 status,url,raw=get_public(SITE+"/robots.txt")
 robots=raw.decode(errors="replace")[:15000]
 st,url,raw=get_public(SITE+"/sitemap.xml")
 rt=ET.fromstring(raw);locs=[x.text for x in rt.iter() if x.tag.split("}")[-1]=="loc" and x.text]
 # parse public sitemap indexes, bounded to 5 children, same domain only.
 children=[];pages=[]
 if rt.tag.split("}")[-1]=="sitemapindex":
  for loc in locs[:5]:
   if urllib.parse.urlsplit(loc).hostname!=urllib.parse.urlsplit(SITE).hostname:continue
   try:
    code,_,body=get_public(loc);t=ET.fromstring(body);p=[x.text for x in t.iter() if x.tag.split("}")[-1]=="loc" and x.text]
    children.append({"url":loc,"count":len(p),"status":code});pages+=p
   except Exception as e:children.append({"url":loc,"error":str(e)[:150]})
 else:pages=locs
 return {"robots_http":status,"robots_text":robots,"sitemap_http":st,"root_locations":locs,"children":children,"sitemap_pages":pages,"coverage_complete":rt.tag.split("}")[-1]!="sitemapindex" or len(locs)<=5 and all("error" not in x for x in children),"note":"Sitemap inclusion is not proof of search indexation."}
async def public_pages(offers):
 async with async_playwright() as p:
  browser=await p.chromium.launch(headless=True,args=["--no-sandbox"])
  results=[]
  urls=[SITE,SITE+"/mehatronik-dq200",SITE+"/catalog"]+[x["url"] for x in offers if x.get("url")]
  async def one(u,device,w,h):
   ctx=await browser.new_context(viewport={"width":w,"height":h},is_mobile=device=="mobile",has_touch=device=="mobile",device_scale_factor=1,service_workers="block")
   async def route(rt):
    req=rt.request;host=(urllib.parse.urlsplit(req.url).hostname or "").lower()
    if req.method not in ("GET","HEAD") or any(v in host for v in ("mc.yandex","metrika","google-analytics","doubleclick","tinkoff","tbank","ozon","payment","paykeeper")):await rt.abort()
    else:await rt.continue_()
   await ctx.route("**/*",route);page=await ctx.new_page();errors=[];page.on("pageerror",lambda e:errors.append(type(e).__name__))
   r={"requested_url":u,"device":device}
   try:
    res=await page.goto(u,wait_until="domcontentloaded",timeout=45000);await page.wait_for_timeout(1500)
    z=await page.evaluate("""() => {
     const meta=(name)=>document.querySelector('meta[name="'+name+'"]')?.content||null;
     const prop=(name)=>document.querySelector('meta[property="'+name+'"]')?.content||null;
     const visible=e=>{let r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
     const cta=[...document.querySelectorAll('a,button')].filter(visible).map(e=>({text:(e.innerText||'').trim().replace(/\\s+/g,' ').slice(0,80),top:Math.round(e.getBoundingClientRect().top)})).filter(x=>/купить|заказать|подобрать|корзин|VIN/i.test(x.text)).slice(0,25);
     const types=[];for(const s of document.querySelectorAll('script[type="application/ld+json"]')){try{const parse=o=>{if(Array.isArray(o))o.forEach(parse);else if(o&&typeof o==='object'){if(o['@type'])types.push(o['@type']);if(o['@graph'])parse(o['@graph'])}};parse(JSON.parse(s.textContent))}catch(e){}}
     let nav=performance.getEntriesByType('navigation')[0];
     const body=document.body.innerText||'';
     return {title:document.title,description:meta('description'),robots:meta('robots'),canonical:document.querySelector('link[rel=canonical]')?.href||null,h1:[...document.querySelectorAll('h1')].map(e=>e.innerText.trim().slice(0,150)),h2:[...document.querySelectorAll('h2')].map(e=>e.innerText.trim().slice(0,150)).slice(0,30),schema_types:types,microdata_product:!!document.querySelector('[itemtype*="schema.org/Product"]'),og_title:prop('og:title'),og_price:prop('product:price:amount')||prop('og:price:amount'),visible_ctas:cta,first_purchase_cta:cta.find(x=>/купить|корзин/i.test(x.text))||null,viewport:innerWidth,document_width:document.documentElement.scrollWidth,dom_ready_ms:nav?Math.round(nav.domContentLoadedEventEnd):null,product_links:[...new Set([...document.querySelectorAll('a[href*="tproduct"]')].map(e=>e.href))].slice(0,50),cart_runtime:typeof window.tcart__init==='function',data_layer:Array.isArray(window.dataLayer),vin_form:!!document.getElementById('form5246286101'),checkout_form:!!document.getElementById('form5260805101'),mentions_warranty:/гаранти/i.test(body),mentions_delivery:/доставк/i.test(body),mentions_exchange:/обмен|возврат.*стар/i.test(body)};
    }""")
    r.update(z,http_status=res.status if res else None,final_url=page.url,js_errors=len(errors))
   except Exception as e:r["error"]=type(e).__name__+": "+str(e)[:150]
   finally:await ctx.close()
   return r
  sem=asyncio.Semaphore(3)
  async def bounded(u,device,w,h):
   async with sem:return await one(u,device,w,h)
  jobs=[bounded(u,"mobile",390,844) for u in urls]+[bounded(SITE+"/mehatronik-dq200","desktop",1440,920)]
  results=await asyncio.gather(*jobs)
  await browser.close()
  return results
async def mobile_cart():
 # Local browser only: do not submit forms, no tracking, no payment navigation.
 async with async_playwright() as p:
  b=await p.chromium.launch(headless=True,args=["--no-sandbox"])
  ctx=await b.new_context(viewport={"width":390,"height":844},is_mobile=True,has_touch=True,device_scale_factor=1,service_workers="block")
  async def route(rt):
   req=rt.request;host=(urllib.parse.urlsplit(req.url).hostname or "").lower()
   if req.method not in ("GET","HEAD") or any(x in host for x in ("mc.yandex","metrika","google-analytics","doubleclick","tinkoff","tbank","ozon","payment","paykeeper")):
    await rt.abort()
   else:await rt.continue_()
  await ctx.route("**/*",route)
  page=await ctx.new_page();res=await page.goto(SITE+"/mehatronik-dq200#bez-platy",wait_until="domcontentloaded",timeout=45000)
  await page.wait_for_timeout(3000)
  buttons=page.locator("a.js-catalog-prod-btn")
  before=await cart.snapshot(page);clicked=False
  for i in range(min(await buttons.count(),20)):
   btn=buttons.nth(i)
   if not await btn.is_visible():continue
   if not await btn.evaluate("el => !!el.closest('.t-catalog__card, .t-store__card')"):continue
   if "купить" not in (await btn.inner_text(timeout=3000)).strip().lower():continue
   await btn.click(timeout=8000);clicked=True;break
  await page.wait_for_timeout(1500)
  after=await cart.snapshot(page)
  checkout=await page.evaluate("""() => {
    const visible=e=>{if(!e)return false;let r=e.getBoundingClientRect(),s=getComputedStyle(e);return r.width>0&&r.height>0&&s.display!=='none'&&s.visibility!=='hidden'};
    const form=document.getElementById('form5260805101');
    const cart=document.querySelector('.t706__cartwin');
    return {cart_open:visible(cart)&&/showed|show/.test(cart.className),cart_element_present:!!cart,form_visible:visible(form),payment_options:form?[...form.querySelectorAll('input[name="paymentsystem"]')].map(x=>({value:x.value,checked:x.checked})):[],delivery_options:form?[...form.querySelectorAll('input[type=radio]')].filter(x=>x.name!=='paymentsystem').map(x=>({name:x.name,value:x.value.slice(0,100)})):[],form_visible_fields:form?[...form.querySelectorAll('input:not([type=hidden]),select,textarea')].filter(visible).map(x=>({name:x.name,type:x.type,required:x.required||x.getAttribute('data-tilda-req')==='1'})):[],cart_text_sample:cart&&visible(cart)?cart.innerText.replace(/\\s+/g,' ').slice(0,1500):null}
  }""")
  result={"http_status":res.status if res else None,"local_product_click":clicked,"before":before,"after":after,"checkout":checkout,"no_submissions":True,"no_payment_attempt":True}
  await ctx.close();await b.close()
  return result

def run():
 d={"mode":"read_only_public_site_and_financial_scenarios","started_utc":now(),"site_mutations":0,"payments_attempted":0,"forms_submitted":0,"errors":{},"sections":{}}
 save(d)
 def go(k,fn):
  try:d["sections"][k]={"status":"ok","data":fn()}
  except Exception as e:d["errors"][k]=safe.scrub(e);d["sections"][k]={"status":"error","error":safe.scrub(e)}
  save(d)
 offers=xml_offers()
 go("economics",lambda:econ(offers))
 go("robots_sitemap",sitemap)
 go("public_pages",lambda:asyncio.run(public_pages(offers)))
 go("mobile_cart",lambda:asyncio.run(mobile_cart()))
 # Previously verified browser code; no real submission and analytics blocked.
 cart.OUT=ROOT/"cart_runtime.json"
 try:asyncio.run(cart.run());d["sections"]["cart_runtime"]={"status":"ok","data":json.loads(cart.OUT.read_text())}
 except Exception as e:d["errors"]["cart_runtime"]=safe.scrub(e)
 d["finished_utc"]=now();d["status"]="complete" if not d["errors"] else "partial";save(d)
 print(json.dumps({"status":d["status"],"sections":list(d["sections"]),"errors":d["errors"],"econ_rows":len(d["sections"].get("economics",{}).get("data",{}).get("rows",[])),"pages":len(d["sections"].get("public_pages",{}).get("data",[]))},ensure_ascii=False))
if __name__=="__main__":run()
