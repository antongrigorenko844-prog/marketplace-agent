"""Authorized full-source import: preserve59, add207 requests, retain234-row proof.

Every write intent is durably committed before network. No blind replay of an
unknown mutation. API duplicate IDs are evidence, never a local semantic guess.
"""
from copy import deepcopy
import hashlib
import html
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.request
import urllib.error
import urllib.parse

CORE = Path(__file__).with_name("apply_two_keywords_20261120.py")
if hashlib.sha256(CORE.read_bytes()).hexdigest() != "c6c52c7337ad3991fb40a76cf9edb6f667d6dabb5072f7e103a07e510cea466b":
    raise RuntimeError("Reviewed read helper changed")
spec = importlib.util.spec_from_file_location("reviewed_direct_core", CORE)
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)
PLAN = Path("data/direct_all234_keywords_plan_20261120.json")
PLAN_SHA = "077f0b4c500c9f035f9e5c6feecf231fe7e0f4685090a876a8bab267353db3e8"
MARKER = Path("data/direct_all234_keywords_request_20261120.txt")
REPORT = Path("data/direct_all234_keywords_report_20261120.json")
JOURNAL = Path("data/direct_all234_keywords_journal_20261120.json")
SOURCE = Path("data/direct_next18_keywords_report_20261120.json")
SOURCE_AFTER_SHA = "ab8ffe773bf3b75033e9ce9f99f38c6f53aa062eb767b9d01eb303905e842a87"
PENDING_AD = 1927488453443618167


def checkpoint():
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RuntimeError("Live operations require Actions durable journal")
    def git(*args):
        try:
            p = subprocess.run(["git", *args], capture_output=True, text=True, timeout=60, check=False)
        except Exception:
            raise RuntimeError("Journal checkpoint failed before API write") from None
        if p.returncode:
            raise RuntimeError("Journal checkpoint failed before API write")
        return p.stdout.strip()
    git("add", "--", str(REPORT), str(JOURNAL))
    paths = set(git("diff", "--cached", "--name-only").splitlines())
    if not paths or not paths <= {str(REPORT), str(JOURNAL)}:
        raise RuntimeError("Checkpoint path scope failed")
    git("commit", "-m", "Checkpoint authorized full234 operation [skip ci]")
    git("pull", "--rebase", "origin", "main")
    git("push", "origin", "HEAD:main")


def counts(s):
    return {"groups": len(s["groups"]), "manual_keywords": sum(k["Keyword"] != "---autotargeting" for k in s["keywords"]),
            "autotargeting": sum(k["Keyword"] == "---autotargeting" for k in s["keywords"]),
            "ads": len(s["ads"]), "campaign_negatives": len(core.items(s["campaign"].get("NegativeKeywords")))}


def existing_differences(old, new):
    issues, natural = [], []
    for kind in ("campaign", "sitelink_set"):
        if core.stable(old[kind]) != core.stable(new[kind]):
            issues.append({"kind": kind, "reason": "Existing fields changed"})
    for kind in ("groups", "keywords", "ads"):
        current = core.by_id(new[kind])
        for ident, obj in core.by_id(old[kind]).items():
            actual = current.get(ident)
            expected = deepcopy(obj)
            if (kind == "ads" and ident == PENDING_AD and obj.get("State") == "OFF"
                    and obj.get("Status") == "MODERATION" and actual and actual.get("State") == "ON"
                    and actual.get("Status") in {"PREACCEPTED", "ACCEPTED"}):
                expected["State"] = "ON"
                natural.append({"ad_id_as_string": str(ident), "transition": "Moderation accepted; no API edit"})
            if core.stable(expected) != core.stable(actual):
                issues.append({"kind": kind, "id_as_string": str(ident), "reason": "Missing or changed old object"})
    return issues, natural


def result_rows(j, key, field):
    op = j["operations"].get(key)
    if not op:
        return None
    if op.get("outcome") != "response_received" or not isinstance(op.get("result", {}).get(field), list):
        raise RuntimeError("Indeterminate prior operation " + key + "; readback required, never replay")
    return op["result"][field]


