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
        "Единый стиль для всей серии постов.",
        "",
        f"КАК СДАТЬ: пришлите ВСЕ слайды сюда одним альбомом (лучше «Файлом», без сжатия). В подписи укажите номер поста: {n}. Порядок в альбоме = порядок слайдов.",
        f"СРОК: до 10:00 {date:%d.%m.%Y}.",
    ]
    return "\n".join(lines)


def build_review_brief(r: dict) -> str:
    n = int(r["№"])
    date = r["Дата выхода"]
    return (f"📌 ПОСТ №{n} (выход {date:%d.%m.%Y}) — на проверку\n\n"
            "Ниже готовые картинки и текст поста. Просмотри.\n"
            "• Всё хорошо — ничего не присылай, пост выйдет в таком виде.\n"
            f"• Поправить текст — пришли «Текст {n}» и новый текст.\n"
            f"• Заменить картинки — пришли свои альбомом, в подписи: {n}\n"
            f"• Добавить картинки к этим — то же, в подписи: {n} добавить\n"
            f"СРОК: до 10:00 {date:%d.%m.%Y}.")


def _send_images(chat, paths: List[str]) -> None:
    """Готовые картинки поста: превью фото (если Telegram не примет — файлами)."""
    if len(paths) == 1:
        with open(paths[0], "rb") as f:
            _tg("sendPhoto", chat_id=chat, files={"photo": (os.path.basename(paths[0]), f)})
        return
    for kind, field in (("photo", "photo"), ("document", "document")):
        media, fh = [], {}
        try:
            for i, p in enumerate(paths[:10]):
                fh[f"f{i}"] = (os.path.basename(p), open(p, "rb"))
                media.append({"type": kind, "media": f"attach://f{i}"})
            _tg("sendMediaGroup", files=fh, chat_id=chat, media=json.dumps(media))
            return
        except DesignerBotError as exc:
            print(f"sendMediaGroup как {kind}: {exc}")
        finally:
            for _, (_, h) in fh.items():
                h.close()


def _draft_text(r: dict) -> str:
    cap = str(r.get("Подпись к посту") or "").strip()
    tags = str(r.get("Хэштеги") or "").strip()
    return cap + ("\n\n" + tags if tags else "")


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
        arts_list = [a.strip() for a in str(r.get("Артикулы в master") or "").split(",") if a.strip() and a.strip() != "—"]
        ready = sp.post_images(n, arts_list)
        if ready:
            brief = build_review_brief(r)
            photos = []
        else:
            brief = build_brief(r)
            photos = _source_photos(str(r.get("Артикулы в master") or ""))
        if dry_run or not _token() or not chat:
            print(f"--- ПРОБНЫЙ РЕЖИМ (отправки нет{'' if _token() else ': нет токена'}{'' if chat else ', дизайнер не привязан'}) ---")
            print(brief)
            print(f"[фото к отправке: {len(photos)}] " + ", ".join(os.path.relpath(p, _ROOT) for p in photos))
            if ready:
                print(f"[готовые картинки поста на проверку: {len(ready)}] " + ", ".join(os.path.relpath(p, _ROOT) for p in ready))
            continue
        _tg("sendMessage", chat_id=chat, text=brief[:4000])
        if ready:
            _send_images(chat, ready)
        draft = _draft_text(r)
        _tg("sendMessage", chat_id=chat, text=(
            f"✏️ ТЕКСТ ПОСТА №{n} (черновик). Это подпись под постом вместе с хэштегами. Можешь править: абзацы, отступы, эмодзи, формулировки.\n"
            f"Когда готово — пришли боту ОДНИМ сообщением, начав со слов «Текст {n}», затем текст. Если править не нужно — ничего не присылай, уйдёт этот вариант.\n"
            f"────────\n") + draft[:3500])
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
        _owner = _owner_id()
        if _owner and _owner != chat:
            try:
                _tg("sendMessage", chat_id=_owner, text=f"✅ Отправил дизайнеру задание по посту №{n} (выход {r['Дата выхода']:%d.%m.%Y}).")
            except DesignerBotError as exc:
                print(f"не удалось уведомить владельца: {exc}")
    return 0


def _last_sent(state: dict) -> Optional[int]:
    """Номер поста, задание по которому отправлено дизайнеру последним."""
    sent = state.get("sent") or {}
    if not sent:
        return None
    return int(max(sent.items(), key=lambda kv: kv[1])[0])


def _download(file_id: str) -> bytes:
    info = _tg("getFile", file_id=file_id)
    r = requests.get(f"https://api.telegram.org/file/bot{_token()}/{info['file_path']}", timeout=120)
    r.raise_for_status()
    return r.content


