"""One new owner-approved eight-piece DQ200 solenoid kit, five channels.
Only verified attributes. Unknown stock means no saleable inventory is sent.
Create new WB/Tilda card when supported without unknown physical facts;
build exact drafts for channels with missing mandatory fields. Never modify
the existing 4-piece SKU or its blocked Avito advertisement.
"""
from __future__ import annotations
import copy, io, json, os, re, sys, time
from pathlib import Path
from openpyxl import load_workbook
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path[:0]=[str(ROOT),str(ROOT/'.github/scripts')]
import content_pipeline as p
import content_manifest as cm
from scripts import dq200_cover_exact_20261126 as media
SKU='solenoidy-dq200-8sht';REF='0AM325473'
REQ=ROOT/'drafts/solenoidy_dq200_8sht_intake_20261126.json'
OUT=ROOT/'data/solenoidy_dq200_8sht_result_20261126.json'
DRAFT=ROOT/'drafts/solenoidy_dq200_8sht_channel_payloads_20261126.json'
MAN=ROOT/'data/content_manifest.json';CACHE=ROOT/'data/photo_upload_cache.json'
J={};D={};R={};URL=''
def require(ok,msg):
    if not ok:raise ValueError(msg)
def save(*paths):
    J['updated_utc']=p.stamp();p.write_json(OUT,J);p.write_json(DRAFT,D)
    p.checkpoint([OUT,DRAFT,*paths],live=True)
def state(ch,status,**kw):
    J['channels'][ch]=dict(J['channels'].get(ch,{}),status=status,**kw)
    print(ch,status,json.dumps(kw,ensure_ascii=False,default=str)[:1200],flush=True);save()
def guard(ch,fn):
    try:fn()
    except Exception as e:state(ch,'blocked_or_unverified',error=type(e).__name__+': '+str(e)[:320])
def exact(rows,key,sku=SKU):
    out=[x for x in rows if str(x.get(key) or '').casefold()==sku.casefold()]
    require(len(out)<=1,'Duplicate exact identity '+sku);return out[0] if out else None
def possible_full(title):
    t=str(title or '').lower()
    return ('соленоид' in t and any(x in t for x in ('dq200','dsg7','dsg 7','дсг7','дсг 7','0am','0cw')) and
            bool(re.search(r'\b8\s*(?:шт|штук|соленоид)|(?:полный|полная)\s+комплект',t)))
def duplicates(rows,key,name):
    return [{key:x.get(key),name:x.get(name)} for x in rows if str(x.get(key) or '').casefold()!=SKU.casefold() and possible_full(x.get(name))]
def init_source():
    global URL
    a=R['media'];path=ROOT/a['path'];raw=media.exact_bytes(path,a['sha256'],a['dimensions'])
    require(len(raw)==a['bytes'],'Original byte count mismatch')
    proof=media.verified_host(path,a['sha256'],a['dimensions'],SKU+'-1-'+a['sha256'][:12]+'.jpeg')
    URL=proof['url'];J['source']=proof
    # Persist only this new SKU's canonical copy and exact source media.
    data=json.loads(MAN.read_text());products=data['products']
    target={'text':{ch:{'description':R['description']} for ch in ('ozon','wb','market','avito','tilda')},'sources':{ch:[URL] for ch in ('ozon','wb','market','avito','tilda')}}
    target['text']['wb']['title']=R['title'];target['text']['avito']['title']=R['title']
    if SKU in products:require(products[SKU]==target,'New SKU canonical content already differs')
    else:products[SKU]=target;p.write_json(MAN,data);cm.load()
    # Register the expressly approved fixed direct-channel prices, without
    # inserting a stock row or a fictitious cost into master/warehouse data.
    pp=ROOT/'data/avito_site_price_overrides.xlsx';book=load_workbook(pp);ws=book.worksheets[0]
    found=[r for r in ws.iter_rows(min_row=2) if str(r[0].value or '').casefold()==SKU.casefold()]
    require(len(found)<=1,'Duplicate fixed price row')
    if found:
        require(float(found[0][1].value)==14800 and str(found[0][2].value or '').strip().lower()=='да','Existing fixed Avito price differs')
    else:
        # Existing pipeline columns: SKU, Avito price, fixed, site price.
        ws.append([SKU,14800,'да',14800]);book.save(pp)
    book.close()
    sp=ROOT/'data/site_price_only_overrides.json';v=json.loads(sp.read_text());require(SKU not in v or v[SKU]==14800,'Existing site price differs');v[SKU]=14800;p.write_json(sp,v)
    save(MAN,CACHE,pp,sp)