def resolved_groups(plan, j):
    m = {g["code"]: g["id"] for g in plan["groups"] if g["id"] is not None}
    new = [g for g in plan["groups"] if g["id"] is None]
    rows = result_rows(j, "groups_add", "AddResults")
    if rows is not None:
        if len(rows) != len(new):
            raise RuntimeError("New-group result cardinality differs")
        for g, r in zip(new, rows):
            if r.get("Errors") or type(r.get("Id")) is not int:
                raise RuntimeError("Group add did not produce verified ID")
            m[g["code"]] = r["Id"]
    return m


def owned_autos(plan, j, snap):
    groups = resolved_groups(plan, j)
    out = []
    for g in plan["groups"]:
        if g["id"] is None and g["code"] in groups:
            a = [k for k in snap["keywords"] if k["AdGroupId"] == groups[g["code"]] and k["Keyword"] == "---autotargeting"]
            if len(a) != 1:
                raise RuntimeError("Expected one mandatory auto per owned new group")
            out.append(a[0])
    return out


def allowed_requests(plan, j, snap):
    new = [g for g in plan["groups"] if g["id"] is None]
    groups = resolved_groups(plan, j)
    allowed = {"groups_add": ("adgroups", "add", {"AdGroups": [
        {"Name": g["name"], "CampaignId": core.CID, "RegionIds": plan["regions"],
         "UnifiedAdGroup": {"OfferRetargeting": "NO"}} for g in new]})}
    if not all(g["code"] in groups for g in new):
        return allowed
    autos = owned_autos(plan, j, snap)
    allowed["autos_settings"] = ("keywords", "update", {"Keywords": [
        {"Id": a["Id"], "AutotargetingSettings": plan["autotargeting_settings"]} for a in autos]})
    allowed["autos_bids"] = ("keywordbids", "set", {"KeywordBids": [
        {"KeywordId": a["Id"], "SearchBid": plan["new_bid_micros"], "AutotargetingSearchBidIsAuto": "NO"} for a in autos]})
    allowed["keywords_add"] = ("keywords", "add", {"Keywords": [
        {"Keyword": k["keyword"], "AdGroupId": groups[k["group_code"]], "Bid": plan["new_bid_micros"]}
        for k in plan["items"] if k["action"] == "ADD_KEYWORD"]})
    allowed["ads_add"] = ("ads", "add", {"Ads": [
        {"AdGroupId": groups[g["code"]], "ResponsiveAd": g["ad"]} for g in new]})
    rows = result_rows(j, "ads_add", "AddResults")
    if rows is not None:
        if len(rows) != len(new) or any(r.get("Errors") or type(r.get("Id")) is not int for r in rows):
            raise RuntimeError("New-ad results invalid")
        allowed["ads_moderate"] = ("ads", "moderate", {"SelectionCriteria": {"Ids": [r["Id"] for r in rows]}})
    return allowed


class Client(core.Client):
    def mutate(self, *args, **kwargs):
        raise RuntimeError("Old operation methods disabled")

    @staticmethod
    def check_result(key, method, params, result):
        field = {"add": "AddResults", "update": "UpdateResults", "set": "SetResults", "moderate": "ModerateResults"}[method]
        rows = result.get(field)
        expected = len(params["SelectionCriteria"]["Ids"]) if method == "moderate" else len(next(iter(params.values())))
        if not isinstance(rows, list) or len(rows) != expected or any(r.get("Errors") for r in rows):
            raise RuntimeError(key + " partial/error response; keep row-level results and do not repeat batch")

    def perform(self, key, snap):
        if self.mode != "apply":
            raise RuntimeError("Read-only mode")
        allowed = allowed_requests(self.plan, self.journal, snap)
        if key not in allowed:
            raise RuntimeError("Write prerequisites or scope failed: " + key)
        service, method, params = allowed[key]
        previous = self.journal["operations"].get(key)
        if previous:
            if (previous["service"], previous["method"], previous["params"]) != (service, method, params):
                raise RuntimeError("Previously journaled payload changed")
            if previous.get("outcome") != "response_received":
                raise RuntimeError("Prior operation outcome unknown; no retry")
            self.check_result(key, method, params, previous["result"])
            return previous["result"]
        op = {"service": service, "method": method, "params": deepcopy(params), "intent_at_utc": core.now(),
              "outcome": "intent_saved_before_network"}
        self.journal["operations"][key] = op
        core.save(JOURNAL, self.journal)
        core.save(REPORT, self.report)
        checkpoint()
        self.report["api_write_attempts_this_run"] += 1
        try:
            result = self.wire(service, method, params)
        except Exception as e:
            op.update(outcome="indeterminate_or_error", error=core.clean(str(e)), response_at_utc=core.now())
            core.save(JOURNAL, self.journal)
            raise
        op.update(outcome="response_received", result=deepcopy(result), response_at_utc=core.now())
        core.save(JOURNAL, self.journal)
        core.save(REPORT, self.report)
        self.check_result(key, method, params, result)
        return result


