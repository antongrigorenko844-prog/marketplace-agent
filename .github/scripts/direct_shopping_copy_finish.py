"""Guarded completion of two approved DQ200 ShoppingAd texts.

Marker modes: preflight (read), refresh (one bounded feed-URL update), apply
(two bounded ad-copy updates, moderation if required), check (read).
apply_default_text/check_default_text use only TitleSources and DefaultTexts,
preserving TextSources=null; they do not force a literal rendered ad text.
Never edits feed files, boards, ResponsiveAds, bids, budgets or sitelinks.
An uncertain write is always read back and never blindly retried.
"""
import copy
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

CID, FID = 716316525, 4061297
TARGETS = {1927186476439690942: 5826171512, 1927189770678081700: 5826183946}
BOARD_AD = 1927190920657966611
ALL_ADS = {**TARGETS, BOARD_AD: 5826320196}
OFFER_GROUPS = {
    1927186476439690942: {"0am325025h", "0am325025x"},
    1927189770678081700: {"0am325025hx", "0am325025hxf", "0am325025hxe", "0am325025hxg", "0am325025hxk"},
    BOARD_AD: {"0am927769d", "0am927769f", "0am927769e", "0am927769g", "0am927769k"},
}
MECH_IDS = set().union(*(OFFER_GROUPS[x] for x in TARGETS))
TITLE = "Мехатроник DSG7 DQ200 0AM325025"
TEXT = "Восстановленные мехатроники. Проверены на авто. Гарантия. Доставка по РФ и СНГ"
DESIRED = {"TitleSources": {"Items": ["name"]}, "TextSources": {"Items": ["description"]}, "DefaultTexts": [TEXT]}
DEFAULT_DESIRED = {"TitleSources": {"Items": ["name"]}, "DefaultTexts": [TEXT]}
FEED_URL = "https://raw.githubusercontent.com/antongrigorenko844-prog/marketplace-agent/main/feeds/dq200_candidate.yml"
REFRESH_URL = FEED_URL + "?copy_rev=20261119_01"
API_BASE = "https://api.direct.yandex.com/json/v501/"
MARKER = Path(".github/agent-requests/direct-shopping-copy-finish.txt")
PLAN = Path("data/direct_shopping_copy_feed_plan_20261119.json")
BEFORE_XML = Path("data/dq200_shopping_copy_before_20261119.xml")
AFTER_XML = Path("data/dq200_shopping_copy_after_20261119.xml")
ACTIVE_XML = Path("feeds/dq200_candidate.yml")
PREFLIGHT = Path("data/direct_shopping_copy_preflight_20261119.json")
SHOP_FIELDS = ["SitelinkSetId", "SitelinksModeration", "AdExtensions", "BusinessId", "FeedId", "GenerationScopes",
               "FeedFilterConditions", "FeedProcessingStatus", "TitleSources", "TextSources", "DefaultTexts",
               "ListingFeedFilterConditions", "ListingTitleSources", "ListingTextSources"]
VOLATILE = {"Status", "StatusClarification", "SitelinksModeration", "FeedProcessingStatus"}


def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    text = str(value)
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
    return (text.replace(token, "[redacted]") if token else text)[:1200]


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class Client:
    def __init__(self, mode):
        self.mode = mode

    def __call__(self, service, method, params):
        if method == "get":
            if service not in {"ads", "feeds"}:
                raise RuntimeError("Read service outside scope")
            wanted = set(ALL_ADS) if service == "ads" else {FID}
            if set(params.get("SelectionCriteria", {}).get("Ids", [])) != wanted:
                raise RuntimeError("Read selection outside approved objects")
        elif service == "feeds" and method == "update" and self.mode == "refresh":
            if params != {"Feeds": [{"Id": FID, "UrlFeed": {"Url": REFRESH_URL}}]}:
                raise RuntimeError("Feed update outside exact approved URL change")
        elif service == "ads" and method == "update" and self.mode in {"apply", "apply_default_text"}:
            rows = params.get("Ads", [])
            patch = DEFAULT_DESIRED if self.mode == "apply_default_text" else DESIRED
            if (set(params) != {"Ads"} or len(rows) != 1 or rows[0].get("Id") not in TARGETS
                    or rows[0] != {"Id": rows[0]["Id"], "ShoppingAd": patch}):
                raise RuntimeError("Ad update outside two approved copy patches")
        elif service == "ads" and method == "moderate" and self.mode in {"apply", "apply_default_text"}:
            ids = params.get("SelectionCriteria", {}).get("Ids", [])
            if (not ids or len(ids) != len(set(ids)) or not set(ids) <= set(TARGETS)
                    or params != {"SelectionCriteria": {"Ids": ids}}):
                raise RuntimeError("Moderation outside approved two ads")
        else:
            raise RuntimeError("Operation outside approved mode")
        token = os.environ.get("YANDEX_DIRECT_TOKEN")
        if not token:
            raise RuntimeError("YANDEX_DIRECT_TOKEN unavailable")
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8", "Accept-Language": "en"}
        if os.environ.get("YANDEX_DIRECT_LOGIN"):
            headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
        req = urllib.request.Request(API_BASE + service, method="POST", headers=headers,
            data=json.dumps({"method": method, "params": params}, ensure_ascii=False).encode("utf-8"))
        try:
            with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise RuntimeError(service + "." + method + " HTTP " + str(code)) from None
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("API response exceeded bound")
        body = json.loads(raw)
        if body.get("error") or not isinstance(body.get("result"), dict):
            raise RuntimeError(service + "." + method + " API error " + str((body.get("error") or {}).get("error_code", "missing result")))
        return body["result"]


