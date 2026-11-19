"""Change only the first sitelink description; preserve and re-read five ads."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

from direct_neutral_ad_copy_20261120 import (
    ADS_GET, CAMPAIGN_GET, SETS_GET, NoRedirect, atomic_save, clean, get_ads,
    get_campaign, get_sets, jhash, now, read_json, stable, subtype, TARGETS,
)

CID = 716316525
OLD_SID = 1526812558
DESCRIPTION = "От 29 500 ₽ при возврате своего старого мехатроника."
SOURCE = Path("data/direct_neutral_copy_apply_20261120.json")
SOURCE_SHA = "ad7290ba8a6b6385809bc11ce4f981e2dd54a261863630174387763c45c7bfff"
MARKER = Path(".github/agent-requests/direct-sitelink-description-20261120.txt")
JOURNAL = Path("data/direct_sitelink_description_journal_20261120.json")
BASE = "https://api.direct.yandex.com/json/v501/"
GETS = {"ads": ADS_GET, "campaigns": CAMPAIGN_GET, "sitelinks": SETS_GET}


class Client:
    def __init__(self, mode, links):
        self.mode, self.links = mode, links
        self.sid, self.permitted, self.used = None, None, set()

    def __call__(self, service, method, params):
        if method == "get" and service in GETS and params == GETS[service]:
            pass
        else:
            key = jhash([service, method, params])
            if self.mode != "apply" or self.permitted != (service, method, params) or key in self.used:
                raise RuntimeError("Operation outside the exact approved request")
            if (service, method) == ("sitelinks", "add"):
                expected = {"SitelinksSets": [{"Sitelinks": self.links}]}
            elif (service, method) == ("ads", "update") and isinstance(self.sid, int) and self.sid != OLD_SID:
                expected = {"Ads": [{"Id": aid, subtype(aid): {"SitelinkSetId": self.sid}} for aid in sorted(TARGETS)]}
            elif (service, method) == ("ads", "moderate"):
                ids = params.get("SelectionCriteria", {}).get("Ids", [])
                if not ids or len(ids) != len(set(ids)) or not set(ids) <= set(TARGETS):
                    raise RuntimeError("Moderation target outside five ads")
                expected = {"SelectionCriteria": {"Ids": ids}}
            else:
                raise RuntimeError("Only set creation, five assignments and own moderation are permitted")
            if params != expected:
                raise RuntimeError("Mutation payload differs from exact permitted fields")
            self.used.add(key)
            self.permitted = None
        token = os.environ.get("YANDEX_DIRECT_TOKEN")
        if not token:
            raise RuntimeError("Direct token unavailable")
        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json; charset=utf-8", "Accept-Language": "en"}
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
            raise RuntimeError(service + "." + method + " transport failure") from None
        if len(raw) > 2 * 1024 * 1024:
            raise RuntimeError("Response exceeds bound")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise RuntimeError("Non-JSON Direct response") from None
        if body.get("error") or not isinstance(body.get("result"), dict):
            raise RuntimeError("Direct API error " + str((body.get("error") or {}).get("error_code", "missing result")))
        return body["result"]


def run():
    mode = read_json(MARKER)[0].get("mode")
    if mode not in {"apply", "check"}:
        raise RuntimeError("Unsupported mode")
    report = {"mode": mode, "campaign_id": CID, "started_at_utc": now(),
        "workflow_commit": os.environ.get("GITHUB_SHA"), "errors": [], "mutation_attempts_this_run": [],
        "requested_description": DESCRIPTION, "rendered_preview_verified": False}
    out = Path("data/direct_sitelink_description_" + mode + "_20261120.json")
    journal = read_json(JOURNAL)[0] if JOURNAL.exists() else None
    def save():
        atomic_save(out, report)
    try:
        source, digest = read_json(SOURCE)
        old_set = source.get("new_sitelinks_set", {})
        old_links = old_set.get("Sitelinks", [])
        if (digest != SOURCE_SHA or source.get("status") != "verified_requested_settings"
                or old_set.get("Id") != OLD_SID or len(old_links) != 3 or len(DESCRIPTION) != 52
                or old_links[0].get("Description") != "Гарантия. От 29 500 ₽ при возврате своего мехатроника."):
            raise RuntimeError("Verified source or approved copy differs")
        links = copy.deepcopy(old_links)
        links[0]["Description"] = DESCRIPTION
        baseline_ads = {int(k): v for k, v in source["ads_after"].items()}
        if set(baseline_ads) != set(TARGETS):
            raise RuntimeError("Source ad inventory differs")
        identity = {"source_sha256": digest, "old_set_id": OLD_SID, "new_links_sha256": jhash(links)}
        if journal is None:
            journal = {**identity, "operations": {}, "new_set_id": None}
        elif any(journal.get(k) != v for k, v in identity.items()):
            raise RuntimeError("Journal belongs to a different operation")
        api = Client(mode, links)
        campaign, current, sets = get_campaign(api), get_ads(api), get_sets(api)
        report.update(before_campaign=campaign, before_ads=current, original_set=old_set)
        if stable(campaign) != stable(source["campaign"]) or sets.get(OLD_SID) != old_set:
            raise RuntimeError("Campaign or original links changed since verified source")
        ops = journal["operations"]
        sid = journal.get("new_set_id")

        def check_ads(ads):
            for aid, original in baseline_ads.items():
                ad = ads[aid]
                expected = copy.deepcopy(original)
                actual_sid = ad[subtype(aid)].get("SitelinkSetId")
                if sid is not None and actual_sid == sid:
                    expected[subtype(aid)]["SitelinkSetId"] = sid
                elif actual_sid != OLD_SID:
                    raise RuntimeError("Unexpected ad link assignment")
                own_pending = ("ads_update" in ops and actual_sid == sid and ad.get("State") == "OFF"
                               and ad.get("Status") in {"DRAFT", "MODERATION"})
                if own_pending:
                    expected["State"] = "OFF"
                if stable(ad) != stable(expected) or (ad.get("State") != "ON" and not own_pending):
                    raise RuntimeError("Ad fields changed outside description update")
                if ad.get("Status") == "REJECTED" or (ad[subtype(aid)].get("SitelinksModeration") or {}).get("Status") == "REJECTED":
                    raise RuntimeError("Moderation rejected; requires review")

        def mutate(key, service, method, params, expected_ids=None, extra=None):
            if key in ops:
                raise RuntimeError("Recorded mutation must not be blindly repeated")
            ops[key] = {"attempted_at_utc": now(), "request_sha256": jhash(params), **(extra or {})}
            atomic_save(JOURNAL, journal)
            report["mutation_attempts_this_run"].append(key)
            save()
            api.permitted = (service, method, params)
            try:
                result = api(service, method, params)
                rows = result.get({"add": "AddResults", "update": "UpdateResults", "moderate": "ModerateResults"}[method], [])
                ops[key]["response"] = [{"Id": row.get("Id"), "errors": [e.get("Code") for e in row.get("Errors", [])]} for row in rows]
                if (not rows or any(row.get("Errors") for row in rows)
                        or (expected_ids is not None and sorted(row.get("Id") for row in rows) != sorted(expected_ids))):
                    raise RuntimeError("Response does not confirm all requested targets")
            except Exception as error:
                ops[key]["uncertain_error"] = clean(error)
            atomic_save(JOURNAL, journal)

        check_ads(current)
        if sid is None:
            candidates = [i for i, value in sets.items() if value.get("Sitelinks") == links]
            if "sitelinks_add" in ops:
                candidates = [i for i in candidates if i not in ops["sitelinks_add"]["ids_before"]]
            if len(candidates) == 1:
                sid = candidates[0]
            elif candidates or mode == "check" or "sitelinks_add" in ops:
                raise RuntimeError("New link set is missing or ambiguous; no retry")
            else:
                ids_before = sorted(sets)
                mutate("sitelinks_add", "sitelinks", "add", {"SitelinksSets": [{"Sitelinks": links}]}, extra={"ids_before": ids_before})
                sets = get_sets(api)
                candidates = [i for i, value in sets.items() if i not in ids_before and value.get("Sitelinks") == links]
                if len(candidates) != 1:
                    raise RuntimeError("Set creation not uniquely verified; no retry")
                sid = candidates[0]
            journal["new_set_id"] = sid
            if mode == "apply":
                atomic_save(JOURNAL, journal)
        if sid == OLD_SID or sets.get(sid, {}).get("Sitelinks") != links:
            raise RuntimeError("New set contents differ")
        api.sid = sid
        current = get_ads(api)
        check_ads(current)
        already = all(ad[subtype(aid)].get("SitelinkSetId") == sid for aid, ad in current.items())
        if not already and mode == "apply":
            if any(ad[subtype(aid)].get("SitelinkSetId") != OLD_SID for aid, ad in current.items()):
                raise RuntimeError("Partial prior update requires review")
            if stable(get_campaign(api)) != stable(campaign):
                raise RuntimeError("Campaign changed before assignment")
            payload = {"Ads": [{"Id": aid, subtype(aid): {"SitelinkSetId": sid}} for aid in sorted(TARGETS)]}
            mutate("ads_update", "ads", "update", payload, expected_ids=set(TARGETS))
            current = get_ads(api)
            report["after_ads"] = current
            check_ads(current)
            if not all(ad[subtype(aid)].get("SitelinkSetId") == sid for aid, ad in current.items()):
                raise RuntimeError("Partial or unconfirmed assignment; no retry")
        drafts = [aid for aid, ad in current.items() if ad.get("Status") == "DRAFT"]
        if mode == "apply" and drafts:
            if "ads_update" not in ops:
                raise RuntimeError("Draft has no own-write proof")
            mutate("moderate", "ads", "moderate", {"SelectionCriteria": {"Ids": sorted(drafts)}}, expected_ids=drafts)
        current, sets, final_campaign = get_ads(api), get_sets(api), get_campaign(api)
        report.update(after_ads=current, new_set=sets.get(sid), after_campaign=final_campaign)
        check_ads(current)
        if sets.get(OLD_SID) != old_set or sets.get(sid, {}).get("Sitelinks") != links or stable(final_campaign) != stable(campaign):
            raise RuntimeError("Final set or campaign preservation failed")
        assigned = all(ad[subtype(aid)].get("SitelinkSetId") == sid for aid, ad in current.items())
        accepted = all(ad.get("State") == "ON" and ad.get("Status") == "ACCEPTED"
            and (ad[subtype(aid)].get("SitelinksModeration") or {}).get("Status") == "ACCEPTED" for aid, ad in current.items())
        report.update(description_saved=assigned, all_five_active_accepted=accepted,
            other_read_ad_fields_preserved=True, campaign_and_130_negatives_preserved=True,
            original_set_preserved=True, status="verified" if assigned and accepted else "saved_pending_moderation" if assigned else "not_applied")
        return 0 if assigned else 1
    except Exception as error:
        report["errors"].append(clean(error))
        report["status"] = "requires_review" if report["mutation_attempts_this_run"] else "blocked_no_write"
        return 1
    finally:
        report["finished_at_utc"] = now()
        save()
        print("Sitelink description:", mode, report["status"])


if __name__ == "__main__":
    sys.exit(run())
