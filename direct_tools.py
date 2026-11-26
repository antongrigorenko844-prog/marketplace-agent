"""
Яндекс Директ: проверка файла импорта, проверка посадочных страниц и ежедневная сводка в Telegram.

Источник правды для кампаний — файлы data/direct/*.tsv (тот же формат, что загружается в Директ
через «Загрузить из файла»: колонки «Название группы», «Фраза (с минус-словами)», «Минус-фразы на группу»,
«Заголовок 1/2», «Текст 1», «Ссылка», «Адреса быстрых ссылок», «Минус-фразы на кампанию» …).

Команды (main.py):
  --direct-lint     проверить все data/direct/*.tsv: мусорные/широкие фразы (как «китайский» из одного слова),
                    чужие коробки (DQ250/DQ500/0B5…), ремонтный трафик, конфликты фраз с минус-словами,
                    длины заголовков/текстов, UTM. Ошибки → сообщение владельцу в Telegram, прогон красный.
  --direct-check-links  открыть все страницы из кампаний (ссылки и быстрые ссылки): ответ 200, нет заглушек
                    (wa.me/71234567890, t.me/username, «renew subscription»), есть якоря (#katalog …).
                    Проблемы → Telegram (одно сообщение в день, только если что-то изменилось).
  --direct-report   сводка Директа за вчера и 7 дней: расход с НДС, клики, цена клика, конверсии,
                    + самые дорогие поисковые запросы вчера (чтобы сразу видеть мусор и дописать минус-слова).
                    Нужен секрет YANDEX_DIRECT_TOKEN (OAuth-токен Директа); без него команда молча выходит.
"""
import csv
import glob
import hashlib
import json
import os
import re
import time
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Tuple

import requests
from zoneinfo import ZoneInfo
import direct_commercial_evidence as commercial

_ROOT = os.path.dirname(os.path.abspath(__file__))
DIRECT_DIR = os.path.join(_ROOT, "data", "direct")
STATE_PATH = os.path.join(_ROOT, "data", "direct_state.json")
OWNER_PATH = os.path.join(_ROOT, "data", "owner_chat.json")
SITE_HOST = "leoparts-akpp.ru"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"}

# Лимиты Директа (текстово-графическое объявление)
LIMITS = {"Заголовок 1": 56, "Заголовок 2": 30, "Текст 1": 81}
SITELINK_TITLE_MAX = 30
SITELINK_DESC_MAX = 60
WORD_MAX = 22

# Признаки «нашей» фразы: коробка/деталь. Фраза без них — слишком широкая.
ANCHORS = ("dq200", "dq 200", "dsg", "дсг", "0am", "0cw", "мехатрон", "гидроблок", "плата", "эбу", "блок")
# Чужие коробки и нецелевой интент — в фразе быть не должно
OFF_TARGET = ("dq250", "dq500", "dq381", "dq400", "0b5", "02e", "0dd", "dsg 6", "дсг 6", "dsg6", "дсг6",
              "ремонт", "замена", "заменить", "ремкомплект", "прошивк", "диагностик", "адаптаци", "своими руками")
# Заглушки шаблона Тильды, которые не должны оказаться на рекламируемой странице
PLACEHOLDERS = ("wa.me/71234567890", "wa.me/11234567890", "t.me/username", "vk.me/username",
                "renew subscription", "renew your subscription")
STOPWORDS = {"в", "на", "с", "со", "для", "по", "от", "до", "из", "к", "о", "об", "и", "а", "но", "или", "за",
             "без", "под", "над", "при", "у", "же", "ли", "не", "как", "это", "что", "такое"}


# ---------------------------------------------------------------- общие мелочи

def _tg_owner(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "")
    try:
        chat = json.load(open(OWNER_PATH, encoding="utf-8")).get("chat_id")
    except (OSError, ValueError):
        chat = None
    if not token or not chat:
        print("Telegram не настроен — сообщение только в лог:\n" + text)
        return False
    try:
        for i in range(0, len(text), 3900):
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                          data={"chat_id": chat, "text": text[i:i + 3900], "disable_web_page_preview": "true"}, timeout=30)
        return True
    except requests.RequestException as exc:
        print(f"Telegram: {exc}")
        return False


def _state() -> dict:
    try:
        return json.load(open(STATE_PATH, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2, sort_keys=True)


def read_tsv(path: str) -> List[Dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))
    if not rows:
        return []
    head = [h.strip() for h in rows[0]]
    return [dict(zip(head, r + [""] * (len(head) - len(r)))) for r in rows[1:] if any(c.strip() for c in r)]


def _split_list(s: str) -> List[str]:
    return [x.strip() for x in re.split(r"[,\n]", s or "") if x.strip()]


def _phrase_core(phrase: str) -> Tuple[str, List[str]]:
    """'мехатроник dq200 -бу -китай' -> ('мехатроник dq200', ['бу','китай'])"""
    parts = re.split(r"\s+-(?=\S)", phrase.strip())
    return parts[0].strip(), [p.strip() for p in parts[1:]]


def _tokens(core: str) -> List[str]:
    core = re.sub(r'["\[\]()]', " ", core.lower())
    return [t for t in core.split() if t]


def _stem(w: str) -> str:
    w = w.lower().lstrip("+")
    if re.search(r"\d", w) or len(w) <= 4:
        return w
    return w[:max(4, len(w) - 2)]


