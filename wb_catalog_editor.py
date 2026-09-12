"""
Построение и разбор редактируемого Excel-каталога WB — название, описание
и ФОТО. По той же схеме, что и catalog_editor.py для Ozon, но под API WB,
у которого другая механика:

- Ключ товара у WB — vendorCode (артикул продавца), но методы обновления
  карточки и фото требуют ЧИСЛОВОЙ nmID (внутренний номер WB) — он берётся
  из уже выгруженного data/wb_cards.json по vendorCode.
- /content/v2/cards/update — правит название/описание, ПЕРЕЗАПИСЫВАЕТ
  карточку целиком (нужно прислать и то, что не меняете), но ЭТОТ метод
  не умеет трогать фото/видео вообще — фото совершенно отдельный метод.
- /content/v3/media/save — правит фото/видео, тоже полная замена (новый
  список ссылок заменяет старый целиком), но никак не связан с названием.

Из-за этого у WB два независимых full-replace метода вместо одного, как у
Ozon — соответственно, ниже две отдельные функции сборки: build_wb_update_items
(текст) и build_wb_media_updates (фото), их можно отправлять по отдельности.
"""
import logging
import os
import re
from typing import Dict, List, Optional
from urllib.parse import quote

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter

import catalog_editor

logger = logging.getLogger("marketplace-agent.wb_catalog_editor")

FONT_NAME = "Arial"
HEADER_FILL = PatternFill(start_color="305496", end_color="305496", fill_type="solid")
HEADER_FONT = Font(name=FONT_NAME, bold=True, color="FFFFFF")
NORMAL_FONT = Font(name=FONT_NAME)
WARN_FILL = PatternFill(start_color="FFF2CC", end_color="FFF2CC", fill_type="solid")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
TITLE_MAX_LEN = 60  # ограничение WB (проверено в живой документации 02.09.2026)

COLUMNS = [
    ("vendor_code", "Артикул WB (vendorCode) — НЕ менять, это ключ"),
    ("title", "Название (до 60 символов — ограничение WB!)"),
    ("description", "Описание"),
    ("images", "Фото: ссылки через | , первая = главная (пусто = не менять)"),
    ("video_url", "Видео: ссылка на .mp4/.mov, до 50 МБ, максимум 1 (пусто = не менять)"),
    ("price", "Цена WB, ₽ (финальная, покупателю — при пересборке подставляется текущая цена с WB)"),
    ("discount", "Скидка WB, % (справочно — текущая; не редактируется отсюда, см. push_wb_price)"),
    ("notes", "Заметки"),
]


def _cards_by_vendor(wb_data: dict) -> Dict[str, dict]:
    return {c.get("vendorCode"): c for c in wb_data.get("cards", []) if c.get("vendorCode")}


def _card_title(card: dict) -> str:
    return card.get("title") or card.get("subjectName") or ""


def _card_description(card: dict) -> str:
    return card.get("description") or ""


def _extract_photo_urls(card: dict) -> List[str]:
    """
    Собирает текущие ссылки на фото карточки. Поле в ответе WB может
    называться "photos" или "mediaFiles" в зависимости от версии ответа —
    проверьте на реальных данных после fetch-wb и поправьте здесь, если
    название поля другое.
    """
    urls: List[str] = []
    for key in ("photos", "mediaFiles", "media"):
        for item in card.get(key) or []:
            url = item.get("big") or item.get("c516x688") or item.get("url") if isinstance(item, dict) else item
            if url and url not in urls:
                urls.append(url)
        if urls:
            break
    return urls