def validate(marker, plan, sha, source, j):
    if (sha != PLAN_SHA or marker.get("plan_sha256") != PLAN_SHA or set(marker) != {"mode", "request_id", "plan_sha256"}
            or marker["mode"] not in {"inspect", "apply", "check"} or not re.fullmatch(r"[A-Za-z0-9_.-]{1,100}", marker["request_id"])
            or plan.get("campaign_id") != core.CID or core.digest(source["after"]) != SOURCE_AFTER_SHA
            or plan.get("source_after_sha256") != SOURCE_AFTER_SHA):
        raise RuntimeError("Plan, marker or59-source identity failed")
    items, rows = plan["items"], plan["source_mapping"]
    if (len(items) != 231 or len(rows) != 234 or len({k["key"] for k in items}) != 231
            or len({k["keyword"] for k in items}) != 231 or sorted(r["source_position"] for r in rows) != list(range(1,235))):
        raise RuntimeError("All234/231 source coverage failed")
    keyed = {k["key"]: k for k in items}
    for r in rows:
        k = keyed[r["item_key"]]
        if (re.split(r"\s+-", r["source_phrase_exact"], maxsplit=1)[0].strip() != r["positive_keyword"]
                or r["positive_keyword"] != k["keyword"] or r["group_code"] != k["group_code"]
                or r["source_position"] not in k["source_positions"]):
            raise RuntimeError("Source wording/operator preservation failed")
    expected = {"ADD_KEYWORD": 207, "EXISTING_UNCHANGED": 22, "EXISTING_ROUTED": 2}
    if {x: sum(k["action"] == x for k in items) for x in expected} != expected or any(k["action"] not in expected for k in items):
        raise RuntimeError("Expected207add/24preserve, no existing update")
    if any(re.search(r"(^|\s)-", k["keyword"]) or len(k["keyword"].split()) > 7 for k in items):
        raise RuntimeError("Invalid positive phrase")
    if plan["new_bid_micros"] != 50000000 or plan["regions"] != core.REGIONS or plan["autotargeting_settings"] != core.AUTO:
        raise RuntimeError("New bid/geo/auto scope differs")
    if {g["code"] for g in plan["groups"] if not g["id"]} != {"HYDRO", "SLEEVE"}:
        raise RuntimeError("Unexpected new groups")
    for g in plan["groups"]:
        if not g["id"] and (set(g["ad"]) != {"Titles", "Texts", "Href"} or len(g["ad"]["Titles"]) != 1
                            or len(g["ad"]["Texts"]) != 1 or len(g["ad"]["Titles"][0]) > 56
                            or len(g["ad"]["Texts"][0]) > 81 or g["ad"]["Href"] != g["landing"]
                            or not g["landing"].startswith("https://leoparts-akpp.ru/tproduct/")):
            raise RuntimeError("New ad content or landing scope failed")
    if j is not None and (j.get("plan_sha256") != PLAN_SHA or j.get("campaign_id") != core.CID
                          or core.digest(j.get("baseline")) != j.get("baseline_sha256")):
        raise RuntimeError("Existing journal integrity failed")


