"""Owner-approved, append ONLY existing originals to Avito 8312205273.
No watermarks, generation, image edits, new listing or changes to 8477970233.
"""
from __future__ import annotations
import copy, io, json, os, sys
from pathlib import Path
from urllib.parse import urlsplit
import requests
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT)
sys.path[:0]=[str(ROOT),str(ROOT/'.github/scripts')]
import avito_feed as af
import avito_client as av
import content_pipeline as p
import content_manifest as cm
from scripts import dq200_hx_d_append_gallery_20261126 as helper
from scripts import avito_dq200_submit_20261126 as base
from scripts.avito_dq200_exchange_text_20261126 import public
from scripts.apply_dq200_prices_20261120 import gh,get_avito_asset,rename_asset
SKU='0am325025H'; AV=8312205273; OTHER=8477970233
helper.SKU=SKU;helper.AV=AV
FEED=ROOT/'data/avito_feed.xlsx'
MAN=ROOT/'data/content_manifest.json'
OUT=ROOT/'data/avito_dq200_33500_append_originals_20261126_result.json'
ORDER=[4,5,3,2,6]
EXPECTED={
4:'66e47f1b3f15cf58b7edd94004b6735e399625bf013d3c34f668c7276ab5f1de',
5:'689c3d068983feff3a48924d3d88f6298bc021e1d533bb9d8b371a626eb850aa',
3:'e02ce53614f72032678e792131792ee6bcb1a476f7da8d17e61f38d6c9089474',
2:'968326b7acbd456c8ce3e73b883eae8b5d4e6fe03c7bac2138c08018779874c3',
6:'578322477df73a581ec7c7f3773389ea296cd89147427fcdc50daeec5cbc6f64'}
J={}
require=base.require
sha=base.sha
def save(*paths):
    J['updated_utc']=p.stamp()
    p.write_json(OUT,J)
    p.checkpoint([OUT,*paths],live=True)
    print(J['status'],flush=True)
def listings():
    rows=av.list_all_items(statuses=['active'])
    result={}
    for i,price in [(AV,33500),(OTHER,29500)]:
        hit=[x for x in rows if str(x.get('id'))==str(i)]
        require(len(hit)==1 and int(hit[0].get('price') or 0)==price,'Listing ID/status/price mismatch '+str(i))
        result[str(i)]={k:hit[0].get(k) for k in ('id','title','price','status','url')}
    return result
def image(raw):
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        return im.convert('RGB')
def source(u):
    parsed=urlsplit(u)
    require(parsed.scheme=='https' and parsed.hostname and not parsed.username and not parsed.password,'Unsafe image URL')
    return base.get(u)
def sources():
    draft=json.loads(base.DRAFT.read_text())
    require(draft['sku']==SKU and draft['existing_listing_protection']['avito_id']==AV,'Original media draft ID mismatch')
    rows={x['position']:x for x in draft['gallery']['additional_photos']}
    result=[]
    for pos in ORDER:
        x=rows[pos];raw=(ROOT/x['repository_path']).read_bytes()
        require(x['sha256']==EXPECTED[pos] and sha(raw)==EXPECTED[pos],'Original source digest mismatch')
        require(x['hosted_url'].startswith(base.BASE) and sha(source(x['hosted_url']))==EXPECTED[pos],'Hosted image differs from original')
        result.append((x['hosted_url'],raw,image(raw)))
    return result
def verify_received(desired):
    evidence=[]
    for kind in ('current','last_successful'):
        try:
            report,items=base.report_items(kind)
            hit=[x for x in items if x.get('ad_id')==SKU and str(x.get('avito_id'))==str(AV)]
            rec={'kind':kind,'upload_id':report.get('upload_id'),'status':report.get('status'),'listing_matches':[{k:x.get(k) for k in ('avito_id','section','messages','avito_status')} for x in hit],'received_gallery_matches':False}
            for f in report.get('feed_urls',[]):
                url=f.get('url','');u=urlsplit(url)
                if u.scheme!='https' or u.hostname!='api.avito.ru' or not u.path.startswith('/autoload/v2/feed/content/'):continue
                rr=requests.get(url,headers={'Authorization':av._headers()['Authorization']},timeout=20,allow_redirects=False)
                if not rr.ok:continue
                try:_,row=helper.avito_row(rr.content)
                except Exception:continue
                rec['received_gallery_matches']=str(row[af.COL_IMAGE_URLS-1]).splitlines()==desired
                if rec['received_gallery_matches']:break
            evidence.append(rec)
        except Exception as exc:evidence.append({'kind':kind,'read_error':type(exc).__name__+': '+str(exc)[:160]})
    return evidence
