"""Add exactly 18 approved manual keywords; no other Direct writes.

Reuse the reviewed transport/read helpers, never its mutation/checkpoint code.
The prior two-keyword reports and journal are read-only sources.
"""
from copy import deepcopy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

CORE_PATH = Path(__file__).with_name("apply_two_keywords_20261120.py")
CORE_SHA = "c6c52c7337ad3991fb40a76cf9edb6f667d6dabb5072f7e103a07e510cea466b"
if hashlib.sha256(CORE_PATH.read_bytes()).hexdigest() != CORE_SHA:
    raise RuntimeError("Reviewed read-only helper version changed")
spec = importlib.util.spec_from_file_location("reviewed_direct_core", CORE_PATH)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

PLAN = Path("data/direct_next18_keywords_plan_20261120.json")
PLAN_SHA = "ba332983f91b5b7c3c834cb51090c55c072dd3c9ee0633a8a41b8ff0b71cd379"
MARKER = Path("data/direct_next18_keywords_request_20261120.txt")
REPORT = Path("data/direct_next18_keywords_report_20261120.json")
JOURNAL = Path("data/direct_next18_keywords_journal_20261120.json")
SOURCE = Path("data/direct_two_keywords_apply_20261120.json")
SOURCE_AFTER_SHA = "936c1eafe29ed0253f5c914f3952e786f296b7ae34df51acfbec3d9770695b59"
GROUP_IDS = {5828476541, 5826320196, 5826183946, 5826171512}
PENDING_AD = 1927488453443618167


