"""
Автоматическая синхронизация каталога с Тильдой через CommerceML
(раздел магазина -> Товары -> ⋯ -> "Синхронизация через CommerceML").

Тильда принимает данные в формате, который обычно шлёт 1С: внешняя система
сама ходит на "ссылку на коннектор" Тильды (логин/пароль — из того же окна)
и последовательно отправляет два файла — import.xml (товары) и offers.xml
(цены и остатки). Здесь эту роль играет бот. Порядок запросов (по справке
Тильды, раздел "Почему не выгружаются товары"), ВСЕ в одной сессии:

  1. ?type=catalog&mode=checkauth                       (авторизация, GET)
  2. ?type=catalog&mode=init                            (параметры обмена, GET)
  3. ?type=catalog&mode=file&filename=import0_1.xml        (POST, в теле — СОДЕРЖИМОЕ файла)
  4. ?type=catalog&mode=file&filename=offers0_1.xml        (POST)
  5. ?type=catalog&mode=import&filename=import0_1.xml      (импорт товаров)
  6. ?type=catalog&mode=import&filename=offers0_1.xml      (импорт цен и остатков)

ИДЕНТИФИКАТОР товара (<Ид>) = артикул (offer_id) — тот же, что мы клали в
колонку "External ID" CSV-импорта (tilda_feed.py), поэтому уже заведённые
товары должны сопоставиться с ним, а не задублироваться. Это ПРЕДПОЛОЖЕНИЕ
(Тильда не публикует правила сопоставления) — проверяется первым реальным
запуском: смотрите "История синхронизации" (Товары -> ⋯ -> Синхронизация
через CommerceML) — там видно, что именно пришло и какие ошибки.

ИСТОЧНИК ДАННЫХ — тот же, что у CSV для Тильды: data/ozon_catalog.xlsx
(название, описание, цена, остаток) + цена-оверрайд для Avito/сайта
(site_pricing.py) + обложка из photos/ по прямой ссылке raw.githubusercontent
(см. tilda_feed._cover_photo_raw_url). Остаток 0 -> в offers0_1.xml уходит
<Количество>0</Количество> (так товар на сайте становится "нет в
наличии"); пустой остаток -> элемент не передаётся (остаток на сайте не
трогаем).

Секреты (GitHub -> Settings -> Secrets): TILDA_COMMERCEML_URL (ссылка на
коннектор), TILDA_COMMERCEML_USER, TILDA_COMMERCEML_PASSWORD.
"""
import datetime
import logging
import os
import time
from typing import Dict, List, Optional, Tuple
from xml.sax.saxutils import escape

import openpyxl
import requests

logger = logging.getLogger("marketplace-agent.tilda_commerceml")

CLASSIFIER_ID = "leoparts-classifier"
CATALOG_ID = "leoparts-catalog"
GROUP_ID = "leoparts-group-parts"
GROUP_NAME = "Запчасти"
PRICE_TYPE_ID = "leoparts-price-retail"


def _x(value) -> str:
    return escape(str(value if value is not None else ""))


