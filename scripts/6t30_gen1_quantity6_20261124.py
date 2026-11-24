"""Owner-approved exact quantity correction: 6t30 Gen1 ONLY, 7 -> 6.
No photo, price, inventory, category, model or unrelated SKU writes.
"""
import copy,csv,hashlib,io,json,os,re,sys,time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor,as_completed
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));os.chdir(ROOT)
import content_pipeline as p
import content_manifest as cm
import content_master_sync as ms
import ozon_description_guard as og
import ozon_client as oz
import wb_client as wb
import yandex_market_client as ym
import avito_client as av
import avito_feed as af
import requests
from market_content import read_rows,grouped
SKU='6t30';OZID='6287285958';WBID='2254117475';AVID='8425207775';UID='695009271403'
OUT=ROOT/'data/6t30_gen1_quantity6_result_20261124.json'
MAN=ROOT/'data/content_manifest.json';UPD=ROOT/'data/content_updates.json'
report={'sku':SKU,'owner_approved':True,'quantity':6,'channels':{},'source_files':{},'price_writes':0,'stock_writes':0,'photo_writes':0,'other_sku_writes':0,'started_utc':p.stamp()}
def req(ok,msg):
    if not ok:raise ValueError(msg)
def one(xs,where):
    req(len(xs)==1,'Nonunique '+where);return xs[0]
def sha(b):return hashlib.sha256(b).hexdigest()
def fix(s):
    if not isinstance(s,str):return s
    # Never touch part numbers, DSG7, model numbers, other quantities or URLs.
    s=re.sub(r'(?<![\w\d])7(?=\s*(?:шт(?:\.|\b)|штук\b|соленоид\w*|клапан\w*))','6',s,flags=re.I)
    s=re.sub(r'\bсемь(?=\s+(?:соленоид\w*|клапан\w*|штук\b))','шесть',s,flags=re.I)
    s=re.sub(r'\bсеми(?=\s+(?:соленоид\w*|клапан\w*))','шести',s,flags=re.I)
    return s
def recursively(v):
    if isinstance(v,str):return fix(v)
    if isinstance(v,list):return [recursively(x) for x in v]
    if isinstance(v,dict):return {k:recursively(x) for k,x in v.items()}
    return v
