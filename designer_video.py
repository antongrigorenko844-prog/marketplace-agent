"""Send a product video, backup copy file and visible Telegram publication text."""
import argparse
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import sys

import designer_bot as bot
from video_copy import build_copy


PLATFORMS = ("YouTube", "RuTube", "TikTok")
QUEUE = Path("data/designer_video_queue.json")
STATE = Path("data/designer_video_sent.json")
COPY_NAME = "publication.txt"


class DeliveryError(RuntimeError):
    pass


def publication_text(entry, root):
    copy = build_copy(entry, root=root)
    if set(copy) != set(PLATFORMS):
        raise DeliveryError("Нужен текст для всех трёх площадок.")
    blocks = []
    for platform in PLATFORMS:
        item = copy[platform]
        fields = [item.get(key) for key in ("title", "description", "hashtags")]
        if any(not isinstance(value, str) or not value.strip() for value in fields):
            raise DeliveryError("В тексте публикации не заполнены заголовок, описание или хештеги.")
        title, description, hashtags = (value.strip() for value in fields)
        blocks.append(f"{platform}\n\nЗаголовок\n{title}\n\nОписание\n{description}\n\nХештеги\n{hashtags}")
    return ("\n\n\n".join(blocks) + "\n").encode("utf-8")


def payload(entry, root):
    path = root / entry["file"]
    if (not path.resolve().is_relative_to(root.resolve()) or path.suffix.lower() != ".mp4"
            or not path.is_file() or not path.stat().st_size):
        raise DeliveryError("Не найден непустой MP4 для очередной позиции.")
    text = publication_text(entry, root)
    digest = hashlib.sha256(text)
    with path.open("rb") as video:
        for chunk in iter(lambda: video.read(1024 * 1024), b""):
            digest.update(chunk)
    return path, text, digest.hexdigest()


def receipt_ids(result, chat, files):
    if not isinstance(result, list) or len(result) != 2:
        raise DeliveryError("Нет подтверждения доставки обоих документов.")
    ids, groups, file_ids = [], [], []
    for message, (expected_name, expected_size) in zip(result, files):
        if not isinstance(message, dict):
            raise DeliveryError("Неполный ответ Telegram.")
        number = message.get("message_id")
        group = message.get("media_group_id")
        document = message.get("document")
        peer = message.get("chat")
        if (type(number) is not int or number <= 0 or not isinstance(group, str) or not group
                or not isinstance(peer, dict) or str(peer.get("id")) != str(chat)
                or not isinstance(document, dict) or not isinstance(document.get("file_id"), str)
                or not document["file_id"]):
            raise DeliveryError("Не подтверждены документы, альбом или получатель.")
        # Telegram's Document.file_name is optional. If omitted, confirm the
        # uploaded file by size at the corresponding position in the album.
        name = document.get("file_name")
        size = document.get("file_size")
        if (name is not None and name != expected_name) or (size is not None and size != expected_size):
            raise DeliveryError("Telegram вернул другой документ.")
        if name is None and size is None:
            raise DeliveryError("Нельзя подтвердить состав файлов в альбоме.")
        ids.append(number)
        groups.append(group)
        file_ids.append(document["file_id"])
    if ids[0] == ids[1] or groups[0] != groups[1] or file_ids[0] == file_ids[1]:
        raise DeliveryError("Telegram не подтвердил ожидаемую пару документов.")
    return ids


def confirmed(record):
    # Existing records contain the one Telegram message ID from the old sender.
    if type(record) is int and record > 0:
        return True
    if isinstance(record, dict) and record.get("status") == "sent":
        ids = record.get("message_ids")
        if (isinstance(ids, list) and len(ids) == 2 and all(type(i) is int and i > 0 for i in ids)
                and ids[0] != ids[1]):
            return True
    return False