def wb_read():
    import wb_client as wb
    return exact(wb.list_cards(limit=100),'vendorCode')
def wb_start():
    import wb_client as wb
    cards=wb.list_cards(limit=100);require(len(cards)>100,'WB catalog paging unexpectedly short')
    found=exact(cards,'vendorCode');candidates=duplicates(cards,'vendorCode','title')
    if found:state('wildberries','existing_new_sku_readback',nmID=found['nmID']);return
    if candidates:state('wildberries','blocked_possible_duplicate',matches=candidates);return
    sample=p.read_wb_exact(REF,'670869819');sid=int(sample['subjectID']);require(sid>0,'WB reference subject missing')
    definitions=wb.get_subject_characteristics(sid);ids={int(x.get('charcID') or x.get('id') or 0):x for x in definitions}
    attrs=[]
    # Only values known to apply to the eight-piece set; do NOT inherit
    # Hiltie brand, factory part numbers, country, or four-piece composition.
    for a in sample.get('characteristics',[]):
        k=int(a['id']);name=str(a.get('name') or ids.get(k,{}).get('name','')).lower()
        if k in ids and ('модель' in name or 'вид автотранспорта' in name):
            v=a.get('value');s=json.dumps(v,ensure_ascii=False).casefold()
            if ('dq200' in s or 'легковые' in s) and not any(x in s for x in ('hiltie','325473','4 шт')):
                attrs.append({'id':k,'value':v})
    if 378533 in ids:attrs=[a for a in attrs if a['id']!=378533]+[{'id':378533,'value':['Соленоиды — 8 шт.']}]
    body={'vendorCode':SKU,'title':R['title'],'description':R['description'],'brand':'','characteristics':attrs,'sizes':[{'techSize':'0','wbSize':'','price':23800}]}
    D['wildberries']={'subjectID':sid,'variant':copy.deepcopy(body),'missing_dimensions':True,'stock_submitted':False,'brand_unconfirmed':True}
    e=J['channels'].get('wildberries',{})
    if e.get('intent'):state('wildberries','creation_pending_readback_no_repeat',intent=True);return
    require(len(body['title'])<=60,'WB title length')
    p.current_main()
    codes=wb.generate_barcodes(1);require(len(codes)==1 and str(codes[0]).isdigit(),'WB barcode generation failed')
    body['sizes'][0]['skus']=[str(codes[0])]
    D['wildberries']['variant']=body
    state('wildberries','creation_intent',intent=True,subjectID=sid,barcode=str(codes[0]),price=23800,dimensions_omitted=True,stock_writes=0)
    p.current_main()
    ack=wb.create_cards(sid,[body],retries=1)
    require(not ack.get('error'),'WB rejected creation: '+str(ack)[:150])
    state('wildberries','creation_submitted_pending_nmid',intent=True)
