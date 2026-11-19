"""Two approved manual keywords and one general mechatronics group/ad.

Only the marker selects inspect/apply/check. Every write intent is committed to
the journal before the API request; an indeterminate operation is never retried
without a readback proving the requested object/settings already exist.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.error
import urllib.request

CID = 716316525
BOARD = 5826320196
SID = 1526814548
BID = 50000000
TITLE = "Мехатроник DSG7 DQ200"
TEXT = "Восстановленные мехатроники. Проверены на авто. Гарантия. Доставка по РФ и СНГ"
HREF = "https://leoparts-akpp.ru/mehatronik-dq200"
GROUP_NAME = "DQ200 | Мехатроники общий запрос | Поиск"
REGIONS = [225, -11024, 977]
BASE = "https://api.direct.yandex.com/json/v501/"
PLAN = Path("data/direct_two_keywords_plan_20261120.json")
PLAN_SHA = "dab383c9bb4f6149c0cd7a1dfd03e4b39ec3e98b17ee308204f8ed4afd007e9a"
SOURCE = Path("data/direct_keywords_live_20261120.json")
SOURCE_SHA = "774147ec0b375083b6c66ad0169875f18e0676b5a781b819cdf8fba0b18f257b"
MARKER = Path("data/direct_two_keywords_request_20261120.txt")
REPORT = Path("data/direct_two_keywords_apply_20261120.json")
JOURNAL = Path("data/direct_two_keywords_journal_20261120.json")
MAX_BYTES = 3 * 1024 * 1024
VOLATILE = {"Status", "StatusClarification", "SitelinksModeration", "FeedProcessingStatus", "ServingStatus"}
AUTO = {"Categories": {"Exact": "YES", "Narrow": "NO", "Alternative": "NO", "Accessory": "NO", "Broader": "NO"},
        "BrandOptions": {"WithoutBrands": "YES", "WithAdvertiserBrand": "YES", "WithCompetitorsBrand": "NO"}}
LINKS = [
    {"Title": TITLE, "Href": HREF + "#bez-platy", "Description": "От 29 500 ₽ при возврате своего старого мехатроника."},
    {"Title": "Мехатроники с платой", "Href": HREF + "#s-platoi", "Description": "Мехатроники DSG7 DQ200 с электронной платой"},
    {"Title": "Электронные платы DQ200", "Href": HREF + "#platy", "Description": "Платы управления мехатроника DSG7 DQ200"},
]
GROUP_PARAMS = {"AdGroups": [{"Name": GROUP_NAME, "CampaignId": CID, "RegionIds": REGIONS,
                              "UnifiedAdGroup": {"OfferRetargeting": "NO"}}]}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def read_json(path):
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise RuntimeError("Local JSON exceeds bounded size")
    return json.loads(raw), hashlib.sha256(raw).hexdigest()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write("\n")
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def clean(value):
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if k.lower() not in {"authorization", "access_token", "token", "client-login"}}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        for key in ("YANDEX_DIRECT_TOKEN", "YANDEX_DIRECT_LOGIN"):
            secret = os.environ.get(key, "")
            if secret:
                value = value.replace(secret, "[redacted]")
        return re.sub(r"(?i)(bearer|oauth)\s+\S+", r"\1 [redacted]", value)[:1500]
    return value


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def by_id(rows):
    result = {r["Id"]: r for r in rows}
    if len(result) != len(rows) or any(type(k) is not int for k in result):
        raise RuntimeError("Invalid or duplicate integer object IDs")
    return result


def items(value):
    if value is None:
        return []
    if not isinstance(value, dict) or not isinstance(value.get("Items"), list):
        raise RuntimeError("Invalid Items wrapper")
    return value["Items"]


def ad_copy(ad):
    r = ad.get("ResponsiveAd", {})
    return {"Type": ad.get("Type"), "AdGroupId": ad.get("AdGroupId"),
            "Titles": [v.get("Title") for v in r.get("Titles", [])],
            "Texts": [v.get("Text") for v in r.get("Texts", [])],
            "Href": r.get("Href"), "SitelinkSetId": r.get("SitelinkSetId")}


def expected_ad(group_id):
    return {"Type": "RESPONSIVE_AD", "AdGroupId": group_id,
            "Titles": [TITLE], "Texts": [TEXT], "Href": HREF, "SitelinkSetId": SID}


def approved_writes(gid, aid, auto):
    return {
        "group_add": ("adgroups", "add", GROUP_PARAMS),
        "auto_settings": ("keywords", "update", {"Keywords": [{"Id": auto, "AutotargetingSettings": AUTO}]}),
        "keywords_add": ("keywords", "add", {"Keywords": [{"Keyword": "мехатроник dq200", "AdGroupId": gid, "Bid": BID}, {"Keyword": "0am927769d", "AdGroupId": BOARD, "Bid": BID}]}),
        "auto_bid": ("keywordbids", "set", {"KeywordBids": [{"KeywordId": auto, "SearchBid": BID, "AutotargetingSearchBidIsAuto": "NO"}]}),
        "ad_add": ("ads", "add", {"Ads": [{"AdGroupId": gid, "ResponsiveAd": {"Titles": [TITLE], "Texts": [TEXT], "Href": HREF, "SitelinkSetId": SID}}]}),
        "ad_moderate": ("ads", "moderate", {"SelectionCriteria": {"Ids": [aid]}}),
    }


def checkpoint_git():
    """Fail closed before the API write if the intent cannot be persisted."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Live writes require the approved GitHub Actions journal checkpoint")
    def git(*args):
        try:
            p = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60, check=False)
        except Exception:
            raise RuntimeError("Durable journal checkpoint transport failed; API write not attempted") from None
        if p.returncode:
            raise RuntimeError("Durable journal checkpoint failed; API write not attempted")
        return p.stdout.strip()
    git("add", "--", str(JOURNAL), str(REPORT))
    staged = set(filter(None, git("diff", "--cached", "--name-only").splitlines()))
    if not staged <= {str(JOURNAL), str(REPORT)} or not staged:
        raise RuntimeError("Checkpoint staged-file scope failed")
    git("commit", "-m", "Checkpoint approved two-keyword operation [skip ci]")
    git("pull", "--rebase", "origin", "main")
    git("push", "origin", "HEAD:main")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, mode, plan, report, journal=None):
        self.mode, self.plan, self.report, self.journal = mode, plan, report, journal
        self.gets = {r["endpoint"][len(BASE):]: r["params"] for r in plan["preflight_read_only_requests"]}

    def wire(self, service, method, params):
        token = os.environ.get("YANDEX_DIRECT_TOKEN")
        if not token:
            raise RuntimeError("YANDEX_DIRECT_TOKEN unavailable")
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8", "Accept-Language": "en"}
        if os.environ.get("YANDEX_DIRECT_LOGIN"):
            headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
        request = urllib.request.Request(BASE + service, data=json.dumps({"method": method, "params": params}, ensure_ascii=False).encode(), headers=headers, method="POST")
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=60) as response:
                raw = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise RuntimeError(service + "." + method + " HTTP " + str(code)) from None
        except Exception:
            raise RuntimeError(service + "." + method + " transport failure") from None
        if len(raw) > MAX_BYTES:
            raise RuntimeError("API response exceeds bounded size")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RuntimeError(service + "." + method + " non-JSON response") from None
        if body.get("error") or not isinstance(body.get("result"), dict):
            error = body.get("error") or {}
            self.report.setdefault("api_errors", []).append(clean({"service": service, "method": method, "error": error}))
            raise RuntimeError(service + "." + method + " API error " + str(error.get("error_code", "missing result")))
        return body["result"]

    def get(self, service):
        if service not in self.gets:
            raise RuntimeError("Read service outside plan")
        result = self.wire(service, "get", self.gets[service])
        if result.get("LimitedBy"):
            raise RuntimeError("Unexpected paginated response; no partial inventory accepted")
        return result

    def mutate(self, key, service, method, params, before_ids=None):
        if self.mode != "apply" or self.journal is None:
            raise RuntimeError("Read-only mode cannot mutate")
        own = self.journal["own"]
        gid, aid, auto = own.get("group_id"), own.get("ad_id"), own.get("autotargeting_id")
        allowed = approved_writes(gid, aid, auto)
        if key not in allowed or (service, method, params) != allowed[key] or key in self.journal["operations"]:
            raise RuntimeError("Write outside exact scope or previously attempted operation; no blind retry")
        if key != "group_add" and type(gid) is not int:
            raise RuntimeError("Owned new group ID missing")
        if key in {"auto_settings", "auto_bid"} and type(auto) is not int:
            raise RuntimeError("Owned automatic keyword ID missing")
        if key == "ad_moderate" and type(aid) is not int:
            raise RuntimeError("Owned new ad ID missing")
        op = {"key": key, "service": service, "method": method, "params": deepcopy(params), "intent_at_utc": now(),
              "before_ids": sorted(before_ids or []), "outcome": "intent_saved_before_network"}
        self.journal["operations"][key] = op
        self.report["pending_operation"] = key
        save(JOURNAL, self.journal)
        save(REPORT, self.report)
        checkpoint_git()
        self.report["api_write_attempts_this_run"] += 1
        try:
            result = self.wire(service, method, params)
        except Exception as error:
            op.update(outcome="indeterminate_or_api_error", error=clean(str(error)), response_at_utc=now())
            save(JOURNAL, self.journal)
            raise
        op.update(outcome="response_received", result=clean(result), response_at_utc=now())
        self.report.setdefault("api_operation_results", []).append({"key": key, "result": clean(result)})
        save(JOURNAL, self.journal)
        save(REPORT, self.report)
        result_key = {"add": "AddResults", "update": "UpdateResults", "set": "SetResults", "moderate": "ModerateResults"}[method]
        rows = result.get(result_key)
        count = 2 if key == "keywords_add" else 1
        if not isinstance(rows, list) or len(rows) != count or any(r.get("Errors") for r in rows):
            raise RuntimeError(key + " returned partial/error results; inspect recorded rows")
        self.report.pop("pending_operation", None)
        return result


