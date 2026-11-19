"""Read-only reconciliation of the observed 42-to-41 negative-phrase normalization."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

CID = 716316525
REMOVED = '"плата ноутбука"'
ADDED = ("плата ноутбука", "компьютерные компоненты", "сервисное оборудование",
         "оборудование автомобильного сервиса")


def verify_thematic_negatives(get_campaigns,
        output="data/direct_thematic_negatives_verification_20261119.json",
        baseline_path="data/direct_service_negatives_apply_20261119.json",
        attempt_path="data/direct_thematic_negatives_apply_20261119.json"):
    """get_campaigns(params) must make campaigns.get and return its unwrapped result.

    The callback is invoked once, with GET selection parameters only. No mutation
    or retry is performed. Both source reports are read and never rewritten.
    """
    report = {"status": "verification_failed", "mode": "read_only", "campaign_id": CID,
        "started_at_utc": datetime.now(timezone.utc).isoformat(),
        "workflow_commit": os.environ.get("GITHUB_SHA"), "errors": [],
        "original_attempt_report_retained": True,
        "original_apply_run_id": "44552767161",
        "only_allowed_removed_phrase": REMOVED, "approved_added_phrases": list(ADDED)}
    output = Path(output)
    if output.resolve() in {Path(baseline_path).resolve(), Path(attempt_path).resolve()}:
        raise ValueError("Verification output must not overwrite either source report")

    def phrases(campaign):
        values = (campaign.get("NegativeKeywords") or {}).get("Items")
        if not isinstance(values, list) or any(not isinstance(x, str) for x in values):
            raise ValueError("Invalid NegativeKeywords.Items")
        return values

    def other_fields(campaign):
        return {k: v for k, v in campaign.items() if k != "NegativeKeywords"}

    try:
        raw_base, raw_attempt = Path(baseline_path).read_bytes(), Path(attempt_path).read_bytes()
        baseline, attempt = json.loads(raw_base), json.loads(raw_attempt)
        report["sources"] = {
            "verified_38": {"path": str(baseline_path), "sha256": hashlib.sha256(raw_base).hexdigest()},
            "first_attempt": {"path": str(attempt_path), "sha256": hashlib.sha256(raw_attempt).hexdigest(),
                "status": attempt.get("status"), "workflow_commit": attempt.get("workflow_commit")}}
        if (baseline.get("campaign_id") != CID or attempt.get("campaign_id") != CID
                or baseline.get("exact_expected_38_phrases") is not True
                or baseline.get("nonnegative_campaign_fields_unchanged") is not True
                or attempt.get("status") != "unexpected_after_requires_review"):
            raise ValueError("Source reports do not describe the verified baseline and original strict failure")
        original = phrases(baseline["after"])
        original_before = attempt["before"]
        if (len(original) != 38 or len(set(original)) != 38 or original.count(REMOVED) != 1
                or set(original) & set(ADDED)
                or Counter(phrases(original_before)) != Counter(original)):
            raise ValueError("Original 38-phrase scope does not match")
        expected = [x for x in original if x != REMOVED] + list(ADDED)
        result = attempt.get("update_response", {}).get("UpdateResults", [])
        if (len(result) != 1 or result[0].get("Id") != CID or result[0].get("Errors")
                or Counter(attempt.get("approved_additions", [])) != Counter(ADDED)
                or Counter(phrases(attempt["after"])) != Counter(expected)
                or other_fields(attempt["after"]) != other_fields(original_before)):
            raise ValueError("First readback is not solely the observed quoted-laptop removal plus four additions")
        params = {"SelectionCriteria": {"Ids": [CID]},
            "FieldNames": ["Id", "Name", "Type", "State", "Status", "NegativeKeywords"]}
        report["fresh_get_params"] = params
        response = get_campaigns(params)
        campaigns = response.get("Campaigns", [])
        if len(campaigns) != 1 or campaigns[0].get("Id") != CID or response.get("LimitedBy"):
            raise ValueError("Unexpected fresh campaign selection")
        fresh = campaigns[0]
        observed = phrases(fresh)
        report.update({"fresh_at_utc": datetime.now(timezone.utc).isoformat(), "after": fresh,
            "expected_phrase_count": 41, "observed_phrase_count": len(observed),
            "removed_from_original_38": list((Counter(original) - Counter(observed)).elements()),
            "added_to_original_38": list((Counter(observed) - Counter(original)).elements()),
            "remaining_original_37_preserved_exactly": all(observed.count(x) == 1 for x in original if x != REMOVED),
            "all_four_present_unquoted": all(observed.count(x) == 1 for x in ADDED),
            "only_quoted_laptop_removed": Counter(original) - Counter(observed) == Counter([REMOVED]),
            "exact_expected_41_phrases": Counter(observed) == Counter(expected),
            "nonnegative_campaign_fields_unchanged": other_fields(fresh) == other_fields(original_before)})
        if not report["exact_expected_41_phrases"] or not report["nonnegative_campaign_fields_unchanged"]:
            raise ValueError("Fresh readback differs from the bounded canonical 41 or other read campaign fields")
        report["status"] = "verified_canonical_41"
        report["explanation"] = ("The first strict guard correctly reported 38-to-42 mismatch. "
            "Observed readback removed only the prior quoted laptop phrase after adding its unquoted counterpart. "
            "Fresh GET confirms the other 37 originals and all four approved additions. No repeat write occurred.")
    except Exception as error:
        message = str(error)
        token = os.environ.get("YANDEX_DIRECT_TOKEN", "")
        report["errors"].append((message.replace(token, "[redacted]") if token else message)[:1800])
    report["finished_at_utc"] = datetime.now(timezone.utc).isoformat()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(output)
    return report
