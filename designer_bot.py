"""
Telegram-бот для дизайнера: отправка заданий и приём готовых макетов.

ЗАЧЕМ: владелец проверяет задание утром, дальше всё идёт само —
  1) designer-send: в 9:15 МСК дизайнеру в Telegram уходит задание на пост следующего рабочего дня
     (текст макета, состав слайдов, правила) + исходные фото из photos/ файлами;
  2) designer-collect: каждые 30 минут бот забирает присланные дизайнером картинки и кладёт их в
     social/<№ поста>/1.png, 2.png … (дальше их публикует social_publish.py в 10:00 МСК в день выхода).

НАСТРОЙКА (один раз):
  а) в Telegram найти @BotFather -> /newbot -> получить токен; добавить в GitHub Secrets как TELEGRAM_BOT_TOKEN;
  б) дизайнер открывает бота и нажимает Start;
  в) (для себя) откройте бота, нажмите Start и запустите designer-link с article=owner: макеты будут приходить вам в Telegram сразу, а «стоп 12» / «старт 12» ставит пост на паузу;
  г) запустить Actions -> marketplace-agent-designer -> designer-link: бот запомнит чат дизайнера
     (data/designer_chat.json). Имя напечатается в логе — проверьте, что это он.

КАК ДИЗАЙНЕР СДАЁТ РАБОТУ: присылает ВСЕ слайды поста одним альбомом (лучше как «Файл», без сжатия),
в подписи пишет номер поста (например «12»). Порядок в альбоме = порядок слайдов. Если пришлёт ещё раз с тем же номером —
папка поста заменится новыми файлами (правки). Бот отвечает «Принял пост N».

Остановить пост: впишите его номер в data/social_hold.txt — задание не уйдёт, публикация пропустит.
"""
import datetime as dt
import json
import logging
import mimetypes
import os
import re
from typing import List, Optional

import requests

import social_publish as sp

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
CHAT_PATH = os.path.join(_ROOT, "data", "designer_chat.json")
STATE_PATH = os.path.join(_ROOT, "data", "designer_state.json")
OWNER_PATH = os.path.join(_ROOT, "data", "owner_chat.json")
PHOTOS_DIR = os.path.join(_ROOT, "photos")
MAX_DOCS = 10


class DesignerBotError(RuntimeError):
    pass


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _tg(method: str, files=None, **params) -> dict:
    token = _token()
    if not token:
        raise DesignerBotError("TELEGRAM_BOT_TOKEN не задан")
    r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=params, files=files, timeout=120)
    try:
        data = r.json()
    except ValueError:
        raise DesignerBotError(f"Telegram {method}: не JSON ({r.status_code})")
    if not data.get("ok"):
        raise DesignerBotError(f"Telegram {method}: {data.get('description')}")
    return data["result"]


def _load(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _save(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, path)


def _chat_id() -> Optional[int]:
    return _load(CHAT_PATH, {}).get("chat_id")


def _owner_id() -> Optional[int]:
    return _load(OWNER_PATH, {}).get("chat_id")


def _read_hold() -> set:
    try:
        with open(sp.HOLD_PATH, "r", encoding="utf-8") as f:
            return {int(x) for x in re.findall(r"\d+", f.read())}
    except FileNotFoundError:
        return set()


def _write_hold(nums: set) -> None:
    os.makedirs(os.path.dirname(sp.HOLD_PATH), exist_ok=True)
    with open(sp.HOLD_PATH, "w", encoding="utf-8") as f:
        f.write(", ".join(str(n) for n in sorted(nums)) + ("\n" if nums else ""))


# --------------------------------------------------------------------------- план
def _plan_rows() -> List[dict]:
    import openpyxl
    ws = openpyxl.load_workbook(sp.PLAN_PATH, data_only=True)[sp.SHEET]
    head = [str(c.value or "").strip() for c in ws[1]]
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0] is None:
            continue
        d = {h: row[i] for i, h in enumerate(head)}
        if isinstance(d.get("Дата выхода"), dt.datetime):
            d["Дата выхода"] = d["Дата выхода"].date()
        rows.append(d)
    return rows


