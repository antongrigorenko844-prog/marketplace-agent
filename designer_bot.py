"""
Telegram-бот для дизайнера: отправка заданий и приём готовых макетов.

ЗАЧЕМ: владелец проверяет задание утром, дальше всё идёт само —
  1) designer-send: в 9:15 МСК (по будням) дизайнеру в Telegram уходят задания на все посты ближайших
     DESIGNER_DAYS_AHEAD дней (по умолчанию 5), по которым задание ещё не отправлялось — посты выходят
     КАЖДЫЙ день, включая выходные, а дизайнер работает наперёд. Каждое задание — один раз;
     дизайнер может попросить ещё: написать боту «ещё» (или «ещё 5») — придут следующие задания;
  2) designer-collect: каждые 30 минут бот забирает присланные дизайнером картинки и кладёт их в
     social/<№ поста>/1.png, 2.png … (посты публикует дизайнер).

Для первичной привязки или смены роли администратор задаёт DESIGNER_LINK_TARGET
в секретах Actions и запускает designer-link (article=owner для владельца).
Обе действующие привязки используются автоматически.

Карточки товаров: «Правка SKU слайд N», «Сдать ID v1», «Мои правки».
Альбомы исходников подписываются «Карточка SKU». Сводка открытых правок
поступает дизайнеру по понедельникам с 09:05 МСК.

КАК ДИЗАЙНЕР СДАЁТ РАБОТУ: присылает ВСЕ слайды поста одним альбомом (лучше как «Файл», без сжатия),
в подписи пишет номер поста (например «12»). Порядок в альбоме = порядок слайдов. Если пришлёт ещё раз с тем же номером —
папка поста заменится новыми файлами (правки). Бот отвечает «Принял пост N».

ВЛАДЕЛЕЦ ПРОВЕРЯЕТ: готовые макеты и тексты от дизайнера сразу пересылаются владельцу. Команды владельца боту:
«план» — что готово на ближайшие 2 недели; «покажи 12» — прислать пост №12 целиком (картинки + итоговый текст);
«стоп 12» / «старт 12» — пауза/снятие паузы; «Дизайнеру 12: …» — переслать правки дизайнеру.

Остановить пост: впишите его номер в data/social_hold.txt — задание дизайнеру не уйдёт.
"""
import datetime as dt
import json
import logging
import mimetypes
import os
import re
from typing import List, Optional

from hashlib import sha256
from pathlib import Path

import requests

import social_publish as sp
from designer_card_media import CardArchive, atomic_write, identity
from designer_card_tasks import CardTasks, CardTaskError
from designer_card_routes import archive_caption, looks_like_card_change, receive, submission_caption
from designer_checkpoint import checkpoint as persist_designer

logger = logging.getLogger(__name__)

_ROOT = os.path.dirname(os.path.abspath(__file__))
CHAT_PATH = os.path.join(_ROOT, "data", "designer_chat.json")
STATE_PATH = os.path.join(_ROOT, "data", "designer_state.json")
OWNER_PATH = os.path.join(_ROOT, "data", "owner_chat.json")
PHOTOS_DIR = os.path.join(_ROOT, "photos")
MAX_DOCS = 10
DAYS_AHEAD = max(1, int(os.getenv("DESIGNER_DAYS_AHEAD", "5") or 5))
# Владелец видит посты раньше дизайнера: предпросмотр — на OWNER_EXTRA_DAYS дней дальше,
# а задание дизайнеру уходит не раньше чем через OWNER_REVIEW_HOURS часов после предпросмотра.
OWNER_EXTRA_DAYS = max(0, int(os.getenv("OWNER_EXTRA_DAYS", "3") or 3))
OWNER_REVIEW_HOURS = max(0, int(os.getenv("OWNER_REVIEW_HOURS", "24") or 24))


class DesignerBotError(RuntimeError):
    pass


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "").strip()


def _tg(method: str, files=None, **params) -> dict:
    token = _token()
    if not token:
        raise DesignerBotError("TELEGRAM_BOT_TOKEN не задан")
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/{method}", data=params, files=files, timeout=120)
    except requests.RequestException:
        raise DesignerBotError(f"Telegram {method}: ошибка соединения") from None
    try:
        data = r.json()
    except ValueError:
        raise DesignerBotError(f"Telegram {method}: не JSON ({r.status_code})")
    if not data.get("ok"):
        raise DesignerBotError(f"Telegram {method}: запрос отклонён ({r.status_code})")
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


def _upcoming(rows: List[dict], today: dt.date, extra: int = 0) -> List[dict]:
    """Посты, задание по которым пора дать: дата выхода после сегодня и не позже чем через DAYS_AHEAD дней
    (но как минимум до следующего рабочего дня — в пятницу захватываются суббота, воскресенье и понедельник)."""
    last = max(today + dt.timedelta(days=DAYS_AHEAD + extra), _next_workday(today))
    out = [r for r in rows if isinstance(r.get("Дата выхода"), dt.date) and today < r["Дата выхода"] <= last]
    return sorted(out, key=lambda r: (r["Дата выхода"], int(r["№"])))