def save():p.write_json(OUT,report)
def source_changes():
    before=json.loads(MAN.read_text());after=copy.deepcopy(before)
    original=before['products'][SKU];row=after['products'][SKU]
    req(str(row['ids']['wb'])==WBID and str(row['ids']['avito'])==AVID and str(row['ids']['tilda'])==UID,'Identity changed')
    row['text']=recursively(row['text']);row['note']=fix(row.get('note',''))
    row['wb_characteristics']=recursively(row.get('wb_characteristics',[]))
    plan=row['market_parameters'];req(plan['category']==71380208,'Unexpected market category')
    for a in plan['set']:
        if a['parameterId']==14805583:
            req(str(a['value']) in ('7','6') and a.get('unitId')==6145205,'Unexpected market pack count')
            a['value']='6'
        elif a['parameterId']==7351754:a['value']=fix(a['value'])
    req(row['sources']==original['sources'] and row['second_slide']==original['second_slide'],'Unexpected media edit')
    verify=copy.deepcopy(after);verify['products'][SKU]=original;req(verify==before,'Non-target manifest edit')
    changes=[]
    if after!=before:
        MAN.write_text(json.dumps(after,ensure_ascii=False,indent=2)+'\n');changes.append(MAN)
    old=json.loads(UPD.read_text());new=copy.deepcopy(old)
    if SKU in new:
        new[SKU]=recursively(new[SKU])
        if old!=new:
            UPD.write_text(json.dumps(new,ensure_ascii=False,indent=2)+'\n');changes.append(UPD)
    # Preserve all worksheet members; only text cells of the exact existing row.
    from content_updates import load as update_load
    cm.load();update_load.cache_clear();update_load()
    for filename,(sheet,col,ch) in ms.BOOKS.items():
        path=ROOT/filename
        if not path.exists():continue
        try:
            raw=path.read_bytes()
            w=load_workbook(io.BytesIO(raw),read_only=True,data_only=False)
            ws=w[sheet] if sheet else w.active
            hits=[r for r in ws.iter_rows(min_row=2) if str(r[0].value or '').strip()==SKU]
            if not hits:report['source_files'][filename]={'status':'not_present'};w.close();continue
            req(len(hits)==1,'Duplicate exact workbook SKU')
            r=hits[0];edits={}
            # Exact approved textual columns only; stock and price numbers cannot match.
            for c in r:
                h=str(ws.cell(1,c.column).value or '').lower() if hasattr(c,'column') else ''
                if c.data_type=='f' or not isinstance(c.value,str):continue
                if not any(k in h for k in ('название','наименование','описание','комплектация','комплектность','характеристик','состав')):continue
                val=fix(c.value)
                if val!=c.value:edits[c.coordinate]=val
            # Match canonical description, as previous description pipeline does.
            desc=r[col-1]
            channel='ozon' if ch=='master' else ch
            value=row['text'][channel]['description']
            if desc.value!=value and desc.data_type!='f':edits[desc.coordinate]=value
            name=ws.title;w.close()
            if edits:
                path.write_bytes(ms.patch_cells(raw,name,edits));changes.append(path)
            report['source_files'][filename]={'status':'verified','changed_cells':list(edits)}
        except Exception as e:report['source_files'][filename]={'status':'blocked','error':str(e)[:260]}
    report['source_files']['manifest']={'status':'verified','all_other_products_unchanged':True,'old_quantity':7,'new_quantity':6}
    report['source_files']['content_updates']={'status':'verified','exact_sku_only':True}
    save();p.checkpoint([*changes,OUT],live=True)
    return row
def oz_snapshot():
    c=one([x for x in oz.get_product_names([SKU]) if x.get('offer_id')==SKU],'Ozon SKU')
    req(str(c.get('id') or c.get('product_id'))==OZID,'Ozon product ID mismatch')
    if not (c.get('model_info') or {}).get('model_id') or not (c.get('model_info') or {}).get('count'):
        info=one([x for x in oz.get_product_info_list([SKU]) if x.get('offer_id')==SKU],'Ozon exact info')
        p.supplement_ozon_model(c,info)
    price=one([x for x in oz.get_prices_and_stocks([SKU]) if x.get('offer_id')==SKU],'Ozon price')
    og.model_identity(c)
    return c,price
def get_attr(c,aid):
    return one([x for x in c.get('attributes',[]) if x.get('id')==aid and x.get('complex_id',0)==0],'attribute '+str(aid))
