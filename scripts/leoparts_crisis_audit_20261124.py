"""LEOPARTS crisis analysis: read-only seller APIs; aggregate data only.
No orders, listings, stock, pricing, ads or payment settings are mutated.
Date intervals use full Moscow days ending 2026-11-23.
"""
import sys,os,json,time,re,math
from pathlib import Path
from collections import defaultdict,Counter
from datetime import date,datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor,as_completed
import requests
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'.github/scripts'))
import ozon_client,wb_client,avito_client,yandex_market_client as ym,price_audit,ozon_unit
from config import config
END=date(2026,11,24); START=END-timedelta(days=180); MOS=ZoneInfo('Europe/Moscow')
OUT=ROOT/'data/crisis_audit_20261124';OUT.mkdir(parents=True,exist_ok=True)
def now():return datetime.now(timezone.utc).isoformat()
def safe_error(e):
    s=str(e)
    for k,v in os.environ.items():
        if len(v)>8 and any(x in k for x in ['TOKEN','KEY','SECRET','PASSWORD']):s=s.replace(v,'[redacted]')
    # Avoid saving any upstream body with buyer information.
    m=re.search(r'(?:HTTP |вернул |status(?: code)?[ =:]+)([45]\d\d)',s,re.I)
    return {'type':type(e).__name__,'http_status':int(m.group(1)) if m else None}
def save(name,r):
    p=OUT/(name+'.json');tmp=p.with_suffix('.tmp')
    tmp.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n',encoding='utf8');tmp.replace(p)
def num(x):
    try:
        if isinstance(x,dict):x=x.get('amount',x.get('value'))
        if x is None or x=='':return None
        v=float(x)
        return v if math.isfinite(v) else None
    except (TypeError,ValueError):return None
def day(x):
    if not x:return None
    s=str(x)
    try:
        z=datetime.fromisoformat(s.replace('Z','+00:00'))
        if z.tzinfo:z=z.astimezone(MOS)
        return z.date()
    except ValueError:
        for f in ['%d-%m-%Y %H:%M:%S','%d-%m-%Y','%d.%m.%Y']:
            try:return datetime.strptime(s,f).date()
            except ValueError:pass
    return None
def utc(d):return datetime.combine(d,datetime.min.time(),tzinfo=MOS).astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def windows(start,end,step=30):
    while start<end:
        stop=min(start+timedelta(days=step),end);yield start,stop;start=stop
def blank():return {'orders':0,'units':0,'gross':0.0,'priced_units':0,'cancelled_orders':0,'cancelled_units':0,'delivered_orders':0,'delivered_units':0,'delivered_gross':0.0,'delivered_priced_units':0,'payout':0.0,'payout_known_units':0}
class Agg:
    def __init__(self):
        self.daily=defaultdict(blank);self.sku=defaultdict(lambda:defaultdict(blank));self.statuses=Counter();self.skipped=Counter()
    def order(self,d,status,lines,cancelled=False,delivered=False):
        if d is None:self.skipped['missing_date']+=1;return
        if not START<=d<END:return
        k=str(d);a=self.daily[k];self.statuses[status]+=1
        if cancelled:a['cancelled_orders']+=1
        else:a['orders']+=1
        if delivered:a['delivered_orders']+=1
        seen=set()
        for sku,q,gross,payout in lines:
            if not sku or not q or q<0:self.skipped['invalid_item']+=1;continue
            b=self.sku[sku][k]
            if sku not in seen:
                b['cancelled_orders' if cancelled else 'orders']+=1
                if delivered:b['delivered_orders']+=1
                seen.add(sku)
            for x in (a,b):
                if cancelled:x['cancelled_units']+=q;continue
                x['units']+=q
                if gross is not None:x['gross']+=gross;x['priced_units']+=q
                if delivered:
                    x['delivered_units']+=q
                    if gross is not None:x['delivered_gross']+=gross;x['delivered_priced_units']+=q
                    if payout is not None:x['payout']+=payout;x['payout_known_units']+=q
    def data(self):return {'daily':dict(self.daily),'sku_daily':{k:dict(v) for k,v in self.sku.items()},'statuses':dict(self.statuses),'skipped':dict(self.skipped)}
