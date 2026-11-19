"""Read-only keyword inventory and dated criterion/query reports; separate outputs.

Reuses the existing negative-review exporter, replacing only its transport,
fields, parser, and output paths. Never calls any advertising mutation method.
"""
import csv
import io
import json
import os
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import direct_negative_review as core

OUT = Path("data/direct_keywords_live_20261120.json")
QUERY_OUT = Path("data/direct_keywords_queries_20261120.tsv")
GROUP_OUT = Path("data/direct_keywords_groups_20261120.tsv")
CRITERIA_OUT = Path("data/direct_keywords_criteria_20261120.tsv")
CRITERIA_FIELDS = ["Date", "CampaignId", "AdGroupId", "AdGroupName", "AdNetworkType",
    "CriterionType", "CriterionId", "Criterion", "Impressions", "Clicks", "Cost", "Conversions"]
QUERY_FIELDS = ["Date", "CampaignId", "AdGroupId", "AdGroupName", "Query",
    "CriterionType", "CriterionId", "Criterion", "MatchedKeyword", "MatchType",
    "Impressions", "Clicks", "Cost", "Conversions"]
KEYWORD_FIELDS = ["Id", "CampaignId", "AdGroupId", "Keyword", "State", "Status", "ServingStatus",
                  "Bid", "ContextBid", "StrategyPriority"]
