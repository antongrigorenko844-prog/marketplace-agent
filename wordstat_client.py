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
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

import requests

from config import config

logger = logging.getLogger("marketplace-agent.wordstat")

# ПОСТОЯННЫЙ (между запусками) кэш ответов Wordstat по фразе — ключ:
# "фраза|регионы|устройства". Если фраза уже когда-либо запрашивалась (в
# ЛЮБОМ прошлом запуске, для ЛЮБОГО артикула — не только в текущем прогоне),
# результат берётся отсюда без обращения к API. Это то, о чём просил
# пользователь: если семантика под "dsg7 dq200" уже собрана один раз, она
# переиспользуется везде, где эта же фраза встречается снова, а не
# запрашивается заново на каждый прогон.
#
# ВАЖНО: кэшируется СЫРОЙ ответ API (results+associations), а НЕ уже
# отфильтрованные "релевантные" фразы. Это специально — фильтр релевантности
# (_is_relevant/_STRONG_TOKENS и т.п.) в этом проекте регулярно дорабатывается
# по мере находок на реальных данных; если бы кэшировался готовый
# отфильтрованный результат, старые записи "заморозили" бы решения СТАРОГО
# фильтра навсегда. А так при каждом сборе (даже из кэша) фильтрация
# применяется заново, актуальной версией _is_relevant.
_CACHE_PATH = os.path.join(os.path.dirname(__file__), "data", "wordstat_cache.json")
_phrase_cache: Optional[Dict[str, dict]] = None
_cache_hits = 0
_cache_misses = 0


def _load_cache() -> Dict[str, dict]:
    global _phrase_cache
    if _phrase_cache is None:
        if os.path.exists(_CACHE_PATH):
            try:
                with open(_CACHE_PATH, "r", encoding="utf-8") as f:
                    _phrase_cache = json.load(f)
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Wordstat: не удалось прочитать кэш %s: %s — начинаю с пустого", _CACHE_PATH, exc)
                _phrase_cache = {}
        else:
            _phrase_cache = {}
    return _phrase_cache


def _save_cache() -> None:
    if _phrase_cache is None:
        return
    try:
        os.makedirs(os.path.dirname(_CACHE_PATH), exist_ok=True)
        tmp_path = _CACHE_PATH + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(_phrase_cache, f, ensure_ascii=False, indent=1, sort_keys=True)
        os.replace(tmp_path, _CACHE_PATH)
    except OSError as exc:
        logger.warning("Wordstat: не удалось сохранить кэш %s: %s", _CACHE_PATH, exc)


def cache_stats() -> Dict[str, int]:
    """Сколько фраз в текущем прогоне взято из постоянного кэша без обращения
    к Wordstat, а сколько реально запрошено у API — для итогового отчёта."""
    return {"hits": _cache_hits, "misses": _cache_misses}


