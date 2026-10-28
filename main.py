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


def cmd_download_ozon_photos() -> int:
    import catalog_editor

    xlsx_path = _data_path("ozon_catalog.xlsx")
    photos_dir = os.path.join(os.path.dirname(__file__), "photos")
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-ozon-catalog.")
        return 1
    downloaded = catalog_editor.download_missing_photos(xlsx_path, photos_dir)
    if downloaded:
        print(f"Скачано {len(downloaded)} фото (были только на карточке Ozon, локального файла не было):")
        for offer_id, fname in downloaded.items():
            print(f"  {offer_id}: {fname}")
    else:
        print("Нечего скачивать: либо у всех товаров уже есть локальный файл, либо ссылок в колонке 'Фото' нет.")
    return 0


def cmd_full_sync_ozon() -> int:
    """
    Составной шаг "всё сразу" для обычных (уже существующих) товаров Ozon —
    ОДИН запуск вместо пяти:
    fetch-ozon -> build-ozon-catalog -> download-ozon-photos -> attach-ozon-photos
    -> build-master-control.
    Останавливается на первой же неудачной части, КРОМЕ download-ozon-photos —
    это подстраховка (докачивает то, чего нет локально), а не обязательный шаг,
    поэтому её сбой не прерывает всё остальное.
    После этого шага остаётся скачать data/master_control.xlsx (уже готовый,
    со свежими фото) и, если правили что-то в нём — sync-master-control, а
    для реальной отправки в Ozon — push-ozon-cards-dryrun как обычно.
    """
    print("=== Шаг 1/5: fetch-ozon ===")
    rc = cmd_fetch_ozon()
    if rc != 0:
        print("full-sync-ozon остановлен: fetch-ozon завершился с ошибкой.")
        return rc

    print("\n=== Шаг 2/5: build-ozon-catalog ===")
    rc = cmd_build_ozon_catalog()
    if rc != 0:
        print("full-sync-ozon остановлен: build-ozon-catalog завершился с ошибкой.")
        return rc

    print("\n=== Шаг 3/5: download-ozon-photos (подтягиваем то, что есть только на карточке Ozon) ===")
    rc = cmd_download_ozon_photos()
    if rc != 0:
        print("download-ozon-photos завершился с ошибкой — продолжаем без остановки (это не критично, просто часть фото останется без локального файла).")

    print("\n=== Шаг 4/5: attach-ozon-photos ===")
    rc = cmd_attach_ozon_photos()
    if rc != 0:
        print("full-sync-ozon остановлен: attach-ozon-photos завершился с ошибкой.")
        return rc

    print("\n=== Шаг 5/5: build-master-control ===")
    rc = cmd_build_master_control()
    if rc != 0:
        print("build-master-control завершился с ошибкой — продолжаем без остановки (data/ozon_catalog.xlsx уже обновлён и это главное; просто пульт master_control.xlsx придётся пересобрать отдельно).")

    print("\nГотово: full-sync-ozon завершён. Скачайте data/master_control.xlsx — он уже пересобран со свежими фото.")
    return 0


def cmd_build_master_control() -> int:
    import build_master_control

    return build_master_control.run()


def cmd_sync_master_control() -> int:
    """
    Переносит правки, сделанные в data/master_control.xlsx (единый
    файл-пульт), обратно в data/ozon_catalog.xlsx / data/ozon_new_products.xlsx
    (название/описание/хэштеги/заметки) и, если в ячейке "Фото" вставлена
    новая картинка, сохраняет её как обложку в photos/<артикул>_1.<расширение>.
    """
    import sync_master_control

    return sync_master_control.run()


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


def _print_validation_warnings(data: dict) -> None:
    import catalog_editor

    warnings = catalog_editor.validate_catalog(data)
    if warnings:
        print("ПРЕДУПРЕЖДЕНИЯ ПЕРЕД ОТПРАВКОЙ (не блокируют, но проверьте):")
        for w in warnings:
            print(f"  - {w}")
        print()


def cmd_push_ozon_cards_dryrun() -> int:
    import catalog_editor

    data, edits = _load_ozon_push_inputs()
    if data is None:
        return 1
    _print_validation_warnings(data)
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
    _print_validation_warnings(data)
    items = catalog_editor.build_import_items(data, edits)
    if not items:
        print(
            "Отправлять нечего: либо ни одна строка xlsx не совпала с offer_id из "
            "ozon_products.json (см. предупреждения выше), либо (чаще) всё, что есть в "
            "xlsx, уже было отправлено раньше и с тех пор не менялось (см. "
            "data/ozon_push_cache.json) — это нормальный исход, не ошибка."
        )
        return 0

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
                # Запоминаем, что ИМЕННО ЭТОТ вариант карточки успешно
                # отправлен — только теперь, после подтверждённого Ozon
                # статуса (не сразу после отправки партии), чтобы при
                # следующем push-ozon-cards не отправлять то, что не
                # поменялось (см. build_import_items/mark_pushed).
                offer_id = it.get("offer_id")
                if offer_id in edits:
                    catalog_editor.mark_pushed(offer_id, edits[offer_id])
    print(f"\nИтого: успешно {total_ok}, с ошибками {total_err}")
    return 0 if total_err == 0 else 1


def cmd_full_sync_wb() -> int:
    """
    Составной шаг "всё сразу" для WB — ОДИН запуск вместо трёх:
    fetch-wb -> build-wb-catalog -> attach-wb-photos, и сразу же
    build-master-control (чтобы пульт master_control.xlsx стал видно
    актуальным для обеих площадок).
    Останавливается на первой же неудачной части.
    После этого шага остаётся скачать data/master_control.xlsx и, если
    правили что-то в нём — sync-master-control, а для реальной отправки в
    WB — push-wb-cards-dryrun как обычно.
    """
    print("=== Шаг 1/4: fetch-wb ===")
    rc = cmd_fetch_wb()
    if rc != 0:
        print("full-sync-wb остановлен: fetch-wb завершился с ошибкой.")
        return rc

    print("\n=== Шаг 2/4: build-wb-catalog ===")
    rc = cmd_build_wb_catalog()
    if rc != 0:
        print("full-sync-wb остановлен: build-wb-catalog завершился с ошибкой.")
        return rc

    print("\n=== Шаг 3/4: attach-wb-photos ===")
    rc = cmd_attach_wb_photos()
    if rc != 0:
        print("full-sync-wb остановлен: attach-wb-photos завершился с ошибкой.")
        return rc

    print("\n=== Шаг 4/4: build-master-control ===")
    rc = cmd_build_master_control()
    if rc != 0:
        print("build-master-control завершился с ошибкой — продолжаем без остановки (data/wb_catalog.xlsx уже обновлён и это главное; просто пульт master_control.xlsx придётся пересобрать отдельно).")

    print("\nГотово: full-sync-wb завершён. Скачайте data/master_control.xlsx — он уже пересобран со свежими данными WB.")
    return 0


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
        print(
            "Отправлять нечего: либо ни одна строка xlsx не совпала с vendorCode из "
            "wb_cards.json, либо (чаще) всё, что есть в xlsx, уже было отправлено раньше "
            "и с тех пор не менялось — это нормальный исход, не ошибка."
        )
        return 0

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
                wb_catalog_editor.mark_text_pushed(batch)
            except wb_client.WbApiError as exc:
                print(f"ОШИБКА при отправке названия/описания (партия {i // chunk + 1}): {exc}")
                total_err += len(batch)

    def _send_media(media):
        """None при успехе, иначе текст ошибки. WB иногда не может скачать только что залитый файл с GitHub —
        это временный сбой, поэтому «скачивание файла»-ошибки повторяем (3 попытки, пауза 25 с)."""
        err = ""
        for attempt in range(3):
            try:
                # На повторах добавляем к ссылкам безвредный параметр: WB, похоже, запоминает неудачное
                # скачивание по ТОЧНО той же ссылке и не пробует её снова.
                urls_try = media["urls"] if attempt == 0 else [
                    u + ("&" if "?" in u else "?") + f"r={attempt}" for u in media["urls"]
                ]
                wb_client.update_media(media["nm_id"], urls_try)
                print(f"Фото: {media['vendor_code']} — {len(media['urls'])} фото отправлено.")
                # Запоминаем именно после подтверждённого успеха (не раньше) —
                # см. mark_media_pushed/build_wb_media_updates.
                wb_catalog_editor.mark_media_pushed(media["vendor_code"], media["urls"])
                return None
            except wb_client.WbApiError as exc:
                err = str(exc)
                if "скачивание файла" not in err or attempt == 2:
                    break
                time.sleep(25)
        return err

    for media in media_items:
        err_text = _send_media(media)
        if err_text is None:
            total_ok += 1
        else:
            print(f"ОШИБКА при отправке фото {media['vendor_code']}: {err_text[:500]}")
            total_err += 1

    print(f"\nИтого успешных операций: {total_ok}, с ошибками: {total_err}")
    return 0 if total_err == 0 else 1


def cmd_attach_photos_all() -> int:
    """
    Composite: после того как поменяли файлы в общей папке photos/ (она одна
    и для Ozon, и для WB), одной командой подставить новые ссылки на фото
    сразу в ozon_catalog.xlsx И wb_catalog.xlsx, и пересобрать
    master_control.xlsx, чтобы он тоже стал актуальным — вместо трёх
    отдельных запусков (attach-ozon-photos, attach-wb-photos,
    build-master-control).
    Если фото совпало только для одной площадки (например, разное написание
    артикула/vendorCode) — это не останавливает вторую; ошибкой считается
    только случай, когда фото не подставилось НИ ДЛЯ ОДНОЙ площадки.
    """
    print("=== Шаг 1/3: attach-ozon-photos ===")
    rc_ozon = cmd_attach_ozon_photos()

    print("\n=== Шаг 2/3: attach-wb-photos ===")
    rc_wb = cmd_attach_wb_photos()

    if rc_ozon != 0 and rc_wb != 0:
        print("\nattach-photos-all остановлен: фото не подставились ни для Ozon, ни для WB — см. ошибки выше.")
        return 1

    print("\n=== Шаг 3/3: build-master-control ===")
    rc_master = cmd_build_master_control()
    if rc_master != 0:
        print(
            "build-master-control завершился с ошибкой — не критично, ozon_catalog.xlsx/wb_catalog.xlsx уже "
            "обновлены, это главное; пульт master_control.xlsx придётся пересобрать отдельно."
        )

    print("\nГотово: attach-photos-all завершён. Дальше — push-photos-dryrun, чтобы проверить, что уйдёт на Ozon и WB.")
    return 0


def cmd_push_photos_dryrun() -> int:
    """
    Composite: пробный прогон push-ozon-cards-dryrun + push-wb-cards-dryrun
    одной командой — ничего никуда не отправляется, только показывает, что
    уйдёт на каждую площадку.
    """
    print("=== Ozon: пробный прогон ===")
    rc_ozon = cmd_push_ozon_cards_dryrun()

    print("\n=== WB: пробный прогон ===")
    rc_wb = cmd_push_wb_cards_dryrun()

    if rc_ozon != 0 and rc_wb != 0:
        print("\npush-photos-dryrun: не удалось подготовить прогон ни для Ozon, ни для WB — см. ошибки выше.")
        return 1
    return 0


def cmd_push_photos() -> int:
    """
    Composite: реально отправить правки карточек (включая фото) сразу в
    Ozon И WB одной командой — вместо push-ozon-cards + push-wb-cards по
    отдельности. Сначала ВСЕГДА делайте push-photos-dryrun!
    Ошибка на одной площадке не останавливает вторую — в конце выводится
    итог по каждой отдельно.
    """
    print("=== Ozon: реальная отправка ===")
    rc_ozon = cmd_push_ozon_cards()

    print("\n=== WB: реальная отправка ===")
    rc_wb = cmd_push_wb_cards()

    print()
    if rc_ozon != 0 and rc_wb != 0:
        print("push-photos: ошибка на ОБЕИХ площадках — см. вывод выше.")
        return 1
    if rc_ozon != 0:
        print("push-photos: WB отправлен успешно, но Ozon завершился с ошибкой — проверьте вывод выше.")
    elif rc_wb != 0:
        print("push-photos: Ozon отправлен успешно, но WB завершился с ошибкой — проверьте вывод выше.")
    # Раньше функция здесь ничего не возвращала (None), а вызывающие
    # (update-photos, update-cards) проверяют "rc != 0" — None считалось
    # ошибкой, и общая команда красилась в красный даже когда всё
    # отправилось успешно. Ошибка только если упала хотя бы одна площадка.
    return 0 if (rc_ozon == 0 and rc_wb == 0) else 1


def cmd_update_photos() -> int:
    """
    Composite "всё в одном" для смены фото: attach-photos-all (подставить
    новые ссылки в ozon_catalog.xlsx/wb_catalog.xlsx/master_control.xlsx) +
    push-photos (реально отправить карточки с фото на Ozon И WB) +
    build-avito-catalog (пересобрать и перезалить фид для Avito — он сам
    подхватит по "Автозагрузке по ссылке"). Без отдельного dry-run — для
    простой смены фото у уже существующего товара это лишний шаг, карточка
    и так уже создана и прошла модерацию, тут меняются только ссылки на
    картинки.
    Ошибка на одном из трёх шагов не останавливает остальные — в конце
    выводится итог по каждому отдельно.
    """
    print("=== Шаг 1/3: attach-photos-all ===")
    rc_attach = cmd_attach_photos_all()

    print("\n=== Шаг 2/3: push-photos ===")
    rc_push = cmd_push_photos()

    print("\n=== Шаг 3/3: build-avito-catalog ===")
    rc_avito = cmd_build_avito_catalog()

    print()
    failed = [
        name for name, rc in (("attach-photos-all", rc_attach), ("push-photos", rc_push), ("build-avito-catalog", rc_avito))
        if rc != 0
    ]
    if failed:
        print(f"update-photos: ошибка на шаге(ах): {', '.join(failed)} — проверьте вывод выше.")
        return 1
    print("update-photos: готово, фото обновлены на Ozon, WB и в фиде Avito.")
    return 0


def _build_tilda_commerceml_files():
    """Общая часть build-tilda-commerceml и push-tilda: собирает import.xml/offers.xml в data/."""
    import tilda_commerceml

    catalog_path = _data_path("ozon_catalog.xlsx")
    photos_dir = os.path.join(os.path.dirname(__file__), "photos")
    if not os.path.exists(catalog_path):
        print(f"Нет файла {catalog_path} — сначала выполните build-ozon-catalog.")
        return None
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        print("Не удалось определить репозиторий (GITHUB_REPOSITORY пуст) — запускайте через GitHub Actions.")
        return None
    raw_base_url = f"https://raw.githubusercontent.com/{repo}/{branch}/photos"

    items, problems = tilda_commerceml.load_items(catalog_path, photos_dir, raw_base_url)
    # Фото — только новым товарам и один раз (см. tilda_commerceml.plan_images)
    image_plan = tilda_commerceml.plan_images(items)
    image_files = []  # [(имя в import_files/..., байты)]
    image_names = {}  # {артикул: [имена для <Картинка>]}
    for oid, files in image_plan.items():
        for path, name in files:
            try:
                data, new_ext = tilda_commerceml._image_bytes(path, 3 * 1024 * 1024)
            except Exception as exc:  # noqa: BLE001 — битое фото не должно ломать синхронизацию
                print(f"Фото для Тильды пропущено ({oid}): {exc}")
                continue
            if new_ext:
                name = os.path.splitext(name)[0] + new_ext
            image_files.append((name, data))
            image_names.setdefault(oid, []).append(name)
    import_xml = tilda_commerceml.build_import_xml(items, image_names)
    offers_xml = tilda_commerceml.build_offers_xml(items)
    with open(_data_path("tilda_import0_1.xml"), "w", encoding="utf-8") as f:
        f.write(import_xml)
    with open(_data_path("tilda_offers0_1.xml"), "w", encoding="utf-8") as f:
        f.write(offers_xml)

    zero = sum(1 for it in items if it["qty"] == 0)
    print(f"Собрано товаров: {len(items)} (из них с остатком 0 -> на сайте 'нет в наличии': {zero}).")
    if problems["no_price"]:
        print(f"Без цены (карточка не создаётся/не обновляется, остаток передаётся): {', '.join(problems['no_price'])}")
    if problems["no_photo"]:
        print(f"Без фото: {', '.join(problems['no_photo'])}")
    if image_names:
        print(f"Фото уйдут новым товарам ({len(image_files)} файлов): {', '.join(sorted(image_names))}")
    return import_xml, offers_xml, image_files, sorted(image_names)


def cmd_yandex_market_export() -> int:
    """
    Выгрузить ВСЕ товары кабинета Яндекс Маркета в data/yandex_market_offers.xlsx
    и сравнить SKU с нашим каталогом: что есть на Маркете, но нет у нас, и наоборот.
    Только чтение — на Маркете ничего не меняется.
    """
    import openpyxl

    import yandex_market_client

    if not yandex_market_client.is_configured():
        print("Яндекс Маркет не подключён: нет секретов YANDEX_MARKET_API_KEY / YANDEX_MARKET_CAMPAIGN_ID.")
        return 1
    try:
        offers = yandex_market_client.get_all_offers(archived=False)
    except yandex_market_client.YandexMarketError as exc:
        print(f"yandex-market-export: ОШИБКА — {exc}")
        return 1

    ours = set()
    catalog_path = _data_path("ozon_catalog.xlsx")
    if os.path.exists(catalog_path):
        ws_c = openpyxl.load_workbook(catalog_path, read_only=True, data_only=True).active
        ours = {str(r[0]).strip() for r in list(ws_c.iter_rows(values_only=True))[1:] if r and r[0]}
    market = {o["sku"] for o in offers}
    only_market = sorted(market - ours)
    only_ours = sorted(ours - market)

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Товары на Маркете"
    ws.append(["SKU (ваш артикул)", "Название", "В архиве", "Артикул Маркета", "Категория", "Есть в нашем каталоге"])
    for o in offers:
        ws.append([o["sku"], o["name"], "да" if o["archived"] else "нет", str(o["market_sku"] or ""), o["category"], "да" if o["sku"] in ours else "НЕТ"])
    ws2 = wb.create_sheet("Есть у нас, нет на Маркете")
    ws2.append(["Артикул"])
    for a in only_ours:
        ws2.append([a])
    wb.save(_data_path("yandex_market_offers.xlsx"))

    print(f"Товаров на Маркете (не в архиве): {len(offers)}; в нашем каталоге: {len(ours)}.")
    print(f"Есть на Маркете, но нет в каталоге ({len(only_market)}): {', '.join(only_market) or '—'}")
    print(f"Есть в каталоге, но нет на Маркете ({len(only_ours)}): {', '.join(only_ours) or '—'}")
    print("\nПолный список сохранён в data/yandex_market_offers.xlsx (после запуска сделайте Pull origin).")
    return 0


def cmd_yandex_market_test() -> int:
    """Проверить доступ к Яндекс Маркету (секреты YANDEX_MARKET_API_KEY / YANDEX_MARKET_CAMPAIGN_ID)."""
    import yandex_market_client

    if os.environ.get("YANDEX_MARKET_API_KEY", "").strip() and not os.environ.get("YANDEX_MARKET_CAMPAIGN_ID", "").strip():
        ok, msg = yandex_market_client.list_campaigns()
        print(f"Яндекс Маркет: {msg}")
        print("\nВпишите нужный номер магазина (кампании) в секрет YANDEX_MARKET_CAMPAIGN_ID и запустите проверку ещё раз.")
        return 0 if ok else 1
    ok, msg = yandex_market_client.test_connection()
    print(f"Яндекс Маркет: {msg}")
    return 0 if ok else 1


def _print_yandex_failures(failed: dict) -> None:
    if failed:
        print(f"\nНе приняты Маркетом ({len(failed)}) — чаще всего такого SKU там нет или он назван иначе:")
        for offer_id, reason in failed.items():
            print(f"  {offer_id}: {reason}")


def cmd_push_yandex_stock(dry_run: bool = False) -> int:
    """
    Остатки -> Яндекс Маркет (FBS): те же числа, что уходят на Ozon/WB
    ("Кол-во к продаже"). SKU на Маркете = артикул. Принятые/отклонённые
    товары печатаются списком; один отклонённый товар остальным не мешает.
    """
    import yandex_market_client

    if not yandex_market_client.is_configured():
        print("Яндекс Маркет не подключён: нет секретов YANDEX_MARKET_API_KEY / YANDEX_MARKET_CAMPAIGN_ID.")
        return 1
    items = _load_stock_items()
    stocks = {it["offer_id"]: it["stock"] for it in items}
    if not stocks:
        print("Нечего отправлять — ни у одного товара не заполнен остаток.")
        return 1

    # Маркет различает регистр SKU: у части товаров там есть второй вариант артикула, отличающийся
    # только регистром (например "0am325025L" рядом с нашим "0am325025l"). Список SKU Маркета берём
    # из data/yandex_market_offers.xlsx (команда yandex-market-export) и отправляем тот же остаток
    # на такие варианты, иначе там остался бы старый остаток.
    unmanaged = []
    offers_path = _data_path("yandex_market_offers.xlsx")
    if os.path.exists(offers_path):
        import openpyxl

        low = {k.lower(): v for k, v in stocks.items()}
        ws_m = openpyxl.load_workbook(offers_path, read_only=True, data_only=True).worksheets[0]
        for r in list(ws_m.iter_rows(values_only=True))[1:]:
            sku = str(r[0]).strip() if r and r[0] else ""
            if not sku or sku in stocks:
                continue
            if sku.lower() in low:
                stocks[sku] = low[sku.lower()]
                print(f"  {sku}: вариант артикула с другим регистром — отправляю остаток {low[sku.lower()]}")
            else:
                unmanaged.append(sku)
    if unmanaged:
        print(
            f"ВНИМАНИЕ: на Маркете есть товары, которых нет в нашем каталоге с остатком — их остаток бот НЕ меняет "
            f"({len(unmanaged)}): {', '.join(unmanaged)}"
        )
    if dry_run:
        print(f"ПРОБНЫЙ ПРОГОН — на Маркет ничего не уходит. Остатков к отправке: {len(stocks)}")
        for offer_id, n in sorted(stocks.items()):
            print(f"  {offer_id}: {n}")
        return 0
    try:
        ok, failed = yandex_market_client.update_stocks(stocks)
    except yandex_market_client.YandexMarketError as exc:
        print(f"push-yandex-stock: ОШИБКА — {exc}")
        return 1
    print(f"push-yandex-stock: принято Маркетом {ok} из {len(stocks)}.")
    _print_yandex_failures(failed)
    return 0 if ok else 1


