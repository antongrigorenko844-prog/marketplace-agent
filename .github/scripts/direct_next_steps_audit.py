"""Read-only Direct goals, legacy targeting categories, and ad-status audit.

Only campaigns.get, ads.get, feeds.get, Reports and Live4 GetStatGoals are permitted.
The Live4 request contains a token: its body is NEVER logged or persisted.
"""
import csv
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

CID = 716316525
GOALS = ["675506966", "675507028"]
DATE_FROM, DATE_TO = "2026-10-20", "2026-11-19"
BASE = "https://api.direct.yandex.com/json/v501/"
LIVE = "https://api.direct.yandex.ru/live/v4/json/"
OUT = Path("data/direct_next_steps_audit_20261119.json")
CATEGORY_OUT = Path("data/direct_next_steps_categories_20261119.tsv")
GOAL_OUT = Path("data/direct_next_steps_goals_20261119.tsv")
VERIFY_OUT = Path("data/direct_thematic_negatives_verification_20261119.json")
FEED_ID = 4061297
PUBLIC_DOMAINS = ("leoparts-akpp.ru", "tildacdn.com", "tilda.ws", "tilda.ru", "tilda.cc")
FIELDS = ["Date", "CampaignId", "AdGroupId", "AdGroupName", "CriterionType",
          "TargetingCategory", "Impressions", "Clicks", "Cost", "Conversions"]


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    value = str(value)
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
    return (value.replace(token, "[redacted]") if token else value)[:500]


def request(url, payload, reports=False, live=False):
    token = os.environ.get("YANDEX_DIRECT_TOKEN")
    if not token:
        raise RuntimeError("YANDEX_DIRECT_TOKEN is unavailable")
    if live:
        if url != LIVE or payload.get("method") != "GetStatGoals":
            raise RuntimeError("Unsupported Live4 operation")
        # Copy into memory only; neither payload nor headers are returned/logged.
        payload = {**payload, "token": token, "locale": "ru"}
        headers = {"Content-Type": "application/json; charset=utf-8"}
    else:
        allowed = {BASE + "campaigns", BASE + "ads", BASE + "feeds", BASE + "reports"}
        if url not in allowed or (reports and url != BASE + "reports"):
            raise RuntimeError("Unsupported endpoint")
        if not reports and payload.get("method") != "get":
            raise RuntimeError("Only get is permitted")
        if reports and (set(payload) != {"params"} or "method" in payload):
            raise RuntimeError("Invalid read-only report envelope")
        headers = {"Authorization": "Bearer " + token,
                   "Content-Type": "application/json; charset=utf-8", "Accept-Language": "en"}
        if os.environ.get("YANDEX_DIRECT_LOGIN"):
            headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
        if reports:
            headers.update({"processingMode": "auto", "returnMoneyInMicros": "false",
                            "skipReportHeader": "true", "skipColumnHeader": "false",
                            "skipReportSummary": "true"})
    req = urllib.request.Request(url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read().decode("utf-8-sig"), dict(response.headers)
    except urllib.error.HTTPError as error:
        # Error content is deliberately omitted: it could echo a credential.
        error.close()
        return error.code, "", dict(error.headers)


def api_get(service, params, collection):
    items, offset = [], 0
    for _ in range(20):
        code, text, _ = request(BASE + service, {"method": "get", "params": {
            **params, "Page": {"Limit": 1000, "Offset": offset}}})
        if code != 200:
            raise RuntimeError(service + ".get HTTP " + str(code))
        body = json.loads(text)
        if body.get("error"):
            raise RuntimeError(service + ".get API error " + str(body["error"].get("error_code", "unknown")))
        result = body.get("result", {})
        if not isinstance(result.get(collection), list):
            raise RuntimeError(service + ".get missing collection")
        id_field = "Id" if collection == "Campaigns" else "CampaignId"
        if any(item.get(id_field) != CID for item in result[collection]):
            raise RuntimeError(service + ".get returned an object outside the campaign")
        items.extend(result[collection])
        if "LimitedBy" not in result:
            return items
        following = result["LimitedBy"]
        if not isinstance(following, int) or following <= offset:
            raise RuntimeError("Invalid pagination")
        offset = following
    raise RuntimeError("Pagination limit exceeded")


def public_url(raw):
    """Return a publishable URL and whether an unauthenticated fetch is allowed."""
    try:
        parsed = urllib.parse.urlsplit(raw)
        hostname = (parsed.hostname or "").lower()
        sensitive = re.compile(r"password|passwd|token|secret|auth|signature|api.?key|^key$|login|username", re.I)
        query = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
        private_query = any(sensitive.search(key) for key, _ in query)
        safe_query = urllib.parse.urlencode([(key, "[redacted]" if sensitive.search(key) else val)
                                            for key, val in query])
        port = parsed.port
        netloc = hostname + ((":" + str(port)) if port not in (None, 443) else "")
        display = urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, safe_query, ""))
        allowed = (parsed.scheme == "https" and port in (None, 443)
                   and not parsed.username and not parsed.password and not private_query
                   and any(hostname == domain or hostname.endswith("." + domain) for domain in PUBLIC_DOMAINS))
        return clean(display) if len(display) <= 500 else "[URL too long to publish]", allowed
    except (TypeError, ValueError):
        return "[invalid URL]", False