def reset_cache_stats() -> None:
    global _cache_hits, _cache_misses
    _cache_hits = 0
    _cache_misses = 0

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
# ВАЖНО (второй уровень фильтра, добавлен после того, как реальный прогон
# на 105 артикулах показал: почти треть собранных фраз — это "мехатроника
# и робототехника" (учебная специальность), "робот человек"/"роботы под
# прикрытием" (кино/мультфильмы), "клубочковая фильтрация" (медицина),
# "крышки для консервирования" (заготовки), "плита дорожная" (стройка) —
# всё это пришло НЕ от прямого запроса по артикулу, а от того, что среди
# associations затесалось голое общее слово вроде "мехатроника"/"фильтр"/
# "крышка"/"плита"/"сальник", оно само по себе прошло проверку через
# _RELEVANT_TOKENS (это ведь тоже "автомобильное" слово), и мы потратили
# на НЕГО отдельный запрос "вглубь" — а у такого голого общесловарного
# запроса собственные associations оказались уже вообще ни о чём: у слова
# слишком много несвязанных значений/контекстов в реальных поисках людей.
# Поэтому словарь разделён на два яруса:
#   _STRONG_TOKENS — конкретные, малодвусмысленные термины (коды платформ,
#     конкретные узлы вроде "гидроаккумулятор"/"пыльник"/"коленвал") —
#     ими можно оправдывать ещё один запрос вглубь (expand_associations).
#   _WEAK_TOKENS — короткие бытовые слова с сильной омонимией ("фильтр"
#     давления/воды/крови, "крышка" кастрюли/банки, "плита" дорожная/
#     кухонная, "вал" в смысле "поток"/"вал канал", "робот" в смысле кино,
#     "прокладка" в бытовом смысле, "вилка" розетки/еды и т.д.) — они
#     достаточно годятся, чтобы ОСТАВИТЬ уже полученную фразу (не резать
#     то, что и так пришло от вашего же артикула), но НЕ годятся, чтобы
#     оправдать ещё один отдельный запрос именно по этому голому слову.
_STRONG_TOKENS = [
    # коды коробок/платформ — однозначны
    "dq200", "dq500", "dq250", "dq381", "0cw", "0bh", "0bt", "0b5",
    "02e", "0d9", "0aw", "dsg", "ea888", "jf015e", "jf011e", "6t30", "6t40",
    "6t45", "6t50",
    # кириллический вариант DSG — реальные пользователи часто набирают его
    # фонетической транслитерацией "ДСГ" — без этого такие фразы не
    # распознавались бы фильтром как релевантные
    "дсг",
    # ВАЖНО: "0am"/"0ам" сюда НЕ добавляем, хотя это тоже код платформы —
    # без границы слова (а в этом списке её нет, см. _STRONG_RE) "0am"
    # ловит подстрокой время суток "10am"/"20am" и т.п. Вместо этого они
    # обрабатываются отдельно ниже (_SHORT_CODE_WORDS) — засчитываются
    # только когда реально являются словом СИДА (т.е. только для статей,
    # где "0am"/"0ам" явно стоит рядом с целевым словом типа "мехатроник
    # 0am"), и с обязательной границей слова через _word_present.
    # конкретные узлы/детали DSG/CVT-трансмиссии и сцепления — низкий риск омонимии
    # ВАЖНО: сюда нельзя добавлять узлы, которые существуют в ЛЮБОМ автомобиле/
    # механизме независимо от коробки (коленвал, распредвал, редуктор, шкив,
    # стартер, спидометр, картер, шестерня и т.п.) — такие слова не омонимы в
    # строгом смысле, но они никак не привязаны к DSG/CVT-мехатронике конкретно
    # и массово тянут абсолютно постороннюю семантику (сальники коленвала ВАЗ,
    # редукторы мотоблоков, стартеры триммеров и т.д.) — проверено на реальных
    # данных: только "коленвал"/"распредвал"/"редуктор"/"шкив"/"шестерн"/
    # "стартер" дали 349 мусорных строк из 2529 в одном тестовом сборе.
    # "гидроблок" и "втулк" сюда же по той же причине: "гидроблок" — родовой
    # термин для гидроблока ЛЮБОЙ коробки (нашли "гидроблока кпп zf 4wg 210" —
    # тракторный/спецтехники ZF, не DSG), "втулк" — родовое машиностроительное
    # слово ("втулка скольжения с фланцем", "втулку распредвала на мтз 82" —
    # трактор Беларус). Оставлены только там, где реально нет альтернативы
    # (JF015E/DQ200-специфичные результаты по-прежнему проходят через
    # собственные слова сида этих статей, не через этот список).
    "шток", "клипс", "пыльник", "поршен",
    "гидроаккумулятор", "соленоид", "толкател", "полумуфт", "диск сцеплени",
]
_WEAK_TOKENS = [
    "вариатор", "мехатроник", "трансмисс", "коробк", "кпп", "акпп",
    "робот", "сцеплени", "сальник", "плита", "крышк", "прокладк",
    "фильтр", "подшипник", "ремкомплект", "накладк", "педал", "привод",
    "вал", "вилк",
]
_RELEVANT_TOKENS = _STRONG_TOKENS + _WEAK_TOKENS
_RELEVANT_RE = re.compile("|".join(re.escape(t) for t in _RELEVANT_TOKENS), re.I)
_STRONG_RE = re.compile("|".join(re.escape(t) for t in _STRONG_TOKENS), re.I)

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
    "gm": ["6t30", "6t40", "6t45", "6t50"],
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


