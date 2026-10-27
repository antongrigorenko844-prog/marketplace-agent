"""
Ревизия цен и прибыли по всем каналам.

Две команды:
  ozon-finance  — фактические начисления Ozon за последние N дней (/v1/finance/accrual/by-day):
                  по каждому товару — сколько продано, выручка и сколько реально пришло на руки.
                  Пишет data/ozon_finance.json.
  price-audit   — сводная таблица data/price_audit.xlsx: закупка, цена и прибыль на Ozon
                  (факт из начислений, иначе оценка по тарифам Ozon), на Авито и на сайте,
                  флаги «убыток», «низкая маржа», «комплект дешевле детали», «дубль с другой ценой».

Закупочная цена — колонка «Закупка, ₽» в master_control.xlsx (единый источник). Если там пусто,
берётся «себестоимость» из кабинета Ozon (net_price), и она же вписывается в мастер, чтобы
дальше править её в одном месте.
"""
from __future__ import annotations

import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FIN_PATH = os.path.join(DATA, "ozon_finance.json")
AUDIT_PATH = os.path.join(DATA, "price_audit.xlsx")
MASTER = os.path.join(DATA, "master_control.xlsx")
COST_HEADER = "Закупка, ₽"

TAX = 0.06          # УСН «доходы» — со слов владельца
AVITO_FEE = 0.05    # комиссия Авито — со слов владельца
SITE_FEE = 0.03     # эквайринг сайта — допущение, уточнить
LOW_MARGIN = 0.15   # прибыль меньше 15% цены — «низкая маржа»


# ---------------------------------------------------------------- Ozon: фактические начисления
# Ozon отключил /v3/finance/transaction/list (с сентября 2026 — 404/400 «obsolete method»).
# Замена — /v1/finance/accrual/by-day: только ОДНА дата за запрос ({"date": "YYYY-MM-DD"}),
# страницы курсором last_id. Категории начислений:
#   POSTING  — отправление: posting.products[] {sku, quantity, commission{seller_price, sale_commission},
#              delivery{total_accrued, services[]}}; total_amount = на руки по отправлению;
#   ITEM     — удержания по товару (эквайринг и т. п.): item_fees.fees[] {sku, quantity, fees[{type_id, accrued}]};
#   NON_ITEM — без товара (реклама, подписка, хранение): non_item_fee {type_id, accrued}.
# Названия видов — справочник /v1/finance/accrual/types. Полной схемы в открытой документации нет,
# поэтому в ozon_finance.json пишутся образцы каждой категории — по ним сверяем разбор.
def _money(v) -> float:
    if isinstance(v, dict):
        v = v.get("amount", v.get("value"))
    s = re.sub(r'^value:"?|"$', "", str(v if v is not None else 0))
    try:
        return float(s.replace(" ", "").replace("\xa0", "").replace(",", "."))
    except ValueError:
        return 0.0


def _accrual_types(ozon_client) -> dict:
    try:
        r = ozon_client._post("/v1/finance/accrual/types", {})
    except Exception:  # noqa: BLE001
        return {}
    lst = r.get("accrual_types") or (r.get("result") or {}).get("accrual_types") or r.get("types") or []
    return {str(t.get("id")): (t.get("description") or t.get("name") or "") for t in lst}


def _accruals_by_day(ozon_client, day: str) -> list:
    out, last_id = [], ""
    for _ in range(100):
        body = {"date": day}
        if last_id:
            body["last_id"] = last_id
        r = ozon_client._post("/v1/finance/accrual/by-day", body)
        lst = r.get("accruals") or (r.get("result") or {}).get("accruals") or []
        out += lst
        last_id = r.get("last_id") or (r.get("result") or {}).get("last_id") or ""
        if not last_id or not lst:
            break
    return out


# SKU для сверки разбора (0am325025l, 122390AK-AF, 0am325066AC): выручка/шт ниже цены в 2–3 раза
DEBUG_SKUS = [x for x in os.environ.get("OZON_FIN_DEBUG_SKUS", "1947309601,1974187724,2169484129").split(",") if x]


