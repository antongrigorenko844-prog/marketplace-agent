"""Only deterministic overlay on the exact existing Avito DQ200 galleries.
No generation, resize, retouch, other-channel writes, new ads, or commercial edits.
"""
from __future__ import annotations
import copy, io, json, os, sys
from pathlib import Path
from urllib.parse import urlsplit,parse_qs
import requests
from PIL import Image, ImageDraw, ImageFont, ImageChops
ROOT=Path(__file__).resolve().parents[1]
os.chdir(ROOT);sys.path[:0]=[str(ROOT),str(ROOT/'.github/scripts')]
import avito_feed as af
import avito_client as av
import content_manifest as cm
import content_pipeline as p
from scripts import avito_dq200_submit_20261126 as base
from scripts import avito_dq200_33500_append_originals_20261126 as prev
from scripts import dq200_hx_d_append_gallery_20261126 as patcher
from scripts.avito_dq200_exchange_text_20261126 import public,workbook as isolated_workbook
from scripts.apply_dq200_prices_20261120 import gh,rename_asset
FONT='/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf'
PRIMARY='avito_feed.xlsx'; ISOLATED=base.ASSET
TARGETS={PRIMARY:(base.SKU,8312205273,33500),ISOLATED:(base.NEW_ID,8477970233,29500)}
LOCAL=ROOT/'data/avito_feed.xlsx'
MAN=ROOT/'data/content_manifest.json'
OUT=ROOT/'data/avito_dq200_watermarks_20261126_result.json'
BACK=ROOT/'watermark_audit'
J={}
require=base.require
sha=base.sha

def save(*paths):
    J['updated_utc']=p.stamp()
    p.write_json(OUT,J)
    p.checkpoint([OUT,*paths],live=True)
    print(J['status'],flush=True)

def release():
    return gh('GET','/releases/tags/product-photos').json()

def asset(rel,name):
    hits=[a for a in rel.get('assets',[]) if a.get('name')==name]
    require(len(hits)<=1,'Duplicate asset name '+name)
    return hits[0] if hits else None

def stable(rel,name):
    a=asset(rel,name);require(a is not None,'Stable feed missing '+name)
    # Stable release names can return stale CDN bytes immediately after a
    # rename. Require the authoritative asset digest AND fresh public bytes.
    # A cache-busting query changes no registered feed URL or seller settings.
    digest=a.get('digest')
    require(isinstance(digest,str) and digest.startswith('sha256:'),'Stable asset digest unavailable '+name)
    import time
    for i in range(8):
        try:
            response=requests.get(base.BASE+name,params={'wm_readback':str(a['id'])+'-'+str(time.time_ns())},headers={'Cache-Control':'no-cache'},timeout=30)
            response.raise_for_status()
            raw=response.content
            if digest=='sha256:'+sha(raw):
                return a,raw
        except requests.RequestException:pass
        time.sleep(2)
    raise ValueError('Stable feed digest differs after fresh reads '+name)

def row(raw,name):
    feedid,avid,price=TARGETS[name]
    patcher.SKU=feedid;patcher.AV=avid
    n,r=patcher.avito_row(raw)
    require(str(r[af.COL_ID-1])==feedid and int(r[af.COL_PRICE-1])==price,'Target identity/price mismatch '+name)
    require(bool(r[af.COL_VIDEO_FILE_URL-1]),'Existing video missing '+name)
    if name==ISOLATED:
        rr,_=isolated_workbook(raw)
        require(rr==r,'Isolated feed row differs')
    return n,r

def urls(raw,name):
    _,r=row(raw,name)
    u=[x.strip() for x in str(r[af.COL_IMAGE_URLS-1] or '').splitlines() if x.strip()]
    require(len(u)==7 and len(set(map(cm.normalize_url,u)))==7,'Expected seven distinct current photos '+name)
    return u

