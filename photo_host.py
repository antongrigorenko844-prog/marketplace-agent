"""
Загрузка фото/видео как ассетов GitHub Release — вместо прямых ссылок на
raw.githubusercontent.com.

ПОЧЕМУ: raw.githubusercontent.com на практике оказался ненадёжным — файлы,
подтверждённо существующие в репозитории (видно в самом GitHub), стабильно
не скачивались ни Ozon, ни WB, ни напрямую браузером, даже спустя 10+ минут
и с обходом кэша. В README уже был отмечен риск проблем с доступом к этому
адресу из России в 2026 году — похоже, это и есть причина. GitHub Release
использует другую раздачу файлов (не тот же самый CDN для "сырых" файлов
репозитория), поэтому используем её вместо raw.githubusercontent.com.

Работает ТОЛЬКО внутри GitHub Actions:
- GITHUB_REPOSITORY передаётся автоматически.
- GITHUB_TOKEN нужно передать в workflow явно в env шага "Run command"
  (см. .github/workflows/main.yml) — это НЕ отдельный секрет, который нужно
  создавать руками, а встроенный токен, который GitHub сам выпускает на
  каждый запуск. Права на него уже есть, если в настройках репозитория
  включено "Read and write permissions" (это уже должно быть сделано —
  без этого не работало бы и сохранение data/ обратно в репозиторий).

Все товары используют ОДИН служебный релиз (тег "product-photos") —
файлы туда просто накапливаются/перезаписываются по имени, сам релиз
трогать в интерфейсе GitHub не нужно.
"""
import logging
import os
import time
from typing import Optional

import requests

logger = logging.getLogger("marketplace-agent.photo_host")

API_BASE = "https://api.github.com"
RELEASE_TAG = "product-photos"

_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
}


class PhotoHostError(RuntimeError):
    pass


def _get_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        raise PhotoHostError(
            "GITHUB_TOKEN недоступен в этом запуске — добавьте в "
            ".github/workflows/main.yml, в шаг \"Run command\", строку "
            "'GH_TOKEN: ${{ secrets.GITHUB_TOKEN }}' (см. README). Это НЕ "
            "новый секрет, который нужно создавать руками — он выпускается "
            "GitHub автоматически."
        )
    return token


def _get_repo() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise PhotoHostError(
            "GITHUB_REPOSITORY пуст — эту команду нужно запускать через GitHub Actions."
        )
    return repo


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _get_or_create_release(token: str, repo: str) -> dict:
    headers = _headers(token)
    resp = requests.get(
        f"{API_BASE}/repos/{repo}/releases/tags/{RELEASE_TAG}", headers=headers, timeout=30
    )
    if resp.status_code == 200:
        return resp.json()
    if resp.status_code != 404:
        raise PhotoHostError(f"GitHub releases/tags вернул {resp.status_code}: {resp.text[:300]}")

    resp = requests.post(
        f"{API_BASE}/repos/{repo}/releases",
        headers=headers,
        json={
            "tag_name": RELEASE_TAG,
            "name": "Фото и видео товаров (служебное, не удалять)",
            "body": (
                "Автоматически создано marketplace-agent для хранения фото/видео "
                "товаров — ссылки отсюда используются в карточках Ozon/WB. "
                "Удалять этот релиз не нужно, иначе перестанут открываться уже "
                "использованные ссылки на фото."
            ),
            "draft": False,
            "prerelease": False,
        },
        timeout=30,
    )
    if not resp.ok:
        raise PhotoHostError(f"Не удалось создать release: {resp.status_code} {resp.text[:300]}")
    return resp.json()


def _delete_asset_if_exists(token: str, repo: str, release: dict, filename: str) -> None:
    headers = _headers(token)
    for asset in release.get("assets", []):
        if asset.get("name") == filename:
            del_resp = requests.delete(
                f"{API_BASE}/repos/{repo}/releases/assets/{asset['id']}",
                headers=headers,
                timeout=30,
            )
            if not del_resp.ok and del_resp.status_code != 404:
                logger.warning(
                    "Не удалось удалить старую версию %s перед перезаливкой: %s",
                    filename,
                    del_resp.text[:200],
                )
            return


def upload_file(local_path: str, filename: Optional[str] = None, retries: int = 3) -> str:
    """
    Загружает файл как ассет GitHub Release (пересоздавая его, если файл с
    таким именем уже был загружен раньше) и возвращает прямую публичную
    ссылку на скачивание — её и подставляем вместо raw.githubusercontent.com.
    """
    token = _get_token()
    repo = _get_repo()
    filename = filename or os.path.basename(local_path)

    release = _get_or_create_release(token, repo)
    _delete_asset_if_exists(token, repo, release, filename)

    upload_url = release["upload_url"].split("{")[0]
    ext = os.path.splitext(filename)[1].lower()
    content_type = _CONTENT_TYPES.get(ext, "application/octet-stream")

    last_error = None
    for attempt in range(1, retries + 1):
        with open(local_path, "rb") as f:
            data = f.read()
        resp = requests.post(
            upload_url,
            headers={**_headers(token), "Content-Type": content_type},
            params={"name": filename},
            data=data,
            timeout=120,
        )
        if resp.ok:
            return resp.json()["browser_download_url"]
        last_error = f"{resp.status_code}: {resp.text[:300]}"
        logger.warning(
            "Загрузка %s как ассета релиза не удалась (попытка %d/%d): %s",
            filename,
            attempt,
            retries,
            last_error,
        )
        time.sleep(2 * attempt)

    raise PhotoHostError(f"Не удалось загрузить {filename} как ассет релиза: {last_error}")
