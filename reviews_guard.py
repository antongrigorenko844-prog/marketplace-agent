"""
Защита карточек с отзывами — ГЛАВНОЕ ПРАВИЛО владельца (2026-10-27):
никогда не навредить и не снять с витрины карточку, у которой есть отзывы.

Вызывается ПЕРЕД любым действием, которое прячет карточку:
  archive-ozon-products, remove-product (остаток 0 везде + архив Ozon), wb-zero-stock.

Решение по каждому артикулу:
  • отзывы найдены (>0)                         → ЗАПРЕЩЕНО, без исключений;
  • проверить не удалось (нет доступа к API,
    ошибка, товар не найден)                    → ЗАПРЕЩЕНО, пока в master_control.xlsx в колонке
                                                  «Отзывов нет (проверено)» не стоит «да»
                                                  (владелец сам посмотрел в кабинете);
  • отзывов 0 по всем проверяемым площадкам     → можно.
Неизвестно = нельзя.

Источники:
  Ozon — /v1/review/list (только с подпиской Premium Plus; без неё — «не удалось проверить»).
         Фильтра по SKU у метода нет — листаем все отзывы и считаем по sku.
  WB   — feedbacks-api: /api/v1/feedbacks (отвеченные и неотвеченные) и /api/v1/feedbacks/archive
         с фильтром nmId. Нужен токен с доступом к «Вопросам и отзывам».
Отчёт последней проверки — data/reviews_guard.txt.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Tuple

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
MASTER = os.path.join(DATA, "master_control.xlsx")
REPORT = os.path.join(DATA, "reviews_guard.txt")
CONFIRM_HEADER = "Отзывов нет (проверено)"
WB_FEEDBACKS = "https://feedbacks-api.wildberries.ru"


# ---------------------------------------------------------------- сопоставление артикулов
def _ozon_skus(offer_ids: List[str]) -> Dict[str, set]:
    out: Dict[str, set] = {o: set() for o in offer_ids}
    try:
        import ozon_client

        for it in ozon_client.get_product_info_list(offer_ids):
            if not isinstance(it, dict) or it.get("offer_id") not in out:
                continue
            s = out[it["offer_id"]]
            if it.get("sku"):
                s.add(str(it["sku"]))
            for src in it.get("sources") or []:
                if src.get("sku"):
                    s.add(str(src["sku"]))
    except Exception:  # noqa: BLE001 — дальше пробуем локальный снимок
        pass
    try:
        d = json.load(open(os.path.join(DATA, "ozon_products.json"), encoding="utf-8"))
        for x in d.get("list", []) + d.get("details", []):
            o = x.get("offer_id")
            if o in out:
                if x.get("sku"):
                    out[o].add(str(x["sku"]))
                for src in x.get("sources") or []:
                    if src.get("sku"):
                        out[o].add(str(src["sku"]))
    except Exception:  # noqa: BLE001
        pass
    return out


def _wb_nm(offer_ids: List[str]) -> Dict[str, Optional[int]]:
    out: Dict[str, Optional[int]] = {o: None for o in offer_ids}
    try:
        d = json.load(open(os.path.join(DATA, "wb_cards.json"), encoding="utf-8"))
        for c in d.get("cards", []):
            v = str(c.get("vendorCode") or "")
            if v in out and c.get("nmID"):
                out[v] = int(c["nmID"])
    except Exception:  # noqa: BLE001
        pass
    return out


# ---------------------------------------------------------------- подсчёт отзывов
def ozon_counts(sku_sets: Dict[str, set]) -> Tuple[Dict[str, Optional[int]], str]:
    """{offer_id: число отзывов | None}, пояснение. None = проверить не удалось."""
    import ozon_client

    if not any(sku_sets.values()):
        return {o: None for o in sku_sets}, "SKU Ozon не найдены"
    per_sku: Dict[str, int] = {}
    last_id = ""
    try:
        for _ in range(500):  # до 50 000 отзывов
            body = {"status": "ALL", "limit": 100, "sort_dir": "DESC"}
            if last_id:
                body["last_id"] = last_id
            r = ozon_client._post("/v1/review/list", body)
            for rv in r.get("reviews") or []:
                k = str(rv.get("sku") or "")
                per_sku[k] = per_sku.get(k, 0) + 1
            last_id = r.get("last_id") or ""
            if not r.get("has_next") or not last_id:
                break
        else:
            return {o: None for o in sku_sets}, "слишком много отзывов, не дочитано"
    except Exception as exc:  # noqa: BLE001
        return {o: None for o in sku_sets}, f"API отзывов Ozon недоступно ({str(exc)[:120]})"
    out = {}
    for o, skus in sku_sets.items():
        out[o] = sum(per_sku.get(s, 0) for s in skus) if skus else None
    return out, "API отзывов Ozon"


def _wb_has(nm: int) -> bool:
    import wb_client

    for path in (f"/api/v1/feedbacks?isAnswered=true&nmId={nm}&take=1&skip=0",
                 f"/api/v1/feedbacks?isAnswered=false&nmId={nm}&take=1&skip=0",
                 f"/api/v1/feedbacks/archive?nmId={nm}&take=1&skip=0"):
        r = wb_client._request("GET", WB_FEEDBACKS, path)
        data = r.get("data") if isinstance(r, dict) else None
        if not isinstance(data, dict) or "feedbacks" not in data:
            raise RuntimeError(f"неожиданный ответ WB {path}: {str(r)[:150]}")
        if data.get("feedbacks"):
            return True
    return False


def wb_counts(nms: Dict[str, Optional[int]]) -> Tuple[Dict[str, Optional[int]], str]:
    """{offer_id: 1 (есть) | 0 (нет) | None (не удалось)}."""
    out: Dict[str, Optional[int]] = {}
    note = "API отзывов WB"
    for o, nm in nms.items():
        if not nm:
            out[o] = None
            continue
        try:
            out[o] = 1 if _wb_has(nm) else 0
        except Exception as exc:  # noqa: BLE001
            out[o], note = None, f"API отзывов WB недоступно ({str(exc)[:120]})"
    return out, note


# ---------------------------------------------------------------- решение
def _confirmed(offer_ids: List[str]) -> set:
    try:
        import openpyxl

        rows = openpyxl.load_workbook(MASTER, read_only=True, data_only=True)["Товары"].iter_rows(values_only=True)
        head = [str(c or "").strip() for c in next(rows)]
        if CONFIRM_HEADER not in head:
            return set()
        i = head.index(CONFIRM_HEADER)
        return {str(r[0]).strip() for r in rows
                if r and r[0] and len(r) > i and str(r[i] or "").strip().lower() == "да"
                and str(r[0]).strip() in offer_ids}
    except Exception:  # noqa: BLE001
        return set()


def check(offer_ids: List[str], channels=("ozon", "wb"), _counts=None) -> Tuple[List[str], List[str]]:
    """(можно, нельзя). Печатает решение по каждому артикулу и пишет data/reviews_guard.txt.
    _counts — только для тестов: {"ozon": ({...}, note), "wb": ({...}, note)}."""
    counts = _counts or {}
    if "ozon" in channels and "ozon" not in counts:
        counts["ozon"] = ozon_counts(_ozon_skus(offer_ids))
    if "wb" in channels and "wb" not in counts:
        nms = _wb_nm(offer_ids)
        # товара нет на WB совсем — WB проверять незачем
        counts["wb"] = wb_counts({o: n for o, n in nms.items() if n})
        counts["wb"][0].update({o: 0 for o, n in nms.items() if not n})
    confirmed = _confirmed(offer_ids)
    allowed, blocked, lines = [], [], []
    for o in offer_ids:
        parts, has, unknown = [], False, False
        for ch in channels:
            v = counts[ch][0].get(o)
            name = "Ozon" if ch == "ozon" else "WB"
            if v is None:
                unknown = True
                parts.append(f"{name}: не проверено ({counts[ch][1]})")
            elif v > 0:
                has = True
                parts.append(f"{name}: ЕСТЬ отзывы ({v if ch == 'ozon' else 'да'})")
            else:
                parts.append(f"{name}: отзывов нет")
        if has:
            verdict = "ЗАПРЕЩЕНО — есть отзывы"
            blocked.append(o)
        elif unknown and o not in confirmed:
            verdict = f"ЗАПРЕЩЕНО — не удалось проверить; если в кабинете отзывов нет, поставьте «да» в колонке «{CONFIRM_HEADER}» мастера"
            blocked.append(o)
        else:
            verdict = "можно" + (" (отзывов нет — подтверждено владельцем в мастере)" if unknown else "")
            allowed.append(o)
        lines.append(f"{o}: {verdict}. " + "; ".join(parts))
    print("\n=== Проверка отзывов (карточки с отзывами не трогаем) ===")
    for ln in lines:
        print("  " + ln)
    try:
        with open(REPORT, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    except OSError:
        pass
    return allowed, blocked