def _word_hits(minus_word: str, token: str) -> bool:
    """Совпадает ли минус-слово с токеном фразы (Директ учитывает все словоформы, если нет «!»)."""
    m = minus_word.lower().strip()
    t = token.lower().lstrip("+!")
    if m.startswith("!"):
        return t == m[1:]
    m = m.lstrip("+")
    if re.search(r"\d", m) or len(m) <= 4:
        return t == m
    s = _stem(m)
    return t.startswith(s) and len(t) - len(s) <= 4


def _minus_hits(minus: str, toks: List[str]) -> bool:
    words = [w for w in minus.split() if w.lower().lstrip("!+") not in STOPWORDS or w.startswith(("!", "+"))]
    if not words:
        return False
    return all(any(_word_hits(w, t) for t in toks) for w in words)


# ---------------------------------------------------------------- проверка файла импорта

def lint_file(path: str) -> Tuple[List[str], List[str]]:
    errors: List[str] = []
    warns: List[str] = []
    rows = read_tsv(path)
    need = ["Название группы", "Фраза (с минус-словами)", "Заголовок 1", "Текст 1", "Ссылка"]
    if not rows:
        return [f"файл пустой"], []
    miss = [c for c in need if c not in rows[0]]
    if miss:
        return [f"нет колонок: {', '.join(miss)}"], []

    camp_minus: List[str] = []
    for r in rows:
        camp_minus += _split_list(r.get("Минус-фразы на кампанию", ""))
    for m in camp_minus:
        ws = m.split()
        if len(ws) == 1 and ws[0].lower() in STOPWORDS:
            errors.append(f"минус-слово кампании «{m}» — стоп-слово, Директ его не примет; пишите «!{m}»")

    seen: Dict[str, str] = {}
    group_minus: Dict[str, List[str]] = {}
    for r in rows:
        g = r["Название группы"].strip()
        if r.get("Минус-фразы на группу", "").strip():
            group_minus[g] = _split_list(r["Минус-фразы на группу"])

    for i, r in enumerate(rows, start=2):
        g = r["Название группы"].strip()
        phrase = r["Фраза (с минус-словами)"].strip()
        if not phrase:
            continue
        where = f"стр.{i} [{g}] «{phrase}»"
        core, own_minus = _phrase_core(phrase)
        toks = _tokens(core)
        low = " " + " ".join(toks) + " "
        key = " ".join(sorted(t.lstrip("+!") for t in toks if t.lstrip("+!") not in STOPWORDS))
        if key in seen:
            warns.append(f"{where}: дубль фразы «{seen[key]}»")
        seen[key] = core
        content = [t for t in toks if t.lstrip("+!") not in STOPWORDS]
        is_code = len(content) == 1 and re.search(r"\d", content[0]) and (
            re.search(r"[a-zа-я]", content[0]) or re.fullmatch(r"\d{5,6}", content[0].lstrip("+!")))  # 01315, 006079
        if len(content) <= 1 and not is_code:
            errors.append(f"{where}: фраза из одного слова — соберёт весь интернет (так «китайский» дал 9 млн показов в прогнозе)")
        elif not any(a in low for a in ANCHORS):
            if g.lower().find("модел") < 0 or "мехатрон" not in low:
                warns.append(f"{where}: нет признака DQ200/DSG7/мехатроника — проверьте, не слишком ли широко")
        for bad in OFF_TARGET:
            if bad.startswith("замен") and "замена" in g.lower():
                continue   # отдельная группа «Замена платы»: замена = покупка новой платы
            if (" " + bad) in low:
                errors.append(f"{where}: нецелевое «{bad}» (чужая коробка или ремонт/услуга)")
                break
        if re.search(r"(^|\s)без\s", " " + core.lower() + " ") and "+без" not in core.lower():
            warns.append(f"{where}: предлог «без» меняет смысл — зафиксируйте как «+без»")
        for scope, lst in (("группы", group_minus.get(g, [])), ("кампании", camp_minus)):
            for m in lst:
                if _minus_hits(m, toks):
                    errors.append(f"{where}: конфликт с минус-словом {scope} «{m}» — фраза не будет показываться")
                    break
        for m in own_minus:
            if _minus_hits(m, toks):
                errors.append(f"{where}: своё минус-слово «{m}» совпадает с фразой")

    for i, r in enumerate(rows, start=2):
        b = (r.get("Ставка") or "").replace(",", ".").strip()
        if b:
            try:
                v = float(b)
                if not 0.3 <= v <= 60:
                    errors.append(f"стр.{i} [{r['Название группы']}] ставка {b} ₽ вне 0,3–60 ₽")
            except ValueError:
                errors.append(f"стр.{i} [{r['Название группы']}] ставка «{b}» — не число")

    # объявления (первая строка каждой группы несёт текст объявления)
    done = set()
    for i, r in enumerate(rows, start=2):
        g = r["Название группы"].strip()
        if g in done or not r.get("Заголовок 1"):
            continue
        done.add(g)
        for col, lim in LIMITS.items():
            v = r.get(col, "")
            n = len(re.sub(r"[!,.;:\"]", "", v))
            if n > lim:
                errors.append(f"[{g}] {col}: {n} > {lim} симв.: «{v}»")
            for w in v.split():
                if len(w) > WORD_MAX:
                    errors.append(f"[{g}] {col}: слово длиннее {WORD_MAX}: «{w}»")
        url = r.get("Ссылка", "")
        if SITE_HOST not in url:
            errors.append(f"[{g}] ссылка не на {SITE_HOST}: {url[:80]}")
        if "utm_source=" not in url:
            warns.append(f"[{g}] в ссылке нет UTM-меток")
        titles = [x for x in r.get("Заголовки быстрых ссылок", "").split("||") if x]
        descs = [x for x in r.get("Описания быстрых ссылок", "").split("||") if x]
        addrs = [x for x in r.get("Адреса быстрых ссылок", "").split("||") if x]
        if titles and len(titles) != len(addrs):
            errors.append(f"[{g}] быстрых ссылок {len(titles)}, а адресов {len(addrs)}")
        if len(set(addrs)) != len(addrs):
            errors.append(f"[{g}] адреса быстрых ссылок повторяются")
        for t in titles:
            if len(t) > SITELINK_TITLE_MAX:
                errors.append(f"[{g}] заголовок быстрой ссылки > {SITELINK_TITLE_MAX}: «{t}»")
        for d in descs:
            if len(d) > SITELINK_DESC_MAX:
                errors.append(f"[{g}] описание быстрой ссылки > {SITELINK_DESC_MAX}: «{d}»")
        if sum(len(t) for t in titles[:4]) > 66:
            warns.append(f"[{g}] первые 4 быстрые ссылки длиннее 66 символов в сумме — часть не покажется")
    return errors, warns