def wb_finish():
    import wb_client as wb
    e=J['channels'].get('wildberries',{})
    if e.get('status')=='blocked_possible_duplicate':return
    found=None
    for i in range(6):
        found=wb_read()
        if found:break
        if i<5:time.sleep(6)
    if not found:
        try:
            errors=wb.get_upload_errors()
            hits=[x for x in errors if SKU in json.dumps(x,ensure_ascii=False)]
        except Exception:hits=[]
        state('wildberries','creation_error' if hits else 'creation_submitted_pending_nmid',errors=hits[:2]);return
    nm=int(found['nmID']);require(found.get('vendorCode')==SKU,'WB exact vendor code mismatch')
    require(found.get('description')==R['description'],'New WB content differs; do not overwrite')
    photos=found.get('photos') or [];video=found.get('video')
    if not photos and not video and not e.get('media_intent'):
        state('wildberries','media_intent',nmID=nm,media_intent=True)
        p.current_main();ack=wb.update_media(nm,[URL],retries=1);require(not ack.get('error'),'WB media rejected')
    rows=[x for x in wb.get_prices(limit=1000) if int(x.get('nmID') or 0)==nm]
    actual=None
    if len(rows)==1:
        vals=rows[0].get('sizes') or []
        if len(vals)==1:actual=vals[0].get('discountedPrice',vals[0].get('price'))
    if actual is not None and float(actual)!=23800 and not e.get('price_intent'):
        state('wildberries','price_intent',nmID=nm,price_intent=True)
        p.current_main();ack=wb.update_prices([{'nmID':nm,'price':23800,'discount':0}],retries=1)
        require(not ack.get('error'),'WB new price request rejected')
    for i in range(6):
        found=p.read_wb_exact(SKU,str(nm));photos=found.get('photos') or []
        if photos:break
        if i<5:time.sleep(5)
    rows=[x for x in wb.get_prices(limit=1000) if int(x.get('nmID') or 0)==nm]
    vals=rows[0].get('sizes') or [] if len(rows)==1 else []
    actual=vals[0].get('discountedPrice',vals[0].get('price')) if len(vals)==1 else None
    exact_photo=False
    try:exact_photo=len(photos)==1 and media.match(photos[0]['big'],media.digest_image((ROOT/R['media']['path']).read_bytes()))
    except Exception:pass
    ready=actual is not None and float(actual)==23800 and exact_photo
    state('wildberries','card_verified_not_for_sale_stock_unconfirmed' if ready else 'card_exists_pending_price_or_photo_verification',nmID=nm,price=actual,photo_count=len(photos),exact_photo_verified=exact_photo,stock_writes=0,dimensions_pending=True)
def tilda():
    import tilda_commerceml as tc
    from scripts.leoparts_direct_operation_nov21 import site_records
    rows=list(site_records().values());require(len(rows)>50,'Tilda catalog incomplete')
    found=exact(rows,'sku');candidates=duplicates(rows,'sku','title')
    if not found and candidates:state('tilda','blocked_possible_duplicate',matches=candidates);return
    e=J['channels'].get('tilda',{})
    if not found and not e.get('intent'):
        fn='import_files/'+SKU+'_'+R['media']['sha256'][:12]+'.jpeg'
        item={'id':SKU,'title':R['title'],'description':R['description'],'price':14800,'qty':0,'photo':''}
        imp=tc.build_import_xml([item],{SKU:[fn]}).replace('<Каталог>','<Каталог СодержитТолькоИзменения="true">')
        offers=tc.build_offers_xml([item]).replace('СодержитТолькоИзменения="false"','СодержитТолькоИзменения="true"')
        require('СодержитТолькоИзменения="true"' in imp and 'СодержитТолькоИзменения="true"' in offers,'Full Tilda import prohibited')
        creds=('TILDA_COMMERCEML_URL','TILDA_COMMERCEML_USER','TILDA_COMMERCEML_PASSWORD')
        require(all(os.getenv(k) for k in creds),'Tilda credentials missing')
        D['tilda']={'title':R['title'],'sku':SKU,'price':14800,'qty':0,'image':URL}
        state('tilda','creation_intent',intent=True,price=14800,availability_disabled=True)
        p.current_main();steps=tc.push(*(os.environ[k] for k in creds),imp,offers,timeout=90,image_files=[(fn,(ROOT/R['media']['path']).read_bytes())])
        state('tilda','creation_submitted',intent=True,steps=len(steps))
    for i in range(5):
        found=exact(list(site_records().values()),'sku')
        if found:break
        if i<4:time.sleep(6)
    if not found:state('tilda','creation_submitted_pending_readback');return
    require(found.get('sku')==SKU and (found.get('externalid') or found.get('external_id'))==SKU,'Tilda exact external identity differs')
    gal=found.get('gallery');gal=json.loads(gal) if isinstance(gal,str) else gal
    qty=found.get('quantity');price=float(found.get('price') or 0)
    image_ok=False
    if isinstance(gal,list) and len(gal)==1:
        x=gal[0];u=x if isinstance(x,str) else x.get('img') or x.get('url')
        try:image_ok=media.match(u,media.digest_image((ROOT/R['media']['path']).read_bytes()))
        except Exception:pass
    zero=str(qty) in ('0','0.0')
    state('tilda','card_verified_not_for_sale_stock_unconfirmed' if price==14800 and image_ok and zero else 'card_exists_pending_verification',uid=found['uid'],price=price,quantity=qty,photo_count=len(gal or []),exact_photo_verified=image_ok,url=found.get('url'))
