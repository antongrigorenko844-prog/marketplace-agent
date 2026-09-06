"""
Клиент для Avito API (OAuth client_credentials) — заказы Авито Доставки и
управление остатками объявлений.

Официальная документация: developers.avito.ru/api-catalog (личный кабинет ->
Настройки -> Avito API -> Регистрация приложения, там же выдаются
client_id/client_secret).

ВАЖНО про доступ:
- Простая "Автозагрузка" (XML-фид) не требует этих ключей вообще — см.
  avito_feed.py. Этот модуль — для ВТОРОГО, отдельного вида доступа: полного
  Avito API с OAuth, нужного для (а) получения заказов Авито Доставки и
  (б) программного обновления остатков в обход фида. Для заказов Авито
  Доставки, по имеющимся данным, требуется тариф "Бизнес" на Авито — если
  его нет, get_orders() будет возвращать ошибку доступа, это нормально и
  означает, что автосписание по Авито недоступно (см. README).

ВАЖНО про пути эндпоинтов: у Avito API (как и у Ozon) пути версионируются.
Ниже расставлены комментарии "# ENDPOINT" — если Avito вернёт 404/NOT_FOUND
именно на get_orders(), в первую очередь проверяйте актуальный путь заказов
Авито Доставки в личном кабинете (Настройки -> Avito API -> документация) —
он не был доступен для проверки при написании этого кода из песочницы.
Эндпоинты авторизации и остатков (get_token/update_stocks/get_stock_info)
подтверждены по официальному описанию API и должны быть стабильны.
"""
import logging
import time
from typing import Dict, List, Optional

import requests

from config import config

logger = logging.getLogger("marketplace-agent.avito")

_TOKEN_TTL_SECONDS = 24 * 60 * 60  # токен живёт 24 часа
_token_cache: Dict[str, float] = {"token": "", "expires_at": 0.0}


class AvitoApiError(RuntimeError):
    pass


def _require_credentials() -> None:
    if not config.avito_client_id or not config.avito_client_secret:
        raise AvitoApiError(
            "AVITO_CLIENT_ID / AVITO_CLIENT_SECRET не заданы (GitHub Secrets) — без "
            "этого нельзя обращаться к полному Avito API (заказы/остатки). Простая "
            "автозагрузка через XML-фид (avito_feed.py) от этого не зависит."
        )


def _get_token(force_refresh: bool = False) -> str:
    """
    OAuth2 client_credentials: POST /token, form-urlencoded. Токен кэшируется
    в памяти процесса на время жизни (24 часа), чтобы не запрашивать его на
    каждый вызов.
    """
    _require_credentials()

    if not force_refresh and _token_cache["token"] and time.time() < _token_cache["expires_at"]:
        return _token_cache["token"]

    url = f"{config.avito_api_base}/token"  # ENDPOINT: POST /token
    try:
        resp = requests.post(
            url,
            data={
                "grant_type": "client_credentials",
                "client_id": config.avito_client_id,
                "client_secret": config.avito_client_secret,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=15,
        )
    except requests.RequestException as exc:
        raise AvitoApiError(f"Avito /token: сетевая ошибка: {exc}") from exc

    if not resp.ok:
        raise AvitoApiError(f"Avito /token вернул {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
    except ValueError as exc:
        raise AvitoApiError(f"Avito /token: не удалось разобрать ответ: {resp.text[:300]}") from exc

    token = data.get("access_token")
    if not token:
        raise AvitoApiError(f"Avito /token: в ответе нет access_token: {data}")

    _token_cache["token"] = token
    # Обновляем чуть раньше формального истечения (запас 5 минут).
    _token_cache["expires_at"] = time.time() + _TOKEN_TTL_SECONDS - 300
    return token


def _headers() -> Dict[str, str]:
    return {
        "Authorization": f"Bearer {_get_token()}",
        "Content-Type": "application/json",
    }


def _request(method: str, path: str, retries: int = 3, **kwargs) -> dict:
    url = f"{config.avito_api_base}{path}"
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        try:
            resp = requests.request(
                method, url, headers=_headers(), timeout=config.request_timeout_seconds, **kwargs
            )
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Avito %s %s: сетевая ошибка (попытка %d/%d): %s", method, path, attempt, retries, exc)
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 401 and attempt == 1:
            # Токен мог протухнуть раньше расчётного времени — обновляем один раз принудительно.
            logger.info("Avito %s: 401, обновляю токен и повторяю", path)
            _get_token(force_refresh=True)
            continue

        if resp.status_code == 429:
            logger.warning("Avito %s: превышен лимит запросов, жду и повторяю", path)
            time.sleep(3 * attempt)
            continue

        if not resp.ok:
            raise AvitoApiError(f"Avito {method} {path} вернул {resp.status_code}: {resp.text[:500]}")

        if not resp.text:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    raise AvitoApiError(f"Avito {method} {path}: не удалось получить ответ после {retries} попыток: {last_error}")


def get_orders(
    date_from: Optional[str] = None,
    statuses: Optional[List[str]] = None,
    page: int = 1,
    limit: int = 100,
) -> List[dict]:
    """
    Список заказов Авито Доставки. Требует тариф "Бизнес" на стороне Авито —
    если его нет, ожидаемо вернёт ошибку доступа (AvitoApiError), это не баг.

    ВНИМАНИЕ: точный путь эндпоинта НЕ подтверждён официальной документацией
    из песочницы (см. предупреждение в начале файла) — если получите 404,
    посмотрите актуальный путь в личном кабинете (Настройки -> Avito API ->
    документация, раздел "Заказы"/"Авито Доставка") и поправьте PATH ниже.

    statuses — например ["ready_to_ship", "in_transit", "delivered", "closed"].
    """
    PATH = "/order-management/1/orders"  # ENDPOINT: подтверждено (GET /order-management/1/orders)
    params: Dict[str, object] = {"page": page, "limit": limit}
    if date_from:
        params["dateFrom"] = date_from
    if statuses:
        params["statuses"] = ",".join(statuses)

    data = _request("GET", PATH, params=params)
    return data.get("orders") or data.get("result") or []


def get_stock_info(item_ids: List[int]) -> List[dict]:
    """
    Текущие остатки по списку ID объявлений Avito.
    """
    if not item_ids:
        return []
    PATH = "/stock-management/1/info"  # ENDPOINT: подтверждено описанием API
    data = _request("POST", PATH, json={"itemIds": item_ids})
    return data.get("items") or data.get("result") or []


def update_stocks(items: List[dict]) -> dict:
    """
    Обновление остатков по объявлениям Avito.
    items — список {"itemId": <int>, "quantity": <int>}.
    """
    if not items:
        return {}
    PATH = "/stock-management/1/stocks"  # ENDPOINT: подтверждено описанием API
    return _request("POST", PATH, json={"items": items})