def cmd_direct_lint(paths: Optional[List[str]] = None) -> int:
    paths = paths or sorted(glob.glob(os.path.join(DIRECT_DIR, "*.tsv")))
    if not paths:
        print(f"Нет файлов {DIRECT_DIR}/*.tsv")
        return 0
    bad = 0
    report = []
    for p in paths:
        errs, warns = lint_file(p)
        name = os.path.basename(p)
        print(f"\n=== {name}: ошибок {len(errs)}, предупреждений {len(warns)}")
        for e in errs:
            print("  ОШИБКА:", e)
        for w in warns:
            print("  внимание:", w)
        if errs:
            bad += 1
            report.append(f"❌ {name}: {len(errs)} ошиб.\n" + "\n".join("• " + e for e in errs[:15])
                          + (f"\n… и ещё {len(errs) - 15}" if len(errs) > 15 else ""))
    if report and os.getenv("GITHUB_ACTIONS"):
        _tg_owner("Директ: файл импорта не прошёл проверку — не загружайте его, пока не поправим.\n\n" + "\n\n".join(report))
    if not bad:
        # .xlsx для загрузки через Excel всегда собирается из .tsv заново — правим только .tsv
        try:
            cmd_direct_xlsx([p for p in paths if os.path.exists(TEMPLATE_PATH) or os.getenv("GITHUB_ACTIONS")])
        except Exception as exc:  # noqa: BLE001
            print(f"direct-xlsx: не собрал — {exc}")
    return 1 if bad else 0


# ---------------------------------------------------------------- проверка посадочных

def active_files() -> List[str]:
    """Файлы кампаний, которые сейчас загружены в Директ (data/direct/active.txt); иначе все."""
    try:
        names = [x.strip() for x in open(os.path.join(DIRECT_DIR, "active.txt"), encoding="utf-8") if x.strip() and not x.startswith("#")]
        return [os.path.join(DIRECT_DIR, n) for n in names if os.path.exists(os.path.join(DIRECT_DIR, n))]
    except OSError:
        return sorted(glob.glob(os.path.join(DIRECT_DIR, "*.tsv")))


def campaign_urls(paths: Optional[List[str]] = None) -> Dict[str, List[str]]:
    """{страница без query: [якоря]}"""
    out: Dict[str, set] = {}
    for p in paths or active_files():
        for r in read_tsv(p):
            for u in [r.get("Ссылка", "")] + r.get("Адреса быстрых ссылок", "").split("||"):
                u = u.strip()
                if not u.startswith("http"):
                    continue
                base, _, frag = u.partition("#")
                page = base.split("?")[0]
                out.setdefault(page, set())
                if frag:
                    out[page].add(frag)
    return {k: sorted(v) for k, v in out.items()}


def _page_auth_required(response) -> bool:
    """Tilda may send its access-password page with HTTP 200."""
    if response.status_code == 401 or response.headers.get("WWW-Authenticate"):
        return True
    html = response.text
    title = re.search(r"<title\b[^>]*>(.*?)</title\s*>", html, re.I | re.S)
    text = re.sub(r"<[^>]*>", " ", title.group(1)).strip() if title else ""
    if re.search(r"authentication required|authorization required|password protected|access restricted|введите пароль|требуется авторизация|страница защищена паролем", text, re.I):
        return True
    # Only treat a password input as an access gate when the normal Tilda
    # page content is absent; account login forms can exist on public stores.
    if not re.search(r'\bid\s*=\s*["\']allrecords["\']', html, re.I):
        for tag in re.findall(r"<input\b[^>]*>", html, re.I):
            if re.search(r'\btype\s*=\s*(?:["\']password["\']|password\b)', tag, re.I):
                return True
        if re.search(r"auth\.tilda|authentication required|страница защищена паролем", html[:12000], re.I):
            return True
    return False


