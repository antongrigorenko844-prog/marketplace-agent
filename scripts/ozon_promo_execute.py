#!/usr/bin/env python3
"""Execute an immutable, owner-approved Ozon batch with individual readback.

Default mode prepares a report without making API writes. Execution requires the
persisted enabled approval, an official request schema, a fresh complete core
snapshot, and the one-use ozon_control_guard permit for every request. Prices
are absolute approved values; this module never calculates a repeated discount.
"""
from __future__ import annotations

import argparse
import copy
import fcntl
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ozon_profit_snapshot import (
    ReadOnlyOzonClient, SnapshotError, collect_protection, money, product_id,
    read_pages, scoped_rows,
)

EXECUTION = ROOT / "data/ozon_promo_execution_20261127"
PRICE_PATH = "/v1/product/import/prices"
ACTION_PATH = "/v1/actions/products/update"
TIMER_PATH = "/v1/product/action/timer/update"
FUTURE_PATH = "/v2/actions/auto-add/products/update"
SAFE_ACTION_TYPES = {"ELASTIC_BOOSTING", "STOCK_DISCOUNT"}
MAX_AGE_SECONDS = 900


class ExecutionError(RuntimeError):
    pass


def now_utc():
    return datetime.now(timezone.utc)


def iso_now():
    return now_utc().isoformat()


def parse_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ExecutionError("INVALID_TIMESTAMP") from None
    if parsed.tzinfo is None:
        raise ExecutionError("TIMEZONE_REQUIRED")
    return parsed


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def rubles(value):
    amount = money(value)
    if amount <= 0 or amount != amount.quantize(Decimal("0.01")):
        raise ExecutionError("INVALID_POSITIVE_RUBLE_AMOUNT")
    return format(amount, "f").rstrip("0").rstrip(".") if "." in format(amount, "f") else format(amount, "f")


def target(item):
    return money(item.get("target_price", item.get("target_price_rub")))


def before_price(item):
    return money(item.get("before_price", item.get("price_before_rub")))


def safe_error(exc):
    if isinstance(exc, (ExecutionError, SnapshotError)):
        # Only internally constructed codes are exposed by ExecutionError.
        return {"code": str(exc).split(":", 1)[0]}
    return {"code": "UNEXPECTED_ERROR"}


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


class OfficialContract:
    """Validate documented wire fields using OpenAPI or a cited evidence bundle.

    Accepts a complete Swagger document or an extract with paths and recursive
    referenced_schemas keyed by their original $ref. A minimal evidence bundle
    may instead cite official endpoint announcements, request examples and the
    seller's observed Money fields. That bundle is never labelled full Swagger.
    """

    def __init__(self, document):
        self.document = document
        source = document.get("source", document.get("source_url"))
        if source not in {"https://docs.ozon.ru/api/seller/swagger.json", "official_evidence_bundle"}:
            raise ExecutionError("OFFICIAL_CONTRACT_SOURCE_REQUIRED")
        self.sha256 = digest(document)

    def resolve(self, schema):
        if not isinstance(schema, dict):
            raise ExecutionError("REQUEST_SCHEMA_UNAVAILABLE")
        if "$ref" in schema:
            ref = schema["$ref"]
            if ref in self.document.get("referenced_schemas", {}):
                schema = self.document["referenced_schemas"][ref]
            else:
                schema = self.document
                try:
                    for part in ref.removeprefix("#/").split("/"):
                        schema = schema[part]
                except (KeyError, TypeError):
                    raise ExecutionError("REQUEST_SCHEMA_REFERENCE_UNAVAILABLE") from None
            return self.resolve(schema)
        if "allOf" in schema:
            merged = {"type": "object", "properties": {}, "required": []}
            for part in schema["allOf"]:
                resolved = self.resolve(part)
                merged["properties"].update(resolved.get("properties", {}))
                merged["required"].extend(resolved.get("required", []))
            merged["properties"].update(schema.get("properties", {}))
            return merged
        return schema

    def request_schema(self, path):
        try:
            if self.document.get("source") == "official_evidence_bundle":
                operation = self.document["operations"][path]
                evidence = operation.get("evidence", [])
                if not any(str(x.get("url", "")).startswith(("https://dev.ozon.ru/", "https://docs.ozon.ru/"))
                           for x in evidence):
                    raise ExecutionError("OFFICIAL_ENDPOINT_EVIDENCE_REQUIRED")
                return self.resolve(operation["request_schema"])
            operation = self.document["paths"][path]["post"]
            for parameter in operation.get("parameters", []):
                if parameter.get("in") == "body":
                    return self.resolve(parameter["schema"])
            content = operation["requestBody"]["content"]
            return self.resolve(content["application/json"]["schema"])
        except (KeyError, TypeError):
            raise ExecutionError("REQUEST_SCHEMA_UNAVAILABLE") from None

    def validate(self, value, schema):
        schema = self.resolve(schema)
        kind = schema.get("type")
        if "enum" in schema and value not in schema["enum"]:
            raise ExecutionError("REQUEST_ENUM_MISMATCH")
        if kind == "object" or "properties" in schema:
            if not isinstance(value, dict):
                raise ExecutionError("REQUEST_OBJECT_REQUIRED")
            properties = schema.get("properties", {})
            if not set(schema.get("required", [])).issubset(value):
                raise ExecutionError("REQUEST_REQUIRED_FIELD_MISSING")
            # Refuse undocumented fields even where an OpenAPI generator permits
            # arbitrary additional properties.
            if not set(value).issubset(properties):
                raise ExecutionError("REQUEST_FIELD_NOT_IN_OFFICIAL_SCHEMA")
            for key, child in value.items():
                self.validate(child, properties[key])
        elif kind == "array":
            if not isinstance(value, list):
                raise ExecutionError("REQUEST_ARRAY_REQUIRED")
            if len(value) < schema.get("minItems", 0) or len(value) > schema.get("maxItems", 1000000):
                raise ExecutionError("REQUEST_ARRAY_LENGTH_INVALID")
            for child in value:
                self.validate(child, schema["items"])
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ExecutionError("REQUEST_BOOLEAN_REQUIRED")
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise ExecutionError("REQUEST_INTEGER_REQUIRED")
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (float, int)):
                raise ExecutionError("REQUEST_NUMBER_REQUIRED")
        elif kind == "string":
            if not isinstance(value, str):
                raise ExecutionError("REQUEST_STRING_REQUIRED")
        elif kind is None and not schema:
            raise ExecutionError("REQUEST_TYPE_UNVERIFIED")

    def payload(self, operation, item, observation, action_id=None, auto_add_date=None):
        amount = rubles(target(item))
        pid = int(item["product_id"])
        if operation == "price":
            path = PRICE_PATH
            schema = self.request_schema(path)
            price = observation["price"]["price"]
            old_price = money(price.get("old_price", 0))
            if old_price != 0 and old_price <= target(item):
                raise ExecutionError("APPROVED_OLD_PRICE_CONFLICT")
            row = {"offer_id": item["sku"], "product_id": pid,
                   "price": amount, "min_price": amount, "currency_code": "RUB",
                   "old_price": format(old_price, "f")}
            # Activation/renewal is done first through the independently
            # documented timer endpoint, then confirmed by readback. Omitted
            # automatic-promotion flags are not changed by this price request.
            payload = {"prices": [row]}
        elif operation in {"activate", "future"}:
            path = ACTION_PATH if operation == "activate" else FUTURE_PATH
            schema = self.request_schema(path)
            payload = {"action_id": int(action_id), "products": [
                {"product_id": pid, "action_price": {"amount": amount, "currency": "RUB"}}]}
            if operation == "future":
                if not auto_add_date:
                    raise ExecutionError("FUTURE_DATE_REQUIRED")
                payload["auto_add_date"] = auto_add_date
        elif operation == "timer":
            path = TIMER_PATH
            schema = self.request_schema(path)
            id_schema = self.resolve(self.resolve(schema["properties"]["product_ids"])["items"])
            payload = {"product_ids": [str(pid) if id_schema.get("type") == "string" else pid]}
        else:
            raise ExecutionError("OPERATION_NOT_ALLOWED")
        self.validate(payload, schema)
        return path, payload


