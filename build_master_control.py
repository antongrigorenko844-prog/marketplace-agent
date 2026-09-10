"""
build_master_control.py — собирает data/master_control.xlsx: единый файл-пульт,
где по каждому артикулу (и уже существующему на Ozon, и новому черновику) в
одной строке видно фото, название, описание, хэштеги и заметки. Это ЕДИНСТВЕННЫЙ
файл, из которого вы редактируете эти поля — после правок запустите
sync-master-control, чтобы перенести их обратно в data/ozon_catalog.xlsx /
data/ozon_new_products.xlsx (и, если заменили картинку в столбце "Фото", в
новый файл-обложку в photos/), см. sync_master_control.py.

master_control.xlsx можно пересобирать сколько угодно раз (build-master-control)
— название/описание/хэштеги/заметки при этом БЕРУТСЯ из ozon_catalog.xlsx /
ozon_new_products.xlsx (то есть уже сделанные там правки не потеряются), а
столбец "Фото" каждый раз строится заново из текущего содержимого photos/.
"""
import logging
import os

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.utils import get_column_letter
from PIL import Image as PILImage

logger = logging.getLogger("marketplace-agent.build_master_control")

ROOT = os.path.dirname(os.path.abspath(__file__))
PHOTOS_DIR = os.path.join(ROOT, "photos")
CATALOG_PATH = os.path.join(ROOT, "data", "ozon_catalog.xlsx")
NEW_PATH = os.path.join(ROOT, "data", "ozon_new_products.xlsx")
MASTER_PATH = os.path.join(ROOT, "data", "master_control.xlsx")

IMAGE_EXTS = {".jpg", ".jpeg", ".png"}
VIDEO_EXTS = {".mp4", ".mov"}
THUMB_DIR = "/tmp/_thumbs_master"
THUMB_PX = 140

HEADERS = [
    "Артикул (offer_id)",
    "Статус",
    "Фото",
    "Кол-во файлов фото/видео",
    "Название товара",
    "Описание",
    "Хэштеги / Теги",
    "Заметки",
    "Файлы (папка photos/)",
]
COL_WIDTHS = [22, 16, 20, 10, 34, 50, 30, 34, 40]


def _read_existing():
    """offer_id -> {name, description, notes, tnved, hashtags, photo_url}."""
    if not os.path.exists(CATALOG_PATH):
        return {}
    wb = openpyxl.load_workbook(CATALOG_PATH)
    ws = wb.active
    h = [c.value for c in ws[1]]
    idx_name = h.index("Название товара")
    idx_desc = h.index("Описание")
    idx_notes = h.index("Заметки")
    idx_tnved = [i for i, x in enumerate(h) if x and "ТН ВЭД" in x][0]
    idx_photo = [i for i, x in enumerate(h) if x and x.startswith("Фото")][0]
    idx_hashtags = next((i for i, x in enumerate(h) if x and "Хэштег" in x), None)

    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        oid = str(row[0]).strip()
        photo_cell = row[idx_photo] or ""
        first_url = photo_cell.split("|")[0].strip() if photo_cell else ""
        out[oid] = {
            "name": row[idx_name] or "",
            "description": row[idx_desc] or "",
            "notes": row[idx_notes] or "",
            "tnved": row[idx_tnved] or "",
            "hashtags": (row[idx_hashtags] or "") if idx_hashtags is not None else "",
            "photo_url": first_url,
        }
    return out


def _read_draft():
    """offer_id -> {name, description, notes, sample, hashtags}."""
    if not os.path.exists(NEW_PATH):
        return {}
    wb = openpyxl.load_workbook(NEW_PATH)
    ws = wb.active
    h = [c.value for c in ws[1]]
    idx_name = next(i for i, x in enumerate(h) if x and "Название" in x)
    idx_desc = next(i for i, x in enumerate(h) if x and "Описание" in x)
    idx_notes = next(i for i, x in enumerate(h) if x and "Заметки" in x)
    idx_sample = next(i for i, x in enumerate(h) if x and "Образец" in x)
    idx_hashtags = next((i for i, x in enumerate(h) if x and "Хэштег" in x), None)

    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        oid = str(row[0]).strip()
        out[oid] = {
            "name": row[idx_name] or "",
            "description": row[idx_desc] or "",
            "notes": row[idx_notes] or "",
            "sample": row[idx_sample] or "",
            "hashtags": (row[idx_hashtags] or "") if idx_hashtags is not None else "",
        }
    return out


def _candidates(fname, known_ids, case_sensitive):
    base, ext = os.path.splitext(fname)
    f_cmp = fname if case_sensitive else fname.lower()
    e_cmp = ext if case_sensitive else ext.lower()
    out = []
    for k in known_ids:
        k_cmp = k if case_sensitive else k.lower()
        if f_cmp == (k_cmp + e_cmp) or f_cmp.startswith(k_cmp + "_") or f_cmp.startswith(k_cmp + "-"):
            out.append(k)
    return out