def _check_page_client(url: str, anchors: List[str], headers: dict) -> List[str]:
    probs = []
    r = None
    for i in range(3):
        try:
            # New HTTP request with no stored browser cookies or credentials.
            r = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
        except requests.RequestException as exc:
            if i == 2:
                return [f"не открывается: {exc.__class__.__name__}"]
            time.sleep(20)
            continue
        if _page_auth_required(r):
            return ["запрашивается авторизация/пароль"]
        if r.status_code in (200, 404):
            break
        # A transient 402/5xx must not alone be treated as an outage.
        time.sleep(20)
    if r.status_code != 200:
        return [f"ответ {r.status_code}" + (" (Тильда 3 раза подряд не отдала страницу — проверьте её в браузере)" if r.status_code == 402 else "")]
    html = r.text
    low = html.lower()
    for ph in PLACEHOLDERS:
        if ph in low:
            probs.append(f"заглушка шаблона «{ph}»")
    for a in anchors:
        if a.startswith("rec") or a in ("zeropopup", "opencart", "order"):
            continue
        if not re.search(r'(id|name|data-anchor|data-record-anchor)\s*=\s*["\']%s["\']' % re.escape(a), html, re.I) \
                and f'href="#{a}"' not in html:
            probs.append(f"нет якоря #{a}")
    return probs


def check_page(url: str, anchors: List[str]) -> List[str]:
    return _check_page_client(url, anchors, UA)


