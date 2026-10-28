"""
avito-dupes (только чтение): дубли объявлений Авито — какой товар фида размещён несколькими объявлениями.

Зачем: ~50 старых объявлений, созданных вручную, дублируют товары фида (правило Авито «один товар —
одно объявление», за дубли блокируют). План: по каждому товару оставить ОДНО объявление — лучшее по
просмотрам/контактам — и привязать его к фиду через AvitoId (фид начинает управлять им), остальные снять.

Что делает:
  1) все объявления аккаунта (active/old/rejected/blocked) — Avito API;
  2) статистика за ~270 дней: просмотры, контакты, избранное (stats API);
  3) какое объявление сейчас ведёт фид (отчёт автозагрузки: ad_id -> avito_id);
  4) каждое НЕ фидовое объявление сопоставляет товару фида по названию (артикул в названии — сильный признак);
  5) пишет data/avito_dupes.xlsx: товар -> все его объявления -> что предлагается оставить.
Ничего на Авито не меняет. Сопоставление по названию — предложение, его надо проверить глазами.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from typing import Dict, List, Optional

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(DATA, "avito_dupes.xlsx")

_STOP = {"для", "и", "с", "на", "в", "шт", "мехатроника", "мехатроник", "акпп", "кпп", "новый", "новая",
         "новые", "оригинал", "оригинальный", "оригинальные", "vag", "dsg", "dsg7", "dq200", "0am", "0cw"}


def _norm(t: str) -> str:
    t = str(t or "").lower().replace("ё", "е")
    t = re.sub(r"dsg[\s\-]*(\d)", r"dsg\1", t)
    return t


def _tokens(t: str) -> set:
    out = set()
    for w in re.findall(r"[a-zа-я0-9]+", _norm(t)):
        if len(w) < 2 or w in _STOP:
            continue
        if re.fullmatch(r"[а-я]+", w) and len(w) > 4:
            w = w[:min(5, max(4, len(w) - 2))]  # грубая основа: болты/болтов, штоки/штока, пыльник/пыльники
        out.add(w)
    return out


def _codes(t: str) -> set:
    # артикулы вида 0am325025b, 02e305051c, 0bh325183, wht001922 — без пробелов
    s = re.sub(r"[\s\-]", "", _norm(t))
    return set(re.findall(r"(?:0am|0cw|02e|0bh|0b5|01x|wht|n0)\d{5,6}[a-z]{0,2}", s))


def _feed_rows() -> List[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(os.path.join(DATA, "avito_feed.xlsx"), read_only=True)
    out = []
    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        hi = None
        for i, r in enumerate(rows[:8]):
            cells = [str(c).strip() for c in r if c]
            if "Название объявления" in cells or "Title" in cells:
                hi = i
                break
        if hi is None:
            continue
        h = [str(c).strip() if c else "" for c in rows[hi]]
        ti = h.index("Название объявления") if "Название объявления" in h else h.index("Title")
        pi = h.index("Цена") if "Цена" in h else (h.index("Price") if "Price" in h else None)
        ai = next((i for i, c in enumerate(h) if "Уникальный идентификатор" in c or c == "Id"), 0)
        for r in rows[hi + 1:]:
            if r and r[ai] and r[ti]:
                out.append({"id": str(r[ai]).strip(), "title": str(r[ti]), "price": r[pi] if pi is not None else None})
    return out


def _stats(item_ids: List[int]) -> Dict[int, dict]:
    import avito_client
    out: Dict[int, dict] = {}
    if not item_ids:
        return out
    try:
        me = avito_client._request("GET", "/core/v1/accounts/self")
        uid = me.get("id")
    except Exception as exc:  # noqa: BLE001
        print(f"Статистика: id аккаунта не получен ({exc}) — таблица будет без просмотров.")
        return out
    d_to, d_from = date.today(), date.today() - timedelta(days=269)
    for i in range(0, len(item_ids), 200):
        body = {"dateFrom": d_from.isoformat(), "dateTo": d_to.isoformat(), "itemIds": item_ids[i:i + 200],
                "fields": ["uniqViews", "uniqContacts", "uniqFavorites"], "periodGrouping": "month"}
        try:
            r = avito_client._request("POST", f"/stats/v1/accounts/{uid}/items", json=body)
        except Exception as exc:  # noqa: BLE001
            print(f"Статистика Авито не получена: {exc}")
            return out
        for it in (r.get("result") or {}).get("items") or []:
            s = {"views": 0, "contacts": 0, "fav": 0}
            for st in it.get("stats") or []:
                s["views"] += int(st.get("uniqViews") or 0)
                s["contacts"] += int(st.get("uniqContacts") or 0)
                s["fav"] += int(st.get("uniqFavorites") or 0)
            out[int(it.get("itemId"))] = s
    return out


def _match(title: str, feed: List[dict]) -> (Optional[dict], float):
    tk, cd = _tokens(title), _codes(title)
    best, score = None, 0.0
    for f in feed:
        ftk, fcd = _tokens(f["title"]) | _tokens(f["id"]), _codes(f["title"]) | _codes(f["id"])
        j = len(tk & ftk) / max(1, len(tk | ftk))
        if cd and cd & fcd:
            j += 0.5
        if j > score:
            best, score = f, j
    return best, score


def cmd_avito_dupes() -> int:
    import avito_client
    import openpyxl
    from openpyxl.styles import Font, PatternFill

    feed = _feed_rows()
    by_fid = {f["id"]: f for f in feed}
    try:
        rep = json.load(open(os.path.join(DATA, "avito_autoload.json"), encoding="utf-8")).get("items") or []
    except Exception:  # noqa: BLE001
        rep = []
    feed_avito = {}  # avito_id -> ad_id фида
    for r in rep:
        if r.get("avito_id") and r.get("ad_id"):
            feed_avito[int(r["avito_id"])] = re.sub(r"-av$", "", str(r["ad_id"]))
    items = avito_client.list_all_items(statuses=["active", "old", "rejected", "blocked", "removed"])
    print(f"Объявлений на Авито: {len(items)}; строк фида: {len(feed)}; привязок фида из отчёта: {len(feed_avito)}")
    stats = _stats([int(x["id"]) for x in items if x.get("id")])
    groups: Dict[str, list] = {}
    unmatched = []
    for x in items:
        aid = int(x["id"])
        st = stats.get(aid, {})
        row = {"avito_id": aid, "title": x.get("title"), "price": x.get("price"), "status": x.get("status"),
               "url": x.get("url"), "views": st.get("views"), "contacts": st.get("contacts"), "fav": st.get("fav")}
        if aid in feed_avito:
            row["kind"], row["score"] = "фид", 1.0
            groups.setdefault(feed_avito[aid], []).append(row)
            continue
        f, sc = _match(row["title"], feed)
        row["kind"], row["score"] = "ручное", round(sc, 2)
        if f and sc >= 0.25:
            groups.setdefault(f["id"], []).append(row)
        else:
            unmatched.append(row)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Дубли"
    head = ["Товар фида (Id)", "Название в фиде", "Цена фида", "Объявление Авито", "Тип", "Статус", "Цена",
            "Просмотры 9 мес", "Контакты", "Избранное", "Совпадение", "Предложение", "Название объявления", "Ссылка"]
    ws.append(head)
    for c in ws[1]:
        c.font = Font(bold=True)
    keep_fill = PatternFill("solid", fgColor="C6EFCE")
    off_fill = PatternFill("solid", fgColor="FFC7CE")
    n_dup = 0
    for fid, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if len(rows) < 2:
            continue
        n_dup += 1
        f = by_fid.get(fid, {})
        live = [r for r in rows if r["status"] in ("active", "old")] or rows
        best = max(live, key=lambda r: ((r["contacts"] or 0), (r["views"] or 0), (r["fav"] or 0), -r["avito_id"]))
        for r in sorted(rows, key=lambda r: r is not best):
            prop = "ОСТАВИТЬ (привязать к фиду)" if r is best else "снять"
            if r is best and r["kind"] == "фид":
                prop = "ОСТАВИТЬ (уже из фида)"
            ws.append([fid, f.get("title"), f.get("price"), r["avito_id"], r["kind"], r["status"], r["price"],
                       r["views"], r["contacts"], r["fav"], r["score"], prop, r["title"], r["url"]])
            for c in ws[ws.max_row]:
                c.fill = keep_fill if r is best else off_fill
        ws.append([])
    ws2 = wb.create_sheet("Без пары")
    ws2.append(["Объявление Авито", "Статус", "Цена", "Просмотры", "Контакты", "Название", "Ссылка"])
    for r in unmatched:
        ws2.append([r["avito_id"], r["status"], r["price"], r["views"], r["contacts"], r["title"], r["url"]])
    for w, col in zip((14, 40, 10, 14, 8, 9, 9, 10, 9, 9, 9, 26, 50, 40), "ABCDEFGHIJKLMN"):
        ws.column_dimensions[col].width = w
    wb.save(OUT)
    print(f"Товаров с дублями: {n_dup}; объявлений без пары (проверить глазами): {len(unmatched)}")
    print("Таблица: data/avito_dupes.xlsx (только чтение — на Авито ничего не менялось).")
    return 0