def _arts(r: dict) -> List[str]:
    return [a.strip() for a in str(r.get("Артикулы в master") or "").split(",") if a.strip() and a.strip() != "—"]


def _single_open(state: dict, need: str) -> Optional[int]:
    """Если дизайнер не указал номер поста: подставляем номер, только когда открытый пост ровно один
    (задание отправлено, дата не прошла, ещё нет картинок / итогового текста). Иначе — переспросим."""
    sent = state.get("sent") or {}
    today = dt.datetime.now(sp.MSK).date()
    hold = _read_hold()
    cands = []
    for r in _plan_rows():
        n = int(r["№"])
        if str(n) not in sent or n in hold or not isinstance(r.get("Дата выхода"), dt.date) or r["Дата выхода"] < today:
            continue
        if need == "img" and not os.path.isdir(os.path.join(sp.IMAGES_DIR, str(n))):
            cands.append(n)
        elif need == "text" and not os.path.exists(sp.final_text_path(n)):
            cands.append(n)
    return cands[0] if len(cands) == 1 else None


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
        f"КАК СДАТЬ: пришлите ВСЕ слайды сюда одним альбомом (лучше «Файлом», без сжатия). В подписи ОБЯЗАТЕЛЬНО укажите номер поста: {n}. Порядок в альбоме = порядок слайдов.",
        f"СРОК: до 10:00 {date:%d.%m.%Y}. Можно сдавать раньше — пост выйдет в свой день.",
        "Хотите сделать больше наперёд — напишите боту «ещё», пришлю следующие задания.",
    ]
    return "\n".join(lines)


def build_review_brief(r: dict) -> str:
    n = int(r["№"])
    date = r["Дата выхода"]
    return (f"📌 ПОСТ №{n} (выход {date:%d.%m.%Y}) — на проверку\n\n"
            "Ниже готовые картинки и текст поста. Просмотри.\n"
            "• Если всё готово — опубликуй пост по контент-плану.\n"
            f"• Поправить текст — пришли «Текст {n}» и новый текст.\n"
            f"• Заменить картинки — пришли свои альбомом, в подписи: {n}\n"
            f"• Добавить картинки к этим — то же, в подписи: {n} добавить\n"
            f"СРОК: до 10:00 {date:%d.%m.%Y}.")


def _send_images(chat, paths: List[str], as_files: bool = False) -> None:
    """Готовые картинки поста: превью фото (если Telegram не примет — файлами). as_files — сразу файлами, без сжатия."""
    if as_files:
        for i in range(0, len(paths), 10):
            part = paths[i:i + 10]
            if len(part) == 1:
                with open(part[0], "rb") as f:
                    _tg("sendDocument", chat_id=chat, files={"document": (os.path.basename(part[0]), f)})
                continue
            media, fh = [], {}
            try:
                for k, p in enumerate(part):
                    fh[f"f{k}"] = (os.path.basename(p), open(p, "rb"))
                    media.append({"type": "document", "media": f"attach://f{k}"})
                _tg("sendMediaGroup", files=fh, chat_id=chat, media=json.dumps(media))
            finally:
                for _, (_, h) in fh.items():
                    h.close()
        return
    if not paths:
        return
    if len(paths) > 10:
        for start in range(0, len(paths), 10):
            _send_images(chat, paths[start:start + 10])
        return
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
    raise DesignerBotError("Не удалось доставить альбом в Telegram.")


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
            seen[c["id"]] = True
    other = _chat_id() if owner_mode else _owner_id()
    cands = {k: v for k, v in seen.items() if k != other}
    if not cands:
        print("Бот пока не получил личных сообщений от нужного человека. Пусть откроет бота, нажмёт Start, и повторите команду.")
        return 1
    previous = _owner_id() if owner_mode else _chat_id()
    target = os.environ.get("DESIGNER_LINK_TARGET", "").strip()
    if target:
        if not target.isdecimal() or int(target) not in cands:
            print("Подтверждённый адресат не найден среди ожидающих личных чатов.")
            return 1
        cid = int(target)
    elif previous in cands:
        cid = previous
    else:
        print("Для новой привязки или смены получателя укажите адресат в секрете DESIGNER_LINK_TARGET и повторите designer-link.")
        return 1
    path = OWNER_PATH if owner_mode else CHAT_PATH
    _save(path, {"chat_id": cid, "linked_at": dt.datetime.now(sp.MSK).isoformat(timespec="seconds")})
    who = "владельца" if owner_mode else "дизайнера"
    persist_designer()
    print(f"Чат {who} привязан.")
    if owner_mode:
        _tg("sendMessage", chat_id=cid, text="Готово: буду присылать сюда макеты от дизайнера. Чтобы остановить пост — напишите «стоп 12» (номер поста), вернуть — «старт 12».")
    else:
        _tg("sendMessage", chat_id=cid, text="Привет! Я бот Leo.Parts. Буду присылать задания на посты. Готовые макеты присылайте сюда альбомом, в подписи — номер поста.")
    return 0


