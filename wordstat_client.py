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

# Фильтр релевантности: expand_associations иногда "уезжает" в совершенно
# случайную тему — на практике собранный ранее файл содержал вперемешку с
# нормальными фразами вроде "сальник входного вала кпп ваз" ещё и "usd цб",
# "доллар в рублях", "остаться в живых сериал", "часть речи это" (см. чат) —
# это эффект расширения "похожих фраз на похожие фразы": если хоть одна
# случайно попавшаяся ассоциация оказалась двусмысленным/созвучным словом,
# её собственные ассоциации уводят в произвольную популярную тему, никак не
# связанную с запчастями. Явный список автомобильной/трансмиссионной лексики
# — самый надёжный способ отсечь такой шум ещё до записи в файл.
#
# ВАЖНО (доп. фильтр по марке, добавлен после того, как первая версия
# словаря пропустила фразы вроде "сальник входного вала кпп ваз классика"
# или "тойота прадо 120 сальник входного вала" — это РЕАЛЬНО автомобильные
# фразы с реальными словами "сальник"/"вал"/"кпп", просто про совершенно
# другие машины, к которым наши детали не имеют отношения): общий словарь
# ниже отвечает только на вопрос "это вообще про машины?", а вопрос "про ТЕ
# ли это машины?" решает отдельно платформенно-брендовый фильтр ниже —
# _detect_platforms()/_allowed_brands_for_seed()/_BRAND_TOKEN_PATTERNS.
_RELEVANT_TOKENS = [
    # коды коробок/платформ и общие термины трансмиссии
    "dq200", "dq500", "dq250", "dq381", "0am", "0cw", "0bh", "0bt", "0b5",
    "02e", "0d9", "0aw", "dsg", "ea888", "jf015e", "jf011e", "6t30", "6t40",
    "6t45", "вариатор", "мехатроник", "трансмисс", "коробк", "кпп", "акпп",
    "робот", "сцеплени",
    # типовые запчасти/узлы
    "сальник", "шток", "втулк", "клипс", "пыльник", "поршен", "плита",
    "крышк", "прокладк", "фильтр", "гидроблок", "гидроаккумулятор",
    "соленоид", "толкател", "вилк", "подшипник", "ремкомплект",
    "накладк", "педал", "коленвал", "распредвал", "шкив", "полумуфт",
    "картер", "стартер", "спидометр", "редуктор", "шестерн", "привод",
    "диск сцеплени", "вал",
]
_RELEVANT_RE = re.compile("|".join(re.escape(t) for t in _RELEVANT_TOKENS), re.I)

# Платформы этого бизнеса: код коробки/платформы в стартовой фразе -> какие
# марки/модели для НЕЁ считаются "своими". Марки/модели вынесены сюда (а не
# в общий _RELEVANT_TOKENS выше) именно затем, чтобы их можно было сверять
# с конкретной платформой, а не просто с общим списком "любая наша марка".
_PLATFORM_CODES: Dict[str, List[str]] = {
    "vag": [
        "dq200", "dq500", "dq250", "dq381", "0am", "0cw", "0bh", "0bt",
        "0b5", "02e", "0d9", "0aw", "ea888",
    ],
    "nissan_alliance": ["jf015e", "jf011e"],
    "gm": ["6t30", "6t40", "6t45"],
}
_PLATFORM_BRANDS: Dict[str, List[str]] = {
    "vag": [
        "volkswagen", "фольксваген", "audi", "ауди", "skoda", "шкода",
        "seat", "сеат", "tiguan", "тигуан", "golf", "гольф", "passat",
        "пассат", "jetta", "джетта", "octavia", "октавиа",
    ],
    "nissan_alliance": [
        "nissan", "ниссан", "renault", "рено", "mitsubishi", "митсубиси",
        "qashqai", "кашкай", "suzuki", "сузуки",
    ],
    "gm": [
        "chevrolet", "шевроле", "opel", "опель", "cruze", "круз", "astra",
        "астра", "buick", "бьюик", "daewoo", "дэу",
    ],
}

# Марки, которые НЕ относятся ни к одной из платформ выше, но регулярно
# всплывают в associations как шум (реальные примеры из чата — "тойота
# прадо 120 сальник входного вала", "...кпп ваз классика"). Если для
# стартовой фразы удалось распознать платформу, любое упоминание марки из
# этого списка отсекает фразу целиком, даже если рядом есть трансмиссионная
# лексика из _RELEVANT_TOKENS.
_OTHER_BRAND_TOKENS = [
    "ваз", "лада", "lada", "жигули", "нива", "niva",
    "тойота", "toyota", "камаз", "kamaz", "газель", "газ",
    "уаз", "uaz", "bmw", "бмв", "kia", "киа", "hyundai",
    "хендай", "хёндай", "ford", "форд", "mazda", "мазда",
    "honda", "хонда", "chery", "чери", "haval", "хавал",
    "great wall", "geely", "джили", "peugeot", "пежо",
    "citroen", "ситроен", "volvo", "вольво", "jeep", "джип",
    "subaru", "субару", "mercedes", "мерседес", "мерс",
    "lifan", "лифан", "datsun", "датсун", "ssangyong", "санг енг",
    "changan", "чанган", "exeed", "эксид", "omoda", "омода",
    "jac", "джак", "foton", "фотон",
]

_ALL_PLATFORM_BRAND_TOKENS = {t for brands in _PLATFORM_BRANDS.values() for t in brands}
_ALL_KNOWN_BRAND_TOKENS = sorted(_ALL_PLATFORM_BRAND_TOKENS | set(_OTHER_BRAND_TOKENS), key=len, reverse=True)
# Границы слова через lookaround (а не \b) — так надёжнее ловит кириллицу и
# не даёт "ваз" случайно сработать внутри "вазелин"/"квазар" и т.п.
_BRAND_TOKEN_PATTERNS = [
    (t, re.compile(r"(?<![a-zа-яё0-9])" + re.escape(t) + r"(?![a-zа-яё0-9])", re.I))
    for t in _ALL_KNOWN_BRAND_TOKENS
]


