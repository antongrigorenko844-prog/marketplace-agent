"""One guarded campaign-negative replacement; marker-only, four explicit modes.

preflight/check are read-only. apply accepts an exact subset of the reviewed 92
and exactly two removals. rollback_new removes only this operation's additions,
preserving the two approved removals. No uncertain write is blindly repeated.
"""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import urllib.error
import urllib.request

CID = 716316525
GROUP_IDS = {5826171512, 5826183946, 5826320196}
AD_IDS = {1927186476439690942, 1927189770678081700, 1927190920657966611,
          1927466195846975291, 1927466206584354174}
BASE = "https://api.direct.yandex.com/json/v501/"
MARKER = Path(".github/agent-requests/direct-mass-negatives-apply-20261120.txt")
SOURCE = Path("data/direct_mass_negative_proposal_final_20261119.json")
SOURCE_SHA = "8522987370ace8a6230f340884b7e68b7a873b57d481cdee77dca362c72a0727"
BASELINE = Path("data/direct_thematic_negatives_verification_20261119.json")
BEFORE_SHA = "2afda2206653bbad7842326d543c0c93bb8da2f4f140226e25fb723f7f5bb7fc"
PLAN = Path("data/direct_mass_negatives_plan_20261120.json")
PREFLIGHT = Path("data/direct_mass_negatives_preflight_20261120.json")
APPLY = Path("data/direct_mass_negatives_apply_20261120.json")
BLOCKED_APPLY = Path("data/direct_mass_negatives_apply_blocked_20261120.json")
ROLLBACK = Path("data/direct_mass_negatives_rollback_new_20261120.json")
REMOVALS = ["куда подходит мехатроник dq200", "купить дсг 7 dq200 новую"]
EXCLUDED_CANDIDATE = "мехатроника поступление"
ADDITIONAL_PROTECTED = "новое поступление мехатроников dq200"
VOLATILE = {"Status", "StatusClarification", "SitelinksModeration", "FeedProcessingStatus"}
MAX_BYTES = 2 * 1024 * 1024
CAMPAIGN_GET = {
    "SelectionCriteria": {"Ids": [CID]},
    "FieldNames": ["Id", "Name", "Type", "State", "Status", "Currency", "StartDate", "EndDate",
                   "DailyBudget", "TimeTargeting", "TimeZone", "NegativeKeywords"],
    "UnifiedCampaignFieldNames": ["CounterIds", "Settings", "BiddingStrategy", "PriorityGoals",
        "TrackingParams", "AttributionModel", "PackageBiddingStrategy", "NegativeKeywordSharedSetIds",
        "WeeklyBudgetRollover"],
    "UnifiedCampaignSearchStrategyPlacementTypesFieldNames": ["SearchResults", "ProductGallery",
        "DynamicPlaces", "Maps", "SearchOrganizationList"],
    "UnifiedCampaignPackageBiddingStrategyPlatformsFieldNames": ["SearchResult", "ProductGallery",
        "Maps", "SearchOrganizationList", "Network", "DynamicPlaces"],
    "Page": {"Limit": 1000},
}
GROUP_GET = {"SelectionCriteria": {"CampaignIds": [CID]}, "FieldNames": ["Id", "CampaignId", "Name",
    "Type", "Status", "NegativeKeywords", "NegativeKeywordSharedSetIds", "RegionIds", "TrackingParams"],
    "Page": {"Limit": 1000}}
ADS_GET = {"SelectionCriteria": {"CampaignIds": [CID]},
    "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status", "StatusClarification"],
    "ResponsiveAdFieldNames": ["Titles", "Texts", "Href", "SitelinkSetId", "SitelinksModeration"],
    "ShoppingAdFieldNames": ["SitelinkSetId", "SitelinksModeration", "AdExtensions", "BusinessId", "FeedId",
        "GenerationScopes", "FeedFilterConditions", "FeedProcessingStatus", "TitleSources", "TextSources",
        "DefaultTexts", "ListingFeedFilterConditions", "ListingTitleSources", "ListingTextSources"],
    "Page": {"Limit": 1000}}
GETS = {"campaigns": CAMPAIGN_GET, "adgroups": GROUP_GET, "ads": ADS_GET}


def now():
    return datetime.now(timezone.utc).isoformat()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def jhash(value):
    return sha(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode())


