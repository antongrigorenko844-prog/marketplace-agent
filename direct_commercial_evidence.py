"""Pure commercial reporting helpers. No network, no order/advertising writes.

Metrika's Ecommerce purchase/revenue metrics are tracking events, not payment
settlement. Keep a separate register of individual owner confirmations. Never
infer the completeness of that register or identify a transaction by amount.
"""
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
import json

CAMPAIGN_ID = 716316525
LAUNCH_DATE = date(2026, 11, 16)
DIMENSION = "ym:s:lastDirectClickOrder"
FILTER = f"{DIMENSION}=={CAMPAIGN_ID}"
METRICS = (
    "ym:s:visits", "ym:s:ecommercePurchases", "ym:s:ecommerceRUBConvertedRevenue",
    "ym:s:goal682257540visits", "ym:s:goal675487735visits",
)
REGISTER = Path(__file__).resolve().parent / "data/direct_audit_20261126/payment_evidence.json"

def _decimal(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Non-numeric commercial metric")
    try:
        n = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("Non-numeric commercial metric") from None
    if not n.is_finite() or n < 0:
        raise ValueError("Invalid commercial metric")
    return n

def _count(value):
    n = _decimal(value)
    if n != n.to_integral_value():
        raise ValueError("Non-integral count")
    return int(n)

def _money(value):
    return float(_decimal(value).quantize(Decimal("0.01")))

def summarize_campaign_rows(rows, total_rows, sampled):
    """Validate exact campaign aggregate; do not substitute other campaigns."""
    if (not isinstance(rows, list) or isinstance(total_rows, bool)
            or not isinstance(total_rows, int) or total_rows != len(rows)
            or len(rows) > 1):
        raise ValueError("Incomplete or unexpected exact campaign report")
    if rows:
        dims = rows[0].get("dimensions")
        if (not isinstance(dims, list) or len(dims) != 1
                or str(dims[0].get("id")) != str(CAMPAIGN_ID)):
            raise ValueError("Exact campaign ID mismatch")
        values = rows[0].get("metrics")
        if not isinstance(values, list) or len(values) != len(METRICS):
            raise ValueError("Incomplete commercial metrics")
    else:
        values = [0] * len(METRICS)
    return {
        "campaign_id": CAMPAIGN_ID,
        "attribution": "last_direct_click_campaign_id",
        "visits": _count(values[0]),
        "ecommerce_purchase_events": _count(values[1]),
        "ecommerce_event_amount_rub": _money(values[2]),
        "purchase_goal_visits": _count(values[3]),
        "cart_goal_visits": _count(values[4]),
        "sampled": sampled,
        "actual_paid_orders": None,
        "actual_paid_revenue_rub": None,
        "actual_ROAS": None,
        "actual_CAC": None,
        "actual_ROMI": None,
    }

def owner_evidence(d_from, d_to, registry=None):
    """Return known cases in attributed-visit window; not a payment census."""
    if d_from > d_to:
        raise ValueError("Invalid evidence window")
    if registry is None:
        registry = json.loads(REGISTER.read_text(encoding="utf-8"))
    if (registry.get("schema_version") != 1
            or registry.get("campaign_id") != CAMPAIGN_ID
            or registry.get("coverage") != "individual_owner_confirmations_only_not_complete_payment_register"
            or not isinstance(registry.get("cases"), list)):
        raise ValueError("Unexpected payment evidence register")
    seen = set()
    cases = []
    for item in registry["cases"]:
        cid = item.get("case_id")
        if not isinstance(cid, str) or not cid or cid in seen:
            raise ValueError("Missing or duplicate evidence case ID")
        seen.add(cid)
        if item.get("campaign_id") != CAMPAIGN_ID:
            raise ValueError("Evidence campaign mismatch")
        day = date.fromisoformat(item["attributed_visit_date"])
        if item.get("status") != "OWNER_CONFIRMED_UNPAID":
            raise ValueError("Unsupported evidence status; authoritative payment reconciliation required")
        amount = _money(item["order_amount_rub"])
        if d_from <= day <= d_to:
            cases.append({
                "case_id": cid, "visit_date": day.isoformat(),
                "sku": item["sku"], "order_amount_rub": amount,
                "status": item["status"], "reason": item.get("reason"),
            })
    return {
        "from": d_from.isoformat(), "to": d_to.isoformat(),
        "coverage": "individual_owner_confirmations_only",
        "known_unpaid_cases": len(cases),
        "known_unpaid_order_amount_rub": _money(sum(_decimal(x["order_amount_rub"]) for x in cases)),
        "cases": cases,
        "note": "Date is attributed visit date. Not joined to future aggregate events by amount. No proof of complete payment coverage.",
    }

def commercial_summary(rows, total_rows, sampled, d_from, d_to, registry=None):
    result = summarize_campaign_rows(rows, total_rows, sampled)
    result.update({
        "from": d_from.isoformat(), "to": d_to.isoformat(),
        "payment_evidence": owner_evidence(d_from, d_to, registry),
        "note": "Ecommerce event amount is not paid revenue. Actual CAC/ROAS/ROMI require reconciled payments.",
    })
    return result

def format_commercial_summary(data, caption):
    def count(n):
        return f"{n:,}".replace(",", " ")
    def rub(n):
        return f"{n:,.2f}".replace(",", " ").replace(".", ",")
    line = (
        f"{caption}: визиты {count(data['visits'])} · корзина {count(data['cart_goal_visits'])}"
        f" · Ecommerce-события покупки {count(data['ecommerce_purchase_events'])}"
        f" на {rub(data['ecommerce_event_amount_rub'])} ₽"
        f" · визиты по цели покупки {count(data['purchase_goal_visits'])}"
    )
    known = data["payment_evidence"]
    if known["known_unpaid_cases"]:
        line += (f" · известные неоплаченные заказы {count(known['known_unpaid_cases'])}"
                 f" на {rub(known['known_unpaid_order_amount_rub'])} ₽")
    if data["sampled"]:
        line += " · выборочный отчёт"
    return line