def market():
    import yandex_market_client as ym
    from market_content import read_rows
    biz=ym.get_business_id();require(int(biz)==194752944,'Market business mismatch')
    rows=ym.get_all_offers(archived=False)+ym.get_all_offers(archived=True)
    require(len(rows)>100,'Market catalog coverage unexpectedly short')
    found=exact(rows,'sku')
    candidates=duplicates(rows,'sku','name')
    if candidates and not found:state('yandex_market','blocked_possible_duplicate',matches=candidates);return
    sample=[x for x in read_rows(biz,'offer-mappings',[REF]) if (x.get('offer') or {}).get('offerId')==REF]
    require(len(sample)==1,'Exact four-piece category reference missing')
    r=sample[0];cat=(r.get('offer') or {}).get('marketCategoryId') or (r.get('mapping') or {}).get('marketCategoryId')
    require(cat and int(cat)>0,'Market category missing')
    # The official create contract requires vendor. Do not assert 'Нет бренда'
    # or copy Hiltie: neither has been confirmed for this eight-piece kit.
    offer={'offerId':SKU,'name':R['title'],'description':R['description'],'marketCategoryId':int(cat),'pictures':[URL],'basicPrice':{'value':23800,'currencyId':'RUR'}}
    D['yandex_market']={'offerMappings':[{'offer':offer}],'required_missing':['vendor'],'packaged_dimensions_missing':True,'stock_unconfirmed':True}
    if found:state('yandex_market','existing_exact_sku_requires_review',market_sku=found.get('market_sku'),category_id=cat)
    else:state('yandex_market','draft_ready_brand_required',category_id=cat,price=23800,stock_writes=0)
def ozon():
    import ozon_client as oz
    rows=oz.list_products(limit=100);require(len(rows)>100,'Ozon catalog paging unexpectedly short')
    found=exact(rows,'offer_id')
    if found:state('ozon','existing_exact_sku_requires_review',product_id=found.get('product_id'));return
    sample=exact(oz.get_product_names([REF]),'offer_id',REF);require(sample is not None,'Exact Ozon category reference missing')
    cat=int(sample.get('description_category_id') or 0);typ=int(sample.get('type_id') or 0);require(cat>0 and typ>0,'Ozon category/type missing')
    definitions=oz.get_category_attributes(cat,typ)
    required=[{'id':a.get('id'),'name':a.get('name')} for a in definitions if a.get('is_required')]
    attrs=[{'id':4180,'complex_id':0,'values':[{'value':R['title']}]},{'id':4191,'complex_id':0,'values':[{'value':R['description']}]}]
    D['ozon']={'items':[{'offer_id':SKU,'name':R['title'],'description_category_id':cat,'type_id':typ,'price':'23800','currency_code':'RUB','primary_image':URL,'images':[],'attributes':attrs}],'required_category_fields':required,'missing_confirmed_packaging_dimensions':True,'missing_confirmed_customs_and_brand':True}
    state('ozon','draft_ready_required_dimensions_and_attributes_missing',description_category_id=cat,type_id=typ,required_fields=required,price=23800)