def snapshot(api):
    result = {}
    for service, key, output in [("campaigns", "Campaigns", "campaign"), ("adgroups", "AdGroups", "groups"),
                                 ("keywords", "Keywords", "keywords"), ("ads", "Ads", "ads"), ("sitelinks", "SitelinksSets", "sitelink_set")]:
        rows = api.get(service).get(key)
        if not isinstance(rows, list):
            raise RuntimeError("Missing result array for " + service)
        by_id(rows)
        if output in {"campaign", "sitelink_set"}:
            expected = CID if output == "campaign" else SID
            if len(rows) != 1 or rows[0]["Id"] != expected:
                raise RuntimeError("Unexpected singleton selection: " + service)
            result[output] = rows[0]
        else:
            if any(x.get("CampaignId") != CID for x in rows):
                raise RuntimeError("Object from another campaign returned")
            result[output] = sorted(rows, key=lambda x: x["Id"])
    return result


def initial_guard(snap, plan, source):
    campaign = snap["campaign"]
    if stable(campaign) != stable(plan["expected_baseline"]["campaign_preserved_fields"]):
        raise RuntimeError("Campaign fields differ from approved baseline")
    if campaign.get("State") != "ON" or campaign.get("Type") != "UNIFIED_CAMPAIGN":
        raise RuntimeError("Campaign state/type changed")
    if len(items(campaign.get("NegativeKeywords"))) != 130:
        raise RuntimeError("Expected 130 campaign negatives")
    for key, src_key in [("groups", "groups"), ("ads", "ads"), ("keywords", "autotargeting_keywords")]:
        current, previous = by_id(snap[key]), by_id(source[src_key])
        if set(current) != set(previous):
            raise RuntimeError("Initial object IDs changed: " + key)
        for ident, old in previous.items():
            # Only fields actually requested by the verified read-only schema.
            expected = {k: v for k, v in old.items() if k in current[ident]}
            actual = {k: current[ident][k] for k in expected}
            if stable(actual) != stable(expected):
                raise RuntimeError("Initial existing object changed: " + key + "/" + str(ident))
    manual = [k for k in snap["keywords"] if k["Keyword"] != "---autotargeting"]
    if (len(manual), len(snap["keywords"]) - len(manual), len(snap["groups"]), len(snap["ads"])) != (39, 3, 3, 5):
        raise RuntimeError("Expected baseline 39 manual / 3 auto / 3 groups / 5 ads")
    if any(k["Bid"] != BID for k in snap["keywords"]):
        raise RuntimeError("Existing 50 RUB bids changed")
    if any(k["Keyword"].strip().casefold() in {"мехатроник dq200", "0am927769d"} for k in manual):
        raise RuntimeError("Approved bare keyword already exists; independent duplicate review required")
    if snap["sitelink_set"] != {"Id": SID, "Sitelinks": LINKS}:
        raise RuntimeError("Current approved sitelinks changed")