def action_lifecycle(action, now):
    start, end = parse_time(action["date_start"]), parse_time(action["date_end"])
    return "future" if now < start else "expired" if now > end else "active"


def safe_action(action):
    return (action.get("action_type") in SAFE_ACTION_TYPES
            and action.get("is_voucher_action") is False
            and action.get("discount_type") == "CURRENCY"
            and money(action.get("discount_value", 0)) == 0)


def unique_row(rows, item):
    matches = [row for row in rows if product_id(row) == str(item["product_id"])]
    if len(matches) > 1:
        raise ExecutionError("DUPLICATE_ACTION_PRODUCT")
    if matches and matches[0].get("offer_id") not in (None, item["sku"]):
        raise ExecutionError("ACTION_SKU_PID_MISMATCH")
    return matches[0] if matches else None


def stock_available(row):
    stocks = row.get("stocks")
    if not isinstance(stocks, list):
        raise ExecutionError("STOCK_SCHEMA_UNKNOWN")
    total = Decimal(0)
    for stock in stocks:
        present, reserved = money(stock.get("present")), money(stock.get("reserved"))
        if present < 0 or reserved < 0:
            raise ExecutionError("NEGATIVE_STOCK")
        # v4 present is the observed available stock used by the existing
        # catalogue; reserved is retained independently, never added to it.
        total += present
    return total


def commercial_state(observation):
    price = observation["price"]["price"]
    protection = observation["protection"]
    return {"price": str(money(price["price"])), "min_price": str(money(price["min_price"])),
            "old_price": str(money(price.get("old_price", 0))),
            "currency_code": price.get("currency_code"),
            "auto_action_enabled": price.get("auto_action_enabled"),
            "auto_add_to_ozon_actions_list_enabled": price.get("auto_add_to_ozon_actions_list_enabled"),
            "min_price_for_auto_actions_enabled": protection.get("enabled"),
            "protection_active": protection.get("active"), "expired_at": protection.get("expired_at")}


def verified_record(item, observation, decision_id):
    fields = commercial_state(observation)
    return {"decision_id": decision_id, "observed_at": observation["observed_at"],
            "source": "direct_Ozon_API_readback", "product_id": item["product_id"],
            "fields": fields}