def routing_proof(plan, snap):
    keys = core.by_id(snap["keywords"])
    proof = []
    for k in plan["items"]:
        if k["action"] == "EXISTING_ROUTED":
            parent = keys.get(k["existing_id"], {})
            children = [keys.get(i, {}) for i in k["child_keyword_ids"]]
            if (parent.get("Keyword") != k["before_keyword"] or any(c.get("AdGroupId") != parent.get("AdGroupId")
                    or c.get("State") != "ON" or c.get("Status") != "ACCEPTED" for c in children)):
                raise RuntimeError("Existing parent/child routing proof changed")
            proof.append({"item_key": k["key"], "parent": parent, "children": children,
                          "note": "Existing inline partition preserved; not a literal unrestricted parent."})
    return proof


def ownership_guard(plan, j, snap):
    diffs, natural = existing_differences(j["baseline"], snap)
    if diffs:
        raise RuntimeError("Existing objects changed: " + json.dumps(diffs, ensure_ascii=False))
    groups = resolved_groups(plan, j)
    extra_groups = set(core.by_id(snap["groups"])) - set(core.by_id(j["baseline"]["groups"]))
    expected_groups = {groups[g["code"]] for g in plan["groups"] if g["id"] is None and g["code"] in groups}
    if extra_groups != expected_groups:
        raise RuntimeError("Unowned group change")
    for g in plan["groups"]:
        if g["id"] is None and g["code"] in groups:
            a = core.by_id(snap["groups"])[groups[g["code"]]]
            if (a["Name"] != g["name"] or a["RegionIds"] != plan["regions"] or a["Type"] != "UNIFIED_AD_GROUP"
                    or core.items(a.get("NegativeKeywords")) or core.items(a.get("NegativeKeywordSharedSetIds"))
                    or a.get("TrackingParams") not in (None, "")):
                raise RuntimeError("New group fields differ")
    allowed_keywords = {a["Id"] for a in owned_autos(plan, j, snap)}
    kr = result_rows(j, "keywords_add", "AddResults")
    if kr is not None:
        allowed_keywords.update(r["Id"] for r in kr if type(r.get("Id")) is int)
    oldk = set(core.by_id(j["baseline"]["keywords"]))
    newk = set(core.by_id(snap["keywords"])) - oldk
    if newk != allowed_keywords - oldk:
        raise RuntimeError("Unexpected or missing added-keyword IDs")
    ar = result_rows(j, "ads_add", "AddResults")
    allowed_ads = {r["Id"] for r in ar if type(r.get("Id")) is int} if ar else set()
    if set(core.by_id(snap["ads"])) - set(core.by_id(j["baseline"]["ads"])) != allowed_ads:
        raise RuntimeError("Unowned ad change")
    if ar:
        for g, r in zip([g for g in plan["groups"] if g["id"] is None], ar):
            a = core.by_id(snap["ads"])[r["Id"]]
            expected = {"Type": "RESPONSIVE_AD", "AdGroupId": groups[g["code"]], "Titles": g["ad"]["Titles"],
                        "Texts": g["ad"]["Texts"], "Href": g["ad"]["Href"], "SitelinkSetId": None}
            if core.ad_copy(a) != expected:
                raise RuntimeError("Owned new ad differs from exact plan")
    routing_proof(plan, snap)
    return natural


