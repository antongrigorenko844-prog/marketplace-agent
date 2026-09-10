"""
sync_master_control.py — переносит правки из ЕДИНОГО файла-пульта
data/master_control.xlsx обратно в рабочие файлы конвейера, отдельно для
каждой площадки:
  - data/ozon_catalog.xlsx        (строки со статусом Ozon "Действующий")
  - data/ozon_new_products.xlsx   (строки со статусом Ozon "Новый (черновик)")
  - data/wb_catalog.xlsx          (строки со статусом WB "Действующий")
  - data/wb_new_products.xlsx     (строки со статусом WB "Новый (черновик)")
а также, если в столбце "Фото" картинку заменили на другую, сохраняет
новую картинку как обложку в photos/<артикул>_1.<расширение> (общая для
обеих площадок — конвейер сам заливает её и на Ozon, и на WB).

ВАЖНО: цену для уже существующих на WB товаров эта функция НЕ отправляет
никуда — в конвейере пока нет отправки цены на WB для готовых карточек
(см. build_master_control.py). Столбец "Цена WB" читается обратно только
для строк со статусом WB "Новый (черновик)".

master_control.xlsx сам НЕ используется ни одним другим шагом конвейера —
он только человеко-читаемый обзор + место для правок. Реальные данные,
которые уходят в Ozon/WB, всегда лежат в ozon_catalog.xlsx / ozon_new_products.xlsx
/ wb_catalog.xlsx / wb_new_products.xlsx — эта функция и есть "мост" между
ними: после правок в master_control.xlsx запустите sync-master-control, а
дальше конвейер работает как обычно (attach-ozon-photos при смене обложки ->
push-ozon-cards-dryrun -> push-ozon-cards, и аналогично attach-ozon-new-photos
-> push-ozon-new-cards-dryrun -> push-ozon-new-cards для черновиков Ozon;
для WB — свои шаги push-wb-cards / push-wb-new-cards).

Столбцы master_control.xlsx (см. build_master_control.py), по позиции:
  1  Артикул (offer_id / vendorCode)
  2  Статус Ozon ("Действующий" / "Новый (черновик)")
  3  Статус WB ("Действующий" / "Новый (черновик)")
  4  Фото (картинка, вставленная в ячейку)
  5  Кол-во файлов фото/видео (справочно, не читается обратно)
  6  Название товара (общее, уходит в Ozon)
  7  Название WB (до 60 симв.) (отдельное, уходит в WB)
  8  Цена, ₽ (Ozon)
  9  Цена до скидки, ₽ (Ozon)
  10 Остаток, шт. (Ozon — "Кол-во к продаже" в ozon_catalog.xlsx/ozon_new_products.xlsx;
     реально уходит в Ozon только отдельной командой push-stock, см. main.py)
  11 Цена WB, ₽ (только для новых WB-товаров)
  12 Описание
  13 Хэштеги / Теги (только Ozon — у WB такого поля нет)
  14 Заметки
  15 Файлы (папка photos/) (справочно, не читается обратно)

Пустая ячейка в текстовых/числовых столбцах означает "не менять" — как и
везде в этом проекте, обнулить значение так нельзя, для явной очистки
впишите один пробел. Для цен пустая ячейка тоже значит "не менять"; число
0 — это явное значение и будет записано (для "Цена до скидки" 0 означает
"без скидки").
"""
import hashlib
import logging
import os
import re

from openpyxl import load_workbook

logger = logging.getLogger("marketplace-agent.sync_master_control")

ROOT = os.path.dirname(os.path.abspath(__file__))
MASTER_PATH = os.path.join(ROOT, "data", "master_control.xlsx")
CATALOG_PATH = os.path.join(ROOT, "data", "ozon_catalog.xlsx")
NEW_PATH = os.path.join(ROOT, "data", "ozon_new_products.xlsx")
WB_CATALOG_PATH = os.path.join(ROOT, "data", "wb_catalog.xlsx")
WB_NEW_PATH = os.path.join(ROOT, "data", "wb_new_products.xlsx")
PHOTOS_DIR = os.path.join(ROOT, "photos")

STATUS_EXISTING = "Действующий"
STATUS_DRAFT = "Новый (черновик)"