def fetch_ozon_finance(days: int = 30) -> dict:
    import time
    import ozon_client

    to = datetime.now(timezone.utc)
    frm = to - timedelta(days=days)
    types = _accrual_types(ozon_client)
    tname = lambda i: types.get(str(i)) or f"вид {i}"  # noqa: E731
    accruals, samples, debug = [], {}, []
    d = frm.date()
    while d <= to.date():
        lst = _accruals_by_day(ozon_client, d.isoformat())
        for a in lst:
            samples.setdefault(a.get("accrued_category") or "?", a)
            # полные записи по контрольным SKU — сверка разбора выручки (провалы «до −65%»)
            raw = json.dumps(a, ensure_ascii=False)
            if len(debug) < 60 and any(k in raw for k in DEBUG_SKUS):
                debug.append(a)
        accruals += lst
        d += timedelta(days=1)
        time.sleep(0.25)

    # sku -> offer_id
    sku2offer = {}
    try:
        d = json.load(open(os.path.join(DATA, "ozon_products.json"), encoding="utf-8"))
        for x in d.get("list", []):
            if x.get("sku"):
                sku2offer[str(x["sku"])] = x["offer_id"]
        for x in d.get("details", []):
            for s in (x.get("sources") or []):
                if s.get("sku"):
                    sku2offer[str(s["sku"])] = x["offer_id"]
    except Exception:  # noqa: BLE001
        pass

    per = defaultdict(lambda: {"sold": 0, "returned": 0, "revenue": 0.0, "commission": 0.0,
                               "logistics": 0.0, "services": 0.0, "net": 0.0})
    unassigned = defaultdict(float)
    by_type = defaultdict(float)   # все удержания по видам — для сверки с кабинетом
    total_payout = 0.0
    for a in accruals:
        total = _money(a.get("total_amount", a.get("amount")))
        total_payout += total
        cat = a.get("accrued_category") or ""
        if a.get("non_item_fee"):
            f = a["non_item_fee"]
            name = tname(f.get("type_id"))
            unassigned[name] += total
            by_type[name] += total
            continue
        for e in (a.get("item_fees") or {}).get("fees") or []:
            sku = str(e.get("sku") or "")
            for f in e.get("fees") or []:
                name, v = tname(f.get("type_id")), _money(f.get("accrued", f.get("amount")))
                by_type[name] += v
                if not sku or sku == "0":
                    unassigned[name] += v
                    continue
                p = per[sku]
                if re.search(r"вознагражд|комисс", name, re.I):
                    p["commission"] += v
                else:
                    p["services"] += v
                p["net"] += v
        prods = (a.get("posting") or {}).get("products") or []
        if cat == "POSTING" and prods:
            parts = []
            for pr in prods:
                cm = pr.get("commission") or {}
                price = _money(cm.get("seller_price"))
                comm = _money(cm.get("sale_commission", cm.get("commission")))
                dl = pr.get("delivery") or {}
                deliv = _money(dl.get("total_accrued")) if dl.get("total_accrued") is not None else \
                    sum(_money(s.get("accrued")) for s in dl.get("services") or [])
                for s in dl.get("services") or []:
                    by_type[tname(s.get("type_id"))] += _money(s.get("accrued"))
                if comm:
                    by_type["Вознаграждение за продажу"] += comm
                parts.append((str(pr.get("sku") or ""), int(abs(_money(pr.get("quantity"))) or 1), price, comm, deliv))
            calc = sum(p[2] + p[3] + p[4] for p in parts)
            for sku, qty, price, comm, deliv in parts:
                # Одно товарное отправление — берём итог Ozon как есть; несколько — по расчёту на товар.
                net = total if len(parts) == 1 else price + comm + deliv
                p = per[sku]
                p["revenue"] += price
                p["commission"] += comm
                p["logistics"] += deliv
                p["net"] += net
                if price > 0:
                    p["sold"] += qty
                elif price < 0:
                    p["returned"] += qty
            if len(parts) > 1 and abs(calc - total) > 1:
                unassigned["Расхождение по многотоварным отправлениям"] += total - calc
        elif not a.get("item_fees"):
            unassigned[f"Прочее ({cat or '?'})"] += total

    out = {}
    for sku, p in per.items():
        oid = sku2offer.get(sku, f"sku:{sku}")
        o = out.setdefault(oid, {"sold": 0, "returned": 0, "revenue": 0.0, "commission": 0.0,
                                 "logistics": 0.0, "services": 0.0, "net": 0.0})
        for k in o:
            o[k] += p[k]
    res = {"from": frm.date().isoformat(), "to": to.date().isoformat(), "method": "/v1/finance/accrual/by-day",
           "operations": len(accruals), "payout_total": round(total_payout, 2),
           "items": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                     for k, v in out.items()},
           "unassigned": {k: round(v, 2) for k, v in unassigned.items()},
           "by_type": {k: round(v, 2) for k, v in sorted(by_type.items(), key=lambda x: x[1])},
           "samples": {k: v for k, v in samples.items()},
           "debug_skus": DEBUG_SKUS, "debug_records": debug}
    with open(FIN_PATH, "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    return res


def cmd_ozon_finance() -> int:
    try:
        r = fetch_ozon_finance()
    except Exception as exc:  # noqa: BLE001
        print(f"ozon-finance: ОШИБКА — {exc}")
        with open(FIN_PATH, "w", encoding="utf-8") as fh:
            json.dump({"error": str(exc)}, fh, ensure_ascii=False)
        return 1
    print(f"Ozon начисления {r['from']}..{r['to']}: начислений {r['operations']}, товаров {len(r['items'])}, "
          f"к выплате итого {r['payout_total']:,.0f} ₽")
    for k, v in list(r["by_type"].items())[:15]:
        print(f"  {k}: {v:,.0f}")
    return 0


# ---------------------------------------------------------------- сводная ревизия
def _num(v):
    try:
        return float(str(v).replace(" ", "").replace("\xa0", "").replace(",", "."))
    except (TypeError, ValueError):
        return None


def build_price_audit() -> dict:
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    oz = json.load(open(os.path.join(DATA, "ozon_products.json"), encoding="utf-8"))
    det = {x["offer_id"].lower(): x for x in oz.get("details", [])}
    fin = {}
    if os.path.exists(FIN_PATH):
        f = json.load(open(FIN_PATH, encoding="utf-8"))
        fin = {k.lower(): v for k, v in (f.get("items") or {}).items()}
    site = {}
    try:
        site = {k.lower(): v for k, v in json.load(open(os.path.join(DATA, "site_prices.json"), encoding="utf-8")).items()}
    except Exception:  # noqa: BLE001
        pass
    avito = {}
    try:
        ws = openpyxl.load_workbook(os.path.join(DATA, "avito_feed.xlsx"), read_only=True)["Объявления"]
        rows = list(ws.iter_rows(values_only=True))
        h = list(rows[1])
        iu, ip = h.index("Уникальный идентификатор объявления"), h.index("Цена")
        avito = {str(r[iu]).lower(): _num(r[ip]) for r in rows[4:] if r[iu]}
    except Exception:  # noqa: BLE001
        pass

    # мастер: читаем и дописываем закупку из Ozon, где пусто
    wb = openpyxl.load_workbook(MASTER)
    ws = wb["Товары"]
    head = [str(c.value or "").strip() for c in ws[1]]
    if COST_HEADER not in head:
        ws.cell(1, len(head) + 1, COST_HEADER)
        head.append(COST_HEADER)
    ci = head.index(COST_HEADER) + 1
    i_price, i_stock, i_name = head.index("Цена, ₽") + 1, head.index("Остаток, шт.") + 1, head.index("Название товара") + 1
    filled = 0
    items = []
    for r in range(2, ws.max_row + 1):
        art = ws.cell(r, 1).value
        if not art:
            continue
        a = str(art).strip()
        cost = _num(ws.cell(r, ci).value)
        e = det.get(a.lower())
        if (cost is None or cost <= 0) and e:
            np_ = _num((e.get("price") or {}).get("net_price"))
            if np_ and np_ > 0:
                ws.cell(r, ci, np_ if not float(np_).is_integer() else int(np_))
                cost, filled = np_, filled + 1
        items.append({"art": a, "name": ws.cell(r, i_name).value or "", "price": _num(ws.cell(r, i_price).value),
                      "stock": _num(ws.cell(r, i_stock).value), "cost": cost})
    if filled:
        wb.save(MASTER)

    out = []
    for it in items:
        a, p, cost = it["art"].lower(), it["price"], it["cost"]
        if not p:
            continue
        e, f = det.get(a), fin.get(a)
        # Ozon: на руки с одной продажи
        oz_net_est = None
        if e and (e.get("commissions") or {}).get("sales_percent_fbs"):
            c = e["commissions"]
            oz_net_est = p * (1 - c["sales_percent_fbs"] / 100) - float(e.get("acquiring") or 0) \
                - float(c.get("fbs_direct_flow_trans_max_amount") or 0) - float(c.get("fbs_deliv_to_customer_amount") or 0) \
                - float(c.get("fbs_first_mile_max_amount") or 0)
        oz_net_fact, src = None, ""
        if f and f.get("sold"):
            oz_net_fact = f["net"] / f["sold"]
            src = f"факт: {f['sold']} продаж"
        oz_net = oz_net_fact if oz_net_fact is not None else oz_net_est
        if oz_net is not None and not src:
            src = "оценка по тарифам"
        oz_profit = (oz_net - p * TAX - cost) if (oz_net is not None and cost is not None) else None
        av = avito.get(a)
        av_profit = (av * (1 - AVITO_FEE - TAX) - cost) if (av and cost is not None) else None
        st = site.get(a)
        st_profit = (st * (1 - SITE_FEE - TAX) - cost) if (st and cost is not None) else None
        flags = []
        if cost is None:
            flags.append("нет закупки")
        for ch, pr, price in (("Ozon", oz_profit, p), ("Авито", av_profit, av), ("сайт", st_profit, st)):
            if pr is None or not price:
                continue
            if pr < 0:
                flags.append(f"УБЫТОК {ch}")
            elif pr < price * LOW_MARGIN:
                flags.append(f"низкая маржа {ch}")
        if oz_profit is not None and av_profit is not None and max(oz_profit, av_profit) > 0:
            diff = abs(oz_profit - av_profit) / max(abs(oz_profit), abs(av_profit))
            if diff > 0.3:
                flags.append("прибыль Ozon/Авито отличается >30%")
        out.append({**it, "oz_net": oz_net, "src": src, "oz_profit": oz_profit, "avito": av, "av_profit": av_profit,
                    "site": st, "st_profit": st_profit, "flags": flags,
                    "fin": f})

    # дубли: одинаковое название без хвоста -v2 / регистра, разные цены
    groups = defaultdict(list)
    for o in out:
        key = re.sub(r"-v\d+$", "", o["art"].lower())
        groups[key].append(o)
    for g in groups.values():
        if len(g) > 1 and len({o["price"] for o in g} | {o["avito"] for o in g if o["avito"]}) > 2:
            for o in g:
                o["flags"].append("дубль с другой ценой: " + ", ".join(x["art"] for x in g if x is not o))

    # комплект дешевле детали: плита/фильтр/мехатроник — по ключевому слову в названии
    def has(o, *w):
        n = str(o["name"]).lower()
        return all(x in n for x in w)
    singles = [o for o in out if has(o, "гидроплит") and "+" not in str(o["name"]) and "комплект" not in str(o["name"]).lower()]
    for s in singles:
        for o in out:
            if o is not s and has(o, "плит") and "+" in str(o["name"]) and o["price"] < s["price"]:
                o["flags"].append(f"комплект дешевле детали {s['art']} ({s['price']:g})")

    # --- запись
    wb2 = openpyxl.Workbook()
    w = wb2.active
    w.title = "Ревизия цен"
    H = ["Артикул", "Название", "Остаток", "Закупка", "Цена Ozon", "Ozon на руки с 1 шт", "Источник",
         "Прибыль Ozon", "Маржа Ozon", "Цена Авито", "Прибыль Авито", "Цена сайт", "Прибыль сайт",
         "Продано на Ozon за период", "Проблемы"]
    hf = Font(name="Arial", bold=True, color="FFFFFF")
    for j, x in enumerate(H, 1):
        c = w.cell(1, j, x)
        c.font, c.fill = hf, PatternFill("solid", fgColor="305496")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    red, yel = PatternFill("solid", fgColor="F8CBAD"), PatternFill("solid", fgColor="FFF2CC")
    out.sort(key=lambda o: (0 if any("УБЫТОК" in x for x in o["flags"]) else 1 if o["flags"] else 2, -(o["price"] or 0)))
    for i, o in enumerate(out, 2):
        rnd = lambda v: round(v) if isinstance(v, (int, float)) else v  # noqa: E731
        vals = [o["art"], o["name"], o["stock"], o["cost"], o["price"], rnd(o["oz_net"]), o["src"], rnd(o["oz_profit"]),
                (o["oz_profit"] / o["price"]) if o["oz_profit"] is not None else None,
                o["avito"], rnd(o["av_profit"]), o["site"], rnd(o["st_profit"]),
                (o["fin"] or {}).get("sold"), "; ".join(o["flags"])]
        for j, v in enumerate(vals, 1):
            c = w.cell(i, j, v)
            c.font = Font(name="Arial", size=10)
            if j in (4, 5, 6, 8, 10, 11, 12, 13):
                c.number_format = "#,##0"
            if j == 9:
                c.number_format = "0%"
        fill = red if any("УБЫТОК" in x for x in o["flags"]) else yel if o["flags"] else None
        if fill:
            for j in range(1, len(H) + 1):
                w.cell(i, j).fill = fill
    for col, wd in zip("ABCDEFGHIJKLMNO", [22, 50, 8, 9, 10, 11, 16, 10, 8, 10, 10, 10, 10, 10, 60]):
        w.column_dimensions[col].width = wd
    w.row_dimensions[1].height = 42
    w.freeze_panes = "C2"
    w.auto_filter.ref = f"A1:O{len(out) + 1}"
    n = wb2.create_sheet("Как считано")
    for k, line in enumerate([
        "Ozon на руки с 1 шт: если за период были продажи — ФАКТ из начислений Ozon (выручка минус комиссия, логистика, эквайринг и прочие удержания по этим отправлениям, делённое на число продаж);",
        "иначе — оценка по тарифам Ozon из API (комиссия % + эквайринг + логистика по МАКСИМАЛЬНОМУ тарифу).",
        f"Налог {TAX:.0%} от цены продажи на всех каналах. Комиссия Авито {AVITO_FEE:.0%}. Эквайринг сайта {SITE_FEE:.0%} (допущение).",
        "Закупка — колонка «Закупка, ₽» в master_control.xlsx; где пусто — взята себестоимость из кабинета Ozon и вписана в мастер.",
        f"Низкая маржа — прибыль меньше {LOW_MARGIN:.0%} цены. Красные строки — убыток хотя бы на одном канале.",
        "Расходы Ozon без привязки к товару (реклама, подписки и т. п.) — на листе «Общие расходы Ozon».",
    ], 1):
        n.cell(k, 1, line).font = Font(name="Arial", size=10)
    n.column_dimensions["A"].width = 160
    if os.path.exists(FIN_PATH):
        f = json.load(open(FIN_PATH, encoding="utf-8"))
        g = wb2.create_sheet("Общие расходы Ozon")
        g.cell(1, 1, f"Период {f.get('from')}..{f.get('to')}").font = Font(name="Arial", bold=True)
        for k, (name, v) in enumerate(sorted((f.get("unassigned") or {}).items(), key=lambda x: x[1]), 3):
            g.cell(k, 1, name)
            g.cell(k, 2, round(v)).number_format = "#,##0"
        k0 = g.max_row + 2
        g.cell(k0, 1, "Все удержания и начисления по видам (для сверки с «Финансы» в кабинете)").font = Font(name="Arial", bold=True)
        for k, (name, v) in enumerate((f.get("by_type") or {}).items(), k0 + 1):
            g.cell(k, 1, name)
            g.cell(k, 2, round(v)).number_format = "#,##0"
        g.column_dimensions["A"].width = 70
    wb2.save(AUDIT_PATH)
    loss = sum(1 for o in out if any("УБЫТОК" in x for x in o["flags"]))
    nocost = sum(1 for o in out if o["cost"] is None)
    return {"items": len(out), "loss": loss, "no_cost": nocost, "cost_filled_from_ozon": filled}


def cmd_price_audit() -> int:
    try:  # сначала свежие начисления Ozon (если не вышло — считаем по тарифам)
        cmd_ozon_finance()
    except Exception as exc:  # noqa: BLE001
        print(f"(начисления Ozon не получены: {exc})")
    r = build_price_audit()
    print(f"price-audit: товаров {r['items']}, с убытком {r['loss']}, без закупки {r['no_cost']}, "
          f"закупка подтянута из Ozon в мастер: {r['cost_filled_from_ozon']}. Файл: data/price_audit.xlsx")
    return 0


# ---------------------------------------------------------------- предпросмотр «одинаковая прибыль»
PREVIEW_PATH = os.path.join(DATA, "price_preview.xlsx")


def build_price_preview() -> dict:
    """Было → станет для Авито и сайта по правилу «одинаковая прибыль с Ozon». НИЧЕГО не отправляет и
    не меняет цены: это файл на проверку. Применение — режим «прибыль» в листе «Настройки»
    data/avito_site_price_overrides.xlsx (B5), дальше update-cards."""
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill

    import site_pricing as sp

    prof = dict(sp.get_profit_settings())
    plan = sp.plan_equal_profit(MASTER, prof=prof)
    oz = json.load(open(os.path.join(DATA, "ozon_products.json"), encoding="utf-8"))
    idx = {x["offer_id"].lower(): (x.get("price_indexes") or {}) for x in oz.get("details", [])}
    wbm = openpyxl.load_workbook(MASTER, read_only=True, data_only=True)
    rows = list(wbm["Товары"].iter_rows(values_only=True))
    h = [str(c or "") for c in rows[0]]
    ic, ist = h.index(COST_HEADER) if COST_HEADER in h else None, h.index("Остаток, шт.")
    master = {str(r[0]).strip(): r for r in rows[1:] if r[0]}

    tax = prof["tax"]
    out = []
    for x in plan:
        r = master.get(x["art"]) or ()
        cost = _num(r[ic]) if ic is not None and len(r) > ic else None
        stock = _num(r[ist]) if len(r) > ist else None
        p, net = x["ozon_price"], x["net"]
        oz_profit = (net - tax * p - cost) if (net is not None and cost is not None) else None
        av_profit = (x["avito_new"] * (1 - tax - prof["avito_fee"]) - cost) if (x["avito_new"] and cost is not None) else None
        st_profit = (x["site_new"] * (1 - tax - prof["site_fee"]) - cost) if (x["site_new"] and cost is not None) else None
        pi = idx.get(x["art"].lower(), {})
        selfm = pi.get("self_marketplaces_index_data") or {}
        flags = []
        if oz_profit is not None and oz_profit < 0:
            flags.append("УБЫТОК на всех каналах (Ozon в минусе)")
        elif oz_profit is not None and oz_profit < p * LOW_MARGIN * 0.5:
            flags.append("прибыль очень низкая")
        if cost is None:
            flags.append("нет закупки — прибыль не посчитана (цена от закупки не зависит)")
        if x["avito_old"] and x["avito_new"] and (x["avito_new"] - x["avito_old"]) / x["avito_old"] < -0.25:
            flags.append("Авито дешевле более чем на 25%")
        if pi.get("color_index") == "RED":
            m = selfm.get("min_price")
            flags.append(f"Ozon УЖЕ красный индекс" + (f": видит нашу цену {m:g} на другой площадке" if m else " (по конкурентам)"))
        out.append({**x, "cost": cost, "stock": stock, "oz_profit": oz_profit, "av_profit": av_profit,
                    "st_profit": st_profit, "color": pi.get("color_index") or "", "flags": flags})

    wb = openpyxl.Workbook()
    w = wb.active
    w.title = "Было → станет"
    H = ["Артикул", "Название", "Остаток", "Закупка", "Цена Ozon/WB (не меняется)", "Ozon на руки с 1 шт", "Источник",
         "Прибыль с 1 шт (Ozon)", "Авито было", "Авито станет", "Авито Δ%", "Прибыль Авито",
         "Сайт было", "Сайт станет", "Сайт Δ%", "Прибыль сайт", "Индекс цен Ozon сейчас", "Замечания"]
    hf = Font(name="Arial", bold=True, color="FFFFFF")
    for j, v in enumerate(H, 1):
        c = w.cell(1, j, v)
        c.font, c.fill = hf, PatternFill("solid", fgColor="305496")
        c.alignment = Alignment(wrap_text=True, vertical="center")
    red, yel, grn = (PatternFill("solid", fgColor=c) for c in ("F8CBAD", "FFF2CC", "E2EFDA"))
    dp = lambda new, old: ((new - old) / old) if (new and old) else None  # noqa: E731
    out.sort(key=lambda o: (0 if any("УБЫТОК" in f for f in o["flags"]) else 1, -(o["stock"] or 0) > 0, -(o["ozon_price"] or 0)))
    rnd = lambda v: round(v) if isinstance(v, (int, float)) else v  # noqa: E731
    for i, o in enumerate(out, 2):
        vals = [o["art"], o["name"], o["stock"], o["cost"], o["ozon_price"], rnd(o["net"]), o["src"], rnd(o["oz_profit"]),
                o["avito_old"], o["avito_new"], dp(o["avito_new"], o["avito_old"]), rnd(o["av_profit"]),
                o["site_old"], o["site_new"], dp(o["site_new"], o["site_old"]), rnd(o["st_profit"]),
                o["color"], "; ".join(o["flags"])]
        for j, v in enumerate(vals, 1):
            c = w.cell(i, j, v)
            c.font = Font(name="Arial", size=10)
            if j in (4, 5, 6, 8, 9, 10, 12, 13, 14, 16):
                c.number_format = "#,##0"
            if j in (11, 15):
                c.number_format = "+0%;-0%;0%"
        if any("УБЫТОК" in f for f in o["flags"]):
            for j in range(1, len(H) + 1):
                w.cell(i, j).fill = red
        elif o["flags"]:
            w.cell(i, len(H)).fill = yel
        for j in (10, 14):
            w.cell(i, j).fill = grn
    for col, wd in zip("ABCDEFGHIJKLMNOPQR", [22, 46, 8, 9, 11, 10, 18, 10, 10, 10, 8, 10, 10, 10, 8, 10, 12, 60]):
        w.column_dimensions[col].width = wd
    w.row_dimensions[1].height = 45
    w.freeze_panes = "C2"
    w.auto_filter.ref = f"A1:R{len(out) + 1}"

    n = wb.create_sheet("Как считано")
    for k, line in enumerate([
        "Цель: с каждой проданной штуки на Авито и на сайте остаётся столько же, сколько с продажи на Ozon.",
        "Прибыль Ozon = на руки с Ozon − налог × цена Ozon − закупка. Прибыль Авито/сайта = цена × (1 − налог − комиссия канала) − закупка.",
        "Отсюда цена канала = (на руки с Ozon − налог × цена Ozon) / (1 − налог − комиссия канала). Закупка сокращается: цена от неё НЕ зависит,",
        "закупка нужна только чтобы видеть, есть ли прибыль вообще (красные строки — убыток уже на Ozon, значит и везде).",
        f"Налог {prof['tax']:.0%}, комиссия Авито {prof['avito_fee']:.0%}, эквайринг сайта {prof['site_fee']:.1%} (уточнить у платёжного сервиса Тильды).",
        "На руки с Ozon: «факт» — из начислений Ozon за 30 дней (если были продажи); «тарифы» — комиссия и эквайринг из API Ozon +",
        "логистика по середине между минимальным и максимальным тарифом. После починки начислений пересчитать (команда price-preview).",
        "Цена Авито/сайта не выше цены Ozon. Округление до 10 ₽. Строки с «да» в колонке «фикс» не трогаются.",
        "Доставку Авито/СДЭК платит покупатель — в расчёт не входит.",
        "Применить: в data/avito_site_price_overrides.xlsx лист «Настройки», B5 = прибыль → update-cards (Авито и сайт обновятся сами).",
    ], 1):
        n.cell(k, 1, line).font = Font(name="Arial", size=10)
    n.column_dimensions["A"].width = 150
    wb.save(PREVIEW_PATH)

    def tot(key):
        return sum(1 for o in out if o.get(key + "_new") and o.get(key + "_old") and o[key + "_new"] < o[key + "_old"] - 0.5), \
               sum(1 for o in out if o.get(key + "_new") and o.get(key + "_old") and o[key + "_new"] > o[key + "_old"] + 0.5)
    return {"items": len(out), "avito_down_up": tot("avito"), "site_down_up": tot("site"),
            "loss": sum(1 for o in out if any("УБЫТОК" in f for f in o["flags"])),
            "red": sum(1 for o in out if o["color"] == "RED"), "rows": out}


def cmd_price_preview() -> int:
    r = build_price_preview()
    print(f"price-preview: товаров {r['items']}; Авито дешевле/дороже: {r['avito_down_up']}; сайт: {r['site_down_up']}; "
          f"убыток: {r['loss']}; красный индекс Ozon сейчас: {r['red']}. Файл data/price_preview.xlsx (ничего не отправлено)")
    return 0
