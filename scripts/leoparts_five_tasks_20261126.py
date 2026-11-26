"""Owner-requested five-task continuation: fresh diagnostics plus a narrow
original-media append where already visually reviewed, exact-SKU evidence exists.
No stock/price/order/description/group mutations. Never fabricate product facts.
"""
from __future__ import annotations
import collections,copy,datetime,hashlib,io,json,os,sqlite3,sys,time
from pathlib import Path
from urllib.parse import urlsplit
import requests
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path[:0]=[str(ROOT),str(ROOT/'.github/scripts')]
import content_pipeline as p
import content_manifest as cm
import ozon_client as oz
import wb_client as wb
import avito_client as av
import avito_feed as af
import yandex_market_client as ym
from config import config
from stock_sync import _item_quantities,_OZON_CANCELLED_STATUSES
from scripts import avito_dq200_watermarks_20261126 as wm
from scripts import avito_dq200_submit_20261126 as base
from scripts import dq200_hx_d_append_gallery_20261126 as media
from market_content import read_rows,grouped
from scripts.leoparts_direct_operation_nov21 import site_records
OUT=ROOT/'data/leoparts_five_tasks_20261126_result.json'
MAN=ROOT/'data/content_manifest.json'
J={'started_utc':p.stamp(),'stock_writes':0,'price_writes':0,'description_writes':0,'ozon_group_writes':0,'media_write_requests':0,'tasks':{}}
def need(ok,msg):
    if not ok:raise ValueError(msg)
def save(*paths):
    J['updated_utc']=p.stamp();p.write_json(OUT,J)
    p.checkpoint([OUT,*paths],live=True)
    print(json.dumps({k:v.get('status') for k,v in J['tasks'].items()},ensure_ascii=False),flush=True)
def task(name,fn):
    try:J['tasks'][name]=fn()
    except Exception as e:J['tasks'][name]={'status':'blocked_or_unverified','error':type(e).__name__+': '+str(e)[:300]}
    save()
def read_stock():
    now=datetime.datetime.now(datetime.timezone.utc)
    since=(now-datetime.timedelta(days=30)).isoformat(timespec='milliseconds').replace('+00:00','Z')
    end=now.isoformat(timespec='milliseconds').replace('+00:00','Z')
    results={};conflicts=[]
    with sqlite3.connect(Path(config.db_path).resolve().as_uri()+'?mode=ro',uri=True) as con:
        con.row_factory=sqlite3.Row
        pending=con.execute('SELECT COUNT(*) FROM stock_pending').fetchone()[0]
        for source,read in [('ozon_fbs',oz.get_fbs_orders_since),('ozon_fbo',oz.get_fbo_orders_since)]:
            try:
                postings=read(since,end)
                count=0;seen=set()
                for posting in postings:
                    number=str(posting.get('posting_number') or '').strip()
                    status=str(posting.get('status') or '').strip().lower()
                    need(number and status,'Unknown order identity/status')
                    cancel=status in _OZON_CANCELLED_STATUSES
                    for sku,qty in _item_quantities(posting.get('products') or [],'offer_id','quantity').items():
                        key=number+':'+sku
                        need(key not in seen,'Duplicate current posting line');seen.add(key);count+=1
                        old=con.execute("""SELECT p.offer_id,p.decremented,p.status,r.components,
                            h.order_key AS deferred
                            FROM processed_orders p
                            LEFT JOIN stock_order_recipe r USING(source,order_key)
                            LEFT JOIN stock_deferred_release h USING(source,order_key)
                            WHERE p.source=? AND p.order_key=?""",(source,key)).fetchone()
                        if not old:continue
                        target=0 if cancel else qty
                        if old['decremented']>0 and old['components'] is None and old['decremented']!=target:
                            conflicts.append({'source':source,'sku':sku,'order_ref_hash':hashlib.sha256((source+'|'+key).encode()).hexdigest()[:14],
                                'old_units':old['decremented'],'requested_units':target,'incoming_qty':qty,
                                'incoming_status':status,'ledger_status':old['status'],'sku_matches':old['offer_id']==sku,
                                'existing_hold':bool(old['deferred']),'original_recipe_missing':True})
                results[source]={'postings':len(postings),'lines':count}
            except Exception as e:results[source]={'error':type(e).__name__+': '+str(e)[:180]}
        return {'status':'conflicts_found' if conflicts else 'no_missing_recipe_changes_observed','pending_projection':pending,
            'coverage':results,'conflicts':conflicts,'stock_mutations':0}