# позиции столбцов в master_control.xlsx (1-based) — см. build_master_control.py
COL_OFFER_ID = 1
COL_STATUS_OZON = 2
COL_STATUS_WB = 3
COL_PHOTO = 4
COL_NAME = 6
COL_NAME_WB = 7
COL_PRICE = 8
COL_OLD_PRICE = 9
COL_QUANTITY = 10
COL_PRICE_WB = 11
COL_DESCRIPTION = 12
COL_HASHTAGS = 13
COL_NOTES = 14

IMAGE_EXTS = (".jpg", ".jpeg", ".png")


def _read_master():
    """
    Возвращает (rows, images):
      rows   — offer_id -> {
                 "status_ozon", "status_wb",
                 "name", "name_wb",
                 "price", "old_price", "price_wb",
                 "description", "hashtags", "notes",
               }
      images — offer_id -> raw bytes картинки, вставленной в столбец "Фото"
    """
    wb = load_workbook(MASTER_PATH)
    ws = wb["Товары"] if "Товары" in wb.sheetnames else wb.active

    row_to_offer = {}
    rows = {}
    for row in ws.iter_rows(min_row=2):
        offer_cell = row[COL_OFFER_ID - 1]
        if not offer_cell.value:
            continue
        offer_id = str(offer_cell.value).strip()
        row_idx = offer_cell.row
        row_to_offer[row_idx] = offer_id
        rows[offer_id] = {
            "status_ozon": str(row[COL_STATUS_OZON - 1].value or "").strip(),
            "status_wb": str(row[COL_STATUS_WB - 1].value or "").strip(),
            "name": row[COL_NAME - 1].value,
            "name_wb": row[COL_NAME_WB - 1].value,
            "price": row[COL_PRICE - 1].value,
            "old_price": row[COL_OLD_PRICE - 1].value,
            "quantity": row[COL_QUANTITY - 1].value,
            "price_wb": row[COL_PRICE_WB - 1].value,
            "description": row[COL_DESCRIPTION - 1].value,
            "hashtags": row[COL_HASHTAGS - 1].value,
            "notes": row[COL_NOTES - 1].value,
        }

    images = {}
    for img in getattr(ws, "_images", []):
        anchor = getattr(img, "anchor", None)
        row0 = None
        if anchor is not None and hasattr(anchor, "_from") and anchor._from is not None:
            row0 = anchor._from.row
        elif isinstance(anchor, str):
            m = re.match(r"[A-Za-z]+(\d+)", anchor)
            if m:
                row0 = int(m.group(1)) - 1
        if row0 is None:
            continue
        offer_id = row_to_offer.get(row0 + 1)
        if not offer_id:
            continue
        try:
            data = img._data()
        except Exception as exc:  # noqa: BLE001 — одна нечитаемая картинка не должна ронять весь sync
            logger.warning("Не удалось прочитать картинку в строке %s (%s): %s", row0 + 1, offer_id, exc)
            continue
        images[offer_id] = data

    return rows, images


def _guess_ext(data: bytes) -> str:
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if data[:3] == b"\xff\xd8\xff":
        return ".jpg"
    return ".png"


def _match_candidates(fname: str, offer_ids, case_sensitive: bool):
    base, ext = os.path.splitext(fname)
    f_cmp = fname if case_sensitive else fname.lower()
    e_cmp = ext if case_sensitive else ext.lower()
    out = []
    for k in offer_ids:
        k_cmp = k if case_sensitive else k.lower()
        if f_cmp == (k_cmp + e_cmp) or f_cmp.startswith(k_cmp + "_") or f_cmp.startswith(k_cmp + "-"):
            out.append(k)
    return out


def _match_known(fname: str, offer_ids):
    exact = _match_candidates(fname, offer_ids, case_sensitive=True)
    if exact:
        return max(exact, key=len)
    loose = _match_candidates(fname, offer_ids, case_sensitive=False)
    if not loose:
        return None
    return max(loose, key=len)


def _thumb_source_photo(offer_id: str, all_offer_ids):
    """
    Файл, из которого build_master_control.py строит маленький эскиз в
    столбце "Фото" для этого артикула — нужен, чтобы пересобрать такой же
    эскиз и сравнить с картинкой, вставленной в ячейку (см. _make_thumb_bytes).
    Повторяет ТУ ЖЕ логику подбора, что и сам build-скрипт: артикулы и файлы
    перебираются в алфавитном порядке имени файла, совпадение — сначала
    точное по регистру, затем без учёта регистра, при нескольких кандидатах
    побеждает самый длинный префикс (чтобы "0am325025H" не перехватывал
    файлы артикула "0am325025HX").
    """
    if not os.path.isdir(PHOTOS_DIR):
        return None
    files = sorted(os.listdir(PHOTOS_DIR))
    media = [f for f in files if os.path.splitext(f)[1].lower() in IMAGE_EXTS]
    for f in media:
        if _match_known(f, all_offer_ids) == offer_id:
            return f
    return None