def cmd_send(dry_run: bool, article: str = "") -> int:
    if not dry_run and _token() and _chat_id():
        # A failed poll may contain the owner's stop instruction.
        cmd_collect()
    rows = _plan_rows()
    nums = re.findall(r"\d+", article or "")
    if nums:
        targets = [r for r in rows if int(r["№"]) in {int(x) for x in nums}]
    else:
        today = dt.datetime.now(sp.MSK).date()
        if today.weekday() >= 5:
            print("Выходной — задание не отправляем.")
            return 0
        targets = _upcoming(rows, today)
        if not targets:
            print(f"На ближайшие {DAYS_AHEAD} дн. в плане нет постов — нечего отправлять.")
            return 0
    _send_targets(targets, bool(nums), dry_run)
    return 0


def _send_targets(targets: List[dict], forced: bool, dry_run: bool) -> int:
    """Отправляет дизайнеру задания по списку постов. Возвращает, сколько отправлено."""
    nums = forced
    count = 0
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
        if not nums and not _reviewed(state, n):
            print(f"Пост №{n} ещё на проверке у владельца (меньше {OWNER_REVIEW_HOURS} ч после предпросмотра) — задание позже.")
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
        count += 1
        print(f"Отправил дизайнеру задание по посту №{n} (фото: {len(photos)}).")
        _owner = _owner_id()
        if _owner and _owner != chat:
            try:
                _tg("sendMessage", chat_id=_owner, text=f"✅ Отправил дизайнеру задание по посту №{n} (выход {r['Дата выхода']:%d.%m.%Y}).")
            except DesignerBotError as exc:
                print(f"не удалось уведомить владельца: {exc}")
    return count


def _reviewed(state: dict, n: int) -> bool:
    """Владелец видел пост не меньше OWNER_REVIEW_HOURS часов назад (или владелец не привязан)."""
    if not _owner_id() or OWNER_REVIEW_HOURS == 0:
        return True
    t = (state.get("previewed") or {}).get(str(n))
    if not t:
        return False
    return dt.datetime.now(sp.MSK) - dt.datetime.fromisoformat(t) >= dt.timedelta(hours=OWNER_REVIEW_HOURS)


def _send_more(k: int) -> int:
    """По просьбе дизайнера («ещё»): следующие k будущих постов, по которым задание ещё не уходило."""
    state = _load(STATE_PATH, {})
    sent = state.get("sent") or {}
    hold = _read_hold()
    today = dt.datetime.now(sp.MSK).date()
    rows = sorted((r for r in _plan_rows() if isinstance(r.get("Дата выхода"), dt.date) and r["Дата выхода"] > today),
                  key=lambda r: (r["Дата выхода"], int(r["№"])))
    todo = [r for r in rows if str(int(r["№"])) not in sent and int(r["№"]) not in hold]
    nxt = [r for r in todo if _reviewed(state, int(r["№"]))][:k]
    # следующие посты, которые владелец ещё не видел, — сразу ему на проверку (дизайнер получит их позже)
    unseen = [str(int(r["№"])) for r in todo if str(int(r["№"])) not in (state.get("previewed") or {})][:k]
    if unseen and _owner_id():
        cmd_owner_preview(",".join(unseen), review=True)
    return _send_targets(nxt, False, False) if nxt else 0


