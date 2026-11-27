"""Append six reviewed campaign negatives, with persistent intent and no blind retry.

Only campaigns.update / NegativeKeywords is writable. Existing API allowlists and
legacy operation locks are not modified. check/readback perform no API mutations.
Rollback removes only this operation's additions from its own fresh live baseline.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from direct_mass_negatives_apply_20261120 import (
    Client, CAMPAIGN_GET, ADS_GET, CID, atomic_save, now, phrases,
    stable, jhash, phrase_hash, read_json,
)
import direct_preflight_20261120 as reader
from direct_neutral_ad_copy_20261120 import (
    Client as AuxiliaryReadClient, FEED_GET, SETS_GET,
)
from direct_semantic_filter_20261124 import tokens, overlap, cost, PROTECTED

OPERATION = "reviewed-six-campaign-negatives-20261127"
OUT = Path("data/direct_semantics_20261127/applied_patch.json")
PLAN = Path("data/direct_semantics_20261127/reviewed_campaign_patch.json")
SOURCE = Path("data/direct_semantics_20261127/live_45979776426/inventory.json")
PPC_REVIEW = Path("data/direct_semantics_20261127/approved_candidate.json")
PRODUCT_REVIEW = Path("data/direct_semantics_20261127/final_independent_review.json")
FEED = Path("feeds/dq200_candidate.yml")
SOURCE_SHA = "e30f892d0d523814acb3ef03966bf7e8f240dc3b0cf07c47285fe25f4e5130c5"
SOURCE_FINISHED = "2026-11-27T10:16:49.550210+00:00"
ADDITIONS = [
    "audi a7", "ауди a7", "audi а7", "ауди а7",
    '"мехатроник пассат сс dq250"', '"мехатроник пассат сс dq 250"',
]
PRIOR_CAMPAIGN = [
    '"авито запчасти +для авто"', '"контрактные коробки дсг"',
    '"разборка запчастей б у"', '"мехатроник +на дсг 09g"',
    '"клонирование платы мехатроника dsg 7"',
]
PRIOR_GROUP = "+какое масло заливать +в мехатроник"
PRIOR_GID = 5828476541
COMPONENT_ADS = {1927493554789861877, 1927493554789861878}
FID = 4061297
FULL_CAMPAIGN_GET = deepcopy(CAMPAIGN_GET)
FULL_CAMPAIGN_GET["FieldNames"].append("StatusPayment")
FULL_GROUP_GET = {
    "SelectionCriteria": {"CampaignIds": [CID]},
    "FieldNames": ["Id", "Name", "CampaignId", "Status", "Type", "Subtype",
                   "RegionIds", "RestrictedRegionIds", "NegativeKeywords",
                   "NegativeKeywordSharedSetIds", "TrackingParams"],
    "UnifiedAdGroupFieldNames": ["OfferRetargeting"], "Page": {"Limit": 1000},
}
FULL_KEYWORD_GET = {
    "SelectionCriteria": {"CampaignIds": [CID]},
    "FieldNames": ["Id", "CampaignId", "AdGroupId", "Keyword", "State", "Status",
                   "ServingStatus", "Bid", "ContextBid", "StrategyPriority",
                   "AutotargetingSearchBidIsAuto", "UserParam1", "UserParam2"],
    "AutotargetingSettingsCategoriesFieldNames": ["Exact", "Narrow", "Alternative", "Accessory", "Broader"],
    "AutotargetingSettingsBrandOptionsFieldNames": ["WithoutBrands", "WithAdvertiserBrand", "WithCompetitorsBrand"],
    "Page": {"Limit": 1000},
}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def indexed(rows, campaign_scoped=False):
    require(isinstance(rows, list) and all(isinstance(r, dict) and "Id" in r for r in rows),
            "Invalid collection")
    require(len({r["Id"] for r in rows}) == len(rows), "Duplicate API ID")
    if campaign_scoped:
        require(all(r.get("CampaignId") == CID for r in rows), "Cross-campaign data")
    return sorted(rows, key=lambda r: r["Id"])


def api_collection(api, service, params, key):
    response = api(service, "get", params)
    require("LimitedBy" not in response, "Unexpected paginated " + service)
    return indexed(response.get(key), campaign_scoped=service == "ads")


def snapshot(api):
    # Reuse established GET clients; do not widen any legacy allowlist.
    campaigns = reader.get("campaigns", FULL_CAMPAIGN_GET, "Campaigns")
    require(len(campaigns) == 1 and campaigns[0].get("Id") == CID,
            "Unexpected campaign identity")
    campaign = campaigns[0]
    require(campaign.get("Type") == "UNIFIED_CAMPAIGN", "Unexpected campaign type")
    groups = indexed(reader.get("adgroups", FULL_GROUP_GET, "AdGroups"), True)
    keywords = indexed(reader.get("keywords", FULL_KEYWORD_GET, "Keywords"), True)
    ads = api_collection(api, "ads", ADS_GET, "Ads")
    auxiliary = AuxiliaryReadClient("check")  # This mode cannot authorize mutations.
    feeds = api_collection(auxiliary, "feeds", FEED_GET, "Feeds")
    require(len(feeds) == 1 and feeds[0].get("Id") == FID, "Unexpected feed identity")
    assigned = {value["SitelinkSetId"] for ad in ads for value in ad.values()
                if isinstance(value, dict) and value.get("SitelinkSetId")}
    sets = api_collection(auxiliary, "sitelinks", SETS_GET, "SitelinksSets")
    sets = [row for row in sets if row["Id"] in assigned]
    require({row["Id"] for row in sets} == assigned, "Assigned sitelinks missing")
    return {"campaign": campaign, "groups": groups, "keywords": keywords,
            "ads": ads, "feeds": feeds, "sitelinks": sets,
            "feed_file_sha256": hashlib.sha256(FEED.read_bytes()).hexdigest()}


def protected(state):
    result = deepcopy(state)
    # Deliberately retain EVERY group's NegativeKeywords, including the prior oil rule.
    result["campaign"].pop("NegativeKeywords", None)
    return stable(result)


def state_matches(actual, before, expected_negatives):
    return (protected(actual) == protected(before)
            and phrase_hash(phrases(actual["campaign"])) == phrase_hash(expected_negatives))


def source_fields_match(actual, expected):
    actual, expected = stable(actual), stable(expected)
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and source_fields_match(actual[key], value)
            for key, value in expected.items())
    if isinstance(expected, list):
        return (isinstance(actual, list) and len(actual) == len(expected)
                and all(source_fields_match(a, b) for a, b in zip(actual, expected)))
    return actual == expected


def section(source, name):
    value = source.get("sections", {}).get(name, {})
    require(value.get("status") == "ok" and isinstance(value.get("data"), list),
            "Incomplete source section: " + name)
    return value["data"]


def reviewed_inputs():
    plan = read_json(PLAN)[0]
    require(plan.get("status") == "reviewed_for_apply" and plan.get("campaign_id") == CID
            and plan.get("additions") == ADDITIONS and plan.get("removals") == [],
            "Reviewed plan is not the exact six-phrase append-only scope")
    require(plan.get("source_snapshot") == str(SOURCE)
            and plan.get("source_snapshot_finished_utc") == SOURCE_FINISHED
            and plan.get("source_campaign_negatives_sha256") == SOURCE_SHA
            and plan.get("expected_before_count") == 688 and plan.get("expected_after_count") == 694
            and plan.get("preserve_prior_approved_six") is True, "Reviewed baseline changed")
    require(plan.get("reviewers") == {"ppc": "ready", "product": "ready"}, "Both reviewers required")
    checks = plan.get("checks", {})
    require(checks.get("protected_cases_passed") is True
            and checks.get("manual_key_full_overlap_count") == 0
            and checks.get("duplicates_count") == 0
            and checks.get("ppc_cases_passed") == 30
            and checks.get("product_cases_passed") == 16
            and checks.get("raw_rows_reviewed") == 18684, "Reviewed collision/QA gate failed")
    require(plan.get("review_files") == [str(PPC_REVIEW), str(PRODUCT_REVIEW)], "Unexpected review files")
    ppc, product = read_json(PPC_REVIEW)[0], read_json(PRODUCT_REVIEW)[0]
    require(ppc.get("status") == "READY_independent_review_passed_no_external_writes"
            and ppc.get("campaign_id") == CID and ppc.get("append_only_expressions") == ADDITIONS
            and ppc.get("source_inventory_finished_utc") == SOURCE_FINISHED
            and ppc.get("manual_full_overlap_conflicts") == []
            and ppc.get("owner_approved_prior_six_preserved") is True,
            "PPC review does not support the exact patch")
    ppc_cases = ppc.get("protected_and_boundary_tests", [])
    product_cases = product.get("limited_corpus", [])
    require(len(ppc_cases) == 30 and all(c.get("pass") is True for c in ppc_cases),
            "PPC corpus did not pass 30/30")
    require(product.get("status") == "INDEPENDENT_REVIEW_READY_FOR_SIX_NARROW_CAMPAIGN_ADDITIONS_ONLY"
            and str(product.get("campaign_id")) == str(CID)
            and product.get("approved_additions") == ADDITIONS
            and product.get("source_finished_utc") == SOURCE_FINISHED
            and len(product_cases) == 16 and all(c.get("pass") is True for c in product_cases),
            "Independent product corpus did not pass 16/16")
    source = read_json(SOURCE)[0]
    require(source.get("status") == "complete" and not source.get("errors")
            and source.get("finished_utc") == SOURCE_FINISHED
            and source.get("campaign_configuration_unchanged_during_read") is True,
            "Source snapshot must be complete and stable")
    campaigns = [c for c in section(source, "campaigns") if c.get("Id") == CID]
    require(len(campaigns) == 1, "Source campaign missing")
    original = phrases(campaigns[0])
    require(len(original) == 688 and phrase_hash(original) == SOURCE_SHA
            and not (set(ADDITIONS) & set(original)), "Source negative baseline differs")
    require(phrase_hash(ppc.get("expected_before_negative_items", [])) == SOURCE_SHA,
            "PPC review was performed on another baseline")
    expected_payload = {"Campaigns": [{"Id": CID, "NegativeKeywords": {"Items": original + ADDITIONS}}]}
    reviewed_payload = ppc.get("params", {})
    require(ppc.get("service") == "campaigns" and ppc.get("method") == "update"
            and reviewed_payload == expected_payload, "PPC payload differs from exact reviewed append")
    return plan, source, {"plan_sha256": jhash(plan), "source_sha256": jhash(source),
                          "ppc_review_sha256": jhash(ppc), "product_review_sha256": jhash(product)}


def validate_initial(current, source):
    source_campaign = next(c for c in section(source, "campaigns") if c["Id"] == CID)
    live = phrases(current["campaign"])
    require(len(live) == 688 and phrase_hash(live) == SOURCE_SHA,
            "Fresh live negatives are not the reviewed 688 baseline")
    require(source_fields_match(current["campaign"], source_campaign), "Campaign drift from reviewed source")
    for name, expected_count in (("groups", 6), ("keywords", 272), ("ads", 8)):
        expected = indexed([row for row in section(source, name) if row.get("CampaignId") == CID], True)
        require(len(current[name]) == len(expected) == expected_count
                and source_fields_match(current[name], expected), "Reviewed " + name + " changed")
    expected_feeds = [f for f in section(source, "feeds") if f["Id"] == FID]
    require(len(expected_feeds) == 1 and source_fields_match(current["feeds"], expected_feeds),
            "Feed configuration changed")
    require(source_fields_match(current["sitelinks"], indexed(section(source, "sitelinks"))),
            "Assigned sitelinks changed")
    require(section(source, "negative_shared_sets") == [], "Reviewed shared-negative sets are not empty")
    campaign = current["campaign"]
    require(campaign.get("Name") == reader.NAME and campaign.get("State") == "ON", "Campaign state/name drift")
    strategy = campaign["UnifiedCampaign"]["BiddingStrategy"]
    require(strategy["Search"]["BiddingStrategyType"] == "HIGHEST_POSITION"
            and strategy["Network"]["BiddingStrategyType"] == "SERVING_OFF"
            and strategy["Search"]["HighestPosition"].get("WeeklySpendLimit") == 4000000000,
            "Campaign budget/strategy drift")
    manual = [k for k in current["keywords"] if k["Keyword"] != "---autotargeting"]
    require(len(manual) == 266 and len(current["keywords"]) - len(manual) == 6, "Keyword/auto count drift")
    require(all(next(a for a in current["ads"] if a["Id"] == aid)["State"] == "SUSPENDED"
                for aid in COMPONENT_ADS), "Component ad suspension changed")
    require(set(PRIOR_CAMPAIGN) <= set(live), "Prior approved campaign negatives missing")
    group = next(g for g in current["groups"] if g["Id"] == PRIOR_GID)
    require(PRIOR_GROUP in phrases(group), "Prior approved group negative missing")
    relevant = [k["Keyword"] for k in manual] + list(PROTECTED)
    require(all(1 <= len(tokens(term)) <= 7 and not any(overlap(term, q) for q in relevant)
                for term in ADDITIONS), "Manual keyword/protected-query collision")
    require(cost(live + ADDITIONS) <= 19000, "Negative character limit reserve exceeded")


def checkpoint(journal):
    atomic_save(OUT, journal)
    # A failed durable checkpoint blocks the API write. Only this journal is staged.
    subprocess.run(["bash", ".github/scripts/save_data.sh", str(OUT)], check=True, timeout=180)


def payload(values):
    return {"Campaigns": [{"Id": CID, "NegativeKeywords": {"Items": values}}]}


def update_once(api, values):
    request = payload(values)
    api.allowed_update = request
    result = api("campaigns", "update", request)
    rows = result.get("UpdateResults", [])
    safe = [{"Id": r.get("Id"), "error_codes": [e.get("Code") for e in r.get("Errors", [])],
             "warning_codes": [w.get("Code") for w in r.get("Warnings", [])]} for r in rows]
    require(len(rows) == 1 and rows[0].get("Id") == CID and not rows[0].get("Errors"),
            "Campaign update result is not exact success: " + json.dumps(safe))
    return safe


def validate_journal(journal, proof):
    require(journal.get("operation") == OPERATION and journal.get("campaign_id") == CID
            and journal.get("additions") == ADDITIONS and journal.get("proof") == proof,
            "Saved operation/review proof changed")
    before = journal["before"]
    require(phrase_hash(phrases(before["campaign"])) == SOURCE_SHA
            and journal.get("target") == phrases(before["campaign"]) + ADDITIONS
            and journal.get("protected_before_sha256") == jhash(protected(before)),
            "Saved before/target was tampered with")
    phase = journal.get("apply_phase", {})
    if phase.get("attempted"):
        require(phase.get("payload_sha256") == jhash(payload(journal["target"]))
                and phase.get("intent_utc"), "Missing exact own write-intent proof")


def execute_write(journal, phase, api, before, target, take_snapshot, persist_checkpoint):
    require(not phase.get("attempted"), "Prior write outcome requires review; no blind retry")
    phase.update(attempted=True, intent_utc=now(), payload_sha256=jhash(payload(target)),
                 before=deepcopy(before), target=list(target))
    persist_checkpoint(journal)  # durable before + exact intent MUST precede network mutation
    latest = take_snapshot()
    if not state_matches(latest, before, phrases(before["campaign"])):
        phase["aborted_before_api"] = True
        raise RuntimeError("Concurrent protected/negative change after checkpoint; no write")
    try:
        phase["api_result"] = update_once(api, target)
    except Exception as error:
        phase["api_error"] = reader.scrub(error)
    # Always read after a transport/API error; never retry the write in this run.
    observed = take_snapshot()
    journal["last_observed"] = observed
    require(state_matches(observed, before, target), "Write readback differs; no blind retry")
    phase.update(verified=True, verified_utc=now())
    return observed


def run(mode, api=None, take_snapshot=None, persist_checkpoint=None):
    require(mode in {"check", "apply", "readback", "rollback-own"}, "Invalid mode")
    require(os.getenv("GITHUB_REF") == "refs/heads/main", "Main branch only")
    api = api or Client("rollback_new" if mode == "rollback-own" else "apply" if mode == "apply" else "check")
    take_snapshot = take_snapshot or (lambda: snapshot(api))
    persist_checkpoint = persist_checkpoint or checkpoint
    journal = None
    success = False
    try:
        plan, source, proof = reviewed_inputs()
        journal = read_json(OUT)[0] if OUT.exists() else None
        current = take_snapshot()
        if journal is None:
            require(mode in {"check", "apply"}, "No saved own operation proof")
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(SOURCE_FINISHED)).total_seconds()
            require(0 <= age <= 86400, "Reviewed source is over 24 hours old or in the future")
            validate_initial(current, source)
            journal = {"operation": OPERATION, "campaign_id": CID, "additions": ADDITIONS,
                       "proof": proof, "started_utc": now(), "before": current,
                       "target": phrases(current["campaign"]) + ADDITIONS,
                       "protected_before_sha256": jhash(protected(current)), "apply_phase": {},
                       "rollback_command": "python .github/scripts/direct_semantics_patch_20261127.py --mode rollback-own"}
        validate_journal(journal, proof)
        phase = journal["apply_phase"]
        attempted = phase.get("attempted") is True and not phase.get("aborted_before_api")
        if mode == "rollback-own":
            require(attempted, "No own campaign write attempt for rollback")
            rollback = journal.setdefault("rollback_phase", {})
            live = phrases(current["campaign"])
            target = [term for term in live if term not in set(ADDITIONS)]
            if target == live:
                journal["status"] = "own_additions_absent_verified"
            else:
                if rollback.get("attempted"):
                    raise RuntimeError("Prior rollback outcome requires review; no blind retry")
                # Protect THIS fresh state, including unrelated later changes.
                current = execute_write(journal, rollback, api, current, target,
                                        take_snapshot, persist_checkpoint)
                journal["status"] = "own_additions_removed_verified"
            journal["rollback_verification"] = {
                "own_additions_absent": not (set(ADDITIONS) & set(phrases(current["campaign"]))),
                "preserves_later_unrelated_changes": True,
                "campaign_negative_count": len(phrases(current["campaign"])),
            }
        else:
            require(not journal.get("rollback_phase", {}).get("attempted"), "Operation rollback already attempted")
            require(protected(current) == protected(journal["before"]), "Protected fields changed since baseline")
            if attempted and state_matches(current, journal["before"], journal["target"]):
                phase.update(verified=True, verified_utc=now())
                journal["status"] = "applied_verified"
            else:
                require(not phase.get("attempted"), "Prior apply outcome requires readback/review; no blind retry")
                require(mode != "readback", "No own write-intent proof for readback")
                validate_initial(current, source)
                if mode == "check":
                    journal["status"] = "checked_ready_no_api_write"
                else:
                    current = execute_write(journal, phase, api, current, journal["target"],
                                            take_snapshot, persist_checkpoint)
                    journal["status"] = "applied_verified"
        journal["after"] = current
        journal["verification"] = {
            "campaign_negative_count": len(phrases(current["campaign"])),
            "all_six_present": set(ADDITIONS) <= set(phrases(current["campaign"])),
            "protected_fields_unchanged_from_apply_baseline": protected(current) == protected(journal["before"]),
            "protected_after_sha256": jhash(protected(current)),
            "manual_keywords": sum(k["Keyword"] != "---autotargeting" for k in current["keywords"]),
            "auto_conditions": sum(k["Keyword"] == "---autotargeting" for k in current["keywords"]),
            "prior_campaign_five_present": set(PRIOR_CAMPAIGN) <= set(phrases(current["campaign"])),
            "prior_group_rule_present": any(g["Id"] == PRIOR_GID and PRIOR_GROUP in phrases(g) for g in current["groups"]),
            "component_ad_states": {str(a["Id"]): a["State"] for a in current["ads"] if a["Id"] in COMPONENT_ADS},
        }
        journal.pop("error", None)
        success = True
    except Exception as error:
        if journal is None:
            # Never invent attempt proof when plan/source/preflight failed.
            print(json.dumps({"status": "blocked_no_write", "error": reader.scrub(error)}, ensure_ascii=False))
            return 1
        journal.update(status="blocked_or_unverified", error=reader.scrub(error))
    finally:
        if journal is not None:
            journal["finished_utc"] = now()
            journal["last_mode"] = mode
            atomic_save(OUT, journal)
            print(json.dumps({key: journal.get(key) for key in ("status", "verification", "error")}, ensure_ascii=False))
    return 0 if success else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("check", "apply", "readback", "rollback-own"), default="check")
    sys.exit(run(parser.parse_args().mode))