def valid_group(group):
    return (group.get("CampaignId") == CID and group.get("Type") == "UNIFIED_AD_GROUP"
            and group.get("Name") == GROUP_NAME and group.get("RegionIds") == REGIONS
            and not items(group.get("NegativeKeywords")) and not items(group.get("NegativeKeywordSharedSetIds"))
            and group.get("TrackingParams") in (None, ""))


def reconcile_guard(api, snap):
    """Reconcile only exact new objects after an already journaled write intent."""
    j, baseline = api.journal, api.journal["baseline"]
    own, ops = j["own"], j["operations"]
    if stable(snap["campaign"]) != stable(baseline["campaign"]):
        raise RuntimeError("Campaign budget/strategy/negatives/settings changed")
    if snap["sitelink_set"] != baseline["sitelink_set"]:
        raise RuntimeError("Approved sitelink set changed")
    extras = {}
    for key in ("groups", "keywords", "ads"):
        old, current = by_id(baseline[key]), by_id(snap[key])
        for ident, item in old.items():
            if ident not in current or stable(item) != stable(current[ident]):
                raise RuntimeError("Existing object changed: " + key + "/" + str(ident))
        extras[key] = {i: current[i] for i in set(current) - set(old)}
    if own.get("group_id") is None and "group_add" in ops and extras["groups"]:
        if len(extras["groups"]) != 1 or not valid_group(next(iter(extras["groups"].values()))):
            raise RuntimeError("Unknown/ambiguous group after add intent")
        own["group_id"] = next(iter(extras["groups"]))
    gid = own.get("group_id")
    if set(extras["groups"]) != ({gid} if gid else set()) or (gid and not valid_group(extras["groups"][gid])):
        raise RuntimeError("Unknown group delta")
    allowed_keywords = set()
    if gid:
        autos = [k for k in extras["keywords"].values() if k.get("AdGroupId") == gid and k.get("Keyword") == "---autotargeting"]
        if len(autos) != 1:
            raise RuntimeError("New group must have exactly one automatically created autotargeting")
        auto = autos[0]["Id"]
        if own.get("autotargeting_id") not in (None, auto):
            raise RuntimeError("Owned autotargeting ID changed")
        own["autotargeting_id"] = auto
        allowed_keywords.add(auto)
    if "keywords_add" in ops:
        for field, phrase, group_id in [("generic_keyword_id", "мехатроник dq200", gid), ("board_keyword_id", "0am927769d", BOARD)]:
            matched = [k for k in extras["keywords"].values() if k.get("Keyword") == phrase and k.get("AdGroupId") == group_id]
            if len(matched) > 1:
                raise RuntimeError("Ambiguous duplicate after keyword add")
            if matched:
                keyword = matched[0]
                if keyword.get("Bid") != BID or own.get(field) not in (None, keyword["Id"]):
                    raise RuntimeError("New keyword bid/ID differs")
                own[field] = keyword["Id"]
                allowed_keywords.add(keyword["Id"])
            elif own.get(field):
                raise RuntimeError("Previously verified new keyword disappeared")
    if set(extras["keywords"]) != allowed_keywords:
        raise RuntimeError("Unknown keyword delta outside two phrases and mandatory new auto")
    if own.get("ad_id") is None and "ad_add" in ops and extras["ads"]:
        if len(extras["ads"]) != 1 or ad_copy(next(iter(extras["ads"].values()))) != expected_ad(gid):
            raise RuntimeError("Unknown/ambiguous ad after add intent")
        own["ad_id"] = next(iter(extras["ads"]))
    aid = own.get("ad_id")
    if set(extras["ads"]) != ({aid} if aid else set()) or (aid and ad_copy(extras["ads"][aid]) != expected_ad(gid)):
        raise RuntimeError("Unknown ad delta")
    for key, proven in [("group_add", bool(gid)), ("keywords_add", bool(own.get("generic_keyword_id") and own.get("board_keyword_id"))), ("ad_add", bool(aid))]:
        if key in ops and proven:
            ops[key]["readback_verified_at_utc"] = now()
    j["last_readback_at_utc"] = now()
    save(JOURNAL, j)
    return snap


