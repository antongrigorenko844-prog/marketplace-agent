"""
Сборка CSV-каталога для импорта в Тильду (раздел магазина -> Товары ->
Импорт/Экспорт -> Импорт).

Тильда НЕ умеет ни подтягивать фид по ссылке (в отличие от Avito), ни
принимать товары через открытое API — единственный способ обновить каталог
это скачать CSV из data/tilda_feed.csv и один раз руками загрузить его в
личном кабинете (см. main.py --build-tilda-catalog).

ЦЕНА: используется та же колонка "Цена, ₽" из ozon_catalog.xlsx, что и для
Ozon/Avito — НЕ отдельная "Цена WB" (в неё заложена комиссия WB, для прямой
продажи через сайт она не нужна).

ФОТО: общая папка photos/ (та же, что у Ozon и WB) — берём только первую
(обложку) ссылку из колонки "Фото", т.к. формат CSV Тильды даёт одно фото на
строку (несколько фото на товар потребовали бы отдельных строк с общим
Parent UID — пока не используется, см. README при необходимости добавить).

СБОРКА "С НУЛЯ" (решение пользователя 2026-09-15): каталог в Тильде сначала
полностью очищается вручную, а этот импорт создаёт все товары заново.
Изначально Tilda UID/External ID оставлялись пустыми, но импорт Тильды не
принимает пустой UID даже для новых товаров ("Empty Uniq column: uid") —
поэтому UID генерируется здесь сами, ДЕТЕРМИНИРОВАННО из артикула (sha256 от
offer_id, взяты первые 12 цифр) — один и тот же товар при пересборке всегда
получает один и тот же UID. External ID = сам артикул (offer_id), для
наглядности и на будущее, если понадобится сопоставление при обновлении.
"""
import hashlib
import logging
import re
from typing import Dict, List, Optional

import openpyxl


def _stable_uid(offer_id: str) -> str:
    """Детерминированный 12-значный числовой UID из артикула (см. docstring)."""
    digest = hashlib.sha256(offer_id.encode("utf-8")).hexdigest()
    num = int(digest, 16) % (10**12)
    return f"{num:012d}"

logger = logging.getLogger("marketplace-agent.tilda_feed")

CSV_HEADER = [
    "Tilda UID",
    "Brand",
    "SKU",
    "Mark",
    "Category",
    "Title",
    "Description",
    "Text",
    "Photo",
    "Price",
    "Quantity",
    "Price Old",
    "Editions",
    "Modifications",
    "External ID",
    "Parent UID",
    "Weight",
    "Length",
    "Width",
    "Height",
]

_HTML_TAG_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_ANY_TAG_RE = re.compile(r"<[^>]+>")


def _clean_description(html_desc: str) -> str:
    """<br/> -> перенос строки, остальные теги вырезаются."""
    if not html_desc:
        return ""
    text = _HTML_TAG_RE.sub("\n", html_desc)
    text = _ANY_TAG_RE.sub("", text)
    return text.strip()


def build_tilda_catalog(catalog_path: str, output_path: str) -> Dict[str, object]:
    """
    Читает data/ozon_catalog.xlsx (название/описание/цена/фото — те же, что
    уже используются для Ozon и Avito) и строит CSV в формате, который
    принимает импорт Тильды (см. CSV_HEADER — совпадает со структурой
    реального экспорта из личного кабинета пользователя от 2026-09-15).

    Возвращает {"written": N, "skipped_no_price": [...], "skipped_no_photo": [...]}.
    """
    wb = openpyxl.load_workbook(catalog_path)
    ws = wb.active

    rows_out: List[List[str]] = []
    skipped_no_price: List[str] = []
    skipped_no_photo: List[str] = []

    for row in ws.iter_rows(min_row=2):
        offer_id = row[0].value
        if not offer_id:
            continue
        offer_id = str(offer_id).strip()
        title = (row[1].value or "").strip()
        description_raw = row[2].value or ""
        price = row[3].value
        old_price = row[4].value
        images_raw = row[5].value or ""

        if not price:
            skipped_no_price.append(offer_id)
            continue

        images = [u.strip() for u in str(images_raw).split("|") if u.strip()]
        if not images:
            skipped_no_photo.append(offer_id)

        cover_photo = images[0] if images else ""
        description = _clean_description(description_raw)
        price_old_val = ""
        try:
            if old_price and float(old_price) > 0:
                price_old_val = str(int(float(old_price)))
        except (TypeError, ValueError):
            price_old_val = ""

        rows_out.append(
            [
                _stable_uid(offer_id),  # Tilda UID — детерминированный, см. docstring
                "",  # Brand
                offer_id,  # SKU
                "",  # Mark
                "",  # Category
                title,  # Title
                description,  # Description
                description,  # Text
                cover_photo,  # Photo
                str(int(price)),  # Price
                "",  # Quantity
                price_old_val,  # Price Old
                "",  # Editions
                "",  # Modifications
                offer_id,  # External ID — тот же артикул, для наглядности
                "",  # Parent UID
                "0",  # Weight
                "0",  # Length
                "0",  # Width
                "0",  # Height
            ]
        )

    import csv

    with open(output_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=";", quoting=csv.QUOTE_MINIMAL)
        writer.writerow(CSV_HEADER)
        writer.writerows(rows_out)

    return {
        "written": len(rows_out),
        "skipped_no_price": skipped_no_price,
        "skipped_no_photo": skipped_no_photo,
    }
