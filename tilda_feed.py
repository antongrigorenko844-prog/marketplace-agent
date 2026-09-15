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
(обложку) ссылку, т.к. формат CSV Тильды даёт одно фото на строку (несколько
фото на товар потребовали бы отдельных строк с общим Parent UID — пока не
используется, см. README при необходимости добавить).

ВАЖНО (обнаружено 2026-09-15): ссылку НЕЛЬЗЯ брать из колонки "Фото" в
ozon_catalog.xlsx как есть — там ссылки на ассеты GitHub Release (через
photo_host.py, нужны для Ozon/WB, см. его docstring), и Тильда при импорте
их не смогла скачать ("Image not available over https"), хотя обычным curl
файл открывается. Вместо этого строим ссылку САМИ напрямую на
raw.githubusercontent.com/.../photos/<артикул>/<файл> — тот же способ, что
раньше использовался для Ozon/WB (см. catalog_editor.attach_local_photos,
там от него отказались из-за проблем с КЭШИРОВАНИЕМ на стороне Ozon/WB, а
не потому что сама раздача не работала — для одноразового импорта в Тильду
кэш ни при чём, поэтому здесь это безопасно).

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
import os
import re
from typing import Dict, List, Optional
from urllib.parse import quote

import openpyxl

logger = logging.getLogger("marketplace-agent.tilda_feed")


def _stable_uid(offer_id: str) -> str:
    """Детерминированный 12-значный числовой UID из артикула (см. docstring)."""
    digest = hashlib.sha256(offer_id.encode("utf-8")).hexdigest()
    num = int(digest, 16) % (10**12)
    return f"{num:012d}"


def _cover_photo_raw_url(photos_dir: str, offer_id: str, raw_base_url: str) -> str:
    """
    Прямая ссылка raw.githubusercontent.com на файл обложки товара из
    photos_dir — см. docstring модуля, почему не берём готовую ссылку из
    ozon_catalog.xlsx.
    """
    import catalog_editor

    files = catalog_editor.own_media_files(photos_dir, offer_id, catalog_editor.IMAGE_EXTS)
    if not files:
        return ""
    fname = files[0]  # own_media_files уже сортирует по номеру, первый = обложка
    full_path = catalog_editor.media_path(photos_dir, offer_id, fname)
    # ВАЖНО (найдено 2026-09-15): папка на диске НЕ всегда называется как
    # сам offer_id — есть FOLDER_ALIASES в catalog_editor.py (например
    # "Dq500" -> "Dq500-sep", из-за регистронезависимой файловой системы на
    # Mac). Поэтому НЕЛЬЗЯ собирать путь из offer_id самим — берём реальный
    # относительный путь от full_path, который media_path уже правильно
    # определила (с учётом алиасов).
    rel_path = os.path.relpath(full_path, photos_dir)
    parts = rel_path.split(os.sep)
    return raw_base_url + "/" + "/".join(quote(p) for p in parts)

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


def build_tilda_catalog(catalog_path: str, output_path: str, photos_dir: str, raw_base_url: str) -> Dict[str, object]:
    """
    Читает data/ozon_catalog.xlsx (название/описание/цена — те же, что уже
    используются для Ozon и Avito) и строит CSV в формате, который
    принимает импорт Тильды (см. CSV_HEADER — совпадает со структурой
    реального экспорта из личного кабинета пользователя от 2026-09-15).
    Фото — см. _cover_photo_raw_url и docstring модуля.

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

        if not price:
            skipped_no_price.append(offer_id)
            continue

        cover_photo = _cover_photo_raw_url(photos_dir, offer_id, raw_base_url)
        if not cover_photo:
            skipped_no_photo.append(offer_id)

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