def _num_or_none(v) -> Optional[float]:
    try:
        if v is None or str(v).strip() == "":
            return None
        return float(str(v).replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def load_items(catalog_path: str, photos_dir: str, raw_base_url: str, price_overrides_path: Optional[str] = None) -> Tuple[List[Dict], Dict[str, List[str]]]:
    """
    Читает каталог и возвращает ([товары], {"no_price": [...], "no_photo": [...]}).
    Товар: id, title, description, price, qty (None = не передавать), photo.
    Колонки ozon_catalog.xlsx: 1 offer_id, 2 название, 3 описание, 4 цена,
    5 цена до скидки, 6 фото, 7 видео, 8 остаток.
    """
    import site_pricing
    import tilda_feed

    overrides = site_pricing.load_price_overrides(price_overrides_path or site_pricing.OVERRIDES_PATH, "site")
    ws = openpyxl.load_workbook(catalog_path, data_only=True).active

    items: List[Dict] = []
    problems: Dict[str, List[str]] = {"no_price": [], "no_photo": []}
    for row in ws.iter_rows(min_row=2, values_only=True):
        row = list(row) + [None] * 11
        if not row[0]:
            continue
        offer_id = str(row[0]).strip()
        price = overrides.get(offer_id, _num_or_none(row[3]))
        qty = _num_or_none(row[7])
        if not price or price <= 0:
            # Без цены карточку не заводим; если она уже есть на сайте — остаток всё равно
            # можно обнулить (см. ниже), поэтому передаём как "только остаток".
            problems["no_price"].append(offer_id)
            if qty is not None:
                items.append({"id": offer_id, "title": str(row[1] or offer_id), "description": "", "price": None, "qty": qty, "photo": ""})
            continue
        photo = tilda_feed._cover_photo_raw_url(photos_dir, offer_id, raw_base_url) if photos_dir else ""
        if not photo:
            problems["no_photo"].append(offer_id)
        photo_files = _local_photo_files(photos_dir, offer_id) if photos_dir else []
        items.append(
            {
                "id": offer_id,
                "title": str(row[1] or "").strip() or offer_id,
                "description": tilda_feed._clean_description(row[2] or ""),
                "price": int(price),
                "qty": qty,
                "photo": photo,
                "photo_files": photo_files,
            }
        )
    try:
        import exchange_items
        for it in items:  # 29.10: обмен = возврат денег — пояснение в начале описания основной карточки
            pre = exchange_items.site_prefix(it["id"])
            if pre and pre not in (it.get("description") or ""):
                it["description"] = pre + "<br/><br/>" + (it.get("description") or "")
        items += exchange_items.site_items(items)  # карточки «по обмену» (выключены, site_card: false)
    except Exception as exc:  # noqa: BLE001 — обмен не должен ломать синхронизацию
        logger.warning("Карточки по обмену не собраны: %s", exc)
    return items, problems


# ---------------------------------------------------------------------------
# ФОТО НА ТИЛЬДУ (добавлено 2026-10-28).
# Раньше в <Картинка> уходила ссылка raw.githubusercontent — Тильда её не берёт:
# новые товары (HXF, плата F, карточки «по обмену») появились на сайте без фото.
# По протоколу CommerceML картинка — это ФАЙЛ, загруженный в той же сессии
# (mode=file, filename=import_files/...), а в <Картинка> — его относительный путь.
# В Тильде должна быть включена галочка «Принимать изображения»
# (Синхронизация через CommerceML -> Дополнительные настройки).
#
# Фото шлём ТОЛЬКО новым товарам, и только ОДИН РАЗ:
#   - товар уже виден на сайте (data/site_links.json) -> фото не трогаем
#     (там фото, загруженные вручную/дизайнером);
#   - товар уже получал фото от нас (data/tilda_images_sent.json) -> не шлём повторно
#     (иначе Тильда может задублировать картинки).
# Чтобы отправить фото товару заново — удалите его артикул из tilda_images_sent.json.
# Остальным товарам <Картинка> не передаётся вовсе (МойСклад делает так же —
# шлёт только ещё не отправленные изображения; уже загруженные на сайте остаются).
# ---------------------------------------------------------------------------
IMAGES_SENT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tilda_images_sent.json")
SITE_LINKS_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "site_links.json")
MAX_PHOTOS = 5
# Выключатель: фото шлём, только если этот файл есть (его создают после того, как в Тильде
# включена галочка «Принимать изображения»; иначе фото ушли бы впустую и пометились как отправленные).
IMAGES_ON_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "tilda_images_on.txt")


def _local_photo_files(photos_dir: str, offer_id: str) -> List[str]:
    import catalog_editor

    files = catalog_editor.own_media_files(photos_dir, offer_id, catalog_editor.IMAGE_EXTS)
    return [catalog_editor.media_path(photos_dir, offer_id, f) for f in files][:MAX_PHOTOS]


def _load_json(path: str, default):
    import json

    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def plan_images(items: List[Dict], sent_path: str = IMAGES_SENT_PATH, site_links_path: str = SITE_LINKS_PATH) -> Dict[str, List[Tuple[str, str]]]:
    """
    {offer_id: [(путь на диске, имя в import_files/...), ...]} — кому в этом запуске шлём фото.
    Если site_links.json не прочитался — фото не шлём никому (не знаем, что уже на сайте).
    """
    import hashlib

    if not os.path.exists(IMAGES_ON_PATH):
        return {}
    site = _load_json(site_links_path, None)
    if not isinstance(site, dict) or not site:
        logger.warning("site_links.json пуст/не читается — фото на Тильду в этом запуске не шлём")
        return {}
    sent = set(_load_json(sent_path, []))
    plan: Dict[str, List[Tuple[str, str]]] = {}
    for it in items:
        oid = it["id"]
        files = it.get("photo_files") or []
        if it.get("price") is None or not files or oid in sent or oid.lower() in site:
            continue
        uid = hashlib.md5(oid.encode("utf-8")).hexdigest()[:12]
        plan[oid] = [
            (path, f"import_files/{uid}_{n}{os.path.splitext(path)[1].lower()}") for n, path in enumerate(files, 1)
        ]
    return plan


