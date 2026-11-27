"""Deterministic original-photo watermark and gallery preparation only.

No network, seller API, uploads or publication. Input pixels are preserved in
individual protected PNGs outside explicitly measured watermark masks.
Run with repository root in CI, or --source-dir for a downloaded local cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont, ImageOps, __version__ as PILLOW_VERSION

PHRASE = "LEOPARTS АКПП"
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed-Bold.ttf"
REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
ORANGE = "#f9760b"
DARK = "#202225"
CREAM = "#faf8f4"


def sha(data):
    return hashlib.sha256(data).hexdigest()


def font(size, bold=True):
    return ImageFont.truetype(FONT if bold else REGULAR, size)


def watermark(source, output, mask_output):
    with Image.open(source) as im:
        im.load()
        assert getattr(im, "n_frames", 1) == 1
        assert im.getexif().get(274, 1) == 1
        rgb = im.convert("RGB")
        icc = im.info.get("icc_profile")
        alpha = im.convert("RGBA").getchannel("A") if "A" in im.mode or "transparency" in im.info else None
    w, h = rgb.size
    info_panel = source.name.endswith("_2.png")
    opacity = 44 if info_panel else 82
    overlay_color = "black" if info_panel else "white"
    f = font(max(12, round(42 * min(w, h) / 960)))
    bb = f.getbbox(PHRASE)
    stamp = Image.new("L", (bb[2] - bb[0] + 12, bb[3] - bb[1] + 12), 0)
    ImageDraw.Draw(stamp).text((6 - bb[0], 6 - bb[1]), PHRASE, font=f, fill=opacity)
    stamp = stamp.rotate(24, resample=Image.Resampling.BICUBIC, expand=True)
    mask = Image.new("L", (w, h), 0)
    centers = [(0.5625, 0.30078125), (0.28125, 0.5703125), (0.7291666667, 0.68359375)]
    if source.name == "03-b64f27f6d0.jpeg":
        centers = [(0.60, 0.17), (0.19, 0.56), (0.66, 0.91)]
    if info_panel:
        centers = [(0.73,0.68),(0.30,0.91),(0.75,0.12)]
    # Keep all visible OEM label areas exactly unchanged, not just readable.
    exclusions = []
    if source.name.endswith("_1.png") or source.name.endswith("_2.png"):
        exclusions.append((0.06, 0.17, 0.49, 0.51))
    if info_panel:
        exclusions.append((0.51,0.19,0.96,0.53))
    if source.name == "0AM927769D_2.jpg":
        exclusions.append((0.78, 0.69, 0.98, 0.97))
    exclusions += {
        "01-8824052d56.jpeg": [(0.54, 0.30, 0.81, 0.69)],
        "02-912c0f46f4.jpeg": [(0.57, 0.00, 0.99, 0.34)],
        "03-b64f27f6d0.jpeg": [(0.32, 0.43, 0.80, 0.77)],
        "05-e13494ab83.jpeg": [(0.50, 0.55, 0.86, 0.82)],
    }.get(source.name, [])
    blocked = [(int(x1*w),int(y1*h),int(x2*w),int(y2*h)) for x1,y1,x2,y2 in exclusions]
    def intersects(a,b):
        return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]
    candidates = centers + [(fx,fy) for fy in (.16,.30,.45,.60,.76,.90) for fx in (.25,.50,.75)]
    placed=[]
    centers=[]
    for fx,fy in candidates:
        x=max(0,min(w-stamp.width,round(fx*w-stamp.width/2)))
        y=max(0,min(h-stamp.height,round(fy*h-stamp.height/2)))
        rect=(x,y,x+stamp.width,y+stamp.height)
        if any(intersects(rect,r) for r in blocked+placed):
            continue
        mask.paste(stamp,(x,y),stamp.point(lambda p:255 if p else 0))
        placed.append(rect)
        centers.append((fx,fy))
        if len(placed)==3:
            break
    assert len(placed)==3, f"Cannot place three complete watermarks: {source.name}"
    result = Image.composite(Image.new("RGB", rgb.size, overlay_color), rgb, mask)
    if alpha is not None:
        result.putalpha(alpha)
    kwargs = {"icc_profile": icc} if icc else {}
    result.save(output, format="PNG", **kwargs)
    mask.save(mask_output, format="PNG")
    with Image.open(output) as check:
        check.load()
        assert check.size == rgb.size and check.format == "PNG"
        outside = mask.point(lambda x: 0 if x else 255)
        diff = ImageChops.difference(check.convert("RGB"), rgb)
        outside_diff = ImageChops.multiply(diff, Image.merge("RGB", [outside] * 3))
        assert outside_diff.getbbox() is None
        assert diff.getbbox() is not None
        assert check.info.get("icc_profile") == icc
        if alpha is not None:
            assert ImageChops.difference(check.getchannel("A"), alpha).getbbox() is None
    return {
        "dimensions": [w, h], "outside_mask_identical": True,
        "png_lossless_verified": True, "alpha_preserved": True,
        "color_profile_preserved": True, "opacity": opacity / 255,
        "watermark_color": overlay_color,
        "angle": 24, "stamp_centers": centers,
        "label_exclusion_rectangles": exclusions,
        "mask_pixels": sum(mask.histogram()[1:]),
        "mask_sha256": sha(mask_output.read_bytes()),
    }


def fit_inside(canvas, image, rect, upscale=True):
    x, y, w, h = rect
    im = image.convert("RGBA")
    size = ImageOps.contain(im, (w, h), Image.Resampling.LANCZOS).size
    if not upscale and (size[0] > im.width or size[1] > im.height):
        size = im.size
    if size != im.size:
        im = im.resize(size, Image.Resampling.LANCZOS)
    position = (x + (w - im.width) // 2, y + (h - im.height) // 2)
    canvas.paste(im.convert("RGB"), position, im.getchannel("A"))
    return {"position": list(position), "dimensions": list(im.size), "source_dimensions": list(image.size), "cropped": False}


def header(canvas, title, subtitle=None):
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 0, canvas.width, 144), fill=CREAM)
    d.rectangle((0, 144, canvas.width, 158), fill=ORANGE)
    d.text((48, 28), title, font=font(58), fill=DARK)
    if subtitle:
        d.text((48, 96), subtitle, font=font(25, False), fill="#555555")


def contact_sheet(paths, target, label_names=True):
    cw, ch, cols = 400, 370, 3
    canvas = Image.new("RGB", (cols*cw, ((len(paths)+cols-1)//cols)*ch), "#eeeeee")
    d = ImageDraw.Draw(canvas)
    for i, path in enumerate(paths):
        x, y = (i % cols)*cw, (i // cols)*ch
        with Image.open(path) as im:
            fit_inside(canvas, im, (x+5, y+30, cw-10, ch-35), upscale=False)
        if label_names:
            d.text((x+8,y+5),path.name,font=font(16),fill=DARK)
    canvas.save(target, quality=93)


def build(args):
    mapping = json.loads(args.source_map.read_text())
    out = args.output
    for name in ("original", "protected", "masks", "gallery", "quarantine"):
        (out/name).mkdir(parents=True,exist_ok=True)
    journal = {"schema_version": 1, "no_generation": True, "source_count": len(mapping["sources"]), "sources": [], "gallery": [], "quarantined": [], "renderer": {"pillow_version":PILLOW_VERSION,"font_sha256":{Path(p).name:sha(Path(p).read_bytes()) for p in (FONT,REGULAR)}}}
    protected = {}
    for row in mapping["sources"]:
        src = (args.source_dir / row["filename"]) if args.source_dir else (args.repo_root / row["repository_path"])
        data = src.read_bytes()
        assert sha(data) == row["sha256"], f"Source changed: {src}"
        dest = out/"original"/row["filename"]
        if src.resolve() != dest.resolve():
            dest.write_bytes(data)
        pname = Path(row["filename"]).stem + ("-avito" if row["filename"].endswith(".jpg") else "") + "-wm.png"
        target = out/"protected"/pname
        mask_file = out/"masks"/(pname+".mask.png")
        proof = watermark(dest,target,mask_file)
        rec = {**row, "original_path": str(dest.relative_to(out)), "protected_path": str(target.relative_to(out)), "protected_sha256": sha(target.read_bytes()), "mask_path":str(mask_file.relative_to(out)), "proof":proof,"gallery_positions":[]}
        journal["sources"].append(rec)
        protected[row["filename"]] = target
        if not row["publish"]:
            q = out/"quarantine"/pname
            shutil.copyfile(target,q)
            journal["quarantined"].append({"source":row["repository_path"],"file":str(q.relative_to(out)),"reason":row["quarantine_reason"]})

    def save_gallery(name, canvas, uses, placements=None, layout=None):
        target = out/"gallery"/name
        canvas.save(target, format="PNG")
        index = len(journal["gallery"])+1
        journal["gallery"].append({"position":index,"path":str(target.relative_to(out)),"sha256":sha(target.read_bytes()),"bytes":target.stat().st_size,"dimensions":list(canvas.size),"sources":uses,"placements":placements or [],"layout":layout,"watermarks":"Every included source is watermarked before composition."})
        for src in journal["sources"]:
            if src["filename"] in uses:
                src["gallery_positions"].append(index)

    # Main image: the actual D photograph, explicit variant label and full range.
    canvas=Image.new("RGB",(1600,1200),CREAM); d=ImageDraw.Draw(canvas)
    d.text((56,36),PHRASE,font=font(32),fill=ORANGE)
    d.text((56,93),"ПЛАТЫ МЕХАТРОНИКА",font=font(76),fill=DARK)
    d.text((58,190),"DSG7 DQ200",font=font(43),fill=DARK)
    d.rectangle((56,258,1544,266),fill=ORANGE)
    with Image.open(protected["01-8824052d56.jpeg"]) as im:
        placement=fit_inside(canvas,im,(56,292,1030,772))
    d.text((56,1087),"На фото: 0AM927769D",font=font(32),fill=DARK)
    d.text((1130,303),"ИСПОЛНЕНИЯ",font=font(30),fill=DARK)
    for i,letter in enumerate("DEFGK"):
        y=370+i*122
        if letter=="D":
            d.rounded_rectangle((1114,y-12,1544,y+78),radius=12,fill=ORANGE)
        d.text((1135,y),f"0AM927769{letter}",font=font(39),fill="white" if letter=="D" else DARK)
    d.text((1133,1017),"Подбор по маркировке",font=font(24,False),fill="#555555")
    save_gallery("01-all-boards-cover.png",canvas,["01-8824052d56.jpeg"],[placement],"Actual D photo plus all five exact SKU labels; no price shown.")

    for letter in "DEFGK":
        names=[f"0AM927769{letter}_{i}.png" for i in range(1,4) if f"0AM927769{letter}_{i}.png" != "0AM927769K_1.png"]
        if letter=="D": names.append("0AM927769D_2.jpg")
        if len(names)==4:
            canvas=Image.new("RGB",(3280,3500),CREAM)
            rects=[(40,190,1560,1600),(1680,190,1560,1600),(40,1840,1560,1600),(1680,1840,1560,1600)]
        else:
            canvas=Image.new("RGB",(len(names)*1240+40,1810),CREAM)
            rects=[(40+i*1240,190,1200,1600) for i in range(len(names))]
        header(canvas,f"0AM927769{letter}","Электронная плата DSG7 DQ200 · собственные фотографии и информация")
        placements=[]
        for name,rect in zip(names,rects):
            with Image.open(protected[name]) as im:
                placements.append({"source":name,**fit_inside(canvas,im,rect)})
        save_gallery(f"0{len(journal['gallery'])+1}-0AM927769{letter}.png",canvas,names,placements,"Whole catalog originals; no crops, aspect ratio preserved.")

    names=["01-8824052d56.jpeg","02-912c0f46f4.jpeg"]
    canvas=Image.new("RGB",(1360,2140),CREAM)
    header(canvas,"0AM927769D", "Общий вид и детали корпуса")
    placements=[]
    for i,name in enumerate(names):
        with Image.open(protected[name]) as im:
            placements.append({"source":name,**fit_inside(canvas,im,(40,190+i*960,1280,960),False)})
    save_gallery("07-0AM927769D-overview-details.png",canvas,names,placements,"Two full-resolution original D photographs.")
    for position,name in [(8,"03-b64f27f6d0.jpeg"),(9,"04-66c3d72cca.jpeg"),(10,"05-e13494ab83.jpeg")]:
        with Image.open(protected[name]) as im:
            canvas=im.copy()
        save_gallery(f"{position:02}-0AM927769D-original.png",canvas,[name],layout="Original pixel dimensions, watermark only.")

    journal["unique_source_count"]=len({r["sha256"] for r in journal["sources"]})
    journal["eligible_source_count"]=sum(r["publish"] for r in journal["sources"])
    journal["gallery_count"]=len(journal["gallery"])
    assert journal["gallery_count"]==10
    assert all(r["gallery_positions"] for r in journal["sources"] if r["publish"])
    assert all(not r["gallery_positions"] for r in journal["sources"] if not r["publish"])
    journal["all_eligible_sources_in_gallery"]=True
    journal["all_originals_retained"]=True
    journal["all_individuals_watermarked"]=True
    (out/"manifest.json").write_text(json.dumps(journal,ensure_ascii=False,indent=2)+"\n")
    contact_sheet([out/r["path"] for r in journal["gallery"]],out/"gallery_contact.jpg")
    contact_sheet(list(protected.values()),out/"protected_contact.jpg")
    print(json.dumps({k:journal[k] for k in ("source_count","unique_source_count","eligible_source_count","gallery_count","all_eligible_sources_in_gallery")},ensure_ascii=False))


if __name__ == "__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument("--repo-root",type=Path,default=Path.cwd())
    parser.add_argument("--source-dir",type=Path)
    parser.add_argument("--source-map",type=Path,default=Path(__file__).with_name("source_map.json"))
    parser.add_argument("--output",type=Path,required=True)
    build(parser.parse_args())
