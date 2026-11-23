"""Compress read-only live Tilda product snapshot for user Excel creation.

This writes ONLY a transport text payload of existing public data. No spreadsheet
edits, no site or marketplace writes.
"""
from __future__ import annotations
import json,gzip,base64
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
LIVE=ROOT/"data/site_price_review_live_20261124.json"
AUD=ROOT/"data/avito_price_audit_20261124.json"
OUT=ROOT/"data/site_price_review_compact_20261124.b64"
def run():
  site=json.loads(LIVE.read_text(encoding="utf-8"))
  audit=json.loads(AUD.read_text(encoding="utf-8"))
  if site["status"]!="success" or site["stats"]["public_rows"]<100:
    raise RuntimeError("No successful live Tilda snapshot")
  bysku={p["sku"]:p for p in audit["products"] if p.get("sku") and p.get("name")}
  collision={q for group in site["stats"]["case_sensitive_collision_groups"] for q in group}
  duplicates=set(site["stats"]["duplicate_sku"])
  records=[]
  for row in site["rows"]:
    sku=row["sku"]
    prod=bysku.get(sku,{})
    source=site["source_override_prices"].get(sku)
    if source is None:source=prod.get("site_cfg")
    url=row.get("url") or ""
    if not (url.startswith("https://leoparts-akpp.ru/") and "/tproduct/" in url):
      raise RuntimeError("Missing trusted Tilda product URL "+sku)
    records.append([row["uid"],sku,row["title"],row["price"],
      source,prod.get("cost_master"),prod.get("ozon_catalog"),
      url,row["externalid"],int(sku in duplicates),int(sku in collision),
      int(row["price"]!=source),len(row.get("editions") or []),
      prod.get("name"),int(sku in site["site_only_prices"])])
  if len(records)!=150 or len({v[0] for v in records})!=150:
    raise RuntimeError("Unexpected product count or duplicated UID")
  compact={"snapshot_utc":site["date_utc"],"source":site["source"],
     "stats":site["stats"],"rows":records,
     "columns":["tilda_uid","sku","live_title","live_price","source_price",
      "purchase_cost","ozon_catalog","direct_url","external_id",
      "duplicate_sku","case_sensitive_sku","live_source_mismatch",
      "editions_count","master_title","site_only_override"]}
  raw=json.dumps(compact,ensure_ascii=False,separators=(",",":")).encode()
  packed=base64.b64encode(gzip.compress(raw,compresslevel=9)).decode("ascii")
  OUT.write_text(packed+"\n",encoding="ascii")
  print("SITE_REVIEW_COMPACT",json.dumps({"rows":len(records),
    "raw_bytes":len(raw),"b64_bytes":len(packed),
    "duplicate_skus":list(duplicates),"collisions":len(collision)}))
if __name__=="__main__":run()