def parse_xml(raw):
    if len(raw) > 2 * 1024 * 1024 or b"\x00" in raw or b"<!ENTITY" in raw.upper():
        raise RuntimeError("Unsafe or oversized XML")
    root = ET.fromstring(raw)
    if root.tag != "yml_catalog":
        raise RuntimeError("Unexpected feed root")
    return root


def model(node, root=True):
    return [node.tag, sorted((k, v) for k, v in node.attrib.items() if not (root and k == "date")),
            (node.text or "").strip(), [model(child, False) for child in node]]


def digest(root):
    return hashlib.sha256(json.dumps(model(root), ensure_ascii=False, separators=(",", ":")).encode()).hexdigest()


def offers(root):
    rows = root.findall("./shop/offers/offer")
    result = {row.get("id"): row for row in rows}
    if len(rows) != 12 or len(result) != 12 or set(result) != set().union(*OFFER_GROUPS.values()):
        raise RuntimeError("Expected exactly the reviewed twelve offers")
    for oid, row in result.items():
        if row.findtext("vendorCode") != oid.upper():
            raise RuntimeError("Unexpected vendorCode for reviewed offer")
    return result


def validate_plan():
    plan = json.loads(PLAN.read_text(encoding="utf-8"))
    before_raw, after_raw = BEFORE_XML.read_bytes(), AFTER_XML.read_bytes()
    before, after = parse_xml(before_raw), parse_xml(after_raw)
    old, new = offers(before), offers(after)
    blob = hashlib.sha1(b"blob " + str(len(before_raw)).encode() + b"\0" + before_raw).hexdigest()
    if (plan.get("campaign_id") != CID or plan.get("feed_id") != FID or plan.get("title") != TITLE
            or plan.get("text") != TEXT or set(plan.get("expected_target_ads", [])) != set(TARGETS)
            or plan.get("source_blob_sha") != blob or len(plan.get("changes", [])) != 7
            or {x.get("offer_id") for x in plan["changes"]} != MECH_IDS):
        raise RuntimeError("Prepared plan identity/copy/blob differs from authorization")
    for oid in MECH_IDS:
        row = old[oid]
        if len(row.findall("name")) != 1 or len(row.findall("description")) > 1:
            raise RuntimeError("Ambiguous feed copy elements")
        if (len(new[oid].findall("name")) != 1 or len(new[oid].findall("description")) != 1
                or new[oid].findtext("name") != TITLE or new[oid].findtext("description") != TEXT):
            raise RuntimeError("Prepared offer lacks exact approved copy")
    def without_copy(root):
        root = copy.deepcopy(root)
        for oid, row in offers(root).items():
            if oid in MECH_IDS:
                for tag in ("name", "description"):
                    for element in row.findall(tag):
                        row.remove(element)
        return root
    if digest(without_copy(before)) != digest(without_copy(after)):
        raise RuntimeError("Prepared feed changes fields outside seven names/descriptions")
    if any(model(old[oid], False) != model(new[oid], False) for oid in OFFER_GROUPS[BOARD_AD]):
        raise RuntimeError("Prepared feed changes an electronic board")
    return {"before": before, "after": after, "before_hash": digest(before), "after_hash": digest(after),
            "plan_sha256": hashlib.sha256(PLAN.read_bytes()).hexdigest(), "source_blob_sha": blob}


