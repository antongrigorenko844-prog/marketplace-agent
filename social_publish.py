"""
Автопубликация постов из data/content_plan.xlsx в VK и Instagram.

КАК ЭТО РАБОТАЕТ
- Тексты (подпись + хэштеги) берутся из data/content_plan.xlsx, лист «Контент-план»
  (колонки «№», «Дата выхода», «Подпись к посту», «Хэштеги»).
- Картинки: если дизайнер прислала свои — social/<№ поста>/1.png, 2.png, ... (до 10 шт.;
  порядок по номеру в имени файла). Нет картинок — пост пропускается, ничего не ломается.
- За один запуск публикуется не больше одного поста на площадку (SOCIAL_MAX_PER_RUN) —
  самый ранний по дате среди тех, у кого «Дата выхода» уже наступила и картинки готовы.
- Что уже опубликовано, запоминается в data/social_state.json — повторно не уйдёт.
- Чтобы остановить пост, впишите его номер в data/social_hold.txt (автопубликация его пропустит).

СЕКРЕТЫ (GitHub -> Settings -> Secrets and variables -> Actions):
  VK_TOKEN, VK_GROUP_ID                — ВКонтакте (токен сообщества или пользователя
                                         с правами wall, photos; ID группы — число без минуса)
  IG_USER_ID, IG_ACCESS_TOKEN          — Instagram (бизнес-аккаунт; Graph API)
  SOCIAL_AUTOPUBLISH = 1               — включает ЕЖЕДНЕВНУЮ автопубликацию по расписанию
                                         (без него по расписанию ничего не уходит, только ручной запуск)
Площадка без своих секретов просто пропускается.
"""
import json
import logging
import mimetypes
import os
import re
import tempfile
import time
import datetime as dt
from typing import Dict, List, Optional

import requests

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
PLAN_PATH = os.path.join(_ROOT, "data", "content_plan.xlsx")
STATE_PATH = os.path.join(_ROOT, "data", "social_state.json")
HOLD_PATH = os.path.join(_ROOT, "data", "social_hold.txt")  # номера постов через запятую/с новой строки — их НЕ публикуем
IMAGES_DIR = os.path.join(_ROOT, "social")
TEXTS_DIR = os.path.join(_ROOT, "data", "post_texts")  # итоговый текст поста от дизайнера: data/post_texts/<№>.txt (если есть — вместо подписи+хэштегов из плана)
SHEET = "Контент-план"

VK_API = os.getenv("VK_API_BASE", "https://api.vk.ru/method")  # VK с осени 2025 принимает API на домене vk.ru
VK_VERSION = "5.199"
IG_BASE = os.getenv("IG_GRAPH_BASE", "https://graph.instagram.com/v21.0")
IMG_EXT = (".png", ".jpg", ".jpeg", ".webp")
MSK = dt.timezone(dt.timedelta(hours=3))


class SocialError(RuntimeError):
    pass


# --------------------------------------------------------------------------- план
def _today() -> dt.date:
    return dt.datetime.now(MSK).date()


def load_plan() -> List[dict]:
    import openpyxl
    wb = openpyxl.load_workbook(PLAN_PATH, data_only=True)
    ws = wb[SHEET]
    head = [str(c.value or "").strip() for c in ws[1]]
    idx = {h: i for i, h in enumerate(head)}
    need = ["№", "Дата выхода", "Подпись к посту", "Хэштеги"]
    for n in need:
        if n not in idx:
            raise SocialError(f"В {PLAN_PATH} нет колонки «{n}»")
    posts = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[idx["№"]] is None:
            continue
        d = row[idx["Дата выхода"]]
        if isinstance(d, dt.datetime):
            d = d.date()
        posts.append({
            "n": int(row[idx["№"]]),
            "date": d,
            "title": str(row[idx.get("Заголовок на макете", 0)] or ""),
            "caption": str(row[idx["Подпись к посту"]] or ""),
            "tags": str(row[idx["Хэштеги"]] or ""),
            "design": str(row[idx["Статус дизайна"]] or "") if "Статус дизайна" in idx else "",
            "arts": [a.strip() for a in str(row[idx["Артикулы в master"]] or "").split(",") if a.strip() and a.strip() != "—"] if "Артикулы в master" in idx else [],
        })
    return posts


def load_state() -> dict:
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"posts": {}}


def save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    tmp = STATE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, STATE_PATH)


