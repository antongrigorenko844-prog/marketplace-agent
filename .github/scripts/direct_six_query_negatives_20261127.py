"""Apply six owner-approved query exclusions; no other Direct mutations."""
import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from direct_mass_negatives_apply_20261120 import (
    Client, campaign_read, phrases, stable, atomic_save, now, CID, GROUP_GET, ADS_GET,
)
import direct_preflight_20261120 as reader
import direct_two_groups_parts_negatives_20261122 as group_writer
from direct_semantic_filter_20261124 import tokens, overlap, cost, PROTECTED

GID = 5828476541
OPERATION = "owner-approved-six-query-negatives-20261127"
OUT = Path("data/direct_six_query_negatives_20261127_result.json")
SOURCE = Path("data/direct_traffic_quality/2026-11-27-45861454649/targeted_supplement.json")
CAMPAIGN_TERMS = [
    '"авито запчасти +для авто"',
    '"контрактные коробки дсг"',
    '"разборка запчастей б у"',
    '"мехатроник +на дсг 09g"',
    '"клонирование платы мехатроника dsg 7"',
]
GROUP_TERMS = ["+какое масло заливать +в мехатроник"]
FEED = Path("feeds/dq200_candidate.yml")


def require(ok, message):
    if not ok:
        raise RuntimeError(message)


