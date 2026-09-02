"""
Клиент для Wildberries API (Content + Prices + Marketplace).

Официальная документация: https://dev.wildberries.ru/
Авторизация — один токен (Personal access token, категории доступа
"Контент" + "Цены и скидки" + "Маркетплейс") в заголовке Authorization.

У WB три разных хоста под разные группы методов — это не ошибка,
так и задумано площадкой:
  - content-api.wildberries.ru       — карточки товаров
  - discounts-prices-api.wildberries.ru — цены и скидки
  - marketplace-api.wildberries.ru   — остатки на складе продавца (FBS) и заказы

ВАЖНО: как и у Ozon, пути методов версионируются — комментарии "# ENDPOINT"
отмечают, что проверять в первую очередь при ошибке 404.
"""
import logging
import time
from typing import Dict, List, Optional

import requests

from config import config

logger = logging.getLogger("marketplace-agent.wb")


class WbApiError(RuntimeError):
    pass


def _headers() -> Dict[str, str]:
    if not config.wb_api_token:
        raise WbApiError(
            "WB_API_TOKEN не задан в .env — без этого нельзя обращаться к API WB. "
            "См. README, раздел 'Wildberries'."
        )
    return {"Authorization": config.wb_api_token, "Content-Type": "application/json"}


def _request(method: str, base: str, path: str, json_body: Optional[dict] = None, retries: int = 3) -> dict:
    url = f"{base}{path}"
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(
                method, url, json=json_body, headers=_headers(), timeout=config.request_timeout_seconds
            )
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("WB %s: сетевая ошибка (попытка %d/%d): %s", path, attempt, retries, exc)
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 429:
            logger.warning("WB %s: превышен лимит запросов, жду и повторяю", path)
            time.sleep(3 * attempt)
            continue

        if not resp.ok:
            raise WbApiError(f"WB {path} вернул {resp.status_code}: {resp.text[:500]}")

        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    raise WbApiError(f"WB {path}: не удалось получить ответ после {retries} попыток: {last_error}")


# ---------- Контент (карточки) ----------

def list_cards(limit: int = 100) -> List[dict]:
    """Полный список карточек продавца постранично, через курсор (updatedAt + nmID)."""
    cards: List[dict] = []
    cursor = {"limit": limit}
    while True:
        # ENDPOINT: POST /content/v2/get/cards/list
        data = _request(
            "POST",
            config.wb_content_base,
            "/content/v2/get/cards/list",
            {"settings": {"cursor": cursor, "filter": {"withPhoto": -1}}},
        )
        page_cards = data.get("cards", [])
        cards.extend(page_cards)
        new_cursor = data.get("cursor", {})
        total = new_cursor.get("total", 0)
        if total < limit or not page_cards:
            break
        cursor = {"limit": limit, "updatedAt": new_cursor.get("updatedAt"), "nmID": new_cursor.get("nmID")}
    logger.info("WB: получено карточек: %d", len(cards))
    return cards


def get_parent_categories() -> List[dict]:
    # ENDPOINT: GET /content/v2/object/parent/all
    return _request("GET", config.wb_content_base, "/content/v2/object/parent/all").get("data", [])


def get_subjects(parent_id: Optional[int] = None, name: str = "") -> List[dict]:
    # ENDPOINT: GET /content/v2/object/all
    path = f"/content/v2/object/all?name={name}"
    if parent_id:
        path += f"&parentID={parent_id}"
    return _request("GET", config.wb_content_base, path).get("data", [])


def get_subject_characteristics(subject_id: int) -> List[dict]:
    # ENDPOINT: GET /content/v2/object/charcs/{subjectId}
    return _request(
        "GET", config.wb_content_base, f"/content/v2/object/charcs/{subject_id}"
    ).get("data", [])


def create_cards(subject_id: int, cards: List[dict]) -> dict:
    """
    Создание новых карточек. Максимум 100 карточек за запрос (см. документацию WB).
    Обработка асинхронная — WB не возвращает nmID сразу, статус нужно смотреть
    отдельным вызовом list_cards() или через историю статусов создания.
    """
    # ENDPOINT: POST /content/v2/cards/upload
    return _request(
        "POST",
        config.wb_content_base,
        "/content/v2/cards/upload",
        [{"subjectID": subject_id, "variants": cards}],
    )


def update_cards(cards: List[dict]) -> dict:
    """Редактирование существующих карточек. До 3000 позиций (nmID) за запрос."""
    # ENDPOINT: POST /content/v2/cards/update
    return _request("POST", config.wb_content_base, "/content/v2/cards/update", cards)


# ---------- Цены ----------

def get_prices(limit: int = 1000) -> List[dict]:
    # ENDPOINT: GET /api/v2/list/goods/filter
    return _request(
        "GET", config.wb_prices_base, f"/api/v2/list/goods/filter?limit={limit}&offset=0"
    ).get("data", {}).get("listGoods", [])


def update_prices(items: List[dict]) -> dict:
    """items: [{"nmID": 123456, "price": 1500, "discount": 0}, ...]"""
    # ENDPOINT: POST /api/v2/upload/task
    return _request("POST", config.wb_prices_base, "/api/v2/upload/task", {"data": items})


# ---------- Остатки и заказы (Marketplace API, схема FBS) ----------

def get_warehouses() -> List[dict]:
    # ENDPOINT: GET /api/v3/warehouses
    return _request("GET", config.wb_marketplace_base, "/api/v3/warehouses")


def update_stocks(warehouse_id: str, items: List[dict]) -> dict:
    """items: [{"sku": "артикул-продавца", "amount": 5}, ...]"""
    # ENDPOINT: PUT /api/v3/stocks/{warehouseId}
    return _request(
        "PUT", config.wb_marketplace_base, f"/api/v3/stocks/{warehouse_id}", {"stocks": items}
    )


def get_new_orders() -> List[dict]:
    """Новые заказы FBS, ещё не подтверждённые — для автоматического списания остатков."""
    # ENDPOINT: GET /api/v3/orders/new
    return _request("GET", config.wb_marketplace_base, "/api/v3/orders/new").get("orders", [])


def test_connection() -> bool:
    """Лёгкая проверка, что токен верный и WB отвечает."""
    try:
        get_parent_categories()
        return True
    except WbApiError as exc:
        logger.error("WB: проверка соединения не удалась: %s", exc)
        return False