def mark_images_sent(ids: List[str], sent_path: str = IMAGES_SENT_PATH) -> None:
    import json

    sent = set(_load_json(sent_path, [])) | set(ids)
    with open(sent_path, "w", encoding="utf-8") as f:
        json.dump(sorted(sent), f, ensure_ascii=False, indent=1)


def _image_bytes(path: str, limit: int) -> Tuple[bytes, str]:
    """Байты файла; если больше лимита Тильды — пережимаем в JPEG (белый фон вместо прозрачности)."""
    with open(path, "rb") as f:
        data = f.read()
    if len(data) <= limit:
        return data, ""
    import io

    from PIL import Image

    im = Image.open(io.BytesIO(data))
    if im.mode in ("RGBA", "LA", "P"):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        im = bg
    else:
        im = im.convert("RGB")
    for side, q in ((2000, 88), (1600, 85), (1200, 80)):
        im2 = im.copy()
        im2.thumbnail((side, side))
        buf = io.BytesIO()
        im2.save(buf, "JPEG", quality=q, optimize=True)
        if buf.tell() <= limit:
            return buf.getvalue(), ".jpg"
    return buf.getvalue(), ".jpg"


def build_import_xml(items: List[Dict], images: Optional[Dict[str, List[str]]] = None) -> str:
    """import0_1.xml — состав каталога (товары, названия, описания, картинки)."""
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<КоммерческаяИнформация ВерсияСхемы="2.05" ДатаФормирования="{now}">',
        "<Классификатор>",
        f"<Ид>{CLASSIFIER_ID}</Ид><Наименование>Классификатор</Наименование>",
        f"<Группы><Группа><Ид>{GROUP_ID}</Ид><Наименование>{_x(GROUP_NAME)}</Наименование></Группа></Группы>",
        "</Классификатор>",
        "<Каталог>",
        f"<Ид>{CATALOG_ID}</Ид><ИдКлассификатора>{CLASSIFIER_ID}</ИдКлассификатора>",
        "<Наименование>Каталог</Наименование><Товары>",
    ]
    for it in items:
        if it["price"] is None:
            continue  # карточку без цены не создаём/не обновляем
        parts.append("<Товар>")
        parts.append(f"<Ид>{_x(it['id'])}</Ид>")
        parts.append(f"<Артикул>{_x(it['id'])}</Артикул>")
        parts.append(f"<Наименование>{_x(it['title'])}</Наименование>")
        parts.append("<БазоваяЕдиница Код=\"796\" НаименованиеПолное=\"Штука\" МеждународноеСокращение=\"PCE\">шт</БазоваяЕдиница>")
        parts.append(f"<Группы><Ид>{GROUP_ID}</Ид></Группы>")
        if it["description"]:
            parts.append(f"<Описание>{_x(it['description'])}</Описание>")
        for name in (images or {}).get(it["id"], []):
            parts.append(f"<Картинка>{_x(name)}</Картинка>")
        parts.append("</Товар>")
    parts += ["</Товары>", "</Каталог>", "</КоммерческаяИнформация>"]
    return "\n".join(parts)


def build_offers_xml(items: List[Dict]) -> str:
    """offers0_1.xml — цены и остатки."""
    now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<КоммерческаяИнформация ВерсияСхемы="2.05" ДатаФормирования="{now}">',
        "<ПакетПредложений СодержитТолькоИзменения=\"false\">",
        f"<Ид>{CATALOG_ID}#offers</Ид><Наименование>Пакет предложений</Наименование>",
        f"<ИдКаталога>{CATALOG_ID}</ИдКаталога><ИдКлассификатора>{CLASSIFIER_ID}</ИдКлассификатора>",
        f"<ТипыЦен><ТипЦены><Ид>{PRICE_TYPE_ID}</Ид><Наименование>Розничная</Наименование></ТипЦены></ТипыЦен>",
        "<Предложения>",
    ]
    for it in items:
        parts.append("<Предложение>")
        parts.append(f"<Ид>{_x(it['id'])}</Ид>")
        parts.append(f"<Артикул>{_x(it['id'])}</Артикул>")
        parts.append(f"<Наименование>{_x(it['title'])}</Наименование>")
        parts.append("<БазоваяЕдиница Код=\"796\" НаименованиеПолное=\"Штука\" МеждународноеСокращение=\"PCE\">шт</БазоваяЕдиница>")
        if it["price"] is not None:
            parts.append(
                f"<Цены><Цена><ИдТипаЦены>{PRICE_TYPE_ID}</ИдТипаЦены><ЦенаЗаЕдиницу>{it['price']}</ЦенаЗаЕдиницу>"
                "<Валюта>RUB</Валюта><Единица>шт</Единица><Коэффициент>1</Коэффициент></Цена></Цены>"
            )
        if it["qty"] is not None:
            parts.append(f"<Количество>{int(it['qty'])}</Количество>")
        parts.append("</Предложение>")
    parts += ["</Предложения>", "</ПакетПредложений>", "</КоммерческаяИнформация>"]
    return "\n".join(parts)