def fingerprint(obj):
    return hashlib.sha256(json.dumps(obj, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def collection(api, service, params, key, count):
    result = api(service, "get", params)
    rows = result.get(key)
    require(not result.get("LimitedBy") and isinstance(rows, list) and len(rows) == count,
            "Unexpected " + service + " inventory")
    require(all(x.get("CampaignId") == CID for x in rows), "Cross-campaign data")
    return sorted(rows, key=lambda x: x["Id"])


def snapshot(api):
    c = campaign_read(api)
    g = collection(api, "adgroups", GROUP_GET, "AdGroups", 6)
    a = collection(api, "ads", ADS_GET, "Ads", 8)
    k = sorted(reader.keywords(), key=lambda x: x["Id"])
    require(len(k) == 272 and sum(x["Keyword"] != "---autotargeting" for x in k) == 266,
            "Keyword inventory changed")
    require(c["State"] == "ON" and c["Name"] == reader.NAME, "Campaign identity/state changed")
    strategy = c["UnifiedCampaign"]["BiddingStrategy"]
    require(strategy["Search"]["BiddingStrategyType"] == "HIGHEST_POSITION"
            and strategy["Network"]["BiddingStrategyType"] == "SERVING_OFF"
            and strategy["Search"]["HighestPosition"].get("WeeklySpendLimit") == 4000000000,
            "Campaign budget/strategy changed")
    require(len([x for x in g if x["Id"] == GID]) == 1, "Target group missing")
    require(target_group({"groups": g})["Name"] == "DQ200 | Мехатроники общий запрос | Поиск",
            "Target group identity changed")
    return {"campaign": c, "groups": g, "keywords": k, "ads": a,
            "feed_file_sha256": hashlib.sha256(FEED.read_bytes()).hexdigest()}


def target_group(s):
    return next(x for x in s["groups"] if x["Id"] == GID)


def lists(s):
    return {"campaign": phrases(s["campaign"]), "group": phrases(target_group(s))}


def protected(s):
    obj = deepcopy(s)
    obj["campaign"].pop("NegativeKeywords", None)
    target_group(obj).pop("NegativeKeywords", None)
    return stable(obj)


def state_matches(s, before, expected):
    return (protected(s) == protected(before)
            and all(set(lists(s)[key]) == set(expected[key]) for key in ("campaign", "group")))


def source_fields_match(actual, expected):
    if isinstance(expected, dict):
        return isinstance(actual, dict) and all(
            key in actual and source_fields_match(actual[key], value)
            for key, value in expected.items() if key != "Status")
    return stable(actual) == stable(expected)


def checkpoint(j):
    atomic_save(OUT, j)
    subprocess.run(["bash", ".github/scripts/save_data.sh", str(OUT)], check=True, timeout=180)


def validate_source(current):
    source = json.loads(SOURCE.read_text())
    require(source.get("status") == "complete" and source.get("campaign_id") == CID,
            "Fresh approved source missing")
    cfg = source["sections"]["config_before"]["data"]
    previous = phrases(cfg["campaign"])
    old_group = next(x for x in cfg["groups"] if x["Id"] == GID)
    require(len(previous) == 683 and not phrases(old_group), "Reviewed baseline differs")
    require(set(lists(current)["campaign"]) == set(previous)
            and lists(current)["group"] == [], "Live negative baseline changed")
    require(source_fields_match(current["campaign"], cfg["campaign"]),
            "Campaign differs from reviewed source fields")
    require(current["feed_file_sha256"] == cfg["production_feed_sha256"],
            "Production feed changed")
    manual = [x for x in current["keywords"] if x["Keyword"] != "---autotargeting"]
    for scope, additions in (("campaign", CAMPAIGN_TERMS), ("group", GROUP_TERMS)):
        existing = lists(current)[scope]
        relevant = [x["Keyword"] for x in manual if scope == "campaign" or x["AdGroupId"] == GID]
        for phrase in additions:
            require(1 <= len(tokens(phrase)) <= 7 and phrase not in existing, "Invalid/duplicate phrase")
            require(not any(overlap(phrase, query) for query in relevant + PROTECTED),
                    "Protected query or manual keyword conflict")
        require(cost(existing + additions) <= (19000 if scope == "campaign" else 4000),
                "Negative phrase length limit")


def update_one(api, scope, values):
    if scope == "campaign":
        payload = {"Campaigns": [{"Id": CID, "NegativeKeywords": {"Items": values}}]}
        api.allowed_update = payload
        response = api("campaigns", "update", payload)
        ident = CID
    else:
        payload = {"AdGroups": [{"Id": GID, "NegativeKeywords": {"Items": values} if values else None}]}
        group_writer.UPDATE = payload
        response = group_writer.api("adgroups", "update", payload)
        ident = GID
    rows = response.get("UpdateResults", [])
    safe = [{"Id": x.get("Id"), "error_codes": [e.get("Code") for e in x.get("Errors", [])],
             "warning_codes": [e.get("Code") for e in x.get("Warnings", [])]} for x in rows]
    require(len(rows) == 1 and rows[0].get("Id") == ident and not rows[0].get("Errors"),
            "Update result did not confirm exactly one requested object: " + json.dumps(safe))
    return safe


def run(mode):
    require(os.getenv("GITHUB_REF") == "refs/heads/main", "Main branch only")
    api = Client("apply" if mode != "rollback-own" else "rollback_new")
    j = json.loads(OUT.read_text()) if OUT.exists() else None
    if j:
        require(j.get("operation") == OPERATION and j.get("campaign_id") == CID
                and j.get("campaign_terms") == CAMPAIGN_TERMS and j.get("group_terms") == GROUP_TERMS,
                "Saved operation scope changed")
    else:
        require(mode == "apply", "No existing operation proof")
        j = {"operation": OPERATION, "campaign_id": CID, "group_id": GID,
             "campaign_terms": CAMPAIGN_TERMS, "group_terms": GROUP_TERMS,
             "owner_authorized": True, "started_utc": now(), "status": "preflight", "phases": {}}
    try:
        current = snapshot(api)
        if "before" not in j:
            validate_source(current)
            j["before"] = current
            original = lists(current)
            j["target"] = {"campaign": original["campaign"] + CAMPAIGN_TERMS,
                           "group": original["group"] + GROUP_TERMS}
            j["protected_before_sha256"] = fingerprint(protected(current))
            j["rollback_command"] = "python .github/scripts/direct_six_query_negatives_20261127.py --mode rollback-own"
            checkpoint(j)
        original = lists(j["before"])
        require(protected(current) == protected(j["before"]), "Protected fields changed since baseline")
        if mode == "readback":
            require(state_matches(current, j["before"], j["target"]), "Six exclusions not all verified")
            j["status"] = "applied_verified"
        elif mode == "rollback-own":
            # Remove only this operation's phrases; preserve any later additions.
            require(any(p.get("attempted") for p in j["phases"].values()), "No own write proof for rollback")
            for scope, additions in (("group", GROUP_TERMS), ("campaign", CAMPAIGN_TERMS)):
                if not j["phases"].get(scope, {}).get("attempted"):
                    continue
                live = lists(current)
                owned = set(additions) - set(original[scope])
                target = [x for x in live[scope] if x not in owned]
                if target == live[scope]:
                    continue
                phase = j.setdefault("rollback_phases", {}).setdefault(scope, {})
                require(not phase.get("attempted"), "Prior rollback outcome requires review")
                expected = deepcopy(live)
                expected[scope] = target
                phase.update(attempted=True, started_utc=now(), before=live[scope], target=target)
                checkpoint(j)
                latest = snapshot(api)
                require(state_matches(latest, current, live), "Concurrent change before rollback")
                try:
                    phase["api_result"] = update_one(api, scope, target)
                except Exception as exc:
                    phase["api_error"] = reader.scrub(exc)
                observed = snapshot(api)
                require(state_matches(observed, current, expected), "Rollback readback differs; no retry")
                phase.update(verified=True, verified_utc=now())
                current = observed
                atomic_save(OUT, j)
            j["status"] = "own_additions_removed_verified"
        else:
            for scope in ("campaign", "group"):
                live = lists(current)
                desired = j["target"][scope]
                phase = j["phases"].setdefault(scope, {})
                if set(live[scope]) == set(desired):
                    phase.update(verified=True, verified_utc=now())
                    continue
                require(set(live[scope]) == set(original[scope]), "Unexpected live negatives: " + scope)
                require(not phase.get("attempted"), "Prior write outcome requires review; no blind retry")
                expected = deepcopy(live)
                expected[scope] = desired
                phase.update(attempted=True, started_utc=now(), before=live[scope], target=desired)
                checkpoint(j)
                latest = snapshot(api)
                require(state_matches(latest, current, live), "Concurrent change immediately before write")
                try:
                    phase["api_result"] = update_one(api, scope, desired)
                except Exception as exc:
                    phase["api_error"] = reader.scrub(exc)
                observed = snapshot(api)
                require(state_matches(observed, current, expected), "Readback differs; no blind retry")
                phase.update(verified=True, verified_utc=now())
                current = observed
                atomic_save(OUT, j)
            require(state_matches(current, j["before"], j["target"]), "Final exact readback differs")
            j["status"] = "applied_verified"
        j["after"] = current
        j["verification"] = {
            "protected_fields_unchanged": protected(current) == protected(j["before"]),
            "campaign_negative_count": len(lists(current)["campaign"]),
            "target_group_negative_count": len(lists(current)["group"]),
            "campaign_terms_present": all(x in lists(current)["campaign"] for x in CAMPAIGN_TERMS),
            "group_term_present": all(x in lists(current)["group"] for x in GROUP_TERMS),
            "all_original_campaign_negatives_preserved": set(original["campaign"]) <= set(lists(current)["campaign"]),
            "all_original_group_negatives_preserved": set(original["group"]) <= set(lists(current)["group"]),
            "protected_after_sha256": fingerprint(protected(current)),
        }
        j.pop("error", None)
    except Exception as exc:
        j.update(status="blocked_or_unverified", error=reader.scrub(exc))
    finally:
        j["finished_utc"] = now()
        atomic_save(OUT, j)
        print(json.dumps({k: j.get(k) for k in ("status", "verification", "error")}, ensure_ascii=False))
    return 0 if j["status"] in ("applied_verified", "own_additions_removed_verified") else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("apply", "readback", "rollback-own"), default="readback")
    sys.exit(run(parser.parse_args().mode))

