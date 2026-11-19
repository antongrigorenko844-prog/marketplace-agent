"""Bounded neutral-title/sitelink/feed operation. No prices, bids or resume.

preflight/check only read. refresh updates only the fixed feed URL. apply creates
one exact sitelink set, changes five assignments and two ResponsiveAd titles.
Persistent write journal prevents blind retries after uncertain responses.
"""
import copy
import json
import os
from pathlib import Path
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from direct_mass_negatives_apply_20261120 import (
    ADS_GET, CAMPAIGN_GET, NoRedirect, atomic_save, clean, jhash, phrase_hash,
    read_json, sha, stable,
)
from direct_shopping_copy_finish import parse_xml, offers, MECH_IDS, TEXT, FEED_URL

CID, FID, OLD_SID = 716316525, 4061297, 1526787288
TITLE = "Мехатроник DSG7 DQ200"
OLD_TITLE = "Мехатроник DSG7 DQ200 0AM325025"
DESCRIPTION = "Гарантия. От 29 500 ₽ при возврате своего мехатроника."
OLD_CATEGORY = "Мехатроники без электронной платы"
CATEGORY = "Мехатроники DSG7 DQ200"
OLD_URL = FEED_URL + "?copy_rev=20261119_01"
NEW_URL = FEED_URL + "?copy_rev=20261120_02"
BASE = "https://api.direct.yandex.com/json/v501/"
NEGATIVE_SHA = "51128e011f6e22567903273b2ef0ee7dd0be4893e70eba0f3baeaaea74ecc6a0"
NEGATIVE_REPORT = Path("data/direct_mass_negatives_apply_20261120.json")
MARKER = Path(".github/agent-requests/direct-neutral-ad-copy-20261120.txt")
PLAN = Path("data/direct_neutral_copy_feed_plan_20261120.json")
XML_BEFORE = Path("data/dq200_neutral_copy_before_20261120.xml")
XML_AFTER = Path("data/dq200_neutral_copy_after_20261120.xml")
ACTIVE_XML = Path("feeds/dq200_candidate.yml")
PREFLIGHT = Path("data/direct_neutral_copy_preflight_20261120.json")
JOURNAL = Path("data/direct_neutral_copy_journal_20261120.json")
RESPONSIVE = {1927466195846975291: 5826171512, 1927466206584354174: 5826183946}
SHOPPING = {1927186476439690942: 5826171512, 1927189770678081700: 5826183946,
            1927190920657966611: 5826320196}
TARGETS = {**SHOPPING, **RESPONSIVE}
LINK_BASE = "https://leoparts-akpp.ru/mehatronik-dq200"
OLD_LINKS = [
    {"Title": "Мехатроники без платы", "Href": LINK_BASE + "#bez-platy",
     "Description": "Мехатроники DSG7 DQ200 без электронной платы"},
    {"Title": "Мехатроники с платой", "Href": LINK_BASE + "#s-platoi",
     "Description": "Мехатроники DSG7 DQ200 с электронной платой"},
    {"Title": "Электронные платы DQ200", "Href": LINK_BASE + "#platy",
     "Description": "Платы управления мехатроника DSG7 DQ200"},
]
LINKS = [{**OLD_LINKS[0], "Title": TITLE, "Description": DESCRIPTION}, *OLD_LINKS[1:]]
FEED_GET = {"SelectionCriteria": {"Ids": [FID]}, "FieldNames": ["Id", "Name", "SourceType", "Status",
    "UpdatedAt", "NumberOfItems", "TitleAndTextSources"], "UrlFeedFieldNames": ["Url"], "Page": {"Limit": 1}}
SETS_GET = {"FieldNames": ["Id", "Sitelinks"], "Page": {"Limit": 1000}}
GETS = {"ads": ADS_GET, "campaigns": CAMPAIGN_GET, "feeds": FEED_GET, "sitelinks": SETS_GET}