REPORT_TYPES = {"SEARCH_QUERY_PERFORMANCE_REPORT", "ADGROUP_PERFORMANCE_REPORT", "CRITERIA_PERFORMANCE_REPORT"}
SHARED_IDS = set()
ACCOUNT_CAMPAIGN_IDS = set()
ACCOUNT_STATES = ["ARCHIVED", "CONVERTED", "ENDED", "OFF", "ON", "SUSPENDED"]
ACCOUNT_FIELDS = {
    "campaigns": ["Id", "Name", "Type", "State", "Status"],
    "adgroups": ["Id", "CampaignId", "Name", "Type", "Status"],
    "keywords": ["Id", "CampaignId", "AdGroupId", "Keyword", "State", "Status", "ServingStatus"],
}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_post(service, payload, reports=False, account=False):
    """Only scoped GET methods and three explicit read-only report types."""
    if account:
        if (reports or service not in ACCOUNT_FIELDS or set(payload) != {"method", "params"}
                or payload.get("method") != "get"):
            raise RuntimeError("Account inventory permits only campaigns/adgroups/keywords.get")
        params = payload["params"]
        if set(params) != {"SelectionCriteria", "FieldNames", "Page"} or params["FieldNames"] != ACCOUNT_FIELDS[service]:
            raise RuntimeError("Account inventory fields outside allowlist")
        page = params["Page"]
        if (set(page) != {"Limit", "Offset"} or page["Limit"] != 1000
                or not isinstance(page["Offset"], int) or not 0 <= page["Offset"] <= 100000):
            raise RuntimeError("Account inventory pagination outside bounds")
        selection = params["SelectionCriteria"]
        if service == "campaigns":
            if selection != {"States": ACCOUNT_STATES}:
                raise RuntimeError("Account campaign inventory must include all documented states")
        else:
            ids = selection.get("CampaignIds", []) if isinstance(selection, dict) else []
            if (selection != {"CampaignIds": ids} or not 1 <= len(ids) <= 10
                    or len(set(ids)) != len(ids) or not set(ids) <= ACCOUNT_CAMPAIGN_IDS):
                raise RuntimeError("Account read outside freshly returned same-client campaign IDs")
    elif reports:
        params = payload.get("params", {})
        if (service != "reports" or set(payload) != {"params"} or params.get("ReportType") not in REPORT_TYPES
                or params.get("SelectionCriteria") != {"DateFrom": core.DATE_FROM, "DateTo": core.DATE_TO,
                    "Filter": [{"Field": "CampaignId", "Operator": "IN", "Values": [str(core.CID)]}]}
                or params.get("DateRangeType") != "CUSTOM_DATE" or params.get("IncludeVAT") != "YES"):
            raise RuntimeError("Report request outside approved scope")
    else:
        if (service not in {"campaigns", "adgroups", "keywords", "ads", "negativekeywordsharedsets"}
                or set(payload) != {"method", "params"} or payload.get("method") != "get"):
            raise RuntimeError("Only approved read-only services/get are permitted")
        selection = payload["params"].get("SelectionCriteria")
        if service == "campaigns":
            expected = {"Ids": [core.CID]}
        elif service == "negativekeywordsharedsets":
            ids = selection.get("Ids", []) if isinstance(selection, dict) else []
            if not ids or not set(ids) <= SHARED_IDS:
                raise RuntimeError("Shared negative-set read outside freshly associated IDs")
            expected = {"Ids": ids}
        else:
            expected = {"CampaignIds": [core.CID]}
        if selection != expected:
            raise RuntimeError("Read selection outside approved campaign")
    req = urllib.request.Request(core.BASE_URL + service, method="POST",
        headers=core.headers(reports), data=json.dumps(payload, ensure_ascii=False).encode())
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            code, metadata = response.status, dict(response.headers)
            raw = response.read(16 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        code, metadata = error.code, dict(error.headers)
        error.close()
        # Do not log a server body that might echo account credentials.
        return code, "", metadata
    except Exception:
        raise RuntimeError(service + " read-only transport error") from None
    if len(raw) > 16 * 1024 * 1024:
        raise RuntimeError("Read response exceeds 16 MiB bound")
    text = raw.decode("utf-8-sig")
    if not reports and code == 200:
        body = json.loads(text)
        if body.get("error"):
            text = json.dumps({"error": {"error_code": body["error"].get("error_code"),
                "error_string": "API error details omitted"}})
        else:
            result = body.get("result", {})
            values = result.get("Campaigns", []) if service == "campaigns" else result.get("AdGroups", [])
            for item in values:
                obj = item.get("UnifiedCampaign", {}) if service == "campaigns" else item
                SHARED_IDS.update((obj.get("NegativeKeywordSharedSetIds") or {}).get("Items", []))
    return code, text, metadata


def inventory_get(service, selection, result_key):
    values, seen, offset = [], set(), 0
    for _ in range(100):
        params = {"SelectionCriteria": selection, "FieldNames": ACCOUNT_FIELDS[service],
                  "Page": {"Limit": 1000, "Offset": offset}}
        status, text, _ = safe_post(service, {"method": "get", "params": params}, account=True)
        if status != 200:
            raise RuntimeError(service + ".get HTTP " + str(status))
        body = json.loads(text)
        if body.get("error"):
            raise RuntimeError(service + ".get API code " + str(body["error"].get("error_code")))
        result = body.get("result", {})
        rows = result.get(result_key)
        if not isinstance(rows, list) or len(rows) > 1000:
            raise RuntimeError("Unexpected account inventory result")
        for item in rows:
            ident = item.get("Id")
            if not isinstance(ident, int) or ident in seen:
                raise RuntimeError("Duplicate or invalid account inventory ID")
            if service != "campaigns" and item.get("CampaignId") not in selection["CampaignIds"]:
                raise RuntimeError("Account inventory result outside selected campaign IDs")
            seen.add(ident)
        values.extend(rows)
        next_offset = result.get("LimitedBy")
        if next_offset is None:
            return values
        if not isinstance(next_offset, int) or next_offset <= offset or next_offset > 100000:
            raise RuntimeError("Invalid account inventory pagination")
        offset = next_offset
    raise RuntimeError("Account inventory exceeded bounded pagination; results incomplete")


def account_inventory():
    result = {"status": "in_progress", "started_at_utc": core.now(),
        "scope": "Only the existing configured Client-Login; no client/account enumeration or login changes.",
        "requested_campaign_states": ACCOUNT_STATES, "campaigns": [], "groups": [], "keywords": [],
        "errors": [], "availability_note": "Deleted/removed objects not returned by get cannot be reconstructed. Archived campaigns are requested explicitly; any unsupported batch is reported as unavailable, not zero keywords."}
    ACCOUNT_CAMPAIGN_IDS.clear()
    try:
        result["campaigns"] = inventory_get("campaigns", {"States": ACCOUNT_STATES}, "Campaigns")
        ACCOUNT_CAMPAIGN_IDS.update(x["Id"] for x in result["campaigns"])
        if len(ACCOUNT_CAMPAIGN_IDS) > 1000:
            raise RuntimeError("More than1000 campaigns; bounded inventory requires review")
        campaign_ids = sorted(ACCOUNT_CAMPAIGN_IDS)
        for start in range(0, len(campaign_ids), 10):
            batch = campaign_ids[start:start + 10]
            for service, key, target in (("adgroups", "AdGroups", "groups"), ("keywords", "Keywords", "keywords")):
                try:
                    rows = inventory_get(service, {"CampaignIds": batch}, key)
                    if len(result[target]) + len(rows) > 100000:
                        raise RuntimeError("Account inventory exceeds100000 object bound")
                    result[target].extend(rows)
                except Exception as error:
                    result["errors"].append({"service": service, "campaign_ids": batch, "error": core.clean(error),
                                             "interpretation": "Unavailable/partial, not zero objects"})
    except Exception as error:
        result["errors"].append({"service": "campaigns", "error": core.clean(error)})
    counts_campaign, counts_group, matches = {}, {}, []
    for item in result["keywords"]:
        for counts, key in ((counts_campaign, str(item["CampaignId"])), (counts_group, str(item["AdGroupId"]))):
            row = counts.setdefault(key, {"all_objects": 0, "manual_keywords": 0, "autotargeting_objects": 0, "states": {}, "statuses": {}})
            row["all_objects"] += 1
            row["autotargeting_objects" if item.get("Keyword") == "---autotargeting" else "manual_keywords"] += 1
            for target, source in (("states", "State"), ("statuses", "Status")):
                value = item.get(source, "MISSING")
                row[target][value] = row[target].get(value, 0) + 1
        text = item.get("Keyword", "")
        # A literal diagnostic only: preserve raw text, never simulate Yandex morphology.
        positive = re.split(r"\s+-", text, maxsplit=1)[0]
        normalized = " ".join(re.sub(r'[!+\[\]"()]', " ", positive.lower()).split())
        if "мехатроник" in normalized and "dq200" in normalized:
            matches.append({**item, "positive_text_for_literal_check": positive,
                            "literal_normalized_equals_target": normalized == "мехатроник dq200"})
    result.update(keyword_counts_by_campaign=counts_campaign, keyword_counts_by_group=counts_group,
        target_query="мехатроник dq200", target_literal_or_substring_candidates=matches,
        matching_note="Literal/substr candidates after removing inline negatives and simple operators; no morphological or semantic equivalence is asserted.",
        totals={"campaigns": len(result["campaigns"]), "groups": len(result["groups"]), "keyword_objects": len(result["keywords"]),
                "manual_keywords": sum(x.get("Keyword") != "---autotargeting" for x in result["keywords"]),
                "autotargeting_objects": sum(x.get("Keyword") == "---autotargeting" for x in result["keywords"])},
        status="partial" if result["errors"] else "complete", finished_at_utc=core.now())
    return result


def summarize(text, fields):
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    columns = reader.fieldnames or []
    if not columns or len(columns) != len(set(columns)):
        raise RuntimeError("Missing or duplicate report columns")
    metric_columns = {}
    for field in fields:
        if field in columns:
            if field in core.METRICS:
                metric_columns[field] = field
        elif field == "Conversions":
            alternatives = [x for x in columns if x.startswith("Conversions_") or x.startswith("Conversions.")]
            if len(alternatives) != 1:
                raise RuntimeError("Expected one all-goals LC conversion column")
            metric_columns[field] = alternatives[0]
        else:
            raise RuntimeError("Missing requested field " + field)
    totals = {key: Decimal(0) for key in core.METRICS}
    known = {key: 0 for key in core.METRICS}
    missing = {key: 0 for key in core.METRICS}
    daily = {}
    count = 0
    for count, row in enumerate(reader, 1):
        if None in row or any(value is None for value in row.values()):
            raise RuntimeError("Malformed TSV row")
        if row["CampaignId"] != str(core.CID) or not core.DATE_FROM <= row["Date"] <= core.DATE_TO:
            raise RuntimeError("Report row outside requested dates/campaign")
        day = daily.setdefault(row["Date"], {"totals": {key: Decimal(0) for key in core.METRICS},
                                             "known": {key: 0 for key in core.METRICS}, "rows": 0})
        day["rows"] += 1
        for key, column in metric_columns.items():
            value = row[column].strip()
            if value in {"", "-", "--"}:
                missing[key] += 1
                continue
            number = Decimal(value.replace("\u00a0", "").replace(",", "."))
            if not number.is_finite() or number < 0:
                raise RuntimeError("Invalid numeric report metric")
            totals[key] += number
            day["totals"][key] += number
            known[key] += 1
            day["known"][key] += 1
    return {"row_count": count, "response_fields": columns, "metric_columns": metric_columns,
        "totals_decimal": {key: str(value) if known[key] else None for key, value in totals.items()},
        "numeric_cells": known, "missing_numeric_cells": missing,
        "metric_coverage": {key: "not_reported" if not known[key] else "partial" if missing[key] else "complete" for key in totals},
        "totals_note": "Only numeric cells are summed; null is not zero. Missing current keywords in dated reports do not prove zero historical demand.",
        "by_day": {date: {"totals_decimal": {key: str(value) if day["known"][key] else None
            for key, value in day["totals"].items()}, "numeric_cells": day["known"], "rows": day["rows"]}
            for date, day in sorted(daily.items())}}


def save(report):
    temporary = OUT.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUT)


