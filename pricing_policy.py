"""LEOPARTS: fail-closed owner pricing policy.

Freeze all automatic repricing on Avito/Tilda, without altering prices stored in
workbooks or published offers. Preview calculators remain available for research.
Only manual, explicitly confirmed per-SKU prices may be applied later.
"""
import json
from pathlib import Path

POLICY_FILE = Path(__file__).resolve().parent / "data" / "pricing_policy.json"


class PricingFrozenError(RuntimeError):
    pass


def load_policy(path=None):
    path = Path(path or POLICY_FILE)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise PricingFrozenError("Price policy unavailable; automatic repricing is forbidden") from exc
    if (not isinstance(data, dict)
            or data.get("schema_version") != 1
            or data.get("mode") not in ("manual_prices_only", "legacy_automatic")
            or data.get("preserve_existing_prices") is not True):
        raise PricingFrozenError("Invalid pricing policy; do not change live prices")
    return data


def auto_repricing_enabled(path=None):
    """Requires EXPLICIT opt-in; any absent flag or bad config fails closed."""
    p = load_policy(path)
    return (
        p["mode"] == "legacy_automatic"
        and p.get("automatic_avito_site_repricing") is True
        and p.get("approved_to_enable") is True
    )


def auto_repricing_frozen(path=None):
    return not auto_repricing_enabled(path)


def require_legacy_new_product_prices(skus, avito_prices, site_prices, fixed):
    """Validate old onboarding BEFORE any external API mutations.

    A missing price is NOT permission to fall back to marketplace price or
    recalculate from 70%/60%. A fixed marker documents explicit approval.
    """
    missing = [
        sku for sku in skus
        if sku not in fixed
        or not isinstance(avito_prices.get(sku), (int, float))
        or avito_prices[sku] <= 0
        or not isinstance(site_prices.get(sku), (int, float))
        or site_prices[sku] <= 0
    ]
    if missing:
        raise PricingFrozenError(
            "Нет утверждённых фиксированных цен Авито/сайта для новых SKU: "
            + ", ".join(sorted(missing))
            + ". Старый конвейер остановлен ДО создания карточек; "
            "добавьте отдельные цены в заявку Product Intake v2."
        )
    return True