def verify(plan, j, snap):
    natural = ownership_guard(plan, j, snap)
    groups, keys = resolved_groups(plan, j), core.by_id(snap["keywords"])
    adds = [k for k in plan["items"] if k["action"] == "ADD_KEYWORD"]
    result = result_rows(j, "keywords_add", "AddResults")
    responses = dict(zip((k["key"] for k in adds), result or []))
    ledger = []
    for k in plan["items"]:
        r = responses.get(k["key"], {})
        ident = r.get("Id") if k["action"] == "ADD_KEYWORD" else k["existing_id"]
        actual = keys.get(ident)
        state = "not_submitted"
        issues = []
        if r.get("Errors"):
            state = "api_error"; issues.append(r["Errors"])
        elif actual:
            if actual.get("AdGroupId") != groups.get(k["group_code"]) or actual.get("CampaignId") != core.CID:
                issues.append("Wrong group/campaign")
            if actual.get("Keyword") == k["keyword"]:
                state = "existing_exact" if k["action"] == "EXISTING_UNCHANGED" else "saved_exact"
            elif k["action"] == "EXISTING_ROUTED" and actual.get("Keyword") == k["before_keyword"]:
                state = "existing_parent_with_verified_children"
            elif k["action"] == "ADD_KEYWORD" and r.get("Warnings"):
                state = "api_duplicate_or_normalized"
                if re.search(r"\s+-", actual.get("Keyword", "")):
                    state = "api_existing_duplicate_with_preserved_inline"
                    if ident not in core.by_id(j["baseline"]["keywords"]):
                        issues.append("Unexpected inline restriction on a newly created keyword")
                    parent_children = {58544149377: [58544149381, 58544149382, 58544149390],
                                       58545151245: [58545151246, 58545151247, 58545151251],
                                       58545169890: [58545169891, 58545169892]}
                    children = [keys.get(cid, {}) for cid in parent_children.get(ident, [])]
                    if not children or any(c.get("AdGroupId") != actual.get("AdGroupId") or c.get("State") != "ON"
                                            or c.get("Status") != "ACCEPTED" for c in children):
                        issues.append("Existing duplicate inline branches need verification")
            else:
                issues.append("Returned keyword differs without documented API warning")
                state = "needs_review"
            if k["action"] == "ADD_KEYWORD" and actual.get("Bid") != plan["new_bid_micros"]:
                issues.append("Bid differs")
            if k["action"] == "ADD_KEYWORD":
                actual_words = actual.get("Keyword", "").casefold().split()
                if any(op.casefold() not in actual_words for op in re.findall(r"[+!]\S+", k["keyword"])):
                    issues.append("Explicit source operator not preserved in returned keyword")
            if actual.get("Status") == "REJECTED" or actual.get("State") in {"SUSPENDED", "ARCHIVED", "DELETED"}:
                issues.append("Rejected or owner-stopped")
        elif ident:
            state = "missing_returned_id"; issues.append("Returned ID absent from fresh GET")
        ledger.append({"item_key": k["key"], "requested_positive": k["keyword"], "group_code": k["group_code"],
                       "group_id": groups.get(k["group_code"]), "source_positions": k["source_positions"],
                       "action": k["action"], "keyword_id": ident, "stored_keyword": actual.get("Keyword") if actual else None,
                       "result": state, "warnings": r.get("Warnings", []), "errors": issues,
                       "keyword_state": actual.get("State") if actual else None, "keyword_status": actual.get("Status") if actual else None})
    lm = {r["item_key"]: r for r in ledger}
    source = [{**r, "verification": lm[r["item_key"]]} for r in plan["source_mapping"]]
    pending = [r for r in ledger if r["result"] == "not_submitted" or r["errors"]]
    autos = owned_autos(plan, j, snap)
    auto_ok = len(autos) == 2 and all(a.get("AutotargetingSettings") == plan["autotargeting_settings"]
                and a.get("Bid") == plan["new_bid_micros"] and a.get("AutotargetingSearchBidIsAuto") == "NO" for a in autos)
    ads_result = result_rows(j, "ads_add", "AddResults")
    newads = [core.by_id(snap["ads"])[r["Id"]] for r in ads_result] if ads_result else []
    ads_sent = len(newads) == 2 and all(a.get("Status") in {"MODERATION", "PREACCEPTED", "ACCEPTED"}
                                       and a.get("State") not in {"SUSPENDED", "ARCHIVED", "DELETED"} for a in newads)
    return {"counts": counts(snap), "all_existing_objects_preserved": True, "natural_moderation_transitions": natural,
            "all234_source_rows_accounted": len(source) == 234, "all231_items_resolved": not pending,
            "all234_represented_and_new_ads_submitted": not pending and auto_ok and ads_sent,
            "unresolved": pending, "item_ledger": ledger, "source_ledger": source,
            "unique_source_keyword_ids": len({r["keyword_id"] for r in ledger if r["keyword_id"]}),
            "api_duplicate_or_normalized_items": [r for r in ledger if r["result"].startswith("api_")],
            "existing_routing_proof": routing_proof(plan, snap), "new_auto_settings_verified": auto_ok,
            "ad_states": [{"Id_as_string": str(a["Id"]), "AdGroupId": a["AdGroupId"], "State": a.get("State"), "Status": a.get("Status")} for a in snap["ads"]],
            "note": "Source mapping is API-object evidence, not proof of impressions, exact universal query matching or paid orders."}


