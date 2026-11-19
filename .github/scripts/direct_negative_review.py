"""Read-only Direct negatives and comparable dated performance exports.

No advertising mutation methods. Only get and the read-only Reports POST.
"""
import csv
import io
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

CID = 716316525
DATE_FROM = "2026-10-20"
DATE_TO = "2026-11-19"
OUT = Path("data/direct_negatives_live_20261119.json")
QUERY_OUT = Path("data/direct_negative_queries_20261119.tsv")
GROUP_OUT = Path("data/direct_negative_groups_20261119.tsv")
QUERY_FIELDS = ["Date", "CampaignId", "AdGroupId", "AdGroupName", "Query", "CriterionType",
                "CriterionId", "Criterion", "Impressions", "Clicks", "Cost", "Conversions"]
GROUP_FIELDS = ["Date", "CampaignId", "AdGroupId", "AdGroupName", "AdNetworkType",
                "CriterionType", "Impressions", "Clicks", "Cost", "Conversions"]
KEYWORD_FIELDS = ["Id", "CampaignId", "AdGroupId", "Keyword", "State", "Status"]
BASE_URL = "https://api.direct.yandex.com/json/v501/"
METRICS = ("Impressions", "Clicks", "Cost", "Conversions")


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
    value = str(value)
    return (value.replace(token, "[redacted]") if token else value)[:1800]


