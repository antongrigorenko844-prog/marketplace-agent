"""One read-only hasSearchVolume request; YES/NO is not search frequency."""
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

CORE_PATH = Path(__file__).with_name("apply_two_keywords_20261120.py")
if hashlib.sha256(CORE_PATH.read_bytes()).hexdigest() != "c6c52c7337ad3991fb40a76cf9edb6f667d6dabb5072f7e103a07e510cea466b":
    raise RuntimeError("Reviewed transport helpers changed")
spec = importlib.util.spec_from_file_location("forecast_transport_helpers", CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

INPUT = Path("data/direct_semantics_forecast_input_20261120.json")
INPUT_SHA = "4ad1623cbc4e7b9ae52914c4f531700678b071eb391f3742c514210430ef19b4"
MARKER = Path("data/direct_semantics_forecast_request_20261120.txt")
OUTPUT = Path("data/direct_semantics_forecast_20261120.json")
URL = "https://api.direct.yandex.com/json/v5/keywordsresearch"
REGIONS = [225, -11024, 977]
FIELDS = ["Keyword", "RegionIds", "AllDevices", "MobilePhones", "Tablets", "Desktops"]


def validate(marker, source, source_sha):
    if (set(marker) != {"request_id", "input_sha256"} or marker["input_sha256"] != INPUT_SHA or source_sha != INPUT_SHA
            or not isinstance(marker["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", marker["request_id"])):
        raise RuntimeError("Marker/input identity failed")
    keywords = source["request"]["params"]["SelectionCriteria"]["Keywords"]
    expected = {"method": "hasSearchVolume", "params": {"SelectionCriteria": {"Keywords": keywords, "RegionIds": REGIONS}, "FieldNames": FIELDS}}
    if (source["endpoint"] != URL or source["request"] != expected or len(keywords) != 231 or len(set(keywords)) != 231
            or any(not isinstance(k, str) or not k or len(k) > 4096 or k == "---autotargeting" for k in keywords)
            or source["source_row_count"] != 234 or sum(len(x["source_rows"]) for x in source["provenance"]) != 234
            or [x["Keyword"] for x in source["provenance"]] != keywords):
        raise RuntimeError("Read-only request scope failed")
    return keywords


def query(request, report):
    if request["method"] != "hasSearchVolume":
        raise RuntimeError("Only hasSearchVolume is allowed")
    token = os.environ.get("YANDEX_DIRECT_TOKEN")
    if not token:
        raise RuntimeError("YANDEX_DIRECT_TOKEN unavailable")
    headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8", "Accept-Language": "en"}
    if os.environ.get("YANDEX_DIRECT_LOGIN"):
        headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
    req = urllib.request.Request(URL, data=json.dumps(request, ensure_ascii=False).encode(), headers=headers, method="POST")
    report["api_read_requests"] += 1
    try:
        with urllib.request.build_opener(core.NoRedirect()).open(req, timeout=60) as response:
            raw = response.read(core.MAX_BYTES + 1)
    except urllib.error.HTTPError as error:
        status = error.code
        error.close()
        raise RuntimeError("hasSearchVolume HTTP " + str(status)) from None
    except Exception:
        raise RuntimeError("hasSearchVolume transport failure; no automatic retry") from None
    if len(raw) > core.MAX_BYTES:
        raise RuntimeError("Forecast response exceeds bounded size")
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeError):
        raise RuntimeError("Forecast response is not JSON") from None
    if data.get("error") or not isinstance(data.get("result"), dict):
        report["api_error"] = core.clean(data.get("error"))
        raise RuntimeError("hasSearchVolume API error")
    return data["result"]


def main():
    report = {"status": "started", "started_at_utc": core.now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
              "input_sha256": INPUT_SHA, "api_read_requests": 0, "api_mutations": 0, "errors": [],
              "interpretation": "Предварительный прогноз наличия показов YES/NO по этим регионам и устройствам. Это не частотность Wordstat, не гарантия показов, трафика, конверсий или продаж. NO не доказывает отсутствие реального спроса.",
              "official_source": "https://yandex.ru/dev/direct/doc/ru/keywordsresearch/hasSearchVolume"}
    try:
        marker, _ = core.read_json(MARKER)
        source, source_sha = core.read_json(INPUT)
        keywords = validate(marker, source, source_sha)
        report.update(request_id=marker["request_id"], source_row_count=234, unique_positive_keyword_count=231,
                      region_ids=REGIONS, request=source["request"], source_mapping=source["provenance"])
        result = query(source["request"], report)
        report["result"] = core.clean(result)
        rows = result.get("HasSearchVolumeResults")
        if not isinstance(rows, list):
            raise RuntimeError("HasSearchVolumeResults missing")
        by_keyword = {}
        for row in rows:
            by_keyword.setdefault(row.get("Keyword"), []).append(row)
        issues = []
        unknown = [r for r in rows if r.get("Keyword") not in set(keywords)]
        if unknown:
            issues.append({"unexpected_keywords": [r.get("Keyword") for r in unknown]})
        normalized = []
        for keyword in keywords:
            found = by_keyword.get(keyword, [])
            if len(found) != 1:
                issues.append({"Keyword": keyword, "response_rows": len(found)})
                normalized.append({"Keyword": keyword, "RegionIds": REGIONS, **{f: None for f in FIELDS[2:]}})
                continue
            row = found[0]
            if Counter(row.get("RegionIds") or []) != Counter(REGIONS):
                issues.append({"Keyword": keyword, "returned_regions": row.get("RegionIds")})
            clean_row = {"Keyword": keyword, "RegionIds": row.get("RegionIds")}
            for field in FIELDS[2:]:
                value = row.get(field)
                if value not in ("YES", "NO"):
                    issues.append({"Keyword": keyword, "field": field, "value": value})
                    value = None
                clean_row[field] = value
            normalized.append(clean_row)
        report["forecasts"] = normalized
        report["validation_issues"] = issues
        report["summary"] = {field: {"YES": sum(r[field] == "YES" for r in normalized), "NO": sum(r[field] == "NO" for r in normalized),
                                    "not_reported": sum(r[field] is None for r in normalized)} for field in FIELDS[2:]}
        report["status"] = "forecast_received_read_only" if not issues else "partial_or_unexpected_response"
        code = 0 if not issues else 1
    except Exception as error:
        report["errors"].append(core.clean(str(error)))
        report["status"] = "forecast_unavailable_no_mutations"
        code = 1
    report["finished_at_utc"] = core.now()
    core.save(OUTPUT, core.clean(report))
    print(json.dumps({"status": report["status"], "api_read_requests": report["api_read_requests"], "api_mutations": 0, "errors": report["errors"]}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