def _extract_video_url(card: dict) -> str:
    """
    Пытается найти ссылку на уже загруженное видео карточки — НЕ ПРОВЕРЕНО
    на реальных данных (в живой документации WB не было явного примера, как
    видео выглядит в ответе /content/v2/get/cards/list; сюда собраны самые
    вероятные варианты названия поля). Если после fetch-wb колонка "Видео"
    у товаров с видео остаётся пустой — напишите мне, поправим по реальному
    JSON. ВАЖНО: пока это не проверено, если меняете фото у товара, у
    которого уже есть видео на WB, проверьте после push, что видео не
    пропало, и при необходимости впишите ссылку на него в колонку "Видео"
    вручную перед повторным push-wb-cards.
    """
    video = card.get("video") or card.get("videoUrl") or card.get("video_url")
    if isinstance(video, dict):
        return video.get("url") or video.get("link") or ""
    if isinstance(video, str):
        return video
    return ""


def _prices_by_nm(wb_data: dict) -> Dict[int, dict]:
    """
    nmID -> {"price": <цена до скидки>, "discount": <% скидки>,
    "discounted_price": <финальная цена покупателю>} из wb_data["prices"]
    (см. wb_client.get_prices — GET /api/v2/list/goods/filter).

    ЭКСПЕРИМЕНТАЛЬНО: точная структура ответа не проверена на реальных
    данных этого кабинета — ниже разобраны самые вероятные названия полей
    по живой документации dev.wildberries.ru на 2026-09-12 (nmID, sizes[0]
    .price/.discountedPrice, discount). Если после fetch-wb + build-wb-
    catalog колонка "Цена WB" у существующих товаров осталась пустой —
    посмотрите сырой data/wb_cards.json (ключ "prices") и поправьте имена
    полей здесь под то, что реально приходит.
    """
    out: Dict[int, dict] = {}
    for item in wb_data.get("prices", []) or []:
        nm_id = item.get("nmID") or item.get("nmId")
        if not nm_id:
            continue
        sizes = item.get("sizes") or [{}]
        size0 = sizes[0] if sizes else {}
        base_price = size0.get("price")
        discount = item.get("discount")
        discounted = size0.get("discountedPrice")
        if discounted is None and base_price is not None and discount is not None:
            try:
                discounted = round(float(base_price) * (1 - float(discount) / 100))
            except (TypeError, ValueError):
                discounted = None
        out[nm_id] = {"price": base_price, "discount": discount, "discounted_price": discounted}
    return out


def current_wb_price(card: dict, prices_by_nm: Dict[int, dict]) -> dict:
    """{"price": <финальная цена или None>, "discount": <% или None>} для карточки."""
    nm_id = card.get("nmID")
    info = prices_by_nm.get(nm_id, {}) if nm_id else {}
    return {"price": info.get("discounted_price"), "discount": info.get("discount")}


def price_before_discount(final_price, discount_pct) -> int:
    """
    Обратный пересчёт: какую "цену до скидки" нужно отправить в WB, чтобы
    после применения ТЕКУЩЕГО % скидки покупатель увидел ровно final_price
    — так push_wb_price.py меняет только видимую цену, не трогая саму
    скидку (см. вопрос про логику цены WB, решили 2026-09-12).
    """
    try:
        discount_pct = float(discount_pct or 0)
    except (TypeError, ValueError):
        discount_pct = 0
    if discount_pct >= 100:
        discount_pct = 0
    return round(float(final_price) / (1 - discount_pct / 100))


def build_wb_catalog(wb_data: dict, xlsx_path: str) -> int:
    """Строит редактируемый xlsx из уже загруженного data/wb_cards.json."""
    cards = _cards_by_vendor(wb_data)
    prices_by_nm = _prices_by_nm(wb_data)

    workbook = Workbook()
    ws = workbook.active
    ws.title = "WB"

    for col_idx, (_, header) in enumerate(COLUMNS, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.fill = HEADER_FILL

    row_idx = 2
    for vendor_code in sorted(cards.keys()):
        card = cards[vendor_code]
        title = _card_title(card)
        description = _card_description(card)
        images_str = "|".join(_extract_photo_urls(card))
        video_str = _extract_video_url(card)
        price_info = current_wb_price(card, prices_by_nm)

        row_values = [
            vendor_code, title, description, images_str, video_str,
            price_info["price"], price_info["discount"], "",
        ]
        for col_idx, value in enumerate(row_values, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.font = NORMAL_FONT
            if col_idx == 2 and len(title) > TITLE_MAX_LEN:
                cell.fill = WARN_FILL
        row_idx += 1

    widths = [18, 45, 45, 55, 45, 14, 12, 25]
    for col_idx, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLUMNS))}{max(row_idx - 1, 1)}"

    workbook.save(xlsx_path)
    return row_idx - 2


