"""Create only the two approved DQ200 ResponsiveAds; default is read-only.

Never edits existing ads, groups, keywords, feeds, bids or budgets. An uncertain
add is reconciled by a fresh read, never retried. Resume/delete are not used.
"""
import argparse
import copy
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CID = 716316525
GROUPS = (5826171512, 5826183946)
SHOPPING = {
    1927186476439690942: 5826171512,
    1927189770678081700: 5826183946,
    1927190920657966611: 5826320196,
}
SID = 1526787288
TITLE = "Мехатроники DSG7 DQ200 с электронной платой и без"
TEXT = "Восстановленные мехатроники. Проверены на авто. Гарантия. Доставка по РФ и СНГ"
HREF = "https://leoparts-akpp.ru/mehatronik-dq200"
BASELINE = Path("data/direct_live_shopping_inventory.json")
OUT = Path("data/direct_approved_copy_20261119.json")
SHOPPING_FIELDS = [
    "SitelinkSetId", "SitelinksModeration", "AdExtensions", "BusinessId",
    "FeedId", "GenerationScopes", "FeedFilterConditions", "FeedProcessingStatus",
    "TitleSources", "TextSources", "DefaultTexts", "ListingFeedFilterConditions",
    "ListingTitleSources", "ListingTextSources",
]
VOLATILE = {"Status", "StatusClarification", "SitelinksModeration", "FeedProcessingStatus"}


def utcnow():
    return datetime.now(timezone.utc).isoformat()


def safe_error(value):
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
    text = str(value)
    return (text.replace(token, "[redacted]") if token else text)[:1800]


def call(service, method, params):
    if method != "get" and (service, method) not in {("ads", "add"), ("ads", "moderate")}:
        raise RuntimeError("Operation outside approved scope")
    headers = {"Authorization": "Bearer " + os.environ["YANDEX_DIRECT_TOKEN"],
               "Content-Type": "application/json; charset=utf-8", "Accept-Language": "ru"}
    if os.environ.get("YANDEX_DIRECT_LOGIN"):
        headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
    req = urllib.request.Request("https://api.direct.yandex.com/json/v501/" + service,
        data=json.dumps({"method": method, "params": params}, ensure_ascii=False).encode("utf-8"),
        headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            raw = res.read()
            content_type = res.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{service}.{method} HTTP {error.code}: " +
                           safe_error(error.read().decode("utf-8", "replace"))) from error
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError(f"{service}.{method}: non-JSON {content_type}; {len(raw)} bytes") from error
    if data.get("error") or "result" not in data:
        raise RuntimeError(f"{service}.{method}: " + safe_error(json.dumps(data, ensure_ascii=False)))
    return data["result"]


def read_snapshot(api):
    def get(service, fields, key, **extra):
        data = api(service, "get", {"SelectionCriteria": {"CampaignIds": [CID]},
                   "FieldNames": fields, "Page": {"Limit": 1000}, **extra})
        if "LimitedBy" in data or key not in data:
            raise RuntimeError("Incomplete " + service + " snapshot")
        return data[key]
    # The first request verifies the live unified groups, not v5 compatibility types.
    groups = get("adgroups", ["Id", "CampaignId", "Name", "Status", "Type", "Subtype",
                 "RegionIds", "NegativeKeywords", "TrackingParams"], "AdGroups",
                 UnifiedAdGroupFieldNames=["OfferRetargeting"])
    keywords = get("keywords", ["Id", "CampaignId", "AdGroupId", "Keyword", "State", "Status"], "Keywords")
    ads = get("ads", ["Id", "CampaignId", "AdGroupId", "Type", "Status", "State", "StatusClarification"], "Ads",
              ShoppingAdFieldNames=SHOPPING_FIELDS,
              ResponsiveAdFieldNames=["Titles", "Texts", "Href", "SitelinkSetId", "SitelinksModeration"])
    return {"groups": groups, "keywords": keywords, "ads": ads}


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def by_id(rows):
    result = {x["Id"]: x for x in rows}
    if len(result) != len(rows):
        raise RuntimeError("Duplicate IDs in snapshot")
    return result


def exact(ad, gid):
    item = ad.get("ResponsiveAd", {})
    return (ad.get("CampaignId") == CID and ad.get("AdGroupId") == gid
        and ad.get("Type") == "RESPONSIVE_AD"
        and item.get("Href") == HREF and item.get("SitelinkSetId") == SID
        and [x.get("Title") for x in item.get("Titles", [])] == [TITLE]
        and [x.get("Text") for x in item.get("Texts", [])] == [TEXT])