def refresh(api):
    snap = core.snapshot(api)
    ownership_guard(api.plan, api.journal, snap)
    return snap


def landing_checks(plan):
    class SameSiteRedirect(urllib.request.HTTPRedirectHandler):
        max_redirections = 3
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            parsed = urllib.parse.urlsplit(newurl)
            if parsed.scheme != "https" or parsed.hostname not in {"leoparts-akpp.ru", "www.leoparts-akpp.ru"}:
                raise RuntimeError("Unexpected product landing redirect host")
            return super().redirect_request(req, fp, code, msg, headers, newurl)
    out = []
    for g in plan["groups"]:
        if g["id"] is not None:
            continue
        request = urllib.request.Request(g["landing"], headers={"User-Agent": "Mozilla/5.0", "Accept": "text/html"})
        try:
            with urllib.request.build_opener(SameSiteRedirect()).open(request, timeout=30) as res:
                raw = res.read(2*1024*1024).decode("utf-8", "replace")
                title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.I|re.S)
                out.append({"group": g["code"], "url": g["landing"], "final_url": res.geturl(), "http_status": res.status,
                            "title": html.unescape(title.group(1).strip()) if title else None,
                            "sku_visible": g["catalog_sku"].casefold() in raw.casefold()})
        except urllib.error.HTTPError as e:
            out.append({"group": g["code"], "url": g["landing"], "error": type(e).__name__, "http_status": e.code,
                        "location": e.headers.get("Location")})
            e.close()
        except Exception as e:
            out.append({"group": g["code"], "url": g["landing"], "error": type(e).__name__})
    return out