def _next_workday(today: dt.date) -> dt.date:
    d = today + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def build_brief(r: dict) -> str:
    n = int(r["№"])
    date = r["Дата выхода"]
    arts = str(r.get("Артикулы в master") or "")
    photos_note = str(r.get("Исходные фото") or "")
    lines = []
    if "НЕТ ФОТО" in photos_note:
        lines.append("⚠️ Для этого товара нужно сначала снять фото (в исходниках его нет).\n")
    lines += [
        f"📌 ЗАДАНИЕ · пост №{n} (выход {date:%d.%m.%Y})",
        f"Формат: {r.get('Формат')}",
        "Размер: 1080×1350 px (4:5), важное — в центральной зоне 1080×1080 (для VK)",
        "",
        "ТЕКСТ НА МАКЕТЕ",
        f"Заголовок: {r.get('Заголовок на макете')}",
        f"Подзаголовок: {r.get('Подзаголовок на макете')}",
    ]
    pts = str(r.get("Пункты на макете") or "")
    if pts and pts != "—":
        lines += ["Пункты:", pts]
    lines += ["", "СЛАЙДЫ / ЧТО ПОКАЗАТЬ", str(r.get("Состав слайдов / что показать") or "")]
    if arts and arts != "—":
        lines += ["", f"Артикулы: {arts}"]
    lines += [
        "",
        "ПРАВИЛА: без цен, без слов «скидка/дешевле/акция», без знака %, без логотипов автопроизводителей. Единый стиль серии.",
        "",
        f"КАК СДАТЬ: пришлите ВСЕ слайды сюда одним альбомом (лучше «Файлом», без сжатия). В подписи укажите номер поста: {n}. Порядок в альбоме = порядок слайдов.",
        f"СРОК: до 10:00 {date:%d.%m.%Y}.",
    ]
    return "\n".join(lines)


def _source_photos(arts: str) -> List[str]:
    out = []
    if not os.path.isdir(PHOTOS_DIR):
        return out
    folders = {d.lower(): d for d in os.listdir(PHOTOS_DIR)}
    for a in [x.strip() for x in arts.split(",") if x.strip() and x.strip() != "—"]:
        d = folders.get(a.lower())
        if not d:
            continue
        full = os.path.join(PHOTOS_DIR, d)
        for f in sorted(os.listdir(full), key=sp._natural):
            if f.lower().endswith(sp.IMG_EXT):
                out.append(os.path.join(full, f))
    return out[:MAX_DOCS]


# --------------------------------------------------------------------------- команды
def cmd_link(role_arg: str = "") -> int:
    owner_mode = role_arg.strip().lower() in ("owner", "я", "владелец")
    ups = _tg("getUpdates", timeout=0, allowed_updates=json.dumps(["message"]))
    seen = {}
    for u in ups:
        m = u.get("message") or {}
        c = m.get("chat") or {}
        if c.get("type") == "private":
            seen[c["id"]] = f"{c.get('first_name', '')} {c.get('last_name', '')} @{c.get('username', '')}".strip()
    other = _chat_id() if owner_mode else _owner_id()
    cands = {k: v for k, v in seen.items() if k != other}
    if not cands:
        print("Бот пока не получил личных сообщений от нужного человека. Пусть откроет бота, нажмёт Start, и повторите команду.")
        return 1
    print("Личные чаты с ботом:")
    for cid, name in seen.items():
        print(f"  {cid}: {name}")
    cid = list(cands)[-1]
    path = OWNER_PATH if owner_mode else CHAT_PATH
    _save(path, {"chat_id": cid, "name": cands[cid], "linked_at": dt.datetime.now(sp.MSK).isoformat(timespec="seconds")})
    who = "владельца" if owner_mode else "дизайнера"
    print(f"Запомнил чат {who}: {cands[cid]} (id {cid}). Если это не тот человек — пусть напишет боту ещё раз и повторите команду.")
    if owner_mode:
        _tg("sendMessage", chat_id=cid, text="Готово: буду присылать сюда макеты от дизайнера. Чтобы остановить пост — напишите «стоп 12» (номер поста), вернуть — «старт 12».")
    else:
        _tg("sendMessage", chat_id=cid, text="Привет! Я бот Leo.Parts. Буду присылать задания на посты. Готовые макеты присылайте сюда альбомом, в подписи — номер поста.")
    return 0


