"""
Ссылки на страницы наших магазинов на маркетплейсах -> data/store_links.json (для шапки Instagram и т.п.).
Ozon: номер продавца = Client-Id API. WB: supplierId из публичной карточки любого нашего товара.
Маркет: businessId из API; адрес страницы проверяется переходом.
"""
import json
import os
import re

import requests

_ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(_ROOT, "data", "store_links.json")
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"}


def _try(url):
    try:
        r = requests.get(url, headers=UA, timeout=30, allow_redirects=True)
        return r.status_code, r.url
    except Exception as exc:  # noqa
        return None, str(exc)[:100]


def cmd_store_links() -> int:
    out = {}
    cid = os.getenv("OZON_CLIENT_ID", "").strip()
    if cid:
        out["ozon"] = f"https://www.ozon.ru/seller/{cid}/"
        print("Ozon:", out["ozon"], _try(out["ozon"]))
    try:
        with open(os.path.join(_ROOT, "data", "wb_cards.json"), "r", encoding="utf-8") as f:
            nms = [c["nmID"] for c in json.load(f)["cards"][:5]]
        for nm in nms:
            for url in (f"https://card.wb.ru/cards/v4/detail?appType=1&curr=rub&dest=-1257786&nm={nm}",
                        f"https://card.wb.ru/cards/v2/detail?appType=1&curr=rub&dest=-1257786&nm={nm}"):
                try:
                    j = requests.get(url, headers=UA, timeout=30).json()
                    prods = (j.get("data") or {}).get("products") or j.get("products") or []
                    if prods and prods[0].get("supplierId"):
                        out["wb"] = f"https://www.wildberries.ru/seller/{prods[0]['supplierId']}"
                        out["wb_name"] = prods[0].get("supplier", "")
                        break
                except Exception as exc:  # noqa
                    print("WB карточка:", url[:60], exc)
            if "wb" in out:
                break
        print("WB:", out.get("wb"), out.get("wb_name"))
    except Exception as exc:  # noqa
        print("WB:", exc)
    try:
        import yandex_market_client as ym
        biz = ym.get_business_id()
        out["ym_business_id"] = biz
        for url in (f"https://market.yandex.ru/business--leoparts/{biz}", f"https://market.yandex.ru/business/{biz}"):
            st, final = _try(url)
            print("Маркет:", url, st, final)
            if st == 200:
                out["ym"] = final.split("?")[0]
                break
        out.setdefault("ym", f"https://market.yandex.ru/business--leoparts/{biz}")
    except Exception as exc:  # noqa
        print("Маркет:", exc)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0