def ozon_groups():
    rows=oz.list_products(limit=100)
    ids=[str(x.get('offer_id') or '') for x in rows]
    need(ids and all(ids) and len(ids)==len(set(ids)),'Incomplete or ambiguous Ozon catalogue')
    info=oz.get_product_info_list(ids)
    by={str(x.get('offer_id') or ''):x for x in info}
    need(len(info)==len(by) and set(by)==set(ids),'Ozon model readback incomplete')
    groups=collections.defaultdict(list);unknown=[]
    for sku,x in by.items():
        m=x.get('model_info') or {};mid=str(m.get('model_id') or '')
        if not mid or mid=='0':unknown.append(sku);continue
        groups[mid].append({'sku':sku,'name':x.get('name'),'product_id':x.get('id') or x.get('product_id'),
            'model_count':m.get('count'),'status':(x.get('statuses') or {}).get('status_name')})
    multi={k:v for k,v in groups.items() if len(v)>1 or any((x['model_count'] or 0)>1 for x in v)}
    old=json.loads((ROOT/'data/ozon_model_group_repair_20261125.json').read_text())
    canary=by.get('0am325025hfe',{});m=canary.get('model_info') or {}
    previous=old['operations'][0]['readback']
    oldgroups=json.loads((ROOT/'data/ozon_model_groups_audit_20261125.json').read_text())['model_groups']
    return {'status':'live_model_census_complete','products':len(ids),'groups':multi,'multi_groups':len(multi),
        'members':sum(len(v) for v in multi.values()),'unknown':unknown,
        'canary':{'sku':'0am325025hfe','previous_model_id':previous['model_id'],'current_model_id':m.get('model_id'),
                  'current_model_count':m.get('count'),'split_verified':m.get('count')==1,
                  'status':(canary.get('statuses') or {}).get('status_name')},
        'previous_group_count':len(oldgroups),'mutation_requests':0,
        'note':'A saved label is not proof of an actual model split; do not repeat or bulk-detach unconfirmed models.'}
def excel_ads(raw):
    b=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
    try:
        s=b[af.SHEET_ADS];rows={}
        for v in s.iter_rows(min_row=af.FIRST_DATA_ROW,values_only=True):
            if not v[af.COL_ID-1]:continue
            k=str(v[af.COL_ID-1]);need(k not in rows,'Duplicate feed id')
            rows[k]={'description':v[af.COL_DESCRIPTION-1],'photos':str(v[af.COL_IMAGE_URLS-1] or '').splitlines(),
                'video':v[af.COL_VIDEO_FILE_URL-1],'price':v[af.COL_PRICE-1]}
        return rows
    finally:b.close()
def avito_check():
    journal=json.loads(wm.OUT.read_text());desired=journal['desired']
    rel=wm.release()
    expected={};published={}
    for name,(fid,aid,price) in wm.TARGETS.items():
        _,raw=wm.stable(rel,name);rows=excel_ads(raw)
        need(fid in rows and int(rows[fid]['price'])==price,'Target identity or price changed')
        published.update(rows)
        expected[name]={'id':aid,'feed_id':fid,'published_gallery_matches':rows[fid]['photos']==desired[name],
            'photos':len(rows[fid]['photos']),'video_present':bool(rows[fid]['video']),'price':rows[fid]['price']}
    active=av.list_all_items(statuses=['active'])
    act={str(x.get('id')):x for x in active}
    # Avito may include duplicate report rows. Match content of the actual
    # downloaded received feed, never treat the report's success alone as proof.
    reports={}
    for kind in ('current','last_successful'):
        try:
            report=base.api('GET','/autoload/v4/uploads/'+kind)
            rec={'upload_id':report.get('upload_id'),'status':report.get('status'),'files':0,'received_rows':0,
                'exact_description_count':0,'exact_gallery_count':0,'different_descriptions':[],
                'different_galleries':[],'two_listings':{}}
            got={}
            for f in report.get('feed_urls',[]):
                u=f.get('url','');pu=urlsplit(u)
                if pu.scheme!='https' or pu.hostname!='api.avito.ru' or not pu.path.startswith('/autoload/v2/feed/content/'):continue
                rr=requests.get(u,headers={'Authorization':av._headers()['Authorization']},timeout=30,allow_redirects=False)
                if not rr.ok:continue
                try:d=excel_ads(rr.content)
                except Exception:continue
                for k,v in d.items():
                    if k in got:need(got[k]==v,'Conflicting duplicate received feed rows')
                    got[k]=v
                rec['files']+=1
            for k in published.keys() & got.keys():
                rec['received_rows']+=1
                if published[k]['description']==got[k]['description']:rec['exact_description_count']+=1
                else:rec['different_descriptions'].append(k)
                if published[k]['photos']==got[k]['photos']:rec['exact_gallery_count']+=1
                else:rec['different_galleries'].append(k)
            for name,x in expected.items():
                f=x['feed_id'];q=got.get(f)
                rec['two_listings'][name]={'received_gallery_matches':bool(q and q['photos']==desired[name]),
                    'received_description_matches':bool(q and q['description']==published[f]['description'])}
            reports[kind]=rec
        except Exception as e:reports[kind]={'error':type(e).__name__+': '+str(e)[:200]}
    account=str(base.api('GET','/core/v1/accounts/self').get('id') or '')
    for name,x in expected.items():
        aid=x['id'];fid=x['feed_id'];x['active']=str(aid) in act
        try:
            d=base.api('GET',f'/core/v1/accounts/{account}/items/{aid}/') if account.isdigit() else {}
            x['public_description_available']=isinstance(d.get('description'),str)
            x['public_description_matches']=x['public_description_available'] and d['description']==published[fid]['description']
            x['public_gallery_exact']=wm.live_gallery(name,desired[name])
        except Exception as e:x['public_error']=type(e).__name__
    return {'status':'fresh_readback','active_listings':len(active),'published_feed_rows':len(published),
            'listings':expected,'reports':reports,'seller_writes':0}
