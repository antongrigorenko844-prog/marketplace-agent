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

    # --- Wildberries API (Personal access token, категории: Контент/Цены/Маркетплейс) ---
    wb_api_token: str = os.getenv("WB_API_TOKEN", "")
    wb_content_base: str = os.getenv("WB_CONTENT_BASE", "https://content-api.wildberries.ru")
    wb_prices_base: str = os.getenv("WB_PRICES_BASE", "https://discounts-prices-api.wildberries.ru")
    wb_marketplace_base: str = os.getenv("WB_MARKETPLACE_BASE", "https://marketplace-api.wildberries.ru")
    # ID склада продавца для остатков по схеме FBS — узнаётся через API складов,
    # заполняется после первого запуска (см. README, раздел про WB).
    wb_warehouse_id: str = os.getenv("WB_WAREHOUSE_ID", "")

    # --- Яндекс Маркет (подключим отдельным шагом) ---
    yandex_api_token: str = os.getenv("YANDEX_API_TOKEN", "")
    yandex_business_id: str = os.getenv("YANDEX_BUSINESS_ID", "")
    yandex_campaign_id: str = os.getenv("YANDEX_CAMPAIGN_ID", "")
    yandex_api_base: str = os.getenv("YANDEX_API_BASE", "https://api.partner.market.yandex.ru")

    # --- Telegram (тот же бот, что и в агенте недвижимости, для уведомлений) ---
    telegram_bot_token: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
    telegram_chat_id: str = os.getenv("TELEGRAM_CHAT_ID", "")

    # --- Общий каталог ---
    catalog_path: str = os.getenv(
        "CATALOG_PATH", os.path.join(os.path.dirname(__file__), "data", "catalog.csv")
    )
    db_path: str = os.getenv(
        "DB_PATH", os.path.join(os.path.dirname(__file__), "data", "orders_seen.db")
    )

    # --- Avito (автозагрузка через фид, без API-ключа) ---
    avito_feed_path: str = os.getenv(
        "AVITO_FEED_PATH", os.path.join(os.path.dirname(__file__), "data", "avito_feed.xml")
    )
    # Название компании/профиля продавца — должно совпадать с тем, что в кабинете Avito.
    avito_seller_name: str = os.getenv("AVITO_SELLER_NAME", "")

    # --- Прочее ---
    request_timeout_seconds: int = _get_int("REQUEST_TIMEOUT_SECONDS", 30)
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


config = Config()