def period_totals(daily,a,b):
    out=blank()
    for day_s,vals in daily.items():
        if str(a)<=day_s<str(b):
            for k,v in vals.items():out[k]+=v
    return {k:round(v,2) if isinstance(v,float) else v for k,v in out.items()}
def comparison(data,coverage):
    days=set()
    for a,b in coverage:
        x=date.fromisoformat(a);y=date.fromisoformat(b)
        while x<y:days.add(x);x+=timedelta(days=1)
    def covered(a,b):return all(a+timedelta(days=i) in days for i in range((b-a).days))
    out={}
    for n in (7,14,30,90):
        a=END-timedelta(days=n);b=a-timedelta(days=n)
        cur=period_totals(data['daily'],a,END);prev=period_totals(data['daily'],b,a)
        out[str(n)]={'current_from':str(a),'current_to':str(END-timedelta(days=1)),'prior_from':str(b),'prior_to':str(a-timedelta(days=1)),'current_complete':covered(a,END),'prior_complete':covered(b,a),'current':cur,'prior':prev}
    return out
def channel(name,fn):
    r={'source':name,'started_utc':now(),'mode':'read_only','external_mutations':0,'coverage':[],'errors':[]}
    try:
        data=fn(r);r.update(data);r['periods']=comparison(data,r['coverage']);r['status']='partial' if r['errors'] else 'ok'
    except Exception as e:r['status']='error';r['errors'].append(safe_error(e))
    r['finished_utc']=now();save(name,r);print(name,r['status'],flush=True)
    return r
def ozon(model,r):
    agg=Agg();seen=set();method=ozon_client.get_fbs_orders_since if model=='fbs' else ozon_client.get_fbo_orders_since
    for a,b in windows(START,END):
        try:
            rows=method(utc(a),utc(b))
            for o in rows:
                key=str(o.get('posting_number') or o.get('id') or '')
                if not key:agg.skipped['missing_posting_id']+=1;continue
                if key in seen:continue
                seen.add(key)
                d=day(o.get('in_process_at') or o.get('created_at'));s=str(o.get('status','')).lower()
                lines=[]
                for x in o.get('products') or []:
                    q=int(x.get('quantity') or 0);p=num(x.get('price'))
                    lines.append((str(x.get('offer_id') or 'sku:'+str(x.get('sku'))),q,p*q if p is not None else None,None))
                agg.order(d,s,lines,'cancel' in s,s=='delivered')
            r['coverage'].append([str(a),str(b)])
        except Exception as e:r['errors'].append({'from':str(a),'to_exclusive':str(b),**safe_error(e)})
    r['definition']='Posting count; non-cancelled ordered units, current status at snapshot; product price from posting, not settled revenue.'
    r['date_basis']='in_process_at, fallback created_at (Moscow)'
    return agg.data()
def wb(r):
    agg=Agg();first=END-timedelta(days=90)
    rows=wb_client.get_orders_since(str(first));seen=set()
    for o in rows:
        key=str(o.get('srid') or '')
        if not key:agg.skipped['missing_srid']+=1;continue
        if key in seen:continue
        seen.add(key)
        d=day(o.get('date'));sku=str(o.get('supplierArticle') or '')
        p=num(o.get('priceWithDisc'));cancel=o.get('isCancel') is True
        agg.order(d,'cancelled' if cancel else 'not_cancelled',[(sku,1,p,None)],cancel,False)
    r['coverage']=[[str(first),str(END)]]
    r['definition']='Statistics API unit-level orders; priceWithDisc; no delivery status inferred. 90-day retention.'
    if len(rows)>=80000:r['coverage']=[];r['errors'].append({'reason':'possible_response_limit'})
    try:
        # Sale/return date is not order date. Keep separate; do not combine into order cohort.
        time.sleep(1)
        res=requests.get(config.wb_statistics_base+'/api/v1/supplier/sales',headers=wb_client._headers(),params={'dateFrom':str(first)},timeout=60)
        if res.status_code==429:
            time.sleep(65);res=requests.get(config.wb_statistics_base+'/api/v1/supplier/sales',headers=wb_client._headers(),params={'dateFrom':str(first)},timeout=60)
        res.raise_for_status();sales=res.json()
        if not isinstance(sales,list):raise ValueError('Invalid WB sales')
        sd=defaultdict(lambda:{'sold':0,'returned':0,'seller_amount':0.0,'for_pay':0.0,'known_pay_rows':0})
        ss=set()
        for x in sales:
            sid=str(x.get('saleID') or '');d=day(x.get('date'))
            if not sid or sid in ss or not d or not first<=d<END:continue
            ss.add(sid);v=sd[str(d)]
            sign=-1 if sid.upper().startswith('R') else 1 if sid.upper().startswith('S') else 0
            if not sign:continue
            v['sold' if sign>0 else 'returned']+=1
            p=num(x.get('priceWithDisc'));pay=num(x.get('forPay'))
            if p is not None:v['seller_amount']+=sign*abs(p)
            if pay is not None:v['for_pay']+=sign*abs(pay);v['known_pay_rows']+=1
        r['sales_daily']=dict(sd);r['sales_rows']=len(sales);r['sales_note']='Preliminary WB sales/returns, forPay not final net profit; separate dates.'
    except Exception as e:r['errors'].append({'section':'sales',**safe_error(e)})
    return agg.data()
