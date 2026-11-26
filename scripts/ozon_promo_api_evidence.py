"""Download public official contracts. This program never calls Seller API."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
import requests

URL = "https://docs.ozon.ru/api/seller/swagger.json"
PATHS = (
    "/v1/product/import/prices",
    "/v1/actions/products/update",
    "/v1/product/action/timer/update",
    "/v1/product/action/timer/status",
    "/v2/actions/auto-add/products/update",
    "/v2/actions/auto-add/products",
    "/v2/actions/auto-add/products/list",
    "/v2/actions/products",
    "/v2/actions/candidates",
)

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", required=True)
    dest = Path(p.parse_args().output)
    dest.mkdir(parents=True, exist_ok=True)
    meta = {"source": URL, "fetched_at": datetime.now(timezone.utc).isoformat(),
            "status": "failed", "seller_writes": 0}
    try:
        response = requests.get(URL, timeout=(15, 60), allow_redirects=False)
        response.raise_for_status()
        if len(response.content) > 32 * 1024 * 1024:
            raise ValueError("public schema exceeds size bound")
        full = response.json()
        meta["sha256"] = hashlib.sha256(response.content).hexdigest()
        meta["bytes"] = len(response.content)
        paths = {k: v for k, v in full.get("paths", {}).items()
                 if k in PATHS or ("auto-add" in k and "actions" in k)}
        refs = {}
        def walk(value):
            if isinstance(value, list):
                for child in value:
                    walk(child)
            if isinstance(value, dict):
                ref = value.get("$ref")
                if ref and ref.startswith("#/") and ref not in refs:
                    target = full
                    for key in ref[2:].split("/"):
                        target = target[key.replace("~1", "/").replace("~0", "~")]
                    refs[ref] = target
                    walk(target)
                for child in value.values():
                    walk(child)
        walk(paths)
        (dest / "official_api_contracts.json").write_text(json.dumps({
            "info": full.get("info"), "paths": paths, "refs": refs,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
        meta.update(status="complete", paths=sorted(paths), missing=[x for x in PATHS if x not in paths])
    except Exception as exc:
        meta["error_type"] = type(exc).__name__
    (dest / "official_api_manifest.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    print(json.dumps(meta))
    return 0 if meta["status"] == "complete" else 1

if __name__ == "__main__":
    raise SystemExit(main())
