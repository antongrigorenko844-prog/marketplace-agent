"""Copy existing product-card originals and add one exact text overlay only.

No new covers, collages, captions, crops or resizing in deliverable images.
Contact sheets are separate QA previews, never publication candidates.
"""
from __future__ import annotations
import hashlib
import json
import argparse
from pathlib import Path
from urllib.parse import urlsplit
from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, __version__ as PILLOW_VERSION

ROOT = Path(__file__).resolve().parent
PREV = ROOT.parent / 'board_media'
FONT = '/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf'
EXPECTED_FONT_SHA256 = '21f02ebcea75529d3ed9d26e089cd1b9c59a4b784119ba3a96e9bc83cf755ec7'
TEXT = 'LEOPARTS АКПП'

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def overlay(source, output, mask_output):
    with Image.open(source) as im:
        im.load()
        assert getattr(im,'n_frames',1)==1
        assert im.getexif().get(274,1)==1
        rgb=im.convert('RGB'); icc=im.info.get('icc_profile')
        alpha=im.convert('RGBA').getchannel('A') if 'A' in im.mode or 'transparency' in im.info else None
    w,h=rgb.size
    is_info=source.name.endswith('_2.png')
    opacity=44 if is_info else 82
    color='black' if is_info else 'white'
    f=ImageFont.truetype(FONT,max(12,round(42*min(w,h)/960)))
    bb=f.getbbox(TEXT)
    stamp=Image.new('L',(bb[2]-bb[0]+12,bb[3]-bb[1]+12),0)
    ImageDraw.Draw(stamp).text((6-bb[0],6-bb[1]),TEXT,font=f,fill=opacity)
    stamp=stamp.rotate(24,resample=Image.Resampling.BICUBIC,expand=True)
    exclusions=[]
    if source.name.endswith('_1.png') or source.name.endswith('_2.png'):
        exclusions.append((.06,.17,.49,.51))
    if is_info:
        exclusions.append((.51,.19,.96,.53))
    if source.name=='0AM927769D_2.jpg':
        exclusions.append((.78,.69,.98,.97))
    exclusions += {
        '01-8824052d56.jpeg':[(.54,.30,.81,.69)],
        '02-912c0f46f4.jpeg':[(.57,0,.99,.34)],
        '03-b64f27f6d0.jpeg':[(.32,.43,.80,.77)],
        '05-e13494ab83.jpeg':[(.50,.55,.86,.82)],
    }.get(source.name,[])
    blocked=[(int(a*w),int(b*h),int(c*w),int(d*h)) for a,b,c,d in exclusions]
    candidates=[(.56,.57),(.28,.57),(.73,.68),(.60,.17),(.19,.56),(.66,.91)]
    if is_info:
        candidates=[(.73,.68),(.30,.91),(.75,.12)]+candidates
    if source.name.endswith('_3.png'):
        candidates=[(.27,.58)]+candidates
    if source.name=='0AM927769K_1.png':
        candidates=[(.75,.20)]+candidates
    if source.name=='03-b64f27f6d0.jpeg':
        candidates=[(.19,.56),(.60,.17),(.66,.91)]+candidates
    candidates += [(fx,fy) for fy in (.16,.30,.45,.60,.76,.90) for fx in (.25,.50,.75)]
    def intersects(a,b):
        return a[0]<b[2] and a[2]>b[0] and a[1]<b[3] and a[3]>b[1]
    mask=Image.new('L',(w,h),0)
    chosen=None
    for fx,fy in candidates:
        x=max(0,min(w-stamp.width,round(fx*w-stamp.width/2)))
        y=max(0,min(h-stamp.height,round(fy*h-stamp.height/2)))
        rect=(x,y,x+stamp.width,y+stamp.height)
        if any(intersects(rect,r) for r in blocked):
            continue
        mask.paste(stamp,(x,y))
        chosen=(fx,fy);break
    assert chosen is not None
    result=Image.composite(Image.new('RGB',rgb.size,color),rgb,mask)
    if alpha is not None: result.putalpha(alpha)
    result.save(output,format='PNG',**({'icc_profile':icc} if icc else {}))
    mask.save(mask_output,format='PNG')
    with Image.open(output) as check:
        check.load()
        assert check.size==rgb.size
        assert check.format=='PNG'
        diff=ImageChops.difference(check.convert('RGB'),rgb)
        outside=mask.point(lambda x:0 if x else 255)
        assert ImageChops.multiply(diff,Image.merge('RGB',[outside]*3)).getbbox() is None
        assert diff.getbbox() is not None
        assert check.info.get('icc_profile')==icc
        if alpha is not None:
            assert ImageChops.difference(check.getchannel('A'),alpha).getbbox() is None
        rgba_sha256=sha(check.convert('RGBA').tobytes())
    return {'dimensions':[w,h],'watermark_count_added':1,'text':TEXT,'color':color,'opacity':opacity/255,'angle_degrees':24,'center':chosen,'outside_watermark_pixels_identical':True,'dimensions_identical':True,'no_resize':True,'no_crop':True,'no_layout_change':True,'no_new_captions':True,'alpha_preserved':True,'color_profile_preserved':True,'label_exclusions':exclusions,'mask_sha256':sha(mask_output.read_bytes()),'rgba_sha256':rgba_sha256}

