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

VK_API = "https://api.vk.com/method"
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
        return per[0][:10]
    return [x[0] for x in per][:10]


def post_images(n: int, arts: Optional[List[str]] = None) -> List[str]:
    """Картинки, присланные дизайнером (social/<№>/), иначе — готовые из каталога (по артикулам поста)."""
    d = os.path.join(IMAGES_DIR, str(n))
    if os.path.isdir(d):
        files = [f for f in os.listdir(d) if f.lower().endswith(IMG_EXT) and not f.startswith(".")]
        if files:
            return [os.path.join(d, f) for f in sorted(files, key=_natural)][:10]
    return auto_images(arts or [])


def final_text_path(n: int) -> str:
    return os.path.join(TEXTS_DIR, f"{n}.txt")


def full_text(p: dict, limit: Optional[int] = None) -> str:
    """Если дизайнер прислал итоговый текст (data/post_texts/<№>.txt) — берём его целиком (хэштеги уже внутри).
    Иначе — подпись + хэштеги из плана."""
    try:
        with open(final_text_path(p["n"]), "r", encoding="utf-8") as f:
            text = f.read().strip()
        if text:
            if limit and len(text) > limit:
                text = text[: limit - 1].rstrip() + "…"
            return text
    except FileNotFoundError:
        pass
    text = p["caption"].strip()
    tags = p["tags"].strip()
    if tags:
        text += "\n\n" + tags
    if limit and len(text) > limit:
        text = text[: limit - 1].rstrip() + "…"
    return text


# --------------------------------------------------------------------------- VK
def _vk_cfg():
    token = os.getenv("VK_TOKEN", "").strip()
    gid = os.getenv("VK_GROUP_ID", "").strip().lstrip("-")
    return (token, gid) if token and gid else None


def _vk(method: str, **params) -> dict:
    cfg = _vk_cfg()
    params.update({"access_token": cfg[0], "v": VK_VERSION})
    r = requests.post(f"{VK_API}/{method}", data=params, timeout=60)
    data = r.json()
    if "error" in data:
        e = data["error"]
        raise SocialError(f"VK {method}: [{e.get('error_code')}] {e.get('error_msg')}")
    return data["response"]


def vk_publish(p: dict, images: List[str]) -> str:
    _, gid = _vk_cfg()
    atts = []
    for path in images:
        srv = _vk("photos.getWallUploadServer", group_id=gid)
        with open(path, "rb") as f:
            ct = mimetypes.guess_type(path)[0] or "image/png"
            up = requests.post(srv["upload_url"], files={"photo": (os.path.basename(path), f, ct)}, timeout=120).json()
        if not up.get("photo") or up.get("photo") == "[]":
            raise SocialError(f"VK не принял файл {path}: {up}")
        saved = _vk("photos.saveWallPhoto", group_id=gid, photo=up["photo"], server=up["server"], hash=up["hash"])
        atts.append(f"photo{saved[0]['owner_id']}_{saved[0]['id']}")
    res = _vk("wall.post", owner_id=f"-{gid}", from_group=1, message=full_text(p, 15000), attachments=",".join(atts))
    return str(res.get("post_id"))


def vk_test() -> bool:
    if not _vk_cfg():
        print("VK: VK_TOKEN / VK_GROUP_ID не заданы — пропуск")
        return False
    _, gid = _vk_cfg()
    info = _vk("groups.getById", group_id=gid)
    g = info["groups"][0] if isinstance(info, dict) and "groups" in info else info[0]
    print(f"VK: токен работает, сообщество «{g.get('name')}» (id {gid})")
    _vk("photos.getWallUploadServer", group_id=gid)
    print("VK: право загружать фото на стену — есть")
    return True


# --------------------------------------------------------------------------- Instagram
def _ig_cfg():
    uid = os.getenv("IG_USER_ID", "").strip()
    token = os.getenv("IG_ACCESS_TOKEN", "").strip()
    return (uid, token) if uid and token else None


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
    text = full_text(p, 2200)
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
    return True


# --------------------------------------------------------------------------- команды
def cmd_test() -> int:
    rc = 0
    for name, fn in (("VK", vk_test), ("Instagram", ig_test)):
        try:
            fn()
        except Exception as exc:  # noqa
            print(f"{name}: ОШИБКА — {exc}")
            rc = 1
    return rc


def _platforms():
    pl = {}
    if _vk_cfg():
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
    plan = load_plan()
    state = load_state()
    posts_state = state.setdefault("posts", {})
    pl = _platforms()
    if not pl and not dry_run:
        print("Не заданы секреты ни для VK, ни для Instagram — публиковать некуда.")
        return 0
    print(f"Площадки: {', '.join(pl) or 'нет (сухой прогон)'} · сегодня {_today():%d.%m.%Y} (МСК) · постов в плане: {len(plan)}")

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