def _seed_keywords(seed: str) -> List[str]:
    """Значимые слова (4+ букв) из стартовой фразы — доп. сигнал релевантности."""
    return [w for w in re.findall(r"[a-zа-яё0-9]+", seed.lower()) if len(w) >= 4]


def _detect_platforms(text: str) -> List[str]:
    """Какие платформы (vag/nissan_alliance/gm) угадываются по коду в тексте."""
    low = text.lower()
    return [p for p, codes in _PLATFORM_CODES.items() if any(c in low for c in codes)]


def _allowed_brands_for_seed(seed_text: str) -> Optional[set]:
    """
    Набор "своих" марок для стартовой фразы(-ах) артикула, или None, если
    платформу распознать не удалось (в этом случае брендовый фильтр ниже
    просто не применяется — лучше не отсеять лишнего, чем ошибочно отсеять
    хорошую фразу по неизвестной платформе).
    """
    platforms = _detect_platforms(seed_text)
    if not platforms:
        return None
    allowed: set = set()
    for p in platforms:
        allowed.update(_PLATFORM_BRANDS[p])
    return allowed


def _is_relevant(phrase: str, seed_words: List[str], allowed_brands: Optional[set]) -> bool:
    """
    True, если фраза похожа на что-то из мира авто-запчастей/трансмиссий
    ЭТОГО бизнеса конкретно — а не просто "что-то автомобильное".

    Сначала (если платформа распознана) отсекаем упоминание чужой марки —
    это решает проблему, когда фраза формально "автомобильная"
    (содержит "сальник"/"вал"/"кпп" и т.п.), но на самом деле про
    Ладу/Тойоту/другую платформу, к которой наши детали не относятся.
    Только после этого — как и раньше, проверка по общему словарю
    трансмиссионной лексики / пересечению слов со стартовой фразой.
    """
    low = phrase.lower()
    if allowed_brands is not None:
        for token, pattern in _BRAND_TOKEN_PATTERNS:
            if token not in allowed_brands and pattern.search(low):
                return False
        if any(pattern.search(low) for token, pattern in _BRAND_TOKEN_PATTERNS if token in allowed_brands):
            return True
    if _RELEVANT_RE.search(low):
        return True
    return any(w in low for w in seed_words)


class WordstatApiError(RuntimeError):
    pass


class WordstatRateLimitedError(WordstatApiError):
    """
    Отдельно от обычной WordstatApiError: поднимается, когда после всех
    попыток последний ответ был именно 429 (лимит запросов) — то есть мы
    почти наверняка ВСЕ ещё упираемся в лимит, а не столкнулись с
    единичной сетевой ошибкой или проблемой в самой фразе. Отличаем этот
    случай, чтобы batch-сбор мог сразу остановиться, а не тратить время и
    оставшиеся попытки на заведомо обречённые запросы (см. лог в чате:
    прогон, начатый внутри того же "часа", что и предыдущий выбравший
    лимит прогон, целиком провалился за 10 минут перебора).
    """
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
    last_was_rate_limit = False
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
            last_was_rate_limit = False
            logger.warning("Wordstat %s: сетевая ошибка (попытка %d/%d): %s", path, attempt, retries, exc)
            time.sleep(2 * attempt)
            continue

        if resp.status_code == 429:
            last_error = None
            last_was_rate_limit = True
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

    if last_was_rate_limit:
        raise WordstatRateLimitedError(
            f"Wordstat {path}: лимит запросов всё ещё превышен после {retries} попыток подряд "
            "(429) — судя по всему, лимит просто ещё не восстановился, а не разовая помеха."
        )
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
    seed_text = " ".join(seed_phrases)
    seed_words = _seed_keywords(seed_text)
    allowed_brands = _allowed_brands_for_seed(seed_text)

    def _absorb(data: dict) -> None:
        for bucket in ("results", "associations"):
            for item in data.get(bucket, []) or []:
                phrase = str(item.get("phrase") or "").strip()
                if not phrase:
                    continue
                if not _is_relevant(phrase, seed_words, allowed_brands):
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
        except WordstatRateLimitedError:
            # Не глотаем это молча и не продолжаем перебор — лимит явно ещё
            # не восстановился, дальше пытаться по остальным фразам того же
            # прогона так же бессмысленно. Пробрасываем наверх, чтобы
            # batch-сбор сразу остановился, а не тратил время на заведомо
            # обречённые запросы по всем оставшимся позициям.
            raise
        except WordstatApiError as exc:
            logger.error("Wordstat: ошибка по фразе '%s': %s", phrase, exc)
            continue
        _absorb(data)

        if expand_associations:
            for assoc in (data.get("associations") or [])[:max_associations_per_seed]:
                assoc_phrase = str(assoc.get("phrase") or "").strip()
                assoc_key = assoc_phrase.casefold()
                if not assoc_phrase or assoc_key in seen:
                    continue
                seen.add(assoc_key)
                if not _is_relevant(assoc_phrase, seed_words, allowed_brands):
                    # Сама ассоциация уже выглядит не по теме (например,
                    # созвучное слово из другой области) — не тратим на неё
                    # ещё один запрос ради расширения вглубь, это как раз
                    # источник "дрейфа" в случайные темы.
                    continue
                try:
                    data2 = get_top_requests(assoc_phrase, num_phrases=num_phrases)
                    _absorb(data2)
                except WordstatRateLimitedError:
                    raise
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
