"""Send existing product videos to the designer for manual publication."""
import json
from pathlib import Path

import designer_bot as bot

QUEUE = Path("data/designer_video_queue.json")
STATE = Path("data/designer_video_sent.json")


def main():
    queue = json.loads(QUEUE.read_text())
    sent = bot._load(STATE, {})
    pending = [entry for entry in queue if entry["file"] not in sent]
    if not pending:
        print("Все готовые видео отправлены дизайнеру.")
        return

    chat = bot._chat_id()
    if not chat:
        raise RuntimeError("Чат дизайнера не привязан")
    entry = pending[0]
    path = Path(entry["file"])
    index = queue.index(entry) + 1
    caption = (
        f"Готовое видео {index}/{len(queue)} для YouTube и Rutube\n"
        f"Связанные карточки: {', '.join(entry['articles'])}\n\n"
        "Проверь, выходил ли ролик на каждом канале. Опубликуй его там, где его ещё нет. "
        "Заголовок и описание составь по содержанию ролика. "
        "Добавь в описание ссылку на https://leoparts-akpp.ru/catalog и пришли ссылки на публикации."
    )
    with path.open("rb") as video:
        result = bot._tg("sendDocument", chat_id=chat, caption=caption,
                         files={"document": (path.name, video, "video/mp4")})
    sent[entry["file"]] = result["message_id"]
    bot._save(str(STATE), sent)
    print(f"Дизайнеру отправлено видео {index}/{len(queue)}: {path.name}")


if __name__ == "__main__":
    main()