def cmd_send(dry_run: bool, article: str = "") -> int:
    rows = _plan_rows()
    nums = re.findall(r"\d+", article or "")
    if nums:
        targets = [r for r in rows if int(r["№"]) in {int(x) for x in nums}]
    else:
        today = dt.datetime.now(sp.MSK).date()
        if today.weekday() >= 5:
            print("Выходной — задание не отправляем.")
            return 0
        nd = _next_workday(today)
        targets = [r for r in rows if r.get("Дата выхода") == nd]
        if not targets:
            print(f"На {nd:%d.%m.%Y} в плане нет поста — нечего отправлять.")
            return 0
    state = _load(STATE_PATH, {})
    sent = state.setdefault("sent", {})
    try:
        with open(sp.HOLD_PATH, "r", encoding="utf-8") as f:
            hold = {int(x) for x in re.findall(r"\d+", f.read())}
    except FileNotFoundError:
        hold = set()
    chat = _chat_id()
    for r in targets:
        n = int(r["№"])
        if n in hold and not nums:
            print(f"Пост №{n} на паузе (social_hold.txt) — задание не отправляем.")
            continue
        if str(n) in sent and not nums:
            print(f"Задание по посту №{n} уже отправлено {sent[str(n)]} — пропуск.")
            continue
        brief = build_brief(r)
        photos = _source_photos(str(r.get("Артикулы в master") or ""))
        if dry_run or not _token() or not chat:
            print(f"--- ПРОБНЫЙ РЕЖИМ (отправки нет{'' if _token() else ': нет токена'}{'' if chat else ', дизайнер не привязан'}) ---")
            print(brief)
            print(f"[фото к отправке: {len(photos)}] " + ", ".join(os.path.relpath(p, _ROOT) for p in photos))
            continue
        _tg("sendMessage", chat_id=chat, text=brief[:4000])
        if len(photos) == 1:
            with open(photos[0], "rb") as f:
                _tg("sendDocument", chat_id=chat, files={"document": (os.path.basename(photos[0]), f)})
        elif len(photos) > 1:
            media, files = [], {}
            for i, p in enumerate(photos):
                key = f"f{i}"
                files[key] = (os.path.basename(p), open(p, "rb"))
                media.append({"type": "document", "media": f"attach://{key}"})
            try:
                _tg("sendMediaGroup", files=files, chat_id=chat, media=json.dumps(media))
            finally:
                for _, (_, fh) in files.items():
                    fh.close()
        sent[str(n)] = dt.datetime.now(sp.MSK).isoformat(timespec="seconds")
        _save(STATE_PATH, state)
        print(f"Отправил дизайнеру задание по посту №{n} (фото: {len(photos)}).")
    return 0


def _download(file_id: str) -> bytes:
    info = _tg("getFile", file_id=file_id)
    r = requests.get(f"https://api.telegram.org/file/bot{_token()}/{info['file_path']}", timeout=120)
    r.raise_for_status()
    return r.content


