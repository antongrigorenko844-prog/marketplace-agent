"""
Клиент для Ozon Seller API.

Официальная документация: https://docs.ozon.ru/api/seller
Авторизация — два заголовка на каждый запрос: Client-Id и Api-Key
(создаются в кабинете: Настройки -> API-ключи, тип "Для личного использования",
роль "Администратор").

ВАЖНО: пути эндпоинтов у Ozon версионируются (v1/v2/v3/v4) и иногда переезжают
на новую версию. Ниже расставлены комментарии "# ENDPOINT" у каждого адреса —
если Ozon вернёт 404/NOT_FOUND, в первую очередь проверяйте актуальный путь
именно в этом месте на docs.ozon.ru/api/seller, а не что-то другое.
"""
import logging
import time
from typing import Dict, List, Optional

import requests

from config import config

logger = logging.getLogger("marketplace-agent.ozon")


class OzonApiError(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    if not config.ozon_client_id or not config.ozon_api_key:
        raise OzonApiError(
            "OZON_CLIENT_ID / OZON_API_KEY не заданы в .env — без этого нельзя "
            "обращаться к Ozon Seller API. См. README, раздел 'Ozon'."
        )
    return {
        "Client-Id": config.ozon_client_id,
        "Api-Key": config.ozon_api_key,
        "Content-Type": "application/json",
    }


def _post(path: str, payload: dict, retries: int = 3) -> dict:
    url = f"{config.ozon_api_base}{path}"
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                url, json=payload, headers=_headers(), timeout=config.request_timeout_seconds
            )
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Ozon %s: сетевая ошибка (попытка %d/%d): %s", path, attempt, retries, exc)
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 429:
            logger.warning("Ozon %s: превышен лимit запросов, жду и повторяю", path)
            time.sleep(3 * attempt)
            continue

        if not resp.ok:
            raise OzonApiError(f"Ozon {path} вернул {resp.status_code}: {resp.text[:500]}")

        try:
            return resp.json()
        except ValueError:
            return {}

    raise OzonApiError(f"Ozon {path}: не удалось получить ответ после {retries} попыток: {last_error}")


def list_products(limit: int = 100) -> List[dict]:
    """
    Полный список товаров продавца (offer_id, product_id) постранично,
    через курсор last_id.
    """
    products: List[dict] = []
    last_id = ""
    while True:
        # ENDPOINT: POST /v3/product/list
        data = _post(
            "/v3/product/list",
            {"filter": {"visibility": "ALL"}, "last_id": last_id, "limit": limit},
        )
        result = data.get("result", {})
        items = result.get("items", [])
        products.extend(items)
        last_id = result.get("last_id", "")
        if not items or not last_id:
            break
    logger.info("Ozon: получено товаров через product/list: %d", len(products))
    return products


def get_prices_and_stocks(offer_ids: List[str]) -> List[dict]:
    """
    Подробная информация по ценам товаров, пачками по 1000 offer_id.
    Проверено напрямую в живой документации docs.ozon.ru 02.09.2026 —
    актуальная версия метода v5 (v1/v4 отключены, поэтому раньше был 404).
    """
    out: List[dict] = []
    chunk = 1000
    for i in range(0, len(offer_ids), chunk):
        batch = offer_ids[i : i + chunk]
        # ENDPOINT: POST /v5/product/info/prices
        data = _post("/v5/product/info/prices", {"filter": {"offer_id": batch}, "limit": len(batch)})
        out.extend(data.get("result", {}).get("items", data.get("items", [])))
    return out


