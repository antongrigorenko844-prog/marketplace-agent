"""Read Wildberries asynchronous new-card validation errors for three exact LEOPARTS SKUs.
No writes, no retries that could create duplicates.
"""
import json,sys
from pathlib import Path
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import wb_client
SKUS={"6t30-solenoids-gen2","6t30-tcm-gen1","6t30-tcm-gen2"}
out={"read_only":True,"timestamp":datetime.now(timezone.utc).isoformat(),"errors":{}}
try:
  errors=wb_client.get_upload_errors(limit=100)
  out["returned_error_batches"]=len(errors)
  for row in errors:
    text=json.dumps(row,ensure_ascii=False)
    for s in SKUS:
      if s in text:out["errors"].setdefault(s,[]).append({
        "vendorCodes":row.get("vendorCodes"),"errors":row.get("errors"),"updatedAt":row.get("updatedAt")})
except Exception as e:out["api_error_type"]=type(e).__name__
try:
  cards=wb_client.list_cards(limit=100)
  out["cards_total"]=len(cards)
  out["cards"]=[{"nmID":q.get("nmID"),"vendorCode":q.get("vendorCode"),"title":q.get("title")}
    for q in cards if q.get("vendorCode") in SKUS]
except Exception as e:out["cards_error_type"]=type(e).__name__
Path("data/6t_family_20261123_wb_errors_result.json").write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n")
print(json.dumps(out,ensure_ascii=False))