def market(r):
    agg=Agg();seen=set();biz=ym.get_business_id();_,cid=ym._creds()
    for a,b in windows(START,END):
        try:
            token=None;tokens=set()
            for _ in range(200):
                params={'limit':50}
                if token:params['pageToken']=token
                status,body=ym._request('POST',f'/v1/businesses/{biz}/orders',params=params,json_body={'campaignIds':[int(cid)],'dates':{'creationDateFrom':str(a),'creationDateTo':str(b)},'fake':False})
                if status!=200:raise RuntimeError('HTTP '+str(status))
                result=body.get('result',body);rows=result.get('orders')
                if not isinstance(rows,list):raise ValueError('No orders array')
                for o in rows:
                    key=str(o.get('orderId') or o.get('id') or '')
                    if not key or key in seen or o.get('fake'):continue
                    seen.add(key);d=day(o.get('creationDate'));s=str(o.get('status','')).upper()
                    lines=[]
                    for x in o.get('items') or []:
                        q=int(x.get('count') or 0);pr=x.get('prices') or {}
                        payment=num(pr.get('payment'));sub=num(pr.get('subsidy'))
                        # Market item pricing is kept as unknown unless a documented total exists.
                        lines.append((str(x.get('offerId') or ''),q,None,None))
                    agg.order(d,s,lines,s in {'CANCELLED','CANCELED','UNPAID','PLACING','RESERVED'},s=='DELIVERED')
                token=(result.get('paging') or {}).get('nextPageToken')
                if not token:break
                if token in tokens:raise RuntimeError('Repeated page token')
                tokens.add(token)
            else:raise RuntimeError('Pagination limit')
            r['coverage'].append([str(a),str(b)])
        except Exception as e:r['errors'].append({'from':str(a),'to_exclusive':str(b),**safe_error(e)})
    r['definition']='Actual non-test market orders; excluded unpaid/placing/reserved/cancelled. Units and delivered only; gross unverified.'
    return agg.data()
def avito(r):
    agg=Agg();rows=avito_client.get_orders(date_from=int(datetime.combine(START,datetime.min.time(),tzinfo=MOS).timestamp()),max_pages=100)
    p=json.loads((ROOT/'data/leoparts_pricing_decision_20261124/decision.json').read_text())
    mapping={str(x['avito_main_id']):x['sku'] for x in p['all_products'] if x.get('avito_main_id')}
    seen=set()
    for o in rows:
        key=str(o.get('id') or o.get('marketplaceId') or '')
        if not key or key in seen:continue
        seen.add(key);d=day(o.get('createdAt'));s=str(o.get('status','')).lower();lines=[]
        for x in o.get('items') or []:
            sku=mapping.get(str(x.get('avitoId')),'avito:'+str(x.get('avitoId')));q=int(x.get('count') or 0);pr=x.get('prices') or {}
            # Avito item total is seller payout after commission, not all account expenses.
            p0=num(pr.get('price'));pay=num(pr.get('total'))
            lines.append((sku,q,p0*q if p0 is not None else None,pay))
        agg.order(d,s,lines,any(t in s for t in ['cancel','reject','return','refund','expired']),s in {'delivered','closed'})
    # API may return a limited history: show observed period rather than claim full 180 days.
    r['coverage']=[];r['requested_from']=str(START);r['rows_returned']=len(rows)
    r['definition']='Avito Delivery orders only, not off-platform/direct deals; history completeness not established. Item price times quantity; payout fields preliminary.'
    if len(rows)>=2000:r['errors'].append({'reason':'pagination_cap'})
    return agg.data()
