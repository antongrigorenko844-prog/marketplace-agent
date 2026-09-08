"""
Точка входа. На этом первом этапе агент умеет:
  --test-ozon        проверить, что ключ Ozon работает
  --test-wb          проверить, что токен WB работает
  --fetch-ozon        выгрusить список товаров Ozon в data/ozon_products.json
  --fetch-wb          выгрузить список карточек WB в data/wb_cards.json

Это самый первый шаг — посмотреть на реальные данные из ваших кабинетов,
прежде чем строить общий каталог и правила сопоставления товаров между
площадками (это будет следующим шагом, в catalog.py).
"""
import argparse
import json
import logging
import os
import subprocess
import sys
import time

from config import config

logging.basicConfig(
    level=config.log_level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("marketplace-agent.main")


def _data_path(filename: str) -> str:
    data_dir = os.path.join(os.path.dirname(__file__), "data")
    os.makedirs(data_dir, exist_ok=True)
    return os.path.join(data_dir, filename)


def _split_errors_by_level(errors):
    """
    Ozon кладёт в 'errors' не только настоящие ошибки, но и информационные
    предупреждения (level='warning', например "заменили значение на
    похожее из справочника") — их не нужно считать провалом.
    """
    blocking = [e for e in errors if str(e.get("level", "error")).lower() == "error"]
    warnings = [e for e in errors if str(e.get("level", "error")).lower() != "error"]
    return blocking, warnings


def cmd_test_ozon() -> int:
    import ozon_client

    ok = ozon_client.test_connection()
    print("Ozon: соединение работает" if ok else "Ozon: ОШИБКА, см. лог выше")
    return 0 if ok else 1


def cmd_test_wb() -> int:
    import wb_client

    ok = wb_client.test_connection()
    print("Wildberries: соединение работает" if ok else "Wildberries: ОШИБКА, см. лог выше")
    return 0 if ok else 1


def cmd_fetch_ozon() -> int:
    import ozon_client

    products = ozon_client.list_products()
    if not products:
        print("Ozon вернул пустой список товаров — либо в кабинете их нет, либо ошибка доступа.")
        return 1
    offer_ids = [p["offer_id"] for p in products if p.get("offer_id")]
    details = ozon_client.get_prices_and_stocks(offer_ids)
    names = ozon_client.get_product_names(offer_ids)
    path = _data_path("ozon_products.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            {"list": products, "details": details, "names": names},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"Сохранено {len(products)} товаров Ozon (с названиями) в {path}")
    return 0


def cmd_fetch_wb() -> int:
    import wb_client

    cards = wb_client.list_cards()
    if not cards:
        print("WB вернул пустой список карточек — либо в кабинете их нет, либо ошибка доступа.")
        return 1
    prices = wb_client.get_prices()
    path = _data_path("wb_cards.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"cards": cards, "prices": prices}, f, ensure_ascii=False, indent=2)
    print(f"Сохранено {len(cards)} карточек WB в {path}")
    return 0


def cmd_build_ozon_catalog() -> int:
    import catalog_editor

    src_path = _data_path("ozon_products.json")
    if not os.path.exists(src_path):
        print(f"Нет файла {src_path} — сначала выполните команду fetch-ozon.")
        return 1
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    xlsx_path = _data_path("ozon_catalog.xlsx")
    count = catalog_editor.build_ozon_catalog(data, xlsx_path)
    print(f"Готово: {count} товаров -> {xlsx_path}. Скачайте файл из репозитория, отредактируйте и загрузите обратно (заменив старый) перед push-ozon-cards.")
    return 0


def cmd_attach_ozon_photos() -> int:
    import catalog_editor

    xlsx_path = _data_path("ozon_catalog.xlsx")
    photos_dir = os.path.join(os.path.dirname(__file__), "photos")
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-ozon-catalog.")
        return 1
    if not os.path.isdir(photos_dir):
        print(f"Нет папки {photos_dir} — создайте в репозитории папку photos, загрузите туда фото (см. README) и запустите снова.")
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        print("Не удалось определить репозиторий (GITHUB_REPOSITORY пуст) — эту команду нужно запускать через GitHub Actions.")
        return 1
    raw_base_url = f"https://raw.githubusercontent.com/{repo}/{branch}/photos"

    matched = catalog_editor.attach_local_photos(xlsx_path, photos_dir, raw_base_url)
    if not matched:
        print("Не нашлось ни одного файла в photos/, чьё имя совпадает с артикулом (offer_id) из таблицы.")
        print("Имя файла должно начинаться с артикула, например: 143210608_1.jpg, 143210608_2.jpg")
        return 1
    print(f"Ссылки на фото подставлены в {xlsx_path} для {len(matched)} товаров:")
    for offer_id, urls in matched.items():
        print(f"  {offer_id}: {len(urls)} фото")
    print("\nСкачайте ozon_catalog.xlsx из data/, проверьте и переходите к push-ozon-cards-dryrun.")
    return 0


def _load_ozon_push_inputs():
    import catalog_editor

    src_path = _data_path("ozon_products.json")
    xlsx_path = _data_path("ozon_catalog.xlsx")
    if not os.path.exists(src_path):
        print(f"Нет файла {src_path} — сначала выполните команду fetch-ozon.")
        return None, None
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-ozon-catalog и заполните его.")
        return None, None
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    edits = catalog_editor.load_catalog_edits(xlsx_path)
    return data, edits


def cmd_push_ozon_cards_dryrun() -> int:
    import catalog_editor

    data, edits = _load_ozon_push_inputs()
    if data is None:
        return 1
    items = catalog_editor.build_import_items(data, edits)
    print(f"ПРОБНЫЙ ПРОГОН — в Ozon ничего не отправляется. Товаров к обновлению: {len(items)}\n")
    print(json.dumps({"items": items}, ensure_ascii=False, indent=2))
    return 0


def cmd_push_ozon_cards() -> int:
    import ozon_client
    import catalog_editor

    data, edits = _load_ozon_push_inputs()
    if data is None:
        return 1
    items = catalog_editor.build_import_items(data, edits)
    if not items:
        print("Нечего отправлять: ни одна строка xlsx не совпала с offer_id из ozon_products.json.")
        return 1

    total_ok = 0
    total_err = 0
    chunk = 100
    for i in range(0, len(items), chunk):
        batch = items[i : i + chunk]
        batch_num = i // chunk + 1
        result = ozon_client.import_products(batch)
        task_id = result.get("result", {}).get("task_id")
        if not task_id:
            print(f"Партия {batch_num}: Ozon не вернул task_id, ответ: {result}")
            total_err += len(batch)
            continue
        print(f"Партия {batch_num}: task_id={task_id}, жду обработку...")
        status = {}
        for _ in range(30):
            time.sleep(2)
            status = ozon_client.get_import_status(task_id)
            if status.get("result", {}).get("items"):
                break
        for it in status.get("result", {}).get("items", []):
            blocking, warnings = _split_errors_by_level(it.get("errors") or [])
            if blocking:
                total_err += 1
                print(f"  ОШИБКА {it.get('offer_id')}: {blocking}")
            else:
                total_ok += 1
                note = f" (предупреждение: {warnings})" if warnings else ""
                print(f"  ОК {it.get('offer_id')}: статус {it.get('status')}{note}")
    print(f"\nИтого: успешно {total_ok}, с ошибками {total_err}")
    return 0 if total_err == 0 else 1


def cmd_build_wb_catalog() -> int:
    import wb_catalog_editor

    src_path = _data_path("wb_cards.json")
    if not os.path.exists(src_path):
        print(f"Нет файла {src_path} — сначала выполните команду fetch-wb.")
        return 1
    with open(src_path, "r", encoding="utf-8") as f:
        wb_data = json.load(f)
    xlsx_path = _data_path("wb_catalog.xlsx")
    count = wb_catalog_editor.build_wb_catalog(wb_data, xlsx_path)
    print(f"Готово: {count} товаров -> {xlsx_path}")
    return 0


def cmd_attach_wb_photos() -> int:
    import wb_catalog_editor

    xlsx_path = _data_path("wb_catalog.xlsx")
    photos_dir = os.path.join(os.path.dirname(__file__), "photos")
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-wb-catalog.")
        return 1
    if not os.path.isdir(photos_dir):
        print(f"Нет папки {photos_dir} — см. README, раздел про фото.")
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        print("Не удалось определить репозиторий (GITHUB_REPOSITORY пуст) — запускайте через GitHub Actions.")
        return 1
    raw_base_url = f"https://raw.githubusercontent.com/{repo}/{branch}/photos"

    matched = wb_catalog_editor.attach_wb_photos(xlsx_path, photos_dir, raw_base_url)
    if not matched:
        print("Не нашлось файлов в photos/, чьё имя совпадает с артикулом WB (vendorCode) из таблицы.")
        return 1
    print(f"Ссылки на фото подставлены в {xlsx_path} для {len(matched)} товаров:")
    for vendor_code, urls in matched.items():
        print(f"  {vendor_code}: {len(urls)} фото")
    return 0


def _load_wb_push_inputs():
    import wb_catalog_editor

    src_path = _data_path("wb_cards.json")
    xlsx_path = _data_path("wb_catalog.xlsx")
    if not os.path.exists(src_path):
        print(f"Нет файла {src_path} — сначала выполните fetch-wb.")
        return None, None
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-wb-catalog.")
        return None, None
    with open(src_path, "r", encoding="utf-8") as f:
        wb_data = json.load(f)
    edits = wb_catalog_editor.load_wb_catalog_edits(xlsx_path)
    return wb_data, edits


def cmd_push_wb_cards_dryrun() -> int:
    import wb_catalog_editor

    wb_data, edits = _load_wb_push_inputs()
    if wb_data is None:
        return 1
    text_items = wb_catalog_editor.build_wb_update_items(wb_data, edits)
    media_items = wb_catalog_editor.build_wb_media_updates(wb_data, edits)

    print(f"ПРОБНЫЙ ПРОГОН — в WB ничего не отправляется.")
    print(f"Правки названия/описания: {len(text_items)} товаров")
    for item in text_items:
        if len(item["title"]) >= wb_catalog_editor.TITLE_MAX_LEN:
            print(f"  ВНИМАНИЕ: {item['vendorCode']} — название обрежется до {wb_catalog_editor.TITLE_MAX_LEN} символов")
    print(json.dumps(text_items, ensure_ascii=False, indent=2))
    print(f"\nПравки фото: {len(media_items)} товаров")
    print(json.dumps(media_items, ensure_ascii=False, indent=2))
    return 0


def cmd_push_wb_cards() -> int:
    import wb_client
    import wb_catalog_editor

    wb_data, edits = _load_wb_push_inputs()
    if wb_data is None:
        return 1
    text_items = wb_catalog_editor.build_wb_update_items(wb_data, edits)
    media_items = wb_catalog_editor.build_wb_media_updates(wb_data, edits)

    if not text_items and not media_items:
        print("Нечего отправлять — ни одна строка xlsx не совпала с vendorCode из wb_cards.json.")
        return 1

    total_ok = 0
    total_err = 0

    if text_items:
        chunk = 100
        for i in range(0, len(text_items), chunk):
            batch = text_items[i : i + chunk]
            try:
                wb_client.update_cards(batch)
                print(f"Название/описание: партия {i // chunk + 1} ({len(batch)} шт.) отправлена.")
                total_ok += len(batch)
            except wb_client.WbApiError as exc:
                print(f"ОШИБКА при отправке названия/описания (партия {i // chunk + 1}): {exc}")
                total_err += len(batch)

    for media in media_items:
        try:
            wb_client.update_media(media["nm_id"], media["urls"])
            print(f"Фото: {media['vendor_code']} — {len(media['urls'])} фото отправлено.")
            total_ok += 1
        except wb_client.WbApiError as exc:
            print(f"ОШИБКА при отправке фото {media['vendor_code']}: {exc}")
            total_err += 1

    print(f"\nИтого успешных операций: {total_ok}, с ошибками: {total_err}")
    return 0 if total_err == 0 else 1


def cmd_build_ozon_new_template() -> int:
    import ozon_new_products

    xlsx_path = _data_path("ozon_new_products.xlsx")
    ozon_new_products.build_new_template(xlsx_path)
    print(f"Готово: {xlsx_path}. Заполните строки (артикул, образец, название, цена...) и загрузите обратно.")
    return 0


def cmd_attach_ozon_new_photos() -> int:
    import ozon_new_products

    xlsx_path = _data_path("ozon_new_products.xlsx")
    photos_dir = os.path.join(os.path.dirname(__file__), "photos")
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-ozon-new-template.")
        return 1
    if not os.path.isdir(photos_dir):
        print(f"Нет папки {photos_dir} — см. README, раздел про фото.")
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        print("Не удалось определить репозиторий — запускайте через GitHub Actions.")
        return 1
    raw_base_url = f"https://raw.githubusercontent.com/{repo}/{branch}/photos"

    matched = ozon_new_products.attach_new_photos(xlsx_path, photos_dir, raw_base_url)
    if not matched:
        print("Не нашлось файлов в photos/, чьё имя совпадает с НОВЫМ артикулом из таблицы.")
        return 1
    for offer_id, urls in matched.items():
        print(f"  {offer_id}: {len(urls)} фото")
    return 0


def _load_ozon_new_inputs():
    import ozon_new_products

    src_path = _data_path("ozon_products.json")
    xlsx_path = _data_path("ozon_new_products.xlsx")
    if not os.path.exists(src_path):
        print(f"Нет файла {src_path} — сначала выполните fetch-ozon (нужны данные образцов).")
        return None, None
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-ozon-new-template и заполните его.")
        return None, None
    with open(src_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    edits = ozon_new_products.load_new_edits(xlsx_path)
    return data, edits


def cmd_push_ozon_new_cards_dryrun() -> int:
    import ozon_new_products

    data, edits = _load_ozon_new_inputs()
    if data is None:
        return 1
    items = ozon_new_products.build_new_import_items(data, edits)
    print(f"ПРОБНЫЙ ПРОГОН — в Ozon ничего не отправляется. Новых товаров к созданию: {len(items)}\n")
    print(json.dumps({"items": items}, ensure_ascii=False, indent=2))
    return 0


def cmd_push_ozon_new_cards() -> int:
    import ozon_client
    import ozon_new_products

    data, edits = _load_ozon_new_inputs()
    if data is None:
        return 1
    items = ozon_new_products.build_new_import_items(data, edits)
    if not items:
        print("Нечего отправлять: ни одна строка не совпала с образцом из ozon_products.json.")
        return 1

    total_ok = 0
    total_err = 0
    result = ozon_client.import_products(items)
    task_id = result.get("result", {}).get("task_id")
    if not task_id:
        print(f"Ozon не вернул task_id, ответ: {result}")
        return 1
    print(f"task_id={task_id}, жду обработку...")
    status = {}
    for _ in range(30):
        time.sleep(2)
        status = ozon_client.get_import_status(task_id)
        if status.get("result", {}).get("items"):
            break
    for it in status.get("result", {}).get("items", []):
        blocking, warnings = _split_errors_by_level(it.get("errors") or [])
        if blocking:
            total_err += 1
            print(f"  ОШИБКА {it.get('offer_id')}: {blocking}")
        else:
            total_ok += 1
            note = f" (предупреждение: {warnings})" if warnings else ""
            print(f"  ОК {it.get('offer_id')}: статус {it.get('status')}{note}")
    print(f"\nИтого: успешно {total_ok}, с ошибками {total_err}")
    if total_ok:
        print("Не забудьте выполнить fetch-ozon ещё раз, чтобы новые товары попали в общий каталог.")
    return 0 if total_err == 0 else 1


def cmd_build_wb_new_template() -> int:
    import wb_new_products

    xlsx_path = _data_path("wb_new_products.xlsx")
    wb_new_products.build_new_template(xlsx_path)
    print(f"Готово: {xlsx_path}. Заполните строки (артикул, образец, название, цена...) и загрузите обратно.")
    return 0


def _load_wb_new_inputs():
    import wb_new_products

    src_path = _data_path("wb_cards.json")
    xlsx_path = _data_path("wb_new_products.xlsx")
    if not os.path.exists(src_path):
        print(f"Нет файла {src_path} — сначала выполните fetch-wb (нужны данные образцов).")
        return None, None
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-wb-new-template и заполните его.")
        return None, None
    with open(src_path, "r", encoding="utf-8") as f:
        wb_data = json.load(f)
    edits = wb_new_products.load_new_edits(xlsx_path)
    return wb_data, edits


def cmd_push_wb_new_cards_dryrun() -> int:
    import wb_client
    import wb_new_products

    wb_data, edits = _load_wb_new_inputs()
    if wb_data is None:
        return 1
    barcodes = wb_client.generate_barcodes(max(len(edits), 1))
    groups = wb_new_products.build_new_card_groups(wb_data, edits, barcodes)
    print(f"ПРОБНЫЙ ПРОГОН — в WB ничего не отправляется. Новых товаров к созданию: {len(groups)}\n")
    print(json.dumps(groups, ensure_ascii=False, indent=2))
    print(
        "\nПосле реального push-wb-new-cards фото/видео сюда не входят — добавляются вторым "
        "шагом через build-wb-catalog/attach-wb-photos/push-wb-cards после fetch-wb (см. README)."
    )
    return 0


def cmd_push_wb_new_cards() -> int:
    import wb_client
    import wb_new_products

    wb_data, edits = _load_wb_new_inputs()
    if wb_data is None:
        return 1
    barcodes = wb_client.generate_barcodes(max(len(edits), 1))
    groups = wb_new_products.build_new_card_groups(wb_data, edits, barcodes)
    if not groups:
        print("Нечего отправлять: ни одна строка не совпала с образцом из wb_cards.json.")
        return 1

    total_ok = 0
    total_err = 0
    for group in groups:
        try:
            wb_client.create_cards(group["subject_id"], [group["variant"]])
            print(f"ОК: {group['vendor_code']} отправлен на создание (раздел {group['subject_id']}).")
            total_ok += 1
        except wb_client.WbApiError as exc:
            print(f"ОШИБКА при создании {group['vendor_code']}: {exc}")
            total_err += 1

    print(f"\nИтого: отправлено {total_ok}, с ошибками {total_err}")
    if total_ok:
        print(
            "Создание асинхронное — подождите 2-5 минут, затем выполните fetch-wb ещё раз: "
            "новые товары появятся со своим nmID, дальше работайте с ними как с обычными."
        )
    return 0 if total_err == 0 else 1


def cmd_compare_ozon_wb() -> int:
    import catalog_compare

    ozon_path = _data_path("ozon_products.json")
    wb_path = _data_path("wb_cards.json")
    if not os.path.exists(ozon_path):
        print(f"Нет файла {ozon_path} — сначала выполните fetch-ozon.")
        return 1
    if not os.path.exists(wb_path):
        print(f"Нет файла {wb_path} — сначала выполните fetch-wb.")
        return 1
    with open(ozon_path, "r", encoding="utf-8") as f:
        ozon_data = json.load(f)
    with open(wb_path, "r", encoding="utf-8") as f:
        wb_data = json.load(f)

    xlsx_path = _data_path("ozon_wb_compare.xlsx")
    counts = catalog_compare.build_report(ozon_data, wb_data, xlsx_path)
    print(f"Сравнение готово -> {xlsx_path}")
    print(f"  Совпадает точно: {counts['exact_matches']}")
    print(f"  Похоже, но не точно (проверить вручную): {counts['candidates']}")
    print(f"  Только на Ozon (нет на WB): {counts['ozon_only']}")
    print(f"  Только на WB (нет на Ozon): {counts['wb_only']}")
    return 0


def cmd_wb_warehouses() -> int:
    import wb_client

    warehouses = wb_client.get_warehouses()
    print(json.dumps(warehouses, ensure_ascii=False, indent=2))
    print("\nСкопируйте нужный ID в WB_WAREHOUSE_ID в .env / GitHub Secrets.")
    return 0


def cmd_test_avito() -> int:
    """
    Проверка доступа к Avito API: только получение токена (без реальных
    запросов заказов/остатков) — чтобы убедиться, что AVITO_CLIENT_ID /
    AVITO_CLIENT_SECRET внесены верно, ДО того как пробовать что-то ещё.
    """
    import avito_client

    try:
        avito_client._get_token()
    except avito_client.AvitoApiError as exc:
        print(f"ОШИБКА доступа к Avito API: {exc}")
        return 1
    print("OK: токен Avito API получен успешно — AVITO_CLIENT_ID/AVITO_CLIENT_SECRET верны.")
    return 0


def cmd_fetch_avito_orders() -> int:
    """
    Получить заказы Авито Доставки за последние 30 дней и сохранить в
    data/avito_orders.json — просто посмотреть, что видно, без каких-либо
    изменений остатков.
    """
    import datetime
    import avito_client

    since = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=30)
    date_from = int(since.timestamp())
    try:
        orders = avito_client.get_orders(date_from=date_from)
    except avito_client.AvitoApiError as exc:
        print(f"ОШИБКА при получении заказов Avito: {exc}")
        print(
            "Если ошибка похожа на 'нет доступа'/403 — скорее всего, для заказов Авито "
            "Доставки нужен тариф 'Бизнес' на Авито, которого сейчас нет. Если 404 — "
            "путь эндпоинта в avito_client.py устарел, см. комментарий '# ENDPOINT' там."
        )
        return 1

    path = _data_path("avito_orders.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(orders, f, ensure_ascii=False, indent=2)
    print(f"Сохранено {len(orders)} заказов Avito (за 30 дней) в {path}")
    return 0


def cmd_list_avito_items() -> int:
    """
    Показать сырой ответ Avito по объявлениям продавца — чтобы увидеть,
    есть ли там наш собственный артикул (или только avitoId и заголовок,
    как в заказах) и как их сопоставлять.
    """
    import avito_client

    try:
        data = avito_client.list_items(per_page=25)
    except avito_client.AvitoApiError as exc:
        print(f"ОШИБКА при получении списка объявлений Avito: {exc}")
        return 1
    print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def cmd_build_avito_item_map() -> int:
    """
    Собрать data/avito_item_map.xlsx: все объявления Avito + подсказка по
    совпадению с вашим каталогом (data/ozon_catalog.xlsx). Ничего не
    подтверждает само — только предлагает, колонку "ПОДТВЕРЖДЁННЫЙ АРТИКУЛ"
    заполняете/проверяете вы сами.
    """
    import avito_client
    import avito_item_map

    catalog_path = _data_path("ozon_catalog.xlsx")
    if not os.path.exists(catalog_path):
        print(f"Нет файла {catalog_path} — сначала выполните fetch-ozon и build-ozon-catalog.")
        return 1
    catalog = avito_item_map.load_catalog(catalog_path)

    try:
        items = avito_client.list_all_items()
    except avito_client.AvitoApiError as exc:
        print(f"ОШИБКА при получении списка объявлений Avito: {exc}")
        return 1
    if not items:
        print("Avito вернул пустой список объявлений.")
        return 1

    xlsx_path = _data_path("avito_item_map.xlsx")
    count = avito_item_map.build_item_map_template(items, catalog, xlsx_path)
    print(f"Готово: {count} объявлений Avito -> {xlsx_path}.")
    print("Откройте файл, проверьте/заполните колонку 'ПОДТВЕРЖДЁННЫЙ АРТИКУЛ' и сохраните обратно, "
          "потом выполните save-avito-item-map.")
    return 0


def cmd_save_avito_item_map() -> int:
    """
    Прочитать заполненный data/avito_item_map.xlsx и сохранить подтверждённое
    сопоставление avitoId -> offer_id в data/avito_item_map.json.
    """
    import avito_item_map

    xlsx_path = _data_path("avito_item_map.xlsx")
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-avito-item-map и заполните его.")
        return 1
    mapping = avito_item_map.load_confirmed_map(xlsx_path)
    if not mapping:
        print("Колонка 'ПОДТВЕРЖДЁННЫЙ АРТИКУЛ' пуста везде — нечего сохранять.")
        return 1
    path = _data_path("avito_item_map.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(mapping, f, ensure_ascii=False, indent=2)
    print(f"Сохранено {len(mapping)} подтверждённых соответствий avitoId -> артикул в {path}")
    return 0


def cmd_sync_orders() -> int:
    """
    Общий учёт остатков: забрать заказы за последние 30 дней с Ozon (FBS+FBO)
    и WB (полная история через Statistics API — не только новые), списать
    проданное с "Кол-во к продаже" в data/ozon_catalog.xlsx (с учётом
    отмен — если заказ отменили, остаток вернётся обратно), и НЕ задвоить
    списание при повторном запуске (data/orders_seen.db).

    Avito сюда пока не входит — там сопоставление объявление->артикул
    требует подтверждения человеком (см. build-avito-item-map), а для новых
    объявлений такая карта ещё не собрана.

    Это только СЧИТАЕТ и правит локальный xlsx. Рассылка обновлённого
    остатка обратно в Ozon/WB — отдельный следующий шаг.
    """
    import stock_sync

    xlsx_path = _data_path("ozon_catalog.xlsx")
    try:
        summary = stock_sync.sync_all_orders(days_back=30, catalog_path=xlsx_path)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    print("Заказов разобрано:")
    print(f"  Ozon FBS: {summary.get('ozon_fbs', 'ошибка — см. ниже')}")
    if "ozon_fbs_error" in summary:
        print(f"    ОШИБКА Ozon FBS: {summary['ozon_fbs_error']}")
    print(f"  Ozon FBO: {summary.get('ozon_fbo', 'ошибка — см. ниже')}")
    if "ozon_fbo_error" in summary:
        print(f"    ОШИБКА Ozon FBO: {summary['ozon_fbo_error']}")
    print(f"  WB: {summary.get('wb', 'ошибка — см. ниже')}")
    if "wb_error" in summary:
        print(f"    ОШИБКА WB: {summary['wb_error']}")

    deltas = summary.get("deltas") or {}
    if not deltas:
        print("\nНовых изменений остатка нет (нечего списывать/возвращать за этот период).")
        return 0

    print(f"\nИзменения остатка ({len(deltas)} артикул(ов)):")
    applied = summary.get("applied") or {}
    for offer_id, delta in sorted(deltas.items()):
        new_qty = applied.get(offer_id, "?")
        sign = "+" if delta > 0 else ""
        print(f"  {offer_id}: {sign}{delta}  -> новый остаток: {new_qty}")

    unmatched = summary.get("unmatched") or []
    if unmatched:
        print(f"\nВНИМАНИЕ: {len(unmatched)} артикул(ов) из заказов НЕ найдены в каталоге "
              f"(остаток не изменён, проверьте вручную): {', '.join(unmatched)}")


def cmd_test_wordstat() -> int:
    import wordstat_client

    ok = wordstat_client.test_connection()
    print("Wordstat: соединение работает" if ok else "Wordstat: ОШИБКА, см. лог выше")
    return 0 if ok else 1


def cmd_wordstat_collect(article: str, seed_phrase: str) -> int:
    """
    Собирает SEO-семантику для одного артикула через Wordstat API: по
    стартовой фразе (можно несколько через ';') берёт частотность и
    автоматически расширяет список похожими фразами (associations), которые
    реально ищут вместе с ней — без ручного придумывания вариантов.
    Результат копится в data/seo_keywords.xlsx (лист "Семантика"), не
    дублируя уже собранное по этому артикулу.
    """
    import seo_store
    import wordstat_client

    article = (article or "").strip()
    seed_phrase = (seed_phrase or "").strip()
    if not article or not seed_phrase:
        print(
            "Нужно указать артикул и стартовую фразу (поля 'article' и 'seed_phrase' "
            "при запуске в Actions, или --article/--seed-phrase в командной строке)."
        )
        return 1

    seeds = [s.strip() for s in seed_phrase.split(";") if s.strip()]
    # Каталожные номера прямо из названия артикула — отдельным запросом
    # (проверено: по точному номеру детали тоже реально ищут, см. чат —
    # "0BH325159" сам по себе дал 193 реальных поиска, а этого номера не
    # было ни в одной обычной разговорной фразе).
    for pn in wordstat_client.extract_part_numbers(article):
        if pn.casefold() not in (s.casefold() for s in seeds):
            seeds.append(pn)
    print(f"Wordstat: собираю семантику для {article} по фразам: {seeds}")
    phrases = wordstat_client.collect_semantics(seeds, expand_associations=True)
    if not phrases:
        print(
            "Wordstat не вернул ни одной фразы — проверьте WORDSTAT_API_KEY/"
            "WORDSTAT_FOLDER_ID в секретах и саму фразу."
        )
        return 1

    added = seo_store.add_semantics(article, phrases)
    print(f"Собрано фраз всего: {len(phrases)}, новых добавлено в data/seo_keywords.xlsx: {added}")
    print("\nТоп по частотности:")
    for phrase, count in sorted(phrases.items(), key=lambda kv: -kv[1])[:25]:
        print(f"  {count:>7}  {phrase}")
    return 0


def _git_checkpoint(reason: str) -> None:
    """
    Коммитит и пушит data/seo_keywords.xlsx и data/wordstat_cache.json ПРЯМО
    СЕЙЧАС, а не в самом конце прогона. Без этого весь прогресс batch-сбора
    живёт только на временном сервере GitHub Actions и теряется целиком, если
    job отменят или он оборвётся — так уже случилось: два прогона с реальными
    данными (сотни собранных фраз) пропали, потому что шаг "Save data/ back
    to repo" в самом конце workflow не успел выполниться при отмене (даже с
    if: always() — при отмене всего job этот шаг тоже помечается
    отменённым, 0 секунд, ничего не сохраняет).

    data/wordstat_cache.json — постоянный кэш ответов Wordstat по каждой
    отдельной фразе (не только по артикулу): если сохранить только его в
    коммит забудут, то каждый следующий запуск снова будет реально
    запрашивать API по уже когда-то собранным фразам, а не брать их из кэша.

    Полностью безопасно вызывать часто: если сохранять нечего — просто
    ничего не делает. Любая ошибка git (например, если запущено не в
    git-репозитории — как при локальном тестировании) молча игнорируется,
    сам сбор при этом не прерывается.
    """
    try:
        if subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True,
        ).returncode != 0:
            return  # не git-репозиторий (например, локальный тест) — пропускаем

        subprocess.run(["git", "config", "user.name", "marketplace-agent-bot"], capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "actions@users.noreply.github.com"],
            capture_output=True,
        )
        # ВАЖНО: "git add" с несуществующим путём (например data/wordstat_cache.json
        # до самого первого запроса к Wordstat в этом прогоне) падает целиком с
        # "fatal: pathspec ... did not match any files" и НЕ добавляет вообще
        # ничего — даже те пути в той же команде, что реально существуют. Поэтому
        # добавляем в индекс только те из отслеживаемых файлов, что уже есть на диске.
        track_paths = [
            p for p in ("data/seo_keywords.xlsx", "data/wordstat_cache.json")
            if os.path.exists(p)
        ]
        if track_paths:
            subprocess.run(["git", "add", *track_paths], capture_output=True)

        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], capture_output=True)
        if diff.returncode == 0:
            return  # нечего коммитить

        commit = subprocess.run(
            ["git", "commit", "-m", f"chore: wordstat progress ({reason}) [skip ci]"],
            capture_output=True, text=True,
        )
        if commit.returncode != 0:
            print(f"  (git commit не удался: {commit.stderr.strip()[:200]})")
            return

        push = subprocess.run(["git", "push"], capture_output=True, text=True)
        if push.returncode != 0:
            print(f"  (git push не удался: {push.stderr.strip()[:200]})")
        else:
            print(f"  (сохранено в репозиторий: {reason})")
    except Exception as exc:  # сбой сохранения не должен ронять сам сбор
        print(f"  (не удалось сохранить прогресс в git: {exc})")


def cmd_wordstat_collect_batch() -> int:
    """
    Пакетный сбор: вместо одного артикула за запуск — берёт СПИСОК артикулов
    из data/wordstat_queue.xlsx (колонка "Артикул", можно просто вставить
    столбец из вашей таблицы) и по очереди собирает семантику по каждому
    через Wordstat API. Стартовой фразой для Wordstat по умолчанию служит
    сам артикул — если для какой-то строки нужна другая формулировка,
    впишите её во вторую колонку файла-очереди.

    Если файла ещё нет — создаёт пустой шаблон и просит вас заполнить и
    прислать/закоммитить обратно.
    """
    import seo_store
    import wordstat_client

    queue_path = seo_store.QUEUE_PATH
    if not os.path.exists(queue_path):
        seo_store.ensure_queue_template(queue_path)
        print(
            f"Файла {queue_path} ещё не было — создал пустой шаблон (колонка 'Артикул' "
            "+ необязательная 'Стартовая фраза'). Заполните столбец артикулов, "
            "закоммитьте файл в data/ и запустите wordstat-collect-batch ещё раз."
        )
        return 0

    rows = seo_store.read_queue(queue_path)
    if not rows:
        print(f"{queue_path} есть, но пуст (нет ни одного артикула в колонке 'Артикул').")
        return 1

    print(f"В очереди {len(rows)} артикул(ов). Собираю семантику по каждому...\n")
    total_added = 0
    total_articles_no_new = 0
    # ВАЖНО: раньше здесь был отдельный "пропустить артикул целиком, если по
    # нему уже что-то собрано в прошлый раз" — но это означало, что НОВЫЕ
    # стартовые фразы, добавленные позже в очередь для уже обработанного
    # артикула (например при расширении семантики), никогда бы не
    # запрашивались. Теперь вместо этого используется постоянный (между
    # запусками) кэш САМИХ ФРАЗ в wordstat_client (data/wordstat_cache.json):
    # каждый артикул обрабатывается заново при каждом прогоне, но любая уже
    # когда-либо запрошенная фраза (для ЭТОГО или любого ДРУГОГО артикула —
    # кэш общий, не по артикулам) берётся из кэша мгновенно, без обращения к
    # Wordstat. Реальные запросы к API уходят только по фразам, которых в
    # кэше ещё никогда не было. add_semantics() при этом сам не дублирует
    # уже имеющиеся пары (артикул, фраза), так что повторная обработка
    # готовых артикулов безопасна и просто ничего не добавляет.
    wordstat_client.reset_cache_stats()
    since_checkpoint = 0
    for i, (article, seed) in enumerate(rows, start=1):
        seeds = [s.strip() for s in seed.split(";") if s.strip()]
        # Каталожные номера прямо из названия артикула — отдельным
        # запросом, автоматически, без ручного заполнения (проверено:
        # "0BH325159" сам по себе дал 193 реальных поиска в Wordstat,
        # которых не было ни в одной обычной разговорной фразе — см. чат).
        for pn in wordstat_client.extract_part_numbers(article):
            if pn.casefold() not in (s.casefold() for s in seeds):
                seeds.append(pn)
        print(f"[{i}/{len(rows)}] {article}: {seeds}")
        try:
            phrases = wordstat_client.collect_semantics(seeds, expand_associations=True)
        except wordstat_client.WordstatRateLimitedError:
            # Лимит запросов ещё не восстановился (например, этот
            # прогон стартовал в том же "часе", что и предыдущий,
            # который уже выбрал лимит) — дальше перебирать оставшиеся
            # позиции бессмысленно, все запросы будут падать так же.
            # Останавливаемся сразу вместо того, чтобы тратить ещё
            # много минут на заведомо обречённые попытки.
            print(
                f"  {article}: Wordstat всё ещё отвечает 'превышен лимит запросов' "
                "даже после повторных попыток."
            )
            print(
                "\nОстанавливаюсь пораньше — похоже, часовой лимит запросов Wordstat "
                "ещё не восстановился с прошлого запуска. Обычно он сбрасывается на "
                "границе часа — попробуйте запустить wordstat-collect-batch снова "
                "примерно через 30-60 минут (или после начала следующего часа). "
                "Уже запрошенные фразы сохранены в постоянном кэше "
                "(data/wordstat_cache.json) и при повторном запуске НЕ потребуют "
                "новых обращений к Wordstat — реальные запросы уйдут только по "
                "действительно новым фразам."
            )
            _git_checkpoint(f"прервано по лимиту на {i}/{len(rows)}")
            stats = wordstat_client.cache_stats()
            print(
                f"\nГотово (прервано по лимиту). Всего новых строк добавлено: {total_added} "
                f"(из кэша: {stats['hits']}, реально запрошено у Wordstat: {stats['misses']})"
            )
            return 1
        except Exception as exc:  # не прерывать всю очередь из-за одного артикула
            print(f"  ОШИБКА по {article}: {exc}")
            continue
        if not phrases:
            print(f"  {article}: Wordstat не вернул ни одной фразы, пропущено")
            total_articles_no_new += 1
            continue
        added = seo_store.add_semantics(article, phrases)
        total_added += added
        if added == 0:
            total_articles_no_new += 1
        print(f"  {article}: собрано {len(phrases)} фраз, новых добавлено {added}")

        if added > 0:
            since_checkpoint += 1
            # Сохраняем прогресс в репозиторий не после КАЖДОЙ позиции (это
            # было бы слишком много лишних коммитов), а каждые несколько —
            # так почти весь прогресс переживёт отмену/обрыв прогона, а не
            # только то, что было в самом конце.
            if since_checkpoint >= 3:
                _git_checkpoint(f"после {i}/{len(rows)}")
                since_checkpoint = 0

    _git_checkpoint("финал прогона")
    stats = wordstat_client.cache_stats()
    print(
        f"\nГотово. Всего новых строк добавлено в data/seo_keywords.xlsx: {total_added} "
        f"(артикулов без новых строк: {total_articles_no_new})\n"
        f"Постоянный кэш фраз (data/wordstat_cache.json): взято из кэша {stats['hits']}, "
        f"реально запрошено у Wordstat {stats['misses']}"
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Синхронизация карточек Ozon/WB/Avito/Яндекс")
    parser.add_argument("--test-ozon", action="store_true")
    parser.add_argument("--test-wb", action="store_true")
    parser.add_argument("--fetch-ozon", action="store_true")
    parser.add_argument("--fetch-wb", action="store_true")
    parser.add_argument("--wb-warehouses", action="store_true", help="Показать склады продавца на WB (для WB_WAREHOUSE_ID)")
    parser.add_argument("--test-avito", action="store_true", help="Проверить доступ к Avito API (AVITO_CLIENT_ID/AVITO_CLIENT_SECRET)")
    parser.add_argument("--fetch-avito-orders", action="store_true", help="Получить заказы Авито Доставки за 30 дней в data/avito_orders.json")
    parser.add_argument("--list-avito-items", action="store_true", help="Показать сырой список объявлений Avito (диагностика сопоставления с артикулом)")
    parser.add_argument("--build-avito-item-map", action="store_true", help="Собрать data/avito_item_map.xlsx: объявления Avito + подсказка по совпадению с каталогом")
    parser.add_argument("--save-avito-item-map", action="store_true", help="Сохранить подтверждённое сопоставление avitoId->артикул в data/avito_item_map.json")
    parser.add_argument("--build-ozon-catalog", action="store_true", help="Собрать data/ozon_catalog.xlsx для редактирования карточек (название, описание, цена, фото)")
    parser.add_argument("--attach-ozon-photos", action="store_true", help="Подставить в xlsx ссылки на фото из папки photos/ по имени файла (offer_id_1.jpg и т.п.)")
    parser.add_argument("--push-ozon-cards-dryrun", action="store_true", help="Показать, что будет отправлено в Ozon, БЕЗ реальной отправки")
    parser.add_argument("--push-ozon-cards", action="store_true", help="Реально отправить правки карточек в Ozon (сначала всегда делайте dryrun!)")
    parser.add_argument("--build-ozon-new-template", action="store_true", help="Создать пустую таблицу для СОВСЕМ НОВЫХ товаров Ozon (по образцу существующего)")
    parser.add_argument("--attach-ozon-new-photos", action="store_true", help="Подставить фото из photos/ в таблицу новых товаров Ozon")
    parser.add_argument("--push-ozon-new-cards-dryrun", action="store_true", help="Показать, что будет создано в Ozon, БЕЗ реальной отправки")
    parser.add_argument("--push-ozon-new-cards", action="store_true", help="Реально создать новые товары в Ozon (сначала всегда dryrun!)")
    parser.add_argument("--build-wb-new-template", action="store_true", help="Создать пустую таблицу для СОВСЕМ НОВЫХ товаров WB (по образцу существующего)")
    parser.add_argument("--push-wb-new-cards-dryrun", action="store_true", help="Показать, что будет создано в WB, БЕЗ реальной отправки")
    parser.add_argument("--push-wb-new-cards", action="store_true", help="Реально создать новые товары в WB (сначала всегда dryrun!)")
    parser.add_argument("--compare-ozon-wb", action="store_true", help="Сравнить каталоги Ozon и WB по артикулу продавца, без объединения")
    parser.add_argument("--build-wb-catalog", action="store_true", help="Собрать data/wb_catalog.xlsx для редактирования карточек WB (название, описание, фото)")
    parser.add_argument("--attach-wb-photos", action="store_true", help="Подставить в wb_catalog.xlsx ссылки на фото из папки photos/ по имени файла")
    parser.add_argument("--push-wb-cards-dryrun", action="store_true", help="Показать, что будет отправлено в WB, БЕЗ реальной отправки")
    parser.add_argument("--push-wb-cards", action="store_true", help="Реально отправить правки карточек WB (сначала всегда делайте dryrun!)")
    parser.add_argument("--sync-orders", action="store_true", help="Общий учёт остатков: списать заказы Ozon+WB за 30 дней из 'Кол-во к продаже' в data/ozon_catalog.xlsx")
    parser.add_argument("--test-wordstat", action="store_true", help="Проверить, что ключ Wordstat API работает")
    parser.add_argument("--wordstat-collect", action="store_true", help="Собрать SEO-семантику по артикулу через Wordstat API (см. --article/--seed-phrase)")
    parser.add_argument("--wordstat-collect-batch", action="store_true", help="Собрать SEO-семантику сразу по списку артикулов из data/wordstat_queue.xlsx")
    parser.add_argument("--article", type=str, default="", help="Артикул товара — для --wordstat-collect")
    parser.add_argument("--seed-phrase", type=str, default="", help="Стартовая фраза(ы) для --wordstat-collect, через ';' если несколько")
    args = parser.parse_args()

    if args.test_ozon:
        return cmd_test_ozon()
    if args.test_wb:
        return cmd_test_wb()
    if args.test_wordstat:
        return cmd_test_wordstat()
    if args.wordstat_collect:
        return cmd_wordstat_collect(args.article, args.seed_phrase)
    if args.wordstat_collect_batch:
        return cmd_wordstat_collect_batch()
    if args.fetch_ozon:
        return cmd_fetch_ozon()
    if args.fetch_wb:
        return cmd_fetch_wb()
    if args.wb_warehouses:
        return cmd_wb_warehouses()
    if args.test_avito:
        return cmd_test_avito()
    if args.fetch_avito_orders:
        return cmd_fetch_avito_orders()
    if args.list_avito_items:
        return cmd_list_avito_items()
    if args.build_avito_item_map:
        return cmd_build_avito_item_map()
    if args.save_avito_item_map:
        return cmd_save_avito_item_map()
    if args.build_ozon_catalog:
        return cmd_build_ozon_catalog()
    if args.attach_ozon_photos:
        return cmd_attach_ozon_photos()
    if args.push_ozon_cards_dryrun:
        return cmd_push_ozon_cards_dryrun()
    if args.push_ozon_cards:
        return cmd_push_ozon_cards()
    if args.build_ozon_new_template:
        return cmd_build_ozon_new_template()
    if args.attach_ozon_new_photos:
        return cmd_attach_ozon_new_photos()
    if args.push_ozon_new_cards_dryrun:
        return cmd_push_ozon_new_cards_dryrun()
    if args.push_ozon_new_cards:
        return cmd_push_ozon_new_cards()
    if args.build_wb_new_template:
        return cmd_build_wb_new_template()
    if args.push_wb_new_cards_dryrun:
        return cmd_push_wb_new_cards_dryrun()
    if args.push_wb_new_cards:
        return cmd_push_wb_new_cards()
    if args.compare_ozon_wb:
        return cmd_compare_ozon_wb()
    if args.build_wb_catalog:
        return cmd_build_wb_catalog()
    if args.attach_wb_photos:
        return cmd_attach_wb_photos()
    if args.push_wb_cards_dryrun:
        return cmd_push_wb_cards_dryrun()
    if args.push_wb_cards:
        return cmd_push_wb_cards()
    if args.sync_orders:
        return cmd_sync_orders()

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
