"""
Простая автозагрузка Avito через XLSX-фид — БЕЗ ключей/OAuth (для заказов
Авито Доставки и остатков используется отдельный avito_client.py с
AVITO_CLIENT_ID/AVITO_CLIENT_SECRET, см. его docstring).

Формат — официальный шаблон Avito для категории "Транспорт - Запчасти и
аксессуары - Запчасти - Для автомобилей - Трансмиссия и привод" (id шаблона
103807 в личном кабинете Avito, лист "Объявления"). Шаблон скачан
пользователем из кабинета и сохранён как data/avito_template.xlsx —
НЕ трогайте в нём строки 1-4 (название категории/параметров/обязательности/
формата) и лист "Справочники" (списки допустимых значений для выпадающих
списков), Avito это не примет, если строки будут другими. Мы просто
открываем этот файл как основу и дозаписываем данные начиная со строки 5.

ВАЖНОЕ РЕШЕНИЕ про CompatibleCars ("Авто для которых подходит запчасть"):
это поле обязательно ТОЛЬКО если Состояние='Б/У' И не заполнены Производитель
(Brand) и Номер детали OEM (см. официальное описание поля, полученное от
пользователя). Заполнять его полным списком совместимых VAG-моделей
(Volkswagen/Audi/Skoda/Seat, десятки поколений) — отдельная большая задача
(там нужны точные названия из каталога Avito). Поэтому пока используем более
простой путь: у нас Состояние='Новое' почти всегда, и артикулы товаров и
так являются настоящими OEM-номерами VAG — значит просто заполняем
Производитель + Номер детали OEM и CompatibleCars можно оставить пустым.
Если позже понадобится завести резервные детали (Б/У) без OEM-номера — для
них надо будет отдельно заполнить CompatibleCars вручную.

Классификация "Тип детали трансмиссии" — по ключевым словам в названии
товара (см. _CLASSIFY_RULES ниже). Список деталей у нас разнообразный, а
Avito требует ровно одно значение из фиксированного списка 16 вариантов —
если ни одно ключевое слово не подошло, строка помечается для ручной
проверки (не заполняется, вместо этого попадает в возвращаемый список
unclassified, чтобы main.py мог вывести предупреждение), потому что
лучше явно попросить проверить, чем угадать неправильную категорию.

ФОТО ДЛЯ AVITO — отдельный комплект, не такой же, как для Ozon/WB
(обнаружено 2026-09-13): наши обычные фото (photos/<артикул>/) — вытянутые
портретные "маркетинговые" картинки с заголовком сверху и плашками снизу
(водяной знак, "100% ресурс" и т.п.). Avito в карточке товара показывает
превью КВАДРАТОМ, обрезая по центру — весь текст сверху/снизу просто не
попадает в кадр. Поэтому для Avito используется ОТДЕЛЬНАЯ папка
photos_avito/<артикул>/ с фото, уже подготовленными под квадратный формат
(пользователь готовит и подбирает их сам). attach_avito_photos() заливает
их как ассеты GitHub Release (как и обычные фото — см. photo_host.py) и
сохраняет ссылки в data/avito_photos.xlsx (offer_id -> ссылки через "|"),
ОТДЕЛЬНО от ozon_catalog.xlsx, чтобы не трогать фото, уже работающие на
Ozon/WB. build_avito_feed() при сборке фида сначала смотрит в этот файл, и
только если там для товара ничего нет — берёт фото из ozon_catalog.xlsx
(обычные). Если позже причина обрезки на Avito уйдёт (например появится
возможность крутить/кадрировать фото прямо в кабинете) — можно просто
удалить data/avito_photos.xlsx, и всё вернётся к общим фото автоматически.
"""
import logging
import os
import re
from typing import Dict, List, Optional, Tuple

import openpyxl
from openpyxl.utils import get_column_letter

logger = logging.getLogger("marketplace-agent.avito_feed")

SHEET_ADS = "Объявления"
FIRST_DATA_ROW = 5

