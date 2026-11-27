"""The separately approved four-product Black Friday pilot, without marketplace I/O.

This is a second fixed decision, not a relaxation of the frozen 96-product plan.
Prices, identities and the sole action are fixed below. The owner's accepted
per-product economics are also hashed into each item and checked on every run.
The legacy evaluator and its 10% volume-growth boundary remain unchanged.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path

from ozon_promo_batch import (
    _number, _pid, _service_terms, _stock_positive, _timestamp,
    financial_comparison, scope_digest,
)

DECISION_ID = "ozon-bf4-20261127"
DEFAULT_PLAN = Path(__file__).resolve().parent / "data/ozon_black_friday_pilot_20261127/approved_plan.json"
ACTION_ID = 4532787
FIXED_SCOPE = {
    "0am325025G": {"product_id": 1790610716, "before_price": 3724, "target_price": 3195},
    "porshni-usil-pylniki-kryshki-v2": {"product_id": 6291485580, "before_price": 3715, "target_price": 3195},
    "0AM325025B": {"product_id": 1504143993, "before_price": 9022, "target_price": 7760},
    "6t30": {"product_id": 6287285958, "before_price": 19155, "target_price": 16475},
}
MONEY_TOLERANCE = Decimal("0.02")
RATE_PCT_TOLERANCE = Decimal("0.0001")
TAX_FRACTION_TOLERANCE = Decimal("0.000001")


def _fixed_item(item):
    if not isinstance(item, dict) or item.get("sku") not in FIXED_SCOPE:
        raise ValueError("Item is outside the exact four-product approval")
    fixed = FIXED_SCOPE[item["sku"]]
    if item.get("decision_id") != DECISION_ID or item.get("strategy_code") != "C":
        raise ValueError("Pilot item must retain its decision and discount strategy")
    if _pid(item.get("product_id")) != fixed["product_id"]:
        raise ValueError("Pilot product identity changed")
    for field in ("before_price", "target_price"):
        if _number(item.get(field)) != Decimal(fixed[field]):
            raise ValueError("Pilot approved price changed")
    if _number(item.get("experiment_floor_rub")) != Decimal(fixed["target_price"]):
        raise ValueError("Pilot minimum price must equal its exact approved target")
    for alias, canonical in (("price_before_rub", "before_price"), ("target_price_rub", "target_price")):
        if alias in item and _number(item[alias]) != Decimal(fixed[canonical]):
            raise ValueError("Conflicting pilot price alias")
    if item.get("allowed_action_ids") != [ACTION_ID]:
        raise ValueError("Pilot action allowlist changed")
    actions = item.get("approved_actions")
    if not isinstance(actions, list) or len(actions) != 1 or not isinstance(actions[0], dict):
        raise ValueError("Pilot requires exactly one approved action")
    if _pid(actions[0].get("action_id")) != ACTION_ID:
        raise ValueError("Pilot approved action changed")
    return fixed


def _bounded(value, *, lower=0, upper=None, positive=False):
    result = _number(value)
    if result < lower or (positive and result <= lower) or (upper is not None and result >= upper):
        raise ValueError("Invalid pilot financial assumption")
    return result


def _validate_economics(item, baseline):
    if not isinstance(baseline, dict) or baseline.get("schema_version") != 1:
        raise ValueError("Missing approved pilot economics")
    if baseline.get("full_profit_verified") is not False:
        raise ValueError("Pilot economics must remain explicitly partial")
    cost = _bounded(baseline.get("procurement_rub"), positive=True)
    tax = _bounded(baseline.get("tax_rate_scenario"), upper=1)
    services = _bounded(baseline.get("percentage_services_rate_pct"), upper=100)
    fixed = _bounded(baseline.get("fixed_services_rub"))
    schemes = baseline.get("by_scheme")
    if not isinstance(schemes, dict) or not schemes or set(schemes) - {"fbs", "fbo"}:
        raise ValueError("Missing or unsupported approved fulfillment scheme")
    before_values, target_values = [], []
    for values in schemes.values():
        if not isinstance(values, dict):
            raise ValueError("Invalid scheme economics")
        commission = _bounded(values.get("commission_rate_pct"), upper=100)
        acquiring = _bounded(values.get("acquiring_rate_pct"), upper=100)
        logistics = _bounded(values.get("logistics_snapshot_max_rub"))
        retention = 1 - (commission + acquiring + services) / 100 - tax
        overhead = cost + logistics + fixed
        expected_before = _number(item["before_price"]) * retention - overhead
        expected_target = _number(item["target_price"]) * retention - overhead
        before = _number(values.get("baseline_partial_contribution_rub"))
        target = _number(values.get("target_partial_contribution_rub"))
        if (before <= 0 or target <= 0 or abs(before - expected_before) > MONEY_TOLERANCE
                or abs(target - expected_target) > MONEY_TOLERANCE):
            raise ValueError("Approved pilot economics do not reconcile or are nonpositive")
        before_values.append(before)
        target_values.append(target)
    before, target = min(before_values), min(target_values)
    if (abs(_number(baseline.get("baseline_partial_contribution_rub")) - before) > MONEY_TOLERANCE
            or abs(_number(baseline.get("target_partial_contribution_rub")) - target) > MONEY_TOLERANCE):
        raise ValueError("Approved pilot reference scheme does not reconcile")
    expected_growth = (before / target - 1) * 100
    if abs(_number(baseline.get("required_unit_growth_pct")) - expected_growth) > Decimal("0.001"):
        raise ValueError("Approved pilot volume-growth diagnostic does not reconcile")


def approval_economics(item, row, service_evidence=None):
    """Build the immutable per-item baseline before calculating scope_digest.

    Missing services are an error, not a zero-cost assumption. This helper is
    also used to compare fresh costs and rates with the owner's approved case.
    """
    _fixed_item(item)
    if row.get("sku") != item["sku"] or _pid(row.get("product_id")) != _pid(item["product_id"]):
        raise ValueError("Financial evidence identity mismatch")
    if isinstance(service_evidence, dict) and service_evidence.get("product_id") is not None:
        if _pid(service_evidence["product_id"]) != _pid(item["product_id"]):
            raise ValueError("Service evidence product identity mismatch")
    rate, fixed, source, exception = _service_terms(item, row, service_evidence)
    if rate is None or fixed is None or exception:
        raise ValueError("Pilot requires supported exact-SKU service costs")
    comparison = financial_comparison(item, row, service_evidence)
    if comparison.get("valid") is not True or comparison.get("services_supported") is not True:
        raise ValueError("Pilot financial evidence is incomplete")
    current = row["economics"]["current"]
    result = {
        "schema_version": 1,
        "procurement_rub": float(_number(row["economics"]["procurement"]["scenario_cost_rub"])),
        "tax_rate_scenario": float(_number(current["tax_rate_scenario"])),
        "percentage_services_rate_pct": float(rate), "fixed_services_rub": float(fixed),
        "service_basis": source, "by_scheme": {},
        "baseline_partial_contribution_rub": comparison["baseline_partial_contribution_rub"],
        "target_partial_contribution_rub": comparison["target_partial_contribution_rub"],
        "required_unit_growth_pct": comparison["required_unit_growth_pct"],
        "metric_kind": "partial_contribution_not_net_profit", "full_profit_verified": False,
    }
    for scheme, projection in current["by_scheme"].items():
        result["by_scheme"][scheme] = {
            "commission_rate_pct": float(_number(projection["commission_rate_pct"])),
            "acquiring_rate_pct": float(_number(projection["acquiring_rate_pct"])),
            "logistics_snapshot_max_rub": float(_number(projection["deductions"]["logistics_snapshot_max_rub"])),
            **deepcopy(comparison["by_scheme"][scheme]),
        }
    _validate_economics(item, result)
    return result


def validate_approved_plan(plan):
    """Accept only this separately approved exact four-product decision."""
    if (not isinstance(plan, dict) or plan.get("schema_version") != 1
            or plan.get("decision_id") != DECISION_ID or plan.get("approved") is not True):
        raise ValueError("Exact Black Friday pilot approval is required")
    if not isinstance(plan.get("enabled"), bool):
        raise ValueError("Pilot enabled state must be explicit")
    if not isinstance(plan.get("approvalReference"), str) or not plan["approvalReference"].strip():
        raise ValueError("Missing pilot owner approval reference")
    items = plan.get("items")
    if not isinstance(items, list) or len(items) != len(FIXED_SCOPE):
        raise ValueError("Pilot must contain exactly four approved items")
    seen = set()
    for item in items:
        _fixed_item(item)
        if item["sku"] in seen:
            raise ValueError("Duplicate pilot SKU")
        seen.add(item["sku"])
        _validate_economics(item, item.get("approved_economics"))
    if seen != set(FIXED_SCOPE) or plan.get("scope_sha256") != scope_digest(plan):
        raise ValueError("Immutable pilot approval scope mismatch")
    return True


def load_approved_plan(path=None):
    plan = json.loads(Path(path or DEFAULT_PLAN).read_text(encoding="utf-8-sig"))
    validate_approved_plan(plan)
    return plan


def _economic_drift(approved, fresh):
    blockers = []
    for key, tolerance in (
        ("procurement_rub", MONEY_TOLERANCE),
        ("tax_rate_scenario", TAX_FRACTION_TOLERANCE),
        ("percentage_services_rate_pct", RATE_PCT_TOLERANCE),
        ("fixed_services_rub", MONEY_TOLERANCE),
    ):
        if _number(fresh[key]) > _number(approved[key]) + tolerance:
            blockers.append("PILOT_APPROVED_FINANCIAL_ASSUMPTION_WORSENED:" + key)
    for scheme, values in fresh["by_scheme"].items():
        prior = approved["by_scheme"].get(scheme)
        if prior is None:
            blockers.append("PILOT_UNAPPROVED_FINANCIAL_SCHEME:" + scheme)
            continue
        for key, tolerance in (("commission_rate_pct", RATE_PCT_TOLERANCE),
                               ("acquiring_rate_pct", RATE_PCT_TOLERANCE),
                               ("logistics_snapshot_max_rub", MONEY_TOLERANCE)):
            if _number(values[key]) > _number(prior[key]) + tolerance:
                blockers.append("PILOT_APPROVED_FINANCIAL_ASSUMPTION_WORSENED:" + scheme + ":" + key)
        if _number(values["target_partial_contribution_rub"]) + MONEY_TOLERANCE < _number(prior["target_partial_contribution_rub"]):
            blockers.append("PILOT_APPROVED_UNIT_CONTRIBUTION_WORSENED:" + scheme)
    return blockers


def evaluate_item(item, live_catalogue_row, service_evidence=None, *, now=None):
    """Executor-compatible pure gate; the individual approved case replaces 10%."""
    _fixed_item(item)
    _validate_economics(item, item.get("approved_economics"))
    row = live_catalogue_row
    now = _timestamp(now) if now is not None else datetime.now(timezone.utc)
    if now is None:
        raise ValueError("Timezone-aware current time required")
    blockers, warnings, ready, deferred = [], [], [], []
    target = float(_number(item["target_price"]))
    if row.get("sku") != item["sku"] or row.get("product_id") != item["product_id"]:
        blockers.append("EXACT_IDENTITY_MISMATCH")
    try:
        live_price = _number(row.get("current_price"))
    except ValueError:
        live_price = None
        blockers.append("INVALID_SELLER_PRICE")
    if live_price not in (_number(item["before_price"]), _number(target)):
        blockers.append("SELLER_PRICE_DRIFT")
    if not _stock_positive(row):
        blockers.append("NO_POSITIVE_OBSERVED_STOCK")
    actions = [a for a in row.get("actions", []) if a.get("action_id") == ACTION_ID]
    if len(actions) != 1:
        blockers.append("APPROVED_ACTION_MISSING_OR_AMBIGUOUS")
    else:
        action = actions[0]
        start, end = _timestamp(action.get("date_start")), _timestamp(action.get("date_end"))
        cap = action.get("cap_rub")
        if start is None or end is None or end < start:
            blockers.append("ACTION_DATES_UNVERIFIED")
        elif end < now:
            blockers.append("APPROVED_ACTION_EXPIRED")
        elif cap is None or _number(cap) < _number(target):
            blockers.append("ACTION_CAP_BELOW_APPROVED_TARGET")
        elif (action.get("eligible_for_price_comparison") is not True
              or action.get("additional_discount_or_unknown_type")
              or action.get("quarantined_or_failed")):
            blockers.append("ACTION_TERMS_REQUIRE_SEPARATE_REVIEW")
        elif start > now:
            deferred.append(ACTION_ID)
        else:
            ready.append(ACTION_ID)
    try:
        financial = financial_comparison(item, row, service_evidence)
        fresh = approval_economics(item, row, service_evidence)
        blockers.extend(_economic_drift(item["approved_economics"], fresh))
        financial["approved_economics"] = deepcopy(item["approved_economics"])
        financial["fresh_economics"] = fresh
        financial["individual_pilot_risk_approved"] = True
    except (ValueError, KeyError, TypeError, ArithmeticError):
        financial = {"valid": False, "full_profit_verified": False,
                     "metric_kind": "partial_contribution_not_net_profit"}
        blockers.append("SERVICE_OR_FINANCIAL_EVIDENCE_REQUIRED")
    value = financial.get("target_partial_contribution_rub")
    if value is None or value <= 0:
        blockers.append("NONPOSITIVE_OR_UNKNOWN_PARTIAL_CONTRIBUTION")
    if not ready and not deferred:
        blockers.append("NO_ELIGIBLE_APPROVED_ACTION")
    status = "HOLD" if blockers else "DEFERRED" if not ready else "READY"
    return {
        "sku": item["sku"], "product_id": item["product_id"], "status": status,
        "price_write_allowed": not blockers and bool(ready) and live_price != _number(target),
        "ready_action_ids": ready if not blockers else [], "deferred_action_ids": deferred,
        "blockers": sorted(set(blockers)), "warnings": warnings, "financial_check": financial,
        "target_price": target, "experiment_floor_rub": target,
        "executor_safeguards_still_required": True,
    }