def public_feed(raw_url):
    """No Direct credentials or cookies enter this independently constructed GET."""
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None

    opener = urllib.request.build_opener(NoRedirect())
    current = raw_url
    for redirect in range(4):
        safe, allowed = public_url(current)
        if not allowed:
            return {"status": "skipped", "reason": "URL outside safe public allowlist or contains credentials", "url": safe}
        req = urllib.request.Request(current, headers={"Accept": "application/xml, text/xml;q=0.9",
                                     "User-Agent": "LEOPARTS-read-only-feed-audit/1.0"}, method="GET")
        try:
            with opener.open(req, timeout=25) as response:
                content = response.read(2 * 1024 * 1024 + 1)
                if len(content) > 2 * 1024 * 1024:
                    return {"status": "skipped", "reason": "Feed exceeds 2 MiB", "url": safe}
                break
        except urllib.error.HTTPError as error:
            code = error.code
            location = error.headers.get("Location")
            error.close()
            if code in (301, 302, 303, 307, 308) and location and redirect < 3:
                current = urllib.parse.urljoin(current, location)
                continue
            return {"status": "failed", "reason": "Public feed HTTP " + str(code), "url": safe}
        except Exception:
            return {"status": "failed", "reason": "Public feed transport error", "url": safe}
    else:
        return {"status": "skipped", "reason": "Redirect limit", "url": public_url(current)[0]}
    # ElementTree never fetches external DTDs. Reject entity declarations and
    # non-UTF-8 style encodings to make the pre-parse entity check unambiguous.
    if b"\x00" in content or b"<!ENTITY" in content.upper():
        return {"status": "skipped", "reason": "Unsupported XML encoding or entity declaration", "url": safe}
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return {"status": "failed", "reason": "Invalid feed XML", "url": safe}
    offers = []
    for offer in root.iter():
        if offer.tag.rsplit("}", 1)[-1] != "offer":
            continue
        fields = {child.tag.rsplit("}", 1)[-1]: " ".join("".join(child.itertext()).split()) for child in offer}
        item = {"id": clean(offer.get("id", "")), "available": offer.get("available")}
        for field in ("name", "price", "currencyId", "categoryId", "description"):
            item[field] = clean(fields.get(field, ""))
        item["url"] = public_url(fields.get("url", ""))[0]
        offers.append(item)
        if len(offers) >= 100:
            return {"status": "partial", "reason": "100-offer bound", "url": safe, "offers": offers}
    return {"status": "complete", "url": safe, "offer_count": len(offers), "offers": offers}