def main():
    report = {"campaign_id": core.CID, "started_at_utc": core.now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
              "plan_sha256": PLAN_SHA, "api_write_attempts_this_run": 0, "status": "started", "errors": []}
    api = None
    try:
        j = core.read_json(JOURNAL)[0] if JOURNAL.exists() else None
        marker, _ = core.read_json(MARKER)
        plan, sha = core.read_json(PLAN)
        source, _ = core.read_json(SOURCE)
        validate(marker, plan, sha, source, j)
        report.update(mode=marker["mode"], request_id=marker["request_id"])
        api = Client(marker["mode"], plan, report, j)
        snap = core.snapshot(api)
        report["initial_readback"] = snap
        settings = snap["campaign"]["UnifiedCampaign"]["Settings"]
        if {x["Option"]: x["Value"] for x in settings}.get("CAMPAIGN_EXACT_PHRASE_MATCHING_ENABLED") != "YES":
            raise RuntimeError("Existing phrase-relevance prioritization is required and must be preserved")
        if j is None:
            diffs, natural = existing_differences(source["after"], snap)
            if diffs or counts(snap) != plan["expected_before"] or marker["mode"] != "inspect":
                raise RuntimeError("Fresh inspect baseline59 required; source differs or mode invalid")
            restrictions = api.wire("clients", "get", {"FieldNames": ["Restrictions"]})
            report["client_restrictions"] = restrictions
            clients = restrictions.get("Clients", [])
            limits = [r.get("Value") for c in clients for r in c.get("Restrictions", []) if r.get("Element") == "KEYWORDS_TOTAL_PER_ADGROUP"]
            if len(limits) != 1 or type(limits[0]) is not int:
                raise RuntimeError("Could not read actual keyword limit")
            for g in plan["groups"]:
                existing = sum(k["AdGroupId"] == g["id"] for k in snap["keywords"]) if g["id"] else 1
                incoming = sum(k["group_code"] == g["code"] and k["action"] == "ADD_KEYWORD" for k in plan["items"])
                if existing + incoming > limits[0]:
                    raise RuntimeError("Group conservative upper-bound exceeds account keyword limit")
            report["landing_checks"] = landing_checks(plan)
            j = {"version": 1, "campaign_id": core.CID, "plan_sha256": PLAN_SHA, "created_at_utc": core.now(),
                 "baseline": snap, "baseline_sha256": core.digest(snap), "operations": {},
                 "keyword_limit": limits[0], "landing_checks": report["landing_checks"]}
            api.journal = j
            core.save(JOURNAL, j)
        elif marker["mode"] == "inspect" and not j["operations"]:
            report["landing_checks"] = landing_checks(plan)
            j["landing_checks"] = report["landing_checks"]
            core.save(JOURNAL, j)
        ownership_guard(plan, j, snap)
        report["baseline_sha256"] = j["baseline_sha256"]
        if marker["mode"] == "apply":
            # Before any live mutation require real landing responses from prior inspect.
            if any(x.get("http_status") != 200 or not x.get("sku_visible") for x in j["landing_checks"]):
                raise RuntimeError("New product landings require review before apply")
            for key in ("groups_add", "autos_settings", "autos_bids", "keywords_add", "ads_add"):
                api.perform(key, snap)
                snap = refresh(api)
                if key == "autos_bids" and not all(a.get("AutotargetingSettings") == plan["autotargeting_settings"]
                        and a.get("Bid") == plan["new_bid_micros"] and a.get("AutotargetingSearchBidIsAuto") == "NO"
                        for a in owned_autos(plan, j, snap)):
                    raise RuntimeError("Required restricted new-auto setup not saved")
                if key == "keywords_add":
                    check = verify(plan, j, snap)
                    if not check["all231_items_resolved"] or not check["new_auto_settings_verified"]:
                        raise RuntimeError("Source ledger unresolved; no new ad creation/moderation")
            check = verify(plan, j, snap)
            if not check["all231_items_resolved"] or not check["new_auto_settings_verified"]:
                raise RuntimeError("Pre-moderation all-source verification failed")
            ar = result_rows(j, "ads_add", "AddResults")
            newads = [core.by_id(snap["ads"])[r["Id"]] for r in ar]
            if all(a.get("Status") == "DRAFT" for a in newads):
                api.perform("ads_moderate", snap)
                snap = refresh(api)
            elif any(a.get("Status") in {"DRAFT", "REJECTED"} for a in newads):
                raise RuntimeError("Mixed/rejected ad states require exact follow-up; do not resume")
        report["after"] = snap
        report["verification"] = verify(plan, j, snap)
        if report["verification"]["all234_represented_and_new_ads_submitted"]:
            report["status"] = "all234_represented_verified"
            code = 0
        elif marker["mode"] == "inspect" and not j["operations"]:
            report["status"] = "inspect_verified_no_api_write"
            code = 0
        else:
            report["status"] = "partial_requires_row_review"
            code = 1
    except Exception as e:
        report["errors"].append(core.clean(str(e)))
        report["status"] = "blocked_or_partial_no_blind_retry"
        code = 1
        if api is not None and api.journal is not None and api.journal["operations"]:
            try:
                snap = core.snapshot(api)
                report["error_readback"] = snap
                report["verification"] = verify(api.plan, api.journal, snap)
            except Exception as e2:
                report["error_readback_error"] = core.clean(str(e2))
    finally:
        report["finished_at_utc"] = core.now()
        if api is not None and api.journal is not None:
            report["operations"] = deepcopy(api.journal["operations"])
        core.save(REPORT, core.clean(report))
    print(json.dumps({"status": report["status"], "api_write_attempts_this_run": report["api_write_attempts_this_run"], "errors": report["errors"]}, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())
