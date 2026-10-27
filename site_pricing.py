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


def load_price_overrides(path: str = OVERRIDES_PATH, channel: str = "avito") -> Dict[str, float]:
    """Цены без комиссии маркетплейса: колонка B — Авито, колонка D — сайт (пусто → как на Авито).
    channel="site" — для Тильды (фид, CommerceML), иначе — Авито."""
    if not os.path.exists(path):
        return {}
    wb = openpyxl.load_workbook(path, data_only=True)
    ws = wb.worksheets[0]
    out: Dict[str, float] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        offer_id = str(row[0]).strip()
        vals = [row[1]]
        if channel == "site" and len(row) > 3:
            vals.insert(0, row[3])
        for v in vals:
            try:
                price = float(v)
            except (TypeError, ValueError):
                continue
            if price > 0:
                out[offer_id] = price
                break
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
                         share: Optional[float] = None, fixed: Optional[set] = None,
                         site: Optional[Dict[str, float]] = None) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    old_share, old_fixed = _read_meta(path)
    prem_thr, prem_share = get_premium(path)
    prof = get_profit_settings(path)
    share = old_share if share is None else share
    fixed = old_fixed if fixed is None else fixed
    if site is None:
        site = {k: v for k, v in load_price_overrides(path, "site").items() if overrides.get(k) != v}
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Цены Avito и сайт"
    ws.append(_HEADER + ["фикс (да = цену не пересчитывать)", "site_price (пусто = как Авито)"])
    for offer_id, price in sorted(overrides.items()):
        sp = site.get(offer_id)
        ws.append([offer_id, price, "да" if offer_id in fixed else None,
                   sp if sp is not None and abs(sp - price) > 0.5 else None])
    st = wb.create_sheet(SETTINGS_SHEET)
    st["A1"] = "Доля от цены маркетплейсов (0.7 = 70%)"
    st["B1"] = share
    st["A2"] = "Округление цены, ₽"
    st["B2"] = 10
    st["A3"] = "Дорогие товары: цена маркетплейса от, ₽"
    st["B3"] = prem_thr
    st["A4"] = "Доля для дорогих товаров (0.6 = 60%)"
    st["B4"] = prem_share
    st["A5"] = "Режим: «доля» (B1/B4 от цены маркетплейса) или «прибыль» (одинаковая прибыль с Ozon)"
    st["B5"] = prof["mode"]
    st["A6"] = "Налог (УСН доходы), доля"
    st["B6"] = prof["tax"]
    st["A7"] = "Комиссия Авито, доля"
    st["B7"] = prof["avito_fee"]
    st["A8"] = "Эквайринг сайта, доля"
    st["B8"] = prof["site_fee"]
    wb.active = 0
    wb.save(path)


DEFAULT_PROFIT = {"mode": "доля", "tax": 0.06, "avito_fee": 0.05, "site_fee": 0.03}


def get_profit_settings(path: str = OVERRIDES_PATH) -> dict:
    """Лист «Настройки»: B5 режим («доля»/«прибыль»), B6 налог, B7 комиссия Авито, B8 эквайринг сайта."""
    out = dict(DEFAULT_PROFIT)
    if not os.path.exists(path):
        return out
    wb = openpyxl.load_workbook(path, data_only=True)
    if SETTINGS_SHEET not in wb.sheetnames:
        return out
    ws = wb[SETTINGS_SHEET]
    m = str(ws["B5"].value or "").strip().lower()
    if m.startswith("приб"):
        out["mode"] = "прибыль"
    for cell, key in (("B6", "tax"), ("B7", "avito_fee"), ("B8", "site_fee")):
        try:
            v = float(ws[cell].value)
            if 0 <= v < 0.5:
                out[key] = v
        except (TypeError, ValueError):
            pass
    return out


# ------------------------------------------------ «одинаковая прибыль на всех каналах»
# Прибыль Ozon с 1 шт = на_руки_Ozon(P) − налог·P − закупка.
# Прибыль Авито/сайта = Ц·(1 − налог − комиссия канала) − закупка.
# Приравниваем: Ц = (на_руки_Ozon(P) − налог·P) / (1 − налог − комиссия канала). Закупка сокращается —
# цена не зависит от неё (закупка нужна только чтобы увидеть, есть ли прибыль вообще).
_DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")


def _ozon_models() -> dict:
    """{offer_id.lower(): (доля_комиссии, фикс_удержания_на_шт (<=0), источник)} — факт из начислений,
    иначе тарифы Ozon из API (логистика — середина между мин. и макс. тарифом)."""
    import json

    out = {}
    try:
        d = json.load(open(os.path.join(_DATA, "ozon_products.json"), encoding="utf-8"))
        for x in d.get("details", []):
            c = x.get("commissions") or {}
            pct = float(c.get("sales_percent_fbs") or 0) / 100
            price = float((x.get("price") or {}).get("price") or 0)
            if pct <= 0 or price <= 0:
                continue
            acq_pct = float(x.get("acquiring") or 0) / price
            logi = (float(c.get("fbs_direct_flow_trans_min_amount") or 0) + float(c.get("fbs_direct_flow_trans_max_amount") or 0)) / 2 \
                + float(c.get("fbs_deliv_to_customer_amount") or 0) + float(c.get("fbs_first_mile_max_amount") or 0)
            out[x["offer_id"].lower()] = (pct + acq_pct, -logi, "тарифы Ozon")
    except Exception:  # noqa: BLE001
        pass
    try:
        f = json.load(open(os.path.join(_DATA, "ozon_finance.json"), encoding="utf-8"))
        for k, v in (f.get("items") or {}).items():
            sold, rev = int(v.get("sold") or 0), float(v.get("revenue") or 0)
            if sold >= 1 and rev > 0:
                pct = -float(v.get("commission") or 0) / rev
                fixed = (float(v.get("logistics") or 0) + float(v.get("services") or 0)) / sold
                # эквайринг в факте сидит в services (фикс на шт) — для другой цены это почти не важно
                out[k.lower()] = (pct, fixed, f"факт Ozon: {sold} продаж")
    except Exception:  # noqa: BLE001
        pass
    return out


