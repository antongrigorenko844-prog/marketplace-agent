"""Ozon description guard, based on TEST001 live evidence (run 45313082317).
The previous blanket hold is replaced only in the autonomous queue. The
legacy publisher stays paused. No non-description attribute is ever written.
"""
import copy
import json

KIZ=23173
MODEL=9048
DESCRIPTION=4191

def canonical(value):
    return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))

def model_identity(card):
    group=card.get('model_info')
    if not isinstance(group,dict) or not group.get('model_id') or not group.get('count'):
        raise ValueError('Missing actual Ozon model identity')
    if not card.get('id') or not card.get('sku') or not card.get('offer_id'):
        raise ValueError('Missing exact Ozon product identity')
    return group

def normalized_model(value, sku):
    # Only collapse repeated, exact copies of the suffix already present.
    # Do not remove the first suffix, change case, or normalize another SKU.
    suffix='_'+sku
    if not sku:return value
    while value.endswith(suffix+suffix):value=value[:-len(suffix)]
    return value

def one_default_kiz(a):
    if set(a)-{'id','complex_id','values'} or a.get('complex_id',0)!=0:return False
    v=a.get('values',[])
    return len(v)==1 and not (set(v[0])-{'value','dictionary_value_id'}) and str(v[0].get('value'))=='1' and v[0].get('dictionary_value_id',0)==0

def attributes(card):
    defaults={str(x) for x in card.get('attributes_with_defaults',[]) or []}
    attrs=[]
    kiz=[a for a in card.get('attributes',[]) if a.get('id')==KIZ]
    sku=card.get('offer_id','')
    for raw in card.get('attributes',[]):
        if raw.get('id')==DESCRIPTION:continue
        a=copy.deepcopy(raw)
        if a.get('id')==KIZ and len(kiz)==1 and one_default_kiz(a):continue
        if a.get('id')==MODEL and a.get('complex_id',0)==0 and len(a.get('values',[]))==1:
            v=a['values'][0]
            if isinstance(v.get('value'),str) and v.get('dictionary_value_id',0)==0:
                v['value']=normalized_model(v['value'],sku)
        if isinstance(a.get('values'),list):a['values'].sort(key=canonical)
        attrs.append(a)
    # An implicit default is equivalent only to the observed explicit value 1.
    if (not kiz and str(KIZ) in defaults) or (len(kiz)==1 and one_default_kiz(kiz[0])):
        attrs.append({'id':KIZ,'effective_observed_default':1})
    return sorted(attrs,key=canonical)

FIELDS=('id','product_id','offer_id','sku','name','barcode','barcodes','description_category_id',
        'type_id','height','depth','width','dimension_unit','weight','weight_unit','primary_image',
        'images','color_image','pdf_list','model_info')
def price_projection(row):
    p=row.get('price')
    if not isinstance(p,dict) or 'price' not in p:raise ValueError('Seller price missing')
    return {k:p[k] for k in ('price','old_price','min_price','currency_code','vat') if k in p}

def projection(card,price):
    model_identity(card)
    return {'fields':{k:card.get(k) for k in FIELDS},
            'attributes':attributes(card),
            'complex_attributes':sorted(card.get('complex_attributes',[]) or [],key=canonical),
            'seller_price':price_projection(price)}

def differences(before,after):
    changes=[]
    for key in before:
        if before[key]==after[key]:continue
        if key=='fields':
            changes.extend(k for k in before[key] if before[key][k]!=after[key][k])
        elif key=='attributes':
            def by_id(rows):
                out={}
                for a in rows:out.setdefault(str(a.get('id')),[]).append(a)
                return out
            a,b=by_id(before[key]),by_id(after[key])
            changes.extend('attribute:'+k for k in sorted(a.keys()|b.keys()) if a.get(k)!=b.get(k))
        else:changes.append(key)
    return changes

def verify(before,after,price_before,price_after):
    a,z=projection(before,price_before),projection(after,price_after)
    changed=differences(a,z)
    if changed:raise ValueError('Protected Ozon state changed: '+', '.join(changed))
    # Report service metadata differences separately. The actual attribute
    # values, price, SKU, images, model_id and model member count are checked.
    return {'default_metadata_changed':before.get('attributes_with_defaults')!=after.get('attributes_with_defaults'),
            'kiz_materialized':not any(x.get('id')==KIZ for x in before.get('attributes',[])) and any(x.get('id')==KIZ for x in after.get('attributes',[])),
            'redundant_model_suffix_changed':next((x for x in before.get('attributes',[]) if x.get('id')==MODEL),None)!=next((x for x in after.get('attributes',[]) if x.get('id')==MODEL),None)}