def aval(a):return [str(x.get('value')) for x in a.get('values',[])]
def ozon(rule):
    before,bp=oz_snapshot();req(before.get('type_id')==970984897,'Ozon type mismatch')
    # Previously verified attribute 8513 is pack quantity, 7202 is one sale unit.
    old=get_attr(before,8513);req(aval(old) in (['7'],['6']),'Unexpected Ozon package quantity')
    req(aval(get_attr(before,7202))==['1'],'Unexpected unit quantity')
    want={4191:rule['text']['ozon']['description'],4384:rule['text']['ozon']['composition'],8513:'6'}
    # If rich content contains an explicit wrong count, correct only textual
    # leaves of its existing JSON; preserve layout, URLs and all other values.
    rich=[x for x in before.get('attributes',[]) if x.get('id')==11254]
    if rich:
        a=one(rich,'Rich Content');v=one(a.get('values',[]),'Rich Content value').get('value','')
        if v and fix(v)!=v:
            obj=json.loads(v);fixed=recursively(obj)
            req(fixed!=obj,'Rich content correction unresolved')
            want[11254]=json.dumps(fixed,ensure_ascii=False,separators=(',',':'))
    # Title on the existing Ozon card lacks count; if unexpectedly incorrect,
    # report separately rather than using a whole-card import.
    title_requires=fix(before.get('name',''))!=before.get('name','')
    attrs=[]
    for aid,v in want.items():
        existing=[x for x in before.get('attributes',[]) if x.get('id')==aid and x.get('complex_id',0)==0]
        req(len(existing)<=1,'Ambiguous Ozon attribute')
        if existing and aval(existing[0])==[v]:continue
        if aid==8513: req(existing and existing[0]['values'][0].get('dictionary_value_id',0)==0,'Unexpected count dictionary')
        attrs.append({'id':aid,'complex_id':0,'values':[{'value':v,'dictionary_value_id':0}]})
    def proj(c,price):
        d=copy.deepcopy(c);d['attributes']=[a for a in d['attributes'] if a['id'] not in want]
        return og.projection(d,price)
    base=proj(before,bp)
    if attrs:
        p.current_main();reply=oz._post('/v1/product/attributes/update',{'items':[{'offer_id':SKU,'attributes':attrs}]},retries=1)
        req(reply and not reply.get('error'),'Ozon did not acknowledge')
    else:reply={}
    result={'status':'submitted_pending_readback','old_pack_count':aval(old)[0],'changed_attribute_ids':[x['id'] for x in attrs],'task_id':reply.get('task_id') or (reply.get('result') or {}).get('task_id')}
    for _ in range(8 if attrs else 1):
        if attrs:time.sleep(5)
        after,ap=oz_snapshot()
        diffs=og.differences(base,proj(after,ap))
        if diffs:return {**result,'status':'protected_drift','protected_differences':diffs}
        values={aid:aval(get_attr(after,aid)) for aid in want}
        result.update(quantity=values[8513][0],unit_quantity=aval(get_attr(after,7202))[0],description_verified=values[4191]==[want[4191]],composition_verified=values[4384]==[want[4384]],protected_unchanged=True,title_requires_review=title_requires)
        matches=all(values[a]==[v] for a,v in want.items() if a!=11254)
        if 11254 in want:
            matches=matches and json.loads(values[11254][0])==json.loads(want[11254])
        if matches and not title_requires:result['status']='verified';return result
    return result
def wildberries(rule):
    b=p.read_wb_exact(SKU,WBID);req(b.get('subjectID')==7807,'Unexpected WB subject')
    plan={a['id']:a['value'] for a in rule.get('wb_characteristics',[])}
    chars=copy.deepcopy(b.get('characteristics',[]))
    defs={d.get('charcID'):str(d.get('name') or '') for d in wb.get_subject_characteristics(b['subjectID'])}
    changed=[]
    for a in chars:
        aid=a.get('id');old=a.get('value');v=recursively(old)
        if aid in plan:v=plan[aid]
        name=defs.get(aid,'').lower()
        if re.search(r'количество.*(?:упаковк|комплект)|комплект.*количество',name):
            def count(x):
                if type(x) is int and x==7:return 6
                if type(x) is float and x==7:return 6.0
                if x=='7':return '6'
                if isinstance(x,list):return [count(y) for y in x]
                return x
            v=count(v)
        if v!=old:a['value']=v;changed.append({'id':aid,'name':name,'value':v})
    req(all(any(x['id']==aid for x in chars) for aid in plan),'Missing WB composition')
    fields=rule['text']['wb'];title=fix(b.get('title',''))
    req(len(title)<=60,'WB title exceeds limit')
    payload={k:copy.deepcopy(b[k]) for k in ['nmID','vendorCode','brand','title','description','dimensions','characteristics','sizes']}
    payload.update(title=title,description=fields['description'],characteristics=chars,kizMarked=b.get('kizMarked',False))
    payload['sizes']=[{k:v for k,v in x.items() if k in {'chrtID','techSize','wbSize','skus'}} for x in payload['sizes']]
    change=b['title']!=title or b['description']!=fields['description'] or bool(changed)
    if change:
        p.current_main();reply=wb._request('POST',wb.config.wb_content_base,'/content/v2/cards/update',[payload],retries=1)
        req(isinstance(reply,dict) and not reply.get('error'),'WB did not acknowledge')
    result={'status':'submitted_pending_readback','changed_characteristics':changed,'title':title}
    for _ in range(8 if change else 1):
        if change:time.sleep(5)
        a=p.read_wb_exact(SKU,WBID)
        bad=[k for k in ('nmID','vendorCode','brand','subjectID','dimensions','sizes','photos','video','kizMarked') if b.get(k)!=a.get(k)]
        if bad:return {**result,'status':'protected_drift','protected_differences':bad}
        result.update(description_verified=a.get('description')==fields['description'],title_verified=a.get('title')==title,characteristics_verified=sorted(chars,key=lambda x:x['id'])==sorted(a.get('characteristics',[]),key=lambda x:x['id']),protected_unchanged=True)
        if all(result[k] for k in ('description_verified','title_verified','characteristics_verified')):
            result['status']='verified';return result
    return result