def cmd_push_yandex_names(dry_run: bool = False) -> int:
    """
    Названия -> Яндекс Маркет: название товара на Маркете приводится к названию из
    data/master_control.xlsx (колонка «Название», то же, что на Ozon). Берётся свежий
    список товаров Маркета; меняются только названия, которые отличаются. Архивные
    и отсутствующие в мастере товары не трогаются. Сначала всегда пробный прогон.
    """
    import re

    import openpyxl

    import yandex_market_client

    if not yandex_market_client.is_configured():
        print("Яндекс Маркет не подключён: нет секретов YANDEX_MARKET_API_KEY / YANDEX_MARKET_CAMPAIGN_ID.")
        return 1
    master_path = _data_path("master_control.xlsx")
    if not os.path.exists(master_path):
        print(f"Нет файла {master_path}.")
        return 1

    def norm(t) -> str:
        return re.sub(r"\s+", " ", str(t or "")).strip().lower()

    master = {}
    ws = openpyxl.load_workbook(master_path, data_only=True)["Товары"]
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row and row[0] and row[5]:
            master[str(row[0]).strip()] = str(row[5]).strip()
    master_low = {k.lower() for k in master}

    try:
        offers = yandex_market_client.get_all_offers(archived=False)
    except yandex_market_client.YandexMarketError as exc:
        print(f"push-yandex-names: ОШИБКА — {exc}")
        return 1
    changes, same, unknown, case_only = {}, 0, [], []
    for o in offers:
        if o["archived"] or not o["sku"]:
            continue
        # Маркет различает регистр SKU: "0am325025L" и "0am325025l" там — РАЗНЫЕ товары. Меняем только точное совпадение.
        want = master.get(o["sku"])
        if not want:
            (case_only if o["sku"].lower() in master_low else unknown).append(o["sku"])
        elif norm(want) == norm(o["name"]):
            same += 1
        else:
            changes[o["sku"]] = (o["name"], want)
    print(f"На Маркете товаров: {len(offers)}; названия совпадают: {same}; отличаются: {len(changes)}; нет в мастере: {len(unknown)}")
    if unknown:
        print(f"  Нет в мастере (не трогаю): {', '.join(unknown)}")
    if case_only:
        print(f"  Артикул совпадает только без учёта регистра (другой товар Маркета, не трогаю): {', '.join(case_only)}")
    for sku, (old, new) in sorted(changes.items()):
        print(f"  {sku}:\n     было:  {old}\n     будет: {new[:yandex_market_client.NAME_MAX]}")
    if not changes:
        print("Менять нечего.")
        return 0
    if dry_run:
        print("\nПРОБНЫЙ ПРОГОН — на Маркет ничего не уходит.")
        return 0
    try:
        ok, failed = yandex_market_client.update_names({k: v[1] for k, v in changes.items()})
    except yandex_market_client.YandexMarketError as exc:
        print(f"push-yandex-names: ОШИБКА — {exc}")
        return 1
    print(f"push-yandex-names: принято Маркетом {ok} из {len(changes)}.")
    _print_yandex_failures(failed)
    return 0 if ok else 1


def cmd_push_yandex_price(dry_run: bool = False) -> int:
    """
    Цены -> Яндекс Маркет: берётся ТА ЖЕ цена, что и на Ozon (колонка 'Цена, ₽' в
    ozon_catalog.xlsx), а 'цена до скидки' передаётся как зачёркнутая, если
    скидка укладывается в 5-99%. Для товаров без цены ничего не отправляется.
    ВАЖНО: перезаписывает цены на Маркете — сначала всегда пробный прогон.
    """
    import openpyxl

    import yandex_market_client

    if not yandex_market_client.is_configured():
        print("Яндекс Маркет не подключён: нет секретов YANDEX_MARKET_API_KEY / YANDEX_MARKET_CAMPAIGN_ID.")
        return 1
    catalog_path = _data_path("ozon_catalog.xlsx")
    if not os.path.exists(catalog_path):
        print(f"Нет файла {catalog_path}.")
        return 1
    prices = {}
    ws = openpyxl.load_workbook(catalog_path, data_only=True).active
    for row in ws.iter_rows(min_row=2, values_only=True):
        row = list(row) + [None] * 6
        if not row[0]:
            continue
        try:
            price = int(float(row[3]))
        except (TypeError, ValueError):
            continue
        if price <= 0:
            continue
        try:
            old = int(float(row[4])) if row[4] not in (None, "") else None
        except (TypeError, ValueError):
            old = None
        prices[str(row[0]).strip()] = (price, old)
    if not prices:
        print("Нечего отправлять — нет товаров с ценой.")
        return 1
    if dry_run:
        print(f"ПРОБНЫЙ ПРОГОН — на Маркет ничего не уходит. Цен к отправке: {len(prices)}")
        for offer_id, (p, old) in sorted(prices.items()):
            print(f"  {offer_id}: {p}" + (f" (было {old})" if old and old > p else ""))
        return 0
    try:
        ok, failed = yandex_market_client.update_prices(prices)
    except yandex_market_client.YandexMarketError as exc:
        print(f"push-yandex-price: ОШИБКА — {exc}")
        return 1
    print(f"push-yandex-price: принято Маркетом {ok} из {len(prices)}.")
    _print_yandex_failures(failed)
    return 0 if ok else 1


def cmd_build_tilda_commerceml() -> int:
    """Только собрать data/tilda_import0_1.xml и data/tilda_offers0_1.xml (без отправки) — для проверки глазами."""
    return 0 if _build_tilda_commerceml_files() else 1


def cmd_push_tilda() -> int:
    """
    Собрать каталог и отправить в Тильду по CommerceML (см. tilda_commerceml.py):
    цены, остатки, новые товары; остаток 0 -> "нет в наличии". Нужны секреты
    TILDA_COMMERCEML_URL / TILDA_COMMERCEML_USER / TILDA_COMMERCEML_PASSWORD.
    Первый запуск — ВНИМАТЕЛЬНО проверьте "Историю синхронизации" в Тильде.
    """
    import tilda_commerceml

    url = os.environ.get("TILDA_COMMERCEML_URL", "").strip()
    user = os.environ.get("TILDA_COMMERCEML_USER", "").strip()
    password = os.environ.get("TILDA_COMMERCEML_PASSWORD", "").strip()
    if not (url and user and password):
        print(
            "Не заданы секреты TILDA_COMMERCEML_URL / TILDA_COMMERCEML_USER / TILDA_COMMERCEML_PASSWORD "
            "(GitHub -> Settings -> Secrets and variables -> Actions). Значения берутся в Тильде: "
            "Товары -> три точки -> Синхронизация через CommerceML. Пока можно запускать build-tilda-commerceml."
        )
        return 1

    built = _build_tilda_commerceml_files()
    if not built:
        return 1
    import_xml, offers_xml, image_files, image_ids = built
    try:
        log = tilda_commerceml.push(url, user, password, import_xml, offers_xml, image_files=image_files)
    except Exception as exc:  # noqa: BLE001
        # В тексте ошибки не печатаем URL/пароль — только ответ сервера.
        print(f"push-tilda: ОШИБКА — {exc}")
        print("Загляните в Тильду: Товары -> три точки -> Синхронизация через CommerceML -> История синхронизации.")
        return 1
    for line in log:
        print("  " + line)
    if image_ids:
        tilda_commerceml.mark_images_sent(image_ids)
        print("Фото отправлены (повторно не шлём): " + ", ".join(image_ids))
        print("Если на сайте их нет — включите в Тильде «Принимать изображения» (Синхронизация через CommerceML -> "
              "Дополнительные настройки) и удалите эти артикулы из data/tilda_images_sent.json.")
    print("push-tilda: готово. Проверьте результат на сайте и в 'Истории синхронизации' в Тильде.")
    return 0


def cmd_validate_master() -> int:
    """Проверка data/master_control.xlsx без отправки куда-либо (см. validate_master.py)."""
    import validate_master

    return validate_master.run()