def load_wb_catalog_edits(xlsx_path: str) -> Dict[str, dict]:
    workbook = load_workbook(xlsx_path, data_only=True)
    ws = workbook.active
    edits: Dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or row[0] in (None, ""):
            continue
        vendor_code = str(row[0]).strip()
        raw = {COLUMNS[i][0]: (row[i] if i < len(row) else None) for i in range(len(COLUMNS))}
        images_raw = raw.get("images") or ""
        images = [u.strip() for u in str(images_raw).split("|") if u.strip()]
        title_val = raw.get("title")
        video_val = raw.get("video_url")
        edits[vendor_code] = {
            "title": title_val.strip() if isinstance(title_val, str) else title_val,
            "description": raw.get("description") or "",
            "images": images,
            "video_url": video_val.strip() if isinstance(video_val, str) else (video_val or ""),
            "price": raw.get("price"),
            "discount": raw.get("discount"),
            "notes": raw.get("notes"),
        }
    return edits


def attach_wb_photos(xlsx_path: str, photos_dir: str, raw_base_url: str = "") -> Dict[str, List[str]]:
    """
    То же самое, что attach_local_photos в catalog_editor.py (Ozon), но
    ключ — vendorCode. Если артикулы у вас общие для Ozon и WB (как и
    задумано), папка photos/<артикул>/ подойдёт сразу для обеих площадок —
    переименовывать/дублировать файлы не нужно (см.
    catalog_editor.own_media_files).

    Каждый файл загружается как ассет GitHub Release (photo_host.py) —
    raw_base_url (raw.githubusercontent.com) больше не используется, он
    оказался ненадёжным на практике; параметр оставлен только для
    обратной совместимости вызова.
    """
    import photo_host

    workbook = load_workbook(xlsx_path)
    ws = workbook.active

    vendor_col_idx = 1
    images_col_idx = next(i for i, (key, _) in enumerate(COLUMNS, start=1) if key == "images")

    matched: Dict[str, List[str]] = {}
    for row in ws.iter_rows(min_row=2):
        vendor_cell = row[vendor_col_idx - 1]
        if not vendor_cell.value:
            continue
        vendor_code = str(vendor_cell.value).strip()

        own_files = catalog_editor.own_media_files(photos_dir, vendor_code, IMAGE_EXTS)
        if not own_files:
            continue

        urls = []
        for f in own_files:
            try:
                url = photo_host.upload_file(
                    catalog_editor.media_path(photos_dir, vendor_code, f),
                    filename=catalog_editor.asset_filename_for(vendor_code, f),
                )
            except Exception as exc:
                logger.warning("%s: не удалось загрузить фото %s: %s", vendor_code, f, exc)
                continue
            urls.append(url)
        if not urls:
            continue
        row[images_col_idx - 1].value = "|".join(urls)
        matched[vendor_code] = urls

    workbook.save(xlsx_path)
    return matched