def checkpoint_git():
    """Durably save only the NEW operation paths before a single API write."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Live write requires the approved Actions checkpoint")
    def git(*args):
        try:
            p = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60, check=False)
        except Exception:
            raise RuntimeError("New journal checkpoint failed; API write not attempted") from None
        if p.returncode:
            raise RuntimeError("New journal checkpoint failed; API write not attempted")
        return p.stdout.strip()
    git("add", "--", str(REPORT), str(JOURNAL))
    staged = set(filter(None, git("diff", "--cached", "--name-only").splitlines()))
    if not staged or not staged <= {str(REPORT), str(JOURNAL)}:
        raise RuntimeError("Checkpoint staged paths outside new report/journal")
    git("commit", "-m", "Checkpoint approved next18 keyword add [skip ci]")
    git("pull", "--rebase", "origin", "main")
    git("push", "origin", "HEAD:main")


class Client(core.Client):
    def mutate(self, *args, **kwargs):
        raise RuntimeError("Previous two-keyword mutation API is disabled")

    def add_once(self, additions, before_ids):
        if self.mode != "apply" or self.journal is None or self.journal.get("operation") is not None:
            raise RuntimeError("No write allowed or prior add intent exists; no blind retry")
        params = {"Keywords": additions}
        if params != self.plan["write_request"]["params"] or len(additions) != 18:
            raise RuntimeError("Keywords.add payload differs from approved exact18")
        op = {"service": "keywords", "method": "add", "params": deepcopy(params), "before_keyword_ids": sorted(before_ids),
              "intent_at_utc": core.now(), "outcome": "intent_saved_before_network"}
        self.journal["operation"] = op
        core.save(JOURNAL, self.journal)
        core.save(REPORT, self.report)
        checkpoint_git()
        self.report["api_write_attempts_this_run"] += 1
        try:
            result = self.wire("keywords", "add", params)
        except Exception as error:
            op.update(outcome="indeterminate_or_api_error", error=core.clean(str(error)), response_at_utc=core.now())
            core.save(JOURNAL, self.journal)
            raise
        op.update(outcome="response_received", result=core.clean(result), response_at_utc=core.now())
        self.report["add_results"] = core.clean(result.get("AddResults"))
        core.save(JOURNAL, self.journal)
        core.save(REPORT, self.report)
        # Per-row errors/warnings are retained; always read back actual account state.
        if not isinstance(result.get("AddResults"), list) or len(result["AddResults"]) != 18:
            self.report.setdefault("response_warnings", []).append("Unexpected AddResults shape; actual readback decides outcome")
        return result


def counts(snap):
    return {"groups": len(snap["groups"]), "manual_keywords": sum(k["Keyword"] != "---autotargeting" for k in snap["keywords"]),
            "autotargeting": sum(k["Keyword"] == "---autotargeting" for k in snap["keywords"]), "ads": len(snap["ads"]),
            "campaign_negatives": len(core.items(snap["campaign"].get("NegativeKeywords")))}


def preserved_differences(before, after):
    issues, natural = [], []
    for field in ("campaign", "sitelink_set"):
        if core.stable(before[field]) != core.stable(after[field]):
            issues.append({"object": field, "reason": "Previously read settings changed"})
    for kind in ("groups", "ads", "keywords"):
        old, current = core.by_id(before[kind]), core.by_id(after[kind])
        if kind != "keywords" and set(old) != set(current):
            issues.append({"object": kind, "reason": "IDs changed", "missing": sorted(set(old)-set(current)), "extra": sorted(set(current)-set(old))})
        for ident, item in old.items():
            if ident not in current:
                issues.append({"object": kind, "Id": ident, "reason": "Existing object missing"})
                continue
            expected = deepcopy(item)
            actual = current[ident]
            if (kind == "ads" and ident == PENDING_AD and item.get("State") == "OFF" and item.get("Status") == "MODERATION"
                    and actual.get("State") == "ON" and actual.get("Status") in {"PREACCEPTED", "ACCEPTED"}):
                expected["State"] = "ON"
                natural.append({"ad_id_as_string": str(ident), "transition": "OFF/MODERATION -> ON/accepted", "api_write": False})
            if core.stable(expected) != core.stable(actual):
                issues.append({"object": kind, "Id": ident, "reason": "Previously read fields changed"})
    return issues, natural


def evaluate(before, snap, additions):
    issues, natural = preserved_differences(before, snap)
    old_ids = set(core.by_id(before["keywords"]))
    extra = [k for k in snap["keywords"] if k["Id"] not in old_ids]
    expected = {(k["AdGroupId"], k["Keyword"]): k for k in additions}
    matched, unexpected, duplicate = {}, [], []
    for k in extra:
        key = (k.get("AdGroupId"), k.get("Keyword"))
        if (key not in expected or k.get("CampaignId") != core.CID or k.get("Bid") != 50000000
                or k.get("Keyword") == "---autotargeting"):
            unexpected.append(k)
        elif key in matched:
            duplicate.append(k)
        else:
            matched[key] = k
    missing = [k for k in additions if (k["AdGroupId"], k["Keyword"]) not in matched]
    exact = not issues and not unexpected and not duplicate and not missing and len(extra) == 18
    statuses = [{"Id": k["Id"], "AdGroupId": k["AdGroupId"], "Keyword": k["Keyword"], "State": k.get("State"), "Status": k.get("Status")}
                for k in matched.values()]
    return {"existing_objects_unchanged": not issues, "existing_object_differences": issues,
            "natural_moderation_transitions": natural, "counts": counts(snap), "actual_new_keywords": extra,
            "actual_positive_phrases_for_review_only": [{"Id": k["Id"], "positive_phrase": re.split(r"\s+-", k.get("Keyword", ""), maxsplit=1)[0]} for k in extra],
            "missing": missing, "unexpected": unexpected, "duplicates": duplicate,
            "all18_exact_saved": exact, "all18_keyword_states_on_accepted": exact and all(k.get("State") == "ON" and k.get("Status") == "ACCEPTED" for k in matched.values()),
            "new_keyword_statuses": statuses,
            "new_keywords_rejected_or_stopped": [k for k in statuses if k["Status"] == "REJECTED" or k["State"] in {"SUSPENDED", "ARCHIVED", "DELETED"}],
            "ad_states_for_context": [{"Id_as_string": str(a["Id"]), "AdGroupId": a["AdGroupId"], "State": a.get("State"), "Status": a.get("Status")} for a in snap["ads"]],
            "interpretation": "Exact text/group/Bid required. Positive phrase is diagnostic only; normalization is not silently accepted. Keyword acceptance does not prove ad impressions or paid sales."}


def validate(marker, plan, plan_sha, source, journal):
    if (set(marker) != {"mode", "request_id", "plan_sha256"} or marker["mode"] not in {"inspect", "apply", "check"}
            or not isinstance(marker["request_id"], str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", marker["request_id"])
            or marker["plan_sha256"] != PLAN_SHA or plan_sha != PLAN_SHA or plan.get("campaign_id") != core.CID
            or core.digest(source.get("after")) != SOURCE_AFTER_SHA or plan.get("source_after_sha256") != SOURCE_AFTER_SHA):
        raise RuntimeError("Marker/plan/canonical source identity failed")
    additions = plan["approved_additions"]
    if (len(additions) != 18 or len({(a["AdGroupId"], a["Keyword"]) for a in additions}) != 18
            or any(set(a) != {"Keyword", "AdGroupId", "Bid"} or a["AdGroupId"] not in GROUP_IDS or a["Bid"] != 50000000
                   or not isinstance(a["Keyword"], str) or a["Keyword"] in {"---autotargeting", "мехатроник dq200", "0am927769d"} for a in additions)
            or plan["write_request"] != {"endpoint": core.BASE + "keywords", "method": "add", "params": {"Keywords": additions}}):
        raise RuntimeError("Exact18 payload scope failed")
    if journal is not None and (journal.get("version") != 1 or journal.get("plan_sha256") != PLAN_SHA
                               or journal.get("campaign_id") != core.CID or core.digest(journal.get("baseline")) != journal.get("baseline_sha256")):
        raise RuntimeError("Prior journal integrity failed; it is not overwritten")
    if journal is not None:
        if counts(journal["baseline"]) != plan["expected_before"]:
            raise RuntimeError("Prior journal baseline counts changed")
        op = journal.get("operation")
        if op is not None and (op.get("service") != "keywords" or op.get("method") != "add"
                               or op.get("params") != {"Keywords": additions}
                               or op.get("before_keyword_ids") != sorted(core.by_id(journal["baseline"]["keywords"]))):
            raise RuntimeError("Prior add intent differs from exact approved18")
    return additions


def main():
    report = {"campaign_id": core.CID, "started_at_utc": core.now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
              "plan_sha256": PLAN_SHA, "api_write_attempts_this_run": 0, "errors": [], "status": "started"}
    api = None
    try:
        journal = core.read_json(JOURNAL)[0] if JOURNAL.exists() else None
        marker, _ = core.read_json(MARKER)
        plan, plan_sha = core.read_json(PLAN)
        source, _ = core.read_json(SOURCE)
        additions = validate(marker, plan, plan_sha, source, journal)
        report.update(mode=marker["mode"], request_id=marker["request_id"])
        api = Client(marker["mode"], plan, report, journal)
        snap = core.snapshot(api)
        report["initial_readback"] = snap
        if journal is None:
            differences, natural = preserved_differences(source["after"], snap)
            report["initial_source_differences"] = differences
            report["initial_natural_moderation_transitions"] = natural
            if differences or counts(snap) != plan["expected_before"]:
                raise RuntimeError("Fresh baseline differs from 41/4/4/6/130 approved source")
            existing_pairs = {(k["AdGroupId"], k["Keyword"].strip().casefold()) for k in snap["keywords"]}
            if any((a["AdGroupId"], a["Keyword"].casefold()) in existing_pairs for a in additions):
                raise RuntimeError("An approved literal phrase already exists; no blind duplicate")
            if marker["mode"] != "inspect":
                raise RuntimeError("Fresh inspect journal required before first apply/check")
            journal = {"version": 1, "campaign_id": core.CID, "plan_sha256": PLAN_SHA, "created_at_utc": core.now(),
                       "baseline": snap, "baseline_sha256": core.digest(snap), "operation": None}
            api.journal = journal
            core.save(JOURNAL, journal)
        v = evaluate(api.journal["baseline"], snap, additions)
        report["before_verification"] = v
        if not v["existing_objects_unchanged"]:
            raise RuntimeError("Existing campaign/groups/ads/keywords changed; no write")
        if v["actual_new_keywords"] and api.journal.get("operation") is None:
            raise RuntimeError("Unowned keyword additions since inspect; no write")
        if marker["mode"] == "apply" and not v["all18_exact_saved"]:
            if v["actual_new_keywords"]:
                raise RuntimeError("Partial/unexpected additions; do not repeat the18-row batch")
            api.add_once(additions, core.by_id(snap["keywords"]))
            snap = core.snapshot(api)
        report["after"] = snap
        report["verification"] = evaluate(api.journal["baseline"], snap, additions)
        v = report["verification"]
        if v["all18_exact_saved"] and not v["new_keywords_rejected_or_stopped"]:
            report["status"] = "applied_verified_keywords_on_accepted" if v["all18_keyword_states_on_accepted"] else "saved_exact18_waiting_keyword_moderation"
            api.journal["readback_verified_at_utc"] = core.now()
            api.journal["verified_keyword_ids"] = [k["Id"] for k in v["actual_new_keywords"]]
            core.save(JOURNAL, api.journal)
            code = 0
        elif marker["mode"] == "inspect" and api.journal.get("operation") is None and not v["actual_new_keywords"]:
            report["status"] = "inspect_verified_no_api_write"
            code = 0
        else:
            report["status"] = "partial_normalized_or_rejected_requires_review"
            code = 1
    except Exception as error:
        report["errors"].append(core.clean(str(error)))
        report["status"] = "blocked_or_partial_no_blind_retry"
        code = 1
        if api is not None and api.journal is not None and api.journal.get("operation") is not None:
            try:
                snap = core.snapshot(api)
                report["error_readback"] = snap
                report["verification"] = evaluate(api.journal["baseline"], snap, api.plan["approved_additions"])
                v = report["verification"]
                if v["all18_exact_saved"] and not v["new_keywords_rejected_or_stopped"]:
                    report["status"] = "exact18_verified_by_readback_after_uncertain_response"
                    api.journal["readback_verified_at_utc"] = core.now()
                    api.journal["verified_keyword_ids"] = [k["Id"] for k in v["actual_new_keywords"]]
                    core.save(JOURNAL, api.journal)
                    code = 0
            except Exception as read_error:
                report["error_readback_error"] = core.clean(str(read_error))
    finally:
        report["finished_at_utc"] = core.now()
        if api is not None and api.journal is not None:
            report["journal_operation"] = deepcopy(api.journal.get("operation"))
        core.save(REPORT, core.clean(report))
    print(json.dumps({"status": report["status"], "api_write_attempts_this_run": report["api_write_attempts_this_run"], "errors": report["errors"]}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
