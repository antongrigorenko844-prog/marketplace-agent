"""Read-only seller diagnosis. Outputs encrypted aggregate results, never customer data."""
import sys,os,json,zlib,base64,time,re,logging
from pathlib import Path
from collections import Counter,defaultdict
from datetime import date,datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1];os.chdir(ROOT);sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'.github/scripts'))
import ozon_client as oz,wb_client as wb,yandex_market_client as ym,avito_client as av
from config import config
import leoparts_purchase_readiness as met
import leoparts_crisis_audit_20261124 as cr
logging.disable(logging.CRITICAL)
KEY=b"""-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAtbgKr1UZFlt+9Grn2jge
VXk2++9wSRJYQZ2cyLVHw/4tFCtGVG5uExNAYN3P4VQDI1qVN7aIc1gC+TppoCPo
FXZ0YV5cD+SgA6JeIUdh+qmEHc0KdzUju35NZ2LGDC+D56oAx/IkZONUM7a7mZrj
PKP/y5lPnfW1e50svMpLo6M/DCt9l52nAO+gT9SZfPd5B9IiMBLbqV4Gbtrij28U
dxoPFs80Pu/jwoUzbP3xReX484akauRGvoV8CA5+vBeo/9VBDUx1mfn+tE3aPaYj
caD1FbaEUX3CI1jw4vndG6+JI+3V6kAqDBOxZh5BN+Jng1qQshtnZEcSchhMYdGi
bQIDAQAB
-----END PUBLIC KEY-----"""
OUT=ROOT/'data/crisis_followup_20261124'
OUT.mkdir(exist_ok=True,parents=True)
PICKS=['0am325025H','0am325025HX','0AM927769D','0am325025','0AM325587E','0am325066AC','122390AK-AF','0am325025f','0am325025x','6t30','0am325025m','0am325091e-set','0am325091f-set','0AM927769N','0AM325443D','0am325025ae','0am325066ae','Dq500','02E927770AL','0AM325025B']
def enc(name,d):
    from cryptography.hazmat.primitives import serialization,hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    k=AESGCM.generate_key(bit_length=256);n=os.urandom(12)
    pub=serialization.load_pem_public_key(KEY);wrapped=pub.encrypt(k,padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()),algorithm=hashes.SHA256(),label=None))
    v=wrapped+n+AESGCM(k).encrypt(n,zlib.compress(json.dumps(d,ensure_ascii=False,separators=(',',':')).encode(),9),None)
    (OUT/(name+'.enc')).write_text(base64.b64encode(v).decode()+'\n')
def call(d,name,fn):
    try:d[name]=fn()
    except Exception as e:d[name]={'error':cr.safe_error(e)}
def expected():
    import catalog_editor,ozon_new_products
    from sales_availability import quantity_for_sale
    a={}
    for p,fn in [('ozon_catalog.xlsx',catalog_editor.load_catalog_edits),('ozon_new_products.xlsx',ozon_new_products.load_new_edits)]:
        if not (ROOT/'data'/p).exists():continue
        for sku,row in fn(str(ROOT/'data'/p)).items():
            if sku in a:continue
            n=cr.num(quantity_for_sale(sku,row.get('quantity_to_sell')))
            if n is not None:a[sku]=max(0,int(n))
    return a
def wbcheck(exp):
    import sync_master_control as sm
    cards=wb.list_cards();prices=wb.get_prices();ware=wb.get_warehouses()
    if isinstance(ware,dict):ware=ware.get('warehouses',[])
    by={x.get('vendorCode'):x for x in cards}
    ids={int(s.get('chrtID') or s.get('chrtId')):str(c.get('vendorCode')) for c in cards for s in c.get('sizes') or [] if s.get('chrtID') or s.get('chrtId')}
    amounts=defaultdict(dict);err=[]
    for w in ware:
        wid=w.get('id');path=f'/api/v3/stocks/{wid}'
        try:
            data=wb._request('POST',config.wb_marketplace_base,path,{'chrtIds':list(ids)},retries=1)
            for x in data.get('stocks') or []:
                cid=int(x.get('chrtId') or x.get('chrtID') or 0)
                if cid in ids and x.get('amount') is not None:amounts[ids[cid]][str(wid)]=int(x['amount'])
        except Exception as e:err.append(cr.safe_error(e))
    p={x.get('vendorCode'):x for x in prices};alias={**sm._WB_ALIAS_OZON_TO_VENDOR,**sm._WB_FORCED_OZON_TO_VENDOR}
    selected={};mismatch=[];missing=[];unknown=[];photo=[]
    allrows=[]
    for sku,n in exp.items():
        vc=alias.get(sku,sku)
        if vc!=sku and sku in by and sku not in sm._WB_FORCED_OZON_TO_VENDOR:vc=sku
        c=by.get(vc)
        if not c:
            if n>0:missing.append(sku)
            continue
        ob=amounts.get(vc,{});qty=sum(ob.values()) if ob and len(ob)==len(ware) else None
        price=(p.get(vc,{}).get('sizes') or [{}])[0].get('discountedPrice')
        row=[sku,n,qty,len(c.get('photos') or []),price,c.get('nmID')]
        allrows.append(row)
        if sku in PICKS:selected[sku]=row[1:]
        if n>0 and qty==0:mismatch.append(row)
        if n>0 and qty is None:unknown.append(sku)
        if n>0 and not row[3]:photo.append(sku)
    return {'cards':len(cards),'warehouse_count':len(ware),'warehouse_flags':[{k:w[k] for k in ['id','isDeleting','isProcessing','deliveryType','cargoType'] if k in w} for w in ware],'stock_errors':err,'positive_catalog':sum(n>0 for n in exp.values()),'known_positive_stock_cards':sum(r[2] is not None and r[2]>0 for r in allrows),'missing':missing,'zero_with_positive_catalog':mismatch,'unknown':unknown,'no_photo':photo,'selected':selected,'new_orders_count':len(wb.get_new_orders())}
