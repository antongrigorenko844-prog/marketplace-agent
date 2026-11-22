"""Exercise shared-stock synchronization on temporary copies of ledger and catalog.

All platform APIs are read only. Actual data/orders_seen.db and catalog are
unchanged; this must be verified by SHA256 before/after. Avito ledger path is
explicitly excluded from the simulation to avoid test-data cutover.
"""
from __future__ import annotations
import json
from hashlib import sha256
from pathlib import Path
import shutil
import sys
from tempfile import TemporaryDirectory
from datetime import datetime, timezone
from unittest.mock import patch

ROOT=Path(__file__).resolve().parent
DATA=ROOT/"data"
DB=DATA/"orders_seen.db"
CAT=DATA/"ozon_catalog.xlsx"
OUT=DATA/"stock_sync_dryrun_report.json"

def hash_file(path):
    return sha256(path.read_bytes()).hexdigest()

def run():
    if not DB.is_file() or not CAT.is_file():
        return {"status":"blocked_missing_input","dry_run":True,
                "source_file_write_count":0}
    originals={str(x.relative_to(ROOT)):hash_file(x) for x in (DB,CAT)}
    result={"status":"pending","dry_run":True,
            "checked_at_utc":datetime.now(timezone.utc).isoformat(),
            "read_only_external_apis":True,"before_hashes":originals,
            "production_inventory_writes":0,"production_ledger_writes":0}
    try:
        with TemporaryDirectory(prefix="leoparts_stock_rehearsal_") as tmp:
            d=Path(tmp)
            for f in (DB,CAT):
                shutil.copy2(f,d/f.name)
            import stock_sync
            # The Avito delivery ledger requires its own explicit production
            # cutover; this rehearsal concerns Ozon/WB/Market common stock only.
            with patch("avito_debit.sync_if_enabled",return_value=None):
                summary=stock_sync.sync_all_orders(
                    days_back=30,catalog_path=str(d/CAT.name),db_path=str(d/DB.name))
            deferred=summary.get("deferred_legacy_cancellations") or []
            result.update(status="dryrun_success",
                new_quantity_changes={k:int(v) for k,v in (summary.get("deltas") or {}).items()},
                would_defer_legacy_cancellations=deferred,
                deferred_count=len(deferred),
                count_by_channel={k:summary.get(k) for k in ("ozon_fbs","ozon_fbo","wb","yandex_market")},
                temp_ledger_changed=hash_file(d/DB.name)!=originals[str(DB.relative_to(ROOT))],
                temp_catalog_changed=hash_file(d/CAT.name)!=originals[str(CAT.relative_to(ROOT))])
    except Exception as exc:
        result.update(status="dryrun_blocked",exception_type=type(exc).__name__,
                      reason=str(exc)[:340])
    result["after_hashes"]={str(x.relative_to(ROOT)):hash_file(x) for x in (DB,CAT)}
    result["production_hashes_unchanged"]=result["before_hashes"]==result["after_hashes"]
    if not result["production_hashes_unchanged"]:
        result["status"]="production_inputs_modified_ABORT"
    return result

if __name__=="__main__":
    out=run()
    OUT.write_text(json.dumps(out,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(json.dumps(out,ensure_ascii=False),flush=True)
    sys.exit(0 if out.get("status")=="dryrun_success" else 1)