def cmd_push_yandex_videos(dry_run: bool = False) -> int:
    """
    Видео -> Яндекс Маркет: те же ссылки, что уходят на Ozon (колонка «Видео» в data/ozon_catalog.xlsx,
    её заполняет attach-ozon-photos из photos/<АРТИКУЛ>/*_video.*). Отправляем только товарам,
    которые УЖЕ есть на Маркете (список из data/yandex_market_offers.xlsx; новые товары метод
    не создаёт). SKU сопоставляются без учёта регистра — видео уходит на все варианты артикула.
    Уходит только новое/изменившееся (кэш data/market_video_cache.json; удалить файл = отправить всё заново).
    """
    import openpyxl
    import yandex_market_client

    if not yandex_market_client.is_configured():
        print("Яндекс Маркет не подключён — пропуск.")
        return 0
    cat = _data_path("ozon_catalog.xlsx")
    offers_path = _data_path("yandex_market_offers.xlsx")
    if not os.path.exists(cat) or not os.path.exists(offers_path):
        print("Нет data/ozon_catalog.xlsx или data/yandex_market_offers.xlsx — пропуск.")
        return 0
    ws = openpyxl.load_workbook(cat, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    vcol = next((i for i, h in enumerate(rows[0]) if h and "идео" in str(h)), None)
    if vcol is None:
        print("В ozon_catalog.xlsx нет колонки видео — пропуск.")
        return 0
    ours = {}
    for r in rows[1:]:
        if r and r[0] and vcol < len(r) and r[vcol]:
            urls = [u.strip() for u in str(r[vcol]).split("|") if u.strip().startswith("http")]
            if urls:
                ours[str(r[0]).strip().lower()] = urls
    ws_m = openpyxl.load_workbook(offers_path, read_only=True, data_only=True).worksheets[0]
    market_skus = [str(r[0]).strip() for r in list(ws_m.iter_rows(values_only=True))[1:] if r and r[0]]
    want = {sku: ours[sku.lower()] for sku in market_skus if sku.lower() in ours}
    missing = sorted(k for k in ours if k not in {s.lower() for s in market_skus})
    if missing:
        print(f"Нет на Маркете (видео не отправляется): {', '.join(missing)}")

    cache_path = _data_path("market_video_cache.json")
    try:
        cache = json.load(open(cache_path, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cache = {}
    todo = {sku: urls for sku, urls in want.items() if cache.get(sku) != urls}
    if not todo:
        print(f"Видео на Маркете актуальны ({len(want)} товаров) — отправлять нечего.")
        return 0
    print(f"Видео к отправке на Маркет: {len(todo)}")
    for sku in sorted(todo):
        print(f"  {sku}: {todo[sku][0].split('/')[-1]}")
    if dry_run:
        print("ПРОБНЫЙ ПРОГОН — на Маркет ничего не уходит.")
        return 0
    try:
        ok, failed = yandex_market_client.update_videos(todo)
    except Exception as exc:  # noqa: BLE001
        print(f"push-yandex-videos: ОШИБКА — {exc}")
        return 1
    for sku, urls in todo.items():
        if sku not in failed:
            cache[sku] = urls
    with open(cache_path, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1, sort_keys=True)
    for sku, why in failed.items():
        print(f"  НЕ принято {sku}: {why}")
    print(f"push-yandex-videos: принято Маркетом {ok} из {len(todo)}.")
    return 1 if failed else 0

def cmd_update_cards() -> int:
    """Обёртка: весь вывод update-cards дублируется в data/update_cards_log.txt (логи Actions нам недоступны)."""
    import io

    class _Tee(io.TextIOBase):
        def __init__(self, a, b):
            self.a, self.b = a, b

        def write(self, x):
            self.a.write(x)
            self.b.write(x)
            return len(x)

        def flush(self):
            self.a.flush()
            self.b.flush()

    old = sys.stdout
    with open(_data_path("update_cards_log.txt"), "w", encoding="utf-8") as fh:
        sys.stdout = _Tee(old, fh)
        try:
            return _cmd_update_cards_impl()
        except Exception as exc:  # noqa: BLE001
            print(f"update-cards: исключение — {exc}")
            raise
        finally:
            sys.stdout = old


def _cmd_update_cards_impl() -> int:
    """
    ОДНА команда "обновить всё везде" после правок в data/master_control.xlsx
    (названия, описания, цены, остатки) и/или замены файлов в photos/:

      0) проверка master_control (ошибки блокируют отправку, предупреждения только показываются);
      0а-2, 0а-3) недостающие товары мастера создаются на WB и на Ozon (до 5 за запуск на каждой);
      0б) цена WB приравнивается к цене Ozon (правило «одна цена на Ozon/WB/Маркете»);
      1) sync-master-control — правки пульта -> рабочие файлы (если упал — дальше
         НЕ идём, иначе отправили бы недосинхронизированное);
      2) attach-photos-all, 3) push-photos — карточки и фото на Ozon и WB
         (уходит только изменившееся);
      4) push-wb-price — цена WB;
      5) push-stock, 6) push-wb-stock — остатки на Ozon и WB;
      7) Яндекс Маркет — остатки (цены он берёт из Ozon сам), только если подключены секреты;
      8) build-avito-catalog — фид Avito (товары с остатком 0 закрываются);
      9) Tilda (сайт) через CommerceML — только если подключены секреты;
     10) ревизия площадок (audit, только чтение) — что где не доехало.

    Ошибка на шагах 2-9 не останавливает следующие; итог по каждому — в конце.
    Шаги Маркета и Tilda без секретов молча пропускаются.
    """
    print("=== Проверка master_control.xlsx (ОШИБКИ блокируют отправку, предупреждения только показываются) ===")
    try:
        import validate_master

        if validate_master.run(quiet_ok=True) != 0:
            print("\nupdate-cards: в master_control.xlsx есть ошибки (см. выше) — на площадки НИЧЕГО не отправлено. Исправьте мастер и Commit+Push (или запустите update-cards ещё раз).")
            return 1
    except Exception as exc:  # noqa: BLE001 — сбой самой проверки не должен ронять update-cards
        print(f"(проверка не выполнена: {exc})")

    # Свежие карточки WB — чтобы только что созданные карточки сразу получили фото/остатки/цену (за один запуск).
    def _refresh_wb_cards():
        cmd_fetch_wb()
        import wb_catalog_editor

        with open(_data_path("wb_cards.json"), "r", encoding="utf-8") as f_:
            n_new = wb_catalog_editor.append_missing_wb_catalog_rows(json.load(f_), _data_path("wb_catalog.xlsx"))
        if n_new:
            print(f"В wb_catalog.xlsx добавлено строк для новых карточек WB: {n_new}")

    print("\n=== Шаг 0а: свежие карточки WB ===")
    try:
        _refresh_wb_cards()
    except Exception as exc:  # noqa: BLE001 — не должно ронять update-cards
        print(f"(обновление карточек WB пропущено: {exc})")

    # Новые товары мастера, которых нет на WB, создаются здесь же (до WB_AUTO_CREATE_MAX за запуск),
    # затем карточки перечитываются — и фото/цена/остаток доезжают в этом же запуске.
    print("\n=== Шаг 0а-2: создать недостающие карточки WB ===")
    try:
        _WB_LAST_CREATED.clear()
        cmd_wb_create_missing(dry_run=False, auto=True)
        if _WB_LAST_CREATED:
            print(f"Создано на WB: {', '.join(_WB_LAST_CREATED)} — перечитываю карточки WB.")
            _refresh_wb_cards()
    except Exception as exc:  # noqa: BLE001 — не должно ронять update-cards
        print(f"(автосоздание карточек WB пропущено: {exc})")

    print("\n=== Шаг 0а-3: создать недостающие карточки Ozon ===")
    try:
        _OZON_LAST_CREATED.clear()
        cmd_ozon_create_missing(dry_run=False, auto=True)
    except Exception as exc:  # noqa: BLE001 — не должно ронять update-cards
        print(f"(автосоздание карточек Ozon пропущено: {exc})")

    # Новые карточки, уже созданные на Ozon, должны попасть в общий каталог (ozon_catalog.xlsx) — иначе им не
    # уходят фото/остаток на Ozon и они не появляются на Авито и сайте (найдено 2026-10-28 на 0AM927769F).
    print("\n=== Шаг 0а-4: новые карточки Ozon -> общий каталог ===")
    try:
        import openpyxl as _ox
        with open(_data_path("ozon_products.json"), "r", encoding="utf-8") as _f:
            _on_ozon = {p.get("offer_id") for p in json.load(_f).get("names", []) if p.get("offer_id")}
        _ws = _ox.load_workbook(_data_path("ozon_catalog.xlsx"), read_only=True).active
        _in_cat = {str(r[0]).strip() for r in _ws.iter_rows(min_row=2, values_only=True) if r and r[0]}
        _missing = sorted((_on_ozon - _in_cat) | (set(_OZON_LAST_CREATED) - _in_cat))
        if _missing:
            print(f"Есть на Ozon, но нет в каталоге: {', '.join(_missing)} — fetch-ozon + build-ozon-catalog.")
            if cmd_fetch_ozon() == 0:
                cmd_build_ozon_catalog()
        else:
            print("Все карточки Ozon уже в каталоге.")
    except Exception as exc:  # noqa: BLE001 — не должно ронять update-cards
        print(f"(обновление каталога Ozon пропущено: {exc})")

    # Правило: цена на Ozon, WB и Маркете одинаковая. Перед переносом правок мастера в каталоги
    # цена WB приравнивается к цене Ozon (колонки «Цена WB» и «Цена, ₽» мастера).
    print("\n=== Шаг 0б: цена WB = цена Ozon ===")
    try:
        cmd_align_wb_prices(dry_run=False)
    except Exception as exc:  # noqa: BLE001 — не должно ронять update-cards
        print(f"(выравнивание цены WB пропущено: {exc})")

    # Цена Авито/сайта = доля (70%) от цены маркетплейсов; доля — в data/avito_site_price_overrides.xlsx (лист «Настройки»).
    print("\n=== Шаг 0в: цены Авито и сайта = доля от цены маркетплейсов ===")
    try:
        import site_pricing

        changed = site_pricing.recalc_from_master(_data_path("master_control.xlsx"))
        _pm = site_pricing.get_profit_settings()
        _rule = (f"равная прибыль (налог {_pm['tax']:.0%}, Авито {_pm['avito_fee']:.0%}, эквайринг сайта {_pm['site_fee']:.1%})"
                 if _pm["mode"] == "прибыль" else f"доля {site_pricing.get_share():.0%}")
        print(f"Правило: {_rule}; пересчитано цен Авито/сайта: {len(changed)}")
        for a_, o_, n_ in changed[:15]:
            print(f"  {a_}: {o_ if o_ is not None else '—'} -> {n_:g}")
        if len(changed) > 15:
            print(f"  ... и ещё {len(changed) - 15}")
    except Exception as exc:  # noqa: BLE001 — не должно ронять update-cards
        print(f"(пересчёт цен Авито/сайта пропущен: {exc})")

    print("\n=== Шаг 1: sync-master-control ===")
    rc_sync = cmd_sync_master_control()
    if rc_sync != 0:
        print("\nupdate-cards: sync-master-control завершился с ошибкой — дальше не иду, проверьте вывод выше.")
        return 1

    results = []

    def step(title: str, name: str, fn) -> None:
        print(f"\n=== {title} ===", flush=True)
        t0 = time.time()
        try:
            rc = fn()
        except Exception as exc:  # noqa: BLE001 — один шаг не должен обрывать остальные
            print(f"{name}: исключение — {exc}")
            rc = 1
        print(f"[{name}: {time.time() - t0:.0f} с]", flush=True)
        results.append((name, rc))

    step("Шаг 2: attach-photos-all", "attach-photos-all", cmd_attach_photos_all)
    step("Шаг 3: push-photos (Ozon + WB)", "push-photos", cmd_push_photos)

    def _ozon_rich_step() -> int:
        import ozon_rich

        return ozon_rich.run(dry_run=False)

    # После фото: rich ссылается на фото карточки на Ozon (метки {{img:N}} в data/ozon_rich/*.json).
    step("Шаг 3б: Ozon rich-контент (data/ozon_rich, только изменённое)", "ozon-rich", _ozon_rich_step)
    step("Шаг 4: push-wb-price", "push-wb-price", cmd_push_wb_price)
    # Цены Ozon после перехода на «предельную цену» (окт. 2026) не всегда доезжают через карточку — добиваем напрямую (только расхождения).
    step("Шаг 4б: цены Ozon = мастер (только расхождения)", "sync-ozon-prices", lambda: cmd_sync_ozon_prices(False))
    step("Шаг 5: push-stock (Ozon)", "push-stock", cmd_push_stock)
    step("Шаг 6: push-wb-stock (WB)", "push-wb-stock", cmd_push_wb_stock)
    step("Шаг 6б: Ozon — контроль акций (цена не ниже нашей)", "ozon-leave-boosting", cmd_ozon_leave_boosting)

    # Яндекс Маркет: ЦЕНЫ приходят из Ozon сами (встроенная синхронизация Маркета), а ОСТАТКИ
    # она не переносит — их отправляем мы (общий остаток, тот же, что на Ozon/WB). Цены на Маркет
    # не шлём (двойной источник = конфликт; при необходимости — push-yandex-price в tools).
    import yandex_market_client

    if yandex_market_client.is_configured():
        step("Шаг 7: Яндекс Маркет — остатки", "push-yandex-stock", cmd_push_yandex_stock)
        step("Шаг 7а: Яндекс Маркет — видео", "push-yandex-videos", cmd_push_yandex_videos)
    else:
        print("\n(Яндекс Маркет не подключён — шаг пропущен)")

    def _avito_photos_step() -> int:
        # Фото для Авито (photos_avito/) заливаются по хэшу содержимого — неизменённые файлы не перезаливаются.
        import avito_feed

        if not os.path.isdir(avito_feed.AVITO_PHOTOS_DIR):
            return 0
        matched = avito_feed.attach_avito_photos()
        print(f"Фото для Авито: товаров с актуальными ссылками — {len(matched)}")
        return 0

    step("Шаг 7б: фото для Авито (photos_avito)", "attach-avito-photos", _avito_photos_step)
    step("Шаг 8: build-avito-catalog", "build-avito-catalog", cmd_build_avito_catalog)

    if os.environ.get("TILDA_COMMERCEML_URL", "").strip():
        step("Шаг 9: Tilda (сайт), CommerceML", "push-tilda", cmd_push_tilda)
    else:
        print("\n(Tilda CommerceML не подключена — шаг пропущен)")

    # Шаг 10: ревизия (только чтение) — сразу видно, что после обновления где-то не доехало.
    print("\n=== Шаг 10: ревизия площадок (только чтение, на результат update-cards не влияет) ===")
    try:
        cmd_audit(refresh=True, skip_wb=True)
    except Exception as exc:  # noqa: BLE001
        print(f"ревизия пропущена: {exc}")

    print()
    failed = [name for name, rc in results if rc != 0]
    if failed:
        print(f"update-cards: ошибка на шаге(ах): {', '.join(failed)} — проверьте вывод выше (остальные шаги выполнены).")
        return 1
    print(
        "update-cards: готово — всё обновлено: Ozon, WB, Avito"
        + (", сайт" if os.environ.get("TILDA_COMMERCEML_URL", "").strip() else "")
        + (", Яндекс Маркет (остатки)" if yandex_market_client.is_configured() else "")
        + "."
    )
    return 0


def cmd_push_wb_price_dryrun() -> int:
    import wb_catalog_editor

    wb_data, edits = _load_wb_push_inputs()
    if wb_data is None:
        return 1
    updates = wb_catalog_editor.build_wb_price_updates(wb_data, edits)

    print("ПРОБНЫЙ ПРОГОН — в WB ничего не отправляется.")
    print(f"Изменений цены: {len(updates)} товаров")
    for u in updates:
        print(
            f"  {u['vendor_code']}: {u['old_final_price']} -> {u['new_final_price']} ₽ "
            f"(скидка {u['discount']}% не меняется, в WB уйдёт 'цена до скидки' = {u['price_to_send']})"
        )
    if not updates:
        print("Либо колонка 'Цена WB' пуста у всех товаров, либо вписанные значения совпадают с текущей ценой на WB.")
    return 0


def cmd_push_wb_price() -> int:
    import wb_client
    import wb_catalog_editor

    wb_data, edits = _load_wb_push_inputs()
    if wb_data is None:
        return 1
    updates = wb_catalog_editor.build_wb_price_updates(wb_data, edits)
    if not updates:
        print("Цены на WB менять не нужно: ни одна цена в wb_catalog.xlsx не отличается от текущей на WB.")
        return 0

    items = [{"nmID": u["nm_id"], "price": u["price_to_send"], "discount": u["discount"]} for u in updates]
    try:
        upload = wb_client.update_prices(items)
    except wb_client.WbApiError as exc:
        print(f"ОШИБКА при отправке цены ({len(items)} товаров): {exc}")
        return 1

    print(f"Цена отправлена для {len(items)} товаров:")
    for u in updates:
        step = "" if u.get("target_price") in (None, u["new_final_price"]) else f"  (ступенью: WB не даёт снижать больше чем вдвое; цель {u['target_price']} ₽ — следующим запуском)"
        print(f"  {u['vendor_code']}: {u['old_final_price']} -> {u['new_final_price']} ₽{step}")
    # Узнаём, что WB реально сделал с ценами (применил / карантин / ошибка) — не ждём вслепую.
    try:
        upload_id = ((upload or {}).get("data") or {}).get("id")
        if upload_id:
            by_nm = {u["nm_id"]: u["vendor_code"] for u in updates}
            res = {}
            for _ in range(6):
                time.sleep(8)
                res = wb_client.get_price_upload_result(upload_id)
                if res["goods"]:
                    break
            bad = 0
            for g in res.get("goods", []):
                err = str(g.get("errorText") or "").strip()
                if err:
                    bad += 1
                    print(f"  WB НЕ ПРИМЕНИЛ {by_nm.get(g.get('nmID'), g.get('nmID'))}: {err[:300]}")
            if res.get("goods") and not bad:
                print("  WB принял все цены без ошибок.")
            elif not res.get("goods"):
                print(f"  (WB ещё обрабатывает загрузку {upload_id}; итог смотрите в кабинете: Цены и скидки → История)")
    except Exception as exc:  # noqa: BLE001 — диагностика не должна ронять шаг
        print(f"  (не удалось получить итог загрузки цен: {exc})")
    print(
        "\nWB обрабатывает изменение цены не мгновенно — проверьте карточку через несколько минут "
        "(следующий fetch-wb покажет уже применившуюся цену)."
    )
    return 0


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
    total_skipped = 0
    submitted_offer_ids = [i.get("offer_id") for i in items if i.get("offer_id")]
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
    skipped_offer_ids = []
    for it in status.get("result", {}).get("items", []):
        blocking, warnings = _split_errors_by_level(it.get("errors") or [])
        item_status = it.get("status")
        if blocking:
            total_err += 1
            print(f"  ОШИБКА {it.get('offer_id')}: {blocking}")
        else:
            total_ok += 1
            note = f" (предупреждение: {warnings})" if warnings else ""
            print(f"  ОК {it.get('offer_id')}: статус {item_status}{note}")
        # "skipped" на практике не всегда означает, что карточка реально
        # создана — Ozon иногда возвращает skipped/без errors для товаров,
        # которые так и не появляются в каталоге (похоже на дедупликацию
        # по похожести карточки с чужим/своим товаром). Печатаем сырой
        # ответ целиком для таких товаров и потом сверяем с list_products.
        if item_status and item_status != "imported":
            total_skipped += 1
            skipped_offer_ids.append(it.get("offer_id"))
            print(f"    сырой ответ Ozon: {json.dumps(it, ensure_ascii=False)}")
    print(f"\nИтого: успешно {total_ok}, с ошибками {total_err}, из них со статусом не 'imported' (skipped/др.): {total_skipped}")

    # Проверяем правду в лоб: реально ли карточки появились у продавца,
    # независимо от того, что сказал статус импорта. /v3/product/list
    # отдаёт offer_id+product_id для ВСЕХ товаров продавца (visibility ALL).
    if submitted_offer_ids:
        print("\nСверяю с фактическим списком товаров продавца (list_products)...")
        try:
            all_products = ozon_client.list_products()
        except Exception as exc:
            print(f"  Не удалось получить список товаров для сверки: {exc}")
            all_products = []
        existing_offer_ids = {p.get("offer_id") for p in all_products if p.get("offer_id")}
        confirmed_missing = [oid for oid in submitted_offer_ids if oid not in existing_offer_ids]
        confirmed_created = [oid for oid in submitted_offer_ids if oid in existing_offer_ids]
        print(f"  Реально существуют у продавца: {len(confirmed_created)} из {len(submitted_offer_ids)} отправленных")
        if confirmed_missing:
            print(f"  ПОДТВЕРЖДЕНО ОТСУТСТВУЮТ несмотря на статус импорта: {confirmed_missing}")
        if skipped_offer_ids:
            skipped_but_created = [oid for oid in skipped_offer_ids if oid in existing_offer_ids]
            skipped_and_missing = [oid for oid in skipped_offer_ids if oid not in existing_offer_ids]
            if skipped_but_created:
                print(f"  Из статуса не-'imported' — на деле СУЩЕСТВУЮТ (создались раньше): {skipped_but_created}")
            if skipped_and_missing:
                print(f"  Из статуса не-'imported' — на деле НЕ СУЩЕСТВУЮТ (реальная проблема): {skipped_and_missing}")

    if total_ok:
        print("\nНе забудьте выполнить fetch-ozon ещё раз, чтобы новые товары попали в общий каталог.")
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


def cmd_build_new_product_template() -> int:
    import new_product

    path = _data_path("new_products.xlsx")
    if os.path.exists(path):
        print(f"{path} уже существует — не трогаю, чтобы не затереть уже заполненное.")
        return 0
    new_product.build_template(path)
    print(
        f"Готово: {path}. Заполните строку (артикул, образец, название, описание, цена...) — "
        "один и тот же артикул и образец используются сразу для Ozon и WB. "
        "Фото просто положите в photos/<артикул>/ — подхватятся сами, отдельно вписывать не нужно."
    )
    return 0


def _load_new_product_edits():
    import new_product

    path = _data_path("new_products.xlsx")
    if not os.path.exists(path):
        print(f"Нет файла {path} — сначала выполните build-new-product-template.")
        return None
    edits = new_product.load_edits(path)
    if not edits:
        print(f"{path} пуст — заполните хотя бы одну строку.")
        return None
    photos_dir = os.path.join(os.path.dirname(__file__), "photos")
    matched = new_product.attach_photos(edits, photos_dir)
    for offer_id in edits:
        if offer_id in matched:
            print(f"  {offer_id}: найдено {len(matched[offer_id])} фото в photos/{offer_id}/")
        else:
            print(f"  ВНИМАНИЕ {offer_id}: нет фото в photos/{offer_id}/ — карточка Ozon создастся без фото.")
    return edits


def cmd_push_new_product_all_dryrun() -> int:
    import new_product

    edits = _load_new_product_edits()
    if edits is None:
        return 1
    ozon_edits = new_product.to_ozon_edits(edits)
    wb_edits = new_product.to_wb_edits(edits)
    print(f"\nПРОБНЫЙ ПРОГОН — никуда ничего не отправляется. Товаров к созданию: {len(edits)}")
    print("\n--- Ozon (создастся сразу с фото) ---")
    print(json.dumps(ozon_edits, ensure_ascii=False, indent=2))
    print("\n--- WB (создастся БЕЗ фото — так работает API WB, фото доедут через finish-new-product) ---")
    print(json.dumps(wb_edits, ensure_ascii=False, indent=2))
    return 0


def cmd_push_new_product_all() -> int:
    import new_product
    import ozon_client
    import ozon_new_products
    import wb_client
    import wb_new_products

    edits = _load_new_product_edits()
    if edits is None:
        return 1

    # --- Ozon ---
    ozon_src_path = _data_path("ozon_products.json")
    ozon_ok = 0
    if not os.path.exists(ozon_src_path):
        print(f"\nOzon: нет файла {ozon_src_path} — сначала выполните fetch-ozon (нужны данные образцов). Ozon-часть пропущена.")
    else:
        with open(ozon_src_path, "r", encoding="utf-8") as f:
            ozon_data = json.load(f)
        ozon_items = ozon_new_products.build_new_import_items(ozon_data, new_product.to_ozon_edits(edits))
        if not ozon_items:
            print("\nOzon: нечего отправлять (образец не найден в ozon_products.json).")
        else:
            result = ozon_client.import_products(ozon_items)
            task_id = result.get("result", {}).get("task_id")
            print(f"\nOzon: task_id={task_id}, жду обработку...")
            status = {}
            if task_id:
                for _ in range(30):
                    time.sleep(2)
                    status = ozon_client.get_import_status(task_id)
                    if status.get("result", {}).get("items"):
                        break
            for it in status.get("result", {}).get("items", []):
                blocking, warnings = _split_errors_by_level(it.get("errors") or [])
                if blocking:
                    print(f"  Ozon ОШИБКА {it.get('offer_id')}: {blocking}")
                else:
                    ozon_ok += 1
                    note = f" (предупреждение: {warnings})" if warnings else ""
                    print(f"  Ozon ОК {it.get('offer_id')}: статус {it.get('status')}{note}")

    # --- WB ---
    wb_src_path = _data_path("wb_cards.json")
    wb_ok = 0
    if not os.path.exists(wb_src_path):
        print(f"\nWB: нет файла {wb_src_path} — сначала выполните fetch-wb (нужны данные образцов). WB-часть пропущена.")
    else:
        with open(wb_src_path, "r", encoding="utf-8") as f:
            wb_data = json.load(f)
        wb_edits = new_product.to_wb_edits(edits)
        barcodes = wb_client.generate_barcodes(max(len(wb_edits), 1))
        groups = wb_new_products.build_new_card_groups(wb_data, wb_edits, barcodes)
        if not groups:
            print("\nWB: нечего отправлять (образец не найден в wb_cards.json).")
        else:
            for group in groups:
                try:
                    wb_client.create_cards(group["subject_id"], [group["variant"]])
                    print(f"  WB ОК: {group['vendor_code']} отправлен на создание (раздел {group['subject_id']}).")
                    wb_ok += 1
                except wb_client.WbApiError as exc:
                    print(f"  WB ОШИБКА при создании {group['vendor_code']}: {exc}")

    # Цена для Avito/сайта = половина от цены на маркетплейсах, только для
    # НОВЫХ товаров (решение пользователя 2026-09-17, см. site_pricing.py).
    # Пишем оверрайд для каждого товара, который реально ушёл хотя бы на
    # одну площадку — если посчитать цену не удалось (пустая/некорректная),
    # не молчим, а прямо просим пользователя проставить её вручную.
    import site_pricing

    if ozon_ok or wb_ok:
        need_manual_price = []
        for offer_id, e in edits.items():
            half = site_pricing.half_price(e.get("price"))
            if half is None:
                need_manual_price.append(offer_id)
                continue
            site_pricing.set_price_override(offer_id, half)
        if need_manual_price:
            print(
                "\nВНИМАНИЕ: не удалось посчитать цену для Avito/сайта (нет/некорректная цена "
                f"в new_products.xlsx) для: {', '.join(need_manual_price)}. "
                f"Проставьте цену вручную в data/avito_site_price_overrides.xlsx (offer_id -> цена) "
                "перед build-avito-catalog/build-tilda-catalog, иначе для них будет использована "
                "обычная (маркетплейсовая) цена."
            )

    print(f"\nИтого: Ozon создано {ozon_ok}, WB отправлено на создание {wb_ok} (из {len(edits)} строк(и)).")
    if ozon_ok or wb_ok:
        print(
            "\nДальше:\n"
            "  1. Подождите 2-5 минут (WB обрабатывает создание асинхронно).\n"
            "  2. Запустите finish-new-product — подтянет товар в общий каталог, доливает фото на WB "
            "и обновит фиды Avito и сайта."
        )
    return 0 if (ozon_ok or wb_ok) else 1


def cmd_finish_new_product() -> int:
    """
    Второй (и последний) шаг после push-new-product-all: подтягивает уже
    реально созданные товары с Ozon/WB в общий каталог (data/ozon_catalog.xlsx),
    доливает фото на новые карточки WB (на Ozon они уже были при создании) и
    обновляет фиды Avito и сайта — после этого новый товар виден везде.
    Безопасно запускать даже если что-то из шагов ещё не готово — каждый шаг
    просто ничего не найдёт и пропустится, ошибка не прерывает остальное.
    """
    print("=== Ozon: fetch-ozon + build-ozon-catalog + фото + master-control ===")
    rc = cmd_full_sync_ozon()
    if rc != 0:
        print("Ozon-часть завершилась с ошибкой — проверьте вывод выше, продолжаю с WB.")

    print("\n=== WB: fetch-wb + build-wb-catalog + attach-wb-photos ===")
    rc_wb = cmd_full_sync_wb()
    if rc_wb != 0:
        print("WB-часть завершилась с ошибкой — проверьте вывод выше, фото на WB не доливаю.")
    else:
        print("\n=== push-wb-cards (доливаем фото на новые карточки WB) ===")
        cmd_push_wb_cards()

    print("\n=== build-avito-catalog ===")
    cmd_build_avito_catalog()

    print("\n=== build-tilda-catalog ===")
    cmd_build_tilda_catalog()

    print(
        "\nГотово. Проверьте: новый товар должен появиться в data/master_control.xlsx, "
        "в фиде Avito (подхватится автозагрузкой) и в свежем data/tilda_feed.csv "
        "(его ещё нужно вручную импортировать в личном кабинете Тильды, см. build-tilda-catalog)."
    )
    return 0


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


def cmd_ozon_warehouses() -> int:
    import ozon_client

    warehouses = ozon_client.get_warehouses()
    print(json.dumps(warehouses, ensure_ascii=False, indent=2))
    print("\nСкопируйте нужный warehouse_id (склад FBS) в OZON_WAREHOUSE_ID в .env / GitHub Secrets — без него push-stock не работает.")
    return 0


def cmd_attach_avito_photos() -> int:
    """
    Залить фото из photos_avito/<артикул>/ (отдельный, квадратный формат
    под Avito — обычные фото из photos/ у Avito обрезаются превью-квадратом,
    см. пояснение в avito_feed.py) как ассеты GitHub Release и сохранить
    ссылки в data/avito_photos.xlsx. Запускать ПЕРЕД build-avito-catalog,
    если появились новые/обновлённые фото в photos_avito/.
    """
    import avito_feed

    photos_dir = avito_feed.AVITO_PHOTOS_DIR
    if not os.path.isdir(photos_dir):
        print(
            f"Нет папки {photos_dir} — загрузите в репозиторий фото под Avito "
            "(по подпапкам photos_avito/<артикул>/) и запустите снова."
        )
        return 1

    matched = avito_feed.attach_avito_photos()
    if not matched:
        print("Не нашлось фото для заливки в photos_avito/.")
        return 1

    print(f"Залито фото для {len(matched)} товаров -> data/avito_photos.xlsx:")
    for offer_id, urls in matched.items():
        print(f"  {offer_id}: {len(urls)} фото")
    print("\nТеперь запустите build-avito-catalog — фид подхватит эти ссылки автоматически.")
    return 0


def cmd_build_avito_catalog() -> int:
    """
    Собрать data/avito_feed.xlsx и залить его как ассет GitHub Release под
    ПОСТОЯННЫМ именем ("avito_feed.xlsx", без версии в имени) — чтобы вставить
    получившуюся ссылку ОДИН РАЗ в личном кабинете Avito (Автозагрузка ->
    Настройки -> "Автозагрузка по ссылке") и дальше Avito сам, по своему
    расписанию (настраивается там же — отметьте как можно больше дней/часов,
    чтобы изменения доезжали быстрее), скачивает и обрабатывает файл — без
    ручной загрузки каждый раз. Данные те же, что уже используются для Ozon
    (data/ozon_catalog.xlsx): название, описание, цена, фото. Команда НЕ
    требует AVITO_CLIENT_ID/AVITO_CLIENT_SECRET — это отдельный, более
    простой способ (см. avito_feed.py). Если запустить build-avito-catalog в
    рамках расписания (как sync-orders, см. .github/workflows/main.yml) —
    весь цикл обновления цен/остатков/фото на Avito становится таким же
    автоматическим, как у Ozon/WB, только опрашивает файл сам Avito, а не мы
    его отправляем.

    Нужен файл data/avito_template.xlsx — официальный шаблон Avito для вашей
    категории (скачивается в личном кабинете, раздел Автозагрузка ->
    Документация), в нём нельзя менять строки 1-4 и лист "Справочники".

    Адрес продавца берётся из AVITO_SELLER_ADDRESS (.env / GitHub Secrets) —
    без него Avito не примет объявления (обязательное поле).
    """
    import avito_feed

    catalog_path = _data_path("ozon_catalog.xlsx")
    template_path = _data_path("avito_template.xlsx")
    output_path = _data_path("avito_feed.xlsx")

    if not os.path.exists(catalog_path):
        print(f"Нет файла {catalog_path} — сначала выполните build-ozon-catalog.")
        return 1
    if not os.path.exists(template_path):
        print(
            f"Нет файла {template_path} — скачайте официальный шаблон Avito для вашей "
            "категории (личный кабинет -> Автозагрузка -> Документация) и положите его "
            "в data/avito_template.xlsx."
        )
        return 1

    seller_address = config.avito_seller_address
    if not seller_address:
        print(
            "Не задан AVITO_SELLER_ADDRESS (.env / GitHub Secrets) — это обязательное "
            "поле 'Адрес' в фиде Avito. Добавьте переменную и запустите снова."
        )
        return 1

    result = avito_feed.build_avito_feed(
        catalog_path=catalog_path,
        template_path=template_path,
        output_path=output_path,
        seller_address=seller_address,
    )

    print(f"Готово: {result['written']} товаров -> {output_path}")
    if result["skipped_no_price"]:
        print(f"Пропущено (нет цены): {', '.join(result['skipped_no_price'])}")
    if result.get("skipped_no_stock"):
        print(f"Пропущено (остаток 0 в master_control.xlsx — сняты с продажи): {', '.join(result['skipped_no_stock'])}")
    if result["unclassified"]:
        print(
            "\nВНИМАНИЕ: не удалось автоматически определить 'Тип детали трансмиссии' "
            f"для {len(result['unclassified'])} товаров — они НЕ включены в файл (это "
            "обязательное поле, пустым Avito бы всё равно отклонил). Если это ошибка "
            "классификации — допишите ключевое слово в avito_feed.py. Если товар не "
            "по теме этого шаблона (не запчасть трансмиссии) — для него нужен другой "
            "шаблон категории Avito:"
        )
        for offer_id, title in result["unclassified"]:
            print(f"  {offer_id}: {title}")

    try:
        import photo_host

        feed_url = photo_host.upload_file(output_path, filename="avito_feed.xlsx")
        # Ссылка со ставкой "?v=<хэш>" нужна только маркетплейсам, которые
        # агрессивно кэшируют картинки по URL (см. photo_host.py) — самому
        # Avito это не нужно, а ссылка для "Автозагрузки по ссылке" должна
        # оставаться БУКВАЛЬНО одной и той же строкой раз и навсегда (её
        # один раз вписывают в личном кабинете), поэтому отрезаем хвост.
        stable_url = feed_url.split("?v=")[0]
        print(f"\nФайл также залит по постоянной ссылке: {stable_url}")
        print(
            "Если ещё не настроили — вставьте эту ссылку ОДИН РАЗ в личном кабинете Avito: "
            "Автозагрузка -> Настройки -> «Автозагрузка по ссылке», отметьте побольше дней/часов "
            "опроса. Дальше Avito сам будет скачивать и обрабатывать файл — загружать вручную "
            "больше не нужно."
        )
    except Exception as exc:  # noqa: BLE001
        print(
            f"\nВНИМАНИЕ: не удалось залить {output_path} по постоянной ссылке ({exc}) — "
            f"скачайте файл из data/ и загрузите в личном кабинете Avito -> Автозагрузка -> "
            "Загрузить файл -> Вручную, как раньше."
        )

    return 0


def cmd_build_tilda_catalog() -> int:
    """
    Собрать data/tilda_feed.csv для импорта в магазин на Тильде (личный
    кабинет -> магазин -> Товары -> Импорт/Экспорт -> Импорт). В отличие от
    Avito, у Тильды нет ни "автозагрузки по ссылке", ни открытого API для
    товаров — файл придётся каждый раз скачивать и загружать руками (см.
    tilda_feed.py). Данные те же, что уже используются для Ozon и Avito
    (data/ozon_catalog.xlsx): название, описание, ЦЕНА (та же, что на Avito,
    БЕЗ наценки WB), фото (первая/обложка из общей папки photos/).

    Решение от 2026-09-15: собираем "с нуля" — при пересборке UID/External ID
    у каждого товара стабильные (не меняются), но каталог в Тильде перед
    первым импортом нужно вручную очистить от старых товаров, заведённых
    руками — иначе получите дубли.

    ФОТО берутся НЕ из ozon_catalog.xlsx (там ссылки на GitHub Release,
    Тильда их не смогла скачать), а напрямую с raw.githubusercontent.com —
    поэтому, как и attach-ozon-photos/attach-wb-photos, эта команда должна
    запускаться через GitHub Actions (нужен GITHUB_REPOSITORY).
    """
    import tilda_feed

    catalog_path = _data_path("ozon_catalog.xlsx")
    output_path = _data_path("tilda_feed.csv")
    photos_dir = os.path.join(os.path.dirname(__file__), "photos")

    if not os.path.exists(catalog_path):
        print(f"Нет файла {catalog_path} — сначала выполните build-ozon-catalog.")
        return 1
    if not os.path.isdir(photos_dir):
        print(f"Нет папки {photos_dir} — см. README, раздел про фото.")
        return 1

    repo = os.environ.get("GITHUB_REPOSITORY", "")
    branch = os.environ.get("GITHUB_REF_NAME", "main")
    if not repo:
        print("Не удалось определить репозиторий (GITHUB_REPOSITORY пуст) — эту команду нужно запускать через GitHub Actions.")
        return 1
    raw_base_url = f"https://raw.githubusercontent.com/{repo}/{branch}/photos"

    result = tilda_feed.build_tilda_catalog(
        catalog_path=catalog_path, output_path=output_path, photos_dir=photos_dir, raw_base_url=raw_base_url
    )

    print(f"Готово: {result['written']} товаров -> {output_path}")
    if result["skipped_no_price"]:
        print(f"Пропущено (нет цены): {', '.join(result['skipped_no_price'])}")
    if result["skipped_no_photo"]:
        print(f"ВНИМАНИЕ: без фото (карточка будет без картинки): {', '.join(result['skipped_no_photo'])}")

    try:
        import photo_host

        feed_url = photo_host.upload_file(output_path, filename="tilda_feed.csv")
        stable_url = feed_url.split("?v=")[0]
        print(f"\nФайл также залит по постоянной ссылке (можно скачать прямо оттуда): {stable_url}")
    except Exception as exc:  # noqa: BLE001
        print(f"\n(не удалось залить по постоянной ссылке: {exc} — скачайте файл из data/ как обычно)")

    print(
        "\nДальше: скачайте tilda_feed.csv из data/ (или по ссылке выше) и загрузите в личном "
        "кабинете Тильды: магазин -> Товары -> Импорт/Экспорт -> Импорт -> выбрать файл -> "
        "сопоставить колонки (они уже названы как у Тильды) -> Импортировать."
    )
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

    if "yandex_market" in summary or "yandex_market_error" in summary:
        print(f"  Яндекс Маркет: {summary.get('yandex_market', 'ошибка — см. ниже')}")
        if "yandex_market_error" in summary:
            print(f"    ОШИБКА Яндекс Маркет: {summary['yandex_market_error']}")

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

    kit_updates = summary.get("kit_updates") or {}
    if kit_updates:
        print(f"\nОстаток комплектов пересчитан ({len(kit_updates)} шт.):")
        for kit_id, new_qty in sorted(kit_updates.items()):
            print(f"  {kit_id}: можно собрать {new_qty} шт.")


def cmd_sync_tilda_order() -> int:
    """
    Списывает остаток по одному заказу с сайта (Тильда). Не предназначена
    для ручного запуска — вызывается автоматически из GitHub Actions по
    событию repository_dispatch (тип 'tilda-order'), которое присылает
    Google Apps Script при срабатывании вебхука Тильды. Данные заказа
    приходят в переменной окружения TILDA_ORDER_PAYLOAD (JSON), которую
    workflow заполняет из github.event.client_payload — см.
    .github/workflows/main.yml.
    """
    import stock_sync

    raw = os.environ.get("TILDA_ORDER_PAYLOAD", "")
    if not raw or raw.strip() in ("", "null", "{}"):
        print(
            "Нет данных заказа (TILDA_ORDER_PAYLOAD пуст) — эта команда "
            "предназначена для автозапуска по вебхуку с сайта, вручную её "
            "запускать не нужно."
        )
        return 1

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Не удалось разобрать TILDA_ORDER_PAYLOAD как JSON: {exc}")
        return 1

    items = payload.get("items") or []
    order_key = str(payload.get("order_key") or "").strip()
    if not order_key:
        import hashlib

        order_key = "tilda-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        print(
            f"ВНИМАНИЕ: в заказе нет order_key от Apps Script, использую "
            f"хэш всего payload как ключ ({order_key})."
        )

    if not items:
        print(
            "В заказе нет товарных позиций (items пуст) — проверьте лог "
            "выполнения Apps Script (Просмотр выполнения/Executions), там "
            "залогирован сырой payload вебхука, возможно формат отличается "
            "от ожидаемого и extractOrderItems нужно поправить."
        )
        return 1

    xlsx_path = _data_path("ozon_catalog.xlsx")
    try:
        summary = stock_sync.sync_tilda_order(order_key, items, catalog_path=xlsx_path)
    except FileNotFoundError as exc:
        print(str(exc))
        return 1

    print(f"Заказ с сайта [{order_key}]: обработано позиций {summary['items']}")
    if summary.get("skipped"):
        print(f"  Пропущено (нет артикула/количества): {summary['skipped']}")

    deltas = summary.get("deltas") or {}
    applied = summary.get("applied") or {}
    for offer_id, delta in sorted(deltas.items()):
        new_qty = applied.get(offer_id, "?")
        print(f"  {offer_id}: {delta} -> новый остаток: {new_qty}")

    unmatched = summary.get("unmatched") or []
    if unmatched:
        print(f"ВНИМАНИЕ: артикул(ы) из заказа не найдены в каталоге: {', '.join(unmatched)}")

    kit_updates = summary.get("kit_updates") or {}
    if kit_updates:
        print(f"Остаток комплектов пересчитан ({len(kit_updates)} шт.):")
        for kit_id, new_qty in sorted(kit_updates.items()):
            print(f"  {kit_id}: можно собрать {new_qty} шт.")
    return 0


def cmd_build_kits_template() -> int:
    """Создаёт пустой data/kits.xlsx для описания состава комплектов (см. kits.py)."""
    import kits

    path = _data_path("kits.xlsx")
    created = kits.build_kits_template(path)
    if created:
        print(
            f"Создан {path}. Заполните по одной строке на каждую деталь комплекта:\n"
            "  Артикул комплекта | Название (для справки) | Артикул детали | Кол-во детали в комплекте\n"
            "Например для 'ремкомплект штоков' = 2 пыльника + 2 сальника + 2 крышки штока — три строки "
            "с одним и тем же 'Артикул комплекта', но разными деталями и количеством 2 в каждой.\n"
            "После заполнения запустите --recompute-kit-stock, чтобы посчитать, сколько комплектов "
            "можно собрать из текущего остатка деталей."
        )
    else:
        print(f"{path} уже существует — не трогаю, чтобы не затереть уже заполненное.")
    return 0


def cmd_recompute_kit_stock() -> int:
    """
    Пересчитывает 'Кол-во к продаже' для строк-комплектов из data/kits.xlsx
    по текущему остатку деталей в ozon_catalog.xlsx. Запускайте вручную
    после того, как поменяли остаток деталей руками (например, только что
    заполнили склад) — дальше это делает сама каждая синхронизация заказов
    (sync-orders/sync-tilda-order).
    """
    import kits

    kits_path = _data_path("kits.xlsx")
    catalog_path = _data_path("ozon_catalog.xlsx")
    if not os.path.exists(kits_path):
        print(f"Нет файла {kits_path} — сначала выполните --build-kits-template и заполните состав комплектов.")
        return 1
    if not os.path.exists(catalog_path):
        print(f"Нет файла {catalog_path} — сначала выполните build-ozon-catalog.")
        return 1

    bom = kits.load_bom(kits_path)
    if not bom:
        print(f"{kits_path} пуст — заполните состав хотя бы одного комплекта и запустите снова.")
        return 1

    updates = kits.recompute_kit_stock(catalog_path, bom)
    if not updates:
        print("Изменений нет — остаток комплектов уже соответствует остатку деталей.")
        return 0

    print(f"Обновлено комплектов: {len(updates)}")
    for kit_id, new_qty in sorted(updates.items()):
        print(f"  {kit_id}: можно собрать {new_qty} шт.")
    return 0


class _SkipOzonOff(Exception):
    pass


def _load_stock_items(warehouse_id=None, skip_ozon_off=False):
    """
    Собирает offer_id -> остаток из ЕДИНОГО столбца "Кол-во к продаже" —
    для уже существующих товаров из data/ozon_catalog.xlsx, для черновиков
    (совсем новых товаров) из data/ozon_new_products.xlsx (это их остаток
    при первом появлении на Ozon). Обе колонки редактируются либо напрямую,
    либо через единый файл-пульт master_control.xlsx + sync-master-control
    (колонка "Остаток, шт.").

    Пустая ячейка = остаток никто не задавал — такой товар в список НЕ
    попадает (ничего не отправляется в Ozon), чтобы случайно не обнулить
    остаток нетронутого товара. Нечисловое значение — пропускается с
    предупреждением в лог.

    warehouse_id (если передан) кладётся в каждый элемент — Ozon требует
    его в /v2/products/stocks (см. ozon_client.update_stocks), без него
    отвечает 400. Для dry-run можно не передавать — просто не попадёт в
    вывод.
    """
    import catalog_editor
    import ozon_new_products

    items = []

    catalog_path = _data_path("ozon_catalog.xlsx")
    if os.path.exists(catalog_path):
        edits = catalog_editor.load_catalog_edits(catalog_path)
        for offer_id, e in edits.items():
            raw = e.get("quantity_to_sell")
            if raw in (None, ""):
                continue
            try:
                stock = int(raw)
            except (TypeError, ValueError):
                print(f"ВНИМАНИЕ: {offer_id} — 'Кол-во к продаже' не число ({raw!r}), пропущен.")
                continue
            item = {"offer_id": offer_id, "stock": max(0, stock)}
            if warehouse_id:
                item["warehouse_id"] = warehouse_id
            items.append(item)

    new_path = _data_path("ozon_new_products.xlsx")
    if os.path.exists(new_path):
        new_edits = ozon_new_products.load_new_edits(new_path)
        already = {it["offer_id"] for it in items}
        for offer_id, e in new_edits.items():
            if offer_id in already:
                continue  # артикул уже есть в основном каталоге — не отправляем остаток дважды (Ozon: TOO_MANY_REQUESTS)
            raw = e.get("quantity_to_sell")
            if raw in (None, ""):
                continue
            try:
                stock = int(raw)
            except (TypeError, ValueError):
                print(f"ВНИМАНИЕ: {offer_id} — 'Кол-во к продаже' не число ({raw!r}), пропущен.")
                continue
            item = {"offer_id": offer_id, "stock": max(0, stock)}
            if warehouse_id:
                item["warehouse_id"] = warehouse_id
            items.append(item)

    # Товары, у которых в мастере «Ozon (да/нет)» = «нет», на Ozon не отправляем
    # (иначе каждый запуск пишет «карточка не создана» про то, что мы и не продаём).
    try:
        if not skip_ozon_off:
            raise _SkipOzonOff()
        import audit

        _m = audit.load_master()
        _fl = audit.channel_flags(_m)
        _off = {audit.key(a) for a, f in _fl.items() if f.get("Ozon") == audit.NO}
        _before = len(items)
        items = [it for it in items if audit.key(str(it.get("offer_id", ""))) not in _off]
        if len(items) != _before:
            print(f"Пропущено по мастеру «Ozon: нет»: {_before - len(items)}")
    except _SkipOzonOff:
        pass  # WB/Маркет/Авито/сайт берут остатки без этого фильтра — он только для Ozon
    except Exception as exc:  # noqa: BLE001
        print(f"(фильтр «Ozon: нет» не применён: {exc})")

    return items


def _ozon_warehouse_id_int():
    from config import config

    raw = (config.ozon_warehouse_id or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        print(f"ВНИМАНИЕ: OZON_WAREHOUSE_ID={raw!r} — не похоже на число, игнорирую.")
        return None


def cmd_push_stock_dryrun() -> int:
    from config import config

    items = _load_stock_items(warehouse_id=_ozon_warehouse_id_int(), skip_ozon_off=True)
    print(f"ПРОБНЫЙ ПРОГОН — в Ozon ничего не отправляется. Остатков к обновлению: {len(items)}\n")
    print(json.dumps(items, ensure_ascii=False, indent=2))
    if not items:
        print(
            "\n(Пусто — заполните столбец 'Остаток, шт.' в master_control.xlsx и запустите "
            "sync-master-control, либо впишите число прямо в 'Кол-во к продаже' в "
            "ozon_catalog.xlsx/ozon_new_products.xlsx.)"
        )
    if items and not config.ozon_warehouse_id:
        print(
            "\nВНИМАНИЕ: OZON_WAREHOUSE_ID не задан — реальный push-stock завершится ошибкой "
            "(Ozon требует warehouse_id в каждой строке). Запустите --ozon-warehouses и впишите "
            "id нужного склада в секрет OZON_WAREHOUSE_ID."
        )
    return 0


def cmd_push_stock() -> int:
    """
    Реально отправляет остатки в Ozon (POST /v2/products/stocks — быстрый
    метод, отдельный от push-ozon-cards). Для НОВЫХ товаров запускайте
    ПОСЛЕ push-ozon-new-cards и fetch-ozon (пока товара нет в Ozon,
    обновлять остаток нечему — Ozon ответит ошибкой по этому offer_id).
    """
    import ozon_client

    warehouse_id = _ozon_warehouse_id_int()
    if not warehouse_id:
        print(
            "OZON_WAREHOUSE_ID не задан (или не число) — Ozon требует warehouse_id в каждой строке "
            "/v2/products/stocks, без него 400. Запустите --ozon-warehouses, чтобы увидеть список "
            "складов, и впишите нужный id в секрет OZON_WAREHOUSE_ID."
        )
        return 1

    items = _load_stock_items(warehouse_id=warehouse_id, skip_ozon_off=True)
    if not items:
        print("Нечего отправлять — ни у одного товара не заполнен остаток ('Кол-во к продаже' / 'Остаток, шт.').")
        return 1

    total_ok = 0
    total_err = 0
    failed_offer_ids = []
    not_created = []
    chunk = 100
    for i in range(0, len(items), chunk):
        batch = items[i : i + chunk]
        batch_num = i // chunk + 1
        result = ozon_client.update_stocks(batch)
        result_items = result.get("result", [])
        if not result_items:
            print(f"Партия {batch_num}: Ozon не вернул result, ответ: {result}")
            total_err += len(batch)
            failed_offer_ids.extend(str(it.get("offer_id", "")) for it in batch)
            continue
        for it in result_items:
            offer_id = it.get("offer_id", "?")
            if it.get("updated"):
                total_ok += 1
                print(f"  ОК {offer_id}: остаток обновлён")
            else:
                codes = {str(e.get("code")) for e in (it.get("errors") or []) if isinstance(e, dict)}
                if codes and codes <= {"PRODUCT_IS_NOT_CREATED"}:
                    # Карточка ещё не создана на Ozon (см. --ozon-status): это не сбой
                    # синхронизации, остальные товары обновлены — не валим весь запуск.
                    not_created.append(offer_id)
                    continue
                total_err += 1
                failed_offer_ids.append(offer_id)
                print(f"  ОШИБКА {offer_id}: {it.get('errors')}")
    print(f"\nИтого: успешно {total_ok}, с ошибками {total_err}, карточка не создана на Ozon {len(not_created)}")
    if not_created:
        print(
            "Карточки не созданы на Ozon (остаток не отправлен): "
            + ", ".join(not_created)
        )
        # Причины сразу в этом же логе (раньше для этого нужна была отдельная команда ozon-status).
        try:
            print("\nПричины (статус карточек на Ozon):")
            os.environ["OZON_STATUS_ARTICLES"] = ",".join(not_created)
            cmd_ozon_status()
        except Exception as exc:  # noqa: BLE001
            print(f"(статусы карточек не получены: {exc})")
        finally:
            os.environ.pop("OZON_STATUS_ARTICLES", None)

    # Диагностика: PRODUCT_IS_NOT_CREATED от /v2/products/stocks не значит
    # обязательно "карточки нет вообще" — товар может существовать (виден в
    # list_products/push-ozon-new-cards), но ещё не пройти внутреннюю
    # обработку/модерацию Ozon, из-за чего остаток пока недоступен для
    # записи. Запрашиваем по упавшим offer_id реальный статус карточки
    # батчем, чтобы увидеть настоящую причину, а не гадать.
    failed_offer_ids = [oid for oid in failed_offer_ids if oid and oid != "?"]
    if failed_offer_ids:
        print("\nПодробности по товарам с ошибкой (статус карточки в Ozon):")
        try:
            infos = ozon_client.get_product_info_list(failed_offer_ids)
        except Exception as exc:
            print(f"  не удалось получить подробности — {exc}")
            infos = []
        for info in infos:
            print(f"  {json.dumps(info, ensure_ascii=False)}")

    return 0 if total_err == 0 else 1


def _wb_stock_rows():
    """
    offer_id -> (vendor_code, штрихкод, остаток) для отправки в WB — из
    ТОГО ЖЕ единого столбца "Кол-во к продаже"/"Остаток, шт.", что и
    push-stock для Ozon (см. _load_stock_items): склад общий на обе
    площадки, sync-orders списывает продажи с Ozon И с WB в одно и то же
    число, а push-stock/push-wb-stock просто рассылают текущее значение
    обратно каждой площадке отдельно (у Ozon и WB разные API для остатков).

    Наш артикул почти всегда совпадает с vendorCode на WB; для редких
    вручную подтверждённых исключений (см. WB_ALIAS в build_master_control.py)
    используется обратная карта _WB_ALIAS_OZON_TO_VENDOR из
    sync_master_control.py. Штрихкод (WB требует именно его, не vendorCode,
    см. wb_client.update_stocks) берётся через get_offer_barcode_map().
    Товары без карточки/штрихкода на WB возвращаются отдельным списком
    unmatched, чтобы не отправлять на них случайный мусор.
    """
    import wb_client
    import sync_master_control

    stock_items = {it["offer_id"]: it["stock"] for it in _load_stock_items()}
    if not stock_items:
        return [], []

    # Карточки WB только что скачаны шагом 0а update-cards — берём их из файла, а не качаем в третий раз.
    cards = None
    try:
        wb_path = _data_path("wb_cards.json")
        if os.path.exists(wb_path) and time.time() - os.path.getmtime(wb_path) < 20 * 60:
            with open(wb_path, "r", encoding="utf-8") as f_:
                cards = json.load(f_).get("cards") or None
    except (OSError, ValueError):
        cards = None
    barcode_map = wb_client.get_offer_barcode_map(cards)
    alias = {**sync_master_control._WB_ALIAS_OZON_TO_VENDOR, **sync_master_control._WB_FORCED_OZON_TO_VENDOR}

    rows = []
    unmatched = []
    for offer_id, qty in sorted(stock_items.items()):
        vendor_code = alias.get(offer_id, offer_id)
        if vendor_code != offer_id and offer_id in barcode_map and offer_id not in sync_master_control._WB_FORCED_OZON_TO_VENDOR:
            vendor_code = offer_id  # такой артикул сам по себе настоящая карточка WB — остаток идёт ей, а не «двойнику»
        barcode = barcode_map.get(vendor_code)
        if not barcode:
            unmatched.append(offer_id)
            continue
        rows.append({"offer_id": offer_id, "vendor_code": vendor_code, "sku": barcode, "amount": max(0, int(qty))})
    # если два артикула дали одну карточку WB — оставляем тот, чей offer_id совпадает с её vendorCode
    best = {}
    for r in rows:
        cur = best.get(r["vendor_code"])
        if cur is None or (r["offer_id"] == r["vendor_code"] and cur["offer_id"] != cur["vendor_code"]):
            best[r["vendor_code"]] = r
    rows = list(best.values())
    # снятые с продажи карточки WB: остаток всегда 0
    for vc in sync_master_control.WB_RETIRED_VENDOR_CODES:
        barcode = barcode_map.get(vc)
        if barcode:
            rows = [r for r in rows if r["vendor_code"] != vc]
            rows.append({"offer_id": f"{vc} (снята)", "vendor_code": vc, "sku": barcode, "amount": 0})
    return rows, unmatched


def cmd_push_wb_stock_dryrun() -> int:
    from config import config

    rows, unmatched = _wb_stock_rows()
    print(f"ПРОБНЫЙ ПРОГОН — в WB ничего не отправляется. Остатков к обновлению: {len(rows)}\n")
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    if not rows and not unmatched:
        print(
            "\n(Пусто — заполните столбец 'Остаток, шт.' в master_control.xlsx и запустите "
            "sync-master-control, либо впишите число прямо в 'Кол-во к продаже' в "
            "ozon_catalog.xlsx/ozon_new_products.xlsx.)"
        )
    if unmatched:
        print(
            f"\nВНИМАНИЕ: {len(unmatched)} артикул(ов) не нашлись на WB (нет карточки или штрихкода) — "
            f"пропущены: {', '.join(unmatched)}"
        )
    if rows and not config.wb_warehouse_id:
        print(
            "\nВНИМАНИЕ: WB_WAREHOUSE_ID не задан — реальный push-wb-stock завершится ошибкой. "
            "Запустите --wb-warehouses и впишите id нужного склада в секрет WB_WAREHOUSE_ID."
        )
    return 0


def cmd_launch_new_products() -> int:
    """
    Запустить совсем новые товары из data/new_products.xlsx ОДНОЙ командой
    сразу на все площадки, где идёт продажа: Ozon, WB, Avito и сайт (Тильда).

    Что реально происходит по шагам (раньше это были 2 отдельные ручные
    команды с ожиданием между ними — push-new-product-all и finish-new-product,
    см. их докстринги):
      1. push-new-product-all — создаёт карточку на Ozon (сразу с фото) и
         отправляет на создание карточку на WB (без фото, это нормально —
         у WB создание асинхронное, фото доедут позже).
      2. Пауза ~3 минуты — даём WB время обработать создание карточки
         (нужен nmID, который WB не отдаёт сразу).
      3. finish-new-product — подтягивает оба товара в общий каталог
         (data/ozon_catalog.xlsx), доливает фото на карточку WB, пересобирает
         фид Avito (он подхватывается автоматически по ссылке — с вашей
         стороны действий не требуется) и фид сайта (data/tilda_feed.csv).

    ЧЕСТНО О ГРАНИЦАХ: Avito подхватывает изменения САМ (автозагрузка по
    постоянной ссылке — см. build-avito-catalog), но у Тильды нет API для
    автоматического импорта фида — CSV всё равно нужно вручную загрузить в
    личном кабинете Тильды (см. README/build-tilda-catalog), просто он уже
    будет свежим сразу после этой команды.
    """
    print("=== Шаг 1/3: push-new-product-all ===")
    rc = cmd_push_new_product_all()
    if rc != 0:
        print(
            "\npush-new-product-all вернул ошибку (или отправлять было нечего) — "
            "всё равно пробую доехать до finish-new-product на случай частичного успеха."
        )

    print("\n=== Шаг 2/3: жду ~3 минуты, пока WB асинхронно обработает создание карточек ===")
    time.sleep(180)

    print("\n=== Шаг 3/3: finish-new-product ===")
    rc_finish = cmd_finish_new_product()

    print(
        "\nГотово. Ozon/WB/Avito обновлены автоматически. Для сайта (Тильда) "
        "скачайте свежий data/tilda_feed.csv и импортируйте его в личном кабинете "
        "Тильды вручную — API для этого шага у Тильды нет."
    )
    return rc_finish


def cmd_fix_wb_dimensions() -> int:
    """
    Разовая правка битых габаритов/веса у двух карточек WB, из-за которых
    WB отклонял ВЕСЬ пакет push-wb-stock (409 CargoWarehouseRestrictionSGT*,
    склад не принимает "негабарит"): 0am325477 (250 кг вместо ~0.25 кг) и
    0am325025HX (70x80x60 см вместо ~30x25x25 см). Значения подтверждены
    пользователем 2026-09-24. Можно запускать повторно — идемпотентно.
    """
    import wb_client

    fixes = {
        "0am325477": {"width": 10, "height": 5, "length": 10, "weightBrutto": 0.25},
        "0am325025HX": {"width": 30, "height": 25, "length": 25, "weightBrutto": 9},
    }

    cards = wb_client.list_cards()
    payload = []
    for c in cards:
        vc = c.get("vendorCode")
        if vc in fixes:
            entry = {
                "nmID": c["nmID"],
                "vendorCode": c["vendorCode"],
                "brand": c.get("brand", ""),
                "title": c.get("title"),
                "description": c.get("description"),
                "dimensions": dict(fixes[vc], isValid=True),
                "sizes": c.get("sizes", []),
            }
            if "characteristics" in c:
                entry["characteristics"] = c["characteristics"]
            payload.append(entry)

    if not payload:
        print("Не нашёл ни одной из целевых карточек (0am325477, 0am325025HX) в list_cards().")
        return 1

    for p in payload:
        print(f"  {p['vendorCode']}: новые габариты -> {p['dimensions']}")

    result = wb_client.update_cards(payload)
    print("Ответ WB:", result)
    print("\nГотово. Проверьте через пару минут push-wb-stock-dryrun/push-wb-stock — пакет больше не должен падать целиком.")
    return 0


def cmd_push_wb_stock() -> int:
    """
    Реально отправляет остатки в WB (PUT /api/v3/stocks/{warehouseId}) —
    из того же единого столбца "Кол-во к продаже"/"Остаток, шт.", что и
    push-stock у Ozon, так что после sync-orders достаточно запустить ОБЕ
    команды (push-stock и push-wb-stock), чтобы актуальный остаток ушёл на
    обе площадки сразу.
    """
    import wb_client
    from config import config

    warehouse_id = (config.wb_warehouse_id or "").strip()
    if not warehouse_id:
        print(
            "WB_WAREHOUSE_ID не задан — запустите --wb-warehouses, чтобы увидеть список складов, "
            "и впишите нужный id в секрет WB_WAREHOUSE_ID."
        )
        return 1

    rows, unmatched = _wb_stock_rows()
    if unmatched:
        print(f"ВНИМАНИЕ: {len(unmatched)} артикул(ов) без карточки/штрихкода на WB, пропущены: {', '.join(unmatched)}")
    if not rows:
        print("Нечего отправлять — ни один артикул с заполненным остатком не нашёлся на WB.")
        return 1

    total_ok = 0
    total_err = 0
    chunk = 1000  # у WB лимит на партию заметно больше, чем у Ozon (см. update_stocks)
    for i in range(0, len(rows), chunk):
        batch = rows[i : i + chunk]
        items = [{"sku": r["sku"], "amount": r["amount"]} for r in batch]
        try:
            wb_client.update_stocks(warehouse_id, items)
            total_ok += len(batch)
            print(f"  ОК: партия из {len(batch)} остатков отправлена ({', '.join(r['offer_id'] for r in batch)})")
        except Exception as exc:
            total_err += len(batch)
            print(f"  ОШИБКА при отправке партии из {len(batch)}: {exc}")

    print(f"\nИтого: успешно {total_ok}, с ошибками {total_err}")
    return 0 if total_err == 0 else 1


def cmd_ozon_status() -> int:
    """
    Статус ВСЕХ товаров каталога на Ozon одной командой (или только --article "а,б,в"):
    сколько товаров в каждом статусе и подробности по тем, у кого что-то не так
    (карточка не создана, не прошла модерацию, скрыта, есть ошибки). Только чтение.
    Помогает понять, почему push-stock отвечает PRODUCT_IS_NOT_CREATED.
    """
    import openpyxl

    import ozon_client

    wanted = _parse_offer_ids_arg(os.environ.get("OZON_STATUS_ARTICLES", "")) or []
    if wanted:
        offer_ids = wanted
    else:
        catalog_path = _data_path("ozon_catalog.xlsx")
        if not os.path.exists(catalog_path):
            print(f"Нет файла {catalog_path}.")
            return 1
        ws = openpyxl.load_workbook(catalog_path, read_only=True, data_only=True).active
        offer_ids = [str(r[0]).strip() for r in list(ws.iter_rows(values_only=True))[1:] if r and r[0]]

    infos = ozon_client.get_product_info_list(offer_ids)
    counts: dict = {}
    problems = []
    for info in infos:
        if not isinstance(info, dict):
            continue
        if info.get("_error"):
            print(f"Ошибка запроса к Ozon: {info['_error']}")
            continue
        st = info.get("statuses") or {}
        status_name = st.get("status_name") or st.get("status") or "?"
        counts[status_name] = counts.get(status_name, 0) + 1
        errs = info.get("errors") or []
        def _err_line(e):
            txt = str(e.get("description") or e.get("message") or "")[:150]
            # Ozon часто присылает пустое описание, но указывает атрибут/поле — выводим всё, что есть
            extra = {k: v for k, v in e.items() if k not in ("code", "description", "message") and v not in (None, "", [], {})}
            if extra:
                txt = (txt + " " if txt else "") + json.dumps(extra, ensure_ascii=False)[:250]
            return f"{e.get('code', '?')}: {txt}"

        err_txt = "; ".join(_err_line(e) for e in errs if isinstance(e, dict))
        is_created = st.get("is_created")
        vis = info.get("visibility_details") or {}
        hidden = [k for k, v in vis.items() if v is False and k.startswith("has_")]
        bad = (is_created is False) or err_txt or str(st.get("status", "")).lower() in ("failed", "failed_moderation", "failed_validation") or (
            "ошиб" in str(status_name).lower() or "не прош" in str(status_name).lower() or "не созд" in str(status_name).lower()
        )
        not_selling = str(status_name).strip().lower() not in ("продается", "продаётся", "?")
        if bad or not_selling or wanted:
            extra = []
            for key in ("status_tooltip", "status_failed", "moderate_status", "validation_state", "state_name", "state_description"):
                if st.get(key):
                    extra.append(f"{key}={st.get(key)}")
            if vis:
                extra.append("visibility=" + ",".join(f"{k}:{v}" for k, v in vis.items()))
            problems.append(
                f"  {info.get('offer_id', '?')}: статус «{status_name}»"
                + (f", {st.get('status_description')}" if st.get("status_description") else "")
                + (f", {'; '.join(extra)}" if extra else "")
                + (f", создан: {is_created}" if is_created is not None else "")
                + (f", ошибки: {err_txt}" if err_txt else "")
            )
    print("Товаров по статусам:")
    for name, n in sorted(counts.items(), key=lambda x: -x[1]):
        print(f"  {name}: {n}")
    if problems:
        print(f"\nТовары с проблемами ({len(problems)}):")
        print("\n".join(problems))
    else:
        print("\nПроблемных товаров не найдено.")
    return 0


def cmd_fix_spu(article: str, dry_run: bool) -> int:
    """
    Лечит конфликт SPU_ALREADY_EXISTS_IN_ANOTHER_ACCOUNT у НЕ СОЗДАННЫХ карточек Ozon:
    делает "Артикул производителя" (7236), "Артикул" (9024) и "Название модели" (9048)
    уникальными (по offer_id), чтобы Ozon не считал карточку копией соседней.
    Меняются ТОЛЬКО эти три характеристики, остальное в карточке не трогается.
    ЗАЩИТА: карточки, которые уже созданы/продаются, команда НЕ ТРОГАЕТ — пропускает с пометкой.
    --dry-run (fix-spu-dryrun) только показывает, что изменится.
    Артикулы — через --article (через запятую, не больше 10 за раз).
    """
    import ozon_client

    wanted = _parse_offer_ids_arg(article) or []
    if not wanted:
        print("Укажите артикулы через --article, например: --fix-spu-dryrun --article plita-filtr-orig")
        return 1
    if len(wanted) > 10:
        print(f"Слишком много артикулов ({len(wanted)}), максимум 10 за раз.")
        return 1

    infos = {str(i.get("offer_id")): i for i in ozon_client.get_product_info_list(wanted) if isinstance(i, dict) and not i.get("_error")}
    local = {}
    try:
        data = json.load(open(_data_path("ozon_products.json"), encoding="utf-8"))
        stack = [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "offer_id" in o and "attributes" in o:
                    local[str(o["offer_id"])] = o
                else:
                    stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    except Exception as exc:
        print(f"Не удалось прочитать data/ozon_products.json: {exc}")
        return 1

    payload = []
    for oid in wanted:
        info = infos.get(oid)
        if not info:
            print(f"  {oid}: Ozon не вернул карточку — пропускаю.")
            continue
        st = info.get("statuses") or {}
        if st.get("is_created") is not False:
            print(f"  {oid}: карточка уже СОЗДАНА (статус «{st.get('status_name')}») — не трогаю (команда лечит только не созданные).")
            continue
        card = local.get(oid)
        if not card:
            print(f"  {oid}: нет в data/ozon_products.json (сначала fetch-ozon) — пропускаю.")
            continue
        cur = {a["id"]: "; ".join(str(v.get("value")) for v in a.get("values", [])) for a in card.get("attributes", []) if isinstance(a, dict) and "id" in a}
        model_old = cur.get(9048, "")
        model_new = model_old if oid.lower() in model_old.lower() else (f"{model_old}_{oid}" if model_old else oid)
        new_vals = {7236: oid, 9024: oid, 9048: model_new}
        print(f"  {oid}:")
        for aid, nv in new_vals.items():
            print(f"      {aid}: «{cur.get(aid, '')}» -> «{nv}»")
        payload.append({
            "offer_id": oid,
            "attributes": [{"id": aid, "complex_id": 0, "values": [{"value": nv}]} for aid, nv in new_vals.items()],
        })

    if not payload:
        print("Нечего менять.")
        return 0
    if dry_run:
        print(f"\nПробный прогон: ничего не отправлено. Карточек к исправлению: {len(payload)}. Для отправки запустите fix-spu.")
        return 0
    res = ozon_client.update_attributes(payload)
    print(f"\nОтправлено на Ozon, ответ: {res}")
    print("Подождите несколько минут и запустите ozon-status с этим артикулом (--article), чтобы увидеть результат.")
    return 0


_OZON_LAST_CREATED: list = []


def cmd_ozon_create_missing(dry_run: bool, article: str = "", auto: bool = False) -> int:
    """
    Создать на Ozon товары, которых там нет, но которые должны быть (по ревизии) — всё из master_control.xlsx
    (колонка «Ozon (да/нет)», пусто = да; образец подбирается по названию). В автоматическом режиме (update-cards):
    не больше WB_AUTO_CREATE_MAX за запуск, дважды отклонённые Ozon-ом не повторяются (data/ozon_create_state.json).
    Фото не отправляются: следующий update-cards добавляет их обычным путём (после обновления каталога Ozon).
    """
    import ozon_client
    import ozon_new_products

    data_dir = os.path.dirname(_data_path("ozon_products.json"))
    plan = ozon_new_products.plan_missing(data_dir)
    edits = plan["edits"]
    if article.strip():
        wanted = {a.strip() for a in article.split(",") if a.strip()}
        unknown = sorted(wanted - set(edits))
        if unknown:
            print("Не в плане (нет в списке недостающих или пропущены): " + ", ".join(unknown))
        edits = {k: v for k, v in edits.items() if k in wanted}
    if auto:
        edits = _wb_auto_filter(edits, "ozon")
    print(f"К созданию на Ozon: {len(edits)} товаров.")
    for a, v in edits.items():
        smp, score = plan["info"][a]
        flag = "" if score >= 0.5 else "   <-- образец подобран неуверенно"
        print(f"  {a}: «{v['name']}» | образец {smp} (сходство {score}) | цена {v['price']}{flag}")
    if plan["skipped"] and not article.strip():
        print(f"\nПропущено ({len(plan['skipped'])}):")
        for a, why in plan["skipped"]:
            print(f"  - {a}: {why}")
    if not edits:
        return 0

    with open(_data_path("ozon_products.json"), "r", encoding="utf-8") as f:
        data = json.load(f)
    items = ozon_new_products.build_new_import_items(data, edits)
    if dry_run:
        print(f"\nПРОБНЫЙ ПРОГОН — в Ozon ничего не отправлено. Готово к созданию: {len(items)}.")
        if items:
            print(json.dumps(items[0], ensure_ascii=False, indent=2)[:2500])
        return 0
    if not items:
        print("Нечего отправлять: ни у одного товара не нашёлся образец в ozon_products.json.")
        return 1

    sent = [i["offer_id"] for i in items if i.get("offer_id")]
    problems: dict = {}
    created: list = []
    try:
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
                problems[it.get("offer_id")] = [json.dumps(blocking, ensure_ascii=False)]
        existing = {p.get("offer_id") for p in ozon_client.list_products() if p.get("offer_id")}
        created = [a for a in sent if a in existing]
        print(f"\nРЕЗУЛЬТАТ: создано на Ozon {len(created)} из {len(sent)}.")
        for a in sent:
            if a in created:
                print(f"  ОК: {a}")
            elif a in problems:
                print(f"  ОТКЛОНЕНО: {a} — {problems[a][0][:400]}")
            else:
                print(f"  ПОКА НЕТ: {a} (ошибок нет — возможно, ещё в обработке или Ozon счёл дублем; проверит следующий запуск)")
    except Exception as exc:  # noqa: BLE001
        print(f"ОШИБКА при создании на Ozon: {exc}")
        return 1
    finally:
        if auto and sent:
            _wb_auto_update_state(sent, created, problems, "ozon")
        global _OZON_LAST_CREATED
        _OZON_LAST_CREATED = list(created)
    print("Дальше: следующий update-cards добавит фото, цену и остаток новым карточкам.")
    return 0


_WB_LAST_CREATED: list = []  # что создал последний вызов cmd_wb_create_missing (для update-cards)
WB_AUTO_CREATE_MAX = 5  # не больше стольких новых карточек WB за один запуск update-cards
WB_AUTO_RETRY_AFTER_H = 6  # отправленную, но ещё не появившуюся карточку повторно не шлём столько часов
WB_AUTO_MAX_REJECTS = 2  # после стольких отказов WB карточку автоматически больше не пробуем


def _wb_auto_state_path(ch: str = "wb") -> str:
    return _data_path(f"{ch}_create_state.json")


def _wb_auto_load_state(ch: str = "wb") -> dict:
    try:
        with open(_wb_auto_state_path(ch), "r", encoding="utf-8") as f_:
            st = json.load(f_)
        return st if isinstance(st, dict) else {}
    except (OSError, ValueError):
        return {}


def _wb_auto_filter(edits: dict, ch: str = "wb") -> dict:
    """Автосоздание: пропускает то, что уже отправлено недавно или дважды отклонено, и ограничивает число."""
    st = _wb_auto_load_state(ch)
    now = time.time()
    keep, skipped = {}, []
    for a, v in edits.items():
        e = st.get(a) or {}
        if int(e.get("rejected", 0)) >= WB_AUTO_MAX_REJECTS:
            skipped.append(f"{a}: WB отклонил {e.get('rejected')} раз(а) — {str(e.get('reason', ''))[:200]} (авто-создание отключено; вручную: {ch}-create-missing --article {a}; сбросить — удалить запись из data/{ch}_create_state.json)")
            continue
        if e.get("sent") and now - float(e["sent"]) < WB_AUTO_RETRY_AFTER_H * 3600:
            skipped.append(f"{a}: отправлен недавно, WB ещё обрабатывает — повторно не шлю")
            continue
        keep[a] = v
    for line in skipped:
        print("  пропуск: " + line)
    if len(keep) > WB_AUTO_CREATE_MAX:
        rest = sorted(keep)[WB_AUTO_CREATE_MAX:]
        keep = {a: keep[a] for a in sorted(keep)[:WB_AUTO_CREATE_MAX]}
        print(f"  лимит {WB_AUTO_CREATE_MAX} за запуск: остальные ({len(rest)}) создадутся при следующих запусках: {', '.join(rest[:10])}{'...' if len(rest) > 10 else ''}")
    return keep


def _wb_auto_update_state(sent: list, created: list, problems: dict, ch: str = "wb") -> None:
    st = _wb_auto_load_state(ch)
    now = time.time()
    for a in sent:
        e = st.get(a) or {}
        if a in created:
            st.pop(a, None)
            continue
        e["sent"] = now
        if a in problems:
            e["rejected"] = int(e.get("rejected", 0)) + 1
            e["reason"] = problems[a][0][:300]
        st[a] = e
    try:
        with open(_wb_auto_state_path(ch), "w", encoding="utf-8") as f_:
            json.dump(st, f_, ensure_ascii=False, indent=1)
    except OSError as exc:
        print(f"(не удалось сохранить data/{ch}_create_state.json: {exc})")


def cmd_wb_create_missing(dry_run: bool, article: str = "", auto: bool = False) -> int:
    """
    Создать на WB товары, которых там нет, но которые должны быть (по ревизии) — БЕЗ отдельной таблицы:
    всё берётся из master_control.xlsx (колонка «WB (да/нет)», при желании «Образец для WB» и габариты)
    и из карточек Ozon. --article (через запятую) ограничивает список — для пробного запуска на 1-3
    товарах; пусто = все подходящие. Фото на этом шаге не отправляются (так работает API WB) — они
    добавляются обычным update-cards после того, как карточки появятся (подождите 2-5 минут, fetch-wb).
    """
    import wb_client
    import wb_new_products

    data_dir = os.path.dirname(_data_path("wb_cards.json"))
    wb_path = _data_path("wb_cards.json")
    if not os.path.exists(wb_path):
        print("Нет data/wb_cards.json — сначала fetch-wb.")
        return 1
    age_h = (time.time() - os.path.getmtime(wb_path)) / 3600.0
    if age_h > 12 and not dry_run:
        print(f"data/wb_cards.json устарел ({age_h:.0f} ч): сначала запустите audit (он обновит WB), иначе можно попытаться создать то, что уже есть.")
        return 1
    plan = wb_new_products.plan_missing(data_dir)
    edits = plan["edits"]
    if article.strip():
        wanted = {a.strip() for a in article.split(",") if a.strip()}
        unknown = sorted(wanted - set(edits))
        if unknown:
            print("Не в плане (нет в списке недостающих или пропущены): " + ", ".join(unknown))
        edits = {k: v for k, v in edits.items() if k in wanted}
    if auto:
        edits = _wb_auto_filter(edits)
    print(f"К созданию на WB: {len(edits)} товаров.")
    for a, v in edits.items():
        smp, subj, score = plan["info"][a]
        flag = "" if score >= 0.5 else "   <-- образец подобран неуверенно, проверьте раздел"
        print(f"  {a}: «{v['title']}» | образец {smp} (раздел «{subj}», сходство {score}) | цена {v['price']} | {v['weight_kg']} кг {v['length_cm']}x{v['width_cm']}x{v['height_cm']} см{flag}")
    if plan["skipped"] and not article.strip():
        print(f"\nПропущено ({len(plan['skipped'])}):")
        for a, why in plan["skipped"]:
            print(f"  - {a}: {why}")
    if not edits:
        return 0

    with open(wb_path, "r", encoding="utf-8") as f:
        wb_data = json.load(f)
    barcodes = wb_client.generate_barcodes(len(edits)) if not dry_run else ["0000000000000"] * len(edits)
    groups = wb_new_products.build_new_card_groups(wb_data, edits, barcodes)
    if dry_run:
        print(f"\nПРОБНЫЙ ПРОГОН — в WB ничего не отправлено. Карточек готово к созданию: {len(groups)}.")
        print("Подробное содержимое одной карточки (для проверки):")
        if groups:
            print(json.dumps(groups[0], ensure_ascii=False, indent=2)[:3000])
        return 0

    ok = err = 0
    for i, g in enumerate(groups):
        if i:
            time.sleep(7)  # лимит WB на создание карточек: примерно 1 запрос в 6 секунд
        try:
            wb_client.create_cards(g["subject_id"], [g["variant"]])
            print(f"ОК: {g['vendor_code']} отправлен на создание (раздел {g['subject_id']}).")
            ok += 1
        except wb_client.WbApiError as exc:
            print(f"ОШИБКА при создании {g['vendor_code']}: {exc}")
            if err == 0:
                print("Что отправляли (для диагностики): " + json.dumps(g, ensure_ascii=False)[:1800])
            err += 1
    print(f"\nИтого: отправлено {ok}, с ошибками {err}.")
    sent = [g["vendor_code"] for g in groups]
    created, problems = [], {}
    if ok:
        # Создание асинхронное: сами ждём и проверяем итог, чтобы не запускать отдельную диагностику.
        print("Проверяю результат каждые 30 с (до 5 минут), пока WB обрабатывает карточки...")
        present, problems = set(), {}
        try:
            for _ in range(10):
                time.sleep(30)
                present = {c.get("vendorCode") for c in wb_client.list_cards()}
                problems = {}
                for e in wb_client.get_upload_errors():
                    for vc in e.get("vendorCodes") or []:
                        if vc in sent and vc not in present:
                            problems.setdefault(vc, []).append(str(e.get("errors")))
                if all(v in present or v in problems for v in sent):
                    break
            created = [v for v in sent if v in present]
            print(f"\nРЕЗУЛЬТАТ: создано на WB {len(created)} из {len(sent)}.")
            for v in sent:
                if v in present:
                    print(f"  ОК: {v}")
                elif v in problems:
                    print(f"  ОТКЛОНЕНО: {v} — {problems[v][0][:400]}")
                else:
                    print(f"  ПОКА НЕТ: {v} (ошибок нет — возможно, ещё в обработке; проверит ночная ревизия)")
        except Exception as exc:  # проверка — удобство, её сбой не должен краснить создание
            print(f"Не удалось проверить результат ({exc}). Запустите audit — он покажет, что появилось.")
    if auto and sent:
        _wb_auto_update_state(sent, created, problems)
    global _WB_LAST_CREATED
    _WB_LAST_CREATED = list(created)
    print("Дальше: update-cards добавит фото и остатки новым карточкам.")
    return 0 if err == 0 else 1


def cmd_wb_zero_stock(articles: str) -> int:
    """Остаток 0 на WB для перечисленных артикулов (снять с продажи товары, у которых в мастере остаток не ведётся)."""
    import wb_client

    import reviews_guard

    asked = [a.strip() for a in articles.split(",") if a.strip()]
    allowed, blocked = reviews_guard.check(asked, channels=("wb",))
    if blocked:
        print(f"Не трогаю (отзывы / не проверено): {', '.join(blocked)}")
    want = {a.lower() for a in allowed}
    if not want:
        return 1
    bmap = {k.lower(): v for k, v in wb_client.get_offer_barcode_map().items()}
    items = [{"sku": bmap[a], "amount": 0} for a in sorted(want) if a in bmap]
    lines = [f"не найдено на WB: {a}" for a in sorted(want) if a not in bmap]
    try:
        if items:
            wb_client.update_stocks(config.wb_warehouse_id, items)
        lines.insert(0, f"WB: остаток 0 отправлен для {len(items)} шт.: " + ", ".join(a for a in sorted(want) if a in bmap))
        rc = 0
    except Exception as exc:  # noqa: BLE001
        lines.insert(0, f"WB: ОШИБКА — {exc}")
        rc = 1
    print("\n".join(lines))
    with open(_data_path("wb_zero_stock_result.txt"), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return rc


def cmd_align_wb_prices(dry_run: bool) -> int:
    """
    Выровнять цену WB с ценой Ozon (правило: на Ozon, WB и Маркете цена покупателя одинаковая).
    Правит ТОЛЬКО колонку «Цена WB» в data/master_control.xlsx: туда вписывается цена Ozon (колонка «Цена, ₽»)
    у всех товаров, где они расходятся. Ничего не отправляет на WB — после этой команды (и Pull)
    запустите push-wb-price-dryrun, посмотрите изменения и затем update-cards (или push-wb-price).
    """
    from openpyxl import load_workbook

    path = _data_path("master_control.xlsx")
    wb = load_workbook(path)
    ws = wb["Товары"] if "Товары" in wb.sheetnames else wb.active
    head = [str(c.value or "") for c in ws[1]]
    try:
        c_art = 0
        c_price = head.index("Цена, ₽")
        c_wb = next(i for i, h in enumerate(head) if h.startswith("Цена WB"))
    except (ValueError, StopIteration):
        print("В мастере не нашёл колонки «Цена, ₽» / «Цена WB».")
        return 1
    c_wbst = head.index("Статус WB") if "Статус WB" in head else None

    def num(v):
        try:
            return float(str(v).replace(" ", "").replace("\xa0", "").replace(",", "."))
        except (TypeError, ValueError):
            return None

    changes = []
    for row in ws.iter_rows(min_row=2):
        art = row[c_art].value
        po, pw = num(row[c_price].value), num(row[c_wb].value)
        on_wb = c_wbst is not None and str(row[c_wbst].value or "").strip() not in ("", "None")
        if art and po and not pw and on_wb:
            changes.append((str(art), 0.0, po, row[c_wb]))  # товар есть на WB, а цена WB пустая/0 — вписываем цену Ozon
            continue
        if not art or not po or not pw:
            continue  # у товара нет цены WB (его нет на WB) — не трогаем
        if abs(po - pw) > 1:
            changes.append((str(art), pw, po, row[c_wb]))
    print(f"Расхождений цены WB и Ozon: {len(changes)}")
    for art, pw, po, _ in sorted(changes, key=lambda x: x[1] / x[2]):
        print(f"  {art}: WB {pw:g} -> {po:g} ₽" + (f" ({po / pw - 1:+.0%})" if pw else " (цена WB была пустой)"))
    if dry_run or not changes:
        print("\nПРОБНЫЙ ПРОГОН — мастер не изменён." if dry_run else "")
        return 0
    for _, _, po, cell in changes:
        cell.value = int(po) if float(po).is_integer() else po
    wb.save(path)
    print("\nГотово: колонка «Цена WB» в мастере обновлена. Дальше: Pull, push-wb-price-dryrun (проверить), затем update-cards.")
    return 0


def cmd_audit(refresh: bool = True, fail_on_new: bool = False, skip_wb: bool = False) -> int:
    """
    РЕВИЗИЯ площадок (только чтение, ничего не меняет на Ozon/WB/Авито/Маркете/сайте).

    refresh=True (команда audit): сначала обновляет выгрузки с площадок — Ozon (товары+остатки),
    WB, Яндекс Маркет, Авито (живые объявления + отчёт автозагрузки) — каждую в try/except,
    чтобы сбой одной площадки не остановил остальные; затем сверяет всё с master_control и
    channels.xlsx и пишет data/audit_report.xlsx + data/audit_summary.md.
    refresh=False (команда audit-offline): только сверка по уже лежащим в data/ файлам.
    Всегда возвращает 0 (расхождения — не ошибка запуска, чтобы не краснел ночной цикл).
    """
    import audit

    if refresh:
        print("=== Ревизия: обновляю выгрузки с площадок ===")

        def safe(title, fn):
            print(f"\n--- {title} ---")
            try:
                fn()
            except Exception as exc:  # noqa: BLE001 — одна площадка не должна ронять остальные
                print(f"{title}: пропущено — {exc}")

        safe("Ozon: товары", cmd_fetch_ozon)
        safe("Ozon: остатки", _audit_fetch_ozon_stocks)
        if skip_wb:
            print("\n--- WB: карточки --- (берём файл, скачанный в начале update-cards)")
        else:
            safe("WB: карточки", cmd_fetch_wb)
        safe("Яндекс Маркет", cmd_yandex_market_export)
        safe("Авито: объявления и отчёт автозагрузки", _audit_fetch_avito)
        print("\n=== Ревизия: сверка ===")
    out = audit.run()
    if fail_on_new and out.get("new_important"):
        # Красный прогон = письмо от GitHub: так узнаём о НОВЫХ проблемах без отдельного бота.
        print(f"\nНОВЫЕ важные проблемы ({len(out['new_important'])}) — прогон помечен ошибкой, чтобы пришло уведомление.")
        return 1
    return 0


def _audit_fetch_ozon_stocks() -> int:
    """data/ozon_stocks.json {offer_id: остаток FBS}. Разбор ответа защитный: не поняли форму — не пишем."""
    import ozon_client

    path = _data_path("ozon_products.json")
    if not os.path.exists(path):
        return 1
    with open(path, "r", encoding="utf-8") as f:
        offer_ids = [p["offer_id"] for p in json.load(f).get("list", []) if p.get("offer_id")]
    items = ozon_client.get_stocks(offer_ids)
    out = {}
    for it in items:
        st = it.get("stocks") or []
        if not isinstance(st, list):
            continue
        fbs = [s.get("present", 0) for s in st if str(s.get("type", "")).lower() == "fbs"]
        if fbs:
            out[str(it.get("offer_id", "")).strip()] = sum(fbs)
    if not out:
        print("Ozon: остатки не разобраны (неожиданная форма ответа) — сверка остатков пропущена.")
        return 1
    with open(_data_path("ozon_stocks.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"Ozon: остатки FBS по {len(out)} товарам -> data/ozon_stocks.json")
    return 0


def _audit_fetch_avito() -> int:
    import avito_client

    try:
        items = avito_client.list_all_items(statuses=["active", "old", "blocked", "rejected"])
        with open(_data_path("avito_live.json"), "w", encoding="utf-8") as f:
            json.dump({"items": items}, f, ensure_ascii=False, indent=1)
        print(f"Авито: {len(items)} объявлений -> data/avito_live.json")
    except Exception as exc:  # noqa: BLE001
        print(f"Авито: список объявлений не получен — {exc}")
    try:
        rep = avito_client.get_autoload_report_items()
        with open(_data_path("avito_autoload.json"), "w", encoding="utf-8") as f:
            json.dump(rep, f, ensure_ascii=False, indent=1)
        print(f"Авито: строк отчёта автозагрузки: {len(rep.get('items', []))} -> data/avito_autoload.json")
    except Exception as exc:  # noqa: BLE001
        print(f"Авито: отчёт автозагрузки не получен — {exc}")
    return 0


def cmd_split_model(article: str, dry_run: bool) -> int:
    """
    Расклеивает уже СОЗДАННЫЕ (в том числе продающиеся) карточки Ozon, которые склеились
    в одну "модель" с соседней из-за одинакового "Названия модели" (характеристика 9048).
    Меняет ТОЛЬКО 9048: ставит значение, равное offer_id карточки (уникальное).
    Название, описание, фото, цена, остаток, 7236/9024 и остальное НЕ затрагиваются.
    Если 9048 уже содержит offer_id — карточку пропускает.
    --dry-run (split-model-dryrun) только показывает, что изменится.
    Артикулы — через --article (через запятую, не больше 5 за раз).
    """
    import ozon_client

    wanted = _parse_offer_ids_arg(article) or []
    if not wanted:
        print("Укажите артикулы через --article, например: --split-model-dryrun --article Ea888gen3")
        return 1
    if len(wanted) > 5:
        print(f"Слишком много артикулов ({len(wanted)}), максимум 5 за раз.")
        return 1

    local = {}
    try:
        data = json.load(open(_data_path("ozon_products.json"), encoding="utf-8"))
        stack = [data]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                if "offer_id" in o and "attributes" in o:
                    local[str(o["offer_id"])] = o
                else:
                    stack.extend(o.values())
            elif isinstance(o, list):
                stack.extend(o)
    except Exception as exc:
        print(f"Не удалось прочитать data/ozon_products.json: {exc}")
        return 1

    payload = []
    for oid in wanted:
        card = local.get(oid)
        if not card:
            print(f"  {oid}: нет в data/ozon_products.json (сначала fetch-ozon) — пропускаю.")
            continue
        cur = {a["id"]: "; ".join(str(v.get("value")) for v in a.get("values", [])) for a in card.get("attributes", []) if isinstance(a, dict) and "id" in a}
        model_old = cur.get(9048, "")
        if oid.lower() in model_old.lower():
            print(f"  {oid}: «Название модели» уже уникальное («{model_old}») — пропускаю.")
            continue
        print(f"  {oid}: 9048 «{model_old}» -> «{oid}» (модель сейчас: {card.get('model_info')})")
        payload.append({
            "offer_id": oid,
            "attributes": [{"id": 9048, "complex_id": 0, "values": [{"value": oid}]}],
        })

    if not payload:
        print("Нечего менять.")
        return 0
    if dry_run:
        print(f"\nПробный прогон: ничего не отправлено. Карточек к расклейке: {len(payload)}. Для отправки запустите split-model.")
        return 0
    res = ozon_client.update_attributes(payload)
    print(f"\nОтправлено на Ozon, ответ: {res}")
    print("Подождите несколько минут, затем fetch-ozon: у карточки должен смениться model_id.")
    return 0


def cmd_diag_ozon_product(article: str) -> int:
    """
    Диагностика одного товара Ozon по offer_id: печатает "сырой" ответ
    /v3/product/info/list (или /v2/product/info/list как откат) — в нём
    видно реальный статус модерации, ошибки и, что важнее всего для
    вопроса "почему не обновились фото", список изображений, которые
    Ozon СЕЙЧАС считает актуальными для карточки (поле images/primary_image
    в ответе — если там всё ещё старые ссылки или счётчик фото не
    совпадает с тем, что мы отправляли, значит правки застряли на стороне
    Ozon, а не в нашем конвейере).
    """
    import ozon_client

    article = (article or "").strip()
    if not article:
        print("Укажите артикул через --article, например: --diag-ozon-product --article 02e305045")
        return 1
    infos = ozon_client.get_product_info_list([article])
    if not infos:
        print(f"Ozon не вернул информацию по {article} — либо офер не существует, либо ошибка запроса.")
        return 1
    for info in infos:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    return 0


def cmd_diag_ozon_orders() -> int:
    """
    Только чтение: проверяет, отдаёт ли Ozon заказы FBS/FBO (те же методы, что
    stock_sync.py), и пишет итог в data/ozon_orders_diag.txt — по дням и с
    текстом ошибки, если она есть. Ничего не меняет (остатки не списывает).
    """
    import collections
    import datetime
    import traceback
    import ozon_client

    lines = []
    now = datetime.datetime.now(datetime.timezone.utc)
    fmt = lambda d: d.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    for days in (30, 10):
        since = now - datetime.timedelta(days=days)
        for name, fn in (("FBS", ozon_client.get_fbs_orders_since), ("FBO", ozon_client.get_fbo_orders_since)):
            try:
                posts = fn(fmt(since), fmt(now))
                by_day = collections.Counter(
                    str(p.get("in_process_at") or p.get("created_at") or "")[:10] for p in posts
                )
                statuses = collections.Counter(str(p.get("status")) for p in posts)
                lines.append(f"{name} за {days} дн.: {len(posts)} отправлений; статусы {dict(statuses)}")
                for d in sorted(by_day):
                    lines.append(f"   {d}: {by_day[d]}")
            except Exception as exc:
                lines.append(f"{name} за {days} дн.: ОШИБКА {exc}")
                lines.append(traceback.format_exc()[-1500:])
    os.makedirs("data", exist_ok=True)
    with open(os.path.join("data", "ozon_orders_diag.txt"), "w", encoding="utf-8") as f:
        f.write(f"Проверка {fmt(now)}\n" + "\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


def cmd_diag_ozon_prices(article: str) -> int:
    """
    Только чтение: сырой ответ /v5/product/info/prices по товарам из --article
    (через запятую) или, если пусто, по всем офферам из data/ozon_products.json.
    Нужен, чтобы увидеть реальные поля цен (price, old_price, min_price,
    auto_action_enabled, marketing_* и т.п.) и понять, как Ozon сейчас хранит
    "предельную цену" для акций. Ничего не меняет.
    """
    import ozon_client

    ids = [a.strip() for a in (article or "").split(",") if a.strip()]
    if not ids:
        try:
            with open("data/ozon_products.json", encoding="utf-8") as f:
                data = json.load(f)
            rows = data.get("list", []) if isinstance(data, dict) else data
            ids = [x.get("offer_id") for x in rows if x.get("offer_id")]
        except Exception as exc:  # noqa: BLE001
            print(f"Не удалось прочитать список товаров: {exc}")
            return 1
    items = ozon_client.get_prices_and_stocks(ids)
    if not items:
        print("Ozon не вернул цен по этим товарам.")
        return 1
    if len(ids) <= 3:
        for it in items:
            print(json.dumps(it, ensure_ascii=False, indent=2))
        return 0
    # Для большого списка — только ключевое в одну строку на товар
    print(f"Товаров: {len(items)}")
    for it in items:
        pr = it.get("price") or {}
        print(
            f"{it.get('offer_id')}: price={pr.get('price')} old={pr.get('old_price')} "
            f"min={pr.get('min_price')} net={pr.get('net_price')} "
            f"auto_action={pr.get('auto_action_enabled')} "
            f"mkt_seller={pr.get('marketing_seller_price')} mkt_price={pr.get('marketing_price')}"
        )
    return 0


def cmd_sync_ozon_prices(dry_run: bool, article: str = "") -> int:
    """
    Выравнивает цены Ozon по мастер-файлу. После перехода Ozon на "предельную
    цену" (октябрь 2026) акции сами опускают price товара ниже нашей цены, и
    наш push её больше не возвращает. Здесь: берём price/old_price из
    data/ozon_catalog.xlsx (он собирается из master_control.xlsx), сравниваем
    с живым /v5/product/info/prices и отправляем ТОЛЬКО те товары, где цена
    на Ozon отличается. Если предельная цена товара выше акционной, Ozon сам
    выводит товар из акции. --article (через запятую) ограничивает список.
    """
    import ozon_client
    import catalog_editor

    edits = catalog_editor.load_catalog_edits(_data_path("ozon_catalog.xlsx"))
    only = {a.strip() for a in (article or "").split(",") if a.strip()}
    ids = [o for o in edits if (not only or o in only)]
    # Товары с "no_price_sync": true в data/ozon_overrides.json не трогаем никогда
    overrides = catalog_editor._load_overrides()
    skipped = [o for o in ids if (overrides.get(o) or {}).get("no_price_sync")]
    if skipped:
        print("Пропускаю (no_price_sync в ozon_overrides.json): " + ", ".join(skipped))
    ids = [o for o in ids if o not in skipped]
    live = {it.get("offer_id"): (it.get("price") or {}) for it in ozon_client.get_prices_and_stocks(ids)}

    def _num(v):
        try:
            return int(round(float(str(v).replace(" ", "").replace(",", "."))))
        except (TypeError, ValueError):
            return 0

    to_push = []
    for oid in ids:
        if oid not in live:
            continue
        want_price = _num(edits[oid].get("price"))
        want_old = _num(edits[oid].get("old_price"))
        if want_price <= 0:
            continue
        cur = live[oid]
        cur_price = _num(cur.get("price"))
        cur_old = _num(cur.get("old_price"))
        if cur_price != want_price or cur_old != want_old:
            print(f"{oid}: на Ozon {cur_price} / до скидки {cur_old} -> по файлу {want_price} / {want_old}")
            to_push.append({
                "offer_id": oid,
                "price": str(want_price),
                "old_price": str(want_old),
                "currency_code": "RUB",
            })
    print(f"\nТоваров с расхождением цены: {len(to_push)} из {len(ids)}")
    if dry_run:
        print("ПРОБНЫЙ ПРОГОН — в Ozon ничего не отправлено.")
        return 0
    bad = 0
    for i in range(0, len(to_push), 100):
        resp = ozon_client.update_prices(to_push[i : i + 100])
        for r in resp.get("result", []):
            if not r.get("updated", True) or r.get("errors"):
                bad += 1
                print(f"ОШИБКА {r.get('offer_id')}: {json.dumps(r.get('errors'), ensure_ascii=False)}")
    print(f"Отправлено: {len(to_push) - bad}, с ошибкой: {bad}")
    return 1 if bad else 0


def _ozon_set_price_floor(offer_ids) -> None:
    """Для товаров, застрявших в акции Ozon: min_price = текущая цена (Ozon не опустит цену ниже нашей).
    Цена и «цена до скидки» не меняются. Если поле не принято — ошибка в лог, ничего не ломается."""
    import ozon_client

    def _n(v):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return 0

    items = []
    for it in ozon_client.get_prices_and_stocks(list(offer_ids)):
        pr = it.get("price") or {}
        price = _n(pr.get("price"))
        if price <= 0:
            continue
        if _n(pr.get("min_price")) == price:
            print(f"  {it.get('offer_id')}: лимит уже = цене ({price})")
            continue
        items.append({
            "offer_id": it.get("offer_id"),
            "price": str(price),
            "old_price": str(_n(pr.get("old_price"))),
            "min_price": str(price),
            "currency_code": "RUB",
        })
        print(f"  {it.get('offer_id')}: лимит {_n(pr.get('min_price'))} -> {price}")
    bad = 0
    for i in range(0, len(items), 100):
        resp = ozon_client.update_prices(items[i : i + 100])
        for r in resp.get("result", []):
            if not r.get("updated", True) or r.get("errors"):
                bad += 1
                print(f"  ОШИБКА {r.get('offer_id')}: {json.dumps(r.get('errors'), ensure_ascii=False)}")
    print(f"  лимит выставлен: {len(items) - bad}, с ошибкой: {bad}")


def _notify_owner_once(key: str, text: str, items) -> None:
    """Сообщение владельцу в Telegram (@SMMLeopartsBot), только если набор тревог НОВЫЙ —
    чтобы не слать одно и то же каждые 3 часа. Состояние: data/owner_alerts.json."""
    path = _data_path("owner_alerts.json")
    try:
        with open(path, encoding="utf-8") as f:
            state = json.load(f)
    except Exception:  # noqa: BLE001
        state = {}
    sig = sorted(str(x).split(":")[0] for x in items)
    if state.get(key) == sig:
        return
    try:
        import designer_bot
        owner = designer_bot._owner_id()
        if owner:
            designer_bot._tg("sendMessage", chat_id=owner, text=text[:4000])
            print("  -> отправлено владельцу в Telegram")
    except Exception as exc:  # noqa: BLE001
        print(f"  Telegram не отправлен: {exc}")
        return
    state[key] = sig
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1)


def cmd_ozon_leave_boosting(dry_run: bool = False) -> int:
    """
    Контроль акций Ozon (с 27.10.2026 — без попыток выхода через API).
    С 12–19.10.2026 у Ozon «единая предельная цена»: методы убрать товар из акции
    (/v1 и /v2 actions/products/deactivate) заблокированы («unified price logic»).
    Товар сам попадает в акцию, если наша цена не выше требуемой цены акции; выйти — только
    поднять цену выше порога (в отчёте — колонка ПОРОГ) или убрать товар вручную в кабинете.
    Что делаем: 1) показываем, в каких акциях наши товары и по какой цене;
    2) ТРЕВОГА, если цена в акции ниже нашей (Ozon продаёт со скидкой из наших денег);
    3) лимит акции (минимальная цена) = наша цена — чтобы Ozon не опускал цену ниже нашей;
    4) выключаем автодобавление в акции, если где-то включилось.
    """
    import ozon_client

    try:
        actions = ozon_client.get_actions()
    except Exception as exc:  # noqa: BLE001
        print(f"Акции через API не получены ({exc}).")
        actions = []
    try:
        with open(_data_path("ozon_products.json"), encoding="utf-8") as _f:
            _pj = json.load(_f)
        _rows = _pj.get("list", []) if isinstance(_pj, dict) else _pj
        by_pid = {int(x["product_id"]): x.get("offer_id") for x in _rows if x.get("product_id")}
    except Exception:  # noqa: BLE001
        by_pid = {}
    print(f"Акций, доступных продавцу через API: {len(actions)}")
    in_actions, alarms = set(), []
    for a in actions:
        title, aid = str(a.get("title") or ""), a.get("id")
        try:
            prods = ozon_client.get_action_products(int(aid))
        except Exception as exc:  # noqa: BLE001
            print(f"  «{title}» (id {aid}): список товаров не получен: {exc}")
            continue
        print(f"  «{title}» (id {aid}): наших товаров в акции {len(prods)}")
        for p in prods:
            oid = str(by_pid.get(int(p.get("id") or 0), p.get("id")))
            in_actions.add(oid)
            try:
                ours, act = float(p.get("price") or 0), float(p.get("action_price") or 0)
            except (TypeError, ValueError):
                ours = act = 0
            if ours and act and act < ours - 0.5:
                alarms.append(f"{oid}: в акции «{title}» цена {act:.0f} ниже нашей {ours:.0f} "
                              f"(выйти — поставить цену выше {p.get('max_action_price')} или убрать в кабинете)")
    if alarms:
        print("\nВНИМАНИЕ — Ozon продаёт ниже нашей цены:")
        for x in alarms:
            print("  " + x)
        if not dry_run:
            _notify_owner_once("ozon_promo_alarms",
                               "⚠️ Ozon продаёт ниже нашей цены в акции:\n" + "\n".join(alarms) +
                               "\n\nНапишите Claude — решим: поднять цену или убрать из акции.", alarms)
    elif in_actions:
        print("Во всех акциях цена = наша (скидки из наших денег нет) — участие бесплатное, ничего не делаем.")
    if in_actions and not dry_run:
        print("\nЛимит акции (минимальная цена = наша цена), чтобы Ozon не опускал цену на сайте ниже нашей:")
        try:
            _ozon_set_price_floor(sorted(in_actions))
        except Exception as exc:  # noqa: BLE001
            print(f"  лимит акции не выставлен: {exc}")
    print("\nАвтодобавление в акции:")
    rc = cmd_disable_ozon_auto_actions(dry_run, "")
    return 1 if (alarms or rc) else 0


def cmd_disable_ozon_auto_actions(dry_run: bool, article: str = "") -> int:
    """
    Выключает у товаров флаг "Автоматически добавлять товар в акции Ozon"
    (price.auto_add_to_ozon_actions_list_enabled -> DISABLED). Цену не меняет:
    в запросе уходят ТЕКУЩИЕ price/old_price с Ozon. Нужно --article (через
    запятую) или пусто = все товары, у которых флаг сейчас включён.
    """
    import ozon_client

    ids = [a.strip() for a in (article or "").split(",") if a.strip()]
    if not ids:
        with open(_data_path("ozon_products.json"), encoding="utf-8") as f:
            data = json.load(f)
        rows = data.get("list", []) if isinstance(data, dict) else data
        ids = [x.get("offer_id") for x in rows if x.get("offer_id")]

    def _num(v):
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return 0

    items = []
    for it in ozon_client.get_prices_and_stocks(ids):
        pr = it.get("price") or {}
        if not pr.get("auto_add_to_ozon_actions_list_enabled"):
            continue
        price = _num(pr.get("price"))
        if price <= 0:
            continue
        print(f"{it.get('offer_id')}: автодобавление в акции ВКЛ (цена {price})")
        items.append({
            "offer_id": it.get("offer_id"),
            "price": str(price),
            "old_price": str(_num(pr.get("old_price"))),
            "currency_code": "RUB",
            "auto_add_to_ozon_actions_list_enabled": "DISABLED",
        })
    print(f"\nТоваров с включённым автодобавлением: {len(items)}")
    if dry_run:
        print("ПРОБНЫЙ ПРОГОН — в Ozon ничего не отправлено.")
        return 0
    bad = 0
    for i in range(0, len(items), 100):
        resp = ozon_client.update_prices(items[i : i + 100])
        for r in resp.get("result", []):
            if not r.get("updated", True) or r.get("errors"):
                bad += 1
                print(f"ОШИБКА {r.get('offer_id')}: {json.dumps(r.get('errors'), ensure_ascii=False)}")
    print(f"Выключено: {len(items) - bad}, с ошибкой: {bad}")
    return 1 if bad else 0


def cmd_diag_tnved(article: str, code: str) -> int:
    """
    Только чтение: показывает, что Ozon знает про ТН ВЭД у товара --article и находит ли
    он код (--seed-phrase, по умолчанию 8708409909) в справочнике этой категории.
    Нужен перед тем, как менять код через колонку "ТН ВЭД" в master_control.xlsx.
    """
    import catalog_editor
    import ozon_client

    article = (article or "").split(",")[0].strip()
    code = (code or "").strip() or "8708409909"
    if not article:
        print("Укажите артикул через --article, например: --diag-tnved --article 0am325025H")
        return 1
    entries = ozon_client.get_product_names([article])
    if not entries:
        print(f"Ozon не вернул характеристики для {article}.")
        return 1
    entry = entries[0]
    cat, typ = entry.get("description_category_id", 0), entry.get("type_id", 0)
    attr_id = catalog_editor._get_tnved_attr_id(cat, typ)
    print(f"{article}: категория {cat}, тип {typ}, id характеристики ТН ВЭД: {attr_id}")
    if not attr_id:
        print("В характеристиках категории не нашлось ТН ВЭД.")
        return 1
    for attr in entry.get("attributes") or []:
        if attr.get("id") == attr_id:
            print("Сейчас в карточке:", json.dumps(attr.get("values"), ensure_ascii=False))
    found = ozon_client.search_attribute_values(cat, typ, attr_id, code)
    print(f"Поиск '{code}' в справочнике Ozon: найдено {len(found)}")
    for it in found[:10]:
        print(f"  id={it.get('id')}: {str(it.get('value'))[:140]}")
    hit = catalog_editor.resolve_tnved_dictionary(cat, typ, attr_id, code)
    print("Точное совпадение:", hit if hit else "НЕТ")
    return 0


def cmd_diag_wb_new_cards() -> int:
    """
    Диагностика: почему push-wb-new-cards/push-new-product-all "принял"
    товар на создание, а он так и не появился в data/wb_cards.json.
    /content/v2/cards/upload асинхронный — WB отвечает "принято в
    обработку" ещё до того, как реально проверит данные (раздел,
    характеристики образца и т.д.), и если проверка не прошла, карточка
    просто не создаётся, без уведомления в моменте. Единственный способ
    узнать причину — отдельный метод с историей последних ошибок
    (/content/v2/cards/error/list — см. wb_client.get_upload_errors,
    важно: это POST с курсором, а не GET), который эта команда печатает
    как есть (там уже видно vendorCodes и текст ошибки по каждому).
    """
    import wb_client

    errors = wb_client.get_upload_errors()
    if not errors:
        print(
            "WB не вернул ни одной ошибки создания карточек за последнее время. "
            "Либо товар ещё в обработке (подождите и запустите fetch-wb ещё раз), "
            "либо ошибка уже 'устарела' в истории WB — попробуйте push-wb-new-cards "
            "заново и сразу после этого запустите diag-wb-new-cards."
        )
        return 0
    for err in errors:
        print(json.dumps(err, ensure_ascii=False, indent=2))
    return 0


def _parse_offer_ids_arg(article: str) -> list:
    """--article "a,b,c" -> ["a","b","c"] — archive/unarchive-ozon-products принимают несколько сразу."""
    return [a.strip() for a in (article or "").split(",") if a.strip()]


def cmd_archive_ozon_products(article: str, dry_run: bool) -> int:
    """
    Перевести товар(ы) Ozon в архив — ОБРАТИМО, история и сами товары не
    удаляются (см. ozon_client.archive_products). Решение пользователя
    2026-09-16: не удалять лишние/дублирующиеся товары из каталога
    насовсем, а архивировать — "мне бы в архив чтобы если мы чтото важное
    удалили я могу потом проверить". article — один или несколько offer_id
    через запятую.
    """
    import ozon_client

    offer_ids = _parse_offer_ids_arg(article)
    if not offer_ids:
        print("Не передан артикул(ы) — заполните поле 'Артикул товара' (через запятую, если несколько).")
        return 1

    id_map = ozon_client.resolve_product_ids(offer_ids)
    missing = [o for o in offer_ids if o not in id_map]
    if missing:
        print(f"Не найдены на Ozon (пропускаю): {', '.join(missing)}")
    if not id_map:
        print("Ни один артикул не найден на Ozon — архивировать нечего.")
        return 1

    import reviews_guard

    allowed, blocked = reviews_guard.check(list(id_map), channels=("ozon",))
    if blocked:
        print(f"НЕ архивирую (отзывы / не проверено): {', '.join(blocked)}")
    id_map = {o: p for o, p in id_map.items() if o in allowed}
    if not id_map:
        print("Архивировать нечего — все артикулы защищены проверкой отзывов.")
        return 1

    print(f"{'[DRY RUN] ' if dry_run else ''}В архив уйдут ({len(id_map)}):")
    for offer_id, pid in id_map.items():
        print(f"  {offer_id} (product_id={pid})")

    if dry_run:
        print("\nЭто был пробный прогон — ничего не отправлено. Запустите archive-ozon-products, чтобы архивировать по-настоящему.")
        return 0

    result = ozon_client.archive_products(list(id_map.values()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("\nГотово. Товары ушли в архив — их видно в кабинете Ozon (фильтр 'Архивные'), можно вернуть командой unarchive-ozon-products.")
    return 0


def cmd_remove_product(article: str, dry_run: bool) -> int:
    """
    "Снять товар отовсюду" одной командой (--article "арт1,арт2"):
      1) остаток в data/master_control.xlsx -> 0 (см. remove_product.py);
      2) sync-master-control — остаток уходит в рабочие каталоги;
      3) push-stock — Ozon: остаток 0;
      4) push-wb-stock — WB: остаток 0;
      5) archive-ozon-products — товар уходит в архив Ozon (обратимо);
      6) build-avito-catalog — товар исчезает из фида, объявление Avito закроется;
      7) Яндекс Маркет — остаток 0; 8) Tilda (CommerceML) — «нет в наличии» (если заданы секреты).
    НЕ делается автоматически: карточка WB остаётся (у WB нет безопасного архива
    в API — удалить можно только вручную в кабинете, остаток 0 = "нет в наличии");
    сайт на Тильде — пока скрыть вручную (после подключения CommerceML скрывается
    само по остатку 0). Каждый шаг идёт независимо: ошибка одного не мешает остальным,
    итог по шагам — в конце. --dry-run (remove-product-dryrun) ничего не меняет.
    """
    import remove_product

    offer_ids = _parse_offer_ids_arg(article)
    if not offer_ids:
        print("Не передан артикул(ы) — заполните поле 'Артикул товара' (через запятую, если несколько).")
        return 1
    if len(offer_ids) > remove_product.MAX_ARTICLES:
        print(f"Слишком много артикулов за раз ({len(offer_ids)}, максимум {remove_product.MAX_ARTICLES}) — разбейте на несколько запусков.")
        return 1

    master_path = remove_product.MASTER_PATH
    if not os.path.exists(master_path):
        print(f"Нет файла {master_path}.")
        return 1

    # ГЛАВНОЕ ПРАВИЛО: карточку с отзывами не снимаем. Проверка — до любых изменений.
    import reviews_guard

    allowed, blocked = reviews_guard.check(offer_ids, channels=("ozon", "wb"))
    if blocked:
        print(f"НЕ снимаю (отзывы / не проверено): {', '.join(blocked)}")
    offer_ids = allowed
    if not offer_ids:
        print("Снимать нечего — все артикулы защищены проверкой отзывов.")
        return 1

    found, missing = remove_product.zero_stock(offer_ids, dry_run=dry_run)
    if missing:
        print(f"НЕ НАЙДЕНЫ в master_control.xlsx (проверьте написание артикула, регистр важен): {', '.join(missing)}")
    if not found:
        print("Ни один артикул не найден — снимать нечего.")
        return 1

    print(f"{'[ПРОБНЫЙ ПРОГОН] ' if dry_run else ''}Снимаются с продажи ({len(found)}):")
    for offer, old in found:
        print(f"  {offer}: остаток был {old if old not in (None, '') else 'не задан'} -> станет 0")

    if dry_run:
        print(
            "\nЭто был пробный прогон — ничего не изменено и не отправлено. Запустите remove-product, "
            "чтобы снять товар по-настоящему: остаток 0 на Ozon и WB, архив Ozon, закрытие объявления Avito."
        )
        return 0

    found_ids = ",".join(o for o, _ in found)
    results = []

    print("\n=== Шаг 1/5: sync-master-control (остаток 0 -> рабочие файлы) ===")
    rc = cmd_sync_master_control()
    results.append(("sync-master-control", rc))
    if rc != 0:
        print("\nremove-product: sync-master-control упал — дальше не иду (иначе отправили бы старые остатки). Проверьте вывод выше.")
        return 1

    print("\n=== Шаг 2/5: push-stock (Ozon, остаток 0) ===")
    results.append(("push-stock", cmd_push_stock()))

    print("\n=== Шаг 3/5: push-wb-stock (WB, остаток 0) ===")
    results.append(("push-wb-stock", cmd_push_wb_stock()))

    print("\n=== Шаг 4/5: archive-ozon-products (архив Ozon, обратимо) ===")
    results.append(("archive-ozon-products", cmd_archive_ozon_products(found_ids, dry_run=False)))

    print("\n=== Шаг 5/5: build-avito-catalog (убрать из фида Avito) ===")
    results.append(("build-avito-catalog", cmd_build_avito_catalog()))

    import yandex_market_client

    if yandex_market_client.is_configured():
        print("\n=== Шаг 6: push-yandex-stock (Яндекс Маркет, остаток 0) ===")
        results.append(("push-yandex-stock", cmd_push_yandex_stock()))
    else:
        print("\n(Яндекс Маркет не подключён — шаг пропущен)")

    if os.environ.get("TILDA_COMMERCEML_URL", "").strip():
        print("\n=== Шаг 7: push-tilda (сайт, остаток 0 -> «нет в наличии») ===")
        results.append(("push-tilda", cmd_push_tilda()))
    else:
        print("\n(Tilda CommerceML не подключена — шаг пропущен)")

    print()
    failed = [name for name, rc in results if rc != 0]
    if failed:
        print(f"remove-product: ошибка на шаге(ах): {', '.join(failed)} — проверьте вывод выше (остальные шаги выполнены).")
    else:
        print(f"remove-product: готово — {found_ids}: остаток 0 на Ozon, WB, Маркете и сайте, архив Ozon, убран из фида Avito.")
    print("Осталось вручную (если нужно): удалить карточку WB в кабинете (сейчас на WB стоит остаток 0).")
    return 1 if failed else 0


def cmd_unarchive_ozon_products(article: str) -> int:
    """Вернуть товар(ы) из архива Ozon обратно в активный каталог (см. cmd_archive_ozon_products)."""
    import ozon_client

    offer_ids = _parse_offer_ids_arg(article)
    if not offer_ids:
        print("Не передан артикул(ы) — заполните поле 'Артикул товара' (через запятую, если несколько).")
        return 1

    id_map = ozon_client.resolve_product_ids(offer_ids)
    missing = [o for o in offer_ids if o not in id_map]
    if missing:
        print(f"Не найдены на Ozon (пропускаю): {', '.join(missing)}")
    if not id_map:
        print("Ни один артикул не найден на Ozon — восстанавливать нечего.")
        return 1

    result = ozon_client.unarchive_products(list(id_map.values()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"\nГотово. Возвращены из архива ({len(id_map)}): {', '.join(id_map.keys())}")
    return 0


def cmd_pull_ozon_stock() -> int:
    """
    Разовое действие: подтягивает ТЕКУЩИЙ остаток напрямую из Ozon (сумма
    "доступно к продаже" по FBO+FBS) и проставляет его в "Кол-во к продаже"
    ozon_catalog.xlsx — но ТОЛЬКО для товаров, у которых эта ячейка сейчас
    пустая (обычный случай для товаров, давно продающихся на Ozon, но ещё
    ни разу не заведённых в эту таблицу). Уже заполненные ячейки (вручную
    или через sync-orders/push-stock) не трогает. После этого разового
    заполнения источником истины по остатку снова становится только этот
    xlsx (см. --sync-orders / --push-stock).
    """
    import ozon_client
    import catalog_editor

    xlsx_path = _data_path("ozon_catalog.xlsx")
    if not os.path.exists(xlsx_path):
        print(f"Нет файла {xlsx_path} — сначала выполните build-ozon-catalog.")
        return 1

    edits = catalog_editor.load_catalog_edits(xlsx_path)
    empty_ids = [oid for oid, e in edits.items() if e.get("quantity_to_sell") in (None, "")]
    if not empty_ids:
        print("У всех товаров в ozon_catalog.xlsx уже проставлен остаток в 'Кол-во к продаже' — подтягивать нечего.")
        return 0

    print(f"Товаров с пустым остатком: {len(empty_ids)} — запрашиваю текущий остаток у Ozon...")
    items = ozon_client.get_stocks(empty_ids)
    print(f"Ozon вернул данные по {len(items)} товарам.")
    if items:
        print("Пример сырого ответа (для проверки полей present/reserved — см. комментарий в ozon_client.get_stocks):")
        print(json.dumps(items[:2], ensure_ascii=False, indent=2))

    stocks = {}
    for it in items:
        oid = it.get("offer_id")
        if not oid:
            continue
        stocks[oid] = catalog_editor.stock_from_ozon_item(it)

    filled, unmatched_in_file = catalog_editor.fill_empty_stock(xlsx_path, stocks)
    print(f"\nЗаполнено остатков в {xlsx_path}: {len(filled)}")
    for oid, qty in sorted(filled.items()):
        print(f"  {oid}: {qty}")

    still_empty = sorted(set(empty_ids) - set(filled))
    if still_empty:
        print(
            f"\nOzon не вернул остаток (или он не распознался) для {len(still_empty)} товаров — "
            f"проверьте вручную: {', '.join(still_empty)}"
        )

    print("\nДальше: build-master-control, чтобы увидеть заполненные остатки в master_control.xlsx.")
    return 0


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
    parser.add_argument("--ozon-warehouses", action="store_true", help="Показать склады продавца на Ozon (для OZON_WAREHOUSE_ID, нужен push-stock)")
    parser.add_argument("--attach-avito-photos", action="store_true", help="Залить фото из photos_avito/ (спец. квадратный формат под Avito) в data/avito_photos.xlsx")
    parser.add_argument("--build-avito-catalog", action="store_true", help="Собрать data/avito_feed.xlsx для загрузки в Avito Автозагрузку (не требует ключей)")
    parser.add_argument("--build-tilda-catalog", action="store_true", help="Собрать data/tilda_feed.csv для ручного импорта в магазин на Тильде")
    parser.add_argument("--test-avito", action="store_true", help="Проверить доступ к Avito API (AVITO_CLIENT_ID/AVITO_CLIENT_SECRET)")
    parser.add_argument("--fetch-avito-orders", action="store_true", help="Получить заказы Авито Доставки за 30 дней в data/avito_orders.json")
    parser.add_argument("--list-avito-items", action="store_true", help="Показать сырой список объявлений Avito (диагностика сопоставления с артикулом)")
    parser.add_argument("--build-avito-item-map", action="store_true", help="Собрать data/avito_item_map.xlsx: объявления Avito + подсказка по совпадению с каталогом")
    parser.add_argument("--save-avito-item-map", action="store_true", help="Сохранить подтверждённое сопоставление avitoId->артикул в data/avito_item_map.json")
    parser.add_argument("--build-ozon-catalog", action="store_true", help="Собрать data/ozon_catalog.xlsx для редактирования карточек (название, описание, цена, фото)")
    parser.add_argument("--attach-ozon-photos", action="store_true", help="Подставить в xlsx ссылки на фото из папки photos/ по имени файла (offer_id_1.jpg и т.п.)")
    parser.add_argument("--push-ozon-cards-dryrun", action="store_true", help="Показать, что будет отправлено в Ozon, БЕЗ реальной отправки")
    parser.add_argument("--push-ozon-cards", action="store_true", help="Реально отправить правки карточек в Ozon (сначала всегда делайте dryrun!)")
    parser.add_argument("--download-ozon-photos", action="store_true", help="Скачать в photos/ фото товаров, у которых нет локального файла, но есть ссылка (уже висит на карточке Ozon) — работает только там, где есть доступ к CDN Ozon, практически только в GitHub Actions")
    parser.add_argument("--full-sync-ozon", action="store_true", help="Composite: fetch-ozon + build-ozon-catalog + download-ozon-photos + attach-ozon-photos одной командой")
    parser.add_argument("--build-master-control", action="store_true", help="Собрать/обновить единый файл-пульт data/master_control.xlsx (фото+название+описание+хэштеги+заметки для всех товаров)")
    parser.add_argument("--sync-master-control", action="store_true", help="Перенести правки из data/master_control.xlsx обратно в ozon_catalog.xlsx/ozon_new_products.xlsx/photos/")
    parser.add_argument("--build-ozon-new-template", action="store_true", help="Создать пустую таблицу для СОВСЕМ НОВЫХ товаров Ozon (по образцу существующего)")
    parser.add_argument("--attach-ozon-new-photos", action="store_true", help="Подставить фото из photos/ в таблицу новых товаров Ozon")
    parser.add_argument("--push-ozon-new-cards-dryrun", action="store_true", help="Показать, что будет создано в Ozon, БЕЗ реальной отправки")
    parser.add_argument("--push-ozon-new-cards", action="store_true", help="Реально создать новые товары в Ozon (сначала всегда dryrun!)")
    parser.add_argument("--build-wb-new-template", action="store_true", help="Создать пустую таблицу для СОВСЕМ НОВЫХ товаров WB (по образцу существующего)")
    parser.add_argument("--push-wb-new-cards-dryrun", action="store_true", help="Показать, что будет создано в WB, БЕЗ реальной отправки")
    parser.add_argument("--push-wb-new-cards", action="store_true", help="Реально создать новые товары в WB (сначала всегда dryrun!)")
    parser.add_argument("--build-new-product-template", action="store_true", help="Создать пустой data/new_products.xlsx — ОДНА таблица для нового товара сразу на Ozon+WB (вместо отдельных ozon-new/wb-new)")
    parser.add_argument("--push-new-product-all-dryrun", action="store_true", help="Показать, что будет создано на Ozon и WB по data/new_products.xlsx, БЕЗ реальной отправки")
    parser.add_argument("--push-new-product-all", action="store_true", help="Реально создать новый товар на Ozon (с фото) и отправить на создание на WB (без фото — см. finish-new-product)")
    parser.add_argument("--launch-new-products", action="store_true", help="ОДНОЙ командой: push-new-product-all + пауза 3 мин + finish-new-product — запускает новые товары сразу на Ozon/WB/Avito (сайт всё равно импортируется вручную)")
    parser.add_argument("--finish-new-product", action="store_true", help="Второй шаг после push-new-product-all: подтянуть новый товар в общий каталог, долить фото на WB, обновить фиды Avito и сайта")
    parser.add_argument("--compare-ozon-wb", action="store_true", help="Сравнить каталоги Ozon и WB по артикулу продавца, без объединения")
    parser.add_argument("--build-wb-catalog", action="store_true", help="Собрать data/wb_catalog.xlsx для редактирования карточек WB (название, описание, фото)")
    parser.add_argument("--attach-wb-photos", action="store_true", help="Подставить в wb_catalog.xlsx ссылки на фото из папки photos/ по имени файла")
    parser.add_argument("--full-sync-wb", action="store_true", help="Composite: fetch-wb + build-wb-catalog + attach-wb-photos + build-master-control одной командой")
    parser.add_argument("--push-wb-cards-dryrun", action="store_true", help="Показать, что будет отправлено в WB, БЕЗ реальной отправки")
    parser.add_argument("--push-wb-cards", action="store_true", help="Реально отправить правки карточек WB (сначала всегда делайте dryrun!)")
    parser.add_argument("--attach-photos-all", action="store_true", help="Composite: attach-ozon-photos + attach-wb-photos + build-master-control одной командой (после правки общей папки photos/)")
    parser.add_argument("--push-photos-dryrun", action="store_true", help="Composite: push-ozon-cards-dryrun + push-wb-cards-dryrun одной командой, ничего не отправляется")
    parser.add_argument("--push-photos", action="store_true", help="Composite: реально отправить карточки (в т.ч. фото) сразу в Ozon и WB (сначала всегда делайте push-photos-dryrun!)")
    parser.add_argument("--update-photos", action="store_true", help="Composite \"всё в одном\" для смены фото: attach-photos-all + push-photos + build-avito-catalog одной командой (без отдельного dry-run)")
    parser.add_argument("--update-cards", action="store_true", help="ОДНА команда \"обновить всё везде\": проверка + sync-master-control + карточки/фото/цены/остатки на Ozon и WB + Яндекс Маркет (если подключён) + фид Avito + сайт Tilda (если подключена)")
    parser.add_argument("--push-wb-price-dryrun", action="store_true", help="Показать, у каких товаров изменится цена на WB (колонка 'Цена WB' в wb_catalog.xlsx), БЕЗ реальной отправки")
    parser.add_argument("--push-wb-price", action="store_true", help="Реально отправить новую цену на WB для существующих товаров (сначала всегда делайте dryrun!)")
    parser.add_argument("--sync-orders", action="store_true", help="Общий учёт остатков: списать заказы Ozon+WB за 30 дней из 'Кол-во к продаже' в data/ozon_catalog.xlsx")
    parser.add_argument("--sync-tilda-order", action="store_true", help="Списать остаток по заказу с сайта (Тильда) — вызывается автоматически по вебхуку через repository_dispatch, вручную не запускать")
    parser.add_argument("--build-kits-template", action="store_true", help="Создать пустой data/kits.xlsx для описания состава комплектов (наборов из тех же деталей, что продаются отдельно)")
    parser.add_argument("--recompute-kit-stock", action="store_true", help="Пересчитать 'Кол-во к продаже' у комплектов по текущему остатку деталей (data/kits.xlsx)")
    parser.add_argument("--push-stock-dryrun", action="store_true", help="Показать остатки ('Кол-во к продаже'/'Остаток, шт.'), которые будут отправлены в Ozon, БЕЗ реальной отправки")
    parser.add_argument("--push-stock", action="store_true", help="Реально отправить остатки в Ozon (сначала всегда делайте dryrun!)")
    parser.add_argument("--push-wb-stock-dryrun", action="store_true", help="Показать остатки ('Кол-во к продаже'/'Остаток, шт.'), которые будут отправлены в WB, БЕЗ реальной отправки")
    parser.add_argument("--push-wb-stock", action="store_true", help="Реально отправить остатки в WB — тот же общий склад, что и push-stock у Ozon (сначала всегда делайте dryrun!)")
    parser.add_argument("--fix-wb-dimensions", action="store_true", help="Разовая правка битых габаритов WB у 0am325477/0am325025HX (см. cmd_fix_wb_dimensions)")
    parser.add_argument("--pull-ozon-stock", action="store_true", help="Разово подтянуть текущий остаток из Ozon в 'Кол-во к продаже' для товаров, где эта ячейка ещё пустая")
    parser.add_argument("--sync-ozon-prices-dryrun", action="store_true", help="Показать товары, где цена на Ozon отличается от мастер-файла (ничего не отправляет)")
    parser.add_argument("--sync-ozon-prices", action="store_true", help="Вернуть на Ozon цены из мастер-файла там, где акции их опустили (--article ограничивает список)")
    parser.add_argument("--ozon-leave-boosting-dryrun", action="store_true", help="Показать акции Ozon и наши товары в «бустинге» (ничего не меняет)")
    parser.add_argument("--ozon-leave-boosting", action="store_true", help="Убрать товары из акций «бустинг» и выключить автодобавление")
    parser.add_argument("--disable-ozon-auto-actions-dryrun", action="store_true", help="Показать товары с включённым автодобавлением в акции Ozon (ничего не меняет)")
    parser.add_argument("--disable-ozon-auto-actions", action="store_true", help="Выключить автодобавление в акции Ozon (цены не меняются; --article ограничивает список)")
    parser.add_argument("--diag-tnved", action="store_true", help="Диагностика (только чтение): ищет код ТН ВЭД (--seed-phrase, по умолчанию 8708409909) в справочнике Ozon для категории товара --article")
    parser.add_argument("--diag-ozon-analytics", action="store_true", help="Диагностика (только чтение): какие метрики аналитики Ozon доступны без Premium; итог data/ozon_analytics_diag.txt + ozon_analytics_weekly.xlsx")
    parser.add_argument("--diag-ozon-orders", action="store_true", help="Диагностика (только чтение): отдаёт ли Ozon заказы FBS/FBO, итог в data/ozon_orders_diag.txt")
    parser.add_argument("--catalog-audit", action="store_true", help="Аудит цен всего каталога по всем каналам (только чтение) -> data/catalog_audit.xlsx")
    parser.add_argument("--diag-ozon-promos", action="store_true", help="Диагностика (только чтение): новые методы акций Ozon из docs.ozon.ru -> data/ozon_promos_diag.txt")
    parser.add_argument("--diag-ozon-prices", action="store_true", help="Диагностика (только чтение): цены и предельная цена Ozon по --article (через запятую) или по всем товарам")
    parser.add_argument("--diag-ozon-product", action="store_true", help="Диагностика: показать сырой ответ Ozon (статус/ошибки/фото) по одному offer_id из --article")
    parser.add_argument("--diag-wb-new-cards", action="store_true", help="Диагностика: почему созданные через push-wb-new-cards/push-new-product-all карточки не появились на WB (причины ошибок)")
    parser.add_argument("--archive-ozon-products-dryrun", action="store_true", help="Показать, какие товары (--article, через запятую) уйдут в архив на Ozon, БЕЗ реальной отправки")
    parser.add_argument("--archive-ozon-products", action="store_true", help="Реально перевести товар(ы) в архив на Ozon — обратимо, не удаление (сначала всегда делайте dryrun!)")
    parser.add_argument("--unarchive-ozon-products", action="store_true", help="Вернуть товар(ы) из архива Ozon обратно в активный каталог")
    parser.add_argument("--validate-master", action="store_true", help="Проверить data/master_control.xlsx (цены, остатки, названия, фото) БЕЗ отправки — ловит ошибки до update-cards")
    parser.add_argument("--fix-spu-dryrun", action="store_true", help="Показать, как будут исправлены НЕ СОЗДАННЫЕ карточки Ozon с конфликтом SPU (--article), БЕЗ отправки")
    parser.add_argument("--fix-spu", action="store_true", help="Сделать характеристики 7236/9024/9048 не созданных карточек (--article) уникальными; созданные/продающиеся карточки не трогает")
    parser.add_argument("--audit", action="store_true", help="РЕВИЗИЯ: обновить выгрузки со всех площадок и показать расхождения с мастером (только чтение) -> data/audit_report.xlsx")
    parser.add_argument("--wb-create-missing-dryrun", action="store_true", help="Показать, какие товары будут созданы на WB (нет на WB, но должны быть по ревизии), БЕЗ отправки. --article (через запятую) — только эти")
    parser.add_argument("--wb-create-missing", action="store_true", help="Реально создать на WB недостающие товары (сначала dryrun!). --article (через запятую) — только эти")
    parser.add_argument("--align-wb-prices-dryrun", action="store_true", help="Показать, у каких товаров цена WB отличается от Ozon и какой станет (мастер не меняется)")
    parser.add_argument("--align-wb-prices", action="store_true", help="Вписать цену Ozon в колонку «Цена WB» мастера у всех расходящихся товаров (на WB пока ничего не уходит)")
    parser.add_argument("--audit-nightly", action="store_true", help="Ночная ревизия: как audit, но прогон краснеет, если появились НОВЫЕ важные расхождения (чтобы пришло письмо)")
    parser.add_argument("--direct-access-check", action="store_true", help="Яндекс Директ: одобрен ли доступ к API (только чтение)")
    parser.add_argument("--direct-upload-draft", action="store_true", help="Загрузить кампанию data/direct/*.tsv в Директ ЧЕРНОВИКОМ (без модерации и запуска)")
    parser.add_argument("--ozon-rich-dryrun", action="store_true", help="Rich-контент Ozon из data/ozon_rich/*.json: проверка и итоговый JSON -> data/ozon_rich_preview.txt (ничего не меняет)")
    parser.add_argument("--ozon-media-diag", action="store_true", help="Что Ozon хранит по видео/фото/rich-контенту карточек с видео в photos/ -> data/ozon_media_diag.txt (только чтение)")
    parser.add_argument("--ozon-content-rating", action="store_true", help="Контент-рейтинг карточек Ozon и чего не хватает -> data/ozon_content_rating.xlsx (только чтение)")
    parser.add_argument("--ozon-attr-plan", action="store_true", help="План заполнения характеристик Ozon для артикулов из data/ozon_attr_articles.txt -> data/ozon_attr_plan.xlsx (только чтение)")
    parser.add_argument("--ozon-attr-apply", action="store_true", help="Отправить на Ozon ТОЛЬКО строки плана с «да» (меняет только эти характеристики)")
    parser.add_argument("--avito-dupes", action="store_true", help="Дубли объявлений Авито со статистикой -> data/avito_dupes.xlsx (только чтение)")
    parser.add_argument("--audit-offline", action="store_true", help="Ревизия только по уже лежащим в data/ файлам, без обращения к площадкам")
    parser.add_argument("--split-model-dryrun", action="store_true", help="Показать, как будет расклеена модель Ozon (характеристика 9048) у карточек из --article, БЕЗ отправки")
    parser.add_argument("--split-model", action="store_true", help="Расклеить модель Ozon у карточек из --article: меняет ТОЛЬКО 9048 на offer_id")
    parser.add_argument("--remove-product-dryrun", action="store_true", help="Показать, какие товары (--article, через запятую) будут сняты с продажи (remove-product), БЕЗ изменений")
    parser.add_argument("--remove-product", action="store_true", help="Снять товар(ы) (--article) отовсюду: остаток 0 на Ozon/WB, архив Ozon, убрать из фида Avito (сначала всегда dryrun!)")
    parser.add_argument("--build-tilda-commerceml", action="store_true", help="Собрать data/tilda_import0_1.xml и tilda_offers0_1.xml для синхронизации с Тильдой (CommerceML), БЕЗ отправки")
    parser.add_argument("--push-tilda", action="store_true", help="Отправить каталог (цены, остатки, новые товары) в Тильду по CommerceML (нужны секреты TILDA_COMMERCEML_*)")
    parser.add_argument("--yandex-market-test", action="store_true", help="Проверить доступ к Яндекс Маркету (секреты YANDEX_MARKET_API_KEY/YANDEX_MARKET_CAMPAIGN_ID)")
    parser.add_argument("--yandex-market-export", action="store_true", help="Выгрузить все товары кабинета Яндекс Маркета в data/yandex_market_offers.xlsx и сверить SKU с нашим каталогом (только чтение)")
    parser.add_argument("--push-yandex-stock-dryrun", action="store_true", help="Показать остатки, которые уйдут на Яндекс Маркет, БЕЗ отправки")
    parser.add_argument("--push-yandex-stock", action="store_true", help="Отправить остатки на Яндекс Маркет (FBS)")
    parser.add_argument("--push-yandex-names-dryrun", action="store_true", help="Показать, какие названия на Яндекс Маркете будут приведены к мастеру, БЕЗ отправки")
    parser.add_argument("--push-yandex-names", action="store_true", help="Привести названия на Яндекс Маркете к названиям из master_control (сначала всегда dryrun!)")
    parser.add_argument("--push-yandex-price-dryrun", action="store_true", help="Показать цены, которые уйдут на Яндекс Маркет, БЕЗ отправки")
    parser.add_argument("--push-yandex-price", action="store_true", help="Отправить цены на Яндекс Маркет (те же, что на Ozon; сначала всегда dryrun!)")
    parser.add_argument("--ozon-status", action="store_true", help="Статус ВСЕХ товаров на Ozon (создан ли, модерация, ошибки) — почему push-stock отвечает PRODUCT_IS_NOT_CREATED; --article \"а,б\" — только указанные")
    parser.add_argument("--test-wordstat", action="store_true", help="Проверить, что ключ Wordstat API работает")
    parser.add_argument("--wordstat-collect", action="store_true", help="Собрать SEO-семантику по артикулу через Wordstat API (см. --article/--seed-phrase)")
    parser.add_argument("--wordstat-collect-batch", action="store_true", help="Собрать SEO-семантику сразу по списку артикулов из data/wordstat_queue.xlsx")
    parser.add_argument("--article", type=str, default="", help="Артикул товара — для wordstat-collect/diag-ozon-product (один) или archive/unarchive-ozon-products (можно несколько через запятую)")
    parser.add_argument("--seed-phrase", type=str, default="", help="Стартовая фраза(ы) для --wordstat-collect, через ';' если несколько")
    parser.add_argument("--social-test", action="store_true", help="Проверить токены VK и Instagram")
    parser.add_argument("--plan-reorder", action="store_true", help="Переставить даты постов в data/content_plan.xlsx: мехатроники/платы первыми, похожие детали не подряд (см. plan_order.py)")
    parser.add_argument("--plan-reorder-dryrun", action="store_true", help="То же без сохранения — показать новый порядок")
    parser.add_argument("--social-status", action="store_true", help="Сколько постов готово/опубликовано (data/content_plan.xlsx + social/)")
    parser.add_argument("--social-publish-dryrun", action="store_true", help="Показать, какие посты уйдут в VK/Instagram, БЕЗ публикации (--article N — принудительно номера постов)")
    parser.add_argument("--social-publish", action="store_true", help="Опубликовать самый ранний готовый пост на каждую площадку (--article N,M — принудительно эти посты)")
    parser.add_argument("--store-links", action="store_true", help="Ссылки на страницы наших магазинов на Ozon/WB/Маркете (data/store_links.json)")
    parser.add_argument("--photo-text-audit", action="store_true", help="Распознать текст на всех картинках и найти «видео/скидка/%/дешевле/оптовикам» (data/photo_text_audit.xlsx)")
    parser.add_argument("--price-audit", action="store_true", help="Ревизия цен и прибыли (data/price_audit.xlsx), с начислениями Ozon")
    parser.add_argument("--reviews-check", action="store_true", help="Есть ли отзывы у артикулов (--article через запятую; пусто = все из мастера) на Ozon и WB -> data/reviews_guard.txt")
    parser.add_argument("--price-preview", action="store_true", help="Предпросмотр цен Авито/сайта по правилу «одинаковая прибыль» (data/price_preview.xlsx), ничего не отправляет")
    parser.add_argument("--ozon-returns", action="store_true", help="Возвраты Ozon за 90 дней с причинами (data/ozon_returns.json)")
    parser.add_argument("--ozon-unit-weekly", action="store_true", help="Юнит-экономика Ozon за 30 дней по товарам + алерт в Telegram (data/ozon_unit_report.xlsx)")
    parser.add_argument("--ozon-finance", action="store_true", help="Фактические начисления Ozon за 30 дней (data/ozon_finance.json)")
    parser.add_argument("--wb-zero-stock", action="store_true", help="Обнулить остаток на WB для артикулов из --article (через запятую)")
    parser.add_argument("--fix-partnumbers", action="store_true", help="Исправить «номер детали» на 7 карточках Ozon/WB (fix_partnumbers.py)")
    parser.add_argument("--fix-partnumbers-dryrun", action="store_true", help="То же без отправки")
    parser.add_argument("--site-links", action="store_true", help="Собрать ссылки на товары сайта (data/site_links.json) для постов")
    parser.add_argument("--direct-template-dump", action="store_true", help="Яндекс Директ: скачать Excel-шаблон Директа и вывести его структуру (только чтение)")
    parser.add_argument("--direct-xlsx", action="store_true", help="Яндекс Директ: собрать data/direct/*.xlsx для загрузки через Excel из *.tsv")
    parser.add_argument("--direct-lint", action="store_true", help="Яндекс Директ: проверить data/direct/*.tsv (мусорные фразы, конфликты минус-слов, длины)")
    parser.add_argument("--direct-check-links", action="store_true", help="Яндекс Директ: проверить страницы активной кампании (200, заглушки, якоря)")
    parser.add_argument("--direct-report", action="store_true", help="Яндекс Директ: сводка за вчера/7 дней в Telegram (YANDEX_DIRECT_TOKEN)")
    parser.add_argument("--social-vk-feed", action="store_true", help="Пересобрать ленту RSS для импорта во VK (data/vk_feed.xml)")
    parser.add_argument("--social-autopublish", action="store_true", help="То же по расписанию; работает только при секрете SOCIAL_AUTOPUBLISH=1")
    parser.add_argument("--designer-link", action="store_true", help="Запомнить Telegram-чат дизайнера (после Start у бота); с --article owner — запомнить ВАШ чат (для пересылки макетов и команд «стоп N»)")
    parser.add_argument("--designer-send-dryrun", action="store_true", help="Показать задание дизайнеру БЕЗ отправки (--article N — номер поста, иначе пост следующего рабочего дня)")
    parser.add_argument("--designer-send", action="store_true", help="Отправить дизайнеру в Telegram задание + исходные фото (пост следующего рабочего дня или --article N)")
    parser.add_argument("--owner-preview", action="store_true", help="Прислать ВЛАДЕЛЬЦУ в Telegram пост следующего рабочего дня на проверку (08:41 МСК)")
    parser.add_argument("--designer-collect", action="store_true", help="Забрать присланные дизайнером макеты из Telegram в social/<№>/")
    args = parser.parse_args()

    if args.yandex_market_test:
        return cmd_yandex_market_test()
    if args.yandex_market_export:
        return cmd_yandex_market_export()
    if args.push_yandex_stock_dryrun:
        return cmd_push_yandex_stock(dry_run=True)
    if args.push_yandex_stock:
        return cmd_push_yandex_stock()
    if args.push_yandex_names_dryrun:
        return cmd_push_yandex_names(dry_run=True)
    if args.push_yandex_names:
        return cmd_push_yandex_names()
    if args.push_yandex_price_dryrun:
        return cmd_push_yandex_price(dry_run=True)
    if args.push_yandex_price:
        return cmd_push_yandex_price()
    if args.build_tilda_commerceml:
        return cmd_build_tilda_commerceml()
    if args.push_tilda:
        return cmd_push_tilda()
    if args.ozon_status:
        if args.article:
            os.environ["OZON_STATUS_ARTICLES"] = args.article
        return cmd_ozon_status()
    if args.validate_master:
        return cmd_validate_master()
    if args.fix_spu_dryrun:
        return cmd_fix_spu(args.article, dry_run=True)
    if args.fix_spu:
        return cmd_fix_spu(args.article, dry_run=False)
    if args.audit:
        return cmd_audit(refresh=True)
    if args.direct_access_check:
        import direct_upload
        return direct_upload.cmd_direct_access_check()
    if args.direct_upload_draft:
        import direct_upload
        return direct_upload.cmd_direct_upload_draft()
    if args.ozon_rich_dryrun:
        import ozon_rich
        return ozon_rich.cmd_ozon_rich_dryrun()
    if args.ozon_media_diag:
        import ozon_media_diag
        return ozon_media_diag.cmd_ozon_media_diag()
    if args.ozon_content_rating:
        import ozon_content_rating
        return ozon_content_rating.cmd_ozon_content_rating()
    if args.ozon_attr_plan:
        import ozon_attrs
        return ozon_attrs.cmd_plan()
    if args.ozon_attr_apply:
        import ozon_attrs
        return ozon_attrs.cmd_apply()
    if args.avito_dupes:
        import avito_dupes
        _audit_fetch_avito()  # свежий отчёт автозагрузки + список объявлений (только чтение)
        return avito_dupes.cmd_avito_dupes()
    if args.audit_offline:
        return cmd_audit(refresh=False)
    if args.wb_create_missing_dryrun:
        return cmd_wb_create_missing(dry_run=True, article=args.article)
    if args.wb_create_missing:
        return cmd_wb_create_missing(dry_run=False, article=args.article)
    if args.align_wb_prices_dryrun:
        return cmd_align_wb_prices(dry_run=True)
    if args.align_wb_prices:
        return cmd_align_wb_prices(dry_run=False)
    if args.audit_nightly:
        return cmd_audit(refresh=True, fail_on_new=True)
    if args.split_model_dryrun:
        return cmd_split_model(args.article, dry_run=True)
    if args.split_model:
        return cmd_split_model(args.article, dry_run=False)
    if args.remove_product_dryrun:
        return cmd_remove_product(args.article, dry_run=True)
    if args.remove_product:
        return cmd_remove_product(args.article, dry_run=False)
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
    if args.ozon_warehouses:
        return cmd_ozon_warehouses()
    if args.attach_avito_photos:
        return cmd_attach_avito_photos()
    if args.build_avito_catalog:
        return cmd_build_avito_catalog()
    if args.build_tilda_catalog:
        return cmd_build_tilda_catalog()
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
    if args.download_ozon_photos:
        return cmd_download_ozon_photos()
    if args.full_sync_ozon:
        return cmd_full_sync_ozon()
    if args.build_master_control:
        return cmd_build_master_control()
    if args.sync_master_control:
        return cmd_sync_master_control()
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
    if args.build_new_product_template:
        return cmd_build_new_product_template()
    if args.push_new_product_all_dryrun:
        return cmd_push_new_product_all_dryrun()
    if args.push_new_product_all:
        return cmd_push_new_product_all()
    if args.launch_new_products:
        return cmd_launch_new_products()
    if args.finish_new_product:
        return cmd_finish_new_product()
    if args.compare_ozon_wb:
        return cmd_compare_ozon_wb()
    if args.build_wb_catalog:
        return cmd_build_wb_catalog()
    if args.attach_wb_photos:
        return cmd_attach_wb_photos()
    if args.full_sync_wb:
        return cmd_full_sync_wb()
    if args.push_wb_cards_dryrun:
        return cmd_push_wb_cards_dryrun()
    if args.push_wb_cards:
        return cmd_push_wb_cards()
    if args.attach_photos_all:
        return cmd_attach_photos_all()
    if args.push_photos_dryrun:
        return cmd_push_photos_dryrun()
    if args.push_photos:
        return cmd_push_photos()
    if args.update_photos:
        return cmd_update_photos()
    if args.update_cards:
        return cmd_update_cards()
    if args.push_wb_price_dryrun:
        return cmd_push_wb_price_dryrun()
    if args.push_wb_price:
        return cmd_push_wb_price()
    if args.sync_orders:
        return cmd_sync_orders()
    if args.sync_tilda_order:
        return cmd_sync_tilda_order()
    if args.build_kits_template:
        return cmd_build_kits_template()
    if args.recompute_kit_stock:
        return cmd_recompute_kit_stock()
    if args.push_stock_dryrun:
        return cmd_push_stock_dryrun()
    if args.push_stock:
        return cmd_push_stock()
    if args.push_wb_stock_dryrun:
        return cmd_push_wb_stock_dryrun()
    if args.push_wb_stock:
        return cmd_push_wb_stock()
    if args.fix_wb_dimensions:
        return cmd_fix_wb_dimensions()
    if args.pull_ozon_stock:
        return cmd_pull_ozon_stock()
    if args.sync_ozon_prices_dryrun:
        return cmd_sync_ozon_prices(True, args.article)
    if args.sync_ozon_prices:
        return cmd_sync_ozon_prices(False, args.article)
    if args.ozon_leave_boosting_dryrun:
        return cmd_ozon_leave_boosting(True)
    if args.ozon_leave_boosting:
        return cmd_ozon_leave_boosting(False)
    if args.disable_ozon_auto_actions_dryrun:
        return cmd_disable_ozon_auto_actions(True, args.article)
    if args.disable_ozon_auto_actions:
        return cmd_disable_ozon_auto_actions(False, args.article)
    if args.diag_tnved:
        return cmd_diag_tnved(args.article, getattr(args, "seed_phrase", ""))
    if args.diag_ozon_analytics:
        import ozon_analytics
        return ozon_analytics.run()
    if args.diag_ozon_orders:
        return cmd_diag_ozon_orders()
    if args.catalog_audit:
        import catalog_audit
        return catalog_audit.run()
    if args.diag_ozon_promos:
        import ozon_promos_diag
        return ozon_promos_diag.run()
    if args.diag_ozon_prices:
        return cmd_diag_ozon_prices(args.article)
    if args.diag_ozon_product:
        return cmd_diag_ozon_product(args.article)
    if args.diag_wb_new_cards:
        return cmd_diag_wb_new_cards()
    if args.archive_ozon_products_dryrun:
        return cmd_archive_ozon_products(args.article, dry_run=True)
    if args.archive_ozon_products:
        return cmd_archive_ozon_products(args.article, dry_run=False)
    if args.unarchive_ozon_products:
        return cmd_unarchive_ozon_products(args.article)

    if args.designer_link:
        import designer_bot
        return designer_bot.cmd_link(args.article)
    if args.designer_send_dryrun:
        import designer_bot
        return designer_bot.cmd_send(True, args.article)
    if args.designer_send:
        import designer_bot
        return designer_bot.cmd_send(False, args.article)
    if args.designer_collect:
        import designer_bot
        return designer_bot.cmd_collect()
    if args.owner_preview:
        import designer_bot
        return designer_bot.cmd_owner_preview(args.article)
    if args.social_test:
        import social_publish
        return social_publish.cmd_test()
    if args.plan_reorder or args.plan_reorder_dryrun:
        import plan_order
        return plan_order.reorder(dry_run=args.plan_reorder_dryrun)
    if args.social_status:
        import social_publish
        return social_publish.cmd_status()
    if args.store_links:
        import store_links
        return store_links.cmd_store_links()
    if args.photo_text_audit:
        import photo_text_audit
        return photo_text_audit.cmd_photo_text_audit()
    if args.price_audit:
        import price_audit
        return price_audit.cmd_price_audit()
    if args.reviews_check:
        import reviews_guard

        arts = _parse_offer_ids_arg(args.article)
        if not arts:
            import openpyxl as _ox
            _r = _ox.load_workbook(_data_path("master_control.xlsx"), read_only=True)["Товары"].iter_rows(min_row=2, values_only=True)
            arts = [str(x[0]).strip() for x in _r if x and x[0]]
        reviews_guard.check(arts)
        return 0
    if args.price_preview:
        import price_audit
        price_audit.cmd_ozon_finance()
        return price_audit.cmd_price_preview()
    if args.ozon_finance:
        import price_audit
        return price_audit.cmd_ozon_finance()
    if args.ozon_returns:
        import ozon_unit
        return ozon_unit.cmd_ozon_returns()
    if args.ozon_unit_weekly:
        import ozon_unit
        return ozon_unit.cmd_ozon_unit_weekly()
    if args.wb_zero_stock:
        return cmd_wb_zero_stock(args.article or "")
    if args.fix_partnumbers or args.fix_partnumbers_dryrun:
        import fix_partnumbers
        return fix_partnumbers.cmd_fix_partnumbers(args.fix_partnumbers_dryrun)
    if args.direct_template_dump:
        import direct_tools
        return direct_tools.cmd_direct_template_dump()
    if args.direct_xlsx:
        import direct_tools
        return direct_tools.cmd_direct_xlsx()
    if args.direct_lint:
        import direct_tools
        return direct_tools.cmd_direct_lint()
    if args.direct_check_links:
        import direct_tools
        return direct_tools.cmd_direct_check_links()
    if args.direct_report:
        import direct_tools
        return direct_tools.cmd_direct_report()
    if args.site_links:
        import site_links
        return site_links.cmd_site_links()
    if args.social_vk_feed:
        import social_publish
        return social_publish.build_vk_feed()
    if args.social_publish_dryrun:
        import social_publish
        return social_publish.cmd_publish(True, article=args.article)
    if args.social_publish:
        import social_publish
        return social_publish.cmd_publish_guarded(False, article=args.article)
    if args.social_autopublish:
        import social_publish
        return social_publish.cmd_publish_guarded(False, auto=True)

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