def patch(raw,name,desired):
    n,before=row(raw,name)
    new=patcher.patch_avito(raw,n,'\n'.join(desired))
    nn,after=row(new,name)
    require(n==nn and urls(new,name)==desired,'Gallery cell verification failed')
    before=list(before);before[af.COL_IMAGE_URLS-1]='\n'.join(desired)
    require(tuple(before)==after,'Unrelated cell changed '+name)
    return new

def exact_binding(feedid,avid):
    try:
        response=base.api('GET','/autoload/v2/items/avito_ids',params={'query':feedid})
        hits=[r for r in response.get('items',[]) if str(r.get('ad_id'))==feedid]
        if hits:
            require(len(hits)==1 and str(hits[0].get('avito_id'))==str(avid),'Conflicting binding')
            return
    except ValueError:raise
    except Exception:pass
    for kind in ('current','last_successful'):
        try:_,items=base.report_items(kind)
        except Exception:continue
        hits=[r for r in items if str(r.get('ad_id'))==feedid]
        if hits:
            require({str(r.get('avito_id')) for r in hits}=={str(avid)},'Conflicting report binding')
            return
    raise ValueError('Exact Avito binding not verified '+feedid)

def expected_originals():
    a=json.loads(prev.OUT.read_text())
    require(a['avito_id']==8312205273 and a['photos_in_feed']==7 and a['public_feed_verified'] is True,'Previous append not confirmed')
    d=json.loads(base.DRAFT.read_text())
    g=[d['gallery']['cover_candidate'],*d['gallery']['additional_photos']]
    require([x['position'] for x in g]==list(range(1,8)),'Isolated original gallery not known')
    return {PRIMARY:a['desired_gallery'],ISOLATED:[x['hosted_url'] for x in g]}, {x['hosted_url']:x['sha256'] for x in g}

def source_bytes(rel,url,expected):
    u=urlsplit(url);require(url.startswith(base.BASE) and u.scheme=='https' and u.hostname=='github.com','Unapproved original host')
    name=u.path.rsplit('/',1)[1];a=asset(rel,name)
    require(a is not None,'Original asset missing '+name)
    raw=base.get(url);h=sha(raw)
    if url in expected:require(h==expected[url],'Original source SHA mismatch '+name)
    version=parse_qs(u.query).get('v',[])
    if version:require(h.startswith(version[0]),'Original URL version mismatch '+name)
    if a.get('digest'):require(a['digest']=='sha256:'+h,'Original asset digest mismatch '+name)
    return raw,name

def watermark(raw):
    # Same approved example: 42px at width 960, white alpha 82/255, 24 degrees.
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        require(getattr(im,'n_frames',1)==1,'Animated input not supported')
        require(im.getexif().get(274,1)==1,'Nonstandard EXIF orientation requires review')
        require(im.mode in ('RGB','RGBA','L','LA','P'),'Unsupported original color mode '+im.mode)
        alpha=im.convert('RGBA').getchannel('A') if im.mode in ('RGBA','LA') or 'transparency' in im.info else None
        rgb=im.convert('RGB');icc=im.info.get('icc_profile')
        w,h=rgb.size
        # Keep original pixel grid. Scale the lettering alone.
        f=ImageFont.truetype(FONT,max(12,round(42*min(w,h)/960)))
        phrase='LEOPARTS АКПП';bb=f.getbbox(phrase)
        stamp=Image.new('L',(bb[2]-bb[0]+12,bb[3]-bb[1]+12),0)
        ImageDraw.Draw(stamp).text((6-bb[0],6-bb[1]),phrase,font=f,fill=82)
        stamp=stamp.rotate(24,resample=Image.Resampling.BICUBIC,expand=True)
        mask=Image.new('L',(w,h),0)
        stencil=stamp.point(lambda x:255 if x else 0)
        require(stamp.width<w and stamp.height<h,'Watermark exceeds source canvas')
        centers=[(.5625,.30078125),(.28125,.5703125),(.7291666666667,.68359375)]
        for fx,fy in centers:
            x=int(fx*w-stamp.width/2);y=int(fy*h-stamp.height/2)
            x=max(0,min(w-stamp.width,x));y=max(0,min(h-stamp.height,y))
            mask.paste(stamp,(x,y),stencil)
        result=Image.composite(Image.new('RGB',rgb.size,'white'),rgb,mask)
        if alpha is not None:result.putalpha(alpha)
        out=io.BytesIO();kwargs={'icc_profile':icc} if icc else {}
        result.save(out,format='PNG',**kwargs)
        data=out.getvalue()
        with Image.open(io.BytesIO(data)) as check:
            check.load()
            require(check.format=='PNG' and check.size==rgb.size,'Output resolution changed')
            outside=mask.point(lambda x:0 if x else 255)
            diff=ImageChops.difference(check.convert('RGB'),rgb)
            require(ImageChops.multiply(diff,Image.merge('RGB',[outside]*3)).getbbox() is None,'Pixels changed outside watermark')
            require(ImageChops.difference(check.convert('RGB'),result.convert('RGB')).getbbox() is None,'PNG roundtrip changed pixels')
            if alpha is not None:require(ImageChops.difference(check.getchannel('A'),alpha).getbbox() is None,'Alpha changed')
            require(check.info.get('icc_profile')==icc,'Color profile changed')
            require(mask.getbbox() is not None and diff.getbbox() is not None,'No visible watermark')
        return data,{'dimensions':[w,h],'outside_mask_identical':True,'png_lossless_verified':True,'color_profile_preserved':True,'alpha_preserved':True,'opacity':82/255,'angle':24,'stamps':3,'font':'DejaVu Sans Condensed Bold','mask_pixels':sum(mask.histogram()[1:])}

