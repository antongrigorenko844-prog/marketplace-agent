"""Append exactly four approved unquoted thematic negatives; preserve existing 38."""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import urllib.error
import urllib.request

CID = 716316525
BASELINE = Path("data/direct_service_negatives_apply_20261119.json")
OUT = Path("data/direct_thematic_negatives_apply_20261119.json")
QUERIES = (
    "плата ноутбука",
    "компьютерные компоненты",
    "сервисное оборудование",
    "оборудование автомобильного сервиса",
)
ADDITIONS = QUERIES  # Intentionally unquoted; only these four approved themes.
CANONICAL_REMOVAL = '"плата ноутбука"'
PROTECTED = (
    "коробка робот замена", "замена корпуса мехатроника", "трещина платы дсг",
    "ремонт платы мехатроника dsg 7 dq200", "замена электронного плата мехатроника",
    "ремонт", "ремонт мехатроника", "ремонт мехатроника dq200", "мехатроник ремонт",
    "замена", "адаптация", "клонирование", "диагностика",
)
GET_FIELDS = ["Id", "Name", "Type", "State", "Status", "NegativeKeywords"]
VERIFIED_BASELINE_STATUSES = {
    "applied_verified", "already_applied_verified", "verified_after_uncertain_response"
}



def now():
    return datetime.now(timezone.utc).isoformat()


def clean(value):
    token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
    value = str(value)
    return (value.replace(token, "[redacted]") if token else value)[:1800]