def refresh(api):
    return reconcile_guard(api, snapshot(api))


def progress(api, snap):
    own = api.journal["own"]
    keys, ads = by_id(snap["keywords"]), by_id(snap["ads"])
    auto = keys.get(own.get("autotargeting_id"), {})
    ad = ads.get(own.get("ad_id"), {})
    r = ad.get("ResponsiveAd", {})
    manual = [keys.get(own.get(name), {}) for name in ("generic_keyword_id", "board_keyword_id")]
    saved = (all(own.get(k) for k in ("group_id", "autotargeting_id", "generic_keyword_id", "board_keyword_id", "ad_id"))
             and auto.get("AutotargetingSettings") == AUTO and auto.get("Bid") == BID
             and auto.get("AutotargetingSearchBidIsAuto") == "NO" and ad_copy(ad) == expected_ad(own.get("group_id")))
    moderation = {"new_ad": {k: ad.get(k) for k in ("State", "Status", "StatusClarification")},
                  "titles": [v.get("Status") for v in r.get("Titles", [])], "texts": [v.get("Status") for v in r.get("Texts", [])],
                  "sitelinks": r.get("SitelinksModeration", {}).get("Status"),
                  "manual_keywords": [{"Id": k.get("Id"), "Keyword": k.get("Keyword"), "State": k.get("State"), "Status": k.get("Status")} for k in manual],
                  "autotargeting": {k: auto.get(k) for k in ("Id", "State", "Status")}}
    rejected = (ad.get("Status") == "REJECTED" or any("REJECTED" in moderation[k] for k in ("titles", "texts"))
                or moderation["sitelinks"] == "REJECTED" or any(k.get("Status") == "REJECTED" for k in manual)
                or auto.get("Status") == "REJECTED")
    stopped = [{"Id": obj.get("Id"), "State": obj.get("State")} for obj in [ad, auto, *manual]
               if obj.get("State") in {"SUSPENDED", "ARCHIVED", "DELETED"}]
    active = (saved and ad.get("State") == "ON" and ad.get("Status") == "ACCEPTED"
              and moderation["titles"] == ["ACCEPTED"] and moderation["texts"] == ["ACCEPTED"]
              and moderation["sitelinks"] == "ACCEPTED" and all(k.get("State") == "ON" and k.get("Status") == "ACCEPTED" for k in manual)
              and auto.get("State") == "ON" and auto.get("Status") == "ACCEPTED")
    return {"owned_ids": deepcopy(own), "exact_requested_objects_and_settings_saved": bool(saved),
            "all_new_objects_active_accepted": bool(active), "rejected_requires_review": rejected,
            "stopped_new_objects_require_review": stopped, "moderation": moderation,
            "counts": {"groups": len(snap["groups"]), "manual_keywords": sum(k["Keyword"] != "---autotargeting" for k in snap["keywords"]),
                       "autotargeting": sum(k["Keyword"] == "---autotargeting" for k in snap["keywords"]), "ads": len(snap["ads"]), "campaign_negatives": len(items(snap["campaign"].get("NegativeKeywords")))},
            "existing_objects_unchanged": True, "campaign_budget_strategy_and_130_negatives_unchanged": True,
            "note": "Автообъект обязателен для новой поисковой группы; это не третий ручной ключ. Saved не означает, что модерация завершена или уже были показы."}


