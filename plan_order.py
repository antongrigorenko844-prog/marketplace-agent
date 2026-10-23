"""
Порядок постов в data/content_plan.xlsx (команды plan-reorder / plan-reorder-dryrun).

ПРАВИЛА
1) В первую очередь — посты с «да» в колонке «Приоритет». Колонка заполняется сама: «да» для рубрики
   «Мехатроники» (мехатроники и электронные платы). Можно поставить «да» любому посту вручную.
   Новые мехатроники/платы, добавленные в план с рубрикой «Мехатроники», тоже уйдут в начало очереди.
2) Соседние посты не повторяются:
   - в соседние дни не бывает двух постов одной рубрики (кроме приоритетных — им можно, если это разные
     товары: мехатроник DQ200 → плата → мехатроник DQ250);
   - похожие детали (кроме приоритетных) (например «Сепараторная пластина» и «Оригинальная сепараторная пластина») —
     не ближе чем через SIMILAR_GAP дней;
   - остальные рубрики равномерно перемешаны по всему плану;
   - полезные посты (рубрика «Полезное») — не чаще одного раза в INFO_GAP дней (2 недели). Если товарные
     посты закончились раньше, оставшиеся полезные встают в конец плана тоже раз в 2 недели (в дни между
     ними постов нет, пока в план не добавятся новые товары — после plan-reorder они заполнят эти дни).
3) Не трогаются (дата остаётся): уже опубликованные, уже отправленные дизайнеру, уже показанные владельцу
   на проверку и те, по которым дизайнер прислал макет (social/<№>/). Номера постов не меняются —
   меняются только даты (папки social/<№>/ и тексты data/post_texts/<№>.txt остаются привязаны к посту).
Посты идут каждый день подряд, начиная с даты первого поста.
"""
import datetime as dt
import json
import os
import re
from typing import Dict, List

import openpyxl

import social_publish as sp

_ROOT = os.path.dirname(os.path.abspath(__file__))
DESIGNER_STATE = os.path.join(_ROOT, "data", "designer_state.json")
SIMILAR_GAP = 4
# Приоритетные посты не раньше этой даты (ГГГГ-ММ-ДД), пусто — сразу. Пост, который уже стоит раньше,
# но ещё не опубликован и без макета дизайнера, переносится.
PRIORITY_FROM = os.getenv("PLAN_PRIORITY_FROM", "2026-10-26").strip()
INFO_RUBRICS = {"полезное"}
# Пост без готовых картинок (нет фото в каталоге, нужен макет дизайнера) — не раньше чем через DESIGN_LEAD дней
DESIGN_LEAD = 4
INFO_GAP = 14
PRIORITY_RUBRICS = {"мехатроники"}
DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_STOP = {"оригинальный", "оригинальная", "оригинальные", "оригинальное", "усиленный", "усиленная", "усиленные",
         "усиленное", "новый", "новая", "новые", "полный", "комплект", "комплект:", "ремкомплект", "капремонт",
         "+", "—", "-", "и", "для", "с", "2", "4", "шт"}


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _kind(title: str) -> str:
    """Тип детали по заголовку: первое значимое слово (без «оригинальная/усиленная/новый…»)."""
    words = re.findall(r"[a-zа-яё0-9\-]+", (title or "").lower())
    for w in words:
        if w not in _STOP and not w.isdigit():
            return w[:7]
    return (title or "")[:7].lower()


def _family(r: dict) -> str:
    """Для приоритетных: плата / мехатроник конкретной коробки."""
    t = str(r.get("title") or "").lower()
    if t.startswith(("электронная плата", "плата", "блок управления")):
        return "плата"
    m = re.search(r"dq\d{3}|0b5|02e|0am", t)
    return "мехатроник " + (m.group(0) if m else t[:12])


def _locked_numbers() -> set:
    st = _load(sp.STATE_PATH, {}).get("posts", {})
    ds = _load(DESIGNER_STATE, {})
    out = {int(n) for n, v in st.items() if v}
    out |= {int(n) for n in (ds.get("sent") or {})}
    out |= {int(n) for n in (ds.get("previewed") or {})}
    if os.path.isdir(sp.IMAGES_DIR):
        for d in os.listdir(sp.IMAGES_DIR):
            if d.isdigit() and sp.post_images(int(d), []):
                out.add(int(d))
    return out


