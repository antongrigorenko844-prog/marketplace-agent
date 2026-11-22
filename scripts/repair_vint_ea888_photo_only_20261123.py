"""Read-only preview of actual EA888 intake M6x65 screw photo for customs-code choice."""
import base64,io,json
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[1]
p=ROOT/"photos/vint-kollektora-ea888/vint-kollektora-ea888_1.png"
im=Image.open(p).convert("RGB")
orig=im.size
im.thumbnail((760,760),Image.Resampling.LANCZOS)
buf=io.BytesIO();im.save(buf,"JPEG",quality=65,optimize=True)
src=ROOT/"data/repair_vint_ea888_photo_only_20261123.b64"
src.write_text(base64.b64encode(buf.getvalue()).decode("ascii"),encoding="utf-8")
print(json.dumps({"file":str(p.relative_to(ROOT)),"original":orig,"preview":im.size,"bytes":buf.tell()}))