def matching(ads, gid):
    values = [ad for ad in ads if exact(ad, gid)]
    if len(values) > 1:
        raise RuntimeError("Multiple matching ads in group " + str(gid))
    if values and values[0].get("State") in {"SUSPENDED", "ARCHIVED"}:
        raise RuntimeError("Matching ad is stopped or archived; preserve it for review")
    return values[0] if values else None


def unchanged(before, after):
    for key in ("groups", "keywords"):
        if {i: stable(v) for i, v in by_id(before[key]).items()} != {
                i: stable(v) for i, v in by_id(after[key]).items()}:
            raise RuntimeError("Existing " + key + " changed")
    old, current = by_id(before["ads"]), by_id(after["ads"])
    for aid, ad in old.items():
        if aid not in current or stable(ad) != stable(current[aid]):
            raise RuntimeError("Existing ad changed: " + str(aid))


def guard(baseline, current):
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(baseline["captured_at_utc"])).total_seconds()
    if baseline.get("campaign_id") != CID or not -60 <= age <= 3600 or baseline.get("errors"):
        raise RuntimeError("Missing, stale or incomplete reviewed inventory")
    groups = by_id(current["groups"])
    if set(groups) != set(SHOPPING.values()):
        raise RuntimeError("Unexpected group inventory")
    if any(groups[g].get("CampaignId") != CID or groups[g].get("Type") != "UNIFIED_AD_GROUP"
           or groups[g].get("Status") != "ACCEPTED" for g in GROUPS):
        raise RuntimeError("Target groups are not accepted unified groups in the approved campaign")
    if len(current["keywords"]) != 42:
        raise RuntimeError("Expected 42 reviewed keyword objects")
    for g in GROUPS:
        if not any(k["AdGroupId"] == g and k.get("State") == "ON" for k in current["keywords"]):
            raise RuntimeError("Target group has no active keyword conditions")
    ads = by_id(current["ads"])
    shops = {a["Id"]: a["AdGroupId"] for a in current["ads"] if a["Type"] == "SHOPPING_AD"}
    if shops != SHOPPING:
        raise RuntimeError("Unexpected shopping ads")
    if any(ads[i].get("State") != "ON" or ads[i].get("CampaignId") != CID
           or ads[i].get("ShoppingAd", {}).get("SitelinkSetId") != SID for i in SHOPPING):
        raise RuntimeError("Shopping ads or approved sitelinks are not live as reviewed")
    unchanged(baseline, current)
    for a in current["ads"]:
        if a["Id"] not in SHOPPING and not any(exact(a, g) for g in GROUPS):
            raise RuntimeError("Unexpected ad outside the approved copy")
    for g in GROUPS:
        matching(current["ads"], g)