def reorder(dry_run: bool) -> int:
    wb = openpyxl.load_workbook(sp.PLAN_PATH)
    ws = wb[sp.SHEET]
    head = [str(c.value or "").strip() for c in ws[1]]
    if "Приоритет" not in head:
        ws.cell(1, len(head) + 1).value = "Приоритет"
        ws.cell(1, len(head) + 1)._style = ws.cell(1, len(head))._style
        head.append("Приоритет")
    col = {h: i for i, h in enumerate(head)}
    ncols = len(head)

    rows = []
    for r in range(2, ws.max_row + 1):
        if ws.cell(r, 1).value is None:
            continue
        cells = [ws.cell(r, c + 1) for c in range(ncols)]
        rows.append({
            "vals": [c.value for c in cells], "styles": [c._style for c in cells],
            "n": int(cells[col["№"]].value),
            "date": cells[col["Дата выхода"]].value,
            "rub": str(cells[col["Рубрика / серия"]].value or "").strip(),
            "title": str(cells[col["Заголовок на макете"]].value or ""),
        })
    for x in rows:
        if isinstance(x["date"], dt.datetime):
            x["date"] = x["date"].date()
        pr = str(x["vals"][col["Приоритет"]] or "").strip().lower()
        if not pr and x["rub"].lower() in PRIORITY_RUBRICS:
            x["vals"][col["Приоритет"]] = pr = "да"
        x["prio"] = pr in ("да", "1", "yes", "+")
        x["kind"] = _kind(x["title"])
        x["info"] = x["rub"].lower() in INFO_RUBRICS
        arts = [a.strip() for a in str(x["vals"][col["Артикулы в master"]] or "").split(",") if a.strip() and a.strip() != "—"]
        x["needs_design"] = not sp.post_images(x["n"], arts)

    start = min(x["date"] for x in rows if isinstance(x["date"], dt.date))
    locked = _locked_numbers()
    pfrom = dt.date.fromisoformat(PRIORITY_FROM) if PRIORITY_FROM else None
    if pfrom:
        published = {int(n) for n, v in _load(sp.STATE_PATH, {}).get("posts", {}).items() if v}
        for x in rows:
            if (x["prio"] and isinstance(x["date"], dt.date) and x["date"] < pfrom and x["n"] in locked
                    and x["n"] not in published and not sp.post_images(x["n"], []) ):
                locked.discard(x["n"])
                print(f"Пост №{x['n']} (приоритетный) переношу: раньше {pfrom:%d.%m} приоритетные не ставим.")
    sched: Dict[dt.date, dict] = {}
    for x in rows:
        if x["n"] in locked and isinstance(x["date"], dt.date):
            sched[x["date"]] = x
    free = [x for x in rows if x not in sched.values()]
    orig = sorted(free, key=lambda x: (x["date"] or dt.date.max, x["n"]))

    prio = [x for x in orig if x["prio"]]
    # остальные — равномерно по рубрикам
    by_rub: Dict[str, List[dict]] = {}
    for x in orig:
        if not x["prio"]:
            by_rub.setdefault(x["rub"], []).append(x)
    first = {rub: i for i, rub in enumerate(by_rub)}
    rest = sorted((x for lst in by_rub.values() for x in lst),
                  key=lambda x: ((by_rub[x["rub"]].index(x) + 0.5) / len(by_rub[x["rub"]]), first[x["rub"]]))

    def ok(x, d, strict_rub=True):
        if x["prio"] and pfrom and d < pfrom:
            return False
        if x["needs_design"] and d < earliest_design:
            return False
        prev = sched.get(d - dt.timedelta(days=1))
        nxt = sched.get(d + dt.timedelta(days=1))
        for nb in (prev, nxt):
            if not nb:
                continue
            if x["prio"] and nb["prio"]:
                if _family(x) == _family(nb):
                    return False
            elif strict_rub and x["rub"] == nb["rub"]:
                return False
        if x["info"]:
            for k in range(1, INFO_GAP):
                for dd in (d - dt.timedelta(days=k), d + dt.timedelta(days=k)):
                    o = sched.get(dd)
                    if o and o["info"]:
                        return False
        for k in range(1, SIMILAR_GAP + 1):
            for dd in (d - dt.timedelta(days=k), d + dt.timedelta(days=k)):
                o = sched.get(dd)
                if o and o["kind"] == x["kind"] and o["rub"] == x["rub"] and not (x["prio"] and o["prio"]):
                    return False
        return True

    earliest_design = dt.datetime.now(sp.MSK).date() + dt.timedelta(days=DESIGN_LEAD)

    def info_ok(x, d):
        return not x["info"] or ok(x, d, False)

    d = start - dt.timedelta(days=1)
    while prio or rest:
        d += dt.timedelta(days=1)
        if d in sched:
            continue
        pick = None
        for pool, strict in ((prio, True), (rest, True), (rest, False), (prio, False)):
            pick = next((x for x in pool if ok(x, d, strict)), None)
            if pick:
                pool.remove(pick)
                break
        if not pick:
            # ослабляем правила, но полезные посты всё равно не чаще раза в 2 недели
            pool = prio if prio else rest
            pick = next((x for x in pool if info_ok(x, d) and not (x["prio"] and pfrom and d < pfrom)), None)
            if not pick:
                continue  # остались только полезные — этот день пропускаем
            pool.remove(pick)
        sched[d] = pick

    changes = [(x["n"], x["date"], d) for d, x in sched.items() if x["date"] != d]
    print(f"Постов: {len(rows)} · не трогаю (опубликованы/у дизайнера/на проверке): {sorted(locked)}")
    print(f"Приоритетных (мехатроники/платы): {sum(1 for x in rows if x['prio'])} · дат меняется: {len(changes)}")
    for d in sorted(sched)[:30]:
        x = sched[d]
        print(f"  {d:%d.%m} {DAYS[d.weekday()]} · №{x['n']:<3} {'★' if x['prio'] else ' '} {x['rub'][:22]:<22} {x['title'][:50]}")
    if len(sched) > 30:
        print(f"  … ещё {len(sched) - 30}")
    if dry_run:
        print("Пробный режим — файл не изменён.")
        return 0

    for i, d in enumerate(sorted(sched), start=2):
        x = sched[d]
        x["vals"][col["Дата выхода"]] = dt.datetime.combine(d, dt.time())
        x["vals"][col["День"]] = DAYS[d.weekday()]
        for c in range(ncols):
            cell = ws.cell(i, c + 1)
            cell.value = x["vals"][c]
            cell._style = x["styles"][c] if x["styles"][c] is not None else cell._style
    wb.save(sp.PLAN_PATH)
    print("Сохранено: data/content_plan.xlsx (строки отсортированы по дате выхода).")
    return 0
