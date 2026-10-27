"""
Аналитика Ozon через Seller API — только чтение, ничего не меняет.

Команда --diag-ozon-analytics:
  1) Проверяет, какие метрики /v1/analytics/data отдаёт без Premium Plus
     (бесплатный набор revenue/ordered_units и «премиальные» — показы в поиске,
     просмотры карточки, корзина, позиция). Лимит Ozon — 1 запрос в минуту,
     поэтому между запросами ждём 62 с; вся проверка идёт ~10 минут.
  2) Пробует методы поисковых запросов по товарам (/v1/analytics/product-queries)
     и контент-рейтинг (/v1/product/rating-by-sku).
  3) Всё, что Ozon отдал, складывает в data/ozon_analytics_weekly.xlsx:
     товар × неделя × метрика, кластер «шток + болты» отмечен отдельно.
  Итог проверки (что доступно, тексты ошибок) — data/ozon_analytics_diag.txt.
"""
import datetime
import json
import os
import time
import traceback

import ozon_client

DATA = "data"
WAIT = 62  # Ozon: /v1/analytics/data — 1 запрос в минуту на кабинет

FREE_METRICS = ["revenue", "ordered_units"]
# Метрики, которые по документации Ozon требуют Premium. Проверяем по факту.
PREMIUM_METRICS = [
    "hits_view_search",     # показы в поиске и каталоге
    "hits_view_pdp",        # показы на карточке
    "hits_view",            # всего показов
    "hits_tocart_search",   # в корзину из поиска
    "hits_tocart_pdp",      # в корзину из карточки
    "hits_tocart",          # всего в корзину
    "session_view_search",  # сессии с показом в поиске
    "session_view_pdp",     # сессии с показом карточки
    "conv_tocart_search",   # конверсия в корзину из поиска
    "conv_tocart_pdp",      # конверсия в корзину из карточки
    "position_category",    # средняя позиция в поиске и каталоге
    "returns",
    "cancellations",
    "delivered_units",
]
# Кластер, который просел с ~8.10 при неизменной цене
CLUSTER = ["0am325025z", "0am325025u", "0am325091e", "wht001922", "01x301127c", "0am325219c"]


def _sku_map():
    """sku (str) -> offer_id по data/ozon_products.json."""
    try:
        with open(os.path.join(DATA, "ozon_products.json"), encoding="utf-8") as f:
            d = json.load(f)
        rows = d.get("list", []) if isinstance(d, dict) else d
        return {str(r.get("sku")): r.get("offer_id") for r in rows if r.get("sku")}
    except Exception:  # noqa: BLE001
        return {}


def _analytics(metrics, date_from, date_to, dims=("sku", "week")):
    rows, offset = [], 0
    while True:
        res = ozon_client._post("/v1/analytics/data", {
            "date_from": date_from, "date_to": date_to,
            "metrics": list(metrics), "dimension": list(dims),
            "filters": [], "sort": [], "limit": 1000, "offset": offset,
        }, retries=4)
        data = (res.get("result") or {}).get("data") or []
        rows.extend(data)
        if len(data) < 1000:
            return rows
        offset += 1000
        time.sleep(WAIT)