def file_limit_from_init(text: str, default: int = 3 * 1024 * 1024) -> int:
    for part in text.replace("\n", "|").split("|"):
        part = part.strip()
        if part.startswith("file_limit="):
            try:
                return int(part.split("=", 1)[1])
            except ValueError:
                pass
    return default


def _url(base: str, **params) -> str:
    sep = "&" if "?" in base else "?"
    q = "&".join(f"{k}={v}" for k, v in params.items())
    return f"{base}{sep}{q}"


def _check(resp: requests.Response, step: str) -> str:
    """Протокол CommerceML: успех — тело начинается со 'success'; иначе — 'failure' + текст."""
    text = resp.text.strip()
    if resp.status_code != 200:
        raise RuntimeError(f"{step}: HTTP {resp.status_code}: {text[:300]}")
    first = text.splitlines()[0].strip().lower() if text else ""
    if first.startswith("failure") or first.startswith("error"):
        raise RuntimeError(f"{step}: Тильда ответила отказом: {text[:500]}")
    return text


def push(
    base_url: str,
    user: str,
    password: str,
    import_xml: str,
    offers_xml: str,
    timeout: int = 120,
    image_files: Optional[List[Tuple[str, bytes]]] = None,
) -> List[str]:
    """
    Отправляет два файла по протоколу CommerceML (см. docstring модуля).
    Возвращает короткий журнал по шагам; при ошибке на любом шаге — исключение
    RuntimeError с ответом Тильды.
    """
    log: List[str] = []
    s = requests.Session()
    s.auth = (user, password)

    r = s.get(_url(base_url, type="catalog", mode="checkauth"), timeout=timeout)
    log.append(f"checkauth: {_check(r, 'checkauth').splitlines()[0]}")
    # 1С после checkauth передаёт cookie из ответа (строки 2-3: имя и значение) — requests.Session
    # сам хранит Set-Cookie; на случай, если сервер вернул cookie только в теле — добавим вручную.
    lines = r.text.strip().splitlines()
    if len(lines) >= 3 and lines[1].strip() and lines[2].strip() and lines[1].strip() not in s.cookies:
        s.cookies.set(lines[1].strip(), lines[2].strip())

    r = s.get(_url(base_url, type="catalog", mode="init"), timeout=timeout)
    log.append(f"init: {_check(r, 'init').replace(chr(10), ' | ')}")

    # Картинки — до XML (как 1С): import_files/... в той же сессии
    for name, data in image_files or []:
        r = s.post(
            _url(base_url, type="catalog", mode="file", filename=name),
            data=data,
            headers={"Content-Type": "application/octet-stream"},
            timeout=timeout,
        )
        _check(r, "file " + name)
    if image_files:
        log.append(f"фото: отправлено файлов {len(image_files)}")

    for name, body in (("import0_1.xml", import_xml), ("offers0_1.xml", offers_xml)):
        r = s.post(
            _url(base_url, type="catalog", mode="file", filename=name),
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/octet-stream"},
            timeout=timeout,
        )
        log.append(f"file {name}: {_check(r, 'file ' + name).splitlines()[0]}")

    for name in ("import0_1.xml", "offers0_1.xml"):
        # импорт может идти порциями: пока ответ "progress" — повторяем (стандарт 1С-протокола)
        # Раньше опрашивали 60 раз подряд без паузы (~1 мин) — на большом каталоге Тильда не успевала
        # (сбой 27.10). Теперь с паузой: 3 с, растёт до 10 с, всего до ~6 минут.
        deadline = time.monotonic() + 360
        pause = 3.0
        while True:
            r = s.get(_url(base_url, type="catalog", mode="import", filename=name), timeout=timeout)
            text = _check(r, "import " + name)
            first = text.splitlines()[0].strip().lower()
            if first != "progress":
                log.append(f"import {name}: {text.splitlines()[0]}")
                break
            if time.monotonic() > deadline:
                raise RuntimeError(f"import {name}: Тильда всё ещё отвечает 'progress' спустя 6 минут")
            time.sleep(pause)
            pause = min(pause * 1.5, 10.0)
    return log