def contact(rows,path):
    cw,ch,cols=390,360,4
    canvas=Image.new('RGB',(cols*cw,((len(rows)+cols-1)//cols)*ch),'#eeeeee')
    d=ImageDraw.Draw(canvas); f=ImageFont.truetype(FONT,17)
    for i,r in enumerate(rows):
        x,y=(i%cols)*cw,(i//cols)*ch
        with Image.open(ROOT/r['protected_path']) as im:
            im=ImageOps.contain(im.convert('RGB'),(cw-12,ch-43),Image.Resampling.LANCZOS)
            canvas.paste(im,(x+(cw-im.width)//2,y+38+(ch-43-im.height)//2))
        label=f"{i+1:02} {r['filename']}"
        d.text((x+7,y+5),label,font=f,fill='#111111')
        if r['disputed']:
            d.text((x+7,y+23),'СПОРНЫЙ ИСХОДНИК ИЗ КАРТОЧКИ',font=ImageFont.truetype(FONT,13),fill='#b83020')
    canvas.save(path,quality=94)

def main():
    global ROOT,FONT
    parser=argparse.ArgumentParser()
    parser.add_argument('--source-map',type=Path,default=ROOT/'source_map.json')
    parser.add_argument('--repo-root',type=Path,default=Path.cwd())
    parser.add_argument('--source-dir',type=Path)
    parser.add_argument('--font',type=Path)
    parser.add_argument('--output',type=Path,default=ROOT)
    args=parser.parse_args()
    ROOT=args.output
    FONT=str(args.font or args.repo_root/'assets/fonts/avito-boards-20261127/DejaVuSansCondensed-Bold.ttf')
    assert sha(Path(FONT).read_bytes())==EXPECTED_FONT_SHA256,'Unexpected font file'
    for folder in ('original','protected','masks','disputed'):
        (ROOT/folder).mkdir(parents=True,exist_ok=True)
    source_map=json.loads(args.source_map.read_text())
    evidence=source_map['avito_feed_evidence']
    avito_order=[r['filename'] for r in source_map['sources']]
    full_order=avito_order
    sources={r['filename']:r for r in source_map['sources']}
    rows=[]
    for position,name in enumerate(full_order,1):
        row=sources[name]
        src=(args.source_dir/name) if args.source_dir else (args.repo_root/row['repository_path'])
        data=src.read_bytes();assert sha(data)==row['sha256']
        original=ROOT/'original'/name;original.write_bytes(data)
        suffix='-avito' if name.endswith('.jpg') else ''
        target=ROOT/'protected'/f"{position:02}-{Path(name).stem}{suffix}-wm.png"
        mask=ROOT/'masks'/(target.name+'.mask.png')
        proof=overlay(original,target,mask)
        disputed=name=='0AM927769K_1.png'
        rec={**row,'position_current_avito_union':position,'original_path':str(original.relative_to(ROOT)),'protected_path':str(target.relative_to(ROOT)),'mask_path':str(mask.relative_to(ROOT)),'protected_sha256':sha(target.read_bytes()),'protected_bytes':target.stat().st_size,'proof':proof,'disputed':disputed,'publish_decision':'owner_requested_preserve_existing_card_image' if disputed else 'eligible_existing_card_image'}
        rec.pop('publish',None)
        rec.pop('quarantine_reason',None)
        if disputed:
            rec['dispute']={'reason':'Existing source is a cropped collage with incomplete text, assembled mechatronics and an E-marking inset; cannot be presented as a correct K-only product photo.','present_in_current_avito_feed':True,'feed_row':evidence['0AM927769K']['feed_row'],'feed_photo_position':evidence['0AM927769K']['photo_urls'].index(row['source_url'])+1,'live_page_verified':False,'source_repository_path':row['repository_path'],'source_url':row['source_url']}
            (ROOT/'disputed'/target.name).write_bytes(target.read_bytes())
        rows.append(rec)
    assert len(rows)==18 and len(avito_order)==18
    doc={'schema_version':2,'owner_correction':'Exact existing Avito product-card photographs. No new cover, montage, captions, crop, resize or reformatted layout. One LEOPARTS АКПП watermark added per image. K_1 retained exactly as requested; content issue documented.','source_count':len(rows),'unique_source_sha256_count':len(set(r['sha256'] for r in rows)),'existing_avito_feed_union_count':len(avito_order),'no_photo_limit_assumed':True,'source_feed_snapshot':'data/avito_feed.xlsx from board inventory; not a live visual readback','current_avito_feed_evidence':evidence,'current_avito_feed_order':[next(r['protected_path'] for r in rows if r['filename']==f) for f in avito_order],'all_source_files_prepared':True,'disputed_source_not_silently_removed':True,'renderer':{'pillow_version':PILLOW_VERSION,'font_sha256':sha(Path(FONT).read_bytes())},'sources':rows}
    doc['gallery']=[{'position':r['position_current_avito_union'],'sku':r['sku'],'source_repository_path':r['repository_path'],'source_url':r['source_url'],'source_sha256':r['sha256'],'filename':Path(r['protected_path']).name,'protected_path':r['protected_path'],'dimensions':r['proof']['dimensions'],'sha256':r['protected_sha256'],'pixel_sha256_rgba':r['proof']['rgba_sha256'],'mask_sha256':r['proof']['mask_sha256'],'outside_mask_identical':r['proof']['outside_watermark_pixels_identical'],'watermarks_added':1,'source_content_issue_retained':r['disputed']} for r in rows]
    doc['gallery_count']=len(doc['gallery'])
    (ROOT/'manifest.json').write_text(json.dumps(doc,ensure_ascii=False,indent=2)+'\n')
    expected={'schema_version':1,'pillow_version':PILLOW_VERSION,'font_sha256':sha(Path(FONT).read_bytes()),'gallery':[{'position':g['position'],'filename':g['filename'],'dimensions':g['dimensions'],'pixel_sha256_rgba':g['pixel_sha256_rgba'],'mask_sha256':g['mask_sha256'],'sha256':g['sha256']} for g in doc['gallery']]}
    (ROOT/'expected_gallery_rgba.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2)+'\n')
    (ROOT/'current_avito_order.txt').write_text('\n'.join(doc['current_avito_feed_order'])+'\n')
    contact(rows,ROOT/'all_originals_watermarked_review.jpg')
    avito_rows=[next(r for r in rows if r['filename']==f) for f in avito_order]
    contact(avito_rows,ROOT/'current_avito_images_review.jpg')
    print(json.dumps({k:doc[k] for k in ('source_count','unique_source_sha256_count','existing_avito_feed_union_count','no_photo_limit_assumed','all_source_files_prepared')},ensure_ascii=False))

if __name__=='__main__':main()