def check_core_mobile_pages() -> Dict[str, List[str]]:
    """Check public social/advertising destinations as an anonymous smartphone."""
    from concurrent.futures import ThreadPoolExecutor
    mobile = {
        "iPhone": {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.5 Mobile/15E148 Safari/604.1"},
        "Android": {"User-Agent": "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Mobile Safari/537.36"},
    }
    urls = ["https://leoparts-akpp.ru/", "https://leoparts-akpp.ru/catalog", "https://leoparts-akpp.ru/mehatronik-dq200"]
    def check(item):
        url, client = item
        return url + " [" + client + "]", _check_page_client(url, [], mobile[client])
    with ThreadPoolExecutor(max_workers=3) as pool:
        return dict(pool.map(check, [(url, client) for url in urls for client in mobile]))


def cmd_direct_check_links(paths: Optional[List[str]] = None, notify: bool = True) -> int:
    urls = campaign_urls(paths)
    if not urls:
        print("Нет ссылок в data/direct/*.tsv")
        return 0
    lines = []
    for page, anchors in sorted(urls.items()):
        probs = check_page(page, anchors)
        print(("OK  " if not probs else "ПЛОХО ") + page + ("" if not probs else ": " + "; ".join(probs)))
        if probs:
            lines.append(f"• {page.replace('https://', '')}: " + "; ".join(probs))
    for page, probs in check_core_mobile_pages().items():
        print(("OK  " if not probs else "ПЛОХО ") + page + ("" if not probs else ": " + "; ".join(probs)))
        if probs:
            lines.append(f"• {page.replace('https://', '')}: " + "; ".join(probs))
    st = _state()
    digest = hashlib.md5("\n".join(lines).encode()).hexdigest() if lines else ""
    today = datetime.utcnow().strftime("%Y-%m-%d")
    if lines and notify and (st.get("links_digest") != digest or st.get("links_day") != today):
        if st.get("links_digest") != digest:
            _tg_owner("⚠️ Директ: реклама ведёт на страницы с проблемами — клики будут теряться:\n\n" + "\n".join(lines)
                      + "\n\nНапишите Claude — поправим ссылки кампании или страницу.")
        st["links_day"] = today
    elif not lines and st.get("links_digest"):
        _tg_owner("✅ Директ: все страницы из кампании снова открываются нормально.")
    st["links_digest"] = digest
    _save_state(st)
    return 0


# ---------------------------------------------------------------- сводка из API Директа

REPORTS_URL = "https://api.direct.yandex.com/json/v5/reports"


def _report(token: str, body: dict, tries: int = 12) -> List[Dict[str, str]]:
    headers = {"Authorization": "Bearer " + token, "Accept-Language": "ru", "processingMode": "auto",
               "returnMoneyInMicros": "false", "skipReportHeader": "true", "skipReportSummary": "true"}
    login = os.getenv("YANDEX_DIRECT_LOGIN", "")
    if login:
        headers["Client-Login"] = login
    for _ in range(tries):
        r = requests.post(REPORTS_URL, json=body, headers=headers, timeout=120)
        if r.status_code == 200:
            rows = list(csv.reader(r.text.splitlines(), delimiter="\t"))
            if not rows:
                return []
            return [dict(zip(rows[0], x)) for x in rows[1:]]
        if r.status_code in (201, 202):
            time.sleep(min(int(r.headers.get("retryIn", "10") or 10), 30))
            continue
        raise RuntimeError(f"Директ API {r.status_code}: {r.text[:300]}")
    raise RuntimeError("Директ API: отчёт не успел сформироваться")


def _num(x: str) -> float:
    try:
        return float(str(x).replace(",", ".")) if x not in ("", "--") else 0.0
    except ValueError:
        return 0.0


# Для рекламы LEOPARTS показываем только кампанию DQ200, а не весь аккаунт.
# ВАЖНО: Conversions — агрегированные достижения целей, НЕ доказанные продажи.
DIRECT_DQ200_CAMPAIGN_ID = 716316525


def _perf(token: str, d_from: date, d_to: date, name: str) -> dict:
    body = {"params": {
        "SelectionCriteria": {
            "DateFrom": d_from.isoformat(),
            "DateTo": d_to.isoformat(),
            "Filter": [{"Field": "CampaignId", "Operator": "IN",
                        "Values": [str(DIRECT_DQ200_CAMPAIGN_ID)]}],
        },
        "FieldNames": ["CampaignId", "Impressions", "Clicks", "Cost", "Conversions"],
        "ReportName": f"{name}-{DIRECT_DQ200_CAMPAIGN_ID}-{d_from}-{d_to}-{int(time.time() * 1000)}",
        "ReportType": "CAMPAIGN_PERFORMANCE_REPORT",
        "DateRangeType": "CUSTOM_DATE", "Format": "TSV",
        "IncludeVAT": "YES", "IncludeDiscount": "NO",
    }}
    rows = _report(token, body)
    totals = {"Impressions": 0.0, "Clicks": 0.0, "Cost": 0.0, "Conversions": 0.0}
    conversion_cells = 0
    for row in rows:
        if row.get("CampaignId") != str(DIRECT_DQ200_CAMPAIGN_ID):
            raise RuntimeError("Отчёт Директа вернул данные другой кампании: отмена сводки")
        for key in ("Impressions", "Clicks", "Cost"):
            totals[key] += _num(row.get(key, "0"))
        conversion = row.get("Conversions", "--")
        if conversion is not None and str(conversion).strip() not in ("", "-", "--"):
            totals["Conversions"] += _num(conversion)
            conversion_cells += 1
    # Нельзя превращать '--' (данных нет) в "0 конверсий".
    if rows and conversion_cells == 0:
        totals["Conversions"] = None
    return totals


def _fmt(t: dict) -> str:
    clicks = t["Clicks"]
    cpc = t["Cost"] / clicks if clicks else 0.0
    conv = t["Conversions"]
    text = (f"расход {t['Cost']:,.0f} ₽ · показы {t['Impressions']:,.0f} "
            f"· клики {clicks:,.0f} · клик {cpc:,.0f} ₽")
    if conv is None:
        text += " · конверсии: данные недоступны"
    else:
        text += f" · конверсии (все цели) {conv:,.0f}"
        if conv > 0:
            text += f" (цена агрегированной конверсии {t['Cost'] / conv:,.0f} ₽)"
    return text.replace(",", " ")


# Read-only Yandex Metrica data: only aggregated paid-source visits.
# Not tied to a particular Direct campaign without matching UTM/session IDs.
METRIKA_COUNTER_ID = 113839860
METRIKA_GOAL_IDS = (675272284, 675487735, 682257540, 675506966, 675507028, 685988392, 686020680)
METRIKA_GOAL_LABELS = {
    675272284: "Форма VIN (автоцель)",
    675487735: "Добавления в корзину",
    682257540: "Ecommerce-покупка",
    675506966: "VIN — приоритетная URL-цель",
    675507028: "Заказ — приоритетная URL-цель",
    685988392: "Начало оплаты (не покупка)",
    686020680: "VIN — успешная отправка (новая цель)",
}


def _metrika_paid_summary(token: str, d_from: date, d_to: date) -> dict:
    """Get per-goal VISITS, not unique submissions or paid orders.

    Paid source includes all advertisements, not only campaign 716316525.
    Never log OAuth tokens, raw URLs or visitor-level data.
    """
    if not token:
        raise ValueError("Empty Metrika token")
    metrics = ["ym:s:visits"] + [f"ym:s:goal{x}visits" for x in METRIKA_GOAL_IDS]
    response = requests.get(
        "https://api-metrika.yandex.net/stat/v1/data",
        params={"ids": METRIKA_COUNTER_ID, "date1": d_from.isoformat(),
                "date2": d_to.isoformat(), "dimensions": "ym:s:lastTrafficSource",
                "metrics": ",".join(metrics), "accuracy": "full", "limit": 30},
        headers={"Authorization": "OAuth " + token}, timeout=35,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Метрика HTTP {response.status_code}")
    payload = response.json()
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Метрика: структура агрегированного отчёта неверна")
    matches = [r for r in data if any(str(d.get("id", "")) == "ad"
                for d in r.get("dimensions", []) if isinstance(d, dict))]
    if len(matches) > 1:
        raise RuntimeError("Метрика: несколько строк рекламного источника")
    row = matches[0] if matches else {"metrics": [0] * len(metrics)}
    values = row.get("metrics")
    if (not isinstance(values, list) or len(values) != len(metrics)
            or any(not isinstance(v, (int, float)) or v < 0 for v in values)):
        raise RuntimeError("Метрика: неполные числовые значения")
    return {"visits": values[0], "goal_visits": dict(zip(METRIKA_GOAL_IDS, values[1:])),
            "attribution": "all_ads_sitewide", "sampled":payload.get("sampled")}


# Exact UTM tag found in the verified 16–20 November LEOPARTS site reports.
# It describes tracked UTM visitors rather than all Direct traffic; visitors
# without such UTM tags are outside this breakdown.
DIRECT_UTM_CAMPAIGN = "dq200_feed_search"
DIRECT_UTM_SOURCES = frozenset({"yandex", "ya"})


def _metrika_utm_campaign_summary(token: str, d_from: date, d_to: date) -> dict:
    """Read only aggregated goal VISITS for this specific campaign UTM label."""
    if not token:
        raise ValueError("Empty Metrica token")
    metrics = ["ym:s:visits"] + [f"ym:s:goal{x}visits" for x in METRIKA_GOAL_IDS]
    response = requests.get(
        "https://api-metrika.yandex.net/stat/v1/data",
        params={"ids": METRIKA_COUNTER_ID, "date1": d_from.isoformat(),
                "date2": d_to.isoformat(),
                "dimensions": "ym:s:lastUTMSource,ym:s:lastUTMCampaign",
                "metrics": ",".join(metrics), "accuracy": "full", "limit": 1000},
        headers={"Authorization": "OAuth " + token}, timeout=35,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Метрика UTM HTTP {response.status_code}")
    payload = response.json()
    records = payload.get("data")
    if (not isinstance(records, list) or len(records) > 1000
            or payload.get("total_rows", 0) > 1000):
        raise RuntimeError("Метрика: список UTM неполный или неверный")
    visits = 0
    counts = {goal: 0 for goal in METRIKA_GOAL_IDS}
    rows_matched = 0
    for row in records:
        dims = row.get("dimensions") or []
        if not isinstance(dims, list) or len(dims) != 2:
            raise RuntimeError("Метрика: неожиданные измерения UTM")
        source = str(dims[0].get("name") or "").strip().lower()
        campaign = str(dims[1].get("name") or "").strip()
        if source not in DIRECT_UTM_SOURCES or campaign != DIRECT_UTM_CAMPAIGN:
            continue
        values = row.get("metrics")
        if (not isinstance(values, list) or len(values) != len(metrics)
                or any(not isinstance(x, (int, float)) or x < 0 for x in values)):
            raise RuntimeError("Метрика: неполные числовые значения UTM")
        visits += values[0]
        for goal, value in zip(METRIKA_GOAL_IDS, values[1:]):
            counts[goal] += value
        rows_matched += 1
    return {"visits": visits, "goal_visits": counts, "rows_matched": rows_matched,
            "attribution": "campaign_utm_only",
            "campaign": DIRECT_UTM_CAMPAIGN, "sampled": payload.get("sampled")}


def _metrika_direct_campaign_summary(token: str, d_from: date, d_to: date) -> dict:
    """Exact Direct campaign attribution, independent of UTM; not payment proof."""
    if not token or d_from > d_to:
        raise ValueError("Invalid campaign summary request")
    response = requests.get(
        "https://api-metrika.yandex.net/stat/v1/data",
        params={"ids": METRIKA_COUNTER_ID, "date1": d_from.isoformat(),
                "date2": d_to.isoformat(), "dimensions": commercial.DIMENSION,
                "metrics": ",".join(commercial.METRICS), "filters": commercial.FILTER,
                "accuracy": "full", "limit": 100},
        headers={"Authorization": "OAuth " + token}, timeout=35, allow_redirects=False,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Метрика ID кампании HTTP {response.status_code}")
    payload = response.json()
    return commercial.commercial_summary(
        payload.get("data"), payload.get("total_rows"), payload.get("sampled"),
        d_from, d_to)


def _format_metrika_paid(data: dict, caption: str) -> str:
    vals = data["goal_visits"]
    return (
        f"{caption}: визиты {data['visits']:,.0f} · "
        f"VIN успешно (новая цель) {vals[686020680]:,.0f} · "
        f"Форма VIN (общая) {vals[675272284]:,.0f} · "
        f"Корзина {vals[675487735]:,.0f} · "
        f"Ecommerce-покупка {vals[682257540]:,.0f} · "
        f"VIN-URL {vals[675506966]:,.0f} · "
        f"Заказ-URL {vals[675507028]:,.0f} · "
        f"Начало оплаты (не покупка) {vals[685988392]:,.0f}"
    ).replace(",", " ")


def cmd_direct_report() -> int:
    token = os.getenv("YANDEX_DIRECT_TOKEN", "")
    if not token:
        print("YANDEX_DIRECT_TOKEN не задан — сводку Директа пропускаю.")
        return 0
    yesterday = datetime.now(ZoneInfo("Europe/Moscow")).date() - timedelta(days=1)
    week = _perf(token, yesterday - timedelta(days=6), yesterday, "leoparts7")
    day = _perf(token, yesterday, yesterday, "leoparts1")
    if not week["Impressions"] and not day["Impressions"]:
        print("Показов за 7 дней нет — сводку не отправляю.")
        return 0
    lines = [
        f"📊 Яндекс Директ LEOPARTS, {yesterday:%d.%m} (с НДС, кампания {DIRECT_DQ200_CAMPAIGN_ID})",
        "Вчера: " + _fmt(day),
        "7 дней: " + _fmt(week),
        "⚠️ Конверсии Директа не равны подтверждённым заявкам и оплаченным заказам.",
    ]
    # Two independent sources: Direct clicks and Metrica ad-source visits.
    # Keep them separate, since 1 click != 1 visit and other ads may exist.
    metr_token = os.getenv("YANDEX_METRIKA_TOKEN", "")
    if metr_token:
        try:
            mk_day = _metrika_paid_summary(metr_token, yesterday, yesterday)
            mk_week = _metrika_paid_summary(
                metr_token, yesterday - timedelta(days=6), yesterday)
            lines += [
                "\nМетрика — все рекламные визиты сайта (не только кампания Директа):",
                _format_metrika_paid(mk_day, "Вчера"),
                _format_metrika_paid(mk_week, "7 дней"),
                "Цель формы и корзина не подтверждают оплату заказа; "
                "Ecommerce-покупка требует отдельной сверки с заказами.",
            ]
            try:
                campaign_day = _metrika_utm_campaign_summary(metr_token, yesterday, yesterday)
                campaign_week = _metrika_utm_campaign_summary(
                    metr_token, yesterday - timedelta(days=6), yesterday)
                lines += [
                    "\nМетрика — только визиты с UTM dq200_feed_search (yandex/ya):",
                    _format_metrika_paid(campaign_day, "Вчера"),
                    _format_metrika_paid(campaign_week, "7 дней"),
                    "UTM не охватывает визиты без метки и не подтверждает оплаты.",
                    "VIN успешно — событие после отправки формы; не гарантия получения заявки в CRM.",
                    "Начало оплаты — промежуточное событие, не Ecommerce-покупка.",
                ]
                if (campaign_week["visits"] > 0
                        and campaign_week["goal_visits"][675506966] == 0
                        and campaign_week["goal_visits"][675507028] == 0):
                    lines.append(
                        "⚠️ В UTM-трафике DQ200 за 7 дней нет достижений "
                        "приоритетных URL-целей VIN и заказа. "
                        "Это не равно нулю реальных продаж; проверяйте успешные "
                        "отправки в Tilda и оплаченные заказы.")
            except Exception as exc:
                lines.append("Метрика UTM: детализация кампании пока недоступна")
                print("Метрика UTM: ошибка агрегирования", type(exc).__name__)
        except Exception as exc:
            # Never interpolate exception detail: secrets or visited URLs must
            # not appear in a Telegram notification.
            lines.append("\nМетрика: агрегированные цели пока недоступны")
            print("Метрика: ошибка получения агрегированных целей",
                  type(exc).__name__)
    if metr_token:
        try:
            exact_week = _metrika_direct_campaign_summary(
                metr_token, yesterday - timedelta(days=6), yesterday)
            lines += [
                "\nМетрика — атрибуция по ID кампании 716316525 (включая визиты без UTM):",
                commercial.format_commercial_summary(exact_week, "7 дней"),
            ]
            if yesterday >= commercial.LAUNCH_DATE:
                exact_all = _metrika_direct_campaign_summary(
                    metr_token, commercial.LAUNCH_DATE, yesterday)
                lines.append(commercial.format_commercial_summary(exact_all, "С 16.11.2026"))
            lines.append("Сумма Ecommerce — не оплаченная выручка. "
                         "Фактические продажи, CAC, ROAS и ROMI — только после сверки оплат.")
        except Exception as exc:
            lines.append("Метрика ID кампании: точная коммерческая сводка пока недоступна")
            print("Метрика ID кампании: ошибка агрегирования", type(exc).__name__)
    try:
        query_report = _report(token, {"params": {
            "SelectionCriteria": {
                "DateFrom": yesterday.isoformat(), "DateTo": yesterday.isoformat(),
                "Filter": [{"Field": "CampaignId", "Operator": "IN",
                            "Values": [str(DIRECT_DQ200_CAMPAIGN_ID)]}],
            },
            "FieldNames": ["CampaignId", "Query", "Clicks", "Cost"],
            "ReportName": f"sq-{DIRECT_DQ200_CAMPAIGN_ID}-{yesterday}-{int(time.time() * 1000)}",
            "ReportType": "SEARCH_QUERY_PERFORMANCE_REPORT",
            "DateRangeType": "CUSTOM_DATE", "Format": "TSV", "IncludeVAT": "YES",
        }})
        if any(row.get("CampaignId") != str(DIRECT_DQ200_CAMPAIGN_ID) for row in query_report):
            raise RuntimeError("Отчёт поисковых запросов содержит другую кампанию")
        paid = [row for row in query_report if _num(row.get("Cost", "0")) > 0]
        paid.sort(key=lambda row: -_num(row.get("Cost", "0")))
        if paid:
            lines.append("\nСамые затратные поисковые запросы (вчера; не добавляйте минусы без проверки):")
            lines += [
                f"• {row['Query']} — {_num(row['Cost']):,.0f} ₽, {int(_num(row['Clicks']))} кл."
                .replace(",", " ") for row in paid[:10]
            ]
    except Exception as exc:
        print(f"Поисковые запросы: {exc}")
    message = "\n".join(lines)
    _tg_owner(message)
    print(message)
    return 0


# ---------------------------------------------------------------------------
# Excel-импорт кампаний (Инструменты → «Управление с помощью Excel»), пока нет доступа к API.
TEMPLATE_URL = "https://doc-static.yandex.net/src/support/direct/ru/files/direct_example.xlsx"
TEMPLATE_PATH = os.path.join("data", "direct", "direct_example.xlsx")


def _ensure_template() -> str:
    """Скачать официальный шаблон Директа один раз (из облака Яндекс недоступен — качаем в Actions)."""
    if not os.path.exists(TEMPLATE_PATH):
        r = requests.get(TEMPLATE_URL, timeout=60)
        r.raise_for_status()
        with open(TEMPLATE_PATH, "wb") as f:
            f.write(r.content)
        print(f"Шаблон скачан: {TEMPLATE_PATH} ({len(r.content)} байт)")
    return TEMPLATE_PATH


def cmd_direct_template_dump() -> int:
    """Скачать шаблон и вывести его структуру (листы, первые строки) в лог — чтобы собрать файлы точно по нему."""
    import openpyxl

    path = _ensure_template()
    wb = openpyxl.load_workbook(path)
    for ws in wb.worksheets:
        print(f"\n### Лист «{ws.title}»: {ws.max_row} строк × {ws.max_column} колонок; объединения: {list(ws.merged_cells.ranges)[:20]}")
        for i, row in enumerate(ws.iter_rows(max_row=min(ws.max_row, 40), values_only=True), 1):
            vals = ["" if v is None else str(v) for v in row]
            while vals and vals[-1] == "":
                vals.pop()
            if vals:
                print(f"{i:>3}: " + " | ".join(vals))
    return 0


# Решение владельца 28.10: показы по России без Чечни; ставка по умолчанию — потолок 60 ₽.
XLSX_REGION_EXCLUDE = ["Чеченская Республика"]
XLSX_DEFAULT_BID = 60
XLSX_CAMPAIGN_TYPE = "Единая перфоманс-кампания"   # единственный тип текстовых кампаний в шаблоне Excel


def tsv_to_xlsx(tsv_path: str, out_path: str) -> Tuple[int, int]:
    """Собрать файл для «Управление с помощью Excel → Создать новую кампанию» из data/direct/*.tsv
    строго по официальному шаблону (лист «Тексты», шапка строк 10–11, данные с 12-й строки)."""
    import openpyxl

    rows = read_tsv(tsv_path)
    wb = openpyxl.load_workbook(_ensure_template())
    # Справочные листы НЕ удалять: без них Директ пишет «Файл содержит слишком мало вкладок»
    # и не находит колонки «Заголовок 1»/«Текст» (проверено загрузкой 29.10).
    ws = wb["Тексты"]
    col = {}
    for r in (10, 11):
        for c in ws[r]:
            if isinstance(c.value, str) and c.value.strip():
                col.setdefault(c.value.strip(), c.column)
    # чистим пример (строки 12+)
    for r in range(12, ws.max_row + 1):
        for c in ws[r]:
            c.value = None

    # Директ при загрузке ищет колонки в ОДНОЙ строке шапки (ошибка 29.10: «Не удалось найти колонку
    # 'Заголовок 1' / 'Текст'»), а в шаблоне шапка двухэтажная (строки 10–11, «Комбинаторика» объединена).
    # Делаем шапку плоской: всё в строку 10, «Текст 1» → «Текст», строку 11 убираем.
    for rng in list(ws.merged_cells.ranges):
        if rng.min_row in (10, 11):
            ws.unmerge_cells(str(rng))
    for c in ws[11]:
        if isinstance(c.value, str) and c.value.strip():
            ws.cell(10, c.column, "Текст" if c.value.strip() == "Текст 1" else c.value.strip())
    ws.delete_rows(11)
    col = {}
    for c in ws[10]:
        if isinstance(c.value, str) and c.value.strip():
            col.setdefault(c.value.strip(), c.column)
    col["Текст 1"] = col["Текст"]
    for r in range(11, ws.max_row + 1):
        for c in ws[r]:
            c.value = None

    camp_minus = next((x.get("Минус-фразы на кампанию", "") for x in rows if x.get("Минус-фразы на кампанию")), "")
    ws["E7"] = XLSX_CAMPAIGN_TYPE
    ws["E8"] = None           # № заказа — Директ присвоит сам
    ws["H8"] = "RUB"
    ws["E9"] = camp_minus

    copy_cols = ["Доп. объявление группы", "Название группы", "Номер группы", "Фраза (с минус-словами)",
                 "Минус-фразы на группу", "Заголовок 1", "Заголовок 2", "Текст 1", "Ссылка",
                 "Отображаемая ссылка", "Заголовки быстрых ссылок", "Описания быстрых ссылок",
                 "Адреса быстрых ссылок"]
    missing = [k for k in copy_cols + ["Тип объявления", "Регион", "Ставка"] if k not in col]
    if missing:
        raise RuntimeError(f"В шаблоне Директа нет колонок: {missing}")
    groups = set()
    for i, src in enumerate(rows):
        r = 11 + i
        for k in copy_cols:
            v = (src.get(k) or "").strip()
            if k == "Номер группы":
                v = int(v)
            ws.cell(r, col[k], v if v != "" else None)
        ws.cell(r, col["Тип объявления"], "Комбинаторное")
        if src.get("Доп. объявление группы", "-").strip() == "-":
            reg = (src.get("Регион") or "Россия").strip()
            for ex in XLSX_REGION_EXCLUDE:
                if ex not in reg:
                    reg += f", -{ex}"
            ws.cell(r, col["Регион"], reg)
            bid = (src.get("Ставка") or "").strip()
            ws.cell(r, col["Ставка"], int(bid) if bid else XLSX_DEFAULT_BID)
        groups.add(src.get("Номер группы"))
    wb.save(out_path)
    return len(rows), len(groups)


def cmd_direct_xlsx(paths: Optional[List[str]] = None) -> int:
    """data/direct/<имя>.tsv → data/direct/<имя>.xlsx (для ручной загрузки в кабинет, пока нет API)."""
    paths = paths or [os.path.join("data", "direct", n) for n in ("dq200_landing.tsv", "dq200_errors.tsv")]
    for p in paths:
        out = p[:-4] + ".xlsx"
        n, g = tsv_to_xlsx(p, out)
        print(f"{out}: {n} фраз, {g} групп")
    return 0
