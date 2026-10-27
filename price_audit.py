"""
Ревизия цен и прибыли по всем каналам.

Две команды:
  ozon-finance  — фактические начисления Ozon за последние N дней (/v3/finance/transaction/list):
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
def fetch_ozon_finance(days: int = 30) -> dict:
    import ozon_client

    to = datetime.now(timezone.utc)
    frm = to - timedelta(days=days)
    ops = []
    # Ozon отдаёт не больше месяца за запрос — режем по 28 дней.
    start = frm
    while start < to:
        end = min(start + timedelta(days=28), to)
        page = 1
        while True:
            res = ozon_client._post("/v3/finance/transaction/list", {
                "filter": {
                    "date": {"from": start.strftime("%Y-%m-%dT%H:%M:%S.000Z"),
                             "to": end.strftime("%Y-%m-%dT%H:%M:%S.000Z")},
                    "operation_type": [], "posting_number": "", "transaction_type": "all",
                },
                "page": page, "page_size": 1000,
            }).get("result", {})
            ops += res.get("operations", []) or []
            if page >= int(res.get("page_count") or 1):
                break
            page += 1
        start = end

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

    # Отправление -> товары (по операциям, где товары перечислены)
    post_items = defaultdict(list)
    for op in ops:
        pn = (op.get("posting") or {}).get("posting_number") or ""
        if pn and op.get("items") and pn not in post_items:
            post_items[pn] = [str(i.get("sku")) for i in op["items"]]

    per = defaultdict(lambda: {"sold": 0, "returned": 0, "revenue": 0.0, "commission": 0.0,
                               "services": 0.0, "net": 0.0, "names": set()})
    unassigned = defaultdict(float)
    for op in ops:
        pn = (op.get("posting") or {}).get("posting_number") or ""
        skus = [str(i.get("sku")) for i in (op.get("items") or [])] or post_items.get(pn, [])
        amount = float(op.get("amount") or 0)
        if not skus:
            unassigned[op.get("operation_type_name") or op.get("operation_type") or "?"] += amount
            continue
        share = 1 / len(skus)
        services = sum(float(s.get("price") or 0) for s in (op.get("services") or []))
        for sku in skus:
            p = per[sku]
            acc = float(op.get("accruals_for_sale") or 0) * share
            p["revenue"] += acc
            p["commission"] += float(op.get("sale_commission") or 0) * share
            p["services"] += services * share
            p["net"] += amount * share
            if op.get("operation_type") == "OperationAgentDeliveredToCustomer":
                p["sold"] += 1
            elif acc < 0:
                p["returned"] += 1
        for i in op.get("items") or []:
            per[str(i.get("sku"))]["names"].add(i.get("name") or "")

    out = {}
    for sku, p in per.items():
        oid = sku2offer.get(sku, f"sku:{sku}")
        o = out.setdefault(oid, {"sold": 0, "returned": 0, "revenue": 0.0, "commission": 0.0,
                                 "services": 0.0, "net": 0.0, "name": ""})
        for k in ("sold", "returned", "revenue", "commission", "services", "net"):
            o[k] += p[k]
        o["name"] = o["name"] or next(iter(p["names"] - {""}), "")
    res = {"from": frm.date().isoformat(), "to": to.date().isoformat(), "operations": len(ops),
           "items": {k: {kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                     for k, v in out.items()},
           "unassigned": {k: round(v, 2) for k, v in unassigned.items()}}
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
    print(f"Ozon начисления {r['from']}..{r['to']}: операций {r['operations']}, товаров {len(r['items'])}")
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
