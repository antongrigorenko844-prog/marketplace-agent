from collections import Counter
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(".github/scripts").resolve()))
import direct_specific_noise_negatives_20261121 as direct

TERMS = (
    "7dct220", "7dct300", "6hp26", "6hp 28", "zf8hp",
    '"ремонт dq200 цена"', '"dq200 стоимость ремонта"',
    '"полная разборка платы мехатроника dsg 7 dq200"',
    '"мехатроник dq200 устройство"',
)

def main():
    direct.TERMS = TERMS
    direct.checked_inventory()
    before = direct.read_campaign()
    previous = before["NegativeKeywords"]["Items"]
    additions = [term for term in TERMS if term not in previous]
    expected = previous + additions
    if direct.read_campaign() != before:
        raise RuntimeError("Campaign changed during preflight")
    if additions:
        payload = {"Campaigns": [{"Id": direct.CID, "NegativeKeywords": {"Items": expected}}]}
        direct.ALLOWED_WRITE = payload
        try:
            result = direct.request("campaigns", "update", payload)
            print("API results:", json.dumps(result.get("UpdateResults"), ensure_ascii=False))
        except Exception as exc:
            print("API reply uncertain:", direct.safe_error(exc))

    for delay in (0, 2, 5, 10, 20):
        time.sleep(delay)
        observed = direct.read_campaign()
        actual = observed["NegativeKeywords"]["Items"]
        if Counter(actual) == Counter(expected) and direct.nonnegative(before) == direct.nonnegative(observed):
            print("VERIFIED", json.dumps({"campaign": direct.CID, "time_utc": direct.now(),
                  "before": len(previous), "after": len(actual), "added": additions,
                  "all_approved_present": all(term in actual for term in TERMS),
                  "all_previous_preserved": True}, ensure_ascii=False))
            return
    raise RuntimeError("Direct readback differs from requested list. No second update attempted.")

if __name__ == "__main__":
    main()