def _match_known(fname, known_ids):
    # сначала пробуем точное совпадение регистра (важно для DQ500 / Dq500 / DQ500-a)
    exact = _candidates(fname, known_ids, case_sensitive=True)
    if exact:
        return max(exact, key=len)
    loose = _candidates(fname, known_ids, case_sensitive=False)
    if not loose:
        return None
    return max(loose, key=len)


def _make_thumb(src_path, dst_path):
    try:
        img = PILImage.open(src_path)
        img = img.convert("RGB")
        img.thumbnail((THUMB_PX, THUMB_PX))
        img.save(dst_path, "JPEG", quality=80)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось построить эскиз %s: %s", src_path, exc)
        return False


def run() -> int:
    os.makedirs(THUMB_DIR, exist_ok=True)
    os.makedirs(os.path.dirname(MASTER_PATH), exist_ok=True)

    existing = _read_existing()
    draft = _read_draft()
    known_ids = sorted(set(existing) | set(draft))

    media_files = []
    if os.path.isdir(PHOTOS_DIR):
        media_files = [
            f for f in sorted(os.listdir(PHOTOS_DIR))
            if os.path.splitext(f)[1].lower() in (IMAGE_EXTS | VIDEO_EXTS)
        ]

    groups = {}
    unmatched = []
    for f in media_files:
        hit = _match_known(f, known_ids)
        if hit:
            groups.setdefault(hit, []).append(f)
        else:
            unmatched.append(f)

    out = openpyxl.Workbook()
    ws = out.active
    ws.title = "Товары"
    ws.append(HEADERS)
    header_fill = PatternFill(start_color="FFD9D9D9", end_color="FFD9D9D9", fill_type="solid")
    for c in range(1, len(HEADERS) + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(bold=True)
        cell.fill = header_fill
        cell.alignment = Alignment(vertical="center", wrap_text=True)
    for i, w in enumerate(COL_WIDTHS, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    thin = Side(style="thin", color="FFBBBBBB")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    row_i = 2
    for oid in known_ids:
        is_existing = oid in existing
        status = "Действующий" if is_existing else "Новый (черновик)"
        src = existing.get(oid) or draft.get(oid) or {}
        name = src.get("name", "")
        desc = src.get("description", "")
        notes = src.get("notes", "")
        hashtags = src.get("hashtags", "")

        fgroup = groups.get(oid, [])
        img_files = [f for f in fgroup if os.path.splitext(f)[1].lower() in IMAGE_EXTS]
        file_count = len(fgroup)
        if fgroup:
            files_str = ", ".join(fgroup)
        else:
            url = (existing.get(oid) or {}).get("photo_url", "")
            files_str = (
                "фото уже на карточке Ozon, локального файла нет: " + url
                if url else "(нет фото ни локально, ни на карточке)"
            )

        ws.cell(row=row_i, column=1, value=oid)
        ws.cell(row=row_i, column=2, value=status)
        ws.cell(row=row_i, column=4, value=file_count)
        ws.cell(row=row_i, column=5, value=name)
        ws.cell(row=row_i, column=6, value=desc)
        ws.cell(row=row_i, column=7, value=hashtags)
        ws.cell(row=row_i, column=8, value=notes)
        ws.cell(row=row_i, column=9, value=files_str)

        for c in range(1, len(HEADERS) + 1):
            ws.cell(row=row_i, column=c).alignment = Alignment(vertical="top", wrap_text=True)
            ws.cell(row=row_i, column=c).border = border

        if img_files:
            src_path = os.path.join(PHOTOS_DIR, img_files[0])
            thumb_path = os.path.join(THUMB_DIR, oid + ".jpg")
            if _make_thumb(src_path, thumb_path):
                xlimg = XLImage(thumb_path)
                xlimg.width = 100
                xlimg.height = 100
                ws.add_image(xlimg, "C" + str(row_i))

        ws.row_dimensions[row_i].height = 78
        row_i += 1

    ws.freeze_panes = "A2"

    ws2 = out.create_sheet("Нераспознанные фото")
    ws2.append(["Файл", "Похожий артикул", "Комментарий"])
    for c in range(1, 4):
        ws2.cell(row=1, column=c).font = Font(bold=True)
        ws2.cell(row=1, column=c).fill = header_fill
    ws2.column_dimensions["A"].width = 30
    ws2.column_dimensions["B"].width = 30
    ws2.column_dimensions["C"].width = 50
    r = 2
    for f in sorted(unmatched):
        base, _ = os.path.splitext(f)
        prefix = base.split("_")[0] if "_" in base else base.split("-")[0]
        ws2.cell(row=r, column=1, value=f)
        ws2.cell(row=r, column=2, value=prefix)
        r += 1

    out.save(MASTER_PATH)
    print(f"Готово: {MASTER_PATH}")
    print(f"Известных артикулов: {len(known_ids)} (существующих {len(existing)}, черновиков {len(draft)})")
    print(f"Медиафайлов: {len(media_files)}, сопоставлено {sum(len(v) for v in groups.values())} в {len(groups)} товарах")
    if unmatched:
        print(f"Нераспознанных файлов: {len(unmatched)} — см. лист 'Нераспознанные фото' в {MASTER_PATH}")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(run())