def market(rule):
    biz=ym.get_business_id()
    def read():
        b=one([x['offer'] for x in read_rows(biz,'offer-mappings',[SKU]) if x['offer'].get('offerId')==SKU],'Market SKU')
        c=one([x for x in read_rows(biz,'offer-cards',[SKU]) if x.get('offerId')==SKU],'Market card')
        req(c['mapping']['marketCategoryId']==71380208,'Market category mismatch')
        return b,c
    b,c=read();params=copy.deepcopy(c.get('parameterValues',[]))
    cnt=one([x for x in params if x['parameterId']==14805583],'Market packaging count')
    req(str(cnt.get('value')) in ('7','6') and cnt.get('unitId')==6145205 and not cnt.get('valueId'),'Unexpected Market count')
    cnt['value']='6'
    for a in params:
        if a['parameterId']!=14805583 and isinstance(a.get('value'),str):
            new=fix(a['value'])
            if new!=a['value']:req(not a.get('valueId'),'Market dictionary text cannot be changed');a['value']=new
    text={'title':fix(b['name']),'description':rule['text']['market']['description']}
    payload=cm.market_offer_payload(b,c,text=text);payload['offer']['parameterValues']=params;payload['offer']['marketCategoryId']=71380208
    change=b.get('name')!=text['title'] or b.get('description')!=text['description'] or grouped(params)!=grouped(c.get('parameterValues',[]))
    if change:
        p.current_main();code,reply=ym._request('POST',f'/v2/businesses/{biz}/offer-mappings/update',json_body={'offerMappings':[payload]},retries=1)
        req(code==200 and reply.get('status')=='OK' and not any(x.get('errors') for x in reply.get('results',[])),'Market did not acknowledge')
    result={'status':'submitted_pending_readback','title':text['title']}
    for _ in range(8 if change else 1):
        if change:time.sleep(5)
        a,z=read()
        bad=[k for k in ('offerId','basicPrice','purchasePrice','vendor','vendorCode','barcodes','params','videos','weightDimensions','pictures','mediaFiles') if b.get(k)!=a.get(k)]
        if c.get('mapping')!=z.get('mapping'):bad.append('mapping')
        if bad:return {**result,'status':'protected_drift','protected_differences':bad}
        result.update(description_verified=a.get('description')==text['description'],title_verified=a.get('name')==text['title'],characteristics_verified=grouped(params)==grouped(z.get('parameterValues',[])),protected_unchanged=True,quantity=next((x.get('value') for x in z.get('parameterValues',[]) if x['parameterId']==14805583),None))
        if all(result[k] for k in ('description_verified','title_verified','characteristics_verified')):result['status']='verified';return result
    return result