def avito():
    import avito_client as av
    import avito_feed as af
    from scripts.avito_dq200_submit_20261126 import profile
    profile();live=av.list_all_items()
    require(len(live)>100,'Avito catalog incomplete')
    suspects=[{'id':x.get('id'),'title':x.get('title'),'status':x.get('status')} for x in live if possible_full(x.get('title'))]
    if suspects:state('avito','blocked_possible_duplicate',matches=suspects);return
    primary=ROOT/'data/avito_feed.xlsx';src=load_workbook(primary,read_only=True,data_only=True);ws=src[af.SHEET_ADS]
    rows=[r for r in ws.iter_rows(min_row=af.FIRST_DATA_ROW,values_only=True) if str(r[af.COL_ID-1] or '')==REF]
    require(len(rows)==1,'Exact four-piece category reference missing in Avito feed')
    sample=rows[0];src.close()
    book=load_workbook(ROOT/'data/avito_template.xlsx');ws=book[af.SHEET_ADS]
    if ws.max_row>=af.FIRST_DATA_ROW:ws.delete_rows(af.FIRST_DATA_ROW,ws.max_row-af.FIRST_DATA_ROW+1)
    values={af.COL_ID:SKU,af.COL_ADDRESS:sample[af.COL_ADDRESS-1],af.COL_TITLE:R['title'],af.COL_DESCRIPTION:R['description'],af.COL_PRICE:14800,af.COL_IMAGE_URLS:URL}
    for col in (af.COL_CATEGORY,af.COL_GOODS_TYPE,af.COL_AD_TYPE,af.COL_PART_FOR,af.COL_PART_KIND,af.COL_TRANSMISSION_PART_TYPE):values[col]=sample[col-1]
    require(len(R['title'])<=50,'Avito title length')
    for col in range(1,ws.max_column+1):
        hdrs=[str(ws.cell(r,col).value or '').strip().casefold() for r in range(1,af.FIRST_DATA_ROW)]
        if any(x in ('listingfee','способ оплаты размещения') for x in hdrs):values[col]='Package'
        if 'adstatus' in hdrs:values[col]='Free'
    for col,v in values.items():ws.cell(af.FIRST_DATA_ROW,col,value=v)
    require(ws.cell(af.FIRST_DATA_ROW,af.COL_CONDITION).value is None,'Unconfirmed condition must remain blank')
    require(ws.cell(af.FIRST_DATA_ROW,af.COL_AVITO_ID).value is None,'No existing ad binding')
    path=ROOT/'drafts/solenoidy_dq200_8sht_avito_20261126.xlsx';book.save(path);book.close()
    D['avito']={'draft':str(path.relative_to(ROOT)),'title':R['title'],'price':14800,'image':URL,'category':sample[af.COL_CATEGORY-1],'part_type':sample[af.COL_TRANSMISSION_PART_TYPE-1],'condition':None,'stock_confirmed':None,'not_registered':True}
    J['channels']['avito']={'status':'draft_ready_condition_and_stock_required','price':14800,'draft':str(path.relative_to(ROOT)),'seller_writes':0};save(path)
def main():
    global J,R,D
    require(os.environ.get('GITHUB_REF')=='refs/heads/main','Main only');p.current_main()
    R=json.loads(REQ.read_text())
    require(R['owner_approved'] is True and R['sku']==SKU and R['quantity_per_kit']==8 and R['bench_tested'] is True and R['stock_confirmed'] is None,'Request changed; review required')
    require(R['prices']=={'ozon':23800,'wildberries':23800,'yandex_market':23800,'avito':14800,'tilda':14800},'Price scope changed')
    require(R['media']['sha256']=='d258f535dd89eba1d97bfc1f4e2970493e571312d2973656b2319e858a4cc5f1','Original hash mismatch')
    J=json.loads(OUT.read_text()) if OUT.exists() else {'sku':SKU,'started_utc':p.stamp(),'channels':{},'stock_confirmed':None,'saleable_initial_qty':0,'existing_card_writes':0,'ad_spend_writes':0}
    D=json.loads(DRAFT.read_text()) if DRAFT.exists() else {}
    init_source()
    for ch,fn in [('wildberries',wb_start),('tilda',tilda),('yandex_market',market),('ozon',ozon),('avito',avito),('wildberries',wb_finish)]:guard(ch,fn)
    J['completion_status']='partial_pending_owner_product_facts';J['finished_utc']=p.stamp();save()
if __name__=='__main__':main()
