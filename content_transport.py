"""Fail-closed HTTP boundary for the content job, independent of caller names."""
import contextlib
import re
from urllib.parse import urlsplit, parse_qs
from unittest.mock import patch
import requests

FORBIDDEN = {'price', 'old_price', 'min_price', 'priceold', 'basicprice',
             'purchaseprice', 'discount', 'discounts', 'stock', 'stocks',
             'quantity', 'amount', 'warehouse_id', 'warehouseid'}


def no_commercial_fields(obj):
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key.lower() in FORBIDDEN:
                raise ValueError('Commercial field forbidden in content write: ' + key)
            no_commercial_fields(value)
    elif isinstance(obj, list):
        for value in obj:
            no_commercial_fields(value)


def classify(method, url, body=None):
    """Return read/content/auth. Unknown POST/PUT/PATCH/DELETE is rejected."""
    p = urlsplit(url); host = p.hostname; path = p.path; method = method.upper()
    if method in {'GET', 'HEAD'}:
        if host and (host.endswith('.tilda.ru') or host=='tilda.ru') and parse_qs(p.query).get('mode',[''])[0] in {'import','file'}:
            raise ValueError('CommerceML import is a mutation, not a content read')
        return 'read'
    if host == 'api-seller.ozon.ru':
        if method == 'POST' and path in {
            '/v4/product/info/attributes', '/v3/product/info/list',
            '/v5/product/info/prices', '/v4/product/info/stocks',
            '/v3/product/info/stocks', '/v1/product/rating-by-sku',
            '/v1/product/import/info', '/v3/product/list',
            '/v1/description-category/attribute',
        }:
            return 'read'
        if method == 'POST' and path == '/v2/product/pictures/import':
            if not isinstance(body, dict) or set(body) != {'items'}:
                raise ValueError('Invalid Ozon media request')
            for item in body['items']:
                if set(item) - {'offer_id','primary_image','images','color_image'}:
                    raise ValueError('Unexpected Ozon picture fields')
            no_commercial_fields(body)
            return 'content'
        if method == 'POST' and path == '/v1/product/attributes/update':
            if not isinstance(body, dict) or set(body) != {'items'}:
                raise ValueError('Invalid Ozon attributes request')
            for item in body['items']:
                if set(item) != {'offer_id','attributes'}:
                    raise ValueError('Unexpected Ozon attribute fields')
                for attr in item['attributes']:
                    if attr['id'] not in {4191,4384,11254}:
                        raise ValueError('Ozon attribute not approved for this content job')
            no_commercial_fields(body)
            return 'content'
    if host == 'content-api.wildberries.ru':
        if method == 'POST' and path in {'/content/v2/get/cards/list','/content/v2/cards/error/list','/content/v2/get/cards/trash'}:
            return 'read'
        if method == 'POST' and path == '/content/v3/media/save':
            if not isinstance(body,dict) or set(body) != {'nmId','data'}:
                raise ValueError('Invalid WB media request')
            no_commercial_fields(body); return 'content'
        if method == 'POST' and path == '/content/v2/cards/update':
            if not isinstance(body,list):raise ValueError('Invalid WB cards request')
            allowed={'nmID','vendorCode','brand','title','description','dimensions','characteristics','sizes','kizMarked'}
            if any(set(row)-allowed for row in body):raise ValueError('Invalid WB content fields')
            no_commercial_fields(body); return 'content'
    if host == 'api.partner.market.yandex.ru':
        if method == 'POST' and re.fullmatch(r'/v2/category/\d+/parameters',path):
            return 'read'
        if method == 'POST' and re.fullmatch(r'/v2/businesses/\d+/(offer-mappings|offer-cards)',path):
            return 'read'
        if method == 'POST' and re.fullmatch(r'/v2/businesses/\d+/offer-mappings/update',path):
            if not isinstance(body,dict) or set(body) != {'offerMappings'}:
                raise ValueError('Invalid Market content request')
            allowed={'offerId','pictures','name','description','parameterValues','marketCategoryId'}
            for row in body['offerMappings']:
                if set(row) != {'offer'} or set(row['offer']) - allowed:
                    raise ValueError('Unexpected Market content fields')
            no_commercial_fields(body); return 'content'
    if host == 'api.avito.ru' and path == '/token' and method == 'POST':
        return 'auth'
    raise ValueError(f'Endpoint not allowed in content job: {method} {host}{path}')


def enforce_description_only(url, body):
    """Narrow the transport boundary further when writing descriptions only."""
    p=urlsplit(url)
    if p.hostname=='api-seller.ozon.ru' and p.path=='/v1/product/attributes/update':
        if any(len(item['attributes']) != 1 or item['attributes'][0]['id'] != 4191
               for item in body['items']):
            raise ValueError('Only Ozon description attribute is allowed')
        return
    if p.hostname=='content-api.wildberries.ru' and p.path=='/content/v2/cards/update':
        # WB requires the existing full card. The batch planner copies the live
        # protected fields and verifies them before and after sending.
        if any('description' not in row or 'nmID' not in row or 'vendorCode' not in row for row in body):
            raise ValueError('WB existing card and description required')
        return
    if p.hostname=='api.partner.market.yandex.ru' and re.fullmatch(r'/v2/businesses/\d+/offer-mappings/update',p.path):
        for row in body['offerMappings']:
            offer=row['offer']
            if 'description' not in offer or set(offer)-{'offerId','description','parameterValues','marketCategoryId'}:
                raise ValueError('Only Market description and retained parameters allowed')
        return
    raise ValueError('Endpoint forbidden in description-only mode')

@contextlib.contextmanager
def guard(audit, allow_write=False, descriptions_only=False):
    original = requests.sessions.Session.request
    def checked(session, method, url, **kwargs):
        kind = classify(method, url, kwargs.get('json'))
        entry = None
        if kind == 'content':
            if not allow_write:
                raise ValueError('Read-only content inspection cannot write')
            if descriptions_only:
                enforce_description_only(url, kwargs.get('json'))
            entry = {'method':method, 'url':url, 'payload':kwargs.get('json')}
            audit.append(entry)
        response = original(session, method, url, **kwargs)
        if entry is not None and isinstance(response, requests.Response):
            entry['http_status'] = response.status_code
            try: entry['response'] = response.json()
            except ValueError: entry['response_text'] = response.text[:2000]
        return response
    with patch.object(requests.sessions.Session, 'request', checked):
        yield
