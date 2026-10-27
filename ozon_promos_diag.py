"""
Диагностика (ТОЛЬКО ЧТЕНИЕ): новые методы акций Ozon («Promos», единая логика цен).

Старый /v1/actions/products/deactivate Ozon закрыл («the method is blocked due to unified price
logic... switch to the new methods docs.ozon.ru/api/seller/#tag/Promos»). Этот скрипт с раннера
GitHub скачивает спецификацию Seller API с docs.ozon.ru, вытаскивает все методы про акции/бустинг
и пишет их (путь, описание, поля запроса и ответа) в data/ozon_promos_diag.txt + data/ozon_promos_spec.json.
Ничего на Ozon не меняет: к API продавца не обращается вообще.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

import requests

DATA = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
OUT_TXT = os.path.join(DATA, "ozon_promos_diag.txt")
OUT_JSON = os.path.join(DATA, "ozon_promos_spec.json")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux x86_64) marketplace-agent-diag"}
START_PAGES = [
    "https://docs.ozon.ru/api/seller/",
    "https://docs.ozon.ru/api/seller/?__rr=1",
]
GUESS_SPECS = [
    "https://docs.ozon.ru/api/seller/swagger.json",
    "https://docs.ozon.ru/api/seller/openapi.json",
    "https://docs.ozon.ru/api/seller/api.json",
    "https://docs.ozon.ru/api/seller/seller.json",
    "https://docs.ozon.ru/api/seller/static/swagger.json",
]
WORDS = ("promo", "action", "boost", "elastic", "акци", "буст", "auto")


def _get(url: str) -> Optional[requests.Response]:
    try:
        r = requests.get(url, headers=UA, timeout=60)
        return r if r.ok else None
    except requests.RequestException:
        return None


def _try_json(text: str) -> Optional[dict]:
    try:
        d = json.loads(text)
    except ValueError:
        return None
    return d if isinstance(d, dict) and "paths" in d else None


def _find_spec(log: List[str]) -> Optional[dict]:
    seen = set()
    queue: List[str] = list(GUESS_SPECS)
    for page in START_PAGES:
        r = _get(page)
        log.append(f"страница {page}: {'ok, ' + str(len(r.text)) + ' байт' if r else 'не открылась'}")
        if not r:
            continue
        html = r.text
        # спецификация прямо в странице (Redoc: __redoc_state = {...})
        m = re.search(r"__redoc_state\s*=\s*(\{.*?\})\s*;?\s*</script>", html, re.S)
        if m:
            try:
                st = json.loads(m.group(1))
                spec = (st.get("spec") or {}).get("data")
                if isinstance(spec, dict) and spec.get("paths"):
                    log.append("спецификация найдена внутри страницы (__redoc_state)")
                    return spec
            except ValueError:
                pass
        for u in re.findall(r"""(?:spec-url|specUrl|url)\s*[=:]\s*["']([^"']+\.(?:json|ya?ml))["']""", html):
            queue.insert(0, requests.compat.urljoin(page, u))
        for u in re.findall(r"""["']([^"'\s]+\.json)["']""", html):
            queue.append(requests.compat.urljoin(page, u))
        # JS-бандлы: в них часто зашит адрес спецификации
        for js in re.findall(r"""<script[^>]+src=["']([^"']+\.js)["']""", html)[:15]:
            ju = requests.compat.urljoin(page, js)
            if ju in seen:
                continue
            seen.add(ju)
            jr = _get(ju)
            if not jr:
                continue
            for u in re.findall(r"""["']([^"'\s]*(?:swagger|openapi|spec)[^"'\s]*\.json)["']""", jr.text):
                queue.insert(0, requests.compat.urljoin(ju, u))
            # встроенная спецификация в бандле
            if '"paths"' in jr.text and "/v1/" in jr.text:
                log.append(f"в бандле {ju} есть '\"paths\"' — возможно, спецификация встроена")
    for u in queue:
        if u in seen:
            continue
        seen.add(u)
        r = _get(u)
        if not r:
            continue
        spec = _try_json(r.text)
        log.append(f"кандидат {u}: {'СПЕЦИФИКАЦИЯ' if spec else 'не то'}")
        if spec:
            return spec
    return None


def _resolve(spec: dict, node: Any, depth: int = 0) -> Any:
    if depth > 6:
        return node
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/"):
            cur: Any = spec
            for part in node["$ref"][2:].split("/"):
                cur = cur.get(part, {}) if isinstance(cur, dict) else {}
            return _resolve(spec, cur, depth + 1)
        return {k: _resolve(spec, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(spec, v, depth + 1) for v in node]
    return node


def _fields(schema: Any, prefix: str = "", out: Optional[List[str]] = None, depth: int = 0) -> List[str]:
    out = [] if out is None else out
    if not isinstance(schema, dict) or depth > 4:
        return out
    req = set(schema.get("required") or [])
    for name, sub in (schema.get("properties") or {}).items():
        if not isinstance(sub, dict):
            continue
        t = sub.get("type") or ("array" if "items" in sub else "object" if "properties" in sub else "")
        enum = f" enum={sub['enum']}" if sub.get("enum") else ""
        desc = re.sub(r"\s+", " ", str(sub.get("description") or ""))[:160]
        out.append(f"{prefix}{name}{'*' if name in req else ''} ({t}){enum} — {desc}")
        if t == "array" and isinstance(sub.get("items"), dict):
            _fields(sub["items"], prefix + name + "[].", out, depth + 1)
        elif "properties" in sub:
            _fields(sub, prefix + name + ".", out, depth + 1)
    return out


def run() -> int:
    log: List[str] = []
    spec = _find_spec(log)
    lines = ["Диагностика новых методов акций Ozon (только чтение; к API продавца не обращается)", ""]
    lines += log
    if not spec:
        lines.append("\nСпецификацию скачать не удалось.")
        with open(OUT_TXT, "w", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
        print("\n".join(lines))
        return 1
    picked: Dict[str, Any] = {}
    for path, ops in (spec.get("paths") or {}).items():
        for method, op in (ops or {}).items():
            if not isinstance(op, dict):
                continue
            tags = " ".join(op.get("tags") or [])
            hay = f"{path} {tags} {op.get('summary', '')} {op.get('operationId', '')}".lower()
            if not any(w in hay for w in WORDS):
                continue
            op_r = _resolve(spec, op)
            picked[f"{method.upper()} {path}"] = op_r
            lines.append("")
            lines.append(f"=== {method.upper()} {path}   [теги: {tags}]")
            lines.append(f"  {op.get('summary', '')}")
            desc = re.sub(r"\s+", " ", str(op.get("description") or ""))
            if desc:
                lines.append(f"  описание: {desc[:1500]}")
            body = (((op_r.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
            if body:
                lines.append("  ЗАПРОС:")
                lines += ["    " + x for x in _fields(body)]
            ok = (op_r.get("responses") or {}).get("200") or {}
            rs = ((ok.get("content") or {}).get("application/json") or {}).get("schema")
            if rs:
                lines.append("  ОТВЕТ:")
                lines += ["    " + x for x in _fields(rs)]
    lines.insert(2, f"Методов про акции/бустинг найдено: {len(picked)}")
    with open(OUT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(picked, f, ensure_ascii=False, indent=1)
    print("\n".join(lines[:400]))
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