def run():
    core.OUT, core.QUERY_OUT, core.GROUP_OUT = OUT, QUERY_OUT, GROUP_OUT
    core.QUERY_FIELDS, core.KEYWORD_FIELDS = QUERY_FIELDS, KEYWORD_FIELDS
    core.post, core.summarize_tsv = safe_post, summarize
    # Existing exporter obtains campaign/group/shared negatives, keyword objects,
    # query/group reports, then current five-category/brand autotarget settings.
    code = core.run()
    report = json.loads(OUT.read_text(encoding="utf-8"))
    report["audit_kind"] = "current_keywords_and_matched_queries"
    report["serving_status_limitation"] = "ServingStatus is deprecated and always ELIGIBLE according to current Keywords.get docs; it cannot diagnose low search frequency."
    report["keyword_text_note"] = "Keyword contains the original text including inline negative words; ---autotargeting marks the separate autotargeting object."
    report["matched_keyword_note"] = "MatchedKeyword is a synonym/related/generated matching phrase; it is normally empty for other matches. CriterionId is the recommended configured targeting-ID field, not MatchedKeyword."
    counts = {}
    for item in report.get("keywords", []):
        if item.get("CampaignId") != core.CID:
            raise RuntimeError("Keyword outside requested campaign")
        group = counts.setdefault(str(item["AdGroupId"]), {"all_objects": 0, "manual_keywords": 0, "autotargeting_objects": 0,
            "states": {}, "statuses": {}})
        group["all_objects"] += 1
        group["autotargeting_objects" if item.get("Keyword") == "---autotargeting" else "manual_keywords"] += 1
        for key, field in (("states", "State"), ("statuses", "Status")):
            val = item.get(field, "MISSING")
            group[key][val] = group[key].get(val, 0) + 1
    report["keyword_object_counts_by_group"] = counts
    report["ui_count_note"] = "API counts distinguish manual keywords and autotargeting. Whether a particular UI counter includes autotargeting must be checked in that UI; this report does not infer its definition."
    report["account_inventory"] = account_inventory()
    report["sections"]["account_inventory"] = {key: report["account_inventory"][key] for key in ("status", "started_at_utc", "finished_at_utc")}
    if report["account_inventory"]["errors"]:
        report["errors"].append({"section": "account_inventory", "error": "Account inventory partial; see its explicit per-service/batch errors."})
        code = 1
    save(report)
    report["sections"]["ads_snapshot"] = {"status": "in_progress", "started_at_utc": core.now()}
    try:
        params_ads = {"SelectionCriteria": {"CampaignIds": [core.CID]},
            "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status", "StatusClarification"],
            "ShoppingAdFieldNames": ["SitelinkSetId", "SitelinksModeration", "FeedProcessingStatus", "TitleSources", "TextSources", "DefaultTexts"],
            "ResponsiveAdFieldNames": ["Titles", "Texts", "Href", "SitelinkSetId", "SitelinksModeration"],
            "Page": {"Limit": 1000}}
        status, text, _ = safe_post("ads", {"method": "get", "params": params_ads})
        if status != 200:
            raise RuntimeError("ads.get HTTP " + str(status))
        body = json.loads(text)
        values = body.get("result", {}).get("Ads", [])
        expected_ids = {1927186476439690942, 1927189770678081700, 1927190920657966611,
                        1927466195846975291, 1927466206584354174}
        if (body.get("error") or body.get("result", {}).get("LimitedBy") or len(values) != 5
                or {x.get("Id") for x in values} != expected_ids
                or any(x.get("CampaignId") != core.CID for x in values)):
            raise RuntimeError("Unexpected current five-ad inventory")
        report["ads"] = values
        report["expected_current_sitelink_set_id"] = 1526814548
        report["ads_all_use_expected_current_sitelink_set"] = all(
            ad.get("ShoppingAd", ad.get("ResponsiveAd", {})).get("SitelinkSetId") == 1526814548 for ad in values)
        report["sections"]["ads_snapshot"]["status"] = "complete"
    except Exception as error:
        report["sections"]["ads_snapshot"]["status"] = "failed"
        report["errors"].append({"section": "ads_snapshot", "error": core.clean(error)})
        code = 1
    report["sections"]["ads_snapshot"]["finished_at_utc"] = core.now()
    CRITERIA_OUT.parent.mkdir(parents=True, exist_ok=True)
    CRITERIA_OUT.write_text("\t".join(CRITERIA_FIELDS) + "\n", encoding="utf-8")
    params = {"SelectionCriteria": {"DateFrom": core.DATE_FROM, "DateTo": core.DATE_TO,
        "Filter": [{"Field": "CampaignId", "Operator": "IN", "Values": [str(core.CID)]}]},
        "FieldNames": CRITERIA_FIELDS, "ReportName": "leoparts-keywords-criteria-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "ReportType": "CRITERIA_PERFORMANCE_REPORT", "DateRangeType": "CUSTOM_DATE", "Format": "TSV", "IncludeVAT": "YES"}
    record = {"params": params, "path": str(CRITERIA_OUT), "attempts": [], "requested_fields": CRITERIA_FIELDS}
    report["reports"]["criteria"] = record
    report["sections"]["criteria_report"] = {"status": "in_progress", "started_at_utc": core.now()}
    save(report)
    try:
        for attempt in range(1, 21):
            status, text, response_headers = safe_post("reports", {"params": params}, reports=True)
            record["attempts"].append({"attempt": attempt, "http_status": status, "at_utc": core.now()})
            if status == 200:
                record.update(summarize(text, CRITERIA_FIELDS))
                CRITERIA_OUT.write_text(text, encoding="utf-8")
                record["captured_at_utc"] = core.now()
                report["sections"]["criteria_report"]["status"] = "complete"
                break
            if status not in (201, 202):
                raise RuntimeError("Criteria Reports HTTP " + str(status))
            try:
                delay = min(30, max(0, int({k.lower(): v for k, v in response_headers.items()}.get("retryin", "10"))))
            except (TypeError, ValueError):
                delay = 10
            save(report)
            if attempt < 20:
                time.sleep(delay)
        else:
            raise RuntimeError("Criteria report was not ready within bounded polling")
    except Exception as error:
        report["sections"]["criteria_report"]["status"] = "failed"
        report["errors"].append({"section": "criteria_report", "error": core.clean(error)})
        code = 1
    report["sections"]["criteria_report"]["finished_at_utc"] = core.now()
    report["status"] = "partial" if report["errors"] else "complete"
    report["finished_at_utc"] = core.now()
    save(report)
    print("Read-only keyword audit:", report["status"], "groups:", len(counts))
    return code


if __name__ == "__main__":
    sys.exit(run())
