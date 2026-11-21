"""Ozon Delivery integration domain layer. No external API or production writes."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Protocol
import re

@dataclass(frozen=True)
class PickupPoint:
    id: str
    city: str
    address: str
    provider: str = "ozon"

@dataclass(frozen=True)
class Quote:
    point_id: str
    amount_rub: Decimal
    eta_min_days: int
    eta_max_days: int
    source: str

class DeliveryProvider(Protocol):
    def list_points(self, city: str) -> list[PickupPoint]: ...
    def quote(self, point_id: str, weight_grams: int) -> Quote: ...
    def create_shipment(self, order_id: str, point_id: str) -> dict: ...

class IntegrationNotReady(RuntimeError):
    pass

class OzonLiveProvider:
    """Fail closed until real logistics API methods and auth are verified."""
    def list_points(self, city: str) -> list[PickupPoint]:
        raise IntegrationNotReady("Live Ozon pickup-point API not connected")
    def quote(self, point_id: str, weight_grams: int) -> Quote:
        raise IntegrationNotReady("Live Ozon tariff API not connected")
    def create_shipment(self, order_id: str, point_id: str) -> dict:
        raise IntegrationNotReady("Live Ozon shipment API not connected")

class DemoProvider:
    """Synthetic fixtures; NOT real addresses, prices, or delivery promises."""
    POINTS = (
        PickupPoint("demo-msk-1", "Москва", "Демонстрационная улица, 1"),
        PickupPoint("demo-msk-2", "Москва", "Тестовый проспект, 2"),
        PickupPoint("demo-spb-1", "Санкт-Петербург", "Примерная улица, 3"),
    )
    def list_points(self, city: str) -> list[PickupPoint]:
        if not isinstance(city, str) or len(city)>100:
            raise ValueError("Invalid city")
        term = city.strip().casefold()
        return [p for p in self.POINTS if term in p.city.casefold()]
    def quote(self, point_id: str, weight_grams: int) -> Quote:
        if not isinstance(weight_grams, int) or isinstance(weight_grams, bool) or not 1<=weight_grams<=30000:
            raise ValueError("Invalid weight_grams")
        if point_id not in {p.id for p in self.POINTS}:
            raise ValueError("Unknown pickup point")
        return Quote(point_id, Decimal("199.00"), 3, 6, "synthetic_demo")
    def create_shipment(self, order_id: str, point_id: str) -> dict:
        raise IntegrationNotReady("Shipment creation is disabled in demo mode")

def validate_order_reference(order_id: str) -> str:
    if not isinstance(order_id,str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}",order_id):
        raise ValueError("Invalid order reference")
    return order_id