def run() -> int:
    log = []
    p = lambda s: (log.append(s), print(s))
    today = datetime.date.today()
    date_to = today.isoformat()
    date_from = (today - datetime.timedelta(weeks=12)).isoformat()
    skus = _sku_map()
    p(f"Проверка аналитики Ozon {datetime.datetime.now():%Y-%m-%d %H:%M} UTC, период {date_from} … {date_to}")
    p(f"Товаров с sku в ozon_products.json: {len(skus)}")

    collected = {}  # (offer, week) -> {metric: value}
    available = []

    def store(metrics, rows):
        for r in rows:
            dims = r.get("dimensions") or []
            if len(dims) < 2:
                continue
            sku = str(dims[0].get("id"))
            week = str(dims[1].get("id") or dims[1].get("name"))
            key = (skus.get(sku, sku), week)
            for m, v in zip(metrics, r.get("metrics") or []):
                collected.setdefault(key, {})[m] = v

    def attempt(label, metrics):
        try:
            rows = _analytics(metrics, date_from, date_to)
            nonzero = sum(1 for r in rows if any(x for x in (r.get("metrics") or [])))
            p(f"[OK] {label}: {len(rows)} строк, ненулевых {nonzero}")
            store(metrics, rows)
            available.extend(m for m in metrics if m not in available)
            return True
        except Exception as exc:  # noqa: BLE001
            p(f"[НЕТ] {label}: {str(exc)[:400]}")
            return False

    # 1. бесплатный набор
    attempt("бесплатные метрики " + ",".join(FREE_METRICS), FREE_METRICS)
    time.sleep(WAIT)
    # 2. все «премиальные» разом
    if not attempt("премиальные метрики (все сразу)", PREMIUM_METRICS):
        # 3. по одной — самые важные для поиска; вдруг часть открыта
        for m in ("hits_view_search", "hits_view_pdp", "hits_tocart", "session_view_search", "position_category"):
            time.sleep(WAIT)
            attempt(f"метрика {m}", [m])
    # 4. разрез по дням (короче период) — для точной даты обвала
    time.sleep(WAIT)
    try:
        rows = _analytics(["ordered_units"], (today - datetime.timedelta(days=40)).isoformat(), date_to, dims=("sku", "day"))
        p(f"[OK] ordered_units по дням (40 дн.): {len(rows)} строк")
    except Exception as exc:  # noqa: BLE001
        p(f"[НЕТ] ordered_units по дням: {str(exc)[:300]}")

    # 5. поисковые запросы по товарам и контент-рейтинг
    cl_skus = [int(s) for s, o in skus.items() if o in CLUSTER and s.isdigit()]
    probes = [
        ("/v1/analytics/product-queries", {
            "date_from": f"{(today - datetime.timedelta(days=28)).isoformat()}T00:00:00Z",
            "date_to": f"{date_to}T00:00:00Z", "skus": [str(s) for s in cl_skus],
            "page": 0, "page_size": 100, "sort_by": "BY_SEARCHES", "sort_dir": "DESCENDING"}),
        ("/v1/analytics/product-queries/details", {
            "date_from": f"{(today - datetime.timedelta(days=28)).isoformat()}T00:00:00Z",
            "date_to": f"{date_to}T00:00:00Z", "skus": [str(s) for s in cl_skus],
            "limit_by_sku": 15, "page": 0, "page_size": 100,
            "sort_by": "BY_SEARCHES", "sort_dir": "DESCENDING"}),
        ("/v1/product/rating-by-sku", {"skus": [int(s) for s in skus if s.isdigit()][:100]}),
    ]
    raw = {}
    for path, body in probes:
        try:
            res = ozon_client._post(path, body, retries=2)
            raw[path] = res
            p(f"[OK] {path}: {json.dumps(res, ensure_ascii=False)[:600]}")
        except Exception as exc:  # noqa: BLE001
            p(f"[НЕТ] {path}: {str(exc)[:400]}")
    with open(os.path.join(DATA, "ozon_analytics_raw.json"), "w", encoding="utf-8") as f:
        json.dump(raw, f, ensure_ascii=False, indent=1)

    # 6. таблица товар × неделя
    try:
        _write_xlsx(collected, available)
        p(f"Таблица: data/ozon_analytics_weekly.xlsx ({len(collected)} строк товар×неделя)")
    except Exception:  # noqa: BLE001
        p("Таблицу собрать не удалось: " + traceback.format_exc()[-600:])

    p("ИТОГ: доступны метрики " + (", ".join(available) or "— никакие"))
    os.makedirs(DATA, exist_ok=True)
    with open(os.path.join(DATA, "ozon_analytics_diag.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(log) + "\n")
    return 0


def _write_xlsx(collected, metrics):
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill

    wb = Workbook()
    ws = wb.active
    ws.title = "Товар×неделя"
    head = ["Артикул", "Кластер шток+болты", "Неделя"] + metrics
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    fill = PatternFill("solid", fgColor="FFF2CC")
    for (offer, week), vals in sorted(collected.items(), key=lambda kv: (kv[0][0] not in CLUSTER, kv[0][0], kv[0][1])):
        ws.append([offer, "да" if offer in CLUSTER else "", week] + [vals.get(m, "") for m in metrics])
        if offer in CLUSTER:
            for c in ws[ws.max_row]:
                c.fill = fill
    ws.freeze_panes = "D2"
    ws.column_dimensions["A"].width = 22
    ws.column_dimensions["C"].width = 14
    wb.save(os.path.join(DATA, "ozon_analytics_weekly.xlsx"))
