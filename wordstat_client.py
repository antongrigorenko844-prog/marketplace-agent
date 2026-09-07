"""
Клиент для Wordstat API (Yandex Cloud AI Studio Search API).

Официальная документация:
https://aistudio.yandex.ru/docs/ru/search-api/operations/wordstat-gettop.html

Это ОФИЦИАЛЬНЫЙ бесплатный API от Яндекса (не парсинг/скрапинг сайта
wordstat.yandex.ru — это было бы против правил площадки и ненадёжно).
Аккаунт в Яндекс Директ с историей рекламных расходов НЕ нужен.

Авторизация — два значения, оба берутся в консоли Yandex Cloud при создании
сервисного аккаунта (см. README, раздел 'Wordstat'):
  - WORDSTAT_API_KEY    — сам API-ключ, заголовок "Authorization: Api-key <ключ>"
  - WORDSTAT_FOLDER_ID  — ID каталога (папки) в Yandex Cloud, где создан
                          сервисный аккаунт (это НЕ то же самое, что ключ)

ВАЖНО: путь метода версионируется (сейчас /v2/...) — комментарий "# ENDPOINT"
отмечает, что проверять первым при ошибке 404.
"""
import logging
import re
import time
from typing import Dict, List, Optional

import requests

from config import config

logger = logging.getLogger("marketplace-agent.wordstat")

# Пауза между КАЖДЫМ запросом (успешным или нет) — держит нас заведомо ниже
# лимита API (~10 запросов/сек) и на практике почти полностью убирает
# капли 429 "превышен лимит запросов" (были замечены при пакетном сборе
# по 100+ фразам подряд без пауз — см. README/чат).
_MIN_INTERVAL_SECONDS = 0.25

# "225" — код региона "Россия" целиком в справочнике регионов Яндекса.
# Официальная документация помечает "regions" как ОБЯЗАТЕЛЬНЫЙ параметр —
# раньше он не отправлялся при отсутствии явного значения, из-за чего
# часть запросов, вероятно, возвращала пустой результат вместо реальных
# данных по всей России.
_DEFAULT_REGION_IDS = ["225"]


class WordstatApiError(RuntimeError):
    pass


def _sanitize_phrase(phrase: str) -> str:
    """
    Wordstat API возвращает 400 "Invalid query" на фразы со спецсимволами
    вроде + ! ( ) , " — обнаружено на практике при пакетном сборе (см. лог
    в чате: все фразы с "+"/"!"/скобками падали с этой ошибкой, фразы без
    них — нет). Убираем такие символы перед отправкой, оставляя только
    буквы/цифры/пробелы/дефис — сам текст в data/seo_keywords.xlsx при этом
    не трогаем, чистим только то, что реально уходит в API.
    """
    cleaned = re.sub(r'[+!()"\'«»,;:]+', " ", phrase)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def _headers() -> Dict[str, str]:
    if not config.wordstat_api_key:
        raise WordstatApiError(
            "WORDSTAT_API_KEY не задан в .env — без этого нельзя обращаться "
            "к Wordstat API. См. README, раздел 'Wordstat'."
        )
    return {
        "Authorization": f"Api-key {config.wordstat_api_key}",
        "Content-Type": "application/json",
    }


def _post(path: str, payload: dict, retries: int = 3) -> dict:
    if not config.wordstat_folder_id:
        raise WordstatApiError(
            "WORDSTAT_FOLDER_ID не задан в .env — это ID каталога сервисного "
            "аккаунта в Yandex Cloud, без него Wordstat API не отвечает. "
            "См. README, раздел 'Wordstat'."
        )
    url = f"{config.wordstat_api_base}{path}"
    body = {"folderId": config.wordstat_folder_id, **payload}
    last_error: Optional[Exception] = None
    for attempt in range(1, retries + 1):
        # Пауза ПЕРЕД каждой попыткой (включая первую) — держит нас ниже
        # лимита API и не даёт повторам после 429 снова упереться в лимит.
        time.sleep(_MIN_INTERVAL_SECONDS)
        try:
            resp = requests.post(
                url, json=body, headers=_headers(), timeout=config.request_timeout_seconds
            )
        except requests.RequestException as exc:
            last_error = exc
            logger.warning("Wordstat %s: сетевая ошибка (попытка %d/%d): %s", path, attempt, retries, exc)
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 429:
            logger.warning("Wordstat %s: превышен лимит запросов, жду и повторяю", path)
            time.sleep(3 * attempt)
            continue

        if not resp.ok:
            raise WordstatApiError(f"Wordstat {path} вернул {resp.status_code}: {resp.text[:500]}")

        if not resp.content:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {}

    raise WordstatApiError(f"Wordstat {path}: не удалось получить ответ после {retries} попыток: {last_error}")