def apply(api, snap):
    own = api.journal["own"]
    if not own.get("group_id"):
        api.mutate("group_add", "adgroups", "add", GROUP_PARAMS, by_id(snap["groups"]))
        snap = refresh(api)
    auto = by_id(snap["keywords"])[own["autotargeting_id"]]
    if auto.get("AutotargetingSettings") != AUTO:
        api.mutate("auto_settings", "keywords", "update", {"Keywords": [{"Id": auto["Id"], "AutotargetingSettings": AUTO}]})
        snap = refresh(api)
    if by_id(snap["keywords"])[own["autotargeting_id"]].get("AutotargetingSettings") != AUTO:
        raise RuntimeError("Narrow new-autotargeting settings not saved; no widening or ad moderation")
    if not (own.get("generic_keyword_id") and own.get("board_keyword_id")):
        api.mutate("keywords_add", "keywords", "add", {"Keywords": [{"Keyword": "мехатроник dq200", "AdGroupId": own["group_id"], "Bid": BID},
                                                                    {"Keyword": "0am927769d", "AdGroupId": BOARD, "Bid": BID}]}, by_id(snap["keywords"]))
        snap = refresh(api)
    auto = by_id(snap["keywords"])[own["autotargeting_id"]]
    if auto.get("Bid") != BID or auto.get("AutotargetingSearchBidIsAuto") != "NO":
        api.mutate("auto_bid", "keywordbids", "set", {"KeywordBids": [{"KeywordId": auto["Id"], "SearchBid": BID, "AutotargetingSearchBidIsAuto": "NO"}]})
        snap = refresh(api)
    if not own.get("ad_id"):
        api.mutate("ad_add", "ads", "add", {"Ads": [{"AdGroupId": own["group_id"], "ResponsiveAd": {"Titles": [TITLE], "Texts": [TEXT], "Href": HREF, "SitelinkSetId": SID}}]}, by_id(snap["ads"]))
        snap = refresh(api)
    status = progress(api, snap)
    if not status["exact_requested_objects_and_settings_saved"]:
        raise RuntimeError("Pre-moderation exact readback failed")
    if status["rejected_requires_review"] or status["stopped_new_objects_require_review"]:
        raise RuntimeError("New object rejected/stopped; no automatic moderate or resume")
    ad = by_id(snap["ads"])[own["ad_id"]]
    if ad.get("Status") == "DRAFT":
        api.mutate("ad_moderate", "ads", "moderate", {"SelectionCriteria": {"Ids": [ad["Id"]]}})
        snap = refresh(api)
    return snap