class ModerationRejected(RuntimeError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat()


class Client:
    def __init__(self, mode):
        self.mode, self.permitted, self.used = mode, None, set()
        self.target_sid = None

    def __call__(self, service, method, params):
        if method == "get" and service in GETS and params == GETS[service]:
            pass
        else:
            key = jhash([service, method, params])
            if self.permitted != (service, method, params) or key in self.used:
                raise RuntimeError("API mutation is not the exact one registered by this operation")
            if not ((self.mode == "refresh" and service == "feeds" and method == "update")
                    or (self.mode == "apply" and (service, method) in
                        {("sitelinks", "add"), ("ads", "update"), ("ads", "moderate")})):
                raise RuntimeError("Mutation outside explicit mode")
            if service == "feeds" and params != {"Feeds": [{"Id": FID, "UrlFeed": {"Url": NEW_URL}}]}:
                raise RuntimeError("Feed mutation outside exact approved URL")
            if service == "sitelinks" and params != {"SitelinksSets": [{"Sitelinks": LINKS}]}:
                raise RuntimeError("Sitelink mutation outside exact three approved links")
            if service == "ads" and method == "update":
                entries = params.get("Ads", [])
                if (len(entries) != 1 or entries[0].get("Id") not in TARGETS
                        or not isinstance(self.target_sid, int) or self.target_sid == OLD_SID
                        or params != {"Ads": [{"Id": entries[0]["Id"],
                            subtype(entries[0]["Id"]): desired_patch(entries[0]["Id"], self.target_sid)}]}):
                    raise RuntimeError("Ad update outside exact titles/sitelink assignments")
            if service == "ads" and method == "moderate":
                ids = params.get("SelectionCriteria", {}).get("Ids", [])
                if len(ids) != 1 or ids[0] not in TARGETS or params != {"SelectionCriteria": {"Ids": ids}}:
                    raise RuntimeError("Moderation outside one approved ad")
            self.used.add(key)
            self.permitted = None
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
                raw = response.read(2 * 1024 * 1024 + 1)
        except urllib.error.HTTPError as error:
            code = error.code
            error.close()
            raise RuntimeError(service + "." + method + " HTTP " + str(code)) from None
        except Exception:
            raise RuntimeError(service + "." + method + " transport error") from None
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("API response exceeds bound")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RuntimeError(service + "." + method + " non-JSON response") from None
        if body.get("error") or not isinstance(body.get("result"), dict):
            raise RuntimeError(service + "." + method + " API error " +
                               str((body.get("error") or {}).get("error_code", "missing result")))
        return body["result"]


def public_get(url):
    if url not in {FEED_URL, OLD_URL, NEW_URL}:
        raise RuntimeError("Public URL outside exact approved host/path/query")
    # This separate unauthenticated request never includes Direct credentials.
    request = urllib.request.Request(url, method="GET", headers={"Accept": "application/xml,text/xml"})
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=30) as response:
            if response.status != 200:
                raise RuntimeError("Public feed must return HTTP 200")
            raw = response.read(2 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        code = error.code
        error.close()
        raise RuntimeError("Public feed HTTP " + str(code)) from None
    parse_xml(raw)
    return raw


def validate_plan():
    plan, plan_sha = read_json(PLAN)
    before, after = XML_BEFORE.read_bytes(), XML_AFTER.read_bytes()
    old, new = offers(parse_xml(before)), offers(parse_xml(after))
    if (plan.get("campaign_id") != CID or plan.get("feed_id") != FID
            or plan.get("requested_title") != TITLE or plan.get("previous_title") != OLD_TITLE
            or plan.get("hashes", {}).get("xml_before_sha256") != sha(before)
            or plan.get("hashes", {}).get("xml_after_sha256") != sha(after)):
        raise RuntimeError("Feed plan identity or exact XML hashes differ")
    old_tag, new_tag = ("<name>" + OLD_TITLE + "</name>").encode(), ("<name>" + TITLE + "</name>").encode()
    old_cat = ('<category id="1">' + OLD_CATEGORY + '</category>').encode()
    new_cat = ('<category id="1">' + CATEGORY + '</category>').encode()
    if (before.count(old_tag) != 7 or after.count(new_tag) != 7 or before.count(old_cat) != 1
            or after.count(new_cat) != 1 or before.replace(old_tag, new_tag).replace(old_cat, new_cat) != after):
        raise RuntimeError("Only seven names and category-1 visible label may change")
    if any(new[oid].findtext("name") != TITLE or old[oid].findtext("description") != TEXT
           or new[oid].findtext("description") != TEXT for oid in MECH_IDS):
        raise RuntimeError("Mechatronic titles/descriptions differ from approved values")
    if len(TITLE) != 21 or len(DESCRIPTION) != 54 or "29 500" in after.decode():
        raise RuntimeError("Price/title scope guard failed")
    return {"plan_sha256": plan_sha, "before_sha256": sha(before), "after_sha256": sha(after)}


def rows(api, service, key):
    result = api(service, "get", GETS[service])
    values = result.get(key)
    if not isinstance(values, list) or result.get("LimitedBy"):
        raise RuntimeError("Incomplete " + service + " read")
    return values


def get_campaign(api):
    values = rows(api, "campaigns", "Campaigns")
    if len(values) != 1 or values[0].get("Id") != CID:
        raise RuntimeError("Unexpected campaign")
    campaign = values[0]
    negatives = (campaign.get("NegativeKeywords") or {}).get("Items", [])
    if len(negatives) != 130 or phrase_hash(negatives) != NEGATIVE_SHA:
        raise RuntimeError("Current 130 negatives differ from completed operation")
    return campaign


def get_ads(api):
    values = rows(api, "ads", "Ads")
    ads = {a.get("Id"): a for a in values}
    if len(values) != 5 or set(ads) != set(TARGETS):
        raise RuntimeError("Unexpected five-ad inventory")
    for aid, ad in ads.items():
        kind = "RESPONSIVE_AD" if aid in RESPONSIVE else "SHOPPING_AD"
        if ad.get("CampaignId") != CID or ad.get("AdGroupId") != TARGETS[aid] or ad.get("Type") != kind:
            raise RuntimeError("Ad campaign/group/type changed")
        if aid in SHOPPING and ad.get("ShoppingAd", {}).get("FeedId") != FID:
            raise RuntimeError("Shopping feed changed")
    return ads


def get_feed(api):
    values = rows(api, "feeds", "Feeds")
    if len(values) != 1 or values[0].get("Id") != FID or values[0].get("SourceType") != "URL":
        raise RuntimeError("Unexpected feed identity/source")
    feed = values[0]
    if feed.get("UrlFeed", {}).get("Url") not in {FEED_URL, OLD_URL, NEW_URL}:
        raise RuntimeError("Unexpected feed URL")
    return feed


def get_sets(api):
    values = rows(api, "sitelinks", "SitelinksSets")
    result = {s.get("Id"): s for s in values}
    if len(values) != len(result) or any(not isinstance(sid, int) for sid in result):
        raise RuntimeError("Invalid sitelink inventory")
    return result


def subtype(aid):
    return "ResponsiveAd" if aid in RESPONSIVE else "ShoppingAd"


def desired_patch(aid, sid):
    return {"SitelinkSetId": sid, **({"Titles": [TITLE]} if aid in RESPONSIVE else {})}


def matches(ad, sid):
    content = ad[subtype(ad["Id"])]
    return (content.get("SitelinkSetId") == sid and (ad["Id"] not in RESPONSIVE
        or [x.get("Title") for x in content.get("Titles", [])] == [TITLE]))


def guard_ads(before, current, sid, proof):
    for aid, old in before.items():
        expected = copy.deepcopy(old)
        ad = current[aid]
        exact = sid is not None and matches(ad, sid)
        if exact:
            expected[subtype(aid)]["SitelinkSetId"] = sid
            if aid in RESPONSIVE:
                expected["ResponsiveAd"]["Titles"] = [{"Title": TITLE}]
        pending_own = (aid in proof and exact and ad.get("State") == "OFF"
                       and ad.get("Status") in {"DRAFT", "MODERATION"})
        if pending_own:
            expected["State"] = "OFF"
        if stable(ad) != stable(expected) or (ad.get("State") != "ON" and not pending_own):
            raise RuntimeError("Ad fields changed outside this operation: " + str(aid))
        if ad.get("Status") == "REJECTED":
            raise ModerationRejected("Ad moderation rejected; requires review: " + str(aid))
        if exact and (ad[subtype(aid)].get("SitelinksModeration") or {}).get("Status") == "REJECTED":
            raise ModerationRejected("New sitelinks rejected; requires review: " + str(aid))
        if exact and aid in RESPONSIVE and any(x.get("Status") == "REJECTED"
                for x in ad["ResponsiveAd"].get("Titles", [])):
            raise ModerationRejected("New ResponsiveAd title rejected; requires review: " + str(aid))


def run(marker=None, api=None, public=None):
    marker = marker or read_json(MARKER)[0]
    mode = marker.get("mode")
    if mode not in {"preflight", "refresh", "apply", "check"}:
        raise RuntimeError("Unsupported mode")
    api, public = api or Client(mode), public or public_get
    out = Path("data/direct_neutral_copy_" + mode + "_20261120.json")
    report = {"mode": mode, "campaign_id": CID, "feed_id": FID, "started_at_utc": now(),
        "workflow_commit": os.environ.get("GITHUB_SHA"), "status": "started", "errors": [],
        "requested_title": TITLE, "requested_first_sitelink_description": DESCRIPTION,
        "rendered_preview_verified": False, "mutation_attempts_this_run": []}
    journal = read_json(JOURNAL)[0] if JOURNAL.exists() else None
    def save():
        atomic_save(out, report)
    def journal_save():
        atomic_save(JOURNAL, journal)
    try:
        plan = validate_plan()
        report.update(plan)
        negative, _ = read_json(NEGATIVE_REPORT)
        if (negative.get("status") != "applied_verified" or negative.get("campaign_id") != CID
                or negative.get("expected_after_phrases_sha256") != NEGATIVE_SHA
                or negative.get("stable_fields_unchanged") is not True or negative.get("errors")
                or negative.get("unexpected_missing_phrases") or negative.get("unexpected_extra_phrases")):
            raise RuntimeError("Completed successful 130-negative operation is required")
        campaign, current, feed, sets = get_campaign(api), get_ads(api), get_feed(api), get_sets(api)
        if OLD_SID not in sets or sets[OLD_SID].get("Sitelinks") != OLD_LINKS:
            raise RuntimeError("Original three-link set changed")
        report.update(campaign=campaign, ads=current, feed=feed, original_sitelinks_set=sets[OLD_SID])
        if mode == "preflight":
            if PREFLIGHT.exists() and read_json(PREFLIGHT)[0].get("status") == "preflight_verified_no_write":
                raise RuntimeError("Preserve successful preflight; do not overwrite its baseline")
            if any(a.get("State") != "ON" or a.get("Status") != "ACCEPTED"
                   or a[subtype(aid)].get("SitelinkSetId") != OLD_SID for aid, a in current.items()):
                raise RuntimeError("Preflight requires the reviewed five accepted active ads and original link set")
            if feed.get("Status") != "DONE" or feed.get("NumberOfItems") != 12 or feed["UrlFeed"]["Url"] == NEW_URL:
                raise RuntimeError("Preflight requires processed original twelve-offer feed")
            raw = public(feed["UrlFeed"]["Url"])
            report["public_xml_sha256"] = sha(raw)
            if sha(raw) != plan["before_sha256"] or sha(ACTIVE_XML.read_bytes()) != plan["before_sha256"]:
                raise RuntimeError("Live/repository feed differs from exact reviewed before XML")
            report.update(status="preflight_verified_no_write", captured_at_utc=now(),
                existing_set_ids=sorted(sets), approved_links=LINKS, approved_refresh_url=NEW_URL)
            return 0
        baseline, baseline_sha = read_json(PREFLIGHT)
        if (baseline.get("status") != "preflight_verified_no_write"
                or baseline.get("plan_sha256") != plan["plan_sha256"]):
            raise RuntimeError("Missing reviewed preflight for this exact feed plan")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(baseline["captured_at_utc"])).total_seconds()
        if mode != "check" and not 0 <= age <= 86400:
            raise RuntimeError("Mutation requires preflight less than 24 hours old")
        before = {int(k): v for k, v in baseline["ads"].items()}
        identity = {"campaign_id": CID, "feed_id": FID, "plan_sha256": plan["plan_sha256"],
                    "preflight_sha256": baseline_sha, "approved_links_sha256": jhash(LINKS)}
        if journal is None:
            journal = {**identity, "operations": {}, "target_sitelink_set_id": None}
        elif any(journal.get(k) != v for k, v in identity.items()):
            raise RuntimeError("Existing write journal belongs to a different reviewed operation")
        ops = journal["operations"]
        proof = {aid for aid in TARGETS if "ad_update_" + str(aid) in ops}
        sid = journal.get("target_sitelink_set_id")
        if stable(campaign) != stable(baseline["campaign"]):
            raise RuntimeError("Campaign budget/strategy/negatives or other read settings changed")
        guard_ads(before, current, sid, proof)
        if sha(ACTIVE_XML.read_bytes()) != plan["after_sha256"]:
            raise RuntimeError("Approved after XML is not published in this checkout")
        # Cache retries are read-only and allowed only for the exact known old bytes.
        for attempt in range(3 if mode == "refresh" else 1):
            raw = public(NEW_URL)
            report.setdefault("public_checks", []).append({"at_utc": now(), "sha256": sha(raw)})
            if sha(raw) == plan["after_sha256"]:
                break
            if sha(raw) != plan["before_sha256"] or mode != "refresh" or attempt == 2:
                raise RuntimeError("Public feed is not exact approved after XML")
            time.sleep(20)

        def mutate(key, service, method, params):
            if key in ops:
                raise RuntimeError("Prior uncertain/recorded mutation must not be repeated: " + key)
            ops[key] = {"service": service, "method": method, "params_sha256": jhash(params),
                        "attempted_at_utc": now(), "verified": False}
            if key == "sitelinks_add":
                ops[key]["ids_before"] = journal["pending_sitelink_ids_before"]
            journal_save()
            report["mutation_attempts_this_run"].append(key)
            save()
            if isinstance(api, Client):
                api.permitted = (service, method, params)
            try:
                result = api(service, method, params)
                result_key = {"add": "AddResults", "update": "UpdateResults", "moderate": "ModerateResults"}[method]
                entries = result.get(result_key, [])
                ops[key]["response_ids"] = [x.get("Id") for x in entries]
                ops[key]["error_codes"] = [x.get("Code") for row in entries for x in row.get("Errors", [])]
                expected_id = FID if service == "feeds" else (params["Ads"][0]["Id"] if method == "update"
                    else params["SelectionCriteria"]["Ids"][0] if method == "moderate" else None)
                if (len(entries) != 1 or ops[key]["error_codes"] or not isinstance(entries[0].get("Id"), int)
                        or (expected_id is not None and entries[0]["Id"] != expected_id)):
                    raise RuntimeError("Mutation response reports failure or no result")
            except Exception as error:
                ops[key]["uncertain_error"] = clean(error)
            journal_save()

        if mode == "refresh":
            if feed["UrlFeed"]["Url"] != NEW_URL:
                mutate("feed_refresh", "feeds", "update", {"Feeds": [{"Id": FID, "UrlFeed": {"Url": NEW_URL}}]})
            feed = get_feed(api)
            report["feed_after"] = feed
            if feed["UrlFeed"]["Url"] != NEW_URL:
                raise RuntimeError("Feed URL refresh is not verified; no retry")
            if "feed_refresh" in ops:
                ops["feed_refresh"]["verified"] = True
                journal_save()
            report["status"] = "feed_refresh_requested_verified"
            return 0
        if feed["UrlFeed"]["Url"] != NEW_URL:
            raise RuntimeError("Feed refresh to approved revision02 must precede ad apply/check")
        if sid is None:
            # Reconcile an interrupted set creation from its persisted prior ID list.
            candidates = [set_id for set_id, value in sets.items() if value.get("Sitelinks") == LINKS]
            prior_add = ops.get("sitelinks_add")
            if prior_add:
                prior_ids = prior_add.get("ids_before", journal.get("pending_sitelink_ids_before"))
                if not isinstance(prior_ids, list):
                    raise RuntimeError("Interrupted sitelink add lacks its recorded prior ID inventory")
                candidates = [set_id for set_id in candidates if set_id not in prior_ids]
            if len(candidates) == 1:
                sid = candidates[0]
            elif candidates or mode == "check":
                raise RuntimeError("New sitelink set is missing or ambiguous")
            elif prior_add:
                raise RuntimeError("Previous sitelink add is unresolved; no retry")
            else:
                sets = get_sets(api)
                ids_before = sorted(sets)
                # Save the list before add in its journal record, before the request.
                journal["pending_sitelink_ids_before"] = ids_before
                journal_save()
                mutate("sitelinks_add", "sitelinks", "add", {"SitelinksSets": [{"Sitelinks": LINKS}]})
                ops["sitelinks_add"]["ids_before"] = ids_before
                journal_save()
                sets = get_sets(api)
                candidates = [i for i, value in sets.items() if i not in ids_before and value.get("Sitelinks") == LINKS]
                returned = ops["sitelinks_add"].get("response_ids", [])
                if len(candidates) != 1 or (returned and returned != candidates):
                    raise RuntimeError("Sitelink add could not be uniquely reconciled; no retry")
                sid = candidates[0]
            journal["target_sitelink_set_id"] = sid
            if prior_add or "sitelinks_add" in ops:
                ops["sitelinks_add"]["verified"] = True
            if mode == "apply":
                journal_save()
        if sid == OLD_SID or sid not in sets or sets[sid].get("Sitelinks") != LINKS:
            raise RuntimeError("New sitelink set contents are not exact")
        if isinstance(api, Client):
            api.target_sid = sid
        report["new_sitelinks_set"] = sets[sid]
        if mode == "apply":
            for aid in TARGETS:
                current = get_ads(api)
                guard_ads(before, current, sid, proof)
                if matches(current[aid], sid):
                    continue
                # Recheck campaign immediately before every ad update.
                if stable(get_campaign(api)) != stable(baseline["campaign"]):
                    raise RuntimeError("Campaign changed before ad update")
                mutate("ad_update_" + str(aid), "ads", "update", {"Ads": [{"Id": aid,
                    subtype(aid): desired_patch(aid, sid)}]})
                proof.add(aid)
                current = get_ads(api)
                guard_ads(before, current, sid, proof)
                if not matches(current[aid], sid):
                    raise RuntimeError("Ad update not verified; no retry")
                ops["ad_update_" + str(aid)]["verified"] = True
                journal_save()
            current = get_ads(api)
            guard_ads(before, current, sid, proof)
            if not all(matches(ad, sid) for ad in current.values()):
                raise RuntimeError("All five approved edits must verify before moderation")
            for aid, ad in current.items():
                if ad.get("Status") == "DRAFT":
                    if aid not in proof:
                        raise RuntimeError("Draft has no own-change proof")
                    mutate("ad_moderate_" + str(aid), "ads", "moderate", {"SelectionCriteria": {"Ids": [aid]}})
        current, feed = get_ads(api), get_feed(api)
        guard_ads(before, current, sid, proof)
        if stable(get_campaign(api)) != stable(baseline["campaign"]):
            raise RuntimeError("Campaign changed during verification")
        final_sets = get_sets(api)
        if (final_sets.get(OLD_SID, {}).get("Sitelinks") != OLD_LINKS
                or final_sets.get(sid, {}).get("Sitelinks") != LINKS):
            raise RuntimeError("Final old/new sitelink contents differ from exact reviewed sets")
        report["final_sitelinks_sets"] = [final_sets[OLD_SID], final_sets[sid]]
        report.update(ads_after=current, feed_after=feed, requested_copy_saved=all(matches(ad, sid) for ad in current.values()),
            other_read_ad_fields_preserved=True, original_sitelinks_set_preserved=True,
            prices_and_other_feed_fields_preserved=True, negative_130_preserved=True)
        feed_ready = (feed.get("Status") == "DONE" and feed.get("NumberOfItems") == 12
            and feed.get("UrlFeed", {}).get("Url") == NEW_URL and bool(feed.get("UpdatedAt"))
            and feed.get("UpdatedAt") != baseline["feed"].get("UpdatedAt")
            and "name" in (feed.get("TitleAndTextSources") or {}).get("Items", []))
        ads_ready = all(a.get("State") == "ON" and a.get("Status") == "ACCEPTED" for a in current.values())
        link_statuses = {str(aid): (ad[subtype(aid)].get("SitelinksModeration") or {}).get("Status")
                         for aid, ad in current.items()}
        links_ready = all(status == "ACCEPTED" for status in link_statuses.values())
        title_statuses = {str(aid): [x.get("Status") for x in current[aid]["ResponsiveAd"].get("Titles", [])]
                          for aid in RESPONSIVE}
        titles_ready = all(values == ["ACCEPTED"] for values in title_statuses.values())
        shopping_statuses = {str(aid): current[aid]["ShoppingAd"].get("FeedProcessingStatus") for aid in SHOPPING}
        shopping_ready = all(value == "PROCESSED" for value in shopping_statuses.values())
        report.update(feed_processing_verified=feed_ready, ads_active_accepted=ads_ready,
                      sitelinks_moderation_statuses=link_statuses, sitelinks_all_accepted=links_ready,
                      title_moderation_statuses=title_statuses, titles_all_accepted=titles_ready,
                      shopping_feed_processing_statuses=shopping_statuses, shopping_feed_all_processed=shopping_ready)
        report["status"] = ("verified_requested_settings" if report["requested_copy_saved"] and feed_ready and ads_ready and links_ready and titles_ready and shopping_ready
            else "requested_settings_saved_pending_processing_or_moderation" if report["requested_copy_saved"]
            else "requested_settings_not_applied")
        return 0 if report["requested_copy_saved"] else 1
    except Exception as error:
        report["errors"].append(clean(error))
        report["status"] = ("requires_review_moderation_rejected" if isinstance(error, ModerationRejected)
            else "requires_review_after_attempt" if report["mutation_attempts_this_run"] else "blocked_no_write")
        return 1
    finally:
        report["finished_at_utc"] = now()
        if not (mode == "preflight" and PREFLIGHT.exists()
                and read_json(PREFLIGHT)[0].get("status") == "preflight_verified_no_write"):
            save()
        print("Neutral ad copy:", mode, report["status"])


if __name__ == "__main__":
    sys.exit(run())