_SHORT_CODE_WORDS = {"0am", "0ам"}  # короткие (<4 симв.), но однозначные коды —
# см. _word_present ниже: "0am"/"0ам" учитываются ТОЛЬКО когда реально стоят
# в стартовой фразе статьи (например "мехатроник 0am"), и только по границе
# слова — иначе "0am" без границы ловит время суток "10am"/"20am" и т.п.


def _seed_keywords(seed: str) -> List[str]:
    """Значимые слова (4+ букв, либо известные короткие коды) из стартовой фразы."""
    words = re.findall(r"[a-zа-яё0-9]+", seed.lower())
    return [w for w in words if len(w) >= 4 or w in _SHORT_CODE_WORDS]


def _word_present(word: str, low: str) -> bool:
    """
    Проверка "слово реально ЕСТЬ во фразе" — по границе слова, а не просто
    подстрокой. Без этого "заглушка" (слово стартовой фразы) находилось
    ВНУТРИ совсем другого слова "пневмозаглушка"/"антизаглушка" (это
    отдельные промышленные термины про заглушки труб, не про деталь
    коробки) — и такая фраза ошибочно считалась релевантной только из-за
    случайного совпадения куска слова.
    """
    return bool(re.search(r"(?<![a-zа-яё0-9])" + re.escape(word) + r"(?![a-zа-яё0-9])", low, re.I))


def _is_weak_word(word: str) -> bool:
    """True, если слово — это, по сути, один из _WEAK_TOKENS (или содержит
    его корень) — то есть само по себе слишком общее/многозначное, чтобы
    оправдать им ещё один запрос вглубь (см. комментарий у _WEAK_TOKENS)."""
    return any(t in word or word in t for t in _WEAK_TOKENS)


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


# ВАЖНО (третий уровень фильтра): выяснилось на реальном запросе
# "Мехатроник DQ200", что Wordstat отдаёт "мехатроника и робототехника"
# (учебная специальность), "робот человек", "колледж мехатроники и
# пищевой индустрии" и т.п. ПРЯМО в associations САМОГО первого запроса —
# то есть до всякого расширения вглубь, ограничение на трату лишнего
# запроса (см. _is_strong_relevant выше) тут не помогает вообще, потому
# что лишнего запроса и не было. У слова "мехатроник(а)" в реальных
# данных Яндекса, судя по всему, два ПОЛНОСТЬЮ разных населения
# поисковых запросов — про коробку передач и про учебную специальность —
# и разделить их можно только по содержимому самой фразы, а не по тому,
# сколько запросов мы готовы на неё потратить. Список ниже — явные
# маркеры "не туда" (образование/профессия, робот в кино и игрушках,
# валюта, сериалы, медицина, бытовые фильтры/крышки, мусор с сайтов) —
# если такой маркер есть, фраза отсекается СРАЗУ, до любых других правил.
_OFF_TOPIC_TOKENS = [
    # "мехатроника" как специальность/профессия, не деталь
    "робототехник", "специальност", "професси", "кем работать", "коллед",
    "институт", "университет", "техникум", "факультет", "поступ", "баллы",
    "направление подготовки", "пищевой индустрии", "15.03", "15.02",
    # "робот" в смысле кино/игрушек/поп-культуры, не "коробка-робот"
    "гуманоид", "распаковк", "роботех", "r2d2", "трансформер",
    "под прикрытием", "боевой робот", "дитя робота", "вкалывают роботы",
    "робот человек", "робот федор", "робот козел", "робот emo",
    # валюта/курсы — короткие корни без окончаний, чтобы ловить и
    # "доллар"/"доллара"/"долларов", и "валюта"/"валют"/"валютный"
    "usd", "цб рф", "нацбанк", "доллар", "валют", "банки ру",
    # кино/сериалы
    "сериал", "мультфильм", "мультсериал", "актеры", "сезон",
    # медицина/техника — общий корень "фильтрац" (а не только
    # "клубочковая фильтрация" именительным падежом) ловит и
    # "клубочковой/скорость клубочковой фильтрации", и "анизотропная
    # фильтрация", и "система фильтрации воды" разом, вне зависимости от
    # падежа — конкретно "фильтр"/"масляный фильтр" этот корень не
    # затрагивает, так что для наших деталей ничего не потеряется
    "фильтрац",
    # бытовые "фильтр"/"крышка"/"плита" не про машину
    "консервирования", "аквафильтр", "фильтр для воды", "фильтр на кран",
    "циклонный фильтр", "самопромывной", "дорожная плита",
    "плита дорожная", "электрическая плита", "индукционной",
    # мусор с сайтов/форумов
    "pedant", "dp ru", "u on", "чмоня", "педри",
]
_OFF_TOPIC_RE = re.compile("|".join(re.escape(t) for t in _OFF_TOPIC_TOKENS), re.I)


