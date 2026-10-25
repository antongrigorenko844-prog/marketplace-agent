"""
Исправление поля «номер детали» на карточках: Ozon — характеристика 7236 «Партномер»/«Артикул производителя»,
WB — характеристика «Артикул производителя». Меняется ТОЛЬКО это поле (название, фото, цены, остатки — нет).
Список правок — FIXES ниже (согласован с владельцем 25.10.2026). Команды fix-partnumbers(-dryrun).
"""
import copy
import json
import os

_ROOT = os.path.dirname(os.path.abspath(__file__))
# offer_id: (новый номер, площадки)
FIXES = {
    "02E927770AL": ("02E927770AL", ("ozon",)),
    "0AM301212A": ("0AM301212A", ("ozon", "wb")),
    "0am325025": ("0AM325025", ("ozon", "wb")),
    "0am325025n": ("0AM325025N", ("ozon", "wb")),
    "0AM927769N": ("0AM927769N", ("ozon",)),
    "korpus-filtra-0bh325159": ("0BH325159", ("ozon",)),
    "filtr-dsg7-dq500": ("0BH325183B", ("wb",)),
}
WB_CHAR = "Артикул производителя"


def cmd_fix_partnumbers(dry_run: bool) -> int:
    import builtins
    import io
    log = io.StringIO()
    _print = builtins.print

    def print(*a, **k):  # noqa: A001 — дублируем вывод в файл, чтобы итог был виден в репозитории
        _print(*a, **k)
        _print(*a, file=log)
    try:
        return _run(dry_run, print)
    finally:
        with open(os.path.join(_ROOT, "data", "fix_partnumbers_result.txt"), "w", encoding="utf-8") as fh:
            fh.write(log.getvalue())


def _run(dry_run, print):
    import main
    main.cmd_fetch_ozon()
    main.cmd_fetch_wb()
    with open(os.path.join(_ROOT, "data", "ozon_products.json"), "r", encoding="utf-8") as f:
        oz = {it["offer_id"]: it for it in json.load(f)["names"]}
    with open(os.path.join(_ROOT, "data", "wb_cards.json"), "r", encoding="utf-8") as f:
        wb = {c["vendorCode"]: c for c in json.load(f)["cards"]}
    oz_items, wb_items = [], []
    for oid, (val, where) in FIXES.items():
        if "ozon" in where:
            it = oz.get(oid)
            cur = [v.get("value") for a in (it or {}).get("attributes", []) if a["id"] == 7236 for v in a.get("values", [])]
            print(f"Ozon {oid}: {cur} -> {val}" + ("" if it else " (карточки нет — пропуск)"))
            if it and cur != [val]:
                oz_items.append({"offer_id": oid, "attributes": [{"id": 7236, "complex_id": 0, "values": [{"value": val}]}]})
        if "wb" in where:
            c = wb.get(oid)
            if not c:
                print(f"WB {oid}: карточки нет — пропуск")
                continue
            chars = copy.deepcopy(c.get("characteristics") or [])
            ch = next((x for x in chars if x.get("name") == WB_CHAR), None)
            print(f"WB {oid}: {ch.get('value') if ch else 'нет поля'} -> {val}")
            if ch is None:
                print(f"WB {oid}: у карточки нет поля «{WB_CHAR}» — пропуск")
                continue
            if ch.get("value") == [val]:
                continue
            ch["value"] = [val]
            wb_items.append({"nmID": c["nmID"], "vendorCode": oid, "kizMarked": c.get("kizMarked", False),
                             "brand": c.get("brand", ""), "title": c.get("title", ""), "description": c.get("description", ""),
                             "dimensions": c.get("dimensions") or {},
                             "characteristics": [{"id": x["id"], "value": x["value"]} for x in chars if "id" in x],
                             "sizes": c.get("sizes") or []})
    print(f"К отправке: Ozon {len(oz_items)}, WB {len(wb_items)}")
    if dry_run:
        print("Пробный режим — ничего не отправлено.")
        return 0
    if oz_items:
        import ozon_client
        print("Ozon:", ozon_client.update_attributes(oz_items))
    if wb_items:
        import wb_client
        print("WB:", wb_client.update_cards(wb_items))
    return 0
