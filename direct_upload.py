"""
direct-upload-draft: загрузить кампанию из data/direct/<файл>.tsv в Яндекс Директ ЧЕРНОВИКОМ (API v5).

Что делает:
  кампания (Текстово-графическая, только поиск, сети выключены, ручные ставки = потолок цены клика,
  недельного бюджета нет, счётчик Метрики) -> группы (регион Россия без Чечни, минус-фразы группы)
  -> фразы (ставка BID_RUB) -> быстрые ссылки -> объявления.

Безопасность:
  - НИЧЕГО не отправляет на модерацию и не запускает: объявления и кампания остаются черновиками.
    Без отправки на модерацию и без денег на счёте показов нет и списаний нет.
  - Если кампания с таким названием уже есть в аккаунте — стоп (дубль не создаётся).
  - Итог и номера созданного -> data/direct_upload_result.txt и data/direct_state.json («draft»).

Файлы — из data/direct/upload.txt (по одному в строке), по умолчанию dq200_landing.tsv и dq200_errors.tsv.
direct-access-check — только проверка, одобрен ли доступ к API (ничего не создаёт).
"""
import csv
import json
import os
from collections import OrderedDict
from datetime import date
from typing import Dict, List

import requests

_ROOT = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(_ROOT, "data")
DIRECT_DIR = os.path.join(DATA, "direct")
RESULT = os.path.join(DATA, "direct_upload_result.txt")
STATE = os.path.join(DATA, "direct_state.json")
API = "https://api.direct.yandex.com/json/v5/"

COUNTER_ID = 113839860
BID_RUB = 60                      # решение владельца 28.10: потолок цены клика 60 ₽, без недельного бюджета
REGIONS = [225, -11024]           # Россия, кроме Чеченской Республики
CAMPAIGN_NAMES = {"dq200_landing.tsv": "Мехатроники DQ200 — посадочная",
                  "dq200_site.tsv": "Мехатроники DQ200 — карточки сайта",
                  "dq200_errors.tsv": "Ошибки DSG7 DQ200 — посадочная"}

_log: List[str] = []


def say(s: str) -> None:
    _log.append(s)
    print(s)


class DirectError(RuntimeError):
    pass


def call(service: str, method: str, params: dict) -> dict:
    token = os.getenv("YANDEX_DIRECT_TOKEN", "")
    h = {"Authorization": "Bearer " + token, "Accept-Language": "ru", "Content-Type": "application/json; charset=utf-8"}
    login = os.getenv("YANDEX_DIRECT_LOGIN", "")
    if login:
        h["Client-Login"] = login
    r = requests.post(API + service, json={"method": method, "params": params}, headers=h, timeout=120)
    try:
        d = r.json()
    except ValueError:
        raise DirectError(f"{service}.{method}: HTTP {r.status_code} {r.text[:300]}")
    if "error" in d:
        e = d["error"]
        raise DirectError(f"{service}.{method}: {e.get('error_code')} {e.get('error_string')} — {e.get('error_detail')}")
    return d.get("result") or {}


def _results(res: dict, key: str, what: str) -> List[int]:
    ids = []
    for i, x in enumerate(res.get(key) or []):
        for w in x.get("Warnings") or []:
            say(f"  предупреждение {what} #{i + 1}: {w.get('Message')} {w.get('Details', '')}")
        if x.get("Errors"):
            raise DirectError(f"{what} #{i + 1}: " + "; ".join(f"{e.get('Message')} {e.get('Details', '')}" for e in x["Errors"]))
        ids.append(x["Id"])
    return ids


def _split(s: str, sep: str) -> List[str]:
    return [x.strip() for x in (s or "").split(sep) if x.strip()]


