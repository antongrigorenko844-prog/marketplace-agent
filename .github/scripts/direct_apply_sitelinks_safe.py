"""Apply the three user-approved DQ200 sitelinks and verify every change."""
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CID = 716316525
EXPECTED = {
    1927186476439690942: 5826171512,
    1927189770678081700: 5826183946,
    1927190920657966611: 5826320196,
}
BASE = "https://leoparts-akpp.ru/mehatronik-dq200"
LINKS = [
    {"Title": "Мехатроники без платы", "Href": BASE + "#bez-platy",
     "Description": "Мехатроники DSG7 DQ200 без электронной платы"},
    {"Title": "Мехатроники с платой", "Href": BASE + "#s-platoi",
     "Description": "Мехатроники DSG7 DQ200 с электронной платой"},
    {"Title": "Электронные платы DQ200", "Href": BASE + "#platy",
     "Description": "Платы управления мехатроника DSG7 DQ200"},
]
FIELDS = [
    "SitelinkSetId", "SitelinksModeration", "AdExtensions", "BusinessId",
    "FeedId", "GenerationScopes", "FeedFilterConditions", "FeedProcessingStatus",
    "TitleSources", "TextSources", "DefaultTexts", "ListingFeedFilterConditions",
    "ListingTitleSources", "ListingTextSources",
]
OUT = Path("data/direct_sitelinks_apply_20261119.json")
TOKEN = os.environ["YANDEX_DIRECT_TOKEN"]
HEAD = {"Authorization": "Bearer " + TOKEN,
        "Content-Type": "application/json; charset=utf-8", "Accept-Language": "ru"}
if os.environ.get("YANDEX_DIRECT_LOGIN"):
    HEAD["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
report = {
    "status": "preflight", "campaign": CID,
    "started_at_utc": datetime.now(timezone.utc).isoformat(),
    "workflow_commit": os.environ.get("GITHUB_SHA"),
    "requested_links": LINKS, "attempted": [], "changed": [],
}
before = []
target_sid = None

def save():
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

def clean_error(value):
    return str(value).replace(TOKEN, "[redacted]")[:1800]

def call(service, method, params):
    req = urllib.request.Request(
        "https://api.direct.yandex.com/json/v501/" + service,
        data=json.dumps({"method": method, "params": params}, ensure_ascii=False).encode("utf-8"),
        headers=HEAD,
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            raw = response.read()
            content_type = response.headers.get("Content-Type", "")
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"{service}.{method} HTTP {error.code}: " +
                           clean_error(error.read().decode("utf-8", "replace"))) from error
    try:
        data = json.loads(raw)
    except (ValueError, UnicodeDecodeError) as error:
        raise RuntimeError(f"{service}.{method}: non-JSON response; {content_type}; {len(raw)} bytes") from error
    if data.get("error"):
        raise RuntimeError(f"{service}.{method}: " + clean_error(json.dumps(data["error"], ensure_ascii=False)))
    if "result" not in data:
        raise RuntimeError(f"{service}.{method}: missing result")
    return data["result"]

def snapshot():
    result = call("ads", "get", {
        "SelectionCriteria": {"CampaignIds": [CID]},
        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "Status", "State", "StatusClarification"],
        "ShoppingAdFieldNames": FIELDS,
        "Page": {"Limit": 1000},
    })
    if "LimitedBy" in result:
        raise RuntimeError("Unexpectedly paginated ad inventory")
    return result["Ads"]

def check_result(result, key, expected_id=None):
    entries = result.get(key, [])
    if len(entries) != 1 or entries[0].get("Errors") or not entries[0].get("Id"):
        raise RuntimeError(clean_error(json.dumps(result, ensure_ascii=False)))
    value = entries[0]["Id"]
    if expected_id is not None and value != expected_id:
        raise RuntimeError("Unexpected ID in mutation response")
    return value

def normalized_links(items):
    return [{k: item.get(k) for k in ("Title", "Href", "Description")} for item in items]

def read_set(sid):
    result = call("sitelinks", "get", {
        "SelectionCriteria": {"Ids": [sid]}, "FieldNames": ["Id", "Sitelinks"],
    })
    values = result.get("SitelinksSets", [])
    if len(values) != 1 or values[0]["Id"] != sid:
        raise RuntimeError("Sitelink set could not be verified")
    return values[0]

def stable_ad(ad):
    shopping = {k: v for k, v in ad["ShoppingAd"].items()
                if k not in ("SitelinkSetId", "SitelinksModeration", "FeedProcessingStatus")}
    return {"Id": ad["Id"], "CampaignId": ad["CampaignId"],
            "AdGroupId": ad["AdGroupId"], "Type": ad["Type"],
            "State": ad["State"], "ShoppingAd": shopping}

def fresh(timestamp, max_seconds=3600):
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(timestamp)).total_seconds()
    return -60 <= age <= max_seconds