def ozcheck(exp):
    cards=oz.get_product_info_list(list(exp));stocks=oz.get_stocks(list(exp));prices=oz.get_prices_and_stocks(list(exp))
    by={x.get('offer_id'):x for x in cards};ps={x.get('offer_id'):x for x in prices};st={x.get('offer_id'):x for x in stocks}
    selected={};zero=[];unknown=[];bad=[];diff=[]
    for sku,n in exp.items():
        c=by.get(sku,{});raw=st.get(sku,{}).get('stocks')
        parts=raw if isinstance(raw,list) else (raw or {}).get('stocks',[])
        fbs=[x for x in parts if 'fbs' in str(x.get('type','')).lower()]
        present=sum(int(x.get('present') or 0) for x in fbs) if fbs else None
        reserved=sum(int(x.get('reserved') or 0) for x in fbs) if fbs else None
        status=c.get('statuses') or {};visibility=c.get('visibility_details') or {}
        p=ps.get(sku,{}).get('price') or {}
        row={'catalog':n,'fbs_present':present,'fbs_reserved':reserved,'prices':{k:p.get(k) for k in ['price','marketing_price','marketing_seller_price','min_price','old_price'] if k in p},'status':{k:status.get(k) for k in ['status','status_name','moderate_status','validation_status','is_created','status_failed'] if k in status},'visibility':{k:v for k,v in visibility.items() if isinstance(v,(str,bool,int))},'archived':c.get('is_archived')}
        if sku in PICKS:selected[sku]=row
        if n>0 and present==0:zero.append(sku)
        if n>0 and present is None:unknown.append(sku)
        if n>0 and present is not None and n!=present:diff.append([sku,n,present,reserved])
        if n>0 and (c.get('is_archived') is True or status.get('is_created') is False or status.get('status_failed') is True):bad.append(sku)
    return {'cards':len(cards),'stock_rows':len(stocks),'price_rows':len(prices),'zero_with_positive_catalog':zero,'unknown':unknown,'quantity_differences':diff,'bad_status':bad,'selected':selected}
def ymcheck(exp):
    _,cid=ym._creds();biz=ym.get_business_id()
    c=ym.get_all_offers();by={x['sku']:x for x in c};code,raw=ym._request('POST',f'/v2/campaigns/{cid}/offers/stocks',json_body={'offerIds':list(exp)})
    if code!=200:raise RuntimeError('HTTP '+str(code))
    result=raw.get('result') or {}
    rows=defaultdict(list)
    for w in result.get('warehouses') or []:
        for x in w.get('offers') or []:
            rows[x.get('offerId')].extend(x.get('stocks') or [])
    selected={};zero=[];missing=[];unknown=[]
    for sku,n in exp.items():
        if n>0 and sku not in by:missing.append(sku)
        rr=rows.get(sku,[])
        fit=[x for x in rr if x.get('type')=='FIT']
        count=sum(int(x.get('count') or 0) for x in fit) if fit else None
        if n>0 and count==0:zero.append(sku)
        if n>0 and count is None:unknown.append(sku)
        if sku in PICKS:selected[sku]=[n,count,sku in by]
    return {'offers':len(c),'warehouses':len(result.get('warehouses') or []),'missing':missing,'zero_with_positive_catalog':zero,'unknown':unknown,'selected':selected}
