#!/usr/bin/env python3
"""Isolated, read-only accrual evidence for 25 September–23 October 2026.

Reuses the normal snapshot's transport, pagination, exact identity mapping and
finance parser. Does not update the normal snapshot, financial assumptions,
promotion plans, service evidence or execution journal. Output must be a new
directory outside this repository. Service rates remain subject to review.
"""
from __future__ import annotations

import argparse
import copy
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ozon_profit_snapshot import (
    ReadOnlyOzonClient, SnapshotError, catalogue_identity, collect_finance,
    envelope_rows, finance_mapping, money, offer_id, product_id, read_pages,
    safe_error, scoped_rows, stamp, write_json,
)

START, END = date(2026, 9, 25), date(2026, 10, 23)
DAYS = (END - START).days + 1
DATES = tuple((START + timedelta(days=n)).isoformat() for n in range(DAYS))
PARSER_NOW = datetime(2026, 10, 24, 12, tzinfo=timezone.utc)
FOCUS = {
    "Dq200dsg70am0cw": "3225148531",
    "0AM927769D": "5054769122",
    "6t30": "6287285958",
    "planka-solenoid-2sht": "6287286104",
    "pylniki-shtokov-orig-v2": "6291485641",
}
LOOKUP_SKU = "3237042002"
ALLOWLIST = frozenset({
    ("POST", "/v3/product/list"),
    ("POST", "/v3/product/info/list"),
    ("POST", "/v1/finance/accrual/types"),
    ("POST", "/v1/finance/accrual/by-day"),
})


class HistoricalReader:
    """Restrict the existing read-only client further; observe finance in memory."""

    def __init__(self, client):
        self.client = client
        self.days, self.types, self.calls = {}, [], []

    def request(self, method, path, body=None):
        if (method.upper(), path) not in ALLOWLIST:
            raise SnapshotError("historical_endpoint_not_allowed")
        if not isinstance(body, dict):
            raise SnapshotError("historical_request_body_required")
        if path.endswith("/by-day") and body.get("date") not in DATES:
            raise SnapshotError("date_outside_fixed_historical_window")
        self.calls.append({"method": method.upper(), "endpoint": path})
        response = self.client.request(method.upper(), path, body)
        if path.endswith("/by-day"):
            rows, _ = envelope_rows(response, ("accruals",))
            self.days.setdefault(body["date"], []).extend(copy.deepcopy(rows))
        elif path.endswith("/types"):
            self.types, _ = envelope_rows(response, ("accrual_types", "types"))
        return response

    def post(self, path, body):
        return self.request("POST", path, body)


# These are financial fields only. Order/posting IDs, buyers, addresses and raw
# HTTP responses are not retained. Missing fields remain missing, never zero.
FEE = {"type_id": "id", "accrued": "money", "amount": "money"}
FINANCIAL_FIELDS = {
    "accrued_category": "category", "total_amount": "money", "amount": "money",
    "non_item_fee": FEE,
    "item_fees": {"fees": [{"sku": "id", "quantity": "money", "fees": [FEE]}]},
    "posting": {"products": [{
        "sku": "id", "quantity": "money",
        "commission": {"seller_price": "money", "sale_amount": "money",
                       "sale_commission": "money", "commission": "money"},
        "delivery": {"total_accrued": "money", "services": [FEE]},
    }]},
}


def financial_fields(value, spec):
    """Keep explicitly present amounts, with strict schemas and no fallback 0."""
    if value is None:
        return None
    if isinstance(spec, dict):
        if not isinstance(value, dict):
            raise SnapshotError("financial_object_schema_unknown")
        return {key: financial_fields(value[key], child) for key, child in spec.items()
                if key in value}
    if isinstance(spec, list):
        if not isinstance(value, list):
            raise SnapshotError("financial_array_schema_unknown")
        return [financial_fields(child, spec[0]) for child in value]
    if spec == "money":
        return str(money(value))
    if spec == "id" and not isinstance(value, bool) and re.fullmatch(r"[0-9]+", str(value)):
        return str(value)
    if spec == "category" and isinstance(value, str) and re.fullmatch(r"[A-Z_]+", value):
        return value
    raise SnapshotError("financial_identifier_schema_unknown")


def market_skus(row):
    sources = row.get("sources") or []
    if not isinstance(sources, list):
        raise SnapshotError("product_sources_schema_unknown")
    values = [source.get("sku") for source in sources if isinstance(source, dict)]
    values.append(row.get("sku"))
    return {str(value) for value in values if not isinstance(value, bool)
            and re.fullmatch(r"[1-9][0-9]*", str(value))}


