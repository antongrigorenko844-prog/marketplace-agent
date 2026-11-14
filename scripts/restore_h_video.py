"""Restore the pre-dimensions H video via a video-attributes-only request."""
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ozon_client as oz

sku = "0am325025H"
source = "https://v-1.ozone.ru/vod/video-71/01M54NXGKTMXAD0Q7MSB330K7Z/asset_2_h264.mp4"
attrs = [
    {"id": 21837, "complex_id": 100001, "values": [{"dictionary_value_id": 0, "value": sku}]},
    {"id": 21841, "complex_id": 100001, "values": [{"dictionary_value_id": 0, "value": source}]},
]
out = Path("/tmp/package-dimensions-result")
out.mkdir(exist_ok=True)
def save(name, value):
    (out / (name + ".json")).write_text(json.dumps(value, ensure_ascii=False, indent=2))
def video(card):
    return [v.get("value") for a in card.get("complex_attributes", []) if a.get("id") == 21841 for v in a.get("values", []) if v.get("value")]
before, = oz.get_product_names([sku])
prices = oz.get_prices_and_stocks([sku])
stocks = oz.get_stocks([sku])
save("restore-before", before)
if not video(before):
    result = oz.update_attributes([{"offer_id": sku, "attributes": attrs}])
    save("restore-request-result", result)
    task = result.get("task_id") or result.get("result", {}).get("task_id")
    assert task, result
    for i in range(24):
        status = oz.get_import_status(task)
        print("RESTORE TASK", status, flush=True)
        save("restore-status", status)
        rows = status.get("result", {}).get("items", [])
        if rows and all(r.get("status") in ("imported", "failed") for r in rows):
            assert all(r.get("status") == "imported" for r in rows), status
            break
        time.sleep(15)
for i in range(24):
    after, = oz.get_product_names([sku])
    save("restore-after", after)
    if video(after):
        break
    time.sleep(15)
assert video(after), "Video restoration not yet confirmed"
assert [after[k] for k in ("depth", "width", "height")] == [400, 300, 300]
for k in ("name", "images", "primary_image", "weight", "type_id", "description_category_id"):
    assert before.get(k) == after.get(k), k
assert sorted(json.dumps(a,sort_keys=True) for a in before["attributes"]) == sorted(json.dumps(a,sort_keys=True) for a in after["attributes"])
final_prices = oz.get_prices_and_stocks([sku])
for k in ("price", "old_price", "min_price", "currency_code", "vat"):
    assert prices[0]["price"].get(k) == final_prices[0]["price"].get(k), k
save("restore-verification", {"video": video(after), "dimensions_mm": [after[k] for k in ("depth", "width", "height")], "prices_unchanged": True, "stocks_unchanged": stocks == oz.get_stocks([sku])})
print("VIDEO RESTORED", sku, video(after), "DIMENSIONS 400 300 300, PRICE UNCHANGED", flush=True)
