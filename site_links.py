"""
Ссылки на товары на сайте (Tilda): data/site_links.json {артикул (в нижнем регистре): ссылка на карточку}.
Адрес карточки на Tilda (.../tproduct/<uid>-<название>) не выводится из нашего артикула, поэтому берём его
с самого сайта: страница каталога -> id блока магазина -> публичный список товаров магазина Tilda
(тот же, что загружает каталог в браузере) -> sku + url. Запасной путь — карта сайта sitemap-store.xml
(там нет артикулов, сопоставляем по названию, только точные совпадения).
"""
import json
import os
import re
from typing import Dict

import requests

_ROOT = os.path.dirname(os.path.abspath(__file__))
SITE = os.getenv("SITE_URL", "https://leoparts-akpp.ru").rstrip("/")
OUT = os.path.join(_ROOT, "data", "site_links.json")
PRICES_OUT = os.path.join(_ROOT, "data", "site_prices.json")  # живые цены с сайта {артикул: цена}
PRICES: Dict[str, float] = {}
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"}


def _store_parts(html: str):
    parts = set(re.findall(r'storepart(?:uid|_uid|Uid)?["\']?\s*[:=]\s*["\']?(\d{6,})', html, re.I))
    parts |= set(re.findall(r'"storepart"\s*:\s*"(\d+)"', html))
    recids = set(re.findall(r'id="rec(\d+)"', html))
    return parts, recids


def fetch() -> Dict[str, str]:
    links: Dict[str, str] = {}
    html = ""
    for path in ("/catalog", "/"):
        try:
            r = requests.get(SITE + path, headers=UA, timeout=30)
            if r.ok:
                html += r.text
        except requests.RequestException as exc:
            print(f"Сайт {SITE}{path}: {exc}")
    parts, recids = _store_parts(html)
    print(f"Сайт: найдено блоков магазина {len(parts)} {sorted(parts)[:5]}, блоков страницы {len(recids)}")
    for part in parts:
        slice_ = 1
        while slice_ < 50:
            try:
                r = requests.get("https://store.tildaapi.com/api/getproductslist/", headers=UA, timeout=30,
                                 params={"storepartuid": part, "recid": next(iter(recids), ""), "c": "", "getparts": "true",
                                         "getoptions": "true", "slice": slice_, "size": 100})
                data = r.json()
            except Exception as exc:  # noqa
                print(f"Список товаров Tilda (блок {part}): {exc}")
                break
            prods = data.get("products") or []
            for p in prods:
                sku = str(p.get("sku") or "").strip()
                url = str(p.get("url") or "").strip()
                if sku and url:
                    links[sku.lower()] = url
                try:
                    if sku and float(p.get("price") or 0) > 0:
                        PRICES[sku.lower()] = float(p["price"])
                except (TypeError, ValueError):
                    pass
                for e in p.get("editions") or []:
                    if e.get("sku") and url:
                        links.setdefault(str(e["sku"]).strip().lower(), url)
            if not data.get("nextslice") or not prods:
                break
            slice_ = int(data["nextslice"])
    print(f"Сайт: ссылок на товары по артикулу — {len(links)}")
    return links


def cmd_site_links() -> int:
    links = fetch()
    if not links:
        print("Ссылки не получены — файл не меняю.")
        return 1
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(links.items())), f, ensure_ascii=False, indent=2)
    print(f"Сохранено: {OUT}")
    if PRICES:
        with open(PRICES_OUT, "w", encoding="utf-8") as f:
            json.dump(dict(sorted(PRICES.items())), f, ensure_ascii=False, indent=2)
        print(f"Цены сайта: {len(PRICES)} → {PRICES_OUT}")
    for k in list(links)[:5]:
        print(f"  {k} -> {links[k]}")
    return 0


def load() -> Dict[str, str]:
    try:
        with open(OUT, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
