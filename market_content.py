"""Market-specific text and typed characteristics; no commercial fields."""
import copy
import yandex_market_client as ym
from content_updates import description_for

def grouped(values):
    out={}
    for value in values:out.setdefault(value['parameterId'],[]).append(copy.deepcopy(value))
    return out

def flatten(groups):
    return [v for values in groups.values() for v in values]

def read_rows(biz,kind,ids):
    key={'offer-cards':'offerCards','offer-mappings':'offerMappings'}[kind]
    rows=[];token=None;seen=set()
    for _ in range(100):
        params={'limit':100}
        if token:params['pageToken']=token
        body={'offerIds':ids}
        if kind=='offer-cards':body['withRecommendations']=True
        status,data=ym._request('POST',f'/v2/businesses/{biz}/{kind}',params=params,json_body=body)
        assert status==200 and data.get('status')=='OK',(status,data)
        result=data['result'];rows+=result.get(key,[]);token=result.get('paging',{}).get('nextPageToken')
        if not token:return rows
        assert token not in seen,'Repeated pagination token'
        seen.add(token)
    raise RuntimeError('Pagination did not end')

def preserve_typed_parameters(biz,payload):
    ids=[r['offer']['offerId'] for r in payload]
    cards={r['offerId']:r for r in read_rows(biz,'offer-cards',ids)}
    assert set(cards)==set(ids)
    for r in payload:
        offer=r['offer'];sku=offer['offerId']
        if cards[sku].get('parameterValues'):
            offer['parameterValues']=copy.deepcopy(cards[sku]['parameterValues'])
            offer['marketCategoryId']=cards[sku]['mapping']['marketCategoryId']
    return payload