class LiveReader:
    def __init__(self, client):
        self.client = client

    def commercial(self, item, *, stocks=False):
        catalogue = {item["sku"]: str(item["product_id"])}
        rows, meta = scoped_rows(self.client, "/v5/product/info/prices", catalogue, kind="prices")
        if meta["status"] != "ok" or len(rows) != 1:
            raise ExecutionError("LIVE_PRICE_READ_INCOMPLETE")
        protection, protection_meta = collect_protection(self.client, catalogue)
        if protection_meta["status"] != "ok" or len(protection["rows"]) != 1:
            raise ExecutionError("LIVE_PROTECTION_READ_INCOMPLETE")
        result = {"sku": item["sku"], "product_id": item["product_id"], "observed_at": iso_now(),
                  "price": rows[0], "protection": protection["rows"][0]}
        if stocks:
            stock_rows, stock_meta = scoped_rows(self.client, "/v4/product/info/stocks", catalogue, kind="stocks")
            if stock_meta["status"] != "ok" or len(stock_rows) != 1:
                raise ExecutionError("LIVE_STOCK_READ_INCOMPLETE")
            result["stock"] = stock_rows[0]
        return result

    def membership(self, item, action_id):
        rows, meta = read_pages(self.client, "/v2/actions/products", {
            "action_id": int(action_id), "limit": 100, "last_id": ""}, keys=("products", "items"))
        if meta["status"] != "ok":
            raise ExecutionError("LIVE_PARTICIPANTS_READ_INCOMPLETE")
        return unique_row(rows, item)

    def observe(self, item, *, candidates=True):
        result = self.commercial(item, stocks=True)
        response = self.client.request("GET", "/v1/actions")
        actions = response.get("result") if isinstance(response, dict) else None
        if not isinstance(actions, list):
            raise ExecutionError("LIVE_ACTIONS_SCHEMA_UNKNOWN")
        ids = [str(action.get("id")) for action in actions]
        if len(ids) != len(set(ids)):
            raise ExecutionError("DUPLICATE_ACTION_ID")
        result["actions"] = {}
        allowed = {str(x) for x in item["allowed_action_ids"]}
        for action in actions:
            aid = str(action["id"])
            participant = self.membership(item, aid)
            candidate = None
            if candidates and aid in allowed:
                rows, meta = read_pages(self.client, "/v2/actions/candidates", {
                    "action_id": int(aid), "limit": 100, "last_id": ""}, keys=("products", "items"))
                if meta["status"] != "ok":
                    raise ExecutionError("LIVE_CANDIDATES_READ_INCOMPLETE")
                candidate = unique_row(rows, item)
            result["actions"][aid] = {"action": action, "participant": participant, "candidate": candidate}
        result["observed_at"] = iso_now()
        return result


def observation_from_snapshot(bundle, item):
    sku, pid = item["sku"], str(item["product_id"])
    def exact(name):
        rows = [row for row in bundle[name] if row.get("offer_id") == sku and product_id(row) == pid]
        if len(rows) != 1:
            raise ExecutionError("SNAPSHOT_IDENTITY_INCOMPLETE")
        return rows[0]
    protections = [r for r in bundle["protection"]["rows"]
                   if str(r.get("product_id")) == pid and r.get("mapped_offer_id") == sku]
    if len(protections) != 1:
        raise ExecutionError("SNAPSHOT_PROTECTION_MISSING")
    actions = {}
    for promotion in bundle["promotions"]:
        action = promotion["action"]
        groups = promotion["sections"]
        actions[str(action["id"])] = {
            "action": action,
            "participant": unique_row(groups["participants"]["rows"], item),
            "candidate": unique_row(groups["candidates"]["rows"], item)}
    return {"sku": sku, "product_id": item["product_id"],
            "observed_at": bundle["manifest"]["completed_at"],
            "price": exact("prices"), "stock": exact("stocks"),
            "protection": protections[0], "actions": actions}


def load_bundle(path, catalogue_path, *, now=None):
    from ozon_profit_catalog import validate_live_core
    path, now = Path(path), now or now_utc()
    bundle = {name: json.loads((path / (name + ".json")).read_text(encoding="utf-8"))
              for name in ("manifest", "products", "prices", "stocks", "promotions", "protection", "master")}
    validate_live_core(bundle["manifest"], bundle["products"], bundle["prices"])
    for name in ("products", "prices", "stocks", "promotions", "protection", "master"):
        section = bundle["manifest"].get("sections", {}).get(name, {})
        age = (now - parse_time(section.get("completed_at"))).total_seconds()
        if section.get("status") != "ok" or section.get("errors") or not -5 <= age <= MAX_AGE_SECONDS:
            raise ExecutionError("PREFLIGHT_SECTION_STALE_OR_INCOMPLETE")
    catalogue = json.loads(Path(catalogue_path).read_text(encoding="utf-8"))
    if (catalogue.get("source_mode") != "LIVE_READ_ONLY" or catalogue.get("snapshot_at") !=
            bundle["manifest"]["sections"]["prices"]["completed_at"]):
        raise ExecutionError("CATALOGUE_NOT_FROM_PREFLIGHT_SNAPSHOT")
    rows = catalogue.get("rows", [])
    if len({row["sku"] for row in rows}) != len(rows):
        raise ExecutionError("DUPLICATE_CATALOGUE_SKU")
    bundle["catalogue"] = {row["sku"]: row for row in rows}
    return bundle


def tariff_constraints(before, after):
    if before["price"].get("commissions") != after["price"].get("commissions"):
        return ["CURRENT_TARIFFS_CHANGED"]
    try:
        original_rate = money(before["price"]["acquiring"]) / money(before["price"]["price"]["price"])
        current_rate = money(after["price"]["acquiring"]) / money(after["price"]["price"]["price"])
        if abs(current_rate - original_rate) > Decimal("0.0001"):
            return ["ACQUIRING_RATE_CHANGED"]
    except (KeyError, SnapshotError, ArithmeticError):
        return ["ACQUIRING_RATE_UNVERIFIED"]
    return []


