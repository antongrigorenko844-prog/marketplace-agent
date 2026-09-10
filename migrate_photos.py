"""
ОДНОРАЗОВЫЙ скрипт миграции: раскладывает существующие плоские файлы
photos/<артикул>_N.ext по папкам photos/<артикул>/<артикул>_N.ext (имя
файла НЕ меняется — только переносится в свою папку, поэтому уже выданные
ссылки на GitHub Release не меняются, см. catalog_editor.asset_filename_for).

Повторяет ТУ ЖЕ логику подбора владельца файла, что раньше была в
build_master_control.py (_candidates/_match_known, теперь оттуда убрана,
т.к. больше не нужна в обычной работе) — используется здесь только один
раз, чтобы понять, кому какой файл принадлежит, ПЕРЕД тем как каждый товар
получит свою папку и эта логика вообще перестанет быть нужна.
"""
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import build_master_control as bmc

PHOTOS_DIR = bmc.PHOTOS_DIR


def _candidates(fname, known_ids, case_sensitive):
    base, ext = os.path.splitext(fname)
    f_cmp = fname if case_sensitive else fname.lower()
    e_cmp = ext if case_sensitive else ext.lower()
    out = []
    for k in known_ids:
        k_cmp = k if case_sensitive else k.lower()
        if f_cmp == (k_cmp + e_cmp) or f_cmp.startswith(k_cmp + "_") or f_cmp.startswith(k_cmp + "-"):
            out.append(k)
    return out


def _match_known(fname, known_ids):
    exact = _candidates(fname, known_ids, case_sensitive=True)
    if exact:
        return max(exact, key=len)
    loose = _candidates(fname, known_ids, case_sensitive=False)
    if not loose:
        return None
    return max(loose, key=len)


def main():
    existing = bmc._read_existing()
    draft = bmc._read_draft()
    wb_existing = bmc._apply_wb_alias(bmc._read_wb_existing())
    wb_draft = bmc._apply_wb_alias(bmc._read_wb_draft())
    ozon_ids = set(existing) | set(draft)
    wb_only_existing = {k: v for k, v in wb_existing.items() if k not in ozon_ids}
    wb_only_draft = {k: v for k, v in wb_draft.items() if k not in ozon_ids and k not in wb_only_existing}
    known_ids = sorted(ozon_ids | set(wb_only_existing) | set(wb_only_draft))

    top_level = sorted(
        f for f in os.listdir(PHOTOS_DIR)
        if os.path.isfile(os.path.join(PHOTOS_DIR, f))
        and os.path.splitext(f)[1].lower() in (bmc.IMAGE_EXTS | bmc.VIDEO_EXTS)
    )

    moved = []
    unmatched = []
    for f in top_level:
        hit = _match_known(f, known_ids)
        if not hit:
            unmatched.append(f)
            continue
        own_dir = os.path.join(PHOTOS_DIR, hit)
        os.makedirs(own_dir, exist_ok=True)
        src = os.path.join(PHOTOS_DIR, f)
        dst = os.path.join(own_dir, f)
        shutil.move(src, dst)
        moved.append((f, hit))

    print(f"Известных артикулов: {len(known_ids)}")
    print(f"Перенесено файлов: {len(moved)}")
    for f, oid in moved:
        print(f"  {f} -> {oid}/{f}")
    print(f"Не распознано (оставлены как есть, на верхнем уровне photos/): {len(unmatched)}")
    for f in unmatched:
        print(f"  {f}")


if __name__ == "__main__":
    main()
