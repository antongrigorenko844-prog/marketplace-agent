"""Isolated, read-only preview of one Tilda product review section.
No writes to Tilda, no fabricated customer reviews.
"""
import csv,html,json,xml.etree.ElementTree as ET,sys
sys.path.insert(0,'.')
from pathlib import Path
SKU="0AM325025H"
src=Path("data/tilda_export.csv")
with src.open(encoding="utf-8-sig",newline="") as f:
    rows=list(csv.DictReader(f,delimiter=";"))
matches=[x for x in rows if (x.get("SKU") or "").strip().casefold()==SKU.casefold()]
if len(matches)!=1:raise RuntimeError(f"Expected exactly one Tilda product {SKU}, found {len(matches)}")
item=matches[0]
original=item.get("Text") or ""
if not original.strip():raise RuntimeError("Original description empty; refusing to create replacement")
heading="Отзывы покупателей на Ozon"
test_note="ТЕСТОВЫЙ БЛОК. Настоящий отзыв ещё не добавлен."
preview=original+"\n\n"+heading+"\n"+test_note
out=Path("data/tilda_review_one_product_preview.json")
out.write_text(json.dumps({"mode":"PREVIEW_ONLY","sku":SKU,"tilda_uid":item.get("Tilda UID"),"title":item.get("Title"),"original_description":original,"proposed_description":preview,"review_verified":False,"tilda_write":False,"other_products_modified":0,"note":"No real review text available; DO NOT publish placeholder"},ensure_ascii=False,indent=2),encoding="utf-8")
# Exercise existing CommerceML serializer with one item, without uploading.
import tilda_commerceml
xml=tilda_commerceml.build_import_xml([{"id":SKU,"title":item.get("Title") or SKU,"description":preview,"price":1,"qty":None}])
root=ET.fromstring(xml)
products=root.findall(".//Товар")
assert len(products)==1 and products[0].findtext("Артикул")==SKU
assert products[0].findtext("Описание")==preview
assert not root.findall(".//Картинка")
print(f"PASS preview SKU={SKU}, title={item.get('Title')}, UID={item.get('Tilda UID')}")
print("PASS: single product CommerceML XML, no photos, no offer/price upload, no Tilda write")
print("BLOCKED publication: authentic Ozon review text and source not yet provided")