def _make_thumb_bytes(path: str):
    """
    Пересобирает эскиз ТОЧНО так же, как build_master_control.py (140x140,
    JPEG качество 80) — чтобы сравнить с картинкой, вставленной в ячейку.
    Нужно потому, что в master_control.xlsx хранится не оригинал фото, а
    его маленькая уменьшенная копия для просмотра; сравнивать напрямую
    оригинал с этой копией нельзя — они разного размера и формата и НИКОГДА
    не совпадут побайтово, даже если пользователь ничего не менял.
    """
    try:
        from PIL import Image as PILImage
        import io

        img = PILImage.open(path).convert("RGB")
        img.thumbnail((140, 140))
        buf = io.BytesIO()
        img.save(buf, "JPEG", quality=80)
        return buf.getvalue()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось пересобрать эскиз %s для сравнения: %s", path, exc)
        return None


def _sync_photos(images: dict, all_offer_ids) -> list:
    """
    Для каждой картинки, вставленной в столбец "Фото" master_control.xlsx,
    сравнивает её с ЗАНОВО ПОСТРОЕННЫМ эскизом текущей обложки (а не с самой
    обложкой — см. _make_thumb_bytes). Совпал эскиз -> пользователь ничего
    не менял, пропускаем. Не совпал -> пользователь вставил другую картинку,
    сохраняем её как новую обложку <артикул>_1.<ext>. Старый файл обложки
    (если он назывался иначе) НЕ удаляется — на всякий случай, чтобы никогда
    не потерять фото молча; название выводится в консоль, чтобы вы проверили
    вручную, не остался ли он лишним.
    Возвращает список (offer_id, новый_файл, старый_файл_или_None).
    """
    os.makedirs(PHOTOS_DIR, exist_ok=True)
    written = []
    for offer_id, data in images.items():
        new_hash = hashlib.sha256(data).hexdigest()

        current = _thumb_source_photo(offer_id, all_offer_ids)
        if current:
            expected_thumb = _make_thumb_bytes(os.path.join(PHOTOS_DIR, current))
            if expected_thumb is not None and hashlib.sha256(expected_thumb).hexdigest() == new_hash:
                continue  # это тот же автосгенерированный эскиз — фото не меняли

        ext = _guess_ext(data)
        target_name = f"{offer_id}_1{ext}"
        with open(os.path.join(PHOTOS_DIR, target_name), "wb") as fh:
            fh.write(data)
        written.append((offer_id, target_name, current))
    return written


def _sync_xlsx(path: str, columns_module, updates: dict, field_map: dict) -> int:
    """
    Точечно обновляет поля по offer_id/vendorCode в уже существующем xlsx,
    не трогая остальные колонки (ссылки на фото, остаток, ТН ВЭД, размеры
    и т.д.) — они остаются как были.

    field_map: {ключ_в_columns_module.COLUMNS: ключ_в_updates[...]} —
    позволяет использовать одну и ту же функцию и для Ozon-файлов (где
    "название" в master_control лежит под ключом "name"), и для WB-файлов
    (где то же самое поле называется "title" и берётся из master_control-
    ключа "name_wb").
    """
    if not updates or not os.path.exists(path):
        return 0

    col_index = {key: i for i, (key, _) in enumerate(columns_module.COLUMNS, start=1)}
    wb = load_workbook(path)
    ws = wb.active

    changed = 0
    for row in ws.iter_rows(min_row=2):
        offer_cell = row[0]
        if not offer_cell.value:
            continue
        offer_id = str(offer_cell.value).strip()
        upd = updates.get(offer_id)
        if not upd:
            continue
        row_changed = False
        for dest_key, src_key in field_map.items():
            col_idx = col_index.get(dest_key)
            if not col_idx:
                continue
            new_val = upd.get(src_key)
            if new_val in (None, ""):
                continue  # пусто в master_control = "не менять" (0 — явное значение, применяется)
            cell = row[col_idx - 1]
            if cell.value != new_val:
                cell.value = new_val
                row_changed = True
        if row_changed:
            changed += 1

    if changed:
        wb.save(path)
    return changed


