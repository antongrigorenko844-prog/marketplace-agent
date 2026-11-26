"""Read-only financial floor and promotion feasibility. Never calls write APIs.

Rates are decimal fractions of seller revenue; fixed costs are RUB per sold unit,
including allocated expected return/warranty/advertising costs where applicable.
No estimates are silently treated as confirmed costs.
"""
from decimal import Decimal, ROUND_CEILING
import csv
import json
from pathlib import Path
from datetime import datetime, timezone


def number(value):
    if isinstance(value, bool) or value is None:
        raise ValueError("Missing or invalid numeric input")
    x = Decimal(str(value))
    if not x.is_finite() or x < 0:
        raise ValueError("Non-finite or negative numeric input")
    return x


def financial_floor(cost, fixed, rate, min_rub, min_pct):
    c, f, r, m, q = map(number, (cost, fixed, rate, min_rub, min_pct))
    if c <= 0 or m <= 0 or q <= 0 or r >= 1 or r + q >= 1:
        raise ValueError("Invalid or non-positive cost/margin constraints")
    return int(max((c + f + m) / (1-r), (c+f) / (1-r-q)).to_integral_value(rounding=ROUND_CEILING))


def evaluate(price, cost, fixed, rate, min_rub, min_pct):
    p, c, f, r = map(number, (price, cost, fixed, rate))
    floor = financial_floor(cost, fixed, rate, min_rub, min_pct)
    if p <= 0:
        raise ValueError("Price must be positive")
    margin = (1-r)*p-c-f
    return {"floor_rub": floor, "contribution_rub": str(margin.quantize(Decimal("0.01"))),
            "margin_fraction": str(margin/p), "meets_floor": p >= floor}


def assess_candidate(row, policy, now=None):
    """Return diagnostics only; missing/expired approval or evidence blocks writes.
    Do not infer eligibility from price alone. Require evidence references.
    """
    now = now or datetime.now(timezone.utc)
    blockers = []
    if policy.get("approved") is not True or not policy.get("approval_reference"):
        blockers.append("POLICY_NOT_APPROVED")
    for key in ("economics_verified", "stock_verified", "identity_verified",
                "competitors_checked", "protection_verified", "combined_promotions_verified"):
        if row.get(key) is not True:
            blockers.append(key.upper())
    for key in ("financial_evidence", "competition_evidence", "protection_evidence", "live_snapshot_at"):
        if not row.get(key):
            blockers.append("MISSING_" + key.upper())
    try:
        ts = datetime.fromisoformat(row["live_snapshot_at"].replace("Z", "+00:00"))
        age = (now-ts).total_seconds()
        if not 0 <= age <= 900:
            blockers.append("STALE_OR_FUTURE_API_DATA")
    except (KeyError, ValueError, TypeError, AttributeError):
        blockers.append("INVALID_API_TIMESTAMP")
    try:
        band = policy["bands"][row["band"]]
        result = evaluate(row["target_price"], row["cost_rub"], row["fixed_rub"],
                          row["variable_rate"], band["min_rub"], band["min_pct"])
        if not result["meets_floor"]:
            blockers.append("BELOW_FINANCIAL_FLOOR")
        if number(row["target_price"]) > number(row["verified_action_cap"]):
            blockers.append("ABOVE_VERIFIED_ACTION_CAP")
        if number(row["verified_protection_price"]) < result["floor_rub"]:
            blockers.append("PROTECTION_BELOW_FLOOR")
        if number(row["verified_protection_price"]) > number(row["target_price"]):
            blockers.append("PROTECTION_ABOVE_TARGET")
    except (KeyError, TypeError, ArithmeticError, ValueError):
        result = None
        blockers.append("INCOMPLETE_OR_INVALID_ECONOMICS")
    return {"sku": row.get("sku"), "result": result, "blockers": sorted(set(blockers)),
            "status": "BLOCKED" if blockers else "PREFLIGHT_PASS_NO_WRITE"}


def review_snapshot(source, destination):
    """All Ozon rows from existing recovery report; scenario data never approve a write."""
    with open(source, encoding="utf-8-sig", newline="") as f:
        raw = [r for r in csv.DictReader(f, delimiter=";") if r["Площадка"] == "Ozon"]
    seen = set()
    out = []
    for r in raw:
        sku = r["Артикул"]
        if not sku or sku in seen:
            raise ValueError("Duplicate or missing exact SKU")
        seen.add(sku)
        model = r["Модель"]
        p = r["Текущая цена продавца"]
        target = r["Цена пилота НЕ УТВЕРЖДЕНА"]
        m = r["Прибыль сценарий текущая"]
        if target and p and number(target)>number(p):
            group = "A_REVIEW_RAISE_WITH_PROMO"
        elif not m or model == "нет модели":
            group = "G_MISSING_ECONOMICS"
        elif Decimal(m) <= 0:
            group = "E_REVIEW_LOW_CONTRIBUTION"
        else:
            group = "REVIEW_SCENARIOS"
        out.append({"sku": sku, "product_id": r["ID"], "title": r["Название"],
                    "observed_price": p or None, "historical_price": r["Прежняя наблюдавшаяся цена"] or None,
                    "observed_min_price": r["Минимальная цена API"] or None,
                    "candidate_price_not_approved": target or None,
                    "scenario_contribution": m or None,
                    "scenario_target_contribution": r["Прибыль сценарий пилот"] or None,
                    "model": model, "active_promos_snapshot": r["Участие в действующих акциях"],
                    "action_caps_snapshot": r["Кандидаты и пороги"], "diagnostic_group": group,
                    "approved_floor": None, "write_allowed": False,
                    "blocker": "OWNER_POLICY_AND_COMPLETE_ECONOMICS_NOT_VERIFIED"})
    from collections import Counter
    result = {"mode": "READ_ONLY_SNAPSHOT", "source": str(source), "count": len(out),
              "groups": dict(Counter(r["diagnostic_group"] for r in out)),
              "models": dict(Counter(r["model"] for r in out)), "rows": out}
    Path(destination).write_text(json.dumps(result, ensure_ascii=False, indent=2)+"\n", encoding="utf-8")
    return result


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", default="data/price_recovery_20261127/recovery_review.csv")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = review_snapshot(args.source, args.output)
    print({k:v for k,v in report.items() if k!="rows"})