def avcheck(exp):
    listings=av.list_all_items();rows=listings if isinstance(listings,list) else listings.get('resources',listings.get('items',[]))
    active=[x for x in rows if str(x.get('status')).lower()=='active']
    ids=[int(x['id']) for x in active if x.get('id')]
    me=av._request('GET','/core/v1/accounts/self',retries=1);uid=me.get('id') or (me.get('result') or {}).get('id')
    end=date(2026,11,23);start=end-timedelta(days=59)
    stats={};errors=[]
    for i in range(0,len(ids),100):
        try:
            d=av._request('POST',f'/stats/v1/accounts/{uid}/items',json={'dateFrom':str(start),'dateTo':str(end),'itemIds':ids[i:i+100],'fields':['uniqViews','uniqContacts','uniqFavorites'],'periodGrouping':'day'},retries=1)
            for x in (d.get('result') or {}).get('items') or []:
                stats[str(x.get('itemId'))]=x.get('stats') or []
        except Exception as e:errors.append(cr.safe_error(e))
    decision=json.loads((ROOT/'data/leoparts_pricing_decision_20261124/decision.json').read_text())
    names={str(x.get('avito_main_id')):x['sku'] for x in decision['all_products'] if x.get('avito_main_id')}
    totals=[0,0,0,0];rank=[]
    for aid,rr in stats.items():
        nums=[0,0,0,0]
        for x in rr:
            dt=str(x.get('date') or x.get('dateFrom') or '')[:10]
            if not str(start)<=dt<=str(end):continue
            j=0 if dt>='2026-10-25' else 2
            nums[j]+=int(x.get('uniqViews') or 0);nums[j+1]+=int(x.get('uniqContacts') or 0)
        totals=[a+b for a,b in zip(totals,nums)]
        rank.append([names.get(aid,'avito:'+aid),*nums])
    return {'active':len(active),'all':len(rows),'stats_returned':len(stats),'requested':len(ids),'errors':errors,'current_prior_views_contacts':totals,'most_views':sorted(rank,key=lambda x:-x[1])[:10],'most_contacts':sorted(rank,key=lambda x:-x[2])[:10],'no_contacts_high_views':sorted([x for x in rank if not x[2]],key=lambda x:-x[1])[:10],'expense_by_ad':'not returned by v1 metrics; no inference from views alone'}
def inventory():
    d={'mode':'read_only','checked_at':cr.now(),'seller_writes':0}
    exp=expected()
    with ThreadPoolExecutor(max_workers=4) as pool:
        tasks=[pool.submit(call,d,k,lambda f=f:f(exp)) for k,f in [('wb',wbcheck),('ozon',ozcheck),('market',ymcheck),('avito',avcheck)]]
        for x in tasks:x.result()
    enc('inventory',d)
def site():
    from playwright.sync_api import sync_playwright
    from urllib.parse import urlsplit
    d={'mode':'read_only','seller_writes':0,'submitted_forms':0,'payments':0,'checked_at':cr.now()}
    call(d,'metrika_goals',met.goal_visits)
    if 'metrika_goals' in d and 'daily' in d['metrika_goals']:
        x=d['metrika_goals'];d['metrika_goals']={k:v for k,v in x.items() if k!='daily'}
    call(d,'webhook',met.tilda_form_dispatch_counts)
    if isinstance(d.get('webhook'),dict):d['webhook'].pop('latest_form_order_event_utc',None)
    browsers=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True)
        for label,settings in [('desktop',{'viewport':{'width':1366,'height':768}}),('mobile',p.devices['iPhone 13'])]:
            ctx=browser.new_context(**settings)
            page=ctx.new_page();events=[];errs=[]
            def watch(resp):
                u=urlsplit(resp.url);h=u.hostname or ''
                if h.endswith('mc.yandex.ru') or h.endswith('mc.yandex.com') or h.endswith('metrika.yandex.ru'):
                    path=re.sub(r'/\d{5,}', '/COUNTER',u.path)
                    events.append([h,path[:110],resp.status,resp.request.method])
            page.on('response',watch)
            page.on('requestfailed',lambda q:errs.append([urlsplit(q.url).hostname,urlsplit(q.url).path[:80]]) if 'mc.yandex.' in q.url else None)
            info={'device':label}
            try:
                response=page.goto('https://leoparts-akpp.ru/mehatronik-dq200?audit=leoparts-readonly',wait_until='domcontentloaded',timeout=45000)
                page.wait_for_timeout(7000)
                page.mouse.move(110,170);page.evaluate('window.scrollTo(0,350)');page.wait_for_timeout(3500);page.evaluate('window.scrollTo(0,800)');page.wait_for_timeout(8000)
                info.update(page.evaluate("""() => ({title:document.title,ym:typeof window.ym,ya:typeof window.Ya,cart:typeof window.tcart,dataLayer:Array.isArray(window.dataLayer),viewport:window.innerWidth,tagScripts:[...document.scripts].filter(x=>x.src.includes('mc.yandex.')).length,webvisorFlag:[...document.scripts].some(x=>/webvisor\\s*:\\s*true/.test(x.textContent||''))})"""))
                info['http']=response.status if response else None
            except Exception as e:info['error']=type(e).__name__
            info['metrika_responses']=[list(x) for x in sorted(set(tuple(x) for x in events))];info['failed_metrika']=errs[:10]
            browsers.append(info);ctx.close()
        browser.close()
    d['browsers']=browsers
    enc('site',d)
if __name__=='__main__':
    if sys.argv[1]=='inventory':inventory()
    elif sys.argv[1]=='site':site()
    else:raise SystemExit(1)