# Индексы колонок (1-based) в официальном шаблоне (id 103807, лист "Объявления").
COL_ADDRESS = 1
COL_ID = 4
COL_CATEGORY = 16
COL_DESCRIPTION = 17
COL_IMAGE_URLS = 19
COL_TITLE = 24
COL_PRICE = 34
COL_GOODS_TYPE = 36  # "Вид товара"
COL_AD_TYPE = 37  # "Вид объявления"
COL_PART_FOR = 38  # "Тип товара"
COL_PART_KIND = 39  # "Вид запчасти"
COL_TRANSMISSION_PART_TYPE = 40  # "Тип детали трансмиссии"
COL_CONDITION = 41  # "Состояние"
COL_BRAND = 44  # "Производитель" (марка авто, для которой деталь — OEM)
COL_OEM = 45  # "Номер детали OEM"

# Константы, одинаковые для всех строк в этой категории.
CATEGORY_VALUE = "Запчасти и аксессуары"
GOODS_TYPE_VALUE = "Запчасти"
PART_FOR_VALUE = "Для автомобилей"
PART_KIND_VALUE = "Трансмиссия и привод"
DEFAULT_AD_TYPE = "Товар приобретен на продажу"
DEFAULT_CONDITION = "Новое"
DEFAULT_BRAND = "VOLKSWAGEN"

# Порядок КРИТИЧЕН: проверяются по очереди, побеждает ПЕРВОЕ совпадение.
# Ключевые слова ищутся в названии товара без учёта регистра.
#
# Слово "мехатроник" встречается почти в КАЖДОМ названии (это описание
# "для какой системы деталь", а не то, что деталь сама и есть мехатроник —
# например, "Болт мехатроника DSG7" — это болт, а не мехатроник). Поэтому
# конкретные типы деталей (болт, прокладка, фильтр, шток и т.п.) стоят
# ПЕРВЫМИ, а "мехатроник"/"соленоид"/"гидроблок" и т.п. — самыми последними,
# как признак того, что деталь — это ДЕЙСТВИТЕЛЬНО начинка/корпус самого
# мехатроника, раз ничего более конкретного в названии не нашлось.
_CLASSIFY_RULES: List[Tuple[str, List[str]]] = [
    ("Крепёж КПП", [
        "болт", "заглушка", "крепеж", "крепёж", "гайка", "винт",
    ]),
    ("Прокладки и уплотнения КПП", [
        "прокладк", "сальник", "уплотнен", "кольцо",
    ]),
    ("Система смазки и охлаждения КПП", [
        "фильтр", "маслян", "радиатор", "теплообменник",
    ]),
    ("Переключение передач", [
        "шток", "вилк", "селектор",
    ]),
    ("Сцепление", [
        "сцеплен",
    ]),
    ("Фрикционы КПП", [
        "фрикцион", "диск сцепления",
    ]),
    ("Приводные валы, полуоси и ШРУСы", [
        "шрус", "полуос", "привод",
    ]),
    ("Раздаточная коробка", [
        "раздатк", "раздаточн",
    ]),
    ("Карданная передача", [
        "кардан",
    ]),
    ("Дифференциал", [
        "дифференциал",
    ]),
    ("Мосты и редукторы", [
        "мост", "редуктор",
    ]),
    ("Муфта полного привода", [
        "муфта полного привода", "халдекс", "haldex",
    ]),
    ("Корпус КПП", [
        "корпус кпп", "картер",
    ]),
    ("КПП в сборе", [
        "кпп в сборе", "коробка в сборе", "акпп в сборе",
    ]),
    ("Валы, муфты, шестерни и подшипники КПП", [
        "вал", "шестерн", "подшипник", "муфта",
    ]),
    # Самое последнее — общие признаки, что деталь это сам мехатроник
    # (корпус/плата/гидроблок в сборе), а не что-то более конкретное выше.
    ("Электронное управление КПП", [
        "соленоид", "гидроаккумулятор", "сепараторн", "гидроблок",
        "блок управления", "электронн", "мехатроник",
    ]),
]

_HTML_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_BLOCK_TAG_RE = re.compile(r"</?(p|div|li|ul|ol|h[1-6])[^>]*>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")