def solenoids():
    sku='solenoidy-dq200-8sht';draft=json.loads((ROOT/'drafts/solenoidy_dq200_8sht_intake_20261126.json').read_text())
    out={}
    for ch,fn,key in [('ozon',lambda:oz.list_products(limit=100),'offer_id'),
        ('wb',lambda:wb.list_cards(limit=100),'vendorCode'),
        ('market',lambda:ym.get_all_offers(archived=False)+ym.get_all_offers(archived=True),'sku'),
        ('tilda',lambda:list(site_records().values()),'sku')]:
        try:
            found=[x for x in fn() if str(x.get(key) or '')==sku]
            need(len(found)<=1,'Duplicate exact SKU')
            x=found[0] if found else {}
            out[ch]={'exists':bool(found),'id':x.get('nmID') or x.get('uid') or x.get('product_id') or x.get('market_sku'),
                'price':x.get('price'),'quantity':x.get('quantity')}
        except Exception as e:out[ch]={'error':type(e).__name__+': '+str(e)[:200]}
    return {'status':'owner_facts_required','sku':sku,'confirmed_qty_per_kit':8,'prices':draft['prices'],
        'missing_facts':[k for k in ('stock_confirmed','brand','condition','dimensions','tnved') if draft.get(k) is None],
        'manufacturer_part_number_and_marking_unconfirmed':True,'current_cards':out,'seller_writes':0}
