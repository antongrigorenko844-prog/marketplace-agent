"""Append only nine approved, quoted queries; preserve all existing negatives."""
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
BASELINE = Path("data/direct_negatives_live_20261119.json")
OUT = Path("data/direct_reviewed_negatives_apply_20261119.json")
QUERIES = (
    "сколько стоит плата на ноутбук",
    "купить плату для ноутбука",
    "плата для ноутбука",
    "плата ноутбука",
    "плата от ноутбука",
    "плата у ноутбука",
    "цена на платы от ноутбуков",
    "эл плата и компьютерные компоненты",
    "скачать сайт для покупки авто запчастей",
)
ADDITIONS = tuple('"' + q + '"' for q in QUERIES)
PROTECTED = ("трещина платы дсг", "коробка робот замена", "ремонт мехатроника сервисы")


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


def run(apply=False, api=None, baseline_path=None, out_path=None):
    api = api or call
    out_path = Path(out_path or OUT)
    report = {"status": "preflight", "mode": "apply" if apply else "read_only_plan",
        "campaign_id": CID, "started_at_utc": now(), "workflow_commit": os.environ.get("GITHUB_SHA"),
        "approved_additions": list(ADDITIONS), "protected_queries": list(PROTECTED),
        "write_attempted": False, "errors": []}

    def save():
        out_path.parent.mkdir(parents=True, exist_ok=True)
        temp = out_path.with_suffix(".json.tmp")
        temp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(out_path)

    def snapshot():
        result = api("get", {"SelectionCriteria": {"Ids": [CID]},
            "FieldNames": ["Id", "Name", "Type", "State", "Status", "NegativeKeywords"]})
        campaigns = result.get("Campaigns", [])
        if len(campaigns) != 1 or campaigns[0].get("Id") != CID or result.get("LimitedBy"):
            raise RuntimeError("Unexpected campaign selection")
        return campaigns[0]

    save()
    try:
        if len(ADDITIONS) != 9 or len(set(ADDITIONS)) != 9:
            raise RuntimeError("Expected exactly nine unique approved additions")
        for phrase in ADDITIONS:
            words = phrase[1:-1].split()
            if (phrase[:1] != '"' or phrase[-1:] != '"' or phrase.count('"') != 2
                    or not 2 <= len(words) <= 7 or any(len(w) > 35 for w in words)
                    or phrase[1:-1] in PROTECTED):
                raise RuntimeError("Approved phrase guard failed")
        baseline = json.loads(Path(baseline_path or BASELINE).read_text(encoding="utf-8"))
        stamp = datetime.fromisoformat(baseline["captured_at_utc"])
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        if baseline.get("status") != "complete" or baseline.get("campaign_id") != CID or not -60 <= age < 3600:
            raise RuntimeError("Read-only baseline is incomplete, stale or outside campaign scope")
        matches = [x for x in baseline.get("campaigns", []) if x.get("Id") == CID]
        if len(matches) != 1:
            raise RuntimeError("Baseline campaign missing or duplicated")
        original = negatives(matches[0])
        if len(original) != 24 or len(set(original)) != 24 or set(original) & set(ADDITIONS):
            raise RuntimeError("Expected the approved baseline of 24 original phrases")
        report["baseline_captured_at_utc"] = baseline["captured_at_utc"]
        report["baseline_original_negatives"] = original
        before = snapshot()
        current = negatives(before)
        report["before"] = before
        already = [x for x in ADDITIONS if x in current]
        # The sole permitted difference from the baseline is our own exact additions.
        if not same_phrases(current, original + already):
            raise RuntimeError("Live negatives differ from baseline plus exact approved additions")
        missing = [x for x in ADDITIONS if x not in current]
        proposed = current + missing
        report.update({"already_present": already, "missing_additions": missing,
            "proposed_negatives": proposed, "payload": {"Campaigns": [
                {"Id": CID, "NegativeKeywords": {"Items": proposed}}]}})
        if not same_phrases(proposed, original + list(ADDITIONS)):
            raise RuntimeError("Proposed list would change original phrases or exceed approved additions")
        save()
        if not apply:
            report["status"] = "plan_verified_no_write"
            return 0
        if missing:
            prewrite = snapshot()
            report["prewrite"] = prewrite
            if not same_phrases(negatives(prewrite), current):
                raise RuntimeError("Negatives changed between preflight and write; preserve concurrent changes")
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
        report["original_24_preserved_exactly"] = all(observed.count(x) == original.count(x) for x in original)
        report["all_nine_present_with_ascii_quotes"] = all(observed.count(x) == 1 for x in ADDITIONS)
        report["exact_expected_33_phrases"] = same_phrases(observed, original + list(ADDITIONS))
        if report["exact_expected_33_phrases"]:
            report["status"] = ("already_applied_verified" if not missing else
                "verified_after_uncertain_response" if "update_error" in report else "applied_verified")
            report["changed_fields"] = ["NegativeKeywords"] if missing else []
            return 0
        report["status"] = "not_applied_verified_unchanged" if same_phrases(observed, current) else "unexpected_after_requires_review"
        report["errors"].append("Readback differs from the exact expected 33 phrases; no retry or rollback performed")
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
