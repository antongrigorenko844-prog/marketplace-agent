"""
Конфигурация агента синхронизации карточек между Ozon, Wildberries,
Яндекс Маркетом и Avito.

Все секреты (ключи API) берутся ТОЛЬКО из переменных окружения / .env —
никогда не хранятся в коде. См. .env.example.
"""
import os
from dataclasses import dataclass, field
from typing import List

from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    try:
        return int(val) if val is not None else default
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on", "да")


@dataclass
class Config:
    # --- Ozon Seller API ---
    ozon_client_id: str = os.getenv("OZON_CLIENT_ID", "")
    ozon_api_key: str = os.getenv("OZON_API_KEY", "")
    ozon_api_base: str = os.getenv("OZON_API_BASE", "https://api-seller.ozon.ru")

    # ID склада продавца для остатков по схеме FBS на Ozon — узнаётся через
    # --ozon-warehouses, заполняется в GitHub Secrets. Без него push-stock
    # падает с AttributeError (баг, найден и исправлен 2026-09-18).
    ozon_warehouse_id: str = os.getenv("OZON_WAREHOUSE_ID", "")

    # --- Wildberries API (Personal access token, категории: Контент/Цены/Маркетплейс) ---
    wb_api_token: str = os.getenv("WB_API_TOKEN", "")
    wb_content_base: str = os.getenv("WB_CONTENT_BASE", "https://content-api.wildberries.ru")
    wb_prices_base: str = os.getenv("WB_PRICES_BASE", "https://discounts-prices-api.wildberries.ru")
    wb_marketplace_base: str = os.getenv("WB_MARKETPLACE_BASE", "https://marketplace-api.wildberries.ru")
    # Statistics API — отдельный хост, полная история заказов/продаж (в
    # отличие от marketplace_api, который отдаёт только НЕподтверждённые
    # новые сборочные задания). Нужен для общего учёта остатков.
    wb_statistics_base: str = os.getenv("WB_STATISTICS_BASE", "https://statistics-api.wildberries.ru")
    # ID склада продавца для остатков по схеме FBS — узнаётся через API складов,
    # заполняется после первого запуска (см. README, раздел про WB).
    wb_warehouse_id: str = os.getenv("WB_WAREHOUSE_ID", "")

    # ---
