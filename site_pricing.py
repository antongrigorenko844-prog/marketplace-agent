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


def save_price_overrides(overrides: Dict[str, float], path: str = OVERRIDES_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Цены Avito и сайт"
    ws.append(_HEADER)
    for offer_id, price in sorted(overrides.items()):
        ws.append([offer_id, price])
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


def half_price(price) -> Optional[float]:
    """round(price/2) до целого рубля, либо None если цена неизвестна/некорректна."""
    try:
        p = float(price)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    return round(p / 2)
