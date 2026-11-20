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
DATE_FROM, DATE_TO = "2026-10-21", "2026-11-20"
BASE = "https://api.direct.yandex.com/json/v501/"
LIVE = "https://api.direct.yandex.ru/live/v4/json/"
OUT = Path("data/direct_audit_20261120/full_audit.json")
CATEGORY_OUT = Path("data/direct_audit_20261120/categories.tsv")
GOAL_OUT = Path("data/direct_audit_20261120/goals.tsv")
VERIFY_OUT = Path("data/direct_audit_20261120/negatives_verification.json")
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
        allowed = {BASE + x for x in ("campaigns", "adgroups", "ads", "keywords", "feeds", "sitelinks", "reports")}
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
    section("adgroups", lambda: api_get("adgroups", {"SelectionCriteria": {"CampaignIds": [CID]}, "FieldNames": ["Id","Name","CampaignId","Status","ServingStatus","RegionIds","NegativeKeywords","Type"]}, "AdGroups"))
    section("keywords", lambda: api_get("keywords", {"SelectionCriteria": {"CampaignIds": [CID]}, "FieldNames": ["Id","Keyword","AdGroupId","CampaignId","State","Status","ServingStatus","Bid"]}, "Keywords"))
    section("ads", lambda: api_get("ads", {
        "SelectionCriteria": {"CampaignIds": [CID]},
        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status", "StatusClarification"],
        "ResponsiveAdFieldNames": ["Titles", "Texts", "Href", "SitelinkSetId", "SitelinksModeration"],
        "ShoppingAdFieldNames": ["SitelinkSetId", "SitelinksModeration", "FeedId", "FeedProcessingStatus"]}, "Ads"))
    section("feed", feed)
    section("goal_names", goal_names)
    section("category_report", lambda: performance("categories", CATEGORY_OUT))
    section("goal_report", lambda: performance("goals", GOAL_OUT, GOALS))
    section("campaign_daily", lambda: extra_report("campaign_daily", "CAMPAIGN_PERFORMANCE_REPORT", ["Date","CampaignId","Impressions","Clicks","Cost","Conversions"]))
    section("groups_daily", lambda: extra_report("groups_daily", "ADGROUP_PERFORMANCE_REPORT", ["Date","CampaignId","AdGroupId","AdGroupName","Impressions","Clicks","Cost","Conversions"], ["Date","CampaignId","AdGroupId","Impressions","Clicks","Cost","Conversions"]))
    section("ads_daily", lambda: extra_report("ads_daily", "AD_PERFORMANCE_REPORT", ["Date","CampaignId","AdGroupId","AdId","Impressions","Clicks","Cost","Conversions"], ["Date","CampaignId","AdId","Impressions","Clicks","Cost","Conversions"]))
    section("criteria_daily", lambda: extra_report("criteria_daily", "CRITERIA_PERFORMANCE_REPORT", ["Date","CampaignId","AdGroupId","CriterionId","Criterion","CriterionType","Impressions","Clicks","Cost","Conversions"], ["Date","CampaignId","AdGroupId","CriterionId","Impressions","Clicks","Cost","Conversions"]))
    section("queries_daily", lambda: extra_report("queries_daily", "SEARCH_QUERY_PERFORMANCE_REPORT", ["Date","CampaignId","AdGroupId","Query","MatchedKeyword","CriterionType","Impressions","Clicks","Cost","Conversions"], ["Date","CampaignId","AdGroupId","Query","Impressions","Clicks","Cost","Conversions"]))
    section("device_region", lambda: extra_report("device_region", "CUSTOM_REPORT", ["CampaignId","Device","LocationOfPresenceName","Impressions","Clicks","Cost","Conversions"], ["CampaignId","Device","Impressions","Clicks","Cost","Conversions"]))
    section("metrika_goals", read_metrika_goals)
    section("sitelinks", lambda: current_sitelinks(audit.get("ads") or []))
    section("historical_comparison", lambda: compare_baseline(audit))
    section("statistical_summary", lambda: calculate_summary(audit))
    audit["status"] = "partial" if audit["errors"] else "complete"
    audit["finished_at_utc"] = now()
    save()
    write_audit_summary(audit)
    print("Read-only full audit 2026-11-20:", audit["status"], "; failed sections:", len(audit["errors"]))
    return 1 if audit["errors"] else 0



# Further read-only diagnostics. The original audited requests and error handling
# above remain intact; extra functions only use method get or Reports.
from collections import defaultdict

