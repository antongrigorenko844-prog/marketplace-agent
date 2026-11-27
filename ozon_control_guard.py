"""Scoped Ozon commercial-write control for a persisted owner-approved plan.

Normal read/stock/content-only endpoints are unchanged. A missing, empty or
explicitly disabled draft plan leaves legacy behaviour unchanged. Once enabled,
managed commercial writes need a single-use permit bound to an exact approved
item and request. This is application coordination, not a credential boundary.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
import logging
from pathlib import Path
import re
from typing import Any

_DEFAULT_PLAN_PATH = Path(__file__).resolve().parent / "data/ozon_promo_execution_20261127/approved_plan.json"
_DEFAULT_BF4_PATH = Path(__file__).resolve().parent / "data/ozon_black_friday_pilot_20261127/approved_plan.json"
PLAN_PATH = _DEFAULT_PLAN_PATH
BF4_PATH = _DEFAULT_BF4_PATH
LEGACY_DECISION_ID = "ozon-promo-96-20261127"
BF4_DECISION_ID = "ozon-bf4-20261127"
EXPECTED_MANAGED_COUNT = 96
logger = logging.getLogger("marketplace-agent.ozon.control")

MONEY_FIELDS = frozenset({"price", "min_price", "old_price", "premium_price"})
FLAG_FIELDS = frozenset({"auto_action_enabled", "auto_add_to_ozon_actions_list_enabled",
                         "min_price_for_auto_actions_enabled", "price_strategy_enabled"})
COMMERCIAL_FIELDS = MONEY_FIELDS | FLAG_FIELDS | {"currency_code"}
READ_COMMERCIAL = frozenset({
    "/v1/actions/products", "/v2/actions/products", "/v1/actions/candidates", "/v2/actions/candidates",
    "/v2/actions/auto-add/products/list", "/v2/actions/auto-add/products/candidates",
    "/v1/product/action/timer/status", "/v1/product/import/info",
})
ACTION_MUTATIONS = frozenset({
    "/v1/actions/products/activate", "/v2/actions/products/activate",
    "/v1/actions/products/deactivate", "/v2/actions/products/deactivate",
    "/v1/actions/products/update", "/v2/actions/products/update",
})
TIMER_MUTATIONS = frozenset({"/v1/product/action/timer/update"})


class OzonControlError(RuntimeError):
    """Safe invariant failure; never contains credentials or request bodies."""


def _fail(code: str):
    raise OzonControlError("Ozon control: " + code)


def _sku(value: Any) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        _fail("invalid_exact_offer_id")
    return value


def _pid(value: Any) -> int:
    if isinstance(value, bool) or not re.fullmatch(r"[1-9][0-9]*", str(value)):
        _fail("invalid_product_or_action_id")
    return int(value)


def _money(value: Any) -> Decimal:
    if isinstance(value, dict):
        if set(value) - {"amount", "currency"} or "amount" not in value or value.get("currency") != "RUB":
            _fail("unsupported_money_object_or_currency")
        value = value["amount"]
    if value is None or isinstance(value, bool):
        _fail("invalid_monetary_value")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        _fail("invalid_monetary_value")
    if not result.is_finite() or result < 0:
        _fail("invalid_monetary_value")
    return result


def _target(item: dict) -> Decimal:
    value = _money(item.get("target_price", item.get("target_price_rub")))
    if "target_price_rub" in item and _money(item["target_price_rub"]) != value:
        _fail("conflicting_approved_target_alias")
    return value


def _flag(value: Any) -> bool:
    if value is True or value == "ENABLED":
        return True
    if value is False or value == "DISABLED":
        return False
    _fail("invalid_commercial_flag")


def _canonical(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError):
        _fail("noncanonical_request_or_plan")


def payload_digest(payload: Any) -> str:
    return hashlib.sha256(_canonical(payload).encode("utf-8")).hexdigest()


def _kind(path: str) -> str | None:
    if not isinstance(path, str) or not path.startswith("/") or "?" in path or "#" in path:
        _fail("invalid_endpoint_path")
    if path in READ_COMMERCIAL or re.fullmatch(r"/v[0-9]+/product/info/prices", path):
        return None
    if re.fullmatch(r"/v[0-9]+/product/import/prices", path):
        return "price"
    if re.fullmatch(r"/v[0-9]+/product/import", path):
        return "import"
    if path in ACTION_MUTATIONS:
        return "action"
    if path in TIMER_MUTATIONS:
        return "timer"
    if any(x in path for x in ("/actions/", "/product/action/", "/product/import/", "/price", "/pricing")):
        return "unknown_commercial"
    return None


def _items(plan: dict) -> dict[str, dict]:
    rows = plan.get("items")
    decision = plan.get("decision_id")
    if decision not in {LEGACY_DECISION_ID, BF4_DECISION_ID}:
        _fail("unregistered_approval_decision")
    count = 4 if decision == BF4_DECISION_ID else EXPECTED_MANAGED_COUNT
    if not isinstance(rows, list) or len(rows) != count:
        _fail("approved_scope_must_contain_" + str(count) + "_items")
    result, pids = {}, set()
    for row in rows:
        if not isinstance(row, dict):
            _fail("invalid_approved_item")
        sku, pid = _sku(row.get("sku")), _pid(row.get("product_id"))
        if sku in result or pid in pids:
            _fail("duplicate_approved_identity")
        target, floor = _target(row), _money(row.get("experiment_floor_rub"))
        if target <= 0 or floor != target:
            _fail("approved_target_floor_mismatch")
        ids = row.get("allowed_action_ids")
        if not isinstance(ids, list):
            _fail("missing_approved_action_allowlist")
        if len({_pid(x) for x in ids}) != len(ids):
            _fail("duplicate_approved_action_id")
        result[sku] = row
        pids.add(pid)
    return result


def _approval_digest(plan: dict) -> str:
    """Use the financial manifest's canonical immutable scope, including evidence."""
    try:
        from ozon_promo_batch import scope_digest
        return scope_digest(plan)
    except Exception:
        _fail("approval_digest_unavailable")


