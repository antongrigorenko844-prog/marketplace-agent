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
    """
    Редактирование существующих карточек. До 3000 позиций (nmID) за запрос.

    ВАЖНО (проверено в живой документации dev.wildberries.ru): карточка
    перезаписывается целиком — присылайте и то, что не меняете (nmID,
    vendorCode, brand, dimensions, characteristics, sizes), иначе рискуете
    затереть. Этот метод НЕ умеет менять фото/видео/теги и цены — для этого
    отдельные методы (update_media, update_prices).
    """
    # ENDPOINT: POST /content/v2/cards/update
    return _request("POST", config.wb_content_base, "/content/v2/cards/update", cards)


def generate_barcodes(count: int) -> List[str]:
    """
    Генерирует уникальные штрихкоды для новых размеров/товаров — нужны при
    СОЗДАНИИ новой карточки (у существующих товаров штрихкод уже есть).
    Проверено в живой документации dev.wildberries.ru: максимум 5000 за раз.
    """
    # ENDPOINT: POST /content/v2/barcodes
    data = _request("POST", config.wb_content_base, "/content/v2/barcodes", {"count": count})
    return data.get("data", [])


def update_media(nm_id: int, urls: List[str]) -> dict:
    """
    Полная замена фото/видео карточки по ссылкам. Проверено в живой
    документации dev.wildberries.ru 02.09.2026: ссылки должны вести напрямую
    на файл (не на превью/логин), без авторизации; до 30 фото + 1 видео на
    карточку. Это ПОЛНАЯ замена: новые ссылки заменяют старые целиком —
    если хотите сохранить старые фото и добавить новые, включите старые
    ссылки в этот же список.
    """
    # ENDPOINT: POST /content/v3/media/save
    return _request(
        "POST", config.wb_content_base, "/content/v3/media/save", {"nmId": nm_id, "data": urls}
    )


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
    """
    items: [{"sku": "<ШТРИХКОД>", "amount": 5}, ...]

    ВАЖНО (исправлено при добавлении общего учёта остатков, см.
    stock_sync.py): несмотря на название поля "sku", WB ждёт здесь
    ШТРИХКОД товара (то, что в карточке хранится в sizes[].skus), а НЕ
    артикул продавца (vendorCode/offer_id) — подтверждено официальным
    описанием метода ("Массив баркодов товаров и их остатков"). Чтобы
    обновить остаток по своему артикулу, сначала переведите его в штрихкод
    через get_offer_barcode_map().
    """
    # ENDPOINT: PUT /api/v3/stocks/{warehouseId}
    return _request(
        "PUT", config.wb_marketplace_base, f"/api/v3/stocks/{warehouse_id}", {"stocks": items}
    )


def get_offer_barcode_map() -> Dict[str, str]:
    """
    Артикул продавца (vendorCode = offer_id из вашего каталога) -> штрихкод
    первого размера/варианта его карточки WB. Нужно только для
    update_stocks() (см. комментарий там). Берёт первую попавшуюся
    карточку/размер/штрихкод — для товаров без размерной сетки (наш
    случай: запчасти) там ровно один вариант и один штрихкод.
    """
    out: Dict[str, str] = {}
    for card in list_cards():
        vendor_code = card.get("vendorCode")
        if not vendor_code:
            continue
        for size in card.get("sizes") or []:
            skus = size.get("skus") or []
            if skus:
                out[str(vendor_code).strip()] = str(skus[0]).strip()
                break
    return out


def get_new_orders() -> List[dict]:
    """Новые заказы FBS, ещё не подтверждённые — для автоматического списания остатков."""
    # ENDPOINT: GET /api/v3/orders/new
    return _request("GET", config.wb_marketplace_base, "/api/v3/orders/new").get("orders", [])


def get_orders_since(date_from_iso: str) -> List[dict]:
    """
    ПОЛНАЯ история заказов (Statistics API, отдельный хост) — в отличие от
    get_new_orders() отдаёт ВСЕ заказы за период, включая уже обработанные
    и отменённые, а не только текущие неподтверждённые. Именно этот метод
    нужен для учёта продаж/списания остатков (см. stock_sync.py).

    date_from_iso — RFC3339, например "2026-08-01" или
    "2026-08-01T00:00:00" (часовой пояс — московский). Возвращает список
    строк заказов; ключевые поля (подтверждено официальным описанием
    метода): supplierArticle (= ваш offer_id, сопоставление автоматическое,
    без ручного подтверждения), nmId, isCancel, date, srid (уникальный ID
    заказа — используйте его для дедупликации).

    ВАЖНО: метод отдаёт ОДНУ СТРОКУ НА КАЖДУЮ ЕДИНИЦУ ТОВАРА (не
    агрегирует по количеству) — поэтому здесь количество не читается из
    отдельного поля "quantity" (в этом методе такого поля обычно нет),
    просто одна строка = 1 штука; stock_sync.py считает строки.
    """
    # ENDPOINT: GET /api/v1/supplier/orders — подтверждено официальным описанием
    data = _request(
        "GET", config.wb_statistics_base, f"/api/v1/supplier/orders?dateFrom={date_from_iso}"
    )
    if isinstance(data, list):
        return data
    return data.get("data", data.get("orders", []))


def test_connection() -> bool:
    """Лёгкая проверка, что токен верный и WB отвечает."""
    try:
        get_parent_categories()
        return True
    except WbApiError as exc:
        logger.error("WB: проверка соединения не удалась: %s", exc)
        return False
