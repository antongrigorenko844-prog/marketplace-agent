"""
Отдельные (более низкие) цены для Avito и сайта (Тильда) — решение
пользователя 2026-09-17: "на маркетплейсы пишу цену например 1950 р а на
авито и сайт ты должен делить цену на 2". Применяется ТОЛЬКО к НОВЫМ
товарам вперёд (существующий каталог трогать не нужно) — поэтому это
отдельный, необязательный оверрайд-файл, а не изменение общей цены в
ozon_catalog.xlsx (та цена — для Ozon/WB, её трогать нельзя).

Файл-оверрайд: data/avito_site_price_overrides.xlsx, колонки offer_id ->
price. avito_feed.py и tilda_feed.py читают его и, если для товара есть
запись — используют ЭТУ цену вместо общей цены из ozon_catalog.xlsx (только
для Avito/сайта; Ozon/WB не затрагиваются). Если записи нет — поведение как
раньше (единая цена для всех площадок), то есть для всего каталога,
созданного ДО этого решения, ничего не меняется.

На сайте (Тильда) "цена до скидки" для товаров с оверрайдом НЕ выводится
вообще (решение пользователя: "убрать совсем" — раз это отдельный, более
выгодный канал, зачёркнутая маркетплейсовая цена тут не нужна и будет
вводить в заблуждение).

Записи в оверрайд-файл добавляются АВТОМАТИЧЕСКИ при реальном создании
нового товара (main.py --push-new-product-all, не dry-run) — цена/2,
округлено до целого рубля. Ручное значение в файле (проставленное
пользователем вручную) никогда не перезаписывается автоматически — см.
save_price_override(overwrite=False по умолчанию).
"""
import logging
import os
from typing import Dict, Optional

import openpyxl

logger = logging.getLogger("marketplace-agent.site_pricing")

OVERRIDES_PATH = os.path.join(os.path.dirname(__file__), "data", "avito_site_price_overrides.xlsx")
_HEADER = ["offer_id", "avito_site_price"]


def load_price_overrides(path: str = OVERRIDES_PATH) -> Dict[str, float]:
    if not os.path.exists(path):
        return {}
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.active
    out: Dict[str, float] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        offer_id = str(row[0]).strip()
        try:
            price = float(row[1])
        except (TypeError, ValueError):
            continue
        if price > 0:
            out[offer_id] = price
    return out


SETTINGS_SHEET = "Настройки"
DEFAULT_SHARE = 0.70  # цена Авито/сайта = доля от цены на маркетплейсах (решение пользователя 2026-10-21)


def _read_meta(path: str = OVERRIDES_PATH):
    """(доля, множество offer_id с ручной ценой). Ручная цена — «да» в колонке C листа с ценами."""
    share, fixed = DEFAULT_SHARE, set()
    if not os.path.exists(path):
        return share, fixed
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0] and len(row) > 2 and str(row[2] or "").strip().lower() in ("да", "yes", "1", "фикс"):
            fixed.add(str(row[0]).strip())
    if SETTINGS_SHEET in wb.sheetnames:
        try:
            v = float(wb[SETTINGS_SHEET]["B1"].value)
            if 0.1 <= v <= 1.0:
                share = v
        except (TypeError, ValueError):
            pass
    return share, fixed


DEFAULT_PREMIUM_FROM = 50000   # цена маркетплейса от этой суммы — «дорогой» товар
DEFAULT_PREMIUM_SHARE = 0.60  # доля для дорогих товаров (решение пользователя 2026-10-21)


def get_share(path: str = OVERRIDES_PATH) -> float:
    return _read_meta(path)[0]


def get_premium(path: str = OVERRIDES_PATH):
    """(порог «дорогого» товара, доля для дорогих) — лист «Настройки», ячейки B3 и B4."""
    thr, share = DEFAULT_PREMIUM_FROM, DEFAULT_PREMIUM_SHARE
    if os.path.exists(path):
        wb = openpyxl.load_workbook(path, data_only=True)
        if SETTINGS_SHEET in wb.sheetnames:
            ws = wb[SETTINGS_SHEET]
            try:
                t = float(ws["B3"].value)
                if t > 0:
                    thr = t
            except (TypeError, ValueError):
                pass
            try:
                v = float(ws["B4"].value)
                if 0.1 <= v <= 1.0:
                    share = v
            except (TypeError, ValueError):
                pass
    return thr, share


