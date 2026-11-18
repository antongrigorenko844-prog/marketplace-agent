"""Isolated, read-only preview of one Tilda product review section.
No writes to Tilda, no fabricated customer reviews.
"""
import csv,html,json,xml.etree.ElementTree as ET,sys
sys.path.insert(0,'.')
from pathlib import Path
SKU="0AM325025H"
src=Path("data/tilda_feed.csv")
with src.open(encoding="utf-8-sig",newline="") as f:
    rows=list(csv.DictReader(f,delimiter=";"))
matches=[x for x in rows if (x.get("SKU") or "").strip().casefold()==SKU.casefold()]
if len(matches)!=1:raise RuntimeError(f"Expected exactly one Tilda product {SKU}, found {len(matches)}")
item=matches[0]
original=item.get("Text") or ""
if not original.strip():raise RuntimeError("Original description empty; refusing to create replacement")
heading="Отзывы покупателей на Ozon"
review="★★★★★ | 21 сентября | Суетолог\\nлето на нём откатал проблем небыло! жду зиму!"
test_note=review
preview=original+"\n\n"+heading+"\n"+test_note
out=Path("data/tilda_review_one_product_preview.json")
out.write_text(json.dumps({"mode":"PREVIEW_ONLY","sku":SKU,"tilda_uid":item.get("Tilda UID"),"title":item.get("Title"),"original_description":original,"proposed_description":preview,"review_verified":True,"tilda_write":False,"other_products_modified":0,"note":"Real review supplied by owner screenshot; preview only, no publication"},ensure_ascii=False,indent=2),encoding="utf-8")
# Exercise existing CommerceML serializer with one item, without uploading.
import tilda_commerceml
xml=tilda_commerceml.build_import_xml([{"id":SKU,"title":item.get("Title") or SKU,"description":preview,"price":1,"qty":None}])
root=ET.fromstring(xml)
products=root.findall(".//Товар")
assert len(products)==1 and products[0].findtext("Артикул")==SKU
assert products[0].findtext("Описание")==preview
assert not root.findall(".//Картинка")
# Local HTML visual preview only; no Tilda write.
html_preview = """<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Leoparts — тест карточки</title><style>body{font-family:Arial,sans-serif;background:#f3f4f6;margin:0;padding:24px;color:#202124}.popup{max-width:520px;margin:24px auto;background:white;border-radius:16px;padding:24px;box-shadow:0 8px 28px #0002}h1{font-size:23px}.reviews{border-top:1px solid #ddd;margin-top:24px;padding-top:18px}.source{color:#005bff;font-weight:bold}.notice{font-size:13px;color:#666}.buy{background:#16803c;color:white;border-radius:8px;padding:15px;text-align:center;font-weight:bold;margin-top:16px}.placeholder{padding:15px;border:1px dashed #bbb;border-radius:8px;color:#666}</style><div class="popup"><h1>""" + html.escape(item.get("Title") or SKU) + """</h1><p class="notice">Артикул """+SKU+""" · визуальный тест, не опубликовано</p><div class="buy">Купить</div><section class="reviews"><h2>Отзывы покупателей</h2><p class="source">Источник: Ozon</p><div class="placeholder">★★★★★ · Суетолог · 21 сентября<br>лето на нём откатал проблем небыло! жду зиму!</div></section></div></html>"""
Path("data/tilda_review_one_product_preview.html").write_text(html_preview,encoding="utf-8")
print(f"PASS preview SKU={SKU}, title={item.get('Title')}, UID={item.get('Tilda UID')}")
print("PASS: single product CommerceML XML, no photos, no offer/price upload, no Tilda write")
print("PASS: review text supplied by owner, author Суетолог; preview only")
