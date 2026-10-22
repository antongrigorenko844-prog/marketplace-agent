"""
Клиент Партнёрского API Яндекс Маркета (модель FBS: вы отгружаете со своего
склада). Нужны два секрета GitHub: YANDEX_MARKET_API_KEY (токен из
кабинета: Настройки -> API-ключи; права: управление товарами и заказами —
"offers-and-cards-management" и "inventory-and-order-processing", либо
"all-methods") и YANDEX_MARKET_CAMPAIGN_ID (номер магазина/кампании).

Что используется (по документации yandex.ru/dev/market/partner-api, 2026-10):
  * GET  /v2/campaigns/{campaignId}                      — проверка доступа
  * PUT  /v2/campaigns/{campaignId}/offers/stocks        — остатки, до 2000 SKU за запрос
  * POST /v2/campaigns/{campaignId}/offer-prices/updates — цены, до 2000 товаров за запрос
  * GET  /v2/campaigns/{campaignId}/orders               — заказы (учёт продаж)
    ВНИМАНИЕ: этот метод помечен Яндексом как устаревший — нестабильно с
    18.01.2027, отключение 12.04.2027; замена POST /v1/businesses/{businessId}/orders
    (нужно будет перевести, см. get_orders).

SKU на Маркете = ваш артикул (offer_id). Если у какого-то товара SKU на
Маркете другой или товар там не заведён — Маркет вернёт ошибку по нему:
такие товары печатаются списком, остальные при этом обновляются.

АВТОРИЗАЦИЯ: токен из кабинета (API-ключ) передаётся заголовком Api-Key;
если токен начинается с "y0_" (OAuth-токен) — заголовком
Authorization: Bearer. Это нужно проверить первым запуском yandex-market-test.
"""
import datetime
import logging
import os
import time
from typing import Dict, Iterable, List, Optional, Tuple

import requests

logger = logging.getLogger("marketplace-agent.yandex_market")

BASE_URL = "https://api.partner.market.yandex.ru"
BATCH = 100  # осторожнее максимума (2000): при ошибке в партии проще понять, где она


class YandexMarketError(RuntimeError):
    pass


def _creds() -> Tuple[str, str]:
    token = os.environ.get("YANDEX_MARKET_API_KEY", "").strip()
    campaign = os.environ.get("YANDEX_MARKET_CAMPAIGN_ID", "").strip()
    if not token or not campaign:
        raise YandexMarketError(
            "Не заданы секреты YANDEX_MARKET_API_KEY / YANDEX_MARKET_CAMPAIGN_ID "
            "(GitHub -> Settings -> Secrets and variables -> Actions)."
        )
    if not campaign.isdigit():
        raise YandexMarketError("YANDEX_MARKET_CAMPAIGN_ID должен быть числом (номер магазина/кампании).")
    return token, campaign


def is_configured() -> bool:
    try:
        _creds()
        return True
    except YandexMarketError:
        return False


def _headers(token: str) -> Dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if token.startswith("y0_"):
        h["Authorization"] = f"Bearer {token}"
    else:
        h["Api-Key"] = token
    return h


def _request(method: str, path: str, *, params=None, json_body=None, timeout: int = 60) -> Tuple[int, dict]:
    """Возвращает (http_status, json). Повторяет при 420/429/5xx до 3 раз. Токен в лог не попадает."""
    token, _ = _creds()
    url = BASE_URL + path
    last_exc: Optional[Exception] = None
    for attempt in range(3):
        try:
            r = requests.request(method, url, headers=_headers(token), params=params, json=json_body, timeout=timeout)
        except requests.RequestException as exc:
            last_exc = exc
            time.sleep(2 * (attempt + 1))
            continue
        if r.status_code in (420, 429) or r.status_code >= 500:
            wait = int(r.headers.get("Retry-After", 0) or 0) or 2 * (attempt + 1)
            time.sleep(min(wait, 30))
            last_exc = YandexMarketError(f"HTTP {r.status_code}")
            continue
        try:
            body = r.json()
        except ValueError:
            body = {"raw": r.text[:300]}
        return r.status_code, body
    raise YandexMarketError(f"{method} {path}: не удалось выполнить запрос ({last_exc})")


def _errors_text(body: dict) -> str:
    errs = body.get("errors") if isinstance(body, dict) else None
    if errs:
        return "; ".join(f"{e.get('code', '?')}: {e.get('message', '')}" for e in errs if isinstance(e, dict))
    return str(body)[:300]


