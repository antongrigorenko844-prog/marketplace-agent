"""Read-only LEOPARTS Yandex Market content, live campaign and order counts."""
import json
import logging
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
sys.path.insert(0, str(Path.cwd()))
import yandex_market_client as ym

def collect_pages(path, body, field):
    # Only the documented read endpoints can be reached.
    if not path.endswith(('/offer-mappings','/offer-cards','/offers')):
        raise ValueError('unexpected read route')
    result, seen = [], set()
    token = None
    for _ in range(300):
        params = {'limit': 100}
        if token:
            params['pageToken'] = token
        status, response = ym._request('POST',path,params=params,json_body=body)
        if status != 200 or response.get('status')!='OK':
            raise RuntimeError(f'HTTP {status}; error codes: {[e.get("code") for e in response.get("errors",[])]}')
        page = response['result']
        result.extend(page[field])
        token = page.get('paging',{}).get('nextPageToken')
        if not token:
            return result
        if token in seen:
            raise RuntimeError('repeated next page token')
        seen.add(token)
    raise RuntimeError('read pagination limit')

def order_metrics():
    # Persist no buyer/contact/address data or order IDs.
    today = datetime.now(timezone.utc).date()
    maps = {}
    for row in ym.get_orders_since(days_back=28):
        order_id = row.get('id')
        if order_id is None:
            continue
        maps[order_id] = row
    totals = defaultdict(Counter)
    start = today-timedelta(days=28)
    for row in maps.values():
        raw = str(row.get('creationDate') or '')[:10]
        try:
            day = datetime.strptime(raw,'%d-%m-%Y').date() if raw[2:3]=='-' else datetime.strptime(raw,'%Y-%m-%d').date()
        except ValueError:
            raise ValueError('unsupported order creation date')
        if start<=day<today:
            for item in row.get('items',[]):
                sku = item.get('offerId')
                if sku:
                    totals[(str(sku),str(day))][row.get('status','UNKNOWN')] += int(item.get('count') or 0)
    return {'date_from_inclusive':str(start),'date_to_exclusive':str(today),'calendar':'UTC/extract; order creation date as returned by seller API','daily':[
        {'sku':sku,'date':day,'units_by_status':dict(cnt)} for (sku,day),cnt in sorted(totals.items())]}

def main():
    _,camp = ym._creds()
    biz = ym.get_business_id()
    out = {'captured_at':datetime.now(timezone.utc).isoformat(),'business_id':biz,'campaign_id':camp,'errors':{}}
    for key, path, body,field in [
        ('active',f'/v2/businesses/{biz}/offer-mappings',{'archived':False},'offerMappings'),
        ('archived',f'/v2/businesses/{biz}/offer-mappings',{'archived':True},'offerMappings'),
        ('campaign',f'/v2/campaigns/{camp}/offers',{},'offers'),
        ('cards',f'/v2/businesses/{biz}/offer-cards',{'withRecommendations':True},'offerCards'),
    ]:
        try:
            out[key] = collect_pages(path,body,field)
        except Exception as exc:
            out['errors'][key] = str(exc) if type(exc) == RuntimeError else type(exc).__name__
    if 'cards' not in out and out.get('active'):
        # Some accounts only return ratings for explicitly requested offers.
        groups=[r['offer']['offerId'] for r in out['active']]
        try:
            cards=[]
            for i in range(0,len(groups),100):
                cards+=collect_pages(f'/v2/businesses/{biz}/offer-cards',{'offerIds':groups[i:i+100]},'offerCards')
            out['cards']=cards
            out['errors'].pop('cards',None)
            out['notes']=['rating content retrieved without withRecommendations']
        except Exception as exc:
            out['errors']['cards_fallback']=type(exc).__name__
    try:
        out['orders']=order_metrics()
    except Exception as exc:
        out['errors']['orders']=type(exc).__name__
    kept_offer={"offerId","name","category","pictures","videos","vendor","description","weightDimensions","vendorCode","params","basicPrice","cardStatus","mediaFiles","archived","marketCategoryId","parameterValues"}
    for group in ("active","archived"):
        if group in out:
            out[group]=[{"offer":{k:v for k,v in r["offer"].items() if k in kept_offer},"mapping":r.get("mapping",{})} for r in out[group]]
    if "campaign" in out:
        out["campaign"]=[{k:v for k,v in r.items() if k in ("offerId","basicPrice","status","cofinancePrice","campaignPrice","updatedAt","sellingPrograms")} for r in out["campaign"]]
    return out

if __name__=='__main__':
    import argparse,contextlib
    from cryptography.hazmat.primitives.serialization import load_pem_public_key
    from live_metrics import encrypt,capture
    parser=argparse.ArgumentParser()
    parser.add_argument("--public-key",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    args=parser.parse_args()
    logging.disable(logging.CRITICAL)
    public=load_pem_public_key(args.public_key.read_bytes())
    with open(os.devnull,"w") as nul,contextlib.redirect_stdout(nul),contextlib.redirect_stderr(nul):
        result=capture(main)
    encrypt(result,public,args.output)
    print("Read-only catalog check finished. Encrypted output is ready.")
