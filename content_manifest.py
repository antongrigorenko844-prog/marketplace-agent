"""Approved content sources. Exact SKUs; no prices, stock or order processing."""
import copy
import json
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parent
PATH = ROOT / 'data/content_manifest.json'
CHANNELS = {'ozon', 'wb', 'market', 'avito', 'tilda'}


def load(path=None):
    path = Path(path or PATH)
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    if data.get('version') != 1 or not isinstance(data.get('products'), dict):
        raise ValueError('Invalid content manifest')
    for sku, row in data['products'].items():
        if not sku or sku != sku.strip():
            raise ValueError('Exact nonempty SKU required')
        if set(row) - {'sources', 'second_slide', 'ids', 'text', 'note', 'market_parameters', 'wb_characteristics'}:
            raise ValueError('Unapproved content fields: ' + sku)
        for channel, urls in row.get('sources', {}).items():
            if channel not in CHANNELS or not isinstance(urls, list) or not urls:
                raise ValueError('Invalid gallery source: ' + sku)
            for url in urls:
                if not isinstance(url, str) or not url.startswith('https://'):
                    raise ValueError('HTTPS media required')
            if len(urls) != len(set(map(normalize_url, urls))):
                raise ValueError('Duplicate gallery source: ' + sku)
        if set(row.get('text', {})) - CHANNELS:
            raise ValueError('Invalid text channel')
        for channel, fields in row.get('text', {}).items():
            if set(fields) - {'description', 'title', 'composition'}:
                raise ValueError('Only explicitly approved text fields allowed')
            if channel == 'ozon' and 'title' in fields:
                raise ValueError('Ozon title needs a separately reviewed content-only endpoint')
        plan=row.get('market_parameters',{})
        if set(plan)-{'category','allowed_previous_categories','set','remove'}:
            raise ValueError('Unknown Market parameter plan')
        for p in plan.get('set',[]):
            if set(p)-{'parameterId','value','valueId','unitId'}:raise ValueError('Unknown parameter field')
        for p in row.get('wb_characteristics',[]):
            if set(p)!={'id','value'} or p['id']!=378533:raise ValueError('Only reviewed WB composition is allowed')
    return data['products']


def normalize_url(url):
    p = urlsplit(str(url))
    host = p.netloc.replace('ir-20.ozone.ru', 'ir.ozone.ru')
    # A version query is a cache key, not a second photo.
    return urlunsplit((p.scheme, host, p.path, '', ''))


def gallery_for(sku, existing, channel, manifest=None):
    """An explicitly reviewed complete source wins over filename inference."""
    rows = load() if manifest is None else manifest
    source = rows.get(sku, {}).get('sources', {}).get(channel)
    return list(source if source is not None else existing)


def insert_second(current, slide, same=None):
    """Preserve the cover and all other media; a retry never duplicates a slide."""
    same = same or (lambda a, b: normalize_url(a) == normalize_url(b))
    current = list(current)
    if not current or not current[0]:
        raise ValueError('A verified existing cover is required')
    matches = [i for i, value in enumerate(current) if same(value, slide)]
    if 0 in matches:
        raise ValueError('Approved additional slide is already the cover; review required')
    if len(matches) > 1:
        raise ValueError('Duplicate live slides; review required')
    value = current[matches[0]] if matches else slide
    return current[:1] + [value] + [v for i, v in enumerate(current[1:], 1) if i not in matches]


def unchanged_fields(before, after, fields):
    changes = [key for key in fields if before.get(key) != after.get(key)]
    if changes:
        raise ValueError('Unrelated fields changed: ' + ', '.join(changes))


def market_offer_payload(offer, card, pictures=None, text=None):
    out = {'offerId': offer['offerId']}
    if pictures is not None:
        out['pictures'] = list(pictures)
    for key, value in (text or {}).items():
        if key not in {'title', 'description'}:
            raise ValueError('Unsupported Market content field')
        out['name' if key == 'title' else key] = value
    # The API otherwise drops typed characteristics in this seller account.
    if card.get('parameterValues'):
        out['parameterValues'] = copy.deepcopy(card['parameterValues'])
        out['marketCategoryId'] = card['mapping']['marketCategoryId']
    return {'offer': out}