def extra_report(name, kind, fields, fallback=None):
    def issue(columns, suffix):
        params = {"SelectionCriteria": {"DateFrom": DATE_FROM, "DateTo": DATE_TO,
                  "Filter": [{"Field":"CampaignId","Operator":"IN","Values":[str(CID)]},
                             {"Field":"AdNetworkType","Operator":"EQUALS","Values":["SEARCH"]}]},
                  "FieldNames": columns, "ReportType":kind,
                  "ReportName":"leoparts20-"+name+"-"+suffix+"-"+datetime.now(timezone.utc).strftime("%H%M%S%f"),
                  "DateRangeType":"CUSTOM_DATE","Format":"TSV","IncludeVAT":"YES","IncludeDiscount":"NO"}
        attempts=[]
        for n in range(1,16):
            status,raw,hdr=request(BASE+"reports",{"params":params},reports=True)
            attempts.append(status)
            if status==200:
                reader=csv.DictReader(io.StringIO(raw),delimiter="\t")
                if not reader.fieldnames or "Clicks" not in reader.fieldnames or "Cost" not in reader.fieldnames:
                    raise RuntimeError(name+" malformed report header")
                rows=list(reader)
                if any(None in r or any(v is None for v in r.values()) for r in rows):
                    raise RuntimeError(name+" malformed report rows")
                if any(r.get("CampaignId") not in (None,str(CID)) for r in rows):
                    raise RuntimeError(name+" cross-campaign data")
                if any(r.get("Date") and not DATE_FROM<=r["Date"]<=DATE_TO for r in rows):
                    raise RuntimeError(name+" date out of requested range")
                dest=OUT.parent/(name+".tsv")
                dest.write_text(raw,encoding="utf-8")
                return {"path":str(dest),"rows":len(rows),"fields":reader.fieldnames,
                        "retrieved_at_utc":now(),"attempts":attempts,"fallback":suffix!="extended",
                        "totals":sum_stats(rows)}
            if status not in (201,202):raise RuntimeError(name+" report HTTP "+str(status))
            h={k.lower():v for k,v in hdr.items()}
            try: wait=min(12,max(1,int(h.get("retryin","5"))))
            except ValueError:wait=5
            time.sleep(wait)
        raise RuntimeError(name+" reports not ready after 15 attempts")
    try:return issue(fields,"extended")
    except Exception as exc:
        if fallback is None:raise
        result=issue(fallback,"basic")
        result["fallback_reason"]=clean(exc)
        return result

def number(v):
    if v is None or str(v).strip() in ("","-","--"):return None
    try:
        n=Decimal(str(v).replace(", .",".").replace(",",".").replace(" ","").replace("\u00a0",""))
        return n if n.is_finite() and n>=0 else None
    except Exception:return None

def sum_stats(rows):
    totals={}
    for col in ("Impressions","Clicks","Cost","Conversions"):
        good=[number(r.get(col)) for r in rows]
        known=[x for x in good if x is not None]
        totals[col]=str(sum(known,Decimal(0))) if known else (None if rows else "0")
        totals[col+"_reported_cells"]=len(known)
        totals[col+"_missing_cells"]=len(good)-len(known)
    cost=number(totals.get("Cost"));clicks=number(totals.get("Clicks"));impr=number(totals.get("Impressions"))
    totals["ctr_percent"]=str(round(Decimal(100)*clicks/impr,2)) if impr and clicks is not None else None
    totals["avg_cpc_rub"]=str(round(cost/clicks,2)) if clicks and cost is not None else None
    return totals

def read_rows(kind):
    path=OUT.parent/(kind+".tsv")
    if not path.exists():return []
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8")),delimiter="\t"))

def aggregate(rows,keys):
    acc=defaultdict(list)
    for row in rows:acc[tuple(row.get(k,"") for k in keys)].append(row)
    result=[dict(zip(keys,ids)) | sum_stats(rs) for ids,rs in acc.items()]
    return sorted(result,key=lambda r:number(r.get("Cost")) or Decimal(0),reverse=True)

def guess_intent(text):
    q=str(text or "").lower().replace("ё","е")
    if any(x in q for x in ("ноутбук","мерседес","ваканси","iphone","гидроблок abs")):return "вероятно нерелевантный"
    if any(x in q for x in ("прошивка","прошить","снятие","установка","как снять")):return "услуга"
    if any(x in q for x in ("что такое","схема","скачать","видео","форум","инструкция")):return "информационный"
    product=any(x in q for x in ("мехатрон","dq200","0am","0cw","дсг7","dsg7"))
    buy=any(x in q for x in ("купить","цен","стоимость","заказ","в наличии","доставка"))
    if product and buy:return "коммерческий"
    if product:return "спорный коммерческий/ремонтный"
    return "требует ручной проверки"

def calculate_summary(audit):
    daily=read_rows("campaign_daily")
    periods={}
    last=datetime.strptime(DATE_TO,"%Y-%m-%d").date()
    for name,days in (("7_days",7),("14_days",14),("30_days",30),("19_november",1),("20_november_partial",0)):
        dfrom=(last-timedelta(days=days)).isoformat()
        dto=(last-timedelta(days=1)).isoformat() if days else last.isoformat()
        rows=[r for r in daily if dfrom<=r.get("Date","")<=dto]
        periods[name]={"from":dfrom,"to":dto,**sum_stats(rows)}
    queries=aggregate(read_rows("queries_daily"),["Query","AdGroupId"])
    for row in queries:row["intent_estimate"]=guess_intent(row.get("Query"))
    goal_columns=(audit.get("goal_report") or {}).get("columns") or []
    coverage={c:(audit["goal_report"].get("metric_coverage") or {}).get(c)
              for c in goal_columns if c.startswith("Conversions_")}
    return {"periods":periods,
        "ad_groups":aggregate(read_rows("groups_daily"),["AdGroupId","AdGroupName"]),
        "ads":aggregate(read_rows("ads_daily"),["AdId","AdGroupId"]),
        "criteria":aggregate(read_rows("criteria_daily"),["AdGroupId","CriterionId","Criterion"]),
        "search_queries":queries,
        "search_queries_intent":aggregate([dict(r,intent=guess_intent(r.get("Query"))) for r in read_rows("queries_daily")],["intent"]),
        "device_region":aggregate(read_rows("device_region"),["Device","LocationOfPresenceName"]),
        "goal_report_coverage":coverage,
        "note":"Intent labels are conservative rules, not verified human classifications; all periods derive from the same daily campaign report."}

