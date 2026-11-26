"""Read-only, exact-SKU Ozon catalogue decisions from saved or live evidence.

Strategies describe scenarios for commercial review, not authority to write.
No arbitrary profit minima, invented costs, price writes or seller credentials.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from copy import deepcopy
import csv
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path

from ozon_profit_economics import (
    procurement_context, historical_contribution, current_projection,
)

ROOT = Path(__file__).resolve().parent
LABELS = {
    "A": "Повышение цены с сохранением или подключением акции — проверить",
    "B": "Новая акция при текущей цене — проверить условия",
    "C": "Ближайший акционный порог со скидкой — отдельный тест",
    "D": "Альтернатива невыгодному максимальному бустингу",
    "E": "Восстановление экономики: известные расходы поглощают выручку",
    "F": "Сохранить текущую стратегию",
    "G": "Недостаточно данных для расчёта цены",
}
UNIFIED_TYPES = {"ELASTIC_BOOSTING", "STOCK_DISCOUNT"}
CSV_FIELDS = [
    "sku", "product_id", "title", "family", "strategy_code", "strategy_label",
    "strategy_reason", "current_price", "observed_min_price", "auto_add_enabled",
    "cost_rub", "current_contribution_rub", "current_profit_on_cost_pct",
    "current_margin_on_revenue_pct", "review_target_price",
    "review_target_contribution_rub", "review_target_delta_rub",
    "required_unit_growth_pct", "current_actions_count", "current_actions_names",
    "same_price_candidates_count", "future_same_price_candidates_count",
    "historical_contribution_rub", "historical_profit_on_cost_pct",
    "historical_margin_on_revenue_pct", "observed_previous_price",
    "flags", "unknown_costs", "snapshot_at", "write_allowed", "approved_floor_rub",
]


def amount(value):
    if isinstance(value, dict):
        if value.get("currency") not in (None, "", "RUB"):
            return None
        value = value.get("amount")
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    return float(number) if number.is_finite() else None


def positive(value):
    value = amount(value)
    return value if value is not None and value > 0 else None


def flag(value):
    if value is True or value == "ENABLED":
        return True
    if value is False or value == "DISABLED":
        return False
    return None


def exact_sku(value):
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"Invalid exact SKU: {value!r}")
    return value


def product_id(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    text = str(value)
    if not text.isdecimal() or int(text) <= 0:
        return None
    return int(text)


def index_rows(rows, key="sku"):
    result = {}
    for row in rows:
        sku = exact_sku(row.get(key))
        if sku in result:
            raise ValueError(f"Duplicate exact SKU in source: {sku!r}")
        result[sku] = row
    return result


def timestamp(value):
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt.astimezone(timezone.utc) if dt.tzinfo is not None else None


def unwrap_rows(value):
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        for key in ("items", "rows", "products"):
            if isinstance(value.get(key), list):
                return value[key]
        if "result" in value:
            return unwrap_rows(value["result"])
    return []


def normalize_prices(rows, products=None):
    product_rows = products or []
    product_by_sku = {}
    for p in product_rows:
        sku = p.get("offer_id") or p.get("sku")
        if isinstance(sku, str):
            exact_sku(sku)
            if sku in product_by_sku:
                raise ValueError("Duplicate product-list exact SKU: " + sku)
            product_by_sku[sku] = p
    result, pids = {}, {}
    for raw in rows:
        sku = exact_sku(raw.get("offer_id") or raw.get("sku"))
        if sku in result:
            raise ValueError("Duplicate price exact SKU: " + sku)
        pr = raw.get("price")
        p = pr if isinstance(pr, dict) and "amount" not in pr else raw
        pid = product_id(raw.get("product_id") or raw.get("id"))
        listed = product_by_sku.get(sku, {})
        list_pid = product_id(listed.get("product_id") or listed.get("id"))
        if pid and list_pid and pid != list_pid:
            raise ValueError("Price/product-list PID mismatch for " + sku)
        pid = pid or list_pid
        if pid and pid in pids and pids[pid] != sku:
            raise ValueError("One product_id maps to multiple exact SKUs")
        if pid:
            pids[pid] = sku
        row = deepcopy(raw)
        row.update({
            "sku": sku, "product_id": pid,
            "price": amount(p.get("price")),
            "min_price": amount(p.get("min_price")),
            "old_price": amount(p.get("old_price")),
            "auto": flag(p.get("auto_add_to_ozon_actions_list_enabled"))
                if "auto_add_to_ozon_actions_list_enabled" in p else flag(raw.get("auto")),
            "protection_enabled": flag(p.get("min_price_for_auto_actions_enabled")),
            "acquiring": amount(raw.get("acquiring")),
            "commissions": raw.get("commissions") or {},
            "price_indexes": raw.get("price_indexes") or {},
        })
        for scheme in ("fbs", "fbo"):
            name = "has_" + scheme + "_stocks"
            row[name] = listed.get(name, raw.get(name))
        if p.get("currency_code") not in (None, "", "RUB"):
            row["price"] = None
            row["invalid_currency"] = p.get("currency_code")
        result[sku] = row
    if product_by_sku:
        unknown = set(result) - set(product_by_sku)
        if unknown:
            raise ValueError("Prices not in active product scope: " + repr(sorted(unknown)))
    return result, pids


def validate_live_core(manifest, products, prices):
    """A partial finance/timer read is allowed; partial catalogue/prices are not."""
    if not isinstance(manifest, dict) or manifest.get("mode") != "READ_ONLY":
        raise ValueError("Live source requires a read-only collector manifest")
    if not timestamp(manifest.get("completed_at")):
        raise ValueError("Live source collection has not completed")
    sections = manifest.get("sections") or {}
    for name in ("products", "prices"):
        section = sections.get(name) or {}
        if section.get("status") != "ok" or section.get("errors"):
            raise ValueError("Live core section incomplete: " + name)
        coverage = section.get("coverage") or {}
        for key in ("missing_offer_ids", "conflicting_offer_ids", "duplicate_offer_ids",
                    "duplicate_product_ids", "missing_product_ids", "unexpected_product_ids"):
            if coverage.get(key):
                raise ValueError("Live core identity/coverage failure: " + name + ":" + key)
    if not isinstance(products, list) or not products or not isinstance(prices, list) or not prices:
        raise ValueError("Live products and prices must be nonempty raw row lists")
    product_pairs, price_pairs = {}, {}
    for name, rows, pairs in (("products", products, product_pairs), ("prices", prices, price_pairs)):
        pids = set()
        for row in rows:
            sku = exact_sku(row.get("offer_id") or row.get("sku"))
            pid = product_id(row.get("product_id") or row.get("id"))
            if pid is None or sku in pairs or pid in pids:
                raise ValueError("Live core missing/duplicate identity: " + name)
            if name == "products" and row.get("archived") is not False:
                raise ValueError("Live active-product archive state is unverified")
            pairs[sku] = pid
            pids.add(pid)
        if sections[name].get("count") != len(rows):
            raise ValueError("Live core manifest/file row count differs: " + name)
    if product_pairs != price_pairs:
        raise ValueError("Live products/prices do not have complete identical exact-SKU/PID coverage")
    catalog = manifest.get("catalog") or {}
    if catalog.get("active_count") != len(product_pairs):
        raise ValueError("Live catalogue count differs from manifest")
    if catalog.get("exact_offer_ids") is not None and sorted(product_pairs) != sorted(catalog["exact_offer_ids"]):
        raise ValueError("Live exact SKU scope differs from manifest")
    normalized, _ = normalize_prices(prices, products)
    if any(positive(row["price"]) is None or row.get("invalid_currency") for row in normalized.values()):
        raise ValueError("Live core contains an invalid/non-RUB seller price")


def normalize_master(value, metadata):
    if value is None:
        return {
            sku: {"title": row.get("title"), "cost": row.get("master_cost"),
                  "price": row.get("master_price"), "raw": row, "source": "saved_all_sku_review"}
            for sku, row in metadata.items()
        }
    if isinstance(value, dict) and isinstance(value.get("rows"), dict):
        value = value["rows"]
    if not isinstance(value, dict):
        raise ValueError("Master source must be an exact-SKU object")
    result = {}
    for sku, row in value.items():
        exact_sku(sku)
        if not isinstance(row, dict):
            raise ValueError("Invalid master row for " + sku)
        embedded = row.get("sku") or row.get("Артикул (offer_id)")
        if embedded is not None and embedded != sku:
            raise ValueError("Master key and row exact SKU differ")
        result[sku] = {
            "title": row.get("title") or row.get("Название товара"),
            "cost": row.get("cost", row.get("Закупка, ₽")),
            "price": row.get("price", row.get("Цена, ₽")),
            "ozon_enabled": row.get("ozon_enabled", row.get("Ozon (да/нет)")),
            "notes": row.get("notes", row.get("Заметки")),
            "raw": row, "source": "master_source",
        }
    return result


def family(title):
    value = (title or "").lower().strip()
    if value.startswith("мехатроник"):
        return "Мехатроники"
    for stems, group in (
        (("гидроплит", "гидроблок"), "Гидроплиты и гидроблоки"),
        (("электронная плата", "электронной платы", "плата управления", "модуль управления"), "Электроника"),
        (("соленоид",), "Соленоиды"),
        (("ремкомплект", "комплект"), "Комплекты"),
        (("шток", "толкател"), "Штоки и толкатели"),
        (("втулк", "гильз"), "Втулки и гильзы"),
        (("фильтр", "сальник", "прокладк", "уплотн"), "Фильтры и уплотнения"),
    ):
        if any(s in value for s in stems):
            return group
    return "Прочие запчасти"


def lifecycle(action, as_of):
    start, end = timestamp(action.get("date_start")), timestamp(action.get("date_end"))
    if start is None or end is None or end < start:
        return "unknown"
    if end < as_of:
        return "expired"
    return "future" if start > as_of else "active"


def normalize_actions(promotions, price_index, pid_index, as_of):
    by_sku, warnings, affected = defaultdict(list), [], defaultdict(set)
    coverage_complete = True
    for bundle in promotions:
        action = bundle.get("action") or {}
        aid = product_id(action.get("id"))
        if aid is None:
            coverage_complete = False
            warnings.append("ACTION_ID_MISSING")
            continue
        state = lifecycle(action, as_of)
        sections = bundle.get("sections") or {}
        for group in ("participants", "candidates"):
            section = sections.get(group)
            if not isinstance(section, dict) or section.get("status") != "ok":
                coverage_complete = False
            if not isinstance(section, dict):
                continue
            seen = set()
            for raw in section.get("rows") or []:
                pid = product_id(raw.get("id") or raw.get("product_id"))
                mapped = raw.get("mapped_offer_id") or raw.get("offer_id")
                sku = pid_index.get(pid)
                if sku is None and isinstance(mapped, str) and mapped in price_index:
                    # Require a supplied PID to agree even when a mapped name exists.
                    expected = price_index[mapped].get("product_id")
                    if pid is None or expected is None or pid != expected:
                        affected[mapped].add("PROMOTION_IDENTITY_UNVERIFIED")
                        continue
                    sku = mapped
                if sku is None:
                    warnings.append(f"UNMAPPED_PROMOTION_PRODUCT:{aid}:{pid}")
                    continue
                if mapped is not None and mapped != sku:
                    affected[sku].add("PROMOTION_IDENTITY_MISMATCH")
                    coverage_complete = False
                    continue
                if sku in seen:
                    affected[sku].add("DUPLICATE_PROMOTION_ROW")
                    coverage_complete = False
                    continue
                seen.add(sku)
                typ = action.get("action_type")
                special = (
                    typ not in UNIFIED_TYPES
                    or action.get("is_voucher_action") is True
                    or action.get("discount_type") not in (None, "CURRENCY")
                    or (amount(action.get("discount_value")) or 0) != 0
                )
                cap = positive(raw.get("max_action_price"))
                if cap is None and typ == "ELASTIC_BOOSTING":
                    cap = positive(raw.get("price_min_elastic"))
                invalid = bool(raw.get("is_quarantined") is True or raw.get("failed") is True
                               or raw.get("alert_max_action_price_failed") is True)
                rec = {
                    "action_id": aid, "title": action.get("title") or str(aid),
                    "action_type": typ, "group": group, "lifecycle": state,
                    "date_start": action.get("date_start"), "date_end": action.get("date_end"),
                    "cap_rub": cap, "observed_action_price": amount(raw.get("action_price")),
                    "current_boost": amount(raw.get("current_boost", raw.get("current"))),
                    "min_boost": amount(raw.get("min_boost", raw.get("min"))),
                    "max_boost": amount(raw.get("max_boost")),
                    "price_max_elastic": positive(raw.get("price_max_elastic")),
                    "price_min_elastic": positive(raw.get("price_min_elastic")),
                    "additional_discount_or_unknown_type": special,
                    "quarantined_or_failed": invalid,
                    "price_qualifies_at_current": cap is not None
                        and price_index[sku]["price"] is not None
                        and price_index[sku]["price"] <= cap,
                    "stock_in_promotion_row": raw.get("stock"),
                    "raw": deepcopy(raw),
                }
                rec["eligible_for_price_comparison"] = (
                    state in ("active", "future") and cap is not None and not invalid and not special
                )
                by_sku[sku].append(rec)
    return by_sku, coverage_complete, warnings, affected


def unit_growth(before, after):
    if positive(before) is None or positive(after) is None:
        return None
    return round((float(before) / float(after) - 1) * 100, 4)


def protection_for(sku, price, source):
    found = []
    for row in (source or {}).get("rows", []):
        pid = product_id(row.get("product_id"))
        mapped = row.get("mapped_offer_id") or row.get("offer_id")
        if mapped == sku or (pid is not None and pid == price.get("product_id")):
            if mapped is not None and mapped != sku:
                return {"status": "IDENTITY_CONFLICT", "verified": False}
            if pid is not None and pid != price.get("product_id"):
                return {"status": "IDENTITY_CONFLICT", "verified": False}
            found.append(row)
    if len(found) > 1:
        return {"status": "AMBIGUOUS", "verified": False}
    if not found:
        return {"status": "NOT_VERIFIED", "verified": False,
                "observed_min_price": price.get("min_price"),
                "enabled_flag_in_price_response": price.get("protection_enabled")}
    row = found[0]
    return {"status": "OBSERVED_ACTIVE" if row.get("active") is True else
            "OBSERVED_INACTIVE" if row.get("active") is False else "SCHEMA_OR_STATUS_UNKNOWN",
            "verified": False, "enabled": row.get("enabled"),
            "active": row.get("active"), "expired_at": row.get("expired_at"),
            "raw": row.get("raw", row)}


def stock_for(sku, price, rows):
    found = []
    for row in rows:
        pid = product_id(row.get("product_id") or row.get("id"))
        if row.get("offer_id") == sku or (pid is not None and pid == price.get("product_id")):
            if row.get("offer_id") not in (None, sku) or (pid is not None and pid != price.get("product_id")):
                return {"status": "IDENTITY_CONFLICT", "rows": [],
                        "has_fbs_stocks": price.get("has_fbs_stocks"),
                        "has_fbo_stocks": price.get("has_fbo_stocks")}
            found.append(row)
    return {"status": "OBSERVED" if found else "FLAGS_ONLY",
            "rows": found, "has_fbs_stocks": price.get("has_fbs_stocks"),
            "has_fbo_stocks": price.get("has_fbo_stocks")}


def preserve_bound(price, actions, coverage_complete):
    current = price["price"]
    if positive(current) is None:
        return None, ["INVALID_SELLER_PRICE"]
    existing = [a for a in actions if a["group"] == "participants" and a["lifecycle"] != "expired"]
    flags, bounds = [], []
    if not coverage_complete:
        return current, ["PROMOTION_COVERAGE_INCOMPLETE"]
    for a in existing:
        if not a["eligible_for_price_comparison"]:
            return current, ["EXISTING_PROMOTION_TERMS_REQUIRE_SEPARATE_REVIEW"]
        if a["cap_rub"] < current:
            return current, ["CURRENT_PRICE_ABOVE_OBSERVED_ACTIVE_CAP"]
        bounds.append(a["cap_rub"])
        if a["action_type"] == "ELASTIC_BOOSTING":
            top, observed, cap = a["max_boost"], a["current_boost"], a["price_max_elastic"]
            if top is not None and top > 0 and observed is not None and observed >= top and cap is not None:
                if cap < current:
                    return current, ["ELASTIC_PRICE_AND_BOOST_CONFLICT"]
                bounds.append(cap)
            else:
                # Membership in elastic is not proof of unchanged strength after a rise.
                bounds.append(current)
                flags.append("ELASTIC_STRENGTH_DOES_NOT_SUPPORT_PRICE_RAISE")
    return (min(bounds) if bounds else None), flags


def scenario(name, target, current, project, actions=(), flags=()):
    econ = project(target) if positive(target) is not None else None
    after = econ.get("contribution_rub") if econ else None
    before = current.get("contribution_rub")
    change = unit_growth(before, after)
    return {
        "name": name, "target_price": target,
        "contribution_rub": after,
        "profit_on_cost_pct": econ.get("profit_on_cost_pct") if econ else None,
        "margin_on_revenue_pct": econ.get("margin_on_revenue_pct") if econ else None,
        "delta_contribution_rub": round(after - before, 2) if after is not None and before is not None else None,
        "required_unit_growth_pct": change,
        "break_even_units_change_pct": change,
        "additional_unit_growth_needed_pct": max(change, 0) if change is not None else None,
        "tolerable_unit_decline_pct": max(-change, 0) if change is not None else None,
        "action_ids": [a["action_id"] for a in actions],
        "action_titles": [a["title"] for a in actions],
        "future_only": bool(actions) and all(a["lifecycle"] == "future" for a in actions),
        "flags": sorted(set(flags)), "economics": econ,
        "status": "REVIEW_ONLY", "write_allowed": False,
        "approved_floor_rub": None,
        "metric_kind": "partial_contribution_not_net_profit",
        "volume_forecast": None,
    }


def build_catalogue(*, prices, metadata, promotions, finance=None, master=None,
                    products=None, stocks=None, protection=None, recovery=None,
                    support=None, snapshot_at=None, as_of=None,
                    source_mode="SAVED_SNAPSHOT", sources=None, manifest=None):
    """Return every priced exact SKU once, with one A-G review strategy."""
    as_of = as_of or datetime.now(timezone.utc)
    if isinstance(as_of, str):
        as_of = timestamp(as_of)
    if as_of is None:
        raise ValueError("as_of must be an explicit timezone-aware timestamp")
    if source_mode == "LIVE_READ_ONLY":
        validate_live_core(manifest, products, prices)
        if master is None:
            master = {}  # A failed live master read never adopts old procurement silently.
    price_map, pid_map = normalize_prices(unwrap_rows(prices), unwrap_rows(products))
    metadata_map = index_rows(metadata)
    master_map = normalize_master(master, metadata_map)
    action_map, promo_complete, action_warnings, identity_flags = normalize_actions(
        promotions, price_map, pid_map, as_of)
    sections = (manifest or {}).get("sections") or {}
    if source_mode == "LIVE_READ_ONLY" and (sections.get("promotions") or {}).get("status") != "ok":
        promo_complete = False
    if source_mode != "LIVE_READ_ONLY" and not promotions:
        promo_complete = False
    finance = finance or {}
    recovery = recovery or {}
    support = support or {}
    bom_map = index_rows(support.get("bom") or [])
    finance_rows = finance.get("items") or {}
    fees = finance.get("fees_by_type") or {}
    finance_complete = finance.get("collection_complete")
    if source_mode == "LIVE_READ_ONLY":
        finance_complete = finance_complete is True and (sections.get("finance") or {}).get("status") == "ok"
    finance_coverage = {
        "collection_complete": finance_complete,
        "from": finance.get("from"), "to": finance.get("to"),
        "window": finance.get("window"), "coverage": finance.get("coverage"),
        "daily_coverage": finance.get("daily_coverage"),
        "source_status": (sections.get("finance") or {}).get("status"),
        "interpretation": "Observed accruals only; unavailable dates are not zero-sales dates.",
    }
    output = []
    global_flags = ["OWNER_FINANCIAL_RULES_NOT_APPROVED", "FULL_PROFIT_NOT_VERIFIED"]
    if source_mode != "LIVE_READ_ONLY":
        global_flags.append("LIVE_RECHECK_REQUIRED")
    if not promo_complete:
        global_flags.append("PROMOTION_COVERAGE_INCOMPLETE")
    if manifest and manifest.get("status") != "complete_read_only":
        global_flags.append("LIVE_COLLECTION_PARTIAL_OR_CATALOG_CHANGED")
    if finance_complete is False:
        global_flags.append("PARTIAL_FINANCE_PERIOD")
    elif finance_complete is None:
        global_flags.append("FINANCE_DATE_COVERAGE_NOT_VERIFIED")
    for section_name, section_data in sections.items():
        if section_data.get("status") != "ok":
            global_flags.append("SOURCE_SECTION_" + section_name.upper() + "_INCOMPLETE")

    for sku, p in price_map.items():
        base = metadata_map.get(sku, {})
        m = master_map.get(sku, {})
        old = recovery.get(sku, {})
        bom = bom_map.get(sku, {})
        row_flags = set(global_flags) | set(identity_flags.get(sku, set()))
        title = m.get("title") or base.get("title") or sku
        reference = max((positive(x) for x in (
            base.get("conservative_cost_reference"), base.get("cost_floor_previous_audit")
        ) if positive(x) is not None), default=None)
        try:
            procurement = procurement_context(m.get("cost"), reference,
                                               bom.get("components_cost") if bom.get("cost_complete") else None)
        except ValueError:
            procurement = procurement_context(None)
            row_flags.add("INVALID_PROCUREMENT_SOURCE")
        cost = procurement["scenario_cost_rub"]
        fin = finance_rows.get(sku)
        if fin is None and not finance_rows and source_mode != "LIVE_READ_ONLY":
            fin = base.get("finance")
            if fin is not None:
                row_flags.add("FINANCE_FROM_SAVED_METADATA_FALLBACK")
        service_fees = fees.get(sku)
        cost_source = {
            "master_source": m.get("source"), "reference": "all_sku_review",
            "component_source": support.get("bom_source") if bom else None,
            "basis": "largest positive supplied reference; not verified landed procurement",
        }

        def project(target):
            try:
                return current_projection(target, cost, p, fin, service_fees, cost_source=cost_source)
            except (ValueError, TypeError, ArithmeticError):
                return {
                    "contribution_rub": None, "profit_on_cost_pct": None, "margin_on_revenue_pct": None,
                    "flags": ["INVALID_FINANCIAL_OR_TARIFF_INPUT"], "unknown_costs": [],
                    "verified": False, "write_allowed": False, "services_estimated": False,
                    "metric_kind": "partial_contribution_not_net_profit",
                }

        current = project(p["price"])
        try:
            historical = historical_contribution(fin, cost, cost_source=cost_source)
        except (ValueError, TypeError, ArithmeticError):
            historical = {"contribution_rub": None, "profit_on_cost_pct": None,
                          "margin_on_revenue_pct": None, "flags": ["INVALID_FINANCE_INPUT"],
                          "unknown_costs": [], "verified": False, "write_allowed": False}
        historical["source_coverage"] = finance_coverage
        historical["procurement_basis"] = {
            "master_cost_rub": procurement["master_cost_rub"],
            "scenario_cost_rub": cost,
            "is_historical_purchase_cost": False,
            "interpretation": "Current conservative procurement scenario, not observed historical landed purchase cost.",
        }
        if cost != procurement["master_cost_rub"]:
            historical["flags"].append("HISTORICAL_COST_USES_CONSERVATIVE_REFERENCE")
        if finance_complete is False:
            historical["flags"].append("PARTIAL_FINANCE_PERIOD")
        row_flags.update(procurement["flags"])
        row_flags.update(current["flags"])
        row_flags.update(historical["flags"])
        if sku not in master_map:
            row_flags.add("SKU_MISSING_FROM_MASTER")
        if p["product_id"] is None:
            row_flags.add("PRODUCT_ID_UNVERIFIED")
        if p.get("invalid_currency"):
            row_flags.add("NON_RUB_PRICE")
        if p.get("has_fbs_stocks") is False and p.get("has_fbo_stocks") is False:
            row_flags.add("NO_STOCK_IN_SNAPSHOT_FLAGS")
        owner_hold = str(m.get("ozon_enabled") or "").strip().lower() in ("нет", "false", "no", "0")
        notes = str(m.get("notes") or "")
        if owner_hold:
            row_flags.add("OWNER_OZON_DISABLED")
        if "пока не прода" in notes.lower():
            row_flags.add("OWNER_SALES_HOLD_NOTE_REQUIRES_REVIEW")
            owner_hold = True
        if "ozon:нет" in notes.lower().replace(" ", "") and not owner_hold:
            row_flags.add("OLDER_NOTE_CONFLICTS_WITH_CURRENT_CHANNEL_FLAG")
        if sku == "TEST001":
            owner_hold = True
            row_flags.add("TEST_ITEM_NOT_COMMERCIAL")
        arows = deepcopy(action_map.get(sku, []))
        active = [a for a in arows if a["group"] == "participants" and a["lifecycle"] == "active"]
        existing = [a for a in arows if a["group"] == "participants" and a["lifecycle"] in ("active", "future")]
        candidates = [a for a in arows if a["group"] == "candidates"
                      and a["eligible_for_price_comparison"]]
        same = [a for a in candidates if a["price_qualifies_at_current"]]
        future_same = [a for a in same if a["lifecycle"] == "future"]
        bound, bound_flags = preserve_bound(p, arows, promo_complete)
        row_flags.update(bound_flags)
        if identity_flags.get(sku):
            bound = p["price"]
        previous = positive(old.get("Прежняя наблюдавшаяся цена"))
        old_candidate = positive(old.get("Цена пилота НЕ УТВЕРЖДЕНА"))
        target, chosen_actions, preservation_flags, target_origin = p["price"], existing, [], "current"
        if positive(p["price"]) is not None and not owner_hold:
            proposed = []
            if old_candidate is not None and old_candidate > p["price"]:
                proposed.append((old_candidate, "previous_unapproved_pilot"))
            if previous is not None and previous > p["price"] and previous != old_candidate:
                proposed.append((previous, "observed_previous_price"))
            for desired, origin in proposed:
                qualifying = [a for a in candidates if desired <= a["cap_rub"]]
                has_promo = bool(existing or qualifying)
                keeps_existing = bound is None or desired <= bound
                if has_promo and keeps_existing and promo_complete and not identity_flags.get(sku):
                    target, target_origin = desired, origin
                    chosen_actions = existing + qualifying
                    preservation_flags.append("COMPETITIVE_BUYER_PRICE_REVIEW_REQUIRED")
                    break
                preservation_flags.append("PREVIOUS_RAISE_CANDIDATE_NOT_SUPPORTED_BY_CURRENT_TERMS")
        preserve = scenario("preservation", target, current, project,
                            chosen_actions, preservation_flags)
        preserve.update({
            "target_origin": target_origin, "preserve_existing_cap_rub": bound,
            "preserves_observed_membership_and_elastic_strength": bool(
                positive(target) is not None and promo_complete and not identity_flags.get(sku)
                and (bound is None or target <= bound)),
            "is_raise": positive(target) is not None and positive(p["price"]) is not None and target > p["price"],
        })
        discounts = [a for a in candidates if positive(p["price"]) is not None and a["cap_rub"] < p["price"]]
        discounts.sort(key=lambda a: (-a["cap_rub"], a["lifecycle"] != "active", a["action_id"]))
        closest_a = discounts[:1]
        closest = scenario("closest_discount", closest_a[0]["cap_rub"] if closest_a else None,
                           current, project, closest_a,
                           ["SALES_UPLIFT_NOT_FORECAST", "PROMOTION_COMBINATION_NOT_VERIFIED"] if closest_a else [])
        elastic = [a for a in arows if a["eligible_for_price_comparison"]
                   and a["action_type"] == "ELASTIC_BOOSTING" and a["price_max_elastic"] is not None]
        elastic.sort(key=lambda a: (-a["price_max_elastic"], a["lifecycle"] != "active", a["action_id"]))
        max_a = elastic[:1]
        maximum = scenario("max_boost", max_a[0]["price_max_elastic"] if max_a else None,
                           current, project, max_a,
                           ["BOOST_PERCENT_IS_NOT_SALES_UPLIFT", "PROMOTION_COMBINATION_NOT_VERIFIED"] if max_a else [])
        maximum["specified_action_boost_pct"] = max_a[0]["max_boost"] if max_a else None
        for a in arows:
            a["at_action_cap"] = scenario(
                "action_cap", a["cap_rub"], current, project, [a],
                (["SEPARATE_DISCOUNT_MODEL_REQUIRED"] if a["additional_discount_or_unknown_type"] else [])
                + (["EXPIRED_OR_UNKNOWN_ACTION_DATES"] if a["lifecycle"] not in ("active", "future") else []))
            a["at_current_price"] = {
                "price_qualifies": a["price_qualifies_at_current"],
                "contribution_before_new_action_specific_costs_rub": current.get("contribution_rub"),
                "additional_action_costs_verified": False,
                "write_allowed": False,
            }

        cp = current.get("contribution_rub")
        pp = preserve.get("contribution_rub")
        dp = closest.get("contribution_rub")
        mp = maximum.get("contribution_rub")
        selected = preserve
        if owner_hold:
            group, reason = "F", "Сохранить исключение владельца/тестовый статус; автоматические коммерческие изменения запрещены."
        elif cp is None or positive(cost) is None:
            group, reason, selected = "G", "Нет закупки или пригодных тарифов; ценовые возможности сохранены для проверки.", None
        elif preserve["is_raise"] and pp is not None and pp > 0 and pp >= cp:
            group, reason = "A", "Ранее наблюдавшаяся или предложенная цена укладывается в проверенные границы акций; расчётный остаток растёт."
        elif cp <= 0:
            group, reason, selected = "E", "Даже предварительный остаток после известных расходов неположителен; нужна сверка расходов и цены.", None
            row_flags.add("NONPOSITIVE_PARTIAL_CONTRIBUTION")
        elif same:
            group = "B"
            reason = ("Есть только будущая новая акция при текущей цене; проверить условия и запланировать, не считать подключённой."
                      if len(future_same) == len(same) else
                      "Есть кандидат на новую действующую акцию при текущей цене; дополнительные расходы требуют проверки.")
            selected = scenario("same_price_new_action", p["price"], current, project, same,
                                ["ACTION_SPECIFIC_COSTS_AND_COMBINATION_NOT_VERIFIED"])
        elif mp is not None and mp <= 0 and (active or (dp is not None and dp > 0)):
            group, reason = "D", "Максимальный эластичный бустинг даёт неположительный расчётный остаток; рассмотреть мягкую акцию или сохранить участие."
            selected = closest if dp is not None and dp > 0 else preserve
        elif dp is not None and dp > 0:
            group, reason, selected = "C", "Ближайший новый акционный порог требует скидки; положительный предварительный остаток не доказывает рост общей прибыли.", closest
        else:
            group, reason = "F", "По доступным условиям улучшение без новой уступки в прибыли не установлено; сохранить текущую стратегию."
        stock = stock_for(sku, p, unwrap_rows(stocks))
        protect = protection_for(sku, p, protection)
        if protect["status"] != "OBSERVED_ACTIVE":
            row_flags.add("PROTECTION_ACTIVE_STATE_NOT_CONFIRMED")
        if stock["status"] == "IDENTITY_CONFLICT":
            row_flags.add("STOCK_IDENTITY_CONFLICT")
        row_flags.add("COMPETITOR_INDEX_IS_NOT_INDEPENDENT_MARKET_REVIEW")
        unknowns = sorted(set(current.get("unknown_costs", [])) | set(historical.get("unknown_costs", [])))
        output.append({
            "sku": sku, "product_id": p["product_id"], "title": title, "family": family(title),
            "strategy_code": group, "strategy_label": LABELS[group], "strategy_reason": reason,
            "current_price": p["price"], "observed_min_price": p["min_price"],
            "old_price": p["old_price"], "auto_add_enabled": p["auto"], "cost_rub": cost,
            "current_contribution_rub": cp,
            "current_profit_on_cost_pct": current.get("profit_on_cost_pct"),
            "current_margin_on_revenue_pct": current.get("margin_on_revenue_pct"),
            "review_target_price": selected["target_price"] if selected else None,
            "review_target_contribution_rub": selected["contribution_rub"] if selected else None,
            "review_target_delta_rub": selected["delta_contribution_rub"] if selected else None,
            "required_unit_growth_pct": selected["required_unit_growth_pct"] if selected else None,
            "additional_unit_growth_needed_pct": selected["additional_unit_growth_needed_pct"] if selected else None,
            "tolerable_unit_decline_pct": selected["tolerable_unit_decline_pct"] if selected else None,
            "historical_contribution_rub": historical.get("contribution_rub"),
            "historical_profit_on_cost_pct": historical.get("profit_on_cost_pct"),
            "historical_margin_on_revenue_pct": historical.get("margin_on_revenue_pct"),
            "observed_previous_price": previous,
            "recorded_reductions": old.get("Обнаруженные снижения UTC"),
            "current_actions_count": len(active), "current_actions_names": [a["title"] for a in active],
            "future_participations_count": sum(a["lifecycle"] == "future" for a in existing),
            "same_price_candidates_count": len(same),
            "active_same_price_candidates_count": len(same) - len(future_same),
            "future_same_price_candidates_count": len(future_same),
            "same_price_candidates": [{"action_id": a["action_id"], "title": a["title"],
                                      "lifecycle": a["lifecycle"], "date_start": a["date_start"]}
                                     for a in same],
            "financial_model": "current_tariffs_with_observed_services" if current.get("services_estimated")
                               else "partial_tariff_subtotal" if cp is not None else "missing_economics",
            "metric_kind": "partial_contribution_not_net_profit",
            "economics": {"procurement": procurement, "historical": historical, "current": current},
            "scenarios": {"preservation": preserve, "closest_discount": closest, "max_boost": maximum},
            "actions": arows, "stock": stock, "protection": protect, "price_indexes": p["price_indexes"],
            "owner_channel_flag": m.get("ozon_enabled"), "owner_notes": m.get("notes"),
            "owner_hold": owner_hold,
            "flags": sorted(row_flags), "unknown_costs": unknowns,
            "snapshot_at": snapshot_at, "sources": deepcopy(sources or {}),
            "decision_status": "REVIEW_ONLY", "write_allowed": False,
            "approved_floor_rub": None, "volume_forecast": None,
        })
    counts = Counter(row["strategy_code"] for row in output)
    flag_counts = Counter(x for row in output for x in row["flags"])
    summary = {
        "schema_version": 1, "mode": "READ_ONLY", "generated_at": as_of.isoformat(),
        "source_mode": source_mode, "snapshot_at": snapshot_at,
        "count": len(output), "groups": {k: counts[k] for k in LABELS},
        "labels": LABELS, "marketplace_writes": 0, "approved_prices": 0,
        "metric_legend": {
            "required_unit_growth_pct": "(current partial contribution / candidate partial contribution - 1) * 100, only if both positive. A negative value is tolerable unit decline; it is not forecast demand.",
            "contribution_rub": "Partial contribution under supplied procurement, tariffs, observed-service and tax assumptions; unknown costs remain explicitly unverified.",
        },
        "financial": {
            "known_procurement": sum(r["cost_rub"] is not None for r in output),
            "current_subtotals_available": sum(r["current_contribution_rub"] is not None for r in output),
            "historical_unit_contribution_available": sum(r["historical_contribution_rub"] is not None for r in output),
            "current_services_estimated": sum(bool(r["economics"]["current"].get("services_estimated")) for r in output),
            "verified_full_profit": 0,
        },
        "opportunities": {
            "preservation_raise": sum(r["scenarios"]["preservation"]["is_raise"] for r in output),
            "same_price_new_action_skus": sum(r["same_price_candidates_count"] > 0 for r in output),
            "active_same_price_new_action_skus": sum(r["active_same_price_candidates_count"] > 0 for r in output),
            "future_same_price_new_action_skus": sum(r["future_same_price_candidates_count"] > 0 for r in output),
            "closest_discount_positive_partial": sum(positive(r["scenarios"]["closest_discount"]["contribution_rub"]) is not None for r in output),
            "max_boost_nonpositive_partial": sum(r["scenarios"]["max_boost"]["contribution_rub"] is not None and
                                               r["scenarios"]["max_boost"]["contribution_rub"] <= 0 for r in output),
        },
        "flags": dict(sorted(flag_counts.items())),
        "owner_hold_count": sum(r["owner_hold"] for r in output),
        "promotion_coverage_complete": promo_complete,
        "source_warnings": sorted(set(action_warnings)),
        "metadata_skus_outside_price_scope": sorted(set(metadata_map) - set(price_map)),
        "finance_period": finance_coverage,
        "unassigned_marketplace_fees": finance.get("unassigned"),
        "limitations": [
            "Every amount is partial contribution, not verified net profit.",
            "No universal percentage reserve, packaging charge or minimum profit is invented.",
            "Action-specific fees and simultaneous promotion effects require verification.",
            "Price indexes are observations, not independent competitor research.",
            "No sales-volume uplift is forecast and no price is approved.",
        ],
    }
    return {
        "schema_version": 1, "generated_at": as_of.isoformat(), "mode": "READ_ONLY",
        "source_mode": source_mode, "snapshot_at": snapshot_at, "scope_count": len(output),
        "sources": deepcopy(sources or {}), "rows": output, "summary": summary,
        "write_allowed": False, "marketplace_writes": 0,
    }


def read_json(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else default


def source_record(path):
    path = Path(path)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} if path.is_file() else {"path": str(path), "missing": True}


def read_recovery(path):
    path = Path(path)
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = [r for r in csv.DictReader(stream, delimiter=";") if r.get("Площадка") == "Ozon"]
    result = {}
    for row in rows:
        sku = exact_sku(row["Артикул"])
        if sku in result:
            raise ValueError("Duplicate recovery SKU: " + sku)
        result[sku] = row
    return result


def generate(baseline, destination, live_source=None, finance_source=None,
             master_source=None, finance_support=None, as_of=None):
    baseline, destination = Path(baseline), Path(destination)
    live = Path(live_source) if live_source else None
    manifest = read_json(live / "manifest.json") if live else None
    if live and not manifest:
        raise ValueError("--live-source requires manifest.json; no silent stale fallback")
    prices_path = live / "prices.json" if live else baseline / "ozon_live_prices.json"
    promo_path = live / "promotions.json" if live else baseline / "ozon_live_promotions.json"
    finance_path = Path(finance_source) if finance_source else (live / "finance.json" if live else ROOT / "data/ozon_finance.json")
    master_path = Path(master_source) if master_source else (live / "master.json" if live else baseline / "master.json")
    support_path = Path(finance_support) if finance_support else destination / "finance_support.json"
    registry = read_json(baseline / "registry.json", {})
    snapshot_at = ((manifest or {}).get("sections", {}).get("prices", {}).get("completed_at")
                   or (manifest or {}).get("completed_at")
                   or registry.get("ozon_live", {}).get("checked_utc")
                   or registry.get("started_utc"))
    source_paths = {"prices": prices_path, "promotions": promo_path, "finance": finance_path,
                    "master": master_path, "metadata": baseline / "all_sku_review.json",
                    "recovery": baseline / "recovery_review.csv", "finance_support": support_path}
    if live:
        source_paths.update({name: live / filename for name, filename in (
            ("manifest", "manifest.json"), ("products", "products.json"),
            ("stocks", "stocks.json"), ("protection", "protection.json"),
        )})
    source_records = {k: source_record(v) for k, v in source_paths.items()}
    for name, record in source_records.items():
        if live and name in (manifest.get("sections") or {}):
            record["collection"] = deepcopy(manifest["sections"][name])
        elif name in ("prices", "promotions"):
            record["snapshot_at"] = snapshot_at
    prices = read_json(prices_path)
    if prices is None:
        raise ValueError("Price source missing")
    report = build_catalogue(
        prices=prices, metadata=read_json(source_paths["metadata"], []),
        promotions=read_json(promo_path, []), finance=read_json(finance_path, {}),
        master=read_json(master_path), recovery=read_recovery(source_paths["recovery"]),
        support=read_json(support_path, {}),
        products=read_json(live / "products.json", []) if live else None,
        stocks=read_json(live / "stocks.json", []) if live else None,
        protection=read_json(live / "protection.json", {}) if live else None,
        snapshot_at=snapshot_at, as_of=as_of,
        source_mode="LIVE_READ_ONLY" if live else "SAVED_SNAPSHOT",
        sources=source_records, manifest=manifest,
    )
    if not report["rows"]:
        raise ValueError("Empty price scope; no catalogue emitted")
    destination.mkdir(parents=True, exist_ok=True)
    for name, value in (("catalogue.json", report), ("summary.json", report["summary"])):
        (destination / name).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    with (destination / "review.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=CSV_FIELDS, delimiter=";")
        writer.writeheader()
        for row in report["rows"]:
            writer.writerow({key: " | ".join(str(v) for v in row[key]) if isinstance(row.get(key), list)
                             else row.get(key) for key in CSV_FIELDS})
    return report["summary"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=ROOT / "data/price_recovery_20261127")
    parser.add_argument("--output", type=Path, default=ROOT / "data/ozon_profit_first_20261127")
    parser.add_argument("--live-source", type=Path)
    parser.add_argument("--finance-source", type=Path)
    parser.add_argument("--master-source", type=Path)
    parser.add_argument("--finance-support", type=Path)
    parser.add_argument("--as-of", help="Timezone-aware ISO timestamp for deterministic action lifecycle analysis")
    args = parser.parse_args()
    summary = generate(args.baseline, args.output, args.live_source, args.finance_source,
                       args.master_source, args.finance_support, args.as_of)
    print(json.dumps({k: summary[k] for k in ("count", "groups", "financial", "opportunities", "marketplace_writes")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