def read_groups(path: str):
    rows = list(csv.DictReader(open(path, encoding="utf-8-sig"), delimiter="\t"))
    camp_minus: List[str] = []
    groups: "OrderedDict[str, dict]" = OrderedDict()
    for r in rows:
        if r.get("Минус-фразы на кампанию"):
            camp_minus += _split(r["Минус-фразы на кампанию"], ",")
        g = groups.setdefault(r["Номер группы"], {"name": r["Название группы"], "minus": [], "phrases": [], "ads": [], "bid": 0.0})
        b = (r.get("Ставка") or "").replace(",", ".").strip()
        if b and not g["bid"]:
            g["bid"] = min(float(b), BID_RUB)   # колонка «Ставка»: своя ставка группы, не выше потолка
        for m in _split(r.get("Минус-фразы на группу", ""), ","):
            if m not in g["minus"]:
                g["minus"].append(m)
        ph = (r.get("Фраза (с минус-словами)") or "").strip()
        if ph and ph not in g["phrases"]:
            g["phrases"].append(ph)
        ad = (r["Заголовок 1"], r["Заголовок 2"], r["Текст 1"], r["Ссылка"], r.get("Отображаемая ссылка", ""),
              r.get("Заголовки быстрых ссылок", ""), r.get("Описания быстрых ссылок", ""), r.get("Адреса быстрых ссылок", ""))
        if ad[0] and ad not in g["ads"]:
            g["ads"].append(ad)
    return list(OrderedDict.fromkeys(camp_minus)), groups


def _state() -> dict:
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def cmd_direct_access_check() -> int:
    """Только чтение: одобрен ли доступ к API Директа и какие кампании уже есть в аккаунте."""
    try:
        if not os.getenv("YANDEX_DIRECT_TOKEN"):
            say("Нет секрета YANDEX_DIRECT_TOKEN.")
            return 1
        camps = call("campaigns", "get", {"SelectionCriteria": {}, "FieldNames": ["Id", "Name", "State", "Status"]}).get("Campaigns") or []
        say(f"{date.today()}: доступ к API Директа ЕСТЬ. Кампаний в аккаунте: {len(camps)}" +
            "".join(f"\n  - {c['Name']} (№{c['Id']}, {c.get('State')}/{c.get('Status')})" for c in camps))
        return 0
    except DirectError as exc:
        say(f"{date.today()}: доступа к API Директа НЕТ: {exc}")
        return 0
    finally:
        os.makedirs(DATA, exist_ok=True)
        open(RESULT, "w", encoding="utf-8").write("\n".join(_log) + "\n")


def cmd_direct_upload_draft() -> int:
    try:
        rc = 0
        for fn in _upload_files():
            rc = max(rc, _run(fn))
        return rc
    except DirectError as exc:
        say(f"ОСТАНОВЛЕНО: {exc}")
        if ": 58 " in str(exc):   # заявка на доступ к API ещё не одобрена — это ожидание, а не сбой
            say("Ждём одобрения заявки на доступ к API Директа — повторю позже.")
            return 0
        return 1
    finally:
        os.makedirs(DATA, exist_ok=True)
        open(RESULT, "w", encoding="utf-8").write("\n".join(_log) + "\n")


def _upload_files() -> List[str]:
    """Файлы из data/direct/upload.txt (по одному в строке); по умолчанию обе кампании посадочной."""
    up = os.path.join(DIRECT_DIR, "upload.txt")
    files = []
    if os.path.exists(up):
        files = [l.strip() for l in open(up, encoding="utf-8") if l.strip() and not l.startswith("#")]
    return files or ["dq200_landing.tsv", "dq200_errors.tsv"]