def main():
    global J
    require(os.environ.get('GITHUB_REF')=='refs/heads/main','Main only')
    p.current_main()
    J=json.loads(OUT.read_text()) if OUT.exists() else {'avito_id':AV,'sku':SKU,'price_rub':33500,'protected_other_id':OTHER,'original_files_only':True,'watermarks':False,'other_listing_writes':0,'price_writes':0,'description_writes':0}
    require(J['avito_id']==AV and J['sku']==SKU,'Wrong journal')
    try:
        profile=base.profile()
        require([x['feed_url'] for x in profile['feeds_data']]==base.EXISTING+[base.URL],'Unexpected feed configuration')
        before=listings()
        other=[sha(base.get(u)) for u in (base.EXISTING[1],base.URL)]
        mapping=base.api('GET','/autoload/v2/items/avito_ids',params={'query':SKU})
        hits=[x for x in mapping.get('items',[]) if x.get('ad_id')==SKU and str(x.get('avito_id'))==str(AV)]
        require(len(hits)==1,'Exact old listing feed binding not verified')
        release,asset,raw=get_avito_asset()
        n,row=helper.avito_row(raw)
        require(int(row[af.COL_PRICE-1])==33500 and bool(row[af.COL_VIDEO_FILE_URL-1]),'Price or video mismatch')
        old=[u.strip() for u in str(row[af.COL_IMAGE_URLS-1]).splitlines() if u.strip()]
        require(old and len(old)==len(set(map(cm.normalize_url,old))),'Existing gallery invalid')
        originals=sources()
        if J.get('desired_gallery'):
            desired=J['desired_gallery'];prior=J['before_gallery']
            require(old in (desired,prior),'Existing gallery changed since recorded intent')
        else:
            require(FEED.read_bytes()==raw,'Repository and published primary feed differ; no overwrite')
            prior=old[:];desired=old[:]
            prior_media=[(u,source(u)) for u in old]
            for url,b,im in originals:
                if any(cm.normalize_url(url)==cm.normalize_url(u) or sha(b)==sha(rb) or helper.same(im,image(rb)) for u,rb in prior_media):
                    continue
                desired.append(url);prior_media.append((url,b))
            require(len(desired)<=af.AVITO_MAX_PHOTOS,'Gallery exceeds Avito limit')
        require(desired[:len(prior)]==prior,'Existing photo order changed')
        require(len(desired)<=af.AVITO_MAX_PHOTOS,'Gallery limit')
        manifest=json.loads(MAN.read_text())
        require(SKU in manifest['products'],'Exact manifest SKU missing')
        mprev=copy.deepcopy(manifest)
        manifest['products'][SKU].setdefault('sources',{})['avito']=desired
        mprev['products'][SKU].setdefault('sources',{})['avito']=desired
        require(manifest==mprev,'Unexpected manifest changes')
        J.update(before_gallery=prior,desired_gallery=desired,appended=desired[len(prior):],photos_before=len(prior),photos_in_feed=len(desired),video_preserved=True,originals_verified=5)
        if old!=desired:
            require(FEED.read_bytes()==raw,'Repository feed differs before publication')
            new=helper.patch_avito(raw,n,'\n'.join(desired))
            h=sha(new);stage_name='avito_feed.dq200_33500_originals_'+h[:12]+'.xlsx'
            stages=[a for a in release['assets'] if a['name']==stage_name]
            require(len(stages)<=1,'Duplicate staging asset')
            stage=stages[0] if stages else gh('POST',release['upload_url'].split('{',1)[0],params={'name':stage_name},data=new,headers={'Content-Type':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}).json()
            require(public(stage['browser_download_url'],new),'Stage verification failed')
            J.update(status='publish_intent',old_sha256=sha(raw),new_sha256=h,old_asset_id=asset['id'],staged_asset_id=stage['id'],only_image_cell_changed=True)
            save()
            p.current_main();_,current,currentraw=get_avito_asset()
            require(current['id']==asset['id'] and currentraw==raw,'Concurrent primary feed edit')
            rename_asset(asset['id'],'avito_feed.before_dq200_33500_originals_'+str(asset['id'])+'.xlsx')
            try:rename_asset(stage['id'],'avito_feed.xlsx')
            except Exception:
                rename_asset(asset['id'],'avito_feed.xlsx');raise
            require(public(base.EXISTING[0],new),'Public updated feed verification failed')
            FEED.write_bytes(new)
        else:
            if FEED.read_bytes()!=raw:
                require(J.get('new_sha256')==sha(raw),'Unrelated repository feed drift')
                require(J.get('old_sha256')==sha(FEED.read_bytes()),'Repository feed drift after intent')
                FEED.write_bytes(raw)
            J['only_image_cell_changed']=True
        p.current_main();p.write_json(MAN,manifest);cm.load()
        J.update(status='feed_updated_pending_avito_processing',public_feed_verified=True,main_photo_preserved=True)
        save(FEED,MAN)
        require([sha(base.get(u)) for u in (base.EXISTING[1],base.URL)]==other,'Other published feeds changed')
        require(listings()==before,'Protected live listing fields differ')
        require(base.profile()['feeds_data']==profile['feeds_data'],'Feed profile changed')
        J.update(other_feeds_unchanged=True,listing_fields_unchanged=True)
        J['reports']=verify_received(desired)
        live=False
        try:
            aid=str(base.api('GET','/core/v1/accounts/self').get('id') or '')
            detail=base.api('GET',f'/core/v1/accounts/{aid}/items/{AV}/') if aid.isdigit() else {}
            values=detail.get('images') or detail.get('photos')
            if isinstance(values,list):
                urls=[x if isinstance(x,str) else x.get('url') or x.get('large') or x.get('big') for x in values]
                if all(isinstance(x,str) and x.startswith('https://') for x in urls):live=helper.matches(urls,desired)
        except Exception:pass
        J['live_gallery_verified']=live
        J['status']='live_gallery_verified' if live else 'avito_received_feed_live_gallery_unverified' if any(x.get('received_gallery_matches') for x in J['reports']) else 'feed_updated_pending_avito_processing'
        J.pop('error',None);save()
        return 0
    except Exception as exc:
        J['status']='blocked_or_unverified';J['error']=type(exc).__name__+': '+str(exc)[:400];save();return 1
if __name__=='__main__':sys.exit(main())