def current_sitelinks(ads):
    ids=sorted({int(z["SitelinkSetId"]) for a in ads for k in ("ResponsiveAd","ShoppingAd")
        for z in [a.get(k) or {}] if z.get("SitelinkSetId")})
    if not ids:return {"status":"none_present","sets":[]}
    items=api_get("sitelinks",{"SelectionCriteria":{"Ids":ids},"FieldNames":["Id","Sitelinks"]},"SitelinksSets")
    return {"status":"ok","sets":items}

def read_metrika_goals():
    token=os.getenv("YANDEX_METRIKA_TOKEN") or os.getenv("YANDEX_DIRECT_TOKEN")
    if not token:return {"status":"missing_read_token"}
    url="https://api-metrika.yandex.net/management/v1/counter/113839860/goals"
    req=urllib.request.Request(url,headers={"Authorization":"OAuth "+token},method="GET")
    try:
        with urllib.request.urlopen(req,timeout=30) as res:d=json.loads(res.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        code=e.code;e.close()
        return {"status":"no_metrika_access","http_status":code,"note":"Direct API authorization does not imply Metrika read permission"}
    return {"status":"ok","goals":[{"id":x.get("id"),"name":x.get("name"),"type":x.get("type"),
        "conditions":x.get("conditions")} for x in d.get("goals",[]) if x.get("id") in [675506966,675507028]]}

def compare_baseline(audit):
    path=Path("data/direct_next_steps_audit_20261119.json")
    if not path.exists():return {"status":"baseline_missing"}
    before=json.loads(path.read_text(encoding="utf-8"))
    old=(before.get("campaign") or [{}])[0];cur=(audit.get("campaign") or [{}])[0]
    verification=Path("data/direct_thematic_negatives_verification_20261119.json")
    oldneg=((json.loads(verification.read_text()).get("after") or {}).get("NegativeKeywords") or {}).get("Items",[]) if verification.exists() else []
    fresh=(cur.get("NegativeKeywords") or {}).get("Items") or []
    return {"as_of_previous_utc":before.get("captured_at_utc"),"previous_status":old.get("State"),
        "current_status":cur.get("State"),"previous_type":old.get("Type"),"current_type":cur.get("Type"),
        "previous_negatives":len(oldneg),"current_negatives":len(fresh),
        "new_negatives":sorted(set(fresh)-set(oldneg)),"removed_negatives":sorted(set(oldneg)-set(fresh)),
        "previous_ads":len(before.get("ads") or []),"current_ads":len(audit.get("ads") or []),
        "previous_strategy":(old.get("UnifiedCampaign") or {}).get("BiddingStrategy"),
        "current_strategy":(cur.get("UnifiedCampaign") or {}).get("BiddingStrategy"),
        "note":"Readback comparison; last change timestamp has not been independently retrieved."}

def write_audit_summary(audit):
    snap=audit.get("statistical_summary") or {}
    p=snap.get("periods") or {}
    lines=["# LEOPARTS — Яндекс Директ, аудит на 20 ноября 2026","",
      "Кампания: 716316525. Только чтение; ни один рекламный объект не менялся.","",
      "| Период | Показы | Клики | Расход с НДС | CTR | CPC |","|---|---:|---:|---:|---:|---:|"]
    for key,title in (("7_days","7 полных дней"),("14_days","14 полных дней"),
         ("30_days","30 полных дней"),("19_november","19 ноября"),("20_november_partial","20 ноября, неполный")):
        row=p.get(key) or {}
        lines.append("| "+title+" | "+str(row.get("Impressions"))+" | "+str(row.get("Clicks"))+
          " | "+str(row.get("Cost"))+" ₽ | "+str(row.get("ctr_percent"))+"% | "+str(row.get("avg_cpc_rub"))+" ₽ |")
    lines+=["","## Качество данных"]
    for k,v in audit.get("sections",{}).items():
        lines.append("- "+k+": "+v.get("status","unknown")+((" — "+v.get("error")) if v.get("error") else ""))
    lines+=["","Конверсии по целям нельзя приравнивать к оплаченным заказам.",
      "Прочерк в колонке конверсии не означает ноль. 20 ноября — неполный день.",
      "Все исходные таблицы расположены рядом с этим файлом; audit.json содержит подробности."]
    (OUT.parent/"README.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

if __name__ == "__main__":
    sys.exit(run())