def parse_report(text, goal_ids=None):
    reader = csv.DictReader(io.StringIO(text), delimiter="\t")
    columns = reader.fieldnames or []
    if not columns or len(columns) != len(set(columns)):
        raise RuntimeError("Missing or duplicate report columns")
    for field in FIELDS:
        if field != "Conversions" and field not in columns:
            raise RuntimeError("Missing report field " + field)
    if goal_ids:
        conversions = {}
        for column in columns:
            match = re.fullmatch(r"Conversions[_.](\d+)[_.]LC", column)
            if match:
                conversions[match.group(1)] = column
        if set(conversions) != set(goal_ids):
            raise RuntimeError("Expected exactly the requested LC goal columns")
        conversion_columns = list(conversions.values())
    else:
        conversion_columns = [c for c in columns if c == "Conversions" or c in ("Conversions_LC", "Conversions.LC")]
        if len(conversion_columns) != 1:
            raise RuntimeError("Expected one aggregate LC conversion column")
    metric_columns = ["Impressions", "Clicks", "Cost"] + conversion_columns
    totals = {name: Decimal(0) for name in metric_columns}
    missing = {name: 0 for name in metric_columns}
    numeric = {name: 0 for name in metric_columns}
    categories = {}
    category_numeric = {}
    category_rows = {}
    count = 0
    for count, row in enumerate(reader, 1):
        if None in row or any(value is None for value in row.values()):
            raise RuntimeError("Malformed report row")
        if row["CampaignId"] != str(CID) or not DATE_FROM <= row["Date"] <= DATE_TO:
            raise RuntimeError("Report row outside requested campaign/dates")
        bucket = row["CriterionType"] + ":" + row["TargetingCategory"]
        sums = categories.setdefault(bucket, {name: Decimal(0) for name in metric_columns})
        known = category_numeric.setdefault(bucket, {name: 0 for name in metric_columns})
        category_rows[bucket] = category_rows.get(bucket, 0) + 1
        for name in metric_columns:
            value = row[name].strip()
            if value in ("", "-", "--"):
                missing[name] += 1
                continue
            number = Decimal(value.replace("\u00a0", "").replace(",", "."))
            if not number.is_finite() or number < 0:
                raise RuntimeError("Invalid numeric report metric")
            totals[name] += number
            sums[name] += number
            numeric[name] += 1
            known[name] += 1
    def coverage(known_count, row_count):
        if row_count == 0:
            return "no_rows"
        if known_count == 0:
            return "not_reported"
        return "complete" if known_count == row_count else "partial_numeric_cells"
    return {"rows": count, "columns": columns,
            "totals_decimal": {k: str(v) if numeric[k] else None for k, v in totals.items()},
            "totals_semantics": "Sums of numeric cells only. null means no numeric values were reported; placeholders are not assumed to be zero. Partial coverage is not a complete total.",
            "missing_numeric_cells": missing,
            "numeric_cells": numeric,
            "metric_coverage": {k: coverage(v, count) for k, v in numeric.items()},
            "all_missing_numeric_columns": [k for k, v in numeric.items() if count and not v],
            "by_criterion_category_decimal": {k: {m: str(v) if category_numeric[k][m] else None
                                                    for m, v in values.items()}
                                                for k, values in categories.items()},
            "by_criterion_category_numeric_cells": category_numeric,
            "by_criterion_category_coverage": {k: {m: coverage(v, category_rows[k])
                                                    for m, v in values.items()}
                                                 for k, values in category_numeric.items()}}