def _is_off_topic(low: str) -> bool:
    return bool(_OFF_TOPIC_RE.search(low))


def _is_bare_weak_word(low: str) -> bool:
    """
    True, если вся фраза — это ОДНО голое многозначное слово (например
    просто "мехатроника" или просто "фильтр"), без вообще ничего рядом.

    Даже отсеяв конкретные "не туда" фразы через _is_off_topic, у самого
    голого слова частотность в Wordstat всё равно — это сумма ВСЕХ его
    значений сразу (см. чат: "мехатроника" — 70225 — это не про вашу
    деталь конкретно, а про слово "мехатроника" вообще, включая учебную
    специальность). Такое число нельзя использовать как частотность
    именно детали, поэтому голое слово просто не оставляем в файле — а
    вот "плата мехатроника"/"dq200 мехатроник"/"мехатроника это" и т.п.
    (слово + ещё хоть что-то) оставляем как раньше.
    """
    words = re.findall(r"[a-zа-яё0-9]+", low)
    return len(words) == 1 and _is_weak_word(words[0])


def _is_relevant(phrase: str, seed_words: List[str], allowed_brands: Optional[set]) -> bool:
    """
    True, если фразу стоит оставить в итоговом файле — не просто "похожа
    на что-то автомобильное", а имеет конкретную "зацепку" за ИМЕННО эту
    деталь: код платформы/узел из _STRONG_TOKENS, свою марку, или "сильное"
    (не общебытовое) слово из стартовой фразы.

    ВАЖНО (почему тут строгое правило, а не просто "есть автомобильное
    слово"): на практике оказалось, что почти любое короткое бытовое
    слово из мира запчастей — "заглушка", "крышка", "фильтр", "сальник",
    "мехатроник" — одновременно является словом ещё из десятка
    несвязанных областей (сантехника, консервация, бытовая техника,
    медицина, вузовские специальности, кино). Готового списка "все
    посторонние темы, где встречается слово X" не существует — конкретные
    примеры (_OFF_TOPIC_TOKENS ниже) ловят только то, что мы уже видели
    вживую, а не то, что появится в следующий раз. Единственный надёжный
    признак "это действительно ваша деталь" — рядом должен быть код
    платформы (dq200 и т.п.), конкретный однозначный узел
    (гидроаккумулятор, пыльник и т.п.) или своя марка — а не просто
    случайное совпадение по обиходному слову типа "заглушка"/"крышка".
    Реальный пример: "заглушка dq200" остаётся, а "крышка с гидрозатвором"
    (сантехника), "пневмозаглушка"/"антизаглушка" (трубная арматура) и
    "как открыть крышку унитаза" — отсекаются, хотя формально содержат то
    же самое слово.

    Сначала — _is_off_topic/_is_bare_weak_word (явные маркеры "не туда" и
    голые общие слова без ничего рядом), затем — брендовый фильтр (чужая
    марка отсекает фразу, даже если тема правильная), и только в конце —
    собственно поиск "зацепки" за деталь.
    """
    low = phrase.lower()
    if _is_off_topic(low) or _is_bare_weak_word(low):
        return False
    if allowed_brands is not None:
        for token, pattern in _BRAND_TOKEN_PATTERNS:
            if token not in allowed_brands and pattern.search(low):
                return False
        if any(pattern.search(low) for token, pattern in _BRAND_TOKEN_PATTERNS if token in allowed_brands):
            return True
    if _STRONG_RE.search(low):
        return True
    strong_seed_words = [w for w in seed_words if not _is_weak_word(w)]
    return any(_word_present(w, low) for w in strong_seed_words)


