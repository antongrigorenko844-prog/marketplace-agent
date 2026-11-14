"""Publish only approved text for two products, preserving other properties."""
import copy
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import content_updates
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym
from content_focus import save, OUT, IDS

def text_value(row, aid):
    return next((a['values'][0].get('value','') for a in row.get('attributes',[]) if a['id']==aid and a.get('values')), '')

def ozon_apply(rules):
    before = oz.get_product_names(IDS)
    save('ozon-before',before)
    assert {x['offer_id'] for x in before}==set(IDS)
    infos=oz.get_product_info_list(IDS)
    skus=[int(x['sku']) for x in infos]
    save('ratings-before',oz._post('/v1/product/rating-by-sku',{'skus':skus}))
    save('ozon-prices-before',oz.get_prices_and_stocks(IDS))
    save('ozon-stocks-before',oz.get_stocks(IDS))
    payload=[]
    for row in before:
        sku=row['offer_id']; rule=rules[sku]
        attrs=[{'id':aid,'complex_id':0,'values':[{'value':val}]} for aid,val in [(4191,rule['description']),(4384,rule['composition'])] if text_value(row,aid)!=val]
        if attrs:payload.append({'offer_id':sku,'attributes':attrs})
    save('ozon-payload',payload)
    if payload:save('ozon-response',oz.update_attributes(payload))
    for _ in range(12):
        after=oz.get_product_names(IDS)
        save('ozon-after',after)
        if all(text_value(r,4191)==rules[r['offer_id']]['description'] and text_value(r,4384)==rules[r['offer_id']]['composition'] for r in after):break
        time.sleep(10)
    save('ratings-after',oz._post('/v1/product/rating-by-sku',{'skus':skus}))
    save('ozon-info-after',oz.get_product_info_list(IDS))
    save('ozon-prices-after',oz.get_prices_and_stocks(IDS))
    save('ozon-stocks-after',oz.get_stocks(IDS))
    for old in before:
        new=next(a for a in after if a['offer_id']==old['offer_id'])
        for k in ['height','depth','width','weight','description_category_id','type_id','primary_image','images','complex_attributes','name']:
            assert old.get(k)==new.get(k),(old['offer_id'],'unexpected content change',k)
    return {r['offer_id']:text_value(r,4191)==rules[r['offer_id']]['description'] and text_value(r,4384)==rules[r['offer_id']]['composition'] for r in after}

def wb_apply(rules):
    before=[r for r in wb.list_cards() if r.get('vendorCode') in IDS]
    save('wb-before',before)
    assert {r['vendorCode'] for r in before}==set(IDS)
    payload=[]
    for row in before:
        if row.get('description')==rules[row['vendorCode']]['description']:continue
        p={k:copy.deepcopy(row[k]) for k in ['nmID','vendorCode','brand','title','dimensions','characteristics','sizes']}
        p['kizMarked']=row.get('kizMarked',False)
        p['description']=rules[row['vendorCode']]['description']
        payload.append(p)
    save('wb-payload',payload)
    if payload:
        response=wb.update_cards(payload);save('wb-response',response)
        assert not response.get('error'),response
    after=[r for r in wb.list_cards() if r.get('vendorCode') in IDS]
    save('wb-after',after)
    return {r['vendorCode']:r.get('description')==rules[r['vendorCode']]['description'] for r in after}

def market_apply(rules):
    biz=ym.get_business_id();path=f'/v2/businesses/{biz}/offer-mappings'
    status,before=ym._request('POST',path,json_body={'offerIds':IDS})
    assert status==200
    rows=before['result']['offerMappings'];save('market-before',before)
    assert {r['offer']['offerId'] for r in rows}==set(IDS)
    payload={'offerMappings':[{'offer':{'offerId':sku,'description':rules[sku]['description']}} for sku in IDS]}
    save('market-payload',payload)
    status,response=ym._request('POST',path+'/update',json_body=payload)
    save('market-response',{'http_status':status,'body':response})
    assert status==200 and response.get('status')=='OK',response
    status,after=ym._request('POST',path,json_body={'offerIds':IDS})
    save('market-after',after)
    return {r['offer']['offerId']:r['offer'].get('description')==rules[r['offer']['offerId']]['description'] for r in after.get('result',{}).get('offerMappings',[])}

def main():
    rules=content_updates.load()
    assert set(rules)==set(IDS)
    result={}
    for name,fn in [('ozon',ozon_apply),('wb',wb_apply),('market',market_apply)]:
        try: result[name]=fn(rules)
        except Exception as exc:
            result[name]={'error':str(exc)[:1000]}
        save('result',result)
    print(json.dumps(result,ensure_ascii=False))

if __name__=='__main__':main()
