"""Read the WB card-quality scores returned by two Seller Analytics reports."""
import datetime as dt
import json
import os
import time
from pathlib import Path
import requests
from cryptography.hazmat.primitives import serialization
from live_metrics import encrypt

ORIGIN = "https://seller-analytics-api.wildberries.ru"
PERIOD = {"start": "2026-11-13", "end": "2026-11-19"}

def request(path, body):
    response = requests.post(
        ORIGIN + path, headers={"Authorization": os.environ["WB_API_TOKEN"]},
        json=body, timeout=35, allow_redirects=False)
    if response.status_code == 429:
        wait = response.headers.get("X-Ratelimit-Retry", response.headers.get("Retry-After", ""))
        if wait.isdigit() and int(wait) <= 60:
            time.sleep(max(1, int(wait)) + 1)
            response = requests.post(
                ORIGIN + path, headers={"Authorization": os.environ["WB_API_TOKEN"]},
                json=body, timeout=35, allow_redirects=False)
    return response

def collect_report(path, payload, name):
    result = {"path": path, "http": None, "rows": []}
    try:
        response = request(path, payload)
        result["http"] = response.status_code
        if response.status_code == 200:
            for entry in response.json()["data"]["products"]:
                p = entry.get("product", entry)
                result["rows"].append({
                    "nmID": p["nmId"],
                    "rated": p.get("isCardRated") if name == "search" else None,
                    "rating": p.get("rating") if name == "search" else p.get("productRating"),
                    "feedback_rating": p.get("feedbackRating"),
                })
    except (KeyError, ValueError, requests.RequestException) as error:
        result["error_type"] = type(error).__name__
    print(name, "HTTP", result["http"], "records", len(result["rows"]), flush=True)
    return result

def main():
    reference = json.loads(Path("data/wb_cards.json").read_text())["cards"]
    ids = {int(card["nmID"]) for card in reference}
    base = {"limit": 1000, "offset": 0}
    search = collect_report("/api/v2/search-report/table/details", {
        **base, "currentPeriod": PERIOD, "orderBy": {"field": "avgPosition", "mode": "asc"},
        "positionCluster": "all", "includeSubstitutedSKUs": True, "includeSearchTexts": True,
    }, "search")
    # An independent documented source supplies the score for goods omitted from search.
    funnel = collect_report("/api/analytics/v3/sales-funnel/products", {
        **base, "selectedPeriod": PERIOD, "skipDeletedNm": False,
    }, "funnel")
    result = {"timestamp": dt.datetime.now(dt.timezone.utc).isoformat(), "period": PERIOD,
              "reference_count": len(ids), "search": search, "funnel": funnel}
    for report in (search, funnel):
        report["rows"] = [r for r in report["rows"] if r["nmID"] in ids]
    pub = serialization.load_pem_public_key(Path("scripts/marketing_audit_verify_public.pem").read_bytes())
    encrypt(result, pub, Path("wb-rating-result/ratings.enc.json"))

if __name__ == "__main__":
    main()