def _plan_arts(n: int) -> List[str]:
    for r in _plan_rows():
        if int(r["№"]) == n:
            return [a.strip() for a in str(r.get("Артикулы в master") or "").split(",") if a.strip() and a.strip() != "—"]
    return []


def _save_set(n: int, files: List[tuple], append: bool) -> int:
    """files: [(bytes, ext)]. Заменяет набор картинок поста или добавляет к текущему."""
    import shutil
    folder = os.path.join(sp.IMAGES_DIR, str(n))
    os.makedirs(folder, exist_ok=True)
    cur = [f for f in os.listdir(folder) if f.lower().endswith(sp.IMG_EXT) and not f.startswith(".")]
    if append and not cur:
        # материализуем текущий набор (готовые картинки из каталога), чтобы добавить к нему
        for k, src in enumerate(sp.auto_images(_plan_arts(n)), start=1):
            shutil.copyfile(src, os.path.join(folder, f"{k}{os.path.splitext(src)[1].lower()}"))
    elif not append:
        for f in cur:
            os.remove(os.path.join(folder, f))
    existing = [f for f in os.listdir(folder) if f.lower().endswith(sp.IMG_EXT) and not f.startswith(".")]
    k0 = len(existing)
    total = 0
    for k, (data, ext) in enumerate(files, start=1):
        if k0 + k > 10:
            break
        if ext not in sp.IMG_EXT:
            ext = ".png"
        with open(os.path.join(folder, f"{k0 + k}{ext}"), "wb") as fh:
            fh.write(data)
        total += 1
    return k0 + total


OUTBOX_DIR = "data/outbox"
OUTBOX_DONE = "data/outbox_done.json"


def _process_outbox(designer, owner) -> int:
    """Сообщения от Claude: файлы data/outbox/*.json вида {"to": "designer"|"owner", "text": "...", "images": ["social/5/1.png"]}.
    Каждый отправляется один раз (список отправленных — data/outbox_done.json)."""
    if not os.path.isdir(OUTBOX_DIR):
        return 0
    done = _load(OUTBOX_DONE, [])
    sent = 0
    for name in sorted(os.listdir(OUTBOX_DIR)):
        if not name.endswith(".json") or name in done:
            continue
        msg = _load(os.path.join(OUTBOX_DIR, name), {})
        chat = owner if msg.get("to") == "owner" else designer
        if not chat:
            print(f"outbox {name}: получатель не привязан — пропуск.")
            continue
        if msg.get("text"):
            _tg("sendMessage", chat_id=chat, text=msg["text"][:4000])
        imgs = [x for x in msg.get("images", []) if os.path.exists(x)]
        if imgs:
            _send_images(chat, imgs)
        for extra in msg.get("texts", []):
            _tg("sendMessage", chat_id=chat, text=str(extra)[:4000])
        if msg.get("mark_sent"):  # посты, задание по которым уже ушло этим сообщением (чтобы 09:15 не отправило повторно)
            st = _load(STATE_PATH, {})
            for n in msg["mark_sent"]:
                st.setdefault("sent", {})[str(n)] = dt.datetime.now(sp.MSK).isoformat(timespec="seconds")
            _save(STATE_PATH, st)
        if owner and chat != owner:
            head = (msg.get("text") or "").strip().splitlines()[0][:80] if msg.get("text") else "(только картинки)"
            _tg("sendMessage", chat_id=owner, text=f"✅ Отправил дизайнеру: {head}" + (f" (+{len(imgs)} карт.)" if imgs else ""))
        done.append(name)
        sent += 1
        print(f"outbox {name}: отправлено ({msg.get('to')}).")
    if sent:
        _save(OUTBOX_DONE, done)
    return sent