def headers(reports=False):
    token = os.environ.get("YANDEX_DIRECT_TOKEN")
    if not token:
        raise RuntimeError("YANDEX_DIRECT_TOKEN is missing")
    result = {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8",
              "Accept-Language": "en"}
    if os.environ.get("YANDEX_DIRECT_LOGIN"):
        result["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
    if reports:
        result.update({"processingMode": "auto", "returnMoneyInMicros": "false",
                       "skipReportHeader": "true", "skipColumnHeader": "false", "skipReportSummary": "true"})
    return result


def post(service, payload, reports=False):
    request = urllib.request.Request(BASE_URL + service,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers=headers(reports), method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return response.status, response.read().decode("utf-8-sig"), dict(response.headers)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace"), dict(error.headers)


def summarize_tsv(text, requested_fields):
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    columns = reader.fieldnames
    if not columns or len(columns) != len(set(columns)):
        raise RuntimeError("Missing or duplicate report columns")
    metric_columns = {}
    for field in requested_fields:
        if field in columns:
            if field in METRICS:
                metric_columns[field] = field
        elif field == "Conversions":
            candidates = [x for x in columns if x.startswith("Conversions_") or x.startswith("Conversions.")]
            if len(candidates) != 1:
                raise RuntimeError("Expected one aggregate conversion column: " + repr(columns))
            metric_columns[field] = candidates[0]
        else:
            raise RuntimeError("Missing requested report field: " + field)
    sums = {field: Decimal("0") for field in METRICS}
    missing = {field: 0 for field in METRICS}
    row_count = 0
    for row_count, row in enumerate(reader, 1):
        if None in row or any(value is None for value in row.values()):
            raise RuntimeError("Malformed TSV row " + str(row_count))
        if row["CampaignId"] != str(CID) or not DATE_FROM <= row["Date"] <= DATE_TO:
            raise RuntimeError("Report row is outside the requested campaign or dates")
        for field, column in metric_columns.items():
            value = row[column].strip()
            if value in ("", "--", "-"):
                missing[field] += 1
                continue
            try:
                number = Decimal(value.replace("\u00a0", "").replace(",", "."))
            except InvalidOperation as error:
                raise RuntimeError("Invalid numeric value in " + column) from error
            if not number.is_finite():
                raise RuntimeError("Non-finite numeric value in " + column)
            sums[field] += number
    return {"row_count": row_count, "response_fields": columns, "metric_columns": metric_columns,
            "totals_decimal": {k: str(v) for k, v in sums.items()}, "missing_numeric_cells": missing}


def run():
    report = {"status": "in_progress", "mode": "read_only", "campaign_id": CID,
        "captured_at_utc": now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
        "requested_period": {"from": DATE_FROM, "to": DATE_TO, "inclusive": True},
        "completed_period": {"from": DATE_FROM, "to": "2026-11-18", "days": 30},
        "current_day": {"date": "2026-11-19", "partial": True},
        "report_timezone": "Europe/Moscow",
        "freshness_note": "Current day is partial; recent search queries may remain aggregated. Direct statistics can be revised.",
        "conversion_interpretation": "Aggregate of all goals under LC attribution; not verified paid orders",
        "comparison_rule": "Compare search-query totals only with group rows where AdNetworkType is SEARCH",
        "sections": {}, "requests": {}, "errors": [], "reports": {}}

    def save():
        OUT.parent.mkdir(parents=True, exist_ok=True)
        temporary = OUT.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(OUT)

    def section(name, action):
        report["sections"][name] = {"status": "in_progress", "started_at_utc": now()}
        save()
        try:
            action()
            report["sections"][name]["status"] = "complete"
        except Exception as error:
            report["sections"][name]["status"] = "failed"
            report["errors"].append({"section": name, "error": clean(error)})
        report["sections"][name]["finished_at_utc"] = now()
        save()

    def collection(name, service, key, params, reset=True):
        if reset:
            report[name] = []
        request_meta = {"endpoint": BASE_URL + service, "method": "get", "params": params, "pages": []}
        report["requests"].setdefault(name, []).append(request_meta)
        offset = 0
        for _ in range(100):
            page = {**params, "Page": {"Limit": 1000, "Offset": offset}}
            code, text, _ = post(service, {"method": "get", "params": page})
            request_meta["pages"].append({"offset": offset, "limit": 1000, "http_status": code})
            if code != 200:
                raise RuntimeError(f"{service}.get HTTP {code}: " + clean(text))
            body = json.loads(text)
            if body.get("error") or key not in body.get("result", {}):
                raise RuntimeError(service + ".get: " + clean(json.dumps(body, ensure_ascii=False)))
            result = body["result"]
            report[name].extend(result[key])
            save()
            if "LimitedBy" not in result:
                break
            following = result["LimitedBy"]
            if not isinstance(following, int) or following <= offset:
                raise RuntimeError("Invalid pagination for " + service)
            offset = following
        else:
            raise RuntimeError("Pagination limit exceeded for " + service)

    def shared_sets():
        ids = set()
        def add(value):
            ids.update((value or {}).get("Items", []))
        for campaign in report.get("campaigns", []):
            add((campaign.get("UnifiedCampaign") or {}).get("NegativeKeywordSharedSetIds"))
        for group in report.get("groups", []):
            add(group.get("NegativeKeywordSharedSetIds"))
        report["shared_sets"] = []
        report["associated_shared_set_ids"] = sorted(ids)
        for offset in range(0, len(ids), 30):
            collection("shared_sets", "negativekeywordsharedsets", "NegativeKeywordSharedSets", {
                "SelectionCriteria": {"Ids": sorted(ids)[offset:offset + 30]},
                "FieldNames": ["Id", "Name", "NegativeKeywords", "Associated"]}, reset=False)
        if any(report["sections"].get(x, {}).get("status") != "complete" for x in ("campaigns", "groups")):
            raise RuntimeError("Shared-set enumeration is incomplete because a parent section failed")
        if {x["Id"] for x in report["shared_sets"]} != ids:
            raise RuntimeError("Not all associated shared sets were returned")

    def performance(name, report_type, fields, destination):
        params = {"SelectionCriteria": {"DateFrom": DATE_FROM, "DateTo": DATE_TO,
            "Filter": [{"Field": "CampaignId", "Operator": "IN", "Values": [str(CID)]}]},
            "FieldNames": fields, "ReportName": f"leoparts-negative-review-{name}-{DATE_TO}-LC-{datetime.now(timezone.utc):%H%M%S}",
            "ReportType": report_type, "DateRangeType": "CUSTOM_DATE", "Format": "TSV",
            "IncludeVAT": "YES", "AttributionModels": ["LC"]}
        record = {"endpoint": BASE_URL + "reports", "params": params,
            "headers": {k: v for k, v in headers(True).items() if k not in {"Authorization", "Client-Login"}},
            "path": str(destination), "requested_fields": fields, "attempts": []}
        report["reports"][name] = record
        save()
        for attempt in range(1, 21):
            code, text, response_headers = post("reports", {"params": params}, reports=True)
            lower_headers = {k.lower(): v for k, v in response_headers.items()}
            entry = {"attempt": attempt, "http_status": code, "at_utc": now()}
            record["attempts"].append(entry)
            if code == 200:
                record.update(summarize_tsv(text, fields))
                destination.write_text(text, encoding="utf-8")
                record["captured_at_utc"] = now()
                save()
                return
            if code not in (201, 202):
                raise RuntimeError(f"Reports HTTP {code}: " + clean(text))
            try:
                delay = max(0, min(int(lower_headers.get("retryin", "10")), 30))
            except (ValueError, TypeError):
                delay = 10
            entry["retry_in_seconds"] = delay
            save()
            if attempt < 20:
                time.sleep(delay)
        raise RuntimeError("Reports generation did not finish after 20 attempts")

    # Initialize both outputs so a failed rerun cannot masquerade as fresh TSV data.
    for destination, fields in ((QUERY_OUT, QUERY_FIELDS), (GROUP_OUT, GROUP_FIELDS)):
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text("\t".join(fields) + "\n", encoding="utf-8")
    save()
    section("campaigns", lambda: collection("campaigns", "campaigns", "Campaigns", {
        "SelectionCriteria": {"Ids": [CID]},
        "FieldNames": ["Id", "Name", "Type", "State", "Status", "NegativeKeywords"],
        "UnifiedCampaignFieldNames": ["NegativeKeywordSharedSetIds", "AttributionModel", "PriorityGoals"]}))
    section("groups", lambda: collection("groups", "adgroups", "AdGroups", {
        "SelectionCriteria": {"CampaignIds": [CID]},
        "FieldNames": ["Id", "CampaignId", "Name", "Type", "Status", "NegativeKeywords",
                       "NegativeKeywordSharedSetIds", "RegionIds", "TrackingParams"]}))
    section("keywords", lambda: collection("keywords", "keywords", "Keywords", {
        "SelectionCriteria": {"CampaignIds": [CID]}, "FieldNames": KEYWORD_FIELDS}))
    section("shared_sets", shared_sets)
    section("query_report", lambda: performance("queries", "SEARCH_QUERY_PERFORMANCE_REPORT", QUERY_FIELDS, QUERY_OUT))
    section("group_report", lambda: performance("groups", "ADGROUP_PERFORMANCE_REPORT", GROUP_FIELDS, GROUP_OUT))
    section("autotargeting_keywords", lambda: collection("autotargeting_keywords", "keywords", "Keywords", {
        "SelectionCriteria": {"CampaignIds": [CID]},
        "FieldNames": KEYWORD_FIELDS + ["AutotargetingSearchBidIsAuto"],
        "AutotargetingSettingsCategoriesFieldNames": ["Exact", "Narrow", "Alternative", "Accessory", "Broader"],
        "AutotargetingSettingsBrandOptionsFieldNames": ["WithoutBrands", "WithAdvertiserBrand", "WithCompetitorsBrand"]}))
    report["status"] = "partial" if report["errors"] else "complete"
    report["finished_at_utc"] = now()
    save()
    print("Read-only negative review:", report["status"], "; errors:", len(report["errors"]))
    for error in report["errors"]:
        print(error["section"] + ": " + error["error"])
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    sys.exit(run())