def deliver(phase, operation, root=Path(".")):
    state_path = root / STATE
    # A missing/corrupt ledger must never make all old videos pending again.
    state = json.loads(state_path.read_text(encoding="utf-8"))
    queue = json.loads((root / QUEUE).read_text(encoding="utf-8"))
    if not isinstance(state, dict) or not isinstance(queue, list):
        raise DeliveryError("Нарушен формат очереди или журнала отправки.")
    if any(isinstance(row, dict) and row.get("operation") == operation and confirmed(row)
           for row in state.values()):
        return "already_sent"
    for index, entry in enumerate(queue, 1):
        key = entry["file"]
        if key not in state or not confirmed(state[key]):
            break
    else:
        return "queue_complete"
    record = state.get(key)
    if key in state and (not isinstance(record, dict) or record.get("status") != "reserved"
                         or record.get("operation") != operation):
        raise DeliveryError("Предыдущая отправка требует сверки перед повтором.")
    chat = bot._chat_id()
    if not chat or not bot._token():
        raise DeliveryError("Чат дизайнера или доступ к Telegram не настроен.")
    path, text, digest = payload(entry, root)
    if phase == "prepare":
        if key not in state:
            state[key] = {"status": "reserved", "operation": operation, "digest": digest}
            bot._save(str(state_path), state)
        elif record.get("digest") != digest:
            raise DeliveryError("Материалы изменились после регистрации отправки.")
        return "reserved"
    if phase != "send" or record is None or record.get("digest") != digest:
        raise DeliveryError("Сначала нужно сохранить резервирование этой версии материалов.")
    caption = (f"Видео {index} из {len(queue)} для YouTube, RuTube и TikTok.\n"
               "Готовые заголовки, описания и хештеги — в следующем сообщении. "
               "Файл publication.txt приложен как резервная копия. "
               "После публикации пришлите ссылки.")
    media = [{"type": "document", "media": "attach://video", "caption": caption},
             {"type": "document", "media": "attach://copy"}]
    plain = text.decode("utf-8").rstrip()
    if len(plain.encode("utf-16-le")) // 2 <= 1024:
        media[1]["caption"] = plain
    # Publication text must be visible directly in Telegram, not only inside
    # an attached .txt document. Never truncate any platform section.
    if len(plain) > 4096:
        raise DeliveryError("Полный текст не помещается в одно сообщение Telegram.")
    # The workflow has already pushed the reservation. A new Actions attempt
    # cannot reuse it, including after remote acceptance and a runner crash.
    record["status"] = "sending"
    bot._save(str(state_path), state)
    try:
        with path.open("rb") as video, BytesIO(text) as copy_file:
            result = bot._tg("sendMediaGroup", chat_id=chat, media=json.dumps(media, ensure_ascii=False),
                             files={"video": (path.name, video, "video/mp4"),
                                    "copy": (COPY_NAME, copy_file, "text/plain")})
        ids = receipt_ids(result, chat, ((path.name, path.stat().st_size), (COPY_NAME, len(text))))
        visible = bot._tg("sendMessage", chat_id=chat, text=plain,
                          disable_web_page_preview="true")
        text_id = visible.get("message_id") if isinstance(visible, dict) else None
        peer = visible.get("chat") if isinstance(visible, dict) else None
        if (type(text_id) is not int or text_id <= 0 or
                not isinstance(peer, dict) or str(peer.get("id")) != str(chat) or text_id in ids):
            raise DeliveryError("Telegram не подтвердил видимый текст для трёх площадок.")
    except Exception:
        record["status"] = "needs_review"
        bot._save(str(state_path), state)
        raise DeliveryError("Ответ Telegram не подтвердил полную отправку. Нужна сверка перед повтором.") from None
    state[key] = {**record, "status": "sent", "message_ids": ids, "text_message_id": text_id}
    bot._save(str(state_path), state)
    return "sent"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("phase", choices=("prepare", "send"))
    phase = parser.parse_args().phase
    run = os.getenv("GITHUB_RUN_ID", "")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "")
    if not run.isdecimal() or not attempt.isdecimal():
        raise DeliveryError("Требуется идентификатор запуска GitHub Actions и номер попытки.")
    print(deliver(phase, run + ":" + attempt))


if __name__ == "__main__":
    try:
        main()
    except DeliveryError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
