"""Append-only archive for authorized designer attachments. Unbound images stay encrypted."""
import base64
import hashlib
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile

from cryptography.fernet import Fernet, InvalidToken


def identity(*parts):
    encoded = json.dumps([str(part) for part in parts], ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def atomic_write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile(dir=path.parent, delete=False) as stream:
        name = stream.name
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        except BaseException:
            os.unlink(name)
            raise
    try:
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


class CardArchive:
    def __init__(self, root, token):
        self.root = Path(root) / "data" / "designer_card_media"
        self.index = self.root / "index.json"
        self.snapshot = self.index.read_bytes() if self.index.exists() else None
        self.groups = json.loads(self.snapshot) if self.snapshot is not None else {}
        key = hashlib.sha256(b"leoparts/designer/unbound/v1\0" + token.encode()).digest()
        self.cipher = Fernet(base64.urlsafe_b64encode(key))

    def save(self):
        # The GitHub runner is Linux. Lock the directory without adding a lock
        # file to the repository, and reject a concurrent collector's stale view.
        import fcntl
        self.root.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.root, os.O_RDONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            before = self.index.read_bytes() if self.index.exists() else None
            if before != self.snapshot:
                raise RuntimeError("Журнал изменился. Повторите опрос со свежей копией.")
            after = json.dumps(self.groups, ensure_ascii=False, indent=2).encode("utf-8")
            if after != before:
                atomic_write(self.index, after)
            self.snapshot = after
        finally:
            os.close(fd)

    def lookup(self, code):
        return self.groups.get(code)

    def reply(self, chat, mid):
        key = identity("message", chat, mid)
        for record in self.groups.values():
            if record.get("target") and (key in record["messages"] or key in record["receipts"]):
                return record["target"]
        return None

    def files_for(self, sku, channel):
        """Ordered submitted positions; two distinct messages can refer to the same blob."""
        target = {"sku": sku, "channel": channel}
        found = {}
        for group in self.groups.values():
            if group["target"] == target:
                for message, asset in group["messages"].items():
                    found.setdefault(message, self._path(target, asset))
        return list(found.values())

    def _path(self, target, asset):
        if not re.fullmatch(r"[a-f0-9]{64}", asset["sha256"]) or not re.fullmatch(r"\.[a-z]{1,5}", asset["ext"]):
            raise ValueError("Некорректное имя вложения в архиве.")
        if target is None:
            return self.root / "unbound" / (asset["sha256"] + ".fernet")
        sku = target["sku"]
        if not sku or sku in (".", "..") or Path(sku).name != sku or target["channel"] not in ("catalog", "avito"):
            raise ValueError("Неверное направление вложения.")
        return self.root / "cards" / target["channel"] / sku / (asset["sha256"] + asset["ext"])

    def _store(self, target, asset, data):
        path = self._path(target, asset)
        if target and path.parent.exists():
            existing = next(path.parent.glob(asset["sha256"] + ".*"), None)
            if existing is not None:
                path = existing
                asset["ext"] = path.suffix
        if not path.exists():
            atomic_write(path, self.cipher.encrypt(data) if target is None else data)
        elif target is not None and hashlib.sha256(path.read_bytes()).hexdigest() != asset["sha256"]:
            raise ValueError("Содержимое архива не совпало с контрольной суммой.")

    def accept(self, chat, gid, files, target, download, *, conflict=False):
        code = identity("group", chat, gid)[:16]
        actor = identity("actor", chat)
        old = self.groups.get(code)
        if old and old["actor"] != actor:
            raise ValueError("Конфликт идентификаторов альбомов.")
        if old and old.get("conflict_code"):
            if all(identity("message", chat, mid) in old["messages"] for mid, _, _ in files):
                return code, old
            files = [(mid, fid, ext) for mid, fid, ext in files if identity("message", chat, mid) not in old["messages"]]
            quarantine = old["conflict_code"]
            code = quarantine
            resolved = self.groups.get(quarantine, {})
            if resolved.get("target") and (conflict or target != resolved["target"]):
                if all(identity("message", chat, mid) in resolved["messages"] for mid, _, _ in files):
                    return code, resolved
                code = identity("unbound continuation", chat, gid, *(mid for mid, _, _ in files))[:16]
            old = self.groups.get(code)
            target, conflict = None, True
        elif old and old["target"] and (conflict or (target and old["target"] != target)):
            # A contradictory continuation cannot change a previously confirmed album.
            code = identity("conflict", chat, gid)[:16]
            old["conflict_code"] = code
            files = [(mid, fid, ext) for mid, fid, ext in files if identity("message", chat, mid) not in old["messages"]]
            old = self.groups.get(code)
            target, conflict = None, True
        if old is None:
            old = {"actor": actor, "target": None, "messages": {}, "receipts": [], "notice": None}
        actual = old["target"] or (None if conflict else target)
        fresh = []
        for mid, file_id, ext in files:
            key = identity("message", chat, mid)
            if key in old["messages"]:
                continue
            try:
                data = download(file_id)
            except Exception:
                raise RuntimeError("Не удалось скачать вложение. Опрос будет повторён.") from None
            if not data:
                raise ValueError("Получен пустой файл.")
            suffix = ext if ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff") else ".img"
            fresh.append((key, {"sha256": hashlib.sha256(data).hexdigest(), "ext": suffix}, data))
        if actual and not old["target"]:
            self._materialize(old, actual)
        for key, asset, data in fresh:
            self._store(actual, asset, data)
            old["messages"][key] = asset
        old["target"] = actual
        self.groups[code] = old
        self.save()
        return code, old

    def _materialize(self, record, target):
        for asset in record["messages"].values():
            try:
                data = self.cipher.decrypt(self._path(None, asset).read_bytes())
            except InvalidToken:
                raise ValueError("Ключ изменился. Пришлите этот альбом повторно с подписью.") from None
            if hashlib.sha256(data).hexdigest() != asset["sha256"]:
                raise ValueError("Контрольная сумма вложения не совпала.")
            self._store(target, asset, data)

    def bind(self, chat, owner, code, target):
        row = self.groups.get(code)
        if row is None or (not owner and row["actor"] != identity("actor", chat)):
            raise ValueError("Альбом с таким кодом недоступен.")
        if row["target"] and row["target"] != target:
            raise ValueError("Альбом уже привязан к другой карточке.")
        if not row["target"]:
            self._materialize(row, target)
            row["target"] = target
            self.save()
        return row

    def remember_receipt(self, row, chat, result):
        if isinstance(result, dict) and type(result.get("message_id")) is int:
            key = identity("message", chat, result["message_id"])
            if key not in row["receipts"]:
                row["receipts"].append(key)
        row["notice"] = [row["target"], len(row["messages"])]
        self.save()
