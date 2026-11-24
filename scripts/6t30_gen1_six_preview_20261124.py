"""Copy the generated numeral 6 onto the previously approved exact Gen1 source.
Do not modify any pixels outside the numeral's small bounding rectangle.
Preview only; publication is separately scoped.
"""
import base64,hashlib,io,json
from collections import deque
from pathlib import Path
from PIL import Image,ImageChops,ImageStat
ROOT=Path(__file__).resolve().parents[1]
SRC=ROOT/'assets/product-intake-v2/bridge/6t30-gen1-cover-20261124.png'
DEST=ROOT/'assets/product-intake-v2/bridge/6t30-gen1-six-20261124.png'
REPORT=ROOT/'data/6t30_gen1_six_preview_20261124.json'
OLD='6d5af9507dd8bb6eaabf26a3b2b9c2084d2f82b8cbd9c906503df951fddc296d'
GLYPH='iVBORw0KGgoAAAANSUhEUgAAACsAAAA0CAAAAAA2tp5SAAAEpklEQVR42sXW228bRRQH4N/MzuzFXl8T20kc2rRpEqKWFKkIgVCLgAoJgXjgD+WFB24qULXloaVFQSmUtiFJYztxYjtxst7rXHjYJI6rVPAE+zj6dHb2zDlzFvj/H3LWIuVQQv8LSyxOTSittEqEAIh+pbW4k7PL5aLFlQ4Gu1tdX0CcbTN2oXxhfm7CNQ1AxJ3mk5WnXpgAANgodTPjV9+arVZzjmkBQNLbWLt1p6kFABgjlGcv3vj47flq1jVNAIDh1sZrViMQ+mXLsjOfXV+YznNOCT3OSY67/aZIXrb2+c+vX5pwLAokUuKIW1pt7MSjltjVL25errmUyODwYM/zoDgAaHPQWwvVyLex8U8/ulIFaOi1XzT2YdXnKpMAiFWZqXRG8kCspQ+uVAEg+Oveo9UDZAvX3xOTLEkCwymN5oxNv7s4CQCdP376alMapL/dsalkIgnaMR2xdv79pToAhOu3v2zpRFHtHD4220wPgv3VLj1trcvX5osAsLn87ZpPTc0QOqteIQyCQOxtx0NLnNzibBkA9lZvPQ8Ty4BWWrY7iJKIRInSQ2uY5xcqLgA0fv/Fi4kShjIgZQxKSayUGO6X2u7CXDEDINr6tUdYNssMriC0JrGMQgk5tIZTvlAtAsBhu1MujlWLrgk/6Gx3fUBKIvUpa01P5m0A8MLCJzNTpaLDk2iw31ldbvQZSykYAEIoHa84HACM8sfTEyXXMSAT6R3MLtx7FMEbWlAjVx93AQC5y6rq2gDAbeTHp+pZI94+aggGAKZTmshlAQDFrDTpSY/aNo8HvUEw7AtC7VLJ5kflTmQ8EDAtMw1TuHh1vUfVSVzDrKXJBeDvd7utPbPwWqlcBcDz5dlzL7wTSw1zrJAHAIhe89lys9nF1JWlS24GQKZcm3KYGOYh63ILAMTWswcPHvf8iDdWt3A+A4C5mXLGio7PWFIz6zAA6Dfuf7O5m0Q6Ubh7caEGANlsMaPIsB6YyQFA+M2HL7xQaCAU6ulumgyTQCsAoAA04eltIPaajSTUBABEvD9IU0UhFI4ttMENAJBhe9+XShMAVBOanoGMo3hoiUHS7PqdKA4VNADu5PPpBkMvlieWUGUc9b5IhIYGQIzceCZdCw5CfWK11FIBgCQM6YuJVRg7VwIAJEHXxyk7OPQBgPEs5+mu7OziXFohnZ2Nw6GF9AY+ANDCRM6yAZBMvvLGhSoAiPZaSw4tSbxON70Vz13LW4xSp1i7+U7dAoD+9sqOGtakVuF2uzsG8MLrMVnuJ9SZuPHh3BgAhJvPf4twqn511Gy2XAtw6+bY0qbHCotXp0oAIJp/3m2K0zYebDyZyVcyYGW3/mZHIF9xbAA4aD+/83AgR+LG/ZWZgi46BmOZ4lTaFyGRorN+++dedDyI0jNQJCI5zilnx+NGRSrydta//2Hz4Djs8V2diN6AKkL5UQ0kURS2Nla+/rHRF3jJArof+MIPEhEnwvf2+93W2v3vHvT8+Iy5SfK5wvx0vVZwTCOODjyv9Xj1MDhUZ89YyzadWiWXYTT2/cFu3A+D6NXzmFjMYAY00bEEQvEPs5uBQCto/R/+a/wNvdAWCqkE+wcAAAAASUVORK5CYII='
def thumb(im):
    o=io.BytesIO();im.save(o,'JPEG',quality=83);return base64.b64encode(o.getvalue()).decode()