def validate_inputs(marker, plan, plan_sha, source, source_sha, journal):
    if (set(marker) != {"mode", "request_id", "plan_sha256"} or marker["mode"] not in {"inspect", "apply", "check"}
            or not isinstance(marker["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", marker["request_id"])
            or marker["plan_sha256"] != PLAN_SHA or plan_sha != PLAN_SHA or source_sha != SOURCE_SHA
            or plan.get("campaign_id") != CID or source.get("campaign_id") != CID):
        raise RuntimeError("Marker/plan/source identity failed")
    if {r["endpoint"] for r in plan["preflight_read_only_requests"]} != {BASE + s for s in ("campaigns", "adgroups", "keywords", "ads", "sitelinks")}:
        raise RuntimeError("Plan read endpoint scope failed")
    if any(r["method"] != "get" for r in plan["preflight_read_only_requests"]):
        raise RuntimeError("Preflight contains a mutation")
    template_writes = approved_writes("__NEW_GROUP_ID_FROM_OWN_ADD_RESULT__", "__NEW_AD_ID_FROM_OWN_ADD_RESULT__",
                                      "__NEW_GROUP_AUTOTARGET_ID_FROM_FRESH_GET__")
    for key, index in [("group_add", 0), ("auto_settings", 2), ("keywords_add", 3),
                       ("auto_bid", 4), ("ad_add", 5), ("ad_moderate", 6)]:
        request = plan["implementation_steps"][index]["request"]
        actual = (request["endpoint"][len(BASE):], request["method"], request["params"])
        if actual != template_writes[key]:
            raise RuntimeError("Plan payload differs from exact implemented operation: " + key)
    if journal is not None:
        if (journal.get("version") != 1 or journal.get("plan_sha256") != PLAN_SHA or journal.get("campaign_id") != CID
                or digest(journal.get("baseline")) != journal.get("baseline_sha256") or not isinstance(journal.get("operations"), dict)
                or not isinstance(journal.get("own"), dict)):
            raise RuntimeError("Existing durable journal identity/integrity failed; it is not overwritten")


def main():
    report = {"campaign_id": CID, "started_at_utc": now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
              "status": "started", "api_write_attempts_this_run": 0, "errors": [], "plan_sha256": PLAN_SHA}
    api = None
    try:
        # Load prior proof before any validation; no finally block rewrites it.
        journal = read_json(JOURNAL)[0] if JOURNAL.exists() else None
        marker, _ = read_json(MARKER)
        plan, plan_sha = read_json(PLAN)
        source, source_sha = read_json(SOURCE)
        validate_inputs(marker, plan, plan_sha, source, source_sha, journal)
        report.update(mode=marker["mode"], request_id=marker["request_id"])
        api = Client(marker["mode"], plan, report, journal)
        snap = snapshot(api)
        if journal is None:
            initial_guard(snap, plan, source)
            if marker["mode"] != "inspect":
                raise RuntimeError("Fresh inspect journal required before first apply/check")
            journal = {"version": 1, "campaign_id": CID, "plan_sha256": PLAN_SHA, "created_at_utc": now(),
                       "inspect_request_id": marker["request_id"], "baseline": snap, "baseline_sha256": digest(snap),
                       "source_sha256": source_sha, "own": {}, "operations": {}}
            api.journal = journal
            save(JOURNAL, journal)
        snap = reconcile_guard(api, snap)
        report["before"] = snap
        report["baseline_sha256"] = api.journal["baseline_sha256"]
        save(REPORT, report)
        if marker["mode"] == "apply":
            snap = apply(api, snap)
        report["after"] = snap
        report["verification"] = progress(api, snap)
        v = report["verification"]
        if v["rejected_requires_review"]:
            report["status"] = "saved_but_rejected_requires_review"
        elif v["stopped_new_objects_require_review"]:
            report["status"] = "saved_but_stopped_requires_review"
        elif v["all_new_objects_active_accepted"]:
            report["status"] = "verified_active_accepted"
        elif v["exact_requested_objects_and_settings_saved"]:
            report["status"] = "saved_waiting_moderation_or_activation"
        elif marker["mode"] == "inspect" and not api.journal["operations"]:
            report["status"] = "inspect_verified_no_api_write"
        else:
            report["status"] = "partial_or_indeterminate_requires_review"
        code = 1 if report["status"] in {"saved_but_rejected_requires_review", "saved_but_stopped_requires_review", "partial_or_indeterminate_requires_review"} else 0
    except Exception as error:
        report["errors"].append(clean(str(error)))
        report["status"] = "blocked_or_partial_no_blind_retry"
        code = 1
        if api is not None and api.journal is not None and api.journal.get("operations"):
            try:
                snap = refresh(api)
                report["error_readback"] = snap
                report["verification"] = progress(api, snap)
            except Exception as read_error:
                report["error_readback_error"] = clean(str(read_error))
    finally:
        report["finished_at_utc"] = now()
        if api is not None and api.journal is not None:
            report["journal_operation_results"] = deepcopy(api.journal.get("operations", {}))
        save(REPORT, clean(report))
    print(json.dumps({"status": report["status"], "api_write_attempts_this_run": report["api_write_attempts_this_run"], "errors": report["errors"]}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
