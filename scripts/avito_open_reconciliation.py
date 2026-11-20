"""Report exact SKU quantities in open Avito orders, encrypted for the recipient."""

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

import avito_order_preview_readonly as preview


def side(created, checkpoint):
    if checkpoint is None:
        return "unknown"
    return "before" if created < checkpoint else "at_or_after"


def aggregate(fetch, mapping, start, end, physical_count_at=None):
    totals = Counter()
    open_orders = 0
    if physical_count_at is not None:
        preview.require(physical_count_at.tzinfo is not None and physical_count_at <= end,
                        "invalid_physical_count_time")

    def collect(page, since):
        nonlocal open_orders
        response = fetch(page, since)
        # scan() independently validates the complete stream, including changing
        # rows, repeated IDs, dates and pagination. Release totals only on success.
        for order in response["orders"]:
            _, (created, _, status, items) = preview.normalize(order)
            if preview.GROUP.get(status) != "active":
                continue
            open_orders += 1
            for listing, qty in items:
                sku = mapping.get(listing)
                preview.require(isinstance(sku, str) and sku, "unmapped_open_item")
                totals[sku, status, side(created, preview.CUT), side(created, physical_count_at)] += qty
        return response

    report = preview.scan(collect, start, end, mapping)
    preview.require(report["accessible_pagination_passed"], "incomplete_or_unstable_scan")
    preview.require(report["all_observed_statuses_classified"], "unknown_order_status")
    checked = [period["active"] for period in report["current_lifecycle_by_creation"].values()]
    preview.require(sum(item["orders"] for item in checked) == open_orders
                    and sum(item["exact_sku_units"] for item in checked) == sum(totals.values()),
                    "open_totals_conflict")
    return {
        "observed_open_orders": open_orders,
        "rows": [
            {"sku": sku, "qty": qty, "status_group": status,
             "creation_vs_publication_checkpoint": publication, "creation_vs_physical_count": physical}
            for (sku, status, publication, physical), qty in sorted(totals.items())
        ],
    }


def save_encrypted(report, recipient_cert, output):
    plaintext = json.dumps(report, ensure_ascii=False, sort_keys=True).encode("utf-8")
    result = subprocess.run(
        ["openssl", "cms", "-encrypt", "-binary", "-aes256", "-outform", "DER", str(recipient_cert.resolve())],
        input=plaintext, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=30, check=False,
    )
    preview.require(result.returncode == 0 and result.stdout, "encryption_failed")
    fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(result.stdout)
    except BaseException:
        output.unlink(missing_ok=True)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--recipient-cert", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True, help="New CMS DER file")
    parser.add_argument("--physical-count-at", help="Confirmed physical count time, ISO 8601 with timezone")
    args = parser.parse_args()
    end = datetime.now(timezone.utc).replace(microsecond=0)
    start = end - timedelta(days=180) + timedelta(minutes=5)
    physical = preview.timestamp(args.physical_count_at) if args.physical_count_at else None
    mapping = preview.exact_map(args.data_dir)
    result = aggregate(preview.avito_pages(), mapping, start, end, physical)
    save_encrypted(result, args.recipient_cert, args.output)
    print("encrypted_report_ready")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print("encrypted_report_unavailable", file=sys.stderr)
        sys.exit(2)