def upload(rel,name,data,mime):
    a=asset(rel,name)
    if not a:
        p.current_main()
        a=gh('POST',rel['upload_url'].split('{',1)[0],params={'name':name},data=data,headers={'Content-Type':mime}).json()
        rel['assets'].append(a)
    require(a.get('digest') in (None,'sha256:'+sha(data)),'Asset digest mismatch '+name)
    require(public(a['browser_download_url'],data),'Hosted bytes differ '+name)
    return a

def preflight_manifest(original,desired):
    m=json.loads(MAN.read_text());before=copy.deepcopy(m)
    require(m['products'][base.SKU]['sources']['avito'] in (original,desired),'Canonical Avito gallery differs')
    m['products'][base.SKU]['sources']['avito']=desired
    before['products'][base.SKU]['sources']['avito']=desired
    require(m==before,'Unexpected manifest changes')
    return m

def received(desired):
    result={}
    for kind in ('current','last_successful'):
        try:
            report,items=base.report_items(kind)
            record={'upload_id':report.get('upload_id'),'status':report.get('status'),'galleries':{}}
            for name,(fid,aid,_) in TARGETS.items():
                hits=[x for x in items if str(x.get('ad_id'))==fid and str(x.get('avito_id'))==str(aid)]
                record['galleries'][name]={'listing_matches':[{k:x.get(k) for k in ('avito_id','avito_status','section','messages')} for x in hits],'received_gallery_matches':False}
            for f in report.get('feed_urls',[]):
                u=f.get('url','');pu=urlsplit(u)
                if pu.scheme!='https' or pu.hostname!='api.avito.ru' or not pu.path.startswith('/autoload/v2/feed/content/'):continue
                response=requests.get(u,headers={'Authorization':av._headers()['Authorization']},timeout=20,allow_redirects=False)
                if not response.ok:continue
                for name in TARGETS:
                    try:got=urls(response.content,name)
                    except Exception:continue
                    if got==desired[name]:record['galleries'][name]['received_gallery_matches']=True
            result[kind]=record
        except Exception as exc:result[kind]={'read_error':type(exc).__name__+': '+str(exc)[:200]}
    return result

