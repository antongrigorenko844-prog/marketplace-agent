"""Summarize verified vs submitted LEOPARTS channel outcomes. Read-only."""
from __future__ import annotations
import json
from pathlib import Path
from datetime import datetime, timezone
import sys

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUT = DATA / "leoparts_ops_status.json"
MARKDOWN = DATA / "leoparts_ops_status.md"

def get(path):
    try:
        value = json.loads((DATA/path).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}

def normalize_media(report):
    result = {}
    for channel, data in report.get("channels", {}).items():
        raw = data.get("status", "unknown")
        status = {"verified":"verified", "needs_update":"not_applied",
                  "pending":"pending_platform", "unverified":"not_verified",
                  "blocked":"failed"}.get(raw, "unknown")
        result[channel] = {"status":status, "sku":report.get("sku"),
                           "reason":data.get("reason", ""),
                           "source_verified":bool(raw == "verified"),
                           "price_written":False, "stock_written":False}
    return result

def normalize_avito(report):
    raw=report.get("result","")
    mapped={"already_published_no_upload":"feed_published_listing_unverified",
            "published_feed_waiting_avito_processing":"feed_published_listing_unverified",
            "skipped_autoload_not_confirmed_ready":"deferred_autoload_disabled",
            "blocked_remote_drift":"failed", "ready_for_apply":"not_applied",
            "ready_for_review":"not_applied", "error":"failed"}
    return {"status":mapped.get(raw,"unknown"), "sku":report.get("sku"),
            "source_verified":bool(report.get("repo_matches_published_feed")),
            "live_listing_photo_confirmed":False, "needs_autoload_check":True,
            "reason":report.get("error",""), "price_written":False, "stock_written":False}

def normalize_tilda(report):
    raw=report.get("status","")
    return {"status":{"verified":"verified","needs_csv_import":"manual_csv_import_needed",
                      "blocked":"failed"}.get(raw,"unknown"),
            "sku":report.get("sku"), "source_verified":bool(raw=="verified"),
            "reason":report.get("reason",""), "price_written":False, "stock_written":False}

def normalize_stock(report):
    raw=report.get("status","")
    ledger=report.get("ledger") or {}
    missing=sum(int(x.get("active_missing_recipes") or 0) for x in ledger.get("by_source",[]))
    return {"status":raw or "unknown", "read_only":True,
            "old_debits_without_recipes":missing,
            "unfinished_projection_count":ledger.get("pending_projection"),
            "live_order_conflicts":sum(int(x.get("counts",{}).get("blocked_legacy") or 0)
                                       for x in report.get("channels",{}).values()),
            "no_stock_mutation_by_this_check":True}

def normalize_stock_dryrun(report):
    proven = report.get("status") == "dryrun_success" and report.get("production_hashes_unchanged") is True
    return {"status": "simulated_success" if proven else "not_verified",
            "read_only": True,
            "deferred_legacy_cancellations": report.get("deferred_count"),
            "production_inputs_unchanged": bool(report.get("production_hashes_unchanged")),
            "source": "temporary ledger and catalog copies only"}

def build(data):
    records={}
    if data.get("media"):records.update(normalize_media(data["media"]))
    if data.get("avito"):records["avito"]=normalize_avito(data["avito"])
    if data.get("tilda"):records["tilda"]=normalize_tilda(data["tilda"])
    if data.get("stock"):records["stock_orders"]=normalize_stock(data["stock"])
    if data.get("stock_dryrun"):records["stock_dryrun"]=normalize_stock_dryrun(data["stock_dryrun"])
    return {"generated_utc":datetime.now(timezone.utc).isoformat(),
            "policy":"verified requires a live readback; submission is not publication",
            "checks":records,
            "sources":{name:bool(value) for name,value in data.items()},
            "commercial_fields_changed_by_status_tool":False}

def render(report):
    names={"ozon":"Ozon","wb":"Wildberries","market":"Яндекс Маркет",
           "avito":"Авито","tilda":"Tilda","stock_orders":"Заказы и остатки","stock_dryrun":"Репетиция склада"}
    lines=["# LEOPARTS — проверяемый статус операций","",
           f"Обновлено (UTC): {report['generated_utc']}","",
           "Подтверждение означает проверку по данным площадки, не только успешную отправку.","",
           "| Канал | Состояние | Комментарий |","|---|---|---|"]
    for name,row in report["checks"].items():
        status=row.get("status") or "unknown"
        note=row.get("reason") or ""
        if name=="avito":
            note="Фид и объявление проверяются отдельно; тариф/расписание могут блокировать публикацию."
        elif name=="tilda" and status=="manual_csv_import_needed":
            note="Требуется точечный импорт CSV с TildaUID, только существующий товар."
        elif name=="stock_orders":
            note=(f"Старых списаний без состава: {row.get('old_debits_without_recipes')}. "
                  f"Конфликтов в текущих заказах: {row.get('live_order_conflicts')}.")
        elif name=="stock_dryrun":
            note=f"Тест на копиях; отложенных отмен: {row.get('deferred_legacy_cancellations')}; исходные файлы целы."
        note=str(note).replace("|","/").replace("\n"," ")[:190]
        lines.append(f"| {names.get(name,name)} | {status} | {note} |")
    return "\n".join(lines)+"\n"

def main():
    inputs={"media":get("media_fast_report.json"),"avito":get("avito_fast_cover_report.json"),
            "tilda":get("tilda_cover_plan_report.json"),"stock":get("stock_preflight_report.json"),
            "stock_dryrun":get("stock_sync_dryrun_report.json")}
    report=build(inputs)
    OUT.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    MARKDOWN.write_text(render(report),encoding="utf-8")
    print(json.dumps({"generated":report["generated_utc"],
        "statuses":{name:row.get("status") for name,row in report["checks"].items()},
        "missing_sources":[k for k,v in report["sources"].items() if not v]},ensure_ascii=False))
    return 0

if __name__=="__main__":
    sys.exit(main())
