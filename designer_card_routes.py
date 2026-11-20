"""Route a single-slide return to its task before handling general chat attachments."""
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from PIL import Image, UnidentifiedImageError

from designer_card_tasks import CardTaskError, ID, expected_submission, submission_ref


LEGACY = "ozon 0am325025 слайд 2"
FORMATS = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp"}


def looks_like_card_change(text, images=()):
    if re.search(r"\b(?:карточк|слайд|правк|исправ|замен|поправ|переимен|обложк)\w*", text, re.I):
        return True
    return any(isinstance(path, str) and path.startswith(("photos/", "photos_avito/")) for path in images)


def submission_caption(message):
    caption = (message.get("caption") or "").strip()
    if caption:
        if caption.casefold() == LEGACY or re.match(r"^сдать\b", caption, re.I):
            return caption
        return None
    parent = message.get("reply_to_message") or {}
    if parent.get("from", {}).get("is_bot") is not True:
        return None
    refs = set(re.findall(rf"\bСдать\s+({ID}\s+v\d+)\b", parent.get("text") or "", re.I))
    return "Сдать " + next(iter(refs)) if len(refs) == 1 else None


def archive_caption(store, labels):
    """A packet of work can be saved as source files without submitting a whole slide."""
    if len(labels) == 1:
        ref = submission_ref(next(iter(labels)))
        if ref:
            try:
                task = store.get(ref[0])
            except CardTaskError:
                task = None
            if task and task["version"] == ref[1]:
                return "Карточка " + task["sku"] + (" Авито" if task["platform"] == "Avito" else "")
    # Nonempty unrecognised captions are held privately until a person assigns a SKU.
    return "Требуется привязка исходников"


def single_image(raw, legacy):
    try:
        with Image.open(BytesIO(raw)) as img:
            fmt = img.format
            width, height = img.size
            img.verify()
    except (OSError, UnidentifiedImageError, ValueError, Image.DecompressionBombError):
        raise CardTaskError("Файл не удалось прочитать как изображение. Отправьте PNG, JPEG или WebP заново.") from None
    if fmt not in FORMATS:
        raise CardTaskError("Сохраните слайд как PNG, JPEG или WebP.")
    if legacy and (fmt not in {"JPEG", "PNG"} or width < 900 or width * 4 != height * 3):
        raise CardTaskError("Для этого слайда Ozon нужен PNG или JPEG 3:4 шириной от 900 px.")
    return FORMATS[fmt]


def put_original(target, raw):
    target.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=target.parent, delete=False) as stream:
        tmp = Path(stream.name)
        try:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
            try:
                os.link(tmp, target)
            except FileExistsError:
                if sha256(target.read_bytes()).digest() != sha256(raw).digest():
                    raise CardTaskError("Для этой версии уже сохранён другой файл. Откройте задачу и запросите новую версию.")
        finally:
            tmp.unlink(missing_ok=True)


def receive(store, role, event_id, message, caption, download):
    if role != "designer":
        raise CardTaskError("Готовый слайд сдаёт дизайнер. Владелец принимает его после проверки.")
    if message.get("media_group_id") or not (message.get("document") or message.get("photo")):
        raise CardTaskError("Для одной правки пришлите один слайд. Скопируйте подпись «Сдать ID vN» из задания.")
    legacy = caption.casefold() == LEGACY
    if not legacy:
        ref = submission_ref(caption)
        if not ref:
            raise CardTaskError("Нужны ID и версия. Скопируйте подпись «Сдать ID vN» из задания.")
        task = store.get(ref[0])
        if task["version"] != ref[1]:
            raise CardTaskError(f"Эта версия устарела. Откройте «Задача {task['id']}» для версии v{task['version']}.")
        if task["status"] not in ("assigned", "returned", "submitted"):
            raise CardTaskError("Правка закрыта. Для нового изменения нужна отдельная задача.")
    document = message.get("document")
    if not document:
        raise CardTaskError("Telegram сжимает фото. Пришлите этот слайд как один файл без сжатия с той же подписью.")
    file_id = (document or message["photo"][-1])["file_id"]
    raw = download(file_id)
    ext = single_image(raw, legacy or task.get("format_rule") == "legacy_ozon_slide")
    if legacy:
        task = store.adopt_legacy_ozon()
    rel = expected_submission(task["id"], task["version"], task["slide"], ext)
    target = Path(store.root) / rel
    if task["status"] in ("submitted", "accepted"):
        if task["artifact"] == rel and target.is_file() and sha256(raw).hexdigest() == task.get("artifact_sha256"):
            return
        raise CardTaskError("Для этой версии уже зарегистрирован другой файл. Откройте задачу для проверки.")
    put_original(target, raw)
    store.submit(role, task["id"], task["version"], event_id, rel)
