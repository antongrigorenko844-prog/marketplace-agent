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
    args = parser.parse_args()

    if args.test_ozon:
        return cmd_test_ozon()
    if args.test_wb:
        return cmd_test_wb()
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

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
