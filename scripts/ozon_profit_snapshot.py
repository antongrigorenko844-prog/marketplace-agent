#!/usr/bin/env python3
"""Read-only Ozon evidence snapshot; never invokes a marketplace writer.

Raw catalogue/pricing/promotion fields are retained. Finance reuses the existing
price_audit parser, but its shipment samples never enter the output directory.
Successful collection establishes data coverage, not verified profitability or
permission to change prices. Run explicitly, outside the general agent runner.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import math
import re
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/ozon_profit_first_20261127/live"
READ_ALLOWLIST = frozenset({
    ("GET", "/v1/actions"),
    ("POST", "/v3/product/list"),
    ("POST", "/v5/product/info/prices"),
    ("POST", "/v3/product/info/list"),
    ("POST", "/v4/product/info/stocks"),
    ("POST", "/v2/actions/products"),
    ("POST", "/v2/actions/candidates"),
    ("POST", "/v1/product/action/timer/status"),
    ("POST", "/v1/finance/accrual/types"),
    ("POST", "/v1/finance/accrual/by-day"),
})


def stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


class SnapshotError(RuntimeError):
    """Only caller-chosen codes, endpoint paths and numeric statuses are public."""

    def __init__(self, code: str, *, path: str | None = None,
                 http_status: int | None = None):
        super().__init__(code)
        self.code, self.path, self.http_status = code, path, http_status

    def safe(self) -> dict:
        out = {"code": self.code}
        if self.path is not None:
            out["endpoint"] = self.path
        if self.http_status is not None:
            out["http_status"] = self.http_status
        return out


def safe_error(exc: Exception) -> dict:
    # Exception messages and HTTP bodies can contain credentials or personal data.
    return exc.safe() if isinstance(exc, SnapshotError) else {"code": "unexpected_error"}


class ReadOnlyOzonClient:
    def __init__(self, client_id: str, api_key: str, *,
                 base_url: str = "https://api-seller.ozon.ru", timeout: int = 30,
                 session=None, sleep=time.sleep):
        if base_url.rstrip("/") != "https://api-seller.ozon.ru":
            raise SnapshotError("unapproved_api_host")
        if not client_id or not api_key:
            raise SnapshotError("missing_ozon_credentials")
        self._headers = {"Client-Id": client_id, "Api-Key": api_key,
                         "Content-Type": "application/json"}
        self._session = session or requests.Session()
        self._sleep = sleep
        self._timeout = (10, max(1, min(int(timeout), 60)))
        self.calls: list[dict] = []

    def request(self, method: str, path: str, body: dict | None = None):
        method = method.upper()
        if (method, path) not in READ_ALLOWLIST:
            raise SnapshotError("endpoint_not_read_allowlisted")
        if method == "GET" and body is not None:
            raise SnapshotError("unexpected_get_body", path=path)
        for attempt in range(1, 4):
            event = {"method": method, "endpoint": path, "attempt": attempt,
                     "started_at": stamp()}
            self.calls.append(event)
            kwargs = {"headers": self._headers, "timeout": self._timeout,
                      "allow_redirects": False}
            if body is not None:
                kwargs["json"] = body
            try:
                response = self._session.request(method, "https://api-seller.ozon.ru" + path,
                                                 **kwargs)
            except requests.RequestException:
                event.update(completed_at=stamp(), error="transport_error")
                if attempt < 3:
                    self._sleep(attempt)
                    continue
                raise SnapshotError("transport_error", path=path) from None
            status = response.status_code
            event.update(completed_at=stamp(), http_status=status)
            if status == 429 or 500 <= status <= 599:
                if attempt < 3:
                    try:
                        delay = float(response.headers.get("Retry-After", attempt))
                        delay = min(max(delay, 0), 30) if math.isfinite(delay) else attempt
                    except (TypeError, ValueError):
                        delay = attempt
                    self._sleep(delay)
                    continue
            if not 200 <= status < 300:
                raise SnapshotError("http_error", path=path, http_status=status)
            try:
                result = response.json()
            except (TypeError, ValueError):
                raise SnapshotError("invalid_json", path=path, http_status=status) from None
            if not isinstance(result, (dict, list)):
                raise SnapshotError("unexpected_json_schema", path=path)
            return result
        raise SnapshotError("retry_limit", path=path)  # defensive; loop returns or raises

    def post(self, path: str, body: dict):
        return self.request("POST", path, body)


def money(value: Any) -> Decimal:
    """Validation only. Do not replace the original Money object in artifacts."""
    if isinstance(value, dict):
        if value.get("currency") not in (None, "", "RUB"):
            raise SnapshotError("unsupported_currency")
        if "amount" not in value and "value" not in value:
            raise SnapshotError("money_schema_unknown")
        value = value.get("amount", value.get("value"))
    if value is None or isinstance(value, bool):
        raise SnapshotError("invalid_money")
    text = str(value).replace(" ", "").replace("\xa0", "").replace(",", ".")
    if text.startswith('value:'):
        text = text[6:].strip('"')
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise SnapshotError("invalid_money") from None
    if not number.is_finite():
        raise SnapshotError("invalid_money")
    return number


def flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value == "ENABLED":
        return True
    if value == "DISABLED":
        return False
    return None


def product_id(row: dict, *, allow_id: bool = True) -> str:
    values = [row[k] for k in ("product_id", "id") if k in row and (k != "id" or allow_id)]
    if not values:
        raise SnapshotError("missing_product_id")
    ids = []
    for value in values:
        if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
            raise SnapshotError("invalid_product_id")
        ids.append(str(value))
    if len(set(ids)) != 1:
        raise SnapshotError("conflicting_product_ids")
    return ids[0]


def offer_id(row: dict) -> str:
    value = row.get("offer_id")
    if not isinstance(value, str) or not value or value != value.strip():
        raise SnapshotError("missing_or_invalid_offer_id")
    return value


def section(rows: list | dict, started: str, *, errors: list | None = None,
            coverage: dict | None = None, **extra) -> dict:
    errors = errors or []
    return {"status": "partial" if errors and rows else "error" if errors else "ok",
            "started_at": started, "completed_at": stamp(), "count": len(rows),
            "coverage": coverage or {}, "errors": errors, **extra}


def envelope_rows(response: Any, keys: tuple[str, ...]) -> tuple[list, dict]:
    if not isinstance(response, dict):
        raise SnapshotError("response_not_object")
    envelopes = [response]
    if isinstance(response.get("result"), dict):
        envelopes.insert(0, response["result"])
    for env in envelopes:
        for key in keys:
            if key in env:
                rows = env[key]
                if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                    raise SnapshotError("rows_schema_unknown")
                return rows, env
    raise SnapshotError("rows_key_missing")


def read_pages(client, path: str, body: dict, *, keys=("items",),
               cursor_key="last_id", limit: int | None = 100,
               expected_ids: set[str] | None = None, identity=product_id,
               max_pages: int = 1000) -> tuple[list, dict]:
    """Preserve partial rows, and fail coverage rather than silently truncating.

    Explicit product-ID batches may stop once every expected ID is present:
    Ozon stock responses can retain their last cursor even on the final page.
    """
    started, rows, errors = stamp(), [], []
    seen_ids, seen_cursors, cursor = set(), set(), body.get(cursor_key, "")
    total, pages, terminal = None, 0, False
    for _ in range(max_pages):
        request = copy.deepcopy(body)
        if cursor or cursor_key in body:
            request[cursor_key] = cursor
        try:
            response = client.post(path, request)
            page, env = envelope_rows(response, tuple(keys))
            pages += 1
            for key in ("total", "total_count"):
                if key in env:
                    reported = env[key]
                    if isinstance(reported, bool) or not isinstance(reported, int) or reported < 0:
                        raise SnapshotError("invalid_pagination_total", path=path)
                    if total is not None and total != reported:
                        raise SnapshotError("pagination_total_changed", path=path)
                    total = reported
                    break
            for row in page:
                rows.append(row)
                try:
                    ident = identity(row)
                    if ident in seen_ids:
                        errors.append({"code": "duplicate_row_identity"})
                    seen_ids.add(ident)
                except SnapshotError as exc:
                    errors.append(exc.safe())
            if total is not None and len(rows) > total:
                raise SnapshotError("pagination_total_exceeded", path=path)
            if expected_ids is not None and expected_ids.issubset(seen_ids):
                terminal = True
                break
            if total is not None and len(rows) == total:
                terminal = True
                break
            next_cursor = env.get(cursor_key, "")
            if next_cursor is None:
                next_cursor = ""
            if not isinstance(next_cursor, (str, int)) or isinstance(next_cursor, bool):
                raise SnapshotError("invalid_pagination_cursor", path=path)
            next_cursor = str(next_cursor) if next_cursor != "" else ""
            if not next_cursor:
                if (total is not None and len(rows) != total) or env.get("has_next") is True:
                    raise SnapshotError("pagination_missing_cursor", path=path)
                if expected_ids is None and total is None and limit and len(page) >= limit:
                    raise SnapshotError("pagination_missing_cursor", path=path)
                terminal = True
                break
            if not page:
                raise SnapshotError("empty_page_with_cursor", path=path)
            if next_cursor == str(cursor) or next_cursor in seen_cursors:
                raise SnapshotError("pagination_repeated_cursor", path=path)
            seen_cursors.add(next_cursor)
            cursor = next_cursor
        except Exception as exc:  # every error is safe, and partial rows remain visible
            errors.append(safe_error(exc))
            break
    if not terminal and not errors:
        errors.append({"code": "pagination_page_limit"})
    coverage = {"pages": pages, "declared_total": total, "unique_id_count": len(seen_ids)}
    if expected_ids is not None:
        missing, unexpected = sorted(expected_ids - seen_ids), sorted(seen_ids - expected_ids)
        coverage.update(expected_count=len(expected_ids), missing_product_ids=missing,
                        unexpected_product_ids=unexpected)
        if missing:
            errors.append({"code": "missing_expected_products"})
        if unexpected:
            errors.append({"code": "unexpected_products"})
    return rows, section(rows, started, errors=errors, coverage=coverage)


def catalogue_identity(rows: list) -> tuple[dict[str, str], dict]:
    started, pairs, errors = stamp(), [], []
    for row in rows:
        try:
            pairs.append((offer_id(row), product_id(row)))
        except SnapshotError as exc:
            errors.append(exc.safe())
    offers, pids = Counter(a for a, _ in pairs), Counter(b for _, b in pairs)
    dup_offers = sorted(k for k, count in offers.items() if count > 1)
    dup_pids = sorted(k for k, count in pids.items() if count > 1)
    if dup_offers:
        errors.append({"code": "duplicate_offer_id"})
    if dup_pids:
        errors.append({"code": "duplicate_product_id"})
    valid = {a: b for a, b in pairs if offers[a] == 1 and pids[b] == 1}
    meta = section(rows, started, errors=errors, coverage={
        "unique_offer_count": len(offers), "unique_product_id_count": len(pids),
        "unambiguous_count": len(valid), "duplicate_offer_ids": dup_offers,
        "duplicate_product_ids": dup_pids})
    return valid, meta


def price_schema_errors(rows: list) -> list:
    errors = []
    for row in rows:
        price_value = row.get("price")
        price = (price_value if isinstance(price_value, dict)
                 and not {"amount", "value"}.intersection(price_value) else row)
        for key in ("price", "min_price"):
            try:
                if key not in price:
                    raise SnapshotError("price_field_missing")
                if money(price[key]) < 0:
                    raise SnapshotError("negative_price")
            except SnapshotError as exc:
                errors.append({**exc.safe(), "field": key,
                               "offer_id": row.get("offer_id")})
        for key in ("auto_action_enabled", "auto_add_to_ozon_actions_list_enabled",
                    "min_price_for_auto_actions_enabled"):
            if key in price and flag(price[key]) is None:
                errors.append({"code": "price_flag_schema_unknown", "field": key,
                               "offer_id": row.get("offer_id")})
    return errors


def scoped_rows(client, path: str, catalogue: dict[str, str], *, kind: str) -> tuple[list, dict]:
    started, rows, errors, page_count = stamp(), [], [], 0
    offers = list(catalogue)
    for offset in range(0, len(offers), 100):
        batch = offers[offset:offset + 100]
        expected = {catalogue[x] for x in batch}
        if kind == "info":
            body = {"offer_id": batch, "product_id": [], "sku": []}
            cursor = "last_id"
        elif kind == "prices":
            body = {"filter": {"offer_id": batch, "visibility": "ALL"}, "limit": 100,
                    "cursor": ""}
            cursor = "cursor"
        else:
            body = {"filter": {"offer_id": batch, "visibility": "ALL"}, "limit": 100,
                    "last_id": ""}
            cursor = "last_id"
        part, meta = read_pages(client, path, body, cursor_key=cursor, limit=100,
                                expected_ids=expected)
        rows.extend(part)
        errors.extend(meta["errors"])
        page_count += meta["coverage"]["pages"]
    observed, identity_meta = catalogue_identity(rows)
    errors.extend(identity_meta["errors"])
    conflicts = sorted(sku for sku, pid in observed.items()
                       if sku not in catalogue or catalogue[sku] != pid)
    missing = sorted(set(catalogue) - set(observed))
    if conflicts:
        errors.append({"code": "catalogue_identity_mismatch"})
    if missing:
        errors.append({"code": "missing_catalogue_skus"})
    if kind == "prices":
        errors.extend(price_schema_errors(rows))
    return rows, section(rows, started, errors=errors, coverage={
        "expected_count": len(catalogue), "unique_offer_count": len(observed),
        "missing_offer_ids": missing, "conflicting_offer_ids": conflicts,
        "pages": page_count})


def collect_promotions(client, catalogue: dict[str, str]) -> tuple[list, dict]:
    started, out, errors = stamp(), [], []
    by_pid = {pid: sku for sku, pid in catalogue.items()}
    try:
        response = client.request("GET", "/v1/actions")
        actions = response.get("result") if isinstance(response, dict) else None
        if not isinstance(actions, list) or any(not isinstance(a, dict) for a in actions):
            raise SnapshotError("actions_schema_unknown")
    except Exception as exc:
        return out, section(out, started, errors=[safe_error(exc)])
    seen = set()
    for action in actions:
        aid = action.get("id")
        try:
            identity = product_id({"id": aid})
            if identity in seen:
                raise SnapshotError("duplicate_action_id")
            seen.add(identity)
        except SnapshotError as exc:
            errors.append(exc.safe())
            continue
        entry = {"action": action, "sections": {}}
        for name, path in (("participants", "/v2/actions/products"),
                           ("candidates", "/v2/actions/candidates")):
            rows, meta = read_pages(client, path, {"action_id": int(identity),
                                   "limit": 100, "last_id": ""}, keys=("products", "items"))
            unmapped = []
            mapped_rows = []
            for row in rows:
                try:
                    pid = product_id(row)
                    mapped = by_pid.get(pid)
                    if mapped is None:
                        unmapped.append(pid)
                    if row.get("offer_id") is not None and row["offer_id"] != mapped:
                        meta["errors"].append({"code": "action_offer_id_mismatch", "product_id": pid})
                except SnapshotError:
                    mapped = None
                mapped_rows.append({**row, "mapped_offer_id": mapped})
            # Offers outside the active catalogue can be legitimate archived candidates.
            # Preserve them, report them, and never invent a mapping.
            meta["coverage"]["unmapped_product_ids"] = sorted(set(unmapped))
            meta["coverage"]["mapped_active_count"] = sum(r["mapped_offer_id"] is not None for r in mapped_rows)
            if meta["errors"]:
                meta["status"] = "partial" if rows else "error"
                errors.append({"code": "promotion_section_incomplete", "action_id": aid, "section": name})
            entry["sections"][name] = {**meta, "rows": mapped_rows}
        out.append(entry)
    return out, section(out, started, errors=errors, coverage={"action_count": len(actions),
                      "collected_action_count": len(out), "active_catalogue_count": len(catalogue)})


def normalize_protection(row: dict, by_pid: dict[str, str], now: datetime) -> dict:
    pid = product_id(row, allow_id=False)
    enabled = flag(row.get("min_price_for_auto_actions_enabled"))
    expiry = row.get("expired_at")
    parsed = None
    if isinstance(expiry, str) and expiry:
        try:
            parsed = datetime.fromisoformat(expiry.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = None
        except ValueError:
            pass
    active = False if enabled is False else (parsed > now if enabled is True and parsed else None)
    return {"product_id": pid, "mapped_offer_id": by_pid.get(pid), "enabled": enabled,
            "expired_at": expiry, "active": active, "raw": row}


def collect_protection(client, catalogue: dict[str, str], *, now: datetime | None = None) -> tuple[dict, dict]:
    started, rows, batches, errors = stamp(), [], [], []
    now = now or datetime.now(timezone.utc)
    by_pid = {pid: sku for sku, pid in catalogue.items()}
    pids = list(by_pid)
    for offset in range(0, len(pids), 100):
        batch = pids[offset:offset + 100]
        record = {"product_ids": batch, "started_at": stamp()}
        try:
            response = client.post("/v1/product/action/timer/status", {"product_ids": [int(x) for x in batch]})
            record["response"] = response
            record["schema_keys"] = sorted(response) if isinstance(response, dict) else []
            raw_rows, _ = envelope_rows(response, ("statuses", "items", "products"))
            local = []
            for row in raw_rows:
                parsed = normalize_protection(row, by_pid, now)
                rows.append(parsed)
                local.append(parsed["product_id"])
                if parsed["enabled"] is None or (parsed["enabled"] is True and parsed["active"] is None):
                    errors.append({"code": "protection_status_schema_unverified", "product_id": parsed["product_id"]})
            if len(set(local)) != len(local):
                errors.append({"code": "duplicate_protection_product"})
            if set(local) != set(batch):
                errors.append({"code": "protection_batch_coverage_mismatch"})
            record["status"] = "ok" if set(local) == set(batch) and all(
                x["active"] is not None for x in rows if x["product_id"] in batch) else "partial"
        except Exception as exc:
            error = safe_error(exc)
            errors.append(error)
            record.update(status="error", error=error)
        record["completed_at"] = stamp()
        batches.append(record)
    observed = {row["product_id"] for row in rows}
    missing, unexpected = sorted(set(pids) - observed), sorted(observed - set(pids))
    if missing:
        errors.append({"code": "missing_protection_products"})
    coverage = {"expected_count": len(pids), "observed_count": len(observed),
                "missing_product_ids": missing, "unexpected_product_ids": unexpected,
                "unknown_status_count": sum(row["active"] is None for row in rows)}
    data = {"rows": rows, "batches": batches, "coverage": coverage, "errors": errors,
            "interpretation": "Read probe only; raw schema retained. No protection was renewed or changed."}
    return data, section(rows, started, errors=errors, coverage=coverage)


def read_master(path: Path) -> tuple[dict, dict]:
    from openpyxl import load_workbook
    started, data, errors, duplicates = stamp(), {}, [], set()
    try:
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            sheet = workbook["Товары"]
            iterator = sheet.iter_rows(values_only=True)
            headers = list(next(iterator))
            required = {k: headers.index(k) for k in ("Название товара", "Цена, ₽", "Закупка, ₽")}
            optional = {target: next((headers.index(name) for name in names if name in headers), None)
                        for target, names in {"ozon_enabled": ("Ozon (да/нет)", "Ozon(да/нет)"),
                                              "notes": ("Заметки",)}.items()}
            if optional["ozon_enabled"] is None:
                errors.append({"code": "master_ozon_scope_column_missing"})
            for row in iterator:
                if not row or row[0] is None:
                    continue
                sku = row[0]
                if not isinstance(sku, str) or not sku or sku != sku.strip():
                    errors.append({"code": "invalid_master_sku"})
                    continue
                if sku in data or sku in duplicates:
                    data.pop(sku, None)
                    duplicates.add(sku)
                    continue
                values = {target: row[required[source]] for source, target in (
                    ("Название товара", "title"), ("Цена, ₽", "price"), ("Закупка, ₽", "cost"))}
                values.update({key: row[index] if index is not None else None
                               for key, index in optional.items()})
                data[sku] = values
        finally:
            workbook.close()
    except Exception as exc:
        errors.append(safe_error(exc))
    if duplicates:
        errors.append({"code": "duplicate_master_sku"})
    return data, section(data, started, errors=errors, coverage={"duplicate_offer_ids": sorted(duplicates),
                      "missing_cost_count": sum(x["cost"] is None for x in data.values())})


def finance_mapping(info: list, catalogue: dict[str, str]) -> tuple[dict, dict]:
    """Only unambiguous active product SKU mappings enter the legacy parser."""
    mappings: dict[str, set[str]] = {}
    for row in info:
        try:
            oid, pid = offer_id(row), product_id(row)
        except SnapshotError:
            continue
        if catalogue.get(oid) != pid:
            continue
        sources = list(row.get("sources") or [])
        if row.get("sku") is not None:
            sources.append({"sku": row["sku"]})
        for source in sources:
            if not isinstance(source, dict):
                continue
            raw = source.get("sku")
            if isinstance(raw, bool) or not re.fullmatch(r"[1-9][0-9]*", str(raw)):
                continue
            mappings.setdefault(str(raw), set()).add(oid)
    conflicts = sorted(sku for sku, offers in mappings.items() if len(offers) != 1)
    valid = {sku: next(iter(offers)) for sku, offers in mappings.items() if len(offers) == 1}
    parser_data = {"list": [{"sku": sku, "offer_id": oid} for sku, oid in sorted(valid.items())], "details": []}
    return parser_data, {"sku_count": len(valid), "conflicting_marketplace_skus": conflicts,
                         "unmapped_offer_ids": sorted(set(catalogue) - set(valid.values()))}


def validate_finance_records(rows: list) -> None:
    """Do not let the legacy parser silently turn malformed known amounts into 0."""
    money_keys = {"total_amount", "amount", "accrued", "sale_amount", "seller_price",
                  "sale_commission", "commission", "total_accrued", "quantity"}
    def visit(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in money_keys and not (key == "commission" and isinstance(child, dict)
                                              and not {"amount", "value"}.intersection(child)):
                    if child is not None:
                        money(child)
                elif isinstance(child, (dict, list)):
                    visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    for row in rows:
        if not ({"total_amount", "amount"}.intersection(row)):
            raise SnapshotError("finance_total_missing")
        money(row.get("total_amount", row.get("amount")))
        visit(row)


def collect_finance(client, mapping: dict, mapping_coverage: dict, *,
                    now: datetime | None = None, days: int = 30) -> tuple[dict, dict]:
    # Import only the finance parser, never price-audit/main/other channel commands.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    import price_audit
    import ozon_client
    started = stamp()
    now = now or datetime.now(timezone.utc)
    end = (now.astimezone(timezone.utc) - timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    errors, daily = [], []
    original = (ozon_client._post, price_audit.DATA, price_audit.FIN_PATH,
                price_audit.datetime, price_audit._accruals_by_day)

    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return end.astimezone(tz) if tz is not None else end.replace(tzinfo=None)

    def readonly_post(path, body):
        # The parser may only read these two endpoints, even though the collector
        # itself also reads catalogue and promotion endpoints.
        if path not in ("/v1/finance/accrual/types", "/v1/finance/accrual/by-day"):
            raise SnapshotError("finance_proxy_endpoint_not_allowed")
        try:
            response = client.post(path, body)
            if path.endswith("/types"):
                envelope_rows(response, ("accrual_types", "types"))
            return response
        except Exception as exc:
            errors.append({**safe_error(exc), "section": "finance_types" if path.endswith("/types") else "finance_day"})
            raise SnapshotError("finance_read_failed", path=path) from None

    def day_reader(_module, day):
        # Fingerprints remain in memory: neither order IDs nor sample records
        # are saved in the public manifest.
        fingerprint = lambda row: hashlib.sha256(json.dumps(row, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        rows, meta = read_pages(client, "/v1/finance/accrual/by-day", {"date": day},
                                keys=("accruals",), limit=None, identity=fingerprint)
        try:
            validate_finance_records(rows)
        except SnapshotError as exc:
            meta["errors"].append(exc.safe())
        if meta["errors"]:
            errors.append({"code": "finance_day_excluded", "date": day,
                           "causes": meta["errors"]})
            meta["status"] = "error"
        daily.append({"date": day, "status": meta["status"], "count": len(rows),
                      "pages": meta["coverage"]["pages"], "errors": meta["errors"]})
        return [] if meta["errors"] else rows

    result = {}
    # Legacy samples/debug_records are written only to a private temporary path,
    # never inside the directory uploaded as a snapshot artifact.
    try:
        with tempfile.TemporaryDirectory(prefix="ozon-finance-private-") as temp:
            Path(temp, "ozon_products.json").write_text(json.dumps(mapping), encoding="utf-8")
            ozon_client._post = readonly_post
            price_audit.DATA, price_audit.FIN_PATH = temp, str(Path(temp, "finance-private.json"))
            price_audit.datetime, price_audit._accruals_by_day = FixedDateTime, day_reader
            parsed = price_audit.fetch_ozon_finance(days=days - 1)
            allowed = ("from", "to", "method", "operations", "payout_total", "items",
                       "fees_by_type", "unassigned", "by_type")
            result = {key: parsed[key] for key in allowed if key in parsed}
    except Exception as exc:
        errors.append(safe_error(exc))
    finally:
        (ozon_client._post, price_audit.DATA, price_audit.FIN_PATH,
         price_audit.datetime, price_audit._accruals_by_day) = original
    failed = [d["date"] for d in daily if d["status"] != "ok"]
    if len(daily) != days:
        errors.append({"code": "finance_date_coverage_incomplete"})
    if mapping_coverage.get("conflicting_marketplace_skus"):
        errors.append({"code": "ambiguous_finance_sku_mapping"})
    unmatched = sorted(key for key in result.get("items", {}) if key.startswith("sku:"))
    if unmatched:
        errors.append({"code": "unmatched_finance_skus"})
    coverage = {"expected_dates": days, "collected_dates": len(daily) - len(failed),
                "failed_dates": failed, "unmatched_marketplace_skus": unmatched,
                "mapping": mapping_coverage}
    result.update(window={"timezone": "UTC", "complete_calendar_dates": days,
                          "from": (end.date() - timedelta(days=days - 1)).isoformat(),
                          "to": end.date().isoformat(), "includes_partial_current_day": False},
                  collection_complete=not errors, daily_coverage=daily, coverage=coverage,
                  errors=errors, parser="price_audit.fetch_ozon_finance",
                  interpretation="Aggregate accrual evidence only. Returns, allocation and net amounts retain the existing parser's assumptions; profitability is not independently verified.")
    return result, section(result.get("items", {}), started, errors=errors, coverage=coverage)


def write_json(path: Path, value: Any):
    target = path.with_suffix(path.suffix + ".tmp")
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    target.replace(path)


def collect_snapshot(client, output: Path, master_path: Path, *, expected_count: int = 147,
                     now: datetime | None = None) -> dict:
    output = output.resolve()
    if output in {ROOT.resolve(), (ROOT / "data").resolve(), master_path.resolve().parent}:
        raise SnapshotError("output_must_be_isolated_subdirectory")
    output.mkdir(parents=True, exist_ok=True)
    manifest = {"schema_version": 1, "mode": "READ_ONLY", "seller_writes": 0,
                "started_at": stamp(), "status": "collecting", "sections": {},
                "interpretation": "Coverage of read-only evidence; not a price/protection or financial approval."}
    write_json(output / "manifest.json", manifest)
    sections = manifest["sections"]
    all_products, sections["products"] = read_pages(client, "/v3/product/list", {
        "filter": {"visibility": "ALL"}, "last_id": "", "limit": 100}, limit=100)
    products = [row for row in all_products if row.get("archived") is not True]
    sections["products"]["count"] = len(products)
    catalogue, identities = catalogue_identity(products)
    sections["products"]["errors"].extend(identities["errors"])
    unknown_archived = sum(not isinstance(row.get("archived"), bool) for row in all_products)
    if unknown_archived:
        sections["products"]["errors"].append({"code": "archive_flag_schema_unknown"})
    if sections["products"]["errors"]:
        sections["products"]["status"] = "partial" if products else "error"
    sections["products"]["coverage"].update(identities["coverage"], active_count=len(products),
        archived_count=len(all_products) - len(products), unknown_archive_state_count=unknown_archived,
        expected_count=expected_count)
    manifest["catalog"] = {"expected_count": expected_count, "active_count": len(products),
        "unambiguous_count": len(catalogue), "changed": len(products) != expected_count,
        "exact_offer_ids": sorted(catalogue), "product_ids": sorted(catalogue.values())}
    write_json(output / "products.json", products)
    master, sections["master"] = read_master(master_path)
    sections["master"]["coverage"].update(missing_active_offer_ids=sorted(set(catalogue) - set(master)))
    if sections["master"]["coverage"]["missing_active_offer_ids"]:
        sections["master"]["errors"].append({"code": "active_products_missing_from_master"})
        sections["master"]["status"] = "partial" if master else "error"
    write_json(output / "master.json", master)
    info = []
    for kind, path, filename in (
        ("prices", "/v5/product/info/prices", "prices.json"),
        ("info", "/v3/product/info/list", "product_info.json"),
        ("stocks", "/v4/product/info/stocks", "stocks.json")):
        rows, meta = scoped_rows(client, path, catalogue, kind=kind)
        if not catalogue:
            meta.update(status="blocked", errors=[{"code": "no_unambiguous_catalogue"}])
        sections["product_info" if kind == "info" else kind] = meta
        write_json(output / filename, rows)
        if kind == "info":
            info = rows
    promotions, sections["promotions"] = collect_promotions(client, catalogue)
    write_json(output / "promotions.json", promotions)
    protection, sections["protection"] = collect_protection(client, catalogue, now=now)
    if not catalogue:
        sections["protection"].update(status="blocked", errors=[{"code": "no_unambiguous_catalogue"}])
    write_json(output / "protection.json", protection)
    mapping, mapping_coverage = finance_mapping(info, catalogue)
    write_json(output / "ozon_products.json", mapping)
    finance, sections["finance"] = collect_finance(client, mapping, mapping_coverage, now=now)
    write_json(output / "finance.json", finance)
    manifest["status"] = ("partial" if any(s["status"] != "ok" for s in sections.values()) else
                          "catalog_changed_review_required" if manifest["catalog"]["changed"] else "complete_read_only")
    manifest["completed_at"] = stamp()
    manifest["requests"] = getattr(client, "calls", [])
    write_json(output / "manifest.json", manifest)
    return manifest


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--master", type=Path, default=ROOT / "data/master_control.xlsx")
    parser.add_argument("--expected-count", type=int, default=147)
    args = parser.parse_args(argv)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    try:
        from config import config
        client = ReadOnlyOzonClient(config.ozon_client_id, config.ozon_api_key,
                                   base_url=config.ozon_api_base,
                                   timeout=config.request_timeout_seconds)
        manifest = collect_snapshot(client, args.output, args.master, expected_count=args.expected_count)
        print(json.dumps({"status": manifest["status"], "catalog": {
            k: v for k, v in manifest["catalog"].items() if k != "exact_offer_ids" and k != "product_ids"},
            "section_status": {k: v["status"] for k, v in manifest["sections"].items()}}, ensure_ascii=False))
        return 0 if manifest["status"] == "complete_read_only" else 2
    except Exception as exc:
        print(json.dumps({"status": "failed", "error": safe_error(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