def list_campaigns() -> Tuple[bool, str]:
    """
    GET /v2/campaigns — список магазинов (кампаний) токена. Нужен, чтобы узнать
    номер кампании для YANDEX_MARKET_CAMPAIGN_ID, если он неизвестен (в адресе
    кабинета после /business/ стоит номер БИЗНЕСА, а не магазина).
    """
    token = os.environ.get("YANDEX_MARKET_API_KEY", "").strip()
    if not token:
        return False, "Не задан секрет YANDEX_MARKET_API_KEY."
    try:
        r = requests.get(BASE_URL + "/v2/campaigns", headers=_headers(token), timeout=60)
        body = r.json()
    except (requests.RequestException, ValueError) as exc:
        return False, f"не удалось получить список магазинов ({exc})"
    if r.status_code != 200:
        return False, f"HTTP {r.status_code}: {_errors_text(body)}"
    rows = []
    for c in body.get("campaigns", []) or []:
        biz = (c.get("business") or {}).get("id", "?")
        rows.append(f"  номер магазина (кампании) = {c.get('id')}, сайт/название: {c.get('domain', '?')}, схема: {c.get('placementType', '?')}, бизнес: {biz}")
    if not rows:
        return True, "токен принят, но магазинов в списке нет"
    return True, "токен принят. Ваши магазины:\n" + "\n".join(rows)


def test_connection() -> Tuple[bool, str]:
    """GET /v2/campaigns/{id} — есть ли доступ к магазину. Возвращает (ok, сообщение)."""
    try:
        _, campaign = _creds()
        status, body = _request("GET", f"/v2/campaigns/{campaign}")
    except YandexMarketError as exc:
        return False, str(exc)
    if status == 200:
        c = body.get("campaign", {}) if isinstance(body, dict) else {}
        dom = (c.get("domain") or c.get("clientId") or "").__str__()
        return True, f"доступ есть (магазин {campaign}{', ' + dom if dom else ''}, схема: {c.get('placementType', '?')})"
    if status in (401, 403):
        return False, f"HTTP {status}: токен не принят или нет прав ({_errors_text(body)})"
    return False, f"HTTP {status}: {_errors_text(body)}"


def _chunks(seq: List, n: int) -> Iterable[List]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _send_isolating(label: str, send, entries: List[Tuple[str, dict]]) -> Tuple[int, Dict[str, str]]:
    """
    entries: [(offer_id, элемент запроса)]. send(список_элементов) -> (status, body).
    Партия целиком; при 400 — повторяем по одному, чтобы найти и показать, какой
    именно товар не принят (обычно: нет такого SKU на Маркете).
    Возвращает (принято, {offer_id: причина отказа}).
    """
    ok = 0
    failed: Dict[str, str] = {}
    for chunk in _chunks(entries, BATCH):
        status, body = send([e for _, e in chunk])
        if status == 200:
            ok += len(chunk)
            continue
        if status in (401, 403):
            raise YandexMarketError(f"{label}: HTTP {status} — токен не принят или нет прав ({_errors_text(body)})")
        for offer_id, elem in chunk:
            st, b = send([elem])
            if st == 200:
                ok += 1
            elif st in (401, 403):
                raise YandexMarketError(f"{label}: HTTP {st} — токен не принят или нет прав ({_errors_text(b)})")
            else:
                failed[offer_id] = f"HTTP {st}: {_errors_text(b)}"
    return ok, failed


def update_stocks(stocks: Dict[str, int]) -> Tuple[int, Dict[str, str]]:
    """stocks: {offer_id: остаток}. PUT /v2/campaigns/{id}/offers/stocks."""
    _, campaign = _creds()
    now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entries = [
        (offer, {"sku": offer, "items": [{"count": max(0, int(cnt)), "updatedAt": now}]}) for offer, cnt in stocks.items()
    ]

    def send(elems):
        return _request("PUT", f"/v2/campaigns/{campaign}/offers/stocks", json_body={"skus": elems})

    return _send_isolating("остатки", send, entries)