def live_gallery(name,desired):
    _,aid,_=TARGETS[name]
    try:
        account=str(base.api('GET','/core/v1/accounts/self').get('id') or '')
        detail=base.api('GET',f'/core/v1/accounts/{account}/items/{aid}/') if account.isdigit() else {}
        values=detail.get('images') or detail.get('photos')
        if not isinstance(values,list) or len(values)!=len(desired):return False
        actual=[v if isinstance(v,str) else v.get('url') or v.get('large') or v.get('big') for v in values]
        # Do not use approximate visual similarity: it can falsely accept unwatermarked originals.
        if not all(isinstance(u,str) and u.startswith('https://') for u in actual):return False
        return all(cm.normalize_url(a)==cm.normalize_url(b) or sha(base.get(a))==sha(base.get(b)) for a,b in zip(actual,desired))
    except Exception:return False

def main():
    global J
    require(os.environ.get('GITHUB_REF')=='refs/heads/main','Main only')
    p.current_main()
    J=json.loads(OUT.read_text()) if OUT.exists() else {'status':'preflight','targets':{'8312205273':33500,'8477970233':29500},'only_watermark_overlay':True,'no_generation':True,'price_writes':0,'description_writes':0,'other_listing_writes':0,'started_utc':p.stamp()}
    require(J['targets']=={'8312205273':33500,'8477970233':29500},'Journal scope mismatch')
    try:
        profile=base.profile()
        require([x['feed_url'] for x in profile['feeds_data']]==base.EXISTING+[base.URL],'Registered feed set changed')
        before=prev.listings()
        tools_sha=sha(base.get(base.EXISTING[1]))
        for fid,aid,_ in TARGETS.values():exact_binding(fid,aid)
        rel=release()
        current={n:stable(rel,n) for n in TARGETS}
        original,expected=expected_originals()
        observed={n:urls(raw,n) for n,(_,raw) in current.items()}
        require(all([cm.normalize_url(x) for x in original[n]]==[cm.normalize_url(x) for x in observed[n]] or J.get('desired',{}).get(n)==observed[n] for n in TARGETS),'Current gallery differs from approved sources')
        if J.get('desired'):
            desired=J['desired']
            require(J['original']==original,'Recorded original gallery changed')
            require(all(sha(current[n][1]) in (J['feeds'][n]['before_sha256'],J['feeds'][n]['after_sha256']) for n in TARGETS),'Concurrent feed change, no replay')
            require(all(observed[n] in (original[n],desired[n]) for n in TARGETS),'Unknown gallery state')
        else:
            require(current[PRIMARY][1]==LOCAL.read_bytes(),'Repository/public primary feed differs')
            require(observed==original,'Original URLs or order changed')
            # First prepare every image and verify every pixel, without changing either feed.
            BACK.mkdir(parents=True,exist_ok=True)
            proofs={};data_by_url={}
            for url in dict.fromkeys(u for n in TARGETS for u in original[n]):
                raw,name=source_bytes(rel,url,expected)
                h=sha(raw);data,proof=watermark(raw)
                (BACK/(h+'_'+name)).write_bytes(raw)
                outname='avito-dq200-wm-v1-'+h[:16]+'-'+sha(data)[:12]+'.png'
                (BACK/outname).write_bytes(data)
                proofs[url]={'original_sha256':h,'protected_sha256':sha(data),'original_asset':name,'protected_asset':outname,**proof}
                data_by_url[url]=data
            require(len(proofs)==len(set(u for n in TARGETS for u in original[n])),'Incomplete pixel verification')
            desired={}
            for url,proof in proofs.items():
                a=upload(rel,proof['protected_asset'],data_by_url[url],'image/png')
                proof['protected_url']=a['browser_download_url']+'?v='+proof['protected_sha256'][:10]
                require(public(proof['protected_url'],data_by_url[url]),'Protected cache URL mismatch')
            for n in TARGETS:desired[n]=[proofs[u]['protected_url'] for u in original[n]]
            require(all(len(desired[n])==len(original[n])==7 and len(set(desired[n]))==7 for n in TARGETS),'Photo order/count invalid')
            preflight_manifest(original[PRIMARY],desired[PRIMARY])
            feeds={}
            for n,(a,raw) in current.items():
                new=patch(raw,n,desired[n]);h=sha(new)
                stage_name=n.replace('.xlsx','.watermark_staged_'+h[:12]+'.xlsx')
                stage=upload(rel,stage_name,new,'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
                (BACK/(n+'.before.xlsx')).write_bytes(raw)
                (BACK/(n+'.after.xlsx')).write_bytes(new)
                feeds[n]={'before_sha256':sha(raw),'after_sha256':h,'old_asset_id':a['id'],'staged_asset_id':stage['id'],'stage_name':stage_name,'backup_name':n.replace('.xlsx','.before_watermark_'+str(a['id'])+'.xlsx'),'only_image_cell_changed':True,'video_unchanged':True}
            J.update(status='both_feeds_staged_verified',original=original,desired=desired,images=proofs,unique_originals=len(proofs),photos_per_listing=7,feeds=feeds)
            save()
        # Revalidate every protected source and its original before stable promotion.
        for u,proof in J['images'].items():
            require(sha(base.get(u))==proof['original_sha256'],'Original changed '+proof['original_asset'])
            require(sha(base.get(proof['protected_url']))==proof['protected_sha256'],'Protected asset changed')
            require(proof['outside_mask_identical'] and proof['png_lossless_verified'],'Pixel proof missing')
        m=preflight_manifest(original[PRIMARY],desired[PRIMARY])
        updated=[]
        try:
            for n in TARGETS:
                f=J['feeds'][n];a,raw=stable(release(),n)
                if sha(raw)==f['after_sha256']:
                    require(urls(raw,n)==desired[n],'Published gallery mismatch')
                    continue
                require(sha(raw)==f['before_sha256'] and a['id']==f['old_asset_id'],'Stable feed changed '+n)
                require(n!=PRIMARY or LOCAL.read_bytes()==raw,'Primary repository drift')
                staged=base.get(base.BASE+f['stage_name'])
                require(sha(staged)==f['after_sha256'] and patch(raw,n,desired[n])==staged,'Stage bytes differ from exact one-cell patch')
                p.current_main()
                rename_asset(a['id'],f['backup_name'])
                try:rename_asset(f['staged_asset_id'],n)
                except Exception:
                    rename_asset(a['id'],n);raise
                updated.append(n)
                require(public(base.BASE+n,staged),'Stable protected feed readback failed '+n)
            final={n:stable(release(),n)[1] for n in TARGETS}
            require(all(sha(final[n])==J['feeds'][n]['after_sha256'] and urls(final[n],n)==desired[n] for n in TARGETS),'Final stable feed mismatch')
            require(sha(base.get(base.EXISTING[1]))==tools_sha,'Unrelated feed changed')
            require(prev.listings()==before,'Live listing protected fields changed')
            require(base.profile()['feeds_data']==profile['feeds_data'],'Profile changed')
        except Exception:
            # Restore the exact prior assets for any promotion from this attempt.
            for n in reversed(updated):
                f=J['feeds'][n]
                rename_asset(f['staged_asset_id'],f['stage_name'])
                rename_asset(f['old_asset_id'],n)
            raise
        p.current_main()
        LOCAL.write_bytes(final[PRIMARY])
        p.write_json(MAN,m);cm.load()
        J.update(status='both_public_feeds_updated_pending_avito_processing',public_feeds_verified=True,other_feed_unchanged=True,protected_listing_fields_unchanged=True,manifest_pinned=True,original_assets_retained=True)
        save(LOCAL,MAN)
        J['reports']=received(desired)
        J['live_gallery_verified']={n:live_gallery(n,desired[n]) for n in TARGETS}
        if all(J['live_gallery_verified'].values()):J['status']='both_live_galleries_verified'
        elif any(x.get('received_gallery_matches') for rec in J['reports'].values() for x in rec.get('galleries',{}).values()):J['status']='avito_received_at_least_one_feed_live_gallery_unverified'
        J.pop('error',None);save()
        return 0
    except Exception as exc:
        J['status']='blocked_or_unverified';J['error']=type(exc).__name__+': '+str(exc)[:450];save();return 1
if __name__=='__main__':sys.exit(main())