def main():
    assert hashlib.sha256(SRC.read_bytes()).hexdigest()==OLD
    a=Image.open(SRC).convert('RGB');w,h=a.size;assert a.size==(1052,1494)
    pix=a.load()
    pts=set()
    for y in range(int(.225*h),int(.288*h)):
        for x in range(int(.552*w),int(.735*w)):
            r,g,b=pix[x,y]
            if r-max(g,b)>50 and r>85:pts.add((x,y))
    groups=[]
    while pts:
        p=pts.pop();q=[p];part=[p]
        while q:
            x,y=q.pop()
            for nx,ny in [(x+1,y),(x-1,y),(x,y+1),(x,y-1)]:
                if (nx,ny) in pts:pts.remove((nx,ny));q.append((nx,ny));part.append((nx,ny))
        if len(part)>100:groups.append(part)
    boxes=sorted([(min(x for x,y in g),min(y for x,y in g),max(x for x,y in g)+1,max(y for x,y in g)+1,len(g)) for g in groups])
    assert len(boxes)>=2,boxes
    x0,y0,x1,y1,n=boxes[0]
    assert 590<=x0<=645 and 335<=y0<=390 and 19<=x1-x0<=42 and 25<=y1-y0<=56,boxes
    assert boxes[1][0]>x1+5,boxes
    # Isolate the numeral; adjacent text is explicitly outside the changed region.
    left,top,right,bottom=x0-4,y0-4,min(x1+7,boxes[1][0]-3),y1+4
    out=a.copy();p=out.load()
    for y in range(top,bottom):
        l=pix[left-1,y];r=pix[right,y]
        for x in range(left,right):
            f=(x-left+1)/(right-left+1);p[x,y]=tuple(round(u*(1-f)+v*f) for u,v in zip(l,r))
    mask=Image.open(io.BytesIO(base64.b64decode(GLYPH))).convert('L')
    mask=mask.point(lambda z:0 if z<13 else z)
    box=mask.getbbox();mask=mask.crop(box)
    hh=y1-y0+2;ww=round(mask.width/mask.height*hh)
    assert ww<=right-left-2
    mask=mask.resize((ww,hh),Image.Resampling.LANCZOS)
    cols=[pix[x,y] for x,y in groups[[min(x for x,y in g) for g in groups].index(x0)] if pix[x,y][0]-max(pix[x,y][1:])>110]
    color=tuple(sorted(c[k] for c in cols)[len(cols)//2] for k in range(3)) if cols else (197,18,29)
    xx=x0+(x1-x0-ww)//2;yy=y0-1
    xx=max(left,xx)
    assert xx+ww<=right and yy+hh<=bottom
    out.paste(color,(xx,yy,xx+ww,yy+hh),mask)
    bbox=ImageChops.difference(a,out).getbbox()
    assert bbox and bbox[0]>=left and bbox[1]>=top and bbox[2]<=right and bbox[3]<=bottom
    out.save(DEST,'PNG',optimize=True)
    view=out.copy();view.thumbnail((300,430))
    report={'sku':'6t30','status':'preview_only','source_sha256':OLD,'sha256':hashlib.sha256(DEST.read_bytes()).hexdigest(),'changed_bbox':list(bbox),'components':boxes,'dimensions':list(out.size),'exact_other_pixels':True,'source_method':'original approved image + numeral copied from latest generated image','preview_jpeg_base64':thumb(view),'detail_jpeg_base64':thumb(out.crop((560,300,800,425)).resize((480,250)))}
    REPORT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
if __name__=='__main__':main()