def avito(rule):
    repo='antongrigorenko844-prog/marketplace-agent';stable=f'https://github.com/{repo}/releases/download/product-photos/avito_feed.xlsx'
    live=one([x for x in av.list_all_items(statuses=['active']) if str(x.get('id'))==AVID and x.get('status')=='active'],'active Avito listing')
    def patch(raw):
        w=load_workbook(io.BytesIO(raw),read_only=True,data_only=False);ws=w[af.SHEET_ADS]
        hits=[r for r in ws.iter_rows(min_row=af.FIRST_DATA_ROW) if str(r[af.COL_ID-1].value or '')==SKU]
        r=one(hits,'Avito exact feed row');req(str(r[af.COL_AVITO_ID-1].value or '') in ('',AVID),'Avito feed ID conflict')
        edits={}
        for col in (af.COL_TITLE,af.COL_DESCRIPTION):
            c=r[col-1];v=fix(c.value)
            req(isinstance(v,str) and v,'Missing Avito title/description')
            if v!=c.value:edits[c.coordinate]=v
        # There is no generic count column in this template; only use explicitly named package/composition fields.
        for c in r:
            if not hasattr(c,'column'):continue
            h=' '.join(str(ws.cell(i,c.column).value or '') for i in range(1,af.FIRST_DATA_ROW)).lower()
            if not re.search(r'количество.*(?:упаковк|комплект)|комплектация|комплектность',h):continue
            v=fix(c.value)
            if c.value=='7':v='6'
            # Never modify stock or unrelated numeric fields; numeric cells need a reviewed numeric schema.
            if isinstance(v,str) and v!=c.value:edits[c.coordinate]=v
        title=fix(r[af.COL_TITLE-1].value);desc=fix(r[af.COL_DESCRIPTION-1].value);name=ws.title;w.close()
        return ms.patch_cells(raw,name,edits),title,desc,list(edits)
    p.current_main();r=requests.get(stable,params={'audit':time.time_ns()},timeout=45);r.raise_for_status()
    raw=r.content;new,title,desc,cells=patch(raw)
    local=ROOT/'data/avito_feed.xlsx';local_before=local.read_bytes();local_new,_,_,local_cells=patch(local_before)
    result={'status':'stable_feed_already_correct','avito_id':AVID,'title':title,'changed_cells':cells,'other_cells_unchanged':True,'public_verified':False}
    if raw!=new:
        h={'Authorization':'Bearer '+os.environ['GH_TOKEN'],'Accept':'application/vnd.github+json','X-GitHub-Api-Version':'2022-11-28'}
        api=f'https://api.github.com/repos/{repo}'
        rel=requests.get(api+'/releases/tags/product-photos',headers=h,timeout=35);rel.raise_for_status();rel=rel.json()
        asset=one([x for x in rel['assets'] if x['name']=='avito_feed.xlsx'],'stable feed asset')
        backup=f'avito_feed.backup_gen1qty6_{asset["id"]}.xlsx'
        stage=f'avito_feed.stage_gen1qty6_{sha(new)[:10]}.xlsx'
        check=requests.get(stable,params={'audit':time.time_ns()},timeout=45);check.raise_for_status();req(check.content==raw,'Concurrent stable feed change')
        p.current_main()
        upload=requests.post(rel['upload_url'].split('{')[0],headers={**h,'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'},params={'name':stage},data=new,timeout=90);upload.raise_for_status();staged=upload.json()
        def rename(id,name):
            a=requests.patch(api+'/releases/assets/'+str(id),headers=h,json={'name':name},timeout=40);a.raise_for_status()
        old_renamed=False;new_renamed=False
        try:
            rename(asset['id'],backup);old_renamed=True;rename(staged['id'],'avito_feed.xlsx');new_renamed=True
            for delay in (0,3,6,10):
                if delay:time.sleep(delay)
                z=requests.get(stable,params={'audit':time.time_ns()},timeout=45)
                if z.ok and z.content==new:break
            else:raise RuntimeError('Stable feed readback failed')
        except Exception:
            if new_renamed:rename(staged['id'],stage+'.failed')
            if old_renamed:rename(asset['id'],'avito_feed.xlsx')
            raise
        result.update(status='stable_feed_verified_not_listing',old_feed_sha=sha(raw),new_feed_sha=sha(new),backup=backup)
    req(local.read_bytes()==local_before,'Local feed changed concurrently')
    if local_new!=local_before:local.write_bytes(local_new)
    result.update(local_cells=local_cells,repository_copy_correct=True)
    # The autoload receipt may be delayed; no claim of live text unless available.
    try:
        a=one([x for x in av.list_all_items(statuses=['active']) if str(x.get('id'))==AVID],'Avito readback')
        result['live_title']=a.get('title')
        result['live_title_verified']=isinstance(a.get('title'),str) and fix(a['title'])==a['title'] and ('6 шт' in a['title'])
        result['live_description_verified']=isinstance(a.get('description'),str) and fix(a['description'])==a['description'] and '6 соленоидов' in a['description']
        if result['live_title_verified'] and result['live_description_verified']:result['status']='verified';result['public_verified']=True
    except Exception:pass
    return result