def cmd_collect() -> int:
    """Забирает сообщения из Telegram. Дизайнер и владелец могут: присылать картинки (подпись «12» — заменить набор,
    «12 добавить» — добавить), «Текст 12 …» — итоговый текст. Владелец: «стоп 12» / «старт 12», «Дизайнеру 12: …» — переслать задание."""
    if not _token():
        print("TELEGRAM_BOT_TOKEN не задан — пропуск.")
        return 0
    designer = _chat_id()
    if not designer:
        print("Дизайнер не привязан (нет data/designer_chat.json) — запустите designer-link.")
        return 0
    owner = _owner_id()
    _process_outbox(designer, owner)
    state = _load(STATE_PATH, {})
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if state.get("offset"):
        params["offset"] = state["offset"]
    ups = _tg("getUpdates", **params)
    if not ups:
        print("Новых сообщений нет.")
        return 0
    last_number = state.get("last_number")
    texts_new = []
    group_cap = {}      # gid -> (номер, добавить?)
    fwd_groups = set()  # альбомы владельца, которые надо переслать дизайнеру
    items = []          # (sender_chat, message_id, gid, file_id, ext)
    for u in ups:
        state["offset"] = u["update_id"] + 1
        m = u.get("message") or {}
        mchat = (m.get("chat") or {}).get("id")
        if mchat not in (designer, owner):
            continue
        is_owner = mchat == owner and mchat != designer
        raw = m.get("text") or m.get("caption") or ""
        gid = m.get("media_group_id") or f"single{m['message_id']}"
        # --- команды владельца
        if is_owner:
            mm = re.fullmatch(r"\s*(стоп|старт)\s*№?\s*(\d+)\s*", raw.lower())
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
            fw = re.match(r"^\s*дизайнер\w*\s*№?\s*(\d+)?\s*[:\-–—]?\s*(.*)$", raw, re.S | re.I)
            if fw and (fw.group(2).strip() or m.get("photo") or m.get("document")):
                num = fw.group(1)
                head = f"📩 ДОПОЛНИТЕЛЬНОЕ ЗАДАНИЕ от владельца" + (f" · пост №{num}" if num else "")
                _tg("sendMessage", chat_id=designer, text=head)
                _tg("copyMessage", chat_id=designer, from_chat_id=mchat, message_id=m["message_id"])
                if m.get("media_group_id"):
                    fwd_groups.add(m["media_group_id"])
                _tg("sendMessage", chat_id=mchat, text="✅ Переслал дизайнеру." )
                print("Задание владельца переслано дизайнеру")
                continue
            if m.get("media_group_id") in fwd_groups:
                _tg("copyMessage", chat_id=designer, from_chat_id=mchat, message_id=m["message_id"])
                continue
        # --- итоговый текст
        mt = re.match(r"^\s*(?:текст|text)\b\s*(?:№?\s*(\d+))?\s*[:\-–—]?\s*(.*)$", m.get("text") or "", re.S | re.I)
        if mt and mt.group(2).strip() and (mt.group(1) or _last_sent(state)):
            tn = int(mt.group(1)) if mt.group(1) else _last_sent(state)
            os.makedirs(sp.TEXTS_DIR, exist_ok=True)
            with open(sp.final_text_path(tn), "w", encoding="utf-8") as fh:
                fh.write(mt.group(2).strip() + "\n")
            _tg("sendMessage", chat_id=mchat, text=f"✅ Принял текст поста №{tn}.")
            print(f"Пост №{tn}: сохранён итоговый текст ({len(mt.group(2).strip())} симв.)")
            if not is_owner:
                texts_new.append(tn)
            continue
        text = (m.get("text") or "").strip()
        if text and re.fullmatch(r"№?\s*\d+", text):
            last_number = int(re.findall(r"\d+", text)[0])
            continue
        # --- картинки
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
        if cap and re.search(r"\d+", cap):
            group_cap[gid] = (int(re.findall(r"\d+", cap)[0]), bool(re.search(r"(\+|добав)", cap, re.I)))
        items.append((mchat, m["message_id"], gid, fid, ext))
    by_post = {}
    for mchat, mid, gid, fid, ext in items:
        n, app = group_cap.get(gid, (None, False))
        n = n or _last_sent(state) or last_number
        if not n:
            _tg("sendMessage", chat_id=mchat, text="Не понял, к какому посту картинки. Напишите номер поста в подписи (например: 12; чтобы добавить к текущим — 12 добавить) и пришлите ещё раз.")
            continue
        e = by_post.setdefault((n, app, mchat), [])
        e.append((mid, fid, ext))
        last_number = n
    state["last_number"] = last_number
    for tn in texts_new:
        _notify_owner_text(tn)
    for (n, app, mchat), lst in by_post.items():
        lst.sort()
        files = [(_download(fid), ext) for _, fid, ext in lst[:10]]
        total = _save_set(n, files, app)
        who = "владельца" if mchat == owner and mchat != designer else "дизайнера"
        _tg("sendMessage", chat_id=mchat, text=f"✅ Принял пост №{n}: {'добавлено ' + str(len(files)) + ', всего ' + str(total) if app else 'файлов ' + str(total)}. Спасибо!")
        print(f"Пост №{n} ({who}): {'добавлено' if app else 'заменено'} файлов {len(files)}, всего {total} в social/{n}/")
        if mchat == designer and mchat != owner:
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


def _notify_owner_text(n: int) -> None:
    owner = _owner_id()
    if not owner:
        return
    try:
        with open(sp.final_text_path(n), "r", encoding="utf-8") as f:
            txt = f.read().strip()
        _tg("sendMessage", chat_id=owner,
            text=f"📝 Дизайнер прислала итоговый текст поста №{n} (так он выйдет). Не подходит — напишите «стоп {n}».\n────────\n" + txt[:3800])
    except Exception as exc:  # noqa
        print(f"Не удалось уведомить владельца о тексте поста №{n}: {exc}")