def execution_gate(item, observation, approved_observation, economic_check, *, now=None):
    now = now or now_utc()
    blockers, ready, deferred, skipped = [], [], [], []
    sku, pid = item["sku"], str(item["product_id"])
    price = observation["price"]
    if price.get("offer_id") != sku or product_id(price) != pid:
        blockers.append("SKU_PID_MISMATCH")
    amount = money(price["price"]["price"])
    if amount not in {before_price(item), target(item)}:
        blockers.append("CURRENT_PRICE_OUTSIDE_APPROVED_BEFORE_OR_TARGET")
    if money(item["experiment_floor_rub"]) != target(item):
        blockers.append("EXPERIMENT_BOUNDARY_DIFFERS_FROM_TARGET")
    if stock_available(observation["stock"]) <= 0:
        blockers.append("NO_AVAILABLE_STOCK")
    if price["price"].get("currency_code") != "RUB":
        blockers.append("NON_RUB_PRICE")
    if observation["protection"].get("active") is None:
        blockers.append("PROTECTION_STATUS_UNKNOWN")
    blockers.extend(tariff_constraints(approved_observation, observation))
    if economic_check.get("status") == "HOLD":
        blockers.extend(economic_check.get("blockers") or ["FINANCIAL_CHECK_HOLD"])
    allowed = {str(x) for x in item["allowed_action_ids"]}
    financial_ready = {str(x) for x in economic_check.get("ready_action_ids", [])}
    financial_deferred = {str(x) for x in economic_check.get("deferred_action_ids", [])}
    if not financial_ready.union(financial_deferred).issubset(allowed):
        blockers.append("FINANCIAL_CHECK_EXPANDED_ACTION_SCOPE")
    if item.get("strategy_code") == "A":
        if not set(active_members(approved_observation, now=now)).issubset(active_members(observation, now=now)):
            blockers.append("EXISTING_REQUIRED_ACTION_ALREADY_LOST")
    for aid, evidence in observation["actions"].items():
        action, participant = evidence["action"], evidence["participant"]
        lifecycle = action_lifecycle(action, now)
        if participant is not None and lifecycle == "active":
            if not safe_action(action):
                blockers.append("EXISTING_ACTION_TERMS_UNVERIFIED")
            if item.get("strategy_code") == "A":
                ceiling = participant.get("price_max_elastic") if action.get("action_type") == "ELASTIC_BOOSTING" else participant.get("max_action_price")
                if ceiling is None or target(item) > money(ceiling):
                    blockers.append("EXISTING_ACTION_OR_BOOST_NOT_PRESERVED")
    for aid in sorted(allowed):
        evidence = observation["actions"].get(aid)
        if evidence is None:
            skipped.append({"action_id": int(aid), "reason": "APPROVED_ACTION_NOT_AVAILABLE"})
            continue
        action = evidence["action"]
        row = evidence["participant"] or evidence["candidate"]
        if not safe_action(action):
            skipped.append({"action_id": int(aid), "reason": "ADDITIONAL_DISCOUNT_VOUCHER_OR_UNKNOWN_ACTION"})
            continue
        if row is None or row.get("is_quarantined") is True or row.get("failed") is True:
            skipped.append({"action_id": int(aid), "reason": "ACTION_CANDIDATE_UNAVAILABLE_OR_QUARANTINED"})
            continue
        cap = row.get("max_action_price")
        if cap is None or money(cap) <= 0 or target(item) > money(cap):
            skipped.append({"action_id": int(aid), "reason": "APPROVED_TARGET_ABOVE_CURRENT_ACTION_CAP"})
            continue
        lifecycle = action_lifecycle(action, now)
        if lifecycle == "future":
            deferred.append(int(aid))
        elif lifecycle == "expired":
            skipped.append({"action_id": int(aid), "reason": "APPROVED_ACTION_EXPIRED"})
        elif aid in financial_ready:
            ready.append(int(aid))
        elif evidence["participant"] is None:
            skipped.append({"action_id": int(aid), "reason": "ACTION_FINANCIAL_CHECK_NOT_READY"})
    if not ready and not deferred:
        blockers.append("NO_CURRENTLY_ELIGIBLE_APPROVED_ACTION")
    price_change = amount != target(item)
    future_only = not financial_ready and bool(financial_deferred)
    if price_change and not economic_check.get("price_write_allowed") and not future_only:
        blockers.append("PRICE_FINANCIAL_CHECK_NOT_READY")
    return {"status": "HOLD" if blockers else "DEFERRED" if future_only else "READY", "blockers": sorted(set(blockers)),
            "ready_action_ids": ready, "deferred_action_ids": deferred,
            "skipped_actions": skipped,
            "price_write_allowed": bool(economic_check.get("price_write_allowed")),
            "financial_check": economic_check.get("financial_check", {}),
            "full_profit_verified": False}


def plan_core(plan):
    from ozon_promo_batch import scope_digest
    return {"decision_id": plan["decision_id"], "scope_sha256": scope_digest(plan)}