def collect_identities(reader):
    """Full catalogue plus exact approved offer/PID checks and an isolated lookup."""
    products, product_meta = read_pages(reader, "/v3/product/list", {
        "filter": {"visibility": "ALL"}, "last_id": "", "limit": 100}, limit=100)
    catalogue, identity_meta = catalogue_identity(products)  # includes archived rows
    info, info_meta = scoped_rows(reader, "/v3/product/info/list", catalogue, kind="info")
    focused, focus_meta = scoped_rows(reader, "/v3/product/info/list", FOCUS, kind="info")
    blocked_offers = set(identity_meta["coverage"]["duplicate_offer_ids"])
    blocked_pids = set(identity_meta["coverage"]["duplicate_product_ids"])
    focus_identity = {}
    for oid, expected_pid in FOCUS.items():
        rows = [row for row in focused if row.get("offer_id") == oid]
        observed = set()
        for row in rows:
            try:
                observed.add(product_id(row))
            except SnapshotError:
                observed.add("invalid")
        conflict = (oid in blocked_offers or expected_pid in blocked_pids
                    or catalogue.get(oid, expected_pid) != expected_pid
                    or any(other != oid and known == expected_pid for other, known in catalogue.items()))
        exact = not conflict and len(rows) == 1 and observed == {expected_pid}
        try:
            expected_skus = market_skus(rows[0]) if exact else set()
        except SnapshotError:
            exact, expected_skus = False, set()
        focus_identity[oid] = {
            "expected_product_id": expected_pid, "observed_product_ids": sorted(observed),
            "identity_exact": exact, "catalogue_conflict": conflict,
            "expected_marketplace_skus": sorted(expected_skus),
        }
        if exact:
            catalogue[oid] = expected_pid
            info.extend(rows)
        else:
            # A wrong PID for an approved offer cannot receive historical costs.
            catalogue.pop(oid, None)

    lookup_rows, lookup_meta = read_pages(reader, "/v3/product/info/list", {
        "offer_id": [], "product_id": [], "sku": [LOOKUP_SKU]}, limit=100)
    matched, rejected = [], []
    for row in lookup_rows:
        try:
            oid, pid = offer_id(row), product_id(row)
            if LOOKUP_SKU not in market_skus(row):
                raise SnapshotError("lookup_marketplace_sku_not_exact")
            if (oid in blocked_offers or pid in blocked_pids
                    or catalogue.get(oid, pid) != pid
                    or any(other != oid and known == pid for other, known in catalogue.items())
                    or (oid in FOCUS and not focus_identity[oid]["identity_exact"])):
                raise SnapshotError("lookup_identity_conflict")
            matched.append({"offer_id": oid, "product_id": pid})
        except SnapshotError as exc:
            rejected.append(exc.safe())
    # Never accept a partial response, multiple matches or a rejected row as an
    # exact resolution. This lookup's failure does not block the five offers.
    accepted = len(matched) == 1 and not rejected and not lookup_meta["errors"]
    if accepted:
        match = matched[0]
        catalogue[match["offer_id"]] = match["product_id"]
        info.extend(lookup_rows)

    mapping, coverage = finance_mapping(info, catalogue)
    mapping_rows = [{"marketplace_sku": row["sku"], "offer_id": row["offer_id"],
                     "product_id": catalogue[row["offer_id"]]} for row in mapping["list"]]
    resolved = next((row for row in mapping_rows if row["marketplace_sku"] == LOOKUP_SKU), None)
    for oid, evidence in focus_identity.items():
        evidence["marketplace_skus"] = sorted(row["marketplace_sku"] for row in mapping_rows
                                             if row["offer_id"] == oid)
        evidence["mapping_exact"] = bool(
            evidence["identity_exact"] and evidence["expected_marketplace_skus"]
            and set(evidence["expected_marketplace_skus"]).issubset(evidence["marketplace_skus"]))
    return mapping, coverage, {
        "mapping": mapping_rows, "coverage": coverage, "focus": focus_identity,
        "direct_lookup": {"requested_marketplace_sku": LOOKUP_SKU,
                          "payload_status": "read_only_probe_not_previously_verified",
                          "accepted_direct_response": accepted, "resolved": resolved,
                          "errors": lookup_meta["errors"] + rejected,
                          "returned_count": len(lookup_rows)},
        "sections": {"catalogue": product_meta, "catalogue_identity": identity_meta,
                     "product_info": info_meta, "approved_offer_info": focus_meta},
        "interpretation": "Exact API identity evidence only; no trading authorization or cost approval.",
    }