def cmd_owner_preview(article: str = "", review: bool = False) -> int:
    """В 08:41 МСК присылает ВЛАДЕЛЬЦУ пост следующего рабочего дня на проверку: текст + картинки.
    Тишина = «ок» (в 09:15 задание уйдёт дизайнеру). «стоп N» — остановить, «старт N» — вернуть."""
    owner = _owner_id()
    if not owner or not _token():
        print("Владелец не привязан или нет токена — предпросмотр не отправлен.")
        return 0
    rows = _plan_rows()
    nums = re.findall(r"\d+", article or "")
    if nums:
        targets = [r for r in rows if int(r["№"]) in {int(x) for x in nums}]
    else:
        today = dt.datetime.now(sp.MSK).date()
        targets = _upcoming(rows, today, OWNER_EXTRA_DAYS)  # посты выходят каждый день — предпросмотр тоже ежедневно
    state = _load(STATE_PATH, {})
    prev = state.setdefault("previewed", {})
    for r in targets:
        n = int(r["№"])
        if str(n) in prev and not nums:
            print(f"Предпросмотр поста №{n} уже отправлялся — пропуск.")
            continue
        arts = [a.strip() for a in str(r.get("Артикулы в master") or "").split(",") if a.strip() and a.strip() != "—"]
        imgs = sp.post_images(n, arts)
        if nums and not review:  # «покажи N» — пост как он выйдет, с итоговым текстом дизайнера, если он есть
            _show_post(owner, r)
            continue
        _tg("sendMessage", chat_id=owner, text=(
            f"🔎 ПОСТ №{n} на проверку (выход {r['Дата выхода']:%d.%m.%Y} в 10:00)\n"
            "Проверьте текст и картинки ниже.\n"
            f"• Всё хорошо — ничего не пишите: задание дизайнеру уйдёт не раньше чем через {OWNER_REVIEW_HOURS} ч (в будни в 9:15).\n"
            f"• Остановить — напишите «стоп {n}».\n"
            "• Правки — напишите их Claude, он передаст дизайнеру."))
        if imgs:
            _send_images(owner, imgs)
        _tg("sendMessage", chat_id=owner, text=("✏️ ТЕКСТ ПОСТА:\n────────\n" + sp.full_text(
            {"n": n, "caption": str(r.get("Подпись к посту") or "").strip(), "tags": str(r.get("Хэштеги") or "").strip(), "arts": arts}, 3800))[:4000])
        prev[str(n)] = dt.datetime.now(sp.MSK).isoformat(timespec="seconds")
        _save(STATE_PATH, state)
        print(f"Отправил владельцу предпросмотр поста №{n}.")
    return 0


def _show_post(chat, r: dict) -> None:
    n = int(r["№"])
    arts = _arts(r)
    imgs = sp.post_images(n, arts)
    final = os.path.exists(sp.final_text_path(n))
    hold = n in _read_hold()
    _tg("sendMessage", chat_id=chat, text=(
        f"👁 ПОСТ №{n} · выход {r['Дата выхода']:%d.%m.%Y} в 10:00 МСК{' · ⏸ НА ПАУЗЕ' if hold else ''}\n"
        f"Картинки: {len(imgs) if imgs else 'нет'} · текст: {'итоговый от дизайнера' if final else 'черновик из плана'}.\n"
        f"Не подходит — «стоп {n}», правки дизайнеру — «Дизайнеру {n}: …»."))
    if imgs:
        _send_images(chat, imgs)
    txt = sp.full_text({"n": n, "caption": str(r.get("Подпись к посту") or "").strip(),
                        "tags": str(r.get("Хэштеги") or "").strip(), "arts": arts}, 3800)
    _tg("sendMessage", chat_id=chat, text=("✏️ ТЕКСТ ПОСТА:\n────────\n" + txt)[:4000])


def _plan_summary(days: int = 14) -> str:
    today = dt.datetime.now(sp.MSK).date()
    state = _load(STATE_PATH, {})
    sent = state.get("sent") or {}
    hold = _read_hold()
    wd = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
    lines = [f"📅 План на {days} дн. (🖼 макет · ✏️ итоговый текст · 📤 задание ушло · ⏸ пауза)"]
    for r in sorted(_plan_rows(), key=lambda r: (r.get("Дата выхода") or dt.date.max, int(r["№"]))):
        d = r.get("Дата выхода")
        if not isinstance(d, dt.date) or not (today <= d <= today + dt.timedelta(days=days)):
            continue
        n = int(r["№"])
        has_img = os.path.isdir(os.path.join(sp.IMAGES_DIR, str(n))) and bool(sp.post_images(n, []))
        marks = ("🖼" if has_img else "⬜") + ("✏️" if os.path.exists(sp.final_text_path(n)) else "") \
            + ("📤" if str(n) in sent else "") + ("⏸" if n in hold else "")
        title = str(r.get("Заголовок на макете") or r.get("Рубрика / серия") or "")[:40]
        lines.append(f"{d:%d.%m} {wd[d.weekday()]} · №{n} {marks} {title}")
    lines.append("Посмотреть пост целиком — «покажи N».")
    return "\n".join(lines)


def _last_sent(state: dict) -> Optional[int]:
    """Номер поста, задание по которому отправлено дизайнеру последним."""
    sent = state.get("sent") or {}
    if not sent:
        return None
    return int(max(sent.items(), key=lambda kv: kv[1])[0])


def _download(file_id: str) -> bytes:
    info = _tg("getFile", file_id=file_id)
    try:
        r = requests.get(f"https://api.telegram.org/file/bot{_token()}/{info['file_path']}", timeout=120)
        r.raise_for_status()
    except requests.RequestException:
        raise DesignerBotError("Telegram не передал вложение.") from None
    return r.content


