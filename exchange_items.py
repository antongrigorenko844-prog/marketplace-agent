"""
Обмен мехатроников (условия владельца, 2026-10-27): покупатель отправляет свой старый мехатроник —
без платы: −4 000 ₽, с платой: −6 000 ₽; доставку старого оплачиваем мы; принимаем, если не ржавый,
не был в воде и подлежит восстановлению; не отправил старый — доплачивает ту же сумму.

Решение владельца 29.10.2026 (вариант А): ОДНА карточка по обычной цене, обмен = ВОЗВРАТ денег —
покупатель платит полную цену, присылает старый мехатроник, после проверки ему возвращают 4 000 / 6 000 ₽
(так же написано на фото карточек: «Пришлите свой старый механ — вернём 4000 ₽»).
- Сайт: отдельные карточки «по обмену» больше НЕ выгружаются (site_card: false в data/exchange_items.json);
  включить снова — site_card: true.
- Авито: отдельных объявлений НЕ создаём (Авито блокирует за повторное размещение одного товара) —
  в описание основного объявления добавляется абзац с ценой по обмену (avito_feed.build_avito_feed).
"""
import json
import os
from typing import Dict, List

PATH = os.path.join(os.path.dirname(__file__), "data", "exchange_items.json")


def load(path: str = PATH) -> List[Dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return [x for x in json.load(f).get("items", []) if x.get("id") and x.get("parent")]
    except (OSError, ValueError):
        return []


def _fmt(n) -> str:
    return f"{int(n):,}".replace(",", " ")


def terms_text(discount: int, with_board: bool) -> str:
    what = "с платой" if with_board else "без платы"
    return (
        f"🔄 ОБМЕН: пришлите нам свой старый мехатроник ({what}) — вернём {_fmt(discount)} ₽. "
        "Доставку старого мехатроника оплачиваем мы. Принимаем, если он не ржавый, не был в воде и подлежит восстановлению."
    )


def site_items(items: List[Dict], path: str = PATH) -> List[Dict]:
    """Готовые позиции сайта «по обмену» на основе уже собранных items (формат tilda_commerceml)."""
    by_id = {it["id"]: it for it in items}
    out = []
    for x in load(path):
        if not x.get("site_card"):
            continue  # 29.10.2026: обмен = возврат денег, отдельных карточек на сайте нет
        p = by_id.get(x["parent"])
        if not p or not p.get("price"):
            continue  # основного ещё нет на сайте (например, ждёт создания на Ozon) — подхватится позже
        disc = int(x.get("discount") or 0)
        what = "с платой" if x.get("with_board") else "без платы"
        out.append(
            {
                "id": x["id"],
                "title": f"{p['title']} — по обмену на старый ({what})",
                "description": terms_text(disc, bool(x.get("with_board"))) + "<br/><br/>" + (p.get("description") or ""),
                "price": int(p["price"]) - disc,
                "qty": p.get("qty"),
                "photo": p.get("photo") or "",
                "photo_files": list(p.get("photo_files") or []),
            }
        )
    return out


def avito_note(offer_id: str, avito_price, path: str = PATH) -> str:
    """Абзац для объявления Авито основного товара (пусто, если обмена у товара нет)."""
    for x in load(path):
        if x["parent"] == offer_id:
            disc = int(x.get("discount") or 0)
            what = "с платой" if x.get("with_board") else "без платы"
            return (
                f"🔄 Обмен: пришлите свой старый мехатроник ({what}) — вернём {_fmt(disc)} ₽. "
                "Доставку старого оплачиваем мы; принимаем, если не ржавый, не был в воде и подлежит восстановлению. "
                "Напишите «обмен» в сообщения."
            )
    return ""
