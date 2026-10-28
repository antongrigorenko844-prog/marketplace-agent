"""
Rich-контент карточек Ozon (атрибут 11254) из файлов data/ozon_rich/<offer_id>.json.

Файл — обычный JSON Ozon (version 0.3), только вместо ссылок на картинки стоят метки
{{img:N}} = N-е фото карточки НА OZON (1 = главное), т.е. порядок photos/<артикул>/_1, _2...
Метки заменяются на ссылки Ozon (ir.ozone.ru) в момент отправки — свои фото Ozon точно покажет.

Отправка — /v1/product/attributes/update ТОЛЬКО с атрибутом 11254: название, фото, описание,
цена, остатки не трогаются. Уходит только изменившееся (data/ozon_rich_cache.json — хэш
итогового JSON). Если на Ozon ещё нет нужного фото (новые слайды только что ушли и Ozon их
обрабатывает) — ждём до ~4 мин; не дождались — карточка не отправляется и не кэшируется,
владельцу уходит сообщение в Telegram, уйдёт со следующим update-cards.

Запуск: шаг 3б update-cards (сам, после Push файлов в data/ozon_rich/);
        ozon-rich-dryrun — только проверка и итоговый JSON -> data/ozon_rich_preview.txt (ничего не меняет).
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from typing import Dict, List

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
RICH_DIR = os.path.join(DATA, "ozon_rich")
CACHE = os.path.join(DATA, "ozon_rich_cache.json")
PREVIEW = os.path.join(DATA, "ozon_rich_preview.txt")
RICH_ID = 11254
IMG_RE = re.compile(r"\{\{img:(\d+)\}\}")
WAIT_S, STEP_S = 240, 30


def _files() -> Dict[str, dict]:
    out = {}
    if not os.path.isdir(RICH_DIR):
        return out
    for fn in sorted(os.listdir(RICH_DIR)):
        if fn.endswith(".json"):
            with open(os.path.join(RICH_DIR, fn), "r", encoding="utf-8") as fh:
                out[fn[:-5]] = json.load(fh)
    return out


def _images(info: dict) -> List[str]:
    prim = info.get("primary_image") or []
    prim = [prim] if isinstance(prim, str) else list(prim)
    return prim + [u for u in (info.get("images") or []) if u not in prim]


def _resolve(doc: dict, imgs: List[str]):
    raw = json.dumps(doc, ensure_ascii=False)
    need = sorted({int(n) for n in IMG_RE.findall(raw)})
    missing = [n for n in need if n > len(imgs)]
    if missing:
        return None, missing
    return IMG_RE.sub(lambda m: imgs[int(m.group(1)) - 1], raw), []


def _load_cache() -> dict:
    try:
        with open(CACHE, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def run(dry_run: bool = False) -> int:
    import ozon_client as oc

    docs = _files()
    if not docs:
        print("Rich-контент: файлов в data/ozon_rich/ нет — пропускаю.")
        return 0
    cache = _load_cache()
    log: List[str] = []
    todo: Dict[str, str] = {}
    waiting = dict(docs)
    deadline = time.time() + (0 if dry_run else WAIT_S)
    while waiting:
        infos = {str(i.get("offer_id")): i for i in oc.get_product_info_list(list(waiting)) if i.get("offer_id")}
        still = {}
        for art, doc in waiting.items():
            inf = infos.get(art)
            if not inf:
                log.append(f"{art}: карточки нет на Ozon — пропускаю")
                continue
            val, missing = _resolve(doc, _images(inf))
            if missing:
                still[art] = doc
                continue
            h = hashlib.sha256(val.encode("utf-8")).hexdigest()[:16]
            if cache.get(art) == h and not dry_run:
                continue
            todo[art] = val
            log.append(f"{art}: rich готов ({len(val)} знаков, фото на Ozon: {len(_images(inf))})")
        waiting = still
        if not waiting or time.time() >= deadline:
            break
        print(f"Rich: жду, пока Ozon обработает новые фото ({', '.join(waiting)})…", flush=True)
        time.sleep(STEP_S)
    for art in waiting:
        log.append(f"{art}: НЕ отправлено — на Ozon ещё нет всех фото, на которые ссылается rich (уйдёт со следующим update-cards)")

    if dry_run:
        with open(PREVIEW, "w", encoding="utf-8") as fh:
            fh.write("\n".join(log) + "\n\n")
            for art, val in todo.items():
                fh.write(f"===== {art}\n{json.dumps(json.loads(val), ensure_ascii=False, indent=1)}\n\n")
        print("\n".join(log))
        return 0

    problems = [l for l in log if "НЕ отправлено" in l]
    if todo:
        items = [{"offer_id": a, "attributes": [{"id": RICH_ID, "complex_id": 0, "values": [{"value": v}]}]}
                 for a, v in todo.items()]
        resp = oc.update_attributes(items)
        task = resp.get("task_id") or (resp.get("result") or {}).get("task_id")
        log.append(f"Отправлено rich: {len(items)}; task_id={task}")
        its = []
        if task:
            for _ in range(12):
                time.sleep(10)
                st = oc.get_import_status(int(task))
                its = (st.get("result") or {}).get("items") or st.get("items") or []
                if its and all(str(i.get("status")) not in ("pending", "processing", "") for i in its):
                    break
        bad = set()
        for i in its:
            errs = i.get("errors") or []
            if errs or str(i.get("status")) in ("failed", "error"):
                bad.add(str(i.get("offer_id")))
                problems.append(f"{i.get('offer_id')}: Ozon не принял rich — {json.dumps(errs, ensure_ascii=False)[:300]}")
            log.append(f"  Ozon: {i.get('offer_id')} {i.get('status')} {json.dumps(errs, ensure_ascii=False)[:400] if errs else ''}")
        for a, v in todo.items():
            if a not in bad:
                cache[a] = hashlib.sha256(v.encode("utf-8")).hexdigest()[:16]
        with open(CACHE, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
    else:
        log.append("Rich-контент: изменений нет.")
    print("\n".join(log))
    if problems and os.getenv("GITHUB_ACTIONS"):
        try:
            import direct_tools
            direct_tools._tg_owner("⚠️ Rich-контент Ozon:\n" + "\n".join(problems)[:3500])
        except Exception as exc:  # noqa: BLE001
            print(f"(Telegram не отправлен: {exc})")
    return 0


def cmd_ozon_rich_dryrun() -> int:
    return run(dry_run=True)