# Проверено 2026-09-17 на живом объявлении (категория "Трансмиссия и привод",
# шаблон 103807): Avito обрезал заголовок ровно на 50-м символе, прямо
# посреди слова ("...с гальван" вместо "...с гальваническим покрытием").
# В рекламных материалах Avito упоминается лимит "до 100 символов", но на
# практике (этот аккаунт/категория) реально применяется 50 — поэтому режем
# ЗАРАНЕЕ на своей стороне до 50 символов, по границе слова, чтобы Avito
# не обрезал сам и не ломал слово посередине.
AVITO_TITLE_MAX_LEN = 50


def _truncate_title(title: str, max_len: int = AVITO_TITLE_MAX_LEN) -> str:
    """Обрезает заголовок до max_len символов ПО ГРАНИЦЕ СЛОВА (не посреди слова)."""
    title = (title or "").strip()
    if len(title) <= max_len:
        return title
    cut = title[:max_len]
    last_space = cut.rfind(" ")
    if last_space > 0:
        cut = cut[:last_space]
    return cut.rstrip(" ,.;:-—")

# Avito требует, чтобы "Номер детали OEM" состоял ТОЛЬКО из латинских букв и
# цифр (проверено на реальной загрузке — товар "123014‑AF" был отклонён с
# ошибкой "Неправильно заполнен обязательный параметр — Номер детали OEM...
# состоящий из латинских букв и цифр"). Наши артикулы иногда содержат пробелы,
# дефисы (в т.ч. "не такой" юникодный дефис ‑, U+2011), суффиксы вида "-set"/
# "-AF", а у части декоративно-ремонтных наборов ("salniki-shtokov-2sht" и
# т.п.) в артикул закралась кириллица, похожая на латиницу (например "0am325025С"
# — последняя буква на самом деле русская "С"). Чтобы поле проходило проверку,
# нормализуем такие похожие буквы в латиницу, а всё остальное (пробелы,
# дефисы любого вида, "_" и т.п.) просто вырезаем.
_OEM_HOMOGLYPHS = str.maketrans({
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x",
    "А": "A", "В": "B", "Е": "E", "К": "K", "М": "M", "Н": "H", "О": "O",
    "Р": "P", "С": "C", "Т": "T", "У": "Y", "Х": "X",
})
_OEM_STRIP_RE = re.compile(r"[^A-Za-z0-9]")


def _clean_oem(offer_id: str) -> str:
    """Приводит артикул к формату, который принимает Avito для Номера OEM."""
    normalized = offer_id.translate(_OEM_HOMOGLYPHS)
    return _OEM_STRIP_RE.sub("", normalized)

# Явно НЕ детали трансмиссии, даже если в названии есть "фильтр"/"маслян" и
# т.п. (например EA888 — это код ДВИГАТЕЛЯ VAG, а не коробки передач; "корпус
# масляного фильтра двигателя" ловится словом "маслян"/"фильтр" из общих
# правил ниже, хотя это вообще не про трансмиссию). Такие товары не подходят
# для этого шаблона категории Avito ("Трансмиссия и привод") — им нужен
# отдельный шаблон ("Двигатель" и т.п.), который мы пока не подключали.
_OUT_OF_SCOPE_KEYWORDS = ["двигател", "ea888", "ea211", "ea113", "ea888"]


def classify_transmission_part_type(title: str) -> Optional[str]:
    """Подбирает значение 'Тип детали трансмиссии' по ключевым словам в названии.

    Возвращает None, если ни одно правило не подошло — вызывающий код должен
    в этом случае вывести предупреждение и не заполнять клетку, а не
    подставлять что-то наугад.
    """
    t = (title or "").lower()
    if any(kw in t for kw in _OUT_OF_SCOPE_KEYWORDS):
        return None
    for category, keywords in _CLASSIFY_RULES:
        if any(kw in t for kw in keywords):
            return category
    return None


AVITO_PHOTOS_DIR = os.path.join(os.path.dirname(__file__), "photos_avito")
AVITO_PHOTOS_MAP_PATH = os.path.join(os.path.dirname(__file__), "data", "avito_photos.xlsx")


