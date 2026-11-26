"""Pure, partial Ozon contribution calculations from explicitly supplied evidence.

No I/O, credentials, marketplace actions, invented profit minima, or implicit
zeroes for unknown business costs. Historical accrual diagnostics and projections
using current tariff snapshots are separate. Neither is certified net profit.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Mapping

ZERO = Decimal("0")
HUNDRED = Decimal("100")
TAX_RATE_SCENARIO = Decimal("0.06")
PERCENTAGE_SERVICE_NAMES = frozenset({
    "Звёздные товары", "Подписка Управление отзывами", "Продвижение бренда",
})
ACQUIRING_NAME = "Эквайринг"
COMMON_UNKNOWNS = (
    "fully_landed_procurement_cost", "seller_packaging", "advertising_outside_item_fees",
    "warranty", "unallocated_marketplace_fees", "business_overheads", "actual_tax_base",
)


def _decimal(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("Boolean is not a monetary amount")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"Invalid numeric value: {value!r}") from exc
    if not result.is_finite():
        raise ValueError("Amounts must be finite")
    return result


def _rounded(value, digits=2):
    if value is None:
        return None
    return float(value.quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))


def _percentage(numerator, denominator):
    if numerator is None or denominator is None or denominator <= 0:
        return None
    return _rounded(HUNDRED * numerator / denominator, 4)


def _tax_rate(value):
    result = _decimal(value)
    if result is None or not ZERO <= result < 1:
        raise ValueError("Tax scenario rate must be in [0, 1)")
    return result


def _positive_cost(value):
    result = _decimal(value)
    return result if result is not None and result > 0 else None


def _base(kind, cost_source):
    return {
        "kind": kind, "metric_kind": "partial_contribution_not_net_profit",
        "contribution_rub": None, "profit_on_cost_pct": None,
        "margin_on_revenue_pct": None, "verified": False, "write_allowed": False,
        "unknown_costs": list(COMMON_UNKNOWNS), "flags": [],
        "cost_source": deepcopy(cost_source),
    }


def exact_sku_index(rows, key="sku"):
    """Reject duplicates; preserve case, Unicode and spaces inside identifiers."""
    result = {}
    for row in rows:
        sku = row.get(key)
        if not isinstance(sku, str) or not sku or sku != sku.strip() or sku in result:
            raise ValueError(f"Missing, padded or duplicate exact SKU: {sku!r}")
        result[sku] = row
    return result


def procurement_context(master_cost, reference_cost=None, component_cost=None):
    """Expose source conflicts; the largest supplied cost is only a scenario basis.

    A missing master cost is not silently filled from an older reference. An
    assembly's component sum is not proof of its complete landed purchase cost.
    """
    master = _positive_cost(master_cost)
    reference = _positive_cost(reference_cost)
    components = _positive_cost(component_cost)
    flags = []
    if master is None:
        flags.append("MISSING_PROCUREMENT_COST")
    if master is not None and reference is not None and reference != master:
        flags.append("PROCUREMENT_REFERENCE_DIFFERS_FROM_MASTER")
    if master is not None and components is not None and components != master:
        flags.append("BOM_COST_DIFFERS_FROM_MASTER")
    if master is not None and any(x is not None and x > master for x in (reference, components)):
        flags.append("CONSERVATIVE_REFERENCE_ABOVE_MASTER")
    scenario = max(x for x in (master, reference, components) if x is not None) if master is not None else None
    return {
        "master_cost_rub": _rounded(master), "reference_cost_rub": _rounded(reference),
        "component_cost_rub": _rounded(components), "scenario_cost_rub": _rounded(scenario),
        "fully_landed_cost_verified": False, "flags": flags,
    }


def _financial_numbers(finance):
    if not isinstance(finance, Mapping):
        return None
    fields = ("sold", "returned", "revenue", "commission", "logistics", "services", "net")
    result = {key: _decimal(finance.get(key)) for key in fields}
    if any(value is None for value in result.values()):
        return None
    if any(result[key] < 0 or result[key] != result[key].to_integral_value() for key in ("sold", "returned")):
        raise ValueError("Sold and returned quantities must be nonnegative integers")
    return result


def historical_contribution(finance, cost, *, tax_rate=TAX_RATE_SCENARIO, cost_source=None):
    """Period accrual contribution using current procurement and a stated tax proxy.

    Return quantities never restore procurement cost automatically. Unit ratios
    are withheld when returns or nonpositive revenue make them ambiguous.
    One or two sales remain visible and explicitly carry a small-sample flag.
    """
    result = _base("historical_accrual_diagnostic", cost_source)
    result.update({"period_contribution_rub": None, "period_contribution_before_tax_rub": None,
                   "unit_metrics_available": False, "return_inventory_credit_rub": None,
                   "tax_rate_scenario": float(_tax_rate(tax_rate))})
    f, purchase = _financial_numbers(finance), _positive_cost(cost)
    if f is None:
        result["flags"].append("MISSING_OR_INCOMPLETE_FINANCE")
        return result
    result["observations"] = {"sold": int(f["sold"]), "returned": int(f["returned"])}
    result["finance"] = {key: _rounded(value) for key, value in f.items()}
    reconciled = abs(f["net"] - sum((f[key] for key in ("revenue", "commission", "logistics", "services")), ZERO)) <= Decimal("0.02")
    if not reconciled:
        result["flags"].append("FINANCE_COMPONENTS_UNRECONCILED")
    if purchase is None:
        result["flags"].append("MISSING_PROCUREMENT_COST")
        return result
    result["procurement_per_unit_rub"] = _rounded(purchase)
    result["flags"].append("CURRENT_REGISTRY_COST_NOT_HISTORICAL_LANDED_COST")
    if f["sold"] < 3:
        result["flags"].append("NO_SALES" if f["sold"] == 0 else "LOW_OBSERVATION_COUNT")
    if f["returned"] > 0:
        result["flags"].append("RETURNS_REQUIRE_POSTING_AND_INVENTORY_RECONCILIATION")
        result["unknown_costs"].append("return_inventory_recovery")
    if f["returned"] > f["sold"]:
        result["flags"].append("RETURNS_FROM_PRIOR_COHORT_OR_PERIOD")
    if f["revenue"] <= 0:
        result["flags"].append("NON_POSITIVE_REVENUE")
    tax = max(f["revenue"], ZERO) * _tax_rate(tax_rate)
    before_tax = f["net"] - purchase * f["sold"]
    after_tax = before_tax - tax
    result.update({"period_contribution_rub": _rounded(after_tax),
                   "period_contribution_before_tax_rub": _rounded(before_tax),
                   "tax_scenario_rub": _rounded(tax),
                   "procurement_debit_rub": _rounded(purchase * f["sold"]),
                   "tax_base_scenario": "max(period seller revenue, 0); actual tax treatment unverified"})
    if f["sold"] > 0 and f["returned"] == 0 and f["revenue"] > 0 and reconciled:
        result.update({"unit_metrics_available": True,
                       "contribution_rub": _rounded(after_tax / f["sold"]),
                       "profit_on_cost_pct": _percentage(after_tax, purchase * f["sold"]),
                       "margin_on_revenue_pct": _percentage(after_tax, f["revenue"])})
    return result


def _service_model(finance, fees_by_type):
    """Separate acquiring, observed percentage programs and fixed-service proxies."""
    result = {"available": False, "percentage_services": [], "fixed_services": [],
              "historical_acquiring_rub": None, "excluded_credits": [], "flags": [],
              "assumptions": ["Observed paid-program rates and fixed allocations remain unchanged at the candidate price.",
                              "Fixed per-unit allocations are historical proxies, not confirmed future tariffs."]}
    f = _financial_numbers(finance)
    if f is None or not isinstance(fees_by_type, Mapping):
        result["flags"].append("SERVICE_COSTS_UNESTIMATED")
        return result, None, None
    values = {name: _decimal(value) for name, value in fees_by_type.items()}
    if any(value is None for value in values.values()) or abs(sum(values.values(), ZERO) - f["services"]) > Decimal("0.02"):
        result["flags"].append("SERVICE_DETAILS_UNRECONCILED")
        return result, None, None
    if f["sold"] <= 0 or f["revenue"] <= 0 or f["returned"] > 0:
        result["flags"].append("SERVICE_COSTS_UNESTIMATED_RETURN_OR_NO_SALE_HISTORY")
        return result, None, None
    if f["sold"] < 3:
        result["flags"].append("LOW_OBSERVATION_COUNT")
    rate, fixed = ZERO, ZERO
    for name, signed in values.items():
        if name == ACQUIRING_NAME:
            result["historical_acquiring_rub"] = _rounded(signed)
            if signed > 0:
                result["flags"].append("HISTORICAL_ACQUIRING_CREDIT_NOT_PROJECTED")
            continue  # Current acquiring is counted once, separately below.
        if signed > 0:
            result["excluded_credits"].append({"name": name, "amount_rub": _rounded(signed)})
            result["flags"].append("HISTORICAL_SERVICE_CREDIT_NOT_PROJECTED")
            continue
        charge = -signed
        if name in PERCENTAGE_SERVICE_NAMES:
            observed = charge / f["revenue"]
            rate += observed
            result["percentage_services"].append({"name": name, "period_charge_rub": _rounded(charge),
                                                   "observed_rate_pct": _rounded(observed * HUNDRED, 6)})
        else:
            allocated = charge / f["sold"]
            fixed += allocated
            result["fixed_services"].append({"name": name, "period_charge_rub": _rounded(charge),
                                              "allocated_per_sold_unit_rub": _rounded(allocated)})
    result.update({"available": True, "percentage_rate_pct": _rounded(rate * HUNDRED, 6),
                   "fixed_per_sold_unit_rub": _rounded(fixed),
                   "flags": sorted(set(result["flags"] + ["HISTORICAL_SERVICE_TERMS_ASSUMED_UNCHANGED"]))})
    return result, rate, fixed


def current_projection(price, cost, live_tariffs, finance=None, fees_by_type=None, *,
                       tax_rate=TAX_RATE_SCENARIO, cost_source=None):
    """Partial current/candidate contribution, per fulfillment scheme.

    ``live_tariffs`` is the saved product price row, including its original
    ``price``, ``acquiring``, ``commissions`` and FBS/FBO stock flags. Candidate
    acquiring scales with the observed amount/original-price rate. Absent
    finance leaves service costs unknown; an available subtotal is not profit.
    The top-level contribution selects the lowest available scheme subtotal.
    """
    result = _base("partial_current_tariff_projection", cost_source)
    target, purchase, tax_fraction = _decimal(price), _positive_cost(cost), _tax_rate(tax_rate)
    result.update({"by_scheme": {}, "reference_scheme": None, "seller_price_rub": _rounded(target),
                   "tax_rate_scenario": float(tax_fraction), "services_estimated": False,
                   "assumptions": ["Current tariff percentages remain applicable to the candidate price.",
                                   "Logistics uses the snapshot maximum within each fulfillment scheme.",
                                   "Tax rate and seller-revenue base are scenario assumptions."]})
    result["unknown_costs"] += ["future_returns_and_cancellations", "promotion_combination_effects", "future_paid_service_terms"]
    if target is None or target <= 0:
        result["flags"].append("INVALID_SELLER_PRICE")
        return result
    if purchase is None:
        result["flags"].append("MISSING_PROCUREMENT_COST")
        return result
    if not isinstance(live_tariffs, Mapping):
        result["flags"].append("MISSING_CURRENT_TARIFFS")
        return result
    result["procurement_per_unit_rub"] = _rounded(purchase)
    co = live_tariffs.get("commissions") or {}
    snapshot_price, acquiring_amount = _decimal(live_tariffs.get("price")), _decimal(live_tariffs.get("acquiring"))
    tariff_values = [_decimal(value) for value in co.values()]
    if tariff_values and all(value == 0 for value in tariff_values) and acquiring_amount == 0:
        result["flags"].append("SUSPECT_ALL_ZERO_TARIFFS_UNVERIFIED")
        return result
    if snapshot_price is None or snapshot_price <= 0 or acquiring_amount is None or acquiring_amount < 0:
        result["flags"].append("MISSING_OR_INVALID_ACQUIRING_RATE")
        return result
    acquiring_rate = acquiring_amount / snapshot_price
    if acquiring_rate >= 1:
        result["flags"].append("INVALID_ACQUIRING_RATE")
        return result
    service_model, service_rate, fixed_services = _service_model(finance, fees_by_type)
    result["service_model"] = service_model
    result["services_estimated"] = service_model["available"]
    result["flags"] += service_model["flags"]
    if not service_model["available"]:
        result["unknown_costs"].append("current_marketplace_service_costs")
    if service_model["excluded_credits"]:
        result["unknown_costs"].append("future_service_credit_eligibility")
    active_schemes = [scheme for scheme in ("fbs", "fbo") if live_tariffs.get("has_" + scheme + "_stocks") is True]
    if not active_schemes:
        result["flags"].append("NO_CONFIRMED_STOCK_SCHEME")
        active_schemes = ["fbs", "fbo"]  # Diagnostics only, without claiming available inventory.
    for scheme in active_schemes:
        commission_pct = _decimal(co.get("sales_percent_" + scheme))
        logistics_parts = [_decimal(co.get(scheme + "_direct_flow_trans_max_amount")),
                           _decimal(co.get(scheme + "_deliv_to_customer_amount")),
                           _decimal(co.get(scheme + "_first_mile_max_amount")) if scheme == "fbs" else ZERO]
        if commission_pct is None or not ZERO <= commission_pct < 100 or any(x is None or x < 0 for x in logistics_parts):
            result["flags"].append("INCOMPLETE_TARIFF_" + scheme.upper())
            continue
        if commission_pct == 0 and acquiring_rate == 0 and all(x == 0 for x in logistics_parts):
            result["flags"].append("SUSPECT_ZERO_TARIFF_" + scheme.upper() + "_UNVERIFIED")
            continue
        commission, acquiring = target * commission_pct / HUNDRED, target * acquiring_rate
        logistics, tax = sum(logistics_parts, ZERO), target * tax_fraction
        subtotal = target - purchase - commission - acquiring - logistics - tax
        percentage_services = target * service_rate if service_rate is not None else None
        if percentage_services is not None:
            subtotal -= percentage_services + fixed_services
        result["by_scheme"][scheme] = {
            "contribution_rub": _rounded(subtotal), "profit_on_cost_pct": _percentage(subtotal, purchase),
            "margin_on_revenue_pct": _percentage(subtotal, target), "is_complete_profit": False,
            "deductions": {"procurement_rub": _rounded(purchase), "commission_rub": _rounded(commission),
                           "acquiring_rub": _rounded(acquiring), "logistics_snapshot_max_rub": _rounded(logistics),
                           "tax_scenario_rub": _rounded(tax), "percentage_services_rub": _rounded(percentage_services),
                           "fixed_services_proxy_rub": _rounded(fixed_services)},
            "commission_rate_pct": _rounded(commission_pct),
            "acquiring_rate_pct": _rounded(acquiring_rate * HUNDRED, 6),
        }
    if result["by_scheme"]:
        reference = min(result["by_scheme"], key=lambda key: result["by_scheme"][key]["contribution_rub"])
        result["reference_scheme"] = reference
        for key in ("contribution_rub", "profit_on_cost_pct", "margin_on_revenue_pct"):
            result[key] = result["by_scheme"][reference][key]
    else:
        result["flags"].append("NO_USABLE_TARIFF_SCHEME")
    result["flags"] = sorted(set(result["flags"]))
    result["unknown_costs"] = sorted(set(result["unknown_costs"]))
    return result