# Расширение вглубь (собственный запрос по ассоциации) использует то же
# самое строгое правило — раз голого совпадения по _WEAK_TOKENS
# недостаточно, чтобы ОСТАВИТЬ фразу, тем более его недостаточно, чтобы
# тратить на неё ещё один запрос.
_is_strong_relevant = _is_relevant


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


# Каталожные номера VAG/OEM — короткая цепочка цифр, буква(-ы), снова
# цифры (0AM325091E, 0BH325159, 02E305045, WHT001922, 01X301127C и т.п.).
# Проверено на реальном запросе в Wordstat: "0BH325159" сам по себе даёт
# 193 реальных поиска + варианты "0bh325159 vag"/"алюминиевый" и т.п. —
# то есть часть покупателей ищут именно по точному номеру детали, а не
# только разговорными фразами вроде "фильтр dq500". Раньше номер детали
# никогда не попадал в Wordstat как отдельный запрос — только внутри
# текста артикула, откуда его никто не извлекал. Минимум 5-6 подряд
# идущих цифр в середине — чтобы не путать с короткими кодами платформ
# вроде "DQ200"/"6T40" (у них цифр меньше, и по ним UZS уже есть отдельный
# основной запрос).
_PART_NUMBER_RE = re.compile(r"\b[0-9]{0,2}[A-Za-z]{1,3}[0-9]{5,6}[A-Za-z]{0,2}\b")


def extract_part_numbers(text: str) -> List[str]:
    """
    Каталожные номера, найденные в тексте (обычно — в названии товара),
    без дублей, в порядке появления.
    """
    seen = set()
    out: List[str] = []
    for m in _PART_NUMBER_RE.findall(text or ""):
        key = m.casefold()
        if key not in seen:
            seen.add(key)
            out.append(m)
    return out


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
    regions = region_ids or _DEFAULT_REGION_IDS
    cache_key = f"{clean_phrase.casefold()}|{','.join(regions)}|{device}"
    cache = _load_cache()
    global _cache_hits, _cache_misses
    if cache_key in cache:
        _cache_hits += 1
        return cache[cache_key]
    payload = {
        "phrase": clean_phrase,
        "numPhrases": num_phrases,
        "devices": device,
        "regions": regions,
    }
    data = _post("/v2/wordstat/topRequests", payload)
    _cache_misses += 1
    cache[cache_key] = data
    _save_cache()
    return data