def _run(fn: str) -> int:
    if not os.getenv("YANDEX_DIRECT_TOKEN"):
        say("Нет секрета YANDEX_DIRECT_TOKEN — загрузить нечем.")
        return 1
    path = os.path.join(DIRECT_DIR, fn)
    name = CAMPAIGN_NAMES.get(fn, "Мехатроники DQ200 — " + fn)
    camp_minus, groups = read_groups(path)
    say(f"Файл {fn}: групп {len(groups)}, фраз {sum(len(g['phrases']) for g in groups.values())}, "
        f"объявлений {sum(len(g['ads']) for g in groups.values())}, минус-фраз кампании {len(camp_minus)}")

    # 1. доступ к API и дубль кампании
    existing = call("campaigns", "get", {"SelectionCriteria": {}, "FieldNames": ["Id", "Name", "State", "Status"]})
    camps = existing.get("Campaigns") or []
    say(f"Доступ к API Директа есть. Кампаний в аккаунте: {len(camps)}" +
        ("".join(f"\n  - {c['Name']} (№{c['Id']}, {c.get('State')}/{c.get('Status')})" for c in camps)))
    if any(c["Name"].strip() == name for c in camps):
        say(f"Кампания «{name}» уже есть — повторно не создаю.")
        return 0

    # 2. кампания
    camp = {
        "Name": name,
        "StartDate": date.today().isoformat(),
        "NegativeKeywords": {"Items": camp_minus},
        "TextCampaign": {
            "BiddingStrategy": {
                "Search": {"BiddingStrategyType": "HIGHEST_POSITION"},
                "Network": {"BiddingStrategyType": "SERVING_OFF"},
            },
            "Settings": [
                {"Option": "ADD_METRICA_TAG", "Value": "YES"},
                {"Option": "ENABLE_SITE_MONITORING", "Value": "YES"},   # сайт упал — показы сами встанут
                {"Option": "ENABLE_EXTENDED_AD_TITLE", "Value": "YES"},
            ],
            "CounterIds": {"Items": [COUNTER_ID]},
        },
    }
    cid = _results(call("campaigns", "add", {"Campaigns": [camp]}), "AddResults", "кампания")[0]
    say(f"Создана кампания «{name}» №{cid} (черновик)")
    st = _state()
    st.setdefault("drafts", {})[fn] = {"campaign_id": cid, "name": name, "groups": {}}
    st["draft"] = st["drafts"][fn]

    bid = BID_RUB * 1_000_000
    n_kw = n_ads = 0
    for num, g in groups.items():
        gid = _results(call("adgroups", "add", {"AdGroups": [{
            "Name": g["name"], "CampaignId": cid, "RegionIds": REGIONS,
            **({"NegativeKeywords": {"Items": g["minus"]}} if g["minus"] else {}),
        }]}), "AddResults", f"группа «{g['name']}»")[0]
        gbid = int((g.get("bid") or BID_RUB) * 1_000_000)
        kws = [{"Keyword": p, "AdGroupId": gid, "Bid": gbid} for p in g["phrases"]]
        kw_ids = _results(call("keywords", "add", {"Keywords": kws}), "AddResults", f"фразы группы «{g['name']}»")
        n_kw += len(kw_ids)
        ad_ids = []
        for t1, t2, txt, href, disp, sl_t, sl_d, sl_h in g["ads"]:
            text_ad = {"Title": t1, "Title2": t2, "Text": txt, "Href": href, "Mobile": "NO"}
            if disp:
                text_ad["DisplayUrlPath"] = disp
            titles, descs, hrefs = _split(sl_t, "||"), _split(sl_d, "||"), _split(sl_h, "||")
            if titles and len(titles) == len(hrefs):
                links = []
                for i, t in enumerate(titles):
                    s = {"Title": t, "Href": hrefs[i]}
                    if i < len(descs):
                        s["Description"] = descs[i]
                    links.append(s)
                text_ad["SitelinkSetId"] = _results(call("sitelinks", "add", {"SitelinksSets": [{"Sitelinks": links}]}),
                                                    "AddResults", "быстрые ссылки")[0]
            ad_ids += _results(call("ads", "add", {"Ads": [{"AdGroupId": gid, "TextAd": text_ad}]}),
                               "AddResults", f"объявление группы «{g['name']}»")
        n_ads += len(ad_ids)
        st["draft"]["groups"][g["name"]] = {"id": gid, "keywords": len(kw_ids), "ads": ad_ids}
        say(f"  группа «{g['name']}»: фраз {len(kw_ids)}, объявлений {len(ad_ids)}")
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    say(f"Готово: кампания №{cid}, групп {len(groups)}, фраз {n_kw}, объявлений {n_ads}. "
        f"Ставка {BID_RUB} ₽ на фразу (группы с колонкой «Ставка» — по ней), регион Россия без Чечни, только поиск, счётчик {COUNTER_ID}.")
    say("На модерацию НЕ отправлено и не запущено. В день запуска: проверить в кабинете, пополнить счёт, "
        "«Отправить на модерацию».")
    return 0
