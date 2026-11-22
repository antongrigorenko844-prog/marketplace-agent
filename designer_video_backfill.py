"""One-time, crash-safe delivery of publication texts for legacy videos sent without copy.

Only legacy Telegram video messages with integer IDs in designer_video_sent.json.
No video reupload, no social publication, no change to product/ads/stock.
Persist a sending marker to GitHub BEFORE contacting Telegram; never resend
automatically after an uncertain send.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import designer_bot as bot
from designer_video import QUEUE, STATE, publication_text

JOURNAL = Path("data/designer_video_copy_backfill.json")
MAX_TEXT_LENGTH = 4096


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def persist(items):
    JOURNAL.parent.mkdir(parents=True, exist_ok=True)
    bot._save(str(JOURNAL), items)
    subprocess.run(["bash", ".github/scripts/save_data.sh", str(JOURNAL)], check=True)


def text_for(entry, index, total):
    text = publication_text(entry, Path(".")).decode("utf-8")
    heading = (
        f"LEOPARTS — ГОТОВЫЕ ТЕКСТЫ К РАНЕЕ ОТПРАВЛЕННОМУ ВИДЕО {index}/{total}\n"
        f"Файл: {Path(entry['file']).name}\n"
        f"Артикулы: {', '.join(entry['articles'])}\n\n"
    )
    message = heading + text
    if len(message) > MAX_TEXT_LENGTH:
        raise ValueError("Telegram message limit exceeded; do not truncate publication text")
    return message


def validate_receipt(reply, chat):
    if not isinstance(reply, dict):
        raise ValueError("Telegram returned no message")
    mid = reply.get("message_id")
    peer = reply.get("chat")
    if (type(mid) is not int or mid < 1 or
        not isinstance(peer, dict) or str(peer.get("id")) != str(chat)):
        raise ValueError("Telegram did not confirm the recipient/message")
    return mid


def run():
    queue = load(QUEUE)
    sent = load(STATE)
    if not isinstance(queue, list) or not isinstance(sent, dict):
        raise ValueError("Unexpected video queue / sent ledger")
    old = [(index, entry, sent[entry["file"]]) for index, entry in enumerate(queue, 1)
           if isinstance(entry, dict) and type(sent.get(entry.get("file"))) is int]
    if not old:
        print("No legacy videos requiring text backfill.")
        return
    if not bot._token() or not bot._chat_id():
        raise RuntimeError("Telegram designer chat is not connected")
    # Build all copy before sending anything. Every source file has a reviewed
    # digest, platform-specific wording and Wordstat evidence.
    prepared = []
    for index, entry, video_id in old:
        if video_id < 1:
            raise ValueError("Invalid original video message ID")
        message = text_for(entry, index, len(queue))
        prepared.append((entry["file"], video_id, message,
                         hashlib.sha256(message.encode("utf-8")).hexdigest()))
    ledger = load(JOURNAL) if JOURNAL.exists() else {"version": 1, "items": {}}
    if ledger.get("version") != 1 or not isinstance(ledger.get("items"), dict):
        raise ValueError("Unrecognized backfill delivery journal")
    # Ensure no changed contents are silently considered sent.
    for key, video_id, message, digest in prepared:
        row = ledger["items"].get(key)
        if row and (row.get("video_message_id") != video_id or row.get("text_sha256") != digest):
            raise ValueError("Copy changed after reserving/sending " + key)
        if row and row.get("status") not in ("sent", "sending"):
            raise ValueError("Unexpected recovery status for " + key)
    for key, video_id, message, digest in prepared:
        row = ledger["items"].get(key)
        if row and row.get("status") == "sent":
            print("Already sent copy for", key)
            continue
        if row and row.get("status") == "sending":
            print("Uncertain earlier send, no automatic repeat for", key)
            continue
        # Commit the fence before interacting with Telegram. A failure leaves a
        # conservative 'sending' record rather than a potentially duplicated send.
        ledger["items"][key] = {
            "status": "sending", "video_message_id": video_id,
            "text_sha256": digest, "operation": os.getenv("GITHUB_RUN_ID", "manual")
        }
        persist(ledger)
        result = bot._tg("sendMessage", chat_id=bot._chat_id(),
                         text=message, disable_web_page_preview="true")
        ledger["items"][key] = {
            **ledger["items"][key], "status": "sent",
            "text_message_id": validate_receipt(result, bot._chat_id())
        }
        persist(ledger)
        print("Delivered all three platform publication texts for", key)
    n = sum(x.get("status") == "sent" for x in ledger["items"].values())
    print(f"Confirmed backfill messages: {n}/{len(prepared)}")


if __name__ == "__main__":
    try:
        run()
    except Exception as exc:
        # Bot token and Telegram message data must never be logged.
        print("Copy backfill blocked or unverified:", type(exc).__name__, str(exc)[:180], file=sys.stderr)
        sys.exit(1)