class Journal:
    def __init__(self, path, plan):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.lock = self.path.with_suffix(self.path.suffix + ".lock").open("a")
        try:
            fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close()
            raise ExecutionError("ANOTHER_EXECUTOR_OWNS_JOURNAL") from None
        if self.path.exists():
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
            if self.data.get("approved_core_sha256") != digest(plan_core(plan)):
                self.close()
                raise ExecutionError("JOURNAL_APPROVAL_CORE_MISMATCH")
        else:
            self.data = {"schema_version": 1, "decision_id": plan["decision_id"],
                         "approved_core_sha256": digest(plan_core(plan)), "created_at": iso_now(),
                         "items": {}, "operations": []}
            self.save()

    def close(self):
        if not self.lock.closed:
            fcntl.flock(self.lock, fcntl.LOCK_UN)
            self.lock.close()

    def save(self):
        self.data["updated_at"] = iso_now()
        atomic_json(self.path, self.data)

    def operation(self, item, operation, path, payload, before, action_id=None):
        identity = digest({"decision_id": self.data["decision_id"], "sku": item["sku"],
                           "operation": operation, "path": path, "payload": payload})
        previous = next((x for x in self.data["operations"] if x["operation_id"] == identity), None)
        if previous is None:
            # A changed old_price or another payload field must not turn an
            # unresolved logical operation into a new network attempt.
            previous = next((x for x in self.data["operations"] if x["sku"] == item["sku"]
                             and x["operation"] == operation and x.get("action_id") == action_id
                             and x["state"] != "VERIFIED_BY_READBACK"), None)
        if previous is not None:
            return previous, False
        record = {"operation_id": identity, "sku": item["sku"], "product_id": item["product_id"],
                  "operation": operation, "action_id": action_id, "path": path, "payload": payload,
                  "payload_sha256": digest(payload), "state": "INTENT", "started_at": iso_now(),
                  "before": commercial_state(before), "events": [{"state": "INTENT", "at": iso_now()}]}
        self.data["operations"].append(record)
        self.save()  # durable checkpoint precedes the first network write
        return record, True

    def finish(self, record, state, **fields):
        record.update(state=state, **fields)
        record["events"].append({"state": state, "at": iso_now()})
        self.save()


def response_summary(response):
    if not isinstance(response, dict):
        return {"schema": type(response).__name__, "acknowledged": False}
    result = response.get("result", response)
    summary = {"schema_keys": sorted(response), "acknowledged": None}
    if isinstance(result, dict):
        for key in ("active_product_ids", "product_ids", "deactivated_product_ids", "deactivated_ids"):
            if isinstance(result.get(key), list):
                summary[key] = [x for x in result[key] if isinstance(x, int) or isinstance(x, str) and x.isdigit()]
        for key in ("rejected", "warnings", "errors"):
            if isinstance(result.get(key), list):
                summary[key + "_count"] = len(result[key])
                if key in ("rejected", "warnings"):
                    summary[key] = []
                    for row in result[key]:
                        if not isinstance(row, dict):
                            continue
                        pid, reason = row.get("product_id"), row.get("reason")
                        # Official ActionsProductsUpdate Rejected/Warning fields
                        # are uint64 product_id and string reason. Keep no raw
                        # context; malformed entries still count above.
                        if type(pid) is int and 0 <= pid < 2 ** 64 and isinstance(reason, str):
                            summary[key].append({"product_id": pid, "reason":
                                "".join(char for char in reason if char.isprintable())[:500]})
    # Only bounded documented promotion reasons are retained; generic error
    # messages and arbitrary response values remain excluded.
    return summary


class GuardedWriter:
    def __init__(self, plan, plan_path=None):
        self.plan, self.plan_path = plan, Path(plan_path or EXECUTION / "approved_plan.json")

    def note_verified(self, item, observation, *, verified=True):
        from ozon_promo_batch import load_approved_plan, scope_digest
        persisted = load_approved_plan(self.plan_path)
        if (persisted.get("enabled") is not True or persisted["decision_id"] != self.plan["decision_id"]
                or persisted["scope_sha256"] != self.plan["scope_sha256"]):
            raise ExecutionError("APPROVAL_CHANGED_DURING_EXECUTION")
        found = [row for row in persisted["items"] if row["sku"] == item["sku"]
                 and str(row["product_id"]) == str(item["product_id"])]
        if len(found) != 1:
            raise ExecutionError("VERIFIED_STATE_IDENTITY_MISMATCH")
        record = verified_record(item, observation, persisted["decision_id"])
        record["verified"] = verified
        found[0]["last_verified"] = record
        if scope_digest(persisted) != self.plan["scope_sha256"]:
            raise ExecutionError("VERIFIED_STATE_CHANGED_APPROVAL_SCOPE")
        atomic_json(self.plan_path, persisted)
        if verified:
            self.plan = persisted

    def mark_pending(self, item, observation):
        # Preserve a private copy of the exact fresh before-fields for the
        # one-use permit; legacy import exceptions see the pending marker.
        private = copy.deepcopy(self.plan)
        private_item = next(row for row in private["items"] if row["sku"] == item["sku"])
        private_item["last_verified"] = {**verified_record(item, observation, private["decision_id"]), "verified": True}
        self.note_verified(item, observation, verified=False)
        self.plan = private

    def write(self, item, operation, path, payload, action_id=None):
        from ozon_control_guard import approved_write
        import ozon_client
        runtime = copy.deepcopy(self.plan)
        runtime["requests"] = [{"request_id": digest({"path": path, "payload": payload}),
                                "sku": item["sku"], "operation": operation, "action_id": action_id,
                                "path": path, "payload": payload, "payload_sha256": digest(payload)}]
        with approved_write(runtime["decision_id"], item["sku"], operation, action_id, plan=runtime):
            return ozon_client._post(path, payload, retries=1)


def price_effect(item, observed):
    price = observed["price"]["price"]
    return money(price["price"]) == target(item) and money(price["min_price"]) == target(item)


def timer_effect(item, observed, *, now=None, renewal=False):
    now = now or now_utc()
    protection = observed["protection"]
    return (protection.get("enabled") is True
            and protection.get("active") is True and protection.get("expired_at") is not None
            and parse_time(protection["expired_at"]) >= now + (timedelta(days=29) if renewal else timedelta(hours=1)))


def active_members(observation, *, now=None):
    now = now or now_utc()
    return {aid: data for aid, data in observation.get("actions", {}).items()
            if data.get("participant") is not None and action_lifecycle(data["action"], now) == "active"}