def get_product_names(offer_ids: Optional[List[str]] = None, limit: int = 1000) -> List[dict]:
    """
    Название товара, штрихкод, изображения и характеристики — постранично,
    через курсор last_id (как list_products).

    Проверено напрямую в живой документации docs.ozon.ru 02.09.2026:
    /v3/product/info/list НЕ содержит названия (только штрихкод, цену,
    категорию, комиссию, ошибки модерации). Название отдаёт либо
    /v1/product/info/description (по одному товару за раз — offer_id или
    product_id, без пакетной выборки), либо /v4/product/info/attributes
    (пакетно, с пагинацией last_id) — используем второй, он эффективнее
    для десятков/сотен товаров разом.
    """
    out: List[dict] = []
    last_id = ""
    filter_body: Dict[str, object] = {"visibility": "ALL"}
    if offer_ids:
        filter_body["offer_id"] = offer_ids
    while True:
        # ENDPOINT: POST /v4/product/info/attributes — подтверждено в живой документации 02.09.2026
        data = _post(
            "/v4/product/info/attributes",
            {"filter": filter_body, "last_id": last_id, "limit": limit, "sort_dir": "ASC"},
        )
        items = data.get("result", [])
        out.extend(items)
        last_id = data.get("last_id", "")
        if not items or not last_id:
            break
    logger.info("Ozon: получено названий товаров через product/info/attributes: %d", len(out))
    return out


def update_stocks(items: List[dict]) -> dict:
    """
    items: [{"offer_id": "...", "stock": 5}, ...] (product_id тоже подходит
    вместо offer_id). Обновление остатков — быстрый метод, отдельный от
    редактирования карточки. Максимум 100 товаров за один запрос.
    """
    # ENDPOINT: POST /v2/products/stocks — подтверждено в живой документации 02.09.2026
    return _post("/v2/products/stocks", {"stocks": items})


def update_prices(items: List[dict]) -> dict:
    """
    items: [{"offer_id": "...", "price": "1500", "old_price": "0",
             "currency_code": "RUB"}, ...]
    Обновление цен — тоже быстрый метод, отдельный от карточки.
    """
    # ENDPOINT: POST /v1/product/import/prices
    return _post("/v1/product/import/prices", {"prices": items})


def get_category_tree(language: str = "RU") -> List[dict]:
    # ENDPOINT: POST /v2/category/tree — дерево категорий Ozon
    data = _post("/v2/category/tree", {"language": language})
    return data.get("result", [])


def get_category_attributes(description_category_id: int, type_id: int, language: str = "RU") -> List[dict]:
    """Обязательные и необязательные характеристики для конкретной категории/типа товара."""
    # ENDPOINT: POST /v2/category/attribute
    data = _post(
        "/v2/category/attribute",
        {
            "description_category_id": description_category_id,
            "type_id": type_id,
            "language": language,
        },
    )
    return data.get("result", [])


def import_products(items: List[dict]) -> dict:
    """
    Создание/обновление карточек товара. Структура каждого элемента items
    зависит от категории (обязательные attributes) — см. get_category_attributes.
    Метод асинхронный — возвращает task_id, статус проверяется отдельным вызовом.
    """
    # ENDPOINT: POST /v3/product/import
    return _post("/v3/product/import", {"items": items})


def get_import_status(task_id: int) -> dict:
    # ENDPOINT: POST /v3/product/import/info
    return _post("/v3/product/import/info", {"task_id": task_id})


def get_fbs_orders_since(since_iso: str, to_iso: str) -> List[dict]:
    """
    Новые заказы FBS за период — используется для автоматического списания
    остатков при продаже (см. sync_orders.py).
    """
    # ENDPOINT: POST /v3/posting/fbs/list
    data = _post(
        "/v3/posting/fbs/list",
        {
            "dir": "ASC",
            "filter": {"since": since_iso, "to": to_iso},
            "limit": 1000,
            "offset": 0,
            "with": {"analytics_data": False, "financial_data": False},
        },
    )
    return data.get("result", {}).get("postings", [])


def test_connection() -> bool:
    """Лёгкая проверка, что Client-Id/Api-Key верные и Ozon отвечает."""
    try:
        _post("/v3/product/list", {"filter": {}, "last_id": "", "limit": 1})
        return True
    except OzonApiError as exc:
        logger.error("Ozon: проверка соединения не удалась: %s", exc)
        return False
