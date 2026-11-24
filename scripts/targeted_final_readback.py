"""Independent exact final diagnostics. Seller APIs are read-only."""
import sys, json, hashlib, re, time
from pathlib import Path
from urllib.parse import urlsplit
from io import BytesIO
from xml.etree import ElementTree as ET
import requests
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import content_pipeline as p
import content_aux_sync as aux
import content_manifest as cmf
import wb_client as wb
import avito_client as av
import ozon_client as oz
from content_transport import guard
import content_master_sync as cms
import avito_feed as af

def sha(b):return hashlib.sha256(b).hexdigest()
def wb_read():
    old='670860344'; new='1922846685'
    _,rows,dupes=cms.read_rows((ROOT/'data/master_control.xlsx').read_bytes(),'Товары',12)
    live=[c for c in wb.list_cards() if str(c.get('nmID')) in (old,new)]
    # Documented trash-list endpoint, exact textSearch, read-only POST.
    trash={}
    for key in (old,new,'0AM927769D','0AM927769D-plata'):
        try:
            r=wb._request('POST',wb.config.wb_content_base,'/content/v2/get/cards/trash',
                          {'settings':{'cursor':{'limit':100},'filter':{'textSearch':key}}},retries=1)
            if not isinstance(r.get('cards'),list):raise ValueError('Missing cards list')
            trash[key]={'cards':[{'nmID':c.get('nmID'),'vendorCode':c.get('vendorCode'),'trashedAt':c.get('trashedAt')} for c in r['cards']],
                        'cursor':r.get('cursor')}
        except Exception as exc:trash[key]={'error':str(exc)[:160]}
    prices=[x for x in wb.get_prices() if str(x.get('nmID')) in (old,new)]
    fields=('nmID','vendorCode','title','brand','subjectID','dimensions','characteristics','sizes','documents','updatedAt')
    result={'live':[dict({k:c.get(k) for k in fields},description_hash=p.digest(c.get('description'))) for c in live],
            'trash_queries':trash,'prices':prices,'master_status':{s:{k:rows.get(s,{}).get(k) for k in ('wb_status','ozon_status')} for s in ('0AM927769D','0AM927769D-plata')},
            'master_duplicates':[s for s in ('0AM927769D','0AM927769D-plata') if s in dupes]}
    return result

def market_read():
    import yandex_market_client as ym
    sku='0am325025Y'
    with guard([],allow_write=False,descriptions_only=True):
        snap=p.read_channel('market',[sku],{'business_id':ym.get_business_id()})
    offer,detail=p.card_detail('market',snap,sku)
    return {'sku':sku,'card_id':p.identity('market',offer,detail),
            'seller_text_matches':p.api.description_value('market',offer)==cmf.load()[sku]['text']['market']['description'],
            'protected_hash':p.protected_hash('market',offer,detail),
            'current_fields':{k:offer.get(k) for k in ('offerId','name','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions','mediaFiles','pictures')},
            'current_mapping':detail.get('mapping'),'current_parameters':detail.get('parameterValues'),
            'baseline_available_in_cleartext':False,
            'baseline_ref':'reports/content-pipeline/runs/45352254208-1/market/0/evidence.enc'}