def update_prices(prices: Dict[str, Tuple[int, Optional[int]]]) -> Tuple[int, Dict[str, str]]:
    """
    prices: {offer_id: (цена, цена_до_скидки_или_None)}.
    POST /v2/campaigns/{id}/offer-prices/updates. discountBase передаётся только
    если скидка укладывается в допустимые Маркетом 5-99%.
    """
    _, campaign = _creds()
    entries = []
    for offer, (price, old) in prices.items():
        p: Dict[str, object] = {"value": int(price), "currencyId": "RUR"}
        if old and old > price and 5 <= (old - price) * 100 / old <= 99:
            p["discountBase"] = int(old)
        entries.append((offer, {"offerId": offer, "price": p}))

    def send(elems):
        return _request("POST", f"/v2/campaigns/{campaign}/offer-prices/updates", json_body={"offers": elems})

    return _send_isolating("цены", send, entries)


def get_orders_since(days_back: int = 29) -> List[dict]:
    """
    Заказы кампании за период (не больше 30 дней — ограничение метода).
    GET /v2/campaigns/{id}/orders с постраничным pageToken (limit=50).
    Каждый заказ: id, status, items[{offerId, count, ...}].
    """
    _, campaign = _creds()
    since = (datetime.datetime.now() - datetime.timedelta(days=days_back)).strftime("%d-%m-%Y")
    to = (datetime.datetime.now() + datetime.timedelta(days=1)).strftime("%d-%m-%Y")
    orders: List[dict] = []
    token: Optional[str] = None
    for _ in range(200):
        params = {"fromDate": since, "toDate": to, "limit": 50}
        if token:
            params["pageToken"] = token
        status, body = _request("GET", f"/v2/campaigns/{campaign}/orders", params=params)
        if status != 200:
            raise YandexMarketError(f"заказы: HTTP {status}: {_errors_text(body)}")
        orders.extend(body.get("orders", []) or [])
        token = (body.get("paging") or {}).get("nextPageToken")
        if not token:
            break
    return orders


def get_business_id() -> int:
    """businessId (номер кабинета) по номеру магазина: GET /v2/campaigns/{campaignId} -> campaign.business.id."""
    _, campaign = _creds()
    status, body = _request("GET", f"/v2/campaigns/{campaign}")
    if status != 200:
        raise YandexMarketError(f"не удалось получить businessId: HTTP {status}: {_errors_text(body)}")
    biz = ((body.get("campaign") or {}).get("business") or {}).get("id")
    if not biz:
        raise YandexMarketError("в ответе Маркета нет номера бизнеса (campaign.business.id)")
    return int(biz)


def get_all_offers(archived: bool = False) -> List[dict]:
    """
    Все товары каталога кабинета: POST /v2/businesses/{businessId}/offer-mappings
    (limit=100, постранично по pageToken). Возвращает список
    {sku, name, archived, market_sku, category}.
    """
    business_id = get_business_id()
    out: List[dict] = []
    token: Optional[str] = None
    for _ in range(500):
        params = {"limit": 100}
        if token:
            params["pageToken"] = token
        status, body = _request(
            "POST", f"/v2/businesses/{business_id}/offer-mappings", params=params, json_body={"archived": archived}
        )
        if status != 200:
            raise YandexMarketError(f"список товаров: HTTP {status}: {_errors_text(body)}")
        result = body.get("result") or {}
        for om in result.get("offerMappings", []) or []:
            offer = om.get("offer") or {}
            mapping = om.get("mapping") or {}
            out.append(
                {
                    "sku": str(offer.get("offerId", "")).strip(),
                    "name": offer.get("name", ""),
                    "archived": bool(offer.get("archived", False)),
                    "market_sku": mapping.get("marketSku", ""),
                    "category": mapping.get("marketCategoryName", ""),
                }
            )
        token = (result.get("paging") or {}).get("nextPageToken")
        if not token:
            break
    return out


NAME_MAX = 256  # предел Маркета на название товара


def update_names(names: Dict[str, str]) -> Tuple[int, Dict[str, str]]:
    """
    names: {offer_id: новое название}. POST /v2/businesses/{businessId}/offer-mappings/update —
    меняется только название (остальные поля карточки не затрагиваются).
    """
    business_id = get_business_id()
    entries = [(offer, {"offer": {"offerId": offer, "name": name[:NAME_MAX]}}) for offer, name in names.items()]

    def send(elems):
        return _request("POST", f"/v2/businesses/{business_id}/offer-mappings/update", json_body={"offerMappings": elems})

    return _send_isolating("названия", send, entries)