def collect_semantics(
    seed_phrases: List[str],
    expand_associations: bool = True,
    num_phrases: int = 50,
    max_associations_per_seed: int = 5,
    applicability: Optional[List[str]] = None,
) -> Dict[str, int]:
    """
    Берёт список стартовых фраз (например разные формулировки для одного
    артикула), для каждой запрашивает topRequests, и если
    expand_associations — дополнительно проходит ОДИН уровень вглубь по
    найденным associations (похожим фразам), чтобы расширить семантику
    автоматически, а не только теми фразами, что вы сами придумали.

    applicability — необязательный список марок/моделей ("Audi A6",
    "Volkswagen Passat B6", ...) для деталей с широкой применимостью на
    разные машины (не привязанных к одному коду коробки/платформы, как,
    например, штоки DQ200, а стоящих на десятках моделей — как корпус
    масляного фильтра 02E305045). В отличие от expand_associations (который
    сам блуждает по похожим фразам и может "уехать" не в ту тему), здесь
    марки/модели заданы ЯВНО пользователем — для каждой из них и для каждой
    уникальной марки отдельно отправляется прямой запрос "<стартовая фраза>
    <марка/модель>", а не угадывается. Заданные марки/модели также
    ГАРАНТИРОВАННО считаются "своими" для брендового фильтра релевантности
    (_is_relevant), даже если платформу не удалось определить по коду в
    стартовой фразе — так фильтр не отсеет собственные же марки бизнеса.

    Возвращает {фраза: частотность} — если фраза встретилась несколько раз
    (как результат и как ассоциация), берётся максимальное значение.
    """
    collected: Dict[str, int] = {}
    seen = set()
    base_seeds = [p.strip() for p in seed_phrases if p and p.strip()]
    applicability = [a.strip() for a in (applicability or []) if a and a.strip()]
    seed_text = " ".join(base_seeds + applicability)
    seed_words = _seed_keywords(seed_text)
    # "Сильные" слова стартовой фразы — без голых общих слов вроде
    # "фильтр"/"сальник" (если весь seed — это, например, "фильтр dq500",
    # то в качестве "сильного" сигнала для расширения вглубь останется
    # только "dq500", а не любое слово с "фильтр" в составе).
    strong_seed_words = [w for w in seed_words if not _is_weak_word(w)]
    allowed_brands = _allowed_brands_for_seed(seed_text)
    if applicability:
        extra_allowed = {
            w for entry in applicability for w in re.findall(r"[a-zа-яё0-9]+", entry.lower())
        }
        allowed_brands = (allowed_brands or set()) | extra_allowed

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

    def _absorb_total(sent_phrase: str, data: dict) -> None:
        """
        ВАЖНО: отдельно от _absorb() — захватывает totalCount, суммарную
        частотность именно ТОЙ фразы, которую мы реально отправили в
        Wordstat (а не "похожих"/"results"/"associations" фраз). Раньше
        это поле нигде не читалось — из-за этого частотность самого кода
        детали (например точный номер "0BH325159") никогда не попадала в
        итоговый файл как отдельная строка, даже когда у него реально есть
        поисковый объём — Wordstat присылает его именно в totalCount, а не
        обязательно повторяет как отдельный элемент внутри "results" (тот
        список — это ПОХОЖИЕ/более широкие фразы, не гарантированное эхо
        исходного запроса). Проверено на реальном примере из README:
        "0BH325159" сам по себе даёт 193 реальных поиска — этот текст
        отсюда и появился, но раньше это число никуда не сохранялось.
        """
        sent_phrase = sent_phrase.strip()
        if not sent_phrase:
            return
        try:
            total = int(data.get("totalCount") or 0)
        except (TypeError, ValueError):
            total = 0
        if total <= 0:
            return
        if not _is_relevant(sent_phrase, seed_words, allowed_brands):
            return
        if sent_phrase not in collected or total > collected[sent_phrase]:
            collected[sent_phrase] = total

    seeds = list(base_seeds)
    if applicability:
        # Уникальные "марки" — первое слово каждой записи применимости
        # (например из "Audi A6" и "Audi A4" получаем одну марку "Audi") —
        # чтобы отдельно спросить и общий по-марочный запрос ("<фраза>
        # audi"), а не только по каждой конкретной модели.
        brands: List[str] = []
        seen_brand = set()
        for entry in applicability:
            first = entry.split()[0] if entry.split() else ""
            key = first.casefold()
            if first and key not in seen_brand:
                seen_brand.add(key)
                brands.append(first)
        for base in base_seeds:
            for entry in applicability:
                seeds.append(f"{base} {entry}")
            for brand in brands:
                seeds.append(f"{base} {brand}")

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
        _absorb_total(phrase, data)

        if expand_associations:
            for assoc in (data.get("associations") or [])[:max_associations_per_seed]:
                assoc_phrase = str(assoc.get("phrase") or "").strip()
                assoc_key = assoc_phrase.casefold()
                if not assoc_phrase or assoc_key in seen:
                    continue
                seen.add(assoc_key)
                if not _is_strong_relevant(assoc_phrase, strong_seed_words, allowed_brands):
                    # Здесь — специально _is_strong_relevant, а не обычная
                    # _is_relevant: одного голого общего слова вроде
                    # "фильтр"/"сальник"/"мехатроник"/"робот" НЕДОСТАТОЧНО,
                    # чтобы тратить на него ещё один запрос "вглубь" — у
                    # таких голых общесловарных запросов собственные
                    # associations оказываются уже вообще не про наш бизнес
                    # (реальный пример: "мехатроника" -> "мехатроника и
                    # робототехника" (учебная специальность) -> "робот
                    # человек"/"роботы под прикрытием" и т.п. — см. чат).
                    continue
                try:
                    data2 = get_top_requests(assoc_phrase, num_phrases=num_phrases)
                    _absorb(data2)
                    _absorb_total(assoc_phrase, data2)
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
