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
    st["A9"] = "Мин. чистая прибыль Авито/сайта, доля от закупки (1 = 100%)"
    st["B9"] = get_min_markup(path)
    st["A10"] = "Дорогие позиции (цена Ozon от, ₽): цену Авито/сайта не пересчитывать"
    st["B10"] = get_freeze_from(path)
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
    # Факт начислений пока НЕ используем (27.10): по товарам из бустинга он даёт провалы до −65%
    # (видимо, захватывает скидки акций) — включить OZON_FACT_PRICING=1 после проверки разбора.
    if os.environ.get("OZON_FACT_PRICING") != "1":
        return out
    try:
        f = json.load(open(os.path.join(_DATA, "ozon_finance.json"), encoding="utf-8"))
        for k, v in (f.get("items") or {}).items():
            sold, rev = int(v.get("sold") or 0), float(v.get("revenue") or 0)
            if sold >= 5 and rev > 0:  # по 1–4 продажам факт шумный (возврат, акция) — тогда тарифы
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


def equal_profit_price(ozon_price: float, net: float, tax: float, fee: float, step: int = 10) -> Optional[int]:
    if not ozon_price or net is None:
        return None
    p = (net - tax * ozon_price) / (1 - tax - fee)
    p = min(p, ozon_price)  # дороже маркетплейса не ставим
    if p <= 0:
        return None
    return int(p / step + 0.5) * step


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