def finance():
    r={'source':'ozon_accrual_by_day','started_utc':now(),'mode':'read_only','external_mutations':0,'daily':{},'sku_daily':{},'errors':[]}
    start=END-timedelta(days=60)
    types=price_audit._accrual_types(ozon_client)
    mapping={}
    try:
        x=json.loads((ROOT/'data/ozon_products.json').read_text())
        for row in x.get('list',[]):
            if row.get('sku'):mapping[str(row['sku'])]=row['offer_id']
        for row in x.get('details',[]):
            for src in row.get('sources') or []:
                if src.get('sku'):mapping[str(src['sku'])]=row['offer_id']
    except Exception:pass
    master=ozon_unit._master()
    def zero():return {'payout':0.0,'revenue':0.0,'sold':0,'returned':0,'known_cost_sold':0.0,'known_cost_return':0.0,'unknown_cost_units':0,'fees':0.0,'unallocated':0.0}
    for i in range((END-start).days):
        d=str(start+timedelta(days=i))
        try:
            records=price_audit._accruals_by_day(ozon_client,d);a=zero();per=defaultdict(zero);by_type=defaultdict(float)
            for x in records:
                total=num(x.get('total_amount',x.get('amount'))) or 0;a['payout']+=total
                prods=(x.get('posting') or {}).get('products') or []
                if x.get('accrued_category')=='POSTING' and prods:
                    parts=[]
                    for p in prods:
                        cm=p.get('commission') or {};q=int(abs(num(p.get('quantity')) or 1))
                        rev=num(cm.get('sale_amount'))
                        if rev is None:rev=(num(cm.get('seller_price')) or 0)*q
                        if rev>0 and price_audit.comm_sign_fix(cm)<0:rev=-rev
                        comm=num(cm.get('sale_commission',cm.get('commission'))) or 0
                        dl=p.get('delivery') or {};log=num(dl.get('total_accrued'))
                        if log is None:log=sum(num(t.get('accrued')) or 0 for t in dl.get('services') or [])
                        sku=mapping.get(str(p.get('sku')),'sku:'+str(p.get('sku')))
                        parts.append((sku,q,rev,comm,log))
                    for sku,q,rev,comm,log in parts:
                        z=per[sku];cost=ozon_unit._master_item(master,sku).get('cost')
                        payout=total if len(parts)==1 else rev+comm+log
                        z['payout']+=payout
                        for v in (a,z):
                            v['revenue']+=rev;v['sold']+=q if rev>0 else 0;v['returned']+=q if rev<0 else 0
                            if rev and isinstance(cost,(float,int)) and cost>0:v['known_cost_sold' if rev>0 else 'known_cost_return']+=cost*q
                            elif rev:v['unknown_cost_units']+=q
                for e in (x.get('item_fees') or {}).get('fees') or []:
                    sku=mapping.get(str(e.get('sku')),'sku:'+str(e.get('sku')))
                    for fee in e.get('fees') or []:
                        v=num(fee.get('accrued',fee.get('amount'))) or 0
                        per[sku]['payout']+=v;by_type[types.get(str(fee.get('type_id')),str(fee.get('type_id')))]+=v
                if x.get('non_item_fee'):
                    fee=x['non_item_fee'];by_type[types.get(str(fee.get('type_id')),str(fee.get('type_id')))]+=total
            a['unallocated']=a['payout']-sum(x['payout'] for x in per.values())
            a['fees']=a['payout']-a['revenue'];a['operations']=len(records);a['fee_types']=dict(by_type)
            r['daily'][d]=a
            for sku,v in per.items():r['sku_daily'].setdefault(sku,{})[d]=v
        except Exception as e:r['errors'].append({'date':d,**safe_error(e)})
        if i%7==0:save('ozon_finance',r)
        time.sleep(.25)
    r['status']='partial' if r['errors'] else 'ok';r['finished_utc']=now();save('ozon_finance',r);print('ozon finance',r['status'],len(r['daily']),flush=True)