def save_price_overrides(overrides: Dict[str, float], path: str = OVERRIDES_PATH,
                         share: Optional[float] = None, fixed: Optional[set] = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old_share, old_fixed = _read_meta(path)
    prem_thr, prem_share = get_premium(path)
    share = old_share if share is None else share
    fixed = old_fixed if fixed is None else fixed
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Цены Avito и сайт"
    ws.append(_HEADER + ["фикс (да = цену не пересчитывать)"])
    for offer_id, price in sorted(overrides.items()):
        ws.append([offer_id, price, "да" if offer_id in fixed else None])
    st = wb.create_sheet(SETTINGS_SHEET)
    st["A1"] = "Доля от цены маркетплейсов (0.7 = 70%)"
    st["B1"] = share
    st["A2"] = "Округление цены, ₽"
    st["B2"] = 10
    st["A3"] = "Дорогие товары: цена маркетплейса от, ₽"
    st["B3"] = prem_thr
    st["A4"] = "Доля для дорогих товаров (0.6 = 60%)"
    st["B4"] = prem_share
    wb.active = 0
    wb.save(path)


def set_price_override(
    offer_id: str,
    price: float,
    path: str = OVERRIDES_PATH,
    overwrite: bool = False,
) -> bool:
    """
    Добавляет/обновляет цену для Avito/сайта для одного товара. По
    умолчанию НЕ перезаписывает уже существующую запись (overwrite=False) —
    чтобы случайный повторный запуск push-new-product-all не затёр цену,
    которую пользователь мог поправить вручную. Возвращает True, если
    запись реально была добавлена/изменена.
    """
    overrides = load_price_overrides(path)
    if offer_id in overrides and not overwrite:
        return False
    overrides[offer_id] = price
    save_price_overrides(overrides, path)
    return True


def site_price(price, share: Optional[float] = None, step: int = 10,
               premium: Optional[tuple] = None) -> Optional[float]:
    """
    Цена Авито/сайта = цена маркетплейса * доля, округление до step ₽ (None, если цена неизвестна).
    Для дорогих товаров (от порога) действует своя, меньшая доля (лист «Настройки» B3/B4).
    """
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    share = get_share() if share is None else share
    thr, prem_share = premium if premium is not None else get_premium()
    if p >= thr:
        share = prem_share
    return int(p * share / step + 0.5) * step


def half_price(price) -> Optional[float]:
    """Историческое имя: теперь считает по доле из файла (по умолчанию 70%), а не пополам."""
    return site_price(price)


def recalc_from_master(master_path: str, path: str = OVERRIDES_PATH, dry_run: bool = False):
    """
    Пересчитать цены Авито/сайта для ВСЕХ товаров мастера: цена «Цена, ₽» * доля (округление до 10 ₽),
    кроме строк с «да» в колонке «фикс». Возвращает список (offer_id, было, стало) по изменившимся.
    """
    from openpyxl import load_workbook

    share, fixed = _read_meta(path)
    premium = get_premium(path)
    ov = load_price_overrides(path)
    wb = load_workbook(master_path, read_only=True, data_only=True)
    ws = wb["Товары"] if "Товары" in wb.sheetnames else wb.active
    rows = ws.iter_rows(values_only=True)
    head = [str(c or "") for c in next(rows)]
    c_price = head.index("Цена, ₽")
    changes = []
    for r in rows:
        art = r[0]
        if not art:
            continue
        art = str(art).strip()
        if art in fixed:
            continue
        new = site_price(r[c_price], share, premium=premium)
        if new is None:
            continue
        old = ov.get(art)
        if old is None or abs(old - new) > 0.5:
            changes.append((art, old, new))
            ov[art] = new
    if changes and not dry_run:
        save_price_overrides(ov, path, share=share, fixed=fixed)
    return changes