def phrase_hash(values):
    return jhash(sorted(values))


def clean(value):
    value = str(value)
    for key in ("YANDEX_DIRECT_TOKEN", "YANDEX_DIRECT_LOGIN"):
        secret = os.environ.get(key, "")
        if secret:
            value = value.replace(secret, "[redacted]")
    return value[:600]


def read_json(path):
    raw = path.read_bytes()
    if len(raw) > MAX_BYTES:
        raise RuntimeError("Local JSON exceeds size limit")
    return json.loads(raw), sha(raw)


def atomic_save(path, report):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def items(obj):
    values = [] if obj is None else obj.get("Items")
    if not isinstance(values, list):
        raise RuntimeError("Invalid Items representation")
    return values


def phrases(campaign):
    values = items(campaign.get("NegativeKeywords"))
    if any(not isinstance(x, str) for x in values) or len(values) != len(set(values)):
        raise RuntimeError("Invalid or duplicate negative phrase")
    return list(values)


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(x) for x in value]
    return value


def stable_snapshot(snapshot):
    result = stable(snapshot)
    result["campaign"].pop("NegativeKeywords", None)
    return result


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, mode):
        self.mode, self.allowed_update, self.attempted = mode, None, False

    def __call__(self, service, method, params):
        if method == "get" and service in GETS and params == GETS[service]:
            pass
        elif (service == "campaigns" and method == "update" and self.mode in {"apply", "rollback_new"}
                and self.allowed_update is not None and params == self.allowed_update and not self.attempted):
            self.attempted = True
        else:
            raise RuntimeError("API operation outside exact approved scope")
        token = os.environ.get("YANDEX_DIRECT_TOKEN")
        if not token:
            raise RuntimeError("YANDEX_DIRECT_TOKEN unavailable")
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8",
                   "Accept-Language": "en"}
        if os.environ.get("YANDEX_DIRECT_LOGIN"):
            headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
        request = urllib.request.Request(BASE + service, method="POST", headers=headers,
            data=json.dumps({"method": method, "params": params}, ensure_ascii=False).encode())
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
                raw = response.read(MAX_BYTES + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise RuntimeError(service + "." + method + " HTTP " + str(code)) from None
        except Exception:
            raise RuntimeError(service + "." + method + " transport failure") from None
        if len(raw) > MAX_BYTES:
            raise RuntimeError("API response exceeds size limit")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RuntimeError(service + "." + method + " non-JSON response") from None
        if body.get("error") or not isinstance(body.get("result"), dict):
            code = (body.get("error") or {}).get("error_code", "missing result")
            raise RuntimeError(service + "." + method + " API error " + str(code))
        return body["result"]


def campaign_read(api):
    result = api("campaigns", "get", CAMPAIGN_GET)
    rows = result.get("Campaigns", [])
    if result.get("LimitedBy") or len(rows) != 1 or rows[0].get("Id") != CID:
        raise RuntimeError("Unexpected campaign selection")
    campaign = rows[0]
    if campaign.get("Type") != "UNIFIED_CAMPAIGN" or "UnifiedCampaign" not in campaign:
        raise RuntimeError("Unexpected campaign type")
    if items(campaign["UnifiedCampaign"].get("NegativeKeywordSharedSetIds")):
        raise RuntimeError("Campaign shared negatives changed; independent review required")
    phrases(campaign)
    return campaign


def snapshot(api):
    campaign = campaign_read(api)
    groups = api("adgroups", "get", GROUP_GET)
    ads = api("ads", "get", ADS_GET)
    for body, key, ids in ((groups, "AdGroups", GROUP_IDS), (ads, "Ads", AD_IDS)):
        rows = body.get(key, [])
        if (body.get("LimitedBy") or len(rows) != len(ids) or {x.get("Id") for x in rows} != ids
                or any(x.get("CampaignId") != CID for x in rows)):
            raise RuntimeError("Campaign object inventory changed: " + key)
    for group in groups["AdGroups"]:
        if items(group.get("NegativeKeywords")) or items(group.get("NegativeKeywordSharedSetIds")):
            raise RuntimeError("Group negatives/shared sets changed; independent review required")
    return {"campaign": campaign, "groups": sorted(groups["AdGroups"], key=lambda x: x["Id"]),
            "ads": sorted(ads["Ads"], key=lambda x: x["Id"])}


def approved_sources():
    source, source_sha = read_json(SOURCE)
    baseline, _ = read_json(BASELINE)
    if source_sha != SOURCE_SHA or source.get("campaign_id") != CID or len(source.get("additions", [])) != 92:
        raise RuntimeError("Reviewed 92-phrase source changed")
    allowed = [row["phrase"] for row in source["additions"]]
    before = phrases(baseline.get("after", {}))
    if (baseline.get("status") != "verified_canonical_41" or baseline.get("campaign_id") != CID
            or len(before) != 41 or phrase_hash(before) != BEFORE_SHA or not set(REMOVALS) <= set(before)
            or len(set(allowed)) != 92):
        raise RuntimeError("Verified baseline/source identity failed")
    return source, before, allowed


def word_tokens(text):
    return re.findall(r"[a-zа-яё0-9]+", text.lower())


def validate_plan(marker, source, before, allowed):
    plan, plan_sha = read_json(PLAN)
    preflight, preflight_sha = read_json(PREFLIGHT)
    if (plan_sha != marker.get("plan_sha256") or plan.get("status") != "APPROVED_FOR_APPLY"
            or plan.get("campaign_id") != CID or plan.get("source_path") != str(SOURCE)
            or plan.get("source_sha256") != SOURCE_SHA or plan.get("preflight_path") != str(PREFLIGHT)
            or plan.get("preflight_sha256") != preflight_sha or plan.get("baseline_phrases_sha256") != BEFORE_SHA
            or preflight.get("status") != "preflight_verified_no_write" or preflight.get("campaign_id") != CID
            or preflight.get("baseline_phrases_sha256") != BEFORE_SHA
            or phrase_hash(phrases(preflight["snapshot"]["campaign"])) != BEFORE_SHA
            or plan.get("stable_snapshot_sha256") != jhash(stable_snapshot(preflight["snapshot"]))):
        raise RuntimeError("Plan/preflight hashes or approval scope failed")
    additions = plan.get("approved_additions", [])
    if (not isinstance(additions, list) or len(additions) != 91
            or any(not isinstance(x, str) for x in additions) or len(set(additions)) != len(additions)
            or set(additions) != set(allowed) - {EXCLUDED_CANDIDATE} or set(additions) & set(before)
            or Counter(plan.get("approved_removals", [])) != Counter(REMOVALS)):
        raise RuntimeError("Only an exact reviewed subset and exactly two removals are permitted")
    protected = plan.get("protected_queries", [])
    if (not isinstance(protected, list) or any(not isinstance(x, str) for x in protected)
            or not (set(source["protected_queries"]) | {ADDITIONAL_PROTECTED}) <= set(protected)):
        raise RuntimeError("Protected query set was weakened")
    for phrase in additions:
        words = word_tokens(phrase)
        if not 1 <= len(words) <= 7 or any(len(word) > 35 for word in words) or phrase.startswith("-"):
            raise RuntimeError("Negative phrase exceeds supported syntax limits")
        quoted = phrase.startswith('"') and phrase.endswith('"')
        for query in protected:
            tokens = word_tokens(query)
            if set(words) <= set(tokens) and (not quoted or len(words) == len(tokens)):
                raise RuntimeError("Literal overlap with protected query requires semantic review")
    after = [x for x in before if x not in REMOVALS] + additions
    if (plan.get("after_phrases_sha256") != phrase_hash(after)
            or sum(len(re.sub(r'[\s\-!+"\[\]]', '', x)) for x in after) > 20000):
        raise RuntimeError("Expected after hash or campaign negative length failed")
    return plan, plan_sha, preflight, after


def prior_apply_proof(prior, plan_sha, after):
    if not isinstance(prior, dict) or prior.get("mode") != "apply" or prior.get("campaign_id") != CID:
        return False
    proof = prior.get("approved_apply_write_proof", {})
    return (prior.get("plan_sha256") == plan_sha and proof.get("plan_sha256") == plan_sha
        and proof.get("write_attempted") is True and proof.get("baseline_phrases_sha256") == BEFORE_SHA
        and proof.get("expected_after_phrases_sha256") == phrase_hash(after))


def run(marker=None, api=None):
    marker = marker or read_json(MARKER)[0]
    mode = marker.get("mode")
    if mode not in {"preflight", "apply", "check", "rollback_new"}:
        raise RuntimeError("Unsupported marker mode")
    request_id = marker.get("request_id", "")
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", request_id):
        raise RuntimeError("Missing/invalid request_id")
    out = Path("data/direct_mass_negatives_" + mode + "_20261120.json")
    prior = read_json(APPLY)[0] if APPLY.exists() else None
    previous_attempt = (isinstance(prior, dict) and prior.get("mode") == "apply"
        and prior.get("campaign_id") == CID and (prior.get("write_attempted") is True
        or prior.get("approved_apply_write_proof", {}).get("write_attempted") is True))
    previous_rollback = read_json(ROLLBACK)[0] if ROLLBACK.exists() else None
    rollback_attempt = (isinstance(previous_rollback, dict) and previous_rollback.get("mode") == "rollback_new"
        and previous_rollback.get("campaign_id") == CID and (previous_rollback.get("write_attempted") is True
        or previous_rollback.get("rollback_attempt_recorded") is True))
    report = {"mode": mode, "request_id": request_id, "campaign_id": CID,
        "started_at_utc": now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
        "status": "started", "write_attempted": False, "mutation_count": 0, "errors": [],
        "previous_apply_attempt_detected": previous_attempt,
        "rollback_attempt_recorded": rollback_attempt if mode == "rollback_new" else False,
        "automatic_canonicalization_acceptance": False,
        "protected_check_limitation": "Literal token guard only; not an emulator of Direct morphology, stop words or keyword priority."}
    api = api or Client(mode)
    def save():
        atomic_save(out, report)
    try:
        # Never overwrite a successful preflight needed by an apply plan.
        if mode == "preflight" and PREFLIGHT.exists():
            old, _ = read_json(PREFLIGHT)
            if old.get("status") == "preflight_verified_no_write":
                raise RuntimeError("Verified preflight already exists; retain it for plan/provenance")
        source, before, allowed = approved_sources()
        report["baseline_phrases_sha256"] = BEFORE_SHA
        report["source_sha256"] = SOURCE_SHA
        if mode == "preflight":
            current = snapshot(api)
            report["snapshot"] = current
            if phrase_hash(phrases(current["campaign"])) != BEFORE_SHA:
                raise RuntimeError("Live campaign is not the exact verified 41-phrase baseline")
            report.update(status="preflight_verified_no_write", live_read_at_utc=now(),
                baseline_phrase_count=41, stable_snapshot_sha256=jhash(stable_snapshot(current)))
            return 0
        plan, plan_sha, preflight, after = validate_plan(marker, source, before, allowed)
        report.update(plan_sha256=plan_sha, approved_additions=plan["approved_additions"],
            approved_removals=REMOVALS, protected_queries=plan["protected_queries"],
            expected_after_phrases_sha256=phrase_hash(after), expected_after=after)
        own_prior = prior_apply_proof(prior, plan_sha, after)
        report["prior_approved_write_proof"] = own_prior
        if own_prior:
            report["approved_apply_write_proof"] = prior["approved_apply_write_proof"]
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(preflight["live_read_at_utc"])).total_seconds()
        if mode != "check" and not 0 <= age <= 86400:
            raise RuntimeError("Mutation requires a reviewed preflight less than 24 hours old")
        current = snapshot(api)
        report["before"] = current
        report["live_read_at_utc"] = now()
        if stable_snapshot(current) != stable_snapshot(preflight["snapshot"]):
            raise RuntimeError("Protected campaign/group/ad configuration changed since preflight")
        current_phrases = phrases(current["campaign"])
        current_hash = phrase_hash(current_phrases)
        rollback_target = [x for x in before if x not in REMOVALS]
        if mode == "check":
            report["after"] = current
            report["stable_fields_unchanged"] = True
            report["status"] = ("verified_expected_after" if current_hash == phrase_hash(after)
                else "verified_before_not_applied" if current_hash == BEFORE_SHA
                else "verified_new_additions_removed" if current_hash == phrase_hash(rollback_target)
                else "unexpected_live_list_requires_review")
            return 1 if report["status"] == "unexpected_live_list_requires_review" else 0
        target = after if mode == "apply" else rollback_target
        expected_start = BEFORE_SHA if mode == "apply" else phrase_hash(after)
        if mode == "rollback_new" and not own_prior:
            raise RuntimeError("Rollback requires this exact plan's prior recorded apply attempt")
        if current_hash == phrase_hash(target):
            report.update(status="already_applied_verified" if mode == "apply" else "already_rolled_back_new_verified",
                after=current, stable_fields_unchanged=True)
            return 0
        if current_hash != expected_start:
            raise RuntimeError("Fresh entire negative list differs from exact expected start")
        if mode == "apply" and (own_prior or previous_attempt):
            raise RuntimeError("Prior apply write exists; current baseline needs review, no blind repeat")
        if mode == "rollback_new" and rollback_attempt:
            raise RuntimeError("Prior rollback write exists; current after list needs review, no blind repeat")
        # One final fresh campaign read immediately before mutation; no overwrite of concurrent edits.
        prewrite = campaign_read(api)
        report["prewrite_campaign"] = prewrite
        if (phrase_hash(phrases(prewrite)) != current_hash
                or stable({k: v for k, v in prewrite.items() if k != "NegativeKeywords"})
                != stable({k: v for k, v in current["campaign"].items() if k != "NegativeKeywords"})):
            raise RuntimeError("Campaign changed between fresh snapshot and write")
        payload = {"Campaigns": [{"Id": CID, "NegativeKeywords": {"Items": target}}]}
        if isinstance(api, Client):
            api.allowed_update = payload
        report.update(write_attempted=True, mutation_count=1, payload=payload)
        if mode == "rollback_new":
            report["rollback_attempt_recorded"] = True
        if mode == "apply":
            report["approved_apply_write_proof"] = {"plan_sha256": plan_sha,
                "original_request_id": request_id, "write_attempted": True,
                "baseline_phrases_sha256": BEFORE_SHA, "expected_after_phrases_sha256": phrase_hash(after)}
        save()
        try:
            result = api("campaigns", "update", payload)
            rows = result.get("UpdateResults", [])
            # Persist only numeric error/warning codes, never provider message bodies.
            report["mutation_result"] = [{"Id": row.get("Id"),
                "error_codes": [x.get("Code") for x in row.get("Errors", [])],
                "warning_codes": [x.get("Code") for x in row.get("Warnings", [])]} for row in rows]
            if len(rows) != 1 or rows[0].get("Id") != CID or rows[0].get("Errors"):
                raise RuntimeError("Update response did not confirm the one requested campaign")
        except Exception as error:
            report["mutation_error"] = clean(error)
            save()
        observed = snapshot(api)
        report["after"] = observed
        observed_phrases = phrases(observed["campaign"])
        report["observed_phrase_count"] = len(observed_phrases)
        report["observed_phrases_sha256"] = phrase_hash(observed_phrases)
        report["stable_fields_unchanged"] = stable_snapshot(observed) == stable_snapshot(current)
        report["unexpected_missing_phrases"] = list((Counter(target) - Counter(observed_phrases)).elements())
        report["unexpected_extra_phrases"] = list((Counter(observed_phrases) - Counter(target)).elements())
        if phrase_hash(observed_phrases) == phrase_hash(target) and report["stable_fields_unchanged"]:
            report["status"] = ("applied_verified" if mode == "apply" else "rolled_back_new_verified")
            if "mutation_error" in report:
                report["status"] += "_after_uncertain_response"
            return 0
        report["status"] = ("verified_unchanged_no_retry" if phrase_hash(observed_phrases) == current_hash
            and report["stable_fields_unchanged"] else "unexpected_after_requires_review")
        return 1
    except Exception as error:
        report["errors"].append(clean(error))
        report["status"] = "write_outcome_requires_review" if report["write_attempted"] else "blocked_no_write"
        return 1
    finally:
        report["finished_at_utc"] = now()
        # Refusing a duplicate preflight must not destroy its existing proof file.
        if (mode == "apply" and previous_attempt and not report.get("approved_apply_write_proof")
                and not report["write_attempted"]):
            # A malformed marker/plan/source must never erase the preceding write
            # evidence. Keep that report intact and persist this separate outcome.
            report["prior_apply_report_preserved"] = True
            atomic_save(BLOCKED_APPLY, report)
        elif not (mode == "preflight" and PREFLIGHT.exists()
                and read_json(PREFLIGHT)[0].get("status") == "preflight_verified_no_write"):
            save()
        print("Direct mass negatives:", mode, report["status"])


if __name__ == "__main__":
    sys.exit(run())