def _validate_active(plan: dict):
    if plan.get("schema_version") != 1 or plan.get("approved") is not True or plan.get("enabled") is not True:
        _fail("plan_not_explicitly_enabled_and_approved")
    if not isinstance(plan.get("decision_id"), str) or not plan["decision_id"]:
        _fail("missing_decision_id")
    _items(plan)
    # The owner-approved financial/selection manifest has its own canonical
    # validator; no second pricing policy is invented inside the transport.
    try:
        if plan["decision_id"] == BF4_DECISION_ID:
            from ozon_black_friday_pilot import validate_approved_plan
        else:
            from ozon_promo_batch import validate_approved_plan
        validate_approved_plan(plan)
    except ImportError:
        _fail("approval_validator_unavailable")
    except OzonControlError:
        raise
    except Exception:
        _fail("approved_manifest_validation_failed")


def _registered_path(decision_id: str) -> Path:
    if decision_id == LEGACY_DECISION_ID:
        return PLAN_PATH
    if decision_id == BF4_DECISION_ID:
        # An explicitly redirected control store must never accidentally read
        # the production pilot. Tests and isolated tools may redirect BF4_PATH
        # explicitly; otherwise the optional decision stays in that same store.
        if PLAN_PATH != _DEFAULT_PLAN_PATH and BF4_PATH == _DEFAULT_BF4_PATH:
            return PLAN_PATH.parent / "ozon_black_friday_pilot_20261127/approved_plan.json"
        return BF4_PATH
    _fail("unregistered_approval_decision")


def _load_active_plan(decision_id: str = LEGACY_DECISION_ID) -> dict | None:
    try:
        raw = _registered_path(decision_id).read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return None
    except OSError:
        _fail("approval_file_unreadable")
    if not raw.strip():
        return None
    try:
        plan = json.loads(raw)
    except (ValueError, TypeError):
        _fail("approval_file_invalid_json")
    if plan in ({}, []):
        return None
    if not isinstance(plan, dict):
        _fail("approval_file_not_object")
    if plan.get("enabled") is False:
        return None
    if "enabled" not in plan and not plan.get("items"):
        return None
    if plan.get("decision_id") != decision_id:
        _fail("registered_plan_decision_mismatch")
    _validate_active(plan)
    return plan