def get_top_requests(
    phrase: str,
    num_phrases: int = 50,
    region_ids: Optional[List[str]] = None,
    device: str = "DEVICE_ALL",
) -> dict:
    """
    Частотность фразы + связанные/похожие фразы (associations) — то, что
    реально ищут вместе с этой фразой, без ручного придумывания вариантов.

    region_ids — список ID регионов Яндекса (например ["225"] — вся Россия);
    если не задано, Wordstat API берёт всю Россию по умолчанию.

    Формат ответа (подтверждено официальной документацией):
    {"totalCount": "...", "results": [{"phrase": "...", "count": "..."}],
     "associations": [{"phrase": "...", "count": "..."}]}
    """
    # ENDPOINT: POST /v2/wordstat/topRequests
    clean_phrase = _sanitize_phrase(phrase)
    if not clean_phrase:
        # После чистки спецсимволов ничего не осталось — отправлять нечего.
        return {}
    payload = {
        "phrase": clean_phrase,
        "numPhrases": num_phrases,
        "devices": device,
        "regions": region_ids or _DEFAULT_REGION_IDS,
    }
    return _post("/v2/wordstat/topRequests", payload)


def collect_semantics(
    seed_phrases: List[str],
    expand_associations: bool = True,
    num_phrases: int = 50,
    max_associations_per_seed: int = 5,
) -> Dict[str, int]:
    """
    Берёт список стартовых фраз (например разные формулировки для одного
    артикула), для каждой запрашивает topRequests, и если
    expand_associations — дополнительно проходит ОДИН уровень вглубь по
    найденным associations (похожим фразам), чтобы расширить семантику
    автоматически, а не только теми фразами, что вы сами придумали.

    Возвращает {фраза: частотность} — если фраза встретилась несколько раз
    (как результат и как ассоциация), берётся максимальное значение.
    """
    collected: Dict[str, int] = {}
    seen = set()

    def _absorb(data: dict) -> None:
        for bucket in ("results", "associations"):
            for item in data.get(bucket, []) or []:
                phrase = str(item.get("phrase") or "").strip()
                if not phrase:
                    continue
                try:
                    count = int(item.get("count") or 0)
                except (TypeError, ValueError):
                    count = 0
                if phrase not in collected or count > collected[phrase]:
                    collected[phrase] = count

    seeds = [p.strip() for p in seed_phrases if p and p.strip()]
    for phrase in seeds:
        key = phrase.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            data = get_top_requests(phrase, num_phrases=num_phrases)
        except WordstatApiError as exc:
            logger.error("Wordstat: ошибка по фразе '%s': %s", phrase, exc)
            continue
        _absorb(data)

        if expand_associations:
            for assoc in (data.get("associations") or [])[:max_associations_per_seed]:
                assoc_phrase = str(assoc.get("phrase") or "").strip()
                assoc_key = assoc_phrase.casefold()
                if assoc_phrase and assoc_key not in seen:
                    seen.add(assoc_key)
                    try:
                        data2 = get_top_requests(assoc_phrase, num_phrases=num_phrases)
                        _absorb(data2)
                    except WordstatApiError as exc:
                        logger.warning(
                            "Wordstat: ошибка по associations-фразе '%s': %s", assoc_phrase, exc
                        )

    return collected


def test_connection() -> bool:
    """Лёгкая проверка, что ключ/folderId верные и Wordstat API отвечает."""
    try:
        get_top_requests("тест", num_phrases=1)
        return True
    except WordstatApiError as exc:
        logger.error("Wordstat: проверка соединения не удалась: %s", exc)
        return False