def _plan_arts(n: int) -> List[str]:
    for r in _plan_rows():
        if int(r["№"]) == n:
            return [a.strip() for a in str(r.get("Артикулы в master") or "").split(",") if a.strip() and a.strip() != "—"]
    return []


def _save_set(n, chat, messages, append):
    """Finish an interrupted write before assigning new numbered slots to a post."""
    import fcntl
    folder = Path(sp.IMAGES_DIR) / str(n)
    folder.mkdir(parents=True, exist_ok=True)
    journal = folder / ".designer-receipts.json"
    staging = folder / ".designer-pending"

    def visible():
        return {p.name for p in folder.iterdir() if not p.name.startswith(".")
                and p.suffix.lower() in sp.IMG_EXT and p.is_file()}

    def save(book):
        atomic_write(journal, json.dumps(book, sort_keys=True).encode())

    def finish(book):
        pending = book.get("pending")
        if pending:
            for name, digest in pending["write"].items():
                data = (staging / digest).read_bytes()
                if sha256(data).hexdigest() != digest:
                    raise DesignerBotError("Не совпала контрольная сумма вложения соцпоста.")
                atomic_write(folder / name, data)
            for name in pending["remove"]:
                (folder / name).unlink(missing_ok=True)
            book["done"].extend(pending["events"])
            del book["pending"]
            save(book)
        if staging.is_dir():
            # Includes unused blobs from a failure before a transaction was prepared.
            for blob in staging.iterdir():
                blob.unlink()
            staging.rmdir()

    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        book = json.loads(journal.read_bytes()) if journal.exists() else {"done": []}
        recovered = set(book.get("pending", {}).get("events", []))
        finish(book)
        seen = set(book["done"])
        events = {identity("social", chat, mid): (fid, ext) for mid, fid, ext in sorted(messages)}
        existing = visible()
        # A previously saved prefix may return with new members of the same album.
        append = append or bool(events.keys() & seen)
        fresh = {k: v for k, v in events.items() if k not in seen}
        recovered_count = len(events.keys() & recovered)
        if not fresh:
            return len(existing), recovered_count
        start = max((int(Path(p).stem) for p in existing if Path(p).stem.isdecimal()), default=0) if append else 0
        writes = {}

        def stage(data, ext):
            if not data:
                raise DesignerBotError("Получен пустой файл для соцпоста.")
            digest = sha256(data).hexdigest()
            if not (staging / digest).exists():
                atomic_write(staging / digest, data)
            writes[f"{start + len(writes) + 1}{ext if ext in sp.IMG_EXT else '.png'}"] = digest

        if append and not existing:
            for src in sp.auto_images(_plan_arts(n)):
                source = Path(src)
                stage(source.read_bytes(), source.suffix.lower())
        for fid, ext in fresh.values():
            stage(_download(fid), ext)
        book["pending"] = {"events": list(fresh), "write": writes,
                           "remove": sorted(existing - writes.keys()) if not append else []}
        save(book)
        finish(book)
        return len(visible()), len(fresh) + recovered_count
    finally:
        os.close(fd)


OUTBOX_DIR = "data/outbox"
OUTBOX_DONE = "data/outbox_done.json"


CARD_RE = re.compile(r"^\s*(?:карточк\w*|card)\b\s*[:\-–—]?\s*(.*?)\s*$", re.I)
POST_CAPTION = re.compile(r"(?:пост\s*)?№?\s*([1-9]\d*)\s*(\+|добав(?:ить)?)?", re.I)
AVITO_DIR = os.path.join(_ROOT, "photos_avito")


def _card_target(art_raw: str, avito: bool):
    """Папка карточки по артикулу из подписи дизайнера: photos/<арт>/ или photos_avito/<арт>/.
    Артикул должен уже существовать в photos/ (папку создаёт Claude, когда шлёт задание) — иначе None."""
    if not os.path.isdir(PHOTOS_DIR):
        return None, None
    requested = art_raw.strip()
    folders = [d for d in os.listdir(PHOTOS_DIR) if os.path.isdir(os.path.join(PHOTOS_DIR, d))]
    matches = [d for d in folders if d.casefold() == requested.casefold()]
    art = requested if requested in folders else (matches[0] if len(matches) == 1 else None)
    if not art:
        return None, None
    return art, os.path.join(AVITO_DIR if avito else PHOTOS_DIR, art)


def _card_route(label):
    suffix = re.search(r"\s+(?:авито|avito)$", label.strip(), re.I)
    sku = label.strip()[:suffix.start()] if suffix else label.strip()
    art, _ = _card_target(sku, bool(suffix))
    return {"sku": art, "channel": "avito" if suffix else "catalog"} if art else None


