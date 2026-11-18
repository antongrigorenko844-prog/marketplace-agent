"""Read-only Yandex Direct search query export for the last 30 completed days."""
import csv
import datetime as dt
import io
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path

token = os.environ["YANDEX_DIRECT_TOKEN"]
end = dt.date.today() - dt.timedelta(days=1)
start = end - dt.timedelta(days=29)
fields = ["Query", "Clicks", "Cost"]  # Known working fields from direct-report
params = {"params": {
    "SelectionCriteria": {"DateFrom": str(start), "DateTo": str(end)},
    "FieldNames": fields,
    "ReportName": "leoparts-search-audit-" + str(end),
    "ReportType": "SEARCH_QUERY_PERFORMANCE_REPORT",
    "DateRangeType": "CUSTOM_DATE",
    "Format": "TSV", "IncludeVAT": "YES",
}}
headers = {
    "Authorization": "Bearer " + token,
    "Accept-Language": "ru",
    "Content-Type": "application/json; charset=utf-8",
    "processingMode": "auto",
    "returnMoneyInMicros": "false",
    "skipReportHeader": "true",
    "skipReportSummary": "true",
}
if os.getenv("YANDEX_DIRECT_LOGIN"):
    headers["Client-Login"] = os.environ["YANDEX_DIRECT_LOGIN"]
body = json.dumps(params).encode()
for attempt in range(20):
    request = urllib.request.Request("https://api.direct.yandex.com/json/v5/reports", data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=120) as resp:
            code = resp.status
            data = resp.read().decode("utf-8-sig")
            retry = resp.headers.get("retryIn", "10")
    except urllib.error.HTTPError as err:
        code = err.code
        data = err.read().decode("utf-8", errors="replace")
        retry = err.headers.get("retryIn", "10")
    if code == 200:
        reader = csv.DictReader(io.StringIO(data), delimiter="\t")
        rows = list(reader)
        if reader.fieldnames is None:
            raise RuntimeError("No report columns returned")
        def cost(row):
            try:
                return float(row.get("Cost", "0").replace(",", "."))
            except ValueError:
                return 0.0
        rows.sort(key=cost, reverse=True)
        output = Path("data/direct_search_audit.tsv")
        output.parent.mkdir(exist_ok=True)
        with output.open("w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=reader.fieldnames, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        print(f"Read-only report {start}..{end}: {len(rows)} queries; saved {output}")
        break
    if code in (201, 202):
        time.sleep(min(int(retry or "10"), 30))
        continue
    raise RuntimeError(f"Yandex Direct report failed HTTP {code}: {data[:500]}")
else:
    raise RuntimeError("Yandex Direct report generation timed out")