def reviewed_media():
    # Only these previously visually accepted original photos. Confirm the same
    # exact SKU in the own current WB gallery and prior Market inventory; never
    # reintroduce an image absent from the source or historically removed.
    approved=json.loads((ROOT/'data/wb_media_additions_20261115.json').read_text())
    inv=json.loads((ROOT/'data/market_recovery_media/inventory.json').read_text())
    wb_cards={x['vendorCode']:x for x in wb.list_cards(limit=100)}
    out={'status':'reviewed','products':{},'video_candidates':0,'note':'No new generated media, and no videos without exact product source.'}
    J['tasks']['media']=out
    video=json.loads((ROOT/'data/market_recovery_media/video_inventory.json').read_text())
    out['video_candidates']=len(video.get('candidates',[]))
    def exact_market(sku):
        b=194752944
        m=read_rows(b,'offer-mappings',[sku]);c=read_rows(b,'offer-cards',[sku])
        mm=[x for x in m if (x.get('offer') or {}).get('offerId')==sku]
        cc=[x for x in c if x.get('offerId')==sku]
        need(len(mm)==len(cc)==1,'Exact Market identity not unique')
        return mm[0],cc[0]
    def protect(m,c):
        o=copy.deepcopy(m['offer'])
        for k in ('pictures','mediaFiles'):o.pop(k,None)
        return {'offer':o,'mapping':m.get('mapping'),'attrs':grouped(c.get('parameterValues',[])),
                'card_mapping':c.get('mapping'),'ugc':c.get('ugc')}
    for sku in ('filtr-setka-0b5-orig','salnikvhod'):
        rec={};out['products'][sku]=rec
        try:
            need(int(ym.get_business_id())==194752944,'Wrong Market business')
            w=wb_cards.get(sku);need(w is not None,'Exact own WB source absent')
            from wb_identity_policy import is_held
            need(not is_held(sku,w.get('nmID')),'Held WB identity cannot be used')
            sources=[x['big'] for x in w.get('photos',[]) if x.get('big')]
            need(sources,'No WB source photos')
            m,c=exact_market(sku);o=m['offer'];old=list(o.get('pictures') or [])
            need(old and len(set(map(cm.normalize_url,old)))==len(old),'Missing or duplicate Market gallery')
            refs=[media.read_image(u) for u in old]
            wb_refs=[media.read_image(u) for u in sources]
            candidates=[x for x in inv['candidates'] if x['sku']==sku and not x.get('historical_removed_urls')]
            cand_refs=[]
            for x in candidates:
                raw=base.get(x['url'])
                # WB CDN slots are mutable. Require exact previous reviewed bytes.
                if base.sha(raw)==x['sha256']:cand_refs.append(media.image(raw))
            append=[]
            for x in approved[sku]:
                u=x['url'];path=ROOT/x['repo_source']
                need(path.is_file() and path.parent.name==sku,'Exact original path missing')
                raw=path.read_bytes();need(base.sha(raw)==base.sha(base.get(u)),'Hosted original differs')
                im=media.image(raw)
                if not any(media.same(im,a) for a in wb_refs):continue
                if not any(media.same(im,a) for a in cand_refs):continue
                if any(media.same(im,a) for a in refs):continue
                append.append(u);refs.append(im)
            rec.update(photos_before=len(old),appended=append,market_sku=(m.get('mapping') or {}).get('marketSku'))
            if not append:rec['status']='no_verified_missing_photos';continue
            desired=old+append
            need(len(desired)<=20 and len(set(map(cm.normalize_url,desired)))==len(desired),'Gallery limit or duplicate')
            previous=protect(m,c)
            # Keep every unrelated field from the current live card; never
            # reassign the model/category or update prices/stock/description.
            payload=cm.market_offer_payload(o,c,pictures=desired)
            rec.update(status='intent',desired_gallery=desired,prior_gallery=old)
            J['media_write_requests']+=1;save()
            p.current_main()
            status,answer=ym._request('POST',f'/v2/businesses/194752944/offer-mappings/update',json_body={'offerMappings':[payload]})
            need(status==200 and answer.get('status')=='OK','Market media update rejected')
            rec['status']='submitted';save()
            for wait in (0,4,8,12):
                if wait:time.sleep(wait)
                am,ac=exact_market(sku)
                if am['offer'].get('pictures')==desired:break
            rec.update(photos_after=len(am['offer'].get('pictures') or []),
                gallery_exact=am['offer'].get('pictures')==desired,protected_unchanged=protect(am,ac)==previous)
            if rec['gallery_exact'] and rec['protected_unchanged']:
                manifest=json.loads(MAN.read_text())
                prior=copy.deepcopy(manifest)
                manifest['products'].setdefault(sku,{}).setdefault('sources',{})['market']=desired
                prior['products'].setdefault(sku,{}).setdefault('sources',{})['market']=desired
                need(prior==manifest,'Unrelated manifest drift')
                p.current_main();p.write_json(MAN,manifest);cm.load()
                rec['status']='verified';save(MAN)
            else:
                rec['status']='submitted_pending_or_protected_diff';save()
                break
        except Exception as e:
            rec['status']='blocked_or_unverified';rec['error']=type(e).__name__+': '+str(e)[:240]
            if rec.get('desired_gallery'):save();break
    out['status']='completed_with_verification' if all(x['status'] in ('verified','no_verified_missing_photos') for x in out['products'].values()) else 'partial_or_pending'
    return out
def main():
    need(os.environ.get('GITHUB_REF')=='refs/heads/main','Main only')
    p.current_main()
    for name,fn in [('stock',read_stock),('ozon_groups',ozon_groups),('solenoids',solenoids),('avito',avito_check),('media',reviewed_media)]:
        task(name,fn)
    J['finished_utc']=p.stamp();save()
if __name__=='__main__':main()