def call(method, params):
    if method not in ("get", "update"):
        raise RuntimeError("Only campaigns.get and scoped campaigns.update are allowed")
    if method == "update":
        campaigns = params.get("Campaigns", [])
        if (set(params) != {"Campaigns"} or len(campaigns) != 1
                or set(campaigns[0]) != {"Id", "NegativeKeywords"}
                or campaigns[0]["Id"] != CID
                or set(campaigns[0]["NegativeKeywords"]) != {"Items"}):
            raise RuntimeError("Mutation scope guard failed")
    token = os.environ.get("YANDEX_DIRECT_TOKEN")
    if not token:
        raise RuntimeError("YANDEX_DIRECT_TOKEN is missing")
    headers = {"Authorization": "Bearer " + token, "Accept-Language": "en",
               "Content-Type": "application/json; charset=utf-8"}
    if os.environ.get("YANDEX_DIRECT_LOGIN"):
        headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
    request = urllib.request.Request("https://api.direct.yandex.com/json/v501/campaigns",
        data=json.dumps({"method": method, "params": params}, ensure_ascii=False).encode("utf-8"),
        headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            data = json.loads(response.read().decode("utf-8-sig"))
    except urllib.error.HTTPError as error:
        raise RuntimeError("campaigns." + method + " HTTP " + str(error.code) + ": " +
                           clean(error.read().decode("utf-8", "replace"))) from error
    if data.get("error") or "result" not in data:
        raise RuntimeError("campaigns." + method + ": " + clean(json.dumps(data, ensure_ascii=False)))
    return data["result"]


def negatives(campaign):
    obj = campaign.get("NegativeKeywords")
    items = [] if obj is None else obj.get("Items")
    if not isinstance(items, list) or any(not isinstance(x, str) for x in items):
        raise RuntimeError("Invalid NegativeKeywords representation")
    return list(items)


def same_phrases(a, b):
    # Array ordering is immaterial; every character/operator and multiplicity is exact.
    return Counter(a) == Counter(b)


def nonnegative_fields(campaign):
    return {key: value for key, value in campaign.items() if key != "NegativeKeywords"}


def run(apply=False, api=None, baseline_path=None, out_path=None):
    api = api or call
    out_path = Path(out_path or OUT)
    report = {"status": "preflight", "mode": "apply" if apply else "read_only_plan",
        "campaign_id": CID, "started_at_utc": now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
        "approved_additions": list(ADDITIONS), "preserved_queries": list(PROTECTED),
        "allowed_canonical_removed_phrase": CANONICAL_REMOVAL,
        "authorization": {
            "source": "Current conversation: user authorized the previously proposed four thematic negatives",
            "scope": "Append these four unquoted phrases only; preserve all existing 38 phrases",
            "leave_ambiguous_queries_and_broad_repair_phrases": True,
        },
        "nonnegative_campaign_fields_checked": [x for x in GET_FIELDS if x != "NegativeKeywords"],
        "write_attempted": False, "errors": []}

    def save():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        temp = out_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(out_path)

    def snapshot():
        result = api("get", {"SelectionCriteria": {"Ids": [CID]},
            "FieldNames": GET_FIELDS})
        campaigns = result.get("Campaigns", [])
        if len(campaigns) != 1 or campaigns[0].get("Id") != CID or result.get("LimitedBy"):
            raise RuntimeError("Unexpected campaign selection")
        return campaigns[0]

    save()
    try:
        if len(ADDITIONS) != 4 or len(set(ADDITIONS)) != 4:
            raise RuntimeError("Expected exactly four unique approved thematic additions")
        for phrase in ADDITIONS:
            words = phrase.split()
            if (phrase != " ".join(words) or not 2 <= len(words) <= 7
                    or any(not w.isalpha() or len(w) > 35 for w in words)
                    or phrase in PROTECTED):
                raise RuntimeError("Only approved unquoted multiword phrases are allowed")
        if any(set(p.split()).issubset(set(q.split())) for p in ADDITIONS for q in PROTECTED):
            raise RuntimeError("Thematic phrase literally overlaps a protected query")
        baseline = json.loads(Path(baseline_path or BASELINE).read_text(encoding="utf-8"))
        if (baseline.get("status") not in VERIFIED_BASELINE_STATUSES
                or baseline.get("campaign_id") != CID
                or baseline.get("original_33_preserved_exactly") is not True
                or baseline.get("all_five_present_with_ascii_quotes") is not True
                or baseline.get("exact_expected_38_phrases") is not True
                or baseline.get("nonnegative_campaign_fields_unchanged") is not True):
            raise RuntimeError("The prior 38-phrase apply report is not verified")
        baseline_after = baseline.get("after", {})
        if baseline_after.get("Id") != CID:
            raise RuntimeError("Baseline campaign mismatch")
        original = negatives(baseline_after)
        if (len(original) != 38 or len(set(original)) != 38 or set(original) & set(ADDITIONS)
                or original.count(CANONICAL_REMOVAL) != 1
                or not same_phrases(original, baseline.get("proposed_negatives", []))):
            raise RuntimeError("Expected the exact verified baseline of 38 phrases")
        canonical = [x for x in original if x != CANONICAL_REMOVAL] + list(ADDITIONS)
        report["baseline_report"] = str(baseline_path or BASELINE)
        report["baseline_verified_at_utc"] = baseline.get("finished_at_utc")
        report["baseline_report_workflow_commit"] = baseline.get("workflow_commit")
        report["baseline_report_source_commit"] = os.environ.get("GITHUB_SHA")
        report["baseline_report_reviewed_commit"] = "f20527b7d4bb2ea0a6b55a54c674801403e1e4ee"
        # No age limit on the verified baseline; fresh live reads are mandatory below.
        report["baseline_original_negatives"] = original
        before = snapshot()
        current = negatives(before)
        report["before"] = before
        report["live_read_at_utc"] = now()
        report["baseline_phrase_count"] = len(original)
        report["before_phrase_count"] = len(current)
        already = [x for x in ADDITIONS if x in current]
        # Also accept only the observed complete canonical form: other 37 + all four.
        canonical_before = same_phrases(current, canonical)
        report["canonical_41_already_present"] = canonical_before
        if not same_phrases(current, original + already) and not canonical_before:
            raise RuntimeError("Live negatives differ from baseline plus exact approved additions")
        missing = [x for x in ADDITIONS if x not in current]
        proposed = current + missing
        report.update({"already_present": already, "missing_additions": missing,
            "proposed_phrase_count": len(proposed),
            "proposed_negatives": proposed, "payload": {"Campaigns": [
                {"Id": CID, "NegativeKeywords": {"Items": proposed}}]}})
        if (not same_phrases(proposed, original + list(ADDITIONS))
                and not same_phrases(proposed, canonical)):
            raise RuntimeError("Proposed list would change original phrases or exceed approved additions")
        save()
        if not apply:
            report["status"] = "plan_verified_no_write"
            return 0
        if missing:
            prewrite = snapshot()
            report["prewrite"] = prewrite
            if (not same_phrases(negatives(prewrite), current)
                    or nonnegative_fields(prewrite) != nonnegative_fields(before)):
                raise RuntimeError("Campaign changed between preflight and write; preserve concurrent changes")
            report["write_attempted"] = True
            save()
            try:
                result = api("update", report["payload"])
                report["update_response"] = result
                entries = result.get("UpdateResults", [])
                if len(entries) != 1 or entries[0].get("Id") != CID or entries[0].get("Errors"):
                    raise RuntimeError("Unexpected/failed UpdateResults: " + clean(json.dumps(result, ensure_ascii=False)))
            except Exception as error:
                # Never repeat an uncertain write. Reconcile it with a fresh get.
                report["update_error"] = clean(error)
                save()
        after = snapshot()
        observed = negatives(after)
        report["after"] = after
        report["after_phrase_count"] = len(observed)
        report["original_38_preserved_exactly"] = all(observed.count(x) == original.count(x) for x in original)
        report["all_four_present_unquoted"] = all(observed.count(x) == 1 for x in ADDITIONS)
        report["exact_expected_42_phrases"] = same_phrases(observed, original + list(ADDITIONS))
        report["exact_observed_canonical_41_phrases"] = same_phrases(observed, canonical)
        report["removed_original_phrases"] = list((Counter(original) - Counter(observed)).elements())
        report["remaining_original_37_preserved_exactly"] = all(
            observed.count(x) == 1 for x in original if x != CANONICAL_REMOVAL)
        report["nonnegative_campaign_fields_unchanged"] = nonnegative_fields(after) == nonnegative_fields(before)
        report["protected_queries_not_added"] = not (set(PROTECTED) & set(QUERIES))
        if ((report["exact_expected_42_phrases"] or report["exact_observed_canonical_41_phrases"])
                and report["nonnegative_campaign_fields_unchanged"]):
            report["status"] = ("already_applied_verified" if not missing else
                "verified_after_uncertain_response" if "update_error" in report else "applied_verified")
            if report["exact_observed_canonical_41_phrases"]:
                report["status"] += "_canonical_41"
                report["canonicalization_note"] = (
                    "Only the quoted laptop phrase is absent; its approved unquoted counterpart "
                    "and all other 37 original phrases are present. No other removal is permitted.")
            report["changed_fields"] = ["NegativeKeywords"] if missing else []
            return 0
        report["status"] = ("not_applied_verified_unchanged"
            if same_phrases(observed, current) and report["nonnegative_campaign_fields_unchanged"]
            else "unexpected_after_requires_review")
        report["errors"].append("Readback differs from the requested 42 or bounded canonical 41 phrases, or other read campaign fields; no retry or rollback performed")
        # Do not replace an unexpected list: it may contain another person's changes.
        return 1
    except Exception as error:
        report["errors"].append(clean(error))
        report["status"] = "verification_failed_write_outcome_unknown" if report["write_attempted"] else "blocked_no_write"
        return 1
    finally:
        report["finished_at_utc"] = now()
        save()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    sys.exit(run(apply=args.apply))