def superseding_decision(decision_id: str, sku: str) -> str | None:
    """Return the enabled later owner decision, independent of campaign expiry.

    The executor can skip these legacy items before creating journal entries.
    The same check is repeated at permit creation and at the transport boundary.
    """
    if decision_id != LEGACY_DECISION_ID:
        return None
    from ozon_black_friday_pilot import FIXED_SCOPE
    if sku not in FIXED_SCOPE:
        return None
    return BF4_DECISION_ID if _load_active_plan(BF4_DECISION_ID) is not None else None


def _require_pilot_overlap(legacy: dict | None, pilot: dict):
    if legacy is None:
        _fail("pilot_requires_enabled_legacy_control")
    old_items, new_items = _items(legacy), _items(pilot)
    for sku, item in new_items.items():
        if sku not in old_items or _pid(old_items[sku]["product_id"]) != _pid(item["product_id"]):
            _fail("pilot_identity_not_in_frozen_legacy_scope")


def _legacy_indices_with_runtime_overlay(legacy: dict, pilot: dict | None):
    by_sku, by_pid = _indices(legacy)
    if pilot is not None:
        _require_pilot_overlap(legacy, pilot)
        for sku, item in _items(pilot).items():
            copied = deepcopy(by_sku[sku])
            state = item.get("last_verified") or item.get("initial_verified")
            copied["last_verified"] = deepcopy(state) if isinstance(state, dict) else {
                "decision_id": BF4_DECISION_ID, "verified": False,
            }
            by_sku[sku] = copied
    return by_sku, by_pid


def _indices(plan: dict):
    by_sku = _items(plan)
    return by_sku, {_pid(v["product_id"]): key for key, v in by_sku.items()}


def _identity(row: dict, by_sku: dict, by_pid: dict) -> tuple[str | None, int | None, bool]:
    if not isinstance(row, dict):
        _fail("mutation_row_not_object")
    sku = _sku(row["offer_id"]) if "offer_id" in row else None
    ids = [_pid(row[key]) for key in ("product_id", "id") if key in row]
    if len(set(ids)) > 1:
        _fail("conflicting_product_identity_fields")
    pid = ids[0] if ids else None
    if sku is None and pid is None:
        _fail("mutation_identity_missing")
    via_sku, via_pid = by_sku.get(sku), by_pid.get(pid)
    if via_sku and pid is not None and _pid(via_sku["product_id"]) != pid:
        _fail("managed_sku_product_id_mismatch")
    if via_pid and sku is not None and via_pid != sku:
        _fail("managed_product_id_offer_mismatch")
    if via_sku:
        return sku, _pid(via_sku["product_id"]), True
    if via_pid:
        return via_pid, pid, True
    return sku, pid, False


def _effective_fields(item: dict, decision_id: str) -> dict | None:
    state = item.get("last_verified") or item.get("initial_verified")
    if not isinstance(state, dict) or state.get("decision_id") != decision_id or not state.get("source"):
        return None
    if state.get("verified") is False or not isinstance(state.get("fields"), dict):
        return None
    try:
        observed = datetime.fromisoformat(str(state.get("observed_at", "")).replace("Z", "+00:00"))
    except ValueError:
        return None
    return state["fields"] if observed.tzinfo is not None else None


def _same_field(key: str, supplied: Any, observed: Any) -> bool:
    try:
        if key in MONEY_FIELDS:
            return _money(supplied) == _money(observed)
        if key in FLAG_FIELDS:
            return _flag(supplied) == _flag(observed)
        return isinstance(supplied, str) and supplied == observed
    except OzonControlError:
        return False


def _content_pending(item: dict) -> bool:
    state = item.get("last_verified") or item.get("initial_verified") or {}
    return not isinstance(state, dict) or state.get("verified") is False


def _safe_content_import(row: dict, item: dict, live_fields: dict | None) -> bool:
    fields = COMMERCIAL_FIELDS.intersection(row)
    unknown = [k for k in row if ("price" in k or "currency" in k or "discount" in k or k.startswith("auto_"))
               and k not in COMMERCIAL_FIELDS]
    if unknown:
        return False
    if not fields:
        return True
    if _content_pending(item):
        return False  # A pending/uncertain mutation needs executor reconciliation.
    return bool(live_fields is not None and all(key in live_fields and _same_field(key, row[key], live_fields[key]) for key in fields))