try:
    if not all(len(x["Title"]) <= 30 and len(x["Description"]) <= 60 for x in LINKS):
        raise RuntimeError("Sitelink character limit")
    review = json.loads(Path("data/direct_landing_anchor_review_20261119.json").read_text(encoding="utf-8"))
    if (review["url"] != BASE or not fresh(review["checked_at_utc"])
            or set(review["anchor_targets"]) != {"bez-platy", "s-platoi", "platy"}
            or not all(x["exists"] for x in review["anchor_targets"].values())):
        raise RuntimeError("Missing or stale independently verified landing anchors")
    baseline = json.loads(Path("data/direct_live_shopping_inventory.json").read_text(encoding="utf-8"))
    if not fresh(baseline["captured_at_utc"]):
        raise RuntimeError("Read-only baseline is stale")
    before = snapshot()
    if ({x["Id"]: x["AdGroupId"] for x in before} != EXPECTED or len(before) != 3
            or any(x["Type"] != "SHOPPING_AD" or x["State"] != "ON" for x in before)):
        raise RuntimeError("Live inventory differs from the approved three active shopping ads")
    baseline_ads = {x["Id"]: x for x in baseline["ads"]}
    if any(stable_ad(x) != stable_ad(baseline_ads[x["Id"]]) for x in before):
        raise RuntimeError("An ad changed since read-only review; do not overwrite")
    report["before"] = before
    report["landing_review"] = review
    save()
    existing = {x["ShoppingAd"].get("SitelinkSetId") for x in before} - {None}
    for sid in existing:
        current_set = read_set(sid)
        if normalized_links(current_set["Sitelinks"]) != LINKS:
            raise RuntimeError("Unexpected existing sitelinks; preserve for separate review")
    if existing:
        target_sid = sorted(existing)[0]
    else:
        target_sid = check_result(call("sitelinks", "add", {
            "SitelinksSets": [{"Sitelinks": LINKS}],
        }), "AddResults")
    report["sitelinks_set_id"] = target_sid
    report["sitelinks_set"] = read_set(target_sid)
    if normalized_links(report["sitelinks_set"]["Sitelinks"]) != LINKS:
        raise RuntimeError("Created sitelink content differs")
    save()
    for ad in before:
        if ad["ShoppingAd"].get("SitelinkSetId") == target_sid:
            continue
        report["attempted"].append(ad["Id"])
        save()
        result = call("ads", "update", {
            "Ads": [{"Id": ad["Id"], "ShoppingAd": {"SitelinkSetId": target_sid}}],
        })
        check_result(result, "UpdateResults", ad["Id"])
        report["changed"].append(ad["Id"])
        save()
    after = snapshot()
    after_by_id = {x["Id"]: x for x in after}
    if set(after_by_id) != set(EXPECTED):
        raise RuntimeError("Ad inventory changed during verification")
    for old in before:
        current = after_by_id[old["Id"]]
        if (current["ShoppingAd"].get("SitelinkSetId") != target_sid
                or stable_ad(current) != stable_ad(old)):
            raise RuntimeError("Post-apply invariants failed")
    report["after"] = after
    report["other_ad_settings_unchanged"] = True
    report["links_verified"] = True
    report["status"] = "verified"
    save()
    drafts = [x["Id"] for x in after if x["Status"] == "DRAFT"]
    if drafts:
        result = call("ads", "moderate", {"SelectionCriteria": {"Ids": drafts}})
        report["moderation_result"] = result
        if any(x.get("Errors") for x in result.get("ModerateResults", [])):
            report["status"] = "verified_moderation_requires_attention"
        report["after"] = snapshot()
        save()
except Exception as error:
    report["error"] = clean_error(error)
    # Re-read after uncertain writes; restore only our sitelink assignment.
    if report["attempted"] and target_sid is not None and not report.get("links_verified"):
        rollback = []
        try:
            current_by_id = {x["Id"]: x for x in snapshot()}
            originals = {x["Id"]: x for x in before}
            for aid in reversed(report["attempted"]):
                if current_by_id.get(aid, {}).get("ShoppingAd", {}).get("SitelinkSetId") != target_sid:
                    continue
                original_sid = originals[aid]["ShoppingAd"].get("SitelinkSetId")
                result = call("ads", "update", {
                    "Ads": [{"Id": aid, "ShoppingAd": {"SitelinkSetId": original_sid}}],
                })
                check_result(result, "UpdateResults", aid)
                rollback.append({"ad_id": aid, "restored_sitelinks_set_id": original_sid})
            restored = {x["Id"]: x for x in snapshot()}
            report["rollback_verified"] = all(
                restored[aid]["ShoppingAd"].get("SitelinkSetId")
                == originals[aid]["ShoppingAd"].get("SitelinkSetId")
                for aid in report["attempted"]
            )
            report["after_rollback"] = list(restored.values())
        except Exception as rollback_error:
            report["rollback_error"] = clean_error(rollback_error)
        report["rollback"] = rollback
    if report.get("links_verified"):
        report["status"] = "verified_moderation_requires_attention"
    else:
        report["status"] = "failed"
    save()
    print(report["status"] + ": " + report["error"])
    sys.exit(1)
report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
save()
print("VERIFIED sitelinks:", len(before), "ads; changed:", len(report["changed"]))