def _card_notice(archive, code, row, chat, owner):
    if row["notice"] == [row["target"], len(row["messages"])]:
        return
    count = len(row["messages"])
    target = row["target"]
    if target:
        channel = "Авито" if target["channel"] == "avito" else "каталог"
        total = len(archive.files_for(target["sku"], target["channel"]))
        text = f"Принял оригиналы для {target['sku']} ({channel}). В группе {count}, всего по карточке {total}."
    else:
        text = (f"Сохранил файлы без привязки ({count}). Пришлите «Привязать {code} АРТИКУЛ». "
                "Для Авито добавьте в конце «Авито».")
    persist_designer()
    result = _tg("sendMessage", chat_id=chat, text=text)
    archive.remember_receipt(row, chat, result)
    if target and owner and owner != chat:
        try:
            _tg("sendMessage", chat_id=owner, text=text)
        except DesignerBotError:
            logger.warning("Не удалось отправить владельцу уведомление о принятых оригиналах.")


def _route_images(archive, items, state, owner):
    groups = {}
    for item in items:
        groups.setdefault((item["chat"], item["gid"]), []).append(item)
    by_post = {}
    for (chat, gid), group in groups.items():
        group.sort(key=lambda item: item["mid"])
        code = identity("group", chat, gid)[:16]
        known = archive.lookup(code)
        cards, posts, other_caption = [], set(), False
        for item in group:
            caption = item["caption"].strip()
            card, post = CARD_RE.fullmatch(caption), POST_CAPTION.fullmatch(caption)
            if card:
                cards.append(_card_route(card.group(1)))
            elif post:
                posts.add((int(post.group(1)), bool(post.group(2))))
            elif caption:
                other_caption = True
            if item["reply"] is not None:
                route = archive.reply(chat, item["reply"])
                if route:
                    cards.append(route)
        references = {tuple(sorted(c.items())) for c in cards if c}
        conflict = (None in cards or len(references) > 1 or bool((cards or known) and posts)
                    or other_caption or len(posts) > 1)
        card_route = dict(next(iter(references))) if len(references) == 1 and not conflict else None
        social_routes = state.setdefault("post_media_groups", {})
        remembered = social_routes.get(code)
        if not cards and not known and not conflict:
            post = next(iter(posts), None)
            if post is None and remembered:
                post = (remembered, True)
            if post is None and group[0]["post"] is not None:
                post = (group[0]["post"], False)
            if post:
                n, append = post
                if group[0]["album"]:
                    social_routes[code] = n
                by_post.setdefault((n, append, chat), []).extend(
                    (i["mid"], i["fid"], i["ext"]) for i in group)
                continue
        files = [(i["mid"], i["fid"], i["ext"]) for i in group]
        code, record = archive.accept(chat, gid, files, card_route, _download, conflict=bool(conflict))
        _card_notice(archive, code, record, chat, owner)
    return by_post


def _send_plain(chat, text):
    part, size = [], 0
    for char in text:
        units = len(char.encode("utf-16-le")) // 2
        if size + units > 4000:
            _tg("sendMessage", chat_id=chat, text="".join(part))
            part, size = [], 0
        part.append(char)
        size += units
    if part:
        _tg("sendMessage", chat_id=chat, text="".join(part))


def _flush_tasks(store, designer, owner):
    report = store.flush(_tg, designer=designer, owner=owner, checkpoint=persist_designer)
    if report["uncertain"] or report["unlinked"]:
        print("::warning::В реестре есть уведомления с неподтверждённой доставкой. Откройте задачу по ID.")


