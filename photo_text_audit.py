"""
Проверка текста НА КАРТИНКАХ (распознавание, tesseract rus): ищем надписи, которые нельзя или не нужно
показывать — «смотри видео» (видео в посте/карточке нет), «скидка», «%», «дешевле», «оптовикам»
(Авито отклоняет такие первые фото; в Instagram — признак рекламы). Отчёт: data/photo_text_audit.xlsx.
"""
import os
import re
import subprocess

_ROOT = os.path.dirname(os.path.abspath(__file__))
PATTERNS = {
    "видео": r"видео",
    "скидка": r"скидк",
    "процент %": r"%",
    "дешевле": r"дешевл",
    "оптовикам": r"оптов",
}


def _ensure_tesseract():
    try:
        out = subprocess.run(["tesseract", "--list-langs"], capture_output=True, text=True).stdout
        if "rus" in out:
            return
    except FileNotFoundError:
        pass
    subprocess.run("sudo apt-get update -qq && sudo apt-get install -y -qq tesseract-ocr tesseract-ocr-rus",
                   shell=True, check=True)


def cmd_photo_text_audit() -> int:
    import openpyxl
    _ensure_tesseract()
    rows = []
    for top in ("photos", "photos_avito", "social"):
        base = os.path.join(_ROOT, top)
        if not os.path.isdir(base):
            continue
        import audit as _audit
        ignored = _audit.load_ignore()  # data/audit_ignore.json — не проверять
        for folder in sorted(os.listdir(base)):
            full = os.path.join(base, folder)
            if not os.path.isdir(full) or _audit.loose(folder) in ignored:
                continue
            for f in sorted(os.listdir(full)):
                if not f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    continue
                path = os.path.join(full, f)
                try:
                    txt = subprocess.run(["tesseract", path, "-", "-l", "rus+eng"], capture_output=True, text=True, timeout=120).stdout
                except Exception as exc:  # noqa
                    txt = f"ОШИБКА: {exc}"
                low = txt.lower()
                hits = [k for k, rx in PATTERNS.items() if re.search(rx, low)]
                rows.append([top, folder, f, ", ".join(hits), " ".join(txt.split())[:300]])
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Картинки"
    ws.append(["Папка", "Артикул / пост", "Файл", "Найдено", "Распознанный текст (начало)"])
    for r in sorted(rows, key=lambda r: (r[3] == "", r[0], r[1], r[2])):
        ws.append(r)
    out = os.path.join(_ROOT, "data", "photo_text_audit.xlsx")
    wb.save(out)
    flagged = [r for r in rows if r[3]]
    print(f"Проверено картинок: {len(rows)}, с проблемными надписями: {len(flagged)} → {out}")
    return 0