def _live_import_fields(skus: list[str], by_sku: dict, reader) -> dict[str, dict]:
    """One scoped read prevents stale full-card imports from undoing owner edits."""
    if not skus or reader is None:
        return {}
    try:
        rows = reader(skus)
        if not isinstance(rows, list):
            return {}
        result, seen, invalid = {}, set(), set()
        wanted = set(skus)
        for row in rows:
            if not isinstance(row, dict):
                return {}
            sku = _sku(row.get("offer_id"))
            if sku not in wanted:
                continue
            if sku in seen:
                invalid.add(sku)
            seen.add(sku)
            pid = _pid(row.get("product_id"))
            if pid != _pid(by_sku[sku]["product_id"]):
                invalid.add(sku)
                continue
            price = row.get("price")
            if not isinstance(price, dict) or price.get("currency_code") != "RUB":
                invalid.add(sku)
                continue
            result[sku] = {key: value for key, value in price.items() if key in COMMERCIAL_FIELDS}
        return {sku: value for sku, value in result.items() if sku not in invalid}
    except Exception:
        logger.warning("Ozon managed full-import live commercial check unavailable; affected rows skipped")
        return {}


def _list_payload(kind: str, payload: dict) -> tuple[str, list, bool]:
    if not isinstance(payload, dict):
        _fail("mutation_payload_not_object")
    if kind == "price":
        key, scalar = "prices", False
    elif kind == "import":
        key, scalar = "items", False
    else:
        candidates = [key for key in ("product_ids", "products") if key in payload]
        if len(candidates) != 1:
            _fail("mutation_product_scope_unknown")
        key, scalar = candidates[0], candidates[0] == "product_ids"
        if kind == "action":
            _pid(payload.get("action_id"))
    rows = payload.get(key)
    if not isinstance(rows, list) or not rows:
        _fail("mutation_product_scope_empty_or_malformed")
    return key, rows, scalar


@dataclass
class _Permit:
    decision_id: str
    sku: str
    operation: str
    action_id: int | None
    request: dict
    runtime_plan: dict
    approval_core_sha256: str
    consumed: bool = False


_CURRENT_PERMIT: ContextVar[_Permit | None] = ContextVar("ozon_approved_commercial_request", default=None)


def _request_records(plan: dict, sku: str) -> list[dict]:
    records = []
    for record in plan.get("requests") or []:
        if isinstance(record, dict) and record.get("sku") == sku:
            records.append(record)
    for record in _items(plan)[sku].get("requests") or []:
        if isinstance(record, dict):
            if record.get("sku", sku) != sku:
                _fail("request_record_scope_mismatch")
            records.append({**record, "sku": sku})
    return records


def _new_permit(decision_id: str, sku: str, *, plan: dict | None,
                operation: str | None = None, action_id: int | None = None,
                request_id: str | None = None) -> _Permit:
    if _CURRENT_PERMIT.get() is not None:
        _fail("nested_permit_not_allowed")
    persisted = _load_active_plan(decision_id)
    if persisted is None:
        _fail("approved_request_requires_enabled_persisted_plan")
    runtime = deepcopy(plan if plan is not None else persisted)
    _validate_active(runtime)
    if decision_id != persisted["decision_id"] or decision_id != runtime["decision_id"]:
        _fail("permit_decision_id_mismatch")
    core = _approval_digest(persisted)
    if core != _approval_digest(runtime):
        _fail("runtime_approved_scope_or_targets_changed")
    sku = _sku(sku)
    if sku not in _items(runtime):
        _fail("permit_sku_outside_approved_scope")
    if superseding_decision(decision_id, sku) is not None:
        _fail("approved_item_superseded_by_later_owner_decision")
    if decision_id == BF4_DECISION_ID:
        _require_pilot_overlap(_load_active_plan(), persisted)
    aid = _pid(action_id) if action_id is not None else None
    records = _request_records(runtime, sku)
    if request_id is not None:
        records = [r for r in records if r.get("request_id") == request_id]
    else:
        records = [r for r in records if r.get("operation") == operation
                   and (_pid(r["action_id"]) if r.get("action_id") is not None else None) == aid]
    if len(records) != 1:
        _fail("approved_request_record_missing_or_ambiguous")
    record = records[0]
    op = record.get("operation")
    if op not in ("price", "activate", "timer"):
        _fail("operation_not_approved_for_this_executor")
    record_aid = _pid(record["action_id"]) if record.get("action_id") is not None else None
    if record.get("payload_sha256") is not None and record["payload_sha256"] != payload_digest(record.get("payload")):
        _fail("request_record_digest_mismatch")
    permit = _Permit(decision_id, sku, op, record_aid, deepcopy(record), runtime, core)
    _validate_permitted_payload(permit, record.get("path"), record.get("payload"))
    return permit