def _process_outbox(designer, owner) -> int:
    """Сообщения от Claude: файлы data/outbox/*.json вида {"to": "designer"|"owner", "text": "...", "images": ["social/5/1.png"]}.
    Каждый отправляется один раз (список отправленных — data/outbox_done.json)."""
    if not os.path.isdir(OUTBOX_DIR):
        return 0
    done = _load(OUTBOX_DONE, [])
    store = CardTasks(_ROOT)
    sent = 0
    for name in sorted(os.listdir(OUTBOX_DIR)):
        if not name.endswith(".json"):
            continue
        packet_path = os.path.join(OUTBOX_DIR, name)
        msg = _load(packet_path, {})
        if not isinstance(msg, dict):
            msg = {}
        def flag(reason):
            with open(packet_path, "rb") as packet_file:
                signature = sha256(packet_file.read()).hexdigest()
            store.outbox_problem(name, signature, reason)
        if "card_task" in msg:
            packet = msg["card_task"]
            # An edit in the original file is a new event only when it explicitly
            # names its target and prior revision. Replay of the same edit is safe.
            event = name
            if isinstance(packet, dict) and packet.get("action") == "edit":
                event = f"{name}#edit-{packet.get('id')}-v{packet.get('version')}"
            try:
                store.ingest_outbox(event, packet)
            except CardTaskError:
                flag("invalid_card_task")
                print("::warning::Одно задание из outbox требует уточнения.")
            continue
        if name in done:
            continue
        if not msg or msg.get("to") not in ("owner", "designer"):
            flag("invalid_packet")
            continue
        if (msg["to"] == "designer" and msg.get("kind") not in ("note", "social_post", "video")
                and not msg.get("mark_sent") and looks_like_card_change(str(msg.get("text") or ""), msg.get("images") or [])):
            flag("missing_kind")
            continue
        chat = owner if msg.get("to") == "owner" else designer
        if not chat:
            print(f"outbox {name}: получатель не привязан — пропуск.")
            continue
        imgs = msg.get("images", [])
        if not isinstance(imgs, list) or any(not isinstance(x, str) or not os.path.isfile(x) for x in imgs):
            flag("missing_attachment")
            continue
        if msg.get("text"):
            _send_plain(chat, msg["text"])
        if imgs:
            _send_images(chat, imgs, as_files=bool(msg.get("as_files")))
        for extra in msg.get("texts", []):
            _send_plain(chat, str(extra))
        if msg.get("mark_sent"):  # посты, задание по которым уже ушло этим сообщением (чтобы 09:15 не отправило повторно)
            st = _load(STATE_PATH, {})
            for n in msg["mark_sent"]:
                st.setdefault("sent", {})[str(n)] = dt.datetime.now(sp.MSK).isoformat(timespec="seconds")
            _save(STATE_PATH, st)
        if owner and chat != owner:
            head = (msg.get("text") or "").strip().splitlines()[0][:80] if msg.get("text") else "(только картинки)"
            _tg("sendMessage", chat_id=owner, text=f"✅ Отправил дизайнеру: {head}" + (f" (+{len(imgs)} карт.)" if imgs else ""))
        done.append(name)
        _save(OUTBOX_DONE, done)
        sent += 1
        print(f"outbox {name}: отправлено ({msg.get('to')}).")
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
    store = CardTasks(_ROOT)
    _process_outbox(designer, owner)
    state = _load(STATE_PATH, {})
    params = {"timeout": 0, "allowed_updates": json.dumps(["message"])}
    if state.get("offset"):
        params["offset"] = state["offset"]
    ups = _tg("getUpdates", **params)
    if not ups:
        _flush_tasks(store, designer, owner)
        print("Новых сообщений нет.")
        return 0
    last_number = {}  # привязка только к предшествующему сообщению этого чата
    archive = CardArchive(_ROOT, _token())
    checkpoint = int(state.get("offset", 0))
    next_offset = checkpoint
    texts_new = []
    fwd_groups = set()  # альбомы владельца, которые надо переслать дизайнеру
    items = []          # (sender_chat, message_id, gid, file_id, ext)
    task_albums = {}
    for update in ups:
        item = update.get("message") or {}
        label = submission_caption(item)
        if label and item.get("media_group_id"):
            group = ((item.get("chat") or {}).get("id"), item["media_group_id"])
            task_albums.setdefault(group, set()).add(label)
    rejected_albums = set()
    for u in ups:
        if u["update_id"] < checkpoint:
            continue
        next_offset = max(next_offset, u["update_id"] + 1)
        m = u.get("message") or {}
        mchat = (m.get("chat") or {}).get("id")
        if mchat is None or mchat not in (designer, owner):
            continue
        is_owner = mchat == owner and mchat != designer
        raw = m.get("text") or m.get("caption") or ""
        gid = m.get("media_group_id") or f"single{m['message_id']}"
        low = raw.strip().lower().replace("ё", "е")
        role = "owner" if is_owner else "designer"
        group_key = (mchat, m.get("media_group_id"))
        label = submission_caption(m)
        if group_key in task_albums or (label and m.get("photo") and not m.get("document")):
            msg_group = group_key if m.get("media_group_id") else (mchat, m["message_id"])
            if msg_group not in rejected_albums:
                _tg("sendMessage", chat_id=mchat, text="Сохраню эти изображения как исходники. На проверку сдайте один слайд файлом без сжатия с подписью «Сдать ID vN».")
                rejected_albums.add(msg_group)
            m = {**m, "caption": archive_caption(store, task_albums.get(group_key, {label}))}
            label = None
        if label:
            try:
                receive(store, role, u["update_id"], m, label, _download)
            except CardTaskError as exc:
                _tg("sendMessage", chat_id=mchat, text=str(exc))
            continue
        if m.get("text"):
            command_text = "Помощь по карточкам" if low in ("/start", "/help") else raw
            if store.handle_text(role, command_text, u["update_id"]):
                continue
        if m.get("text") and re.match(r"^\s*привязать\b", raw, re.I):
            command = re.fullmatch(r"\s*Привязать\s+([0-9a-f]{16})\s+(.+?)\s*", raw, re.I)
            route = _card_route(command.group(2)) if command else None
            if not route:
                _tg("sendMessage", chat_id=mchat, text="Укажите код альбома и известный точный артикул. Для Авито добавьте «Авито».")
                continue
            code = command.group(1).lower()
            try:
                row = archive.bind(mchat, is_owner, code, route)
            except ValueError as exc:
                _tg("sendMessage", chat_id=mchat, text=str(exc))
            else:
                _card_notice(archive, code, row, mchat, owner)
            continue
        # --- дизайнер просит задания наперёд: «ещё» / «ещё 5»
        me = re.fullmatch(r"(?:еще|дальше|следующ\w*)\s*(\d+)?", low)
        if not is_owner and me and not (m.get("photo") or m.get("document")):
            k = min(int(me.group(1) or 3), 10)
            _save(STATE_PATH, state)
            got = _send_more(k)
            state = _load(STATE_PATH, {})
            if not got:
                _tg("sendMessage", chat_id=mchat, text="Следующие посты сейчас на проверке у владельца — задания придут, как только пройдёт проверка (обычно на следующий день) 👍")
            print(f"Дизайнер попросил задания наперёд: отправлено {got}.")
            continue
        # --- команды владельца
        if is_owner and low in ("план", "plan", "статус"):
            _tg("sendMessage", chat_id=mchat, text=_plan_summary()[:4000])
            continue
        ms = re.fullmatch(r"(?:покажи|показать|пост)\s*№?\s*(\d+)", low)
        if is_owner and ms:
            _save(STATE_PATH, state)
            cmd_owner_preview(ms.group(1))
            state = _load(STATE_PATH, {})
            continue
        if is_owner:
            mm = re.fullmatch(r"\s*(стоп|старт)\s*№?\s*(\d+)\s*", raw.lower())
            if mm:
                hold = _read_hold()
                num = int(mm.group(2))
                if mm.group(1) == "стоп":
                    hold.add(num)
                    reply = f"⏸ Пост №{num} поставлен на паузу: задание дизайнеру не уйдёт."
                else:
                    hold.discard(num)
                    reply = f"▶️ Пост №{num} снят с паузы."
                _write_hold(hold)
                _tg("sendMessage", chat_id=mchat, text=reply)
                print(reply)
                continue
            fw = re.match(r"^\s*дизайнер\w*\b\s*(?:№?\s*([1-9]\d*)(?=\s|[:\-–—]|$))?\s*[:\-–—]?\s*(.*)$", raw, re.S | re.I)
            if fw and (fw.group(2).strip() or m.get("photo") or m.get("document")):
                num = fw.group(1)
                if num is None and looks_like_card_change(raw):
                    _tg("sendMessage", chat_id=owner, text="Для правки товара укажите «Правка SKU слайд N». Со следующей строки напишите, что изменить, и «Текст:» с текстом макета. Для дополнения к соцпосту укажите «Дизайнеру 12: …».")
                    continue
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
        tn_guess = None if (not mt or mt.group(1)) else _single_open(state, "text")
        if mt and mt.group(2).strip() and not mt.group(1) and not tn_guess:
            _tg("sendMessage", chat_id=mchat, text="Не понял, к какому посту текст. Начните со слов «Текст 12» (номер поста) и пришлите ещё раз.")
            continue
        if mt and mt.group(2).strip() and (mt.group(1) or tn_guess):
            tn = int(mt.group(1)) if mt.group(1) else tn_guess
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
            last_number[mchat] = int(re.findall(r"\d+", text)[0])
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
        if cap and not POST_CAPTION.fullmatch(cap.strip()):
            last_number.pop(mchat, None)
        items.append({"chat": mchat, "gid": gid, "mid": m["message_id"], "fid": fid,
                      "ext": ext, "caption": cap, "post": last_number.get(mchat),
                      "album": bool(m.get("media_group_id")),
                      "reply": (m.get("reply_to_message") or {}).get("message_id")})
    by_post = _route_images(archive, items, state, owner)
    state.pop("last_number", None)
    for tn in texts_new:
        _notify_owner_text(tn)
    for (n, app, mchat), lst in by_post.items():
        total, added = _save_set(n, mchat, lst, app)
        if not added:
            continue
        who = "владельца" if mchat == owner and mchat != designer else "дизайнера"
        _tg("sendMessage", chat_id=mchat, text=f"✅ Принял пост №{n}: {'добавлено ' + str(added) + ', всего ' + str(total) if app else 'файлов ' + str(total)}. Спасибо!")
        print(f"Пост №{n} ({who}): {'добавлено' if app else 'заменено'} файлов {added}, всего {total} в social/{n}/")
        if mchat == designer and mchat != owner:
            _notify_owner(n)
    _flush_tasks(store, designer, owner)
    state["offset"] = next_offset
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