def run(apply=False, api=call, baseline_path=BASELINE, output_path=OUT):
    output_path = Path(output_path)
    prior = json.loads(output_path.read_text(encoding="utf-8")) if output_path.exists() else {}
    prior_owned = set(prior.get("created_ids", [])) if (prior.get("campaign") == CID
        and prior.get("requested") == {"Titles": [TITLE], "Texts": [TEXT], "Href": HREF, "SitelinkSetId": SID}) else set()
    report = {"status": "preflight", "campaign": CID, "target_groups": list(GROUPS),
        "started_at_utc": utcnow(), "workflow_commit": os.environ.get("GITHUB_SHA"),
        "mode": "apply" if apply else "read_only", "requested": {"Titles": [TITLE], "Texts": [TEXT],
        "Href": HREF, "SitelinkSetId": SID}, "created_ids": sorted(prior_owned),
        "attempts": [], "selected_ids": {}, "moderation_attempted_ids": []}
    before = None

    def save():
        output_path.parent.mkdir(parents=True, exist_ok=True)
        temp = output_path.with_suffix(output_path.suffix + ".tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        temp.replace(output_path)

    def verify(current):
        unchanged(before, current)
        permitted = set(by_id(before["ads"])) | set(report["created_ids"])
        if set(by_id(current["ads"])) != permitted:
            raise RuntimeError("Unexpected ad inventory change during apply")

    try:
        if len(TITLE) != 49 or len(TEXT) != 78 or max(map(len, TITLE.split())) > 22 or max(map(len, TEXT.split())) > 23:
            raise RuntimeError("Approved copy or character limits changed")
        baseline = json.loads(Path(baseline_path).read_text(encoding="utf-8"))
        before = read_snapshot(api)
        report["before"] = before
        save()
        guard(baseline, before)
        prior_owned.intersection_update(by_id(before["ads"]))
        report["created_ids"] = sorted(prior_owned)
        if not apply:
            report["status"] = "preflight_verified_no_changes"
            return 0
        for gid in GROUPS:
            current = read_snapshot(api)
            verify(current)
            found = matching(current["ads"], gid)
            if found is None:
                ids_before = set(by_id(current["ads"]))
                attempt = {"group_id": gid, "ids_before": sorted(ids_before), "started_at_utc": utcnow()}
                report["attempts"].append(attempt)
                save()
                returned_id = None
                try:
                    result = api("ads", "add", {"Ads": [{"AdGroupId": gid, "ResponsiveAd": copy.deepcopy(report["requested"])}]})
                    attempt["result"] = result
                    entries = result.get("AddResults", [])
                    if len(entries) != 1 or entries[0].get("Errors") or not isinstance(entries[0].get("Id"), int):
                        raise RuntimeError("Invalid add response: " + safe_error(json.dumps(result, ensure_ascii=False)))
                    returned_id = entries[0]["Id"]
                    if returned_id in ids_before:
                        raise RuntimeError("Add returned an existing ID")
                except Exception as error:
                    attempt["uncertain_error"] = safe_error(error)
                save()
                # Always reconcile by ID difference and exact server-returned content.
                # No retry of add, even if this read fails or returns no new object.
                after_add = read_snapshot(api)
                new_ids = set(by_id(after_add["ads"])) - ids_before
                found = matching(after_add["ads"], gid)
                if (found is None or new_ids != {found["Id"]}
                        or (returned_id is not None and returned_id != found["Id"])):
                    report["after_uncertain_add"] = after_add
                    raise RuntimeError("Add outcome is not uniquely verified; do not retry automatically")
                report["created_ids"].append(found["Id"])
                attempt["verified_id"] = found["Id"]
                attempt["reconciled_after_error"] = "uncertain_error" in attempt
                report["after_last_add"] = after_add
                save()
                verify(after_add)
                if found.get("Status") != "DRAFT" or found.get("State") != "OFF":
                    raise RuntimeError("New ad is not DRAFT/OFF; preserve it for review")
            report["selected_ids"][str(gid)] = found["Id"]
            save()
        ready = read_snapshot(api)
        verify(ready)
        selected = [matching(ready["ads"], g) for g in GROUPS]
        if any(not ad or ad["Id"] != report["selected_ids"][str(g)] for g, ad in zip(GROUPS, selected)):
            raise RuntimeError("Both approved ads must be verified before moderation")
        report["before_moderation"] = ready
        report["copy_verified"] = True
        report["existing_configuration_unchanged"] = True
        draft_ids = [a["Id"] for a in selected if a["Status"] == "DRAFT" and a["Id"] in report["created_ids"]]
        save()
        if draft_ids:
            report["moderation_attempted_ids"] = draft_ids
            save()
            try:
                result = api("ads", "moderate", {"SelectionCriteria": {"Ids": draft_ids}})
                report["moderation_result"] = result
                rows = result.get("ModerateResults", [])
                if len(rows) != len(draft_ids) or {x.get("Id") for x in rows} != set(draft_ids) or any(x.get("Errors") for x in rows):
                    report["moderation_error"] = "Incomplete or rejected moderation response"
            except Exception as error:
                report["moderation_error"] = safe_error(error)
            save()
        after = read_snapshot(api)
        # Only new owned copies can change lifecycle; existing shopping ads remain immutable.
        unchanged({**before, "ads": [a for a in before["ads"] if a["Id"] not in prior_owned]}, after)
        if set(by_id(after["ads"])) != set(by_id(before["ads"])) | set(report["created_ids"]):
            raise RuntimeError("Unexpected final ad inventory")
        selected = [matching(after["ads"], g) for g in GROUPS]
        if any(not a for a in selected):
            raise RuntimeError("Approved copy missing during final verification")
        report["after"] = after
        report["actual_statuses"] = [{"Id": a["Id"], "AdGroupId": a["AdGroupId"],
            "Status": a["Status"], "State": a["State"]} for a in selected]
        if all(a["Status"] == "ACCEPTED" and a["State"] == "ON" for a in selected):
            report["status"] = "verified_accepted_eligible"
        elif all(a["Status"] in {"MODERATION", "PREACCEPTED", "ACCEPTED"} for a in selected):
            report["status"] = "verified_awaiting_moderation_or_activation"
        else:
            report["status"] = "verified_copy_moderation_requires_attention"
        report["delivery_or_impressions_verified"] = False
        return 1 if report["status"].endswith("requires_attention") else 0
    except Exception as error:
        report["status"] = "failed_preserved_for_review"
        report["error"] = safe_error(error)
        if before is not None:
            try:
                report["after_failure"] = read_snapshot(api)
            except Exception as read_error:
                report["after_failure_error"] = safe_error(read_error)
        return 1
    finally:
        report["finished_at_utc"] = utcnow()
        save()
        print(report["status"] + (": " + report["error"] if report.get("error") else ""))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="Create and moderate only the approved copies")
    sys.exit(run(apply=parser.parse_args().apply))