def cmd_collect() -> int:
    if not _token():
        print("TELEGRAM_BOT_TOKEN не задан — пропуск.")
        return 0
    chat = _chat_id()
    if not chat:
        print("Дизайнер не привязан (нет data/designer_chat.json) — запустите designer-link.")
        return 0
    state = _load(STATE_PATH, {})
    offset = state.get("offset")
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if offset:
        params["offset"] = offset
    ups = _tg("getUpdates", **params)
    if not ups:
        print("Новых сообщений нет.")
        return 0
    last_number = state.get("last_number")
    groups = {}   # номер поста -> список (message_id, file_id, ext)
    group_caption = {}
    items = []
    for u in ups:
        state["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        mchat = (m.get("chat") or {}).get("id")
        if mchat == _owner_id() and mchat != chat:
            t = (m.get("text") or "").strip().lower()
            mm = re.fullmatch(r"(стоп|старт)\s*№?\s*(\d+)", t)
            if mm:
                hold = _read_hold()
                num = int(mm.group(2))
                if mm.group(1) == "стоп":
                    hold.add(num)
                    reply = f"⏸ Пост №{num} поставлен на паузу: задание дизайнеру и публикация по нему не пройдут."
                else:
                    hold.discard(num)
                    reply = f"▶️ Пост №{num} снят с паузы."
                _write_hold(hold)
                _tg("sendMessage", chat_id=mchat, text=reply)
                print(reply)
            continue
        if mchat != chat:
            continue
        text = (m.get("text") or "").strip()
        if text and re.fullmatch(r"№?\s*\d+", text):
            last_number = int(re.findall(r"\d+", text)[0])
            continue
        fid, ext = None, None
        if m.get("document") and str(m["document"].get("mime_type", "")).startswith("image/"):
            fid = m["document"]["file_id"]
            ext = os.path.splitext(m["document"].get("file_name") or "")[1].lower() or (mimetypes.guess_extension(m["document"]["mime_type"]) or ".png")
        elif m.get("photo"):
            fid = m["photo"][-1]["file_id"]
            ext = ".jpg"
        if not fid:
            continue
        cap = m.get("caption") or ""
        gid = m.get("media_group_id") or f"single{m['message_id']}"
        if cap and re.search(r"\d+", cap):
            group_caption[gid] = int(re.findall(r"\d+", cap)[0])
        items.append((m["message_id"], gid, fid, ext))
    by_post = {}
    for mid, gid, fid, ext in items:
        n = group_caption.get(gid) or last_number
        if not n:
            _tg("sendMessage", chat_id=chat, text="Не понял, к какому посту файл. Напишите номер поста в подписи к альбому (например: 12) и пришлите ещё раз.")
            continue
        by_post.setdefault(n, []).append((mid, fid, ext))
        last_number = n
    state["last_number"] = last_number
    for n, lst in by_post.items():
        lst.sort()
        folder = os.path.join(sp.IMAGES_DIR, str(n))
        os.makedirs(folder, exist_ok=True)
        for f in os.listdir(folder):  # правки: заменяем набор целиком
            if f.lower().endswith(sp.IMG_EXT):
                os.remove(os.path.join(folder, f))
        for k, (_, fid, ext) in enumerate(lst[:10], start=1):
            data = _download(fid)
            if ext not in sp.IMG_EXT:
                ext = ".png"
            with open(os.path.join(folder, f"{k}{ext}"), "wb") as fh:
                fh.write(data)
        _tg("sendMessage", chat_id=chat, text=f"✅ Принял пост №{n}: файлов {min(len(lst), 10)}. Спасибо!")
        print(f"Пост №{n}: сохранено файлов {min(len(lst), 10)} в social/{n}/")
        _notify_owner(n)
    _save(STATE_PATH, state)
    return 0


def _notify_owner(n: int) -> None:
    """Сразу пересылает владельцу готовые макеты поста на проверку."""
    owner = _owner_id()
    if not owner:
        return
    try:
        row = next((r for r in _plan_rows() if int(r["№"]) == n), None)
        when = f"{row['Дата выхода']:%d.%m.%Y}" if row and row.get("Дата выхода") else "по плану"
        files = sp.post_images(n)
        _tg("sendMessage", chat_id=owner,
            text=f"🖼 Дизайнер сдал пост №{n} ({len(files)} файл.). Выйдет {when} в 10:00 МСК в VK и Instagram. "
                 f"Если макет не подходит — напишите «стоп {n}» (и передайте правки дизайнеру).")
        if len(files) == 1:
            with open(files[0], "rb") as f:
                _tg("sendDocument", chat_id=owner, files={"document": (os.path.basename(files[0]), f)})
        elif files:
            media, fh = [], {}
            for i, p in enumerate(files):
                fh[f"f{i}"] = (os.path.basename(p), open(p, "rb"))
                media.append({"type": "document", "media": f"attach://f{i}"})
            try:
                _tg("sendMediaGroup", files=fh, chat_id=owner, media=json.dumps(media))
            finally:
                for _, (_, h) in fh.items():
                    h.close()
    except Exception as exc:  # noqa — уведомление не должно ломать приём файлов
        print(f"Не удалось уведомить владельца о посте №{n}: {exc}")