def preservation_errors(item, before, after, required_actions):
    errors = []
    if not price_effect(item, after):
        errors.append("PRICE_OR_EXPERIMENT_MIN_READBACK_MISMATCH")
    if after["protection"].get("active") is not True:
        errors.append("PROTECTION_NOT_ACTIVE_AFTER_WRITE")
    before_price_fields, after_price_fields = before["price"]["price"], after["price"]["price"]
    for key in ("old_price", "auto_action_enabled", "auto_add_to_ozon_actions_list_enabled"):
        if before_price_fields.get(key) != after_price_fields.get(key):
            errors.append("UNAPPROVED_COMMERCIAL_FIELD_CHANGED")
    current = active_members(after)
    if not {str(x) for x in required_actions}.issubset(current):
        errors.append("REQUESTED_ACTIVE_PARTICIPATION_NOT_OBSERVED")
    if item.get("strategy_code") == "A":
        existing = active_members(before)
        if not set(existing).issubset(current):
            errors.append("EXISTING_ACTION_LOST")
        for aid in set(existing).intersection(current):
            previous = existing[aid]["participant"]
            present = current[aid]["participant"]
            previous_boost = previous.get("current_boost", previous.get("current"))
            present_boost = present.get("current_boost", present.get("current"))
            if previous_boost is not None:
                if present_boost is None or money(present_boost) < money(previous_boost):
                    errors.append("EXISTING_BOOST_LOST_OR_UNVERIFIED")
    return sorted(set(errors))


def rollback_eligibility(item, operation, observed):
    """Only expose a possible rollback; never automatically undo other SKUs."""
    authorization = item.get("rollback")
    if not isinstance(authorization, dict) or authorization.get("approved") is not True:
        return {"allowed": False, "reason": "NO_EXPLICIT_ROLLBACK_RECORD"}
    recorded_after = operation.get("after")
    if recorded_after is None or commercial_state(observed) != recorded_after:
        return {"allowed": False, "reason": "CURRENT_STATE_NOT_EXACTLY_OURS"}
    before = operation.get("before")
    if authorization.get("before") != before:
        return {"allowed": False, "reason": "ROLLBACK_BEFORE_NOT_APPROVED"}
    return {"allowed": True, "before": before, "execution": "separate_explicit_guarded_operation_required"}


