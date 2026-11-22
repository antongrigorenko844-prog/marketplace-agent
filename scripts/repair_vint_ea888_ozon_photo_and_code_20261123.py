"""Read-only generate a small preview of the real EA888 M6x65 screw and lookup
generic Ozon customs values; do not guess screw head/metal from product title."""
import base64,json,io,sys
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client
PHOTO=ROOT/"photos/vint-kollektora-ea888/vint-kollektora-ea888_1.png"
OUT=ROOT/"data/repair_vint_ea888_photo_and_code_20261123.json"
BASE64=ROOT/"data/repair_vint_ea888_photo_20261123.b64"
im=Image.open(PHOTO).convert("RGB")
original=im.size
im.thumbnail((560,560),Image.Resampling.LANCZOS)
bio=io.BytesIO();im.save(bio,"JPEG",quality=58,optimize=True)
BASE64.write_text(base64.b64encode(bio.getvalue()).decode("ascii"),encoding="utf-8")
r={"photo_source":str(PHOTO.relative_to(ROOT)),"original_pixels":original,
   "preview_pixels":im.size,"preview_bytes":len(bio.getvalue()),"code_matches":{}}
for q in ["731815900","7318159","7318156900","731815","731819"]:
    try:
        vals=ozon_client.search_attribute_values(74606916,970858643,22232,q,limit=80)
        r["code_matches"][q]=[{"id":x.get("id"),"value":x.get("value")} for x in vals[:45]]
    except Exception as exc:r["code_matches"][q]={"error_type":type(exc).__name__}
OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
print(json.dumps({"image":r["preview_pixels"],"preview_bytes":r["preview_bytes"],
    "query_counts":{k:len(v) if isinstance(v,list) else v for k,v in r["code_matches"].items()}},ensure_ascii=False),flush=True)