@contextmanager
def approved_write(decision_id: str, sku: str, operation: str, action_id: int | None = None,
                   *, plan: dict | None = None):
    """One exact recorded mutation; network retry requires a new readback/intent."""
    permit = _new_permit(decision_id, sku, plan=plan, operation=operation, action_id=action_id)
    token = _CURRENT_PERMIT.set(permit)
    try:
        yield
    finally:
        _CURRENT_PERMIT.reset(token)


@contextmanager
def approved_request(decision_id: str, sku: str, request_id: str, *, plan: dict | None = None):
    permit = _new_permit(decision_id, sku, plan=plan, request_id=request_id)
    token = _CURRENT_PERMIT.set(permit)
    try:
        yield
    finally:
        _CURRENT_PERMIT.reset(token)


def _validate_permitted_payload(permit: _Permit, path: str, payload: dict):
    kind = _kind(path)
    expected = {"price": "price", "activate": "action", "timer": "timer"}[permit.operation]
    if kind != expected:
        _fail("permit_endpoint_operation_mismatch")
    if permit.operation == "activate" and not path.endswith(("/activate", "/update")):
        _fail("deactivation_not_approved")
    if path != permit.request.get("path") or _canonical(payload) != _canonical(permit.request.get("payload")):
        _fail("payload_differs_from_approved_request_record")
    by_sku, by_pid = _indices(permit.runtime_plan)
    item = by_sku[permit.sku]
    target, floor = _target(item), _money(item["experiment_floor_rub"])
    key, rows, scalar = _list_payload(kind, payload)
    if len(rows) != 1:
        _fail("permit_must_target_one_exact_approved_item")
    row = {"product_id": rows[0]} if scalar else rows[0]
    sku, pid, managed = _identity(row, by_sku, by_pid)
    if not managed or sku != permit.sku or pid != _pid(item["product_id"]):
        _fail("permit_payload_identity_mismatch")
    if kind == "price":
        if set(payload) != {"prices"}:
            _fail("unexpected_price_request_fields")
        allowed = COMMERCIAL_FIELDS | {"offer_id", "product_id"}
        if set(row) - allowed:
            _fail("unexpected_price_item_fields")
        for field in ("price", "min_price"):
            if field in row and _money(row[field]) != (target if field == "price" else floor):
                _fail("price_or_floor_outside_approved_target")
        if "price" not in row and "min_price" not in row and not FLAG_FIELDS.intersection(row):
            _fail("empty_price_operation")
        if "currency_code" in row and row["currency_code"] != "RUB":
            _fail("non_rub_approved_write")
        observed = _effective_fields(item, permit.decision_id)
        for field in ("old_price", "premium_price"):
            if field in row and (observed is None or field not in observed or not _same_field(field, row[field], observed[field])):
                _fail("unapproved_reference_price_change")
        for field in FLAG_FIELDS.intersection(row):
            wanted = field == "min_price_for_auto_actions_enabled"
            if _flag(row[field]) is not wanted:
                _fail("unapproved_auto_or_protection_flag")
    elif kind == "action":
        aid = _pid(payload.get("action_id"))
        if permit.action_id != aid or aid not in {_pid(x) for x in item["allowed_action_ids"]}:
            _fail("action_outside_approved_allowlist")
        if set(payload) - {"action_id", key}:
            _fail("unexpected_action_request_fields")
        if not scalar:
            if set(row) - {"product_id", "id", "offer_id", "action_price", "price", "stock"}:
                _fail("unexpected_action_item_fields")
            for field in ("action_price", "price"):
                if field in row and _money(row[field]) != target:
                    _fail("action_price_outside_approved_target")
            if "stock" in row:
                # Activating an action must not invent a stock reservation.
                observed_stock = item.get("approved_action_stock")
                if observed_stock is None or row["stock"] != observed_stock:
                    _fail("unapproved_action_stock")
    elif kind == "timer":
        # The exact documented timer fields are deliberately narrow. Unsupported
        # schema requires an explicit code review, never a generic bypass.
        allowed = {key, "min_price_for_auto_actions_enabled"}
        if set(payload) - allowed:
            _fail("unexpected_timer_request_fields")
        if "min_price_for_auto_actions_enabled" in payload and _flag(payload["min_price_for_auto_actions_enabled"]) is not True:
            _fail("protection_disable_not_approved")