def _natural(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


PHOTOS_DIR = os.path.join(_ROOT, "photos")


def auto_images(arts: List[str]) -> List[str]:
    """Готовые картинки из каталога (photos/<артикул>/): для одного товара — все его картинки,
    для карусели из нескольких товаров — главная картинка каждого. Не больше 10."""
    if not arts or not os.path.isdir(PHOTOS_DIR):
        return []
    folders = {d.lower(): d for d in os.listdir(PHOTOS_DIR)}
    per = []
    for a in arts:
        d = folders.get(a.lower())
        if not d:
            continue
        full = os.path.join(PHOTOS_DIR, d)
        files = [f for f in os.listdir(full) if f.lower().endswith(IMG_EXT) and not f.startswith(".")]
        per.append([os.path.join(full, f) for f in sorted(files, key=_natural)])
    per = [x for x in per if x]
    if not per:
        return []
    if len(per) == 1:
        return _dedupe(per[0])[:10]
    mains = _dedupe([x[0] for x in per])
    if len(mains) < len(per):
        # несколько карточек одного и того же товара (одинаковые главные фото) — показываем все разные фото
        return _dedupe([f for x in per for f in x])[:10]
    return mains[:10]


def _dedupe(paths: List[str]) -> List[str]:
    """Убирает одинаковые по содержимому картинки (одно и то же фото в папках разных карточек)."""
    import hashlib
    seen, out = set(), []
    for pth in paths:
        try:
            with open(pth, "rb") as fh:
                h = hashlib.md5(fh.read()).hexdigest()
        except OSError:
            continue
        if h not in seen:
            seen.add(h)
            out.append(pth)
    return out


_NO_VIDEO = None


def _video_slides() -> set:
    """Картинки с надписью «смотри видео» (по распознаванию, data/photo_text_audit.xlsx) — в посты их не берём:
    видео в посте нет. В карточках маркетплейсов они остаются (там видео может быть)."""
    global _NO_VIDEO
    if _NO_VIDEO is None:
        _NO_VIDEO = set()
        try:
            import openpyxl
            ws = openpyxl.load_workbook(os.path.join(_ROOT, "data", "photo_text_audit.xlsx"), read_only=True).active
            for r in ws.iter_rows(min_row=2, values_only=True):
                if r[3] and "видео" in str(r[3]) and r[0] == "photos":
                    _NO_VIDEO.add(os.path.join(PHOTOS_DIR, str(r[1]), str(r[2])))
        except Exception:  # noqa
            pass
    return _NO_VIDEO


def post_images(n: int, arts: Optional[List[str]] = None) -> List[str]:
    """Картинки, присланные дизайнером (social/<№>/), иначе — готовые из каталога (по артикулам поста)."""
    d = os.path.join(IMAGES_DIR, str(n))
    if os.path.isdir(d):
        files = [f for f in os.listdir(d) if f.lower().endswith(IMG_EXT) and not f.startswith(".")]
        if files:
            return [os.path.join(d, f) for f in sorted(files, key=_natural)][:10]
    imgs = auto_images(arts or [])
    clean = [x for x in imgs if x not in _video_slides()]
    return clean or imgs


def final_text_path(n: int) -> str:
    return os.path.join(TEXTS_DIR, f"{n}.txt")


# --------------------------------------------------------------------------- где купить (концовка поста)
# Строка артикулов площадок добавляется к товарным постам автоматически (Ozon -> WB -> Маркет; площадки,
# где товара нет, не показываются). В VK перед ней — ссылка на товар на сайте, если задан SITE_PRODUCT_URL
# (шаблон с {uid} — Tilda UID товара, {offer} — наш артикул). В Instagram ссылок нет (некликабельны в подписи).
SITE_PRODUCT_URL = os.getenv("SITE_PRODUCT_URL", "").strip()
UTM = "utm_source={src}&utm_medium=social&utm_campaign=post{n}"
_IDS = None


def _marketplace_ids() -> Dict[str, dict]:
    """offer_id (в нижнем регистре) -> {"ozon": sku, "wb": nmID, "ym": артикул Маркета}."""
    global _IDS
    if _IDS is not None:
        return _IDS
    ids: Dict[str, dict] = {}
    try:
        with open(os.path.join(_ROOT, "data", "ozon_products.json"), "r", encoding="utf-8") as f:
            for it in json.load(f).get("list", []):
                if it.get("sku") and not it.get("archived"):
                    ids.setdefault(str(it["offer_id"]).lower(), {})["ozon"] = it["sku"]
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        pass
    try:
        with open(os.path.join(_ROOT, "data", "wb_cards.json"), "r", encoding="utf-8") as f:
            for c in json.load(f).get("cards", []):
                if c.get("nmID") and c.get("vendorCode"):
                    ids.setdefault(str(c["vendorCode"]).lower(), {})["wb"] = c["nmID"]
    except (FileNotFoundError, json.JSONDecodeError, AttributeError):
        pass
    try:
        import openpyxl
        ws = openpyxl.load_workbook(os.path.join(_ROOT, "data", "yandex_market_offers.xlsx"), read_only=True, data_only=True).worksheets[0]
        rows = ws.iter_rows(values_only=True)
        head = [str(h or "") for h in next(rows)]
        i_sku, i_ym, i_arch = head.index("SKU (ваш артикул)"), head.index("Артикул Маркета"), head.index("В архиве")
        for r in rows:
            v = r[i_ym]
            if isinstance(v, float):  # старый файл: номер сохранён числом с потерей точности — не показываем
                v = int(v) if v.is_integer() and v < 1e15 else None
            if v and str(v).strip().isdigit() and int(str(v).strip()) > 2 ** 53:
                v = None  # такие номера Маркет отдаёт уже округлёнными (кратны 32) — точный номер неизвестен, не показываем
            if r[i_sku] and v and str(r[i_arch] or "").lower() != "да":
                ids.setdefault(str(r[i_sku]).lower(), {})["ym"] = str(v).strip()
    except Exception:  # noqa — файла может не быть
        pass
    _IDS = ids
    return ids


def _site_url(p: dict, src: str) -> str:
    """Ссылка на карточку товара на сайте (data/site_links.json, собирается командой site-links) + UTM-метка."""
    import site_links
    links = site_links.load()
    for a in p.get("arts") or []:
        url = links.get(a.lower())
        if url:
            return url + ("&" if "?" in url else "?") + UTM.format(src=src, n=p["n"])
    arts = p.get("arts") or []
    if arts and SITE_PRODUCT_URL:
        url = SITE_PRODUCT_URL.format(uid=_tilda_uid(arts[0]), offer=arts[0])
        return url + ("&" if "?" in url else "?") + UTM.format(src=src, n=p["n"])
    return ""


def _tilda_uid(offer: str) -> str:
    import tilda_feed
    return tilda_feed._stable_uid(offer)


# Цена в посте подставляется в момент публикации (всегда актуальная): маркетплейсы — из master_control,
# сайт — из data/tilda_feed.csv. Для дорогих товаров (мехатроники, платы: от PRICE_HIDE_FROM ₽) цену не пишем —
# исследования показывают, что цена «первой» у дорогих покупок переключает внимание с ценности на «дорого».
PRICE_HIDE_FROM = int(os.getenv("POST_PRICE_HIDE_FROM", "50000") or 50000)
_PRICES = None


def _prices():
    global _PRICES
    if _PRICES is not None:
        return _PRICES
    mp, site = {}, {}
    try:
        import openpyxl
        ws = openpyxl.load_workbook(os.path.join(_ROOT, "data", "master_control.xlsx"), read_only=True, data_only=True).worksheets[0]
        rows = ws.iter_rows(values_only=True)
        head = [str(h or "") for h in next(rows)]
        i_a, i_p = head.index("Артикул (offer_id)"), head.index("Цена, ₽")
        for r in rows:
            try:
                if r[i_a] and r[i_p]:
                    mp[str(r[i_a]).lower()] = int(float(r[i_p]))
            except (TypeError, ValueError):
                pass
    except Exception:  # noqa
        pass
    try:  # живые цены с сайта (собирает site-links перед публикацией)
        with open(os.path.join(_ROOT, "data", "site_prices.json"), "r", encoding="utf-8") as fh:
            site = {k: int(v) for k, v in json.load(fh).items()}
    except (FileNotFoundError, json.JSONDecodeError, ValueError):
        site = {}
    if not site:
      try:
        import csv
        with open(os.path.join(_ROOT, "data", "tilda_feed.csv"), "r", encoding="utf-8-sig") as fh:
            rd = csv.reader(fh, delimiter=";")
            head = next(rd)
            i_s, i_p = head.index("SKU"), head.index("Price")
            for r in rd:
                try:
                    site[r[i_s].lower()] = int(float(r[i_p]))
                except (IndexError, ValueError):
                    pass
      except Exception:  # noqa
        pass
    _PRICES = (mp, site)
    return _PRICES


def _rub(v: int) -> str:
    return f"{v:,}".replace(",", " ") + " ₽"


def price_line(p: dict, platform: str) -> str:
    arts = [a.lower() for a in (p.get("arts") or [])]
    if not arts:
        return ""
    mp, site = _prices()
    m = [mp[a] for a in arts if a in mp]
    if not m or max(m) >= PRICE_HIDE_FROM:
        return ""
    s_ = [site[a] for a in arts if a in site]
    pre = "от " if len(set(m)) > 1 else ""
    line = f"💰 Цена: {pre}{_rub(min(m))} на маркетплейсах"
    if platform == "vk" and s_ and min(s_) < min(m):
        line += f" · на нашем сайте {('от ' if len(set(s_)) > 1 else '')}{_rub(min(s_))}"
    return line


def buy_block(p: dict, platform: str) -> str:
    """Одна строка артикулов: для каждой площадки — первый товар поста, который там есть
    (в каруселях несколько карточек часто одного и того же товара — не путаем покупателя вариантами)."""
    arts = p.get("arts") or []
    if not arts:
        return ""
    ids = _marketplace_ids()
    parts = []
    for k, name in (("ozon", "Ozon"), ("wb", "WB"), ("ym", "Маркет")):
        v = next((ids[a.lower()][k] for a in arts if ids.get(a.lower(), {}).get(k)), None)
        if v:
            parts.append(f"{name} {v}")
    out = []
    pl = price_line(p, platform)
    if pl:
        out.append(pl)
    url = _site_url(p, "vk") if platform == "vk" else ""
    if url:
        out.append(f"🛒 На сайте дешевле, чем на маркетплейсах: {url}")
    if parts:
        out.append("Артикулы: " + " · ".join(parts))
    return "\n".join(out)


def _with_buy(text: str, p: dict, platform: Optional[str]) -> str:
    """Вставляет блок «где купить» перед хэштегами (или в конец)."""
    if not platform:
        return text
    if platform == "vk":  # подписи писались под Instagram — во VK «шапки профиля» нет
        text = text.replace("в шапке профиля", "в описании сообщества")
    block = buy_block(p, platform)
    if not block or "Ozon " in text:
        return text
    lines = text.rstrip().split("\n")
    k = len(lines)
    while k > 0 and (lines[k - 1].strip().startswith("#") or not lines[k - 1].strip()):
        k -= 1
    if k == len(lines):
        return text.rstrip() + "\n\n" + block
    return "\n".join(lines[:k]).rstrip() + "\n\n" + block + "\n\n" + "\n".join(lines[k:]).strip()


def _cut(text: str, limit: Optional[int]) -> str:
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


def full_text(p: dict, limit: Optional[int] = None, platform: Optional[str] = None) -> str:
    """Если дизайнер прислал итоговый текст (data/post_texts/<№>.txt) — берём его целиком (хэштеги уже внутри).
    Иначе — подпись + хэштеги из плана. platform="vk"/"ig" — добавить блок «где купить»."""
    try:
        with open(final_text_path(p["n"]), "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            return _cut(_with_buy(text, p, platform), limit)
    except FileNotFoundError:
        pass
    text = p["caption"].strip()
    tags = p["tags"].strip()
    if tags:
        text += "\n\n" + tags
    return _cut(_with_buy(text, p, platform), limit)


# --------------------------------------------------------------------------- VK
# Два вида токена. VK_GROUP_TOKEN — ключ сообщества (Управление → Работа с API): бессрочный, не привязан к
# человеку и IP; фото загружаются через сервер сообщений (getWallUploadServer с ключом сообщества VK не даёт).
# VK_TOKEN — токен пользователя (старый путь). Если задан ключ сообщества — используется он.
def _vk_group_token() -> str:
    return os.getenv("VK_GROUP_TOKEN", "").strip()


def _vk_cfg():
    token = _vk_group_token() or os.getenv("VK_TOKEN", "").strip()
    gid = os.getenv("VK_GROUP_ID", "").strip().lstrip("-")
    return (token, gid) if token and gid else None


# VK не пускает запросы с ключом из зарубежных дата-центров (серверы GitHub получают «Flood control»),
# поэтому запросы идут через функцию в Яндекс Облаке (российский адрес): секреты VK_PROXY_URL, VK_PROXY_KEY.
# Код функции — vk_proxy/index.py. Загрузка самих файлов на upload_url идёт напрямую (без ключа VK пускает).
def _vk_proxy():
    url = os.getenv("VK_PROXY_URL", "").strip()
    key = os.getenv("VK_PROXY_KEY", "").strip()
    return (url, key) if url and key else None


def _vk(method: str, **params) -> dict:
    cfg = _vk_cfg()
    params.update({"access_token": cfg[0], "v": VK_VERSION})
    proxy = _vk_proxy()
    if proxy:
        r = requests.post(proxy[0], json={"method": method, "params": params},
                          headers={"X-Proxy-Key": proxy[1]}, timeout=60)
        if r.status_code != 200:
            raise SocialError(f"VK-посредник (Яндекс Облако): HTTP {r.status_code} {r.text[:200]}")
    else:
        r = requests.post(f"{VK_API}/{method}", data=params, timeout=60)
    data = r.json()
    if "error" in data:
        e = data["error"]
        raise SocialError(f"VK {method}: [{e.get('error_code')}] {e.get('error_msg')}")
    return data["response"]


def vk_publish(p: dict, images: List[str]) -> str:
    _, gid = _vk_cfg()
    atts = []
    group_mode = bool(_vk_group_token())
    for path in images:
        if group_mode:
            srv = _vk("photos.getMessagesUploadServer", peer_id=0)
            with open(path, "rb") as f:
                ct = mimetypes.guess_type(path)[0] or "image/png"
                up = requests.post(srv["upload_url"], files={"photo": (os.path.basename(path), f, ct)}, timeout=120).json()
            if not up.get("photo") or up.get("photo") == "[]":
                raise SocialError(f"VK не принял файл {path}: {up}")
            saved = _vk("photos.saveMessagesPhoto", photo=up["photo"], server=up["server"], hash=up["hash"])[0]
            atts.append(f"photo{saved['owner_id']}_{saved['id']}" + (f"_{saved['access_key']}" if saved.get("access_key") else ""))
            continue
        srv = _vk("photos.getWallUploadServer", group_id=gid)
        with open(path, "rb") as f:
            ct = mimetypes.guess_type(path)[0] or "image/png"
            up = requests.post(srv["upload_url"], files={"photo": (os.path.basename(path), f, ct)}, timeout=120).json()
        if not up.get("photo") or up.get("photo") == "[]":
            raise SocialError(f"VK не принял файл {path}: {up}")
        saved = _vk("photos.saveWallPhoto", group_id=gid, photo=up["photo"], server=up["server"], hash=up["hash"])
        atts.append(f"photo{saved[0]['owner_id']}_{saved[0]['id']}")
    res = _vk("wall.post", owner_id=f"-{gid}", from_group=1, message=full_text(p, 15000, "vk"), attachments=",".join(atts))
    return str(res.get("post_id"))


def vk_test() -> bool:
    if not _vk_cfg():
        print("VK: VK_TOKEN / VK_GROUP_ID не заданы — пропуск")
        return False
    _, gid = _vk_cfg()
    try:  # диагностика: запрос БЕЗ токена — если и он «Flood control», VK режет сам адрес сервера (IP), а не ключ
        r = requests.get(f"{VK_API}/utils.getServerTime", params={"v": VK_VERSION}, timeout=30).json()
        print("VK: запрос без токена — " + ("OK" if "response" in r else f"ОШИБКА {r.get('error', {}).get('error_code')} {r.get('error', {}).get('error_msg')}"))
        ip = requests.get("https://api.ipify.org", timeout=15).text.strip()
        print(f"VK: IP сервера GitHub — {ip}")
    except Exception as exc:  # noqa
        print(f"VK: диагностика без токена не удалась: {exc}")
    # диагностика: тот же ключ разными способами (GET/POST, с браузерным User-Agent и без)
    tok = _vk_cfg()[0]
    UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
    for label, fn in (
        ("GET, обычный", lambda: requests.get(f"{VK_API}/groups.getById", params={"v": VK_VERSION, "access_token": tok}, timeout=30)),
        ("GET, браузер", lambda: requests.get(f"{VK_API}/groups.getById", params={"v": VK_VERSION, "access_token": tok}, headers={"User-Agent": UA}, timeout=30)),
        ("POST, браузер", lambda: requests.post(f"{VK_API}/groups.getById", data={"v": VK_VERSION, "access_token": tok}, headers={"User-Agent": UA}, timeout=30)),
        ("POST, ключ в заголовке Authorization: Bearer", lambda: requests.post(f"{VK_API}/groups.getById", data={"v": VK_VERSION}, headers={"Authorization": f"Bearer {tok}", "User-Agent": UA}, timeout=30)),
        ("GET, ключ в заголовке Authorization: Bearer", lambda: requests.get(f"{VK_API}/groups.getById", params={"v": VK_VERSION}, headers={"Authorization": f"Bearer {tok}"}, timeout=30)),
        ("GET, браузер, api.vk.com", lambda: requests.get("https://api.vk.com/method/groups.getById", params={"v": VK_VERSION, "access_token": tok}, headers={"User-Agent": UA}, timeout=30)),
    ):
        try:
            j = fn().json()
            print(f"VK: проба [{label}] — " + ("OK" if "response" in j else f"ошибка {j['error'].get('error_code')} {j['error'].get('error_msg')}"))
        except Exception as exc:  # noqa
            print(f"VK: проба [{label}] — исключение {exc}")
    print("VK: запросы идут " + ("через посредника в Яндекс Облаке" if _vk_proxy() else "напрямую с серверов GitHub (посредник не задан)"))
    print("VK: используется " + ("ключ сообщества (VK_GROUP_TOKEN)" if _vk_group_token() else "токен пользователя (VK_TOKEN) — VK_GROUP_TOKEN не найден"))
    if _vk_group_token():
        ok = True
        for label, method, kw in (("загрузка фото", "photos.getMessagesUploadServer", {"peer_id": 0}),
                                  ("стена (чтение)", "wall.get", {"owner_id": f"-{gid}", "count": 1}),
                                  ("сообщество", "groups.getById", {})):
            try:
                res = _vk(method, **kw)
                extra = ""
                if method == "groups.getById":
                    g = res["groups"][0] if isinstance(res, dict) and "groups" in res else res[0]
                    extra = f": «{g.get('name')}» (id {g.get('id')})"
                print(f"VK: {label} — OK{extra}")
            except SocialError as exc:
                print(f"VK: {label} — ОШИБКА {exc}")
                ok = False
        if not ok:
            raise SocialError("часть проверок VK не прошла (см. выше)")
        return True
    info = _vk("groups.getById", group_id=gid)
    g = info["groups"][0] if isinstance(info, dict) and "groups" in info else info[0]
    print(f"VK: токен работает, сообщество «{g.get('name')}» (id {gid})")
    _vk("photos.getWallUploadServer", group_id=gid)
    print("VK: право загружать фото на стену — есть")
    return True


# --------------------------------------------------------------------------- Instagram
# ---- продление токена Instagram (живёт 60 дней; продлевается не раньше чем через 24 ч после выдачи)
# Раз в IG_REFRESH_DAYS дней бот продлевает токен. Если Instagram выдал НОВУЮ строку токена, она хранится
# зашифрованной в data/ig_token.enc (ключ — секрет IG_TOKEN_KEY, любая длинная строка); в открытом виде
# токен в репозиторий не попадает. Срок действия — в data/ig_token_state.json (без токена).
# Если продлить не удаётся и до конца срока < 10 дней — бот пишет владельцу в Telegram.
IG_TOKEN_ENC = os.path.join(_ROOT, "data", "ig_token.enc")
IG_TOKEN_STATE = os.path.join(_ROOT, "data", "ig_token_state.json")
IG_REFRESH_DAYS = 7


def _fernet():
    key = os.getenv("IG_TOKEN_KEY", "").strip()
    if not key:
        return None
    import base64
    import hashlib
    from cryptography.fernet import Fernet
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(("leoparts-ig:" + key).encode()).digest()))