def market_parameters_for(card, plan, definitions):
    """Apply an exact content plan, retaining unrelated compatible parameters."""
    if not plan:return card['mapping']['marketCategoryId'],copy.deepcopy(card.get('parameterValues',[]))
    old=card['mapping']['marketCategoryId'];new=plan.get('category',old)
    if old not in set(plan.get('allowed_previous_categories',[new]))|{new}:
        raise ValueError('Market category changed since review')
    defs={p['id']:p for p in definitions[str(new)]['parameters']}
    replacements=plan.get('set',[])
    replace_ids={p['parameterId']for p in replacements}
    if len(replace_ids)!=len(replacements):raise ValueError('Duplicate parameter plan')
    remove=set(plan.get('remove',[]))
    values=[copy.deepcopy(p)for p in card.get('parameterValues',[])if p['parameterId']not in replace_ids|remove]
    for p in values:
        if p['parameterId']not in defs:raise ValueError('Category migration would discard an unreviewed parameter')
    for p in replacements:
        d=defs[p['parameterId']]
        if 'valueId'in p:
            if not any(v['id']==p['valueId']and v['value']==p['value']for v in d.get('values',[])):
                raise ValueError('Dictionary value no longer matches')
        elif not d.get('allowCustomValues'):raise ValueError('Custom parameter value not allowed')
        if 'unitId'in p and p['unitId']not in {u['id']for u in d.get('unit',{}).get('units',[])}:
            raise ValueError('Invalid parameter unit')
        if d['type']=='NUMERIC':
            number=float(p['value']);limits=d.get('constraints',{})
            if not limits.get('minValue',number)<=number<=limits.get('maxValue',number):raise ValueError('Numeric parameter out of range')
        if len(str(p.get('value','')))>d.get('constraints',{}).get('maxLength',100000):raise ValueError('Parameter too long')
        values.append(copy.deepcopy(p))
    return new,values


def assert_canonical_description(sku, channel, value, *, present=True):
    """Reject stale content writes from legacy scripts, not just the new pipeline."""
    if not isinstance(sku, str) or not sku:
        return
    wanted=load().get(sku,{}).get('text',{}).get(channel,{}).get('description')
    if wanted is None:
        return
    if not present or value != wanted:
        raise ValueError(f'Canonical description mismatch for {channel}/{sku}; update content_manifest.json first')


def check_ozon_description_write(path, payload):
    if path not in {'/v1/product/attributes/update','/v3/product/import'}:
        return
    for row in payload.get('items',[]):
        sku=row.get('offer_id')
        values=[v.get('value') for attr in row.get('attributes',[]) if attr.get('id')==4191
                for v in attr.get('values',[])]
        if values or path=='/v3/product/import':
            if len(values)>1:
                raise ValueError(f'Ambiguous Ozon description for {sku}')
            assert_canonical_description(sku,'ozon',values[0] if values else None,present=bool(values))


def check_wb_description_write(path, payload):
    if path=='/content/v2/cards/update':
        rows=payload or []
    elif path=='/content/v2/cards/upload':
        rows=[row for group in (payload or []) for row in group.get('variants',[])]
    else:
        return
    for row in rows:
        assert_canonical_description(row.get('vendorCode'),'wb',row.get('description'),
                                     present='description' in row)


def check_market_description_write(path, payload):
    import re
    if not re.fullmatch(r'/v2/businesses/\d+/offer-mappings/update',path):
        return
    for mapping in (payload or {}).get('offerMappings',[]):
        row=mapping.get('offer',{})
        if 'description' in row:
            assert_canonical_description(row.get('offerId'),'market',row['description'])
