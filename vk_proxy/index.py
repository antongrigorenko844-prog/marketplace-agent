# Посредник для VK API в Яндекс Облаке (Cloud Functions, Python 3.12).
# Бот присылает {"method": "...", "params": {...}} с заголовком X-Proxy-Key,
# функция с российского адреса передаёт запрос в VK и возвращает ответ VK как есть.
import base64
import json
import os
import urllib.parse
import urllib.request

VK_API = "https://api.vk.ru/method/"


def handler(event, context):
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    if not os.environ.get("PROXY_KEY") or headers.get("x-proxy-key") != os.environ["PROXY_KEY"]:
        return {"statusCode": 403, "body": "forbidden"}
    body = event.get("body") or "{}"
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    req = json.loads(body)
    method = str(req.get("method", ""))
    if not method or "/" in method:
        return {"statusCode": 400, "body": "bad method"}
    data = urllib.parse.urlencode(req.get("params") or {}).encode("utf-8")
    r = urllib.request.Request(VK_API + method, data=data, method="POST")
    with urllib.request.urlopen(r, timeout=25) as resp:
        text = resp.read().decode("utf-8")
    return {"statusCode": 200, "headers": {"Content-Type": "application/json"}, "body": text}