def _ig_token() -> str:
    f = _fernet()
    if f and os.path.exists(IG_TOKEN_ENC):
        try:
            with open(IG_TOKEN_ENC, "rb") as fh:
                return f.decrypt(fh.read().strip()).decode()
        except Exception as exc:  # noqa — битый файл/другой ключ: работаем с секретом
            print(f"Instagram: сохранённый токен не расшифрован ({type(exc).__name__}) — беру IG_ACCESS_TOKEN")
    return os.getenv("IG_ACCESS_TOKEN", "").strip()


def _ig_cfg():
    token = _ig_token()
    return ("", token) if token else None


def _notify_owner(text: str) -> None:
    tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    try:
        with open(os.path.join(_ROOT, "data", "owner_chat.json"), "r", encoding="utf-8") as fh:
            chat = json.load(fh).get("chat_id")
    except (FileNotFoundError, json.JSONDecodeError):
        chat = None
    if tok and chat:
        try:
            requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", data={"chat_id": chat, "text": text[:4000]}, timeout=30)
        except Exception:  # noqa
            pass


def ig_refresh_token(force: bool = False) -> None:
    """Продлевает токен Instagram раз в IG_REFRESH_DAYS дней. Ошибки не роняют публикацию."""
    if not _ig_cfg():
        return
    try:
        with open(IG_TOKEN_STATE, "r", encoding="utf-8") as fh:
            st = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        st = {}
    now = dt.datetime.now(MSK)
    last = st.get("refreshed_at")
    if not force and last and now - dt.datetime.fromisoformat(last) < dt.timedelta(days=IG_REFRESH_DAYS):
        return
    old = _ig_token()
    try:
        r = requests.get("https://graph.instagram.com/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": old}, timeout=60).json()
        if "access_token" not in r:
            raise SocialError((r.get("error") or {}).get("message") or str(r)[:200])
        new = r["access_token"]
        exp = now + dt.timedelta(seconds=int(r.get("expires_in") or 0))
        changed = new != old
        if changed:
            f = _fernet()
            if not f:
                raise SocialError("Instagram выдал новый токен, а секрета IG_TOKEN_KEY нет — сохранить его негде")
            with open(IG_TOKEN_ENC, "wb") as fh:
                fh.write(f.encrypt(new.encode()))
        st.update({"refreshed_at": now.isoformat(timespec="seconds"), "expires_at": exp.isoformat(timespec="seconds"),
                   "last_error": "", "token_changed_on_last_refresh": changed})
        print(f"Instagram: токен продлён до {exp:%d.%m.%Y}" + (" (новый токен сохранён зашифрованным)" if changed else ""))
    except Exception as exc:  # noqa
        st.update({"last_error": f"{now:%d.%m.%Y %H:%M}: {exc}", "last_attempt": now.isoformat(timespec="seconds")})
        print(f"Instagram: продлить токен не удалось — {exc}")
        exp_s = st.get("expires_at")
        left = (dt.datetime.fromisoformat(exp_s) - now).days if exp_s else None
        if left is not None and left < 10:
            _notify_owner(f"⚠️ Токен Instagram истекает через {left} дн. ({exp_s[:10]}), а продлить его автоматически не удалось: {exc}\n"
                          "Напишите Claude — нужно получить новый токен, иначе посты в Instagram остановятся.")
    os.makedirs(os.path.dirname(IG_TOKEN_STATE), exist_ok=True)
    with open(IG_TOKEN_STATE, "w", encoding="utf-8") as fh:
        json.dump(st, fh, ensure_ascii=False, indent=2)