def attach_avito_photos(
    photos_dir: str = AVITO_PHOTOS_DIR,
    map_path: str = AVITO_PHOTOS_MAP_PATH,
) -> Dict[str, List[str]]:
    """
    Заливает фото из photos_avito/<артикул>/ как ассеты GitHub Release (как
    и обычные фото товара, через photo_host.py) и сохраняет ссылки в
    data/avito_photos.xlsx — отдельно от ozon_catalog.xlsx, см. пояснение
    про формат фото для Avito в начале модуля.

    Имена файлов должны начинаться с артикула (например
    "0AM325025B_1.jpg") — так уже готовит их сборка на нашей стороне,
    вручную раскладывать не нужно.

    Возвращает offer_id -> список залитых ссылок (для вывода в лог).
    """
    import photo_host

    if not os.path.isdir(photos_dir):
        return {}

    matched: Dict[str, List[str]] = {}
    for offer_id in sorted(os.listdir(photos_dir)):
        offer_dir = os.path.join(photos_dir, offer_id)
        if not os.path.isdir(offer_dir):
            continue
        files = sorted(
            f for f in os.listdir(offer_dir)
            if os.path.splitext(f)[1].lower() in (".jpg", ".jpeg", ".png", ".webp")
        )
        urls = []
        for fname in files:
            try:
                url = photo_host.upload_file(os.path.join(offer_dir, fname), filename=fname)
            except Exception as exc:  # noqa: BLE001
                logger.warning("%s: не удалось загрузить фото для Avito %s: %s", offer_id, fname, exc)
                continue
            urls.append(url)
        if urls:
            matched[offer_id] = urls

    if matched:
        os.makedirs(os.path.dirname(map_path), exist_ok=True)
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "Фото для Avito"
        ws.append(["offer_id", "image_urls"])
        for offer_id, urls in matched.items():
            ws.append([offer_id, "|".join(urls)])
        wb.save(map_path)

    return matched


def _load_avito_photo_overrides(map_path: str) -> Dict[str, List[str]]:
    if not os.path.exists(map_path):
        return {}
    wb = openpyxl.load_workbook(map_path)
    ws = wb.active
    overrides: Dict[str, List[str]] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        offer_id = str(row[0]).strip()
        urls = [u.strip() for u in str(row[1] or "").split("|") if u.strip()]
        if urls:
            overrides[offer_id] = urls
    return overrides