class Executor:
    def __init__(self, plan, bundle, contract, journal, reader=None, writer=None,
                 evaluator=None, service_evidence=None, sleep=time.sleep):
        self.plan, self.bundle, self.contract, self.journal = plan, bundle, contract, journal
        self.reader, self.writer, self.sleep = reader, writer, sleep
        self.service_evidence = service_evidence or {}
        if evaluator is None:
            from ozon_promo_batch import evaluate_item
            evaluator = evaluate_item
        self.evaluator = evaluator

    def perform(self, item, operation, observation, *, action_id=None):
        path, payload = self.contract.payload(operation, item, observation, action_id)
        record, new = self.journal.operation(item, operation, path, payload, observation, action_id)

        def read_and_check():
            observed = self.reader.commercial(item)
            if operation == "price":
                success = price_effect(item, observed) and observed["protection"].get("active") is True
            elif operation == "timer":
                success = timer_effect(item, observed, now=parse_time(record["started_at"]), renewal=True)
            else:
                participant = self.reader.membership(item, action_id)
                observed["action_readback"] = {"action_id": action_id, "participant": participant}
                success = price_effect(item, observed) and participant is not None
            if hasattr(self.writer, "note_verified"):
                # Save the actual observed fields even on a partial effect.
                # Unresolved writes remain ineligible for legacy imports.
                self.writer.note_verified(item, observed, verified=success)
            return observed, success

        if not new:
            # An unresolved durable INTENT is reconciled before any possible
            # repeat. This invocation never resends an ambiguous operation.
            observed, success = read_and_check()
            if success:
                self.journal.finish(record, "VERIFIED_BY_READBACK", after=commercial_state(observed),
                                    readback=observed, resumed=True)
                if hasattr(self.writer, "note_verified"):
                    self.writer.note_verified(item, observed)
                return observed, True
            self.journal.finish(record, "HOLD_UNRESOLVED_PREVIOUS_OPERATION", readback=observed)
            return observed, False
        try:
            if hasattr(self.writer, "mark_pending"):
                self.writer.mark_pending(item, observation)
            response = self.writer.write(item, operation, path, payload, action_id)
            record["response_summary"] = response_summary(response)
            record["request_returned_at"] = iso_now()
            self.journal.save()
        except Exception as exc:
            self.journal.finish(record, "AMBIGUOUS_RESPONSE", error=safe_error(exc))
        observed = observation
        for attempt in range(3):
            try:
                observed, success = read_and_check()
                if success:
                    self.journal.finish(record, "VERIFIED_BY_READBACK", after=commercial_state(observed),
                                        readback=observed)
                    if hasattr(self.writer, "note_verified"):
                        self.writer.note_verified(item, observed)
                    return observed, True
            except Exception as exc:
                record["readback_error"] = safe_error(exc)
            if attempt < 2:
                self.sleep(2 * (attempt + 1))
        self.journal.finish(record, "HOLD_READBACK_NOT_CONFIRMED", readback=observed)
        return observed, False

    def run_item(self, item, *, execute=False):
        sku = item["sku"]
        approved_observation = observation_from_snapshot(self.bundle, item)
        current = self.reader.observe(item) if execute else approved_observation
        pending = [record for record in self.journal.data["operations"] if record["sku"] == sku
                   and record["state"] != "VERIFIED_BY_READBACK"]
        if execute and hasattr(self.writer, "note_verified"):
            self.writer.note_verified(item, current, verified=not pending)
        unresolved = []
        for record in pending:
            effect = (timer_effect(item, current, now=parse_time(record["started_at"]), renewal=True) if record["operation"] == "timer" else
                      price_effect(item, current) and current["protection"].get("active") is True)
            if record["operation"] == "activate":
                effect = effect and str(record["action_id"]) in active_members(current)
            if effect:
                self.journal.finish(record, "VERIFIED_BY_READBACK", after=commercial_state(current),
                                    readback=current, resumed=True)
            else:
                unresolved.append(record["operation_id"])
        if unresolved:
            return {"sku": sku, "product_id": item["product_id"], "status": "HOLD_PENDING_OPERATION",
                    "unresolved_operation_ids": unresolved, "before": current, "full_profit_verified": False}
        if pending and execute and hasattr(self.writer, "note_verified"):
            self.writer.note_verified(item, current)
        prior_prices = [record for record in self.journal.data["operations"]
                        if record["sku"] == sku and record["operation"] == "price"
                        and record["state"] == "VERIFIED_BY_READBACK" and record.get("after")]
        if prior_prices:
            owned = prior_prices[-1]["after"]
            actual = commercial_state(current)
            protected = ("price", "min_price", "old_price", "currency_code", "auto_action_enabled",
                         "auto_add_to_ozon_actions_list_enabled")
            if any(actual.get(key) != owned.get(key) for key in protected):
                return {"sku": sku, "product_id": item["product_id"], "status": "HOLD_OWNER_DRIFT",
                        "reason": "A verified price was later changed; do not restore the old approved target.",
                        "before": current, "full_profit_verified": False}
        catalogue_row = copy.deepcopy(self.bundle["catalogue"][sku])
        # Tariff drift is independently blocked below. The economic evaluator
        # receives the observed price, inventory and action lifecycle now.
        catalogue_row["current_price"] = float(money(current["price"]["price"]["price"]))
        catalogue_row["observed_min_price"] = float(money(current["price"]["price"]["min_price"]))
        catalogue_row["stock"] = {"status": "OBSERVED", "rows": [current["stock"]]}
        current_actions = []
        for aid, evidence in current["actions"].items():
            action, raw = evidence["action"], evidence["participant"] or evidence["candidate"]
            if raw is None:
                continue
            current_actions.append({"action_id": int(aid), "title": action.get("title"),
                "date_start": action.get("date_start"), "date_end": action.get("date_end"),
                "action_type": action.get("action_type"),
                "cap_rub": float(money(raw["max_action_price"])) if raw.get("max_action_price") is not None else None,
                "group": "participants" if evidence["participant"] is not None else "candidates",
                "additional_discount_or_unknown_type": not safe_action(action),
                "quarantined_or_failed": raw.get("is_quarantined") is True or raw.get("failed") is True,
                "eligible_for_price_comparison": (safe_action(action) and raw.get("max_action_price") is not None
                    and money(raw["max_action_price"]) > 0 and raw.get("is_quarantined") is not True
                    and raw.get("failed") is not True),
                "current_boost": raw.get("current_boost", raw.get("current")),
                "max_boost": raw.get("max_boost"),
                "price_max_elastic": float(money(raw["price_max_elastic"])) if raw.get("price_max_elastic") is not None else None})
        catalogue_row["actions"] = current_actions
        service_evidence = self.service_evidence.get(sku)
        if (item["strategy_code"] == "B" and service_evidence is None
                and before_price(item) == target(item)
                and all(str(aid) in current["actions"] and safe_action(current["actions"][str(aid)]["action"])
                        for aid in item["allowed_action_ids"])):
            service_evidence = {
                "sku": sku, "verified": True, "kind": "official_no_incremental_cost",
                "no_incremental_seller_cost": True, "seller_price_unchanged": True,
                "action_ids": item["allowed_action_ids"],
                "source_reference": "https://seller.ozon.ru/media/boost/kak-prodavcu-uchastvovat-v-akciyah-na-ozon/ (official participation is free; seller funds discounts); exact live safe-action type/CURRENCY/zero additional discount; observed_at=" + current["observed_at"],
                "interpretation": "Official free enrollment plus unchanged approved seller price and no additional-discount mechanism. This confirms no added participation fee or seller discount, not zero total service costs or complete profit."}
        economic_check = self.evaluator(item, catalogue_row, service_evidence)
        gate = execution_gate(item, current, approved_observation, economic_check)
        result = {"sku": sku, "product_id": item["product_id"], "target_price": rubles(target(item)),
                  "experiment_floor_rub": rubles(target(item)), "gate": gate,
                  "status": ("HOLD" if gate["blockers"] else "DEFERRED_FUTURE_ONLY"
                             if not gate["ready_action_ids"] and gate["deferred_action_ids"] else "PREPARED"),
                  "full_profit_verified": False, "before": current, "future_preparations": [],
                  "service_evidence": service_evidence}
        for aid in gate["deferred_action_ids"]:
            action = current["actions"][str(aid)]["action"]
            dates = [value for value in action.get("auto_add_dates", []) if parse_time(value) > now_utc()]
            prepared = {"action_id": aid, "status": "DEFERRED", "active": False}
            if dates:
                try:
                    path, payload = self.contract.payload("future", item, current, aid, min(dates, key=parse_time))
                    prepared.update(status="FUTURE_REQUEST_PREPARED_NOT_SENT", path=path, payload=payload)
                except ExecutionError as exc:
                    prepared["reason"] = safe_error(exc)["code"]
            result["future_preparations"].append(prepared)
        requests = []
        if not gate["blockers"] and gate["ready_action_ids"]:
            operations = []
            if not timer_effect(item, current):
                operations.append(("timer", None))
            if not price_effect(item, current):
                operations.append(("price", None))
            operations.extend(("activate", aid) for aid in gate["ready_action_ids"]
                              if str(aid) not in active_members(current))
            for operation, aid in operations:
                path, payload = self.contract.payload(operation, item, current, aid)
                requests.append({"sku": sku, "operation": operation, "action_id": aid,
                                 "path": path, "payload": payload, "payload_sha256": digest(payload)})
        result["prepared_requests"] = requests
        if gate["blockers"] or not execute:
            return result
        if not gate["ready_action_ids"] and gate["deferred_action_ids"]:
            result["status"] = "DEFERRED_FUTURE_ONLY"
            return result
        before = current
        if not timer_effect(item, current):
            current, verified = self.perform(item, "timer", current)
            if not verified:
                result.update(status="HOLD_PROTECTION_READBACK", after=current)
                return result
        if not price_effect(item, current):
            current, verified = self.perform(item, "price", current)
            if not verified:
                result.update(status="HOLD_PRICE_READBACK", after=current)
                return result
        for aid in gate["ready_action_ids"]:
            if str(aid) in active_members(before):
                continue
            current, verified = self.perform(item, "activate", current, action_id=aid)
            if not verified:
                result.update(status="HOLD_ACTION_READBACK", after=current, held_action_id=aid)
                return result
        after = self.reader.observe(item, candidates=False)
        errors = preservation_errors(item, before, after, gate["ready_action_ids"])
        result.update(status="HOLD_FINAL_READBACK" if errors else "VERIFIED", after=after,
                      readback_errors=errors, last_verified=verified_record(item, after, self.plan["decision_id"]))
        return result

    def run(self, *, execute=False, only_skus=None):
        if execute and (self.plan.get("approved") is not True or self.plan.get("enabled") is not True):
            raise ExecutionError("APPROVED_BATCH_NOT_ENABLED")
        all_skus = {item["sku"] for item in self.plan["items"]}
        selected = set(only_skus or all_skus)
        if not selected.issubset(all_skus):
            raise ExecutionError("SKU_OUTSIDE_APPROVED_BATCH")
        results = []
        for item in self.plan["items"]:
            if item["sku"] not in selected:
                continue
            prior_writes = len(self.journal.data["operations"])
            try:
                result = self.run_item(item, execute=execute)
            except Exception as exc:
                result = {"sku": item["sku"], "product_id": item["product_id"], "status": "HOLD",
                          "error": safe_error(exc), "full_profit_verified": False}
            result["marketplace_write_attempts"] = len(self.journal.data["operations"]) - prior_writes
            result["completed_at"] = iso_now()
            self.journal.data["items"][item["sku"]] = result
            self.journal.save()
            results.append(result)
            if execute:
                print(canonical({key: result[key] for key in ("sku", "status", "marketplace_write_attempts")}), flush=True)
        return {"schema_version": 1, "decision_id": self.plan["decision_id"],
                "mode": "EXECUTE_APPROVED" if execute else "PREPARE_NO_WRITE",
                "generated_at": iso_now(), "contract_sha256": self.contract.sha256,
                "approved_core_sha256": digest(plan_core(self.plan)), "items": results,
                "counts": dict(__import__("collections").Counter(row["status"] for row in results)),
                "marketplace_write_attempts": sum(row["marketplace_write_attempts"] for row in results),
                "full_profit_verified": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=EXECUTION / "approved_plan.json")
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--catalogue", type=Path, required=True)
    parser.add_argument("--contract", type=Path, default=EXECUTION / "official_contract.json")
    parser.add_argument("--journal", type=Path, default=EXECUTION / "execution_journal.json")
    parser.add_argument("--report", type=Path, default=EXECUTION / "execution_report.json")
    parser.add_argument("--service-evidence", type=Path, default=EXECUTION / "service_evidence.json")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--only-sku", action="append")
    args = parser.parse_args(argv)
    journal = None
    try:
        from ozon_promo_batch import load_approved_plan
        plan = load_approved_plan(args.plan)
        bundle = load_bundle(args.snapshot, args.catalogue)
        contract = OfficialContract(json.loads(args.contract.read_text(encoding="utf-8")))
        journal = Journal(args.journal, plan)
        reader = writer = None
        if args.execute:
            from config import config
            client = ReadOnlyOzonClient(config.ozon_client_id, config.ozon_api_key,
                                       base_url=config.ozon_api_base, timeout=config.request_timeout_seconds)
            reader, writer = LiveReader(client), GuardedWriter(plan, args.plan)
        service_evidence = json.loads(args.service_evidence.read_text(encoding="utf-8")) if args.service_evidence.is_file() else {}
        service_evidence = service_evidence.get("evidence_by_sku", service_evidence.get("by_sku", service_evidence))
        executor = Executor(plan, bundle, contract, journal, reader, writer, service_evidence=service_evidence)
        report = executor.run(execute=args.execute, only_skus=args.only_sku)
        atomic_json(args.report, report)
        print(canonical({key: report[key] for key in ("decision_id", "mode", "counts", "marketplace_write_attempts")}))
        return 2 if any(row["status"].startswith("HOLD") for row in report["items"]) else 0
    except Exception as exc:
        print(canonical({"status": "FAILED", "error": safe_error(exc)}))
        return 1
    finally:
        if journal is not None:
            journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