def _ig(method: str, path: str, **params) -> dict:
    uid, token = _ig_cfg()
    params["access_token"] = token
    url = f"{IG_BASE}/{path}"
    r = requests.request(method, url, params=params if method == "GET" else None,
                         data=params if method != "GET" else None, timeout=90)
    try:
        data = r.json()
    except ValueError:
        raise SocialError(f"Instagram {path}: не JSON ({r.status_code})")
    if "error" in data:
        e = data["error"]
        raise SocialError(f"Instagram {path}: [{e.get('code')}/{e.get('error_subcode')}] {e.get('message')}")
    return data


def _to_jpeg(path: str, n: int, k: int) -> str:
    """Instagram принимает по ссылке только JPEG. Сохраняем JPEG и заливаем как ассет GitHub Release."""
    from PIL import Image
    import photo_host
    im = Image.open(path).convert("RGB")
    w, h = im.size
    if w > 1440:
        im = im.resize((1440, int(h * 1440 / w)))
    ratio = im.size[0] / im.size[1]
    if ratio < 0.8:
        # Instagram не принимает картинки уже 4:5 — расширяем холст по бокам цветом краёв (карточки 3:4 -> 4:5)
        w, h = im.size
        new_w = int(round(h * 0.8))
        edge = [im.getpixel((x, y)) for x in (0, w - 1) for y in range(0, h, max(1, h // 20))]
        col = tuple(int(sum(c[i] for c in edge) / len(edge)) for i in range(3))
        canvas = Image.new("RGB", (new_w, h), col)
        canvas.paste(im, ((new_w - w) // 2, 0))
        im = canvas
        ratio = im.size[0] / im.size[1]
    if not (0.8 <= ratio <= 1.91):
        logger.warning("Пост %s, слайд %s: пропорции %.2f вне 4:5…1.91:1 — Instagram может отклонить", n, k, ratio)
    tmp = os.path.join(tempfile.gettempdir(), f"social_{n}_{k}.jpg")
    im.save(tmp, "JPEG", quality=92)
    return photo_host.upload_file(tmp, f"social_{n}_{k}.jpg")


def _ig_wait(container_id: str, tries: int = 30):
    for _ in range(tries):
        st = _ig("GET", container_id, fields="status_code")
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code in ("ERROR", "EXPIRED"):
            raise SocialError(f"Instagram: контейнер {container_id} в статусе {code}")
        time.sleep(4)
    raise SocialError(f"Instagram: контейнер {container_id} не обработался за отведённое время")


def ig_publish(p: dict, images: List[str]) -> str:
    uid = str(_ig("GET", "me", fields="user_id")["user_id"])
    urls = [_to_jpeg(path, p["n"], k) for k, path in enumerate(images, start=1)]
    text = full_text(p, 2200, "ig")
    if len(urls) == 1:
        c = _ig("POST", f"{uid}/media", image_url=urls[0], caption=text)["id"]
    else:
        kids = []
        for u in urls:
            kid = _ig("POST", f"{uid}/media", image_url=u, is_carousel_item="true")["id"]
            _ig_wait(kid)
            kids.append(kid)
        c = _ig("POST", f"{uid}/media", media_type="CAROUSEL", children=",".join(kids), caption=text)["id"]
    _ig_wait(c)
    return _ig("POST", f"{uid}/media_publish", creation_id=c)["id"]


def ig_test() -> bool:
    if not _ig_cfg():
        print("Instagram: IG_USER_ID / IG_ACCESS_TOKEN не заданы — пропуск")
        return False
    uid, _ = _ig_cfg()
    info = _ig("GET", "me", fields="username")
    print(f"Instagram: токен работает, аккаунт @{info.get('username')}")
    try:
        with open(IG_TOKEN_STATE, "r", encoding="utf-8") as fh:
            st = json.load(fh)
        print(f"Instagram: токен действует до {st.get('expires_at', '?')[:10]}, продлён {st.get('refreshed_at', '?')[:10]}"
              + (f"; последняя ошибка продления: {st['last_error']}" if st.get("last_error") else "")
              + ("" if os.getenv("IG_TOKEN_KEY") else "; ⚠️ нет секрета IG_TOKEN_KEY"))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
    return True


# --------------------------------------------------------------------------- команды
def cmd_test() -> int:
    """Проверка подключений. Итог пишется и в data/social_test_result.txt (без токенов), чтобы его было видно в репозитории."""
    import contextlib
    import io
    rc = 0
    buf = io.StringIO()
    if _vk_via_rss():
        buf.write(f"VK: публикация через импорт RSS сообщества, лента {VK_FEED_URL}\n")
        print(buf.getvalue(), end="")
    for name, fn in ((("VK", vk_test),) if not _vk_via_rss() else ()) + (("Instagram", ig_test),):
        out = io.StringIO()
        try:
            with contextlib.redirect_stdout(out):
                fn()
        except Exception as exc:  # noqa
            out.write(f"{name}: ОШИБКА — {exc}\n")
            rc = 1
        print(out.getvalue(), end="")
        buf.write(out.getvalue())
    try:
        with open(os.path.join(_ROOT, "data", "social_test_result.txt"), "w", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now(MSK):%d.%m.%Y %H:%M} МСК\n" + buf.getvalue())
    except OSError:
        pass
    return rc


# VK через API с серверов не пускает (ошибка 9 «Flood control» с любых дата-центров, 10.2026), поэтому по
# умолчанию VK получает посты через ВСТРОЕННЫЙ ИМПОРТ RSS сообщества: бот ведёт ленту data/vk_feed.xml,
# VK сам забирает из неё новые записи. Вернуть публикацию через API — секрет VK_MODE=api.
def _vk_via_rss() -> bool:
    return os.getenv("VK_MODE", "rss").strip().lower() != "api"


VK_FEED_PATH = os.path.join(_ROOT, "data", "vk_feed.xml")
VK_FEED_URL = "https://raw.githubusercontent.com/antongrigorenko844-prog/marketplace-agent/main/data/vk_feed.xml"
VK_FEED_ITEMS = 20


def _product_link(p: dict) -> str:
    arts = p.get("arts") or []
    url = _site_url(p, "vk")
    if url:
        return url
    if arts:
        sku = _marketplace_ids().get(arts[0].lower(), {}).get("ozon")
        if sku:
            return f"https://www.ozon.ru/product/{sku}/"
    return "https://vk.ru/leopartsakpp"


def build_vk_feed(verbose: bool = True) -> int:
    """Лента RSS для импорта во VK: посты с наступившей датой (не на паузе; поставленные на паузу — только
    после публикации в Instagram), последние VK_FEED_ITEMS. Картинки — публичные ссылки (photo_host)."""
    from email.utils import format_datetime
    from xml.sax.saxutils import escape
    import photo_host
    plan = load_plan()
    posts_state = load_state().get("posts", {})
    try:
        with open(HOLD_PATH, "r", encoding="utf-8") as f:
            hold = {int(x) for x in re.findall(r"\d+", f.read())}
    except FileNotFoundError:
        hold = set()
    today = _today()
    items = []
    for p in plan:
        st = posts_state.get(str(p["n"]), {})
        ig_at = (st.get("ig") or {}).get("at")
        if not ig_at and (p["date"] is None or p["date"] > today or p["n"] in hold):
            continue
        imgs = post_images(p["n"], p.get("arts"))
        if not imgs:
            continue
        when = dt.datetime.fromisoformat(ig_at) if ig_at else dt.datetime.combine(p["date"], dt.time(10, 0), MSK)
        items.append((when, p, imgs))
    items = sorted(items, key=lambda x: x[0])[-VK_FEED_ITEMS:]
    out = []
    for when, p, imgs in reversed(items):
        urls = []
        for k, path in enumerate(imgs[:10], start=1):
            try:
                urls.append(photo_host.upload_file(path, f"vk_{p['n']}_{k}{os.path.splitext(path)[1].lower()}"))
            except Exception as exc:  # noqa
                print(f"VK RSS: пост №{p['n']}, картинка {k} не загружена: {exc}")
        text = full_text(p, 15000, "vk")
        body = "".join(f"<p>{escape(line)}</p>" if line.strip() else "" for line in text.split("\n"))
        figs = "".join(f'<figure><img src="{escape(u)}"/></figure>' for u in urls)
        enclosure = f'<enclosure url="{escape(urls[0])}" type="image/jpeg" length="0"/>' if urls else ""
        title = p.get("title") or f"Пост {p['n']}"
        out.append(
            "<item>"
            f"<title>{escape(title)}</title>"
            f"<link>{escape(_product_link(p))}</link>"
            f'<guid isPermaLink="false">leoparts-post-{p["n"]}</guid>'
            f"<pubDate>{format_datetime(when)}</pubDate>"
            f"<description><![CDATA[{escape(text)}]]></description>"
            f"<content:encoded><![CDATA[{figs}{body}]]></content:encoded>"
            f"{enclosure}"
            "</item>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<rss version="2.0" xmlns:content="http://purl.org/rss/1.0/modules/content/">'
           "<channel><title>LeoParts — запчасти для DSG</title>"
           "<link>https://vk.ru/leopartsakpp</link>"
           "<description>Посты LeoParts</description>"
           f"<lastBuildDate>{format_datetime(dt.datetime.now(MSK))}</lastBuildDate>"
           + "".join(out) + "</channel></rss>\n")
    os.makedirs(os.path.dirname(VK_FEED_PATH), exist_ok=True)
    with open(VK_FEED_PATH, "w", encoding="utf-8") as f:
        f.write(xml)
    if verbose:
        print(f"VK RSS: в ленте {len(out)} пост(ов): " + ", ".join(f"№{p['n']}" for _, p, _ in reversed(items)) + f" → {VK_FEED_URL}")
    return 0


def _platforms():
    pl = {}
    if _vk_cfg() and not _vk_via_rss():
        pl["vk"] = vk_publish
    if _ig_cfg():
        pl["ig"] = ig_publish
    return pl


def cmd_publish(dry_run: bool, auto: bool = False, article: str = "") -> int:
    """
    auto=True — режим расписания: работает только если SOCIAL_AUTOPUBLISH=1.
    article — номера постов через запятую (принудительно, независимо от даты).
    """
    if auto and os.getenv("SOCIAL_AUTOPUBLISH", "").strip() not in ("1", "true", "yes", "on"):
        print("Автопубликация выключена (секрет SOCIAL_AUTOPUBLISH != 1) — ничего не делаем.")
        return 0
    if not dry_run:
        ig_refresh_token()
        try:  # свежие ссылки на товары сайта (если сайт недоступен — остаются вчерашние)
            import site_links
            site_links.cmd_site_links()
        except Exception as exc:  # noqa
            print(f"Ссылки сайта не обновлены: {exc}")
    plan = load_plan()
    state = load_state()
    posts_state = state.setdefault("posts", {})
    pl = _platforms()
    if not pl and not dry_run:
        print("Не заданы секреты ни для VK, ни для Instagram — публиковать некуда.")
        return 0
    print(f"Площадки: {', '.join(pl) or 'нет (сухой прогон)'} · сегодня {_today():%d.%m.%Y} (МСК) · постов в плане: {len(plan)}")

    once = re.fullmatch(r"\s*([\d,\s]+)@(\d{4}-\d{2}-\d{2})\s*", article or "")
    if once:  # разовый запуск по расписанию: «1@2026-10-24» — только в этот день (МСК), в другие годы ничего
        if dt.date.fromisoformat(once.group(2)) != _today():
            print(f"Разовая публикация на {once.group(2)} — сегодня не тот день, пропуск.")
            return 0
        article = once.group(1)
    forced = {int(x) for x in re.findall(r"\d+", article or "")}
    try:
        with open(HOLD_PATH, "r", encoding="utf-8") as f:
            hold = {int(x) for x in re.findall(r"\d+", f.read())}
    except FileNotFoundError:
        hold = set()
    if hold:
        print("На паузе (data/social_hold.txt):", ", ".join(map(str, sorted(hold))))
    max_run = int(os.getenv("SOCIAL_MAX_PER_RUN", "1"))
    targets = pl.keys() if pl else ("vk", "ig")
    rc = 0
    for plat in targets:
        done = 0
        for p in sorted(plan, key=lambda x: (x["date"] or dt.date.max, x["n"])):
            if done >= max_run and not forced:
                break
            if forced and p["n"] not in forced:
                continue
            if not forced and (p["date"] is None or p["date"] > _today()):
                continue
            if plat in posts_state.get(str(p["n"]), {}):
                continue
            if p["n"] in hold and not forced:
                continue
            imgs = post_images(p["n"], p.get("arts"))
            if not imgs:
                if forced:
                    print(f"[{plat}] пост {p['n']}: нет картинок в social/{p['n']}/ — пропуск")
                continue
            label = f"[{plat}] пост №{p['n']} от {p['date']:%d.%m.%Y} «{p['title']}», картинок: {len(imgs)}, текст {len(full_text(p))} симв."
            if dry_run:
                print("СУХОЙ ПРОГОН ·", label)
                done += 1
                continue
            try:
                post_id = pl[plat](p, imgs)
            except Exception as exc:  # noqa
                print(f"ОШИБКА · {label}: {exc}")
                rc = 1
                break  # на этой площадке дальше не идём, чтобы не сломать порядок
            posts_state.setdefault(str(p["n"]), {})[plat] = {
                "id": post_id, "at": dt.datetime.now(MSK).isoformat(timespec="seconds")}
            save_state(state)
            print("ОПУБЛИКОВАНО ·", label, f"→ id {post_id}")
            done += 1
        if done == 0:
            print(f"[{plat}] публиковать нечего (нет постов с наступившей датой и готовыми картинками).")
    if not dry_run and _vk_via_rss():
        try:
            build_vk_feed()
        except Exception as exc:  # noqa — лента VK не должна ломать Instagram
            print(f"VK RSS: ОШИБКА сборки ленты: {exc}")
            rc = 1
    return rc


def cmd_status() -> int:
    plan = load_plan()
    state = load_state().get("posts", {})
    ready = pub_vk = pub_ig = 0
    missing = []
    for p in plan:
        has = bool(post_images(p["n"], p.get("arts")))
        ready += has
        s = state.get(str(p["n"]), {})
        pub_vk += "vk" in s
        pub_ig += "ig" in s
        if not has and p["date"] and p["date"] <= _today() + dt.timedelta(days=7):
            missing.append(p["n"])
    print(f"Постов в плане: {len(plan)} · картинки готовы: {ready} · опубликовано VK: {pub_vk}, Instagram: {pub_ig}")
    if missing:
        print("Нет картинок у постов на ближайшую неделю:", ", ".join(map(str, missing)))
    return 0