def run():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    for path in (CATEGORY_OUT, GOAL_OUT):
        path.write_text("\t".join(FIELDS) + "\n", encoding="utf-8")
    VERIFY_OUT.write_text(json.dumps({"mode": "read_only", "status": "not_started",
                                    "captured_at_utc": now()}), encoding="utf-8")
    audit = {"mode": "read_only", "status": "in_progress", "campaign_id": CID,
             "captured_at_utc": now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
             "period": {"from": DATE_FROM, "to": DATE_TO, "timezone": "Europe/Moscow", "today_partial": True},
             "goal_ids": GOALS, "sections": {}, "errors": [],
             "limitations": [
                 "TargetingCategory is a deprecated legacy report dimension. It cannot resolve new Exact vs Narrow separately.",
                 "Category report contains SEARCH/AUTOTARGETING only; goal report contains all SEARCH criterion types.",
                 "Aggregate conversions are all-goal visits using default LC; goal-specific conversions use explicit LC.",
                 "Do not sum goal conversion columns as unique orders or count repeated traffic across the two reports.",
                 "TSV placeholders are not numeric zeros. Totals without numeric observations are null; partial numeric coverage is labelled explicitly.",
                 "Live4 returns goal names, not goal types or event conditions. No Metrica scope/access is attempted.",
                 "Goal names and configured goal values do not verify paid orders or realized profit.",
                 "Recent data and the partial current day can change; decimal totals cover the full requested period."]}

    def save():
        temporary = OUT.with_suffix(".json.tmp")
        temporary.write_text(json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(OUT)

    def section(name, action):
        audit["sections"][name] = {"status": "in_progress", "started_at_utc": now()}
        save()
        try:
            audit[name] = action()
            audit["sections"][name]["status"] = "complete"
        except Exception as error:
            audit["sections"][name]["status"] = "failed"
            audit["errors"].append({"section": name, "error": clean(error)})
        audit["sections"][name]["finished_at_utc"] = now()
        save()

    def goal_names():
        code, text, _ = request(LIVE, {"method": "GetStatGoals", "param": {"CampaignIDS": [CID]}}, live=True)
        if code != 200:
            raise RuntimeError("GetStatGoals HTTP " + str(code))
        body = json.loads(text)
        if body.get("error_code") or not isinstance(body.get("data"), list):
            raise RuntimeError("GetStatGoals API error " + str(body.get("error_code", "unexpected response")))
        names = [{"CampaignID": item.get("CampaignID"), "GoalID": item.get("GoalID"), "Name": item.get("Name")}
                 for item in body["data"] if item.get("CampaignID") == CID and str(item.get("GoalID")) in GOALS]
        result = {"source": "Live4.GetStatGoals", "names": names,
                  "missing_goal_ids": sorted(set(GOALS) - {str(item["GoalID"]) for item in names}),
                  "goal_types_and_conditions": "unavailable_without_Metrica_read_access"}
        if result["missing_goal_ids"]:
            audit["goal_names"] = result
            raise RuntimeError("GetStatGoals omitted requested goal IDs")
        return result

    def thematic_verification():
        from direct_thematic_verification import verify_thematic_negatives
        result = verify_thematic_negatives(
            lambda params: {"Campaigns": api_get("campaigns", params, "Campaigns")},
            output=str(VERIFY_OUT))
        if result.get("status") != "verified_canonical_41":
            audit["thematic_verification"] = result
            raise RuntimeError("Read-only canonical-41 reconciliation requires review; see separate output")
        return {"status": result["status"], "path": str(VERIFY_OUT),
                "observed_phrase_count": result["observed_phrase_count"]}

    def feed():
        params = {"SelectionCriteria": {"Ids": [FEED_ID]},
                  "FieldNames": ["Id", "Name", "SourceType", "FilterSchema", "Status", "NumberOfItems", "TitleAndTextSources"],
                  "UrlFeedFieldNames": ["Url"], "Page": {"Limit": 100}}
        code, text, _ = request(BASE + "feeds", {"method": "get", "params": params})
        if code != 200:
            raise RuntimeError("feeds.get HTTP " + str(code))
        body = json.loads(text)
        if body.get("error"):
            raise RuntimeError("feeds.get API error " + str(body["error"].get("error_code", "unknown")))
        result = body.get("result", {})
        feeds = result.get("Feeds", [])
        if len(feeds) != 1 or feeds[0].get("Id") != FEED_ID or result.get("LimitedBy"):
            raise RuntimeError("Unexpected feed selection")
        item = feeds[0]
        # Select response fields explicitly: never persist Login/Password even
        # if the server were to add an unrequested field.
        output = {key: item.get(key) for key in params["FieldNames"]}
        raw_url = (item.get("UrlFeed") or {}).get("Url")
        safe, allowed = public_url(raw_url)
        output["url"] = safe
        output["public_feed"] = (public_feed(raw_url) if allowed else {
            "status": "skipped", "reason": "Missing URL, credentials, or host outside safe allowlist"})
        return output

    def performance(name, destination, goal_ids=None):
        filters = [{"Field": "CampaignId", "Operator": "IN", "Values": [str(CID)]},
                   {"Field": "AdNetworkType", "Operator": "EQUALS", "Values": ["SEARCH"]}]
        if not goal_ids:
            filters.append({"Field": "CriterionType", "Operator": "EQUALS", "Values": ["AUTOTARGETING"]})
        params = {"SelectionCriteria": {"DateFrom": DATE_FROM, "DateTo": DATE_TO, "Filter": filters},
                  "FieldNames": FIELDS, "ReportType": "ADGROUP_PERFORMANCE_REPORT",
                  "ReportName": "leoparts-next-" + name + "-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f"),
                  "DateRangeType": "CUSTOM_DATE", "Format": "TSV", "IncludeVAT": "YES"}
        if goal_ids:
            params.update({"Goals": goal_ids, "AttributionModels": ["LC"]})
        # Report params have no credentials; Live4 request bodies are never recorded.
        metadata = {"params": params, "path": str(destination), "attempts": []}
        audit[name + "_request"] = metadata
        for attempt in range(1, 21):
            code, text, headers = request(BASE + "reports", {"params": params}, reports=True)
            metadata["attempts"].append({"attempt": attempt, "http_status": code, "at_utc": now()})
            save()
            if code == 200:
                parsed = parse_report(text, goal_ids)
                destination.write_text(text, encoding="utf-8")
                return {"captured_at_utc": now(), **parsed}
            if code not in (201, 202):
                raise RuntimeError("Reports HTTP " + str(code))
            lower = {k.lower(): v for k, v in headers.items()}
            try:
                delay = max(1, min(30, int(lower.get("retryin", "10"))))
            except (TypeError, ValueError):
                delay = 10
            if attempt < 20:
                time.sleep(delay)
        raise RuntimeError("Report not ready after 20 attempts")

    save()
    section("campaign", lambda: api_get("campaigns", {
        "SelectionCriteria": {"Ids": [CID]},
        "FieldNames": ["Id", "Name", "Type", "State", "Status", "Currency"],
        "UnifiedCampaignFieldNames": ["CounterIds", "PriorityGoals", "AttributionModel", "BiddingStrategy", "Settings"]}, "Campaigns"))
    section("thematic_verification", thematic_verification)
    section("ads", lambda: api_get("ads", {
        "SelectionCriteria": {"CampaignIds": [CID]},
        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status", "StatusClarification"],
        "ResponsiveAdFieldNames": ["Titles", "Texts", "Href", "SitelinkSetId", "SitelinksModeration"],
        "ShoppingAdFieldNames": ["SitelinkSetId", "SitelinksModeration", "FeedId", "FeedProcessingStatus"]}, "Ads"))
    section("feed", feed)
    section("goal_names", goal_names)
    section("category_report", lambda: performance("categories", CATEGORY_OUT))
    section("goal_report", lambda: performance("goals", GOAL_OUT, GOALS))
    audit["status"] = "partial" if audit["errors"] else "complete"
    audit["finished_at_utc"] = now()
    save()
    print("Read-only next-steps audit:", audit["status"], "; failed sections:", len(audit["errors"]))
    return 1 if audit["errors"] else 0


if __name__ == "__main__":
    sys.exit(run())