def nice_price(p: float) -> int:
    """
    Сетка владельца: цена стоит чуть ниже круглого уровня, К КОТОРОМУ ИДЁТ (следующего выше):
    к 500 -> 450, к 1000 -> 950, к 1500/2000/... -> 1350/1850/... (9500 -> 9850, 1490 -> 1350);
    от 10 000 — только ...850 к следующей тысяче (12890 -> 12850); от 30 000 — ...500 чуть ниже
    следующей тысячи, а рядом с круглой десяткой тысяч — под ней (~71 000 -> 69 500).
    """
    import math
    if p >= 30000:
        c = math.ceil(p / 1000) * 1000 - 500
        ten = int(p // 10000) * 10000
        if p - ten < 1500 and c >= ten:
            c = ten - 500
        return int(c)
    if p >= 10000:
        return int(math.floor(p / 1000) * 1000 + 1000 - 150)
    lvl = int(p // 500) * 500 + 500
    return int(lvl - 50 if lvl in (500, 1000) else lvl - 150)


MIN_PROFIT = 300  # минимальная прибыль с продажи на Авито/сайте, ₽ (если закупка не указана)
# Реклама Авито/сайта: +10% цены на ВЕСЬ каталог (27.10: сначала было только от 30 000 ₽, затем — на всё).
AD_FROM, AD_SHARE = 0, 0.10
# Пол цены Авито/сайта (27.10): ЧИСТАЯ прибыль (после налога, комиссии канала и рекламы) — не меньше
# этой доли от закупки (1.0 = 100%: закупка 2 000 -> чистыми не меньше 2 000). Лист «Настройки», B9.
DEFAULT_MIN_MARKUP = 1.0
# Дорогие позиции (мехатроники, платы — цена Ozon от этой суммы): цену Авито/сайта НЕ пересчитываем,
# остаётся текущая (решение владельца 27.10: «и так дорого»). Лист «Настройки», B10.
DEFAULT_FREEZE_FROM = 20000


def get_freeze_from(path: str = OVERRIDES_PATH) -> float:
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
        v = float(wb[SETTINGS_SHEET]["B10"].value)
        return v if v > 0 else DEFAULT_FREEZE_FROM
    except Exception:  # noqa: BLE001
        return DEFAULT_FREEZE_FROM


def get_min_markup(path: str = OVERRIDES_PATH) -> float:
    try:
        wb = openpyxl.load_workbook(path, data_only=True)
        v = float(wb[SETTINGS_SHEET]["B9"].value)
        return v if 0 <= v <= 10 else DEFAULT_MIN_MARKUP
    except Exception:  # noqa: BLE001 — нет листа/ячейки -> значение по умолчанию
        return DEFAULT_MIN_MARKUP


def equal_profit_nice(ozon_price: float, net: float, tax: float, fee: float) -> Optional[int]:
    """Равная прибыль с Ozon (для дорогих — с запасом на рекламу) по «красивой» сетке, не дороже Ozon."""
    if not ozon_price or net is None:
        return None
    ad = AD_SHARE if ozon_price >= AD_FROM else 0.0
    target = (net - tax * ozon_price) / (1 - tax - fee - ad)
    if target <= 0:
        return None
    return min(nice_price(target), int(ozon_price))


def price_floor(ozon_price: float, cost: Optional[float], prof: dict, markup: float, raw: bool = False):
    """Минимальная цена Авито/сайта: чистыми (после налога, комиссии, рекламы) не меньше
    markup·закупки и не меньше MIN_PROFIT ₽. raw=True — без округления по сетке."""
    ad = AD_SHARE if ozon_price >= AD_FROM else 0.0
    keep = 1 - prof["tax"] - prof["avito_fee"] - ad
    c = cost or 0.0
    need = (c + max(c * markup, MIN_PROFIT)) / keep
    return need if raw else nice_price(need)


def _below(total: float) -> int:
    """Цена чуть дешевле суммы частей: по сетке, если это не дешевле 85% суммы, иначе сумма −10% до 50 ₽."""
    import math
    grid = [nice_price(x) for x in range(50, int(total), 50)]
    under = [g for g in grid if g < total]
    best = max(under) if under else 0
    if best >= 0.85 * total:
        return best
    return max(50, int(math.floor(total * 0.9 / 50) * 50))


def _cap_kits(plan: list, prof: dict, markup: float) -> None:
    """Правило владельца: комплект дешевле суммы своих частей (состав — data/kits.xlsx).
    Фиксированные цены не трогаем. Если пол прибыли выше суммы частей — оставляем пол и пишем почему."""
    try:
        import kits

        bom = kits.load_bom()
    except Exception:  # noqa: BLE001
        return
    by = {x["art"].lower(): x for x in plan}
    for kit, parts in bom.items():
        x = by.get(kit.lower())
        if not x or x["fixed"] or not x["avito_new"] or len(parts) == 1 and parts[0][1] == 1:
            continue  # 1 деталь ×1 — это тот же товар под другим артикулом, не комплект
        if any(p.lower() not in by or not by[p.lower()]["avito_new"] for p, _ in parts):
            continue
        total = sum(by[p.lower()]["avito_new"] * q for p, q in parts)
        if x["avito_new"] < total:
            continue
        cap = _below(total)
        floor = price_floor(x["ozon_price"], x.get("cost"), prof, markup, raw=True)
        if cap < floor:
            x["src"] += f"; комплект не дешевле частей ({total:g}) — мешает мин. прибыль, проверьте закупку"
            continue
        x["avito_new"] = x["site_new"] = cap
        x["src"] += f"; комплект дешевле частей ({total:g})"


def plan_equal_profit(master_path: str, path: str = OVERRIDES_PATH, prof: Optional[dict] = None) -> list:
    """Расчёт без записи: [{art, name, ozon_price, net, src, avito_old, avito_new, site_old, site_new, fixed}]."""
    from openpyxl import load_workbook

    prof = prof or get_profit_settings(path)
    share, fixed = _read_meta(path)
    premium = get_premium(path)
    av_old, st_old = load_price_overrides(path), load_price_overrides(path, "site")
    models = _ozon_models()
    markup = get_min_markup(path)
    freeze = get_freeze_from(path)
    wb = load_workbook(master_path, read_only=True, data_only=True)
    ws = wb["Товары"] if "Товары" in wb.sheetnames else wb.active
    rows = ws.iter_rows(values_only=True)
    head = [str(c or "") for c in next(rows)]
    c_price, c_name = head.index("Цена, ₽"), head.index("Название товара")
    c_cost = head.index("Закупка, ₽") if "Закупка, ₽" in head else None
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
        try:
            cost = float(r[c_cost]) if c_cost is not None and r[c_cost] not in (None, "") else None
        except (TypeError, ValueError):
            cost = None
        if art in fixed:
            a_new, s_new, src = av_old.get(art), st_old.get(art), "фикс (вручную)"
        elif p >= freeze and av_old.get(art):
            a_new, s_new = av_old.get(art), st_old.get(art) or av_old.get(art)
            src = f"дорогая позиция (Ozon от {freeze:g} ₽) — не пересчитываем"
        else:
            if net is None:
                a_new = site_price(p, share, premium=premium)
                src = "нет данных Ozon — по доле"
            else:
                # одна цена на Авито и сайт (считаем по Авито — у него расходы выше), по сетке владельца
                a_new = equal_profit_nice(p, net, prof["tax"], prof["avito_fee"])
            if a_new:
                # пол: чистая прибыль (после налога, комиссии и рекламы) не меньше markup·закупки;
                # без закупки — не меньше MIN_PROFIT ₽. Дороже Ozon всё равно не ставим.
                floor = price_floor(p, cost, prof, markup)
                if a_new < floor:
                    why = (f"; поднято до чистой прибыли {markup:.0%} от закупки"
                           if cost and cost * markup >= MIN_PROFIT else f"; поднято до мин. прибыли {MIN_PROFIT} ₽")
                    if floor > p:
                        why += " (упёрлось в цену Ozon — Ozon дешевле правила)"
                    a_new = min(floor, int(p))
                    src += why
            s_new = a_new
        out.append({"art": art, "name": r[c_name] or "", "ozon_price": p, "net": net, "src": src,
                    "cost": cost, "avito_old": av_old.get(art), "avito_new": a_new,
                    "site_old": st_old.get(art), "site_new": s_new, "fixed": art in fixed})
    _cap_kits(out, prof, markup)
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