def traffic():
    import direct_preflight_20261120 as d
    import leoparts_purchase_readiness as m
    r={'mode':'read_only','external_mutations':0,'started_utc':now(),'errors':[]}
    try:
        goals=['675487735','675506966','675507028','682257540','685988392','686020680']
        metrics=['ym:s:visits']+['ym:s:goal'+g+'visits' for g in goals]
        q={'ids':'113839860','date1':str(START),'date2':str(END-timedelta(days=1)),'metrics':','.join(metrics),'dimensions':'ym:s:date,ym:s:lastTrafficSource','accuracy':'full','limit':'10000','lang':'ru'}
        z=m.metrika_get('/stat/v1/data',q)
        r['metrika']={'sampled':z.get('sampled'),'data':[{'date':x['dimensions'][0]['name'],'source':x['dimensions'][1]['name'],'visits':x['metrics'][0],'goals':dict(zip(goals,x['metrics'][1:]))} for x in z.get('data',[])]}
    except Exception as e:r['errors'].append({'section':'metrika',**safe_error(e)})
    try:
        p={'SelectionCriteria':{'DateFrom':str(START),'DateTo':str(END-timedelta(days=1)),'Filter':[{'Field':'CampaignId','Operator':'IN','Values':['716316525']}]},'FieldNames':['Date','CampaignId','Impressions','Clicks','Cost','Conversions'],'ReportName':'crisis-audit-'+str(time.time_ns()),'ReportType':'CAMPAIGN_PERFORMANCE_REPORT','DateRangeType':'CUSTOM_DATE','Format':'TSV','IncludeVAT':'YES'}
        import csv,io
        for _ in range(14):
            status,raw,_=d.request('reports','read',p,True)
            if status==200:
                r['direct_daily']=list(csv.DictReader(io.StringIO(raw),delimiter='\t'));break
            time.sleep(5)
        else:raise RuntimeError('Report not ready')
    except Exception as e:r['errors'].append({'section':'direct',**safe_error(e)})
    r['status']='partial' if r['errors'] else 'ok';r['finished_utc']=now();save('traffic',r)
def wordstat():
    r={'mode':'read_only','started_utc':now(),'external_mutations':0,'series':{},'errors':[],'limit':'Official API unavailable after 2026-10-07; no November trend inferred.'}
    try:
        key=os.environ['WORDSTAT_API_KEY'];folder=os.environ['WORDSTAT_FOLDER_ID']
        for phrase in ['мехатроник dq200','гидроаккумулятор dq200','ремкомплект мехатроника dq200','плата мехатроника dq200']:
            try:
                resp=requests.post('https://searchapi.api.cloud.yandex.net/v2/wordstat/dynamics',headers={'Authorization':'Api-Key '+key,'Content-Type':'application/json'},json={'phrase':phrase,'period':'PERIOD_WEEKLY','fromDate':'2025-11-03T00:00:00Z','toDate':'2026-10-05T00:00:00Z','regions':['225'],'devices':['DEVICE_ALL'],'folderId':folder},timeout=35)
                resp.raise_for_status();z=resp.json()
                r['series'][phrase]=[{'date':x.get('date'),'count':x.get('count'),'share':x.get('share')} for x in z.get('results',[])]
            except Exception as e:r['errors'].append({'phrase':phrase,**safe_error(e)})
    except Exception as e:r['errors'].append(safe_error(e))
    r['status']='partial' if r['errors'] else 'ok';r['finished_utc']=now();save('wordstat',r)
if __name__=='__main__':
    mode=sys.argv[1]
    if mode=='orders':
        jobs={'ozon_fbs':lambda r:ozon('fbs',r),'ozon_fbo':lambda r:ozon('fbo',r),'wb':wb,'market':market,'avito':avito}
        with ThreadPoolExecutor(max_workers=5) as pool:
            fs=[pool.submit(channel,n,f) for n,f in jobs.items()]
            for f in as_completed(fs):f.result()
    elif mode=='finance':finance()
    elif mode=='traffic':
        with ThreadPoolExecutor(max_workers=2) as pool:
            for f in [pool.submit(traffic),pool.submit(wordstat)]:f.result()
    else:raise SystemExit('unknown mode')
