"""
Точка входа. На этом первом этапе агент умеет:
  --test-ozon        проверить, что ключ Ozon работает
  --test-wb          проверить, что токен WB работает
  --fetch-ozon        выгрузить список товаров Ozon в data/ozon_products.json
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


def cmd_wb_warehouses() -> int:
    import wb_client

    warehouses = wb_client.get_warehouses()
    print(json.dumps(warehouses, ensure_ascii=False, indent=2))
    print("\nСкопируйте нужный ID в WB_WAREHOUSE_ID в .env / GitHub Secrets.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Синхронизация карточек Ozon/WB/Avito/Яндекс")
    parser.add_argument("--test-ozon", action="store_true")
    parser.add_argument("--test-wb", action="store_true")
    parser.add_argument("--fetch-ozon", action="store_true")
    parser.add_argument("--fetch-wb", action="store_true")
    parser.add_argument("--wb-warehouses", action="store_true", help="Показать склады продавца на WB (для WB_WAREHOUSE_ID)")
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

    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
