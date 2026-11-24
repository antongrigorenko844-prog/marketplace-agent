"""Independent read-only final evidence; no product mutations."""
import json,datetime,sys,collections
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
import ozon_client as oz
from ozon_full_seo_20261125 import direct_read,one,ids,typ,values,attr,protected,check_row,good
from ozon_description_guard import differences
SRC=ROOT/"data/ozon_full_seo_20261125.json"
OUT=ROOT/"data/ozon_full_seo_readback_20261125.json"
d=json.loads(SRC.read_text())
r={"mode":"read_only","external_writes":0,"items":{},"errors":{},"started_utc":datetime.datetime.now(datetime.timezone.utc).isoformat()}
def save():OUT.write_text(json.dumps(r,ensure_ascii=False,indent=2,default=str)+"\n")
save()
for b in d["batches"]:
 for sku in b["items"]:
  row=d["registry"][sku]
  try:
   pid=row["product_id"];a=direct_read(sku,pid);i=one(oz.get_product_info_list,sku,pid);pr=one(oz.get_prices_and_stocks,sku);st=one(oz.get_stocks,sku)
   old=b["before"][sku] if b["kind"]=="attributes" else b["before"]
   ch=row["attribute_changes"] if b["kind"]=="attributes" else []
   before=(old["card"],old["info"],old["price"],old["stock"])
   checks=check_row(row,before,(a,i,pr,st),changes=ch,category=b["kind"]=="category")
   out={"checks":checks,"type":list(typ(a)),"moderation":i.get("statuses"),"actual_oem":values(a,7324),"marking_required_before":attr(old["card"],23536),"marking_required_after":attr(a,23536),"kiz_before":attr(old["card"],23173),"kiz_after":attr(a,23173),"kiz_implicit_before":23173 in (old["card"].get("attributes_with_defaults") or []),"kiz_implicit_after":23173 in (a.get("attributes_with_defaults") or []),"status":"verified" if good(checks) and (i.get("statuses") or {}).get("moderate_status")=="approved" else "review"}
   r["items"][sku]=out
  except Exception as e:r["errors"][sku]=str(e)[:500]
  save()
# Try current exact analytics access; no guessed conversions.
try:
 skus=[str(d["registry"][s]["ozon_sku"]) for s in ("0am325025","0AM927769D","0am325025H") if d["registry"].get(s,{}).get("ozon_sku")]
 r["query_analytics"]=oz._post("/v1/analytics/product-queries",{"date_from":"2026-11-18T00:00:00Z","date_to":"2026-11-24T00:00:00Z","skus":skus,"page":0,"page_size":100,"sort_by":"BY_SEARCHES","sort_dir":"DESCENDING"},retries=1)
except Exception as e:r["analytics_error"]=str(e)[:500]
r["finished_utc"]=datetime.datetime.now(datetime.timezone.utc).isoformat();save()
print(json.dumps({"statuses":dict(collections.Counter(x["status"] for x in r["items"].values())),"errors":r["errors"],"analytics_error":r.get("analytics_error")},ensure_ascii=False))
