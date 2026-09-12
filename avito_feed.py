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
_ANY_TAG_RE = re.compile(r"<[^>]+>")

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


def _clean_description(html_desc: str) -> str:
    """<br/> -> перенос строки, остальные теги вырезаются, эмодзи оставляем как есть."""
    if not html_desc:
        return ""
    text = _HTML_TAG_RE.sub("\n", html_desc)
    text = _ANY_TAG_RE.sub("", text)
    return text.strip()


def build_avito_feed(
    catalog_path: str,
    template_path: str,
    output_path: str,
    seller_address: str,
    brand: str = DEFAULT_BRAND,
    ad_type: str = DEFAULT_AD_TYPE,
    condition: str = DEFAULT_CONDITION,
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

    src_wb = openpyxl.load_workbook(catalog_path)
    src_ws = src_wb.active

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
        price = row[3].value
        images_raw = row[5].value or ""

        if not price:
            skipped_no_price.append(offer_id)
            continue

        images = [u.strip() for u in str(images_raw).split("|") if u.strip()]
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
        ws.cell(row=out_row, column=COL_TITLE, value=title)
        ws.cell(row=out_row, column=COL_PRICE, value=int(price))
        ws.cell(row=out_row, column=COL_GOODS_TYPE, value=GOODS_TYPE_VALUE)
        ws.cell(row=out_row, column=COL_AD_TYPE, value=ad_type)
        ws.cell(row=out_row, column=COL_PART_FOR, value=PART_FOR_VALUE)
        ws.cell(row=out_row, column=COL_PART_KIND, value=PART_KIND_VALUE)
        if part_type:
            ws.cell(row=out_row, column=COL_TRANSMISSION_PART_TYPE, value=part_type)
        ws.cell(row=out_row, column=COL_CONDITION, value=condition)
        ws.cell(row=out_row, column=COL_BRAND, value=brand)
        ws.cell(row=out_row, column=COL_OEM, value=offer_id)

        out_row += 1
        written += 1

    tmpl_wb.save(output_path)

    return {
        "written": written,
        "skipped_no_price": skipped_no_price,
        "unclassified": unclassified,
    }