def build_wb_update_items(wb_data: dict, edits: Dict[str, dict]) -> List[dict]:
    """
    Собирает items для /content/v2/cards/update — правит ТОЛЬКО title и
    description, всё остальное (brand, dimensions, characteristics, sizes,
    kizMarked) копирует из уже выгруженной карточки без изменений. Фото
    сюда не входят — для них build_wb_media_updates.
    """
    cards = _cards_by_vendor(wb_data)
    items: List[dict] = []
    for vendor_code, edit in edits.items():
        card = cards.get(vendor_code)
        if not card:
            continue
        title = (edit.get("title") or _card_title(card) or "").strip()
        description = edit.get("description") or _card_description(card)
        items.append(
            {
                "nmID": card.get("nmID"),
                "vendorCode": vendor_code,
                "kizMarked": card.get("kizMarked", False),
                "brand": card.get("brand", ""),
                "title": title[:TITLE_MAX_LEN],
                "description": description,
                "dimensions": card.get("dimensions") or {},
                "characteristics": card.get("characteristics") or [],
                "sizes": card.get("sizes") or [],
            }
        )
    return items


def build_wb_price_updates(wb_data: dict, edits: Dict[str, dict]) -> List[dict]:
    """
    Собирает список товаров, у которых "Цена WB" в xlsx отличается от
    текущей цены на WB (пустая ячейка или совпадение с текущей ценой — не
    считается изменением, такой товар в список не попадёт). Для каждого
    считает "цену до скидки", которую нужно отправить в WB, чтобы после
    применения ТЕКУЩЕГО % скидки покупатель увидел ровно вписанную цену
    (см. price_before_discount) — сама скидка не меняется.

    Возвращает список {"vendor_code", "nm_id", "old_final_price",
    "new_final_price", "discount", "price_to_send"} — готово и для печати
    в dry-run, и для сборки items под wb_client.update_prices (нужны
    только "nm_id"/"price_to_send"/"discount" оттуда).
    """
    cards = _cards_by_vendor(wb_data)
    prices_by_nm = _prices_by_nm(wb_data)
    out: List[dict] = []
    for vendor_code, edit in edits.items():
        new_price = edit.get("price")
        if new_price in (None, ""):
            continue
        card = cards.get(vendor_code)
        if not card or not card.get("nmID"):
            logger.warning("%s: нет карточки/nmID в wb_cards.json — цену отправить некуда, пропущено", vendor_code)
            continue
        current = current_wb_price(card, prices_by_nm)
        old_final = current.get("price")
        discount = current.get("discount") or 0
        try:
            new_final = round(float(new_price))
        except (TypeError, ValueError):
            logger.warning("%s: 'Цена WB' = %r не число, пропущено", vendor_code, new_price)
            continue
        if old_final is not None and new_final == round(float(old_final)):
            continue  # не изменилось — нечего отправлять
        out.append(
            {
                "vendor_code": vendor_code,
                "nm_id": card["nmID"],
                "old_final_price": old_final,
                "new_final_price": new_final,
                "discount": discount,
                "price_to_send": price_before_discount(new_final, discount),
            }
        )
    return out


def build_wb_media_updates(wb_data: dict, edits: Dict[str, dict]) -> List[dict]:
    """
    Собирает список {"nm_id":..., "vendor_code":..., "urls": [...]} для
    вызова wb_client.update_media — для строк, где указаны фото И/ИЛИ видео
    в xlsx (обе колонки пустые = не трогаем медиа этого товара вообще).

    ВАЖНО: WB заменяет фото+видео целиком одним списком — если товар уже
    отправлялся с видео, а сейчас меняете только фото, видео нужно оставить
    в колонке "Видео" (не стирать), иначе оно пропадёт при этом запросе.
    Видео, если указано, всегда добавляется В КОНЕЦ списка (после фото).
    """
    cards = _cards_by_vendor(wb_data)
    out: List[dict] = []
    for vendor_code, edit in edits.items():
        images = list(edit.get("images") or [])
        video_url = (edit.get("video_url") or "").strip()
        if not images and not video_url:
            continue
        card = cards.get(vendor_code)
        if not card or not card.get("nmID"):
            continue
        urls = images + ([video_url] if video_url else [])
        out.append({"nm_id": card["nmID"], "vendor_code": vendor_code, "urls": urls})
    return out
