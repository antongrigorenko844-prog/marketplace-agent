"""Private scheduled business pulse. No seller writes and no plaintext report in GitHub."""
import os,sys,json,logging,tempfile
from datetime import datetime,timedelta
from pathlib import Path
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
import requests
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'.github/scripts'))
import leoparts_crisis_audit_20261124 as cr
import leoparts_crisis_followup_20261124 as follow
import leoparts_purchase_readiness as met
import ozon_client
logging.disable(logging.CRITICAL)
def r(v):return f'{v:,.0f}'.replace(',',' ')
def delta(c,p):return f'{(c/p-1)*100:+.0f}%' if p else 'н/д'
def sumdata(d,a,b,keys):
    rows=[v for k,v in d.items() if str(a)<=k<str(b)]
    return {k:sum(float(v.get(k) or 0) for v in rows) for k in keys}
def private_chat():
    token=os.getenv('TELEGRAM_BOT_TOKEN','')
    cfg=json.loads((ROOT/'data/owner_chat.json').read_text())
    chat=str(cfg.get('chat_id') or '')
    if not token or not chat.isdigit() or int(chat)<=0:raise RuntimeError('No private recipient')
    base='https://api.telegram.org/bot'+token
    resp=requests.post(base+'/getChat',json={'chat_id':chat},timeout=20)
    if not resp.ok or resp.json().get('result',{}).get('type')!='private':raise RuntimeError('Recipient not verified private')
    return base,chat
def main(send=False):
    base,chat=private_chat()
    now=datetime.now(cr.MOS).date();cr.END=now;cr.START=now-timedelta(days=60)
    with tempfile.TemporaryDirectory() as t:
        cr.OUT=Path(t)
        jobs={'ozon_fbs':lambda x:cr.ozon('fbs',x),'ozon_fbo':lambda x:cr.ozon('fbo',x),'wb':cr.wb,'market':cr.market,'avito':cr.avito}
        with ThreadPoolExecutor(max_workers=6) as pool:
            fs={k:pool.submit(cr.channel,k,f) for k,f in jobs.items()}
            finance=pool.submit(cr.finance,14)
            results={k:f.result() for k,f in fs.items()}
            finance.result()
        fin=json.loads((cr.OUT/'ozon_finance.json').read_text())
        a=now-timedelta(days=7);b=a-timedelta(days=7)
        lines=[f'LEOPARTS | бизнес-контроль за {now-timedelta(days=1):%d.%m.%Y}',f'7 полных дней {a:%d.%m}–{now-timedelta(days=1):%d.%m} против предыдущих 7']
        fbs,fbo=results['ozon_fbs'],results['ozon_fbo']
        def complete(x,n):return x.get('periods',{}).get(n,{}).get('current_complete') and x.get('periods',{}).get(n,{}).get('prior_complete')
        for n in ('7','30'):
            if complete(fbs,n) and complete(fbo,n):
                cur=sum(x['periods'][n]['current']['gross'] for x in [fbs,fbo]);prev=sum(x['periods'][n]['prior']['gross'] for x in [fbs,fbo])
                u=sum(x['periods'][n]['current']['units'] for x in [fbs,fbo]);pu=sum(x['periods'][n]['prior']['units'] for x in [fbs,fbo])
                lines.append(f'Ozon {n}д: заказы {r(cur)} ₽ ({delta(cur,prev)}); {r(u)} шт. ({delta(u,pu)})')
            else:lines.append(f'Ozon {n}д: неполная история заказов')
        for k,title in [('wb','WB'),('market','Маркет'),('avito','Авито Доставка')]:
            x=results[k];z=x.get('periods',{}).get('7',{})
            if z and (complete(x,'7') or k=='avito'):
                lines.append(f'{title}: {z["current"]["orders"]} против {z["prior"]["orders"]} за 7д'+(' (только доступная история)' if k=='avito' else ''))
                if k=='wb' and z['current']['orders']==0:lines.append('ВНИМАНИЕ: нет новых неотменённых заказов WB за 7 дней.')
            else:lines.append(f'{title}: источник неполный')
        keys=['payout','revenue','known_cost_sold','known_cost_return','unknown_cost_units','sold','returned']
        if len(fin.get('daily',{}))==14 and not fin.get('errors'):
            c=sumdata(fin['daily'],a,now,keys);p=sumdata(fin['daily'],b,a,keys)
            est=lambda x:x['payout']-x['revenue']*.06-x['known_cost_sold']+x['known_cost_return']
            lines.append(f'Ozon начисления 7д: {r(c["payout"])} ₽ ({delta(c["payout"],p["payout"])})')
            lines.append(f'Расчётный вклад после закупки и УСН: {r(est(c))} ₽ ({delta(est(c),est(p))}); не чистая прибыль; без закупки {int(c["unknown_cost_units"])} шт.')
            ftypes=defaultdict(float)
            for d,v in fin['daily'].items():
                if str(a)<=d<str(now):
                    for name,value in v.get('fee_types',{}).items():ftypes[name]+=value
            for name in ['Звёздные товары','Подписка Управление отзывами','Превышение индекса ошибок','Гибкий график выплат']:
                lines.append(f'{name}: {r(-ftypes.get(name,0))} ₽ (начисления 7д)')
        else:lines.append('Ozon финансы: неполная выгрузка; прибыль не рассчитана.')
        try:
            gg=met.goal_visits()['daily']
            v=sum(float(x.get('visits') or 0) for d,x in gg.items() if str(a)<=d<str(now))
            g=lambda key:sum(float(x.get('goals',{}).get(key) or 0) for d,x in gg.items() if str(a)<=d<str(now))
            lines.append(f'Сайт 7д: {r(v)} визитов / корзина {r(g("675487735"))} / начало оплаты {r(g("685988392"))} / покупка-цель {r(g("682257540"))}. Цели не равны оплате.')
        except Exception:lines.append('Метрика: данные недоступны.')
        try:
            e=follow.expected();st=ozon_client.get_stocks(follow.PICKS);zero=[]
            for item in st:
                sku=item.get('offer_id');parts=item.get('stocks') or []
                if isinstance(parts,dict):parts=parts.get('stocks') or []
                fbs=[x for x in parts if 'fbs' in str(x.get('type','')).lower()]
                if fbs and e.get(sku,0)>0 and sum(int(x.get('present') or 0) for x in fbs)==0:zero.append(sku)
            lines.append('Приоритетные позиции с нулевым FBS при положительном каталоге: '+(', '.join(zero) if zero else 'не обнаружены среди полученных строк'))
        except Exception:lines.append('Остатки Ozon: проверка не завершилась.')
        msg='\n'.join(lines)
        if send:
            response=requests.post(base+'/sendMessage',json={'chat_id':chat,'text':msg,'disable_web_page_preview':True},timeout=30)
            if not response.ok or not response.json().get('ok'):raise RuntimeError('Telegram delivery failed')
        else:
            follow.enc('daily_preview',{'private_recipient_verified':True,'sent':False,'message':msg})
if __name__=='__main__':
    main('--send' in sys.argv)