def collect_history(client, output: Path):
    output = output.resolve()
    if output == ROOT.resolve() or ROOT.resolve() in output.parents:
        raise SnapshotError("historical_output_must_be_outside_repository")
    if output.exists():
        raise SnapshotError("historical_output_already_exists")
    output.mkdir(parents=True, exist_ok=False)
    manifest = {
        "schema_version": 1, "mode": "READ_ONLY_HISTORICAL_FINANCE", "seller_writes": 0,
        "started_at": stamp(), "status": "collecting", "from": START.isoformat(),
        "to": END.isoformat(), "complete_calendar_dates": DAYS,
        "includes_partial_current_day": False,
        "interpretation": "Historical evidence for analysis. Service rates are not verified or activated.",
    }
    write_json(output / "manifest.json", manifest)
    reader = HistoricalReader(client)
    try:
        mapping, coverage, identities = collect_identities(reader)
        write_json(output / "identity_evidence.json", identities)
        finance, finance_meta = collect_finance(reader, mapping, coverage, now=PARSER_NOW, days=DAYS)
        write_json(output / "finance.json", finance)
        daily = []
        for day in finance.get("daily_coverage", []):
            result = {key: day[key] for key in ("date", "status", "count", "pages", "errors")}
            if day["status"] == "ok":
                try:
                    result["accruals"] = [financial_fields(row, FINANCIAL_FIELDS)
                                          for row in reader.days.get(day["date"], [])]
                    if len(result["accruals"]) != day["count"]:
                        raise SnapshotError("observed_finance_count_mismatch")
                except SnapshotError as exc:
                    result.pop("accruals", None)
                    result.update(status="error", errors=[exc.safe()])
            daily.append(result)
        types = [{key: row[key] for key in ("id", "description", "name") if key in row}
                 for row in reader.types]
        write_json(output / "financial_fields_by_day.json", {
            "from": START.isoformat(), "to": END.isoformat(), "accrual_types": types,
            "days": daily, "interpretation": (
                "Financial field allowlist only; missing amounts are not filled. Returns and sale amounts "
                "retain their source signs. Reconcile with the existing parser before estimating rates. "
                "Missing SKU service charges do not prove a program is disabled or its cost is zero."),
        })
        all_dates = (len(daily) == DAYS and {day["date"] for day in daily} == set(DATES)
                     and all(day["status"] == "ok" for day in daily))
        focus = {}
        for oid, identity in identities["focus"].items():
            has_history = oid in finance.get("items", {})
            focus[oid] = {**identity, "all_29_dates_and_pages_collected": all_dates,
                          "aggregate_present": has_history, "reconciliation_status": "pending_review",
                          "service_rates_verified": False,
                          "status": "ready_for_reconciliation" if all_dates and identity["mapping_exact"]
                                    and has_history else "incomplete"}
        source_sections = identities["sections"]
        manifest.update(
            status="complete_read_only" if all_dates and finance_meta["status"] == "ok"
                   and all(part["status"] == "ok" for part in source_sections.values())
                   and all(row["status"] == "ready_for_reconciliation" for row in focus.values())
                   and identities["direct_lookup"]["resolved"] else "partial",
            finance=finance_meta, focus=focus,
            direct_lookup=identities["direct_lookup"], requests=reader.calls,
            files=["identity_evidence.json", "finance.json", "financial_fields_by_day.json"],
            completed_at=stamp(),
        )
    except Exception as exc:
        manifest.update(status="failed", error=safe_error(exc), requests=reader.calls, completed_at=stamp())
    write_json(output / "manifest.json", manifest)
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        from config import config
        client = ReadOnlyOzonClient(config.ozon_client_id, config.ozon_api_key,
                                   base_url=config.ozon_api_base,
                                   timeout=config.request_timeout_seconds)
        result = collect_history(client, args.output)
        print(json.dumps({"status": result["status"], "from": START.isoformat(),
                          "to": END.isoformat(), "error": result.get("error")}, ensure_ascii=False))
        return 0 if result["status"] == "complete_read_only" else 1 if result["status"] == "failed" else 2
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": safe_error(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