def avito_read():
    rules=cmf.load()
    profile=av._request('GET','/autoload/v2/profile',retries=1)
    result={'autoload_enabled':profile.get('autoload_enabled'),'feed_names':[x.get('feed_name') for x in profile.get('feeds_data',[])],'uploads':{}}
    from scripts.apply_dq200_prices_20261120 import STABLE_AVITO_URL
    from content_completion import TOOLS_URL
    expected={}
    for name,url in (('LEOPARTS запчасти',STABLE_AVITO_URL),('LEOPARTS инструменты DSG',TOOLS_URL)):
        try:
            r=requests.get(url,timeout=40)
            r.raise_for_status()
            expected[name]=r.content
        except Exception as exc:result.setdefault('source_read_errors',{})[name]=type(exc).__name__
    for label in ('last_successful','current'):
        try:
            u=av._request('GET','/autoload/v4/uploads/'+label,retries=1)
            data={k:u.get(k) for k in ('upload_id','status','started_at','finished_at')}
            data['feeds']=[]
            for f in u.get('feed_urls') or []:
                name=f.get('name');url=f.get('url') or ''
                row={'name':name}
                data['feeds'].append(row)
                parsed=urlsplit(url)
                if parsed.scheme!='https' or parsed.hostname!='api.avito.ru' or not parsed.path.startswith('/autoload/v2/feed/content/'):
                    row['status']='unsupported provider URL';continue
                try:
                    # Only the returned Avito content endpoint; never publish
                    # or persist the private download URL or authorization.
                    r=requests.get(url,headers=av._headers(),timeout=35,allow_redirects=False)
                    row['http_status']=r.status_code
                    if not r.ok:continue
                    blob=r.content
                    row['sha256']=sha(blob)
                    row['size']=len(blob)
                    row['same_bytes_as_current_source']=name in expected and expected[name]==blob
                    if blob.startswith(b'PK'):
                        book=load_workbook(BytesIO(blob),read_only=True,data_only=True)
                        ws=book[af.SHEET_ADS] if af.SHEET_ADS in book.sheetnames else book.active
                        values={}
                        for v in ws.iter_rows(min_row=af.FIRST_DATA_ROW,values_only=True):
                            if len(v)>=af.COL_DESCRIPTION and v[af.COL_ID-1]:
                                sku=str(v[af.COL_ID-1])
                                desired=rules.get(sku,{}).get('text',{}).get('avito',{}).get('description')
                                if desired:
                                    want=aux.desired_avito(sku,desired,v[af.COL_PRICE-1])
                                    values[sku]=aux.normalized(v[af.COL_DESCRIPTION-1])==aux.normalized(want)
                        book.close()
                        row.update(format='xlsx',reviewed_descriptions=len(values),matching_descriptions=sum(values.values()),control_H=values.get('0am325025H'))
                    else:
                        try:
                            root=ET.fromstring(blob)
                            values={}
                            for ad in root.findall('.//Ad'):
                                sku=ad.findtext('Id')
                                desired=rules.get(sku,{}).get('text',{}).get('avito',{}).get('description')
                                if desired:values[sku]=aux.normalized(ad.findtext('Description'))==aux.normalized(desired)
                            row.update(format='xml',reviewed_descriptions=len(values),matching_descriptions=sum(values.values()),control_H=values.get('0am325025H'),tools={s:values.get(s) for s in ('tool-dq200-acc-restore','tool-dsg-pressure-hp')})
                        except ET.ParseError:row['format']='unrecognized'
                except Exception as exc:row['error_type']=type(exc).__name__
            result['uploads'][label]=data
        except Exception as exc:result['uploads'][label]={'error_type':type(exc).__name__}
    legacy=p.read_json(ROOT/'data/avito_legacy_catalog_candidates_20261124.json',{}).get('entries',[])
    ids=[str(x['avito_id']) for x in legacy]
    try:
        # Documented query parameter; no new ad IDs generated or written.
        mapping=av._request('GET','/autoload/v2/items/ad_ids',params={'query':','.join(ids)},retries=1)
        result['legacy_mapping_response']={k:v for k,v in mapping.items() if k in ('items','errors','result')}
    except Exception as exc:result['legacy_mapping_error']=type(exc).__name__
    result['legacy_owner_confirmed']=[{'avito_id':x['avito_id'],'skus':x.get('owner_confirmed_related_master_skus')} for x in legacy if x.get('owner_confirmed_related_master_skus')]
    return result

def tilda_read():
    from scripts.leoparts_direct_operation_nov21 import site_records
    records=site_records()
    uids=['484120372803','351198279983','602430815843']
    out={}
    for uid in uids:
        row=records.get(uid)
        if row:
            out[uid]={k:row.get(k) for k in ('uid','sku','title','externalid','text','descr','gallery','image','photo','url','price','editions')}
    # No inferred product identity. Keep current photo URLs and exact content
    # for resolving the known duplicate and conflicting title.
    return out

def main():
    out={'checked_utc':p.stamp(),'seller_writes':0}
    for key,fn in [('wb',wb_read),('market',market_read),('avito',avito_read),('tilda',tilda_read)]:
        try:out[key]=fn()
        except Exception as exc:out[key]={'error':type(exc).__name__+': '+str(exc)[:200]}
    path=ROOT/'reports/content-pipeline/targeted_final_readback.json'
    p.write_json(path,out)
    print('Final read-only diagnostics saved; seller writes: 0')
if __name__=='__main__':main()