def fetch_public(url):
    if url not in (FEED_URL, REFRESH_URL):
        raise RuntimeError("Public URL outside exact approved URLs")
    req = urllib.request.Request(url, method="GET", headers={"Accept": "application/xml,text/xml"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError("Public feed did not return HTTP 200")
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        raise RuntimeError("Public feed HTTP " + str(code)) from None
    return parse_xml(raw)


def public_observation(root, plan, url, attempt):
    rows = {row.get("id"): row for row in root.findall("./shop/offers/offer")}
    values = [{"offer_id": oid, "name": rows[oid].findtext("name") if oid in rows else None,
               "description_present": bool(rows[oid].findtext("description")) if oid in rows else False,
               "description_matches_approved": rows[oid].findtext("description") == TEXT if oid in rows else False}
              for oid in sorted(MECH_IDS)]
    observed_hash = digest(root)
    return {"attempt": attempt, "checked_at_utc": now(), "url": url, "semantic_sha256": observed_hash,
            "matches_before": observed_hash == plan["before_hash"], "matches_after": observed_hash == plan["after_hash"],
            "mechatronics": values, "approved_name_count": sum(x["name"] == TITLE for x in values),
            "description_present_count": sum(x["description_present"] for x in values),
            "approved_description_count": sum(x["description_matches_approved"] for x in values)}


def get_feed(api):
    result = api("feeds", "get", {"SelectionCriteria": {"Ids": [FID]},
        "FieldNames": ["Id", "Name", "SourceType", "Status", "UpdatedAt", "NumberOfItems", "TitleAndTextSources"],
        "UrlFeedFieldNames": ["Url"], "Page": {"Limit": 1}})
    rows = result.get("Feeds", [])
    if (len(rows) != 1 or result.get("LimitedBy") or rows[0].get("Id") != FID
            or rows[0].get("SourceType") != "URL" or rows[0].get("UrlFeed", {}).get("Url") not in (FEED_URL, REFRESH_URL)):
        raise RuntimeError("Feed identity/source/URL differs from approved scope")
    return {**{key: rows[0].get(key) for key in ("Id", "Name", "SourceType", "Status", "UpdatedAt", "NumberOfItems", "TitleAndTextSources")},
            "UrlFeed": {"Url": rows[0]["UrlFeed"]["Url"]}}


def get_ads(api):
    result = api("ads", "get", {"SelectionCriteria": {"Ids": list(ALL_ADS)},
        "FieldNames": ["Id", "CampaignId", "AdGroupId", "Type", "State", "Status", "StatusClarification"],
        "ShoppingAdFieldNames": SHOP_FIELDS, "Page": {"Limit": 10}})
    rows = result.get("Ads", [])
    ads = {x.get("Id"): x for x in rows}
    if len(rows) != 3 or set(ads) != set(ALL_ADS) or result.get("LimitedBy"):
        raise RuntimeError("Shopping-ad inventory differs from the reviewed three")
    for aid, ad in ads.items():
        if (ad.get("CampaignId") != CID or ad.get("AdGroupId") != ALL_ADS[aid]
                or ad.get("Type") != "SHOPPING_AD" or ad.get("ShoppingAd", {}).get("FeedId") != FID):
            raise RuntimeError("Unexpected ad campaign/group/type/feed")
    return ads


def stable(value):
    if isinstance(value, dict):
        return {k: stable(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [stable(v) for v in value]
    return value


def copy_fields(ad, desired=DESIRED):
    return {key: ad["ShoppingAd"].get(key) for key in desired}


def guard_ads(before, current, allow_desired=True, write_proof=(), desired=DESIRED):
    for aid in ALL_ADS:
        expected = copy.deepcopy(before[aid])
        if aid in TARGETS and allow_desired and copy_fields(current[aid], desired) == desired:
            expected["ShoppingAd"].update(copy.deepcopy(desired))
        own_pending_edit = (aid in TARGETS and aid in write_proof
                            and copy_fields(current[aid], desired) == desired
                            and current[aid].get("State") == "OFF"
                            and current[aid].get("Status") in {"DRAFT", "MODERATION"})
        if aid in TARGETS and copy_fields(current[aid], desired) == desired and current[aid].get("Status") == "REJECTED":
            raise RuntimeError("Approved copy is rejected and requires review: " + str(aid))
        if own_pending_edit:
            expected["State"] = "OFF"
        if stable(current[aid]) != stable(expected):
            raise RuntimeError("Ad changed outside this approved operation: " + str(aid))
        if current[aid].get("State") != "ON" and not own_pending_edit:
            raise RuntimeError("Reviewed active ad is no longer ON")


def guard_filters(ads, feed_root):
    rows = offers(feed_root)
    for aid, ad in ads.items():
        filters = (ad["ShoppingAd"].get("FeedFilterConditions") or {}).get("Items", [])
        if not filters:
            raise RuntimeError("Unbounded product filter")
        selected = set(rows)
        for f in filters:
            operand, operator, args = f.get("Operand"), f.get("Operator"), f.get("Arguments", [])
            if operand == "price" and operator == "IN_RANGE" and len(args) == 1:
                lower, upper = map(Decimal, args[0].split("-"))
                selected &= {oid for oid, row in rows.items() if lower <= Decimal(row.findtext("price")) <= upper}
            elif operand == "categoryId" and operator == "EQUALS_ANY":
                selected &= {oid for oid, row in rows.items() if row.findtext("categoryId") in args}
            else:
                raise RuntimeError("Filter operator not covered by bounded guard")
        if selected != OFFER_GROUPS[aid]:
            raise RuntimeError("Filter selects unexpected offer category")


def feed_ready(feed, baseline, require_description=True):
    required_sources = {"name", "description"} if require_description else {"name"}
    return (feed.get("Status") == "DONE" and feed.get("NumberOfItems") == 12
            and required_sources <= set((feed.get("TitleAndTextSources") or {}).get("Items", []))
            and bool(feed.get("UpdatedAt")) and feed.get("UpdatedAt") != baseline.get("UpdatedAt"))


def write_result_ok(result, key, ids):
    rows = result.get(key, [])
    return (len(rows) == len(ids) and {x.get("Id") for x in rows} == set(ids)
            and not any(x.get("Errors") for x in rows))


def run(mode, api=None, public=None, poll_attempts=1):
    if mode not in {"preflight", "refresh", "apply", "check", "apply_default_text", "check_default_text"} or not 1 <= poll_attempts <= 6:
        raise ValueError("Unsupported marker mode or poll bound")
    default_mode = mode in {"apply_default_text", "check_default_text"}
    apply_mode = "apply_default_text" if default_mode else "apply"
    desired_patch = DEFAULT_DESIRED if default_mode else DESIRED
    api, public = api or Client(mode), public or fetch_public
    out = Path("data/direct_shopping_copy_" + mode + "_20261119.json")
    apply_path = Path("data/direct_shopping_copy_" + apply_mode + "_20261119.json")
    prior_apply = json.loads(apply_path.read_text(encoding="utf-8")) if apply_path.exists() else {}
    fallback_path = Path("data/direct_shopping_copy_apply_default_text_20261119.json")
    prior_fallback = json.loads(fallback_path.read_text(encoding="utf-8")) if fallback_path.exists() else {}
    write_proof = set()
    report = {"mode": mode, "status": "started", "campaign_id": CID, "feed_id": FID,
              "target_ad_ids": list(TARGETS), "started_at_utc": now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
              "mutation_attempts": [], "mutations_verified": [], "errors": []}
    def save():
        out.parent.mkdir(parents=True, exist_ok=True)
        temp = out.with_suffix(".json.tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(out)
    save()
    try:
        plan = validate_plan()
        report["plan_sha256"] = plan["plan_sha256"]
        if (mode == "apply" and prior_fallback.get("mode") == "apply_default_text"
                and prior_fallback.get("campaign_id") == CID and prior_fallback.get("feed_id") == FID
                and prior_fallback.get("plan_sha256") == plan["plan_sha256"]
                and (prior_fallback.get("approved_copy_write_ids") or prior_fallback.get("mutation_attempts"))):
            raise RuntimeError("Literal fallback already attempted; automatic switch to description-source mode is forbidden")
        if (prior_apply.get("mode") == apply_mode and prior_apply.get("campaign_id") == CID
                and prior_apply.get("feed_id") == FID and prior_apply.get("plan_sha256") == plan["plan_sha256"]
                and set(prior_apply.get("target_ad_ids", [])) == set(TARGETS)):
            write_proof.update(i for i in prior_apply.get("approved_copy_write_ids", []) if i in TARGETS)
            write_proof.update(x["id"] for x in prior_apply.get("mutation_attempts", [])
                               if x.get("service") == "ads" and x.get("method") == "update" and x.get("id") in TARGETS)
        report["approved_copy_write_ids"] = sorted(write_proof)
        report["approved_copy_patch"] = desired_patch
        if default_mode:
            report["fallback_semantics"] = "DefaultTexts is a fallback if generated/source text is unavailable; TextSources=null does not disable generation or prove the literal text was displayed."
        report["feed_semantic_hashes"] = {"before": plan["before_hash"], "after": plan["after_hash"]}
        report["feed"] = feed = get_feed(api)
        report["ads"] = current = get_ads(api)
        if mode == "preflight":
            if feed.get("UrlFeed", {}).get("Url") != FEED_URL or feed.get("Status") != "DONE" or feed.get("NumberOfItems") != 12:
                raise RuntimeError("Preflight requires the original processed 12-offer feed")
            if digest(public(FEED_URL)) != plan["before_hash"] or digest(parse_xml(ACTIVE_XML.read_bytes())) != plan["before_hash"]:
                raise RuntimeError("Original public or repository feed differs from reviewed source")
            if any(a.get("State") != "ON" or a.get("Status") != "ACCEPTED" for a in current.values()):
                raise RuntimeError("Preflight ads must be active and accepted")
            guard_filters(current, plan["before"])
            report["status"] = "ready"
            report["approved_copy_patch"] = DESIRED
            report["approved_refresh_url"] = REFRESH_URL
        else:
            baseline = json.loads(PREFLIGHT.read_text(encoding="utf-8"))
            if baseline.get("status") != "ready" or baseline.get("plan_sha256") != plan["plan_sha256"]:
                raise RuntimeError("Missing successful preflight for this exact plan")
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(baseline["finished_at_utc"])).total_seconds()
            if not -60 <= age <= 86400:
                raise RuntimeError("Preflight is older than 24 hours")
            before = {int(key): value for key, value in baseline["ads"].items()}
            if default_mode and any(before[aid]["ShoppingAd"].get("TextSources") is not None
                                    or current[aid]["ShoppingAd"].get("TextSources") is not None for aid in TARGETS):
                raise RuntimeError("Literal fallback requires TextSources=null to remain unchanged")
            guard_ads(before, current, write_proof=write_proof, desired=desired_patch)
            guard_filters(current, plan["after"])
            if digest(parse_xml(ACTIVE_XML.read_bytes())) != plan["after_hash"]:
                raise RuntimeError("Authorized feed version is not published in this checkout")
            source_url = REFRESH_URL if mode == "refresh" else feed["UrlFeed"]["Url"]
            report["public_feed_observations"] = []
            public_attempts = 3 if mode == "refresh" else 1
            for attempt in range(1, public_attempts + 1):
                observation = public_observation(public(source_url), plan, source_url, attempt)
                report["public_feed_observations"].append(observation)
                save()
                if observation["matches_after"]:
                    break
                if mode == "refresh" and observation["matches_before"] and attempt < public_attempts:
                    time.sleep(20)
                    continue
                raise RuntimeError("Public feed does not match the exact authorized patch; observed semantic SHA256 "
                                   + observation["semantic_sha256"] + "; exact previous version=" + str(observation["matches_before"]))
            report["public_after_feed_verified"] = True
            if mode == "refresh":
                if feed["UrlFeed"]["Url"] != REFRESH_URL:
                    report["mutation_attempts"].append({"service": "feeds", "method": "update", "id": FID})
                    save()
                    try:
                        result = api("feeds", "update", {"Feeds": [{"Id": FID, "UrlFeed": {"Url": REFRESH_URL}}]})
                        report["refresh_response_ok"] = write_result_ok(result, "UpdateResults", [FID])
                    except Exception as error:
                        report["refresh_response_error"] = clean(error)
                report["feed"] = feed = get_feed(api)
                if feed["UrlFeed"]["Url"] != REFRESH_URL:
                    raise RuntimeError("Refresh URL change not confirmed; no retry performed")
                report["mutations_verified"].append({"service": "feeds", "id": FID, "url": REFRESH_URL})
                report["ads"] = after = get_ads(api)
                guard_ads(current, after, allow_desired=False, write_proof=write_proof, desired=desired_patch)
                report["status"] = "feed_ready" if feed_ready(feed, baseline["feed"]) else "refresh_requested_waiting_processing"
            else:
                for attempt in range(poll_attempts):
                    if feed_ready(feed, baseline["feed"], require_description=not default_mode):
                        break
                    if attempt + 1 < poll_attempts:
                        time.sleep(20)
                        report["feed"] = feed = get_feed(api)
                if not feed_ready(feed, baseline["feed"], require_description=not default_mode):
                    report["status"] = "waiting_feed_processing"
                else:
                    if mode in {"apply", "apply_default_text"}:
                        # Re-read after any processing wait before the first write.
                        report["ads"] = current = get_ads(api)
                        guard_ads(before, current, write_proof=write_proof, desired=desired_patch)
                        for aid in TARGETS:
                            guard_ads(before, current, write_proof=write_proof, desired=desired_patch)
                            if copy_fields(current[aid], desired_patch) == desired_patch:
                                continue
                            report["mutation_attempts"].append({"service": "ads", "method": "update", "id": aid})
                            write_proof.add(aid)
                            report["approved_copy_write_ids"] = sorted(write_proof)
                            save()
                            try:
                                result = api("ads", "update", {"Ads": [{"Id": aid, "ShoppingAd": copy.deepcopy(desired_patch)}]})
                                if not write_result_ok(result, "UpdateResults", [aid]):
                                    report.setdefault("write_response_warnings", []).append("Update response requires reconciliation: " + str(aid))
                            except Exception as error:
                                report.setdefault("uncertain_write_responses", []).append({"id": aid, "error": clean(error)})
                            report["ads"] = current = get_ads(api)
                            guard_ads(before, current, write_proof=write_proof, desired=desired_patch)
                            if copy_fields(current[aid], desired_patch) != desired_patch:
                                raise RuntimeError("Ad copy update not confirmed; no retry: " + str(aid))
                            report["mutations_verified"].append({"service": "ads", "id": aid})
                            save()
                        drafts = [aid for aid in TARGETS if current[aid].get("Status") == "DRAFT"]
                        if drafts:
                            report["mutation_attempts"].append({"service": "ads", "method": "moderate", "ids": drafts})
                            save()
                            try:
                                result = api("ads", "moderate", {"SelectionCriteria": {"Ids": drafts}})
                                report["moderation_response_ok"] = write_result_ok(result, "ModerateResults", drafts)
                            except Exception as error:
                                report["moderation_response_error"] = clean(error)
                    report["ads"] = current = get_ads(api)
                    guard_ads(before, current, write_proof=write_proof, desired=desired_patch)
                    copy_exact = all(copy_fields(current[aid], desired_patch) == desired_patch for aid in TARGETS)
                    live = all(current[aid].get("State") == "ON" and current[aid].get("Status") == "ACCEPTED"
                               and current[aid]["ShoppingAd"].get("FeedProcessingStatus") == "PROCESSED" for aid in TARGETS)
                    report["copy_saved_exactly"] = copy_exact
                    report["unrelated_ad_fields_and_board_unchanged"] = True
                    report["status"] = ("verified" if live else "copy_saved_pending_processing_or_moderation") if copy_exact else "copy_not_applied"
                    if default_mode:
                        report["default_text_saved_exactly"] = all(current[aid]["ShoppingAd"].get("DefaultTexts") == [TEXT] for aid in TARGETS)
                        report["text_sources_preserved_null"] = all(current[aid]["ShoppingAd"].get("TextSources") is None for aid in TARGETS)
                        report["status"] = ("verified_default_text_settings" if live else "default_text_saved_pending_processing_or_moderation") if copy_exact else "default_text_not_applied"
                    report["rendered_ad_preview_verified"] = False
    except Exception as error:
        report["errors"].append(clean(error))
        report["status"] = "requires_review_after_attempt" if report["mutation_attempts"] else "guard_failed_no_mutations"
        # Preserve successfully applied approved changes. Restoring an original
        # empty DefaultTexts list is not assumed valid; never do a blind rollback.
        if report["mutation_attempts"]:
            try:
                report["final_readback_ads"] = get_ads(api)
                report["final_readback_feed"] = get_feed(api)
            except Exception as read_error:
                report["errors"].append("Final readback unavailable: " + clean(read_error))
    report["finished_at_utc"] = now()
    save()
    print("Shopping copy finish:", mode, report["status"])
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    marker = json.loads(MARKER.read_text(encoding="utf-8"))
    sys.exit(run(marker.get("mode", "preflight"), poll_attempts=int(marker.get("poll_attempts", 1))))