def tilda(rule):
    from scripts.leoparts_direct_operation_nov21 import site_records
    from scripts.tilda_price_pipeline_v1 import index_by_uid
    import tilda_feed as tf
    rows=index_by_uid(site_records());r=rows[UID]
    req(r.get('sku')==SKU and r.get('externalid')==SKU,'Tilda exact identity mismatch')
    title=fix(r.get('title',''));oldtext=r.get('text') or '';oldshort=r.get('descr') or ''
    # Keep real live title and text other than the wrong count. If live text
    # has no quantity, use approved description so the six-piece set is clear.
    text=fix(oldtext)
    if not re.search(r'(?<!\w)6\s*(?:соленоид|шт|клапан)',text,re.I):text=rule['text']['tilda']['description']
    short=fix(oldshort)
    if not short or not re.search(r'(?<!\w)6\s*(?:соленоид|шт|клапан)',short,re.I):short=tf._short_description(tf._clean_description(text))
    path=ROOT/'data/6t30_gen1_quantity6_tilda_update_20261124.csv'
    fields=['Tilda UID','External ID','SKU','Title','Description','Text']
    with path.open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields,delimiter=';');w.writeheader();w.writerow(dict(zip(fields,[UID,SKU,SKU,title,short,text])))
    verified=title==r.get('title') and text==oldtext and short==oldshort
    return {'status':'verified' if verified else 'editor_import_required','uid':UID,'title':title,'live_title':r.get('title'),'live_wrong_count':any(fix(v)!=v for v in (r.get('title',''),oldshort,oldtext)),'patch':str(path.relative_to(ROOT)),'no_price_stock_or_photo_fields':True,'reason':'Existing CommerceML test did not change full public text; do not repeat or use undocumented endpoint.'}
def main():
    req(os.getenv('GITHUB_REF')=='refs/heads/main','main only')
    req(not OUT.exists(),'Previous attempt exists: independent readback before retry')
    p.current_main()
    rule=source_changes()
    tasks={'ozon':ozon,'wb':wildberries,'market':market,'avito':avito,'tilda':tilda}
    with ThreadPoolExecutor(max_workers=5) as pool:
        fs={pool.submit(f,rule):k for k,f in tasks.items()}
        for fut in as_completed(fs):
            k=fs[fut]
            try:report['channels'][k]=fut.result()
            except Exception as e:report['channels'][k]={'status':'blocked_or_uncertain','error':str(e)[:450]}
            save();print(k,report['channels'][k]['status'],flush=True)
    report['finished_utc']=p.stamp();save()
    paths=[OUT,ROOT/'data/avito_feed.xlsx',ROOT/'data/6t30_gen1_quantity6_tilda_update_20261124.csv']
    p.checkpoint(paths,live=True)
if __name__=='__main__':main()
