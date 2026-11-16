"""Staging-only checkout boundary; never alters Tilda, Yandex or CDEK."""
from dataclasses import dataclass
from decimal import Decimal
from typing import Mapping
from .delivery_core import PickupPoint, Quote

class CheckoutError(ValueError): pass

@dataclass(frozen=True)
class DeliverySelection:
    carrier: str
    pickup_point_id: str
    pickup_point_label: str
    quote_amount_rub: Decimal
    quote_source: str
    checkout_enabled: bool

def make_demo_selection(point: PickupPoint, quote: Quote) -> DeliverySelection:
    if point.provider != "ozon" or quote.point_id != point.id:
        raise CheckoutError("Pickup point and quote mismatch")
    if quote.source != "synthetic_demo":
        raise CheckoutError("Unexpected quote source")
    return DeliverySelection("ozon",point.id,f"{point.city}, {point.address}",
                             quote.amount_rub,quote.source,False)

def validate_server_quote(point_id: str, quote: Quote) -> None:
    if quote.point_id != point_id:
        raise CheckoutError("Quote belongs to a different pickup point")
    if quote.amount_rub < Decimal("0"):
        raise CheckoutError("Negative delivery price")
    if quote.eta_min_days < 0 or quote.eta_max_days < quote.eta_min_days:
        raise CheckoutError("Invalid delivery window")

def production_checkout_payload(*args, **kwargs):
    """Fail closed: Tilda ST100 compatibility and verified prices are prerequisites."""
    raise RuntimeError("Production checkout is disabled until ST100 staging validation")

def create_live_order(*args, **kwargs):
    raise RuntimeError("Ozon shipment creation is disabled until API and idempotency are verified")
