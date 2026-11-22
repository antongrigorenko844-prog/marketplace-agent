"""Read-only export low-resolution preview of existing LEOPARTS Gen1 card designs
as base64 text so assistant can inspect source rather than invent the product.
No marketplace writes.
"""
import base64, io, json
from pathlib import Path
from PIL import Image
root=Path(__file__).resolve().parents[1]
out=root/"data"/"6t30_gen1_preview_20261123"
out.mkdir(parents=True,exist_ok=True)
report={}
for n in (1,2):
    p=root/"photos"/"6t30"/f"6t30_{n}.png"
    if not p.exists():raise RuntimeError("Missing 6t30 sample image "+str(p))
    im=Image.open(p).convert("RGB")
    dims=im.size
    im.thumbnail((700,700),Image.Resampling.LANCZOS)
    b=io.BytesIO();im.save(b,format="JPEG",quality=55,optimize=True)
    coded=base64.b64encode(b.getvalue()).decode("ascii")
    (out/f"gen1_{n}.b64").write_text(coded,encoding="utf-8")
    report[f"gen1_{n}"]={"original":dims,"preview":im.size,"bytes":len(b.getvalue()),"b64":len(coded)}
(out/"report.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
print(json.dumps(report,ensure_ascii=False))