# сопоставление полей master_control.xlsx -> колонки целевых файлов конвейера
_OZON_FIELD_MAP = {
    "name": "name",
    "price": "price",
    "old_price": "old_price",
    "quantity_to_sell": "quantity",
    "description": "description",
    "hashtags": "hashtags",
    "notes": "notes",
}
_WB_EXISTING_FIELD_MAP = {
    "title": "name_wb",
    "description": "description",
    "notes": "notes",
}
_WB_DRAFT_FIELD_MAP = {
    "title": "name_wb",
    "price": "price_wb",
    "description": "description",
    "notes": "notes",
}

# Должно совпадать с WB_ALIAS в build_master_control.py — там же и
# пояснение, что это за пары и почему они добавлены вручную. Там ключ —
# vendorCode на WB, значение — offer_id на Ozon, под которым эта строка
# показана в master_control.xlsx; здесь нужна обратная связка, чтобы найти
# правильную строку в wb_catalog.xlsx/wb_new_products.xlsx (её ключ —
# настоящий vendorCode, а не Ozon offer_id).
_WB_ALIAS_OZON_TO_VENDOR = {
    "0am325477ae": "0am325477",
    "02e305045": "02E305045E",
}


def _remap_wb_keys(updates: dict) -> dict:
    return {_WB_ALIAS_OZON_TO_VENDOR.get(oid, oid): upd for oid, upd in updates.items()}


def run() -> int:
    if not os.path.exists(MASTER_PATH):
        print(f"Нет файла {MASTER_PATH} — сначала создайте его (build_master_control.py) и загрузите в репозиторий.")
        return 1

    import catalog_editor
    import ozon_new_products
    import wb_catalog_editor
    import wb_new_products

    rows, images = _read_master()

    existing_updates = {oid: r for oid, r in rows.items() if r["status_ozon"] == STATUS_EXISTING}
    draft_updates = {oid: r for oid, r in rows.items() if r["status_ozon"] == STATUS_DRAFT}
    wb_existing_updates = _remap_wb_keys({oid: r for oid, r in rows.items() if r["status_wb"] == STATUS_EXISTING})
    wb_draft_updates = _remap_wb_keys({oid: r for oid, r in rows.items() if r["status_wb"] == STATUS_DRAFT})

    changed_existing = _sync_xlsx(CATALOG_PATH, catalog_editor, existing_updates, _OZON_FIELD_MAP)
    changed_draft = _sync_xlsx(NEW_PATH, ozon_new_products, draft_updates, _OZON_FIELD_MAP)
    changed_wb_existing = _sync_xlsx(WB_CATALOG_PATH, wb_catalog_editor, wb_existing_updates, _WB_EXISTING_FIELD_MAP)
    changed_wb_draft = _sync_xlsx(WB_NEW_PATH, wb_new_products, wb_draft_updates, _WB_DRAFT_FIELD_MAP)

    written_photos = _sync_photos(images, set(rows.keys()))

    print(f"master_control.xlsx -> ozon_catalog.xlsx: обновлено строк {changed_existing}")
    print(f"master_control.xlsx -> ozon_new_products.xlsx: обновлено строк {changed_draft}")
    print(f"master_control.xlsx -> wb_catalog.xlsx: обновлено строк {changed_wb_existing}")
    print(f"master_control.xlsx -> wb_new_products.xlsx: обновлено строк {changed_wb_draft}")
    if written_photos:
        print(f"Обложки обновлены/добавлены в photos/ ({len(written_photos)}):")
        for oid, fname, old_file in written_photos:
            note = ""
            if old_file and old_file != fname:
                note = f"  (проверьте, не остался ли лишним старый файл {old_file})"
            print(f"  {oid}: {fname}{note}")
    else:
        print("Изменённых обложек в столбце 'Фото' не найдено.")

    print(
        "\nГотово. Дальше как обычно: attach-ozon-photos (если менялись обложки существующих) "
        "-> push-ozon-cards-dryrun -> push-ozon-cards; для черновиков Ozon — "
        "attach-ozon-new-photos -> push-ozon-new-cards-dryrun -> push-ozon-new-cards; "
        "для WB — свои шаги push-wb-cards / push-wb-new-cards."
    )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(run())
