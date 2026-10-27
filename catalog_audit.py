"""
Аудит цен по всему каталогу и всем каналам (ТОЛЬКО ЧТЕНИЕ — ничего не отправляет).

Источники (всё уже лежит в data/ после обычных запусков):
  master_control.xlsx           — цена Ozon (= WB, Маркет), цена WB, закупка, остаток, флаги каналов
  avito_site_price_overrides    — текущие цены Авито / сайта и «фикс»
  ozon_products.json            — фактическая цена на Ozon, тарифы (комиссия/логистика), индекс цен
  ozon_finance.json             — факт начислений (если есть) — точнее тарифов
  wb_cards.json                 — фактическая цена на WB
  site_prices.json              — цены, которые сейчас стоят на сайте
  kits.xlsx                     — состав комплектов (комплект -> детали)

Что проверяет:
  1. Равная прибыль: цена Авито/сайта, при которой прибыль = прибыли с Ozon
     (для товаров от 30 000 ₽ — плюс 10% цены на рекламу), по «красивой» сетке владельца.
  2. Комплекты: комплект дороже суммы деталей / не дороже самой дорогой детали — на Ozon и на сайте.
  3. Дубли: одна и та же деталь в нескольких карточках с разной ценой.
  4. Каналы: WB / сайт / Ozon-факт расходятся с мастером; цена сайта выше Ozon.
  5. Маржа Ozon: убыток / меньше 15%; нет закупки; индекс цен Ozon «невыгодный».

Результат: data/catalog_audit.xlsx (листы «Цены было→станет», «Комплекты», «Дубли», «Каналы»).
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict

import openpyxl
from openpyxl.styles import Alignment, Font, PatternFill

import site_pricing

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(DATA, "catalog_audit.xlsx")
TAX, AVITO_FEE, SITE_FEE = 0.06, 0.05, 0.03
AD_FROM, AD_SHARE = 30000, 0.10
LOW_MARGIN = 0.15


def _num(v):
    try:
        return float(str(v).replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


from site_pricing import nice_price  # noqa: E402  (единая сетка цен — в site_pricing)


def load_master():
    wb = openpyxl.load_workbook(os.path.join(DATA, "master_control.xlsx"), data_only=True)
    ws = wb["Товары"] if "Товары" in wb.sheetnames else wb.active
    rows = list(ws.iter_rows(values_only=True))
    h = [str(c or "") for c in rows[0]]
    ix = {k: h.index(k) for k in h if k}
    out = []
    for r in rows[1:]:
        if not r[0]:
            continue
        g = lambda k: r[ix[k]] if k in ix else None  # noqa: E731
        out.append({
            "art": str(r[0]).strip(),
            "name": g("Название товара") or g("Название WB (до 60 симв.)") or "",
            "ozon_status": g("Статус Ozon"), "wb_status": g("Статус WB"),
            "price": _num(g("Цена, ₽")), "wb_price": _num(g("Цена WB, ₽ (финальная, покупателю)")),
            "stock": _num(g("Остаток, шт.")), "cost": _num(g("Закупка, ₽")),
            "flags": {c: g(f"{c} (да/нет)") for c in ("Ozon", "WB", "Авито", "Маркет", "Сайт")},
        })
    return out


def load_overrides():
    wb = openpyxl.load_workbook(site_pricing.OVERRIDES_PATH, data_only=True)
    ws = wb.worksheets[0]
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        if r and r[0]:
            av = _num(r[1])
            st = _num(r[3]) if len(r) > 3 and r[3] not in (None, "") else av
            out[str(r[0]).strip()] = {"avito": av, "site": st,
                                       "fixed": str(r[2] or "").strip().lower() in ("да", "yes", "1", "фикс")}
    return out


def load_ozon():
    d = json.load(open(os.path.join(DATA, "ozon_products.json"), encoding="utf-8"))
    out = {}
    for x in d.get("details", []):
        p = x.get("price") or {}
        idx = x.get("price_indexes") or {}
        out[str(x.get("offer_id"))] = {
            "price": _num(p.get("price")), "min_price": _num(p.get("min_price")),
            "index": idx.get("color_index") or idx.get("price_index") or "",
        }
    return CaseMap(out)


def load_wb():
    d = json.load(open(os.path.join(DATA, "wb_cards.json"), encoding="utf-8"))
    out = {}
    for x in d.get("prices") or []:
        s = (x.get("sizes") or [{}])[0]
        out[str(x.get("vendorCode"))] = _num(s.get("discountedPrice"))
    return CaseMap(out)


def load_kits():
    wb = openpyxl.load_workbook(os.path.join(DATA, "kits.xlsx"), data_only=True)
    ws = wb.worksheets[0]
    kits = defaultdict(list)
    for r in list(ws.iter_rows(values_only=True))[1:]:
        if r and r[0] and r[2]:
            kits[str(r[0]).strip()].append((str(r[2]).strip(), int(_num(r[3]) or 1)))
    return kits


class CaseMap(dict):
    """Поиск по артикулу: сначала точно, потом без регистра — только если без регистра он однозначен
    (DQ500 и Dq500, EA888gen3 и Ea888gen3 — РАЗНЫЕ товары)."""
    def __init__(self, items):
        super().__init__(items)
        cnt = defaultdict(list)
        for k in self:
            cnt[str(k).lower()].append(k)
        self._low = {lk: ks[0] for lk, ks in cnt.items() if len(ks) == 1}

    def find(self, key, default=None):
        if key in self:
            return self[key]
        k = self._low.get(str(key).lower())
        return self[k] if k is not None else default


def run() -> int:
    master = load_master()
    ov = load_overrides()
    oz = load_ozon()
    wbp = load_wb()
    try:
        site_live = CaseMap({k: v for k, v in json.load(open(os.path.join(DATA, "site_prices.json"), encoding="utf-8")).items()})
    except Exception:  # noqa: BLE001
        site_live = CaseMap({})
    kits = load_kits()
    models = site_pricing._ozon_models()
    by_art = CaseMap({m["art"]: m for m in master})

    price_rows = []
    for m in master:
        art, P = m["art"], m["price"]
        if not P:
            continue
        net, src = site_pricing.ozon_net(art, P, models)
        cost = m["cost"]
        o = ov.get(art, {})
        cur_av, cur_site, fixed = o.get("avito"), o.get("site"), o.get("fixed", False)
        oz_profit = (net - TAX * P - cost) if (net is not None and cost is not None) else None
        new = None
        if net is not None:
            ad = AD_SHARE if P >= AD_FROM else 0.0
            # прибыль до закупки на Ozon = net - налог; на Авито = P*(1-налог-комиссия-реклама)
            target = (net - TAX * P) / (1 - TAX - AVITO_FEE - ad)
            new = min(nice_price(target), int(P))
        site_profit_new = (new * (1 - TAX - SITE_FEE) - cost) if (new and cost is not None) else None
        flags = []
        if cost is None:
            flags.append("нет закупки")
        if oz_profit is not None:
            if oz_profit < 0:
                flags.append("УБЫТОК на Ozon")
            elif oz_profit < LOW_MARGIN * P:
                flags.append("маржа Ozon < 15%")
        if cur_site and cur_site > P:
            flags.append("сайт дороже Ozon")
        live = oz.find(art, {})
        if live.get("index") and str(live["index"]).upper() not in ("WITHOUT_INDEX", "GREEN", "SUPER", "GOOD", ""):
            flags.append(f"индекс цен Ozon: {live['index']}")
        if fixed:
            flags.append("фикс (ручная цена)")
        cur = cur_av if cur_av is not None else cur_site
        change = (new - cur) if (new is not None and cur is not None) else None
        price_rows.append([
            art, m["name"], m["stock"], cost, P,
            round(net) if net is not None else None, src,
            round(oz_profit) if oz_profit is not None else None,
            cur_av, cur_site, new if not fixed else cur,
            change if not fixed else 0,
            round(site_profit_new) if site_profit_new is not None and not fixed else None,
            "; ".join(flags),
        ])

    # --- комплекты
    kit_rows = []
    for kit, parts in kits.items():
        k = by_art.find(kit)
        if not k or not k["price"]:
            continue
        if len(parts) == 1 and parts[0][1] == 1:
            continue  # «комплект» из одной детали (тот же товар под другим артикулом) — не сравниваем
        sum_oz, max_oz, sum_site, max_site, sum_cost, miss = 0.0, 0.0, 0.0, 0.0, 0.0, []
        for part, q in parts:
            p = by_art.find(part)
            if not p or not p["price"]:
                miss.append(part)
                continue
            sum_oz += p["price"] * q
            max_oz = max(max_oz, p["price"])
            ps = (ov.get(p["art"], {}).get("site")) or p["price"]
            sum_site += ps * q
            max_site = max(max_site, ps)
            sum_cost += (p["cost"] or 0) * q
        ks = ov.get(k["art"], {}).get("site")
        probs = []
        if not miss:
            if k["price"] >= sum_oz:
                probs.append(f"Ozon: комплект {int(k['price'])} ≥ деталей вместе {int(sum_oz)}")
            if k["price"] <= max_oz and len(parts) > 1:
                probs.append(f"Ozon: комплект {int(k['price'])} ≤ самой дорогой детали {int(max_oz)}")
            if ks and ks >= sum_site:
                probs.append(f"сайт: комплект {int(ks)} ≥ деталей вместе {int(sum_site)}")
            if ks and ks <= max_site and len(parts) > 1:
                probs.append(f"сайт: комплект {int(ks)} ≤ самой дорогой детали {int(max_site)}")
        if miss:
            probs.append("нет цены у деталей: " + ", ".join(miss))
        kit_rows.append([kit, k["name"], k["stock"], k["price"], int(sum_oz) if not miss else None,
                         ks, int(sum_site) if not miss else None,
                         ", ".join(f"{a}×{q}" for a, q in parts), "; ".join(probs)])

    # --- дубли (одинаковое название без цвета/регистра) с разной ценой
    groups = defaultdict(list)
    for m in master:
        key = re.sub(r"[^a-zа-я0-9]", "", str(m["name"]).lower())
        if key:
            groups[key].append(m)
    dup_rows = []
    for g in groups.values():
        prices = {m["price"] for m in g if m["price"]}
        if len(g) > 1 and len(prices) > 1:
            for m in g:
                dup_rows.append([m["art"], m["name"], m["price"], m["wb_price"], m["stock"],
                                 m["ozon_status"], m["wb_status"]])
            dup_rows.append([None] * 7)

    # --- каналы: факт против мастера
    ch_rows = []
    for m in master:
        art, P = m["art"], m["price"]
        if not P:
            continue
        issues = []
        live_oz = oz.find(art, {}).get("price")
        if live_oz and abs(live_oz - P) > 1:
            issues.append(f"Ozon факт {int(live_oz)} ≠ мастер {int(P)}")
        live_wb = wbp.find(art)
        if live_wb and abs(live_wb - P) > max(5, 0.01 * P):
            issues.append(f"WB факт {int(live_wb)} ≠ Ozon {int(P)}")
        if m["wb_price"] and abs(m["wb_price"] - P) > max(5, 0.01 * P):
            issues.append(f"WB в мастере {int(m['wb_price'])} ≠ Ozon {int(P)}")
        sl = site_live.find(art)
        so = ov.get(art, {}).get("site")
        if sl and so and abs(sl - so) > 1:
            issues.append(f"сайт сейчас {int(sl)}, по таблице {int(so)} (обновится при синхронизации)")
        if issues:
            ch_rows.append([art, m["name"], m["stock"], "; ".join(issues)])

    # --- запись
    wb = openpyxl.Workbook()
    def sheet(title, head, rows, widths):
        ws = wb.create_sheet(title)
        ws.append(head)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="FFF2CC")
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for r in rows:
            ws.append(r)
        for i, w in enumerate(widths, start=1):
            ws.column_dimensions[openpyxl.utils.get_column_letter(i)].width = w
        ws.freeze_panes = "C2"
        return ws
    wb.remove(wb.active)
    price_rows.sort(key=lambda r: (r[11] is None, -(abs(r[11] or 0) * (r[2] or 0))))
    sheet("Цены было→станет",
          ["Артикул", "Название", "Остаток", "Закупка", "Цена Ozon/WB/Маркет", "Ozon на руки с 1 шт",
           "Источник расходов Ozon", "Прибыль Ozon", "Авито сейчас", "Сайт сейчас",
           "Авито/сайт станет (равная прибыль)", "Изменение", "Прибыль сайт станет", "Флаги"],
          price_rows, [16, 48, 8, 9, 11, 11, 16, 10, 10, 10, 13, 10, 11, 40])
    sheet("Комплекты", ["Комплект", "Название", "Остаток", "Ozon комплект", "Ozon детали вместе",
                        "Сайт комплект", "Сайт детали вместе", "Состав", "Проблемы"],
          kit_rows, [20, 48, 8, 11, 11, 11, 11, 40, 50])
    sheet("Дубли", ["Артикул", "Название", "Цена Ozon", "Цена WB", "Остаток", "Статус Ozon", "Статус WB"],
          dup_rows, [18, 60, 10, 10, 8, 14, 14])
    sheet("Каналы", ["Артикул", "Название", "Остаток", "Расхождения"], ch_rows, [18, 55, 8, 80])
    wb.save(OUT)

    n_change = sum(1 for r in price_rows if r[11])
    print(f"Товаров с ценой: {len(price_rows)}, цена Авито/сайта поменяется у {n_change}")
    print(f"Комплектов с проблемами: {sum(1 for r in kit_rows if r[8])} из {len(kit_rows)}")
    print(f"Дублей с разной ценой (строк): {sum(1 for r in dup_rows if r[0])}")
    print(f"Расхождений по каналам: {len(ch_rows)}")
    print(f"-> {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
