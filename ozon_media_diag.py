"""
ozon-media-diag (только чтение): что Ozon РЕАЛЬНО хранит по видео, фото и rich-контенту
карточек, у которых в photos/<АРТИКУЛ>/ лежит видео, плюс карточки из теста rich-контента.

Зачем: кэш ozon_push_cache.json говорит «отправлено», а контент-рейтинг Ozon по части
карточек видео/rich не засчитывает. Здесь видно, где правда застряло:
  - complex-атрибут видео (21841 ссылка / 21837 название) есть или нет;
  - rich-контент (атрибут 11254) есть или нет;
  - сколько фото Ozon считает у карточки;
  - ошибки/предупреждения модерации из /v3/product/info/list (там пишут, если видео отклонено).

Итог -> data/ozon_media_diag.txt. Ничего на Ozon не меняет.
"""
import glob
import json
import os

import ozon_client as oc

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT = os.path.join(DATA, "ozon_media_diag.txt")
RICH_ID = 11254
VIDEO_URL_ID = 21841
VIDEO_NAME_ID = 21837
RICH_TEST = ["02e305045", "0BH325183"]


def _video_articles():
    out = []
    for f in glob.glob(os.path.join(os.path.dirname(DATA), "photos", "*", "*")):
        if os.path.splitext(f)[1].lower() in (".mp4", ".mov"):
            out.append(os.path.basename(os.path.dirname(f)))
    return sorted(set(out))


def cmd_ozon_media_diag() -> int:
    arts = _video_articles() + [a for a in RICH_TEST]
    arts = sorted(set(arts))
    lines = [f"Карточек к проверке: {len(arts)}"]
    attrs = {str(i.get("offer_id")): i for i in oc.get_product_names(arts)}
    infos = {str(i.get("offer_id")): i for i in oc.get_product_info_list(arts)}
    with open(os.path.join(DATA, "ozon_media_diag_raw.json"), "w", encoding="utf-8") as f:
        json.dump({"attributes": attrs, "info": infos}, f, ensure_ascii=False, indent=1)
    for a in arts:
        at = attrs.get(a) or {}
        inf = infos.get(a) or {}
        vids = []
        for ca in at.get("complex_attributes") or []:
            # в /v4/product/info/attributes complex-атрибуты плоские: {id, complex_id, values}
            for x in [ca] + list(ca.get("attributes") or []):
                if int(x.get("id", 0)) == VIDEO_URL_ID:
                    vids += [str(v.get("value")) for v in x.get("values") or []]
        rich = next((x for x in at.get("attributes") or [] if int(x.get("id", 0)) == RICH_ID), None)
        rich_len = len(json.dumps(rich.get("values"), ensure_ascii=False)) if rich else 0
        imgs = list(at.get("images") or []) + ([at["primary_image"]] if at.get("primary_image") else [])
        lines.append("")
        lines.append(f"== {a}: найден на Ozon: {'да' if at else 'НЕТ'}")
        lines.append(f"   фото у Ozon: {len(imgs)}; видео (21841): {len(vids)} {'; '.join(v.split('/')[-1] for v in vids)[:200]}")
        lines.append(f"   rich-контент (11254): {'есть, ' + str(rich_len) + ' знаков' if rich else 'НЕТ'}")
        st = inf.get("statuses") or inf.get("status") or {}
        if st:
            lines.append(f"   статус: {json.dumps(st, ensure_ascii=False)[:400]}")
        errs = inf.get("errors") or []
        for e in errs[:10]:
            lines.append(f"   ОШИБКА/предупреждение: {json.dumps(e, ensure_ascii=False)[:500]}")
        if not errs:
            lines.append("   ошибок модерации нет")
    os.makedirs(DATA, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0
