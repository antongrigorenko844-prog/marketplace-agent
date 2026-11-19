"""Read only the PerformanceDefault feed-filter dictionary; no ad mutations."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

OUT = Path("data/direct_feed_schema_20261119.json")
SCHEMA = "PerformanceDefault"
REQUEST = {"method": "get", "params": {"DictionaryNames": ["FilterSchemas"]}}


def run():
    report = {"status": "failed", "mode": "read_only", "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_commit": os.environ.get("GITHUB_SHA"), "feed_id_context": 4061297,
        "endpoint": "https://api.direct.yandex.com/json/v501/dictionaries",
        "request": REQUEST, "schema_name": SCHEMA, "errors": []}
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "")

    def clean(value):
        value = str(value)
        return (value.replace(token, "[redacted]") if token else value)[:1600]

    try:
        if not token:
            raise RuntimeError("YANDEX_DIRECT_TOKEN is missing")
        headers = {"Authorization": "Bearer " + token, "Accept-Language": "en",
                   "Content-Type": "application/json; charset=utf-8"}
        if os.environ.get("YANDEX_DIRECT_LOGIN"):
            headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
        req = urllib.request.Request(report["endpoint"], data=json.dumps(REQUEST).encode("utf-8"),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=60) as response:
                raw = json.loads(response.read().decode("utf-8-sig"))
        except urllib.error.HTTPError as error:
            raise RuntimeError("dictionaries.get HTTP " + str(error.code) + ": " +
                               clean(error.read().decode("utf-8", "replace"))) from error
        if raw.get("error") or "result" not in raw:
            raise RuntimeError("dictionaries.get failed: " + clean(json.dumps(raw.get("error", {}))))
        matches = [x for x in raw["result"].get("FilterSchemas", []) if x.get("Name") == SCHEMA]
        if len(matches) != 1 or not isinstance(matches[0].get("Fields"), list):
            raise RuntimeError("Expected one PerformanceDefault schema with Fields")
        fields = [{key: field[key] for key in ("Name", "Type", "Operators") if key in field}
                  for field in matches[0]["Fields"]]
        report["schema"] = {"Name": SCHEMA, "Fields": fields}
        category = [x for x in fields if x.get("Name") == "categoryId"]
        report["category_id_equals_any_supported"] = len(category) == 1 and any(
            op.get("Type") == "EQUALS_ANY" for op in category[0].get("Operators", []))
        report["category_id_field"] = category[0] if len(category) == 1 else None
        report["status"] = "complete"
    except Exception as error:
        report["errors"].append(clean(error))
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    temporary = OUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUT)
    return 0 if report["status"] == "complete" else 1


if __name__ == "__main__":
    sys.exit(run())