def ozon_net(offer_id: str, price: float, models: Optional[dict] = None):
    """(на руки с Ozon за 1 шт при цене price, источник) или (None, "")."""
    m = (models if models is not None else _ozon_models()).get(str(offer_id).lower())
    if not m or not price:
        return None, ""
    pct, fixed, src = m
    return price * (1 - pct) + fixed, src


def pretty_price(p) -> Optional[int]:
    """
    «Красивая» цена по правилу пользователя (2026-10-27): цена стоит чуть ниже
    круглого уровня, к которому идёт. До 1 000: …450 / …950 (к 500 и 1000);
    1 000–9 999: …350 / …850 (1 350, 1 850 … 9 850); от 10 000: только …850
    (10 850, 11 850 …), берётся ближайшая. Пример: 1 490 → 1 350, 9 500 → 9 850,
    44 910 → 44 850; от 50 000 шаг 5 000: 71 310 → 69 850.
    Меньше 450 — без изменений (округление до 10 ₽).
    """
    import math
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    if p <= 0:
        return None
    if p < 450:
        return int(p / 10 + 0.5) * 10
    if p < 10000 and p % 500 == 0:  # ровно на уровне (9 500) — идёт к следующему (9 850)
        return int(p + 500 - (50 if p + 500 <= 1000 else 150))
    # ближайшая из сетки 450, 950, 1 350, 1 850 … 9 850, 10 850, 11 850 …
    grid = [450, 950] + [k * 500 - 150 for k in range(3, 21)]
    top = int(math.ceil(p / 1000)) + 1
    grid += [k * 1000 - 150 for k in range(11, min(top, 50) + 1)]
    # от 50 000 — шаг 5 000: «чуть ниже 70 тысяч» = 69 850 (решение пользователя 2026-10-27)
    grid += [k * 5000 - 150 for k in range(10, top // 5 + 2)]
    return min(grid, key=lambda g: (abs(g - p), -g))


def equal_profit_price(ozon_price: float, net: float, tax: float, fee: float, step: int = 10) -> Optional[int]:
    if not ozon_price or net is None:
        return None
    p = (net - tax * ozon_price) / (1 - tax - fee)
    p = min(p, ozon_price)  # дороже маркетплейса не ставим
    if p <= 0:
        return None
    return pretty_price(p)


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
    return pretty_price(p * share)


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
    prof = get_profit_settings(path)
    if prof["mode"] == "прибыль":
        return _recalc_equal_profit(master_path, path, prof, fixed, dry_run)
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


def plan_equal_profit(master_path: str, path: str = OVERRIDES_PATH, prof: Optional[dict] = None) -> list:
    """Расчёт без записи: [{art, name, ozon_price, net, src, avito_old, avito_new, site_old, site_new, fixed}]."""
    from openpyxl import load_workbook

    prof = prof or get_profit_settings(path)
    share, fixed = _read_meta(path)
    premium = get_premium(path)
    av_old, st_old = load_price_overrides(path), load_price_overrides(path, "site")
    models = _ozon_models()
    wb = load_workbook(master_path, read_only=True, data_only=True)
    ws = wb["Товары"] if "Товары" in wb.sheetnames else wb.active
    rows = ws.iter_rows(values_only=True)
    head = [str(c or "") for c in next(rows)]
    c_price, c_name = head.index("Цена, ₽"), head.index("Название товара")
    out = []
    for r in rows:
        if not r[0]:
            continue
        art = str(r[0]).strip()
        try:
            p = float(r[c_price])
        except (TypeError, ValueError):
            continue
        if p <= 0:
            continue
        net, src = ozon_net(art, p, models)
        if art in fixed:
            a_new, s_new, src = av_old.get(art), st_old.get(art), "фикс (вручную)"
        elif net is None:
            a_new = s_new = site_price(p, share, premium=premium)
            src = "нет данных Ozon — по доле"
        else:
            a_new = equal_profit_price(p, net, prof["tax"], prof["avito_fee"])
            s_new = equal_profit_price(p, net, prof["tax"], prof["site_fee"])
        out.append({"art": art, "name": r[c_name] or "", "ozon_price": p, "net": net, "src": src,
                    "avito_old": av_old.get(art), "avito_new": a_new,
                    "site_old": st_old.get(art), "site_new": s_new, "fixed": art in fixed})
    return out


def _recalc_equal_profit(master_path, path, prof, fixed, dry_run):
    plan = plan_equal_profit(master_path, path, prof)
    ov = load_price_overrides(path)
    site = load_price_overrides(path, "site")
    changes = []
    for x in plan:
        if x["fixed"] or not x["avito_new"]:
            continue
        a = x["art"]
        if x["avito_old"] is None or abs(x["avito_old"] - x["avito_new"]) > 0.5:
            changes.append((a, x["avito_old"], x["avito_new"]))
        if x["site_old"] is None or abs(x["site_old"] - (x["site_new"] or 0)) > 0.5:
            changes.append((a + " (сайт)", x["site_old"], x["site_new"]))
        ov[a] = x["avito_new"]
        site[a] = x["site_new"] or x["avito_new"]
    if changes and not dry_run:
        save_price_overrides(ov, path, fixed=fixed, site=site)
    return changes