@dataclass
class RequestDecision:
    payload: dict | None
    kind: str | None = None
    skipped: list[dict] = field(default_factory=list)
    permitted: bool = False

    def finish(self, response: Any) -> dict:
        if not self.skipped:
            return response
        out = deepcopy(response) if isinstance(response, dict) else {}
        out["ozon_control_guard"] = {"skipped": deepcopy(self.skipped), "marketplace_writes_for_skipped": 0}
        if self.kind == "price":
            result = out.get("result")
            if not isinstance(result, list):
                result = []
            for row in self.skipped:
                result.append({"offer_id": row["sku"], "product_id": row["product_id"],
                               "updated": False, "guard_skipped": True,
                               "errors": [{"code": "OZON_CONTROL_GUARD_SKIP", "message": row["reason"]}]})
            out["result"] = result
        elif self.payload is None:
            out["result"] = {"task_id": None} if self.kind == "import" else {}
        return out


def prepare_request(path: str, payload: dict, *, read_prices=None) -> RequestDecision:
    """Filter legacy commercial batches or validate the current one-use permit."""
    kind = _kind(path)
    if kind is None:
        return RequestDecision(payload)
    permit = _CURRENT_PERMIT.get()
    plan = _load_active_plan(permit.decision_id if permit is not None else LEGACY_DECISION_ID)
    pilot = None
    if permit is None:
        pilot = _load_active_plan(BF4_DECISION_ID)
        if pilot is not None:
            _require_pilot_overlap(plan, pilot)
    if plan is None:
        if permit is not None:
            _fail("persisted_approval_removed_during_permit")
        return RequestDecision(payload, kind)
    if kind == "unknown_commercial":
        _fail("unknown_commercial_endpoint_with_managed_scope")
    if permit is not None:
        if permit.consumed:
            _fail("approved_request_permit_already_consumed")
        if plan["decision_id"] != permit.decision_id or _approval_digest(plan) != permit.approval_core_sha256:
            _fail("persisted_approval_changed_during_permit")
        if superseding_decision(permit.decision_id, permit.sku) is not None:
            _fail("approved_item_superseded_by_later_owner_decision")
        if permit.decision_id == BF4_DECISION_ID:
            _require_pilot_overlap(_load_active_plan(), plan)
        _validate_permitted_payload(permit, path, payload)
        permit.consumed = True
        return RequestDecision(deepcopy(payload), kind, permitted=True)
    by_sku, by_pid = _legacy_indices_with_runtime_overlay(plan, pilot)
    key, rows, scalar = _list_payload(kind, payload)
    kept, skipped, seen, parsed = [], [], set(), []
    for value in rows:
        row = {"product_id": value} if scalar else value
        sku, pid, managed = _identity(row, by_sku, by_pid)
        identity = (sku, pid)
        if identity in seen:
            _fail("duplicate_mutation_identity")
        seen.add(identity)
        parsed.append((value, row, sku, pid, managed))
    needs_live = [sku for _, row, sku, _, managed in parsed
                  if managed and COMMERCIAL_FIELDS.intersection(row)
                  and not _content_pending(by_sku[sku])] if kind == "import" else []
    live_fields = _live_import_fields(needs_live, by_sku, read_prices) if kind == "import" else {}
    for value, row, sku, pid, managed in parsed:
        if not managed:
            kept.append(deepcopy(value))
            continue
        if kind == "import" and _safe_content_import(row, by_sku[sku], live_fields.get(sku)):
            kept.append(deepcopy(value))
            continue
        reason = "managed_commercial_write_requires_approved_request" if kind != "import" else "content_import_commercial_fields_stale_or_unverified"
        skipped.append({"sku": sku, "product_id": pid, "reason": reason})
        logger.info("Ozon managed write skipped: sku=%s product_id=%s endpoint=%s reason=%s", sku, pid, path, reason)
    filtered = deepcopy(payload)
    filtered[key] = kept
    return RequestDecision(filtered if kept else None, kind, skipped)