def _clean_description(html_desc: str) -> str:
    """
    <br/> и блочные теги (<p>/<div>/<li>/<ul>/<ol>/<h1-6>) -> перенос строки,
    остальные теги вырезаются, эмодзи оставляем как есть.

    ВАЖНО (нашли 2026-09-17 на живых объявлениях): раньше остальные теги
    вырезались в "" — если тег стоял ВПЛОТНУЮ к тексту без пробела с двух
    сторон (например "Назначение:<ul><li>Компенсирует..."), слова
    склеивались в одно ("Назначение:Компенсирует"). Теги теперь заменяются
    на перенос строки/пробел, а не на пустоту, поэтому склейки быть не
    должно — дополнительно схлопываем лишние пробелы/переносы.
    """
    if not html_desc:
        return ""
    text = _HTML_TAG_RE.sub("\n", html_desc)
    text = _BLOCK_TAG_RE.sub("\n", text)
    text = _ANY_TAG_RE.sub(" ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()


def build_avito_feed(
    catalog_path: str,
    template_path: str,
    output_path: str,
    seller_address: str,
    brand: str = DEFAULT_BRAND,
    ad_type: str = DEFAULT_AD_TYPE,
    condition: str = DEFAULT_CONDITION,
    avito_photos_map_path: str = AVITO_PHOTOS_MAP_PATH,
    price_overrides_path: Optional[str] = None,
) -> Dict[str, object]:
    """
    Читает data/ozon_catalog.xlsx (общие данные — название/описание/цена/фото,
    те же, что уже используются для Ozon), заполняет ими копию официального
    шаблона Avito (лист "Объявления", строки 1-4 и лист "Справочники" не
    трогаются) и сохраняет результат в output_path.

    Возвращает статистику: {"written": N, "skipped_no_price": [...],
    "unclassified": [(offer_id, title), ...]} — unclassified нужно проверить
    и классифицировать вручную (main.py печатает эти строки).
    """
    if not os.path.exists(template_path):
        raise FileNotFoundError(
            f"{template_path} не найден — сначала положите официальный шаблон Avito "
            "(скачанный из личного кабинета, раздел Автозагрузка) в data/avito_template.xlsx."
        )
    if not seller_address:
        raise ValueError(
            "Не задан адрес продавца (AVITO_SELLER_ADDRESS в .env / GitHub Secrets) — "
            "это обязательное поле в фиде Avito, без него объявления не пройдут модерацию."
        )

    import site_pricing

    src_wb = openpyxl.load_workbook(catalog_path)
    src_ws = src_wb.active
    photo_overrides = _load_avito_photo_overrides(avito_photos_map_path)
    price_overrides = site_pricing.load_price_overrides(price_overrides_path or site_pricing.OVERRIDES_PATH)

    tmpl_wb = openpyxl.load_workbook(template_path)
    ws = tmpl_wb[SHEET_ADS]

    # Чистим старые данные (если фид уже когда-то заполнялся и пересобирается
    # заново) — начиная со строки 5 и до конца текущих данных.
    if ws.max_row >= FIRST_DATA_ROW:
        ws.delete_rows(FIRST_DATA_ROW, ws.max_row - FIRST_DATA_ROW + 1)

    written = 0
    skipped_no_price: List[str] = []
    unclassified: List[Tuple[str, str]] = []

    out_row = FIRST_DATA_ROW
    for row in src_ws.iter_rows(min_row=2):
        offer_id = row[0].value
        if not offer_id:
            continue
        offer_id = str(offer_id).strip()
        title = (row[1].value or "").strip()
        description_raw = row[2].value or ""
        # Цена для Avito: если для товара есть оверрайд (новые товары,
        # см. site_pricing.py) — берём его, иначе как раньше, общая цена.
        price = price_overrides.get(offer_id, row[3].value)
        images_raw = row[5].value or ""

        if not price:
            skipped_no_price.append(offer_id)
            continue

        images = photo_overrides.get(offer_id) or [
            u.strip() for u in str(images_raw).split("|") if u.strip()
        ]
        part_type = classify_transmission_part_type(title)
        if part_type is None:
            # "Тип детали трансмиссии" — обязательное поле в этом шаблоне.
            # Не пишем строку с пустым обязательным полем (Avito её всё
            # равно отклонит) — пропускаем и просим проверить вручную. Так
            # же сюда попадают товары не по теме этого шаблона (например,
            # аксессуары, а не запчасти трансмиссии) — для них нужен другой
            # шаблон категории, скачанный отдельно.
            unclassified.append((offer_id, title))
            continue

        ws.cell(row=out_row, column=COL_ADDRESS, value=seller_address)
        ws.cell(row=out_row, column=COL_ID, value=offer_id)
        ws.cell(row=out_row, column=COL_CATEGORY, value=CATEGORY_VALUE)
        ws.cell(row=out_row, column=COL_DESCRIPTION, value=_clean_description(description_raw))
        if images:
            ws.cell(row=out_row, column=COL_IMAGE_URLS, value="\n".join(images))
        ws.cell(row=out_row, column=COL_TITLE, value=_truncate_title(title))
        ws.cell(row=out_row, column=COL_PRICE, value=int(price))
        ws.cell(row=out_row, column=COL_GOODS_TYPE, value=GOODS_TYPE_VALUE)
        ws.cell(row=out_row, column=COL_AD_TYPE, value=ad_type)
        ws.cell(row=out_row, column=COL_PART_FOR, value=PART_FOR_VALUE)
        ws.cell(row=out_row, column=COL_PART_KIND, value=PART_KIND_VALUE)
        if part_type:
            ws.cell(row=out_row, column=COL_TRANSMISSION_PART_TYPE, value=part_type)
        ws.cell(row=out_row, column=COL_CONDITION, value=condition)
        ws.cell(row=out_row, column=COL_BRAND, value=brand)
        ws.cell(row=out_row, column=COL_OEM, value=_clean_oem(offer_id))

        out_row += 1
        written += 1

    tmpl_wb.save(output_path)

    return {
        "written": written,
        "skipped_no_price": skipped_no_price,
        "unclassified": unclassified,
    }
